#!/usr/bin/env python3
"""Copy data/restaurant-month.json into tools/restaurant-month/index.html. Spec: tools/RESTAURANT-MONTH.md.

Not a build step: run it BY HAND after any edit to data/restaurant-month.json, then commit both files.
The page is served exactly as committed. Same pattern as tools/markets/inline.py.

It refuses to inline a file that fails tools/restaurant-month/validate.py (schema, or a reduction
against the last published copy without --allow-removals). Deterministic for a given JSON and date.

Three regions of index.html are rewritten, each between marker comments:
  rm-body    the static page for the current state (each promotion's header and list, and the offers
             list when a promotion is active), so the page works without JavaScript. It is rendered
             for one Los Angeles date, recorded in the region; with JavaScript the page re-renders for
             the visitor's day.
  rm-footer  the footer line from the spec, with meta.last_reviewed, and the map credit
  rm-data    the JSON as <script type="application/json" id="rm-data">

  python3 tools/restaurant-month/inline.py                    # validate, then inline for today
  python3 tools/restaurant-month/inline.py --check            # exit 1 if the page is out of date
  python3 tools/restaurant-month/inline.py --allow-removals   # a confirmed withdrawal removed entries
  python3 tools/restaurant-month/inline.py --today 2027-01-01 # render the no-JS page for another date

Re-run it when a promotion changes state (its start, the day after its end) so visitors without
JavaScript see the right state; --check reports when the rendered state is no longer today's.
"""
import argparse
import html
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import rules  # noqa: E402
import validate  # noqa: E402

PAGE = HERE / "index.html"
DATA = validate.DATA
RENDERED_RE = re.compile(r"<!-- rendered for (\d{4}-\d{2}-\d{2}) -->")


def esc(s):
    return html.escape(s, quote=True)


def json_block(data):
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=False)
    # Every "<" becomes \u003c, so nothing in the data can open or close a tag inside the script.
    text = text.replace("<", "\\u003c")
    return '<script type="application/json" id="rm-data">%s</script>' % text


def _link(href, text, cls=None, label=None):
    ok = rules.safe_url(href)
    if not ok:
        return None
    attrs = ' href="%s" rel="noopener"' % esc(ok)
    if cls:
        attrs += ' class="%s"' % cls
    if label:
        attrs += ' aria-label="%s"' % esc(label)
    return "<a%s>%s</a>" % (attrs, esc(text))


def _by_name(entries):
    return sorted(entries, key=lambda e: (e["name"].lower(), e["id"]))


def full_entry(e, promo, today, ind="  "):
    out = ['%s<li class="entry" data-id="%s">' % (ind, esc(e["id"])),
           "%s  <h3>%s</h3>" % (ind, esc(e["name"])),
           '%s  <p class="where">%s</p>' % (ind, esc(e["town"] + (" · " + e["cuisine"] if e["cuisine"] else "")))]
    if e["offers"]:
        out.append('%s  <ul class="offers">' % ind)
        for o in e["offers"]:
            current = rules.offer_current(o, promo, today)
            cls = "" if current else ' class="ended"'
            when = rules.describe_offer_when(o, promo)
            meal = '<span class="meal">%s</span>' % esc(rules.MEAL_LABELS[o["meal"]]) if rules.offer_label_shown(o) else ""
            out.append('%s    <li%s><span class="price">%s</span>%s<span class="desc">%s</span>%s%s</li>'
                       % (ind, cls, esc(rules.fmt_price(o["price"]) or rules.BUCKET_LABELS["none"]),
                          meal, esc(o["description"]),
                          '<span class="when">%s</span>' % esc(when) if when else "",
                          "" if current else '<span class="visually-hidden"> %s</span>' % rules.ENDED_TEXT))
        out.append("%s  </ul>" % ind)
    links = [_link(e["booking_url"], "Book", "book", "Book %s (the restaurant's booking page)" % e["name"]),
             _link(e["website"], "Website"),
             '<a href="tel:%s">%s</a>' % (esc(re.sub(r"[^0-9+]", "", e["phone"])), esc(e["phone"])) if e["phone"] else None]
    links = [x for x in links if x]
    if links:
        out.append('%s  <p class="links">%s</p>' % (ind, " · ".join(links)))
    if rules.display_state(promo, today) == "active" and rules.is_stale(e["last_checked"], today):
        out.append('%s  <p class="stale">%s</p>' % (ind, esc(rules.stale_text(e["last_checked"]))))
    out.append("%s</li>" % ind)
    return out


def reference_entry(e, ind="  "):
    out = ['%s<li class="entry entry--ref">' % ind, "%s  <h3>%s</h3>" % (ind, esc(e["name"])),
           '%s  <p class="where">%s</p>' % (ind, esc(e["town"]))]
    w = _link(e["website"], "Website")
    if w:
        out.append('%s  <p class="links">%s</p>' % (ind, w))
    out.append("%s</li>" % ind)
    return out


