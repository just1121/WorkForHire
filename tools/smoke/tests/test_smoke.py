"""Smoke watch tests. Offline: every source is a saved fixture in tests/fixtures (recorded 2026-10-31).
Run from the repository root:  python3 -m unittest discover -s tools/smoke/tests -t . -v
"""
import copy
import datetime as dt
import json
import math
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import fetch  # noqa: E402

FIX = HERE / "fixtures"
# the fixtures were recorded on Saturday 2026-10-31 at about 11 am Pacific
NOW = dt.datetime(2026, 10, 31, 11, 0, tzinfo=fetch.TZ)
SAT, SUN = dt.date(2026, 10, 31), dt.date(2026, 11, 1)


def load(name):
    return json.loads((FIX / name).read_text())


def raw_from_fixtures():
    return {
        "aq": load("aq.json"),
        "wind": load("wind.json"),
        "fires": load("fires.json"),
        "nws": {t[1]: load("nws/%s.json" % t[1]) for t in fetch.TOWNS},
    }


def destination(lat, lon, bearing, miles):
    """Point `miles` from (lat, lon) along `bearing`; used to place test fires."""
    r = 3958.8
    d = miles / r
    b = math.radians(bearing)
    p1, l1 = math.radians(lat), math.radians(lon)
    p2 = math.asin(math.sin(p1) * math.cos(d) + math.cos(p1) * math.sin(d) * math.cos(b))
    l2 = l1 + math.atan2(math.sin(b) * math.sin(d) * math.cos(p1), math.cos(d) - math.sin(p1) * math.sin(p2))
    return math.degrees(p2), math.degrees(l2)


def town_index(name):
    return [t[0] for t in fetch.TOWNS].index(name)


class Bands(unittest.TestCase):
    def test_band_edges(self):
        cases = [(0, "good", "Clear"), (50, "good", "Clear"), (51, "moderate", "A little haze"),
                 (100, "moderate", "A little haze"), (101, "usg", "Smoky"), (150, "usg", "Smoky"),
                 (151, "unhealthy", "Unhealthy"), (200, "unhealthy", "Unhealthy"),
                 (201, "very_unhealthy", "Very unhealthy"), (300, "very_unhealthy", "Very unhealthy"),
                 (301, "hazardous", "Hazardous"), (999, "hazardous", "Hazardous")]
        for aqi, key, word in cases:
            with self.subTest(aqi=aqi):
                self.assertEqual(fetch.band(aqi), (key, word))

    def test_band_rounds(self):
        self.assertEqual(fetch.band(50.4)[0], "good")
        self.assertEqual(fetch.band(50.5)[0], "moderate")
        self.assertEqual(fetch.round_half_up(100.5), 101)

    def test_pm25_breakpoints(self):
        cases = [(0, 0), (9.0, 50), (9.1, 51), (12.0, 56), (35.4, 100), (35.5, 101), (55.4, 150),
                 (55.5, 151), (125.4, 200), (125.5, 201), (225.5, 301), (325.4, 500), (900, 500), (-3, 0)]
        for c, aqi in cases:
            with self.subTest(pm25=c):
                self.assertEqual(fetch.pm25_to_aqi(c), aqi)

    def test_pm25_truncates(self):
        self.assertEqual(fetch.pm25_to_aqi(9.09), 50)  # 9.09 truncates to 9.0, not up to 9.1


class Geometry(unittest.TestCase):
    def test_compass(self):
        cases = [(0, "N"), (22.4, "N"), (22.5, "NE"), (45, "NE"), (90, "E"), (180, "S"), (225, "SW"),
                 (270, "W"), (337.4, "NW"), (337.5, "N"), (359.9, "N"), (360, "N")]
        for deg, name in cases:
            with self.subTest(deg=deg):
                self.assertEqual(fetch.compass(deg), name)

    def test_distance_napa_santa_rosa(self):
        d = fetch.distance_mi(38.297, -122.287, 38.440, -122.714)
        self.assertAlmostEqual(d, 25.2, delta=0.5)

    def test_bearing_cardinals(self):
        self.assertAlmostEqual(fetch.bearing_deg(38.5, -122.5, 39.0, -122.5), 0, delta=0.1)
        self.assertAlmostEqual(fetch.bearing_deg(38.5, -122.5, 38.0, -122.5), 180, delta=0.1)
        self.assertAlmostEqual(fetch.bearing_deg(38.5, -122.5, 38.5, -122.0), 90, delta=0.5)
        self.assertAlmostEqual(fetch.bearing_deg(38.5, -122.5, 38.5, -123.0), 270, delta=0.5)

    def test_destination_round_trip(self):
        lat, lon = destination(38.611, -122.869, 45, 31)
        self.assertAlmostEqual(fetch.distance_mi(38.611, -122.869, lat, lon), 31, delta=0.05)
        self.assertAlmostEqual(fetch.bearing_deg(38.611, -122.869, lat, lon), 45, delta=0.5)

    def test_angle_diff_wraps(self):
        self.assertEqual(fetch.angle_diff(350, 10), 20)
        self.assertEqual(fetch.angle_diff(10, 350), 20)
        self.assertEqual(fetch.angle_diff(90, 270), 180)
        self.assertEqual(fetch.angle_diff(45, 45), 0)


