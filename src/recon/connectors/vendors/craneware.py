"""Craneware's Claims Report, as a column map and one declared reversal rule.

Requirement D3, read through requirement D1 and ``DOC2-013``: *"Use Craneware as the second
file-based pattern after Verity; both can share the same secure-file connector framework."*
This module is the proof of that sentence. It holds no parsing, no trailer handling and no
reversal counting — all of that is in :mod:`recon.connectors.vendors` and is byte-for-byte
the same code Verity runs. What is here is Craneware's spelling and Craneware's declared
reversal representation, and nothing else. Put a ``csv`` import in this file and D1's
acceptance stops being true.

**The transport and the report names are Craneware's; every field is ours.**
``CRANEWARE-001`` is Craneware's own 14 June 2023 announcement, retrieved verbatim, naming
all five reports and the SFTP folder they land in — and naming no column of any of them.
``CRANEWARE-004`` adds Doc 2's transport line: file-level controls, file schema versioning
and daily ingestion. ``CRANEWARE-005`` records that no public customer API specification
exists, which is why this is a file pattern at all. The columns below are the ones
``src/recon/mocks/craneware_export.py`` writes, tagged ``INVENTED`` one by one in
``src/recon/mocks/MOCK_FIELDS.md``. One of the five reports is mapped here, because one of
the five has a registered schema contract.

═══ The one thing Craneware does that Verity does not ══════════════════════════════

Craneware writes ``schema_version`` onto **every row**, so the wire names its own contract and
:func:`~recon.connectors.vendors.schema_violation` passes it through to the registry. Verity
writes only a record count, so the registry has to be told and the current registration is
what a record is checked against. That single difference is why
:attr:`~recon.connectors.vendors.SecureFileMapping.schema_version_column` exists, and it is
also the concrete consequence: a Verity re-cut is not self-announcing, while a Craneware
re-cut arrives naming a version nobody registered and is parked as
``SCHEMA_VERSION_MISMATCH`` on the first row.

═══ Requirement B3 — how Craneware says a dispense was reversed ════════════════════

Craneware does not publish it — gap 4 of ``docs/vendor_evidence/craneware.md`` — so it was
decided, and :data:`REVERSAL_REPRESENTATION` below is that decision, read from the module
that writes the files rather than re-typed here.

A reversal is a **status flag on the restated row**. The Claims Report emits exactly one row
per dispense; a reversed dispense carries ``dispense_status = REVERSED`` plus the reversal's
own date, reason and signed quantity, and the ``rebate_amount`` beside it is the amount that
was actually paid, unchanged. There is never a second row and no amount is ever negated. The
argument is that a scheduled report is a snapshot of state as of the run rather than an event
journal, and the plausible thing for a snapshot to do is restate the row.

**What a connector written for the other representation does wrong, concretely.** Suppose it
was built against a vendor that represents a reversal as a negative-quantity row: the
original stays on the wire and a second, correcting row arrives beside it. Point it at
``claims_report.csv`` and it scans the detail rows for a negative quantity or a negative
amount, finds neither, and concludes nothing was reversed. In the demo export that is four
reversed dispenses read as zero. The first carries ``1290.80`` in ``rebate_amount`` and
``-28`` in ``reversal_quantity``, a column that connector never opens; the other three carry
``26500.00``, ``6948.00`` and ``900.00`` the same way. **The rebates are kept and the ledger
is four rebates long.** Nothing errors, no quarantine row is written, and every report
downstream is internally consistent and wrong.

The mirror failure is just as quiet. A connector written against *this* file — one that nets
the amount out whenever ``dispense_status`` is ``REVERSED`` — pointed at a negative-row vendor
nets the reversal once from the flag and once from the correcting row, and **the ledger ends
up one rebate short.** Double-count in one direction, lost rebate in the other.

**The hazard is live in this repository.** ``recon.generators.tpa`` writes the same episode's
reversal to ``tpa_340b_events.jsonl`` as a separate ``DISPENSE_REVERSAL`` record carrying a
negative ``quantity_dispensed``, per NCPDP (``STD-NCPDP-TELECOM``). Two representations, one
build, one episode — so a mapping that assumed the wrong one is demonstrably wrong rather
than theoretically wrong. ``reversal_quantity`` on this report is that already-negative figure
copied across untouched, and it is never re-signed here: negating it a second time once put a
reversal on the wire as a positive quantity and destroyed the one property the feed spec
insists on.

**The in-vendor exception, which is a third way to double-count.** Craneware's Audit report
*is* a journal, so a reversal appears there as its own event row. A connector must take
Claims **or** Audit as its claim source and never both — Audit is the trail, Claims is the
state, and reading both as claim rows counts every reversal twice. Only the Claims Report is
mapped here, and that is the reason.
"""

from __future__ import annotations

from recon.connectors.vendors import FlagReversal, SecureFileMapping, mappings_by_source_id
from recon.mocks import craneware_export

__all__ = [
    "VENDOR",
    "REVERSAL_REPRESENTATION",
    "REVERSED_STATUS",
    "CLAIMS_REPORT",
    "MAPPINGS",
]

#: As ``registry.Source.vendor`` spells it.
VENDOR = "craneware"

