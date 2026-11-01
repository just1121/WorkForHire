/* Farmers market map. Spec: tools/MARKETS.md (v1.2).
 *
 * The schedule functions below mirror tools/markets/schedule.py one for one (tested in both languages:
 * tests/test_markets.py and tests/markets.test.js). Change one, change the other.
 *
 * All date and time math is in America/Los_Angeles via Intl, never the visitor's own zone. Dates are
 * "YYYY-MM-DD" strings; day arithmetic runs on UTC midnights so daylight saving never shifts a day.
 * Data comes from the JSON block inlined by tools/markets/inline.py; no runtime AI, no trackers.
 */
(function (root) {
  "use strict";

  var TZ = "America/Los_Angeles";
  var WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  var WEEKDAY_NAMES = { Mon: "Monday", Tue: "Tuesday", Wed: "Wednesday", Thu: "Thursday",
    Fri: "Friday", Sat: "Saturday", Sun: "Sunday" };
  var MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
    "September", "October", "November", "December"];
  var ORDINALS = { 1: "First", 2: "Second", 3: "Third", 4: "Fourth", 5: "Fifth" };
  var WEEK_DAYS = 7;
  var STALE_DAYS = 45;
  var STATES = {
    today: "Scheduled today",
    week: "Scheduled this week",
    season: "In season, not this week",
    closed: "Closed for season",
    unconfirmed: "Schedule not yet confirmed"
  };
  var GLYPHS = { today: "T", week: "W", season: "S", closed: "C", unconfirmed: "?" };

  // ------------------------------------------------------------ dates

  var laFormat = null;
  function laNow(instant) {
    // {date: "YYYY-MM-DD", hm: "HH:MM"} in Los Angeles for a Date (an instant).
    if (!laFormat) {
      laFormat = new Intl.DateTimeFormat("en-US", { timeZone: TZ, year: "numeric", month: "2-digit",
        day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
    }
    var p = {};
    laFormat.formatToParts(instant).forEach(function (x) { p[x.type] = x.value; });
    var hour = p.hour === "24" ? "00" : p.hour;
    return { date: p.year + "-" + p.month + "-" + p.day, hm: hour + ":" + p.minute };
  }

  function toUTC(iso) {
    return Date.UTC(+iso.slice(0, 4), +iso.slice(5, 7) - 1, +iso.slice(8, 10));
  }
  function fromUTC(ms) {
    return new Date(ms).toISOString().slice(0, 10);
  }
  function addDays(iso, n) {
    return fromUTC(toUTC(iso) + n * 86400000);
  }
  function daysBetween(a, b) {
    return Math.round((toUTC(b) - toUTC(a)) / 86400000);
  }
  function weekdayKey(iso) {
    return WEEKDAYS[(new Date(toUTC(iso)).getUTCDay() + 6) % 7];
  }
  function mmdd(iso) {
    return iso.slice(5, 10);
  }

  // ------------------------------------------------------------ rules

  function inSeason(session, d, validThrough) {
    if (session.recurrence === "confirm_each_year") {
      if (!validThrough || d > validThrough) return false;
    }
    var dates = session.dates;
    if (dates && dates.length) {
      var lo = dates.reduce(function (a, b) { return a < b ? a : b; });
      var hi = dates.reduce(function (a, b) { return a > b ? a : b; });
      return lo <= d && d <= hi;
    }
    var ss = session.season_start, se = session.season_end;
    if (!ss || !se) return true;
    var md = mmdd(d);
    if (ss <= se) return ss <= md && md <= se;
    return md >= ss || md <= se;
  }

  function occursOn(session, d, validThrough) {
    if (!inSeason(session, d, validThrough)) return false;
    if (session.dates && session.dates.length) return session.dates.indexOf(d) >= 0;
    if (weekdayKey(d) !== session.weekday) return false;
    if (session.nth_of_month) return Math.floor((+d.slice(8, 10) - 1) / 7) + 1 === session.nth_of_month;
    return true;
  }

  function exceptionOn(market, d) {
    var list = market.exceptions || [];
    for (var i = 0; i < list.length; i++) if (list[i].date === d) return list[i];
    return null;
  }

  function sessionsOn(market, d) {
    var exc = exceptionOn(market, d);
    if (exc && exc.status === "closed") return [];
    var vt = market.schedule_valid_through;
    return market.sessions
      .filter(function (s) { return occursOn(s, d, vt); })
      .map(function (s) { return { session: s, exception: exc }; })
      .sort(function (a, b) { return a.session.start < b.session.start ? -1 : a.session.start > b.session.start ? 1 : 0; });
  }

  // A moved day keeps its row (to show the note) but never counts as scheduled at the regular hours.
  function counts(exc) {
    return !(exc && exc.status === "moved");
  }

  function state(market, today, hm) {
    var week = false;
    for (var offset = 0; offset < WEEK_DAYS; offset++) {
      var list = sessionsOn(market, addDays(today, offset));
      for (var i = 0; i < list.length; i++) {
        if (!counts(list[i].exception)) continue;
        if (offset === 0) {
          if (hm < list[i].session.end) return "today";
        } else {
          week = true;
        }
      }
    }
    if (week) return "week";
    var vt = market.schedule_valid_through;
    if (market.sessions.some(function (s) { return inSeason(s, today, vt); })) return "season";
    if (market.sessions.some(function (s) { return s.recurrence === "confirm_each_year"; })) {
      if (!vt || today > vt) return "unconfirmed";
    }
    return "closed";
  }

  function stateLabel(key, today) {
    if (key === "unconfirmed") return "Schedule not yet confirmed for " + today.slice(0, 4);
    return STATES[key];
  }

  function scheduledNow(market, today, hm) {
    return sessionsOn(market, today).some(function (x) {
      return counts(x.exception) && x.session.start <= hm && hm < x.session.end;
    });
  }

  function yearRound(market) {
    return market.sessions.some(function (s) { return s.recurrence === "year_round"; });
  }

  function hasWeekday(market, wd) {
    return market.sessions.some(function (s) {
      if (s.dates && s.dates.length) return s.dates.some(function (x) { return weekdayKey(x) === wd; });
      return s.weekday === wd;
    });
  }

  function thisWeek(markets, today, hm) {
    var out = [];
    for (var offset = 0; offset < WEEK_DAYS; offset++) {
      var d = addDays(today, offset);
      var rows = [];
      markets.forEach(function (m) {
        sessionsOn(m, d).forEach(function (x) {
          if (offset === 0 && counts(x.exception) && hm >= x.session.end) return;
          rows.push({ market: m, session: x.session, exception: x.exception });
        });
      });
      rows.sort(function (a, b) {
        if (a.session.start !== b.session.start) return a.session.start < b.session.start ? -1 : 1;
        return a.market.name < b.market.name ? -1 : a.market.name > b.market.name ? 1 : 0;
      });
      out.push({ date: d, rows: rows });
    }
    return out;
  }

  function isStale(checked, today) {
    return !checked || daysBetween(checked, today) > STALE_DAYS;
  }

  // ------------------------------------------------------------ words

  function fmtTime(hm) {
    var h = +hm.slice(0, 2), m = +hm.slice(3, 5);
    if (h === 12 && m === 0) return "noon";
    var suffix = h < 12 ? "am" : "pm";
    var h12 = h % 12 || 12;
    return m === 0 ? h12 + " " + suffix : h12 + ":" + (m < 10 ? "0" : "") + m + " " + suffix;
  }

  function fmtDate(iso, withYear) {
    var s = MONTHS[+iso.slice(5, 7) - 1] + " " + (+iso.slice(8, 10));
    return withYear ? s + ", " + iso.slice(0, 4) : s;
  }

  var MONTH_END = [31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  function fmtSeason(ss, se) {
    var m1 = +ss.slice(0, 2), d1 = +ss.slice(3, 5), m2 = +se.slice(0, 2), d2 = +se.slice(3, 5);
    if (d1 === 1 && d2 >= MONTH_END[m2 - 1] - (m2 === 2 ? 1 : 0)) {
      if (m1 === m2) return MONTHS[m1 - 1];
      return MONTHS[m1 - 1] + " through " + MONTHS[m2 - 1];
    }
    return MONTHS[m1 - 1] + " " + d1 + " to " + MONTHS[m2 - 1] + " " + d2;
  }

  var YEAR_NOT_GIVEN = " (year not given by CDFA)";

  function describeSession(session, validThrough, yearInferred) {
    var hours = fmtTime(session.start) + " to " + fmtTime(session.end);
    var dates = session.dates;
    if (dates && dates.length) {
      var listed = dates.map(function (x) { return fmtDate(x); });
      var text = listed.length > 1 ? listed.slice(0, -1).join(", ") + " and " + listed[listed.length - 1] : listed[0];
      if (yearInferred) return hours + " on " + text + YEAR_NOT_GIVEN;
      return hours + " on " + text + ", " + dates[dates.length - 1].slice(0, 4);
    }
    var day = WEEKDAY_NAMES[session.weekday];
    var when = session.nth_of_month ? ORDINALS[session.nth_of_month] + " " + day + " of the month" : day + "s";
    var season;
    if (session.recurrence === "year_round") {
      season = "year-round";
    } else {
      season = fmtSeason(session.season_start, session.season_end);
      if (session.recurrence === "confirm_each_year") {
        if (yearInferred) season += YEAR_NOT_GIVEN;
        else if (validThrough) season += ", " + validThrough.slice(0, 4);
      }
    }
    return when + ", " + hours + ", " + season;
  }

  // Only plain web links leave the data; anything else (javascript:, data:) is dropped.
  function safeUrl(href) {
    return typeof href === "string" && /^https?:\/\//i.test(href) ? href : null;
  }

  // Focus survives a re-render: remember which zone and market a focused button belonged to, then
  // find the equivalent button in the new DOM. Pure functions over {zone, id} so Node can test them.
  function focusKey(zone, id) {
    return zone && id ? { zone: zone, id: id } : null;
  }
  function findFocusTarget(key, candidates) {
    // candidates: [{zone, id, el}] in document order; returns the el to focus or null.
    if (!key) return null;
    for (var i = 0; i < candidates.length; i++) {
      if (candidates[i].zone === key.zone && candidates[i].id === key.id) return candidates[i].el;
    }
    for (var j = 0; j < candidates.length; j++) if (candidates[j].id === key.id) return candidates[j].el;
    return null;
  }

  // True when a click at (x, y) is inside the box (the dialog's own area, padding included).
  function insideBox(x, y, r) {
    return x >= r.left && x <= r.right && y >= r.top && y <= r.bottom;
  }

  var api = {
    TZ: TZ, WEEKDAYS: WEEKDAYS, STATES: STATES, GLYPHS: GLYPHS, STALE_DAYS: STALE_DAYS,
    laNow: laNow, addDays: addDays, daysBetween: daysBetween, weekdayKey: weekdayKey,
    inSeason: inSeason, occursOn: occursOn, exceptionOn: exceptionOn, sessionsOn: sessionsOn,
    state: state, stateLabel: stateLabel, scheduledNow: scheduledNow, counts: counts, safeUrl: safeUrl,
    focusKey: focusKey, findFocusTarget: findFocusTarget, insideBox: insideBox, yearRound: yearRound, hasWeekday: hasWeekday,
    thisWeek: thisWeek, isStale: isStale, fmtTime: fmtTime, fmtDate: fmtDate, fmtSeason: fmtSeason,
    describeSession: describeSession
  };

  if (typeof module === "object" && module.exports) {
    module.exports = api;
    return;
  }
  root.Markets = api;
  if (typeof document === "undefined") return;

  // ============================================================ page

  var data = null, map = null, markers = {}, now = null, lastFocus = null, lastFocusKey = null;

  function zoneOf(e) {
    if (!e || !e.closest) return null;
    if (e.closest("#week-days")) return "week";
    if (e.closest("#market-list")) return "list";
    if (e.closest(".leaflet-marker-icon")) return "pin";
    return null;
  }
  function keyFor(e) {
    if (!e || !e.getAttribute) return null;
    var id = e.getAttribute("data-id");
    if (!id) {
      for (var k in markers) if (markers[k].getElement && markers[k].getElement() === e) id = k;
    }
    return focusKey(zoneOf(e), id);
  }
  function focusCandidates() {
    var out = [];
    Array.prototype.forEach.call(document.querySelectorAll("button.linklike[data-id]"), function (b) {
      out.push({ zone: zoneOf(b), id: b.getAttribute("data-id"), el: b });
    });
    Object.keys(markers).forEach(function (k) {
      var e = markers[k].getElement && markers[k].getElement();
      if (e) out.push({ zone: "pin", id: k, el: e });
    });
    return out;
  }

  function el(tag, attrs, text) {
    var e = document.createElement(tag);
    if (attrs) Object.keys(attrs).forEach(function (k) {
      if (k === "class") e.className = attrs[k]; else e.setAttribute(k, attrs[k]);
    });
    if (text != null) e.textContent = text;
    return e;
  }

  function dayHeading(iso, offset) {
    var name = WEEKDAY_NAMES[weekdayKey(iso)];
    var label = name + ", " + fmtDate(iso);
    if (offset === 0) return "Today, " + label;
    if (offset === 1) return "Tomorrow, " + label;
    return label;
  }

  function badge(key) {
    var b = el("span", { class: "badge badge--" + key });
    b.appendChild(el("span", { class: "badge-glyph", "aria-hidden": "true" }, GLYPHS[key]));
    b.appendChild(document.createTextNode(stateLabel(key, now.date)));
    return b;
  }

  function staleLine(m) {
    if (!isStale(m.schedule_checked, now.date)) return null;
    return el("p", { class: "stale" }, "Checked " + fmtDate(m.schedule_checked, true) + "; confirm hours before you go.");
  }

  // ---- This week
  function renderWeek() {
    var box = document.getElementById("week-days");
    box.textContent = "";
    thisWeek(data.markets, now.date, now.hm).forEach(function (day, offset) {
      var sec = el("section", { class: "week-day" });
      sec.appendChild(el("h3", null, dayHeading(day.date, offset)));
      if (!day.rows.length) {
        sec.appendChild(el("p", { class: "week-none" }, "No markets scheduled."));
      } else {
        var ul = el("ul");
        day.rows.forEach(function (r) {
          var li = el("li");
          var moved = !counts(r.exception);
          var t = el("span", { class: "week-time" }, moved ? "Moved" : fmtTime(r.session.start) + " to " + fmtTime(r.session.end));
          var b = el("button", { type: "button", class: "linklike", "data-id": r.market.id }, r.market.name);
          li.appendChild(t);
          li.appendChild(b);
          li.appendChild(document.createTextNode(", " + r.market.city));
          if (moved) li.appendChild(el("span", { class: "week-exc" }, r.exception.note));
          ul.appendChild(li);
        });
        sec.appendChild(ul);
      }
      box.appendChild(sec);
    });
  }

  // ---- List
  function marketItem(m) {
    var li = el("li", { class: "market", id: "m-" + m.id, "data-id": m.id });
    var h = el("h3");
    var b = el("button", { type: "button", class: "linklike", "data-id": m.id }, m.name);
    h.appendChild(b);
    li.appendChild(h);
    li.appendChild(badge(state(m, now.date, now.hm)));
    li.appendChild(el("p", { class: "market-where" }, m.address + " (" + m.county + " County)"));
    var ul = el("ul", { class: "sessions" });
    m.sessions.forEach(function (s) { ul.appendChild(el("li", null, describeSession(s, m.schedule_valid_through, m.season_year_inferred))); });
    li.appendChild(ul);
    var st = staleLine(m);
    if (st) li.appendChild(st);
    return li;
  }

  function filtered() {
    var county = document.getElementById("f-county").value;
    var day = document.getElementById("f-day").value;
    var schedNow = document.getElementById("f-scheduled").checked;
    var yr = document.getElementById("f-year").checked;
    return data.markets.filter(function (m) {
      if (county && m.county !== county) return false;
      if (day && !hasWeekday(m, day)) return false;
      if (schedNow && !scheduledNow(m, now.date, now.hm)) return false;
      if (yr && !yearRound(m)) return false;
      return true;
    });
  }

  function renderList() {
    var list = document.getElementById("market-list");
    list.textContent = "";
    var shown = filtered();
    shown.forEach(function (m) { list.appendChild(marketItem(m)); });
    document.getElementById("list-count").textContent =
      "Showing " + shown.length + " of " + data.markets.length + " markets.";
  }

  // ---- Map
  function pinIcon(key) {
    return root.L.divIcon({
      className: "pin pin--" + key,
      html: '<span class="pin-glyph" aria-hidden="true">' + GLYPHS[key] + "</span>",
      iconSize: [44, 44], iconAnchor: [22, 22]
    });
  }

  function renderMap() {
    var L = root.L;
    var box = document.getElementById("map");
    if (!L || !box) {
      var note = document.getElementById("map-unavailable");
      if (note) note.hidden = false;
      if (box) box.hidden = true;
      return;
    }
    if (!map) {
      map = L.map(box, { scrollWheelZoom: false });
      L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxZoom: 19,
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
      }).addTo(map);
      // Give the map its view first: Leaflet only creates marker elements on a map that has one.
      map.fitBounds(data.markets.map(function (m) { return [m.lat, m.lon]; }), { padding: [20, 20] });
      map.whenReady(function () {
        data.markets.forEach(function (m) {
          var key = state(m, now.date, now.hm);
          var mk = L.marker([m.lat, m.lon], { icon: pinIcon(key), keyboard: true, title: m.name + ": " + stateLabel(key, now.date) });
          mk.on("click", function () { openDialog(m.id, mk.getElement()); });
          mk.addTo(map);
          markers[m.id] = mk;
          var e = mk.getElement();
          if (!e) return;
          e.setAttribute("aria-label", m.name + ", " + stateLabel(key, now.date));
          // Leaflet's own Enter handling is not reliable; open on Enter and Space explicitly.
          e.addEventListener("keydown", function (ev) {
            if (ev.key === "Enter" || ev.key === " " || ev.key === "Spacebar") {
              ev.preventDefault();
              mk.fire("click");
            }
          });
        });
      });
      return;
    }
    // Later passes update classes and labels in place so a focused pin keeps focus.
    data.markets.forEach(function (m) {
      var key = state(m, now.date, now.hm);
      var e = markers[m.id] && markers[m.id].getElement();
      if (!e) return;
      e.className = e.className.replace(/\bpin--\w+/, "pin--" + key);
      var g = e.querySelector(".pin-glyph");
      if (g) g.textContent = GLYPHS[key];
      e.setAttribute("title", m.name + ": " + stateLabel(key, now.date));
      e.setAttribute("aria-label", m.name + ", " + stateLabel(key, now.date));
    });
  }

  // ---- Dialog
  function byId(id) {
    for (var i = 0; i < data.markets.length; i++) if (data.markets[i].id === id) return data.markets[i];
    return null;
  }

  function link(href, text) {
    var ok = safeUrl(href);
    return ok ? el("a", { href: ok, rel: "noopener" }, text) : null;
  }

  function openDialog(id, returnTo) {
    var m = byId(id);
    var dlg = document.getElementById("market-dialog");
    if (!m || !dlg || dlg.open) return;
    lastFocus = returnTo || document.activeElement;
    lastFocusKey = keyFor(lastFocus) || focusKey("pin", id);
    document.getElementById("dlg-title").textContent = m.name;
    var body = document.getElementById("dlg-body");
    body.textContent = "";
    body.appendChild(badge(state(m, now.date, now.hm)));
    body.appendChild(el("p", { class: "market-where" }, m.address));
    body.appendChild(el("p", { class: "market-county" }, m.city + ", " + m.county + " County"));
    body.appendChild(el("h3", null, "When"));
    var ul = el("ul", { class: "sessions" });
    m.sessions.forEach(function (s) { ul.appendChild(el("li", null, describeSession(s, m.schedule_valid_through, m.season_year_inferred))); });
    body.appendChild(ul);
    (m.exceptions || []).forEach(function (x) {
      if (x.date >= now.date) body.appendChild(el("p", { class: "exc" }, fmtDate(x.date, true) + ": " + (x.status === "closed" ? "Closed. " : "Moved. ") + x.note));
    });
    var links = [];
    if (m.website) links.push(link(m.website, "Market website"));
    if (m.instagram) links.push(link("https://www.instagram.com/" + encodeURIComponent(m.instagram) + "/", "@" + m.instagram + " on Instagram"));
    links = links.filter(Boolean);
    if (links.length) {
      var p = el("p", { class: "links" });
      links.forEach(function (a, i) {
        if (i) p.appendChild(document.createTextNode(" · "));
        p.appendChild(a);
      });
      body.appendChild(p);
    }
    if (m.notes) body.appendChild(el("p", { class: "notes" }, m.notes));
    var st = staleLine(m);
    if (st) body.appendChild(st);
    var src = m.schedule_source === "cdfa" ? "Hours from the CDFA list" : "Hours from the market's site";
    body.appendChild(el("p", { class: "src" }, src + ", checked " + fmtDate(m.schedule_checked, true) + "."));
    if (typeof dlg.showModal === "function") dlg.showModal(); else dlg.setAttribute("open", "");
    document.getElementById("dlg-close").focus();
  }

  function closeDialog() {
    var dlg = document.getElementById("market-dialog");
    if (typeof dlg.close === "function" && dlg.open) dlg.close(); else dlg.removeAttribute("open");
  }

  function wireDialog() {
    var dlg = document.getElementById("market-dialog");
    document.getElementById("dlg-close").addEventListener("click", closeDialog);
    dlg.addEventListener("close", function () {
      var key = lastFocusKey;
      if (lastSig === null) { renderAll(); lastSig = signature(); }  // held back while the dialog was open
      var target = lastFocus && document.contains(lastFocus) ? lastFocus : findFocusTarget(key, focusCandidates());
      if (target) target.focus();
      lastFocus = null;
      lastFocusKey = null;
    });
    dlg.addEventListener("keydown", function (e) {
      if (e.key === "Escape") { e.preventDefault(); closeDialog(); return; }
      if (e.key !== "Tab") return;
      var f = dlg.querySelectorAll('a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])');
      if (!f.length) return;
      var first = f[0], last = f[f.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    });
    // Close only on a click on the backdrop, never on the dialog's own area or padding.
    dlg.addEventListener("click", function (e) {
      if (e.target !== dlg) return;
      if (e.clientX === 0 && e.clientY === 0) return;  // keyboard-generated click
      if (!insideBox(e.clientX, e.clientY, dlg.getBoundingClientRect())) closeDialog();
    });
    document.addEventListener("click", function (e) {
      var b = e.target.closest && e.target.closest("button.linklike[data-id]");
      if (b) openDialog(b.getAttribute("data-id"), b);
    });
  }

  // ---- Clock
  // Re-rendering replaces the week and list buttons; put focus back on the same market's button.
  function renderAll() {
    var key = keyFor(document.activeElement);
    renderWeek();
    renderList();
    renderMap();
    var a = document.activeElement;
    if (key && (!a || a === document.body || !document.contains(a))) {
      var t = findFocusTarget(key, focusCandidates());
      if (t) t.focus();
    }
  }

  // What the visitor can see change: each market's state and open-now flag, and the week's rows.
  // Re-rendering only when this changes keeps keyboard focus where it is on an ordinary minute.
  var lastSig = null;
  function signature() {
    var s = now.date + "|";
    data.markets.forEach(function (m) { s += state(m, now.date, now.hm) + (scheduledNow(m, now.date, now.hm) ? "o" : "") + ","; });
    thisWeek(data.markets, now.date, now.hm).forEach(function (d) { s += d.rows.length + ";"; });
    return s;
  }

  function tick() {
    now = laNow(new Date());
    var sig = signature();
    if (sig === lastSig) return;
    var dlg = document.getElementById("market-dialog");
    if (dlg && dlg.open) { renderMap(); lastSig = null; return; }  // finish after the dialog closes
    lastSig = sig;
    renderAll();
  }

  function loadData() {
    var block = document.getElementById("markets-data");
    if (block && block.textContent.trim()) {
      try { return Promise.resolve(JSON.parse(block.textContent)); } catch (e) { /* fall through */ }
    }
    return fetch("../../data/markets.json").then(function (r) { return r.json(); });
  }

  function start() {
    loadData().then(function (d) {
      data = d;
      document.documentElement.classList.add("js");
      var f = document.getElementById("filters");
      if (f) f.hidden = false;
      var w = document.getElementById("week-days");
      if (w) w.hidden = false;
      ["f-county", "f-day", "f-scheduled", "f-year"].forEach(function (id) {
        document.getElementById(id).addEventListener("change", renderList);
      });
      if (f) f.addEventListener("submit", function (e) { e.preventDefault(); });
      wireDialog();
      tick();
      setInterval(tick, 30000);
      document.addEventListener("visibilitychange", function () { if (!document.hidden) tick(); });
    }).catch(function () {
      document.documentElement.classList.add("js");
      var mb = document.getElementById("map");
      if (mb) mb.hidden = true;
      var n = document.getElementById("map-unavailable");
      if (n) n.hidden = false;
    });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})(typeof window !== "undefined" ? window : this);
