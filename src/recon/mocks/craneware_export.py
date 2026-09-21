"""Craneware's five scheduled SFTP reports, written from data the generators already made.

Requirement D3 of ``docs/connectivity_layer_requirements.md``, and the one vendor mock whose
outer shape is not a guess.  ``CRANEWARE-001`` is Craneware's own 14 June 2023 announcement,
retrieved verbatim:

    Users now have the option of having Claims Report, Ordering Problems, Unreplenished
    Costs, Audit, and Bulk Dispensations Report and other commonly requested reports
    delivered straight to their Secure File Transfer Protocol (SFTP) folder

So the report *names* and the *transport* are Craneware's.  Everything inside the files is
ours, and the point of this docstring is that the seam between those two is visible rather
than smoothed over.  ``docs/vendor_evidence/craneware.md`` lists seven things that remain
genuinely unknown; the column list of all five reports is the first of them.

**Craneware's wording is used, not Doc 2's.**  ``DOC2-012`` writes "Claims" and "Bulk
Dispensations"; the vendor writes "Claims Report" and "Bulk Dispensations Report".  The
difference is trivial and it is preserved on purpose — the whole value of
``docs/vendor_evidence/`` is that wording gets checked instead of tidied, and a mock that
quietly improved on its own source would be the first place that discipline broke.

``CRANEWARE-001`` also says "and other commonly requested reports", so these five are
**not** the full set.  A coverage report may assert these five are non-empty; it may not
assert they are everything Craneware emits.

═══ Why this module is allowed to exist at all ═════════════════════════════════════

It is a *formatter*, never a generator — see :mod:`recon.mocks` for why that distinction is
load-bearing.  It reads a :class:`~recon.mocks.source.VendorSource` and re-dresses it.  It
decides nothing: qualification, amounts and timing were settled upstream by a pipeline that
was blind to the answer, and every value below is read straight back.  Concretely:

* **No money arithmetic.**  ``rebate_amount`` is copied as the text the payment batch wrote.
  Nothing here sums, nets or reformats a figure.  That is why the Bulk Dispensations Report
  aggregates *counts* and never a total: a mock that could add two amounts could disagree
  with the ledger, and the reconciliation it exists to exercise would then be measuring the
  mock.  Counting rows is integer arithmetic on rows, not on money, and is fine.
* **No privileged imports.**  ``recon.mocks.source``, ``recon.config``, stdlib.  Nothing
  from ``recon.generators``, ``recon.reference.pricing``, ``recon.money``,
  ``decision_tree`` or anything that can reach ``ground_truth``.

═══ How a reversal is represented, and why it is declared ══════════════════════════

Requirement B3, and the defect that destroys a build silently.  Craneware does not publish
it — gap 4 of the evidence file — so we choose, and the choice is
:data:`REVERSAL_REPRESENTATION`.

The TPA feed underneath uses a **negative row**: ``DISPENSE_REVERSAL`` carries a negative
``quantity_dispensed`` and the original dispense stays on the wire.  This export
deliberately does **not** copy that convention, because a *report* is a different kind of
artefact from an *event journal*.  A scheduled report is a snapshot of state as of the run,
and the plausible thing for a snapshot to do is restate the original row with a reversed
status — which is what happens here.  The Claims Report emits exactly one row per dispense,
carrying ``dispense_status = REVERSED`` plus the reversal's own date, reason and signed
quantity.  There is never a second row and no amount is ever negated.

That is the entire reason B3 exists.  A connector written against a negative-row vendor and
pointed at this file finds no reversal row, concludes nothing was reversed, and keeps the
rebate — and every report downstream looks clean.  A connector written against this file and
pointed at a negative-row vendor nets the reversal twice.  Both failures are invisible, so
the representation is declared as data rather than left for the connector to infer.

**The Audit report is the one exception, and it is not a contradiction.**  Audit is a
journal, so the reversal appears there as its own event row.  A connector must not ingest
Claims *and* Audit as claim rows — Audit is the trail, Claims is the state.

═══ File-level controls and schema versioning ══════════════════════════════════════

``CRANEWARE-004`` quotes Doc 2: *"use file-level controls, file schema versioning and daily
ingestion."*  Craneware's own format for either is unknown (gap 5), so both are invented,
and both are put where a plain :class:`csv.DictReader` will find them rather than in comment
lines a reader has to know to skip:

* Every row carries ``record_type`` (``DETAIL`` or ``TRAILER``) and ``schema_version``.
* The last row of every file is a ``TRAILER`` whose ``declared_record_count`` is the number
  of ``DETAIL`` rows above it.  Requirement F2 reconciles that against what was ingested,
  because silent truncation is the failure mode that parses cleanly.

The trailer does not count itself.  :func:`declared_record_count` reads it back so F2 never
has to re-implement the parse.

Filenames are stable and **undated**, which a real scheduled drop would not be.  Dating them
would mean either inventing a cadence Craneware has not published (gap 3) or stamping the
run date into the bytes, and the second makes the same input produce two different files on
two different days — which is the reproducibility guarantee the rest of this repository is
built on.  The transport layer can date the drop; the payload should not.
"""

