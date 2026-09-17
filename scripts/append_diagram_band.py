"""Append one connectivity-layer band to the living Excalidraw canvas.

``docs/images/project-overview.excalidraw`` is hand-authored and already holds 271
elements across three regions: the user's 340B ecosystem sketch at negative *y*, the clean
"THE WHOLE PROJECT, END TO END" diagram at *y* 40-1500, and working copies below it.  The
connectivity layer appends **below all of it**, one band per wave.

The rule this script exists to enforce is *append, never redraw*.  Every existing element
keeps its id, its coordinates and its ``versionNonce``; a band is added by writing new
elements after them, never by regenerating the file.  Hand-editing a 271-element JSON
document eight times is how that rule gets broken by accident.

Re-running a band is safe.  Every element a band creates is prefixed with that band's key
(``c0_``, ``c1_``, ...), and the script deletes any element carrying its own prefix before
appending, so a band can be corrected and re-applied without ever duplicating.

Determinism matches the rest of the repository: ``seed`` and ``versionNonce`` are derived
from the element id with ``blake2b``, and ``updated`` is a fixed constant.  No wall-clock
leaks into the canvas, so re-running a band produces byte-identical output.

Usage::

    python scripts/append_diagram_band.py --wave 0
    python scripts/append_diagram_band.py --list
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

#: Any element this script has ever created, whichever wave made it.
_BAND_ID = re.compile(r"^c\d+_")

REPO_ROOT = Path(__file__).resolve().parents[1]
CANVAS = REPO_ROOT / "docs" / "images" / "project-overview.excalidraw"

#: Fixed, so a re-run is byte-identical.  The value matches the existing elements' stamp.
UPDATED = 1789568626194

#: Where the connectivity layer starts.  The existing canvas ends at y ~= 4841.
BAND_ORIGIN_Y = 5100
BAND_STEP_Y = 210
LEFT_X = 90

# Palette, lifted from the bands already on the canvas so the new region does not look
# like it was drawn by a different hand.
INK_HEADING = "#1e40af"
INK_BODY = "#334155"
BOX_STROKE = "#0369a1"
BOX_FILL = "#e0f2fe"
BOX_INK = "#0c4a6e"
EVIDENCE_STROKE = "#b45309"
EVIDENCE_FILL = "#fef3c7"
EVIDENCE_INK = "#7c2d12"
MOCK_STROKE = "#7e22ce"
MOCK_FILL = "#f3e8ff"
MOCK_INK = "#581c87"


def _derive(identifier: str, salt: str) -> int:
    """A stable pseudo-random int for ``seed`` / ``versionNonce``.

    ``blake2b`` rather than the builtin ``hash()``, for the same reason
    ``recon.rng.derive_seed`` does it: ``hash()`` is salted per process by
    ``PYTHONHASHSEED`` and would make every re-run produce a different file.
    """
    digest = hashlib.blake2b(f"{salt}:{identifier}".encode("utf-8"), digest_size=4)
    return int.from_bytes(digest.digest(), "big") % 2_000_000_000


def _base(identifier: str, index: str, kind: str) -> dict[str, Any]:
    return {
        "type": kind,
        "id": identifier,
        "angle": 0,
        "seed": _derive(identifier, "seed"),
        "version": 1,
        "versionNonce": _derive(identifier, "nonce"),
        "isDeleted": False,
        "groupIds": [],
        "boundElements": [],
        "link": None,
        "locked": False,
        "index": index,
        "frameId": None,
        "updated": UPDATED,
        "created": None,
        "fillStyle": "solid",
        "strokeWidth": 2,
        "strokeStyle": "solid",
        "roughness": 0,
        "opacity": 100,
        "backgroundColor": "transparent",
    }


def text(
    identifier: str,
    index: str,
    *,
    x: float,
    y: float,
    body: str,
    size: int = 16,
    colour: str = INK_BODY,
    align: str = "left",
    width: float | None = None,
) -> dict[str, Any]:
    lines = body.split("\n")
    element = _base(identifier, index, "text")
    element.update(
        {
            "x": x,
            "y": y,
            "width": width if width is not None else max(len(line) for line in lines) * size * 0.58,
            "height": len(lines) * size * 1.25,
            "text": body,
            "originalText": body,
            "fontSize": size,
            "fontFamily": 3,
            "textAlign": align,
            "verticalAlign": "top",
            "strokeColor": colour,
            "strokeWidth": 1,
            "containerId": None,
            "lineHeight": 1.25,
            "roundness": None,
            "autoResize": True,
            "labelPosition": None,
            "baseFontSize": None,
        }
    )
    return element


def box(
    identifier: str,
    index_rect: str,
    index_label: str,
    *,
    x: float,
    y: float,
    width: float,
    height: float,
    label: str,
    stroke: str = BOX_STROKE,
    fill: str = BOX_FILL,
    ink: str = BOX_INK,
    size: int = 15,
) -> list[dict[str, Any]]:
    """A labelled rectangle, matching the bound-text pattern the canvas already uses."""
    rect_id = f"{identifier}r"
    label_id = f"{identifier}t"
    rect = _base(rect_id, index_rect, "rectangle")
    rect.update(
        {
            "x": x,
            "y": y,
            "width": width,
            "height": height,
            "strokeColor": stroke,
            "backgroundColor": fill,
            "roundness": {"type": 3},
            "boundElements": [{"id": label_id, "type": "text"}],
        }
    )
    lines = label.split("\n")
    caption = _base(label_id, index_label, "text")
    caption.update(
        {
            "x": x + 10,
            "y": y + (height - len(lines) * size * 1.25) / 2,
            "width": width - 20,
            "height": len(lines) * size * 1.25,
            "text": label,
            "originalText": label,
            "fontSize": size,
            "fontFamily": 3,
            "textAlign": "center",
            "verticalAlign": "middle",
            "strokeColor": ink,
            "strokeWidth": 1,
            "containerId": rect_id,
            "lineHeight": 1.25,
            "roundness": None,
            "autoResize": True,
            "labelPosition": None,
            "baseFontSize": None,
        }
    )
    return [rect, caption]


class Band:
    """Accumulates elements for one wave and hands out stable fractional indices.

    The index scheme is derived from the **wave number**, never from whatever happens to be
    on the canvas at the time.  An earlier version took the base from the maximum index over
    every kept element — which included *later* bands, so re-rendering band 1 while band 2
    existed gave band 1 indices after band 2's, reshuffling z-order and rewriting thirty
    lines for no visual change.  Worse, it never converged: each alternating re-run grew the
    index strings again.

    Keying on the wave makes ``--wave N`` a pure function of N and the band definition, which
    is what "re-running a band is byte-identical" has to mean if it is worth claiming.
    """

    def __init__(self, prefix: str, wave: int, base_index: str) -> None:
        self.prefix = prefix
        self._wave = wave
        self._base_index = base_index
        self._n = 0
        self.elements: list[dict[str, Any]] = []

    def next_index(self) -> str:
        # Appending to the pre-band maximum keeps every band after the original canvas; the
        # zero-padded wave number keeps the bands ordered among themselves.
        self._n += 1
        return f"{self._base_index}z{self._wave:02d}{self._n:04d}"

    def text(self, name: str, **kwargs: Any) -> None:
        self.elements.append(text(f"{self.prefix}{name}", self.next_index(), **kwargs))

    def box(self, name: str, **kwargs: Any) -> None:
        self.elements.extend(
            box(f"{self.prefix}{name}", self.next_index(), self.next_index(), **kwargs)
        )


# ───────────────────────────── band definitions ─────────────────────────────


def band_wave_0(b: Band, y: int) -> None:
    """Wave 0 also plants the section heading for the whole connectivity layer."""
    b.text(
        "title",
        x=LEFT_X,
        y=y - 70,
        body="6  ·  CONNECT TO REAL SOURCES",
        size=32,
        colour=INK_HEADING,
    )
    b.text(
        "sub",
        x=LEFT_X,
        y=y - 28,
        body="the connectivity layer  —  Assignment Doc 2, steps 1-5.  Connector-ready, not production-ready.",
        size=15,
    )

    b.text("h", x=LEFT_X, y=y, body="WAVE 0  ·  EVIDENCE", size=20, colour=INK_HEADING)
    b.text(
        "n",
        x=LEFT_X,
        y=y + 28,
        body="a vendor with no evidence file has no code  —  the gate, not a guideline",
        size=13,
    )

    b.box(
        "d2",
        x=LEFT_X,
        y=y + 56,
        width=250,
        height=74,
        label="assignment_doc2.md\n17 claims · page-cited",
        stroke=EVIDENCE_STROKE,
        fill=EVIDENCE_FILL,
        ink=EVIDENCE_INK,
    )
    b.box(
        "bea",
        x=LEFT_X + 270,
        y=y + 56,
        width=250,
        height=74,
        label="beacon.md\n7 UNAVAILABLE · 403 wall",
        stroke=EVIDENCE_STROKE,
        fill=EVIDENCE_FILL,
        ink=EVIDENCE_INK,
    )
    b.box(
        "ver",
        x=LEFT_X + 540,
        y=y + 56,
        width=250,
        height=74,
        label="verity.md\n7 RETRIEVED first-hand",
        stroke=EVIDENCE_STROKE,
        fill=EVIDENCE_FILL,
        ink=EVIDENCE_INK,
    )
    b.box(
        "cra",
        x=LEFT_X + 810,
        y=y + 56,
        width=250,
        height=74,
        label="craneware.md\nSFTP + 5 reports confirmed",
        stroke=EVIDENCE_STROKE,
        fill=EVIDENCE_FILL,
        ink=EVIDENCE_INK,
    )
    b.box(
        "std",
        x=LEFT_X + 1080,
        y=y + 56,
        width=250,
        height=74,
        label="standards.md\nX12 · NCPDP · NACHA",
        stroke=EVIDENCE_STROKE,
        fill=EVIDENCE_FILL,
        ink=EVIDENCE_INK,
    )
    b.box(
        "idx",
        x=LEFT_X + 1350,
        y=y + 56,
        width=270,
        height=74,
        label="index.jsonl  →  47 rows\ntest walks it · delete one → red",
        stroke="#be123c",
        fill="#ffe4e6",
        ink="#881337",
    )
    b.text(
        "f",
        x=LEFT_X,
        y=y + 140,
        body="SPEC = cited vendor source   ·   STANDARD = public standard   ·   INVENTED = ours, and declared as ours",
        size=13,
        colour="#881337",
    )


def band_wave_1(b: Band, y: int) -> None:
    b.text("h", x=LEFT_X, y=y, body="WAVE 1  ·  DATA LAYER", size=20, colour=INK_HEADING)
    b.text(
        "n",
        x=LEFT_X,
        y=y + 28,
        body="built first  —  formatters, never generators, so the blind-slice guarantee survives",
        size=13,
    )

    b.box(
        "orch",
        x=LEFT_X,
        y=y + 56,
        width=300,
        height=74,
        label="orchestrator mints\nbeacon_id · accumulation_id · invoice_number",
        stroke="#b45309",
        fill=EVIDENCE_FILL,
        ink=EVIDENCE_INK,
        size=13,
    )
    b.box(
        "side",
        x=LEFT_X + 320,
        y=y + 56,
        width=280,
        height=74,
        label="vendor/identifiers.jsonl\nsidecar · NOT a seventh feed",
        stroke="#be123c",
        fill="#ffe4e6",
        ink="#881337",
        size=13,
    )
    b.box(
        "ver",
        x=LEFT_X + 620,
        y=y + 56,
        width=250,
        height=74,
        label="verity_export\n5 datasets · CSV",
        stroke=MOCK_STROKE,
        fill=MOCK_FILL,
        ink=MOCK_INK,
    )
    b.box(
        "cra",
        x=LEFT_X + 890,
        y=y + 56,
        width=250,
        height=74,
        label="craneware_export\n5 reports · CSV",
        stroke=MOCK_STROKE,
        fill=MOCK_FILL,
        ink=MOCK_INK,
    )
    b.box(
        "bea",
        x=LEFT_X + 1160,
        y=y + 56,
        width=250,
        height=74,
        label="beacon_payloads\n5 kinds · JSONL",
        stroke=MOCK_STROKE,
        fill=MOCK_FILL,
        ink=MOCK_INK,
    )
    b.box(
        "cov",
        x=LEFT_X + 1430,
        y=y + 56,
        width=250,
        height=74,
        label="coverage report\n15 types · 0 empty",
        stroke=BOX_STROKE,
        fill=BOX_FILL,
        ink=BOX_INK,
    )
    b.text(
        "f",
        x=LEFT_X,
        y=y + 140,
        body="the six feeds stay BYTE-IDENTICAL  —  proven by regenerating from the previous commit and diffing every hash",
        size=13,
        colour="#881337",
    )


def band_wave_2(b: Band, y: int) -> None:
    b.text("h", x=LEFT_X, y=y, body="WAVE 2  ·  FRAMEWORK", size=20, colour=INK_HEADING)
    b.text(
        "n",
        x=LEFT_X,
        y=y + 28,
        body="the one genuine refactor  —  load_feeds grows a transport seam, and nothing below it moves",
        size=13,
    )

    b.box(
        "reg",
        x=LEFT_X,
        y=y + 56,
        width=250,
        height=74,
        label="registry\nsources as data (A1)",
        stroke=BOX_STROKE,
        fill=BOX_FILL,
        ink=BOX_INK,
    )
    b.box(
        "tr",
        x=LEFT_X + 270,
        y=y + 56,
        width=250,
        height=74,
        label="Transport protocol\nDocument: name + text only",
        stroke=BOX_STROKE,
        fill=BOX_FILL,
        ink=BOX_INK,
    )
    b.box(
        "loc",
        x=LEFT_X + 540,
        y=y + 56,
        width=250,
        height=74,
        label="LocalDirectory\ntoday's behaviour, named",
        stroke=BOX_STROKE,
        fill=BOX_FILL,
        ink=BOX_INK,
    )
    b.box(
        "cred",
        x=LEFT_X + 810,
        y=y + 56,
        width=250,
        height=74,
        label="credentials (A3)\nSecret redacts · env / file",
        stroke="#be123c",
        fill="#ffe4e6",
        ink="#881337",
    )
    b.box(
        "sch",
        x=LEFT_X + 1080,
        y=y + 56,
        width=250,
        height=74,
        label="schema registry (B1)\nSCHEMA_VERSION_MISMATCH",
        stroke=BOX_STROKE,
        fill=BOX_FILL,
        ink=BOX_INK,
    )
    b.box(
        "con",
        x=LEFT_X + 1350,
        y=y + 56,
        width=270,
        height=74,
        label="contracts/ (B2)\nfield table checked vs code",
        stroke=EVIDENCE_STROKE,
        fill=EVIDENCE_FILL,
        ink=EVIDENCE_INK,
    )
    b.text(
        "f",
        x=LEFT_X,
        y=y + 140,
        body="the existing 585 tests pass UNEDITED  —  if one had needed changing, the seam was cut in the wrong place",
        size=13,
        colour="#881337",
    )


def band_wave_3(b: Band, y: int) -> None:
    b.text("h", x=LEFT_X, y=y, body="WAVE 3  ·  FILE PATTERN", size=20, colour=INK_HEADING)
    b.text(
        "n",
        x=LEFT_X,
        y=y + 28,
        body="real SFTP on loopback  —  prove the fabric on the boring transport first",
        size=13,
    )

    b.box(
        "srv",
        x=LEFT_X,
        y=y + 56,
        width=260,
        height=74,
        label="loopback SFTP server\nEd25519 · deterministic key",
        stroke=MOCK_STROKE,
        fill=MOCK_FILL,
        ink=MOCK_INK,
        size=13,
    )
    b.box(
        "tr",
        x=LEFT_X + 280,
        y=y + 56,
        width=260,
        height=74,
        label="SftpTransport (A2)\nlist · filter · download",
        stroke=BOX_STROKE,
        fill=BOX_FILL,
        ink=BOX_INK,
        size=13,
    )
    b.box(
        "ck",
        x=LEFT_X + 560,
        y=y + 56,
        width=280,
        height=74,
        label="checkpoint (A4 · A5)\n2nd run = ZERO BYTES",
        stroke="#be123c",
        fill="#ffe4e6",
        ink="#881337",
        size=13,
    )
    b.box(
        "ver",
        x=LEFT_X + 860,
        y=y + 56,
        width=250,
        height=74,
        label="verity mapping\nSTATUS_FLAG_ROW",
        stroke=EVIDENCE_STROKE,
        fill=EVIDENCE_FILL,
        ink=EVIDENCE_INK,
        size=13,
    )
    b.box(
        "cra",
        x=LEFT_X + 1130,
        y=y + 56,
        width=250,
        height=74,
        label="craneware mapping\nFLAG_ON_ORIGINAL_ROW",
        stroke=EVIDENCE_STROKE,
        fill=EVIDENCE_FILL,
        ink=EVIDENCE_INK,
        size=12,
    )
    b.box(
        "b3",
        x=LEFT_X + 1400,
        y=y + 56,
        width=250,
        height=74,
        label="B3: net effect = 1\nnot zero, not two",
        stroke="#be123c",
        fill="#ffe4e6",
        ink="#881337",
        size=13,
    )
    b.text(
        "f",
        x=LEFT_X,
        y=y + 140,
        body="tpa.py writes a NEGATIVE ROW upstream; both exports write a FLAG  —  the B3 hazard is live here, not theoretical",
        size=13,
        colour="#881337",
    )


def band_wave_4(b: Band, y: int) -> None:
    b.text("h", x=LEFT_X, y=y, body="WAVE 4  ·  API PATTERN", size=20, colour=INK_HEADING)
    b.text(
        "n",
        x=LEFT_X,
        y=y + 28,
        body="Beacon  —  the only bidirectional connector in the whole assessment",
        size=13,
    )

    b.box(
        "srv",
        x=LEFT_X,
        y=y + 56,
        width=260,
        height=74,
        label="beacon_server\nstdlib http · seeded",
        stroke=MOCK_STROKE,
        fill=MOCK_FILL,
        ink=MOCK_INK,
        size=13,
    )
    b.box(
        "http",
        x=LEFT_X + 280,
        y=y + 56,
        width=270,
        height=74,
        label="HttpApiTransport (A2)\nAccess + Private token",
        stroke=BOX_STROKE,
        fill=BOX_FILL,
        ink=BOX_INK,
        size=13,
    )
    b.box(
        "out",
        x=LEFT_X + 570,
        y=y + 56,
        width=250,
        height=74,
        label="outbound (C2)\nclaim → Beacon ID",
        stroke=BOX_STROKE,
        fill=BOX_FILL,
        ink=BOX_INK,
        size=13,
    )
    b.box(
        "in",
        x=LEFT_X + 840,
        y=y + 56,
        width=250,
        height=74,
        label="inbound (C3)\nreason kept VERBATIM",
        stroke=BOX_STROKE,
        fill=BOX_FILL,
        ink=BOX_INK,
        size=13,
    )
    b.box(
        "mode",
        x=LEFT_X + 1110,
        y=y + 56,
        width=250,
        height=74,
        label="mode A / mode B (C4)\nB ⇒ outbound UNREACHABLE",
        stroke="#be123c",
        fill="#ffe4e6",
        ink="#881337",
        size=12,
    )
    b.box(
        "key",
        x=LEFT_X + 1380,
        y=y + 56,
        width=250,
        height=74,
        label="KeyType.BEACON_ID (C5)\njoins episode → rebate → cash",
        stroke=EVIDENCE_STROKE,
        fill=EVIDENCE_FILL,
        ink=EVIDENCE_INK,
        size=12,
    )
    b.text(
        "f",
        x=LEFT_X,
        y=y + 140,
        body="VERITY-004 is first-hand: a shipping TPA offers the covered entity exactly this A/B choice  —  so C4 guards a real double submission",
        size=13,
        colour="#881337",
    )


BANDS = {
    0: ("c0_", band_wave_0),
    1: ("c1_", band_wave_1),
    2: ("c2_", band_wave_2),
    3: ("c3_", band_wave_3),
    4: ("c4_", band_wave_4),
}


def apply_band(wave: int) -> None:
    if wave not in BANDS:
        sys.exit(f"no band defined for wave {wave}; defined: {sorted(BANDS)}")
    prefix, builder = BANDS[wave]

    assert CANVAS.exists(), (
        f"{CANVAS} is missing. It is the living architecture canvas, appended to once per "
        "wave; restore it from git rather than regenerating it."
    )
    document = json.loads(CANVAS.read_text(encoding="utf-8"))
    elements: list[dict[str, Any]] = document["elements"]

    kept = [el for el in elements if not str(el.get("id", "")).startswith(prefix)]
    replaced = len(elements) - len(kept)

    # The base is the maximum index over the *original* canvas only — every element not
    # created by any band.  Measuring against ``kept`` would fold later bands into the base
    # and make this band's indices depend on which other bands happen to exist, which is
    # exactly the non-determinism this scheme exists to remove.
    original = [el for el in kept if not _BAND_ID.match(str(el.get("id", "")))]
    base_index = max(str(el.get("index") or "") for el in original)

    band = Band(prefix, wave, base_index)
    builder(band, BAND_ORIGIN_Y + wave * BAND_STEP_Y)

    # Sorted by index rather than simply appended.  Stable indices are only half of
    # idempotency: appending puts a re-rendered band at the end of the array, so the file
    # changed even when every element in it was identical.  Sorting makes the array order a
    # pure function of the indices, which the scheme above already made a pure function of
    # the wave.  ``index`` is Excalidraw's own ordering key, so this is the order it would
    # have applied anyway.
    document["elements"] = sorted(
        kept + band.elements, key=lambda el: str(el.get("index") or "")
    )
    CANVAS.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    verb = "replaced" if replaced else "appended"
    print(f"wave {wave}: {verb} band {prefix!r} — {len(band.elements)} elements")
    print(f"  canvas: {len(kept)} kept + {len(band.elements)} new = {len(document['elements'])}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--wave", type=int, help="wave number whose band to append")
    parser.add_argument("--list", action="store_true", help="list defined bands")
    args = parser.parse_args()

    if args.list:
        for wave, (prefix, _) in sorted(BANDS.items()):
            print(f"wave {wave}: prefix {prefix!r}")
        return
    if args.wave is None:
        parser.error("pass --wave N or --list")
    apply_band(args.wave)


if __name__ == "__main__":
    main()
