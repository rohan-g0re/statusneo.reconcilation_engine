"""Build SOLUTION_PITCH.pptx — the same 14 slides as index.html, as a real editable deck.

Images are embedded as bytes by python-pptx, not linked, so the file travels on its own.
Run:  uv run --with python-pptx python docs/pitch/deck/build_pptx.py
"""
from __future__ import annotations

import pathlib
import re

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Emu, Inches, Pt

import sys

HERE = pathlib.Path(__file__).resolve().parent
IMG = HERE / "img"
# Optional argv[1] output path — handy when the real file is open in PowerPoint
# and therefore locked against writing.
OUT = pathlib.Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else HERE.parent / "SOLUTION_PITCH.pptx"

# ---- palette (light theme from the HTML deck) ------------------------------
PAPER = RGBColor(0xFB, 0xFB, 0xF9)
PLATE = RGBColor(0xFF, 0xFF, 0xFF)
INK = RGBColor(0x1E, 0x1E, 0x1E)
INK_SOFT = RGBColor(0x55, 0x53, 0x4E)
INK_FAINT = RGBColor(0x8A, 0x87, 0x81)
RULE = RGBColor(0xD9, 0xD7, 0xD2)
RULE_SOFT = RGBColor(0xEC, 0xEB, 0xE7)
EXCEPTION = RGBColor(0xD0, 0x2B, 0x2B)
PENDING = RGBColor(0xC9, 0x7A, 0x00)
SETTLED = RGBColor(0x2F, 0x8A, 0x41)
REFERENCE = RGBColor(0x1A, 0x66, 0xB0)

MONO = "Cascadia Mono"
SERIF = "Georgia"

# ---- geometry --------------------------------------------------------------
SW, SH = 13.333, 7.5
ML, MR = 0.78, 0.78
CW = SW - ML - MR
MAST_Y, RULE1_Y = 0.44, 0.74
BODY_TOP, BODY_BOT = 1.00, 6.50
RULE2_Y, COLO_Y = 6.64, 6.70


def rgb(c):
    return c


def bg(slide):
    f = slide.background.fill
    f.solid()
    f.fore_color.rgb = PAPER


def rule(slide, y, color=RULE, left=ML, width=CW, thick=0.75):
    s = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(left), Inches(y), Inches(width), Pt(thick))
    s.fill.solid()
    s.fill.fore_color.rgb = color
    s.line.fill.background()
    s.shadow.inherit = False
    return s


_TOKEN = re.compile(r"(\*\*.+?\*\*|\*.+?\*|`.+?`)", re.S)


def _runs(paragraph, markup, size, font, color, bold=False, italic=False):
    """Render a tiny **bold** / *italic* / `code` markup into runs."""
    for piece in _TOKEN.split(markup):
        if not piece:
            continue
        b, i, fnt = bold, italic, font
        text = piece
        if piece.startswith("**") and piece.endswith("**"):
            text, b = piece[2:-2], True
        elif piece.startswith("`") and piece.endswith("`"):
            text, fnt = piece[1:-1], MONO
        elif piece.startswith("*") and piece.endswith("*"):
            text, i = piece[1:-1], True
        r = paragraph.add_run()
        r.text = text
        r.font.name = fnt
        r.font.size = Pt(size * (0.92 if fnt is MONO and font is SERIF else 1))
        r.font.bold = b
        r.font.italic = i
        r.font.color.rgb = color


def text(slide, left, top, width, height, body, *, size=15, font=SERIF, color=INK,
         bold=False, align=PP_ALIGN.LEFT, spacing=1.32, space_after=8,
         caps=False, track=False, anchor=MSO_ANCHOR.TOP):
    tb = slide.shapes.add_textbox(Inches(left), Inches(top), Inches(width), Inches(height))
    tf = tb.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    tf.vertical_anchor = anchor
    for n, line in enumerate(body.split("\n")):
        p = tf.paragraphs[0] if n == 0 else tf.add_paragraph()
        p.alignment = align
        p.line_spacing = spacing
        p.space_after = Pt(space_after)
        _runs(p, line.upper() if caps else line, size, font, color, bold=bold)
        if track:
            for r in p.runs:
                r.font._rPr.set("spc", "120")
    return tb


def masthead(slide, part, right, page, total, colophon):
    text(slide, ML, MAST_Y, CW * 0.6, 0.26, part, size=9, font=MONO,
         color=INK_SOFT, caps=True, track=True, space_after=0)
    text(slide, ML + CW * 0.4, MAST_Y, CW * 0.6, 0.26, right, size=9, font=MONO,
         color=INK_FAINT, caps=True, track=True, align=PP_ALIGN.RIGHT, space_after=0)
    rule(slide, RULE1_Y)
    rule(slide, RULE2_Y, RULE_SOFT)
    text(slide, ML, COLO_Y, CW * 0.82, 0.3, colophon, size=8.5, font=MONO,
         color=INK_FAINT, space_after=0)
    text(slide, ML + CW * 0.82, COLO_Y, CW * 0.18, 0.3, f"{page:02d} / {total}",
         size=8.5, font=MONO, color=INK_FAINT, align=PP_ALIGN.RIGHT, space_after=0)


