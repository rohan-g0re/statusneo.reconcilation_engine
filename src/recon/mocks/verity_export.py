"""The Verity 340B Secure Data Export formatter: five datasets, one file each.

Verity publishes *which* datasets it exports and *how* they arrive.  It does not publish
what is inside them.  ``DOC2-009`` names the five — accumulations, contract-pharmacy
claims backing, invoices, matches/split transactions, unmatched claims — and names the
transport, Secure Data Exports over SFTP on a daily, weekly or monthly cadence.
``VERITY-001`` confirms the transfer product first-hand, automated and encrypted.
``VERITY-002`` confirms Split Billing is a real Verity product, which is what the
"matches/split transactions" dataset name refers to.  ``VERITY-006`` confirms that a named
"Split Transaction data specification" exists and was extended for the rebate model.

``VERITY-006`` is also where the boundary sits.  That specification is *referenced* and
never *published*; no field list for any of the five datasets was retrievable from
anywhere.  So the dataset names, the transport and the cadence are cited, and **every
column this module writes is `INVENTED`** — declared one by one in ``MOCK_FIELDS.md``
beside this file, so a reviewer sees exactly where the evidence stopped and we started.
``DOC2-011`` is explicit that Verity's reversal and requalification representation is
unconfirmed, and ``REVERSAL_REPRESENTATION`` below is our declaration of the one we chose.

**This is a formatter, not a generator.**  It reads a :class:`~recon.mocks.source.
VendorSource` and returns text.  It decides nothing: no qualification, no amount, no
timing.  Every value here was decided upstream by the orchestrator and is read back.  The
package docstring explains why that distinction is load-bearing rather than stylistic.

═══ CSV, because an honest invention should be reviewable ══════════════════════════

Verity publishes no file format.  CSV with a header row is the format a reviewer can open
without tooling and diff without a parser, which is the right property for a payload whose
whole claim on the reader is "check what I assumed."  Line endings are pinned to ``\\n``
for the same reason :mod:`recon.generators.bank` pins them — letting :mod:`csv` default to
``\\r\\n`` on Windows makes the same input produce two different file hashes on two
platforms, and the reproducibility manifest becomes a lie.

═══ The control-total trailer ══════════════════════════════════════════════════════

``DOC2-010`` says a Verity transport should use file landing, checksum, file-level
idempotency and **source-control totals**.  Requirement F2 is what consumes that: compare
the vendor's declared count against what was ingested, because silent truncation is the
failure mode that parses cleanly and reconciles wrongly.

Every file therefore ends with a trailer row carrying the number of detail rows above it.
Two leading columns make that unambiguous: ``record_type`` is ``DETAIL`` or ``TRAILER``,
and ``record_count`` is populated only on the trailer.  A positional trailer that reused
the first data column would work until someone read the file with a ``DictReader``, which
is how a control total quietly becomes a covered entity id.

The trailer declares a **count and no sum**.  A sum would mean adding amounts, and this
package may not do arithmetic on money — a mock that could add two amounts could disagree
with the ledger, and the reconciliation it exists to exercise would be measuring the mock.
Where a declared money figure is genuinely useful, ``invoices`` carries the payment batch's
own ``total_rebate_amount`` string beside each line, copied verbatim.  F2 gets a declared
figure it can reconcile without this module ever having computed one.

═══ The generated timestamp in the file name ═══════════════════════════════════════

``DOC2-010`` asks that the vendor file name, the generated timestamp and the export type
be retained for audit lineage, so the name carries all three: ``verity_<dataset>_<stamp>``.

The stamp is derived from **the data**, never from the clock.  Wall-clock is banned
outside connector checkpoints and logs (``docs/connectivity_layer_requirements.md`` §4.11),
and a ``datetime.now()`` here would make the same seed produce a different file name on
every run — which breaks the reproducibility manifest and, worse, makes file-level
idempotency untestable, because the same export would land under a new name each time.
The stamp is the latest ``received_at`` present in the source: a real export is named for
the moment the vendor cut it, and the moment this data stops arriving is the closest thing
to that moment this repository actually knows.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Any, Iterable, Sequence

from recon.config import Settings
from recon.mocks.source import Dispense, VendorSource

__all__ = [
    "DATASETS",
    "COLUMNS",
    "REVERSAL_REPRESENTATION",
    "VENDOR_NAME",
    "declared_record_count",
    "export_filename",
    "generated_stamp",
    "render",
    "write",
]

#: The subdirectory of ``settings.vendor_dir()`` these files land in.
VENDOR_NAME = "verity"

#: The five datasets, spelled as this repository spells them.  The names come from
#: ``DOC2-009``; ``split_transactions`` follows Verity's own vocabulary for the
#: "matches/split transactions" dataset, which ``VERITY-006`` gives first-hand as the
#: "Split Transaction data specification".
DATASETS: tuple[str, ...] = (
    "accumulations",
    "contract_pharmacy_claims_backing",
    "invoices",
    "split_transactions",
    "unmatched_claims",
)

#: How a reversal is represented in every Verity file this module writes.
#:
#: **INVENTED.**  ``DOC2-011`` states in as many words that Verity's reversal and
#: requalification representation is not publicly confirmed, and requirement B3 exists
#: because guessing it wrong is the defect that destroys a build quietly: you double-count
#: or you lose a rebate, and both look clean in every report afterwards.  So it is declared
#: here rather than assumed, and it is declared as invented rather than cited.
#:
#: The three candidates were a negative-quantity row, a replacement row, and a status flag
#: on the restated row.  This module writes the **status flag**, and the deciding argument
#: is that these exports are full files, not deltas.  ``DOC2-009``'s cadence is daily,
#: weekly or monthly, and whether Verity sends increments is listed as unknown in
#: ``docs/vendor_evidence/verity.md``, so the mock emits full files.  Under full-file
#: re-delivery a second, negative row is re-delivered too — an ingester that accumulates
#: rows across two deliveries counts the reversal twice, which is exactly B3's "not zero,
#: not two" failing in the direction nobody notices.  A flag on the one row for the
#: dispense is idempotent under re-delivery, which is what the file-level idempotency of
#: ``DOC2-010`` already assumes of this transport.
#:
#: The second argument is that a negative row needs a negative amount to be worth having,
#: and negating an amount is arithmetic on money, which this package may not do.  A flag
#: keeps exactly one money figure per dispense, copied as text.
#:
#: The reversal's own detail is not lost: ``reversal_received_at``, ``reversal_reason`` and
#: the signed ``reversal_quantity`` are copied verbatim from the ``DISPENSE_REVERSAL``
#: event onto the same row.  Note the wider system does **not** agree with this choice and
#: is not meant to: ``recon.generators.tpa`` writes a reversal to the TPA feed as a
#: negative-quantity row, following NCPDP.  Two vendors representing a reversal two
#: different ways is the situation B3 was written for, and this constant is how a reader
#: tells which one they are looking at.
REVERSAL_REPRESENTATION = "STATUS_FLAG_ROW"

#: The sentinel values of the ``record_type`` column.
DETAIL_ROW = "DETAIL"
TRAILER_ROW = "TRAILER"

#: The status written into ``reversal_status`` when the dispense carries a reversal.
REVERSED_STATUS = "REVERSED"

#: The two control columns every dataset leads with.  See the module docstring.
CONTROL_COLUMNS: tuple[str, ...] = ("record_type", "record_count")

#: The four reversal columns every dataset ends with, carrying the representation declared
#: in ``REVERSAL_REPRESENTATION``.  Present on all five datasets, including the two where
#: they can only ever be empty, because a fixed column set with some of it null is what a
#: real vendor export looks like — :mod:`recon.generators.tpa` makes the same point about
#: ``provider_npi``.  A column that appears only on the datasets that happen to use it is a
#: column a consumer has to special-case per file.
REVERSAL_COLUMNS: tuple[str, ...] = (
    "reversal_status",
    "reversal_received_at",
    "reversal_reason",
    "reversal_quantity",
)

#: The column order per dataset, fixed here as the single authority.  A reordering would
#: silently break a positional reader, which is the same reason
#: :mod:`recon.generators.bank` pins ``BANK_CSV_COLUMNS`` in one place.
COLUMNS: dict[str, tuple[str, ...]] = {
    "accumulations": (
        *CONTROL_COLUMNS,
        "accumulation_id",
        "beacon_id",
        "covered_entity_id",
        "manufacturer",
        "ndc_11",
        "fill_date",
        "rx_number",
        "pharmacy_npi",
        "provider_npi",
        "prescriber_npi",
        "hin",
        "wholesaler_invoice_number",
        "qualification_status",
        "qualification_received_at",
        *REVERSAL_COLUMNS,
    ),
    "contract_pharmacy_claims_backing": (
        *CONTROL_COLUMNS,
        "beacon_id",
        "accumulation_id",
        "invoice_number",
        "covered_entity_id",
        "manufacturer",
        "ndc_11",
        "fill_date",
        "rx_number",
        "pharmacy_npi",
        "provider_npi",
        "claim_archetype",
        "qualification_status",
        "disqualification_reason",
        "manufacturer_decision_status",
        "rejection_reason",
        "rebate_request_submitted_date",
        "qualification_received_at",
        "rebate_request_received_at",
        "manufacturer_decision_received_at",
        *REVERSAL_COLUMNS,
    ),
    "invoices": (
        *CONTROL_COLUMNS,
        "invoice_number",
        "beacon_id",
        "accumulation_id",
        "covered_entity_id",
        "manufacturer",
        "ndc_11",
        "fill_date",
        "rx_number",
        "pharmacy_npi",
        "provider_npi",
        "rebate_allocation_code",
        "batch_line_manufacturer_status",
        "invoice_line_amount",
        "batch_total_rebate_amount",
        "payment_effective_date",
        "batch_received_at",
        *REVERSAL_COLUMNS,
    ),
    "split_transactions": (
        *CONTROL_COLUMNS,
        "beacon_id",
        "accumulation_id",
        "covered_entity_id",
        "manufacturer",
        "match_key",
        "rx_number",
        "pharmacy_npi",
        "provider_npi",
        "ndc_11",
        "fill_date",
        "qualification_status",
        "manufacturer_decision_status",
        "rejection_reason",
        "batch_line_manufacturer_status",
        "rebate_allocation_code",
        "matched_rebate_amount",
        "manufacturer_decision_received_at",
        *REVERSAL_COLUMNS,
    ),
    "unmatched_claims": (
        *CONTROL_COLUMNS,
        "beacon_id",
        "accumulation_id",
        "invoice_number",
        "covered_entity_id",
        "manufacturer",
        "ndc_11",
        "fill_date",
        "rx_number",
        "pharmacy_npi",
        "provider_npi",
        "qualification_status",
        "disqualification_reason",
        "qualification_received_at",
        "rebate_request_received_at",
        *REVERSAL_COLUMNS,
    ),
}

#: The stamp used when the source carries no arrival time at all.  A degenerate case — a
#: dispense always has at least one event — but the file name has to be *something*, and
#: the one thing it may never fall back to is the wall clock.
_EMPTY_WINDOW_STAMP = "00000000T000000Z"

#: Event type names, spelled as ``tpa_340b_events.jsonl`` spells them.
_QUALIFICATION = "QUALIFICATION_DECISION"
_REBATE_REQUEST = "REBATE_REQUEST"
_MANUFACTURER_DECISION = "MANUFACTURER_DECISION"
_REVERSAL = "DISPENSE_REVERSAL"


# ═══ public surface ═════════════════════════════════════════════════════════════════


def render(source: VendorSource) -> dict[str, str]:
    """Format the whole source as the five Verity export files, keyed by dataset name.

    Returns text, never a path.  Nothing in this package opens a file except
    :mod:`recon.mocks.source`, and keeping the rendering separable from the writing is what
    lets a test assert on the bytes without a temporary directory — requirement M6's "a
    human can open it and a test can read it directly", before any transport exists.
    """
    return {dataset: _render_dataset(dataset, source) for dataset in DATASETS}


def write(settings: Settings, source: VendorSource) -> dict[str, Path]:
    """Write the five files under ``settings.vendor_dir() / "verity"``.

    A sibling of the identifier sidecar and well away from ``feeds_dir()``, so the six
    generated feeds stay byte-identical and ``load_feeds`` cannot reach any of this.
    """
    directory = settings.vendor_dir() / VENDOR_NAME
    directory.mkdir(parents=True, exist_ok=True)

    stamp = generated_stamp(source)
    written: dict[str, Path] = {}
    for dataset, text in render(source).items():
        path = directory / export_filename(dataset, stamp)
        path.write_text(text, encoding="utf-8", newline="")
        written[dataset] = path
    return written


def export_filename(dataset: str, stamp: str) -> str:
    """``verity_<export type>_<generated stamp>.csv`` — the three things ``DOC2-010`` asks
    an audit trail to retain, in the name itself.
    """
    _require_known(dataset)
    return f"{VENDOR_NAME}_{dataset}_{stamp}.csv"


def generated_stamp(source: VendorSource) -> str:
    """The export's generated timestamp, derived from the data rather than from the clock.

    The latest ``received_at`` anywhere in the source, compacted to ``YYYYMMDDTHHMMSSZ``.
    ``max`` here is over fixed-width UTC timestamp *strings*, where lexicographic order is
    chronological order by construction (see :mod:`recon.config`), so no parsing is needed
    and none happens.

    Deriving this from ``datetime.now()`` would be the obvious thing and the wrong one: the
    file name would change on every run of an otherwise byte-identical dataset, which
    breaks the reproducibility manifest and makes the connector's file-level idempotency
    impossible to test, because no two runs would ever land the same file.
    """
    stamps = [
        value
        for value in _all_received_at(source)
        if isinstance(value, str) and value
    ]
    if not stamps:
        return _EMPTY_WINDOW_STAMP
    return max(stamps).replace("-", "").replace(":", "")


def declared_record_count(text: str) -> int:
    """Read a file's declared record count back out of its trailer row.

    The consumer half of the control total.  Requirement F2 compares this against what was
    actually ingested, because a truncated file parses perfectly and reconciles wrongly —
    500 declared against 450 arrived has to fail the batch, not ingest 450 successfully.

    Raises rather than returning zero on a file with no trailer.  A missing control total
    returned as ``0`` would compare equal to an empty ingest and report agreement.
    """
    rows = list(csv.DictReader(io.StringIO(text, newline="")))
    for row in reversed(rows):
        if row.get("record_type") == TRAILER_ROW:
            return int(row["record_count"])
    raise ValueError(
        f"no {TRAILER_ROW} row in this export; every file this module writes ends with "
        "one, so a file without it was truncated or is not a Verity export"
    )


# ═══ dataset populations ════════════════════════════════════════════════════════════
#
# Each of these selects rows by reading a status back.  None of them decides anything: the
# qualification, the manufacturer's verdict, the amount and every timestamp were settled
# upstream by the orchestrator, and a formatter that re-derived any of them would be the
# generator this package exists to not be.


def _accumulations(source: VendorSource) -> list[Dispense]:
    """Dispenses the TPA qualified.  An accumulation exists because 340B eligibility was
    granted, so ``QUALIFIED`` is the whole selection — read off the record, not inferred.

    Note what this deliberately keeps: a dispense the TPA qualified and the manufacturer
    later rejected still accumulated, and still appears here.  Dropping it would hide the
    state where most 340B dispute volume actually lives (see :mod:`recon.generators.tpa`).
    """
    return [d for d in source.dispenses if d.qualification_status == "QUALIFIED"]


def _claims_backing(source: VendorSource) -> list[Dispense]:
    """Every dispense.  This is the backing detail the other four datasets are backed *by*,
    so it is the one file where a claim missing is a claim nobody can trace.

    It is also what makes requirement M5's four archetypes provable: paid, rejected,
    reversed and unmatched all appear here by construction, rather than by hoping the other
    four populations happen to cover them between them.
    """
    return list(source.dispenses)


def _invoices(source: VendorSource) -> list[Dispense]:
    """Dispenses that have a line inside a ``REBATE_PAYMENT_BATCH``.

    Driven off dispenses rather than off batch lines, which means a batch line whose
    natural key matches no dispense does not appear.  That is correct and deliberate: the
    generator injects identifier drift into exactly one feed per episode, and a line that
    has drifted out of the join is a line a real operator also cannot invoice.  Repairing
    it here would hand the connector a join the real feed does not have.
    """
    return [d for d in source.dispenses if d.is_paid]


def _split_transactions(source: VendorSource) -> list[Dispense]:
    """Dispenses matched to a manufacturer outcome — a decision record, a payment line, or
    both.

    "Matches/split transactions" is one dataset name in ``DOC2-009`` and this is the match
    half of it, on the only bridge 340B actually has: the natural key.  A dispense with
    neither a decision nor a payment has not been matched to anything yet and belongs in
    ``unmatched_claims``, not here.
    """
    return [d for d in source.dispenses if d.event(_MANUFACTURER_DECISION) or d.is_paid]


def _unmatched_claims(source: VendorSource) -> list[Dispense]:
    """The ``unmatched`` archetype, straight from :meth:`VendorSource.by_archetype`.

    Classified there rather than here, and by reading events back rather than by judging
    them — "unmatched" means no reversal, no rejection and no payment arrived, which is an
    observation about the feed and not a verdict about the claim.
    """
    return source.by_archetype("unmatched")


_POPULATIONS = {
    "accumulations": _accumulations,
    "contract_pharmacy_claims_backing": _claims_backing,
    "invoices": _invoices,
    "split_transactions": _split_transactions,
    "unmatched_claims": _unmatched_claims,
}


# ═══ row builders ═══════════════════════════════════════════════════════════════════


def _accumulation_row(dispense: Dispense) -> dict[str, Any]:
    return {
        "accumulation_id": dispense.accumulation_id,
        "beacon_id": dispense.beacon_id,
        "covered_entity_id": dispense.covered_entity_id,
        "manufacturer": dispense.manufacturer,
        "ndc_11": dispense.ndc_11,
        "fill_date": dispense.fill_date,
        "rx_number": dispense.rx_number,
        "pharmacy_npi": dispense.pharmacy_npi,
        "provider_npi": dispense.provider_npi,
        "prescriber_npi": _event_field(dispense, _QUALIFICATION, "prescriber_npi"),
        "hin": _event_field(dispense, _QUALIFICATION, "hin"),
        "wholesaler_invoice_number": _event_field(
            dispense, _QUALIFICATION, "wholesaler_invoice_number"
        ),
        "qualification_status": dispense.qualification_status,
        "qualification_received_at": _event_field(dispense, _QUALIFICATION, "received_at"),
    }


def _claims_backing_row(dispense: Dispense) -> dict[str, Any]:
    return {
        "beacon_id": dispense.beacon_id,
        "accumulation_id": dispense.accumulation_id,
        "invoice_number": dispense.invoice_number,
        "covered_entity_id": dispense.covered_entity_id,
        "manufacturer": dispense.manufacturer,
        "ndc_11": dispense.ndc_11,
        "fill_date": dispense.fill_date,
        "rx_number": dispense.rx_number,
        "pharmacy_npi": dispense.pharmacy_npi,
        "provider_npi": dispense.provider_npi,
        # Our own label for the claim's shape, not a Verity field and not a judgement —
        # ``Dispense.archetype`` reads the events back.  It rides here so requirement M5's
        # "all four archetypes appear in the vendor output" is a property a reader can
        # check by opening the file, instead of one a test has to reconstruct by joining
        # back to the feed.
        "claim_archetype": dispense.archetype,
        "qualification_status": dispense.qualification_status,
        "disqualification_reason": dispense.disqualification_reason,
        "manufacturer_decision_status": dispense.manufacturer_status,
        "rejection_reason": dispense.rejection_reason,
        "rebate_request_submitted_date": dispense.submitted_at,
        "qualification_received_at": _event_field(dispense, _QUALIFICATION, "received_at"),
        "rebate_request_received_at": _event_field(dispense, _REBATE_REQUEST, "received_at"),
        "manufacturer_decision_received_at": _event_field(
            dispense, _MANUFACTURER_DECISION, "received_at"
        ),
    }


def _invoice_row(dispense: Dispense) -> dict[str, Any]:
    line = dispense.payment_line or {}
    batch = dispense.payment_batch or {}
    return {
        "invoice_number": dispense.invoice_number,
        "beacon_id": dispense.beacon_id,
        "accumulation_id": dispense.accumulation_id,
        "covered_entity_id": dispense.covered_entity_id,
        "manufacturer": dispense.manufacturer,
        "ndc_11": dispense.ndc_11,
        "fill_date": dispense.fill_date,
        "rx_number": dispense.rx_number,
        "pharmacy_npi": dispense.pharmacy_npi,
        "provider_npi": dispense.provider_npi,
        "rebate_allocation_code": dispense.allocation_code,
        "batch_line_manufacturer_status": line.get("manufacturer_status"),
        # Both amounts are strings lifted out of the feed unchanged.  The line figure is
        # the batch's own ``rebate_amount``; the total is the batch's own
        # ``total_rebate_amount``, carried rather than recomputed so there is no second
        # copy of the figure free to disagree with the wire.
        "invoice_line_amount": dispense.rebate_amount,
        "batch_total_rebate_amount": batch.get("total_rebate_amount"),
        "payment_effective_date": dispense.payment_effective_date,
        "batch_received_at": batch.get("received_at"),
    }


def _split_transaction_row(dispense: Dispense) -> dict[str, Any]:
    line = dispense.payment_line or {}
    return {
        "beacon_id": dispense.beacon_id,
        "accumulation_id": dispense.accumulation_id,
        "covered_entity_id": dispense.covered_entity_id,
        "manufacturer": dispense.manufacturer,
        # The natural key, spelled the way the TPA feed spells it — drift included.  It is
        # printed as one column because it is the thing the match was actually made on, and
        # a reviewer looking for why a match failed should not have to reassemble it from
        # five columns.
        "match_key": _match_key(dispense),
        "rx_number": dispense.rx_number,
        "pharmacy_npi": dispense.pharmacy_npi,
        "provider_npi": dispense.provider_npi,
        "ndc_11": dispense.ndc_11,
        "fill_date": dispense.fill_date,
        "qualification_status": dispense.qualification_status,
        # Two status columns, because there are genuinely two sources and they are not
        # interchangeable: a standalone ``MANUFACTURER_DECISION`` record carries a
        # rejection, and a paid dispense carries its status inside the payment batch line
        # with no decision record at all.  Collapsing them into one column would invent an
        # agreement the feed does not have.
        "manufacturer_decision_status": dispense.manufacturer_status,
        "rejection_reason": dispense.rejection_reason,
        "batch_line_manufacturer_status": line.get("manufacturer_status"),
        "rebate_allocation_code": dispense.allocation_code,
        "matched_rebate_amount": dispense.rebate_amount,
        "manufacturer_decision_received_at": _event_field(
            dispense, _MANUFACTURER_DECISION, "received_at"
        ),
    }


def _unmatched_claim_row(dispense: Dispense) -> dict[str, Any]:
    return {
        "beacon_id": dispense.beacon_id,
        "accumulation_id": dispense.accumulation_id,
        "invoice_number": dispense.invoice_number,
        "covered_entity_id": dispense.covered_entity_id,
        "manufacturer": dispense.manufacturer,
        "ndc_11": dispense.ndc_11,
        "fill_date": dispense.fill_date,
        "rx_number": dispense.rx_number,
        "pharmacy_npi": dispense.pharmacy_npi,
        "provider_npi": dispense.provider_npi,
        "qualification_status": dispense.qualification_status,
        "disqualification_reason": dispense.disqualification_reason,
        "qualification_received_at": _event_field(dispense, _QUALIFICATION, "received_at"),
        "rebate_request_received_at": _event_field(dispense, _REBATE_REQUEST, "received_at"),
    }


_ROW_BUILDERS = {
    "accumulations": _accumulation_row,
    "contract_pharmacy_claims_backing": _claims_backing_row,
    "invoices": _invoice_row,
    "split_transactions": _split_transaction_row,
    "unmatched_claims": _unmatched_claim_row,
}


def _reversal_fields(dispense: Dispense) -> dict[str, Any]:
    """The declared reversal representation, applied identically on all five datasets.

    A dispense that was never reversed leaves all four columns empty.  A dispense that was
    gets the flag and the reversal's own detail on its existing row — no second row, and
    no negated amount.  See :data:`REVERSAL_REPRESENTATION`.
    """
    if not dispense.is_reversed:
        return {name: None for name in REVERSAL_COLUMNS}
    return {
        "reversal_status": REVERSED_STATUS,
        "reversal_received_at": _event_field(dispense, _REVERSAL, "received_at"),
        "reversal_reason": _event_field(dispense, _REVERSAL, "reversal_reason"),
        # The feed's own signed unit count, copied as it stands.  It is already negative —
        # ``recon.generators.tpa`` negates it once, on the way onto the wire, and negating
        # it a second time here is the exact bug that module's comment records.
        "reversal_quantity": _event_field(dispense, _REVERSAL, "quantity_dispensed"),
    }


# ═══ rendering ══════════════════════════════════════════════════════════════════════


def _render_dataset(dataset: str, source: VendorSource) -> str:
    _require_known(dataset)
    dispenses = _POPULATIONS[dataset](source)
    build = _ROW_BUILDERS[dataset]

    rows: list[dict[str, Any]] = []
    for dispense in dispenses:
        row = {"record_type": DETAIL_ROW, "record_count": None}
        row.update(build(dispense))
        row.update(_reversal_fields(dispense))
        rows.append(row)

    # The declared total is the number of detail rows, counted.  Counting rows is not
    # arithmetic on money, which is why the trailer declares a count and no sum.
    trailer = {"record_type": TRAILER_ROW, "record_count": len(rows)}
    rows.append(trailer)

    return _to_csv(COLUMNS[dataset], rows)


def _to_csv(columns: Sequence[str], rows: Iterable[dict[str, Any]]) -> str:
    """Header row, detail rows, trailer row, ``\\n`` throughout.

    ``extrasaction="raise"`` so a row carrying a key the column list does not know fails
    loudly instead of being dropped — a silently discarded field is how a mock ends up
    declaring provenance for a column it never actually writes.
    """
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(
        buffer,
        fieldnames=list(columns),
        lineterminator="\n",
        extrasaction="raise",
        restval="",
    )
    writer.writeheader()
    for row in rows:
        writer.writerow({name: _text(row.get(name)) for name in columns})
    return buffer.getvalue()


# ═══ read-throughs ══════════════════════════════════════════════════════════════════


def _text(value: Any) -> str:
    """``None`` becomes an empty cell, not the literal ``"null"``.

    The same choice :mod:`recon.generators.bank` makes for a missing ``trn02``: a CSV
    export writes an empty cell, and a connector that had to recognise ``"null"`` as
    absence would be coping with our formatting rather than with the vendor's.
    """
    return "" if value is None else str(value)


def _event_field(dispense: Dispense, event_type: str, field: str) -> Any:
    """One field off one event, or ``None`` when that event never arrived.

    Absence is a real state here and is preserved as one.  A dispense sitting in review
    has a qualification row with a null status; a dispense nobody has decided on has no
    decision row at all.  Collapsing either into a default would erase the distinction the
    unmatched archetype is made of.
    """
    record = dispense.event(event_type)
    return None if record is None else record.get(field)


def _match_key(dispense: Dispense) -> str:
    """The natural 340B key as one pipe-joined string, empty components included.

    ``{rx_number, pharmacy_npi, provider_npi, ndc_11, fill_date}`` is the only bridge
    between a TPA dispense and anything else, because 340B has no shared identifier space.
    Printed with its gaps rather than compacted: a medical-benefit dispense genuinely has
    no ``rx_number``, and a key that hid that would look like a key that matched.
    """
    return "|".join(_text(component) for component in dispense.natural_key)


def _all_received_at(source: VendorSource) -> Iterable[Any]:
    for dispense in source.dispenses:
        for event in dispense.events:
            yield event.get("received_at")
    for batch in source.rebate_batches:
        yield batch.record.get("received_at")


def _require_known(dataset: str) -> None:
    if dataset not in DATASETS:
        raise ValueError(
            f"unknown Verity dataset {dataset!r}; expected one of {', '.join(DATASETS)}"
        )
