"""Append the 340B TPA vendor leg to the living canvas, as flow, without touching a byte of it.

``docs/images/project-overview.excalidraw`` is hand-authored and cumulative.  A previous
attempt at this exact drawing deleted 122 pre-existing elements — the oracle box,
``classify.py``, the orchestrator box, 31 arrows — while reporting that it had appended.  It
had rebuilt ``elements`` from scratch and kept only what its own filter happened to match.
The file had to be restored from ``HEAD``.

So this script is built the other way round, and the rule is enforced in code rather than
promised in a docstring:

* the existing ``elements`` list is **loaded and kept by identity**.  It is never rebuilt,
  never filtered, never sorted, never regenerated.  The only operation performed on it is
  ``.extend(new_elements)``;
* before the file is written, :func:`_verify` asserts that every pre-existing id survives,
  that no pre-existing element's ``x``, ``y`` or ``id`` moved, and — stricter than asked —
  that no pre-existing element differs in *any* field except ``boundElements``, and then only
  by having entries appended to the end.  A single arrow binding onto the existing ``engine``
  box is the whole of that exception, and it is checked to be exactly that;
* if any of it fails, the script raises and **nothing is written**.

Re-running is refused rather than made idempotent.  Idempotency here would mean "delete the
elements matching my prefix, then re-append", and deleting elements out of this file is the
precise failure this script exists to make impossible.  To redraw: ``git checkout --
docs/images/project-overview.excalidraw`` first, then run again.

**What it draws, and why it is a flow and not a stack of bands.**  An earlier revision of
this canvas carried ten labelled rectangles narrating what was built in which wave, and the
feedback was blunt: *"These are just blocks... it's barely a block diagram because you have
just written what you have done in a wave of development."*  A changelog has no components
and no direction, so it cannot answer the only question a system diagram exists to answer.
This draws the vendor leg the way ``draw_connectivity_architecture.py`` drew the connectivity
layer: nodes that exist in the source tree, joined by arrows that follow the actual call
order, in the palette the canvas already uses.

Every node below was read off the code before it was drawn:

======================================  ==========================================
node                                    source
======================================  ==========================================
the three registry rows                 ``connectors/registry.py:278-300``
``enabled=False`` on the fetch edge     ``connectors/registry.py:313`` / ``:325``
``SftpTransport.fetch``                 ``connectors/sftp.py:317``, ``:405``
``Document(name, text, modified_at)``   ``connectors/transport.py:77-97``
``_read_rows`` / ``VENDOR_CSV``         ``ingest/pipeline.py:515``, ``:566``
``vendors.mapping_for``                 ``connectors/vendors/__init__.py:666-678``
``verity.py`` / ``craneware.py``        the only two modules ``mapping_for`` merges
``vendors.read`` → ``ParsedDocument``   ``connectors/vendors/__init__.py:390``, ``:450``
``control_totals.reconcile_or_fail``    ``ingest/pipeline.py:417``; trailer at
                                        ``connectors/control_totals.py:265``
``schema_registry.check``               ``ingest/pipeline.py:713``
``_adapt_vendor_export``                ``ingest/adapters.py:726``
``_QUALIFICATION_DATASETS`` fence       ``ingest/adapters.py:723``, ``:749-754``
``TPA_QUALIFICATION`` parent            ``ingest/adapters.py:814``
``TPA_REVERSAL`` child                  ``ingest/adapters.py:836-841``
engine untouched                        nothing under ``src/recon/engine/`` imports
                                        ``recon.connectors``
======================================  ==========================================

One correction to the brief this was drawn from, made because the code says otherwise.  The
brief has ``verity_invoices`` stopping at the contract check.  It does not: it is registered
(``schema_registry.py:643``), mapped (``verity.py:172``), read by the same reader and it
*passes* the contract check.  It stops one step later, at ``_QUALIFICATION_DATASETS``, where
``_adapt_vendor_export`` raises rather than land a qualification whose status is null.  That
is where the dashed red arrow stops, because that is where the code stops.

Usage::

    python scripts/draw_vendor_leg_append.py
"""

from __future__ import annotations

