#!/usr/bin/env python3
"""Smoke watch fetcher. Spec: tools/SMOKE-WATCH.md.

Public data in, data/smoke.json out. Python 3.9+, stdlib plus `requests` (live mode only).
No LLM, no API keys. The wording is a lookup table, not generated text.

  python3 tools/smoke/fetch.py                     # live fetch, writes data/smoke.json
  python3 tools/smoke/fetch.py --fixtures DIR      # offline, reads saved responses from DIR
  python3 tools/smoke/fetch.py --record DIR        # live fetch, also saves raw responses to DIR

Environment: SMOKE_WATCH_CONTACT, a contact string for the User-Agent header the National Weather
Service asks for (for example a site URL or an email). Set it as a repository variable in GitHub.
"""
import argparse
import datetime as dt
import json
import math
import os
import sys
import urllib.parse
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    sys.exit("Python 3.9 or newer is required (zoneinfo).")

TZ = ZoneInfo("America/Los_Angeles")
REPO = Path(__file__).resolve().parents[2]
DEFAULT_OUT = REPO / "data" / "smoke.json"

# Coordinates checked against the Open-Meteo geocoder on 2026-10-31; all within 0.2 miles.
TOWNS = [
    ("Calistoga", "94515", 38.579, -122.580),
    ("St. Helena", "94574", 38.505, -122.470),
    ("Yountville", "94599", 38.401, -122.361),
    ("Napa", "94558", 38.297, -122.287),
    ("Sonoma", "95476", 38.292, -122.458),
    ("Healdsburg", "95448", 38.611, -122.869),
    ("Santa Rosa", "95404", 38.440, -122.714),
    ("Geyserville", "95441", 38.705, -122.902),
    ("Cloverdale", "95425", 38.805, -123.017),
    ("Middletown", "95461", 38.752, -122.615),
]

FIRE_RADIUS_MI = 50
UPWIND_DEG = 45
DAYTIME_HOURS = range(8, 20)  # 8 am through 7:59 pm local, for the upwind test
FORECAST_DAYS = 7             # Monday needs six days ahead to reach Sunday
WARN_EVENTS = ("Red Flag Warning", "Air Quality Alert", "Fire Weather Watch")

# Source ids -> how a failure is named on the page ("No data from X right now").
SOURCES = {
    "aq": {"name": "Open-Meteo air quality", "short": "Open-Meteo", "credit": "Open-Meteo"},
    "wind": {"name": "Open-Meteo forecast", "short": "Open-Meteo", "credit": "Open-Meteo"},
    "nws": {"name": "National Weather Service alerts", "short": "the National Weather Service", "credit": "National Weather Service"},
    "fires": {"name": "NIFC current wildland fire incidents", "short": "NIFC", "credit": "NIFC"},
}

# ---------------------------------------------------------------- rules (the "AI" is a table)

# US EPA AQI bands: (upper bound, key, plain word for the Now column)
BANDS = [
    (50, "good", "Clear"),
    (100, "moderate", "A little haze"),
    (150, "usg", "Smoky"),
    (200, "unhealthy", "Unhealthy"),
    (300, "very_unhealthy", "Very unhealthy"),
    (math.inf, "hazardous", "Hazardous"),
]
BAND_ORDER = [b[1] for b in BANDS]

# Weekend column wording for the worst band reached
WEEKEND_WORDS = {
    "moderate": "Some haze likely",
    "usg": "Smoke likely",
    "unhealthy": "Unhealthy smoke likely",
    "very_unhealthy": "Very unhealthy smoke likely",
    "hazardous": "Hazardous smoke likely",
}

# US EPA PM2.5 breakpoints (2024 revision): (C low, C high, AQI low, AQI high), ug/m3
PM25_BREAKPOINTS = [
    (0.0, 9.0, 0, 50),
    (9.1, 35.4, 51, 100),
    (35.5, 55.4, 101, 150),
    (55.5, 125.4, 151, 200),
    (125.5, 225.4, 201, 300),
    (225.5, 325.4, 301, 500),
]

COMPASS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]


def round_half_up(x):
    """Ordinary rounding (50.5 -> 51). Python's round() goes to the even neighbor (50.5 -> 50)."""
    return int(math.floor(x + 0.5))


def parse_iso(s):
    """ISO 8601 time, accepting a trailing Z (Python 3.9 and 3.10 do not)."""
    s = str(s).strip()
    return dt.datetime.fromisoformat(s[:-1] + "+00:00" if s.endswith(("Z", "z")) else s)


