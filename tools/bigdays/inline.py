#!/usr/bin/env python3
"""Copy data/bigdays/*.json into tools/bigdays/index.html and write tools/bigdays/sources.html.
Spec: tools/BUSY-DAYS.md (v1.2).

Not a build step: run it BY HAND after any edit to data/bigdays/ (and once a week or so, so the
no-JavaScript agenda starts near today), then commit the results. The page is served as committed.

It refuses to inline data that fails tools/bigdays/validate.py (schema, holidays.json out of step with
the rules, or a reduction against the last published copy without --allow-removals).

Regions of index.html rewritten between marker comments:
  bigdays-data    the JSON as <script type="application/json" id="bigdays-data">
  bigdays-agenda  the eight weeks as a plain list, so the page works without JavaScript
sources.html is generated whole.

  python3 tools/bigdays/inline.py                       # validate, then inline (agenda starts today)
  python3 tools/bigdays/inline.py --start 2026-11-01    # agenda from a given date
  python3 tools/bigdays/inline.py --check               # exit 1 if either page is out of date (no writes)
  python3 tools/bigdays/inline.py --allow-removals      # a confirmed removal or cancellation
"""
import argparse
import html
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import bigdays  # noqa: E402
import validate  # noqa: E402

PAGE = HERE / "index.html"
SOURCES = HERE / "sources.html"
COUNTY_NAMES = {"napa": "Napa", "sonoma": "Sonoma", "both": "Napa and Sonoma"}
STATUS_WORDS = {"confirmed": "confirmed", "tentative": "tentative", "canceled": "canceled (hidden)"}


def esc(s):
    return html.escape(str(s), quote=True)


def payload(data):
    return {"events": data["events"], "school_breaks": data["school_breaks"],
            "holidays": data["holidays"], "weather_points": data["weather_points"]}


def json_block(data):
    text = json.dumps(payload(data), ensure_ascii=False, separators=(",", ":"))
    text = text.replace("</", "<\\/")
    return '<script type="application/json" id="bigdays-data">%s</script>' % text


def agenda_tags(iso, data):
    """Tags for the no-JavaScript list: both counties, each event marked with its county."""
    tags = []
    for e in data["events"]["events"]:
        if e["status"] == "canceled" or not bigdays.in_range(iso, e.get("start"), e.get("end")):
            continue
        text = "%s (%s%s)" % (e["name"], COUNTY_NAMES[e["county"]], ", tentative" if e["status"] == "tentative" else "")
        tags.append({"type": "event", "text": text})
    rest = [t for t in bigdays.day_tags(iso, data, "napa") if t["type"] != "event"]
    if tags:
        rest = [t for t in rest if t["type"] != "quiet"]
    return tags + rest


def agenda_html(data, start):
    out = ['<section id="agenda" class="agenda nojs-only" aria-labelledby="agenda-h" data-start="%s">' % start,
           '  <h2 id="agenda-h">%s to %s</h2>' % (esc(bigdays.fmt_date(start, True)),
                                                    esc(bigdays.fmt_date(bigdays.add_days(start, bigdays.WINDOW_DAYS - 1), True))),
           '  <p class="agenda-note">Weather needs JavaScript.</p>',
           '  <ol class="agenda-days">']
    for iso in bigdays.window(start):
        out.append('    <li><h3>%s</h3><ul class="tags">%s</ul></li>' % (
            esc(bigdays.fmt_date(iso, True, True)),
            "".join('<li class="tag tag--%s">%s</li>' % (t["type"], esc(t["text"])) for t in agenda_tags(iso, data))))
    out.append("  </ol>")
    out.append('  <p class="change-note">Events subject to change. Confirm with the venue before you plan around one.</p>')
    out.append("</section>")
    return "\n".join(out)


def replace_region(page, name, body):
    start, end = "<!-- %s:start -->" % name, "<!-- %s:end -->" % name
    a, b = page.find(start), page.find(end)
    if a < 0 or b < a:
        raise SystemExit("index.html is missing the %s markers" % name)
    return page[:a + len(start)] + "\n" + body + "\n" + page[b:]


def stored_start(page):
    m = re.search(r'id="agenda"[^>]*data-start="(\d{4}-\d{2}-\d{2})"', page)
    return m.group(1) if m else None


