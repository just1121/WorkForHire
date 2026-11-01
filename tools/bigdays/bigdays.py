#!/usr/bin/env python3
"""Big days calendar: the date logic. Spec: tools/BUSY-DAYS.md (v1.2).

Mirrors tools/bigdays/bigdays.js one for one; both run the shared cases in tests/fixtures/cases.json.
Change one, change the other. Stdlib only. All dates are "YYYY-MM-DD" strings in America/Los_Angeles.

  python3 tools/bigdays/bigdays.py --emit-holidays 2026 2027   # rewrite data/bigdays/holidays.json
"""
import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    sys.exit("Python 3.9 or newer is required (zoneinfo).")

TZ = ZoneInfo("America/Los_Angeles")
HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
DATA_DIR = REPO / "data" / "bigdays"

WEEKS = 8
WINDOW_DAYS = WEEKS * 7
HARVEST = ("08-15", "10-31")          # month-day, inclusive, every year
WEATHER_DAYS = 7                      # nothing past day seven (today is day one)
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
          "October", "November", "December"]

# Fixed holiday rules. kind "federal" = 5 U.S.C. 6103; "california" = Cesar Chavez Day (Gov. Code 6700) and
# the day after Thanksgiving (Gov. Code 19853). rule: ("fixed", month, day), ("nth", month, weekday 0=Mon, n)
# with n = -1 for the last one, or ("nth_after", month, weekday, n, days) = that nth weekday plus `days`.
HOLIDAY_RULES = [
    ("New Year's Day", "federal", ("fixed", 1, 1)),
    ("Martin Luther King Jr. Day", "federal", ("nth", 1, 0, 3)),
    ("Washington's Birthday", "federal", ("nth", 2, 0, 3)),
    ("Cesar Chavez Day", "california", ("fixed", 3, 31)),
    ("Memorial Day", "federal", ("nth", 5, 0, -1)),
    ("Juneteenth", "federal", ("fixed", 6, 19)),
    ("Independence Day", "federal", ("fixed", 7, 4)),
    ("Labor Day", "federal", ("nth", 9, 0, 1)),
    ("Columbus Day", "federal", ("nth", 10, 0, 2)),
    ("Veterans Day", "federal", ("fixed", 11, 11)),
    ("Thanksgiving Day", "federal", ("nth", 11, 3, 4)),
    ("Day after Thanksgiving", "california", ("nth_after", 11, 3, 4, 1)),
    ("Christmas Day", "federal", ("fixed", 12, 25)),
]


# ------------------------------------------------------------------ dates