from __future__ import annotations

import csv
import io
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Sequence

from recon.config import Settings
from recon.mocks.source import Dispense, VendorSource

__all__ = [
    "REPORTS",
    "SCHEMA_VERSION",
    "REVERSAL_REPRESENTATION",
    "CRANEWARE_SUBDIR",
    "REPORT_FILENAMES",
    "REPORT_COLUMNS",
    "CONTROL_COLUMNS",
    "TRAILER_COLUMN",
    "DETAIL_RECORD_TYPE",
    "TRAILER_RECORD_TYPE",
    "render",
    "write",
    "declared_record_count",
]


#: The five report names, in Craneware's own order and Craneware's own wording
#: (``CRANEWARE-001``).  "Claims Report" and "Bulk Dispensations Report" carry the word
#: *Report*; the other three do not.  That asymmetry is the source's, not a typo.
REPORTS: tuple[str, ...] = (
    "Claims Report",
    "Ordering Problems",
    "Unreplenished Costs",
    "Audit",
    "Bulk Dispensations Report",
)

#: The value written into every row's ``schema_version`` column, and the string a schema
#: registry (requirement B1) would key this layout on.  Invented — Craneware publishes no
#: field dictionary, so there is no vendor version to track.  Bump it whenever a column is
#: added, removed or reordered, because a positional reader breaks silently otherwise.
SCHEMA_VERSION: str = "craneware-sftp-export-1.0.0"

#: **Requirement B3, declared rather than inferred.**  A reversal is a *status flag on the
#: original row*: the Claims Report restates the dispense once, with ``dispense_status``
#: set to ``REVERSED`` and the reversal's date, reason and signed quantity beside it.  No
#: second row is emitted and no amount is negated — deliberately unlike the TPA feed
#: underneath, which uses a negative row, and deliberately unlike whatever another vendor
#: may do.  See the module docstring for why guessing this wrong is invisible in every
#: report afterwards.
REVERSAL_REPRESENTATION: str = "STATUS_FLAG_ON_ORIGINAL_ROW"

#: ``dispense_status`` values.  A rename of :attr:`Dispense.is_reversed`, which is itself a
#: lookup for a ``DISPENSE_REVERSAL`` event.  Nothing here decides that a reversal happened.
DISPENSE_STATUS_ACTIVE: str = "ACTIVE"
DISPENSE_STATUS_REVERSED: str = "REVERSED"

#: The directory under ``settings.vendor_dir()`` these files are written to.
CRANEWARE_SUBDIR: str = "craneware"

#: The two control columns every row starts with, and the one it ends with.  Fixed here as
#: the single authority: a reordering would break a positional reader without an error.
CONTROL_COLUMNS: tuple[str, ...] = ("record_type", "schema_version")
TRAILER_COLUMN: str = "declared_record_count"

DETAIL_RECORD_TYPE: str = "DETAIL"
TRAILER_RECORD_TYPE: str = "TRAILER"

#: The four golden-claim archetypes (``DOC2-002`` step 5), named as
#: :attr:`Dispense.archetype` spells them.  Every one appears in the Claims Report and in
#: the Audit trail, and each gets its own count column on the Bulk Dispensations Report.
ARCHETYPES: tuple[str, ...] = ("paid", "rejected", "reversed", "unmatched")


