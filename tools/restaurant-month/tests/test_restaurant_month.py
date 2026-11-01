"""Restaurant Month tests. Offline. Shared rule cases in tests/fixtures/cases.json (also run by
tests/restaurant-month.test.js against restaurant-month.js); schema, reduction and inline checks here.
Run from the repository root:  python3 -m unittest discover -s tools/restaurant-month/tests -t . -v
"""
import copy
import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOL = HERE.parent
sys.path.insert(0, str(TOOL))
import inline  # noqa: E402
import rules  # noqa: E402
import validate  # noqa: E402

CASES = json.loads((HERE / "fixtures" / "cases.json").read_text())
P = CASES["promotions"]
ENTRIES = CASES["entries"]
DATA_PATH = validate.DATA
DATA = json.loads(DATA_PATH.read_text())
TODAY = "2026-11-01"


def promos(ids):
    return [P[i] for i in ids]


class Buckets(unittest.TestCase):
    def test_boundaries(self):
        for price, want in CASES["buckets"]:
            with self.subTest(price=price):
                self.assertEqual(rules.price_bucket(price), want)

    def test_each_amount_in_exactly_one_bucket(self):
        for cents in range(0, 20001, 1):
            price = cents / 100
            hits = [b for b, test in (("under30", price < 30), ("30to49", 30 <= price < 50),
                                      ("50to79", 50 <= price < 80), ("80up", price >= 80)) if test]
            self.assertEqual(hits, [rules.price_bucket(price)])

    def test_labels_are_the_spec_words(self):
        self.assertEqual([rules.BUCKET_LABELS[b] for b in rules.BUCKETS],
                         ["Under $30", "$30 to $49", "$50 to $79", "$80 and up", "No fixed price"])

    def test_prices(self):
        for price, text in CASES["prices"]:
            self.assertEqual(rules.fmt_price(price), text)


class States(unittest.TestCase):
    def test_display_state(self):
        for pid, today, want, why in CASES["states"]:
            with self.subTest(promo=pid, today=today, why=why):
                self.assertEqual(rules.display_state(P[pid], today), want)

    def test_series_view(self):
        for ids, series, today, state, cur, ref, year in CASES["views"]:
            with self.subTest(ids=ids, series=series, today=today):
                v = rules.series_view(promos(ids), series, today)
                if state is None:
                    self.assertIsNone(v)
                    continue
                self.assertEqual((v["state"], v["current"]["id"], v["reference"]["id"] if v["reference"] else None,
                                  v["next_year"]), (state, cur, ref, year))

    def test_banner(self):
        for ids, series, today, text in CASES["banners"]:
            with self.subTest(series=series, today=today):
                self.assertEqual(rules.banner_text(rules.series_view(promos(ids), series, today)), text)

    def test_countdown(self):
        for today, start, n, text in CASES["countdown"]:
            self.assertEqual(rules.countdown_days(today, start), n)
            self.assertEqual(rules.countdown_text(today, start), text)

    def test_ranges(self):
        for a, b, text in CASES["ranges"]:
            self.assertEqual(rules.fmt_range(a, b), text)

    def test_labels(self):
        self.assertEqual(rules.reference_label(P["sonoma-2026"]), "2026 participants, for reference")
        self.assertEqual(rules.confirmed_label(P["napa-2027"]), "Confirmed for 2027")

    def test_la_today_across_year_end_and_dst(self):
        import datetime as dt
        utc = dt.timezone.utc
        self.assertEqual(rules.la_today(dt.datetime(2027, 1, 1, 7, 59, tzinfo=utc)), "2026-12-31")
        self.assertEqual(rules.la_today(dt.datetime(2027, 1, 1, 8, 0, tzinfo=utc)), "2027-01-01")
        self.assertEqual(rules.la_today(dt.datetime(2026, 11, 1, 8, 30, tzinfo=utc)), "2026-11-01")
        self.assertEqual(rules.la_today(dt.datetime(2026, 11, 2, 7, 59, tzinfo=utc)), "2026-11-01")


