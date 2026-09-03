#!/usr/bin/env python3
"""Render README.md into the printable runbook PDF.

Adds what a printed document needs and markdown has no notion of:
  * a title page,
  * an index carrying real page numbers (resolved by rendering, reading back the
    page each heading landed on, and re-rendering until the numbers stop moving),
  * clickable cross-references, so "Section 4" in the text jumps to Section 4,
  * a page-number footer.

Run:  make runbook      (or: .venv/bin/python scripts/build_runbook_pdf.py)
"""
from __future__ import annotations

import io
import os
import re
import subprocess
import sys
from pathlib import Path

import markdown
from pypdf import PdfReader, PdfWriter
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / ".build"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
DATE = os.environ.get("RUNBOOK_DATE", "30 August 2026")

# Each document: source markdown, output PDF, cover text, and the phrase that
# marks where the body starts (used to skip cover+index when resolving page numbers).
DOCS = {
    "runbook": dict(
        src=ROOT / "README.md",
        out=ROOT.parent / "GauTrack_Deployment_Runbook.pdf",
        title="GauTrack Deployment and Operations Runbook",
        subtitles=["Rewari district stray-cattle registry",
                   "Field app, dashboard, and the database behind them"],
        note=("Written to be read by people who do not write software. Technical terms are "
              "used properly, because they recur in every vendor conversation and every "
              "audit, and each is explained where it first appears."),
        body_marker="GauTrack is one program",
    ),
    "architecture": dict(
        src=ROOT / "ARCHITECTURE.md",
        out=ROOT.parent / "GauTrack_Technical_Architecture.pdf",
        title="GauTrack Technical Architecture",
        subtitles=["Rewari district stray-cattle registry",
                   "How the system works, and why it was built this way"],
        note=("For an engineer or IT officer who has to review, extend, audit or take over "
              "this system. Assumes Python and SQL. Each section names the techniques it "
              "relies on, so unfamiliar ground is easy to spot."),
        body_marker="One Python process serving four surfaces",
    ),
    "questions": dict(
        src=ROOT / "QUESTIONS.md",
        out=ROOT.parent / "GauTrack_Questions_for_the_Department.pdf",
        title="GauTrack: Questions for the Department",
        subtitles=["Rewari district stray-cattle registry",
                   "What the office must arrange, and what only the department can answer"],
        note=("Companion to the screen-by-screen document. Nothing here is a software "
              "question. Each item says why it is being asked, what has already been "
              "established, and who can settle it."),
        body_marker="Errands, not decisions",
        toc=False,
    ),
}
SRC = DOCS["runbook"]["src"]      # rebound in main()
OUT = DOCS["runbook"]["out"]
TITLE = DOCS["runbook"]["title"]

CSS = """
@page { size: A4; margin: 17mm 15mm 18mm 15mm; }
body { font-family: Georgia, "Times New Roman", serif; font-size: 10.4pt; line-height: 1.42; color: #000; }
h1 { font-size: 15pt; }
h2 { font-size: 12.5pt; margin-top: 17pt; border-bottom: 1px solid #ccc; padding-bottom: 2pt; page-break-after: avoid; }
h3 { font-size: 11pt; margin-top: 12pt; page-break-after: avoid; }
p { margin: 0 0 6pt 0; }
ul, ol { margin: 0 0 6pt 0; padding-left: 18pt; }
li { margin: 0 0 3.5pt 0; }
code { font-family: Menlo, monospace; font-size: 8.7pt; }
.box { page-break-inside: avoid; margin: 7pt 0 9pt 0; }
pre { padding: 7pt 8pt; white-space: pre-wrap; word-break: break-word; margin: 0; }
pre code { white-space: pre-wrap; }
/* plain code (not a terminal command / not expected output) */
pre:not(.term):not(.out) { background: #f6f7f9; border: 1px solid #dde; }
.lbl { font-family: Helvetica, Arial, sans-serif; font-size: 8pt; letter-spacing: .05em;
       text-transform: uppercase; padding: 2pt 7pt; display: inline-block; font-weight: bold; }
.lbl.term { background: #1f2937; color: #fff; }
.lbl.out  { background: #d9c46a; color: #3a3000; }
pre.term { background: #1f2937; color: #e5e7eb; border: 1px solid #1f2937; }
pre.term code { color: #e5e7eb; }
pre.out { background: #fffbea; border: 1px solid #d9c46a; color: #3a3000; }
pre.out code { color: #3a3000; }
p.open { background: #e8f0fe; border-left: 4px solid #0b3d91; padding: 5pt 8pt;
         margin: 6pt 0 9pt 0; page-break-inside: avoid; }
table { border-collapse: collapse; font-size: 9.2pt; width: 100%; margin: 6pt 0 10pt 0; }
th, td { border: 1px solid #666; padding: 3pt 5pt; vertical-align: top; text-align: left; }
th { background: #eee; }
tr { page-break-inside: avoid; }
blockquote { border-left: 3px solid #999; margin: 8pt 0; padding: 2pt 0 2pt 10pt; color: #333; }
a { color: #0b3d91; text-decoration: none; }
/* title page */
.cover { height: 245mm; display: flex; flex-direction: column; align-items: center;
         justify-content: center; text-align: center; page-break-after: always; }
.cover .t { font-size: 21pt; font-weight: bold; margin-bottom: 10pt; }
.cover .s { font-size: 12pt; color: #333; margin-bottom: 4pt; }
.cover .d { font-size: 11pt; color: #555; margin-top: 16pt; }
.cover .n { font-size: 9.5pt; color: #666; margin-top: 26pt; max-width: 115mm; line-height: 1.5; }
/* index */
h2.toc-h { border-bottom: none; }
table.toc { border: none; font-size: 10pt; }
table.toc td { border: none; padding: 1.6pt 4pt; }
table.toc td.pg { text-align: right; width: 34pt; }
table.toc tr.l2 td { font-weight: bold; padding-top: 5pt; }
table.toc tr.l3 td.t { padding-left: 18pt; font-weight: normal; }
.tocbreak { page-break-after: always; }
"""