# ═══ column layouts ═════════════════════════════════════════════════════════════════
#
# Every column below is INVENTED.  ``docs/vendor_evidence/craneware.md`` is explicit: "Every
# Craneware payload field is an honest invention."  The only field with any evidential
# backing at all is ``ndc11`` on Unreplenished Costs, and ``CRANEWARE-002`` is a search-index
# summary rather than a retrieved source, so even that is tagged INVENTED in MOCK_FIELDS.md.
# The column *names* follow the vocabulary of the TPA feed they are read from, because a
# mapping whose names match its source is a mapping a reviewer can check by eye.

#: Claims Report — the 340B dispenses themselves, one row each, whatever became of them.
#: This is the report that carries the reversal flag, so it is where B3 is visible.
CLAIMS_COLUMNS: tuple[str, ...] = (
    "covered_entity_id",
    "rx_number",
    "pharmacy_npi",
    "provider_npi",
    "ndc11",
    "fill_date",
    "manufacturer",
    "qualification_status",
    "disqualification_reason",
    "manufacturer_status",
    "rejection_reason",
    "dispense_status",
    "reversal_date",
    "reversal_reason",
    "reversal_quantity",
    "rebate_submitted_date",
    "rebate_amount",
    "rebate_payment_date",
    "rebate_allocation_code",
)

#: Ordering Problems — dispenses that failed a gate.  The mapping from Craneware's name to
#: "failed qualification or was rejected" is an **inference**: the name suggests wholesaler
#: ordering and replenishment exceptions, and the nearest honest analogue in this dataset is
#: a dispense that did not clear one of its two independent approvals.  ``hin`` and
#: ``wholesaler_invoice_number`` are carried because they are the two genuinely
#: ordering-flavoured fields the qualification event already holds.
ORDERING_PROBLEMS_COLUMNS: tuple[str, ...] = (
    "covered_entity_id",
    "rx_number",
    "pharmacy_npi",
    "provider_npi",
    "ndc11",
    "fill_date",
    "manufacturer",
    "problem_stage",
    "problem_status",
    "problem_code",
    "hin",
    "wholesaler_invoice_number",
    "reported_date",
)

#: Unreplenished Costs — NDC first, because ``CRANEWARE-002`` summarises the real report as
#: keyed by NDC with a filterable replenishment status.  **There is no money column, and the
#: report is named Costs.**  The source carries no amount for a dispense that has not been
#: paid — ``rebate_amount`` is populated by the payment batch or not at all — and deriving
#: one is exactly the arithmetic rule 2 of :mod:`recon.mocks` forbids.  Printing an invented
#: figure under a column named cost would be a worse lie than leaving the column out, so it
#: is left out and recorded here.
UNREPLENISHED_COSTS_COLUMNS: tuple[str, ...] = (
    "ndc11",
    "covered_entity_id",
    "rx_number",
    "pharmacy_npi",
    "provider_npi",
    "fill_date",
    "manufacturer",
    "replenishment_status",
    "qualification_status",
    "rebate_submitted_date",
)

#: Audit — one row per TPA event, in arrival order, verbatim.  A journal, not a snapshot.
AUDIT_COLUMNS: tuple[str, ...] = (
    "event_sequence",
    "record_id",
    "source_system",
    "event_type",
    "event_timestamp",
    "covered_entity_id",
    "rx_number",
    "pharmacy_npi",
    "provider_npi",
    "ndc11",
    "fill_date",
    "event_outcome",
    "event_detail",
)

#: Bulk Dispensations Report — one row per covered entity, NDC and manufacturer.  Counts
#: only.  See the module docstring for why there is no amount total here.
BULK_DISPENSATIONS_COLUMNS: tuple[str, ...] = (
    "covered_entity_id",
    "ndc11",
    "manufacturer",
    "dispense_count",
    "paid_count",
    "rejected_count",
    "reversed_count",
    "unmatched_count",
    "earliest_fill_date",
    "latest_fill_date",
)

