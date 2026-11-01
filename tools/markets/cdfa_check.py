#!/usr/bin/env python3
"""Weekly check of the CDFA certified farmers' market list. Spec: tools/MARKETS.md (v1.2).

Reads only; never edits data/markets.json or the page, never commits. It downloads the CDFA PDF,
records its checksum and "as of" date, extracts text with pypdf, counts rows for the five counties, and
diffs those counties' sections against tools/markets/cdfa-snapshot.txt (ignoring the "as of" line and
whitespace). The workflow (.github/workflows/markets.yml) opens or reuses ONE issue when this reports a
problem: PDF missing, checksum changed but extraction failed, a county with zero rows, or changed content.
Extraction succeeding and verification succeeding are logged separately.

  python3 tools/markets/cdfa_check.py                         # download and check
  python3 tools/markets/cdfa_check.py --pdf FILE              # check a local copy
  python3 tools/markets/cdfa_check.py --pdf FILE --write-snapshot   # by hand, after the JSON is updated

Python 3.9+, stdlib plus pypdf (this job only). No LLM.
Outputs: --report FILE (JSON), --issue-body FILE (markdown), and GITHUB_OUTPUT problem=true|false.
"""
import argparse
import difflib
import hashlib
import json
import os
import re
import sys
import tempfile
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
SNAPSHOT = HERE / "cdfa-snapshot.txt"
DATA = HERE.parents[1] / "data" / "markets.json"
URL = "https://www.cdfa.ca.gov/is/docs/CurrentMrktsCounty.pdf"
COUNTIES = ("Napa", "Sonoma", "Marin", "Mendocino", "Lake")
USER_AGENT = "farmers-market-map weekly check (static site; one request a week)"

AS_OF_RE = re.compile(r"as of\s+([A-Z][a-z]+ \d{1,2}, \d{4})")
# In pypdf's layout text a row's county sits in the first column followed by a wide gap; continuation
# lines start with spaces. "Sonoma Valley Certified" (one space) is a market name, not a county cell.
LABEL_RE = re.compile(r"^([A-Z][A-Za-z.' ]*?[A-Za-z.])\s{2,}\S")
HEADER_LINES = (re.compile(r"^Certified Farmers' Markets by County$"), re.compile(r"^as of "),
                re.compile(r"^County$"), re.compile(r"^Name Market Name Market Location"))


# ---------------------------------------------------------------- text

def extract_layout(pdf_path):
    """Layout-mode text of every page, joined. Raises on any extraction failure."""
    import pypdf  # imported here so the offline tests never need it
    reader = pypdf.PdfReader(str(pdf_path))
    pages = [p.extract_text(extraction_mode="layout") or "" for p in reader.pages]
    text = "\n".join(pages)
    if not text.strip():
        raise ValueError("pypdf returned no text")
    return text


def as_of(text):
    m = AS_OF_RE.search(text)
    return m.group(1) if m else None


def as_of_iso(s):
    """"October 1, 2026" -> "2026-10-01", or None."""
    import datetime as dt
    try:
        return dt.datetime.strptime(s, "%B %d, %Y").date().isoformat()
    except (TypeError, ValueError):
        return None


def data_as_of(path=DATA):
    """source.as_of from data/markets.json, or None."""
    try:
        return json.loads(Path(path).read_text())["source"]["as_of"]
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _norm(line):
    return " ".join(line.split())


def _is_header(line):
    n = _norm(line)
    return any(r.match(n) for r in HEADER_LINES)


def clean_lines(text):
    """Lines with page headers dropped (the "as of" line is one) and blank lines removed."""
    return [ln.rstrip() for ln in text.splitlines() if ln.strip() and not _is_header(ln)]


def label(line):
    m = LABEL_RE.match(line)
    return m.group(1) if m else None


def count_rows(text):
    """{county: rows} for the five counties."""
    counts = {c: 0 for c in COUNTIES}
    for ln in clean_lines(text):
        c = label(ln)
        if c in counts:
            counts[c] += 1
    return counts


def sections(text):
    """{county: [normalized lines]}: from just after the last row of the county before it to just
    before the first row of the county after it, so wrapped lines above and below a row stay in."""
    lines = clean_lines(text)
    labels = [(i, label(ln)) for i, ln in enumerate(lines)]
    labels = [(i, c) for i, c in labels if c]
    out = {}
    for county in COUNTIES:
        idx = [k for k, (_, c) in enumerate(labels) if c == county]
        if not idx:
            out[county] = []
            continue
        first, last = idx[0], idx[-1]
        lo = labels[first - 1][0] + 1 if first > 0 else 0
        hi = labels[last + 1][0] if last + 1 < len(labels) else len(lines)
        out[county] = [_norm(x) for x in lines[lo:hi]]
    return out