def body_html(data, today):
    promos = {p["id"]: p for p in data["promotions"]}
    out = ["<!-- rendered for %s -->" % today]
    for s in rules.SERIES:
        v = rules.series_view(data["promotions"], s, today)
        if not v:
            continue
        cur = v["current"]
        out.append('<section class="series series--%s" id="s-%s" aria-labelledby="s-%s-h">' % (v["state"], s, s))
        out.append('<h2 id="s-%s-h">%s</h2>' % (s, esc(cur["name"])))
        if v["state"] in ("upcoming", "active"):
            out.append('<p class="dates">%s</p>' % esc(rules.fmt_range(cur["start"], cur["end"])))
        if v["state"] == "active" and not any(e["promotion_id"] == cur["id"] for e in data["entries"]):
            org = _link(cur["source_url"], "Organizer's page")
            out.append('<p class="empty">No participants listed yet. %s</p>' % (org or ""))
        if v["state"] == "upcoming":
            # No countdown here: a static count would be wrong the next day. The script adds it.
            out.append('<p class="label">%s</p>' % esc(rules.confirmed_label(cur)))
            mine = _by_name([e for e in data["entries"] if e["promotion_id"] == cur["id"]])
            if mine:
                out.append('<ul class="entries">')
                for e in mine:
                    out += full_entry(e, cur, today)
                out.append("</ul>")
            else:
                out.append('<p class="empty">No participants confirmed for %d yet.</p>' % rules.year_of(cur["id"]))
        if v["state"] == "archived":
            out.append('<p class="banner">%s</p>' % esc(rules.banner_text(v)))
            ref = v["reference"]
            refs = _by_name([e for e in data["entries"] if ref and e["promotion_id"] == ref["id"]])
            if ref:
                out.append('<p class="label">%s</p>' % esc(rules.reference_label(ref)))
            if refs:
                out.append('<ul class="entries entries--ref">')
                for e in refs:
                    out += reference_entry(e)
                out.append("</ul>")
            else:
                out.append('<p class="empty">Participant list not yet collected.</p>')
        out.append("</section>")
    active = [e for e in data["entries"] if rules.display_state(promos[e["promotion_id"]], today) == "active"]
    if active:
        out.append('<section class="active-static" aria-labelledby="active-static-h">')
        out.append('<h2 id="active-static-h">Offers</h2>')
        out.append('<ul class="entries">')
        for e in rules.sort_entries(active, "town", promos, {}, today):
            out += full_entry(e, promos[e["promotion_id"]], today)
        out.append("</ul>")
        out.append("</section>")
    return "\n".join(out)


def footer_html(data):
    srcs = {}
    for p in sorted(data["promotions"], key=lambda p: rules.year_of(p["id"])):
        srcs[rules.series_of(p["id"])] = p["source_url"]   # the latest season's organizer page wins
    napa = '<a href="%s">Visit Napa Valley</a>' % esc(srcs.get("napa", "https://www.visitnapavalley.com/restaurant-month/"))
    sonoma = '<a href="%s">Sonoma County Restaurant Week</a>' % esc(srcs.get("sonoma", "https://socorestaurantweek.org/"))
    napa_note = "" if any(rules.series_of(e["promotion_id"]) == "napa" for e in data["entries"]) \
        else " Napa participants are not listed yet."
    return ('<p class="source">Collected from %s and %s pages, read by script and spot-checked, checked %s.%s '
            "We aim to list every participant and cannot guarantee it. Offers change; the restaurant's page wins.</p>\n"
            '<p class="credit">Map data &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors.</p>'
            % (napa, sonoma, rules.fmt_date(data["meta"]["last_reviewed"], True), napa_note))


def replace_region(page, name, body):
    start, end = "<!-- %s:start -->" % name, "<!-- %s:end -->" % name
    a, b = page.find(start), page.find(end)
    if a < 0 or b < a:
        raise SystemExit("index.html is missing the %s markers" % name)
    return page[:a + len(start)] + "\n" + body + "\n" + page[b:]


def rendered_for(page):
    m = RENDERED_RE.search(page)
    return m.group(1) if m else None


def render(page, data, today):
    page = replace_region(page, "rm-body", body_html(data, today))
    page = replace_region(page, "rm-footer", footer_html(data))
    page = replace_region(page, "rm-data", json_block(data))
    return page


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--allow-removals", action="store_true")
    ap.add_argument("--baseline", help="compare against this file instead of the inlined copy or git HEAD")
    ap.add_argument("--check", action="store_true", help="exit 1 if index.html does not match the JSON")
    ap.add_argument("--today", help="render for this Los Angeles date (YYYY-MM-DD) instead of today")
    a = ap.parse_args(argv)
    ok, data = validate.check_file(DATA, a.allow_removals, a.baseline, page=PAGE)
    if not ok:
        print("refusing to inline: fix data/restaurant-month.json first", file=sys.stderr)
        return 1
    page = PAGE.read_text()
    today = a.today or rules.la_today()
    if a.check:
        was = rendered_for(page)
        if not was or render(page, data, was) != page:
            print("index.html is out of date; run python3 tools/restaurant-month/inline.py", file=sys.stderr)
            return 1
        if body_html(data, was).split("\n", 1)[1] != body_html(data, today).split("\n", 1)[1]:
            print("index.html matches the JSON, but its no-JS page was rendered for %s and today's differs; "
                  "run python3 tools/restaurant-month/inline.py" % was, file=sys.stderr)
            return 1
        print("index.html matches data/restaurant-month.json (rendered for %s)" % was)
        return 0
    new = render(page, data, today)
    if new != page:
        PAGE.write_text(new)
        print("inlined %d entries into %s (rendered for %s)" % (len(data["entries"]), PAGE.relative_to(validate.REPO), today))
    else:
        print("index.html already up to date")
    return 0


if __name__ == "__main__":
    sys.exit(main())
