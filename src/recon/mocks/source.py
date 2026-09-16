"""Read what the generators already produced, and hand it to the vendor formatters.

This is the only module in ``recon.mocks`` that touches the filesystem.  Every formatter
takes a :class:`VendorSource` and returns text; none of them opens a file, and none of them
knows where the data came from.

**What it reads:** the six feed files, verbatim, plus the identifier sidecar the
orchestrator writes to ``data/generated/<profile>/vendor/identifiers.jsonl``.

**What it deliberately does not read:** ``truth/ground_truth.json``.  The prohibition is the
same one ``load_feeds`` holds structurally — this module takes a :class:`Settings` and uses
only ``feeds_dir()`` and ``vendor_identifiers_path()``, never ``truth_dir()`` — and
``tests/test_mocks.py`` asserts it by scanning the source text, because a commented-out
reference would be a crack the prohibition is supposed to have none of.

**The join.**  A 340B dispense appears in ``tpa_340b_events.jsonl`` as up to four events
(qualification, rebate request, manufacturer decision, reversal) plus, if it was paid, a
line inside a ``REBATE_PAYMENT_BATCH``.  They are tied together by the natural 340B key —
``(rx_number | provider_npi, pharmacy_npi, ndc_11, fill_date)`` — which is the same key the
crosswalk uses, and the same one the sidecar carries.

That last point is the subtle one.  The generator injects identifier drift (defect D-6) into
exactly one feed per episode, so a dispense's ``rx_number`` in the TPA feed may legitimately
*not* match the same dispense elsewhere.  The sidecar therefore stores the key spelled the
way the TPA feed spells it, drift included.  A mock that silently repaired the drift would
hand the connector a working join the real feed does not have, and the crosswalk-miss
exception the defect exists to produce would quietly disappear.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from io import StringIO
from pathlib import Path
from typing import Any, Iterator, Sequence

from recon import config
from recon.config import Settings

__all__ = [
    "Dispense",
    "RebateBatch",
    "VendorSource",
    "load_source",
]

#: The TPA event types a dispense can be described by, in the order they can arrive.
_DISPENSE_EVENTS = (
    "QUALIFICATION_DECISION",
    "REBATE_REQUEST",
    "MANUFACTURER_DECISION",
    "DISPENSE_REVERSAL",
)

#: The natural 340B key's components, spelled as ``tpa_340b_events.jsonl`` spells them.
_JOIN_FIELDS = ("rx_number", "pharmacy_npi", "provider_npi", "ndc_11", "fill_date")

NaturalKey = tuple[Any, ...]


def _natural_key(record: dict[str, Any]) -> NaturalKey:
    return tuple(record.get(name) for name in _JOIN_FIELDS)


@dataclass(frozen=True, slots=True)
class Dispense:
    """One 340B dispense, as the feeds describe it, plus its minted vendor identifiers.

    Every attribute is either read straight off a feed record or minted by the orchestrator.
    Nothing here is computed, and in particular no amount is ever derived — ``rebate_amount``
    is the payment batch's own text, carried across unchanged.
    """

    beacon_id: str
    accumulation_id: str
    invoice_number: str

    rx_number: str | None
    pharmacy_npi: str | None
    provider_npi: str | None
    ndc_11: str
    fill_date: str
    covered_entity_id: str
    manufacturer: str

    #: Every TPA event for this dispense, in arrival order, verbatim.
    events: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    #: The matching line inside a ``REBATE_PAYMENT_BATCH``, if the dispense was paid.
    payment_line: dict[str, Any] | None = None
    #: The batch that line came from, if any.
    payment_batch: dict[str, Any] | None = None

    # -- read-throughs.  Lookups, never decisions. -------------------------

    def event(self, event_type: str) -> dict[str, Any] | None:
        for record in self.events:
            if record.get("event_type") == event_type:
                return record
        return None

    @property
    def natural_key(self) -> NaturalKey:
        return (
            self.rx_number,
            self.pharmacy_npi,
            self.provider_npi,
            self.ndc_11,
            self.fill_date,
        )

    @property
    def qualification_status(self) -> str | None:
        record = self.event("QUALIFICATION_DECISION")
        return None if record is None else record.get("qualification_status")

    @property
    def disqualification_reason(self) -> str | None:
        record = self.event("QUALIFICATION_DECISION")
        return None if record is None else record.get("disqualification_reason")

    @property
    def manufacturer_status(self) -> str | None:
        record = self.event("MANUFACTURER_DECISION")
        return None if record is None else record.get("manufacturer_status")

    @property
    def rejection_reason(self) -> str | None:
        record = self.event("MANUFACTURER_DECISION")
        return None if record is None else record.get("rejection_reason")

    @property
    def is_reversed(self) -> bool:
        return self.event("DISPENSE_REVERSAL") is not None

    @property
    def is_paid(self) -> bool:
        return self.payment_line is not None

    @property
    def rebate_amount(self) -> str | None:
        """The rebate as the payment batch wrote it. Copied as text, never re-derived."""
        return None if self.payment_line is None else self.payment_line.get("rebate_amount")

    @property
    def allocation_code(self) -> str | None:
        return None if self.payment_batch is None else self.payment_batch.get("allocation_code")

    @property
    def payment_effective_date(self) -> str | None:
        if self.payment_batch is None:
            return None
        return self.payment_batch.get("payment_effective_date")

    @property
    def submitted_at(self) -> str | None:
        record = self.event("REBATE_REQUEST")
        return None if record is None else record.get("submission_date")

    @property
    def archetype(self) -> str:
        """Which of Doc 2's four golden-claim archetypes this dispense is.

        A classification of what already happened, not a decision about what should.  The
        four names come from ``DOC2-002`` step 5: *"Trace representative paid, rejected,
        reversed and unmatched claims end-to-end."*  Requirement M5 asserts all four appear
        in the vendor output, so something has to name them, and naming them by reading the
        events back is the only way a formatter is allowed to do it.
        """
        if self.is_reversed:
            return "reversed"
        if self.manufacturer_status == "REJECTED" or self.qualification_status == "NOT_QUALIFIED":
            return "rejected"
        if self.is_paid:
            return "paid"
        return "unmatched"


@dataclass(frozen=True, slots=True)
class RebateBatch:
    """A ``REBATE_PAYMENT_BATCH`` record, verbatim."""

    record: dict[str, Any]

    @property
    def allocation_code(self) -> str:
        return str(self.record.get("allocation_code", ""))

    @property
    def manufacturer(self) -> str:
        return str(self.record.get("manufacturer", ""))

    @property
    def total_rebate_amount(self) -> str:
        return str(self.record.get("total_rebate_amount", ""))

    @property
    def dispenses(self) -> list[dict[str, Any]]:
        return list(self.record.get("dispenses", []))


@dataclass(frozen=True, slots=True)
class VendorSource:
    """Everything the vendor formatters are allowed to see."""

    dispenses: tuple[Dispense, ...]
    rebate_batches: tuple[RebateBatch, ...]
    #: The raw feed records, keyed by feed filename, for formatters that need more context.
    feeds: dict[str, list[dict[str, Any]]]

    def by_archetype(self, name: str) -> list[Dispense]:
        return [dispense for dispense in self.dispenses if dispense.archetype == name]

    @property
    def archetype_counts(self) -> dict[str, int]:
        counts = {"paid": 0, "rejected": 0, "reversed": 0, "unmatched": 0}
        for dispense in self.dispenses:
            counts[dispense.archetype] = counts.get(dispense.archetype, 0) + 1
        return counts


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _read_csv(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return list(csv.DictReader(StringIO(path.read_text(encoding="utf-8"))))


def _payment_lines(batches: Sequence[RebateBatch]) -> Iterator[tuple[NaturalKey, dict, dict]]:
    for batch in batches:
        for line in batch.dispenses:
            yield _natural_key(line), line, batch.record


def load_source(settings: Settings) -> VendorSource:
    """Read the generated dataset and join it into dispenses the formatters can dress.

    Raises rather than returning an empty source when the sidecar is missing.  An empty
    vendor layer that looked successful would satisfy every "did it write a file" check
    while producing nothing, and requirement M5's coverage report would then be measuring
    silence.
    """
    feeds_dir = settings.feeds_dir()
    feeds: dict[str, list[dict[str, Any]]] = {}
    for name in config.FEED_FILENAMES:
        path = feeds_dir / name
        feeds[name] = _read_csv(path) if name.endswith(".csv") else _read_jsonl(path)

    identifiers_path = settings.vendor_identifiers_path()
    if not identifiers_path.exists():
        raise FileNotFoundError(
            f"{identifiers_path} is missing. The vendor identifier sidecar is written by "
            "recon.generators.orchestrator.write_outputs; regenerate the dataset before "
            "running the vendor mocks."
        )
    identifiers = _read_jsonl(identifiers_path)

    tpa_records = feeds.get(config.TPA_340B_EVENTS_FILE, [])
    batches = tuple(
        RebateBatch(record)
        for record in tpa_records
        if record.get("event_type") == "REBATE_PAYMENT_BATCH"
    )

    events_by_key: dict[NaturalKey, list[dict[str, Any]]] = {}
    for record in tpa_records:
        if record.get("event_type") not in _DISPENSE_EVENTS:
            continue
        events_by_key.setdefault(_natural_key(record), []).append(record)
    for records in events_by_key.values():
        records.sort(key=lambda item: str(item.get("received_at") or ""))

    lines_by_key: dict[NaturalKey, tuple[dict, dict]] = {}
    for key, line, batch_record in _payment_lines(batches):
        lines_by_key.setdefault(key, (line, batch_record))

    dispenses: list[Dispense] = []
    for row in identifiers:
        key = _natural_key(row)
        line, batch_record = lines_by_key.get(key, (None, None))
        dispenses.append(
            Dispense(
                beacon_id=row["beacon_id"],
                accumulation_id=row["accumulation_id"],
                invoice_number=row["invoice_number"],
                rx_number=row.get("rx_number"),
                pharmacy_npi=row.get("pharmacy_npi"),
                provider_npi=row.get("provider_npi"),
                ndc_11=row["ndc_11"],
                fill_date=row["fill_date"],
                covered_entity_id=row.get("covered_entity_id", ""),
                manufacturer=row.get("manufacturer", ""),
                events=tuple(events_by_key.get(key, ())),
                payment_line=line,
                payment_batch=batch_record,
            )
        )

    return VendorSource(
        dispenses=tuple(dispenses),
        rebate_batches=batches,
        feeds=feeds,
    )
