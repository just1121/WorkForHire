#!/usr/bin/env python3
"""Farmers market schedule logic. Spec: tools/MARKETS.md (v1.2).

This file mirrors the schedule functions in tools/markets/markets.js line for line, so the rules can be
unit tested in Python (tests/test_markets.py) and in Node (tests/markets.test.js) against the same cases.
Change one, change the other.

All date and time math is in America/Los_Angeles. A market's day is a plain calendar date; times are
"HH:MM" strings compared as text. No LLM, no network.
"""
import datetime as dt
import sys

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    sys.exit("Python 3.9 or newer is required (zoneinfo).")

TZ = ZoneInfo("America/Los_Angeles")
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
WEEKDAY_NAMES = {"Mon": "Monday", "Tue": "Tuesday", "Wed": "Wednesday", "Thu": "Thursday",
                 "Fri": "Friday", "Sat": "Saturday", "Sun": "Sunday"}
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
          "October", "November", "December"]
ORDINALS = {1: "First", 2: "Second", 3: "Third", 4: "Fourth", 5: "Fifth"}
WEEK_DAYS = 7          # "this week" is today plus the next six days
STALE_DAYS = 45        # schedule_checked older than this shows the confirm-hours line

# State keys -> visitor label. Labels are the spec's words; the page shows text with every color.
STATES = {
    "today": "Scheduled today",
    "week": "Scheduled this week",
    "season": "In season, not this week",
    "closed": "Closed for season",
    "unconfirmed": "Schedule not yet confirmed",
}


# ---------------------------------------------------------------- dates

def la_now(instant):
    """(date, "HH:MM") in Los Angeles for an aware datetime (any zone)."""
    local = instant.astimezone(TZ)
    return local.date(), local.strftime("%H:%M")


def parse_date(s):
    return dt.date.fromisoformat(s) if s else None


def mmdd(d):
    return "%02d-%02d" % (d.month, d.day)


def weekday_key(d):
    return WEEKDAYS[d.weekday()]


# ---------------------------------------------------------------- rules

def in_season(session, d, valid_through):
    """True when date d falls inside the session's season (ignores weekday)."""
    if session["recurrence"] == "confirm_each_year":
        vt = parse_date(valid_through)
        if vt is None or d > vt:
            return False
    dates = session.get("dates")
    if dates:
        return min(dates) <= d.isoformat() <= max(dates)
    ss, se = session.get("season_start"), session.get("season_end")
    if not ss or not se:
        return True
    md = mmdd(d)
    if ss <= se:
        return ss <= md <= se
    return md >= ss or md <= se          # season wraps the new year (Nov to Apr)


def occurs_on(session, d, valid_through):
    """True when the session is scheduled on date d."""
    if not in_season(session, d, valid_through):
        return False
    dates = session.get("dates")
    if dates:
        return d.isoformat() in dates
    if weekday_key(d) != session["weekday"]:
        return False
    nth = session.get("nth_of_month")
    if nth:
        return (d.day - 1) // 7 + 1 == nth
    return True


def exception_on(market, d):
    """The exception record for date d, or None."""
    iso = d.isoformat()
    for e in market.get("exceptions") or []:
        if e["date"] == iso:
            return e
    return None


def sessions_on(market, d):
    """[(session, exception_or_None)] scheduled on d, by start time. A "closed" exception removes the
    day; a "moved" exception keeps the row so its note can be shown, but a moved day never counts as
    scheduled at the regular hours (see counts())."""
    exc = exception_on(market, d)
    if exc and exc["status"] == "closed":
        return []
    vt = market.get("schedule_valid_through")
    found = [(s, exc) for s in market["sessions"] if occurs_on(s, d, vt)]
    return sorted(found, key=lambda p: p[0]["start"])


def counts(exc):
    """True when a session on that day counts as scheduled at its regular hours."""
    return not (exc and exc["status"] == "moved")


def state(market, today, hm):
    """One of STATES' keys for the market at local date `today`, local time `hm`."""
    week = False
    for offset in range(WEEK_DAYS):
        d = today + dt.timedelta(days=offset)
        for s, exc in sessions_on(market, d):
            if not counts(exc):
                continue
            if offset == 0:
                if hm < s["end"]:
                    return "today"
            else:
                week = True
    if week:
        return "week"
    vt = market.get("schedule_valid_through")
    if any(in_season(s, today, vt) for s in market["sessions"]):
        return "season"
    if any(s["recurrence"] == "confirm_each_year" for s in market["sessions"]):
        v = parse_date(vt)
        if v is None or today > v:
            return "unconfirmed"
    return "closed"


