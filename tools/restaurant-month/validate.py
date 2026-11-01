#!/usr/bin/env python3
"""Validate data/restaurant-month.json. Spec: tools/RESTAURANT-MONTH.md (v1.2); data rule from PLAN.md
2026-10-31 12:20.

Never publish an empty, invalid, or unexpectedly reduced dataset. This checks the schema and refuses a
reduction against the last published copy unless --allow-removals is given (a restaurant can withdraw;
confirm that by hand, then pass the flag). A reduction is any of: a promotion id or entry id that
disappeared, fewer entries in any promotion, fewer offers (or fewer priced offers) on an entry, a
promotion whose confirmed dates changed, or a promotion whose dates_confirmed went from true to false.

The last published copy is, in order: --baseline PATH; the block inlined in
tools/restaurant-month/index.html (what visitors are served); `git show HEAD:data/restaurant-month.json`.
If none exists the reduction check is skipped (and said so).

  python3 tools/restaurant-month/validate.py                    # validate data/restaurant-month.json
  python3 tools/restaurant-month/validate.py --allow-removals   # a confirmed withdrawal removed entries
Exit status 0 when valid, 1 when not.
"""
import argparse
import datetime as dt
import json
import math
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
import rules  # noqa: E402

DATA = REPO / "data" / "restaurant-month.json"
PAGE = HERE / "index.html"

TOP_KEYS = ("meta", "promotions", "entries")
PROMO_KEYS = ("id", "name", "start", "end", "source_url", "dates_confirmed")
ENTRY_KEYS = ("id", "promotion_id", "name", "town", "county", "address", "lat", "lon", "geo_source",
              "cuisine", "booking_url", "website", "phone", "source_url", "last_checked", "offers")
OFFER_REQUIRED = ("meal", "price", "description")
OFFER_OPTIONAL = ("start", "end", "weekdays", "exclusions")
COUNTY_OF_SERIES = {"napa": "Napa", "sonoma": "Sonoma"}
GEO_SOURCES = ("census", "manual", "none")
# A generous box around Napa and Sonoma counties; a pin outside it is a typo.
LAT_RANGE = (38.05, 38.95)
LON_RANGE = (-123.65, -122.05)

DATA_START = "<!-- rm-data:start -->"
DATA_END = "<!-- rm-data:end -->"
ID_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def _date(v):
    try:
        return isinstance(v, str) and dt.date.fromisoformat(v).isoformat() == v
    except ValueError:
        return False


def _url(v):
    return isinstance(v, str) and re.match(r"^https?://[^\s]+$", v) is not None


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _text(v):
    return isinstance(v, str) and v.strip() != ""


def check_promotion(p, i, today):
    where = "promotions[%d]" % i
    if not isinstance(p, dict) or set(p) != set(PROMO_KEYS):
        return ["%s: keys must be exactly %s" % (where, ", ".join(PROMO_KEYS))]
    where = "promotions[%d] (%s)" % (i, p["id"])
    errs = []
    if not (isinstance(p["id"], str) and rules.PROMO_RE.match(p["id"])):
        errs.append("%s: id must be napa-YYYY or sonoma-YYYY" % where)
    if not _text(p["name"]):
        errs.append("%s: name must be a non-empty string" % where)
    if not _url(p["source_url"]):
        errs.append("%s: source_url must be the organizer's page" % where)
    if not isinstance(p["dates_confirmed"], bool):
        errs.append("%s: dates_confirmed must be true or false" % where)
    elif p["dates_confirmed"]:
        if not (_date(p["start"]) and _date(p["end"])):
            errs.append("%s: confirmed dates need start and end as YYYY-MM-DD" % where)
        elif p["start"] > p["end"]:
            errs.append("%s: start is after end" % where)
        elif rules.year_of(p["id"]) is not None and int(p["start"][:4]) != rules.year_of(p["id"]):
            errs.append("%s: start year does not match the id" % where)
    elif p["start"] is not None or p["end"] is not None:
        errs.append("%s: dates_confirmed false means start and end are null (never inferred)" % where)
    return errs