#: Report name to its report-specific columns.  The control columns wrap these; see
#: :func:`_file_columns`.
REPORT_COLUMNS: dict[str, tuple[str, ...]] = {
    "Claims Report": CLAIMS_COLUMNS,
    "Ordering Problems": ORDERING_PROBLEMS_COLUMNS,
    "Unreplenished Costs": UNREPLENISHED_COSTS_COLUMNS,
    "Audit": AUDIT_COLUMNS,
    "Bulk Dispensations Report": BULK_DISPENSATIONS_COLUMNS,
}

#: Report name to filename.  The slugs are invented — Craneware's naming convention is gap 3
#: of the evidence file — and they are undated on purpose; see the module docstring.
REPORT_FILENAMES: dict[str, str] = {
    "Claims Report": "claims_report.csv",
    "Ordering Problems": "ordering_problems.csv",
    "Unreplenished Costs": "unreplenished_costs.csv",
    "Audit": "audit.csv",
    "Bulk Dispensations Report": "bulk_dispensations_report.csv",
}

#: ``replenishment_status`` values on Unreplenished Costs.  ``CRANEWARE-002`` says the real
#: report filters on "various replenishment statuses"; these three are the only ones this
#: dataset can honestly distinguish, and each is a lookup on one field that already exists.
#:
#: ``REBATE_REJECTED`` matters more than it looks.  A manufacturer rejection leaves
#: ``qualification_status`` at ``QUALIFIED`` and the dispense unpaid, so it is genuinely an
#: unreplenished cost — the entity bought the drug and the rebate never came.  Without this
#: value it would sit on the report labelled as awaiting a payment that is never coming,
#: which is the report telling an operator to keep waiting.
REPLENISHMENT_REJECTED: str = "REBATE_REJECTED"
REPLENISHMENT_SUBMITTED: str = "SUBMITTED_AWAITING_PAYMENT"
REPLENISHMENT_AWAITING_SUBMISSION: str = "AWAITING_SUBMISSION"

#: ``problem_stage`` values on Ordering Problems — which of the two independent approvals
#: the dispense failed.  The TPA decides qualification; the manufacturer decides the rebate.
#: They are separate systems that do not coordinate, so a dispense can fail either.
PROBLEM_STAGE_QUALIFICATION: str = "QUALIFICATION"
PROBLEM_STAGE_MANUFACTURER: str = "MANUFACTURER_REBATE"

#: On an Audit row, the first of these keys the event actually carries becomes
#: ``event_outcome``; likewise ``event_detail``.  A fixed key list searched in order is a
#: lookup, not a decision — no event is interpreted, and an event carrying none of them
#: gets an empty cell rather than an invented one.
_AUDIT_OUTCOME_KEYS: tuple[str, ...] = ("qualification_status", "manufacturer_status")
_AUDIT_DETAIL_KEYS: tuple[str, ...] = (
    "disqualification_reason",
    "rejection_reason",
    "reversal_reason",
    "submission_date",
)


# ═══ public API ═════════════════════════════════════════════════════════════════════


def render(source: VendorSource) -> dict[str, str]:
    """Render all five reports as CSV text, keyed by Craneware's own report name.

    Returns text rather than writing it, for the same reason every other formatter in this
    package does: a test can assert on the bytes without a filesystem, and the transport
    layer that eventually moves them (requirement D1) is a separate concern that arrives
    later.  Requirement M6 is the rule — the data layer has to stand up with
    ``src/recon/connectors/`` absent from the repository entirely.

    Every report is produced even when it has no rows.  An absent file and an empty file
    mean different things to a scheduled ingest — the first is a delivery failure, the
    second is a quiet day — and collapsing them would hide the failure that matters.
    """
    rows_by_report = {
        "Claims Report": _claims_rows(source.dispenses),
        "Ordering Problems": _ordering_problems_rows(source.dispenses),
        "Unreplenished Costs": _unreplenished_costs_rows(source.dispenses),
        "Audit": _audit_rows(source.dispenses),
        "Bulk Dispensations Report": _bulk_dispensations_rows(source.dispenses),
    }
    return {report: _render_csv(report, rows_by_report[report]) for report in REPORTS}


