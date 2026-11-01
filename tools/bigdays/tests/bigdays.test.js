// Big days calendar: the same shared cases as tests/test_bigdays.py, run against bigdays.js.
// Offline, no packages. Run from the repository root:  node --test tools/bigdays/tests/bigdays.test.js
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const B = require("../bigdays.js");
const FIX = path.join(__dirname, "fixtures");
const CASES = JSON.parse(fs.readFileSync(path.join(FIX, "cases.json"), "utf8"));
const DATA = path.join(__dirname, "..", "..", "..", "data", "bigdays");
const readJSON = (p) => JSON.parse(fs.readFileSync(p, "utf8"));
const texts = (tags) => tags.map((t) => t.text);
const KEEP = ["startTime", "endTime", "isDaytime", "temperature", "temperatureUnit", "shortForecast"];
const periods = (name) => readJSON(path.join(FIX, "nws", name)).properties.periods
  .map((p) => Object.fromEntries(KEEP.map((k) => [k, p[k] === undefined ? null : p[k]])));

test("date math across year end and daylight saving", () => {
  for (const [iso, n, want] of CASES.add_days) assert.equal(B.addDays(iso, n), want, `${iso} ${n}`);
  for (const [iso, wd] of CASES.weekday) assert.equal(B.weekday(iso), wd, iso);
  for (const [iso, y, w, want] of CASES.fmt_date) assert.equal(B.fmtDate(iso, y, w), want);
});

test("eight-week window and Sunday-first grid", () => {
  for (const [start, last] of CASES.window) {
    const w = B.windowDates(start);
    assert.equal(w.length, 56);
    assert.equal(w[0], start);
    assert.equal(w[55], last);
  }
  for (const [start, rows, lead, last] of CASES.grid) {
    const g = B.gridRows(start);
    assert.equal(g.length, rows, start);
    assert.ok(g.every((r) => r.length === 7));
    assert.deepEqual(g[0].slice(0, lead), Array(lead).fill(null));
    assert.equal(g[0][lead], start);
    const flat = g.flat().filter(Boolean);
    assert.equal(flat.length, 56);
    assert.equal(flat[55], last);
  }
});

test("today is the Los Angeles date, whatever the host zone", () => {
  assert.equal(B.todayLA(new Date("2027-01-01T07:30:00Z")), "2026-12-31");
  assert.equal(B.todayLA(new Date("2027-01-01T08:30:00Z")), "2027-01-01");
});

test("holiday rules and three-day weekends (shared cases)", () => {
  for (const [iso, want] of CASES.holiday_tags) assert.deepEqual(texts(B.holidayTags(iso)), want, iso);
  for (const [year, name, want] of CASES.rule_dates) {
    const h = B.holidays(year).find((x) => x.name === name);
    assert.equal(h.date, want, `${year} ${name}`);
  }
  const w = B.threeDayWeekends(B.holidays(2026)).map((x) => x.holiday);
  assert.ok(!w.includes("Thanksgiving Day"));
  assert.ok(w.includes("Christmas Day"));
  const tg = B.threeDayWeekends(B.holidays(2026)).find((x) => x.holiday === "Day after Thanksgiving");
  assert.deepEqual(tg.dates, ["2026-11-26", "2026-11-27", "2026-11-28", "2026-11-29"]);  // Thu to Sun
});

test("holidays.json agrees with the rules in bigdays.js", () => {
  const doc = readJSON(path.join(DATA, "holidays.json"));
  const ys = doc.meta.years.map(String);
  const want = [];
  for (let y = Math.min(...doc.meta.years) - 1; y <= Math.max(...doc.meta.years) + 1; y++) {
    for (const h of B.holidays(y)) if (ys.includes(h.date.slice(0, 4)) || ys.includes(h.observed.slice(0, 4))) want.push(h);
  }
  assert.deepEqual(doc.holidays, want);
  assert.deepEqual(doc.three_day_weekends, B.threeDayWeekends(want));
});

test("harvest band", () => {
  for (const [iso, want] of CASES.harvest) assert.equal(B.isHarvest(iso), want, iso);
});

test("county toggle filters events; tentative says so; canceled and undated never show", () => {
  const ev = CASES.data.events.events;
  for (const [iso, county, want] of CASES.event_tags) assert.deepEqual(texts(B.eventTags(iso, ev, county)), want, `${iso} ${county}`);
});

test("district break ranges", () => {
  for (const [iso, want] of CASES.break_tags) assert.deepEqual(texts(B.breakTags(iso, CASES.data.school_breaks)), want, iso);
  const sb = readJSON(path.join(DATA, "school_breaks.json"));
  assert.deepEqual(texts(B.breakTags("2027-01-04", sb)), ["OUSD winter break"]);
  assert.deepEqual(texts(B.breakTags("2027-01-05", sb)), []);
  assert.deepEqual(texts(B.breakTags("2026-11-25", sb)), ["SFUSD Thanksgiving break", "OUSD Thanksgiving break", "SJUSD Thanksgiving break"]);
});

test("day tags and the verified_through rule", () => {
  for (const [iso, county, want] of CASES.day_tags) assert.deepEqual(texts(B.dayTags(iso, CASES.data, county)), want, `${iso} ${county}`);
});

test("weather word: the fixed rule", () => {
  for (const [period, threshold, want] of CASES.weather_word) assert.equal(B.weatherWord(period, threshold), want, JSON.stringify(period));
});

test("day/night reduction against fixture NWS responses", () => {
  for (const c of CASES.reduce) assert.deepEqual(B.reducePeriods(periods(c.fixture), c.today, c.threshold), c.want, c.why);
  const nights = periods("synthetic-forecast.json").filter((p) => !p.isDaytime);
  assert.deepEqual(B.reducePeriods(nights, "2026-12-30", 95), {});
});

test("weather tags: labels, staleness, failures, nothing past day seven", () => {
  for (const c of CASES.weather_tag) {
    const doc = c.doc ? CASES.docs[c.doc] : null;
    const area = B.weatherArea(doc, c.county, CASES.points, c.today, Date.parse(c.now));
    const tag = B.weatherTag(c.iso, c.today, area);
    assert.equal(tag ? tag.text : null, c.want, JSON.stringify(c));
  }
});

test("page inlines exactly the JSON files", () => {
  const page = fs.readFileSync(path.join(__dirname, "..", "index.html"), "utf8");
  const m = page.match(/<!-- bigdays-data:start -->\s*<script type="application\/json" id="bigdays-data">([\s\S]*?)<\/script>/);
  assert.ok(m, "inlined block missing");
  const inl = JSON.parse(m[1].replace(/<\\\//g, "</"));
  for (const k of ["events", "school_breaks", "holidays", "weather_points"]) {
    assert.deepEqual(inl[k], readJSON(path.join(DATA, k + ".json")), k + " differs; run python3 tools/bigdays/inline.py");
  }
});