class Offers(unittest.TestCase):
    def test_offer_on(self):
        for offer, pid, date, want, why in CASES["offer_on"]:
            with self.subTest(date=date, why=why):
                self.assertEqual(rules.offer_on(offer, P[pid], date), want)

    def test_offer_when(self):
        for offer, pid, text in CASES["offer_when"]:
            with self.subTest(text=text):
                self.assertEqual(rules.describe_offer_when(offer, P[pid]), text)

    def test_any_offer_matching(self):
        for today, f, want, why in CASES["match"]:
            with self.subTest(filters=f, why=why):
                got = [e["id"] for e in ENTRIES if rules.entry_matches(e, P[e["promotion_id"]], f, today)]
                self.assertEqual(got, want)

    def test_sweet_perk_never_sets_sort_price(self):
        promo = {"id": "p", "start": "2027-01-01", "end": "2027-01-31", "dates_confirmed": True}
        perk_only = {"id": "x1", "promotion_id": "p", "offers": [{"meal": "any", "price": 6, "description": "d"}]}
        perk_and_dinner = {"id": "x2", "promotion_id": "p", "offers": [{"meal": "any", "price": 6, "description": "d"},
                                                                      {"meal": "dinner", "price": 40, "description": "d"}]}
        brunch_only = {"id": "x3", "promotion_id": "p", "offers": [{"meal": "brunch", "price": 25, "description": "d"}]}
        self.assertIsNone(rules.lowest_eligible_price(perk_only, promo, {}, "2027-01-05"))
        self.assertEqual(rules.lowest_eligible_price(perk_and_dinner, promo, {}, "2027-01-05"), 40)
        self.assertIsNone(rules.lowest_eligible_price(brunch_only, promo, {}, "2027-01-05"))
        self.assertEqual(rules.lowest_eligible_price(brunch_only, promo, {"meal": "brunch"}, "2027-01-05"), 25)

    def test_lowest_eligible_price(self):
        for today, f, want in CASES["lowest"]:
            for e in ENTRIES:
                with self.subTest(entry=e["id"], filters=f, today=today):
                    self.assertEqual(rules.lowest_eligible_price(e, P[e["promotion_id"]], f, today), want[e["id"]])

    def test_sorts(self):
        by_id = {"napa-2027": P["napa-2027"]}
        for today, by, f, want, why in CASES["sorts"]:
            with self.subTest(by=by, why=why):
                self.assertEqual([e["id"] for e in rules.sort_entries(ENTRIES, by, by_id, f, today)], want)

    def test_meal_label_shown_once(self):
        for offer, want in CASES["labels"]:
            with self.subTest(desc=offer["description"]):
                self.assertEqual(rules.offer_label_shown(offer), want)

    def test_stale(self):
        for checked, today, want, why in CASES["stale"]:
            with self.subTest(why=why):
                self.assertEqual(rules.is_stale(checked, today), want)
        self.assertEqual(rules.stale_text("2027-01-02"), "Checked January 2, 2027; confirm with the restaurant.")

    def test_safe_url(self):
        self.assertEqual(rules.safe_url("https://example.org/"), "https://example.org/")
        for bad in ("javascript:alert(1)", "data:x", "//x", "", None):
            self.assertIsNone(rules.safe_url(bad))