def today_la(now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    return now.astimezone(TZ).date().isoformat()


def d(iso):
    return dt.date.fromisoformat(iso)


def add_days(iso, n):
    return (d(iso) + dt.timedelta(days=n)).isoformat()


def weekday(iso):
    """0 = Monday .. 6 = Sunday."""
    return d(iso).weekday()


def window(start, days=WINDOW_DAYS):
    return [add_days(start, i) for i in range(days)]


def grid_rows(start, days=WINDOW_DAYS):
    """Weeks Sunday to Saturday; dates outside the window are None."""
    dates = window(start, days)
    lead = (weekday(start) + 1) % 7          # Sunday-first column of the start date
    cells = [None] * lead + dates
    while len(cells) % 7:
        cells.append(None)
    return [cells[i:i + 7] for i in range(0, len(cells), 7)]


def fmt_date(iso, with_year=False, with_weekday=False):
    x = d(iso)
    s = "%s %d" % (MONTHS[x.month - 1], x.day)
    if with_year:
        s += ", %d" % x.year
    if with_weekday:
        s = WEEKDAYS[x.weekday()] + ", " + s
    return s


def in_range(iso, start, end):
    return start is not None and end is not None and start <= iso <= end


# ------------------------------------------------------------------ holidays

def rule_date(year, rule):
    if rule[0] == "fixed":
        return dt.date(year, rule[1], rule[2]).isoformat()
    if rule[0] == "nth_after":
        return add_days(rule_date(year, ("nth",) + tuple(rule[1:4])), rule[4])
    _, month, wd, n = rule
    if n > 0:
        first = dt.date(year, month, 1)
        off = (wd - first.weekday()) % 7
        return (first + dt.timedelta(days=off + 7 * (n - 1))).isoformat()
    nxt = dt.date(year + (month == 12), month % 12 + 1, 1)
    last = nxt - dt.timedelta(days=1)
    return (last - dt.timedelta(days=(last.weekday() - wd) % 7)).isoformat()


def observed(iso, kind):
    """Day off for a holiday. Federal (OPM): Saturday -> Friday before, Sunday -> Monday after.
    California (Gov. Code 6700): Sunday -> Monday after; Saturday is not moved."""
    wd = weekday(iso)
    if wd == 6:
        return add_days(iso, 1)
    if wd == 5 and kind == "federal":
        return add_days(iso, -1)
    return iso


def holidays(year):
    """Holidays whose actual date falls in `year`, sorted by actual date:
    [{name, kind, date, observed}] where observed may fall in the neighboring year."""
    out = []
    for name, kind, rule in HOLIDAY_RULES:
        actual = rule_date(year, rule)
        out.append({"name": name, "kind": kind, "date": actual, "observed": observed(actual, kind)})
    return sorted(out, key=lambda h: h["date"])


def three_day_weekends(hols):
    """A three-day weekend is a holiday whose day off (observed date) is a Monday (Sat, Sun, Mon) or a
    Friday (Fri, Sat, Sun). Thanksgiving week is the exception: with the day after Thanksgiving off
    (California), Thursday through Sunday is a four-day holiday weekend. Other midweek holidays make none."""
    out = []
    for h in hols:
        o = h["observed"]
        wd = weekday(o)
        if h["name"] == "Day after Thanksgiving":
            days = [add_days(o, -1), o, add_days(o, 1), add_days(o, 2)]
        elif wd == 0:
            days = [add_days(o, -2), add_days(o, -1), o]
        elif wd == 4:
            days = [o, add_days(o, 1), add_days(o, 2)]
        else:
            continue
        out.append({"holiday": h["name"], "dates": days})
    return out


def holidays_doc(years):
    ys = {str(y) for y in years}
    # include a neighbor-year holiday whose day off lands inside the years (New Year's Day 2028 is
    # observed Friday, December 31, 2027)
    span = range(min(years) - 1, max(years) + 2)
    hols = [h for y in span for h in holidays(y) if h["date"][:4] in ys or h["observed"][:4] in ys]
    return {
        "meta": {
            "years": list(years),
            "generated_by": "python3 tools/bigdays/bigdays.py --emit-holidays " + " ".join(str(y) for y in years),
            "counts": "U.S. federal holidays (5 U.S.C. 6103) plus California's Cesar Chavez Day (March 31, Gov. Code 6700) and the day after Thanksgiving (Gov. Code 19853).",
            "observed_rule": "Federal: Saturday holidays are observed the Friday before, Sunday holidays the Monday after. California holidays: Sunday is observed the Monday after; Saturday is not moved (the day after Thanksgiving is always a Friday).",
            "three_day_weekend_rule": "An observed holiday on a Monday makes Saturday, Sunday and Monday a holiday weekend; on a Friday, Friday, Saturday and Sunday. Thanksgiving week: Thursday through Sunday (the day after Thanksgiving is a California holiday). Other midweek holidays make none.",
        },
        "holidays": hols,
        "three_day_weekends": three_day_weekends(hols),
    }


def holiday_tags(iso):
    """Text tags for holidays on `iso` (computed by rule, any year)."""
    y = int(iso[:4])
    hols = holidays(y - 1) + holidays(y) + holidays(y + 1)
    tags = []
    for h in hols:
        if h["observed"] == iso and h["date"] != iso:
            tags.append({"type": "holiday", "text": h["name"] + " (observed)"})
        elif h["date"] == iso:
            tags.append({"type": "holiday", "text": h["name"]})
    if any(iso in w["dates"] for w in three_day_weekends(hols)):
        tags.append({"type": "weekend", "text": "holiday weekend"})
    return tags


# ------------------------------------------------------------------ events, breaks, harvest

def is_harvest(iso):
    return HARVEST[0] <= iso[5:] <= HARVEST[1]


def event_tags(iso, events, county):
    """Events on `iso` for the chosen county ("napa" or "sonoma"). Confirmed shows its name, tentative
    shows its name and the word, canceled is hidden, and an event with no dates never shows."""
    tags = []
    for e in events:
        if e["status"] == "canceled" or not in_range(iso, e.get("start"), e.get("end")):
            continue
        if e["county"] not in (county, "both"):
            continue
        text = e["name"] + (" (tentative)" if e["status"] == "tentative" else "")
        tags.append({"type": "event", "text": text})
    return tags


def break_tags(iso, school_breaks):
    tags = []
    for dist in school_breaks["districts"]:
        for b in dist["breaks"]:
            if in_range(iso, b["start"], b["end"]):
                tags.append({"type": "break", "text": b["tag"]})
    return tags


def day_tags(iso, data, county, weather=None):
    """Every tag for one day, in display order. `data` = {events, school_breaks}; `weather` is the
    result of weather_tag() for this day and county (or None)."""
    ev = data["events"]
    tags = event_tags(iso, ev["events"], county)
    tags += holiday_tags(iso)
    tags += break_tags(iso, data["school_breaks"])
    if is_harvest(iso):
        tags.append({"type": "harvest", "text": "harvest"})
    vt = ev["meta"]["verified_through"]
    if iso > vt:
        tags.append({"type": "unchecked", "text": "not yet checked past " + fmt_date(vt, True)})
    elif not tags:
        tags.append({"type": "quiet", "text": "quiet"})
    if weather:
        tags.append(weather)
    return tags


# ------------------------------------------------------------------ weather

RAIN_RE = re.compile(r"rain|showers|thunderstorm|drizzle", re.I)
FOG_RE = re.compile(r"fog", re.I)
CLOUDY_RE = re.compile(r"cloudy|overcast", re.I)


def weather_word(period, threshold):
    """The fixed rule, for one DAYTIME period."""
    short = period.get("shortForecast") or ""
    if RAIN_RE.search(short):
        return "rain"
    t = period.get("temperature")
    if isinstance(t, (int, float)) and not isinstance(t, bool):
        f = t if (period.get("temperatureUnit") or "F") == "F" else t * 9 / 5 + 32
        if f >= threshold:
            return "heat"
    if FOG_RE.search(short):
        return "fog"
    if CLOUDY_RE.search(short):
        return "cloudy"
    return "clear"


def reduce_periods(periods, today, threshold, days=WEATHER_DAYS):
    """{date: word} from NWS 12-hour periods. Only daytime periods count; night is ignored; only
    `today` through day seven. The period's date is its local start date (NWS gives local offsets)."""
    last = add_days(today, days - 1)
    out = {}
    for p in periods:
        if not p.get("isDaytime"):
            continue
        day = str(p.get("startTime", ""))[:10]
        if not (today <= day <= last) or day in out:
            continue
        out[day] = weather_word(p, threshold)
    return out


def weather_tag(iso, today, area):
    """Weather tag for one day. area = {"label", "ok", "words"} or None when nothing could be loaded.
    Days outside today..day seven get nothing. A failed or missing area says "no forecast available";
    a day the file does not cover says the same, except today once its daytime period has passed."""
    if not (today <= iso <= add_days(today, WEATHER_DAYS - 1)):
        return None
    label = (area or {}).get("label")
    if not area or not area.get("ok"):
        return {"type": "weather", "text": "no forecast available" + (", " + label if label else "")}
    word = area["words"].get(iso)
    if word:
        return {"type": "weather", "text": word + ", " + label}
    if iso == today:
        return None
    return {"type": "weather", "text": "no forecast available, " + label}


STALE_HOURS = 48   # an older weather file counts as a failed fetch


def parse_iso(s):
    """Aware datetime; an explicit zone (Z or +hh:mm) is required, as in bigdays.js."""
    s = str(s).strip()
    x = dt.datetime.fromisoformat(s[:-1] + "+00:00" if s.endswith(("Z", "z")) else s)
    if x.tzinfo is None:
        raise ValueError("time has no zone: " + s)
    return x


def weather_area(doc, county, points, today, now):
    """{label, ok, words} for the county's point from data/bigdays/weather.json (doc may be None when
    the fetch failed). `now` is an aware datetime. The label always comes from weather_points.json."""
    label = points["points"][county]["label"]
    try:
        age = now - parse_iso(doc["generated_at"])
        pt = doc["points"][county]
        if age > dt.timedelta(hours=STALE_HOURS) or age < -dt.timedelta(hours=1) or not pt.get("ok"):
            return {"label": label, "ok": False, "words": {}}
        return {"label": label, "ok": True,
                "words": reduce_periods(pt["periods"], today, points["heat_threshold_f"], points.get("days", WEATHER_DAYS))}
    except (KeyError, TypeError, ValueError, AttributeError):
        return {"label": label, "ok": False, "words": {}}


# ------------------------------------------------------------------ files

def load(name):
    return json.loads((DATA_DIR / name).read_text())


def write_json(path, doc):
    Path(path).write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--emit-holidays", nargs="+", type=int, metavar="YEAR")
    a = ap.parse_args(argv)
    if a.emit_holidays:
        write_json(DATA_DIR / "holidays.json", holidays_doc(a.emit_holidays))
        print("wrote data/bigdays/holidays.json for", ", ".join(map(str, a.emit_holidays)))
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
