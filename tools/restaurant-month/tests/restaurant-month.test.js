// Restaurant Month: the same rule cases as tests/test_restaurant_month.py, run against restaurant-month.js.
// Offline, no packages. Run from the repository root:
//   node --test tools/restaurant-month/tests/restaurant-month.test.js
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const R = require("../restaurant-month.js");
const CASES = JSON.parse(fs.readFileSync(path.join(__dirname, "fixtures", "cases.json"), "utf8"));
const P = CASES.promotions;
const ENTRIES = CASES.entries;
const DATA = JSON.parse(fs.readFileSync(path.join(__dirname, "..", "..", "..", "data", "restaurant-month.json"), "utf8"));
const promos = (ids) => ids.map((i) => P[i]);

test("price buckets at the boundaries", () => {
  for (const [price, want] of CASES.buckets) assert.equal(R.priceBucket(price), want, String(price));
  assert.equal(R.priceBucket(undefined), "none");
});

test("each amount falls in exactly one bucket", () => {
  for (let cents = 0; cents <= 20000; cents++) {
    const p = cents / 100;
    const hits = [["under30", p < 30], ["30to49", p >= 30 && p < 50], ["50to79", p >= 50 && p < 80], ["80up", p >= 80]]
      .filter((x) => x[1]).map((x) => x[0]);
    assert.deepEqual(hits, [R.priceBucket(p)]);
  }
});

test("bucket labels are the spec's words", () => {
  assert.deepEqual(R.BUCKETS.map((b) => R.BUCKET_LABELS[b]), ["Under $30", "$30 to $49", "$50 to $79", "$80 and up", "No fixed price"]);
});

test("prices", () => {
  for (const [price, text] of CASES.prices) assert.equal(R.fmtPrice(price), text);
});

test("three states across promotion boundaries and year end", () => {
  for (const [pid, today, want, why] of CASES.states) assert.equal(R.displayState(P[pid], today), want, `${pid} ${today}: ${why}`);
});

test("series view: current season, reference season, banner year", () => {
  for (const [ids, series, today, state, cur, ref, year] of CASES.views) {
    const v = R.seriesView(promos(ids), series, today);
    if (state === null) { assert.equal(v, null); continue; }
    assert.deepEqual([v.state, v.current.id, v.reference ? v.reference.id : null, v.next_year], [state, cur, ref, year], `${series} ${today}`);
  }
});

test("archived banner", () => {
  for (const [ids, series, today, text] of CASES.banners) assert.equal(R.bannerText(R.seriesView(promos(ids), series, today)), text);
});

test("countdown and date ranges", () => {
  for (const [today, start, n, text] of CASES.countdown) {
    assert.equal(R.countdownDays(today, start), n);
    assert.equal(R.countdownText(today, start), text);
  }
  for (const [a, b, text] of CASES.ranges) assert.equal(R.fmtRange(a, b), text);
  assert.equal(R.referenceLabel(P["sonoma-2026"]), "2026 participants, for reference");
  assert.equal(R.confirmedLabel(P["napa-2027"]), "Confirmed for 2027");
});

test("Los Angeles date across the year end and DST, whatever the host zone", () => {
  assert.equal(R.laToday(new Date("2027-01-01T07:59:00Z")), "2026-12-31");
  assert.equal(R.laToday(new Date("2027-01-01T08:00:00Z")), "2027-01-01");
  assert.equal(R.laToday(new Date("2026-11-01T08:30:00Z")), "2026-11-01");
  assert.equal(R.laToday(new Date("2026-11-02T07:59:00Z")), "2026-11-01");
  assert.equal(R.addDays("2026-12-31", 1), "2027-01-01");
  assert.equal(R.isDate("2027-02-29"), false);
  assert.equal(R.isDate("2028-02-29"), true);
});

test("offer-level dates, weekdays and exclusions", () => {
  for (const [offer, pid, date, want, why] of CASES.offer_on) assert.equal(R.offerOn(offer, P[pid], date), want, `${date}: ${why}`);
  for (const [offer, pid, text] of CASES.offer_when) assert.equal(R.describeOfferWhen(offer, P[pid]), text);
});

test("any-offer matching", () => {
  for (const [today, f, want, why] of CASES.match) {
    assert.deepEqual(ENTRIES.filter((e) => R.entryMatches(e, P[e.promotion_id], f, today)).map((e) => e.id), want, why);
  }
});

test("lowest eligible price", () => {
  for (const [today, f, want] of CASES.lowest) {
    for (const e of ENTRIES) assert.equal(R.lowestEligiblePrice(e, P[e.promotion_id], f, today), want[e.id], `${e.id} ${today} ${JSON.stringify(f)}`);
  }
});

