"""The 340B/TPA generator: qualification, rebate request, manufacturer decision, reversal.

This is deliberately the structurally **poorest** of the four feeds.  The PBM track has
NCPDP and an 835.  The medical track has an 837 and an 835.  The bank track has NACHA.
340B has none of that: no 835, no trace number, no standards body underneath it at all —
just a TPA vendor's own portal export, with event names ("QUALIFICATION_DECISION",
"REBATE_REQUEST") that the vendor invented rather than inherited.  The PBM's
``authorization_number`` (see :mod:`recon.generators.pbm`) never reaches here, because the
two systems do not talk, so the **natural key** — ``{pharmacy_npi, rx_number, ndc_11,
fill_date}`` — is the only bridge a connector has back to a pharmacy claim.

That is not a modelling shortcut.  It is the actual state of this part of the industry:
340B administration runs on vendor portals with no shared identifier space, and a feed
with no shared identifier space is structurally prone to losing the thread.  That is
exactly why "unmatched rebate" is one of the assignment's own named exceptions rather than
something hand-placed into this generator — it falls out of the mechanism.

═══ Two independent decision-makers ═════════════════════════════════════════════════

A 340B rebate has **two** separate approvals, made by two separate systems that do not
coordinate:

1. The **TPA** decides *qualification* — is this dispense even eligible for 340B pricing,
   given the prescriber's affiliation and the covered entity's registration?  That is
   ``QUALIFICATION_DECISION``.
2. The **manufacturer** decides *approval*, and pays, once a rebate request reaches them —
   that is ``MANUFACTURER_DECISION`` and ``REBATE_PAYMENT_BATCH``.

A dispense that is ``QUALIFIED`` at the TPA and then ``REJECTED`` by the manufacturer (a
non-conforming 45-day submission window, say) is a real and common state, not an edge
case — it is where most of the dispute volume in 340B rebate administration actually
lives, and the two decisions can arrive weeks apart with nothing connecting them but the
natural key.

═══ The medical-benefit shape ═══════════════════════════════════════════════════════

A medically-administered drug — infused at the covered entity's own clinic rather than
dispensed by a pharmacy — has no prescription, so there is nothing pharmacy-shaped to key
on: ``rx_number`` and ``pharmacy_npi`` are both ``null``, and ``provider_npi`` (the
administering site's billing NPI, which also appears on the 837) carries the identity
instead.  The vendor column is still called ``fill_date`` even on these records, even
though it carries the administration date — the export schema was built for pharmacy
and reused for medical.  That is realistic, not sloppy: TPA vendors did not build a
second schema for a smaller line of business.

The resulting join key, ``{provider_npi, ndc_11, fill_date}``, is strictly weaker than the
pharmacy key: two administrations of the same drug at the same site on the same day are
genuinely indistinguishable on this feed alone.  A connector has to park that as
ambiguous rather than guess through it — guessing is how a reconciliation tool quietly
starts lying.

═══ The money side ══════════════════════════════════════════════════════════════════

``gen_rebate_batch`` returns a :class:`~recon.generators.contracts.MoneyMovement` whose
``payer_reference`` is the batch's ``allocation_code`` — the same column (``trn02``) and
the same roughly 20% addenda-loss odds as a PBM claim payment's reassociation trace
number, but a **different issuer**: the payer here is the manufacturer, not the health
plan.  ``trn02`` is semantically "the payer-assigned business reference from the
addenda", and that meaning does not change with who the payer is.

``entry_description`` is ``"CCD"``, deliberately **not** ``"HCCLAIMPMT"``.  The
CORE/CAQH operating rule that mandates ``HCCLAIMPMT`` applies to claim EFTs specifically;
a manufacturer rebate is not a claim payment under that mandate, so it rides the bank as
a plain corporate credit.  That is a named defect in waiting: a connector that filters
the bank feed to "claim payments only" by checking for ``HCCLAIMPMT`` will silently skip
every rebate deposit, and nothing on the bank side will say so.
"""

from __future__ import annotations

from typing import Any, Sequence

from recon.config import TPA_340B_EVENTS_FILE
from recon.crosswalk import keys
from recon.generators.contracts import (
    MoneyMovement,
    RebateBatchSlice,
    RebateDispenseLineSlice,
    Record,
    TpaDispenseSlice,
)
from recon.money import format_amount
from recon.reference import entities

__all__ = ["gen_tpa_events", "gen_rebate_batch"]


def gen_tpa_events(slices: Sequence[TpaDispenseSlice]) -> list[Record]:
    """Emit up to four record types per slice, each only when its field is populated.

    A single dispense can be mid-flight through several of these at once — qualified,
    request submitted, decision still pending — so each stage gets checked and emitted
    independently rather than assumed to follow from the last.  The ordering below
    (qualification, request, decision, reversal) is the realistic arrival order, which
    keeps the sequence counter reading the way an export actually would.
    """
    records: list[Record] = []
    sequence = 0
    for dispense in slices:
        # Gated on the *arrival* of a qualification row, not on it carrying a decision.  A
        # dispense sitting in review is a row with a null status, and without that row nothing on
        # the wire separates "awaiting the first gate" (C-01) from "not a 340B dispense" (C-00).
        if dispense.qualification_received_at is not None:
            sequence += 1
            records.append(_qualification_record(dispense, sequence))
        if dispense.request_received_at is not None:
            sequence += 1
            records.append(_rebate_request_record(dispense, sequence))
        if dispense.decision_received_at is not None:
            sequence += 1
            records.append(_manufacturer_decision_record(dispense, sequence))
        if dispense.reversal_received_at is not None:
            sequence += 1
            records.append(_reversal_record(dispense, sequence))
    return records


