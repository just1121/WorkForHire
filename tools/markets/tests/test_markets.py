"""Farmers market map tests. Offline: shared schedule cases in tests/fixtures/cases.json (also run by
tests/markets.test.js against markets.js) and a saved pypdf layout extract of the CDFA PDF pages for the
five counties (tests/fixtures/cdfa-layout-pages.txt, from the October 1, 2026 list).
Run from the repository root:  python3 -m unittest discover -s tools/markets/tests -t . -v
"""
import copy
import datetime as dt
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import cdfa_check  # noqa: E402
import inline  # noqa: E402
import schedule  # noqa: E402
import validate  # noqa: E402

FIX = HERE / "fixtures"
CASES = json.loads((FIX / "cases.json").read_text())
M = CASES["markets"]
DATA_PATH = validate.DATA
DATA = json.loads(DATA_PATH.read_text())


def D(s):
    return dt.date.fromisoformat(s)


def n_on(market_id, date):
    return len(schedule.sessions_on(M[market_id], D(date)))


def st(market_id, date, hm):
    return schedule.state(M[market_id], D(date), hm)


class SharedCases(unittest.TestCase):
    """Every case in cases.json; markets.test.js runs the same list."""

    def test_occurs(self):
        for m, d, n, why in CASES["occurs"]:
            with self.subTest(market=m, date=d, why=why):
                self.assertEqual(n_on(m, d), n)

    def test_state(self):
        for m, d, hm, want, why in CASES["state"]:
            with self.subTest(market=m, date=d, hm=hm, why=why):
                self.assertEqual(st(m, d, hm), want)

    def test_scheduled_now(self):
        for m, d, hm, want in CASES["scheduled_now"]:
            with self.subTest(market=m, date=d, hm=hm):
                self.assertEqual(schedule.scheduled_now(M[m], D(d), hm), want)

    def test_la_now_dst_and_year_end(self):
        for utc, d, hm, why in CASES["la_now"]:
            with self.subTest(utc=utc, why=why):
                inst = dt.datetime.fromisoformat(utc.replace("Z", "+00:00"))
                self.assertEqual(schedule.la_now(inst), (D(d), hm))

    def test_words(self):
        for case in CASES["words"]:
            session, vt, text = case[:3]
            inferred = case[3] if len(case) > 3 else False
            with self.subTest(text=text):
                self.assertEqual(schedule.describe_session(session, vt, inferred), text)

    def test_stale(self):
        for checked, today, want in CASES["stale"]:
            with self.subTest(checked=checked, today=today):
                self.assertEqual(schedule.is_stale(checked, D(today)), want)

    def test_fixture_markets_are_valid_records(self):
        for k, m in M.items():
            with self.subTest(market=k):
                self.assertEqual(validate.check_market(m, 0), [])


