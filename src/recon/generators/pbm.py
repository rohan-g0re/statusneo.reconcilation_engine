"""The PBM generator: NCPDP adjudication events, and the 835 that pays them.

Two entry points, because in reality these are two systems on different rails arriving
days to weeks apart (Decision 12).  Collapsing them into one file would hand the
connector a payment already attached to its claim and delete the entire reassociation
problem.

This generator knows: the NCPDP transaction key, the plan, the drug, what it adjudicated,
and what it is paying.  It does **not** know the episode id, the verdict, the expected
amount *we* computed, anything about the bank, or whether the dispense qualifies for 340B
— a real PBM knows none of those either.  The one 340B-adjacent thing it sees is NCPDP
420-DK (``submission_clarification_code = 20``), which is a flag the *pharmacy* put on its
own claim, not an identifier the PBM owns.

═══ Three things here create work downstream, deliberately ═════════════════════════

**CLP01 is ``"7845102FILL00"``.**  Not a claim id — the pharmacy's own Rx number with the
fill number glued on after the literal word ``FILL``.  That is NCPDP's documented
convention for pharmacy 835s, and taking it apart again is a real crosswalk step rather
than a join.  The round trip lives in :mod:`recon.crosswalk.keys` so both sides agree.

**TRN02 is the only link to the bank.**  When the CCD+ addenda survive, matching is
trivial; when they do not, all that is left is amount and date.  This generator always
emits it — losing it is the realizer's decision, because the loss happens in transit.

**PLB does not balance to any claim.**  A ``WO`` of $412.60 is a clawback for some
*earlier* claim, netted out of today's payment, so
``Σ(CLP04) − Σ(PLB, signed) = BPR02 = what hits the bank``.  The bank shows only the net,
and nothing on the bank side says a clawback happened.  This is the most realistic
reconciliation trap in the dataset and it is why bank amount will not equal the sum of
claim payments.
"""

from __future__ import annotations

import random
from typing import Any, Sequence

from recon.config import PBM_CLAIM_EVENTS_FILE, PBM_REMITTANCE_835_FILE
from recon.crosswalk import keys
from recon.generators.contracts import (
    MoneyMovement,
    PbmClaimSlice,
    PbmRemittanceSlice,
    Record,
)
from recon.money import format_amount
from recon.reference import codes, drugs, entities

__all__ = ["gen_pbm_claim_events", "gen_pbm_remittance"]


def gen_pbm_claim_events(slices: Sequence[PbmClaimSlice]) -> list[Record]:
    """Emit NCPDP B1 adjudications and B2 reversals.

    One record per transaction, never a claim summary: a reversal is its own transaction
    and it carries **no new identity of its own**, matching the original by repeating
    ``{service_provider_id, prescription_ref_number, fill_number, date_of_service}``.
    That is exactly how the real B2 works, and it means the connector has to match on the
    composite key rather than follow a foreign key.
    """
    records: list[Record] = []
    counter = 0
    for claim in slices:
        counter += 1
        records.append(_b1_record(claim, counter))
        if claim.reversal is not None:
            counter += 1
            records.append(_b2_record(claim, counter))
    return records


def _b1_record(claim: PbmClaimSlice, sequence: int) -> Record:
    pbm = entities.pbm_by_id(claim.pbm_id)
    drug = drugs.by_ndc(claim.ndc11)
    rng = random.Random(claim.rng_seed)

    payload: dict[str, Any] = {
        "record_id": f"PBM-EVT-{sequence:06d}",
        "source_system": "PBM_ADJUDICATION",
        "transaction_code": "B1",
        "received_at": claim.received_at,
        "service_provider_id": claim.pharmacy_npi,
        "service_provider_id_qualifier": "01",
        "prescription_ref_number": claim.rx_number,
        "fill_number": claim.fill_number,
        "date_of_service": keys.iso_date_to_wire(claim.date_of_service),
        "product_service_id": claim.ndc11,
        "product_service_id_qualifier": "03",
        # NCPDP 442-E7 carries three implied decimals: 30.000 units is "030000".
        "quantity_dispensed": f"{claim.quantity_milli:06d}",
        "days_supply": f"{claim.days_supply:03d}",
        "prescriber_id": claim.prescriber_npi,
        "prescriber_id_qualifier": "01",
        "bin": pbm.bin,
        "pcn": pbm.pcn,
        "group_id": pbm.group_id,
        "cardholder_id": claim.cardholder_id,
        "person_code": claim.person_code,
    }
    if claim.is_340b_flagged:
        payload["submission_clarification_code"] = "20"

    if claim.accepted:
        ingredient = claim.ingredient_cost_paid_cents or 0
        fee = claim.dispensing_fee_paid_cents or 0
        patient = claim.patient_pay_amount_cents or 0
        payload.update(
            response_status="P",
            authorization_number=_mint_authorization_number(claim, rng),
            ingredient_cost_paid=_money(ingredient),
            dispensing_fee_paid=_money(fee),
            patient_pay_amount=_money(patient),
            total_amount_paid=_money(ingredient + fee - patient),
            reject_codes=[],
        )
    else:
        # Two PBMs, two reject vocabularies, one canonical meaning.  This is Decision 33
        # made concrete: adding a third PBM adds mapping rows, not engine branches.
        wire_code = _reject_wire_code(claim.pbm_id, claim.reject_canonical)
        payload.update(
            response_status="R",
            authorization_number=None,
            ingredient_cost_paid=None,
            dispensing_fee_paid=None,
            patient_pay_amount=None,
            total_amount_paid=None,
            reject_codes=[wire_code],
        )
    return Record(feed_file=PBM_CLAIM_EVENTS_FILE, payload=payload, slice_ref=claim.slice_ref)


