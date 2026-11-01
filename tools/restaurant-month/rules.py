#!/usr/bin/env python3
"""Restaurant Month rules. Spec: tools/RESTAURANT-MONTH.md (v1.2).

This file mirrors the rule functions in tools/restaurant-month/restaurant-month.js one for one, so the
rules can be unit tested in Python (tests/test_restaurant_month.py) and in Node
(tests/restaurant-month.test.js) against the same cases (tests/fixtures/cases.json). Change one, change
the other.

Dates are "YYYY-MM-DD" strings compared as text; "today" is the Los Angeles date. No LLM, no network.
"""
import datetime as dt
import re
import sys

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    sys.exit("Python 3.9 or newer is required (zoneinfo).")

TZ = ZoneInfo("America/Los_Angeles")
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
          "October", "November", "December"]
WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
WEEKDAY_NAMES = {"mon": "Mondays", "tue": "Tuesdays", "wed": "Wednesdays", "thu": "Thursdays",
                 "fri": "Fridays", "sat": "Saturdays", "sun": "Sundays"}
MEALS = ["lunch", "brunch", "dinner", "any"]
MEAL_LABELS = {"lunch": "Lunch", "brunch": "Brunch", "dinner": "Dinner", "any": "Any meal"}
SERIES = ["napa", "sonoma"]
STALE_DAYS = 7
# Bucket keys in display order -> the spec's labels. Each amount falls in exactly one.
BUCKETS = ["under30", "30to49", "50to79", "80up", "none"]
BUCKET_LABELS = {"under30": "Under $30", "30to49": "$30 to $49", "50to79": "$50 to $79",
                 "80up": "$80 and up", "none": "No fixed price"}
PROMO_RE = re.compile(r"^(napa|sonoma)-(\d{4})$")
LABEL_RE = re.compile(r"^([^:\n]{1,40}):")
MATCH_TEXT = "matches your filters"
ENDED_TEXT = "(ended)"


# ---------------------------------------------------------------- dates

def la_today(instant=None):
    """The Los Angeles calendar date (YYYY-MM-DD) for an aware datetime (default: now)."""
    instant = instant or dt.datetime.now(dt.timezone.utc)
    return instant.astimezone(TZ).date().isoformat()


def add_days(iso, n):
    return (dt.date.fromisoformat(iso) + dt.timedelta(days=n)).isoformat()


def days_between(a, b):
    return (dt.date.fromisoformat(b) - dt.date.fromisoformat(a)).days


def weekday_key(iso):
    return WEEKDAYS[dt.date.fromisoformat(iso).weekday()]


def fmt_date(iso, with_year=False):
    s = "%s %d" % (MONTHS[int(iso[5:7]) - 1], int(iso[8:10]))
    return s + ", " + iso[:4] if with_year else s


def fmt_range(start, end):
    """January 1 to 31, 2027 / February 23 to March 1, 2026 / December 28, 2026 to January 3, 2027."""
    if start == end:
        return fmt_date(start, True)
    if start[:4] != end[:4]:
        return fmt_date(start, True) + " to " + fmt_date(end, True)
    if start[:7] == end[:7]:
        return fmt_date(start) + " to " + str(int(end[8:10])) + ", " + end[:4]
    return fmt_date(start) + " to " + fmt_date(end, True)


# ---------------------------------------------------------------- promotions and states

def series_of(promo_id):
    m = PROMO_RE.match(promo_id or "")
    return m.group(1) if m else None


def year_of(promo_id):
    m = PROMO_RE.match(promo_id or "")
    return int(m.group(2)) if m else None


def display_state(promo, today):
    """upcoming (confirmed, before start), active (start <= today <= end), archived (after end, or
    no confirmed dates yet)."""
    if not promo.get("dates_confirmed") or not promo.get("start") or not promo.get("end"):
        return "archived"
    if today < promo["start"]:
        return "upcoming"
    if today <= promo["end"]:
        return "active"
    return "archived"


def series_view(promotions, series, today):
    """What one organizer's section shows today, or None if the series has no promotion.

    current: the active season, else the next upcoming one, else the latest year. state: its state.
    reference: for archived, the most recent confirmed season that has ended (its entries are listed
    "for reference"); None when there is none. next_year: the year the banner promises."""
    mine = [p for p in promotions if series_of(p["id"]) == series]
    if not mine:
        return None
    # An active season wins, then the next upcoming one, then the latest year (archived).
    active = [p for p in mine if display_state(p, today) == "active"]
    upcoming = [p for p in mine if display_state(p, today) == "upcoming"]
    if active:
        current = max(active, key=lambda p: p["start"])
    elif upcoming:
        current = min(upcoming, key=lambda p: p["start"])
    else:
        current = max(mine, key=lambda p: year_of(p["id"]))
    state = display_state(current, today)
    reference = None
    if state == "archived":
        past = [p for p in mine if p.get("dates_confirmed") and p.get("end") and p["end"] < today]
        if past:
            reference = max(past, key=lambda p: p["end"])
    if state == "archived" and current.get("dates_confirmed") and current.get("end") and current["end"] < today:
        next_year = year_of(current["id"]) + 1
    elif state == "archived":
        next_year = max(year_of(current["id"]), int(today[:4]))   # unconfirmed: never a past year
    else:
        next_year = year_of(current["id"])
    return {"series": series, "current": current, "state": state, "reference": reference,
            "next_year": next_year}


def countdown_days(today, start):
    return days_between(today, start)


def countdown_text(today, start):
    n = countdown_days(today, start)
    if n == 1:
        return "Starts tomorrow."
    return "Starts in %d days." % n


