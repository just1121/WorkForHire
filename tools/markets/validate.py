#!/usr/bin/env python3
"""Validate data/markets.json. Spec: tools/MARKETS.md (v1.2), data rule from PLAN.md 2026-10-31 12:20.

Never publish an empty, invalid, or unexpectedly reduced dataset. This checks the schema and refuses a
reduction against the last published copy unless --allow-removals is given (a market can close for
good; confirm that by hand, then pass the flag). A reduction is any of: a market id that disappeared,
fewer markets in any county, or fewer sessions on a market.

The last published copy is, in order: --baseline PATH; the block inlined in tools/markets/index.html
(what visitors are served); `git show HEAD:data/markets.json`. If none exists the reduction check is
skipped (and said so).

  python3 tools/markets/validate.py                      # validate data/markets.json
  python3 tools/markets/validate.py --allow-removals     # a confirmed closure removed a market
Exit status 0 when valid, 1 when not.
"""
import argparse
import datetime as dt
from zoneinfo import ZoneInfo
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
DATA = REPO / "data" / "markets.json"
PAGE = HERE / "index.html"

COUNTIES = ("Napa", "Sonoma", "Marin", "Mendocino", "Lake")
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
RECURRENCES = ("year_round", "annual_season", "confirm_each_year")
MARKET_KEYS = ("id", "name", "county", "city", "address", "lat", "lon", "geo_source", "sessions",
               "exceptions", "website", "instagram", "notes", "source", "schedule_source",
               "schedule_checked", "schedule_valid_through", "season_year_inferred", "last_checked")
SESSION_KEYS = ("recurrence", "weekday", "nth_of_month", "dates", "start", "end", "season_start",
                "season_end")
EXCEPTION_KEYS = ("date", "status", "note", "source")
# A generous box around the five counties; a pin outside it is a typo, not a market.
LAT_RANGE = (37.80, 40.05)
LON_RANGE = (-124.10, -122.00)

DATA_START = "<!-- markets-data:start -->"
DATA_END = "<!-- markets-data:end -->"

ID_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
HM_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
MMDD_RE = re.compile(r"^(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$")
HANDLE_RE = re.compile(r"^[A-Za-z0-9._]{1,30}$")


def _date(v):
    try:
        return isinstance(v, str) and dt.date.fromisoformat(v).isoformat() == v
    except ValueError:
        return False


def _mmdd(v):
    if not (isinstance(v, str) and MMDD_RE.match(v)):
        return False
    try:
        dt.date(2024, int(v[:2]), int(v[3:]))  # leap year, so 02-29 is allowed
        return True
    except ValueError:
        return False


def _url(v):
    return isinstance(v, str) and re.match(r"^https?://[^\s]+$", v) is not None


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def today_la():
    return dt.datetime.now(ZoneInfo("America/Los_Angeles")).date()


def check_session(s, where, market):
    errs = []
    if not isinstance(s, dict):
        return ["%s: not an object" % where]
    if set(s) != set(SESSION_KEYS):
        errs.append("%s: keys must be exactly %s" % (where, ", ".join(SESSION_KEYS)))
        return errs
    rec = s["recurrence"]
    if rec not in RECURRENCES:
        errs.append("%s: recurrence %r" % (where, rec))
    if s["weekday"] not in WEEKDAYS and not (s["weekday"] is None and s["dates"]):
        errs.append("%s: weekday %r" % (where, s["weekday"]))
    if not (isinstance(s["start"], str) and HM_RE.match(s["start"])):
        errs.append("%s: start %r" % (where, s["start"]))
    elif not (isinstance(s["end"], str) and HM_RE.match(s["end"])):
        errs.append("%s: end %r" % (where, s["end"]))
    elif s["start"] >= s["end"]:
        errs.append("%s: start must be before end" % where)
    nth = s["nth_of_month"]
    if nth is not None and (not isinstance(nth, int) or isinstance(nth, bool) or not 1 <= nth <= 5):
        errs.append("%s: nth_of_month %r" % (where, nth))
    ss, se = s["season_start"], s["season_end"]
    if (ss is None) != (se is None):
        errs.append("%s: season_start and season_end go together" % where)
    elif ss is not None and not (_mmdd(ss) and _mmdd(se)):
        errs.append("%s: season bounds must be MM-DD" % where)
    dates = s["dates"]
    if dates is not None:
        if not (isinstance(dates, list) and dates and all(_date(x) for x in dates)):
            errs.append("%s: dates must be a non-empty list of YYYY-MM-DD" % where)
        else:
            if dates != sorted(set(dates)):
                errs.append("%s: dates must be sorted and unique" % where)
            if s["weekday"] in WEEKDAYS:
                bad = [x for x in dates if WEEKDAYS[dt.date.fromisoformat(x).weekday()] != s["weekday"]]
                if bad:
                    errs.append("%s: dates not on %s: %s" % (where, s["weekday"], ", ".join(bad)))
            if rec != "confirm_each_year":
                errs.append("%s: a date list is confirm_each_year" % where)
            if ss is not None or nth is not None:
                errs.append("%s: a date list has no season or nth_of_month" % where)
            vt = market.get("schedule_valid_through")
            if _date(vt) and vt < max(dates):
                errs.append("%s: schedule_valid_through %s is before the last listed date %s" % (where, vt, max(dates)))
    if rec == "year_round" and ss is not None:
        errs.append("%s: year_round has no season bounds" % where)
    if rec == "annual_season" and ss is None:
        errs.append("%s: annual_season needs season bounds" % where)
    if rec == "confirm_each_year":
        if ss is None and not dates:
            errs.append("%s: confirm_each_year needs season bounds or dates" % where)
        if not _date(market.get("schedule_valid_through")):
            errs.append("%s: confirm_each_year needs the market's schedule_valid_through" % where)
    return errs


