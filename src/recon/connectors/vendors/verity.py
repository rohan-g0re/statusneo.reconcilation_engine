"""Verity's two registered datasets, as column maps and one declared reversal rule.

Requirement D2, read through requirement D1. There is no parsing in this file and there must
never be any: the reader, the trailer split, the control total and the reversal counting all
live in :mod:`recon.connectors.vendors`, and what is here is the part that is genuinely
Verity's — how it spells things, and how it says a dispense was reversed. If this module ever
grows a ``csv`` import, D1's acceptance (*"Verity and Craneware differ by a config row and a
mapping module, nothing else"*) has stopped being true.

**The transport and the dataset names are Verity's; every field is ours.** ``DOC2-009`` names
the five Secure Data Export datasets and the SFTP cadence; ``VERITY-001`` confirms first-hand
that Verity Secure Data Transfer exists, is automated and is encrypted — it does not say SFTP.
``VERITY-006`` confirms a named *Split Transaction data specification* exists and was extended
for the rebate model, and that specification is referenced and never published. No field list
for any of the five datasets was retrievable from anywhere, so the column names below are the
ones ``src/recon/mocks/verity_export.py`` writes, tagged ``INVENTED`` one by one in
``src/recon/mocks/MOCK_FIELDS.md``. Two of the five datasets are mapped here, because two of
the five have registered schema contracts; the other three get mappings when they get
contracts.

═══ Requirement B3 — how Verity says a dispense was reversed ═══════════════════════

``DOC2-011`` states in as many words that Verity's reversal and requalification
representation is not publicly confirmed. So it was decided, and
:data:`REVERSAL_REPRESENTATION` below is that decision — read from the module that writes the
files rather than re-typed here, because two copies of a string can disagree and one cannot.

A reversal is a **flag plus its own detail on the one row for the dispense**. There is never
a second row, and no amount is ever negated. Four columns carry it, and they are present on
all five datasets including the ones where they can only ever be empty.

**What a connector written for the other representation does wrong, concretely.** Suppose it
was built against a vendor that represents a reversal as a negative-quantity row — the
original stays on the wire and a correcting row arrives beside it. Point it at
``verity_invoices_20260409T130000Z.csv`` and it does the natural thing: it walks the detail
rows looking for a negative ``invoice_line_amount``, finds none in the file, and concludes
nothing was reversed. In the demo export that is four reversed dispenses read as zero. Each
still carries its full ``invoice_line_amount`` — ``1290.80``, ``26500.00``, ``6948.00`` and
``900.00`` — with ``reversal_status = REVERSED`` four columns to the right, so all four go
onto the ledger as rebates the covered entity no longer holds. **The rebates are kept and the
ledger is four rebates long.** The trailer count agrees, the control total agrees, nothing is
quarantined, and the variance surfaces months later as an unexplained receivable.

The mirror failure is just as quiet and is why :class:`~recon.connectors.vendors.FlagReversal`
refuses a non-flag representation at import rather than at parse time. A connector written
against *this* file — one that nets the line out whenever ``reversal_status`` is ``REVERSED``
— pointed at a negative-row vendor nets the reversal once from the flag and once from the
correcting row, and **the ledger ends up one rebate short.** Double-count in one direction,
lost rebate in the other; both look clean in every report afterwards, which is the entire
reason B3 exists.

**The hazard is live in this repository, not theoretical.** ``recon.generators.tpa`` writes a
reversal to ``tpa_340b_events.jsonl`` as a separate ``DISPENSE_REVERSAL`` record carrying a
negative ``quantity_dispensed``, following NCPDP (``STD-NCPDP-TELECOM``), while this export
carries a flag — over the same episode. Both representations are genuinely present in one
build, so a mapping that assumed the wrong one here is demonstrably wrong rather than
theoretically wrong.

``reversal_quantity`` is that already-negative figure copied across untouched — the demo
export's four are ``-28``, ``-80``, ``-30`` and ``-30``. It is never re-signed here. Negating
it a second time once put a reversal on the wire as a positive quantity and silently
destroyed the one property the feed spec insists on.
"""

from __future__ import annotations

from recon.connectors.vendors import FlagReversal, SecureFileMapping, mappings_by_source_id
from recon.mocks import verity_export

__all__ = [
    "VENDOR",
    "REVERSAL_REPRESENTATION",
    "REVERSED_STATUS",
    "ACCUMULATIONS",
    "INVOICES",
    "MAPPINGS",
]

#: As ``registry.Source.vendor`` spells it.
VENDOR = "verity"

#: **Requirement B3, declared rather than inferred — and read, not re-typed.**
#:
#: ``verity_export.REVERSAL_REPRESENTATION`` is ``"STATUS_FLAG_ROW"``. Importing the constant
#: rather than copying the six characters is the difference between a declaration and a
#: comment: if the writer ever changes its mind, this module changes with it, and
#: :class:`~recon.connectors.vendors.FlagReversal` refuses at import anything that is no
#: longer a flag. A re-typed copy would have gone on reading a column that no longer means
#: what it used to, and reported zero reversals with no error anywhere.
#:
#: Importing :mod:`recon.mocks` for a *constant* is the intended direction and importing it
#: for *data* is not. Nothing here reads a mock object; the connector reads documents.
#: ``tests/test_mocks.py`` enforces the other direction — no mock may import
#: ``recon.connectors`` — so the data layer still stands alone with this package deleted,
#: which is requirement M6.
REVERSAL_REPRESENTATION: str = verity_export.REVERSAL_REPRESENTATION