def render_page(page, data, start):
    page = replace_region(page, "bigdays-data", json_block(data))
    page = replace_region(page, "bigdays-agenda", agenda_html(data, start))
    return page


def link(url, text=None):
    return '<a href="%s" rel="noopener">%s</a>' % (esc(url), esc(text or url))


def dates_text(e):
    if e.get("start") is None:
        return "dates not published"
    if e["start"] == e["end"]:
        return bigdays.fmt_date(e["start"], True)
    return "%s to %s" % (bigdays.fmt_date(e["start"], True), bigdays.fmt_date(e["end"], True))


def sources_html(data):
    ev, sb, hol, wp = data["events"], data["school_breaks"], data["holidays"], data["weather_points"]
    rows = []
    for e in sorted(ev["events"], key=lambda e: (e.get("start") is None, e.get("start") or "", e["name"])):
        note = ('<br><span class="note">%s</span>' % esc(e["note"])) if e.get("note") else ""
        rows.append("<tr><td>%s%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" % (
            esc(e["name"]), note, esc(COUNTY_NAMES[e["county"]]), esc(dates_text(e)), esc(STATUS_WORDS[e["status"]]),
            link(e["source_url"]), esc(bigdays.fmt_date(e["last_checked"], True))))
    hol_items = "".join("<li>%s (%s)</li>" % (esc(name), esc(describe_rule(rule)))
                        for name, kind, rule in bigdays.HOLIDAY_RULES)
    hol_list = "".join("<li>%s: %s%s</li>" % (
        esc(bigdays.fmt_date(h["date"], True)), esc(h["name"]),
        esc(", day off %s" % bigdays.fmt_date(h["observed"], True)) if h["observed"] != h["date"] else "")
        for h in hol["holidays"])
    dist = []
    for x in sb["districts"]:
        br = "".join("<li>%s: %s to %s (district calls it %s)</li>" % (
            esc(b["tag"]), esc(bigdays.fmt_date(b["start"], True)), esc(bigdays.fmt_date(b["end"], True)),
            esc(b["district_label"])) for b in x["breaks"])
        dist.append("<li><h3>%s (%s)</h3><p>%s, school year %s. Checked %s.</p><ul>%s</ul></li>" % (
            esc(x["name"]), esc(x["id"]), link(x["source_url"], x["calendar_title"]), esc(sb["meta"]["school_year"]),
            esc(bigdays.fmt_date(x["last_checked"], True)), br))
    pts = "".join("<li>%s: %s, %.4f, %.4f</li>" % (esc(p["label"]), esc(p["place"]), p["lat"], p["lon"])
                  for p in wp["points"].values())
    return """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Big days calendar sources</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,500;9..144,700&amp;family=Source+Sans+3:wght@400;600;700&amp;family=JetBrains+Mono:wght@400;500&amp;display=swap">
<link rel="icon" href="data:,">
<link rel="stylesheet" href="bigdays.css">
</head>
<body>
<!-- Generated by tools/bigdays/inline.py from data/bigdays/. Do not edit by hand. -->
<div class="wrap">
<header class="top">
<p class="back"><a href="index.html">Back to the calendar</a></p>
<h1>Sources</h1>
</header>
<main class="sources">
<h2>Events</h2>
<p>Event calendars checked through %(vt)s. Last reviewed %(lr)s.</p>
<p>%(evnote)s</p>
<div class="table-wrap"><table>
<thead><tr><th scope="col">Event</th><th scope="col">County</th><th scope="col">Dates</th><th scope="col">Status</th><th scope="col">Source</th><th scope="col">Checked</th></tr></thead>
<tbody>
%(rows)s
</tbody>
</table></div>
<h2>Upkeep</h2>
<div class="table-wrap"><table>
<thead><tr><th scope="col">Task</th><th scope="col">What it does</th><th scope="col">Last run</th></tr></thead>
<tbody>
<tr><td>sweep-events</td><td>Monthly by hand: re-reads every event source, the district calendars, and the Restaurant Month organizer pages; updates dates; adds new events as tentative.</td><td>%(sweep)s</td></tr>
</tbody>
</table></div>
<h2>Holidays</h2>
<p>%(counts)s</p>
<ul>%(hol_items)s</ul>
<p>%(observed)s</p>
<p>%(three)s</p>
<p>Listed for %(years)s:</p>
<ul>%(hol_list)s</ul>
<h2>School breaks</h2>
<p>%(sbnote)s</p>
<ul class="districts">%(dist)s</ul>
<h2>Harvest</h2>
<p>August 15 to October 31, every year.</p>
<h2>Weather</h2>
<p>%(wsource)s. One point per county; the word for a day comes from its daytime period only: rain if it mentions rain, showers, thunderstorms, or drizzle; else heat if the high is %(heat)s&deg;F or above; else fog if it says fog, or cloudy if it says cloudy or overcast; else clear. Seven days at most.</p>
<ul>%(pts)s</ul>
</main>
</div>
</body>
</html>
""" % {
        "vt": esc(bigdays.fmt_date(ev["meta"]["verified_through"], True)),
        "sweep": esc(bigdays.fmt_date(ev["meta"]["last_sweep"], True)) if ev["meta"].get("last_sweep") else "not yet run",
        "lr": esc(bigdays.fmt_date(ev["meta"]["last_reviewed"], True)),
        "rows": "\n".join(rows), "counts": esc(hol["meta"]["counts"]), "hol_items": hol_items,
        "observed": esc(hol["meta"]["observed_rule"]), "three": esc(hol["meta"]["three_day_weekend_rule"]),
        "years": esc(" and ".join(str(y) for y in hol["meta"]["years"])), "hol_list": hol_list,
        "dist": "".join(dist), "sbnote": esc(sb["meta"].get("note") or ""), "evnote": esc(ev["meta"].get("note") or ""), "wsource": esc(wp["source"]), "heat": esc(wp["heat_threshold_f"]), "pts": pts,
    }