def check_market(m, i):
    where = "markets[%d]" % i
    if not isinstance(m, dict):
        return ["%s: not an object" % where]
    if isinstance(m.get("id"), str):
        where = "markets[%d] (%s)" % (i, m["id"])
    missing = [k for k in MARKET_KEYS if k not in m]
    extra = [k for k in m if k not in MARKET_KEYS]
    if missing or extra:
        return ["%s: missing %s; unexpected %s" % (where, missing or "none", extra or "none")]
    errs = []
    if not (isinstance(m["id"], str) and ID_RE.match(m["id"])):
        errs.append("%s: id must be a lowercase slug" % where)
    for k in ("name", "city", "address"):
        if not (isinstance(m[k], str) and m[k].strip()):
            errs.append("%s: %s must be a non-empty string" % (where, k))
    if m["county"] not in COUNTIES:
        errs.append("%s: county %r" % (where, m["county"]))
    if not (_num(m["lat"]) and LAT_RANGE[0] <= m["lat"] <= LAT_RANGE[1]):
        errs.append("%s: lat %r outside the five counties" % (where, m["lat"]))
    if not (_num(m["lon"]) and LON_RANGE[0] <= m["lon"] <= LON_RANGE[1]):
        errs.append("%s: lon %r outside the five counties" % (where, m["lon"]))
    if m["geo_source"] not in ("census", "manual"):
        errs.append("%s: geo_source must be census or manual" % where)
    if not (m["schedule_source"] == "cdfa" or _url(m["schedule_source"])):
        errs.append("%s: schedule_source must be \"cdfa\" or the market's URL" % where)
    for k in ("schedule_checked", "last_checked"):
        if not _date(m[k]):
            errs.append("%s: %s must be YYYY-MM-DD" % (where, k))
        elif m[k] > today_la().isoformat():
            errs.append("%s: %s %s is in the future" % (where, k, m[k]))
    if not isinstance(m["season_year_inferred"], bool):
        errs.append("%s: season_year_inferred must be true or false" % where)
    if m["schedule_valid_through"] is not None and not _date(m["schedule_valid_through"]):
        errs.append("%s: schedule_valid_through must be YYYY-MM-DD or null" % where)
    if m["website"] is not None and not _url(m["website"]):
        errs.append("%s: website must be a URL or null" % where)
    if m["instagram"] is not None and not (isinstance(m["instagram"], str) and HANDLE_RE.match(m["instagram"])):
        errs.append("%s: instagram is the handle only (no @, no URL) or null" % where)
    if m["notes"] is not None and not isinstance(m["notes"], str):
        errs.append("%s: notes must be a string or null" % where)
    src = m["source"]
    if not (isinstance(src, dict) and _url(src.get("url")) and _date(src.get("as_of"))
            and isinstance(src.get("listed_name"), str)):
        errs.append("%s: source needs url, as_of and listed_name (the CDFA listing)" % where)
    sessions = m["sessions"]
    if not (isinstance(sessions, list) and sessions):
        errs.append("%s: sessions must be a non-empty list" % where)
    else:
        for j, s in enumerate(sessions):
            errs += check_session(s, "%s.sessions[%d]" % (where, j), m)
    exc = m["exceptions"]
    if not isinstance(exc, list):
        errs.append("%s: exceptions must be a list" % where)
    else:
        for j, e in enumerate(exc):
            w = "%s.exceptions[%d]" % (where, j)
            if not isinstance(e, dict) or set(e) != set(EXCEPTION_KEYS):
                errs.append("%s: keys must be exactly %s" % (w, ", ".join(EXCEPTION_KEYS)))
                continue
            if not _date(e["date"]):
                errs.append("%s: date" % w)
            if e["status"] not in ("closed", "moved"):
                errs.append("%s: status must be closed or moved" % w)
            if not (isinstance(e["note"], str) and e["note"].strip()):
                errs.append("%s: note is required" % w)
            if not (e["source"] == "cdfa" or _url(e["source"])):
                errs.append("%s: source must be \"cdfa\" or a URL" % w)
    return errs