import copy
import json
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

#: Every id this script creates starts with this.  Nothing on the canvas does.
PREFIX = "vl_"

#: Index prefix.  Excalidraw orders by ``index`` as a string, the canvas's maximum is
#: ``cx0097``, and ``"cy…" > "cx…"`` — so every index minted here sorts after every index
#: already on the file, which is asserted rather than assumed.
INDEX_PREFIX = "cy"

#: The existing "engine" node in the connectivity diagram, drawn by
#: ``draw_connectivity_architecture.py``.  The vendor leg binds onto it rather than drawing a
#: second engine, because there is only one engine and it did not change.
ENGINE_ID = "cx_p5r"

#: Below everything.  The canvas's current maximum y extent is 6303.5.
TOP = 6600

# ── palette ─────────────────────────────────────────────────────────────────
# Read off the canvas's own elements.  Not one colour here is new.
OCHRE = "#b45309"        # the vendor side
OCHRE_INK = "#7c2d12"
OCHRE_FILL = "#fef3c7"
RUST = "#c2410c"         # emphasis within the vendor side
BLUE = "#1e3a5f"         # the deterministic pipeline
BLUE_FILL = "#93c5fd"
GREEN = "#047857"        # checks that hold, and the records that come out
GREEN_FILL = "#a7f3d0"
RED = "#b91c1c"          # refusals and gaps
GREY = "#64748b"         # annotation
HEAD = "#1e40af"


class Sheet:
    """Accumulates NEW elements only.  It never sees the existing list."""

    def __init__(self) -> None:
        self._n = 0
        self.elements: list[dict[str, Any]] = []
        self.by_id: dict[str, dict[str, Any]] = {}
        #: ``(shape_id, arrow_id)`` pairs to write onto shapes that already exist on the
        #: canvas.  Collected rather than applied, so the one mutation of a pre-existing
        #: element happens in one place and under the verifier's eye.
        self.foreign_bindings: list[tuple[str, str]] = []

    # -- plumbing ----------------------------------------------------------
    def _index(self) -> str:
        self._n += 1
        return f"{INDEX_PREFIX}{self._n:04d}"

    def _add(self, element: dict[str, Any]) -> dict[str, Any]:
        self.elements.append(element)
        self.by_id[element["id"]] = element
        return element

    # -- nodes -------------------------------------------------------------
    def text(self, key: str, **kw: Any) -> None:
        self._add(text(f"{PREFIX}{key}", self._index(), **kw))

    def note(self, key: str, *, x: float, y: float, body: str, size: int = 12,
             colour: str = GREY) -> None:
        self.text(key, x=x, y=y, body=body, size=size, colour=colour)

    def box(self, key: str, **kw: Any) -> None:
        for element in box(f"{PREFIX}{key}", self._index(), self._index(), **kw):
            self._add(element)

    # -- arrows ------------------------------------------------------------
    def _anchor(self, node_key: str, fx: float, fy: float) -> tuple[dict[str, Any], float, float]:
        """Resolve ``(box key, fractional point)`` to a real point on that box's border."""
        element = self.by_id[f"{PREFIX}{node_key}r"]
        return element, element["x"] + element["width"] * fx, element["y"] + element["height"] * fy

    def arrow(
        self,
        key: str,
        *,
        start: tuple[str, float, float],
        end: tuple[str, float, float] | None = None,
        end_point: tuple[float, float] | None = None,
        end_foreign: tuple[str, float, float, float, float] | None = None,
        via: tuple[tuple[float, float], ...] = (),
        colour: str = BLUE,
        dashed: bool = False,
        head: str | None = "arrow",
    ) -> None:
        """A bound, orthogonally-routed arrow.

        ``startBinding``/``endBinding`` plus the matching ``boundElements`` entry on each
        shape, in this file's own binding schema (``mode`` + ``fixedPoint``, no ``focus`` or
        ``gap`` — read off the eight bound arrows the canvas already carries).  Unbound
        arrows detach the moment anybody drags a box, which on a diagram nobody re-renders is
        a slow corruption rather than an obvious one.

        ``end_foreign`` binds onto a shape that already exists on the canvas; its absolute
        endpoint is passed explicitly, because this sheet holds no geometry for it.
        """
        arrow_id = f"{PREFIX}{key}"
        start_el, sx, sy = self._anchor(*start)

        start_binding = {
            "elementId": start_el["id"],
            "mode": "orbit",
            "fixedPoint": [start[1], start[2]],
        }
        start_el["boundElements"].append({"id": arrow_id, "type": "arrow"})

        end_binding: dict[str, Any] | None = None
        if end is not None:
            end_el, ex, ey = self._anchor(*end)
            end_binding = {
                "elementId": end_el["id"],
                "mode": "orbit",
                "fixedPoint": [end[1], end[2]],
            }
            end_el["boundElements"].append({"id": arrow_id, "type": "arrow"})
        elif end_foreign is not None:
            foreign_id, fx, fy, ex, ey = end_foreign
            end_binding = {"elementId": foreign_id, "mode": "orbit", "fixedPoint": [fx, fy]}
            self.foreign_bindings.append((foreign_id, arrow_id))
        elif end_point is not None:
            ex, ey = end_point
        else:  # pragma: no cover - programmer error
            raise ValueError("arrow needs one of end / end_point / end_foreign")

        absolute = [(sx, sy), *via, (ex, ey)]
        points = [[px - sx, py - sy] for px, py in absolute]
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]

        element = _base(arrow_id, self._index(), "arrow")
        element.update(
            {
                "x": sx,
                "y": sy,
                "width": max(xs) - min(xs),
                "height": max(ys) - min(ys),
                "strokeColor": colour,
                "strokeStyle": "dashed" if dashed else "solid",
                "points": points,
                "startBinding": start_binding,
                "endBinding": end_binding,
                "startArrowhead": None,
                "endArrowhead": head,
                "roundness": None,
                "elbowed": False,
                "moveMidPointsWithElement": False,
                "updated": UPDATED,
            }
        )
        self._add(element)


