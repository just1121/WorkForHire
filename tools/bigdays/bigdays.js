/* Big days calendar. Spec: tools/BUSY-DAYS.md (v1.2).
 *
 * The date functions below mirror tools/bigdays/bigdays.py one for one (both run the shared cases in
 * tests/fixtures/cases.json: tests/test_bigdays.py and tests/bigdays.test.js). Change one, change the other.
 *
 * Dates are "YYYY-MM-DD" strings in America/Los_Angeles; day arithmetic runs on UTC midnights so daylight
 * saving never shifts a day. Calendar data comes from the JSON block inlined by tools/bigdays/inline.py.
 * The only runtime request is data/bigdays/weather.json on this same site. No runtime AI, no trackers.
 */
(function (root) {
  "use strict";

  var TZ = "America/Los_Angeles";
  var WEEKS = 8;
  var WINDOW_DAYS = WEEKS * 7;
  var HARVEST = ["08-15", "10-31"];
  var WEATHER_DAYS = 7;
  var STALE_HOURS = 48;
  var WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
  var MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
    "September", "October", "November", "December"];

  // [name, kind, rule]; rule ["fixed", month, day] or ["nth", month, weekday (0 = Monday), n (-1 = last)]
  var HOLIDAY_RULES = [
    ["New Year's Day", "federal", ["fixed", 1, 1]],
    ["Martin Luther King Jr. Day", "federal", ["nth", 1, 0, 3]],
    ["Washington's Birthday", "federal", ["nth", 2, 0, 3]],
    ["Cesar Chavez Day", "california", ["fixed", 3, 31]],
    ["Memorial Day", "federal", ["nth", 5, 0, -1]],
    ["Juneteenth", "federal", ["fixed", 6, 19]],
    ["Independence Day", "federal", ["fixed", 7, 4]],
    ["Labor Day", "federal", ["nth", 9, 0, 1]],
    ["Columbus Day", "federal", ["nth", 10, 0, 2]],
    ["Veterans Day", "federal", ["fixed", 11, 11]],
    ["Thanksgiving Day", "federal", ["nth", 11, 3, 4]],
    ["Day after Thanksgiving", "california", ["nth_after", 11, 3, 4, 1]],  // Gov. Code 19853
    ["Christmas Day", "federal", ["fixed", 12, 25]]
  ];

  // ------------------------------------------------------------ dates

  function pad(n) { return (n < 10 ? "0" : "") + n; }
  function toUTC(iso) { return Date.UTC(+iso.slice(0, 4), +iso.slice(5, 7) - 1, +iso.slice(8, 10)); }
  function fromUTC(ms) {
    var x = new Date(ms);
    return x.getUTCFullYear() + "-" + pad(x.getUTCMonth() + 1) + "-" + pad(x.getUTCDate());
  }
  function ymd(y, m, d) { return fromUTC(Date.UTC(y, m - 1, d)); }
  function addDays(iso, n) { return fromUTC(toUTC(iso) + n * 86400000); }
  function weekday(iso) { return (new Date(toUTC(iso)).getUTCDay() + 6) % 7; } // 0 = Monday

  var laFmt = null;
  function todayLA(instant) {
    if (!laFmt) {
      laFmt = new Intl.DateTimeFormat("en-US", { timeZone: TZ, year: "numeric", month: "2-digit", day: "2-digit" });
    }
    var p = {};
    laFmt.formatToParts(instant || new Date()).forEach(function (x) { p[x.type] = x.value; });
    return p.year + "-" + p.month + "-" + p.day;
  }

  function windowDates(start, days) {
    var out = [];
    for (var i = 0; i < (days || WINDOW_DAYS); i++) out.push(addDays(start, i));
    return out;
  }

  function gridRows(start, days) {
    var dates = windowDates(start, days);
    var lead = (weekday(start) + 1) % 7;
    var cells = [];
    for (var i = 0; i < lead; i++) cells.push(null);
    cells = cells.concat(dates);
    while (cells.length % 7) cells.push(null);
    var rows = [];
    for (var j = 0; j < cells.length; j += 7) rows.push(cells.slice(j, j + 7));
    return rows;
  }

  function fmtDate(iso, withYear, withWeekday) {
    var s = MONTHS[+iso.slice(5, 7) - 1] + " " + (+iso.slice(8, 10));
    if (withYear) s += ", " + iso.slice(0, 4);
    if (withWeekday) s = WEEKDAYS[weekday(iso)] + ", " + s;
    return s;
  }

  function inRange(iso, start, end) {
    return start != null && end != null && start <= iso && iso <= end;
  }

  // ------------------------------------------------------------ holidays

  function ruleDate(year, rule) {
    if (rule[0] === "nth_after") return addDays(ruleDate(year, ["nth", rule[1], rule[2], rule[3]]), rule[4]);
    if (rule[0] === "fixed") return ymd(year, rule[1], rule[2]);
    var month = rule[1], wd = rule[2], n = rule[3];
    if (n > 0) {
      var first = ymd(year, month, 1);
      var off = ((wd - weekday(first)) % 7 + 7) % 7;
      return addDays(first, off + 7 * (n - 1));
    }
    var last = addDays(month === 12 ? ymd(year + 1, 1, 1) : ymd(year, month + 1, 1), -1);
    return addDays(last, -(((weekday(last) - wd) % 7 + 7) % 7));
  }

  function observed(iso, kind) {
    var wd = weekday(iso);
    if (wd === 6) return addDays(iso, 1);
    if (wd === 5 && kind === "federal") return addDays(iso, -1);
    return iso;
  }

  function holidays(year) {
    var out = HOLIDAY_RULES.map(function (r) {
      var actual = ruleDate(year, r[2]);
      return { name: r[0], kind: r[1], date: actual, observed: observed(actual, r[1]) };
    });
    return out.sort(function (a, b) { return a.date < b.date ? -1 : a.date > b.date ? 1 : 0; });
  }

  function threeDayWeekends(hols) {
    var out = [];
    hols.forEach(function (h) {
      var o = h.observed, wd = weekday(o), days = null;
      if (h.name === "Day after Thanksgiving") days = [addDays(o, -1), o, addDays(o, 1), addDays(o, 2)];  // Thu to Sun
      else if (wd === 0) days = [addDays(o, -2), addDays(o, -1), o];
      else if (wd === 4) days = [o, addDays(o, 1), addDays(o, 2)];
      if (days) out.push({ holiday: h.name, dates: days });
    });
    return out;
  }

  function holidayTags(iso) {
    var y = +iso.slice(0, 4);
    var hols = holidays(y - 1).concat(holidays(y), holidays(y + 1));
    var tags = [];
    hols.forEach(function (h) {
      if (h.observed === iso && h.date !== iso) tags.push({ type: "holiday", text: h.name + " (observed)" });
      else if (h.date === iso) tags.push({ type: "holiday", text: h.name });
    });
    if (threeDayWeekends(hols).some(function (w) { return w.dates.indexOf(iso) >= 0; })) {
      tags.push({ type: "weekend", text: "holiday weekend" });
    }
    return tags;
  }

  // ------------------------------------------------------------ events, breaks, harvest

  function isHarvest(iso) { var md = iso.slice(5); return HARVEST[0] <= md && md <= HARVEST[1]; }

  function eventTags(iso, events, county) {
    var tags = [];
    events.forEach(function (e) {
      if (e.status === "canceled" || !inRange(iso, e.start, e.end)) return;
      if (e.county !== county && e.county !== "both") return;
      tags.push({ type: "event", text: e.name + (e.status === "tentative" ? " (tentative)" : "") });
    });
    return tags;
  }

  function breakTags(iso, schoolBreaks) {
    var tags = [];
    schoolBreaks.districts.forEach(function (dist) {
      dist.breaks.forEach(function (b) {
        if (inRange(iso, b.start, b.end)) tags.push({ type: "break", text: b.tag });
      });
    });
    return tags;
  }

  function dayTags(iso, data, county, weather) {
    var ev = data.events;
    var tags = eventTags(iso, ev.events, county)
      .concat(holidayTags(iso), breakTags(iso, data.school_breaks));
    if (isHarvest(iso)) tags.push({ type: "harvest", text: "harvest" });
    var vt = ev.meta.verified_through;
    if (iso > vt) tags.push({ type: "unchecked", text: "not yet checked past " + fmtDate(vt, true) });
    else if (!tags.length) tags.push({ type: "quiet", text: "quiet" });
    if (weather) tags.push(weather);
    return tags;
  }

  // ------------------------------------------------------------ weather

  var RAIN_RE = /rain|showers|thunderstorm|drizzle/i, FOG_RE = /fog/i, CLOUDY_RE = /cloudy|overcast/i;

  function weatherWord(period, threshold) {
    var short = period.shortForecast || "";
    if (RAIN_RE.test(short)) return "rain";
    var t = period.temperature;
    if (typeof t === "number" && isFinite(t)) {
      var f = (period.temperatureUnit || "F") === "F" ? t : t * 9 / 5 + 32;
      if (f >= threshold) return "heat";
    }
    if (FOG_RE.test(short)) return "fog";
    if (CLOUDY_RE.test(short)) return "cloudy";
    return "clear";
  }

  function reducePeriods(periods, today, threshold, days) {
    var last = addDays(today, (days || WEATHER_DAYS) - 1);
    var out = {};
    (periods || []).forEach(function (p) {
      if (!p || !p.isDaytime) return;
      var day = String(p.startTime || "").slice(0, 10);
      if (!(today <= day && day <= last) || Object.prototype.hasOwnProperty.call(out, day)) return;
      out[day] = weatherWord(p, threshold);
    });
    return out;
  }

  function parseISO(s) {
    s = String(s).trim();
    // an explicit zone is required (Z or +hh:mm), as in bigdays.py; a bare local time is refused
    if (!/(Z|[+-]\d\d:\d\d)$/i.test(s)) throw new Error("no zone");
    var t = Date.parse(s);
    if (isNaN(t)) throw new Error("bad time");
    return t;
  }

  function weatherArea(doc, county, points, today, nowMs) {
    var label = points.points[county].label;
    try {
      var age = nowMs - parseISO(doc.generated_at);
      var pt = doc.points[county];
      if (age > STALE_HOURS * 3600000 || age < -3600000 || !pt.ok) return { label: label, ok: false, words: {} };
      return { label: label, ok: true, words: reducePeriods(pt.periods, today, points.heat_threshold_f, points.days || WEATHER_DAYS) };
    } catch (e) {
      return { label: label, ok: false, words: {} };
    }
  }

  function weatherTag(iso, today, area) {
    if (!(today <= iso && iso <= addDays(today, WEATHER_DAYS - 1))) return null;
    var label = area && area.label;
    if (!area || !area.ok) return { type: "weather", text: "no forecast available" + (label ? ", " + label : "") };
    var word = area.words[iso];
    if (word) return { type: "weather", text: word + ", " + label };
    if (iso === today) return null;
    return { type: "weather", text: "no forecast available, " + label };
  }

  var API = {
    WEEKS: WEEKS, WINDOW_DAYS: WINDOW_DAYS, HARVEST: HARVEST, WEATHER_DAYS: WEATHER_DAYS, STALE_HOURS: STALE_HOURS,
    addDays: addDays, weekday: weekday, todayLA: todayLA, windowDates: windowDates, gridRows: gridRows,
    fmtDate: fmtDate, ruleDate: ruleDate, observed: observed, holidays: holidays,
    threeDayWeekends: threeDayWeekends, holidayTags: holidayTags, isHarvest: isHarvest,
    eventTags: eventTags, breakTags: breakTags, dayTags: dayTags, weatherWord: weatherWord,
    reducePeriods: reducePeriods, weatherArea: weatherArea, weatherTag: weatherTag
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = API;
    return;
  }
  if (typeof document === "undefined") return;

  // ------------------------------------------------------------ page

  var data = null, county = "napa", weatherDoc = null, weatherFailed = false, selected = null;

  function el(tag, cls, text) {
    var x = document.createElement(tag);
    if (cls) x.className = cls;
    if (text != null) x.textContent = text;
    return x;
  }

  function tagList(tags) {
    var ul = el("ul", "tags");
    tags.forEach(function (t) { ul.appendChild(el("li", "tag tag--" + t.type, t.text)); });
    return ul;
  }

  // Inside a day button only phrasing content is valid, so the tags there are spans.
  function tagSpans(tags) {
    var box = el("span", "tags");
    tags.forEach(function (t) { box.appendChild(el("span", "tag tag--" + t.type, t.text)); });
    return box;
  }

  function areaFor(today) {
    if (weatherFailed || !weatherDoc) {
      return weatherFailed ? { label: data.weather_points.points[county].label, ok: false, words: {} } : null;
    }
    return weatherArea(weatherDoc, county, data.weather_points, today, Date.now());
  }

  function tagsFor(iso, today, area) {
    // Before the weather file has answered, weather days show nothing rather than a false failure.
    var w = area ? weatherTag(iso, today, area) : null;
    return dayTags(iso, data, county, w);
  }

  var shownDay = null;   // the LA date the grid was last built for

  // Full rebuild: on load, county change, weather arrival, and a new day in Los Angeles.
  function render(announce) {
    var today = todayLA();
    shownDay = today;
    var area = areaFor(today);
    var grid = document.getElementById("grid-body");
    var focusedIso = document.activeElement && document.activeElement.getAttribute
      ? document.activeElement.getAttribute("data-date") : null;
    grid.textContent = "";
    var dates = windowDates(today);
    if (!selected || dates.indexOf(selected) < 0) selected = today;
    gridRows(today).forEach(function (row) {
      var tr = el("tr");
      row.forEach(function (iso) {
        var td = el("td");
        if (iso) {
          var tags = tagsFor(iso, today, area);
          var b = el("button", "day" + (iso === today ? " day--today" : "") +
            (iso > data.events.meta.verified_through ? " day--unchecked" : ""));
          b.type = "button";
          b.setAttribute("data-date", iso);
          b.setAttribute("aria-pressed", iso === selected ? "true" : "false");
          if (iso === today) b.setAttribute("aria-current", "date");
          b.setAttribute("aria-label", fmtDate(iso, true, true) + ": " + tags.map(function (t) { return t.text; }).join("; "));
          var num = el("span", "day-num");
          if (iso.slice(8, 10) === "01" || iso === today) {
            num.appendChild(el("span", "day-mon", MONTHS[+iso.slice(5, 7) - 1].slice(0, 3) + " "));
          }
          num.appendChild(document.createTextNode(String(+iso.slice(8, 10))));
          num.setAttribute("aria-hidden", "true");
          b.appendChild(num);
          var list = tagSpans(tags);
          list.setAttribute("aria-hidden", "true");
          b.appendChild(list);
          // the narrow-screen count: things on that day (not weather, quiet, or the unchecked note)
          var n = tags.filter(function (t) { return t.type !== "quiet" && t.type !== "unchecked" && t.type !== "weather"; }).length;
          var count = el("span", "day-count", n ? String(n) : "");
          count.setAttribute("aria-hidden", "true");
          b.appendChild(count);
          td.appendChild(b);
        } else {
          td.className = "out";
        }
        tr.appendChild(td);
      });
      grid.appendChild(tr);
    });
    renderDetail(today, area, !!announce);
    if (focusedIso) {
      var f = grid.querySelector('button[data-date="' + focusedIso + '"]');
      if (f) f.focus();
    }
    var wl = document.getElementById("weather-line-area");
    if (wl) wl.textContent = data.weather_points.points[county].label;
  }

  // Selecting a day changes only aria-pressed and the panel below the grid.
  function select(iso) {
    if (todayLA() !== shownDay) { selected = iso; render(true); return; }
    selected = iso;
    Array.prototype.forEach.call(document.querySelectorAll("#grid-body button[data-date]"), function (b) {
      b.setAttribute("aria-pressed", b.getAttribute("data-date") === iso ? "true" : "false");
    });
    renderDetail(shownDay, areaFor(shownDay), true);
    var box = document.getElementById("detail");
    // On a phone the panel sits below the grid; bring it into view without moving focus.
    if (box.scrollIntoView && root.matchMedia && root.matchMedia("(max-width: 760px)").matches) {
      box.scrollIntoView({ block: "nearest" });
    }
  }

  // The panel below the grid is redrawn silently. Only a visitor's own action (a day, the county)
  // writes to #announce, a separate visually hidden node that is always aria-live="polite".
  function renderDetail(today, area, announce) {
    var box = document.getElementById("detail");
    var tags = tagsFor(selected, today, area);
    box.textContent = "";
    box.appendChild(el("h2", null, fmtDate(selected, true, true)));
    box.appendChild(tagList(tags));
    if (announce) {
      var live = document.getElementById("announce");
      if (live) live.textContent = fmtDate(selected, true, true) + ": " + tags.map(function (t) { return t.text; }).join("; ");
    }
  }

  function setCounty(c) {
    county = c;
    Array.prototype.forEach.call(document.querySelectorAll("[data-county]"), function (b) {
      b.setAttribute("aria-pressed", b.getAttribute("data-county") === c ? "true" : "false");
    });
    render(true);
  }

  function loadWeather() {
    if (!root.fetch) { weatherFailed = true; render(); return; }
    root.fetch("../../data/bigdays/weather.json", { cache: "no-cache" })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function (doc) { weatherDoc = doc; weatherFailed = false; render(); })
      .catch(function () { weatherFailed = true; render(); });
  }

  function start() {
    var node = document.getElementById("bigdays-data");
    try { data = JSON.parse(node.textContent); } catch (e) { return; }
    document.documentElement.classList.add("js");
    Array.prototype.forEach.call(document.querySelectorAll(".js-only"), function (x) { x.hidden = false; });
    Array.prototype.forEach.call(document.querySelectorAll("[data-county]"), function (b) {
      b.addEventListener("click", function () { setCounty(b.getAttribute("data-county")); });
    });
    var grid = document.getElementById("grid-body");
    grid.addEventListener("click", function (e) {
      var b = e.target.closest ? e.target.closest("button[data-date]") : null;
      if (b) select(b.getAttribute("data-date"));
    });
    grid.addEventListener("keydown", function (e) {
      var b = e.target.closest ? e.target.closest("button[data-date]") : null;
      var step = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7 }[e.key];
      if (!b || !step) return;
      var next = grid.querySelector('button[data-date="' + addDays(b.getAttribute("data-date"), step) + '"]');
      if (next) { e.preventDefault(); next.focus(); }
    });
    render();
    loadWeather();
    // The day changes at midnight in Los Angeles: rebuild and refetch the weather when it does.
    function checkDay() { if (todayLA() !== shownDay) { render(); loadWeather(); } }
    setInterval(checkDay, 60000);
    document.addEventListener("visibilitychange", function () { if (!document.hidden) checkDay(); });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})(typeof window !== "undefined" ? window : this);