class Upwind(unittest.TestCase):
    def test_majority_needed(self):
        self.assertTrue(fetch.is_upwind(45, [45] * 7 + [225] * 5))
        self.assertFalse(fetch.is_upwind(45, [45] * 6 + [225] * 6))  # a tie is not a majority

    def test_within_45_degrees(self):
        self.assertTrue(fetch.is_upwind(0, [45, 315, 0]))
        self.assertFalse(fetch.is_upwind(0, [46, 314, 180]))

    def test_wind_direction_is_from(self):
        # fire to the NE; wind blowing FROM the NE carries smoke toward the town
        self.assertTrue(fetch.is_upwind(45, [40, 50, 45]))
        # wind blowing FROM the SW pushes smoke away
        self.assertFalse(fetch.is_upwind(45, [225, 230, 220]))

    def test_no_hours(self):
        self.assertIsNone(fetch.is_upwind(45, []))
        self.assertIsNone(fetch.is_upwind(45, [None, None]))


class Weekend(unittest.TestCase):
    def test_days(self):
        self.assertEqual(fetch.weekend_days(dt.date(2026, 10, 30)), [SAT, SUN])        # Friday
        self.assertEqual(fetch.weekend_days(SAT), [SAT, SUN])                          # Saturday
        self.assertEqual(fetch.weekend_days(SUN), [SUN])                               # Sunday
        self.assertEqual(fetch.weekend_days(dt.date(2026, 11, 2)),
                         [dt.date(2026, 11, 7), dt.date(2026, 11, 8)])                 # Monday

    def summary(self, sat, sun, days=(SAT, SUN)):
        times, pm = [], []
        for d, v in ((SAT, sat), (SUN, sun)):
            if v is None:
                continue
            for h in range(24):
                times.append("%sT%02d:00" % (d.isoformat(), h))
                pm.append(v if h == 15 else 2.0)  # peak at 3 pm, clean otherwise
        return fetch.weekend_summary(times, pm, list(days))

    def test_clear(self):
        self.assertEqual(self.summary(5, 8), ("Clear", "good"))

    def test_haze_one_day(self):
        self.assertEqual(self.summary(20, 5), ("Some haze likely Sat", "moderate"))

    def test_smoke_both_days(self):
        self.assertEqual(self.summary(40, 50), ("Smoke likely Sat and Sun", "usg"))

    def test_worst_band_wins(self):
        self.assertEqual(self.summary(20, 80), ("Unhealthy smoke likely Sun", "unhealthy"))
        self.assertEqual(self.summary(150, 20), ("Very unhealthy smoke likely Sat", "very_unhealthy"))
        self.assertEqual(self.summary(400, 400), ("Hazardous smoke likely Sat and Sun", "hazardous"))

    def test_missing_day(self):
        self.assertEqual(self.summary(40, None), ("Smoke likely Sat; no forecast yet for Sun", "usg"))
        self.assertEqual(self.summary(5, None), ("Clear Sat; no forecast yet for Sun", "good"))

    def test_no_forecast(self):
        self.assertEqual(fetch.weekend_summary([], [], [SAT, SUN]), ("No forecast yet", None))

    def test_nulls_ignored(self):
        times = ["2026-10-31T10:00", "2026-10-31T11:00"]
        self.assertEqual(fetch.weekend_summary(times, [None, 4.0], [SAT]), ("Clear", "good"))


