"""The medical-benefit generator: 837 institutional/professional claims (plus the
277CA that rides the same rail), and the 835 that pays them.

Two entry points, the same reason as :mod:`recon.generators.pbm`: in reality the
provider's claim submission and the payer's remittance are different systems on
different rails, arriving days to weeks apart.  Collapsing them would hand the
connector a payment already attached to its claim and delete the reassociation
problem this dataset exists to exercise.

This generator knows: the provider's own claim key, the drug and its administration,
what the clearinghouse said about the submission, and what the payer is paying.  It
does **not** know the episode id, the verdict, or the expected amount *we* computed —
a real clearinghouse or payer system knows none of those either.

═══ The identifier that actually matters: CLM01 vs CLP01 vs CLP07 ══════════════════

**CLM01** (Patient Control Number) is assigned by the **provider** on the 837 — it is
the provider's own internal claim key, and it never changes across the whole life of a
claim, including every resubmission and every reprocess.

**CLP01**, on the 835, simply echoes CLM01 back.  **CLP07** is a completely different
thing: the payer's own Internal Control Number (ICN/DCN), assigned payer-side, and a
reprocessed claim typically gets a **brand-new CLP07** every time even though CLP01
never moves.  That distinction is the crux of this whole feed: a connector that treats
CLP07 as a stable claim identifier will silently fork one claim's history into
unrelated pieces the first time it is reprocessed.  ``MedicalClaimLineSlice`` names
this directly with ``assign_new_icn`` — true for a reprocessed claim — but nothing in
this module has to branch on that flag to get it right: every claim-line slice already
carries its own ``slice_ref``, the ICN is minted from that ref, and a reprocessed claim
is, by construction, a *new* slice for the same CLM01.  A fresh ref mints a fresh ICN
automatically; the flag exists to make the orchestrator's intent legible, not to drive
a conditional here.

═══ A frequency-7 replacement is a full replacement, not a patch ═══════════════════

CLM05-3 carries the frequency code: ``1`` original, ``7`` replacement, ``8`` void.
Whatever a ``7`` leaves out is dropped from the payer's record — there is no merge.
``MedicalSubmissionSlice.drops_a_service_line`` models exactly this: when it is
``True``, this generator emits the drug line alone and omits the administration line a
non-dropping claim would otherwise carry alongside it, because that is what "full
replacement" means on the wire.  A void (frequency ``8``) carries no clinical content
at all on its own 837 — it exists only to cancel a payer ICN via ``REF*F8`` — which
matches the worked example in ``docs/feed_formats.md`` exactly.

═══ Medical 835s always balance ═════════════════════════════════════════════════════

``CLP03 = CLP04 + Σ(adjustments)`` holds on every claim line this generator emits,
with no exception.  That is a deliberate contrast with the pharmacy track, where
``PbmClaimLineSlice.is_underpayment_defect`` lets a line *not* balance — the pharmacy
underpayment defect is specifically that the arithmetic itself is wrong.  On the
medical side the dispute a connector has to surface is never "does this add up"; it is
always "why was it reduced" — a ``CO-197`` absent-prior-auth paired with an ``N522``
RARC, say.  Reusing the pharmacy's unbalanced-line mechanism here would normalise two
genuinely different failure modes into one, so this generator simply never produces
one.

═══ PLB sits outside every claim loop, same mechanism as the pharmacy side ═════════

``BPR02 = Σ(CLP04) − Σ(PLB, signed)``, computed here from this batch's own lines, for
the same reason :mod:`recon.generators.pbm` computes it from its own: the slice never
carries a precomputed total, so there is exactly one place the figure is formatted and
no second copy free to disagree with it.  The sign convention is unchanged — a
positive PLB (``WO``, ``FB``, ``CS``, ``72``) reduces the deposit; a negative one
(``L6`` interest owed, ``RA`` carried as an appeal credit) increases it.  An infusion
claim's administration lines (``96413``, then ``96415`` for a second) behave exactly
as the field reference describes: they carry no NDC, because CPT administration codes
are not drugs.
"""

from __future__ import annotations

from typing import Any, Sequence

from recon.config import MEDICAL_835_REMITTANCE_FILE, MEDICAL_837_SUBMISSIONS_FILE
from recon.crosswalk import keys
from recon.generators.contracts import (
    MedicalAcknowledgmentSlice,
    MedicalRemittanceSlice,
    MedicalSubmissionSlice,
    MoneyMovement,
    Record,
)
from recon.money import format_amount
from recon.reference import codes, entities

__all__ = ["gen_medical_submissions", "gen_medical_remittance"]