def band(aqi):
    """(key, plain word) for a US AQI value."""
    a = round_half_up(aqi)
    for top, key, word in BANDS:
        if a <= top:
            return key, word
    raise ValueError(aqi)  # unreachable


def pm25_to_aqi(c):
    """US AQI for a PM2.5 concentration, EPA piecewise-linear formula."""
    c = math.floor(max(0.0, c) * 10) / 10  # EPA truncates to one decimal
    if c > 325.4:
        return 500
    for lo, hi, ilo, ihi in PM25_BREAKPOINTS:
        if lo <= c <= hi:
            return round_half_up((ihi - ilo) / (hi - lo) * (c - lo) + ilo)
    raise ValueError(c)  # unreachable after truncation


def compass(deg):
    """Eight-point compass name for a bearing in degrees."""
    return COMPASS[int(math.floor((deg % 360) / 45 + 0.5)) % 8]


def distance_mi(lat1, lon1, lat2, lon2):
    """Great-circle distance in miles."""
    r = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def bearing_deg(lat1, lon1, lat2, lon2):
    """Initial bearing from point 1 to point 2, degrees clockwise from north, 0 to 360."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dl) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def angle_diff(a, b):
    """Smallest difference between two bearings, 0 to 180."""
    return abs((a - b + 180) % 360 - 180)


def is_upwind(fire_bearing, wind_from_dirs):
    """True if the fire lies within UPWIND_DEG of where the wind blows FROM for a majority of hours.
    None if there are no hours to judge by."""
    dirs = [d for d in wind_from_dirs if d is not None]
    if not dirs:
        return None
    hits = sum(1 for d in dirs if angle_diff(fire_bearing, d) <= UPWIND_DEG)
    return hits * 2 > len(dirs)


def weekend_days(today):
    """The next Saturday and Sunday. On Saturday that is today and tomorrow; on Sunday, today only."""
    wd = today.weekday()  # Monday 0 .. Sunday 6
    if wd == 6:
        return [today]
    sat = today + dt.timedelta(days=5 - wd)
    return [sat, sat + dt.timedelta(days=1)]


def day_name(d):
    return d.strftime("%a")


def join_words(words):
    words = list(words)
    if len(words) <= 1:
        return "".join(words)
    return ", ".join(words[:-1]) + " and " + words[-1]


def local_date(t):
    """Date part of an Open-Meteo local time string like 2026-10-31T13:00."""
    return dt.date.fromisoformat(t[:10])


def local_hour(t):
    return int(t[11:13])


def weekend_summary(times, pm25, days):
    """(wording, worst band key or None) from hourly PM2.5 over the weekend days."""
    peak = {}
    for t, v in zip(times, pm25):
        if v is None:
            continue
        d = local_date(t)
        if d in days:
            peak[d] = max(v, peak.get(d, v))
    if not peak:
        return "No forecast yet", None
    bands = {d: band(pm25_to_aqi(v))[0] for d, v in peak.items()}
    worst = max(bands.values(), key=BAND_ORDER.index)
    missing = [d for d in days if d not in peak]
    if worst == "good":
        text = "Clear" if not missing else "Clear " + join_words(day_name(d) for d in days if d in peak)
    else:
        text = WEEKEND_WORDS[worst] + " " + join_words(day_name(d) for d in days if bands.get(d) == worst)
    if missing:
        text += "; no forecast yet for " + join_words(day_name(d) for d in missing)
    return text, worst


def fmt_time(when):
    """'Sun 6 pm' or 'Sun 6:30 pm' in Pacific time."""
    t = when.astimezone(TZ)
    h = t.hour % 12 or 12
    mins = "" if t.minute == 0 else ":%02d" % t.minute
    return "%s %d%s %s" % (t.strftime("%a"), h, mins, "am" if t.hour < 12 else "pm")


# CAL FIRE unit codes that appear in complex names; kept upper case when re-casing
UNIT_CODES = {"LNU", "SCU", "CZU", "SQF", "SHU", "MEU", "BEU", "TCU", "AEU", "NEU", "BTU", "LMU", "SKU", "TGU"}


def fire_display_name(raw):
    """'COOLEY' -> 'the Cooley Fire'. Source casing is kept unless the whole name is upper case."""
    words = str(raw).strip().split()
    if not words:
        return "an unnamed fire"
    if str(raw).isupper():
        words = [w if w in UNIT_CODES else w.capitalize() for w in words]
    name = " ".join(words)
    if words[-1].lower() in ("fire", "complex"):
        return "the " + name
    return "the " + name + " Fire"


# ---------------------------------------------------------------- parsing the sources

def as_list(payload):
    """Open-Meteo returns one object for one location and a list for several."""
    return payload if isinstance(payload, list) else [payload]


def parse_fires(payload):
    if not isinstance(payload, dict) or "features" not in payload:
        raise ValueError("unexpected NIFC response")
    fires = []
    for f in payload["features"]:
        a, g = f.get("attributes") or {}, f.get("geometry") or {}
        if a.get("IncidentTypeCategory") not in (None, "WF"):
            continue
        if g.get("x") is None or g.get("y") is None:
            continue
        if a.get("PercentContained") is not None and a["PercentContained"] >= 100:
            continue  # fully contained: not counted as active
        updated = a.get("ModifiedOnDateTime_dt")
        fires.append({
            "name": a.get("IncidentName") or "",
            "lat": float(g["y"]),
            "lon": float(g["x"]),
            "acres": a.get("IncidentSize"),
            "contained": a.get("PercentContained"),
            "updated": dt.datetime.fromtimestamp(updated / 1000, dt.timezone.utc).isoformat() if updated else None,
        })
    return fires


def parse_warnings(payload):
    if not isinstance(payload, dict) or "features" not in payload:
        raise ValueError("unexpected NWS response")
    seen, out = set(), []
    for f in payload["features"]:
        p = f.get("properties") or {}
        event = p.get("event")
        if event not in WARN_EVENTS or event in seen:
            continue
        seen.add(event)
        # "ends" is when the hazard ends; "expires" is only when this message lapses, so it is never used
        out.append({"event": event, "ends": p.get("ends")})
    return out


# ---------------------------------------------------------------- one row

def future_hours(times, values, cutoff):
    """Keep hourly pairs at or after `cutoff` (a local 'YYYY-MM-DDTHH:00' string), so hours already gone don't count."""
    return [(t, v) for t, v in zip(times or [], values or []) if t >= cutoff]