def write(settings: Settings, source: VendorSource) -> dict[str, Path]:
    """Write the five reports under ``settings.vendor_dir() / "craneware"``.

    ``newline=""`` on the write, matching
    :func:`recon.generators.orchestrator.write_outputs`.  Without it Python's text mode
    translates every ``\\n`` to ``os.linesep``, so the same input produces a different file
    on Windows than on Linux — and a reproducibility manifest that hashes those bytes
    becomes a statement about the operating system rather than about the data.
    """
    directory = settings.vendor_dir() / CRANEWARE_SUBDIR
    directory.mkdir(parents=True, exist_ok=True)

    written: dict[str, Path] = {}
    for report, text in render(source).items():
        path = directory / REPORT_FILENAMES[report]
        path.write_text(text, encoding="utf-8", newline="")
        written[report] = path
    return written


def declared_record_count(text: str) -> int:
    """Read the count the trailer declares, without re-implementing the parse.

    Requirement F2 compares this against what was actually ingested.  It lives here, beside
    the writer, so the declared-count contract has exactly one definition — a connector that
    reconstructed it by counting lines would agree with this file right up until the day the
    file was truncated mid-transfer, which is the one day the number matters.
    """
    for row in csv.DictReader(io.StringIO(text, newline="")):
        if row.get("record_type") == TRAILER_RECORD_TYPE:
            return int(row[TRAILER_COLUMN])
    raise ValueError(
        f"no {TRAILER_RECORD_TYPE} row found; every Craneware report ends with one, so a "
        "file without it was truncated or is not a Craneware export"
    )


# ═══ the five reports ═══════════════════════════════════════════════════════════════


def _claims_rows(dispenses: Sequence[Dispense]) -> list[dict[str, str]]:
    """One row per dispense, whatever became of it, in the order the sidecar listed them.

    All four archetypes land here, because every dispense does.  ``rebate_amount`` is the
    payment batch's own text carried across untouched; a reversed dispense that was paid
    keeps the amount it was paid and is marked ``REVERSED`` beside it, rather than having
    the figure negated or a second row emitted.  See :data:`REVERSAL_REPRESENTATION`.
    """
    rows: list[dict[str, str]] = []
    for dispense in dispenses:
        reversal = dispense.event("DISPENSE_REVERSAL")
        rows.append(
            {
                "covered_entity_id": _text(dispense.covered_entity_id),
                "rx_number": _text(dispense.rx_number_craneware),
                "pharmacy_npi": _text(dispense.pharmacy_npi),
                "provider_npi": _text(dispense.provider_npi),
                "ndc11": _text(dispense.ndc_11),
                "fill_date": _text(dispense.fill_date),
                "manufacturer": _text(dispense.manufacturer),
                "qualification_status": _text(dispense.qualification_status),
                "disqualification_reason": _text(dispense.disqualification_reason),
                "manufacturer_status": _text(dispense.manufacturer_status),
                "rejection_reason": _text(dispense.rejection_reason),
                # A rename of a lookup, not a verdict: ``is_reversed`` is "is there a
                # DISPENSE_REVERSAL event", nothing more.
                "dispense_status": (
                    DISPENSE_STATUS_REVERSED if dispense.is_reversed else DISPENSE_STATUS_ACTIVE
                ),
                "reversal_date": _text(_get(reversal, "received_at")),
                "reversal_reason": _text(_get(reversal, "reversal_reason")),
                # Already signed negative on the TPA feed.  Copied, never re-signed — the
                # generator learned that lesson the hard way; see tpa.py's reversal record.
                "reversal_quantity": _text(_get(reversal, "quantity_dispensed")),
                "rebate_submitted_date": _text(dispense.submitted_at),
                # TEXT.  Copied from the payment batch verbatim.
                "rebate_amount": _text(dispense.rebate_amount),
                "rebate_payment_date": _text(dispense.payment_effective_date),
                "rebate_allocation_code": _text(dispense.allocation_code),
            }
        )
    return rows