#: CPT administration codes, in the order a second administration line is added.  They
#: carry no NDC because they describe the act of infusing, not the drug itself — the
#: field reference is explicit that administration lines "legitimately carry no NDC".
_ADMINISTRATION_CPT_CODES: tuple[str, ...] = ("96413", "96415")

#: Nominal per-encounter administration charge and paid amount, by administration-line
#: index.  Nothing in any slice supplies these — an infusion centre's charge master
#: sets them independently of the drug's own charge — so this generator mints fixed,
#: documented figures rather than inventing a precomputed one per call.  They are
#: deliberately **not** rolled into the claim-level ``CLP03``/``CLP04`` totals, which
#: are fixed facts carried on the slice for the drug line alone; real institutional
#: billing would fold an administration charge into the claim total, but
#: ``MedicalClaimLineSlice`` carries one combined figure by design (Decision 44 —
#: facts live on the slice), so the line-level split beneath that figure is this
#: generator's own formatting choice.  Fully allowed, no adjustment, is the simplest
#: choice that is still realistic: these codes bundle cleanly and payers rarely reduce
#: them.
_ADMINISTRATION_CHARGE_CENTS: tuple[int, ...] = (15_000, 9_000)
_ADMINISTRATION_PAID_CENTS: tuple[int, ...] = (15_000, 9_000)

#: ICNs render in the payer's own 14-digit space, matching the worked examples'
#: ``20260610......`` shape.  The prefix is arbitrary and opaque on purpose: an ICN
#: carries no business content a connector may rely on.
_ICN_BASE = 20_260_610_000_000


def gen_medical_submissions(slices: Sequence[MedicalSubmissionSlice]) -> list[Record]:
    """Emit 837 claim submissions and their 277CA acknowledgments into one file.

    Both record types land in ``medical_837_submissions.jsonl`` because the 277CA
    rides the same rail as the 837 it is acknowledging: a clearinghouse rejection
    (verdict B-01) is otherwise indistinguishable on the wire from "accepted, awaiting
    835" (B-02) — both would be an 837 with no 835 anywhere.  One shared sequence
    numbers everything emitted, matching the worked example's adjacent
    ``MED-837-000501`` / ``MED-277-000502`` pair.

    An ordinary 837 never carries ``record_type``; the 277CA always does.  That single
    field is the only thing that tells the two apart on the wire.
    """
    records: list[Record] = []
    sequence = 0
    for submission in slices:
        sequence += 1
        records.append(_submission_837_record(submission, sequence))
        if submission.acknowledgment is not None:
            sequence += 1
            records.append(
                _acknowledgment_277ca_record(submission, submission.acknowledgment, sequence)
            )
    return records


def _submission_837_record(submission: MedicalSubmissionSlice, sequence: int) -> Record:
    payer = entities.payer_by_id(submission.payer_id)
    payload: dict[str, Any] = {
        "record_id": f"MED-837-{sequence:06d}",
        "source_system": "CLEARINGHOUSE_837",
        "received_at": submission.received_at,
        "clm01_patient_control_number": submission.clm01,
        "clm05_3_frequency_code": submission.frequency_code,
        "ref_f8_original_icn": submission.ref_f8_original_icn,
        # Loop 2010BB, the payer the claim is being submitted to.  Every real 837 names its
        # payer — a claim has to be addressed to someone — and without it nothing downstream
        # can say what this claim was *worth*, because the contracted rate is a property of
        # the (payer, drug) pair.  An earlier version omitted this loop, which left the
        # medical expected amount uncomputable from the feeds alone.
        "loop_2010bb": {
            "nm103_payer_name": payer.name,
            "nm109_payer_id": payer.trn03,
        },
    }

    # A void (frequency 8) carries no clinical content of its own — it exists purely
    # to cancel the payer ICN named in REF*F8, exactly as the worked example shows.
    # Adding service lines to a void would invent detail no real void carries.
    if submission.frequency_code == "8":
        return Record(
            feed_file=MEDICAL_837_SUBMISSIONS_FILE,
            payload=payload,
            slice_ref=submission.slice_ref,
        )

    service_lines: list[dict[str, Any]] = [
        {
            "line_number": 1,
            "svc01_composite": f"HC:{submission.hcpcs_j_code}:JW",
            "svc02_charge_amount": _money(submission.charge_cents),
            "units": submission.svc05_units,
        }
    ]
    # A non-dropping claim always carries the administration line alongside the drug
    # line: an infusion encounter bills the act of infusing separately from the
    # product.  `drops_a_service_line` is exactly the frequency-7 defect where a
    # replacement leaves this line out — a full replacement, not a patch, so the
    # omission is a deletion rather than something preserved from the original.
    if not submission.drops_a_service_line:
        service_lines.append(
            {
                "line_number": 2,
                "svc01_composite": f"HC:{_ADMINISTRATION_CPT_CODES[0]}",
                "svc02_charge_amount": _money(_ADMINISTRATION_CHARGE_CENTS[0]),
                "units": 1,
            }
        )

    payload.update(
        billing_provider_npi=submission.billing_provider_npi,
        rendering_provider_npi=submission.rendering_provider_npi,
        date_of_service=keys.iso_date_to_wire(submission.date_of_service),
        service_lines=service_lines,
        loop_2410={
            "lin02_qualifier": "N4",
            "ndc11": submission.ndc11,
            # CTP04 renders as text on the wire even though the slice carries an int —
            # the worked example shows "400", not 400.
            "ctp04_quantity": str(submission.ctp04_quantity),
            "ctp05_uom_qualifier": submission.ctp05_uom_qualifier,
        },
    )
    return Record(
        feed_file=MEDICAL_837_SUBMISSIONS_FILE, payload=payload, slice_ref=submission.slice_ref
    )


