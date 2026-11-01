"""Big days calendar tests. Offline; stdlib unittest. Run from the repository root:
    python3 -m unittest discover -s tools/bigdays/tests -t . -v
The shared cases in fixtures/cases.json also run in Node (tests/bigdays.test.js)."""
import copy
import datetime as dt
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOL = HERE.parent
sys.path.insert(0, str(TOOL))
import bigdays  # noqa: E402
import fetch_weather  # noqa: E402
import inline  # noqa: E402
import validate  # noqa: E402

FIX = HERE / "fixtures"
CASES = json.loads((FIX / "cases.json").read_text())
NWS = FIX / "nws"


def texts(tags):
    return [t["text"] for t in tags]


class DateMath(unittest.TestCase):
    def test_add_days_across_year_end_and_dst(self):
        for iso, n, want in CASES["add_days"]:
            self.assertEqual(bigdays.add_days(iso, n), want, (iso, n))

    def test_window_is_eight_weeks_from_start(self):
        for start, last in CASES["window"]:
            w = bigdays.window(start)
            self.assertEqual(len(w), 56)
            self.assertEqual((w[0], w[-1]), (start, last))

    def test_grid_rows_sunday_first(self):
        for start, rows, lead, last in CASES["grid"]:
            g = bigdays.grid_rows(start)
            self.assertEqual(len(g), rows, start)
            self.assertTrue(all(len(r) == 7 for r in g))
            self.assertEqual(g[0][:lead], [None] * lead)
            self.assertEqual(g[0][lead], start)
            flat = [x for r in g for x in r if x]
            self.assertEqual(flat[-1], last)
            self.assertEqual(len(flat), 56)

    def test_weekday_and_format(self):
        for iso, wd in CASES["weekday"]:
            self.assertEqual(bigdays.weekday(iso), wd)
        for iso, y, w, want in CASES["fmt_date"]:
            self.assertEqual(bigdays.fmt_date(iso, y, w), want)

    def test_today_is_los_angeles_date(self):
        # 07:30 UTC on Jan 1 is still Dec 31 in Los Angeles
        self.assertEqual(bigdays.today_la(dt.datetime(2027, 1, 1, 7, 30, tzinfo=dt.timezone.utc)), "2026-12-31")
        self.assertEqual(bigdays.today_la(dt.datetime(2027, 1, 1, 8, 30, tzinfo=dt.timezone.utc)), "2027-01-01")


class Holidays(unittest.TestCase):
    def test_rule_dates(self):
        rules = {n: r for n, k, r in bigdays.HOLIDAY_RULES}
        for year, name, want in CASES["rule_dates"]:
            self.assertEqual(bigdays.rule_date(year, rules[name]), want, (year, name))

    def test_holiday_tags_and_three_day_weekends(self):
        for iso, want in CASES["holiday_tags"]:
            self.assertEqual(texts(bigdays.holiday_tags(iso)), want, iso)

    def test_three_day_weekend_derivation(self):
        hols = bigdays.holidays(2026)
        w = bigdays.three_day_weekends(hols)
        names = [x["holiday"] for x in w]
        self.assertNotIn("Thanksgiving Day", names)        # Thursday
        self.assertNotIn("Veterans Day", names)            # Wednesday in 2026
        self.assertIn("Christmas Day", names)              # Friday in 2026
        self.assertIn("Day after Thanksgiving", names)     # California, Gov. Code 19853
        for x in w:
            wds = [bigdays.weekday(d) for d in x["dates"]]
            if x["holiday"] == "Day after Thanksgiving":
                self.assertEqual(x["dates"], ["2026-11-26", "2026-11-27", "2026-11-28", "2026-11-29"])  # Thu to Sun
                self.assertEqual(wds, [3, 4, 5, 6])
            else:
                self.assertEqual(len(x["dates"]), 3)
                self.assertIn(wds, ([5, 6, 0], [4, 5, 6]))

    def test_holidays_json_matches_rules(self):
        doc = json.loads((bigdays.DATA_DIR / "holidays.json").read_text())
        self.assertEqual(doc, bigdays.holidays_doc(doc["meta"]["years"]))
        # and the per-day tags agree with the file for every day of the covered years
        years = [str(y) for y in doc["meta"]["years"]]
        named, weekend = {}, set()
        for h in doc["holidays"]:
            named.setdefault(h["date"], []).append(h["name"])
            if h["observed"] != h["date"]:
                named.setdefault(h["observed"], []).append(h["name"] + " (observed)")
        for w in doc["three_day_weekends"]:
            weekend.update(w["dates"])
        iso = years[0] + "-01-01"
        while iso[:4] in years:
            tags = bigdays.holiday_tags(iso)
            self.assertEqual(sorted(t["text"] for t in tags if t["type"] == "holiday"), sorted(named.get(iso, [])), iso)
            self.assertEqual(any(t["type"] == "weekend" for t in tags), iso in weekend, iso)
            iso = bigdays.add_days(iso, 1)

    def test_holidays_json_covers_the_window(self):
        doc = json.loads((bigdays.DATA_DIR / "holidays.json").read_text())
        for y in (2026, 2027):
            self.assertIn(y, doc["meta"]["years"])