#: **Requirement B3, declared rather than inferred — and read, not re-typed.**
#:
#: ``craneware_export.REVERSAL_REPRESENTATION`` is ``"STATUS_FLAG_ON_ORIGINAL_ROW"``. It is a
#: different string from Verity's ``"STATUS_FLAG_ROW"`` and the difference is real rather
#: than cosmetic: Verity's file is an export of state, Craneware's is a *report* run, so the
#: row a Craneware reversal rides is a restatement rather than the original. Both put the
#: reversal on the dispense's own row, which is what
#: :class:`~recon.connectors.vendors.FlagReversal` checks; a representation that did not
#: would be refused at import of this module rather than found out at parse time.
#:
#: Importing :mod:`recon.mocks` for a *constant* is the intended direction and importing it
#: for *data* is not. Nothing here reads a mock object; the connector reads documents.
REVERSAL_REPRESENTATION: str = craneware_export.REVERSAL_REPRESENTATION

#: The ``dispense_status`` literal that means reversed, read back from the writer for the
#: same reason. Craneware writes ``ACTIVE`` for a dispense that was not reversed where Verity
#: leaves the cell empty — which is why the rule keys on this positive literal and not on
#: absence. A rule keyed on emptiness would report every row of this file as reversed.
REVERSED_STATUS: str = craneware_export.DISPENSE_STATUS_REVERSED

#: The five components of the natural 340B key, under this fabric's spelling, in the order
#: ``recon.mocks.source.Dispense.natural_key`` uses.
_NATURAL_KEY = ("rx_number", "pharmacy_npi", "provider_npi", "ndc11", "fill_date")

#: The report that carries both the money and the reversal flag, which makes it the one where
#: a shape change does the most damage before anyone notices.
CLAIMS_REPORT = SecureFileMapping(
    source_id="craneware_claims_report",
    vendor=VENDOR,
    dataset="Claims Report",
    # Stable and undated. Dating it would mean either inventing a cadence Craneware has not
    # published or stamping the run date into the bytes, and the second makes the same input
    # produce a different file on two different days. The transport may date the drop; the
    # payload does not — which is the opposite of Verity, whose name carries a data-derived
    # stamp, and is why the prefix is the whole name here.
    filename_prefix="claims_report.csv",
    fields={
        "covered_entity_id": "covered_entity_id",
        "rx_number": "rx_number",
        "pharmacy_npi": "pharmacy_npi",
        "provider_npi": "provider_npi",
        # ``ndc11`` here, ``ndc_11`` on Verity. The same concept under two spellings, and the
        # single most load-bearing line in this file: a connector that assumed one spelling
        # across both vendors reads ``None`` and joins nothing, silently.
        "ndc11": "ndc11",
        "fill_date": "fill_date",
        "manufacturer": "manufacturer",
        "qualification_status": "qualification_status",
        "disqualification_reason": "disqualification_reason",
        # Read off a standalone decision event, which is **not** the same thing as Verity's
        # ``batch_line_manufacturer_status`` read off a payment batch line. Kept as distinct
        # canonical names so that collapsing them cannot invent an agreement the feed does
        # not have: a dispense paid straight out of a batch has no decision event at all.
        "manufacturer_status": "manufacturer_decision_status",
        "rejection_reason": "rejection_reason",
        # The reversal flag. Verity calls the same fact ``reversal_status``.
        "dispense_status": "reversal_status",
        # Named ``reversal_date`` and typed ``timestamp``, because it is written from the
        # reversal event's ``received_at``. Mapped onto the canonical ``reversal_received_at``
        # so the name stops lying downstream; truncating it to a date would lose the time the
        # reversal actually arrived, which is the ordering the pipeline reads.
        "reversal_date": "reversal_received_at",
        "reversal_reason": "reversal_reason",
        "reversal_quantity": "reversal_quantity",
        "rebate_submitted_date": "rebate_submitted_date",
        # Text, copied. The same figure Verity prints as ``invoice_line_amount``.
        "rebate_amount": "rebate_amount",
        # When the money is dated. Verity spells it ``payment_effective_date``, which is also
        # what the TPA feed underneath calls it, so that is the canonical spelling.
        "rebate_payment_date": "payment_effective_date",
        "rebate_allocation_code": "rebate_allocation_code",
    },
    natural_key_fields=_NATURAL_KEY,
    reversal=FlagReversal(
        column="dispense_status",
        reversed_value=REVERSED_STATUS,
        representation=REVERSAL_REPRESENTATION,
        received_at_column="reversal_date",
        reason_column="reversal_reason",
        quantity_column="reversal_quantity",
    ),
    # Three control columns here against Verity's two, and the count sits at the back rather
    # than the front. Both are read by name, so neither position matters to this fabric —
    # which is the point of addressing columns by name rather than by index.
    declared_count_column="declared_record_count",
    schema_version_column="schema_version",
    # No ``received_at_column``, and that absence is the finding rather than an oversight.
    # The Claims Report publishes four temporal columns and every one of them is a *date*:
    # ``fill_date``, ``reversal_date``, ``rebate_submitted_date``, ``rebate_payment_date``.
    # None of them says when Craneware delivered the row. Promoting one — ``fill_date`` is
    # the tempting one, since it is the only required column of the four — would date a row
    # to the day the drug left the shelf and hide every day of TPA lag behind it, which is
    # the lag a 340B reconciliation exists to measure.
    #
    # So these rows inherit the delivery's fetch stamp. That is weaker than Verity's per-row
    # timestamp and it is honestly weaker: we know when the file landed and we do not know
    # when the row did. ``CRANEWARE-005`` records that no column list is published at all,
    # so this may change the day a real customer sends a real file — and it changes here, in
    # one declaration, not in the reader.
)

#: Every Craneware report this fabric can read, by source id.
MAPPINGS = mappings_by_source_id((CLAIMS_REPORT,))