class SpecFixtures(unittest.TestCase):
    """One test per fixture case named in tools/MARKETS.md."""

    def test_first_thursday_only(self):
        self.assertEqual(n_on("first-thursdays", "2026-06-04"), 1)
        for d in ("2026-06-11", "2026-06-18", "2026-06-25"):
            self.assertEqual(n_on("first-thursdays", d), 0)

    def test_explicit_date_list(self):
        listed = M["date-list"]["sessions"][0]["dates"]
        for d in listed:
            self.assertEqual(n_on("date-list", d), 1)
        fridays = [D("2026-05-15") + dt.timedelta(days=7 * i) for i in range(18)]
        self.assertEqual(sum(n_on("date-list", f.isoformat()) for f in fridays), len(listed))

    def test_different_seasons_on_different_days(self):
        self.assertEqual(n_on("two-seasons", "2026-02-07"), 1)  # Saturday, year-round
        self.assertEqual(n_on("two-seasons", "2026-02-10"), 0)  # Tuesday, before April
        self.assertEqual(n_on("two-seasons", "2026-06-09"), 1)  # Tuesday, in season

    def test_exact_date_season(self):
        self.assertEqual(n_on("exact-season", "2026-04-30"), 0)
        self.assertEqual(n_on("exact-season", "2026-10-08"), 1)
        self.assertEqual(n_on("exact-season", "2026-10-15"), 0)

    def test_two_windows_in_one_day(self):
        got = schedule.sessions_on(M["two-windows"], D("2026-07-01"))
        self.assertEqual([s["start"] for s, _ in got], ["10:00", "16:30"])
        self.assertEqual(st("two-windows", "2026-07-01", "14:00"), "today")

    def test_exception_on_a_scheduled_day(self):
        self.assertEqual(n_on("exception", "2026-11-28"), 0)
        moved = schedule.sessions_on(M["exception"], D("2026-12-05"))
        self.assertEqual(moved[0][1]["status"], "moved")

    def test_moved_day_is_not_scheduled_today_or_now(self):
        self.assertNotEqual(st("exception", "2026-12-05", "08:00"), "today")
        self.assertFalse(schedule.scheduled_now(M["exception"], D("2026-12-05"), "10:00"))

    def test_moved_day_stays_in_this_week_with_its_note(self):
        week = schedule.this_week([M["exception"]], D("2026-12-05"), "15:00")  # after the old end time
        rows = week[0][1]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][2]["note"], "Moved to the north lot for the parade.")

    def test_confirm_each_year_past_valid_through(self):
        self.assertEqual(st("expired", "2026-06-07", "10:00"), "unconfirmed")
        self.assertEqual(schedule.state_label("unconfirmed", D("2026-06-07")), "Schedule not yet confirmed for 2026")


class Rules(unittest.TestCase):
    def test_session_drops_out_of_today_at_end(self):
        self.assertEqual(st("saturday", "2026-10-31", "11:59"), "today")
        self.assertEqual(st("saturday", "2026-10-31", "12:00"), "season")

    def test_this_week_is_rolling_seven_days(self):
        week = schedule.this_week([M["saturday"], M["two-windows"]], D("2026-07-01"), "12:00")
        self.assertEqual([d for d, _ in week], [D("2026-07-01") + dt.timedelta(days=i) for i in range(7)])
        today_rows = week[0][1]
        self.assertEqual([s["start"] for _, s, _ in today_rows], ["10:00", "16:30"])  # 10:00 still running
        week = schedule.this_week([M["two-windows"]], D("2026-07-01"), "13:00")
        self.assertEqual([s["start"] for _, s, _ in week[0][1]], ["16:30"])           # first window ended
        sat = week[3]
        self.assertEqual(sat[0], D("2026-07-04"))
        self.assertEqual(len(sat[1]), 1)

    def test_this_week_across_year_end(self):
        week = schedule.this_week([M["wrap"]], D("2026-12-29"), "09:00")
        days = [(d.isoformat(), len(r)) for d, r in week if r]
        self.assertEqual(days, [("2027-01-01", 1)])

    def test_this_week_skips_closed_exception(self):
        week = schedule.this_week([M["exception"]], D("2026-11-23"), "09:00")
        self.assertEqual(sum(len(r) for _, r in week), 0)

    def test_season_boundaries_are_inclusive(self):
        s = M["two-seasons"]["sessions"][1]
        self.assertTrue(schedule.in_season(s, D("2026-04-01"), None))
        self.assertTrue(schedule.in_season(s, D("2026-12-31"), None))
        self.assertFalse(schedule.in_season(s, D("2026-03-31"), None))

    def test_year_round_filter(self):
        self.assertTrue(schedule.year_round(M["two-seasons"]))
        self.assertFalse(schedule.year_round(M["exact-season"]))
        self.assertTrue(schedule.year_round(M["mixed-expired"]))

    def test_weekday_filter(self):
        self.assertTrue(schedule.has_weekday(M["two-seasons"], "Tue"))
        self.assertFalse(schedule.has_weekday(M["two-seasons"], "Wed"))
        self.assertTrue(schedule.has_weekday(M["date-list"], "Fri"))

    def test_labels_are_the_spec_words(self):
        self.assertEqual(set(schedule.STATES.values()), {
            "Scheduled today", "Scheduled this week", "In season, not this week", "Closed for season",
            "Schedule not yet confirmed"})

    def test_markets_js_mirrors_constants(self):
        js = (HERE.parent / "markets.js").read_text()
        for label in schedule.STATES.values():
            self.assertIn('"%s"' % label, js)
        self.assertIn('"America/Los_Angeles"', js)
        self.assertIn("STALE_DAYS = %d" % schedule.STALE_DAYS, js)