# ---------------------------------------------------------------- snapshot

def render_snapshot(sha, asof, counts, secs):
    head = ["# CDFA certified farmers' market list: the five counties, as extracted by cdfa_check.py.",
            "# Written by hand with --write-snapshot after data/markets.json is brought up to date.",
            "# sha256: %s" % sha,
            "# as_of: %s" % asof,
            "# counts: %s" % " ".join("%s=%d" % (c, counts[c]) for c in COUNTIES)]
    body = []
    for c in COUNTIES:
        body.append("## %s" % c)
        body.extend(secs[c])
    return "\n".join(head + body) + "\n"


def read_snapshot(path=SNAPSHOT):
    """(meta, {county: [lines]}) or (None, None) when the file is missing."""
    p = Path(path)
    if not p.exists():
        return None, None
    meta, secs, cur = {}, {}, None
    for ln in p.read_text().splitlines():
        if ln.startswith("## "):
            cur = ln[3:].strip()
            secs[cur] = []
        elif ln.startswith("# "):
            k, _, v = ln[2:].partition(":")
            if k in ("sha256", "as_of", "counts"):
                meta[k] = v.strip()
        elif cur is not None and ln.strip():
            secs[cur].append(_norm(ln))
    return meta, secs


def diff_sections(old, new):
    """{county: unified diff text} for counties whose lines differ (whitespace already normalized)."""
    out = {}
    for c in COUNTIES:
        a, b = old.get(c, []), new.get(c, [])
        if a != b:
            out[c] = "\n".join(difflib.unified_diff(a, b, "snapshot", "current", lineterm="", n=1))
    return out


# ---------------------------------------------------------------- check

def check(pdf_bytes, snapshot_path=SNAPSHOT, extract=extract_layout, tmp_dir=None, published_as_of="data"):
    """The whole decision, as a report dict. `extract` takes a path to the PDF file. published_as_of is
    the as-of date data/markets.json was built from ("data" reads it from the file; None skips)."""
    if published_as_of == "data":
        published_as_of = data_as_of()
    meta, snap = read_snapshot(snapshot_path)
    r = {"problems": [], "pdf_ok": False, "sha256": None, "previous_sha256": (meta or {}).get("sha256"),
         "checksum_changed": None, "as_of": None, "previous_as_of": (meta or {}).get("as_of"),
         "data_as_of": published_as_of, "counts": None, "extraction_ok": False, "verification_ok": False, "diff": {}}
    if not pdf_bytes or not pdf_bytes.startswith(b"%PDF"):
        r["problems"].append("The CDFA PDF is missing or is not a PDF (%s)." % URL)
        return r
    r["pdf_ok"] = True
    r["sha256"] = hashlib.sha256(pdf_bytes).hexdigest()
    r["checksum_changed"] = r["previous_sha256"] is not None and r["sha256"] != r["previous_sha256"]
    tmp = Path(tmp_dir or os.environ.get("RUNNER_TEMP") or tempfile.gettempdir()) / "cdfa-current.pdf"
    tmp.write_bytes(pdf_bytes)
    try:
        text = extract(tmp)
        r["extraction_ok"] = True
    except Exception as e:  # any pypdf failure counts
        r["extraction_error"] = "%s: %s" % (type(e).__name__, e)
        if r["checksum_changed"] or snap is None:
            r["problems"].append("The PDF changed (sha256 %s) but text extraction failed: %s"
                                 % (r["sha256"][:12], r["extraction_error"]))
        return r
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass
    r["as_of"] = as_of(text)
    if r["as_of"] is None:
        r["problems"].append("No \"as of\" date found in the PDF text.")
    elif published_as_of and as_of_iso(r["as_of"]) != published_as_of:
        r["problems"].append("CDFA list is now dated %s; data/markets.json was built from the %s list."
                             % (r["as_of"], published_as_of))
    r["counts"] = count_rows(text)
    secs = sections(text)
    r["_sections"] = secs
    zero = [c for c in COUNTIES if r["counts"][c] == 0]
    if zero:
        r["problems"].append("No rows found for %s. The layout may have changed, or the county was dropped."
                             % ", ".join(zero))
    if snap is None:
        r["problems"].append("No snapshot to compare with (tools/markets/cdfa-snapshot.txt).")
    else:
        r["diff"] = diff_sections(snap, secs)
        if r["diff"]:
            old_counts = dict(kv.split("=") for kv in meta.get("counts", "").split() if "=" in kv)
            changed = ", ".join("%s (%s rows, was %s)" % (c, r["counts"][c], old_counts.get(c, "?")) for c in r["diff"])
            r["problems"].append("CDFA content changed for: %s." % changed)
    r["verification_ok"] = not r["problems"]
    return r


