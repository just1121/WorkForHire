#!/usr/bin/env python3
"""Validate data/bigdays/*.json. Spec: tools/BUSY-DAYS.md (v1.2); data rule from PLAN.md 2026-10-31 12:20.

Never publish an empty, invalid, or unexpectedly reduced dataset. This checks the schema of
events.json, school_breaks.json, weather_points.json and holidays.json (which must equal the rules in
bigdays.py), and refuses a reduction against the last published copy unless --allow-removals is given.

A reduction is any of: an event id that disappeared; a confirmed event that is no longer confirmed;
fewer events; verified_through moved earlier; a district that disappeared; fewer breaks for a district.

The last published copy is, in order: --baseline PATH (a page or a JSON file holding the inlined
block); the block inlined in tools/bigdays/index.html (what visitors are served); `git show HEAD:` of
the data files. If none exists the reduction check is skipped (and said so).

  python3 tools/bigdays/validate.py
  python3 tools/bigdays/validate.py --allow-removals     # a confirmed removal or cancellation
Exit status 0 when valid, 1 when not.
"""
import argparse
import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import bigdays  # noqa: E402

REPO = bigdays.REPO
DATA_DIR = bigdays.DATA_DIR
PAGE = HERE / "index.html"
FILES = {"events": "events.json", "school_breaks": "school_breaks.json",
         "holidays": "holidays.json", "weather_points": "weather_points.json"}
DATA_START = "<!-- bigdays-data:start -->"
DATA_END = "<!-- bigdays-data:end -->"

COUNTIES = ("napa", "sonoma", "both")
STATUSES = ("confirmed", "tentative", "canceled")
KINDS = ("thanksgiving", "winter", "spring")
EVENT_KEYS = ("id", "name", "county", "start", "end", "status", "source_url", "last_checked")
ID_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def _date(v):
    try:
        return isinstance(v, str) and dt.date.fromisoformat(v).isoformat() == v
    except ValueError:
        return False


def _url(v):
    return isinstance(v, str) and re.match(r"^https://[^\s]+$", v) is not None


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _text(v):
    return isinstance(v, str) and v.strip() != "" and "—" not in v


def load_all(folder=DATA_DIR):
    return {k: json.loads((Path(folder) / f).read_text()) for k, f in FILES.items()}


def check_events(doc, today):
    p = []
    if not isinstance(doc, dict) or not isinstance(doc.get("meta"), dict):
        return ["events.json: no meta"]
    m = doc["meta"]
    for k in ("verified_through", "last_reviewed"):
        if not _date(m.get(k)):
            p.append("events.json: meta.%s is not a YYYY-MM-DD date" % k)
    if _date(m.get("last_reviewed")) and m["last_reviewed"] > today:
        p.append("events.json: meta.last_reviewed is in the future")
    evs = doc.get("events")
    if not isinstance(evs, list) or not evs:
        return p + ["events.json: no events (an empty list is never published)"]
    seen = set()
    for i, e in enumerate(evs):
        where = "events.json: event %d (%s)" % (i, e.get("id") if isinstance(e, dict) else "?")
        if not isinstance(e, dict):
            p.append(where + " is not an object")
            continue
        missing = [k for k in EVENT_KEYS if k not in e]
        if missing:
            p.append(where + " missing " + ", ".join(missing))
            continue
        if not (isinstance(e["id"], str) and ID_RE.match(e["id"])):
            p.append(where + " id must be lower-case words joined by hyphens")
        elif e["id"] in seen:
            p.append(where + " duplicate id")
        seen.add(e["id"])
        if not _text(e["name"]):
            p.append(where + " name is empty or has an em dash")
        if e["county"] not in COUNTIES:
            p.append(where + " county must be one of " + ", ".join(COUNTIES))
        if e["status"] not in STATUSES:
            p.append(where + " status must be one of " + ", ".join(STATUSES))
        if e["start"] is None and e["end"] is None:
            if e["status"] == "confirmed":
                p.append(where + " a confirmed event needs start and end dates")
        elif not (_date(e["start"]) and _date(e["end"])):
            p.append(where + " start and end must both be dates (or both null for a tentative event)")
        elif e["end"] < e["start"]:
            p.append(where + " ends before it starts")
        if not _url(e["source_url"]):
            p.append(where + " source_url must be an https URL")
        if not _date(e["last_checked"]):
            p.append(where + " last_checked is not a date")
        elif e["last_checked"] > today:
            p.append(where + " last_checked is in the future")
        if "note" in e and e["note"] is not None and not _text(e["note"]):
            p.append(where + " note is empty or has an em dash")
    return p


