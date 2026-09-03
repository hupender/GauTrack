#!/usr/bin/env python3
"""Build the screen-by-screen walkthrough, in both forms, from one source.

Two outputs, one body of text, so the printed document and the shareable page
can never drift apart:

  * ``WALKTHROUGH.html``  -- for the browser and for publishing. Images are
    resampled down so the page stays light enough to open on a phone.
  * ``GauTrack_Screens.pdf`` -- for printing, forwarding and lifting pictures
    out of. Images are embedded at high resolution precisely so that someone
    can cut one out of the PDF and drop it into a slide without it going soft.

Every desktop capture is 3120x1880 and every phone capture is 1200x2580, which
is what lets the layout give each family one fixed size. The captures come from
``scripts/shoot.py``-style runs against a live demo server; re-take them into
``.build/screens/`` before rebuilding if the dashboard has changed.

Run:  make walkthrough
"""
from __future__ import annotations

import base64
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SHOTS = ROOT / ".build" / "screens"
WORK = ROOT / ".build"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

HTML_OUT = ROOT / "WALKTHROUGH.html"
PDF_OUT = ROOT.parent / "GauTrack_Screens.pdf"

# Wide captures and phone captures are resampled differently: a phone screen is
# already narrow, so it keeps its native width in both builds.
SIZES = {
    #                desktop px, phone px, jpeg quality
    "web":   (1200, 760, 80),
    "print": (2400, 1200, 90),
}


# --------------------------------------------------------------------------
# content: written once, rendered twice
# --------------------------------------------------------------------------
DESKTOP = {
    "01-overview": ("The day at a glance",
        "Nine headline figures across the top, then the charts. The map shows where animals "
        "keep being found: the bigger, darker circles are the junctions to fix first. The "
        "right-hand table shows how full each shelter is, which is the real limit on how many "
        "animals can be picked up."),
    "12-card-enlarged": ("One click: bigger",
        "Any figure or chart opens to full size for the room to read."),
    "13-card-provenance": ("Two clicks: where it came from",
        "The same card turned over, showing what was counted, over what period, and a button "
        "that opens those records."),
    "02-owners": ("Cattle keepers",
        "Every household, dairy and shed that has been visited, with what the keeper said they "
        "own against what was actually found. A gap is the list the next visit works from."),
    "03-animals": ("Animals",
        "Every animal on the register: what it is, who owns it, whether it carries a tag, and "
        "where it is now."),
    "10-owner-detail": ("One keeper",
        "Everything recorded about one keeper: their animals, their offences, their fines, and "
        "whether they paid."),
    "11-animal-detail": ("One animal",
        "Photograph, tag number, identifying marks, and everything that has ever happened to "
        "this animal."),
    "04-events": ("Everything that happened",
        "Every sighting, impounding and release, in order, each one signed by the officer who "
        "recorded it."),
    "05-fines": ("Fines",
        "What was charged, under what rule, and whether the money actually arrived. The gap "
        "between issued and collected is shown on purpose."),
    "06-shelters": ("Shelters",
        "Each gaushala, nandishala and pound, with how many animals are in it against how many "
        "it can hold."),
    "07-users": ("Staff accounts",
        "Who has an account, what they are allowed to see, and whether they are still working "
        "here."),
    "08-audit": ("The tamper check",
        "Every change ever made to the register is recorded in a sealed list that the software "
        "itself cannot rewrite. Pressing the check button confirms nothing has been altered "
        "after the fact. This is what makes the numbers defensible if they are challenged."),
    "09-cm-view": ("Totals only",
        "Six headline figures, how full the shelters are, how far each municipal committee has "
        "got, and the hours of day when animals reach the road. Those two peaks are the grazing "
        "routine: animals let out in the morning and brought back to be milked in the evening. "
        "They are the argument for when to put patrols out."),
}

PHONES = {
    "15-field-signin": ("Signing in",
        "One account per officer. Every record is signed with their name."),
    "16-field-home": ("The six things it does",
        "Large buttons, made for one thumb and bright sunlight."),
    "20-field-register-owner": ("Registering a keeper",
        "Name, village, how many animals they say they have, and the size of the shed. The "
        "phone records where it is standing."),
    "17-field-register-animal": ("Registering an animal",
        "Cow or buffalo, male or female, age, colour, two identifying marks, a photograph, and "
        "the tag number."),
    "18-field-road-sighting": ("An animal on the road",
        "Photograph and location, recorded on the spot in a few seconds."),
    "19-field-tag-lookup": ("Looking up a tag",
        "Type the tag number and the owner comes back. This is the step that does not exist "
        "today."),
}