def check_offer(o, where, promo):
    if not isinstance(o, dict):
        return ["%s: not an object" % where]
    errs = []
    missing = [k for k in OFFER_REQUIRED if k not in o]
    extra = [k for k in o if k not in OFFER_REQUIRED + OFFER_OPTIONAL]
    if missing or extra:
        return ["%s: missing %s; unexpected %s" % (where, missing or "none", extra or "none")]
    if o["meal"] not in rules.MEALS:
        errs.append("%s: meal must be one of %s" % (where, ", ".join(rules.MEALS)))
    if o["price"] is not None and not (_num(o["price"]) and o["price"] >= 0):
        errs.append("%s: price must be a number or null" % where)
    if not _text(o["description"]):
        errs.append("%s: description (the restaurant's words) is required" % where)
    for k in ("start", "end"):
        if k in o and not _date(o[k]):
            errs.append("%s: %s must be YYYY-MM-DD" % (where, k))
    if _date(o.get("start")) and _date(o.get("end")) and o["start"] > o["end"]:
        errs.append("%s: start is after end" % where)
    if promo and promo.get("dates_confirmed"):
        for k in ("start", "end"):
            if _date(o.get(k)) and not (promo["start"] <= o[k] <= promo["end"]):
                errs.append("%s: %s %s is outside the promotion's dates" % (where, k, o[k]))
    if "weekdays" in o:
        w = o["weekdays"]
        if not (isinstance(w, list) and w and all(x in rules.WEEKDAYS for x in w) and len(set(w)) == len(w)):
            errs.append("%s: weekdays must be a non-empty list of mon..sun, no repeats" % where)
    if "exclusions" in o:
        x = o["exclusions"]
        if not (isinstance(x, list) and x and all(_date(d) for d in x)):
            errs.append("%s: exclusions must be a non-empty list of YYYY-MM-DD" % where)
    return errs


def check_entry(e, i, promos, today):
    where = "entries[%d]" % i
    if not isinstance(e, dict):
        return ["%s: not an object" % where]
    if isinstance(e.get("id"), str):
        where = "entries[%d] (%s)" % (i, e["id"])
    missing = [k for k in ENTRY_KEYS if k not in e]
    extra = [k for k in e if k not in ENTRY_KEYS]
    if missing or extra:
        return ["%s: missing %s; unexpected %s" % (where, missing or "none", extra or "none")]
    errs = []
    if not (isinstance(e["id"], str) and ID_RE.match(e["id"])):
        errs.append("%s: id must be a lowercase slug" % where)
    promo = promos.get(e["promotion_id"])
    if promo is None:
        errs.append("%s: promotion_id %r does not exist" % (where, e["promotion_id"]))
    elif e["county"] != COUNTY_OF_SERIES.get(rules.series_of(e["promotion_id"])):
        errs.append("%s: county %r does not match %s" % (where, e["county"], e["promotion_id"]))
    for k in ("name", "town"):
        if not _text(e[k]):
            errs.append("%s: %s must be a non-empty string" % (where, k))
    for k in ("address", "cuisine", "phone"):
        if e[k] is not None and not _text(e[k]):
            errs.append("%s: %s must be text or null" % (where, k))
    if e["geo_source"] not in GEO_SOURCES:
        errs.append("%s: geo_source must be census, manual or none" % where)
    elif e["geo_source"] == "none":
        if e["lat"] is not None or e["lon"] is not None:
            errs.append("%s: geo_source none means lat and lon are null" % where)
    else:
        if not (_num(e["lat"]) and LAT_RANGE[0] <= e["lat"] <= LAT_RANGE[1]):
            errs.append("%s: lat %r outside Napa and Sonoma" % (where, e["lat"]))
        if not (_num(e["lon"]) and LON_RANGE[0] <= e["lon"] <= LON_RANGE[1]):
            errs.append("%s: lon %r outside Napa and Sonoma" % (where, e["lon"]))
    for k in ("booking_url", "website"):
        if e[k] is not None and not _url(e[k]):
            errs.append("%s: %s must be a URL or null" % (where, k))
    if not _url(e["source_url"]):
        errs.append("%s: source_url must be the page the entry was read from" % where)
    if not _date(e["last_checked"]):
        errs.append("%s: last_checked must be YYYY-MM-DD" % where)
    elif e["last_checked"] > today:
        errs.append("%s: last_checked %s is in the future" % (where, e["last_checked"]))
    if not isinstance(e["offers"], list):
        errs.append("%s: offers must be a list" % where)
    else:
        for j, o in enumerate(e["offers"]):
            errs += check_offer(o, "%s.offers[%d]" % (where, j), promo)
    return errs