class Wording(unittest.TestCase):
    def test_time(self):
        self.assertEqual(fetch.fmt_time(dt.datetime.fromisoformat("2026-11-01T18:00:00-08:00")), "Sun 6 pm")
        self.assertEqual(fetch.fmt_time(dt.datetime.fromisoformat("2026-10-31T09:30:00-07:00")), "Sat 9:30 am")
        self.assertEqual(fetch.fmt_time(fetch.parse_iso("2026-10-31T19:00:00Z")), "Sat 12 pm")

    def test_fire_names(self):
        self.assertEqual(fetch.fire_display_name("COOLEY"), "the Cooley Fire")
        self.assertEqual(fetch.fire_display_name("Glass Fire"), "the Glass Fire")
        self.assertEqual(fetch.fire_display_name("LNU LIGHTNING COMPLEX"), "the LNU Lightning Complex")
        self.assertEqual(fetch.fire_display_name("McKinney"), "the McKinney Fire")
        self.assertEqual(fetch.fire_display_name("Wildfire Ridge"), "the Wildfire Ridge Fire")
        self.assertEqual(fetch.fire_display_name(""), "an unnamed fire")

    def test_time_across_dst(self):
        # clocks fall back at 2 am on Sunday 2026-11-01
        self.assertEqual(fetch.fmt_time(fetch.parse_iso("2026-11-01T08:30:00Z")), "Sun 1:30 am")   # PDT
        self.assertEqual(fetch.fmt_time(fetch.parse_iso("2026-11-01T10:00:00Z")), "Sun 2 am")      # PST

    def test_join(self):
        self.assertEqual(fetch.join_words([]), "")
        self.assertEqual(fetch.join_words(["Sat"]), "Sat")
        self.assertEqual(fetch.join_words(["Sat", "Sun"]), "Sat and Sun")

    def test_warnings_filtered_and_deduped(self):
        payload = {"features": [
            {"properties": {"event": "Coastal Flood Advisory", "ends": None}},
            {"properties": {"event": "Red Flag Warning", "ends": "2026-11-01T18:00:00-08:00"}},
            {"properties": {"event": "Red Flag Warning", "ends": "2026-11-01T18:00:00-08:00"}},
            {"properties": {"event": "Air Quality Alert", "ends": None, "expires": "2026-11-01T09:00:00-08:00"}},
        ]}
        self.assertEqual(fetch.parse_warnings(payload), [
            {"event": "Red Flag Warning", "ends": "2026-11-01T18:00:00-08:00"},
            {"event": "Air Quality Alert", "ends": None}])  # expires is never used as an end time


