#!/usr/bin/env python3
"""Big days calendar: daily National Weather Service fetch. Spec: tools/BUSY-DAYS.md (v1.2).

api.weather.gov in, data/bigdays/weather.json out. Stdlib only. No keys, no LLM.
For each point in data/bigdays/weather_points.json: GET /points/{lat},{lon}, then the forecast URL it
names. Only the 12-hour periods' fields the page needs are kept; the page applies the reduction rule
(bigdays.js reducePeriods, mirrored in bigdays.py) against the visitor's today.

  python3 tools/bigdays/fetch_weather.py                 # live, writes data/bigdays/weather.json
  python3 tools/bigdays/fetch_weather.py --fixtures DIR  # offline: DIR/<county>-points.json, DIR/<county>-forecast.json
  python3 tools/bigdays/fetch_weather.py --record DIR    # live, also saves the raw responses to DIR

Environment: BIGDAYS_CONTACT, else SMOKE_WATCH_CONTACT: a contact string (a site URL or an email) for
the User-Agent the National Weather Service asks for. Set it as a repository variable. Nothing personal
is written in this file.

The data rule: the file is never written empty or invalid. If any point fails, nothing is written, the
exit status is nonzero, and the workflow opens one issue. The last good file stays; the page treats a
file older than 48 hours as "no forecast available".
"""
import argparse
import datetime as dt
import json
import os
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import bigdays  # noqa: E402

DEFAULT_OUT = bigdays.DATA_DIR / "weather.json"
MIN_DAYTIME_DATES = 5   # NWS gives about seven; fewer means a truncated or odd response
KEEP = ("startTime", "endTime", "isDaytime", "temperature", "temperatureUnit", "shortForecast")


def contact():
    return os.environ.get("BIGDAYS_CONTACT") or os.environ.get("SMOKE_WATCH_CONTACT") or "contact not set"


class LiveSource:
    def __init__(self, record_dir=None):
        self.headers = {"User-Agent": "BigDaysCalendar/1.0 (%s)" % contact(),
                        "Accept": "application/geo+json"}
        self.record = Path(record_dir) if record_dir else None

    def get(self, key, url):
        last = None
        for _ in range(2):  # one retry
            try:
                req = urllib.request.Request(url, headers=self.headers)
                with urllib.request.urlopen(req, timeout=20) as r:
                    data = json.loads(r.read().decode("utf-8"))
                if self.record:
                    self.record.mkdir(parents=True, exist_ok=True)
                    (self.record / (key + ".json")).write_text(json.dumps(data, indent=1) + "\n")
                return data
            except Exception as e:  # network, HTTP, JSON
                last = e
        raise last


class FixtureSource:
    def __init__(self, folder):
        self.folder = Path(folder)

    def get(self, key, url):
        return json.loads((self.folder / (key + ".json")).read_text())


def trim_periods(forecast):
    periods = forecast["properties"]["periods"]
    if not isinstance(periods, list) or not periods:
        raise ValueError("no periods")
    return [{k: p.get(k) for k in KEEP} for p in periods]


def collect(source, points):
    """(doc points {county: {...}}, errors {county: text})."""
    out, errors = {}, {}
    for county, pt in points["points"].items():
        base = {"label": pt["label"], "lat": pt["lat"], "lon": pt["lon"]}
        try:
            meta = source.get(county + "-points", "https://api.weather.gov/points/%.4f,%.4f" % (pt["lat"], pt["lon"]))
            url = meta["properties"]["forecast"]
            fc = source.get(county + "-forecast", url)
            base.update(ok=True, forecast_url=url,
                        updated=fc["properties"].get("updateTime") or fc["properties"].get("updated"),
                        periods=trim_periods(fc))
        except Exception as e:
            errors[county] = "%s: %s" % (type(e).__name__, str(e)[:200])
            base.update(ok=False, periods=[])
        out[county] = base
    return out, errors


def validate(doc):
    """Problems that must stop a write. Empty list means OK."""
    p = []
    if not isinstance(doc, dict) or not doc.get("generated_at"):
        return ["missing generated_at"]
    pts = doc.get("points")
    if not isinstance(pts, dict) or set(pts) != {"napa", "sonoma"}:
        return ["points must be exactly napa and sonoma"]
    for k, v in pts.items():
        if not v.get("ok"):
            p.append("%s: fetch failed" % k)
            continue
        per = v.get("periods")
        if not isinstance(per, list) or not per:
            p.append("%s: no periods" % k)
            continue
        days = {str(x.get("startTime", ""))[:10] for x in per if x.get("isDaytime") is True}
        if len(days) < MIN_DAYTIME_DATES:
            p.append("%s: only %d daytime dates (at least %d expected)" % (k, len(days), MIN_DAYTIME_DATES))
        for x in per:
            if not bigdays_date(str(x.get("startTime", ""))[:10]):
                p.append("%s: a period has no valid startTime" % k)
                break
            if not isinstance(x.get("shortForecast"), str) or not isinstance(x.get("isDaytime"), bool):
                p.append("%s: a period lacks shortForecast or isDaytime" % k)
                break
            t = x.get("temperature")
            if not isinstance(t, (int, float)) or isinstance(t, bool):
                p.append("%s: a period lacks a numeric temperature" % k)
                break
    return p


def bigdays_date(s):
    try:
        return dt.date.fromisoformat(s).isoformat() == s
    except ValueError:
        return False


def build(source, points, now):
    pts, errors = collect(source, points)
    doc = {
        "version": 1,
        "generated_at": now.astimezone(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "source": "National Weather Service, api.weather.gov",
        "heat_threshold_f": points["heat_threshold_f"],
        "points": pts,
    }
    return doc, errors


def main(argv=None):
    ap = argparse.ArgumentParser(description="Big days: NWS in, data/bigdays/weather.json out.")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--fixtures")
    ap.add_argument("--record")
    ap.add_argument("--now", help="ISO time to stamp (tests)")
    a = ap.parse_args(argv)
    now = bigdays.parse_iso(a.now) if a.now else dt.datetime.now(dt.timezone.utc)
    points = bigdays.load("weather_points.json")
    source = FixtureSource(a.fixtures) if a.fixtures else LiveSource(a.record)
    doc, errors = build(source, points, now)
    problems = validate(doc)
    if problems:
        detail = "; ".join(problems + ["%s: %s" % kv for kv in sorted(errors.items())])
        print("bigdays weather: refusing to write %s: %s" % (a.out, detail), file=sys.stderr)
        return 3
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
    os.replace(tmp, out)
    print("bigdays weather: wrote %s (%s)" % (a.out, ", ".join("%s %d periods" % (k, len(v["periods"])) for k, v in doc["points"].items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