def _ordering_problems_rows(dispenses: Sequence[Dispense]) -> list[dict[str, str]]:
    """Dispenses that failed one of the two independent approvals — one row per failure.

    **Selected by reading the status fields, deliberately not by** ``by_archetype("rejected")``.
    :attr:`Dispense.archetype` gives ``reversed`` precedence over ``rejected``, so a dispense
    that was both would be classified ``reversed`` and would silently vanish from this
    report.  A problems report that omits a problem is the exact failure this repository
    keeps finding: clean-looking output with a hole in it.  The two status fields are read
    back directly instead, which loses nothing.

    A dispense that failed at both gates produces two rows, one per stage, rather than one
    row that has to pick a winner.
    """
    rows: list[dict[str, str]] = []
    for dispense in dispenses:
        qualification = dispense.event("QUALIFICATION_DECISION")
        decision = dispense.event("MANUFACTURER_DECISION")
        if dispense.qualification_status == "NOT_QUALIFIED":
            rows.append(
                _ordering_problem_row(
                    dispense,
                    stage=PROBLEM_STAGE_QUALIFICATION,
                    status=_text(dispense.qualification_status),
                    # Verbatim, never paraphrased — requirement C3 says so about the
                    # rejection reason and the same argument covers this one: a reason we
                    # reworded is a reason the vendor cannot be held to.
                    code=_text(dispense.disqualification_reason),
                    reported_at=_get(qualification, "received_at"),
                )
            )
        if dispense.manufacturer_status == "REJECTED":
            rows.append(
                _ordering_problem_row(
                    dispense,
                    stage=PROBLEM_STAGE_MANUFACTURER,
                    status=_text(dispense.manufacturer_status),
                    code=_text(dispense.rejection_reason),
                    reported_at=_get(decision, "received_at"),
                )
            )
    return rows


def _ordering_problem_row(
    dispense: Dispense,
    *,
    stage: str,
    status: str,
    code: str,
    reported_at: Any,
) -> dict[str, str]:
    """``hin`` and ``wholesaler_invoice_number`` ride the qualification event only.

    They are read from there rather than from the dispense, because :class:`Dispense` does
    not surface them — and they are on this report rather than on Claims because they are
    the two fields that make "Ordering Problems" mean anything in replenishment terms.
    """
    qualification = dispense.event("QUALIFICATION_DECISION")
    return {
        "covered_entity_id": _text(dispense.covered_entity_id),
        "rx_number": _text(dispense.rx_number_craneware),
        "pharmacy_npi": _text(dispense.pharmacy_npi),
        "provider_npi": _text(dispense.provider_npi),
        "ndc11": _text(dispense.ndc_11),
        "fill_date": _text(dispense.fill_date),
        "manufacturer": _text(dispense.manufacturer),
        "problem_stage": stage,
        "problem_status": status,
        "problem_code": code,
        "hin": _text(_get(qualification, "hin")),
        "wholesaler_invoice_number": _text(_get(qualification, "wholesaler_invoice_number")),
        "reported_date": _text(reported_at),
    }


def _unreplenished_costs_rows(dispenses: Sequence[Dispense]) -> list[dict[str, str]]:
    """Dispenses that qualified and have not been paid — the honest analogue of the name.

    ``CRANEWARE-002`` is a search-index summary, not a retrieved source, and all it suggests
    is that the real report is keyed by NDC and filterable by replenishment status.  Both
    are honoured; neither is claimed as ``SPEC``.

    "Qualified and unpaid" is two field reads.  It is narrower than the ``unmatched``
    archetype on purpose: a dispense still awaiting its qualification decision has not been
    found replenishable yet, so listing it as an unreplenished cost would assert something
    the TPA has not decided.  Those rows are still visible — on Claims and on Audit.
    """
    rows: list[dict[str, str]] = []
    for dispense in dispenses:
        if dispense.qualification_status != "QUALIFIED" or dispense.is_paid:
            continue
        rows.append(
            {
                "ndc11": _text(dispense.ndc_11),
                "covered_entity_id": _text(dispense.covered_entity_id),
                "rx_number": _text(dispense.rx_number_craneware),
                "pharmacy_npi": _text(dispense.pharmacy_npi),
                "provider_npi": _text(dispense.provider_npi),
                "fill_date": _text(dispense.fill_date),
                "manufacturer": _text(dispense.manufacturer),
                "replenishment_status": _replenishment_status(dispense),
                "qualification_status": _text(dispense.qualification_status),
                "rebate_submitted_date": _text(dispense.submitted_at),
            }
        )
    return rows