class Schema(unittest.TestCase):
    def test_data_file_is_valid(self):
        self.assertEqual(validate.validate(DATA), [])

    def test_geo_and_schedule_source_on_every_record(self):
        for m in DATA["markets"]:
            with self.subTest(id=m["id"]):
                self.assertIn(m["geo_source"], ("census", "manual"))
                self.assertTrue(m["schedule_source"] == "cdfa" or m["schedule_source"].startswith("http"))
                self.assertTrue(m["schedule_checked"])

    def test_counts_match_cdfa_snapshot(self):
        meta, _ = cdfa_check.read_snapshot()
        want = dict((k, int(v)) for k, v in (kv.split("=") for kv in meta["counts"].split()))
        got = {c: sum(1 for m in DATA["markets"] if m["county"] == c) for c in validate.COUNTIES}
        self.assertEqual(got, want)

    def test_no_invented_contact_fields(self):
        # Seeded from CDFA only; a website or handle needs a source before it goes in.
        for m in DATA["markets"]:
            if m["instagram"] is not None:
                self.assertNotIn("@", m["instagram"])

    def test_guessed_years_not_printed_as_fact(self):
        # five were inferred from CDFA; three were confirmed from official sources on 2026-11-01
        # (First Thursdays, Cloverdale, Sonoma Tuesday Night), so two remain
        guessed = {"marin-novato-hamilton", "sonoma-bodega-bay"}
        self.assertEqual({m["id"] for m in DATA["markets"] if m["season_year_inferred"]}, guessed)
        page = inline.PAGE.read_text()
        for m in DATA["markets"]:
            if m["id"] in guessed:
                text = schedule.describe_session(m["sessions"][0], m["schedule_valid_through"], True)
                self.assertIn("(year not given by CDFA)", text)
                self.assertNotIn(m["schedule_valid_through"][:4], text)
                self.assertIn(text, page.replace("&#x27;", "'"))

    def test_confirm_each_year_has_valid_through(self):
        for m in DATA["markets"]:
            if any(s["recurrence"] == "confirm_each_year" for s in m["sessions"]):
                self.assertIsNotNone(m["schedule_valid_through"], m["id"])

    def _bad(self, mutate):
        d = copy.deepcopy(DATA)
        mutate(d)
        return validate.validate(d)

    def test_rejects(self):
        cases = {
            "empty": lambda d: d.__setitem__("markets", []),
            "missing geo_source": lambda d: d["markets"][0].pop("geo_source"),
            "missing schedule_source": lambda d: d["markets"][0].pop("schedule_source"),
            "bad geo_source": lambda d: d["markets"][0].__setitem__("geo_source", "google"),
            "bad recurrence": lambda d: d["markets"][0]["sessions"][0].__setitem__("recurrence", "weekly"),
            "end before start": lambda d: d["markets"][0]["sessions"][0].__setitem__("end", "07:00"),
            "pin in the ocean": lambda d: d["markets"][0].__setitem__("lon", -125.5),
            "instagram with @": lambda d: d["markets"][0].__setitem__("instagram", "@market"),
            "duplicate id": lambda d: d["markets"][1].__setitem__("id", d["markets"][0]["id"]),
            "empty county": lambda d: d.__setitem__("markets", [m for m in d["markets"] if m["county"] != "Lake"]),
            "confirm_each_year without valid_through": lambda d: (
                d["markets"][0]["sessions"][0].update(recurrence="confirm_each_year", season_start="05-01", season_end="10-31"),
                d["markets"][0].__setitem__("schedule_valid_through", None)),
            "date off its weekday": lambda d: d["markets"][0]["sessions"].__setitem__(0, dict(
                d["markets"][0]["sessions"][0], recurrence="confirm_each_year", dates=["2026-05-16"],
                season_start=None, season_end=None, nth_of_month=None, weekday="Fri")),
            "bad exception status": lambda d: d["markets"][0].__setitem__("exceptions", [
                {"date": "2026-11-26", "status": "maybe", "note": "x", "source": "cdfa"}]),
            "valid_through before last listed date": lambda d: next(
                m for m in d["markets"] if m["id"] == "marin-san-rafael-summer").__setitem__(
                "schedule_valid_through", "2026-08-01"),
            "schedule_checked in the future": lambda d: d["markets"][0].__setitem__("schedule_checked", "2999-01-01"),
            "season_year_inferred not a bool": lambda d: d["markets"][0].__setitem__("season_year_inferred", None),
        }
        for name, fn in cases.items():
            with self.subTest(case=name):
                self.assertTrue(self._bad(fn), name)