# ── the drawing ─────────────────────────────────────────────────────────────

SPINE_X = 340
SPINE_W = 560
SPINE_R = SPINE_X + SPINE_W          # 900
SIDE_X = 1020                        # the right-hand column


def build() -> Sheet:
    s = Sheet()

    s.text("title", x=90, y=TOP, body="THE 340B TPA VENDOR LEG, END TO END", size=28, colour=HEAD)
    s.text(
        "sub",
        x=90,
        y=TOP + 38,
        body=(
            "two vendors, one transport, one reader, one adapter.  the only per-vendor code "
            "in the whole leg is a column map."
        ),
        size=13,
        colour=GREY,
    )

    # ── the registry rows ──────────────────────────────────────────────────
    s.note("srccap", x=90, y=TOP + 100, body="connectors/registry.py  —  the source rows")

    src_y = TOP + 124        # 6724
    s.box("va", x=90, y=src_y, width=330, height=66,
          label="verity_accumulations\nVerity SFTP  ·  verity-export-1.0.0",
          stroke=OCHRE, fill=OCHRE_FILL, ink=OCHRE_INK, size=13)
    s.box("cc", x=450, y=src_y, width=330, height=66,
          label="craneware_claims_report\nCraneware SFTP  ·  claims_report.csv",
          stroke=OCHRE, fill=OCHRE_FILL, ink=OCHRE_INK, size=13)
    s.box("vi", x=810, y=src_y, width=330, height=66,
          label="verity_invoices\nsame Verity path, same credential",
          stroke=RED, fill="transparent", ink=RED, size=13)

    # ── transport ──────────────────────────────────────────────────────────
    tr_y = TOP + 256         # 6856
    s.box("sftp", x=SPINE_X, y=tr_y, width=SPINE_W, height=66,
          label="SftpTransport.fetch(source, should_fetch)\n"
                "connectors/sftp.py  ·  ONE transport, both vendors",
          stroke=BLUE, fill=BLUE_FILL, ink=BLUE, size=13)

    s.arrow("a1", start=("va", 0.5, 1.0), end=("sftp", 0.179, 0.0),
            via=((255, src_y + 104), (440, src_y + 104)), colour=OCHRE)
    s.arrow("a2", start=("cc", 0.5, 1.0), end=("sftp", 0.491, 0.0), colour=OCHRE)
    s.arrow("a3", start=("vi", 0.5, 1.0), end=("sftp", 0.804, 0.0),
            via=((975, src_y + 104), (790, src_y + 104)), colour=RED, dashed=True)

    s.note(
        "gate",
        x=1200,
        y=src_y + 86,
        body=(
            "enabled=False  —  DOC2-016, Doc 2's own Week 3 access gate.\n"
            "registry.enabled() yields none of these rows, so fetch() is never called.\n"
            "THE GATE IS ON THIS EDGE and on nothing below it: the reader and the\n"
            "adapter are written, tested and complete."
        ),
        colour=RED,
    )

    doc_y = TOP + 362        # 6962
    s.box("doc", x=SPINE_X, y=doc_y, width=SPINE_W, height=60,
          label="Document(name, text, modified_at)",
          stroke=BLUE, fill="transparent", ink=BLUE, size=13)
    s.arrow("a4", start=("sftp", 0.5, 1.0), end=("doc", 0.5, 0.0), colour=BLUE)

    # ── the one reader ─────────────────────────────────────────────────────
    read_y = TOP + 468       # 7068
    s.box("read", x=SPINE_X, y=read_y, width=SPINE_W, height=66,
          label="_read_rows(PayloadFormat.VENDOR_CSV)\n"
                "ingest/pipeline.py:566  —  ONE reader for both vendors",
          stroke=BLUE, fill=BLUE_FILL, ink=BLUE, size=12)
    s.arrow("a5", start=("doc", 0.5, 1.0), end=("read", 0.5, 0.0), colour=BLUE)

    # the single point of difference
    s.box("mapfor", x=SIDE_X, y=read_y, width=510, height=66,
          label="vendors.mapping_for(source_id)\nverity.MAPPINGS  |  craneware.MAPPINGS",
          stroke=RUST, fill=OCHRE_FILL, ink=OCHRE_INK, size=13)
    s.arrow("a18", start=("read", 1.0, 0.41), end=("mapfor", 0.0, 0.41), colour=RUST)

    map_y = TOP + 574        # 7174
    s.box("mv", x=SIDE_X, y=map_y, width=245, height=80,
          label="verity.py\nreversal_status\nqualification_received_at",
          stroke=RUST, fill=OCHRE_FILL, ink=OCHRE_INK, size=12)
    s.box("mc", x=SIDE_X + 265, y=map_y, width=245, height=80,
          label="craneware.py\ndispense_status · reversal_date\nno arrival column",
          stroke=RUST, fill=OCHRE_FILL, ink=OCHRE_INK, size=11)
    s.arrow("a19", start=("mv", 0.5, 0.0), end=("mapfor", 0.24, 1.0), colour=RUST)
    s.arrow("a20", start=("mc", 0.5, 0.0), end=("mapfor", 0.76, 1.0), colour=RUST)
    s.note(
        "mapn",
        x=SIDE_X,
        y=map_y + 92,
        body=(
            "THE ONLY PER-VENDOR CODE IN THE LEG.  both declare STATUS_FLAG_ON_ORIGINAL_ROW,\n"
            "so the record shape is identical and only the column spellings differ."
        ),
        colour=RUST,
    )

    # ── reading the document ───────────────────────────────────────────────
    parse_y = map_y          # 7174, level with the mapping modules
    s.box("parse", x=SPINE_X, y=parse_y, width=SPINE_W, height=66,
          label="vendors.read(mapping, document)\n"
                "ParsedDocument:  rows  ·  trailers  ·  unrecognised",
          stroke=BLUE, fill="transparent", ink=BLUE, size=12)
    s.arrow("a6", start=("read", 0.5, 1.0), end=("parse", 0.5, 0.0), colour=BLUE)
    # the mapping travels down the 120px corridor between the spine and the side column
    s.arrow("a21", start=("mapfor", 0.0, 0.79), end=("parse", 1.0, 0.5),
            via=((960, read_y + 52), (960, parse_y + 33)), colour=RUST)

    # ── the control total ──────────────────────────────────────────────────
    ct_y = TOP + 680         # 7280
    s.box("ct", x=SPINE_X, y=ct_y, width=SPINE_W, height=66,
          label="control_totals.reconcile_or_fail\ntrailer record_count   vs   len(rows)",
          stroke=GREEN, fill=GREEN_FILL, ink=GREEN, size=13)
    s.arrow("a7", start=("parse", 0.5, 1.0), end=("ct", 0.5, 0.0), colour=BLUE)
    s.note(
        "ctn",
        x=SIDE_X,
        y=ct_y + 40,
        body=(
            "F2, and written BEFORE the ingest_batch row exists.  declared 500 against\n"
            "450 observed and the batch never comes into existence at all."
        ),
        colour=GREEN,
    )

    # ── landing ────────────────────────────────────────────────────────────
    raw_y = TOP + 786        # 7386
    s.box("raw", x=SPINE_X, y=raw_y, width=SPINE_W, height=66,
          label="raw_record\nstored in the VENDOR's spelling, not renamed",
          stroke=BLUE, fill=BLUE_FILL, ink=BLUE, size=13)
    s.arrow("a8", start=("ct", 0.5, 1.0), end=("raw", 0.5, 0.0), colour=GREEN)
    s.note(
        "rawn",
        x=SIDE_X,
        y=raw_y + 4,
        body=(
            "ndc_11 on Verity, ndc11 on Craneware.  the vendor's own columns survive to\n"
            "raw_record so the registered contract can be checked against the shape the\n"
            "vendor can be held to.  renaming is the adapter's job, one step later."
        ),
    )

    # ── validate before adapt ──────────────────────────────────────────────
    chk_y = TOP + 892        # 7492
    s.box("chk", x=SPINE_X, y=chk_y, width=SPINE_W, height=66,
          label="schema_registry.check(source_id, record)\n"
                "_process_raw_record:713  —  validate BEFORE adapt",
          stroke=BLUE, fill="transparent", ink=BLUE, size=12)
    s.arrow("a9", start=("raw", 0.5, 1.0), end=("chk", 0.5, 0.0), colour=BLUE)

    s.box("quar", x=SIDE_X, y=chk_y, width=430, height=66,
          label="quarantine\nSCHEMA_VERSION_MISMATCH",
          stroke=RED, fill="transparent", ink=RED, size=13)
    s.arrow("a11", start=("chk", 1.0, 0.5), end=("quar", 0.0, 0.5), colour=RED, dashed=True)

    # ── the one adapter, and its fence ─────────────────────────────────────
    ad_y = TOP + 998         # 7598
    s.box("adapt", x=SPINE_X, y=ad_y, width=SPINE_W, height=66,
          label="_adapt_vendor_export(payload)\n"
                "ingest/adapters.py:726  —  ONE adapter for both vendors",
          stroke=BLUE, fill=BLUE_FILL, ink=BLUE, size=12)
    s.arrow("a10", start=("chk", 0.5, 1.0), end=("adapt", 0.5, 0.0), colour=BLUE)

    s.box("noad", x=SIDE_X, y=ad_y - 6, width=700, height=86,
          label="verity_invoices  —  MAPPED, READABLE, AND NO ADAPTER\n"
                "adapters.py:723   _QUALIFICATION_DATASETS = {verity_accumulations, craneware_claims_report}\n"
                "it is rebate money: a REBATE_BATCH parent with REBATE_DISPENSE_LINE children,\n"
                "and the batch/line split is a decision no evidence we hold settles.",
          stroke=RED, fill="transparent", ink=RED, size=11)
    # stops short, and carries no arrowhead, because nothing arrives
    s.arrow("a12", start=("adapt", 1.0, 0.5), end_point=(SIDE_X - 40, ad_y + 33),
            colour=RED, dashed=True, head=None)

    # ── the two records ────────────────────────────────────────────────────
    rec_y = TOP + 1104       # 7704
    s.box("qual", x=SPINE_X, y=rec_y, width=300, height=66,
          label="TPA_QUALIFICATION\nparent record",
          stroke=GREEN, fill=GREEN_FILL, ink=GREEN, size=13)
    s.box("rev", x=700, y=rec_y, width=400, height=66,
          label="TPA_REVERSAL\nchild of the parent  ·  one, never two  (B3)",
          stroke=GREEN, fill="transparent", ink=GREEN, size=12)
    s.arrow("a13", start=("adapt", 0.25, 1.0), end=("qual", 0.5, 0.0), colour=GREEN)
    s.arrow("a14", start=("qual", 1.0, 0.5), end=("rev", 0.0, 0.5), colour=GREEN)
    s.note(
        "revn",
        x=700,
        y=rec_y + 74,
        body=(
            "parent.children.append(...), only when the row carries the reversal flag  ·  "
            "idempotency_key = source_id | natural key | REVERSAL"
        ),
        colour=GREEN,
    )

    # ── back into the pipeline that already existed ────────────────────────
    cross_y = TOP + 1210     # 7810
    s.box("cross", x=SPINE_X, y=cross_y, width=300, height=60,
          label="crosswalk", stroke=BLUE, fill="transparent", ink=BLUE, size=14)
    s.arrow("a15", start=("qual", 0.5, 1.0), end=("cross", 0.5, 0.0), colour=BLUE)
    s.note(
        "crossn",
        x=700,
        y=cross_y + 10,
        body=(
            "a vendor row and a feed row describing the same dispense must produce the SAME key,\n"
            "or the crosswalk silently splits one claim into two."
        ),
    )

    epi_y = TOP + 1316       # 7916
    s.box("epi", x=SPINE_X, y=epi_y, width=300, height=60,
          label="episode", stroke=BLUE, fill="transparent", ink=BLUE, size=14)
    s.arrow("a16", start=("cross", 0.5, 1.0), end=("epi", 0.5, 0.0), colour=BLUE)

    # onto the engine the connectivity diagram already drew, routed up the free corridor at
    # x=1900 and in along the gap between the pipeline row (ends y 5950) and the ground-truth
    # box (starts y 5992).
    s.arrow(
        "a17",
        start=("epi", 1.0, 0.5),
        end_foreign=(ENGINE_ID, 0.5, 1.0, 939, 5950),
        via=((1900, epi_y + 30), (1900, 5970), (939, 5970)),
        colour=BLUE,
    )
    s.note(
        "engn",
        x=SIDE_X,
        y=epi_y - 16,
        body=(
            "THE ENGINE IS UNTOUCHED.  by the time a vendor row is an episode the engine cannot\n"
            "tell which door it came through — which is why this leg is additive and not a rewrite.\n"
            "nothing under src/recon/engine/ imports recon.connectors."
        ),
        colour=BLUE,
    )

    return s