def check_breaks(doc, today):
    p = []
    ds = doc.get("districts") if isinstance(doc, dict) else None
    if not isinstance(ds, list) or not ds:
        return ["school_breaks.json: no districts"]
    if not (isinstance(doc.get("meta"), dict) and _text(doc["meta"].get("school_year"))):
        p.append("school_breaks.json: meta.school_year missing")
    seen = set()
    for dist in ds:
        if not isinstance(dist, dict):
            p.append("school_breaks.json: a district is not an object")
            continue
        did = dist.get("id")
        where = "school_breaks.json: %s" % did
        if not (isinstance(did, str) and re.match(r"^[A-Z]+$", did)) or did in seen:
            p.append(where + ": id must be unique upper-case letters")
            continue
        seen.add(did)
        for k in ("name", "calendar_title"):
            if not _text(dist.get(k)):
                p.append(where + ": missing " + k)
        if not _url(dist.get("source_url")):
            p.append(where + ": source_url must be an https URL")
        if not _date(dist.get("last_checked")) or dist["last_checked"] > today:
            p.append(where + ": last_checked missing or in the future")
        bs = dist.get("breaks")
        if not isinstance(bs, list) or not bs:
            p.append(where + ": no breaks")
            continue
        for b in bs:
            if not isinstance(b, dict):
                p.append(where + ": a break is not an object")
                continue
            if b.get("kind") not in KINDS:
                p.append(where + ": break kind must be one of " + ", ".join(KINDS))
            if not (_text(b.get("tag")) and b["tag"].startswith(did + " ")):
                p.append(where + ": break tag must name the district first (\"%s spring break\")" % did)
            if not _text(b.get("district_label")):
                p.append(where + ": break needs the district's own label")
            if not (_date(b.get("start")) and _date(b.get("end"))) or b["end"] < b["start"]:
                p.append(where + ": break %s has bad dates" % b.get("tag"))
    return p


def check_points(doc):
    p = []
    if not isinstance(doc, dict):
        return ["weather_points.json is not an object"]
    if not _text(doc.get("source")):
        p.append("weather_points.json: source missing")
    if not _num(doc.get("heat_threshold_f")):
        p.append("weather_points.json: heat_threshold_f must be a number")
    if doc.get("days") != bigdays.WEATHER_DAYS:
        p.append("weather_points.json: days must be %d (nothing past day seven)" % bigdays.WEATHER_DAYS)
    pts = doc.get("points")
    if not isinstance(pts, dict) or set(pts) != {"napa", "sonoma"}:
        return p + ["weather_points.json: points must be exactly napa and sonoma"]
    for k, v in pts.items():
        if not isinstance(v, dict) or not _text(v.get("place")):
            p.append("weather_points.json: point %s needs a place" % k)
            continue
        if not _text(v.get("label")) or not _num(v.get("lat")) or not _num(v.get("lon")):
            p.append("weather_points.json: point %s needs label, lat, lon" % k)
        elif not (37.8 <= v["lat"] <= 39.0 and -123.5 <= v["lon"] <= -122.0):
            p.append("weather_points.json: point %s is outside Napa and Sonoma" % k)
    return p


def check_holidays(doc):
    try:
        want = bigdays.holidays_doc(doc["meta"]["years"])
    except (KeyError, TypeError, ValueError):
        return ["holidays.json: missing meta.years"]
    if doc != want:
        return ["holidays.json differs from the rules in bigdays.py; run python3 tools/bigdays/bigdays.py --emit-holidays "
                + " ".join(str(y) for y in doc["meta"]["years"])]
    return []


def schema_problems(data, today=None):
    today = today or bigdays.today_la()
    return (check_events(data["events"], today) + check_breaks(data["school_breaks"], today)
            + check_points(data["weather_points"]) + check_holidays(data["holidays"]))