def _acknowledgment_277ca_record(
    submission: MedicalSubmissionSlice,
    acknowledgment: MedicalAcknowledgmentSlice,
    sequence: int,
) -> Record:
    """The 277CA.  ``record_type`` is the one field that separates it from an 837.

    The payer ICN is minted here, on acceptance only, because that is the one place in
    this feed a payer control number is first assigned — a rejection never gets one.
    """
    icn = None
    if acknowledgment.assigns_payer_claim_control_number:
        icn = _mint_icn(submission.rng_seed, acknowledgment.slice_ref)

    payload: dict[str, Any] = {
        "record_id": f"MED-277-{sequence:06d}",
        "source_system": "CLEARINGHOUSE_837",
        "record_type": "277CA",
        "received_at": acknowledgment.received_at,
        "clm01_patient_control_number": submission.clm01,
        "stc01_composite": acknowledgment.stc01_composite,
        "stc12_free_form": acknowledgment.stc12_free_form,
        "payer_claim_control_number": icn,
    }
    return Record(
        feed_file=MEDICAL_837_SUBMISSIONS_FILE,
        payload=payload,
        slice_ref=acknowledgment.slice_ref,
    )


def gen_medical_remittance(batch: MedicalRemittanceSlice) -> tuple[Record, MoneyMovement]:
    """Emit one whole 835 file — one payment covering many claims — and declare the money.

    The BPR total is computed **here**, from the lines, for the same reason
    :func:`recon.generators.pbm.gen_pbm_remittance` computes its own: the slice never
    carries a precomputed total, so this is the one place the figure is formatted and
    there is no second copy free to disagree with it::

        BPR02 = Σ(CLP04) − Σ(PLB, signed)

    Every claim line balances: ``CLP03 = CLP04 + Σ(adjustments)`` always, with no
    exception.  That is the medical side's deliberate contrast with the pharmacy
    underpayment defect (``PbmClaimLineSlice.is_underpayment_defect``) — here the
    reconciliation question a connector has to answer is never "does the arithmetic
    add up", it is always "why was the claim reduced", which is exactly what the CARC
    and RARC codes on each adjustment exist to answer.

    ``MedicalClaimLineSlice.assign_new_icn`` documents a reprocessed claim: CLP01
    (echoing CLM01) stays fixed across the claim's whole life while CLP07 — the
    payer's own ICN — gets a brand-new value.  This function does not need to branch
    on that flag to get it right: the ICN is minted from the line's own ``slice_ref``,
    and a reprocessed claim is, by construction, a new slice for the same CLM01, so a
    fresh ref already mints a fresh ICN.  A connector that instead treated CLP07 as
    stable claim identity would fork this claim's history the first time it saw two
    different ICNs under one CLM01 — which is correct payer behaviour, not two
    unrelated claims.
    """
    payer = entities.payer_by_id(batch.payer_id)
    claim_payments: list[dict[str, Any]] = []
    claim_total = 0

    for line in batch.claim_lines:
        claim_total += line.payment_cents

        service_lines: list[dict[str, Any]] = [
            {
                "svc01_composite": f"HC:{line.hcpcs_j_code}:JW",
                "svc02_charge": _money(line.charge_cents),
                "svc03_paid": _money(line.payment_cents),
                "svc05_units": line.svc05_units,
            }
        ]
        # Zero to two CPT administration lines, legitimately carrying no NDC: an
        # infusion claim is one J-code drug line plus these.
        for index in range(line.administration_line_count):
            service_lines.append(
                {
                    "svc01_composite": f"HC:{_ADMINISTRATION_CPT_CODES[index]}",
                    "svc02_charge": _money(_ADMINISTRATION_CHARGE_CENTS[index]),
                    "svc03_paid": _money(_ADMINISTRATION_PAID_CENTS[index]),
                    "svc05_units": 1,
                }
            )

        claim_payments.append(
            {
                # CLP01 simply echoes CLM01 — the provider's key, stable for the
                # claim's whole life.  It is emphatically not the payer's ICN below.
                "clp01_patient_control_number": line.clm01,
                "clp02_status_code": line.clp02_status_code,
                "clp03_total_charge": _money(line.charge_cents),
                "clp04_payment_amount": _money(line.payment_cents),
                "clp05_patient_responsibility": _money(line.patient_responsibility_cents),
                # The payer's own ICN, opaque and reassigned on every reprocess — see
                # the module and function docstrings for why `assign_new_icn` needs no
                # branch here.
                "clp07_payer_claim_control_number": _mint_icn(batch.rng_seed, line.slice_ref),
                "service_lines": service_lines,
                "adjustments": [_adjustment_payload(adjustment) for adjustment in line.adjustments],
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
                # Absent by design on FB and untraceable CS lines (the D-7 residual);
                # the medical 835's own field name is `reference_icn`, matching the
                # worked example, where the pharmacy 835 spells the same idea
                # `reference_id`.
                "reference_icn": entry.reference_id,
                "amount": _money(signed),
            }
        )

    bpr_total = claim_total - plb_signed_total

    payload: dict[str, Any] = {
        "record_id": f"MED-835-{batch.sequence:06d}",
        "source_system": "MEDICAL_REMITTANCE",
        "received_at": batch.received_at,
        "bpr": {
            "bpr02_total_payment": _money(bpr_total),
            "bpr04_payment_method": "ACH",
            "bpr16_eft_effective_date": keys.iso_date_to_wire(batch.eft_effective_date),
        },
        "trn": {
            "trn01": "1",
            "trn02_trace_number": batch.trace_number,
            # "1" + TIN is TRN03's own render rule; the payer entity carries it as a
            # property precisely so this generator never re-derives it by hand.
            "trn03_payer_tin": payer.trn03,
        },
        "claim_payments": claim_payments,
    }
    if provider_level:
        payload["provider_level_adjustments"] = provider_level

    record = Record(feed_file=MEDICAL_835_REMITTANCE_FILE, payload=payload, slice_ref=batch.slice_ref)
    movement = MoneyMovement(
        slice_ref=batch.slice_ref,
        amount_cents=bpr_total,
        payer_reference=batch.trace_number,
        company_name=_truncate_company_name(payer.name),
        company_id=payer.company_id,
        ach_routing_prefix=payer.ach_routing_prefix,
        # HCCLAIMPMT is a CORE/CAQH mandate for claim EFT specifically, letting a
        # connector classify a bank line as a claim payment before parsing an amount.
        entry_description="HCCLAIMPMT",
        effective_date=batch.eft_effective_date,
    )
    return record, movement