# ── the part that matters ───────────────────────────────────────────────────


def _verify(before: list[dict[str, Any]], after: list[dict[str, Any]], engine_arrows: set[str]) -> None:
    """Refuse to write unless this really was an append.

    Four assertions, in the order they would catch a destructive edit:

    1. every pre-existing id is still present;
    2. the list still *starts* with the pre-existing elements, in order and unmoved — which
       is what "appended" means and what a rebuilt-from-scratch list cannot satisfy;
    3. no pre-existing element's ``x``, ``y`` or ``id`` changed;
    4. no pre-existing element changed in any *other* field either, except ``boundElements``
       on the engine box, and there only by appending the arrows named in ``engine_arrows``.
    """
    old_ids = [str(e.get("id")) for e in before]
    new_ids = [str(e.get("id")) for e in after]

    missing = set(old_ids) - set(new_ids)
    if missing:
        raise AssertionError(
            f"{len(missing)} pre-existing element(s) would be destroyed, e.g. "
            f"{sorted(missing)[:8]}. Nothing written."
        )
    if len(after) < len(before):
        raise AssertionError(f"element count fell: {len(before)} -> {len(after)}. Nothing written.")
    if new_ids[: len(old_ids)] != old_ids:
        raise AssertionError(
            "the pre-existing elements are no longer the prefix of the list in their original "
            "order — this is a rebuild, not an append. Nothing written."
        )

    index = {str(e.get("id")): e for e in after}
    for original in before:
        identifier = str(original.get("id"))
        current = index[identifier]
        for field in ("id", "x", "y"):
            if original.get(field) != current.get(field):
                raise AssertionError(
                    f"pre-existing element {identifier!r} had its {field!r} changed "
                    f"({original.get(field)!r} -> {current.get(field)!r}). Nothing written."
                )
        for field in set(original) | set(current):
            if field == "boundElements":
                continue
            if original.get(field) != current.get(field):
                raise AssertionError(
                    f"pre-existing element {identifier!r} had {field!r} changed. Nothing written."
                )
        was = original.get("boundElements") or []
        now = current.get("boundElements") or []
        if was == now:
            continue
        if identifier != ENGINE_ID:
            raise AssertionError(
                f"pre-existing element {identifier!r} had boundElements changed, and it is not "
                f"the engine node. Nothing written."
            )
        if now[: len(was)] != was:
            raise AssertionError(
                f"{identifier!r}'s existing bindings were rewritten rather than appended to. "
                "Nothing written."
            )
        added = {entry["id"] for entry in now[len(was):]}
        if added != engine_arrows:
            raise AssertionError(
                f"{identifier!r} gained unexpected bindings {added - engine_arrows}. Nothing written."
            )