def _qualification_record(dispense: TpaDispenseSlice, sequence: int) -> Record:
    """TPA_PORTAL decides eligibility.  Carries the full natural key plus the verdict.

    ``provider_npi`` rides alongside ``rx_number``/``pharmacy_npi`` on every record —
    ``None`` on a pharmacy dispense, populated on a medical-benefit one — rather than
    being a field that only sometimes exists on the payload, matching how the wire
    export actually behaves (a fixed column set, some of it null).
    """
    payload: dict[str, Any] = {
        "record_id": f"TPA-EVT-{sequence:06d}",
        "source_system": "TPA_PORTAL",
        "event_type": "QUALIFICATION_DECISION",
        "received_at": dispense.qualification_received_at,
        "rx_number": dispense.rx_number,
        "pharmacy_npi": dispense.pharmacy_npi,
        "provider_npi": dispense.provider_npi,
        "ndc_11": dispense.ndc11,
        # Still called fill_date on a medical-benefit record — see module docstring.
        "fill_date": keys.iso_date_to_wire(dispense.fill_date),
        "prescriber_npi": dispense.prescriber_npi,
        "covered_entity_id": dispense.covered_entity_id,
        "hin": dispense.hin,
        "wholesaler_invoice_number": dispense.wholesaler_invoice_number,
        "qualification_status": dispense.qualification_status,
        "disqualification_reason": dispense.disqualification_reason,
    }
    return Record(feed_file=TPA_340B_EVENTS_FILE, payload=payload, slice_ref=dispense.slice_ref)


def _rebate_request_record(dispense: TpaDispenseSlice, sequence: int) -> Record:
    """The TPA submits a rebate request to the manufacturer.  Still TPA_PORTAL —
    the manufacturer has not spoken yet; that is the next record, if it ever arrives.
    """
    assert dispense.submission_date is not None
    payload: dict[str, Any] = {
        "record_id": f"TPA-EVT-{sequence:06d}",
        "source_system": "TPA_PORTAL",
        "event_type": "REBATE_REQUEST",
        "received_at": dispense.request_received_at,
        "rx_number": dispense.rx_number,
        "pharmacy_npi": dispense.pharmacy_npi,
        "provider_npi": dispense.provider_npi,
        "ndc_11": dispense.ndc11,
        "fill_date": keys.iso_date_to_wire(dispense.fill_date),
        "covered_entity_id": dispense.covered_entity_id,
        "submission_date": keys.iso_date_to_wire(dispense.submission_date),
        "manufacturer": dispense.manufacturer_short_name,
    }
    return Record(feed_file=TPA_340B_EVENTS_FILE, payload=payload, slice_ref=dispense.slice_ref)


def _manufacturer_decision_record(dispense: TpaDispenseSlice, sequence: int) -> Record:
    """The manufacturer's own verdict — the second, independent decision-maker.

    Source system flips to MANUFACTURER_REBATE here: this is no longer the TPA's own
    system of record speaking, it is the manufacturer's, arriving on its own schedule.
    A rejection has to stand alone as a record — it cannot ride inside a payment batch,
    because a rejected line is never paid — so this event type exists even when the
    eventual outcome is ``REJECTED`` and no money ever moves for this dispense.
    """
    payload: dict[str, Any] = {
        "record_id": f"TPA-EVT-{sequence:06d}",
        "source_system": "MANUFACTURER_REBATE",
        "event_type": "MANUFACTURER_DECISION",
        "received_at": dispense.decision_received_at,
        "rx_number": dispense.rx_number,
        "pharmacy_npi": dispense.pharmacy_npi,
        "provider_npi": dispense.provider_npi,
        "ndc_11": dispense.ndc11,
        "fill_date": keys.iso_date_to_wire(dispense.fill_date),
        "covered_entity_id": dispense.covered_entity_id,
        "manufacturer": dispense.manufacturer_short_name,
        "manufacturer_status": dispense.manufacturer_status,
        "rejection_reason": dispense.rejection_reason,
    }
    return Record(feed_file=TPA_340B_EVENTS_FILE, payload=payload, slice_ref=dispense.slice_ref)