def validate(data):
    """List of problems; empty means valid."""
    if not isinstance(data, dict):
        return ["top level must be an object"]
    errs = []
    if data.get("schema_version") != 1:
        errs.append("schema_version must be 1")
    src = data.get("source")
    if not (isinstance(src, dict) and _url(src.get("url")) and _date(src.get("as_of")) and _date(src.get("checked"))):
        errs.append("source needs url, as_of and checked")
    markets = data.get("markets")
    if not isinstance(markets, list) or not markets:
        return errs + ["markets must be a non-empty list"]
    for i, m in enumerate(markets):
        errs += check_market(m, i)
    ids = [m.get("id") for m in markets if isinstance(m, dict)]
    dupes = sorted({x for x in ids if ids.count(x) > 1 and x is not None})
    if dupes:
        errs.append("duplicate ids: %s" % ", ".join(map(str, dupes)))
    counties = {m.get("county") for m in markets if isinstance(m, dict)}
    for c in COUNTIES:
        if c not in counties:
            errs.append("no markets in %s (an empty county is never published)" % c)
    return errs


def reduction(new, old, allow_removals=False):
    """(problems, removed_ids). Against the last published copy, each of these is a reduction: an id
    that disappeared, fewer markets in a county, fewer sessions on a market. --allow-removals overrides."""
    if old is None:
        return [], []
    old_m = {m.get("id"): m for m in old.get("markets", [])}
    new_m = {m.get("id"): m for m in new.get("markets", [])}
    removed = [x for x in old_m if x not in new_m]
    found = []
    if removed:
        found.append("markets removed: %s" % ", ".join(map(str, removed)))
    for c in COUNTIES:
        a = sum(1 for m in old_m.values() if m.get("county") == c)
        b = sum(1 for m in new_m.values() if m.get("county") == c)
        if b < a:
            found.append("%s has %d markets, down from %d" % (c, b, a))
    for k, m in new_m.items():
        if k in old_m:
            a, b = len(old_m[k].get("sessions") or []), len(m.get("sessions") or [])
            if b < a:
                found.append("%s has %d sessions, down from %d" % (k, b, a))
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
    m = re.search(r'<script type="application/json" id="markets-data">(.*?)</script>', chunk, re.S)
    if not m:
        return None
    return m.group(1).replace("<\\/", "</").strip()


def last_published(baseline=None):
    """(data, where) for the last published copy, or (None, reason)."""
    if baseline:
        return json.loads(Path(baseline).read_text()), str(baseline)
    if PAGE.exists():
        block = inlined_block(PAGE.read_text())
        if block:
            try:
                return json.loads(block), "tools/markets/index.html (inlined copy)"
            except ValueError:
                pass
    try:
        out = subprocess.run(["git", "show", "HEAD:data/markets.json"], cwd=str(REPO),
                             capture_output=True, text=True, check=True).stdout
        return json.loads(out), "git HEAD"
    except Exception:
        pass
    return None, "no earlier copy found"


def check_file(path=DATA, allow_removals=False, baseline=None, quiet=False):
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
    old, where = last_published(baseline)
    red, removed = reduction(data, old, allow_removals) if not errs else ([], [])
    for e in errs + red:
        say("INVALID:", e)
    if errs or red:
        return False, data
    if old is None:
        say("note: reduction check skipped (%s)" % where)
    elif allow_removals:
        say("note: --allow-removals given; removed ids: %s" % (", ".join(removed) or "none"))
    say("valid: %d markets (compared with %s)" % (len(data["markets"]), where))
    return True, data


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("path", nargs="?", default=str(DATA))
    ap.add_argument("--allow-removals", action="store_true")
    ap.add_argument("--baseline", help="compare against this file instead of git HEAD")
    a = ap.parse_args(argv)
    ok, _ = check_file(a.path, a.allow_removals, a.baseline)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