PUBLIC = ("14-public-report", "Report an animal on the road",
    "No account, no app, no name required. A photograph and a location. Public reports are "
    "kept separate and are never counted in the headline figures until an officer has verified "
    "them, so an open reporting channel cannot be used to inflate or deflate the numbers.")


# --------------------------------------------------------------------------
# image preparation
# --------------------------------------------------------------------------
def prepare(mode: str) -> Path:
    """Resample every capture for this build and return the directory."""
    wide_px, phone_px, quality = SIZES[mode]
    out = WORK / f"img-{mode}"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    for src in sorted(SHOTS.glob("*.png")):
        px = phone_px if src.stem in PHONES or src.stem == PUBLIC[0] else wide_px
        subprocess.run(
            ["sips", "-Z", str(px), "-s", "format", "jpeg",
             "-s", "formatOptions", str(quality), str(src),
             "--out", str(out / f"{src.stem}.jpg")],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return out


def data_uri(d: Path, name: str) -> str:
    return "data:image/jpeg;base64," + base64.b64encode((d / f"{name}.jpg").read_bytes()).decode()


def fig(d: Path, name: str, title: str, body: str, cls: str = "shot") -> str:
    return (f'<figure class="{cls}"><img src="{data_uri(d, name)}" alt="{title}">'
            f'<figcaption><b>{title}</b> {body}</figcaption></figure>')


# --------------------------------------------------------------------------
# shared stylesheet, with the print-only rules appended for the PDF build
# --------------------------------------------------------------------------
FONTS = ('<link rel="preconnect" href="https://fonts.googleapis.com">\n'
         '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>\n'
         '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?'
         'family=Archivo:wght@500;600;700&family=Source+Serif+4:opsz,wght@8..60,400;8..60,600'
         '&family=IBM+Plex+Mono:wght@400;500&display=swap">')

BASE_CSS = """
/* Palette taken from the subject rather than from a screen: the deep green is
   GauTrack's own, already on every page of the software, and the browns and
   ochres are the ones the charts now use. The document and the product are
   meant to read as one thing. */
:root{
  --paper:#faf8f4; --panel:#ffffff; --ink:#221c15; --ink-2:#4a4239; --muted:#736a5e;
  --rule:#e2dbd0; --rule-2:#cec4b6;
  --green:#12603f; --green-soft:#e7f0ea;
  --hide:#8a5a2b; --ochre:#b3762a; --brick:#9c4a2f;
  --mast-bg:#0d4a30; --mast-ink:#f4efe6; --mast-kicker:#8fd9b4; --mast-lede:#dfe9e2; --mast-stamp:#a9c6b6;
  --shadow:0 1px 2px rgba(40,30,18,.05), 0 10px 30px rgba(40,30,18,.07);
}
@media (prefers-color-scheme: dark){
  :root:not([data-theme="light"]){
    --paper:#17140f; --panel:#211c16; --ink:#f2ece2; --ink-2:#cfc6b8; --muted:#a2988a;
    --rule:#332c23; --rule-2:#463d31;
    --green:#4fbf8b; --green-soft:#16281f;
    --hide:#c9945c; --ochre:#e0a961; --brick:#d17a58;
    --mast-bg:#0b3a26; --mast-ink:#eef4ef; --mast-kicker:#6cc79b; --mast-lede:#c6d8cd; --mast-stamp:#8aab9a;
    --shadow:0 1px 2px rgba(0,0,0,.4), 0 10px 30px rgba(0,0,0,.45);
  }
}
:root[data-theme="dark"]{
  --paper:#17140f; --panel:#211c16; --ink:#f2ece2; --ink-2:#cfc6b8; --muted:#a2988a;
  --rule:#332c23; --rule-2:#463d31;
  --green:#4fbf8b; --green-soft:#16281f;
  --hide:#c9945c; --ochre:#e0a961; --brick:#d17a58;
  --mast-bg:#0b3a26; --mast-ink:#eef4ef; --mast-kicker:#6cc79b; --mast-lede:#c6d8cd; --mast-stamp:#8aab9a;
  --shadow:0 1px 2px rgba(0,0,0,.4), 0 10px 30px rgba(0,0,0,.45);
}
*{box-sizing:border-box}
body{margin:0; background:var(--paper); color:var(--ink);
  font:400 17px/1.62 "Source Serif 4", Georgia, "Times New Roman", serif;
  -webkit-font-smoothing:antialiased}
.wrap{max-width:1180px; margin:0 auto; padding:0 clamp(1rem,4vw,3rem) 6rem}
.measure{max-width:62ch}
h1,h2,h3,.label,.num,figcaption b,th{font-family:Archivo,"Helvetica Neue",Arial,sans-serif}

header.top{background:var(--mast-bg); color:var(--mast-ink);
  padding:clamp(2.2rem,5vw,4rem) 0 clamp(1.8rem,4vw,3rem); margin-bottom:clamp(2rem,4vw,3.5rem)}
header.top .wrap{padding-bottom:0}
h1{font-size:clamp(2.1rem,5.4vw,3.9rem); font-weight:700; line-height:1.02; letter-spacing:-.02em;
   margin:0; text-wrap:balance; color:var(--mast-ink)}
header.top .kicker{font-family:Archivo,sans-serif; font-size:.78rem; font-weight:600;
  letter-spacing:.14em; text-transform:uppercase; color:var(--mast-kicker); margin:0 0 .9rem}
header.top p.lede{font-size:clamp(1.02rem,1.6vw,1.22rem); line-height:1.55; margin:1.2rem 0 0;
  max-width:60ch; color:var(--mast-lede)}
.stamp{margin-top:1.6rem; padding-top:1.1rem; border-top:1px solid rgba(255,255,255,.16);
  display:flex; flex-wrap:wrap; gap:.5rem 2rem; font-family:"IBM Plex Mono",monospace;
  font-size:.76rem; color:var(--mast-stamp)}

section{margin:clamp(2.8rem,6vw,4.5rem) 0 0}
.sec-head{display:flex; gap:1.1rem; align-items:baseline; border-top:2px solid var(--ink);
  padding-top:.85rem; margin-bottom:1.4rem}
.num{font-size:.8rem; font-weight:700; letter-spacing:.1em; color:var(--green);
  font-variant-numeric:tabular-nums; padding-top:.35rem; flex:0 0 auto}
h2{font-size:clamp(1.35rem,2.7vw,2rem); font-weight:600; line-height:1.15; letter-spacing:-.015em;
   margin:0; text-wrap:balance}
h3{font-size:1.02rem; font-weight:600; margin:2.4rem 0 .6rem; color:var(--ink)}
p{margin:0 0 1.05rem}
.lead{font-size:1.08rem; color:var(--ink-2)}
strong,b{font-weight:600}
a{color:var(--green); text-underline-offset:2px}

.people{display:grid; grid-template-columns:repeat(auto-fit,minmax(215px,1fr)); gap:1rem; margin:1.6rem 0 0}
.person{background:var(--panel); border:1px solid var(--rule); border-radius:3px;
  padding:1.1rem 1.15rem; box-shadow:var(--shadow)}
.person .who{font-family:Archivo,sans-serif; font-weight:600; font-size:1.02rem; margin:0 0 .1rem}
.person .dev{font-family:"IBM Plex Mono",monospace; font-size:.72rem; color:var(--green); margin:0 0 .55rem}
.person p{font-size:.9rem; line-height:1.5; color:var(--ink-2); margin:0}

figure{margin:1.8rem 0 0}
figure img{display:block; width:100%; height:auto; border:1px solid var(--rule-2);
  border-radius:3px; box-shadow:var(--shadow); background:var(--panel)}
figcaption{font-size:.88rem; line-height:1.55; color:var(--muted); margin-top:.6rem; max-width:74ch}
figcaption b{font-family:Archivo,sans-serif; font-weight:600; font-size:.83rem; color:var(--ink);
  display:block; margin-bottom:.12rem}
.grid2{display:grid; grid-template-columns:repeat(auto-fit,minmax(330px,1fr)); gap:1.6rem 1.5rem}
.grid2 figure{margin-top:0}
/* Every phone capture is the same 1200 x 2580, so one width gives the whole
   row one height and the captions line up without any cropping. */
.phones{display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr));
  gap:1.7rem 1.3rem; margin-top:1.6rem; align-items:start}
.phones figure{margin:0; display:flex; flex-direction:column}
.phones img{border-radius:14px}
.phones figcaption{margin-top:.65rem}
figure.solo{display:grid; grid-template-columns:minmax(0,230px) minmax(0,1fr);
  gap:1.6rem; align-items:center}
figure.shot.solo img{border-radius:14px}
figure.solo figcaption{margin-top:0}

.tbl{overflow-x:auto; margin:1.4rem 0 0}
table{width:100%; border-collapse:collapse; font-size:.94rem; min-width:520px}
th{font-size:.74rem; font-weight:600; letter-spacing:.07em; text-transform:uppercase;
  color:var(--muted); text-align:left; padding:0 1rem .5rem 0; border-bottom:1.5px solid var(--ink)}
td{padding:.7rem 1rem .7rem 0; border-bottom:1px solid var(--rule); vertical-align:top; color:var(--ink-2)}
td:first-child{color:var(--ink); font-weight:600; font-family:Archivo,sans-serif; font-size:.92rem; white-space:nowrap}
.mono{font-family:"IBM Plex Mono",monospace; font-size:.85rem; color:var(--green)}

.note{background:var(--green-soft); border-left:3px solid var(--green); padding:1rem 1.2rem;
  margin:1.6rem 0; border-radius:0 3px 3px 0}
.note p{margin:0; font-size:.95rem; color:var(--ink-2)}

footer{margin-top:4rem; padding-top:1.2rem; border-top:1px solid var(--rule);
  font-size:.82rem; color:var(--muted)}
@media (max-width:640px){ body{font-size:16px} .sec-head{gap:.7rem} }
"""

# Print rules. The design language is unchanged; what changes is that the page
# is now a fixed 210 mm wide, every picture is pinned to one of two sizes, and
# nothing is allowed to break across a page boundary halfway through a figure.
PRINT_CSS = """
@page{ size:A4 portrait; margin:14mm 15mm 16mm; }
:root{ --paper:#fff; }
html,body{ background:#fff; }
body{ font-size:11pt; line-height:1.52; }
.wrap{ max-width:none; padding:0; }
.measure{ max-width:none; }

/* The masthead is a contained panel here, not the full-bleed band it is on the
   web. Chrome's print-to-PDF paints nothing outside the page margin box, so the
   negative margins that produce the bleed on screen were simply discarded and
   the band came out as an inset rectangle still carrying bleed-sized padding:
   text pushed far from its own edges and a wide empty green strip underneath.
   Sizing it as a panel makes the printed page match what it looks like. */
header.top{ margin:0 0 8mm; padding:7mm 9mm 6mm; border-radius:2px; break-after:avoid; }
h1{ font-size:28pt; }
header.top .kicker{ font-size:9pt; }
header.top p.lede{ font-size:12pt; max-width:150mm; margin-top:5mm; }
.stamp{ font-size:8.5pt; margin-top:5mm; padding-top:3mm; }

/* No forced page breaks. Every figure already refuses to split across pages,
   which is the only guarantee that matters; starting each section on a fresh
   sheet on top of that left half-empty pages wherever a section happened to
   end near the top of one. */
section{ margin:8mm 0 0; }
.sec-head{ margin-bottom:4mm; break-after:avoid; }
h2{ font-size:17pt; }
h3{ font-size:12pt; margin:7mm 0 2mm; break-after:avoid; }
.num{ font-size:8.5pt; }
p{ margin:0 0 3.6mm; }
.lead{ font-size:12pt; }

/* The two fixed picture sizes. Every desktop capture is 3120 x 1880 and every
   phone capture is 1200 x 2580, so a single width per family is all it takes
   for them to come out identical throughout the document, which is what makes
   them safe to lift straight into a slide. */
figure{ margin:5mm 0 0; break-inside:avoid; }
figure.shot img{ width:180mm; height:108.5mm; }
/* Fixed track widths rather than 1fr: a fraction is resolved against whatever
   the grid's own width turns out to be, and a caption then sets its own
   measure from that instead of from the picture it belongs to, which is how
   the right-hand captions ended up running past the page margin. */
.grid2{ grid-template-columns:87mm 87mm; gap:5mm 6mm; justify-content:space-between; }
.grid2 > figure{ width:87mm; max-width:87mm; }
.grid2 figure.shot img{ width:87mm; height:52.4mm; }
.phones{ grid-template-columns:repeat(3,56mm); gap:6mm; justify-content:space-between; }
.phones > figure{ width:56mm; max-width:56mm; }
/* `.phones figure.shot img` rather than `.phones img`: every figure carries the
   `shot` class, so the plain class selector lost to `figure.shot img` above and
   the phone captures were being drawn at the desktop size and clipped by their
   own column. */
.phones figure.shot img{ width:56mm; height:120.4mm; border-radius:5px; }
figure.solo{ grid-template-columns:56mm 1fr; gap:9mm; align-items:start; }
figure.shot.solo img{ width:56mm; height:120.4mm; border-radius:5px; }
figure img{ box-shadow:none; }
figcaption{ font-size:10pt; line-height:1.45; margin-top:2.4mm; max-width:100%;
            overflow-wrap:break-word; }
figcaption b{ font-size:10.2pt; }

.people{ grid-template-columns:repeat(4,1fr); gap:4mm; break-inside:avoid; }
.person{ box-shadow:none; padding:3.5mm 4mm; }
.person .who{ font-size:11pt; } .person p{ font-size:9.2pt; line-height:1.4; }
.person .dev{ font-size:8.5pt; }
.note{ break-inside:avoid; margin:5mm 0; padding:4mm 5mm; }
.note p{ font-size:10.6pt; }
table{ font-size:10pt; min-width:0; break-inside:avoid; }
.tbl{ overflow:visible; }
/* Screen row padding on a printed table wastes about a centimetre over four
   rows, which was the difference between the closing note sitting under the
   table and getting a page of its own. */
.tbl th{ padding:0 5mm 2mm 0; }
.tbl td{ padding:2.6mm 5mm 2.6mm 0; }
footer{ display:none; }
"""


def document(d: Path, *, printing: bool) -> str:
    return f"""<title>GauTrack, Screen by Screen</title>
{FONTS}
<style>{BASE_CSS}{PRINT_CSS if printing else ""}</style>

<header class="top">
  <div class="wrap">
    <p class="kicker">Rewari district &middot; Haryana</p>
    <h1>GauTrack, screen by screen</h1>
    <p class="lede">Every screen in the system, what it shows, and who is allowed to see it.
      This is the working software, not a mock-up. The names, animals and numbers in these
      pictures are invented for the demonstration; no real person appears anywhere.</p>
    <div class="stamp">
      <span>Prepared for the office of the District Municipal Commissioner</span>
      <span>Questions for the department: separate document</span>
      <span>30 August 2026</span>
    </div>
  </div>
</header>

<div class="wrap">

<section>
  <div class="sec-head"><span class="num">01</span><h2>What it is</h2></div>
  <div class="measure">
    <p class="lead">Today nobody can answer three questions: how many cattle are loose on
      Rewari's roads, whose they are, and whether anything was done about the last one.
      Every argument about strays is an argument about numbers nobody can produce.</p>
    <p>GauTrack is the record that answers all three. Staff in the field enter what they find
      on an ordinary phone. The office sees it the same minute. The Chief Minister's office
      sees the totals, and nothing else. A member of the public can report an animal on the
      road without needing an account or an app.</p>
    <p>Nothing is installed on anyone's computer or phone. Every screen below is a web page.
      Whoever you want to give access to, you give them a web address and a password.</p>
  </div>
</section>

<section>
  <div class="sec-head"><span class="num">02</span><h2>Four people, one record</h2></div>
  <div class="measure"><p>Everyone below is looking at the same information. What differs is
    how much of it each is allowed to see, which is decided by the password they sign in
    with, not by which page they happen to open.</p></div>
  <div class="people">
    <div class="person"><p class="who">Field officer</p><p class="dev">phone</p>
      <p>Registers keepers and animals, records an animal found on the road, sends one to a
      shelter, looks up a tag. Works with no signal and catches up later.</p></div>
    <div class="person"><p class="who">The office</p><p class="dev">office computer</p>
      <p>Sees everything: every keeper, every animal, every sighting, every fine, and who
      recorded it. This is where the day's work is checked.</p></div>
    <div class="person"><p class="who">Chief Minister's office</p><p class="dev">any computer</p>
      <p>One screen of totals. No names, no phone numbers, no photographs. Safe to put on a
      wall or hand to the press.</p></div>
    <div class="person"><p class="who">General public</p><p class="dev">their own phone</p>
      <p>One short form: there is an animal on the road, here is a photograph, here is where.
      No account needed.</p></div>
  </div>
</section>

<section>
  <div class="sec-head"><span class="num">03</span><h2>The office screen</h2></div>
  <div class="measure"><p>This is what the office sees on signing in. Everything on it is
    counted from the records at the moment the page opens. Nothing is typed in by hand and
    nothing is prepared the night before.</p></div>

  {fig(d, *(("01-overview",) + DESKTOP["01-overview"]))}

  <div class="note"><p><b>The two clicks that settle an argument.</b> Click any figure or
    chart and it grows to fill the screen, so a room can read it. Click again and it turns
    over to show exactly what was counted and where those records are, with a button that
    opens them. If somebody asks where 74% came from, the answer is two clicks away and ends
    on the actual records.</p></div>

  <div class="grid2">
    {fig(d, *(("12-card-enlarged",) + DESKTOP["12-card-enlarged"]))}
    {fig(d, *(("13-card-provenance",) + DESKTOP["13-card-provenance"]))}
  </div>

  <h3>The registers behind the numbers</h3>
  <div class="grid2">
    {"".join(fig(d, *((k,) + DESKTOP[k])) for k in
             ["02-owners", "03-animals", "10-owner-detail", "11-animal-detail",
              "04-events", "05-fines", "06-shelters", "07-users"])}
  </div>

  {fig(d, *(("08-audit",) + DESKTOP["08-audit"]))}
</section>

<section>
  <div class="sec-head"><span class="num">04</span><h2>The Chief Minister's screen</h2></div>
  <div class="measure"><p>A separate screen with its own password. It fits one screen with no
    scrolling, is designed to be read from the back of a room, and deliberately contains no
    personal information at all, so it can be projected or shared without any clearance.</p></div>
  {fig(d, *(("09-cm-view",) + DESKTOP["09-cm-view"]))}
</section>

<section>
  <div class="sec-head"><span class="num">05</span><h2>The phone the field team carries</h2></div>
  <div class="measure"><p>An ordinary Android phone. Nothing to install from an app store: it
    is a web page that behaves like an app. It keeps working where there is no signal and
    sends everything up once the phone is back in coverage, which is the difference between a
    system field staff use and one they abandon.</p></div>
  <div class="phones">
    {"".join(fig(d, *((k,) + v)) for k, v in PHONES.items())}
  </div>
  <div class="note"><p><b>Why the identifying marks matter.</b> The most likely way an owner
    escapes a fine is to cut the ear tag off. The photograph, the muzzle picture and two
    written marks are what still identify the animal when the tag is gone.</p></div>
</section>

<section>
  <div class="sec-head"><span class="num">06</span><h2>The form the public uses</h2></div>
  {fig(d, *PUBLIC, cls="shot solo")}
</section>

<section>
  <div class="sec-head"><span class="num">07</span><h2>How each person gets in</h2></div>
  <div class="tbl">
    <table>
      <thead><tr><th>Who</th><th>What they open</th><th>On what</th><th>What they need</th></tr></thead>
      <tbody>
        <tr><td>Field officer</td><td class="mono">/app</td><td>Any Android phone with mobile data</td><td>Their own account. The phone must be allowed to use the camera and location.</td></tr>
        <tr><td>The office</td><td class="mono">/admin</td><td>The office computer</td><td>Their own account, with a second code at sign-in for extra safety.</td></tr>
        <tr><td>CM's office</td><td class="mono">/cm</td><td>Any computer, anywhere</td><td>One read-only account.</td></tr>
        <tr><td>General public</td><td class="mono">/report</td><td>Their own phone</td><td>Nothing. The link, and a printed code on a poster.</td></tr>
      </tbody>
    </table>
  </div>
  <div class="measure"><p style="margin-top:1.4rem">Each address is the same web address with a
    different ending, the way a department's website has different pages.
    <b>Everybody gets their own account and nobody shares one:</b> the moment two people use
    one login, the record of who did what is worthless, and that record is most of the value
    here.</p></div>
</section>

<footer>
  <p>GauTrack &middot; Rewari district stray cattle registry. All figures and names in these
  screens are demonstration data. Every screen shown is working software.
  The questions this raises for the department are in a separate document.</p>
</footer>

</div>
"""


def main() -> None:
    if not SHOTS.exists():
        sys.exit(f"no captures in {SHOTS}; take them first")

    web = document(prepare("web"), printing=False)
    HTML_OUT.write_text(web, encoding="utf-8")
    print(f"{HTML_OUT.name}: {HTML_OUT.stat().st_size / 1_000_000:.2f} MB")

    tmp = WORK / "walkthrough-print.html"
    tmp.write_text(document(prepare("print"), printing=True), encoding="utf-8")
    subprocess.run(
        [CHROME, "--headless", "--disable-gpu", "--no-pdf-header-footer",
         "--virtual-time-budget=20000", f"--print-to-pdf={PDF_OUT}", tmp.as_uri()],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"{PDF_OUT.name}: {PDF_OUT.stat().st_size / 1_000_000:.2f} MB")


if __name__ == "__main__":
    main()