#: The literal in ``reversal_status`` that means reversed, read back from the writer for the
#: same reason. Note Verity leaves the cell **empty** for a dispense that was not reversed,
#: where Craneware writes ``ACTIVE``. The rule keys on this positive literal, so one rule
#: serves both spellings of "no reversal".
REVERSED_STATUS: str = verity_export.REVERSED_STATUS

#: The four reversal columns, spelled as this vendor spells them. Craneware calls the first
#: two ``dispense_status`` and ``reversal_date``; the second of those is a full timestamp
#: despite its name, which is exactly the kind of difference a field map exists to absorb.
_REVERSAL = FlagReversal(
    column="reversal_status",
    reversed_value=REVERSED_STATUS,
    representation=REVERSAL_REPRESENTATION,
    received_at_column="reversal_received_at",
    reason_column="reversal_reason",
    quantity_column="reversal_quantity",
)

#: The five components of the natural 340B key, under this fabric's spelling, in the order
#: ``recon.mocks.source.Dispense.natural_key`` uses. Both Verity datasets key the same way.
_NATURAL_KEY = ("rx_number", "pharmacy_npi", "provider_npi", "ndc11", "fill_date")

#: Columns whose canonical spelling is the vendor's own, shared by both datasets. Listed once
#: so the two field maps below differ only where the datasets genuinely differ.
_SHARED_FIELDS: dict[str, str] = {
    "beacon_id": "beacon_id",
    "covered_entity_id": "covered_entity_id",
    "manufacturer": "manufacturer",
    # ``ndc_11`` here, ``ndc11`` on Craneware. The same concept under two spellings, and the
    # single most load-bearing line in this file: a connector that assumed one spelling
    # across both vendors reads ``None`` and joins nothing, silently.
    "ndc_11": "ndc11",
    "fill_date": "fill_date",
    "rx_number": "rx_number",
    "pharmacy_npi": "pharmacy_npi",
    "provider_npi": "provider_npi",
    "accumulation_id": "accumulation_id",
    "reversal_status": "reversal_status",
    "reversal_received_at": "reversal_received_at",
    "reversal_reason": "reversal_reason",
    "reversal_quantity": "reversal_quantity",
}

#: The dispenses the TPA qualified, and therefore the dataset carrying the 340B join keys.
#:
#: ``qualification_status`` can only be ``QUALIFIED`` here — the population is selected on it
#: — which is why the registered contract marks it required on this dataset and optional
#: elsewhere, and why contracts are per dataset rather than per vendor.
ACCUMULATIONS = SecureFileMapping(
    source_id="verity_accumulations",
    vendor=VENDOR,
    dataset="accumulations",
    filename_prefix="verity_accumulations_",
    fields={
        **_SHARED_FIELDS,
        "prescriber_npi": "prescriber_npi",
        "hin": "hin",
        "wholesaler_invoice_number": "wholesaler_invoice_number",
        "qualification_status": "qualification_status",
        "qualification_received_at": "qualification_received_at",
    },
    natural_key_fields=_NATURAL_KEY,
    reversal=_REVERSAL,
    # The qualification decision is the event this dataset reports, so the moment it was
    # decided is the moment the row became true. The alternative on this file is
    # ``fill_date``, which is when the drug was dispensed — days to weeks earlier, and not a
    # fact about when Verity told us anything.
    received_at_column="qualification_received_at",
)

#: The rebate money: one row per paid dispense, drawn from the lines inside a payment batch.
#: The Verity dataset that touches the ledger, which makes it the one where a shape change
#: costs cash rather than context.
INVOICES = SecureFileMapping(
    source_id="verity_invoices",
    vendor=VENDOR,
    dataset="invoices",
    filename_prefix="verity_invoices_",
    fields={
        **_SHARED_FIELDS,
        "invoice_number": "invoice_number",
        "rebate_allocation_code": "rebate_allocation_code",
        # **Deliberately not folded into Craneware's ``manufacturer_decision_status``.** This
        # one is read off the payment batch line; that one is read off a standalone decision
        # event. A dispense that was paid usually has no decision event at all, so a
        # connector that collapsed the two would either report every paid rebate as
        # undecided or invent an agreement the feed does not have.
        "batch_line_manufacturer_status": "batch_line_manufacturer_status",
        # The batch line's own ``rebate_amount``, which is the same figure Craneware prints
        # under ``rebate_amount``. Text, copied. Nothing in this package adds two amounts.
        "invoice_line_amount": "rebate_amount",
        "batch_total_rebate_amount": "batch_total_rebate_amount",
        # When the money is dated. ``batch_received_at`` is when the batch record arrived,
        # and they are genuinely different things — reconciling against the wrong one puts a
        # payment in the wrong period.
        "payment_effective_date": "payment_effective_date",
        "batch_received_at": "batch_received_at",
    },
    natural_key_fields=_NATURAL_KEY,
    reversal=_REVERSAL,
    # ``batch_received_at`` and not ``payment_effective_date``, and the distinction is the
    # one the field comment above already draws: effective date is when the money counts,
    # arrival is when we learned of it. The pipeline orders by arrival because a cursor asks
    # *what did we know by now* — so dating these rows by effective date would let an
    # evaluation see a payment before the batch announcing it had landed.
    received_at_column="batch_received_at",
)

#: Every Verity dataset this fabric can read, by source id.
MAPPINGS = mappings_by_source_id((ACCUMULATIONS, INVOICES))