def _reversal_record(dispense: TpaDispenseSlice, sequence: int) -> Record:
    """A dispense reversal — negative quantity, not a delete, same convention NCPDP
    uses for a pharmacy-claim reversal (:mod:`recon.generators.pbm`).  Deleting the
    original record would erase evidence a connector needs; negating the quantity keeps
    both the original dispense and the correction on the wire, in their own order.
    """
    assert dispense.reversal_quantity_milli is not None
    # The vendor's own column carries whole units here, not NCPDP's three-implied-decimal
    # convention, so the milli figure this generator receives is converted down to a
    # plain signed unit count before it goes on the wire.
    quantity_units = -(dispense.reversal_quantity_milli // 1000)
    payload: dict[str, Any] = {
        "record_id": f"TPA-EVT-{sequence:06d}",
        "source_system": "TPA_PORTAL",
        "event_type": "DISPENSE_REVERSAL",
        "received_at": dispense.reversal_received_at,
        "rx_number": dispense.rx_number,
        "pharmacy_npi": dispense.pharmacy_npi,
        "provider_npi": dispense.provider_npi,
        "ndc_11": dispense.ndc11,
        "fill_date": keys.iso_date_to_wire(dispense.fill_date),
        "covered_entity_id": dispense.covered_entity_id,
        "quantity_dispensed": quantity_units,
        "reversal_reason": dispense.reversal_reason,
    }
    return Record(feed_file=TPA_340B_EVENTS_FILE, payload=payload, slice_ref=dispense.slice_ref)


def gen_rebate_batch(batch: RebateBatchSlice) -> tuple[Record, MoneyMovement]:
    """Emit one manufacturer payment batch, covering many dispenses, and declare the money.

    ``total_rebate_amount`` is computed **here**, by summing this batch's own dispense
    lines, because this is the one place it is formatted — the slice carries no
    precomputed total, so there is no second copy of the figure free to disagree with the
    wire.  The same rule governs the PBM 835's ``BPR02`` in :mod:`recon.generators.pbm`;
    340B just has a flatter total with no PLB-style clawback to net out.
    """
    manufacturer = _manufacturer_by_short_name(batch.manufacturer_short_name)

    dispenses: list[dict[str, Any]] = []
    total_cents = 0
    for line in batch.dispense_lines:
        total_cents += line.rebate_amount_cents
        dispenses.append(_dispense_line_payload(line))

    payload: dict[str, Any] = {
        "record_id": f"TPA-EVT-{batch.sequence:06d}",
        "source_system": "MANUFACTURER_REBATE",
        "event_type": "REBATE_PAYMENT_BATCH",
        "received_at": batch.received_at,
        "manufacturer": batch.manufacturer_short_name,
        "allocation_code": batch.allocation_code,
        "total_rebate_amount": format_amount(total_cents),
        "payment_effective_date": keys.iso_date_to_wire(batch.payment_effective_date),
        "dispenses": dispenses,
    }
    record = Record(feed_file=TPA_340B_EVENTS_FILE, payload=payload, slice_ref=batch.slice_ref)

    movement = MoneyMovement(
        slice_ref=batch.slice_ref,
        amount_cents=total_cents,
        # Rides to the bank in the same trn02 column a claim payment's reassociation
        # trace number uses, under the same ~20% addenda-loss odds, but issued by the
        # manufacturer rather than the health plan — see module docstring.
        payer_reference=batch.allocation_code,
        company_name=_truncate_company_name(manufacturer.name),
        company_id=manufacturer.company_id,
        ach_routing_prefix=manufacturer.ach_routing_prefix,
        # Not HCCLAIMPMT: that code is a CORE/CAQH mandate for claim EFTs specifically,
        # and a rebate is not a claim payment under it.  Plain CCD is correct here, and
        # is exactly what lets a "claim payments only" filter miss this deposit entirely.
        entry_description="CCD",
        effective_date=batch.payment_effective_date,
    )
    return record, movement


# ═══ identifier namespaces this generator owns ══════════════════════════════════════


def _dispense_line_payload(line: RebateDispenseLineSlice) -> dict[str, Any]:
    """One line of a payment batch.  Medical-benefit lines use the same null-rx,
    null-pharmacy-npi, populated-provider-npi shape as their qualification record.
    """
    return {
        "rx_number": line.rx_number,
        "pharmacy_npi": line.pharmacy_npi,
        "provider_npi": line.provider_npi,
        "ndc_11": line.ndc11,
        "fill_date": keys.iso_date_to_wire(line.fill_date),
        "covered_entity_id": line.covered_entity_id,
        "manufacturer_status": line.manufacturer_status,
        "rebate_amount": format_amount(line.rebate_amount_cents),
    }


def _manufacturer_by_short_name(short_name: str) -> entities.Manufacturer:
    """The slice carries the TPA's own vocabulary — a short name, not the reference
    entity id — because that is genuinely what a TPA export would print.  This looks the
    reference entity up the other way round rather than asking the orchestrator to leak
    an id the TPA never sees.
    """
    for manufacturer in entities.manufacturers():
        if manufacturer.short_name == short_name:
            return manufacturer
    raise ValueError(f"no manufacturer with short_name {short_name!r}")


def _truncate_company_name(name: str) -> str:
    """NACHA caps the company name at 16 characters; see pbm.py for the full rationale.

    Carried faithfully rather than normalised away, because a connector matching bank
    deposits by company name has to cope with the truncated form on the wire.
    """
    return name[:16]