def _b2_record(claim: PbmClaimSlice, sequence: int) -> Record:
    """A reversal.  Repeats the transaction key; invents no identity of its own."""
    reversal = claim.reversal
    assert reversal is not None
    pbm = entities.pbm_by_id(claim.pbm_id)
    rng = random.Random(claim.rng_seed)
    payload: dict[str, Any] = {
        "record_id": f"PBM-EVT-{sequence:06d}",
        "source_system": "PBM_ADJUDICATION",
        "transaction_code": "B2",
        "received_at": reversal.received_at,
        "service_provider_id": claim.pharmacy_npi,
        "prescription_ref_number": claim.rx_number,
        "fill_number": claim.fill_number,
        "date_of_service": keys.iso_date_to_wire(claim.date_of_service),
        "product_service_id": claim.ndc11,
        "bin": pbm.bin,
        "pcn": pbm.pcn,
        "response_status": "P",
        "authorization_number": _mint_authorization_number(claim, rng) if claim.accepted else None,
        "reversal_reason": reversal.reversal_reason,
    }
    return Record(feed_file=PBM_CLAIM_EVENTS_FILE, payload=payload, slice_ref=reversal.slice_ref)


def gen_pbm_remittance(batch: PbmRemittanceSlice) -> tuple[Record, MoneyMovement]:
    """Emit one whole 835 file — one payment covering many claims — and declare the money.

    The BPR total is computed **here**, from the lines, because this is the one place it
    is formatted.  The slice carries no precomputed total, so there is no second copy free
    to disagree with the wire (Decision 44).

        BPR02 = Σ(CLP04) − Σ(PLB, signed)

    A positive PLB reduces the deposit; ``L6``/``RA`` are negative because they increase
    it.  Getting that sign backwards inverts the arithmetic on every remittance, which is
    why the sign lives in a reference table rather than in this function.
    """
    pbm = entities.pbm_by_id(batch.pbm_id)
    claim_payments: list[dict[str, Any]] = []
    claim_total = 0

    for line in batch.claim_lines:
        claim_total += line.payment_cents
        claim_payments.append(
            {
                # The NCPDP pharmacy-835 convention: Rx number + "FILL" + fill number.
                "clp01_patient_control_number": keys.format_clp01_pharmacy(
                    line.rx_number, line.fill_number
                ),
                # Settlement rides here and nowhere else (Decision 40).
                "clp02_claim_status_code": line.clp02_status_code,
                "clp03_total_charge": _money(line.charge_cents),
                "clp04_payment_amount": _money(line.payment_cents),
                "clp05_patient_responsibility": _money(line.patient_responsibility_cents),
                "clp06_claim_filing_indicator": "CI",
                "clp07_payer_claim_control_number": _mint_payer_claim_control_number(
                    batch, line.slice_ref
                ),
                "ref_authorization_number": line.authorization_number,
                "service_line": {
                    "product_id_qualifier": "N4",
                    "product_id": line.ndc11,
                    "charge_amount": _money(line.charge_cents),
                    "paid_amount": _money(line.payment_cents),
                    "quantity": str(line.quantity_milli // 1000),
                    "date_of_service": keys.iso_date_to_wire(line.date_of_service),
                },
                "adjustments": [
                    {
                        "group_code": adjustment.group_code,
                        "reason_code": adjustment.reason_code,
                        "amount": _money(adjustment.amount_cents),
                    }
                    for adjustment in line.adjustments
                ],
            }
        )

    plb_signed_total = 0
    provider_level: list[dict[str, Any]] = []
    for entry in batch.provider_level_adjustments:
        semantics = codes.plb_semantics(entry.reason_code)
        signed = entry.amount_cents * semantics.sign
        plb_signed_total += signed
        provider_level.append(
            {
                "reason_code": entry.reason_code,
                # Absent by design on FB and untraceable CS lines: that residual has to be
                # tracked as unexplained rather than silently absorbed (D-7), and it is
                # what makes a recoupment untraceable to a deposit (A-13 rather than A-12).
                "reference_id": entry.reference_id,
                "amount": _money(signed),
            }
        )

    bpr_total = claim_total - plb_signed_total

    payload: dict[str, Any] = {
        "record_id": f"PBM-835-{batch.sequence:06d}",
        "source_system": "PBM_REMITTANCE",
        "received_at": batch.received_at,
        "bpr": {
            "transaction_handling_code": "I",
            "total_actual_provider_payment": _money(bpr_total),
            "credit_debit_flag": "C",
            "payment_method_code": "ACH",
            "payment_effective_date": keys.iso_date_to_wire(batch.payment_effective_date),
        },
        "trn": {
            "trace_type_code": "1",
            # The only thread back to the bank — when the addenda survive the trip.
            "reassociation_trace_number": batch.reassociation_trace_number,
            "originating_company_id": pbm.originating_company_id,
        },
        "payer_name": pbm.payer_name,
        "payee_npi": batch.payee_npi,
        "claim_payments": claim_payments,
    }
    if provider_level:
        payload["provider_level_adjustments"] = provider_level

    record = Record(
        feed_file=PBM_REMITTANCE_835_FILE, payload=payload, slice_ref=batch.slice_ref
    )
    movement = MoneyMovement(
        slice_ref=batch.slice_ref,
        amount_cents=bpr_total,
        payer_reference=batch.reassociation_trace_number,
        company_name=_truncate_company_name(pbm.name),
        company_id=pbm.company_id,
        ach_routing_prefix=pbm.ach_routing_prefix,
        # A CORE/CAQH mandate for claim EFT specifically, which is what lets a connector
        # classify a bank line as a claim payment before parsing a dollar amount.
        entry_description="HCCLAIMPMT",
        effective_date=batch.payment_effective_date,
    )
    return record, movement


# ═══ identifier namespaces this generator owns ══════════════════════════════


def _mint_authorization_number(claim: PbmClaimSlice, rng: random.Random) -> str:
    """An opaque, PBM-specific authorization number.

    The PBM's namespace, and it is the value that **never reaches the TPA** — which is why
    the only bridge from a 340B record to a pharmacy claim is the natural key.
    """
    return f"AUTH{rng.randint(1_000_000, 9_999_999):07d}{chr(65 + rng.randint(0, 25))}"


#: ICNs are rendered in the payer's own 14-digit space.  The prefix is arbitrary and
#: opaque on purpose: an ICN carries no business content a connector may rely on.
_ICN_BASE = 20_260_610_000_000


def _mint_payer_claim_control_number(batch: PbmRemittanceSlice, line_ref: str) -> str:
    """The payer's own ICN.  Opaque, payer-assigned, reassigned on reprocessing.

    Derived from the slice ref with an explicit character sum rather than ``hash()``,
    which is salted per process by ``PYTHONHASHSEED`` and would make the dataset
    irreproducible between two runs on the same machine.
    """
    digest = sum(ord(char) * (index + 1) for index, char in enumerate(line_ref))
    return f"{_ICN_BASE + (batch.rng_seed + digest) % 9_999_999:014d}"


def _reject_wire_code(pbm_id: str, canonical: str | None) -> str:
    """Map canonical reject semantics to this PBM's own wire spelling."""
    if canonical is None:
        canonical = "NOT_COVERED"
    for reject in codes.reject_codes_for(pbm_id):
        if reject.canonical == canonical:
            return reject.code
    raise ValueError(f"PBM {pbm_id} has no code for canonical reject {canonical!r}")


def _truncate_company_name(name: str) -> str:
    """NACHA caps the company name at 16 characters, and real exports show the truncation.

    Not a defect — a field limit.  It is carried faithfully because a connector matching on
    company name has to cope with ``BLUE HARBOR HEAL``.
    """
    return name[:16]


def _money(cents: int) -> str:
    """Money on the wire is fixed two-decimal text, rendered from integer cents.

    Never ``str(float(...))``: a float round trip through ``47218.40`` leaves a residue
    indistinguishable from a real variance, and this is a reconciliation dataset.
    """
    return format_amount(cents)