def town_row(town, aq, wind, warnings, fires, failed, days, cutoff=""):
    """Build one table row. aq/wind are this town's Open-Meteo objects (or None if failed),
    warnings a parsed list (or None), fires a parsed list (or None). `failed` is a set of source ids.
    `cutoff` drops forecast hours before now."""
    name, zipcode, lat, lon = town
    row = {"name": name, "zip": zipcode, "lat": lat, "lon": lon,
           "aqi": None, "band": None, "weekend_band": None, "fire": None, "warnings": []}
    why = []

    # Now and This weekend: Open-Meteo air quality
    cur_aqi = (aq.get("current") or {}).get("us_aqi") if aq else None
    if aq is None or cur_aqi is None:
        row["now"] = "No data from %s right now" % SOURCES["aq"]["short"]
    else:
        key, word = band(cur_aqi)
        row.update(aqi=round_half_up(cur_aqi), band=key, now="%s. AQI %d" % (word, round_half_up(cur_aqi)))
    if aq is None:
        row["weekend"] = "No data from %s right now" % SOURCES["aq"]["short"]
    else:
        h = aq.get("hourly") or {}
        pairs = future_hours(h.get("time"), h.get("pm2_5"), cutoff)
        row["weekend"], row["weekend_band"] = weekend_summary([p[0] for p in pairs], [p[1] for p in pairs], days)

    # Why: wind
    weekend_dirs = []
    if wind is None or (wind.get("current") or {}).get("wind_direction_10m") is None:
        why.append("No data from %s right now" % SOURCES["wind"]["short"])
    else:
        c = wind["current"]
        speed = c.get("wind_speed_10m")
        if speed is None:
            why.append("Wind from the %s" % compass(c["wind_direction_10m"]))  # speed missing: say only what is known
        elif speed < 1:
            why.append("Calm wind")
        else:
            why.append("Wind from the %s at %d mph" % (compass(c["wind_direction_10m"]), round_half_up(speed)))
    if wind is not None:
        h = wind.get("hourly") or {}
        for t, d in future_hours(h.get("time"), h.get("wind_direction_10m"), cutoff):
            if local_date(t) in days and local_hour(t) in DAYTIME_HOURS:
                weekend_dirs.append(d)

    # Why: fires
    if fires is None:
        why.append("No data from %s right now" % SOURCES["fires"]["short"])
    else:
        near = []
        for f in fires:
            dist = distance_mi(lat, lon, f["lat"], f["lon"])
            if dist <= FIRE_RADIUS_MI:
                near.append((dist, f))
        if not near:
            why.append("no active fires within %d miles" % FIRE_RADIUS_MI)
        else:
            # the nearest upwind fire if there is one, else the nearest
            judged = []
            for d_, f_ in sorted(near, key=lambda p: p[0]):
                b_ = bearing_deg(lat, lon, f_["lat"], f_["lon"])
                judged.append((d_, f_, b_, is_upwind(b_, weekend_dirs) if wind is not None else None))
            dist, f, brg, upwind = next((j for j in judged if j[3] is True), judged[0])
            facts = []
            if f.get("acres") is not None:
                facts.append("{:,} acres".format(round_half_up(f["acres"])))
            if f.get("contained") is not None:
                facts.append("%d%% contained" % round_half_up(f["contained"]))
            label = fire_display_name(f["name"])
            if facts:
                label += " (" + ", ".join(facts) + ")"
            where = "%s, %d miles %s," % (label, round_half_up(dist), compass(brg))
            if upwind is True:
                why.append(where + " is upwind this weekend")
            elif upwind is False:
                why.append(where + " is not upwind this weekend")
            else:
                why.append(where + " weekend wind not known yet")
            row["fire"] = {"name": f["name"], "miles": round_half_up(dist), "bearing": compass(brg),
                           "upwind": upwind, "acres": f.get("acres"), "contained": f.get("contained")}

    # Why: warnings
    if warnings is None:
        why.append("No data from %s right now" % SOURCES["nws"]["short"])
    else:
        for w in warnings:
            row["warnings"].append(w)
            try:
                why.append("%s until %s" % (w["event"], fmt_time(parse_iso(w["ends"]))) if w["ends"] else "%s in effect" % w["event"])
            except ValueError:
                why.append("%s in effect" % w["event"])

    if row["band"] == "usg":
        why.append("sensitive people take care")

    text = "; ".join(why)
    row["why"] = text[:1].upper() + text[1:] + "."
    row["sources_failed"] = sorted(failed)
    return row