def _adjustment_payload(adjustment: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "group_code": adjustment.group_code,
        "reason_code": adjustment.reason_code,
        "amount": _money(adjustment.amount_cents),
    }
    if adjustment.rarc is not None:
        payload["rarc"] = adjustment.rarc
    return payload


# ═══ identifier namespaces this generator owns ══════════════════════════════


def _mint_icn(seed: int, ref: str) -> str:
    """The payer's own ICN/DCN.  Opaque, payer-assigned, reassigned on reprocessing.

    Derived from a seed plus an explicit character sum of the ref rather than the
    builtin ``hash()``, which is salted per process by ``PYTHONHASHSEED`` and would
    make the dataset irreproducible between two runs on the same machine.  Two
    distinct refs — two distinct submissions, two distinct claim-line slices — mint
    two distinct ICNs, which is exactly what lets a reprocessed claim (a new slice
    under the same CLM01) get a genuinely new CLP07 without this module tracking any
    claim history of its own.
    """
    digest = sum(ord(char) * (index + 1) for index, char in enumerate(ref))
    return f"{_ICN_BASE + (seed + digest) % 9_999_999:014d}"



def _truncate_company_name(name: str) -> str:
    """NACHA caps the company name at 16 characters, and real exports show the truncation.

    Not a defect — a field limit.  It is carried faithfully because a connector
    matching on company name has to cope with ``BLUE HARBOR HEAL``, the exact
    truncation of ``BLUE HARBOR HEALTH``.
    """
    return name[:16]


def _money(cents: int) -> str:
    """Money on the wire is fixed two-decimal text, rendered from integer cents.

    Never ``str(float(...))``: a float round trip leaves a residue indistinguishable
    from a real variance, and this is a reconciliation dataset whose whole behaviour
    depends on that distinction staying clean.
    """
    return format_amount(cents)