ORD = {1: "first", 2: "second", 3: "third", 4: "fourth", -1: "last"}


def describe_rule(rule):
    if rule[0] == "fixed":
        return "%s %d" % (bigdays.MONTHS[rule[1] - 1], rule[2])
    if rule[0] == "nth_after":
        _, month, wd, n, days = rule
        return "the day after the %s %s in %s" % (ORD[n].lower(), bigdays.WEEKDAYS[wd], bigdays.MONTHS[month - 1]) if days == 1 \
            else "%d days after the %s %s in %s" % (days, ORD[n].lower(), bigdays.WEEKDAYS[wd], bigdays.MONTHS[month - 1])
    _, month, wd, n = rule
    return "%s %s in %s" % (ORD[n], bigdays.WEEKDAYS[wd], bigdays.MONTHS[month - 1])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--allow-removals", action="store_true")
    ap.add_argument("--baseline")
    ap.add_argument("--start", help="first day of the no-JavaScript agenda (default: today in Los Angeles)")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args(argv)
    ok, data = validate.check_files(a.allow_removals, a.baseline)
    if not ok:
        print("refusing to inline: fix data/bigdays first", file=sys.stderr)
        return 1
    page = PAGE.read_text()
    start = a.start or (stored_start(page) if a.check else None) or bigdays.today_la()
    new_page = render_page(page, data, start)
    new_sources = sources_html(data)
    old_sources = SOURCES.read_text() if SOURCES.exists() else ""
    if a.check:
        bad = [p.name for p, new, old in ((PAGE, new_page, page), (SOURCES, new_sources, old_sources)) if new != old]
        if bad:
            print("%s out of date; run python3 tools/bigdays/inline.py" % " and ".join(bad), file=sys.stderr)
            return 1
        if start < bigdays.add_days(bigdays.today_la(), -7):
            print("note: the no-JavaScript agenda starts %s, over a week ago; run python3 tools/bigdays/inline.py to move it" % start, file=sys.stderr)
        print("index.html and sources.html match data/bigdays")
        return 0
    PAGE.write_text(new_page)
    SOURCES.write_text(new_sources)
    print("inlined %d events, %d districts; agenda from %s" % (len(data["events"]["events"]), len(data["school_breaks"]["districts"]), start))
    return 0


if __name__ == "__main__":
    sys.exit(main())