def plate(slide, img_name, box_l, box_t, box_w, box_h, caption=None):
    """Drop an image into a white bordered plate, preserving aspect."""
    from PIL import Image

    path = IMG / img_name
    iw, ih = Image.open(path).size
    pad = 0.12
    cap_h = 0.30 if caption else 0.0
    avail_w, avail_h = box_w - 2 * pad, box_h - 2 * pad - cap_h
    scale = min(avail_w / iw, avail_h / ih)
    w, h = iw * scale, ih * scale

    plate_w, plate_h = w + 2 * pad, h + 2 * pad
    plate_l = box_l + (box_w - plate_w) / 2
    plate_t = box_t

    bgr = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(plate_l), Inches(plate_t),
                                 Inches(plate_w), Inches(plate_h))
    bgr.fill.solid()
    bgr.fill.fore_color.rgb = PLATE
    bgr.line.color.rgb = RULE
    bgr.line.width = Pt(0.75)
    bgr.shadow.inherit = False

    slide.shapes.add_picture(str(path), Inches(plate_l + pad), Inches(plate_t + pad),
                             Inches(w), Inches(h))
    if caption:
        # Pinned to the bottom of the box, not to the plate, so captions on a
        # slide share one baseline even when the plates differ in aspect.
        text(slide, box_l, box_t + box_h - cap_h, box_w, cap_h, caption,
             size=8.5, font=MONO, color=INK_FAINT, caps=True, track=True, space_after=0)


def card(slide, left, top, width, heading, body, accent=INK):
    rule(slide, top, accent, left=left, width=width, thick=2)
    text(slide, left, top + 0.14, width, 0.3, heading, size=9.5, font=MONO,
         color=INK_SOFT, bold=True, caps=True, track=True, space_after=0)
    text(slide, left, top + 0.46, width, 1.8, body, size=11.5, spacing=1.28, space_after=0)


def stat(slide, left, top, width, figure, label, color=INK):
    text(slide, left, top, width, 0.62, figure, size=27, font=MONO, bold=True,
         color=color, space_after=0)
    text(slide, left, top + 0.62, width, 0.9, label, size=8.5, font=MONO,
         color=INK_FAINT, caps=True, track=True, spacing=1.25, space_after=0)


def numbered(slide, top, items, gap=1.18):
    for n, body in enumerate(items):
        y = top + n * gap
        text(slide, ML, y, 0.5, 0.3, f"{n + 1:02d}", size=10, font=MONO,
             color=INK_FAINT, space_after=0)
        text(slide, ML + 0.72, y - 0.04, CW - 0.72, 1.0, body, size=13.5,
             spacing=1.34, space_after=0)
        if n < len(items) - 1:
            rule(slide, y + gap - 0.22, RULE_SOFT)


# ---------------------------------------------------------------------------
prs = Presentation()
prs.slide_width, prs.slide_height = Inches(SW), Inches(SH)
BLANK = prs.slide_layouts[6]
TOTAL = 16


def new(part, right, colophon, page):
    s = prs.slides.add_slide(BLANK)
    bg(s)
    masthead(s, part, right, page, TOTAL, colophon)
    return s


# 01 — title
s = new("Solution architecture", "2026", "Post-claim reconciliation", 1)
rule(s, 3.05, EXCEPTION, width=1.5, thick=3)
text(s, ML, 3.25, CW * 0.82, 1.5, "Post-Claim Pharmacy\nFinancial Reconciliation",
     size=38, font=MONO, bold=True, spacing=1.06, space_after=0)
text(s, ML, 4.92, CW * 0.72, 0.9,
     "Four independent feeds, no shared claim number, and one question:\n"
     "what were we owed, what arrived, and what needs a human?",
     size=17, spacing=1.3, space_after=0)
text(s, ML, 5.92, CW * 0.72, 0.5,
     "Every number is computed in Python. The model only explains numbers it was handed.",
     size=12.5, color=INK_SOFT, space_after=0)

# 02 — the problem
s = new("The problem", "Three roads and a witness", "Post-claim reconciliation", 2)
text(s, ML, BODY_TOP + 0.10, CW, 0.9,
     "A drug goes out the door. Three parties now owe money, and none of them talk.",
     size=24, font=MONO, bold=True, spacing=1.14, space_after=0)