class ReportFromFixtures(unittest.TestCase):
    """The recorded live responses, end to end."""

    def setUp(self):
        self.raw = raw_from_fixtures()
        self.fetched = {k: "2026-10-31T18:07:00Z" for k in ("aq", "wind", "nws", "fires")}

    def build(self, raw=None, failed=None):
        return fetch.build_report(raw or self.raw, failed or {}, self.fetched, NOW)

    def test_shape(self):
        doc = self.build()
        self.assertEqual(doc["version"], 1)
        self.assertEqual(doc["weekend"], {"days": ["2026-10-31", "2026-11-01"], "label": "Sat and Sun"})
        self.assertEqual(doc["credits"], ["Open-Meteo", "National Weather Service", "NIFC"])
        self.assertEqual([s["id"] for s in doc["sources"]], ["aq", "wind", "nws", "fires"])
        self.assertTrue(all(s["ok"] for s in doc["sources"]))
        self.assertEqual(len(doc["towns"]), 10)
        self.assertEqual(doc["generated_at"], "2026-10-31T18:00:00Z")
        for row in doc["towns"]:
            for key in ("name", "zip", "now", "weekend", "why"):
                self.assertTrue(row[key], (row["name"], key))
            self.assertTrue(row["why"].endswith("."))
            self.assertEqual(row["why"][0], row["why"][0].upper())
            self.assertNotIn("—", row["why"])  # no em dashes

    def test_recorded_rows(self):
        rows = {r["name"]: r for r in self.build()["towns"]}
        self.assertEqual(rows["Calistoga"]["now"], "A little haze. AQI 54")
        self.assertEqual(rows["Healdsburg"]["now"], "Clear. AQI 24")
        self.assertIn("no active fires within 50 miles", rows["Napa"]["why"])
        self.assertIn("the Cooley Fire (1,365 acres, 98% contained), 9 miles W,", rows["Cloverdale"]["why"])
        self.assertIn("this weekend", rows["Cloverdale"]["why"])

    def test_scenario_fire_upwind_with_red_flag(self):
        """The spec's Healdsburg example: smoky now, smoke Saturday, a fire 31 miles NE upwind, Red Flag Warning."""
        raw = copy.deepcopy(self.raw)
        i = town_index("Healdsburg")
        name, zipcode, lat, lon = fetch.TOWNS[i]
        flat, flon = destination(lat, lon, 45, 31)
        raw["fires"] = {"features": [{"attributes": {"IncidentName": "TEST", "IncidentSize": 2500,
                                                     "PercentContained": 10, "IncidentTypeCategory": "WF",
                                                     "ModifiedOnDateTime_dt": 1793500000000},
                                      "geometry": {"x": flon, "y": flat}}]}
        aq, wind = raw["aq"][i], raw["wind"][i]
        aq["current"]["us_aqi"] = 112
        aq["hourly"]["pm2_5"] = [40.0 if t.startswith("2026-10-31T15") else 3.0 for t in aq["hourly"]["time"]]
        wind["hourly"]["wind_direction_10m"] = [45 for _ in wind["hourly"]["time"]]
        raw["nws"][zipcode] = {"features": [{"properties": {"event": "Red Flag Warning",
                                                            "ends": "2026-11-01T18:00:00-08:00"}}]}
        row = self.build(raw)["towns"][i]
        self.assertEqual(row["now"], "Smoky. AQI 112")
        self.assertEqual(row["weekend"], "Smoke likely Sat")
        self.assertIn("the Test Fire (2,500 acres, 10% contained), 31 miles NE, is upwind this weekend", row["why"])
        self.assertIn("Red Flag Warning until Sun 6 pm", row["why"])
        self.assertTrue(row["why"].endswith("sensitive people take care."))
        self.assertEqual(row["fire"]["upwind"], True)

    def test_fire_beyond_50_miles_ignored(self):
        raw = copy.deepcopy(self.raw)
        lat, lon = destination(38.579, -122.580, 0, 400)  # far north of every town
        raw["fires"] = {"features": [{"attributes": {"IncidentName": "FAR", "IncidentTypeCategory": "WF"},
                                      "geometry": {"x": lon, "y": lat}}]}
        for row in self.build(raw)["towns"]:
            self.assertIn("no active fires within 50 miles", row["why"])
            self.assertIsNone(row["fire"])

    def test_prescribed_burns_skipped(self):
        raw = copy.deepcopy(self.raw)
        raw["fires"] = {"features": [{"attributes": {"IncidentName": "PILE BURN", "IncidentTypeCategory": "RX"},
                                      "geometry": {"x": -122.58, "y": 38.6}}]}
        self.assertIn("no active fires", self.build(raw)["towns"][0]["why"])

    def test_air_quality_failure_says_so(self):
        doc = self.build(failed={"aq": "timeout"})
        self.assertFalse(doc["sources"][0]["ok"])
        for row in doc["towns"]:
            self.assertEqual(row["now"], "No data from Open-Meteo right now")
            self.assertEqual(row["weekend"], "No data from Open-Meteo right now")
            self.assertIsNone(row["aqi"])

    def test_wind_failure_says_so_and_does_not_guess_upwind(self):
        rows = {r["name"]: r for r in self.build(failed={"wind": "timeout"})["towns"]}
        self.assertTrue(rows["Napa"]["why"].startswith("No data from Open-Meteo right now"))
        self.assertIn("weekend wind not known yet", rows["Cloverdale"]["why"])
        self.assertIsNone(rows["Cloverdale"]["fire"]["upwind"])

    def test_fire_failure_says_so(self):
        for row in self.build(failed={"fires": "HTTP 500"})["towns"]:
            self.assertIn("No data from NIFC right now", row["why"])

    def test_one_town_nws_failure(self):
        raw = copy.deepcopy(self.raw)
        raw["nws"]["94558"] = None
        doc = self.build(raw)
        rows = {r["zip"]: r for r in doc["towns"]}
        self.assertIn("No data from the National Weather Service right now", rows["94558"]["why"])
        self.assertNotIn("National Weather Service", rows["94515"]["why"])
        nws = [s for s in doc["sources"] if s["id"] == "nws"][0]
        self.assertFalse(nws["ok"])
        self.assertIn("94558", nws["error"])

    def scenario_fire(self, raw, town, bearing, miles, name, contained=10, wind_from=None):
        i = town_index(town)
        _, _, lat, lon = fetch.TOWNS[i]
        flat, flon = destination(lat, lon, bearing, miles)
        raw["fires"]["features"].append({"attributes": {"IncidentName": name, "IncidentSize": 100,
                                                        "PercentContained": contained, "IncidentTypeCategory": "WF"},
                                         "geometry": {"x": flon, "y": flat}})
        if wind_from is not None:
            raw["wind"][i]["hourly"]["wind_direction_10m"] = [wind_from for _ in raw["wind"][i]["hourly"]["time"]]
        return i

    def test_upwind_fire_preferred_over_nearer_one(self):
        raw = copy.deepcopy(self.raw)
        raw["fires"] = {"features": []}
        self.scenario_fire(raw, "Napa", 180, 10, "NEAR")                       # 10 mi south, not upwind
        i = self.scenario_fire(raw, "Napa", 0, 40, "FAR", wind_from=0)         # 40 mi north, wind from the north
        row = self.build(raw)["towns"][i]
        self.assertIn("the Far Fire (100 acres, 10% contained), 40 miles N, is upwind this weekend", row["why"])

    def test_fully_contained_fire_not_counted(self):
        raw = copy.deepcopy(self.raw)
        raw["fires"] = {"features": []}
        i = self.scenario_fire(raw, "Napa", 0, 10, "DONE", contained=100, wind_from=0)
        self.assertIn("no active fires within 50 miles", self.build(raw)["towns"][i]["why"])

    def test_warning_without_end_time(self):
        raw = copy.deepcopy(self.raw)
        raw["nws"]["94558"] = {"features": [{"properties": {"event": "Red Flag Warning", "ends": None,
                                                            "expires": "2026-10-31T04:00:00-07:00"}}]}
        row = {r["zip"]: r for r in self.build(raw)["towns"]}["94558"]
        self.assertIn("Red Flag Warning in effect", row["why"])
        self.assertNotIn("4 am", row["why"])

    def test_unparseable_warning_end(self):
        raw = copy.deepcopy(self.raw)
        raw["nws"]["94558"] = {"features": [{"properties": {"event": "Air Quality Alert", "ends": "soon"}}]}
        row = {r["zip"]: r for r in self.build(raw)["towns"]}["94558"]
        self.assertIn("Air Quality Alert in effect", row["why"])

    def test_null_current_block(self):
        raw = copy.deepcopy(self.raw)
        raw["aq"][0]["current"] = None
        row = self.build(raw)["towns"][0]
        self.assertEqual(row["now"], "No data from Open-Meteo right now")
        self.assertNotEqual(row["weekend"], "No data from Open-Meteo right now")  # the forecast still works

    def test_odd_entry_sinks_one_row_only(self):
        raw = copy.deepcopy(self.raw)
        raw["wind"][2] = "not an object"
        rows = self.build(raw)["towns"]
        self.assertEqual(rows[2]["now"], "No data from Open-Meteo right now")
        self.assertNotIn("No data", rows[3]["now"])

    def test_past_hours_dropped(self):
        """Saturday 4 pm: a smoky Saturday morning is over and must not count."""
        raw = copy.deepcopy(self.raw)
        for aq in raw["aq"]:
            aq["hourly"]["pm2_5"] = [40.0 if t.startswith("2026-10-31T09") else 3.0 for t in aq["hourly"]["time"]]
        late = dt.datetime(2026, 10, 31, 16, 0, tzinfo=fetch.TZ)
        early = dt.datetime(2026, 10, 31, 8, 0, tzinfo=fetch.TZ)
        self.assertEqual(fetch.build_report(raw, {}, self.fetched, late)["towns"][0]["weekend"], "Clear")
        self.assertEqual(fetch.build_report(raw, {}, self.fetched, early)["towns"][0]["weekend"], "Smoke likely Sat")

    def test_sunday_run_covers_sunday_only(self):
        sunday = dt.datetime(2026, 11, 1, 9, 0, tzinfo=fetch.TZ)
        doc = fetch.build_report(self.raw, {}, self.fetched, sunday)
        self.assertEqual(doc["weekend"], {"days": ["2026-11-01"], "label": "Sun"})
        for row in doc["towns"]:
            self.assertNotIn("Sat", row["weekend"])

    def test_failed_source_has_no_fetch_time(self):
        doc = fetch.build_report(self.raw, {"fires": "HTTP 500"}, dict(self.fetched, fires=None), NOW)
        fires = [s for s in doc["sources"] if s["id"] == "fires"][0]
        self.assertIsNone(fires["fetched_at"])

    def test_malformed_response_counts_as_failure(self):
        raw = copy.deepcopy(self.raw)
        raw["aq"] = raw["aq"][:3]  # wrong number of locations
        self.assertEqual(self.build(raw)["towns"][0]["now"], "No data from Open-Meteo right now")


