// Farmers market map: the same schedule cases as tests/test_markets.py, run against markets.js.
// Offline, no packages. Run from the repository root:  node --test tools/markets/tests/markets.test.js
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const M = require("../markets.js");
const CASES = JSON.parse(fs.readFileSync(path.join(__dirname, "fixtures", "cases.json"), "utf8"));
const MK = CASES.markets;
const DATA = JSON.parse(fs.readFileSync(path.join(__dirname, "..", "..", "..", "data", "markets.json"), "utf8"));

const nOn = (id, d) => M.sessionsOn(MK[id], d).length;

test("occurs (shared cases)", (t) => {
  for (const [m, d, n, why] of CASES.occurs) assert.equal(nOn(m, d), n, `${m} ${d}: ${why}`);
});

test("state (shared cases)", () => {
  for (const [m, d, hm, want, why] of CASES.state) assert.equal(M.state(MK[m], d, hm), want, `${m} ${d} ${hm}: ${why}`);
});

test("scheduled now (shared cases)", () => {
  for (const [m, d, hm, want] of CASES.scheduled_now) assert.equal(M.scheduledNow(MK[m], d, hm), want, `${m} ${d} ${hm}`);
});

test("Los Angeles clock across DST and year end, whatever the host zone", () => {
  for (const [utc, d, hm, why] of CASES.la_now) assert.deepEqual(M.laNow(new Date(utc)), { date: d, hm }, why);
});

test("session words (shared cases)", () => {
  for (const [session, vt, text, inferred] of CASES.words) assert.equal(M.describeSession(session, vt, !!inferred), text);
});

test("staleness (shared cases)", () => {
  for (const [checked, today, want] of CASES.stale) assert.equal(M.isStale(checked, today), want, `${checked} ${today}`);
});

test("spec fixture: first Thursday only", () => {
  assert.equal(nOn("first-thursdays", "2026-06-04"), 1);
  for (const d of ["2026-06-11", "2026-06-18", "2026-06-25"]) assert.equal(nOn("first-thursdays", d), 0);
});

test("spec fixture: explicit date list", () => {
  for (const d of MK["date-list"].sessions[0].dates) assert.equal(nOn("date-list", d), 1);
  assert.equal(nOn("date-list", "2026-05-22"), 0);
});

test("spec fixture: different seasons on different days", () => {
  assert.equal(nOn("two-seasons", "2026-02-07"), 1);
  assert.equal(nOn("two-seasons", "2026-02-10"), 0);
  assert.equal(nOn("two-seasons", "2026-06-09"), 1);
});

test("spec fixture: exact-date season", () => {
  assert.equal(nOn("exact-season", "2026-04-30"), 0);
  assert.equal(nOn("exact-season", "2026-10-08"), 1);
  assert.equal(nOn("exact-season", "2026-10-15"), 0);
});

test("spec fixture: two windows in one day", () => {
  assert.deepEqual(M.sessionsOn(MK["two-windows"], "2026-07-01").map((x) => x.session.start), ["10:00", "16:30"]);
});

test("spec fixture: exception on a scheduled day", () => {
  assert.equal(nOn("exception", "2026-11-28"), 0);
  assert.equal(M.sessionsOn(MK.exception, "2026-12-05")[0].exception.status, "moved");
});

test("moved day: not scheduled today or now at the old hours, kept in this week with its note", () => {
  assert.notEqual(M.state(MK.exception, "2026-12-05", "08:00"), "today");
  assert.equal(M.scheduledNow(MK.exception, "2026-12-05", "10:00"), false);
  const rows = M.thisWeek([MK.exception], "2026-12-05", "15:00")[0].rows;
  assert.equal(rows.length, 1);
  assert.equal(rows[0].exception.note, "Moved to the north lot for the parade.");
});

test("links: only http(s) URLs pass", () => {
  assert.equal(M.safeUrl("https://example.org/"), "https://example.org/");
  assert.equal(M.safeUrl("http://example.org"), "http://example.org");
  for (const bad of ["javascript:alert(1)", "data:text/html,x", "//example.org", "", null, "ftp://x"]) assert.equal(M.safeUrl(bad), null);
});

test("focus restore after re-render: same market, same zone first", () => {
  const a = {}, b = {}, c = {};
  const cands = [{ zone: "week", id: "x", el: a }, { zone: "list", id: "x", el: b }, { zone: "list", id: "y", el: c }];
  assert.equal(M.findFocusTarget(M.focusKey("list", "x"), cands), b);
  assert.equal(M.findFocusTarget(M.focusKey("week", "y"), cands), c);   // falls back to any zone
  assert.equal(M.findFocusTarget(M.focusKey("list", "z"), cands), null);
  assert.equal(M.findFocusTarget(null, cands), null);
});

test("dialog: a click inside its own box (padding included) does not count as the backdrop", () => {
  const r = { left: 100, right: 500, top: 50, bottom: 400 };
  assert.equal(M.insideBox(100, 50, r), true);
  assert.equal(M.insideBox(300, 399, r), true);
  assert.equal(M.insideBox(99, 200, r), false);
  assert.equal(M.insideBox(300, 401, r), false);
});

test("spec fixture: confirm_each_year past valid_through", () => {
  assert.equal(M.state(MK.expired, "2026-06-07", "10:00"), "unconfirmed");
  assert.equal(M.stateLabel("unconfirmed", "2026-06-07"), "Schedule not yet confirmed for 2026");
});

test("this week: rolling seven days, ended sessions drop out of today", () => {
  const week = M.thisWeek([MK["two-windows"]], "2026-07-01", "13:00");
  assert.equal(week.length, 7);
  assert.equal(week[6].date, "2026-07-07");
  assert.deepEqual(week[0].rows.map((r) => r.session.start), ["16:30"]);
  assert.equal(week[3].rows.length, 1);
});

test("this week across the year end", () => {
  const week = M.thisWeek([MK.wrap], "2026-12-29", "09:00");
  assert.deepEqual(week.filter((d) => d.rows.length).map((d) => d.date), ["2027-01-01"]);
});

test("date arithmetic ignores DST", () => {
  assert.equal(M.addDays("2026-03-07", 1), "2026-03-08");
  assert.equal(M.addDays("2026-03-08", 1), "2026-03-09");
  assert.equal(M.addDays("2026-10-31", 1), "2026-11-01");
  assert.equal(M.addDays("2026-11-01", 1), "2026-11-02");
  assert.equal(M.addDays("2026-12-31", 1), "2027-01-01");
  assert.equal(M.weekdayKey("2026-10-31"), "Sat");
  assert.equal(M.daysBetween("2026-09-16", "2026-10-31"), 45);
});

test("real data: every record has a state and words", () => {
  for (const m of DATA.markets) {
    assert.ok(["census", "manual"].includes(m.geo_source), m.id);
    assert.ok(m.schedule_source === "cdfa" || /^https?:\/\//.test(m.schedule_source), m.id);
    assert.ok(Object.keys(M.STATES).includes(M.state(m, "2026-10-31", "10:00")), m.id);
    for (const s of m.sessions) assert.ok(M.describeSession(s, m.schedule_valid_through).length > 10);
  }
});

test("inlined block in index.html matches data/markets.json", () => {
  const page = fs.readFileSync(path.join(__dirname, "..", "index.html"), "utf8");
  const m = page.match(/<script type="application\/json" id="markets-data">([\s\S]*?)<\/script>/);
  assert.ok(m, "no inlined block; run python3 tools/markets/inline.py");
  assert.deepEqual(JSON.parse(m[1]), DATA);
});