class Schema(unittest.TestCase):
    def test_data_file_is_valid(self):
        self.assertEqual(validate.validate(DATA), [])

    def test_every_entry_has_a_promotion_that_exists(self):
        ids = {p["id"] for p in DATA["promotions"]}
        for e in DATA["entries"]:
            self.assertIn(e["promotion_id"], ids, e["id"])

    def test_every_offer_has_meal_and_price_or_null(self):
        for e in DATA["entries"]:
            for o in e["offers"]:
                self.assertIn(o["meal"], rules.MEALS, e["id"])
                self.assertIn("price", o, e["id"])
                self.assertTrue(o["price"] is None or isinstance(o["price"], (int, float)), e["id"])

    def test_geo_source_present(self):
        for e in DATA["entries"]:
            self.assertIn(e["geo_source"], ("census", "manual", "none"), e["id"])
            self.assertEqual(e["geo_source"] == "none", e["lat"] is None, e["id"])

    def test_promotions_have_source_and_confirmation(self):
        for p in DATA["promotions"]:
            self.assertTrue(p["source_url"].startswith("http"), p["id"])
            self.assertIsInstance(p["dates_confirmed"], bool)
            if not p["dates_confirmed"]:
                self.assertIsNone(p["start"])
                self.assertIsNone(p["end"])

    def test_verified_dates(self):
        by = {p["id"]: p for p in DATA["promotions"]}
        self.assertEqual((by["napa-2027"]["start"], by["napa-2027"]["end"], by["napa-2027"]["dates_confirmed"]),
                         ("2027-01-01", "2027-01-31", True))
        self.assertFalse(by["sonoma-2027"]["dates_confirmed"])

    def test_states_today(self):
        self.assertEqual(rules.series_view(DATA["promotions"], "napa", TODAY)["state"], "upcoming")
        self.assertEqual(rules.series_view(DATA["promotions"], "sonoma", TODAY)["state"], "archived")

    def _bad(self, mutate):
        d = copy.deepcopy(DATA)
        mutate(d)
        return validate.validate(d)

    def test_rejects(self):
        e0 = lambda d: d["entries"][0]  # noqa: E731
        cases = {
            "missing promotion": lambda d: e0(d).update(promotion_id="napa-1999"),
            "meal": lambda d: e0(d)["offers"].append({"meal": "supper", "price": 10, "description": "x"}),
            "price missing": lambda d: e0(d)["offers"].append({"meal": "lunch", "description": "x"}),
            "price text": lambda d: e0(d)["offers"].append({"meal": "lunch", "price": "$10", "description": "x"}),
            "offer extra key": lambda d: e0(d)["offers"].append({"meal": "lunch", "price": 1, "description": "x", "note": "y"}),
            "weekday": lambda d: e0(d)["offers"].append({"meal": "lunch", "price": 1, "description": "x", "weekdays": ["monday"]}),
            "exclusion": lambda d: e0(d)["offers"].append({"meal": "lunch", "price": 1, "description": "x", "exclusions": ["Jan 3"]}),
            "geo_source": lambda d: e0(d).update(geo_source=None),
            "geo none with pin": lambda d: e0(d).update(geo_source="none", lat=38.5, lon=-122.7),
            "pin outside": lambda d: e0(d).update(geo_source="manual", lat=40.0, lon=-122.7),
            "county mismatch": lambda d: e0(d).update(county="Marin"),
            "duplicate id": lambda d: d["entries"].append(copy.deepcopy(e0(d))),
            "javascript url": lambda d: e0(d).update(website="javascript:alert(1)"),
            "future check": lambda d: e0(d).update(last_checked="2099-01-01"),
            "inferred dates": lambda d: d["promotions"][[p["id"] for p in d["promotions"]].index("sonoma-2027")].update(start="2027-02-22"),
            "unconfirmed flag": lambda d: d["promotions"][0].update(dates_confirmed="yes"),
            "promotion id": lambda d: d["promotions"][0].update(id="marin-2027"),
            "no entries": lambda d: d.update(entries=[]),
            "no promotions": lambda d: d.update(promotions=[]),
            "extra entry key": lambda d: e0(d).update(rating=5),
        }
        for name, fn in cases.items():
            with self.subTest(name=name):
                self.assertTrue(self._bad(fn), name)

    def test_offer_dates_inside_promotion(self):
        d = copy.deepcopy(DATA)
        p = next(x for x in d["promotions"] if x["dates_confirmed"])
        d["entries"].append({"id": "fx-new", "promotion_id": p["id"], "name": "X", "town": "Napa" if p["id"].startswith("napa") else "Sonoma",
                             "county": "Napa" if p["id"].startswith("napa") else "Sonoma", "address": None, "lat": None, "lon": None,
                             "geo_source": "none", "cuisine": None, "booking_url": None, "website": None, "phone": None,
                             "source_url": "https://example.org/", "last_checked": "2026-11-01",
                             "offers": [{"meal": "lunch", "price": 1, "description": "x", "start": p["start"]}]})
        self.assertEqual(validate.validate(d), [])
        d["entries"][-1]["offers"][0]["start"] = "1999-01-01"
        self.assertTrue(validate.validate(d))


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

    def check(self, d, allow=False):
        return validate.check_file(self.write(d), allow_removals=allow, baseline=self.base, quiet=True)[0]

    def test_same_is_fine(self):
        self.assertTrue(self.check(DATA))

    def test_more_is_fine(self):
        d = copy.deepcopy(DATA)
        extra = copy.deepcopy(d["entries"][0])
        extra["id"] = extra["id"] + "-new"
        d["entries"].append(extra)
        self.assertTrue(self.check(d))

    def test_fewer_entries_refused_then_allowed_by_hand(self):
        d = copy.deepcopy(DATA)
        gone = d["entries"].pop()["id"]
        self.assertFalse(self.check(d))
        self.assertTrue(self.check(d, allow=True))
        problems, removed = validate.reduction(d, DATA)
        self.assertEqual(removed, [gone])
        self.assertIn("--allow-removals", problems[0])

    def test_id_swapped_same_total_refused(self):
        d = copy.deepcopy(DATA)
        d["entries"][0]["id"] = "renamed-entry"
        self.assertFalse(self.check(d))
        self.assertTrue(self.check(d, allow=True))

    def test_per_promotion_count_drop_refused(self):
        old = {"promotions": [{"id": "napa-2027"}, {"id": "sonoma-2026"}],
               "entries": [{"id": "a", "promotion_id": "napa-2027"}, {"id": "b", "promotion_id": "sonoma-2026"}]}
        new = copy.deepcopy(old)
        new["entries"][0]["promotion_id"] = "sonoma-2026"   # same ids, same total, napa loses one
        problems, _ = validate.reduction(new, old)
        self.assertIn("napa-2027 has 0 entries, down from 1", problems[0])
        self.assertEqual(validate.reduction(new, old, allow_removals=True)[0], [])

    def test_promotion_removed_refused(self):
        d = copy.deepcopy(DATA)
        pid = next(p["id"] for p in d["promotions"] if not any(e["promotion_id"] == p["id"] for e in d["entries"]))
        d["promotions"] = [p for p in d["promotions"] if p["id"] != pid]
        problems, _ = validate.reduction(d, DATA)
        self.assertIn("promotions removed: %s" % pid, problems[0])

    def test_fewer_offers_refused(self):
        d = copy.deepcopy(DATA)
        e = next(x for x in d["entries"] if x["offers"])
        e["offers"].pop()
        problems, _ = validate.reduction(d, DATA)
        self.assertTrue(problems)
        self.assertIn("%s has" % e["id"], problems[0])

    def test_empty_refused_even_with_allow_removals(self):
        d = copy.deepcopy(DATA)
        d["entries"] = []
        self.assertFalse(self.check(d, allow=True))

    def test_invalid_file_refused(self):
        p = self.tmp / "broken.json"
        p.write_text("{not json")
        self.assertFalse(validate.check_file(p, baseline=self.base, quiet=True)[0])

    def test_broken_baseline_is_a_clear_failure(self):
        bad = self.tmp / "bad-base.json"
        bad.write_text("{not json")
        self.assertFalse(validate.check_file(self.write(DATA), baseline=bad, quiet=True)[0])
        self.assertEqual(validate.main([str(self.write(DATA)), "--baseline", str(bad)]), 1)
        self.assertEqual(validate.main([str(self.write(DATA)), "--baseline", str(self.tmp / "missing.json")]), 1)

    def test_non_finite_price_rejected(self):
        for bad in ("Infinity", "NaN", "-Infinity"):
            d = copy.deepcopy(DATA)
            e = next(x for x in d["entries"] if x["offers"])
            text = json.dumps(d).replace('"price": %s' % json.dumps(e["offers"][0]["price"]), '"price": %s' % bad, 1)
            p = self.tmp / "nf.json"
            p.write_text(text)
            with self.subTest(price=bad):
                self.assertFalse(validate.check_file(p, baseline=self.base, quiet=True)[0])

    def _promo(self, d, pid):
        return next(p for p in d["promotions"] if p["id"] == pid)

    def test_changed_dates_flagged(self):
        d = copy.deepcopy(DATA)
        self._promo(d, "napa-2027")["end"] = "2027-01-30"
        problems, _ = validate.reduction(d, DATA)
        self.assertIn("napa-2027 dates changed", problems[0])
        self.assertTrue(self.check(d, allow=True))
        self.assertFalse(self.check(d))

    def test_confirmed_flip_flagged(self):
        d = copy.deepcopy(DATA)
        p = self._promo(d, "napa-2027")
        p.update(dates_confirmed=False, start=None, end=None)
        problems, _ = validate.reduction(d, DATA)
        self.assertIn("napa-2027 dates_confirmed went from true to false", problems[0])
        self.assertTrue(self.check(d, allow=True))

    def test_newly_confirmed_dates_are_fine(self):
        d = copy.deepcopy(DATA)
        self._promo(d, "sonoma-2027").update(dates_confirmed=True, start="2027-02-22", end="2027-02-28")
        self.assertEqual(validate.reduction(d, DATA)[0], [])

    def test_fewer_priced_offers_flagged(self):
        d = copy.deepcopy(DATA)
        e = next(x for x in d["entries"] if x["offers"])
        e["offers"][0]["price"] = None
        problems, _ = validate.reduction(d, DATA)
        self.assertIn("%s has %d priced offers" % (e["id"], len(e["offers"]) - 1), problems[0])
        self.assertTrue(self.check(d, allow=True))

    def test_baseline_is_the_inlined_page(self):
        data, where = validate.last_published()
        self.assertIn("index.html", where)
        self.assertEqual(data, DATA)

    def test_baseline_falls_back_without_page(self):
        data, where = validate.last_published(page=self.tmp / "nope.html")
        self.assertIn(where, ("git HEAD", "no earlier copy found"))