# ---------------------------------------------------------------- the whole report

def build_report(raw, failed, fetched, now):
    """Pure function: raw responses -> the smoke.json document.
    raw: {"aq": payload, "wind": payload, "fires": payload, "nws": {zip: payload}}
    failed: {source id: error string} for whole-source failures; NWS per town is in raw["nws"][zip] = None.
    fetched: {source id: ISO time}. now: aware datetime."""
    local_now = now.astimezone(TZ)
    today = local_now.date()
    days = weekend_days(today)
    cutoff = local_now.strftime("%Y-%m-%dT%H:00")

    aq_list = wind_list = fires = None
    errors = dict(failed)
    if "aq" not in failed:
        try:
            aq_list = as_list(raw["aq"])
            assert len(aq_list) == len(TOWNS)
        except Exception as e:  # malformed counts as failed
            aq_list, errors["aq"] = None, "unexpected response: %s" % e
    if "wind" not in failed:
        try:
            wind_list = as_list(raw["wind"])
            assert len(wind_list) == len(TOWNS)
        except Exception as e:
            wind_list, errors["wind"] = None, "unexpected response: %s" % e
    if "fires" not in failed:
        try:
            fires = parse_fires(raw["fires"])
        except Exception as e:
            fires, errors["fires"] = None, str(e)

    rows, nws_bad = [], []
    for i, town in enumerate(TOWNS):
        warnings = None
        payload = (raw.get("nws") or {}).get(town[1])
        if payload is not None:
            try:
                warnings = parse_warnings(payload)
            except Exception:
                warnings = None
        if warnings is None:
            nws_bad.append(town[1])
        town_failed = {k for k in ("aq", "wind", "fires") if k in errors}
        if warnings is None:
            town_failed.add("nws")
        aq_i = aq_list[i] if aq_list else None
        wind_i = wind_list[i] if wind_list else None
        try:
            if aq_i is not None and not isinstance(aq_i, dict):
                raise ValueError("air quality entry is not an object")
            if wind_i is not None and not isinstance(wind_i, dict):
                raise ValueError("wind entry is not an object")
            rows.append(town_row(town, aq_i, wind_i, warnings, fires, town_failed, days, cutoff))
        except Exception as e:
            # one odd entry must not sink the other nine rows: this town says "No data" instead
            errors.setdefault("aq", "town %s: %s" % (town[1], e))
            errors.setdefault("wind", "town %s: %s" % (town[1], e))
            rows.append(town_row(town, None, None, warnings, fires, town_failed | {"aq", "wind"}, days, cutoff))
    if nws_bad:
        errors.setdefault("nws", "no data for zip " + ", ".join(nws_bad))

    credits = []
    for k in ("aq", "wind", "nws", "fires"):
        if SOURCES[k]["credit"] not in credits:
            credits.append(SOURCES[k]["credit"])

    return {
        "version": 1,
        "generated_at": now.astimezone(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "timezone": "America/Los_Angeles",
        "weekend": {"days": [d.isoformat() for d in days], "label": join_words(day_name(d) for d in days)},
        "credits": credits,
        "sources": [
            {"id": k, "name": SOURCES[k]["name"], "ok": k not in errors,
             "fetched_at": fetched.get(k), "error": errors.get(k)}
            for k in ("aq", "wind", "nws", "fires")
        ],
        "towns": rows,
    }


# ---------------------------------------------------------------- fetching

def urls():
    lats = ",".join("%.3f" % t[2] for t in TOWNS)
    lons = ",".join("%.3f" % t[3] for t in TOWNS)
    aq = "https://air-quality-api.open-meteo.com/v1/air-quality?" + urllib.parse.urlencode({
        "latitude": lats, "longitude": lons, "current": "us_aqi,pm2_5", "hourly": "pm2_5",
        "forecast_days": FORECAST_DAYS, "timezone": "America/Los_Angeles"})
    wind = "https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode({
        "latitude": lats, "longitude": lons, "current": "wind_speed_10m,wind_direction_10m",
        "hourly": "wind_direction_10m", "wind_speed_unit": "mph",
        "forecast_days": FORECAST_DAYS, "timezone": "America/Los_Angeles"})
    # bounding box of the towns, widened by the fire radius (about 0.73 deg lat, 0.93 deg lon here)
    pad_lat = FIRE_RADIUS_MI / 69.0
    pad_lon = FIRE_RADIUS_MI / (69.0 * math.cos(math.radians(38.5)))
    box = "%.3f,%.3f,%.3f,%.3f" % (min(t[3] for t in TOWNS) - pad_lon, min(t[2] for t in TOWNS) - pad_lat,
                                   max(t[3] for t in TOWNS) + pad_lon, max(t[2] for t in TOWNS) + pad_lat)
    fires = ("https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/"
             "WFIGS_Incident_Locations_Current/FeatureServer/0/query?" + urllib.parse.urlencode({
                 "where": "IncidentTypeCategory='WF'", "geometry": box, "geometryType": "esriGeometryEnvelope",
                 "inSR": 4326, "spatialRel": "esriSpatialRelIntersects",
                 "outFields": "IncidentName,IncidentSize,PercentContained,ModifiedOnDateTime_dt,IncidentTypeCategory",
                 "returnGeometry": "true", "outSR": 4326, "f": "json"}))
    nws = {t[1]: "https://api.weather.gov/alerts/active?point=%.4f,%.4f" % (t[2], t[3]) for t in TOWNS}
    return {"aq": aq, "wind": wind, "fires": fires, "nws": nws}


