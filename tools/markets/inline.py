#!/usr/bin/env python3
"""Copy data/markets.json into tools/markets/index.html. Spec: tools/MARKETS.md (v1.2).

Not a build step: run it BY HAND after any edit to data/markets.json, then commit both files. The page
is served exactly as committed, so a first-time visitor never depends on a runtime fetch.

It refuses to inline a file that fails tools/markets/validate.py (schema, or fewer markets than the
last published copy without --allow-removals). It is deterministic: the same JSON gives the same page.

Three regions of index.html are rewritten, each between marker comments:
  markets-data    the JSON as <script type="application/json" id="markets-data">
  markets-list    the plain list of markets, so the page works without JavaScript
  markets-footer  the source line, with the CDFA as-of date and the checked date

  python3 tools/markets/inline.py                    # validate, then inline
  python3 tools/markets/inline.py --check            # exit 1 if the page is out of date (no writes)
  python3 tools/markets/inline.py --allow-removals   # a confirmed closure removed a market
"""
import argparse
import html
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import schedule  # noqa: E402
import validate  # noqa: E402

PAGE = HERE / "index.html"
DATA = validate.DATA
REGIONS = ("markets-data", "markets-list", "markets-footer")


def esc(s):
    return html.escape(s, quote=True)


def json_block(data):
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=False)
    # "</" can never appear inside the script element; JSON allows "\/" for "/".
    text = text.replace("</", "<\\/")
    return '<script type="application/json" id="markets-data">%s</script>' % text


def list_html(data):
    out = ['<ul id="market-list" class="market-list">']
    for m in data["markets"]:
        out.append('  <li class="market" id="m-%s" data-id="%s">' % (esc(m["id"]), esc(m["id"])))
        out.append("    <h3>%s</h3>" % esc(m["name"]))
        out.append('    <p class="market-where">%s (%s County)</p>' % (esc(m["address"]), esc(m["county"])))
        out.append('    <ul class="sessions">')
        for s in m["sessions"]:
            out.append("      <li>%s</li>" % esc(schedule.describe_session(s, m["schedule_valid_through"], m["season_year_inferred"])))
        out.append("    </ul>")
        links = []
        if m["website"]:
            links.append('<a href="%s" rel="noopener">Market website</a>' % esc(m["website"]))
        if m["instagram"]:
            links.append('<a href="https://www.instagram.com/%s/" rel="noopener">@%s on Instagram</a>'
                         % (esc(m["instagram"]), esc(m["instagram"])))
        if links:
            out.append('    <p class="links">%s</p>' % " · ".join(links))
        if m["notes"]:
            out.append('    <p class="notes">%s</p>' % esc(m["notes"]))
        out.append("  </li>")
    out.append("</ul>")
    return "\n".join(out)


def footer_html(data):
    src = data["source"]
    return ('<p class="source">Source: <a href="%s">CDFA certified farmers\' market list</a>, %s, checked %s. '
            'Hours change; the market\'s own site wins.</p>\n'
            '<p class="credit">Map data &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors.</p>'
            % (esc(src["url"]), schedule.fmt_date(src["as_of"], True), schedule.fmt_date(src["checked"], True)))


def replace_region(page, name, body):
    start, end = "<!-- %s:start -->" % name, "<!-- %s:end -->" % name
    a, b = page.find(start), page.find(end)
    if a < 0 or b < a:
        raise SystemExit("index.html is missing the %s markers" % name)
    return page[:a + len(start)] + "\n" + body + "\n" + page[b:]


def render(page, data):
    page = replace_region(page, "markets-data", json_block(data))
    page = replace_region(page, "markets-list", list_html(data))
    page = replace_region(page, "markets-footer", footer_html(data))
    return page


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--allow-removals", action="store_true")
    ap.add_argument("--baseline", help="compare against this file instead of git HEAD or the inlined copy")
    ap.add_argument("--check", action="store_true", help="exit 1 if index.html does not match the JSON")
    a = ap.parse_args(argv)
    ok, data = validate.check_file(DATA, a.allow_removals, a.baseline)
    if not ok:
        print("refusing to inline: fix data/markets.json first", file=sys.stderr)
        return 1
    page = PAGE.read_text()
    new = render(page, data)
    if a.check:
        if new != page:
            print("index.html is out of date; run python3 tools/markets/inline.py", file=sys.stderr)
            return 1
        print("index.html matches data/markets.json")
        return 0
    if new != page:
        PAGE.write_text(new)
        print("inlined %d markets into %s" % (len(data["markets"]), PAGE.relative_to(validate.REPO)))
    else:
        print("index.html already up to date")
    return 0


if __name__ == "__main__":
    sys.exit(main())