def reductions(new, old):
    """Reductions of `new` against the published `old` (both {events, school_breaks})."""
    p = []
    oe = {e["id"]: e for e in old["events"]["events"]}
    ne = {e["id"]: e for e in new["events"]["events"]}
    for i in sorted(set(oe) - set(ne)):
        p.append("event %s was removed" % i)
    for i in sorted(set(oe) & set(ne)):
        if oe[i]["status"] == "confirmed" and ne[i]["status"] != "confirmed":
            p.append("event %s is no longer confirmed (%s)" % (i, ne[i]["status"]))
    for i in sorted(set(oe) & set(ne)):
        o, n = oe[i], ne[i]
        if o.get("start") and (not n.get("start") or n["start"] > o["start"] or n["end"] < o["end"]):
            p.append("event %s no longer covers %s to %s" % (i, o["start"], o["end"]))
        if o.get("county") != n.get("county") and n.get("county") != "both":
            p.append("event %s moved from %s to %s" % (i, o.get("county"), n.get("county")))
    if len(ne) < len(oe):
        p.append("fewer events than the published copy (%d < %d)" % (len(ne), len(oe)))
    ov, nv = old["events"]["meta"]["verified_through"], new["events"]["meta"]["verified_through"]
    if nv < ov:
        p.append("verified_through moved earlier (%s < %s)" % (nv, ov))
    od = {x["id"]: x for x in old["school_breaks"]["districts"]}
    nd = {x["id"]: x for x in new["school_breaks"]["districts"]}
    for i in sorted(set(od) - set(nd)):
        p.append("district %s was removed" % i)
    for i in sorted(set(od) & set(nd)):
        if len(nd[i]["breaks"]) < len(od[i]["breaks"]):
            p.append("district %s has fewer breaks (%d < %d)" % (i, len(nd[i]["breaks"]), len(od[i]["breaks"])))
        new_days = {(b["start"], b["end"]) for b in nd[i]["breaks"]}
        for b in od[i]["breaks"]:
            if not any(s0 <= b["start"] and b["end"] <= e0 for s0, e0 in new_days):
                p.append("district %s break %s (%s to %s) shrank or disappeared" % (i, b["tag"], b["start"], b["end"]))
    return p


def inlined(page_text):
    a, b = page_text.find(DATA_START), page_text.find(DATA_END)
    if a < 0 or b < a:
        return None
    m = re.search(r"<script[^>]*>(.*)</script>", page_text[a:b], re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(1).replace("<\\/", "</"))
    except ValueError:
        return None


def git_head():
    out = {}
    for k in ("events", "school_breaks"):
        try:
            r = subprocess.run(["git", "show", "HEAD:data/bigdays/" + FILES[k]], cwd=str(REPO),
                               capture_output=True, text=True, check=True)
            out[k] = json.loads(r.stdout)
        except (OSError, subprocess.CalledProcessError, ValueError):
            return None
    return out


def baseline(path=None):
    """(label, {events, school_breaks}) of the last published copy, or (None, None)."""
    if path:
        text = Path(path).read_text()
        doc = inlined(text)
        if doc is None:
            doc = json.loads(text)
        return "--baseline " + str(path), doc
    if PAGE.exists():
        doc = inlined(PAGE.read_text())
        if doc and "events" in doc and "school_breaks" in doc:
            return "the copy inlined in tools/bigdays/index.html", doc
    doc = git_head()
    if doc:
        return "git HEAD", doc
    return None, None


def check(data, allow_removals=False, baseline_path=None, today=None, quiet=False):
    """(ok, problems, notes)."""
    problems = schema_problems(data, today)
    notes = []
    if problems:
        return False, problems, notes
    label, old = baseline(baseline_path)
    if old is None:
        notes.append("no published copy found (no inlined block, no git HEAD); reduction check skipped")
    else:
        try:
            red = reductions(data, old)
        except (KeyError, TypeError, AttributeError) as e:
            red = ["the published copy (%s) could not be compared: %s" % (label, e)]
        if red and allow_removals:
            notes.append("reductions allowed by --allow-removals: " + "; ".join(red))
        elif red:
            problems = [r + " (against %s); rerun with --allow-removals if confirmed" % label for r in red]
    return not problems, problems, notes


def check_files(allow_removals=False, baseline_path=None):
    try:
        data = load_all()
    except (OSError, ValueError) as e:
        print("bigdays: cannot read data/bigdays: %s" % e, file=sys.stderr)
        return False, None
    ok, problems, notes = check(data, allow_removals, baseline_path)
    for n in notes:
        print("bigdays: " + n, file=sys.stderr)
    for pr in problems:
        print("bigdays: " + pr, file=sys.stderr)
    return ok, data


def main(argv=None):
    ap = argparse.ArgumentParser(description="Validate data/bigdays/*.json.")
    ap.add_argument("--allow-removals", action="store_true")
    ap.add_argument("--baseline")
    a = ap.parse_args(argv)
    ok, data = check_files(a.allow_removals, a.baseline)
    if ok:
        print("bigdays: data/bigdays valid (%d events, %d districts)"
              % (len(data["events"]["events"]), len(data["school_breaks"]["districts"])))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