def main() -> None:
    document = json.loads(CANVAS.read_text(encoding="utf-8"))
    existing: list[dict[str, Any]] = document["elements"]
    before = copy.deepcopy(existing)
    count_before = len(existing)

    already = [e for e in existing if str(e.get("id", "")).startswith(PREFIX)]
    if already:
        sys.exit(
            f"{len(already)} element(s) with the {PREFIX!r} prefix are already on the canvas.\n"
            "This script appends and never deletes, so it refuses to run twice. To redraw:\n"
            "  git checkout -- docs/images/project-overview.excalidraw\n"
            "  python scripts/draw_vendor_leg_append.py"
        )

    sheet = build()

    taken = {str(e.get("id")) for e in existing}
    clashes = taken & {str(e["id"]) for e in sheet.elements}
    if clashes:
        sys.exit(f"id collision with the existing canvas: {sorted(clashes)}")

    max_existing_index = max(str(e.get("index") or "") for e in existing)
    min_new_index = min(str(e["index"]) for e in sheet.elements)
    if not min_new_index > max_existing_index:
        sys.exit(
            f"index {min_new_index!r} does not sort after the canvas maximum "
            f"{max_existing_index!r}; refusing to reorder the canvas."
        )

    # The one mutation of a pre-existing element: the engine box learns about the arrow that
    # now points at it.  Additive, and checked by _verify to be exactly this.
    engine_arrows: set[str] = set()
    by_id = {str(e.get("id")): e for e in existing}
    for shape_id, arrow_id in sheet.foreign_bindings:
        shape = by_id.get(shape_id)
        if shape is None:
            sys.exit(f"cannot bind to {shape_id!r}: it is not on the canvas")
        shape.setdefault("boundElements", [])
        shape["boundElements"].append({"id": arrow_id, "type": "arrow"})
        engine_arrows.add(arrow_id)

    # THE APPEND.  The existing list object, extended.  Never rebuilt, never filtered.
    existing.extend(sheet.elements)
    assert document["elements"] is existing

    _verify(before, existing, engine_arrows)

    CANVAS.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    arrows = sum(1 for e in sheet.elements if e["type"] == "arrow")
    bound = sum(
        1 for e in sheet.elements
        if e["type"] == "arrow" and e.get("startBinding") and e.get("endBinding")
    )
    print(f"appended {len(sheet.elements)} elements ({arrows} arrows, {bound} bound at both ends)")
    print(f"canvas: {count_before} -> {len(existing)}")
    print(f"indices {min_new_index}..{max(str(e['index']) for e in sheet.elements)} "
          f"(all after {max_existing_index})")
    print(f"engine node {ENGINE_ID} gained bindings: {sorted(engine_arrows)}")


if __name__ == "__main__":
    main()