def cover_html(doc: dict) -> str:
    subs = "".join(f'<div class="s">{t}</div>' for t in doc["subtitles"])
    return (f'<div class="cover"><div class="t">{doc["title"]}</div>{subs}'
            f'<div class="d">{DATE}</div><div class="n">{doc["note"]}</div></div>\n')




def md_to_html(md_text: str) -> str:
    html = markdown.markdown(md_text, extensions=["tables", "fenced_code", "toc"])
    # A bare "Terminal" / "Expected output" paragraph before a code block becomes
    # that block's coloured label; colour plus one word is the whole signal.
    for lang, cls, lbl in (("bash", "term", "Terminal"), ("text", "out", "Expected output")):
        html = re.sub(
            rf"<p>{lbl}</p>\s*<pre><code class=\"language-{lang}\">(.*?)</code></pre>",
            rf'<div class="box"><div class="lbl {cls}">{lbl}</div>'
            rf'<pre class="{cls}"><code>\1</code></pre></div>',
            html, flags=re.S)
        html = re.sub(
            rf"<pre><code class=\"language-{lang}\">(.*?)</code></pre>",
            rf'<div class="box"><div class="lbl {cls}">{lbl}</div>'
            rf'<pre class="{cls}"><code>\1</code></pre></div>',
            html, flags=re.S)
    html = re.sub(r"<p>Open in Safari", '<p class="open">Open in Safari', html)
    return html


def collect_headings(html: str):
    """[(level, number-or-None, text, anchor-id)] in document order."""
    out = []
    for m in re.finditer(r'<h([23]) id="([^"]+)">(.*?)</h\1>', html, flags=re.S):
        level, anchor, inner = int(m.group(1)), m.group(2), m.group(3)
        text = re.sub(r"<[^>]+>", "", inner).strip()
        num = None
        nm = re.match(r"(\d+)\.\s", text)
        if nm:
            num = int(nm.group(1))
        out.append((level, num, text, anchor))
    return out


def linkify_sections(html: str, num_to_anchor: dict[int, str]) -> str:
    """Turn "Section 4" / "Sections 15 to 19" in the prose into real links.

    Only inside text: never inside a tag, an anchor, or a code block, and never
    the heading that defines the section itself.
    """
    protected = []

    def stash(m):
        protected.append(m.group(0))
        return f"\x00{len(protected) - 1}\x00"

    # protect headings, existing anchors, code and pre blocks
    html = re.sub(r"<h[1-6][^>]*>.*?</h[1-6]>", stash, html, flags=re.S)
    html = re.sub(r"<a\b.*?</a>", stash, html, flags=re.S)
    html = re.sub(r"<pre\b.*?</pre>", stash, html, flags=re.S)
    html = re.sub(r"<code\b.*?</code>", stash, html, flags=re.S)
    html = re.sub(r"<table class=\"toc\">.*?</table>", stash, html, flags=re.S)

    def one(m):
        word, n = m.group(1), int(m.group(2))
        anchor = num_to_anchor.get(n)
        if not anchor:
            return m.group(0)
        return f'<a href="#{anchor}">{word} {n}</a>'

    # "Section 4", "section 10"
    html = re.sub(r"\b([Ss]ection)\s+(\d{1,2})\b", one, html)

    # "Sections 15 to 19" -> link both ends
    def rng(m):
        a, b = int(m.group(2)), int(m.group(3))
        aa, bb = num_to_anchor.get(a), num_to_anchor.get(b)
        if not (aa and bb):
            return m.group(0)
        return f'<a href="#{aa}">{m.group(1)} {a}</a> to <a href="#{bb}">{b}</a>'

    html = re.sub(r"\b([Ss]ections)\s+(\d{1,2})\s+to\s+(\d{1,2})\b", rng, html)

    for i, orig in enumerate(protected):
        html = html.replace(f"\x00{i}\x00", orig)
    return html


