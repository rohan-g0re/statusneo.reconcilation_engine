"""Merge every pitch diagram into one editable Excalidraw canvas.

Element ids are namespaced per source file and every reference that points at an
id — boundElements, containerId, startBinding/endBinding, groupIds, frameId — is
rewritten to match. Without that the merged file loads with arrows detached from
their shapes and labels floating free of their containers.

Run:  uv run python docs/pitch/diagrams/build_all_in_one.py
"""
from __future__ import annotations

import json
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
OUT = HERE / "ALL-DIAGRAMS.excalidraw"

SOURCES = [
    ("01-system-overview.excalidraw", "System overview"),
    ("02-data-generation.excalidraw", "How the test data is made"),
    ("03-episode-build.excalidraw", "One episode, arrival by arrival"),
    ("06-rebate-track-doors.excalidraw", "The 340B rebate track, door by door"),
    ("04-crosswalk-keys.excalidraw", "Which fields the system joins over"),
    ("07-episode-assembly.excalidraw", "How one episode gets assembled"),
    ("05-verdict-engine.excalidraw", "How a verdict is computed"),
]

GAP = 700          # vertical breathing room between diagrams
HEADER_DROP = 220  # space reserved above each diagram for its section header
INK = "#1e1e1e"
ACCENT = "#e03131"


def bounds(elements):
    xs0 = [e["x"] for e in elements if not e.get("isDeleted")]
    ys0 = [e["y"] for e in elements if not e.get("isDeleted")]
    xs1 = [e["x"] + e.get("width", 0) for e in elements if not e.get("isDeleted")]
    ys1 = [e["y"] + e.get("height", 0) for e in elements if not e.get("isDeleted")]
    return min(xs0), min(ys0), max(xs1), max(ys1)


def renamespace(elements, tag):
    """Prefix every id and rewrite every reference that points at one."""
    def nid(old):
        return f"{tag}_{old}" if old else old

    for e in elements:
        e["id"] = nid(e["id"])
        if e.get("containerId"):
            e["containerId"] = nid(e["containerId"])
        if e.get("frameId"):
            e["frameId"] = nid(e["frameId"])
        if e.get("groupIds"):
            e["groupIds"] = [nid(g) for g in e["groupIds"]]
        if e.get("boundElements"):
            e["boundElements"] = [{**b, "id": nid(b["id"])} for b in e["boundElements"]]
        for side in ("startBinding", "endBinding"):
            b = e.get(side)
            if isinstance(b, dict) and b.get("elementId"):
                e[side] = {**b, "elementId": nid(b["elementId"])}
    return elements


def text_el(eid, x, y, body, size, color, seed):
    lines = body.split("\n")
    return {
        "id": eid, "type": "text", "x": x, "y": y,
        "width": max(len(l) for l in lines) * size * 0.58,
        "height": len(lines) * size * 1.25,
        "angle": 0, "strokeColor": color, "backgroundColor": "transparent",
        "fillStyle": "solid", "strokeWidth": 2, "strokeStyle": "solid",
        "roughness": 1, "opacity": 100, "groupIds": [], "frameId": None,
        "roundness": None, "seed": seed, "version": 1, "versionNonce": seed + 1,
        "isDeleted": False, "boundElements": [], "updated": 1, "link": None,
        "locked": False, "text": body, "originalText": body, "fontSize": size,
        "baseFontSize": None, "fontFamily": 5, "textAlign": "left",
        "verticalAlign": "top", "containerId": None, "autoResize": False,
        "lineHeight": 1.25, "labelPosition": None,
    }


merged: list[dict] = []
cursor_y = 0.0

for n, (filename, heading) in enumerate(SOURCES, start=1):
    src = HERE / filename
    doc = json.loads(src.read_text(encoding="utf-8"))
    els = [e for e in doc["elements"] if not e.get("isDeleted")]
    renamespace(els, f"d{n}")

    x0, y0, _, y1 = bounds(els)
    dx, dy = -x0, cursor_y + HEADER_DROP - y0

    for e in els:
        e["x"] += dx
        e["y"] += dy

    merged.append(text_el(f"hdr{n}_num", 0, cursor_y, f"{n:02d}", 64, ACCENT, 900000 + n * 100))
    merged.append(text_el(f"hdr{n}_txt", 130, cursor_y + 8, heading, 52, INK, 900050 + n * 100))
    merged.extend(els)

    cursor_y += HEADER_DROP + (y1 - y0) + GAP

OUT.write_text(json.dumps({
    "type": "excalidraw",
    "version": 2,
    "source": "https://excalidraw.com",
    "elements": merged,
    "appState": {
        "gridSize": 20, "gridStep": 5, "gridModeEnabled": False,
        "viewBackgroundColor": "#ffffff", "lockedMultiSelections": {},
    },
    "files": {},
}, ensure_ascii=False, indent=0), encoding="utf-8")

print(OUT)
print(f"{len(SOURCES)} diagrams · {len(merged)} elements · canvas height {cursor_y - GAP:.0f}")