def state_label(key, today):
    if key == "unconfirmed":
        return "Schedule not yet confirmed for %d" % today.year
    return STATES[key]


def scheduled_now(market, today, hm):
    return any(counts(exc) and s["start"] <= hm < s["end"] for s, exc in sessions_on(market, today))


def year_round(market):
    return any(s["recurrence"] == "year_round" for s in market["sessions"])


def has_weekday(market, wd):
    for s in market["sessions"]:
        if s.get("dates"):
            if any(weekday_key(parse_date(x)) == wd for x in s["dates"]):
                return True
        elif s["weekday"] == wd:
            return True
    return False


def this_week(markets, today, hm):
    """[(date, [(market, session, exception)])] for today and the next six days. Today's sessions
    drop out once their end time has passed. A moved day stays listed (the page shows its note
    instead of the regular hours)."""
    out = []
    for offset in range(WEEK_DAYS):
        d = today + dt.timedelta(days=offset)
        rows = []
        for m in markets:
            for s, exc in sessions_on(m, d):
                if offset == 0 and counts(exc) and hm >= s["end"]:
                    continue
                rows.append((m, s, exc))
        rows.sort(key=lambda r: (r[1]["start"], r[0]["name"]))
        out.append((d, rows))
    return out


def is_stale(checked, today):
    c = parse_date(checked)
    return c is None or (today - c).days > STALE_DAYS


# ---------------------------------------------------------------- words

def fmt_time(hm):
    h, m = int(hm[:2]), int(hm[3:])
    if h == 12 and m == 0:
        return "noon"
    suffix = "am" if h < 12 else "pm"
    h12 = h % 12 or 12
    return ("%d %s" % (h12, suffix)) if m == 0 else ("%d:%02d %s" % (h12, m, suffix))


def fmt_date(iso, with_year=False):
    d = parse_date(iso)
    s = "%s %d" % (MONTHS[d.month - 1], d.day)
    return s + (", %d" % d.year) if with_year else s


def _month_end(month):
    return [31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1]


def fmt_season(ss, se):
    m1, d1 = int(ss[:2]), int(ss[3:])
    m2, d2 = int(se[:2]), int(se[3:])
    if d1 == 1 and d2 >= _month_end(m2) - (1 if m2 == 2 else 0):
        if m1 == m2:
            return MONTHS[m1 - 1]
        return "%s through %s" % (MONTHS[m1 - 1], MONTHS[m2 - 1])
    return "%s %d to %s %d" % (MONTHS[m1 - 1], d1, MONTHS[m2 - 1], d2)


YEAR_NOT_GIVEN = " (year not given by CDFA)"


def describe_session(session, valid_through=None, year_inferred=False):
    """One session in plain words, e.g. "Saturdays, 8 am to noon, year-round". With year_inferred
    (the market's season_year_inferred), a confirm_each_year year is not printed as fact."""
    hours = "%s to %s" % (fmt_time(session["start"]), fmt_time(session["end"]))
    dates = session.get("dates")
    if dates:
        listed = [fmt_date(x) for x in dates]
        if len(listed) > 1:
            listed = ", ".join(listed[:-1]) + " and " + listed[-1]
        else:
            listed = listed[0]
        if year_inferred:
            return "%s on %s%s" % (hours, listed, YEAR_NOT_GIVEN)
        return "%s on %s, %s" % (hours, listed, dates[-1][:4])
    day = WEEKDAY_NAMES[session["weekday"]]
    nth = session.get("nth_of_month")
    when = ("%s %s of the month" % (ORDINALS[nth], day)) if nth else day + "s"
    if session["recurrence"] == "year_round":
        season = "year-round"
    else:
        season = fmt_season(session["season_start"], session["season_end"])
        if session["recurrence"] == "confirm_each_year":
            if year_inferred:
                season += YEAR_NOT_GIVEN
            elif valid_through:
                season += ", " + valid_through[:4]
    return "%s, %s, %s" % (when, hours, season)