def build_toc(headings, pages: dict[str, int]) -> str:
    rows = []
    for level, _num, text, anchor in headings:
        pg = pages.get(anchor, "")
        rows.append(
            f'<tr class="l{level}"><td class="t">'
            f'<a href="#{anchor}">{text}</a></td><td class="pg">{pg}</td></tr>')
    return ('<h2 class="toc-h" id="index">Index</h2>\n<table class="toc">\n'
            + "\n".join(rows) + "\n</table>\n<div class=\"tocbreak\"></div>\n")


def render(html_body: str, title: str) -> Path:
    WORK.mkdir(exist_ok=True)
    page = (f'<!doctype html><html><head><meta charset="utf-8">'
            f"<title>{title}</title><style>{CSS}</style></head><body>{html_body}</body></html>")
    src = WORK / "runbook.html"
    src.write_text(page, encoding="utf-8")
    tmp = WORK / "runbook_raw.pdf"
    subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                    f"--print-to-pdf={tmp}", src.as_uri()],
                   capture_output=True, check=False)
    if not tmp.exists():
        sys.exit("Chrome did not produce a PDF")
    return tmp


def page_of_headings(pdf: Path, headings, body_marker: str) -> dict[str, int]:
    reader = PdfReader(str(pdf))
    text = [" ".join((p.extract_text() or "").split()) for p in reader.pages]
    # body starts after the cover and the index
    start = 0
    for i, t in enumerate(text):
        if body_marker in t:
            start = i
            break
    found = {}
    for _lvl, _num, title, anchor in headings:
        key = " ".join(title.split())[:40]
        for pn in range(start, len(text)):
            if key in text[pn]:
                found[anchor] = pn + 1
                break
    return found


def stamp_page_numbers(src: Path, dest: Path, title: str) -> int:
    reader = PdfReader(str(src))
    writer = PdfWriter()
    total = len(reader.pages)
    for i, page in enumerate(reader.pages):
        if i == 0:                       # title page carries no number
            writer.add_page(page)
            continue
        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=A4)
        c.setFont("Times-Roman", 9)
        c.drawCentredString(A4[0] / 2, 26, f"Page {i + 1} of {total}")
        c.save()
        buf.seek(0)
        page.merge_page(PdfReader(buf).pages[0])
        writer.add_page(page)
    writer.add_metadata({"/Title": title})
    with open(dest, "wb") as fh:
        writer.write(fh)
    return total


def build(doc: dict) -> None:
    md_text = doc["src"].read_text(encoding="utf-8")
    # a lone em dash in a table cell means "not applicable"; leave it as a dash.
    md_text = re.sub(r"\|\s*\u2014\s*\|", "| - |", md_text)
    md_text = re.sub(r"\s*\u2014\s*", ", ", md_text)   # elsewhere it is a comma break
    md_text = md_text.replace("\u2013", "-")             # en dash inside ranges
    # the markdown "Contents" list is replaced by the generated, page-numbered index
    md_text = re.sub(r"^## Contents\b.*?(?=^## 1\. )", "", md_text, flags=re.S | re.M)

    body = md_to_html(md_text)
    headings = collect_headings(body)
    num_to_anchor = {num: anchor for _l, num, _t, anchor in headings if num is not None}
    body = linkify_sections(body, num_to_anchor)

    # An index earns its place in a thirty-page runbook and gets in the way of a
    # two-page list of questions, where it would be longer than the thing it
    # indexes. Documents that skip it render once instead of iterating to fixed
    # page numbers, because there are no page numbers left to resolve.
    pages: dict[str, int] = {}
    if not doc.get("toc", True):
        raw = render(cover_html(doc) + body, doc["title"])
    else:
        raw = None
        for _ in range(4):
            raw = render(cover_html(doc) + build_toc(headings, pages) + body, doc["title"])
            found = page_of_headings(raw, headings, doc["body_marker"])
            if found == pages:
                break
            pages = found

    total = stamp_page_numbers(raw, doc["out"], doc["title"])
    if not doc.get("toc", True):
        print(f"{doc['out'].name}: {total} pages, no index, "
              f"{len(num_to_anchor)} numbered sections")
        return
    missing = [t for _l, _n, t, a in headings if a not in pages]
    print(f"{doc['out'].name}: {total} pages, {len(headings)} index entries, "
          f"{len(num_to_anchor)} numbered sections")
    if missing:
        print("  page number not resolved for:", "; ".join(missing))


def main() -> None:
    wanted = sys.argv[1:] or list(DOCS)
    for name in wanted:
        if name not in DOCS:
            sys.exit(f"unknown document {name!r}; choose from {', '.join(DOCS)}")
        build(DOCS[name])


if __name__ == "__main__":
    main()