class EventsBreaksHarvest(unittest.TestCase):
    D = CASES["data"]

    def test_harvest(self):
        for iso, want in CASES["harvest"]:
            self.assertEqual(bigdays.is_harvest(iso), want, iso)

    def test_county_toggle(self):
        for iso, county, want in CASES["event_tags"]:
            self.assertEqual(texts(bigdays.event_tags(iso, self.D["events"]["events"], county)), want, (iso, county))

    def test_break_ranges(self):
        for iso, want in CASES["break_tags"]:
            self.assertEqual(texts(bigdays.break_tags(iso, self.D["school_breaks"])), want, iso)

    def test_day_tags_and_verified_through(self):
        for iso, county, want in CASES["day_tags"]:
            self.assertEqual(texts(bigdays.day_tags(iso, self.D, county)), want, (iso, county))

    def test_published_district_ranges(self):
        """The 2026-27 breaks as read from each district's own calendar on 2026-11-01."""
        sb = json.loads((bigdays.DATA_DIR / "school_breaks.json").read_text())
        got = {(d["id"], b["tag"]): (b["start"], b["end"]) for d in sb["districts"] for b in d["breaks"]}
        want = {
            ("SFUSD", "SFUSD Thanksgiving break"): ("2026-11-23", "2026-11-27"),
            ("SFUSD", "SFUSD winter break"): ("2026-12-21", "2027-01-01"),
            ("SFUSD", "SFUSD spring break"): ("2027-03-26", "2027-04-02"),
            ("OUSD", "OUSD Thanksgiving break"): ("2026-11-23", "2026-11-27"),
            ("OUSD", "OUSD winter break"): ("2026-12-21", "2027-01-04"),
            ("OUSD", "OUSD spring break"): ("2027-03-29", "2027-04-02"),
            ("SJUSD", "SJUSD Thanksgiving break"): ("2026-11-25", "2026-11-27"),
            ("SJUSD", "SJUSD winter break"): ("2026-12-21", "2027-01-01"),
            ("SJUSD", "SJUSD February break"): ("2027-02-15", "2027-02-19"),
            ("SJUSD", "SJUSD spring break"): ("2027-04-05", "2027-04-09"),
        }
        self.assertEqual(got, want)
        for iso in ("2026-11-22", "2027-01-05"):
            self.assertEqual(bigdays.break_tags(iso, sb), [], iso)
        self.assertEqual(texts(bigdays.break_tags("2027-01-04", sb)), ["OUSD winter break"])


