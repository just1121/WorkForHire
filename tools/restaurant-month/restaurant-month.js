/* Restaurant Month. Spec: tools/RESTAURANT-MONTH.md (v1.2).
 *
 * The rule functions below mirror tools/restaurant-month/rules.py one for one (tested in both languages
 * against tests/fixtures/cases.json). Change one, change the other.
 *
 * "Today" is the America/Los_Angeles date via Intl, never the visitor's own zone. Dates are
 * "YYYY-MM-DD" strings; day arithmetic runs on UTC midnights so daylight saving never shifts a day.
 * Data comes from the JSON block inlined by tools/restaurant-month/inline.py. No runtime AI, no trackers.
 * The map pieces are copied from tools/markets/markets.js (not shared; see BUILD-NOTES.md).
 */
(function (root) {
  "use strict";

  var TZ = "America/Los_Angeles";
  var MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
    "September", "October", "November", "December"];
  var WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
  var WEEKDAY_NAMES = { mon: "Mondays", tue: "Tuesdays", wed: "Wednesdays", thu: "Thursdays",
    fri: "Fridays", sat: "Saturdays", sun: "Sundays" };
  var MEALS = ["lunch", "brunch", "dinner", "any"];
  var MEAL_LABELS = { lunch: "Lunch", brunch: "Brunch", dinner: "Dinner", any: "Any meal" };
  var SERIES = ["napa", "sonoma"];
  var STALE_DAYS = 7;
  var BUCKETS = ["under30", "30to49", "50to79", "80up", "none"];
  var BUCKET_LABELS = { under30: "Under $30", "30to49": "$30 to $49", "50to79": "$50 to $79",
    "80up": "$80 and up", none: "No fixed price" };
  var PROMO_RE = /^(napa|sonoma)-(\d{4})$/;

  // ------------------------------------------------------------ dates

  var laFormat = null;
  function laToday(instant) {
    if (!laFormat) {
      laFormat = new Intl.DateTimeFormat("en-US", { timeZone: TZ, year: "numeric", month: "2-digit", day: "2-digit" });
    }
    var p = {};
    laFormat.formatToParts(instant || new Date()).forEach(function (x) { p[x.type] = x.value; });
    return p.year + "-" + p.month + "-" + p.day;
  }
  function toUTC(iso) { return Date.UTC(+iso.slice(0, 4), +iso.slice(5, 7) - 1, +iso.slice(8, 10)); }
  function addDays(iso, n) { return new Date(toUTC(iso) + n * 86400000).toISOString().slice(0, 10); }
  function daysBetween(a, b) { return Math.round((toUTC(b) - toUTC(a)) / 86400000); }
  function weekdayKey(iso) { return WEEKDAYS[(new Date(toUTC(iso)).getUTCDay() + 6) % 7]; }
  function isDate(s) {
    return typeof s === "string" && /^\d{4}-\d{2}-\d{2}$/.test(s) && addDays(s, 0) === s;
  }

  function fmtDate(iso, withYear) {
    var s = MONTHS[+iso.slice(5, 7) - 1] + " " + (+iso.slice(8, 10));
    return withYear ? s + ", " + iso.slice(0, 4) : s;
  }
  function fmtRange(start, end) {
    if (start === end) return fmtDate(start, true);
    if (start.slice(0, 4) !== end.slice(0, 4)) return fmtDate(start, true) + " to " + fmtDate(end, true);
    if (start.slice(0, 7) === end.slice(0, 7)) return fmtDate(start) + " to " + (+end.slice(8, 10)) + ", " + end.slice(0, 4);
    return fmtDate(start) + " to " + fmtDate(end, true);
  }

  // ------------------------------------------------------------ promotions and states

  function seriesOf(id) { var m = PROMO_RE.exec(id || ""); return m ? m[1] : null; }
  function yearOf(id) { var m = PROMO_RE.exec(id || ""); return m ? +m[2] : null; }

  function displayState(promo, today) {
    if (!promo.dates_confirmed || !promo.start || !promo.end) return "archived";
    if (today < promo.start) return "upcoming";
    if (today <= promo.end) return "active";
    return "archived";
  }

  function seriesView(promotions, series, today) {
    var mine = promotions.filter(function (p) { return seriesOf(p.id) === series; });
    if (!mine.length) return null;
    // An active season wins, then the next upcoming one, then the latest year (archived).
    var active = mine.filter(function (p) { return displayState(p, today) === "active"; });
    var upcoming = mine.filter(function (p) { return displayState(p, today) === "upcoming"; });
    var current;
    if (active.length) current = active.reduce(function (a, b) { return b.start > a.start ? b : a; });
    else if (upcoming.length) current = upcoming.reduce(function (a, b) { return b.start < a.start ? b : a; });
    else current = mine.reduce(function (a, b) { return yearOf(b.id) > yearOf(a.id) ? b : a; });
    var state = displayState(current, today);
    var reference = null;
    if (state === "archived") {
      mine.forEach(function (p) {
        if (p.dates_confirmed && p.end && p.end < today && (!reference || p.end > reference.end)) reference = p;
      });
    }
    var nextYear;
    if (state === "archived" && current.dates_confirmed && current.end && current.end < today) nextYear = yearOf(current.id) + 1;
    else if (state === "archived") nextYear = Math.max(yearOf(current.id), +today.slice(0, 4));  // unconfirmed: never a past year
    else nextYear = yearOf(current.id);
    return { series: series, current: current, state: state, reference: reference, next_year: nextYear };
  }

  function countdownDays(today, start) { return daysBetween(today, start); }
  function countdownText(today, start) {
    var n = countdownDays(today, start);
    return n === 1 ? "Starts tomorrow." : "Starts in " + n + " days.";
  }
  function bannerText(view) {
    if (view.state !== "archived") return null;
    return view.current.name + " returns: " + view.next_year + " dates to be announced.";
  }
  function referenceLabel(promo) { return yearOf(promo.id) + " participants, for reference"; }
  function confirmedLabel(promo) { return "Confirmed for " + yearOf(promo.id); }

  // ------------------------------------------------------------ prices and offers

  function priceBucket(price) {
    if (price === null || price === undefined) return "none";
    if (price < 30) return "under30";
    if (price < 50) return "30to49";
    if (price < 80) return "50to79";
    return "80up";
  }
  function fmtPrice(price) {
    if (price === null || price === undefined) return null;
    return Number.isInteger(price) ? "$" + price : "$" + price.toFixed(2);
  }

  function offerWindow(offer, promo) {
    return [offer.start || promo.start, offer.end || promo.end];
  }
  function offerOn(offer, promo, date) {
    var w = offerWindow(offer, promo);
    if (!w[0] || !w[1] || date < w[0] || date > w[1]) return false;
    if (offer.weekdays && offer.weekdays.length && offer.weekdays.indexOf(weekdayKey(date)) < 0) return false;
    if ((offer.exclusions || []).indexOf(date) >= 0) return false;
    return true;
  }
  function offerCurrent(offer, promo, today) {
    var end = offerWindow(offer, promo)[1];
    return !!end && today <= end;
  }
  function offerMatches(offer, filters) {
    var b = filters.bucket || "";
    if (b && priceBucket(offer.price) !== b) return false;
    var meal = filters.meal || "";
    if (meal && offer.meal !== meal) return false;  // "any" matches only All meals
    return true;
  }
  function filtersActive(filters) { return !!(filters.bucket || filters.meal); }
  var LABEL_RE = /^([^:\n]{1,40}):/;
  function offerLabelShown(offer) {
    var m = LABEL_RE.exec(offer.description || "");
    return !(m && m[1].toLowerCase().indexOf("course") < 0);
  }
  function eligibleOffers(entry, promo, filters, today) {
    return (entry.offers || []).filter(function (o) { return offerCurrent(o, promo, today) && offerMatches(o, filters); });
  }
  function entryMatches(entry, promo, filters, today) {
    if (filters.promotion && entry.promotion_id !== filters.promotion) return false;
    if (filters.town && entry.town !== filters.town) return false;
    if (filters.booking && !entry.booking_url) return false;
    if (filters.bucket || filters.meal) return eligibleOffers(entry, promo, filters, today).length > 0;
    return true;
  }
  // Sort price (Jay, 2026-11-01): cheapest lunch or dinner offer matching the filters; an "any" offer
  // (the $6 Sweet Perk) never sets it. With a meal filter chosen, offers for that meal count.
  var SORT_MEALS = ["lunch", "dinner"];
  function lowestEligiblePrice(entry, promo, filters, today) {
    var meals = filters.meal ? [filters.meal] : SORT_MEALS, low = null;
    eligibleOffers(entry, promo, filters, today).forEach(function (o) {
      if (o.price !== null && o.price !== undefined && meals.indexOf(o.meal) >= 0 && (low === null || o.price < low)) low = o.price;
    });
    return low;
  }
  function cmp(a, b) { return a < b ? -1 : a > b ? 1 : 0; }
  function sortEntries(entries, by, promosById, filters, today) {
    var keyed = entries.map(function (e) {
      var k;
      if (by === "price") {
        var p = lowestEligiblePrice(e, promosById[e.promotion_id], filters, today);
        k = [p === null ? 1 : 0, p === null ? 0 : p, e.name.toLowerCase(), e.id];
      } else {
        k = [e.town.toLowerCase(), e.name.toLowerCase(), e.id];
      }
      return { e: e, k: k };
    });
    keyed.sort(function (x, y) {
      for (var i = 0; i < x.k.length; i++) { var c = cmp(x.k[i], y.k[i]); if (c) return c; }
      return 0;
    });
    return keyed.map(function (x) { return x.e; });
  }

  function describeOfferWhen(offer, promo) {
    var parts = [];
    var w = offerWindow(offer, promo);
    if ((offer.start && offer.start !== promo.start) || (offer.end && offer.end !== promo.end)) parts.push(fmtRange(w[0], w[1]));
    var wds = offer.weekdays;
    if (wds && wds.length && wds.length < 7) {
      var names = WEEKDAYS.filter(function (d) { return wds.indexOf(d) >= 0; }).map(function (d) { return WEEKDAY_NAMES[d]; });
      parts.push(names.length > 1 ? names.slice(0, -1).join(", ") + " and " + names[names.length - 1] : names[0]);
    }
    var exc = (offer.exclusions || []).slice().sort();
    if (exc.length) parts.push("not " + exc.map(function (x) { return fmtDate(x); }).join(", "));
    return parts.join("; ");
  }

  function isStale(lastChecked, today) { return !lastChecked || daysBetween(lastChecked, today) > STALE_DAYS; }
  function staleText(lastChecked) { return "Checked " + fmtDate(lastChecked, true) + "; confirm with the restaurant."; }
  function safeUrl(href) { return typeof href === "string" && /^https?:\/\//i.test(href) ? href : null; }
  function insideBox(x, y, r) { return x >= r.left && x <= r.right && y >= r.top && y <= r.bottom; }

  var api = {
    TZ: TZ, MEALS: MEALS, MEAL_LABELS: MEAL_LABELS, SERIES: SERIES, STALE_DAYS: STALE_DAYS, BUCKETS: BUCKETS,
    BUCKET_LABELS: BUCKET_LABELS, laToday: laToday, addDays: addDays, daysBetween: daysBetween,
    weekdayKey: weekdayKey, isDate: isDate, fmtDate: fmtDate, fmtRange: fmtRange, seriesOf: seriesOf,
    yearOf: yearOf, displayState: displayState, seriesView: seriesView, countdownDays: countdownDays,
    countdownText: countdownText, bannerText: bannerText, referenceLabel: referenceLabel,
    confirmedLabel: confirmedLabel, priceBucket: priceBucket, fmtPrice: fmtPrice, offerWindow: offerWindow,
    offerOn: offerOn, offerCurrent: offerCurrent, offerMatches: offerMatches, eligibleOffers: eligibleOffers,
    entryMatches: entryMatches, lowestEligiblePrice: lowestEligiblePrice, sortEntries: sortEntries,
    describeOfferWhen: describeOfferWhen, filtersActive: filtersActive, offerLabelShown: offerLabelShown, isStale: isStale, staleText: staleText, safeUrl: safeUrl,
    insideBox: insideBox
  };

  if (typeof module === "object" && module.exports) {
    module.exports = api;
    return;
  }
  root.RestaurantMonth = api;
  if (typeof document === "undefined") return;

  // ============================================================ page

  var data = null, today = null, promosById = {}, map = null, pinLayer = null, lastFocus = null;

  function el(tag, attrs, text) {
    var e = document.createElement(tag);
    if (attrs) Object.keys(attrs).forEach(function (k) {
      if (k === "class") e.className = attrs[k]; else e.setAttribute(k, attrs[k]);
    });
    if (text != null) e.textContent = text;
    return e;
  }
  function link(href, text, cls, label) {
    var ok = safeUrl(href);
    if (!ok) return null;
    var a = el("a", { href: ok, rel: "noopener" }, text);
    if (cls) a.className = cls;
    if (label) a.setAttribute("aria-label", label);
    return a;
  }
  function joinLinks(p, links) {
    links.filter(Boolean).forEach(function (a, i) {
      if (i) p.appendChild(document.createTextNode(" · "));
      p.appendChild(a);
    });
    return p;
  }
  function entriesOf(promo) {
    return data.entries.filter(function (e) { return e.promotion_id === promo.id; });
  }
  function byName(a, b) { return cmp(a.name.toLowerCase(), b.name.toLowerCase()) || cmp(a.id, b.id); }

  // An entry with its offers, Book link, website and phone (Upcoming and Active, list and dialog).
  // filters (optional): the Active filters; offers that satisfy them are marked in words.
  function fullEntry(e, promo, tag, filters) {
    var li = el(tag || "li", { class: "entry", "data-id": e.id });
    if (tag !== "div") li.appendChild(el("h3", null, e.name));
    li.appendChild(el("p", { class: "where" }, e.town + (e.cuisine ? " · " + e.cuisine : "")));
    if ((e.offers || []).length) {
      var ul = el("ul", { class: "offers" });
      e.offers.forEach(function (o) {
        var item = el("li");
        item.appendChild(el("span", { class: "price" }, fmtPrice(o.price) || BUCKET_LABELS.none));
        if (offerLabelShown(o)) item.appendChild(el("span", { class: "meal" }, MEAL_LABELS[o.meal]));
        item.appendChild(el("span", { class: "desc" }, o.description));
        var when = describeOfferWhen(o, promo);
        if (when) item.appendChild(el("span", { class: "when" }, when));
        var current = offerCurrent(o, promo, today);
        if (!current) {
          item.className = "ended";
          item.appendChild(el("span", { class: "visually-hidden" }, " (ended)"));
        } else if (filters && filtersActive(filters) && offerMatches(o, filters)) {
          item.appendChild(el("span", { class: "match" }, "matches your filters"));
        }
        ul.appendChild(item);
      });
      li.appendChild(ul);
    }
    var links = [
      link(e.booking_url, "Book", "book", "Book " + e.name + " (the restaurant's booking page)"),
      link(e.website, "Website"),
      e.phone ? el("a", { href: "tel:" + e.phone.replace(/[^0-9+]/g, "") }, e.phone) : null
    ];
    if (links.some(Boolean)) li.appendChild(joinLinks(el("p", { class: "links" }), links));
    if (displayState(promo, today) === "active" && isStale(e.last_checked, today)) {
      li.appendChild(el("p", { class: "stale" }, staleText(e.last_checked)));
    }
    return li;
  }

  // Archived: names, towns and links only; no prices, no Book links.
  function referenceEntry(e) {
    var li = el("li", { class: "entry entry--ref" });
    li.appendChild(el("h3", null, e.name));
    li.appendChild(el("p", { class: "where" }, e.town));
    var w = link(e.website, "Website");
    if (w) li.appendChild(joinLinks(el("p", { class: "links" }), [w]));
    return li;
  }

  function renderSeries(box) {
    box.textContent = "";
    SERIES.forEach(function (s) {
      var v = seriesView(data.promotions, s, today);
      if (!v) return;
      var cur = v.current;
      var sec = el("section", { class: "series series--" + v.state, id: "s-" + s, "aria-labelledby": "s-" + s + "-h" });
      sec.appendChild(el("h2", { id: "s-" + s + "-h" }, cur.name));
      if (v.state === "upcoming" || v.state === "active") {
        sec.appendChild(el("p", { class: "dates" }, fmtRange(cur.start, cur.end)));
      }
      if (v.state === "active" && !entriesOf(cur).length) {
        var none = el("p", { class: "empty" }, "No participants listed yet. ");
        var org = link(cur.source_url, "Organizer's page");
        if (org) none.appendChild(org);
        sec.appendChild(none);
      }
      if (v.state === "upcoming") {
        sec.appendChild(el("p", { class: "countdown" }, countdownText(today, cur.start)));
        sec.appendChild(el("p", { class: "label" }, confirmedLabel(cur)));
        var list = entriesOf(cur).sort(byName);
        if (list.length) {
          var ul = el("ul", { class: "entries" });
          list.forEach(function (e) { ul.appendChild(fullEntry(e, cur)); });
          sec.appendChild(ul);
        } else {
          sec.appendChild(el("p", { class: "empty" }, "No participants confirmed for " + yearOf(cur.id) + " yet."));
        }
      }
      if (v.state === "archived") {
        sec.appendChild(el("p", { class: "banner" }, bannerText(v)));
        if (v.reference) {
          sec.appendChild(el("p", { class: "label" }, referenceLabel(v.reference)));
          var refs = entriesOf(v.reference).sort(byName);
          if (refs.length) {
            var ru = el("ul", { class: "entries entries--ref" });
            refs.forEach(function (e) { ru.appendChild(referenceEntry(e)); });
            sec.appendChild(ru);
          } else {
            sec.appendChild(el("p", { class: "empty" }, "Participant list not yet collected."));
          }
        } else {
          sec.appendChild(el("p", { class: "empty" }, "Participant list not yet collected."));
        }
      }
      box.appendChild(sec);
    });
  }

  // ---- Active: filters, map, list
  // Active promotions that have entries; one with none gets its own line and no map or filters.
  function activePromos() {
    return data.promotions.filter(function (p) { return displayState(p, today) === "active" && entriesOf(p).length > 0; });
  }
  function activeEntries() {
    return data.entries.filter(function (e) { return displayState(promosById[e.promotion_id], today) === "active"; });
  }
  function readFilters() {
    return {
      promotion: document.getElementById("f-promotion").value,
      town: document.getElementById("f-town").value,
      bucket: document.getElementById("f-price").value,
      meal: document.getElementById("f-meal").value,
      booking: document.getElementById("f-booking").checked
    };
  }
  function fillSelect(id, options, allText) {
    var s = document.getElementById(id);
    var keep = s.value;
    s.textContent = "";
    s.appendChild(el("option", { value: "" }, allText));
    options.forEach(function (o) { s.appendChild(el("option", { value: o[0] }, o[1])); });
    s.value = options.some(function (o) { return o[0] === keep; }) ? keep : "";
  }
  function setupFilters() {
    fillSelect("f-promotion", activePromos().map(function (p) { return [p.id, p.name]; }), "All");
    var towns = [];
    activeEntries().forEach(function (e) { if (towns.indexOf(e.town) < 0) towns.push(e.town); });
    towns.sort();
    fillSelect("f-town", towns.map(function (t) { return [t, t]; }), "All towns");
  }

  function shownEntries() {
    var f = readFilters();
    var list = activeEntries().filter(function (e) { return entryMatches(e, promosById[e.promotion_id], f, today); });
    return sortEntries(list, document.getElementById("f-sort").value, promosById, f, today);
  }

  function renderActive() {
    var box = document.getElementById("active-box");
    var on = activePromos().length > 0;
    box.hidden = !on;
    if (!on) return;
    var shown = shownEntries();
    var ul = document.getElementById("active-list");
    ul.textContent = "";
    var f = readFilters();
    shown.forEach(function (e) { ul.appendChild(fullEntry(e, promosById[e.promotion_id], "li", f)); });
    document.getElementById("list-count").textContent =
      "Showing " + shown.length + " of " + activeEntries().length + " restaurants.";
    renderMap(shown);
  }

  // ---- Map (copied from the Markets map: pins added after the map has a view, 44px hit area,
  // aria-label on each pin, explicit Enter and Space handling)
  function pinIcon(series) {
    return root.L.divIcon({
      className: "pin pin--" + series,
      html: '<span class="pin-glyph" aria-hidden="true">' + (series === "napa" ? "N" : "S") + "</span>",
      iconSize: [44, 44], iconAnchor: [22, 22]
    });
  }
  function renderMap(shown) {
    var L = root.L;
    var box = document.getElementById("map");
    if (!L || !box) {
      document.getElementById("map-unavailable").hidden = false;
      if (box) box.hidden = true;
      return;
    }
    var pinned = shown.filter(function (e) { return e.lat !== null && e.lon !== null; });
    if (!map) {
      map = L.map(box, { scrollWheelZoom: false });
      L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxZoom: 19,
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
      }).addTo(map);
      pinLayer = L.layerGroup().addTo(map);
    }
    pinLayer.clearLayers();
    // Give the map its view first: Leaflet only creates marker elements on a map that has one.
    if (pinned.length) map.fitBounds(pinned.map(function (e) { return [e.lat, e.lon]; }), { padding: [20, 20], maxZoom: 15 });
    else map.setView([38.45, -122.6], 9);
    map.whenReady(function () {
      pinned.forEach(function (e) {
        var s = seriesOf(e.promotion_id);
        var mk = L.marker([e.lat, e.lon], { icon: pinIcon(s), keyboard: true, title: e.name + ", " + e.town });
        mk.on("click", function () { openDialog(e.id, mk.getElement()); });
        mk.addTo(pinLayer);
        var node = mk.getElement();
        if (!node) return;
        node.setAttribute("aria-label", e.name + ", " + e.town);
        node.addEventListener("keydown", function (ev) {
          if (ev.key === "Enter" || ev.key === " " || ev.key === "Spacebar") {
            ev.preventDefault();
            mk.fire("click");
          }
        });
      });
    });
  }

  // ---- Dialog
  function openDialog(id, returnTo) {
    var dlg = document.getElementById("entry-dialog");
    var e = null;
    data.entries.forEach(function (x) { if (x.id === id) e = x; });
    if (!e || !dlg || dlg.open) return;
    lastFocus = returnTo || document.activeElement;
    document.getElementById("dlg-title").textContent = e.name;
    var body = document.getElementById("dlg-body");
    body.textContent = "";
    body.appendChild(fullEntry(e, promosById[e.promotion_id], "div", readFilters()));
    if (e.address) body.appendChild(el("p", { class: "address" }, e.address));
    if (typeof dlg.showModal === "function") dlg.showModal(); else dlg.setAttribute("open", "");
    document.getElementById("dlg-close").focus();
  }
  function closeDialog() {
    var dlg = document.getElementById("entry-dialog");
    if (typeof dlg.close === "function" && dlg.open) dlg.close(); else dlg.removeAttribute("open");
  }
  function wireDialog() {
    var dlg = document.getElementById("entry-dialog");
    document.getElementById("dlg-close").addEventListener("click", closeDialog);
    dlg.addEventListener("close", function () {
      if (lastFocus && document.contains(lastFocus)) lastFocus.focus();
      lastFocus = null;
    });
    dlg.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape") { ev.preventDefault(); closeDialog(); return; }
      if (ev.key !== "Tab") return;
      var f = dlg.querySelectorAll('a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])');
      if (!f.length) return;
      var first = f[0], last = f[f.length - 1];
      if (ev.shiftKey && document.activeElement === first) { ev.preventDefault(); last.focus(); }
      else if (!ev.shiftKey && document.activeElement === last) { ev.preventDefault(); first.focus(); }
    });
    // Close only on a click on the backdrop, never on the dialog's own area or padding.
    dlg.addEventListener("click", function (ev) {
      if (ev.target !== dlg) return;
      if (ev.clientX === 0 && ev.clientY === 0) return;  // keyboard-generated click
      if (!insideBox(ev.clientX, ev.clientY, dlg.getBoundingClientRect())) closeDialog();
    });
  }

  // ---- Start
  function pageToday() {
    // ?today=YYYY-MM-DD shows the page as of another date (for checking the three states by eye).
    var m = /[?&]today=(\d{4}-\d{2}-\d{2})/.exec(root.location ? root.location.search : "");
    if (m && isDate(m[1])) {
      var note = document.getElementById("as-of");
      if (note) { note.textContent = "Showing the page as of " + fmtDate(m[1], true) + "."; note.hidden = false; }
      return { date: m[1], fixed: true };
    }
    return { date: laToday(new Date()), fixed: false };
  }

  function renderAll() {
    renderSeries(document.getElementById("rm-body"));
    setupFilters();
    renderActive();
  }

  function start() {
    var block = document.getElementById("rm-data");
    try { data = JSON.parse(block.textContent); } catch (err) { return; }  // the static list stays
    data.promotions.forEach(function (p) { promosById[p.id] = p; });
    var t = pageToday();
    today = t.date;
    document.getElementById("filters").hidden = false;
    ["f-promotion", "f-town", "f-price", "f-meal", "f-booking", "f-sort"].forEach(function (id) {
      document.getElementById(id).addEventListener("change", renderActive);
    });
    document.getElementById("filters").addEventListener("submit", function (ev) { ev.preventDefault(); });
    wireDialog();
    var body = document.getElementById("rm-body"), staticHtml = body.innerHTML;
    try {
      renderAll();
      document.documentElement.classList.add("js");
    } catch (err) {
      body.innerHTML = staticHtml;  // keep the static list if the script fails
      document.getElementById("active-box").hidden = true;
      return;
    }
    if (!t.fixed) {
      // A new Los Angeles day can change a state; re-render then (held while the dialog is open).
      setInterval(function () {
        var d = laToday(new Date());
        var dlg = document.getElementById("entry-dialog");
        if (d !== today && !(dlg && dlg.open)) { today = d; renderAll(); }
      }, 60000);
    }
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})(typeof window !== "undefined" ? window : this);