def validate(data, today=None):
    """List of problems; empty means valid."""
    today = today or rules.la_today()
    if not isinstance(data, dict):
        return ["top level must be an object"]
    errs = []
    if set(data) != set(TOP_KEYS):
        errs.append("top level keys must be exactly %s" % ", ".join(TOP_KEYS))
    meta = data.get("meta")
    if not (isinstance(meta, dict) and set(meta) == {"last_reviewed"} and _date(meta.get("last_reviewed"))):
        errs.append("meta must be {last_reviewed: YYYY-MM-DD}")
    elif meta["last_reviewed"] > today:
        errs.append("meta.last_reviewed is in the future")
    promotions = data.get("promotions")
    if not isinstance(promotions, list) or not promotions:
        return errs + ["promotions must be a non-empty list"]
    for i, p in enumerate(promotions):
        errs += check_promotion(p, i, today)
    pids = [p.get("id") for p in promotions if isinstance(p, dict)]
    dupes = sorted({x for x in pids if pids.count(x) > 1 and x is not None})
    if dupes:
        errs.append("duplicate promotion ids: %s" % ", ".join(map(str, dupes)))
    promos = {p.get("id"): p for p in promotions if isinstance(p, dict)}
    entries = data.get("entries")
    if not isinstance(entries, list) or not entries:
        return errs + ["entries must be a non-empty list (an empty dataset is never published)"]
    for i, e in enumerate(entries):
        errs += check_entry(e, i, promos, today)
    ids = [e.get("id") for e in entries if isinstance(e, dict)]
    dupes = sorted({x for x in ids if ids.count(x) > 1 and x is not None})
    if dupes:
        errs.append("duplicate entry ids: %s" % ", ".join(map(str, dupes)))
    return errs


def reduction(new, old, allow_removals=False):
    """(problems, removed_ids). Against the last published copy: a promotion or entry id that
    disappeared, fewer entries in a promotion, fewer offers or fewer priced offers on an entry, changed
    confirmed dates, dates_confirmed true -> false. --allow-removals overrides."""
    if old is None:
        return [], []
    found = []
    old_p = [p.get("id") for p in old.get("promotions", [])]
    new_p = [p.get("id") for p in new.get("promotions", [])]
    gone_p = [x for x in old_p if x not in new_p]
    if gone_p:
        found.append("promotions removed: %s" % ", ".join(map(str, gone_p)))
    new_by = {p.get("id"): p for p in new.get("promotions", [])}
    for p in old.get("promotions", []):
        q = new_by.get(p.get("id"))
        if not q or not p.get("dates_confirmed"):
            continue
        if not q.get("dates_confirmed"):
            found.append("%s dates_confirmed went from true to false" % p["id"])
        elif (q.get("start"), q.get("end")) != (p.get("start"), p.get("end")):
            found.append("%s dates changed from %s..%s to %s..%s" % (p["id"], p.get("start"), p.get("end"),
                                                                    q.get("start"), q.get("end")))
    old_e = {e.get("id"): e for e in old.get("entries", [])}
    new_e = {e.get("id"): e for e in new.get("entries", [])}
    removed = [x for x in old_e if x not in new_e]
    if removed:
        found.append("entries removed: %s" % ", ".join(map(str, removed)))
    for pid in old_p:
        a = sum(1 for e in old_e.values() if e.get("promotion_id") == pid)
        b = sum(1 for e in new_e.values() if e.get("promotion_id") == pid)
        if b < a:
            found.append("%s has %d entries, down from %d" % (pid, b, a))
    for k, e in new_e.items():
        if k in old_e:
            a, b = len(old_e[k].get("offers") or []), len(e.get("offers") or [])
            if b < a:
                found.append("%s has %d offers, down from %d" % (k, b, a))
            pa = sum(1 for o in old_e[k].get("offers") or [] if o.get("price") is not None)
            pb = sum(1 for o in e.get("offers") or [] if o.get("price") is not None)
            if pb < pa:
                found.append("%s has %d priced offers, down from %d" % (k, pb, pa))
    if found and not allow_removals:
        return ["Reduced against the last published copy: %s. If this is confirmed, run again with "
                "--allow-removals." % "; ".join(found)], removed
    return [], removed