def _replenishment_status(dispense: Dispense) -> str:
    """Where this unpaid dispense actually stopped.  Three single-field reads, in order.

    Rejection is checked first because it is terminal: once the manufacturer has said no,
    the submission date on the row is history rather than a thing still in flight, and a
    status derived from the submission date alone would read it as the latter.
    """
    if dispense.manufacturer_status == "REJECTED":
        return REPLENISHMENT_REJECTED
    if dispense.submitted_at is not None:
        return REPLENISHMENT_SUBMITTED
    return REPLENISHMENT_AWAITING_SUBMISSION


def _audit_rows(dispenses: Sequence[Dispense]) -> list[dict[str, str]]:
    """Every TPA event for every dispense, in arrival order, flattened one per row.

    ``source.py`` already sorted each dispense's events by ``received_at``, so the sequence
    number below numbers the trail rather than imposing an order on it.  It restarts at 1
    per dispense because it is a position within one claim's history, not a file offset.

    This is the one report where a reversal is its own row.  That is correct for a journal
    and wrong for a snapshot, which is why the Claims Report does the opposite — and why a
    connector must pick one of the two as its claim source rather than reading both.
    """
    rows: list[dict[str, str]] = []
    for dispense in dispenses:
        for sequence, event in enumerate(dispense.events, start=1):
            rows.append(
                {
                    "event_sequence": str(sequence),
                    "record_id": _text(event.get("record_id")),
                    "source_system": _text(event.get("source_system")),
                    "event_type": _text(event.get("event_type")),
                    "event_timestamp": _text(event.get("received_at")),
                    "covered_entity_id": _text(event.get("covered_entity_id")),
                    "rx_number": _text(event.get("rx_number")),
                    "pharmacy_npi": _text(event.get("pharmacy_npi")),
                    "provider_npi": _text(event.get("provider_npi")),
                    "ndc11": _text(event.get("ndc_11")),
                    "fill_date": _text(event.get("fill_date")),
                    "event_outcome": _text(_first_present(event, _AUDIT_OUTCOME_KEYS)),
                    "event_detail": _text(_first_present(event, _AUDIT_DETAIL_KEYS)),
                }
            )
    return rows


def _bulk_dispensations_rows(dispenses: Sequence[Dispense]) -> list[dict[str, str]]:
    """Aggregated by covered entity, NDC and manufacturer.  Counts only, never a total.

    Manufacturer is part of the grouping key rather than a value carried along, so no row
    ever has to choose between two manufacturers for the same NDC.  Whether that can happen
    is not this module's call to make, and building the key so the question cannot arise is
    cheaper than deciding it.

    The four count columns are where all four archetypes are provably present:
    :attr:`Dispense.archetype` is the same read-back
    :meth:`VendorSource.by_archetype` filters on, so a test asserting coverage through
    ``by_archetype`` and this report are reading the same classification.

    ``earliest_fill_date`` and ``latest_fill_date`` are ``min``/``max`` over the wire's own
    ``YYYYMMDD`` strings.  Fixed width with no separator, so lexicographic order *is*
    chronological order and no date is ever parsed, reformatted or compared as a date.
    Selecting an existing value is not deriving one.
    """
    groups: dict[tuple[str, str, str], list[Dispense]] = {}
    for dispense in dispenses:
        key = (dispense.covered_entity_id, dispense.ndc_11, dispense.manufacturer)
        groups.setdefault(key, []).append(dispense)

    rows: list[dict[str, str]] = []
    for (covered_entity_id, ndc11, manufacturer), members in groups.items():
        fill_dates = sorted(member.fill_date for member in members)
        # A tally of rows by archetype.  Integer counts of records, which is the only
        # aggregation this module is permitted to do.
        counts = Counter(member.archetype for member in members)
        row = {
            "covered_entity_id": _text(covered_entity_id),
            "ndc11": _text(ndc11),
            "manufacturer": _text(manufacturer),
            # Integer counts of rows.  Not money, and deliberately the only aggregation
            # this module performs.
            "dispense_count": str(len(members)),
            "earliest_fill_date": _text(fill_dates[0]) if fill_dates else "",
            "latest_fill_date": _text(fill_dates[-1]) if fill_dates else "",
        }
        # Every archetype gets a column on every row, including the ones this group has
        # none of.  A zero that is written down is a fact; a column that disappears when
        # the count is zero makes an absent archetype indistinguishable from an absent
        # column, which is what requirement M5 is measuring.
        for archetype in ARCHETYPES:
            row[f"{archetype}_count"] = str(counts.get(archetype, 0))
        rows.append(row)
    return rows