class Weather(unittest.TestCase):
    def periods(self, name):
        return fetch_weather.trim_periods(json.loads((NWS / name).read_text()))

    def test_weather_word(self):
        for period, threshold, want in CASES["weather_word"]:
            self.assertEqual(bigdays.weather_word(period, threshold), want, period)

    def test_reduction_against_fixtures(self):
        for c in CASES["reduce"]:
            got = bigdays.reduce_periods(self.periods(c["fixture"]), c["today"], c["threshold"])
            self.assertEqual(got, c["want"], c["why"])

    def test_night_periods_never_used(self):
        per = self.periods("synthetic-forecast.json")
        nights_only = [p for p in per if not p["isDaytime"]]
        self.assertEqual(bigdays.reduce_periods(nights_only, "2026-12-30", 95), {})

    def test_nothing_past_day_seven(self):
        got = bigdays.reduce_periods(self.periods("synthetic-forecast.json"), "2026-12-30", 95)
        self.assertEqual(max(got), "2027-01-05")
        self.assertNotIn("2027-01-06", got)

    def test_weather_tags(self):
        docs = CASES["docs"]
        for c in CASES["weather_tag"]:
            doc = docs.get(c["doc"]) if c["doc"] else None
            area = bigdays.weather_area(doc, c["county"], CASES["points"], c["today"], bigdays.parse_iso(c["now"]))
            tag = bigdays.weather_tag(c["iso"], c["today"], area)
            self.assertEqual(tag["text"] if tag else None, c["want"], c)

    def test_fetch_offline_from_recorded_fixtures(self):
        points = json.loads((bigdays.DATA_DIR / "weather_points.json").read_text())
        doc, errors = fetch_weather.build(fetch_weather.FixtureSource(NWS), points,
                                          dt.datetime(2026, 11, 1, 15, tzinfo=dt.timezone.utc))
        self.assertEqual(errors, {})
        self.assertEqual(fetch_weather.validate(doc), [])
        self.assertEqual(doc["points"]["napa"]["label"], "Napa area")
        self.assertEqual(doc["points"]["sonoma"]["label"], "Santa Rosa area")
        self.assertEqual(len(doc["points"]["napa"]["periods"]), 14)

    def test_fetch_refuses_empty_or_failed(self):
        points = json.loads((bigdays.DATA_DIR / "weather_points.json").read_text())
        with tempfile.TemporaryDirectory() as tmp:
            # only the Napa responses: Sonoma fails, so nothing may be written
            for f in ("napa-points.json", "napa-forecast.json"):
                shutil.copy(NWS / f, tmp)
            out = Path(tmp) / "weather.json"
            with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
                rc = fetch_weather.main(["--fixtures", tmp, "--out", str(out)])
            self.assertNotEqual(rc, 0)
            self.assertFalse(out.exists())
            few = fetch_weather.trim_periods(json.loads((NWS / "napa-forecast.json").read_text()))[:6]
            short = {"generated_at": "x", "points": {"napa": {"ok": True, "periods": few}, "sonoma": {"ok": True, "periods": few}}}
            self.assertTrue(any("daytime dates" in p for p in fetch_weather.validate(short)))
            empty = {"generated_at": "x", "points": {"napa": {"ok": True, "periods": []}, "sonoma": {"ok": True, "periods": []}}}
            self.assertTrue(fetch_weather.validate(empty))

    def test_contact_from_env_only(self):
        src = (TOOL / "fetch_weather.py").read_text()
        self.assertIn("BIGDAYS_CONTACT", src)
        self.assertIn("SMOKE_WATCH_CONTACT", src)
        self.assertNotIn("@", "".join(l for l in src.splitlines() if "User-Agent" in l))


