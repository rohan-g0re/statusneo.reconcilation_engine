"""Draw the connectivity layer as a system diagram, in the existing canvas's own language.

This replaces ten bands that should never have been drawn.  Those bands were one row of
labelled rectangles per wave, each summarising *what was done in that wave* — a changelog
rendered as boxes.  A changelog is not an architecture: it has no components, no direction and
no arrows, so it cannot answer the only question a system diagram exists to answer, which is
*what talks to what, and in which direction*.

The canvas already contained the right answer at y 40-1500: "THE WHOLE PROJECT, END TO END",
99 elements, 26 of them arrows.  This draws the connectivity layer the same way and in the
same palette, positioned below it, so the two read as one document rather than two styles.

What it deliberately draws that a prettier diagram would leave out:

* **The unwired vendor leg**, as a dashed red arrow that stops short.  ``connectors/vendors/``
  is written, documented and tested, and nothing in ``src/`` imports it — which is the single
  cause behind C2, C5, E3 and F2.  Drawing it as though it connected would make the diagram a
  nicer picture and a false one.
* **The truth refusal**, as three guards meeting one crossed-out store.  That is the property
  the whole build rests on, and it had a working bypass until a review found it.

Determinism matches the rest of the repository: ids are stable, ``seed`` and ``versionNonce``
derive from the id via blake2b, ``updated`` is fixed.  Re-running produces byte-identical
output, so the diagram can be corrected and re-applied without ever duplicating.

Usage::

    python scripts/draw_connectivity_architecture.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from append_diagram_band import (  # noqa: E402
    CANVAS,
    UPDATED,
    _base,
    box,
    text,
)

#: Everything this script owns.  Also the ten changelog bands it replaces, which are matched
#: by the broader pattern below and removed.
PREFIX = "cx_"
_SUPERSEDED = re.compile(r"^(c\d+|cx)_")

#: Below the existing canvas, which ends at y ~= 4841.
TOP = 5100

# The existing diagram's palette, read off its own elements rather than invented.
OCHRE = "#b45309"  # the generation path
OCHRE_INK = "#7c2d12"
OCHRE_FILL = "#fef3c7"
RUST = "#c2410c"  # emphasis inside the generation path
BLUE = "#1e3a5f"  # the deterministic pipeline
BLUE_FILL = "#93c5fd"
GREEN = "#047857"  # proof and outcomes
GREEN_FILL = "#a7f3d0"
VIOLET = "#6d28d9"  # the surface
VIOLET_FILL = "#ddd6fe"
RED = "#b91c1c"  # refusals and gaps
GREY = "#64748b"
HEAD = "#1e40af"


class Sheet:
    """Accumulates elements and hands out stable, monotonic fractional indices."""

    def __init__(self) -> None:
        self._n = 0
        self.elements: list[dict[str, Any]] = []

    def _index(self) -> str:
        self._n += 1
        return f"cx{self._n:04d}"

    def text(self, key: str, **kw: Any) -> None:
        self.elements.append(text(f"{PREFIX}{key}", self._index(), **kw))

    def box(self, key: str, **kw: Any) -> None:
        self.elements.extend(box(f"{PREFIX}{key}", self._index(), self._index(), **kw))

    def arrow(
        self,
        key: str,
        *,
        x: float,
        y: float,
        dx: float,
        dy: float = 0,
        colour: str = OCHRE,
        dashed: bool = False,
        head: str | None = "arrow",
        bend: tuple[float, float] | None = None,
    ) -> None:
        """A straight or single-bend arrow, matching the canvas's existing arrow elements.

        No ``startBinding``/``endBinding``: every arrow already on this canvas is unbound, and
        binding would make the arrows move when a box is nudged — which is the opposite of
        what an append-only, deterministically-regenerated diagram wants.
        """
        points = [[0, 0], [bend[0], bend[1]], [dx, dy]] if bend else [[0, 0], [dx, dy]]
        element = _base(f"{PREFIX}{key}", self._index(), "arrow")
        element.update(
            {
                "x": x,
                "y": y,
                "width": abs(dx),
                "height": abs(dy),
                "strokeColor": colour,
                "strokeStyle": "dashed" if dashed else "solid",
                "points": points,
                "startBinding": None,
                "endBinding": None,
                "startArrowhead": None,
                "endArrowhead": head,
                "roundness": None,
                "updated": UPDATED,
            }
        )
        self.elements.append(element)

    def label(self, key: str, *, x: float, y: float, body: str, size: int = 12,
              colour: str = GREY) -> None:
        self.text(key, x=x, y=y, body=body, size=size, colour=colour)


def build() -> list[dict[str, Any]]:
    s = Sheet()
    y = TOP

    s.text("title", x=90, y=y, body="THE CONNECTIVITY LAYER, END TO END", size=28, colour=HEAD)
    s.text(
        "sub",
        x=90,
        y=y + 38,
        body=(
            "Doc 2 steps 1-5.  Everything left of the seam is new; everything right of it is the "
            "pipeline that already existed and did not change."
        ),
        size=13,
        colour=GREY,
    )

    # ── 1. the vendors, and the fact that none of them is real ──────────────
    row = y + 90
    s.text("s1", x=90, y=row, body="1  ·  THE VENDOR SIDE", size=15, colour=HEAD)
    s.label("s1n", x=330, y=row + 3, body="not one of these is a vendor system. every one is a local mock.")

    vend = row + 34
    for i, (key, name) in enumerate(
        [("v1", "Verity\nSFTP export"), ("v2", "Craneware\nSFTP export"), ("v3", "Beacon\nREST API")]
    ):
        s.box(key, x=90 + i * 250, y=vend, width=220, height=62,
              label=name, stroke=RED, fill="transparent", ink=RED, size=13)

    mock = vend + 110
    for i, (key, name) in enumerate(
        [("m1", "mocks/sftp_server.py\nreal paramiko SSH-2.0 on loopback"),
         ("m2", "mocks/beacon_server.py\nstdlib http.server · seeded · no clock")]
    ):
        s.box(key, x=90 + i * 480, y=mock, width=460, height=62,
              label=name, stroke=OCHRE, fill=OCHRE_FILL, ink=OCHRE_INK, size=13)

    for i, key in enumerate(["a1", "a2", "a3"]):
        s.arrow(key, x=200 + i * 250, y=vend + 62, dx=0, dy=48, colour=RED, dashed=True)
    s.label("mn", x=1070, y=mock + 20,
            body="the mocks REPLAY what the generator already decided.\nneither one adjudicates anything.")

    # ── 2. the fabric ───────────────────────────────────────────────────────
    row = mock + 110
    s.text("s2", x=90, y=row, body="2  ·  THE CONNECTOR FABRIC", size=15, colour=HEAD)
    s.label("s2n", x=400, y=row + 3, body="src/recon/connectors/  —  how to obtain a document, and whether we are allowed to")

    fab = row + 34
    fabric = [
        ("f1", "registry.py\nA1 · sources are data", 215),
        ("f2", "credentials.py\nA3 · env, never config", 215),
        ("f3", "transport.py\nA2 · the protocol", 195),
        ("f4", "schema_registry.py\nB1 · validate BEFORE adapt", 245),
        ("f5", "checkpoint.py\nA4/A5 · name + mtime", 215),
        ("f6", "control_totals.py\nF2 · declared vs observed", 235),
        ("f7", "authority.py\nE5 · who may set what", 215),
    ]
    x = 90
    for key, label, w in fabric:
        s.box(key, x=x, y=fab, width=w, height=62, label=label,
              stroke=BLUE, fill=BLUE_FILL, ink=BLUE, size=12)
        x += w + 16

    tr = fab + 96
    for i, (key, name, w) in enumerate(
        [("t1", "LocalDirectoryTransport", 250), ("t2", "SftpTransport", 230), ("t3", "HttpApiTransport", 240)]
    ):
        s.box(key, x=90 + i * 266, y=tr, width=w, height=50, label=name,
              stroke=BLUE, fill="transparent", ink=BLUE, size=12)
    s.arrow("a4", x=285, y=fab + 62, dx=0, dy=34, colour=BLUE)
    s.label("trn", x=900, y=tr + 14,
            body="one fetch() returning Documents.  a Document carries text and a name and no filesystem handle,\n"
                 "which is what keeps a transport from being a general file opener.")

    # arrows from the mocks down into the transports
    s.arrow("a5", x=320, y=mock + 62, dx=0, dy=48, colour=OCHRE)
    s.arrow("a6", x=800, y=mock + 62, dx=0, dy=48, colour=OCHRE)

    # ── 3. the seam ─────────────────────────────────────────────────────────
    row = tr + 104
    s.text("s3", x=90, y=row, body="3  ·  THE SEAM", size=15, colour=HEAD)
    s.label("s3n", x=260, y=row + 3, body="the one genuine refactor in the whole build")

    seam = row + 34
    s.box("sm", x=90, y=seam, width=430, height=74,
          label="load_from_sources(conn, sources)\nthe source is the parameter, not the directory",
          stroke=RUST, fill=OCHRE_FILL, ink=OCHRE_INK, size=13)
    s.box("lf", x=540, y=seam, width=380, height=74,
          label="load_feeds(conn, feeds_dir)\na thin wrapper · every existing caller untouched",
          stroke=GREY, fill="transparent", ink=GREY, size=12)
    s.arrow("a7", x=540, y=seam + 37, dx=-20, dy=0, colour=GREY)
    s.arrow("a8", x=305, y=tr + 50, dx=0, dy=34, colour=BLUE)

    # the unwired leg — drawn because it is true
    s.box("vw", x=1000, y=seam - 8, width=420, height=90,
          label="connectors/vendors/  —  verity · craneware · beacon\n"
                "mappings, contracts and adapters, all written and tested\n"
                "NOTHING IN src/ IMPORTS THEM",
          stroke=RED, fill="transparent", ink=RED, size=12)
    s.arrow("a9", x=1000, y=seam + 37, dx=-60, dy=0, colour=RED, dashed=True, head=None)
    s.label("vwn", x=1000, y=seam + 92,
            body="this gap is the single cause behind C2, C5, E3 and F2.\n"
                 "0 BEACON_ID rows and 0 PAYMENT_REFERENCE rows in any database this build produces.")

    # ── 4. the pipeline that did not change ─────────────────────────────────
    row = seam + 150
    s.text("s4", x=90, y=row, body="4  ·  THE PIPELINE THAT DID NOT CHANGE", size=15, colour=HEAD)
    s.label("s4n", x=560, y=row + 3,
            body="a row is a row regardless of how it arrived — the payoff of the original event-driven decision")

    pipe = row + 34
    stages = [("p1", "raw_record", 180), ("p2", "ingest", 150), ("p3", "normalize", 170),
              ("p4", "crosswalk", 170), ("p5", "engine", 150), ("p6", "verdict + ledger", 210)]
    x = 90
    for i, (key, name, w) in enumerate(stages):
        s.box(key, x=x, y=pipe, width=w, height=54, label=name,
              stroke=BLUE, fill=BLUE_FILL if i < 2 else "transparent", ink=BLUE, size=13)
        if i:
            s.arrow(f"ap{i}", x=x - 26, y=pipe + 27, dx=22, dy=0, colour=BLUE)
        x += w + 26
    s.arrow("a10", x=180, y=seam + 74, dx=0, dy=pipe - seam - 74, colour=RUST)

    # ── 5. ground truth, refused ────────────────────────────────────────────
    gt = pipe + 96
    s.box("gt", x=1420, y=gt, width=420, height=62,
          label="truth/ground_truth.json   —   WITHHELD\nno transport may resolve a path under it",
          stroke=RED, fill="transparent", ink=RED, size=13)
    s.text("gx", x=1330, y=gt + 14, body="✕", size=28, colour=RED)
    s.arrow("a11", x=1180, y=gt + 30, dx=140, dy=0, colour=RED, dashed=True, head=None)
    s.label("gtn", x=1420, y=gt + 70,
            body="three transports, three copies of the guard,\nand they disagreed: /truth refused, /TRUTH\n"
                 "fetched over real SSH.  now one _names_truth(),\ncasefolded, and checked twice.")

    # ── 6. the surface ──────────────────────────────────────────────────────
    row = gt + 130
    s.text("s5", x=90, y=row, body="5  ·  THE SURFACE", size=15, colour=HEAD)
    s.label("s5n", x=330, y=row + 3, body="projections only — nothing here calculates")

    surf = row + 34
    s.box("r1", x=90, y=surf, width=300, height=62,
          label="readiness.py  ·  F3\nevery cell DERIVED, none typed in",
          stroke=VIOLET, fill=VIOLET_FILL, ink=VIOLET, size=12)
    s.box("r2", x=420, y=surf, width=260, height=62,
          label="GET /api/connectivity\nbuilt per request, never cached",
          stroke=VIOLET, fill="transparent", ink=VIOLET, size=12)
    s.box("r3", x=710, y=surf, width=280, height=62,
          label="Connectivity view\n14 sources · gate evidence",
          stroke=VIOLET, fill="transparent", ink=VIOLET, size=12)
    s.arrow("a12", x=390, y=surf + 31, dx=22, dy=0, colour=VIOLET)
    s.arrow("a13", x=680, y=surf + 31, dx=22, dy=0, colour=VIOLET)
    s.arrow("a14", x=240, y=fab + 62, dx=0, dy=surf - fab - 62, colour=VIOLET, dashed=True)

    s.box("gate", x=1050, y=surf, width=340, height=62,
          label="declared 0 · connector-ready 14\nworking connection 0 · production 0",
          stroke=GREEN, fill=GREEN_FILL, ink=GREEN, size=12)
    s.arrow("a15", x=990, y=surf + 31, dx=52, dy=0, colour=GREEN)
    s.label("gaten", x=1050, y=surf + 70,
            body="three of the four rungs read zero, so connector-ready\ncannot be misread as connected.")

    # ── the closing line ────────────────────────────────────────────────────
    end = surf + 130
    s.text(
        "close",
        x=90,
        y=end,
        body=(
            "THE HONEST SHAPE  —  the fabric is real and wired; the vendor adapters are real and are not.  "
            "connector-ready is Doc 2 steps 1-5 against a mock, and that is exactly what this draws."
        ),
        size=14,
        colour=RED,
    )
    return s.elements


def main() -> None:
    canvas = json.loads(CANVAS.read_text(encoding="utf-8"))
    before = len(canvas["elements"])
    kept = [e for e in canvas["elements"] if not _SUPERSEDED.match(e.get("id", ""))]
    dropped = before - len(kept)
    fresh = build()
    canvas["elements"] = kept + fresh
    CANVAS.write_text(json.dumps(canvas, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    arrows = sum(1 for e in fresh if e["type"] == "arrow")
    print(f"removed {dropped} superseded elements (the ten changelog bands)")
    print(f"drew {len(fresh)} elements, {arrows} of them arrows")
    print(f"canvas: {len(kept)} kept + {len(fresh)} new = {len(canvas['elements'])}")


if __name__ == "__main__":
    main()