class LiveSource:
    def __init__(self, contact, record_dir=None):
        import requests  # live mode only; tests never need it
        self.requests = requests
        self.headers = {"User-Agent": "SmokeWatch/1.0 (%s)" % (contact or "contact not set"),
                        "Accept": "application/geo+json, application/json"}
        self.record = Path(record_dir) if record_dir else None

    def get(self, key, url):
        last = None
        for _ in range(2):  # one retry
            try:
                r = self.requests.get(url, headers=self.headers, timeout=15)
                r.raise_for_status()
                data = r.json()
                if isinstance(data, dict) and data.get("error"):  # ArcGIS and Open-Meteo report errors in 200s
                    raise ValueError(str(data.get("error"))[:200])
                if self.record:
                    p = self.record / (key + ".json")
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_text(json.dumps(data, indent=1))
                return data
            except Exception as e:
                last = e
        raise last


class FixtureSource:
    """Reads saved responses: aq.json, wind.json, fires.json, nws/<zip>.json. A missing file is a failure."""
    def __init__(self, folder):
        self.folder = Path(folder)

    def get(self, key, url):
        return json.loads((self.folder / (key + ".json")).read_text())


def collect(source):
    """Fetch everything. Returns (raw, failed, fetched)."""
    u = urls()
    raw, failed, fetched = {"nws": {}}, {}, {}

    def stamp():
        return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

    for key in ("aq", "wind", "fires"):
        try:
            raw[key] = source.get(key, u[key])
        except Exception as e:
            failed[key] = "%s: %s" % (type(e).__name__, str(e)[:200])
        fetched[key] = None if key in failed else stamp()
    for zipcode, url in u["nws"].items():
        try:
            raw["nws"][zipcode] = source.get("nws/" + zipcode, url)
        except Exception:
            raw["nws"][zipcode] = None
    fetched["nws"] = stamp() if any(v is not None for v in raw["nws"].values()) else None
    return raw, failed, fetched