def inlined_block(html):
    """The JSON text between the page's data markers, or None."""
    a, b = html.find(DATA_START), html.find(DATA_END)
    if a < 0 or b < 0 or b < a:
        return None
    chunk = html[a + len(DATA_START):b]
    m = re.search(r'<script type="application/json" id="rm-data">(.*?)</script>', chunk, re.S)
    if not m:
        return None
    return m.group(1).strip()  # JSON text; "<" is written as \u003c, which json.loads reads back


class BaselineError(Exception):
    pass


def last_published(baseline=None, page=None):
    """(data, where) for the last published copy, or (None, reason)."""
    if baseline:
        try:
            return json.loads(Path(baseline).read_text()), str(baseline)
        except (OSError, ValueError) as e:
            raise BaselineError("cannot read baseline %s: %s" % (baseline, e))
    page = Path(page) if page else PAGE
    if page.exists():
        block = inlined_block(page.read_text())
        if block:
            try:
                return json.loads(block), "tools/restaurant-month/index.html (inlined copy)"
            except ValueError:
                pass
    try:
        out = subprocess.run(["git", "show", "HEAD:data/restaurant-month.json"], cwd=str(REPO),
                             capture_output=True, text=True, check=True).stdout
        return json.loads(out), "git HEAD"
    except Exception:
        pass
    return None, "no earlier copy found"


def check_file(path=DATA, allow_removals=False, baseline=None, quiet=False, page=None):
    """Full check used by the CLI and inline.py. Returns (ok, data)."""
    def say(*a):
        if not quiet:
            print(*a)
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError) as e:
        say("INVALID: cannot read %s: %s" % (path, e))
        return False, None
    errs = validate(data)
    try:
        old, where = last_published(baseline, page)
    except BaselineError as e:
        say("INVALID:", e)
        return False, data
    red, removed = reduction(data, old, allow_removals) if not errs else ([], [])
    for e in errs + red:
        say("INVALID:", e)
    if errs or red:
        return False, data
    if old is None:
        say("note: reduction check skipped (%s)" % where)
    elif allow_removals:
        say("note: --allow-removals given; removed ids: %s" % (", ".join(removed) or "none"))
    counts = ", ".join("%s %d" % (p["id"], sum(1 for e in data["entries"] if e["promotion_id"] == p["id"]))
                       for p in data["promotions"])
    say("valid: %d entries (%s; compared with %s)" % (len(data["entries"]), counts, where))
    return True, data


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("path", nargs="?", default=str(DATA))
    ap.add_argument("--allow-removals", action="store_true")
    ap.add_argument("--baseline", help="compare against this file instead of the inlined copy or git HEAD")
    a = ap.parse_args(argv)
    ok, _ = check_file(a.path, a.allow_removals, a.baseline)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
