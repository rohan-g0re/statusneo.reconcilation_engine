"""The one contract all four generators build against (Decision 44).

Read this before any generator.  Everything here exists to enforce one property:

    **a generator knows only what its real source system would know.**

That is not style.  It is the whole reason the crosswalk is a real problem in this
dataset rather than an assumed one.  A generator that can see the episode id, or the
target verdict, or the other track's amounts, will leak a shared key across feeds —
accidentally is enough — and the connector under test degenerates into a no-op join
that proves nothing (Decision 10 / 11).

═══ The four rules ═════════════════════════════════════════════════════════════════

**1. No slice carries an episode id, a verdict code, a case id or a cross-track flag.**
:func:`assert_slice_is_blind` checks it by introspection and a test runs it over every
slice instance a full generation produces.  ``slice_ref`` is the opaque handle a
generator echoes back so the orchestrator can reattach the result; it is deliberately
not derived from anything meaningful.

**2. Division of labour.**  The orchestrator owns all *facts*: dates (``received_at``
included), per-claim and per-line amounts, and batch composition — which claim payments
share a remittance, which PLB entry attaches to which later batch, which dispenses share
a rebate batch.  The netting ledger is orchestrator machinery.

Generators own native *formatting* and their own identifier namespaces
(``authorization_number``, ``clp07``, ``ach_trace_number``, ``record_id``), plus their
channel's **wire arithmetic**.  A batch slice carries no precomputed net total: the
remittance generator computes ``BPR = Σ(CLP04) − Σ(PLB, signed)`` from its own lines,
and the rebate generator totals its own dispense lines.  The total exists exactly once,
in the place that formats it — so there is no second copy to disagree with.

**3. Money flows declare → realize → format.**  Payer-side generators return
:class:`MoneyMovement` — "this much money, under this reference, is leaving on this
date", net of PLB.  The orchestrator's *realizer* is the only place cash faults live: it
turns movements into :class:`CashEvent`, applying ``MATCHED`` / ``PARTIAL`` / ``ABSENT``,
injecting orphan deposits and true-reversal debits, and deciding whether ``trn02``
survived the trip.

``gen_bank`` is then a **pure formatter** of ``CashEvent[]``.  It never learns that a
deposit is missing, because it never sees an expected figure — an absent deposit is
simply a ``CashEvent`` that was never created.  That is what makes "remittance with no
cash" a property of the data rather than a flag someone set.

**4. No file back-channels.**  Generators talk to the orchestrator through return values
only.  The orchestrator invoked them; it already holds everything ground truth needs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Sequence

from recon.domain.enums import ReimbursementTrack

__all__ = [
    "Record",
    "MoneyMovement",
    "CashEvent",
    "PbmClaimSlice",
    "PbmRemittanceSlice",
    "PbmClaimLineSlice",
    "MedicalSubmissionSlice",
    "MedicalRemittanceSlice",
    "MedicalClaimLineSlice",
    "TpaDispenseSlice",
    "RebateBatchSlice",
    "RebateDispenseLineSlice",
    "PlbEntrySlice",
    "AdjustmentSlice",
    "FORBIDDEN_SLICE_FIELDS",
    "assert_slice_is_blind",
]


# ═══ what a generator returns ═══════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class Record:
    """One emitted record, ready to be written to a feed file.

    ``payload`` is the native shape — exactly what goes on the wire, already carrying
    its own ``received_at``.  ``slice_ref`` is echoed back from the slice that produced
    it so the orchestrator can attribute it for ground truth without the generator ever
    knowing what it is attributing to.
    """

    feed_file: str
    payload: dict[str, Any]
    slice_ref: str
    #: Set only for the deliberate D-1 duplicate-delivery defect: the same payload is
    #: emitted twice, with a later ``received_at`` on the second copy.
    is_duplicate_delivery: bool = False


@dataclass(frozen=True, slots=True)
class MoneyMovement:
    """A payer-side declaration that money is leaving, net of PLB.

    ``amount_cents`` is what the *payer* says will land — the BPR figure the remittance
    generator computed from its own lines.  It is emphatically not "what the episode
    expected": the gap between the two is the reconciliation problem.

    ``payer_reference`` is the trace the bank *may* carry: a ``trn02`` for a claim
    payment, an ``allocation_code`` for a rebate batch.  Whether it survives the trip is
    the realizer's decision, not this one's.
    """

    slice_ref: str
    amount_cents: int
    payer_reference: str
    company_name: str
    company_id: str
    ach_routing_prefix: str
    #: ``HCCLAIMPMT`` is a CORE/CAQH mandate for claim EFT specifically.  A manufacturer
    #: rebate is not a claim payment under that mandate, so it rides as plain ``CCD`` —
    #: which is exactly why a naive "claim payments only" filter silently skips rebates.
    entry_description: Literal["HCCLAIMPMT", "CCD"]
    effective_date: str
    direction: Literal["CREDIT", "DEBIT"] = "CREDIT"


@dataclass(frozen=True, slots=True)
class CashEvent:
    """A bank row that will exist, after the realizer has applied cash faults.

    The realizer creates one of these per movement that actually settled.  A movement
    whose episode was supposed to show ``cash = ABSENT`` produces **no** CashEvent at
    all, which is why the bank generator cannot tell the difference between "no deposit"
    and "no such payment" — and neither can a connector reading only the bank feed.
    """

    posting_date: str
    amount_cents: int
    company_name: str
    company_id: str
    ach_routing_prefix: str
    entry_description: str
    direction: Literal["CREDIT", "DEBIT"]
    received_at: str
    #: ``None`` models the ~20% CCD+ addenda loss.  When it is None the only remaining
    #: handle is amount + date, which is the whole reason TRN02 matters.
    payer_reference: str | None
    #: Opaque handle back to the movement, for ground truth only.  Never written to the
    #: feed: a bank statement has no idea what a remittance is.
    movement_ref: str | None = None


# ═══ per-episode event slices ══════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class PbmClaimSlice:
    """What a PBM's adjudication system knows at the point of sale.

    Note what is absent: any expected amount of *ours*, anything about the bank, and
    anything about 340B beyond the ``submission_clarification_code`` flag the pharmacy
    itself put on the claim.  A PBM genuinely does not know whether a dispense qualifies
    for a rebate.
    """

    slice_ref: str
    rng_seed: int
    received_at: str

    pharmacy_npi: str
    #: Rendered as directed by ``rx_number_rendering``; see :mod:`recon.generators.plan`.
    rx_number: str
    fill_number: str
    date_of_service: str
    ndc11: str
    quantity_milli: int
    days_supply: int
    prescriber_npi: str

    pbm_id: str
    cardholder_id: str
    person_code: str

    accepted: bool
    #: Canonical reject semantics; the generator maps it through its PBM's own
    #: vocabulary, which is how two PBMs spell the same rejection differently.
    reject_canonical: str | None

    #: What the PBM adjudicated.  Present only when ``accepted``.
    ingredient_cost_paid_cents: int | None
    dispensing_fee_paid_cents: int | None
    patient_pay_amount_cents: int | None

    #: ``20`` in NCPDP 420-DK: the pharmacy flagging a 340B-related dispense.
    is_340b_flagged: bool

    #: A B2 reversal carries no identity of its own — it repeats the NCPDP transaction
    #: key.  ``None`` when no reversal happened.
    reversal: "PbmReversalSlice | None" = None


@dataclass(frozen=True, slots=True)
class PbmReversalSlice:
    """An NCPDP B2.  Matches its original by repeating the transaction key."""

    slice_ref: str
    received_at: str
    reversal_reason: str


@dataclass(frozen=True, slots=True)
class MedicalSubmissionSlice:
    """What the clearinghouse and the provider's billing system know.

    The drug appears twice on two different unit bases — ``svc05_units`` (J-code billing
    units) and ``ctp04_quantity`` (mL of drug product).  Both are carried because they
    are legitimately different numbers describing the same vial, and a connector that
    expects them to reconcile numerically is wrong to expect it.
    """

    slice_ref: str
    rng_seed: int
    received_at: str

    clm01: str
    frequency_code: Literal["1", "7", "8"]
    #: Populated on a replacement or void, pointing at the ICN being superseded.
    ref_f8_original_icn: str | None

    billing_provider_npi: str
    rendering_provider_npi: str
    date_of_service: str
    ndc11: str
    hcpcs_j_code: str
    svc05_units: int
    ctp04_quantity: int
    ctp05_uom_qualifier: str
    charge_cents: int
    payer_id: str

    #: The 277CA rides in the same file because it is the same rail.  Without it a
    #: clearinghouse rejection is indistinguishable on the wire from "accepted, awaiting
    #: 835" — both are an 837 with no 835 — and verdict B-01 is unreachable (Decision 39).
    acknowledgment: "MedicalAcknowledgmentSlice | None" = None
    #: A replacement that silently drops a line the original carried.  A frequency-7 is a
    #: full replacement, not a patch: whatever it omits is deleted, not preserved.
    drops_a_service_line: bool = False


@dataclass(frozen=True, slots=True)
class MedicalAcknowledgmentSlice:
    slice_ref: str
    received_at: str
    accepted: bool
    #: ``A1:19`` accepted, or one of ``A3:21`` / ``A3:33`` / ``A3:187``.
    stc01_composite: str
    stc12_free_form: str
    #: The payer ICN is minted by the clearinghouse/payer and appears only on acceptance.
    assigns_payer_claim_control_number: bool


@dataclass(frozen=True, slots=True)
class TpaDispenseSlice:
    """What the TPA portal knows — and it is strikingly little.

    No 835, no trace number, no standard underneath it at all.  The PBM's
    ``authorization_number`` never reaches here, because the two systems do not talk, so
    the only bridge back to a claim is the natural key.

    On a **medical-benefit** dispense ``rx_number`` and ``pharmacy_npi`` are ``None``:
    a clinic-infused drug has no prescription, so nothing pharmacy-shaped exists to key
    on.  The vendor column is still called ``fill_date`` even though it carries the
    administration date, because the export schema was built for pharmacy and reused.
    That is realistic, not sloppy.
    """

    slice_ref: str
    rng_seed: int

    rx_number: str | None
    pharmacy_npi: str | None
    provider_npi: str | None
    ndc11: str
    fill_date: str
    prescriber_npi: str
    covered_entity_id: str
    hin: str
    wholesaler_invoice_number: str
    manufacturer_short_name: str

    #: ``None`` means no decision has been emitted yet — the C-01 pending state.
    qualification_status: Literal["QUALIFIED", "NOT_QUALIFIED"] | None
    qualification_received_at: str | None
    disqualification_reason: str | None

    #: A ``REBATE_REQUEST`` record, which is what separates C-03 (qualified, not yet
    #: submitted) from C-05 (submitted, manufacturer silent) on the wire.
    request_received_at: str | None
    submission_date: str | None

    #: A standalone ``MANUFACTURER_DECISION``.  A rejection cannot ride inside a payment
    #: batch, so it needs its own record; an approval may come either way.
    decision_received_at: str | None
    manufacturer_status: Literal["APPROVED", "REJECTED"] | None
    rejection_reason: str | None

    #: A ``DISPENSE_REVERSAL`` — negative quantity, not a delete.
    reversal_received_at: str | None
    reversal_quantity_milli: int | None
    reversal_reason: str | None


# ═══ cross-episode batch slices ════════════════════════════════════════════
#
# Batching is irreducibly cross-episode: one 835 covers many claims, one rebate batch
# covers many dispenses.  That is what makes allocation real work, and it is why these
# slices exist as a second level rather than being folded into the per-episode ones.


@dataclass(frozen=True, slots=True)
class AdjustmentSlice:
    """One CAS adjustment on a claim line."""

    group_code: str
    reason_code: str
    amount_cents: int
    rarc: str | None = None


@dataclass(frozen=True, slots=True)
class PbmClaimLineSlice:
    """One claim's payment inside a pharmacy remittance.

    ``clp02_status_code`` is where settlement lives, and it is binding: ``"1"`` means
    settled, ``"19"``/``"25"`` mean money moved but the receivable is not closed
    (Decision 40).  Neither NCPDP nor the 835 has a settlement transaction, so there is
    no other place to put it — and the engine must mirror this exact read or verdict
    A-06 silently never fires.
    """

    slice_ref: str
    rx_number: str
    fill_number: str
    ndc11: str
    date_of_service: str
    authorization_number: str
    clp02_status_code: str
    charge_cents: int
    payment_cents: int
    patient_responsibility_cents: int
    quantity_milli: int
    adjustments: tuple[AdjustmentSlice, ...]
    #: When True the line deliberately does **not** balance: ``clp04`` falls short of the
    #: adjudicated promise with a ``CO-45`` that does not explain the whole gap.  This is
    #: the one sanctioned exception to ``clp03 = clp04 + Σ(adjustments)`` and it is the
    #: pharmacy underpayment defect.  Medical 835s always balance — there the dispute is
    #: about the *reason* for a reduction, never the arithmetic.
    is_underpayment_defect: bool = False


@dataclass(frozen=True, slots=True)
class MedicalClaimLineSlice:
    slice_ref: str
    clm01: str
    #: A reprocessed claim gets a brand-new ICN while CLM01 never changes.  A connector
    #: that treats this as claim identity forks one claim's history into unrelated pieces.
    assign_new_icn: bool
    clp02_status_code: str
    charge_cents: int
    payment_cents: int
    patient_responsibility_cents: int
    hcpcs_j_code: str
    svc05_units: int
    ndc11: str
    adjustments: tuple[AdjustmentSlice, ...]
    #: Zero to two CPT administration lines (96413 / 96415) that legitimately carry no
    #: NDC.  An infusion claim is one J-code drug line plus these.
    administration_line_count: int = 0


@dataclass(frozen=True, slots=True)
class PlbEntrySlice:
    """A provider-level adjustment: money moved for reasons outside any claim loop.

    This is the most valuable trap in the dataset.  A netted recoupment is **invisible to
    the bank**: the payer simply pays less in a later batch, and the only record of why
    lives here.  So ``Σ(claim payments) − PLB = what hits the bank``, and the bank shows
    only the net.

    ``reference_id`` absent is not sloppiness — it is the D-7 untraceable-offset case, and
    it is what makes a recoupment impossible to tie to a deposit (verdict A-13 rather than
    A-12).  ``FB`` and general ``CS`` lines routinely carry no reference in reality.

    Sign convention, under the 835's own identity ``BPR02 = Σ(CLP04) − Σ(PLB, signed)``:
    a **positive** PLB reduces the deposit.  So ``WO`` is positive and ``L6`` (interest
    owed to the provider) is negative, because it increases what is paid.  ``RA`` carried
    as an appeal credit is negative for the same reason.
    """

    reason_code: str
    amount_cents: int
    reference_id: str | None
    #: Opaque link back to the episode being clawed back, for ground truth only.  Never
    #: written to the wire — the wire carries ``reference_id`` or nothing.
    slice_ref: str | None = None


@dataclass(frozen=True, slots=True)
class PbmRemittanceSlice:
    """One whole pharmacy 835 file: one payment covering many claims.

    Carries no net total.  The generator computes ``BPR = Σ(CLP04) − Σ(PLB, signed)``
    from the lines below, so the figure exists exactly once — in the place that formats
    it.  A precomputed total here would be a second copy free to disagree.
    """

    slice_ref: str
    #: A unique, dense batch number assigned by the orchestrator, which owns batch
    #: composition and is therefore the only thing that can number batches without
    #: collisions.  The generator renders it into ``record_id``.
    #:
    #: This field exists because deriving a record id from a digest of ``slice_ref``
    #: collides, and a colliding record id is silently destructive: the connector's
    #: idempotency key is the source record id, so a second batch that happens to mint the
    #: same id is discarded as a duplicate delivery, taking its claim lines and its trace
    #: number with it.  Dense integers cannot collide.
    sequence: int
    rng_seed: int
    received_at: str
    payment_effective_date: str
    pbm_id: str
    payee_npi: str
    reassociation_trace_number: str
    claim_lines: tuple[PbmClaimLineSlice, ...]
    provider_level_adjustments: tuple[PlbEntrySlice, ...] = ()


@dataclass(frozen=True, slots=True)
class MedicalRemittanceSlice:
    slice_ref: str
    #: See PbmRemittanceSlice.sequence — a dense, collision-free batch number.
    sequence: int
    rng_seed: int
    received_at: str
    eft_effective_date: str
    payer_id: str
    trace_number: str
    claim_lines: tuple[MedicalClaimLineSlice, ...]
    provider_level_adjustments: tuple[PlbEntrySlice, ...] = ()


@dataclass(frozen=True, slots=True)
class RebateDispenseLineSlice:
    slice_ref: str
    rx_number: str | None
    pharmacy_npi: str | None
    provider_npi: str | None
    ndc11: str
    fill_date: str
    covered_entity_id: str
    manufacturer_status: Literal["APPROVED", "REJECTED"]
    rebate_amount_cents: int


@dataclass(frozen=True, slots=True)
class RebateBatchSlice:
    """One manufacturer payment covering many dispenses.

    ``allocation_code`` is what makes this splittable, and it rides to the bank in the
    ``trn02`` column — same column, same ~20% addenda-loss odds, different issuer
    (Decision 42).  A rebate deposit that loses its allocation code *and* cannot be
    rescued by amount-plus-date is exactly D-4, the unmatched rebate — which emerges from
    the mechanism rather than being hand-placed.

    No total here either: the generator sums its own dispense lines.
    """

    slice_ref: str
    #: See PbmRemittanceSlice.sequence — a dense, collision-free batch number.
    sequence: int
    rng_seed: int
    received_at: str
    payment_effective_date: str
    manufacturer_short_name: str
    allocation_code: str
    dispense_lines: tuple[RebateDispenseLineSlice, ...]


# ═══ the blindness check ═══════════════════════════════════════════════════

#: Field names that must never appear on any slice.  Each one would hand a generator a
#: fact only the orchestrator is allowed to hold.
FORBIDDEN_SLICE_FIELDS: frozenset[str] = frozenset(
    {
        "episode_id",
        "case_id",
        "verdict",
        "verdict_code",
        "reimbursement_verdict",
        "reimbursement_verdict_code",
        "rebate_verdict",
        "rebate_verdict_code",
        "cross_track_flags",
        "coherence",
        "disposition",
        "expected_reimbursement_cents",
        "expected_rebate_cents",
        "cash_reimb_in",
        "cash_reimb_out",
        "cash_rebate_in",
        "cash_rebate_out",
        "ground_truth",
    }
)


def assert_slice_is_blind(slice_obj: Any) -> None:
    """Fail if a slice carries a fact its source system could not know.

    Run over every slice instance a full generation produces, not just over the class
    definitions — because the cheapest way to leak the episode id is to stuff it into an
    existing string field, and only looking at values catches that.

    Raises:
        AssertionError: naming the slice type and the offending field.
    """
    fields = getattr(type(slice_obj), "__dataclass_fields__", None)
    if fields is None:
        raise AssertionError(f"{slice_obj!r} is not a slice dataclass")

    offenders = sorted(set(fields) & FORBIDDEN_SLICE_FIELDS)
    if offenders:
        raise AssertionError(
            f"{type(slice_obj).__name__} declares forbidden field(s) {offenders}. "
            "A generator must know only what its real source system would know "
            "(Decision 10); handing it this collapses the crosswalk into a join."
        )

    # An episode id smuggled into a free-text field is the same leak wearing a hat.
    for name in fields:
        value = getattr(slice_obj, name, None)
        if isinstance(value, str) and _looks_like_an_episode_id(value):
            raise AssertionError(
                f"{type(slice_obj).__name__}.{name} = {value!r} looks like an episode id. "
                "Episode identity is the orchestrator's alone; a generator that can see "
                "it can leak a shared key across feeds."
            )


def _looks_like_an_episode_id(value: str) -> bool:
    return value.startswith(("EP-", "EPISODE-", "CASE-"))


def assert_slices_are_blind(slices: Sequence[Any]) -> None:
    """:func:`assert_slice_is_blind` over a collection, including nested child slices."""
    for item in slices:
        assert_slice_is_blind(item)
        for name in getattr(type(item), "__dataclass_fields__", {}):
            child = getattr(item, name, None)
            if isinstance(child, tuple):
                for element in child:
                    if hasattr(type(element), "__dataclass_fields__"):
                        assert_slice_is_blind(element)
            elif hasattr(type(child), "__dataclass_fields__"):
                assert_slice_is_blind(child)