class Reduction(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.base = self.tmp / "base.json"
        self.base.write_text(json.dumps(DATA))

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def write(self, d):
        p = self.tmp / "new.json"
        p.write_text(json.dumps(d))
        return p

    def test_fewer_markets_refused(self):
        d = copy.deepcopy(DATA)
        d["markets"].pop()
        ok, _ = validate.check_file(self.write(d), baseline=self.base, quiet=True)
        self.assertFalse(ok)

    def test_fewer_markets_allowed_by_hand(self):
        d = copy.deepcopy(DATA)
        d["markets"].pop()
        ok, _ = validate.check_file(self.write(d), allow_removals=True, baseline=self.base, quiet=True)
        self.assertTrue(ok)

    def test_same_or_more_is_fine(self):
        ok, _ = validate.check_file(self.write(DATA), baseline=self.base, quiet=True)
        self.assertTrue(ok)
        d = copy.deepcopy(DATA)
        extra = copy.deepcopy(d["markets"][0])
        extra["id"] = "napa-new-market"
        d["markets"].append(extra)
        ok, _ = validate.check_file(self.write(d), baseline=self.base, quiet=True)
        self.assertTrue(ok)

    def test_reports_removed_ids(self):
        d = copy.deepcopy(DATA)
        gone = d["markets"].pop(3)["id"]
        problems, removed = validate.reduction(d, DATA)
        self.assertEqual(removed, [gone])
        self.assertIn("--allow-removals", problems[0])

    def test_id_swapped_same_total_refused(self):
        d = copy.deepcopy(DATA)
        d["markets"][0]["id"] = "napa-renamed-market"
        ok, _ = validate.check_file(self.write(d), baseline=self.base, quiet=True)
        self.assertFalse(ok)
        ok, _ = validate.check_file(self.write(d), allow_removals=True, baseline=self.base, quiet=True)
        self.assertTrue(ok)

    def test_county_count_drop_refused(self):
        d = copy.deepcopy(DATA)
        m = next(x for x in d["markets"] if x["county"] == "Napa")
        m["county"] = "Sonoma"   # same ids, same total, but Napa loses one
        problems, _ = validate.reduction(d, DATA)
        self.assertTrue(problems)
        self.assertIn("Napa has 7 markets, down from 8", problems[0])
        self.assertEqual(validate.reduction(d, DATA, allow_removals=True)[0], [])

    def test_fewer_sessions_refused(self):
        d = copy.deepcopy(DATA)
        m = next(x for x in d["markets"] if x["id"] == "napa-napa")
        m["sessions"].pop()
        problems, _ = validate.reduction(d, DATA)
        self.assertTrue(problems)
        self.assertIn("napa-napa has 1 sessions, down from 2", problems[0])
        self.assertEqual(validate.reduction(d, DATA, allow_removals=True)[0], [])

    def test_baseline_prefers_inlined_page(self):
        data, where = validate.last_published()
        self.assertIn("index.html", where)
        self.assertEqual(data, DATA)

    def test_invalid_file_refused(self):
        p = self.tmp / "broken.json"
        p.write_text("{not json")
        ok, _ = validate.check_file(p, baseline=self.base, quiet=True)
        self.assertFalse(ok)


class Inline(unittest.TestCase):
    def test_inlined_block_matches_json(self):
        page = inline.PAGE.read_text()
        block = validate.inlined_block(page)
        self.assertIsNotNone(block, "index.html has no inlined data; run tools/markets/inline.py")
        self.assertEqual(json.loads(block), DATA, "inlined data differs; run tools/markets/inline.py")

    def test_page_is_up_to_date(self):
        page = inline.PAGE.read_text()
        self.assertEqual(inline.render(page, DATA), page, "run python3 tools/markets/inline.py")

    def test_render_is_deterministic_and_idempotent(self):
        page = inline.PAGE.read_text()
        once = inline.render(page, DATA)
        self.assertEqual(inline.render(once, DATA), once)

    def test_script_close_is_escaped(self):
        d = copy.deepcopy(DATA)
        d["markets"][0]["notes"] = "a </script> in a note"
        block = inline.json_block(d)
        self.assertEqual(block.count("</script>"), 1)
        page = inline.render(inline.PAGE.read_text(), d)
        self.assertEqual(json.loads(validate.inlined_block(page)), d)

    def test_no_js_list_has_every_market(self):
        page = inline.PAGE.read_text()
        for m in DATA["markets"]:
            self.assertIn('id="m-%s"' % m["id"], page)
        self.assertIn("Hours change; the market's own site wins.", page)
        self.assertIn("October 1, 2026", page)

    def test_refuses_invalid_data(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            bad = copy.deepcopy(DATA)
            bad["markets"][0]["geo_source"] = None
            (tmp / "markets.json").write_text(json.dumps(bad))
            page = tmp / "index.html"
            shutil.copy(inline.PAGE, page)
            before = page.read_text()
            old = (inline.DATA, inline.PAGE)
            inline.DATA, inline.PAGE = tmp / "markets.json", page
            try:
                rc = inline.main(["--baseline", str(DATA_PATH)])
            finally:
                inline.DATA, inline.PAGE = old
            self.assertEqual(rc, 1)
            self.assertEqual(page.read_text(), before)
        finally:
            shutil.rmtree(tmp)

    def test_page_house_rules(self):
        page = inline.PAGE.read_text()
        self.assertNotIn("\u2014", page)                       # no em dashes
        self.assertIn('integrity="sha256-', page)              # Leaflet pinned with SRI
        self.assertIn("leaflet@1.9.4", page)
        self.assertEqual(page.count("fonts.googleapis.com/css2"), 1)
        for f in ("markets.js", "markets.css"):
            self.assertNotIn("\u2014", (HERE.parent / f).read_text())

    def test_scheduled_not_open_and_no_js_rules(self):
        page = inline.PAGE.read_text()
        head = page.split("<!-- markets-data:start -->")[0]
        self.assertIn('<label for="f-scheduled">Scheduled now</label>', head)
        self.assertNotIn("Open now", head)
        self.assertNotIn("what's open", head)
        self.assertNotIn("[", head.split("<body>")[1])            # no bracketed placeholders left
        self.assertIn('class="mapbox js-only"', head)              # map hidden until JS runs
        self.assertIn('<form id="filters" class="filters" hidden>', head)
        self.assertIn('<div id="week-days" hidden>', head)
        css = (HERE.parent / "markets.css").read_text()
        self.assertIn("[hidden] { display: none !important; }", css)
        self.assertIn("html:not(.js) .js-only", css)
        js = (HERE.parent / "markets.js").read_text()
        self.assertIn("iconSize: [44, 44]", js)
        self.assertIn('ev.key === " "', js)


class CdfaCheck(unittest.TestCase):
    TEXT = (FIX / "cdfa-layout-pages.txt").read_text()

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def run_check(self, text=None, fail=False, pdf=b"%PDF-1.6 fixture", snapshot=cdfa_check.SNAPSHOT):
        def extract(_path):
            if fail:
                raise ValueError("cannot read")
            return self.TEXT if text is None else text
        return cdfa_check.check(pdf, snapshot, extract=extract, tmp_dir=self.tmp)

    def test_as_of_and_counts(self):
        self.assertEqual(cdfa_check.as_of(self.TEXT), "October 1, 2026")
        self.assertEqual(cdfa_check.count_rows(self.TEXT),
                         {"Napa": 8, "Sonoma": 24, "Marin": 14, "Mendocino": 6, "Lake": 3})

    def test_sections_match_snapshot(self):
        _, snap = cdfa_check.read_snapshot()
        self.assertEqual(cdfa_check.sections(self.TEXT), snap)

    def test_unchanged_content_verifies(self):
        r = self.run_check()
        self.assertTrue(r["extraction_ok"])
        self.assertTrue(r["verification_ok"])
        self.assertEqual(r["problems"], [])

    def test_whitespace_ignored(self):
        r = self.run_check(self.TEXT.replace("  ", "     "))
        self.assertEqual(r["problems"], [])

    def test_new_as_of_is_flagged_but_not_diffed(self):
        r = self.run_check(self.TEXT.replace("as of October 1, 2026", "as of November 1, 2026"))
        self.assertEqual(r["as_of"], "November 1, 2026")
        self.assertEqual(r["diff"], {})
        self.assertEqual(len(r["problems"]), 1)
        self.assertIn("now dated November 1, 2026", r["problems"][0])

    def test_missing_as_of_is_flagged(self):
        r = self.run_check(self.TEXT.replace("as of October 1, 2026", ""))
        self.assertIsNone(r["as_of"])
        self.assertTrue(any("as of" in p for p in r["problems"]))

    def test_as_of_matches_data(self):
        self.assertEqual(cdfa_check.as_of_iso("October 1, 2026"), DATA["source"]["as_of"])

    def test_missing_pdf(self):
        for pdf in (None, b"", b"<html>not found</html>"):
            r = cdfa_check.check(pdf, cdfa_check.SNAPSHOT, extract=lambda p: self.TEXT, tmp_dir=self.tmp)
            self.assertFalse(r["pdf_ok"])
            self.assertTrue(r["problems"])

    def test_checksum_changed_and_extraction_failed(self):
        r = self.run_check(fail=True)
        self.assertTrue(r["checksum_changed"])
        self.assertFalse(r["extraction_ok"])
        self.assertTrue(r["problems"])

    def test_extraction_failed_same_checksum_is_logged_not_filed(self):
        snap = self.tmp / "snap.txt"
        pdf = b"%PDF-1.6 same"
        import hashlib
        text = cdfa_check.SNAPSHOT.read_text().replace(
            "# sha256: 846d121f3e38ec1ee5f83c95c2260a6ae3857b11b3ad88d31ba9dc548fb06742",
            "# sha256: " + hashlib.sha256(pdf).hexdigest())
        snap.write_text(text)
        r = self.run_check(fail=True, pdf=pdf, snapshot=snap)
        self.assertFalse(r["checksum_changed"])
        self.assertFalse(r["extraction_ok"])
        self.assertFalse(r["verification_ok"])
        self.assertEqual(r["problems"], [])

    def test_county_with_zero_rows(self):
        lines = [ln for ln in self.TEXT.splitlines() if not ln.startswith("Lake ")]
        r = self.run_check("\n".join(lines))
        self.assertEqual(r["counts"]["Lake"], 0)
        self.assertTrue(any("No rows found for Lake" in p for p in r["problems"]))

    def test_changed_content(self):
        text = self.TEXT.replace("Calistoga Farmers Market", "Calistoga Farmers Market Moved")
        r = self.run_check(text)
        self.assertIn("Napa", r["diff"])
        self.assertFalse(r["verification_ok"])
        body = cdfa_check.issue_body(r)
        self.assertIn("Napa diff", body)
        self.assertIn("Nothing was changed automatically", body)

    def test_reduced_rows_flagged(self):
        lines = self.TEXT.splitlines()
        i = max(k for k, ln in enumerate(lines) if ln.startswith("Mendocino "))  # the Willits row
        del lines[i]
        r = self.run_check("\n".join(lines))
        self.assertEqual(r["counts"]["Mendocino"], 5)
        self.assertTrue(any("Mendocino (5 rows, was 6)" in p for p in r["problems"]))

    def test_never_touches_data_or_page(self):
        before = (validate.DATA.read_bytes(), inline.PAGE.read_bytes())
        self.run_check(self.TEXT.replace("Napa", "Napa!"))
        self.assertEqual((validate.DATA.read_bytes(), inline.PAGE.read_bytes()), before)


if __name__ == "__main__":
    unittest.main()