def issue_body(r):
    lines = ["The weekly CDFA check found something to review. Nothing was changed automatically; "
             "data/markets.json and the page stay as they are until updated by hand.", ""]
    lines += ["- %s" % p for p in r["problems"]]
    lines += ["", "| | |", "|---|---|",
              "| PDF downloaded | %s |" % ("yes" if r["pdf_ok"] else "no"),
              "| sha256 | `%s` (was `%s`) |" % (r["sha256"], r["previous_sha256"]),
              "| as of | %s (was %s) |" % (r["as_of"], r["previous_as_of"]),
              "| extraction | %s |" % ("ok" if r["extraction_ok"] else "failed"),
              "| verification | %s |" % ("ok" if r["verification_ok"] else "needs review"),
              "| rows | %s |" % (" ".join("%s=%s" % kv for kv in (r["counts"] or {}).items()) or "none")]
    for c, d in r["diff"].items():
        lines += ["", "<details><summary>%s diff</summary>" % c, "", "```diff", d[:6000], "```", "", "</details>"]
    lines += ["", "To close: update data/markets.json by hand, run `python3 tools/markets/inline.py`, then "
              "`python3 tools/markets/cdfa_check.py --pdf <the PDF> --write-snapshot`, and commit all three.",
              "", "Source: %s" % URL]
    return "\n".join(lines) + "\n"


def download(url=URL, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except Exception as e:
        print("download failed: %s" % e)
        return None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--pdf", help="check this local PDF instead of downloading")
    ap.add_argument("--snapshot", default=str(SNAPSHOT))
    ap.add_argument("--report", help="write the report as JSON here")
    ap.add_argument("--issue-body", help="write the issue text (markdown) here when there is a problem")
    ap.add_argument("--write-snapshot", action="store_true", help="replace the snapshot (by hand only)")
    a = ap.parse_args(argv)

    pdf = Path(a.pdf).read_bytes() if a.pdf else download()
    r = check(pdf, a.snapshot)
    secs = r.pop("_sections", None)

    print("pdf: %s" % ("downloaded" if r["pdf_ok"] else "MISSING"))
    print("sha256: %s (snapshot %s; %s)" % (r["sha256"], r["previous_sha256"],
                                            "no snapshot" if r["previous_sha256"] is None else "changed" if r["checksum_changed"] else "unchanged"))
    print("as of: %s" % r["as_of"])
    print("extraction: %s" % ("ok" if r["extraction_ok"] else "FAILED %s" % r.get("extraction_error", "")))
    print("rows: %s" % r["counts"])
    print("verification: %s" % ("ok" if r["verification_ok"] else "NEEDS REVIEW"))
    for p in r["problems"]:
        print("problem: %s" % p)

    if a.write_snapshot:
        if not (r["extraction_ok"] and secs and all(r["counts"][c] for c in COUNTIES)):
            print("refusing to write a snapshot from a failed or empty extraction", file=sys.stderr)
            return 1
        Path(a.snapshot).write_text(render_snapshot(r["sha256"], r["as_of"], r["counts"], secs))
        print("snapshot written: %s" % a.snapshot)
        return 0

    if a.report:
        Path(a.report).write_text(json.dumps(r, indent=2) + "\n")
    problem = bool(r["problems"])
    if problem and a.issue_body:
        Path(a.issue_body).write_text(issue_body(r))
    gh = os.environ.get("GITHUB_OUTPUT")
    if gh:
        with open(gh, "a") as f:
            f.write("problem=%s\n" % ("true" if problem else "false"))
            f.write("extraction_ok=%s\n" % ("true" if r["extraction_ok"] else "false"))
            f.write("verification_ok=%s\n" % ("true" if r["verification_ok"] else "false"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