# ═══ the CSV envelope ═══════════════════════════════════════════════════════════════


def _file_columns(report: str) -> tuple[str, ...]:
    """The control columns wrap the report's own: two in front, one at the back.

    Front and back rather than all together, so a reader that slices off the controls to get
    at the payload takes a contiguous window, and so the trailer's count is never mistaken
    for a payload column while scanning left to right.
    """
    return CONTROL_COLUMNS + REPORT_COLUMNS[report] + (TRAILER_COLUMN,)


def _render_csv(report: str, rows: Sequence[dict[str, str]]) -> str:
    """Header row, detail rows, then a trailer declaring how many detail rows there were.

    ``\\n`` line endings explicitly, rather than letting :mod:`csv` default to ``\\r\\n`` on
    Windows, for the reason :func:`recon.generators.bank.write_bank_csv` gives: the same
    input has to produce the same bytes on every platform or the hashes stop meaning
    anything.

    ``extrasaction="raise"`` so a row carrying a column this report does not declare fails
    loudly here.  The alternative is a field that is silently dropped on the way out, which
    is the kind of gap nobody finds until a reconciliation is short and nobody knows why.
    """
    columns = _file_columns(report)
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(
        buffer, fieldnames=list(columns), lineterminator="\n", extrasaction="raise"
    )
    writer.writeheader()

    blanks = {column: "" for column in columns}
    for row in rows:
        payload = dict(blanks)
        payload.update(row)
        payload["record_type"] = DETAIL_RECORD_TYPE
        payload["schema_version"] = SCHEMA_VERSION
        payload[TRAILER_COLUMN] = ""
        writer.writerow(payload)

    # The trailer counts the detail rows above it and does not count itself.  Requirement
    # F2 fails a batch when this disagrees with what was ingested, so it has to be the
    # number of things a reader is expected to find, not the number of lines in the file.
    trailer = dict(blanks)
    trailer["record_type"] = TRAILER_RECORD_TYPE
    trailer["schema_version"] = SCHEMA_VERSION
    trailer[TRAILER_COLUMN] = str(len(rows))
    writer.writerow(trailer)

    return buffer.getvalue()


# ═══ read-backs ═════════════════════════════════════════════════════════════════════


def _text(value: Any) -> str:
    """``None`` becomes an empty cell, never the literal ``"null"``.

    Same call :mod:`recon.generators.bank` makes about ``trn02``: a CSV export writes an
    empty cell, and a connector that had to special-case the four characters ``null`` would
    be coping with our formatting rather than with the vendor's.
    """
    return "" if value is None else str(value)


def _get(record: dict[str, Any] | None, key: str) -> Any:
    """Read a key off an event that may not have arrived at all.

    A missing event and an event with a null field are both absence, and both produce an
    empty cell.  Distinguishing them here would mean deciding which absence means what,
    which is upstream's job.
    """
    return None if record is None else record.get(key)


def _first_present(record: dict[str, Any], keys: Iterable[str]) -> Any:
    """The first of ``keys`` this record actually carries a value for.

    A fixed list searched in order — a lookup, not an interpretation.  An event carrying
    none of them yields ``None``, and therefore an empty cell, rather than a value invented
    to fill the column.
    """
    for key in keys:
        value = record.get(key)
        if value is not None:
            return value
    return None
