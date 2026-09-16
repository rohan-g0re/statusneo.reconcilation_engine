"""Count what the vendor layer actually produced, per record type.

Requirement M5: *"a coverage report lists every vendor record type against the count
produced, and a test fails on a type with zero rows."*

The requirement exists because "complete" is the kind of claim that decays silently.  A
formatter whose population filter quietly excludes every row still writes a file, still
parses, still passes a "did it produce output" check, and still produces nothing.  The
failure looks identical to success everywhere except in a count — so the count is the
artefact, and a test reads it.

Two things are measured, because they fail differently:

* **Record types.**  Every dataset Verity exports, every report Craneware delivers, every
  payload kind Beacon exchanges.  A zero here means a formatter is dead.
* **Archetypes.**  Doc 2's four golden claims — paid, rejected, reversed, unmatched
  (``DOC2-002`` step 5).  A zero here means the *data* cannot exercise a case the
  connector layer will later be asked to prove end to end, which wave 6 would only
  discover at the point it could no longer be fixed cheaply.

This module counts.  It decides nothing, and like every other module in this package it
never performs arithmetic on money — the only numbers here are row counts.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from recon.config import Settings
from recon.mocks import beacon_payloads, craneware_export, source as source_module, verity_export
from recon.mocks.source import VendorSource

__all__ = ["COVERAGE_FILE", "VendorCoverage", "build_report", "render_report", "write_all"]

COVERAGE_FILE = "coverage.json"

#: Doc 2's four golden-claim archetypes, in the order ``DOC2-002`` step 5 names them.
ARCHETYPES = ("paid", "rejected", "reversed", "unmatched")


@dataclass(frozen=True, slots=True)
class VendorCoverage:
    """One vendor's record types and the row count each produced."""

    vendor: str
    #: record type -> number of data rows produced (control rows excluded).
    counts: dict[str, int]

    @property
    def empty_types(self) -> list[str]:
        return sorted(name for name, count in self.counts.items() if count == 0)


def _data_row_count(text: str) -> int:
    """Rows of actual content in a rendered file.

    Deliberately not ``len(splitlines())``.  Both CSV exports carry a header and a trailer
    control row, so a naive line count reports two rows for an empty dataset — which would
    make the zero-row test that this module exists to feed permanently, quietly green.
    """
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return 0
    if lines[0].lstrip().startswith("{"):  # JSONL: every line is a record
        return len(lines)
    body = lines[1:]  # drop the CSV header
    return sum(1 for line in body if not line.startswith("TRAILER,"))


def build_report(source: VendorSource) -> dict[str, Any]:
    """Count every vendor record type and every archetype in one pass."""
    vendors = [
        VendorCoverage(
            "verity",
            {name: _data_row_count(text) for name, text in verity_export.render(source).items()},
        ),
        VendorCoverage(
            "craneware",
            {name: _data_row_count(text) for name, text in craneware_export.render(source).items()},
        ),
        VendorCoverage(
            "beacon",
            {name: _data_row_count(text) for name, text in beacon_payloads.render(source).items()},
        ),
    ]

    archetypes = source.archetype_counts
    return {
        "schema": "recon.vendor_coverage/1",
        "dispenses": len(source.dispenses),
        "rebate_batches": len(source.rebate_batches),
        "archetypes": {name: archetypes.get(name, 0) for name in ARCHETYPES},
        "vendors": {
            coverage.vendor: {
                "record_types": dict(sorted(coverage.counts.items())),
                "empty_record_types": coverage.empty_types,
            }
            for coverage in vendors
        },
        "empty_record_types": sorted(
            f"{coverage.vendor}.{name}" for coverage in vendors for name in coverage.empty_types
        ),
        "empty_archetypes": sorted(
            name for name in ARCHETYPES if archetypes.get(name, 0) == 0
        ),
    }


def render_report(report: dict[str, Any]) -> str:
    """The same counts as a table a human can read without parsing JSON."""
    lines = [
        "# Vendor layer coverage",
        "",
        f"Dispenses: {report['dispenses']}   ·   Rebate batches: {report['rebate_batches']}",
        "",
        "## Golden-claim archetypes (DOC2-002 step 5)",
        "",
        "| archetype | count |",
        "|---|---|",
    ]
    lines.extend(f"| {name} | {count} |" for name, count in report["archetypes"].items())
    lines.extend(["", "## Record types", "", "| vendor | record type | rows |", "|---|---|---|"])
    for vendor, detail in report["vendors"].items():
        for name, count in detail["record_types"].items():
            lines.append(f"| {vendor} | {name} | {count} |")

    empty = report["empty_record_types"] + report["empty_archetypes"]
    lines.extend(["", "## Completeness", ""])
    lines.append(
        "Every record type and every archetype produced at least one row."
        if not empty
        else f"**EMPTY: {', '.join(empty)}** — a formatter or a population filter is dead."
    )
    return "\n".join(lines) + "\n"


def write_all(settings: Settings) -> dict[str, Any]:
    """Render every vendor's files and the coverage report to ``vendor_dir()``.

    The single entry point for the whole data layer.  Requirement M6 says this must stand
    alone — no SFTP, no HTTP, no connector — so it takes a :class:`Settings` and writes
    files, and that is the entire contract.  Transport arrives in a later wave and only
    changes how the bytes move.
    """
    source = source_module.load_source(settings)
    verity_export.write(settings, source)
    craneware_export.write(settings, source)
    beacon_payloads.write(settings, source)

    report = build_report(source)
    vendor_dir = settings.vendor_dir()
    vendor_dir.mkdir(parents=True, exist_ok=True)
    (vendor_dir / COVERAGE_FILE).write_text(
        json.dumps(report, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline=""
    )
    (vendor_dir / "COVERAGE.md").write_text(
        render_report(report), encoding="utf-8", newline=""
    )
    return report