class DataRule(unittest.TestCase):
    def data(self):
        return validate.load_all()

    def test_real_data_is_valid(self):
        self.assertEqual(validate.schema_problems(self.data()), [])

    def test_schema_rejects_bad(self):
        d = self.data()
        bad = copy.deepcopy(d)
        bad["events"]["events"] = []
        self.assertTrue(validate.schema_problems(bad))
        bad = copy.deepcopy(d)
        bad["events"]["events"][0]["county"] = "marin"
        self.assertTrue(validate.schema_problems(bad))
        bad = copy.deepcopy(d)
        bad["events"]["events"][0]["start"] = None
        bad["events"]["events"][0]["end"] = None        # confirmed with no dates
        self.assertTrue(validate.schema_problems(bad))
        bad = copy.deepcopy(d)
        bad["events"]["events"][0]["end"] = "2020-01-01"
        self.assertTrue(validate.schema_problems(bad))
        bad = copy.deepcopy(d)
        bad["school_breaks"]["districts"][0]["breaks"][0]["tag"] = "spring break"   # district not named
        self.assertTrue(validate.schema_problems(bad))
        bad = copy.deepcopy(d)
        bad["holidays"]["holidays"].pop()
        self.assertTrue(validate.schema_problems(bad))
        bad = copy.deepcopy(d)
        bad["weather_points"]["days"] = 10
        self.assertTrue(validate.schema_problems(bad))

    def test_reduction_rule(self):
        d = self.data()
        self.assertEqual(validate.reductions(d, d), [])
        fewer = copy.deepcopy(d)
        fewer["events"]["events"].pop(0)
        self.assertTrue(any("removed" in p for p in validate.reductions(fewer, d)))
        down = copy.deepcopy(d)
        conf = next(e for e in down["events"]["events"] if e["status"] == "confirmed")
        conf["status"] = "canceled"
        self.assertTrue(any("no longer confirmed" in p for p in validate.reductions(down, d)))
        back = copy.deepcopy(d)
        back["events"]["meta"]["verified_through"] = bigdays.add_days(d["events"]["meta"]["verified_through"], -1)
        self.assertTrue(validate.reductions(back, d))
        nb = copy.deepcopy(d)
        nb["school_breaks"]["districts"].pop()
        self.assertTrue(validate.reductions(nb, d))
        short = copy.deepcopy(d)
        conf = next(e for e in short["events"]["events"] if e["status"] == "confirmed" and e["start"] != e["end"])
        conf["end"] = conf["start"]
        self.assertTrue(any("no longer covers" in p for p in validate.reductions(short, d)))
        sb = copy.deepcopy(d)
        sb["school_breaks"]["districts"][0]["breaks"][0]["end"] = sb["school_breaks"]["districts"][0]["breaks"][0]["start"]
        self.assertTrue(any("shrank" in p for p in validate.reductions(sb, d)))
        longer = copy.deepcopy(d)
        longer["events"]["events"].append(dict(longer["events"]["events"][0], id="extra-event"))
        self.assertEqual(validate.reductions(longer, d), [])

    def test_check_refuses_reduction_unless_allowed(self):
        d = self.data()
        fewer = copy.deepcopy(d)
        fewer["events"]["events"].pop(0)
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(inline.payload(d), f)
            base = f.name
        try:
            ok, problems, _ = validate.check(fewer, baseline_path=base)
            self.assertFalse(ok)
            ok, problems, notes = validate.check(fewer, allow_removals=True, baseline_path=base)
            self.assertTrue(ok, problems)
            ok, _, _ = validate.check(d, baseline_path=base)
            self.assertTrue(ok)
        finally:
            Path(base).unlink()

    def test_inlined_baseline_is_read_from_page(self):
        doc = validate.inlined(inline.PAGE.read_text())
        self.assertIsNotNone(doc)
        label, base = validate.baseline()
        self.assertIn("inlined", label)


class Inline(unittest.TestCase):
    def test_page_matches_json(self):
        page = inline.PAGE.read_text()
        data = validate.load_all()
        self.assertEqual(validate.inlined(page), inline.payload(data),
                         "tools/bigdays/index.html is out of date; run python3 tools/bigdays/inline.py")
        start = inline.stored_start(page)
        self.assertIsNotNone(start)
        self.assertEqual(inline.render_page(page, data, start), page)
        self.assertEqual(inline.sources_html(data), inline.SOURCES.read_text())

    def test_inline_check_command(self):
        r = subprocess.run([sys.executable, str(TOOL / "inline.py"), "--check"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_sources_page_lists_everything(self):
        data = validate.load_all()
        s = inline.SOURCES.read_text()
        for e in data["events"]["events"]:
            self.assertIn(e["source_url"].replace("&", "&amp;"), s)
        for dist in data["school_breaks"]["districts"]:
            self.assertIn(dist["source_url"].replace("&", "&amp;"), s)
        for name, kind, rule in bigdays.HOLIDAY_RULES:
            self.assertIn(inline.esc(name), s)

    def test_copy_rules(self):
        page = inline.PAGE.read_text()
        for f in (inline.PAGE, inline.SOURCES, TOOL / "bigdays.js", TOOL / "bigdays.css"):
            self.assertNotIn("—", f.read_text(), f.name)
        self.assertIn("Public calendars, listed, not scored. Sources:", page)
        # "forecast" appears only on the weather line (the lede and meta text never use it)
        visible = [l for l in page.splitlines() if "forecast" in l.lower()]
        self.assertEqual(len(visible), 1, visible)
        self.assertIn('class="weather-line"', visible[0])
        self.assertNotIn("forecast", inline.SOURCES.read_text().lower())

    def test_no_js_agenda_hidden_controls(self):
        page = inline.PAGE.read_text()
        self.assertEqual(page.count("<li><h3>"), 56)
        self.assertIn('class="controls js-only" hidden', page)
        self.assertIn('class="cal js-only" aria-label="Eight weeks" hidden', page)
        css = (TOOL / "bigdays.css").read_text()
        self.assertIn("[hidden] { display: none !important; }", css)
        self.assertIn("html.js .nojs-only", css)


if __name__ == "__main__":
    unittest.main()