test("sorts: price (lowest eligible) and town", () => {
  const byId = { "napa-2027": P["napa-2027"] };
  for (const [today, by, f, want, why] of CASES.sorts) assert.deepEqual(R.sortEntries(ENTRIES, by, byId, f, today).map((e) => e.id), want, why);
});

test("meal label shown once; filters active only with a price or meal", () => {
  for (const [offer, want] of CASES.labels) assert.equal(R.offerLabelShown(offer), want, offer.description);
  assert.equal(R.filtersActive({}), false);
  assert.equal(R.filtersActive({ town: "Napa", booking: true }), false);
  assert.equal(R.filtersActive({ meal: "lunch" }), true);
  assert.equal(R.filtersActive({ bucket: "none" }), true);
});

test("an any-meal offer matches only All meals", () => {
  const any = { meal: "any", price: 6, description: "Sweet Perk: Cookie" };
  assert.equal(R.offerMatches(any, {}), true);
  for (const meal of ["lunch", "brunch", "dinner"]) assert.equal(R.offerMatches(any, { meal }), false, meal);
  assert.equal(R.offerMatches(any, { bucket: "under30" }), true);
});

test("staleness: older than 7 days", () => {
  for (const [checked, today, want, why] of CASES.stale) assert.equal(R.isStale(checked, today), want, why);
  assert.equal(R.staleText("2027-01-02"), "Checked January 2, 2027; confirm with the restaurant.");
});

test("links: only http(s) URLs pass", () => {
  assert.equal(R.safeUrl("https://example.org/"), "https://example.org/");
  for (const bad of ["javascript:alert(1)", "data:x", "//x", "", null]) assert.equal(R.safeUrl(bad), null);
});

test("dialog: a click inside its own box does not count as the backdrop", () => {
  const r = { left: 100, right: 500, top: 50, bottom: 400 };
  assert.equal(R.insideBox(100, 50, r), true);
  assert.equal(R.insideBox(99, 200, r), false);
});

test("real data: schema basics", () => {
  const ids = new Set(DATA.promotions.map((p) => p.id));
  assert.ok(DATA.entries.length > 0);
  for (const e of DATA.entries) {
    assert.ok(ids.has(e.promotion_id), e.id);
    assert.ok(["census", "manual", "none"].includes(e.geo_source), e.id);
    for (const o of e.offers) {
      assert.ok(R.MEALS.includes(o.meal), e.id);
      assert.ok("price" in o && (o.price === null || typeof o.price === "number"), e.id);
    }
  }
  for (const p of DATA.promotions) if (!p.dates_confirmed) assert.deepEqual([p.start, p.end], [null, null], p.id);
});

test("real data: today's states (November 1, 2026)", () => {
  assert.equal(R.seriesView(DATA.promotions, "napa", "2026-11-01").state, "upcoming");
  assert.equal(R.seriesView(DATA.promotions, "sonoma", "2026-11-01").state, "archived");
});

test("inlined block in index.html matches data/restaurant-month.json", () => {
  const page = fs.readFileSync(path.join(__dirname, "..", "index.html"), "utf8");
  const m = page.match(/<script type="application\/json" id="rm-data">([\s\S]*?)<\/script>/);
  assert.ok(m, "no inlined block; run python3 tools/restaurant-month/inline.py");
  assert.ok(!m[1].includes("<"), "every < in the data block is written as \\u003c");
  assert.deepEqual(JSON.parse(m[1]), DATA);
});

test("a Sweet Perk (meal any) never sets the sort price", () => {
  const promo = { id: "p", start: "2027-01-01", end: "2027-01-31", dates_confirmed: true };
  const perkOnly = { id: "x1", promotion_id: "p", offers: [{ meal: "any", price: 6, description: "d" }] };
  const perkAndDinner = { id: "x2", promotion_id: "p", offers: [{ meal: "any", price: 6, description: "d" }, { meal: "dinner", price: 40, description: "d" }] };
  const brunchOnly = { id: "x3", promotion_id: "p", offers: [{ meal: "brunch", price: 25, description: "d" }] };
  assert.equal(R.lowestEligiblePrice(perkOnly, promo, {}, "2027-01-05"), null);
  assert.equal(R.lowestEligiblePrice(perkAndDinner, promo, {}, "2027-01-05"), 40);
  assert.equal(R.lowestEligiblePrice(brunchOnly, promo, {}, "2027-01-05"), null);
  assert.equal(R.lowestEligiblePrice(brunchOnly, promo, { meal: "brunch" }, "2027-01-05"), 25);
});