cw4 = (CW - 3 * 0.38) / 4
for n, (h, b, c) in enumerate([
    ("Pharmacy benefit", "Pills at a counter, billed to a **PBM**. Adjudicated in seconds, paid in weeks.", REFERENCE),
    ("Medical benefit", "Drugs infused in a chair, billed to a **medical payer** via a clearinghouse.", REFERENCE),
    ("340B rebate", "A manufacturer owes cash back — but only once a **TPA** rules the dispense qualifies.", PENDING),
    ("The bank", "Owes nothing. It is the only proof the other three actually landed.", SETTLED),
]):
    card(s, ML + n * (cw4 + 0.38), 2.55, cw4, h, b, c)
text(s, ML, 4.70, CW * 0.92, 0.9,
     "Nobody sends the hospital a statement saying *here is what you are owed and here is what you got.* "
     "**That statement is the product.**", size=15, spacing=1.36, space_after=0)

# 03 — three threads
s = new("The argument", "Three threads", "Post-claim reconciliation", 3)
text(s, ML, BODY_TOP + 0.08, CW, 0.3,
     "Every design decision is one of these three wearing a different hat",
     size=9.5, font=MONO, color=EXCEPTION, caps=True, track=True, space_after=0)
numbered(s, 2.00, [
    "**Nothing carries one claim number end to end.** There is no shared ID. That is the hard part — not the arithmetic.",
    "**The system never guesses.** A wrong match looks identical to a correct one in every report afterwards, so it parks instead.",
    "**Everything is a replay.** Delete every answer it has produced, feed it the same files, get the same answers back.",
], gap=1.35)

# 04 — architecture
s = new("Architecture", "Five blocks",
        "Connectors fetch and never interpret · ingest writes records in · the engine writes verdicts out", 4)
plate(s, "01-system-overview.png", ML, BODY_TOP + 0.05, CW, BODY_BOT - BODY_TOP - 0.10)

# 05 — the rule
s = new("Architecture", "The rule",
        "A number a model produced is a number you cannot defend in an audit", 5)
text(s, ML, BODY_TOP + 0.25, CW * 0.94, 1.2,
     "Every number is computed in Python.\nThe model only explains numbers it was handed.",
     size=24, font=MONO, bold=True, spacing=1.16, space_after=0)
cw3 = (CW - 2 * 0.5) / 3
for n, (fig, lab, col) in enumerate([
    ("9", "Tools the agents can call", INK),
    ("8", "Of them read-only", INK),
    ("0", "Write tools in any model's tool list", EXCEPTION),
]):
    stat(s, ML + n * (cw3 + 0.5), 3.30, cw3, fig, lab, col)
text(s, ML, 5.05, CW * 0.9, 1.0,
     "The one write creates a **work item** for a human. Role tool lists are built by *removing* it, "
     "so no prompt can reach it — and the record names the person who pressed the button, not the model.",
     size=15, spacing=1.36, space_after=0)

# 06 — making the data
s = new("Making the data", "Built backwards from a proof",
        "A generator never sees an episode id, a verdict, or an expected amount — asserted at runtime", 6)
cw4 = (CW - 3 * 0.38) / 4
for n, (fig, lab, col) in enumerate([
    ("12.09bn", "Raw combinations", INK),
    ("4,224", "Legally possible", REFERENCE),
    ("372", "Verdict pairs — all reachable", REFERENCE),
    ("4,224", "Reproduced by the engine · no threshold", SETTLED),
]):
    stat(s, ML + n * (cw4 + 0.38), BODY_TOP + 0.08, cw4, fig, lab, col)
plate(s, "02-data-generation.png", ML, 2.62, CW, BODY_BOT - 2.62 - 0.05)

# 07 — episode build
s = new("Building an episode", "Six arrivals, one episode",
        "Episode E-000012 · every code read off the running engine · a record that fits nowhere is parked", 7)
plate(s, "03-episode-build.png", ML, BODY_TOP + 0.05, CW, BODY_BOT - BODY_TOP - 0.10)

# 08 — the rebate track, door by door
s = new("Building an episode", "The 340B rebate, door by door",
        "Qualification is the TPA's to assert · approval and payment belong to Beacon and the manufacturer", 8)
plate(s, "06-rebate-track-doors.png", ML, BODY_TOP + 0.05, CW, BODY_BOT - BODY_TOP - 0.10)

# 09 — crosswalk
s = new("The hard part", "Three bridges, each with a named failure",
        "No identifier normalisation on the match path — 7845102 and 07845102 build different keys, on purpose", 9)
plate(s, "04-crosswalk-keys.png", ML, BODY_TOP + 0.05, CW, BODY_BOT - BODY_TOP - 0.10)