class CommandLine(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_writes_json_from_fixtures(self):
        out = self.tmp / "smoke.json"
        code = fetch.main(["--fixtures", str(FIX), "--out", str(out), "--now", "2026-10-31T11:00:00-07:00"])
        self.assertEqual(code, 0)
        doc = json.loads(out.read_text())
        self.assertEqual(len(doc["towns"]), 10)
        self.assertFalse((self.tmp / "smoke.json.tmp").exists())

    def test_all_sources_failed_keeps_old_file(self):
        empty = self.tmp / "empty"
        empty.mkdir()
        out = self.tmp / "smoke.json"
        out.write_text('{"old": true}')
        code = fetch.main(["--fixtures", str(empty), "--out", str(out), "--now", "2026-10-31T11:00:00-07:00"])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out.read_text()), {"old": True})

    def test_validate_rejects_empty_invalid_and_shrunken(self):
        good = fetch.build_report(raw_from_fixtures(), {}, {}, NOW)
        self.assertEqual(fetch.validate(good, good), [])
        self.assertEqual(fetch.validate({"towns": []}), ["no towns"])
        bad = json.loads(json.dumps(good)); bad["towns"][0]["why"] = ""
        self.assertTrue(any("missing why" in p for p in fetch.validate(bad)))
        fewer = json.loads(json.dumps(good)); fewer["towns"] = fewer["towns"][:9]
        self.assertTrue(any("fewer towns" in p for p in fetch.validate(fewer, good)))
        self.assertEqual(fetch.validate(fewer, good, allow_fewer=True), [])

    def test_air_quality_outage_not_published(self):
        good = fetch.build_report(raw_from_fixtures(), {}, {}, NOW)
        down = fetch.build_report(raw_from_fixtures(), {"aq": "timeout"}, {}, NOW)
        self.assertTrue(any("no town has an air quality reading" in p for p in fetch.validate(down, good)))
        raw = raw_from_fixtures(); raw["aq"][0]["current"] = None
        one_less = fetch.build_report(raw, {}, {}, NOW)
        self.assertTrue(any("fewer towns with air quality" in p for p in fetch.validate(one_less, good)))
        part = self.tmp / "noaq"
        shutil.copytree(FIX, part)
        (part / "aq.json").unlink()
        out = self.tmp / "smoke.json"
        out.write_text(json.dumps(good))
        self.assertEqual(fetch.main(["--fixtures", str(part), "--out", str(out), "--now", "2026-10-31T11:00:00-07:00"]), 3)
        self.assertEqual(json.loads(out.read_text())["towns"][0]["aqi"], good["towns"][0]["aqi"])  # last good kept

    def test_missing_wind_speed_is_not_calm(self):
        raw = raw_from_fixtures(); raw["wind"][0]["current"]["wind_speed_10m"] = None
        why = fetch.build_report(raw, {}, {}, NOW)["towns"][0]["why"]
        self.assertTrue(why.startswith("Wind from the "))
        self.assertNotIn("Calm", why)

    def test_shrunken_output_not_written(self):
        out = self.tmp / "smoke.json"
        old = fetch.build_report(raw_from_fixtures(), {}, {}, NOW)
        old["towns"].append(dict(old["towns"][0], name="Extra", zip="00000"))  # pretend the live file had 11
        out.write_text(json.dumps(old))
        code = fetch.main(["--fixtures", str(FIX), "--out", str(out), "--now", "2026-10-31T11:00:00-07:00"])
        self.assertEqual(code, 3)
        self.assertEqual(len(json.loads(out.read_text())["towns"]), 11)  # untouched
        self.assertEqual(fetch.main(["--fixtures", str(FIX), "--out", str(out), "--now", "2026-10-31T11:00:00-07:00",
                                     "--allow-fewer"]), 0)

    def test_partial_failure_still_writes(self):
        part = self.tmp / "part"
        shutil.copytree(FIX, part)
        (part / "fires.json").unlink()
        out = self.tmp / "smoke.json"
        self.assertEqual(fetch.main(["--fixtures", str(part), "--out", str(out), "--now", "2026-10-31T11:00:00-07:00"]), 0)
        doc = json.loads(out.read_text())
        self.assertFalse([s for s in doc["sources"] if s["id"] == "fires"][0]["ok"])


if __name__ == "__main__":
    unittest.main()