class Inline(unittest.TestCase):
    def page(self):
        return inline.PAGE.read_text()

    def test_inlined_block_matches_json(self):
        block = validate.inlined_block(self.page())
        self.assertIsNotNone(block, "no inlined data; run tools/restaurant-month/inline.py")
        self.assertEqual(json.loads(block), DATA, "inlined data differs; run tools/restaurant-month/inline.py")

    def test_page_is_up_to_date(self):
        page = self.page()
        was = inline.rendered_for(page)
        self.assertIsNotNone(was)
        self.assertEqual(inline.render(page, DATA, was), page, "run python3 tools/restaurant-month/inline.py")

    def test_render_is_idempotent(self):
        once = inline.render(self.page(), DATA, TODAY)
        self.assertEqual(inline.render(once, DATA, TODAY), once)

    def test_script_close_is_escaped(self):
        d = copy.deepcopy(DATA)
        d["entries"][0]["name"] = "a </script> name"
        self.assertEqual(inline.json_block(d).count("</script>"), 1)
        self.assertEqual(json.loads(validate.inlined_block(inline.render(self.page(), d, TODAY))), d)

    def test_footer_line(self):
        text = re.sub(r"<[^>]+>", "", inline.footer_html(DATA)).replace("&#x27;", "'")
        self.assertIn("Collected from Visit Napa Valley and Sonoma County Restaurant Week pages, read by script and "
                      "spot-checked, checked " + rules.fmt_date(DATA["meta"]["last_reviewed"], True)
                      + ". Napa participants are not listed yet."
                      + " We aim to list every participant and cannot guarantee it. Offers change; the restaurant's page wins.",
                      text)
        d = copy.deepcopy(DATA)
        e = copy.deepcopy(d["entries"][0])
        e.update(id="napa-fixture", promotion_id="napa-2027", county="Napa", town="Napa")
        d["entries"].append(e)
        self.assertNotIn("Napa participants are not listed yet.", inline.footer_html(d))

    def test_footer_links_the_organizers_current_pages(self):
        foot = inline.footer_html(DATA)
        self.assertIn('href="https://www.visitnapavalley.com/restaurant-month/">Visit Napa Valley</a>', foot)
        self.assertIn('href="https://socorestaurantweek.org/">Sonoma County Restaurant Week</a>', foot)
        self.assertNotIn("web.archive.org", foot)

    def test_no_js_upcoming_and_archived(self):
        body = inline.body_html(DATA, TODAY)
        self.assertIn("2027 dates to be announced", body)
        self.assertIn("January 1 to 31, 2027", body)
        self.assertIn("Confirmed for 2027", body)
        self.assertNotIn('class="book"', body)        # nothing bookable on November 1
        self.assertNotIn('class="price"', body)       # archived shows no prices
        sonoma_past = [e for e in DATA["entries"] if e["promotion_id"] == "sonoma-2026"]
        if sonoma_past:
            self.assertIn("2026 participants, for reference", body)
            for e in sonoma_past:
                self.assertIn(inline.esc(e["name"]), body)
        else:
            self.assertIn("Participant list not yet collected.", body)

    def test_no_js_active_has_prices_book_and_staleness(self):
        d = copy.deepcopy(DATA)
        d["entries"].append({"id": "fx-active", "promotion_id": "napa-2027", "name": "Fixture Room", "town": "Napa",
                             "county": "Napa", "address": None, "lat": None, "lon": None, "geo_source": "none",
                             "cuisine": None, "booking_url": "https://example.org/book", "website": None, "phone": "(707) 555-0100",
                             "source_url": "https://example.org/", "last_checked": "2026-12-20",
                             "offers": [{"meal": "dinner", "price": 55, "description": "Three courses", "weekdays": ["mon"]}]})
        body = inline.body_html(d, "2027-01-04")
        self.assertIn('id="active-static-h">Offers', body)
        self.assertIn('<span class="price">$55</span>', body)
        self.assertIn('class="book"', body)
        self.assertIn("Mondays", body)
        self.assertIn("Checked December 20, 2026; confirm with the restaurant.", body)
        self.assertIn("tel:7075550100", body)
        self.assertNotIn("Checked December 20, 2026", inline.body_html(d, "2026-12-27"))  # 7 days: not stale

    def test_active_promotion_with_no_entries(self):
        # January 5, 2027: Napa is active with no entries; no offers section, a link to the organizer.
        body = inline.body_html(DATA, "2027-01-05")
        self.assertIn("series--active", body)
        self.assertIn('No participants listed yet. <a href="https://www.visitnapavalley.com/restaurant-month/" rel="noopener">Organizer&#x27;s page</a>', body)
        self.assertNotIn("active-static", body)
        js = (TOOL / "restaurant-month.js").read_text()
        self.assertIn('displayState(p, today) === "active" && entriesOf(p).length > 0', js)

    def test_ended_offer_has_text_not_only_strike(self):
        d = copy.deepcopy(DATA)
        d["entries"].append({"id": "fx-ended", "promotion_id": "napa-2027", "name": "Fixture", "town": "Napa",
                             "county": "Napa", "address": None, "lat": None, "lon": None, "geo_source": "none",
                             "cuisine": None, "booking_url": None, "website": None, "phone": None,
                             "source_url": "https://example.org/", "last_checked": "2026-11-01",
                             "offers": [{"meal": "lunch", "price": 20, "description": "Early lunch", "end": "2027-01-03"},
                                        {"meal": "any", "price": 6, "description": "Sweet Perk: Cookie"}]})
        body = inline.body_html(d, "2027-01-05")
        self.assertIn('<li class="ended">', body)
        self.assertIn('<span class="visually-hidden"> (ended)</span>', body)
        self.assertIn('<span class="price">$6</span><span class="desc">Sweet Perk: Cookie</span>', body)  # label once

    def test_heading_order(self):
        body = inline.body_html(DATA, TODAY)
        self.assertNotIn('<h3 class="label">', body)
        self.assertIn('<p class="label">2026 participants, for reference</p>', body)
        js = (TOOL / "restaurant-month.js").read_text()
        self.assertNotIn('el("h3", { class: "label" }', js)

    def test_json_block_escapes_every_lt(self):
        d = copy.deepcopy(DATA)
        d["entries"][0]["name"] = "a <b>bold</b> and <!-- comment"
        block = inline.json_block(d)
        inner = block[len('<script type="application/json" id="rm-data">'):-len("</script>")]
        self.assertNotIn("<", inner)
        self.assertEqual(json.loads(validate.inlined_block(inline.render(self.page(), d, TODAY))), d)

    def test_john_ash_placeholder_replaced(self):
        e = next(x for x in DATA["entries"] if x["id"] == "sonoma-2026-john-ash-restaurant")
        self.assertTrue(all("See menu below" not in o["description"] for o in e["offers"]))
        self.assertEqual(sum(o["description"] == "Menu is on the restaurant's page." for o in e["offers"]), 2)

    def test_empty_participant_lists_are_honest(self):
        d = copy.deepcopy(DATA)
        d["entries"] = [e for e in d["entries"] if e["promotion_id"] != "sonoma-2026"] or d["entries"]
        if all(e["promotion_id"] != "sonoma-2026" for e in d["entries"]):
            self.assertIn("Participant list not yet collected.", inline.body_html(d, TODAY))
        self.assertIn("No participants confirmed for 2027 yet.", inline.body_html(
            {"meta": DATA["meta"], "promotions": DATA["promotions"], "entries": []}, TODAY))

    def test_refuses_invalid_data(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            bad = copy.deepcopy(DATA)
            bad["entries"][0]["geo_source"] = None
            (tmp / "rm.json").write_text(json.dumps(bad))
            page = tmp / "index.html"
            shutil.copy(inline.PAGE, page)
            before = page.read_text()
            old = (inline.DATA, inline.PAGE)
            inline.DATA, inline.PAGE = tmp / "rm.json", page
            try:
                rc = inline.main(["--baseline", str(DATA_PATH)])
            finally:
                inline.DATA, inline.PAGE = old
            self.assertEqual(rc, 1)
            self.assertEqual(page.read_text(), before)
        finally:
            shutil.rmtree(tmp)


class HouseRules(unittest.TestCase):
    def files(self):
        return {f: (TOOL / f).read_text() for f in ("index.html", "restaurant-month.js", "restaurant-month.css",
                                                    "rules.py", "inline.py", "validate.py")}

    def test_no_em_dashes(self):
        # Our copy never has one. A restaurant's own words are quoted as given (data block, offers).
        for f, text in self.files().items():
            if f == "index.html":
                text = text.split("<!-- rm-data:start -->")[0]
                text = re.sub(r'<span class="desc">.*?</span>', "", text, flags=re.S)
            self.assertNotIn("—", text, f)

    def test_no_ai_in_headings(self):
        page = inline.PAGE.read_text()
        for h in re.findall(r"<h[1-6][^>]*>(.*?)</h[1-6]>", page, re.S):
            self.assertIsNone(re.search(r"\bAI\b", h), h)
        js = (TOOL / "restaurant-month.js").read_text()
        self.assertIsNone(re.search(r"\bAI\b", js.split("// ===")[-1]))

    def test_page_assets(self):
        page = inline.PAGE.read_text()
        self.assertEqual(page.count("fonts.googleapis.com/css2"), 1)
        for fam in ("Fraunces", "Source+Sans+3", "JetBrains+Mono"):
            self.assertIn(fam, page)
        self.assertIn('leaflet@1.9.4/dist/leaflet.js" integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo="', page)
        self.assertIn('leaflet@1.9.4/dist/leaflet.css" integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY="', page)
        self.assertNotRegex(page, r"(?i)analytics|gtag|googletagmanager|anthropic|openai")

    def test_no_js_gating(self):
        page = inline.PAGE.read_text()
        css = (TOOL / "restaurant-month.css").read_text()
        self.assertIn("[hidden] { display: none !important; }", css)
        self.assertIn("html:not(.js) .js-only", css)
        self.assertIn('class="active-box js-only"', page)
        self.assertIn('<form id="filters" class="filters" hidden>', page)
        self.assertIn("--ground: #FDFCF9;", css)
        self.assertIn("--ink: #161614;", css)

    def test_map_pieces_copied(self):
        js = (TOOL / "restaurant-month.js").read_text()
        self.assertIn("iconSize: [44, 44]", js)
        self.assertIn('ev.key === " "', js)
        self.assertIn('mk.fire("click")', js)
        self.assertIn('setAttribute("aria-label"', js)
        self.assertIn("tile.openstreetmap.org", js)
        self.assertIn("insideBox(ev.clientX", js)

    def test_contrast(self):
        def lum(h):
            c = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
            c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
            return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]

        def ratio(a, b):
            la, lb = sorted((lum(a), lum(b)), reverse=True)
            return (la + 0.05) / (lb + 0.05)
        css = (TOOL / "restaurant-month.css").read_text()
        var = dict(re.findall(r"--([a-z]+):\s*(#[0-9A-Fa-f]{6})", css))
        for fg in ("ink", "mute", "link", "accent"):
            for bg in ("ground", "panel"):
                self.assertGreaterEqual(ratio(var[fg], var[bg]), 4.5, (fg, bg))
        for pin in ("napa", "sonoma"):
            self.assertGreaterEqual(ratio("#FFFFFF", var[pin]), 4.5, pin)
        self.assertGreaterEqual(ratio(var["mute"], "#FFFFFF"), 4.5)

    def test_js_mirrors_python_constants(self):
        js = (TOOL / "restaurant-month.js").read_text()
        self.assertIn("var STALE_DAYS = %d;" % rules.STALE_DAYS, js)
        for k, v in rules.BUCKET_LABELS.items():
            self.assertIn('"%s"' % v, js)
        for v in ("Participant list not yet collected.", " participants, for reference", "Confirmed for ",
                  " dates to be announced.", "; confirm with the restaurant."):
            self.assertIn(v, js)


if __name__ == "__main__":
    unittest.main()