# 10 — how it all resolves to one episode
s = new("The hard part", "How one episode gets assembled",
        "The episode is the hub · crosswalk_key is the one lookup table · a miss parks and comes back", 10)
plate(s, "07-episode-assembly.png", ML, BODY_TOP + 0.05, CW, BODY_BOT - BODY_TOP - 0.10)

# 11 — engine
s = new("The engine", "Evidence, dimensions, verdict, disposition",
        "A pure function of (episode, cursor) · 43 track verdicts + 7 cross-track rules · aging is never an input", 11)
plate(s, "05-verdict-engine.png", ML, BODY_TOP + 0.05, CW, BODY_BOT - BODY_TOP - 0.10)

# 12 — measured
s = new("Evidence", "Measured, not asserted", "Post-claim reconciliation", 12)
text(s, ML, BODY_TOP + 0.15, CW, 0.8,
     "The difference between a claim and a demonstration.",
     size=24, font=MONO, bold=True, space_after=0)
for n, (fig, lab, col) in enumerate([
    ("4,224", "of 4,224 oracle configurations · no threshold", SETTLED),
    ("99.6%", "crosswalk reassembly against a withheld key", SETTLED),
    ("978", "tests passing · no API key, no network", INK),
    ("0", "table scans · every hot query pins its plan", INK),
]):
    stat(s, ML + n * (cw4 + 0.38), 2.60, cw4, fig, lab, col)
text(s, ML, 4.55, CW * 0.94, 1.4,
     "**Stated, not hidden:** no authentication and no tenant isolation yet; five of the 1,354 resolvable "
     "episodes do not reproduce their intended verdict; one query-plan test fails and is reported as a "
     "failure rather than rounded to green.",
     size=13, color=INK_SOFT, spacing=1.36, space_after=0)

# 13 — demo divider
s = new("Demo", "What we built", "Post-claim reconciliation", 13)
text(s, ML, 2.55, CW, 0.3, "Demo", size=9.5, font=MONO, color=EXCEPTION,
     caps=True, track=True, space_after=0)
text(s, ML, 2.95, CW, 1.1, "What we built.", size=44, font=MONO, bold=True, space_after=0)
text(s, ML, 4.25, CW * 0.8, 1.0,
     "A React dashboard over the read API, an episode dossier, the connector fabric, "
     "and an agent layer that explains but never calculates.",
     size=17, spacing=1.3, space_after=0)

# 14 — screens, the book
s = new("Demo", "The book at a glance",
        "Every figure on screen was written by the engine and is returned verbatim", 14)
half = (CW - 0.45) / 2
plate(s, "shot-queues.png", ML, BODY_TOP + 0.05, half, BODY_BOT - BODY_TOP - 0.10,
      "Replay cursor · three dispositions, counted live")
plate(s, "shot-queue.png", ML + half + 0.45, BODY_TOP + 0.05, half, BODY_BOT - BODY_TOP - 0.10,
      "The exception queue — a query, not a table")

# 15 — screens, one episode
s = new("Demo", "One episode, then the agent",
        "The claim was denied and the rebate was paid anyway — a story no single-track view can tell", 15)
left_w = CW * 0.38
right_w = CW - left_w - 0.45
plate(s, "shot-dossier.png", ML, BODY_TOP + 0.05, left_w, BODY_BOT - BODY_TOP - 0.10,
      "E-000006 · B-10 / C-08 · X-1 cross-track")
plate(s, "shot-agent.png", ML + left_w + 0.45, BODY_TOP + 0.05, right_w, BODY_BOT - BODY_TOP - 0.10,
      "What happened · why it is open · what it could not determine")

# 16 — live
s = new("Demo", "Switching to the app", "localhost:5173 · profile demo · seed 20250701", 16)
text(s, ML, BODY_TOP + 0.20, CW, 0.3, "Live", size=9.5, font=MONO, color=EXCEPTION,
     caps=True, track=True, space_after=0)
text(s, ML, BODY_TOP + 0.58, CW, 1.1, "Let me show you.", size=44, font=MONO, bold=True, space_after=0)
for n, cue in enumerate([
    "Drag the replay cursor — the answer as it stood in March",
    "Open the exception queue, pick the largest variance",
    "Read the dossier — records, cash movements, verdict changes",
    "Ask the agent to explain it, then to decide next steps",
    "Edit the draft and commit it through the human gate",
]):
    y = 3.35 + n * 0.58
    text(s, ML, y, 0.4, 0.3, str(n + 1), size=13, font=MONO, bold=True,
         color=EXCEPTION, space_after=0)
    text(s, ML + 0.58, y - 0.02, CW - 0.58, 0.45, cue, size=14, color=INK_SOFT, space_after=0)

prs.save(OUT)
print(OUT)
print(f"{len(prs.slides.__iter__.__self__._sldIdLst)} slides")