REQUIRED_ROW_KEYS = ("name", "zip", "now", "weekend", "why")


def validate(doc, previous=None, allow_fewer=False):
    """Problems that must stop a write (the data rule: never publish an empty, invalid, or
    unexpectedly reduced dataset). Returns a list of strings; empty means OK."""
    problems = []
    towns = doc.get("towns") if isinstance(doc, dict) else None
    if not isinstance(towns, list) or not towns:
        return ["no towns"]
    for i, row in enumerate(towns):
        if not isinstance(row, dict):
            problems.append("row %d is not an object" % i)
            continue
        missing = [k for k in REQUIRED_ROW_KEYS if not row.get(k)]
        if missing:
            problems.append("row %d (%s) missing %s" % (i, row.get("name"), ", ".join(missing)))
    for key in ("generated_at", "sources", "credits", "weekend"):
        if not doc.get(key):
            problems.append("missing " + key)
    # air quality is the table's point: never replace readings with a table of "No data"
    with_aqi = sum(1 for r in towns if isinstance(r, dict) and r.get("aqi") is not None)
    if with_aqi == 0:
        problems.append("no town has an air quality reading")
    if previous and isinstance(previous.get("towns"), list) and not allow_fewer:
        if len(towns) < len(previous["towns"]):
            problems.append("fewer towns than the published file (%d < %d); rerun with --allow-fewer if intended"
                            % (len(towns), len(previous["towns"])))
        prev_aqi = sum(1 for r in previous["towns"] if isinstance(r, dict) and r.get("aqi") is not None)
        if with_aqi < prev_aqi:
            problems.append("fewer towns with air quality readings than the published file (%d < %d)" % (with_aqi, prev_aqi))
    return problems


def read_previous(out):
    try:
        return json.loads(Path(out).read_text())
    except (OSError, ValueError):
        return None


def write_json(doc, out):
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
    os.replace(tmp, out)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Smoke watch: public data in, data/smoke.json out.")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--fixtures", help="read saved responses from this folder instead of the network")
    ap.add_argument("--record", help="also save the raw live responses to this folder")
    ap.add_argument("--now", help="ISO time to use as 'now' (tests)")
    ap.add_argument("--allow-fewer", action="store_true", help="allow a confirmed reduction in towns (by hand only)")
    args = ap.parse_args(argv)

    now = parse_iso(args.now) if args.now else dt.datetime.now(dt.timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=TZ)
    source = FixtureSource(args.fixtures) if args.fixtures else LiveSource(os.environ.get("SMOKE_WATCH_CONTACT"), args.record)

    raw, failed, fetched = collect(source)
    doc = build_report(raw, failed, fetched, now)
    if not any(s["ok"] for s in doc["sources"]) and all(v is None for v in raw["nws"].values()):
        # every source failed: keep the last good file; the page will mark it stale after 12 hours
        print("smoke watch: every source failed; %s left unchanged" % args.out, file=sys.stderr)
        return 1
    problems = validate(doc, read_previous(args.out), args.allow_fewer)
    if problems:
        print("smoke watch: refusing to write %s: %s" % (args.out, "; ".join(problems)), file=sys.stderr)
        return 3
    write_json(doc, args.out)
    bad = [s["id"] for s in doc["sources"] if not s["ok"]]
    print("smoke watch: wrote %s (%d towns%s)" % (args.out, len(doc["towns"]), "; failed: " + ", ".join(bad) if bad else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