def banner_text(view):
    """The archived banner. A season whose dates are confirmed and still ahead is Upcoming (header
    with dates and countdown), so an archived section never has confirmed future dates to show."""
    if view["state"] != "archived":
        return None
    return "%s returns: %d dates to be announced." % (view["current"]["name"], view["next_year"])


def reference_label(promo):
    return "%d participants, for reference" % year_of(promo["id"])


def confirmed_label(promo):
    return "Confirmed for %d" % year_of(promo["id"])


# ---------------------------------------------------------------- prices and offers

def price_bucket(price):
    if price is None:
        return "none"
    if price < 30:
        return "under30"
    if price < 50:
        return "30to49"
    if price < 80:
        return "50to79"
    return "80up"


def fmt_price(price):
    if price is None:
        return None
    if float(price).is_integer():
        return "$%d" % int(price)
    return "$%.2f" % price


def offer_window(offer, promo):
    """(start, end) for an offer: its own dates, defaulting to the promotion's."""
    return (offer.get("start") or promo.get("start"), offer.get("end") or promo.get("end"))


def offer_on(offer, promo, date):
    """True when the offer is available on that date: inside its window, on one of its weekdays (if it
    lists any), and not one of its exclusions."""
    start, end = offer_window(offer, promo)
    if not start or not end or date < start or date > end:
        return False
    wds = offer.get("weekdays")
    if wds and weekday_key(date) not in wds:
        return False
    if date in (offer.get("exclusions") or []):
        return False
    return True


def offer_current(offer, promo, today):
    """An offer still counts while today is on or before its last day (its own end, else the
    promotion's). An offer whose own dates have ended never matches a filter or sets a price."""
    start, end = offer_window(offer, promo)
    return bool(end) and today <= end


def offer_matches(offer, filters):
    """filters: {"bucket": key or "", "meal": key or ""}. An "any" meal offer matches only when no meal
    is chosen (All meals); a chosen meal needs an offer for exactly that meal."""
    b = filters.get("bucket") or ""
    if b and price_bucket(offer.get("price")) != b:
        return False
    meal = filters.get("meal") or ""
    if meal and offer.get("meal") != meal:
        return False
    return True


def filters_active(filters):
    return bool(filters.get("bucket") or filters.get("meal"))


def offer_label_shown(offer):
    """The meal label is shown once: hidden when the description already opens with its own label
    ("Sweet Perk:", "Breakfast:", "Dinner Shared Meal for 4:"), shown before course lists ("First Course:")."""
    m = LABEL_RE.match(offer.get("description") or "")
    return not (m and "course" not in m.group(1).lower())


def eligible_offers(entry, promo, filters, today):
    """Offers that are current and match the price and meal filters together (one offer must match
    both: a $25 lunch does not make a restaurant a dinner under $30)."""
    return [o for o in entry.get("offers") or []
            if offer_current(o, promo, today) and offer_matches(o, filters)]


def entry_matches(entry, promo, filters, today):
    """filters: promotion, town, bucket, meal (each "" for all), booking (bool)."""
    if filters.get("promotion") and entry["promotion_id"] != filters["promotion"]:
        return False
    if filters.get("town") and entry["town"] != filters["town"]:
        return False
    if filters.get("booking") and not entry.get("booking_url"):
        return False
    if filters.get("bucket") or filters.get("meal"):
        return len(eligible_offers(entry, promo, filters, today)) > 0
    return True


SORT_MEALS = ("lunch", "dinner")


def lowest_eligible_price(entry, promo, filters, today):
    """The sort price (Jay, 2026-11-01): the cheapest lunch or dinner offer among those matching the
    filters. An "any" offer (the $6 Sweet Perk) never sets it, so a restaurant with only a Sweet Perk sorts
    as if it had no priced offer. When a meal filter is chosen, offers for that meal count."""
    meals = (filters.get("meal"),) if filters.get("meal") else SORT_MEALS
    prices = [o["price"] for o in eligible_offers(entry, promo, filters, today)
              if o.get("price") is not None and o.get("meal") in meals]
    return min(prices) if prices else None


def sort_entries(entries, by, promos_by_id, filters, today):
    """by "town": town, then name. by "price": lowest eligible price, entries with none last, then name."""
    if by == "price":
        def key(e):
            p = lowest_eligible_price(e, promos_by_id[e["promotion_id"]], filters, today)
            return (p is None, p if p is not None else 0, e["name"].lower(), e["id"])
    else:
        def key(e):
            return (e["town"].lower(), e["name"].lower(), e["id"])
    return sorted(entries, key=key)


def describe_offer_when(offer, promo):
    """The offer's own dates, weekdays and exclusions in words, or "" when it runs the promotion's
    dates on every day."""
    parts = []
    start, end = offer_window(offer, promo)
    if (offer.get("start") and offer["start"] != promo.get("start")) or \
            (offer.get("end") and offer["end"] != promo.get("end")):
        parts.append(fmt_range(start, end))
    wds = offer.get("weekdays")
    if wds and len(wds) < 7:
        names = [WEEKDAY_NAMES[w] for w in WEEKDAYS if w in wds]
        parts.append(" and ".join([", ".join(names[:-1]), names[-1]]) if len(names) > 1 else names[0])
    exc = offer.get("exclusions") or []
    if exc:
        parts.append("not " + ", ".join(fmt_date(x) for x in sorted(exc)))
    return "; ".join(parts)


def is_stale(last_checked, today):
    return not last_checked or days_between(last_checked, today) > STALE_DAYS


def stale_text(last_checked):
    return "Checked %s; confirm with the restaurant." % fmt_date(last_checked, True)


def safe_url(href):
    return href if isinstance(href, str) and re.match(r"^https?://", href, re.I) else None
