"""The orchestrator: the only component that sees the whole picture.

It decides everything and formats nothing.  The four generators format everything and
decide nothing.  That split is the load-bearing part of the generator design (Decision
10/11/44), because it is what makes the crosswalk a real problem: no generator can leak a
shared key across feeds, since no generator knows there is anything to share.

What the orchestrator owns:

* which configurations to realise, and which entities they bind;
* every amount, computed once through :mod:`recon.reference.pricing` — the same module the
  engine imports, which is what makes an injected underpayment *exactly* the underpayment
  the engine will report;
* every ``received_at``, because arrival order is a fact about our pipeline rather than
  about any source system;
* **batch composition** — which claim payments share a remittance, which PLB entry attaches
  to which later batch, which dispenses share a rebate batch.  The netting ledger is
  orchestrator machinery;
* ``truth/ground_truth.json``, which the ingestion layer and the engine are forbidden to
  read.  That prohibition is what makes the crosswalk *scoreable* rather than assumed.

═══ Why batches are grouped by cash outcome ════════════════════════════════════════

The decision tree declares cash per episode; a deposit is per batch.  One ACH cannot be
simultaneously present for one claim in it and absent for another.  So episodes are grouped
into batches by ``(payer, cycle, cash outcome)`` and every claim in a batch shares the
batch's fate — which is also what really happens, because a payer's settlement either
arrives or it does not.

═══ Why the recoupment lands on a *later* batch ════════════════════════════════════

Because that is the trap.  A netted recoupment is invisible to the bank: the payer simply
pays less in a later cycle, and the only record of why lives in that cycle's ``PLB``
segment.  ``Σ(claim payments) − PLB = what hits the bank``, and the bank shows only the net.
A connector reading bank lines alone sees an unremarkable deposit and never learns money
was taken back.
"""

from __future__ import annotations

import hashlib
import json
import random
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable, Sequence

from recon import config
from recon.config import Profile, ProfileSelection, Settings
from recon.crosswalk import keys
from recon.generators import bank as bank_gen
from recon.generators import medical as medical_gen
from recon.generators import pbm as pbm_gen
from recon.generators import tpa as tpa_gen
from recon.generators.contracts import (
    AdjustmentSlice,
    CashEvent,
    MedicalAcknowledgmentSlice,
    MedicalClaimLineSlice,
    MedicalRemittanceSlice,
    MedicalSubmissionSlice,
    MoneyMovement,
    PbmClaimLineSlice,
    PbmClaimSlice,
    PbmRemittanceSlice,
    PbmReversalSlice,
    PlbEntrySlice,
    RebateBatchSlice,
    RebateDispenseLineSlice,
    Record,
    TpaDispenseSlice,
    assert_slice_is_blind,
)
from recon.generators.plan import EpisodePlan, underpayment_shortfall_cents
from recon.generators.realizer import DeclaredMovement, realize
from recon.generators.sampling import (
    bind_entities,
    load_leaves,
    select_configurations,
    select_curated,
)
from recon.generators.timeline import Timeline, build_timeline, stamp
from recon.money import apply_bps, format_amount
from recon.reference import calendar, codes, drugs, entities
from recon.reference.fingerprint import fingerprint as reference_fingerprint
from recon.rng import derive_seed, rng_for, stable_choice

#: A fixed namespace for identifier minting.  Distinct from the dataset's master seed so that
#: changing the seed reshuffles *content* without renumbering every trace number, which keeps
#: a diff between two runs readable.
_MINT_NAMESPACE = 0

__all__ = ["GenerationResult", "generate", "write_outputs"]


#: D-1: the same payload delivered twice.  An SFTP job rerun, or a retry after an ambiguous
#: timeout.  Not two real events — an idempotency key must collapse them, not sum them.
DUPLICATE_DELIVERY_BPS = 300
#: D-2: a record whose arrival is shifted late while its event dates stay untouched.
LATE_ARRIVAL_BPS = 800
LATE_ARRIVAL_DAYS = (7, 45)
#: Identifier drift, assigned to exactly one feed per episode so the defect is reproducible.
RX_DRIFT_BPS = 600
RX_TRUNCATION_BPS = 200

#: How often the two TPAs disagree about how a dispense's Rx is spelled, in basis points.
#:
#: Lower than either drift rate on purpose.  A vendor disagreement is the expensive exception
#: rather than routine noise, and a rate high enough to be convenient would make "the two
#: vendors agree" the unusual case — which would misrepresent the programme to anyone reading
#: the queue and would drown the D-6 misses it has to be told apart from.
VENDOR_DIVERGENCE_BPS = 300
#: D-7: an unreferenced forward balance on otherwise-clean remittances.
FB_RESIDUAL_BPS = 500
#: How many claims a remittance cycle bundles before spilling into a second file.
MAX_CLAIMS_PER_REMITTANCE = 24
MAX_DISPENSES_PER_REBATE_BATCH = 18


class SliceRefs:
    """Opaque handles a generator echoes back, carrying no meaning of their own.

    ``slice_ref`` exists so the orchestrator can reattach a generator's output to the episode
    that produced it.  It must therefore be **opaque**: an earlier version spelled it
    ``f"{episode_id}:pbm-claim"``, which handed every generator the episode identity it is
    specifically not allowed to know (Decision 10/11) — the leak wearing a hat that
    :func:`assert_slice_is_blind` exists to catch.

    A dense counter plus a side table gives the orchestrator the same attribution with none of
    the disclosure.  The mapping lives here, on the orchestrator's side of the boundary, where
    it belongs.
    """

    __slots__ = ("_next", "_by_ref")

    def __init__(self) -> None:
        self._next = 0
        self._by_ref: dict[str, tuple[str, str]] = {}

    def mint(self, episode_id: str, role: str) -> str:
        self._next += 1
        ref = f"SR{self._next:07d}"
        self._by_ref[ref] = (episode_id, role)
        return ref

    def episode_for(self, ref: str) -> str | None:
        entry = self._by_ref.get(ref)
        return None if entry is None else entry[0]

    def role_for(self, ref: str) -> str | None:
        entry = self._by_ref.get(ref)
        return None if entry is None else entry[1]


@dataclass(frozen=True, slots=True)
class GenerationResult:
    """Everything one generation run produced."""

    plans: tuple[EpisodePlan, ...]
    records_by_feed: dict[str, list[Record]]
    ground_truth: dict[str, Any]
    leaf_digest: str
    #: Cross-system identifiers for the vendor-format layer, with the keys to join them on.
    #: Empty by default so every existing caller that builds a ``GenerationResult`` keeps
    #: working unchanged -- the six feeds are the same object they always were.
    vendor_refs: tuple[dict[str, Any], ...] = ()

    @property
    def episode_count(self) -> int:
        return len(self.plans)


# ═══ the run ════════════════════════════════════════════════════════════════


def generate(settings: Settings, *, decision_tree_dir: Path | None = None) -> GenerationResult:
    """Plan every episode, run the four generators, and realise the cash.

    Pure with respect to the filesystem except for reading the frozen decision tree:
    nothing is written until :func:`write_outputs`.  That split exists so a test can
    generate a dataset in memory and assert over it without touching ``data/``.
    """
    tree_dir = decision_tree_dir or (settings.repo_root / "decision_tree")
    catalogue = load_leaves(tree_dir)
    profile_name = str(settings.profile)
    curated = settings.selection is ProfileSelection.CURATED

    # The two profiles select differently on purpose (Decision 14).  ``demo`` guarantees one
    # episode per named edge case and samples the rest, so a walkthrough reliably reaches
    # every interesting state in sixty readable episodes while two seeds still differ in
    # their queue mix; ``full`` stratifies over verdict *pairs* so coverage of all 372 is
    # arithmetic rather than luck.
    if curated:
        leaves = select_curated(
            catalogue,
            episode_count=settings.episode_count,
            master_seed=settings.master_seed,
            profile_name=profile_name,
            spine=settings.curated_spine,
        )
    else:
        leaves = select_configurations(
            catalogue,
            episode_count=settings.episode_count,
            master_seed=settings.master_seed,
            profile_name=profile_name,
        )

    plans: list[EpisodePlan] = []
    timelines: dict[str, Timeline] = {}
    # The medical 340B key is ``{provider_npi, ndc11, service_date}`` and nothing more, so two
    # administrations of the same drug at the same site on the same day are genuinely
    # indistinguishable (Decision C9).  The connector must park that as ambiguous rather than
    # guess — but left to chance, one billing provider over five infusion drugs and a
    # twelve-month window produces collisions by the hundred, and the hazard stops being a
    # demonstrable defect and becomes the dominant behaviour of the whole rebate track.
    #
    # So the collision is *placed* rather than stumbled into: medical 340B episodes get
    # distinct triples, and a small deliberate quota is forced to collide.  Same defect, known
    # rate, still emergent from the key's real weakness rather than hand-stamped on a record.
    used_medical_340b: set[tuple[str, str]] = set()
    collision_rng = rng_for(settings.master_seed, profile_name, "medical-340b-collisions")

    for sequence, leaf in enumerate(leaves, start=1):
        episode_id = f"EP-{sequence:06d}"
        bindings = bind_entities(
            leaf,
            sequence=sequence,
            master_seed=settings.master_seed,
            profile_name=profile_name,
        )
        bindings.update(
            _defect_directives(
                leaf,
                sequence=sequence,
                master_seed=settings.master_seed,
                profile_name=profile_name,
                curated=curated,
            )
        )
        service_day = _date_of_service(
            settings, sequence=sequence, profile_name=profile_name
        )
        # EVERY medical episode, not only the 340B-bearing ones.
        #
        # A medical 837 publishes ``NATURAL_340B_MEDICAL`` unconditionally, because the feed gives
        # it no way not to: unlike a pharmacy claim's NCPDP 420-DK flag, an 837 carries no 340B
        # marker at all.  So a non-340B medical episode is a live resolution target for TPA
        # records, and if it shares a triple with a 340B episode the rebate either attaches to the
        # wrong episode or goes ambiguous.  Either way the collision is accidental rather than
        # designed, which is precisely what the uniqueness pass exists to prevent.
        needs_unique_340b_key = leaf["reimbursement_track"]["reimb_type"] == "MEDICAL"
        if needs_unique_340b_key:
            service_day = _place_medical_340b_date(
                service_day,
                ndc11=bindings["ndc11"],
                used=used_medical_340b,
                settings=settings,
                rng=collision_rng,
                curated=curated,
            )
        drug = drugs.by_ndc(bindings["ndc11"])
        quantity_milli = bindings["quantity_units"] * 1_000

        plan = EpisodePlan.build(
            episode_id=episode_id,
            leaf=leaf,
            bindings=bindings,
            date_of_service=service_day,
            quantity_milli=quantity_milli,
        )
        plans.append(plan)
        timelines[episode_id] = build_timeline(
            plan.configuration,
            date_of_service=service_day,
            verdicts=(plan.reimbursement_verdict, plan.rebate_verdict),
            rejection_reason=bindings.get("rejection_reason"),
            rng=rng_for(settings.master_seed, profile_name, "timeline", sequence),
        )

    records_by_feed: dict[str, list[Record]] = defaultdict(list)
    declared: list[DeclaredMovement] = []
    refs = SliceRefs()

    # --- per-episode event slices -----------------------------------------
    #
    # Every slice is checked before it crosses the boundary.  The check is cheap and it is the
    # only thing standing between the design and its most likely failure: an episode id, a
    # verdict code or an expected amount smuggled into a slice field, after which the connector
    # under test has a free shared key and proves nothing (Decision 10/11).
    claim_slices = _guard(_pharmacy_claim_slices(plans, timelines, settings, refs))
    records_by_feed[config.PBM_CLAIM_EVENTS_FILE].extend(
        pbm_gen.gen_pbm_claim_events(claim_slices)
    )

    submission_slices = _guard(_medical_submission_slices(plans, timelines, settings, refs))
    records_by_feed[config.MEDICAL_837_SUBMISSIONS_FILE].extend(
        medical_gen.gen_medical_submissions(submission_slices)
    )

    tpa_slices = _guard(_tpa_dispense_slices(plans, timelines, settings, refs))
    records_by_feed[config.TPA_340B_EVENTS_FILE].extend(tpa_gen.gen_tpa_events(tpa_slices))

    # --- cross-episode batch slices ---------------------------------------
    for batch, outcome in _guard_batches(
        _pharmacy_remittance_batches(plans, timelines, settings, refs)
    ):
        record, movement = pbm_gen.gen_pbm_remittance(batch)
        records_by_feed[config.PBM_REMITTANCE_835_FILE].append(record)
        declared.append(DeclaredMovement(movement=movement, outcome=outcome))

    for batch, outcome in _guard_batches(
        _medical_remittance_batches(plans, timelines, settings, refs)
    ):
        record, movement = medical_gen.gen_medical_remittance(batch)
        records_by_feed[config.MEDICAL_835_REMITTANCE_FILE].append(record)
        declared.append(DeclaredMovement(movement=movement, outcome=outcome))

    for batch, outcome, reversal_outcome in _guard_batches(
        _rebate_batches(plans, timelines, settings, refs)
    ):
        record, movement = tpa_gen.gen_rebate_batch(batch)
        records_by_feed[config.TPA_340B_EVENTS_FILE].append(record)
        declared.append(
            DeclaredMovement(
                movement=movement, outcome=outcome, reversal_outcome=reversal_outcome
            )
        )

    # --- declare -> realize -> format -------------------------------------
    cash_events = realize(
        declared,
        master_seed=settings.master_seed,
        profile_name=profile_name,
        curated=curated,
    )
    records_by_feed[config.BANK_TRANSACTIONS_FILE].extend(bank_gen.gen_bank(cash_events))

    # --- feed-level defects that are about delivery, not content ----------
    for feed_file, records in list(records_by_feed.items()):
        records_by_feed[feed_file] = _apply_delivery_defects(
            records,
            feed_file=feed_file,
            plans=plans,
            master_seed=settings.master_seed,
            profile_name=profile_name,
            curated=curated,
        )

    ground_truth = _build_ground_truth(
        settings=settings,
        plans=plans,
        timelines=timelines,
        declared=declared,
        cash_events=cash_events,
        leaf_digest=catalogue.digest,
    )

    # --- vendor-layer identifiers (requirement M1) -------------------------
    #
    # Minted *last*, and the ordering is load-bearing rather than tidy.  ``SliceRefs`` is a
    # dense counter, and ``_mint_trace_number`` derives every ``trn02`` from the slice ref it
    # is given -- so taking refs earlier would renumber every remittance's trace number and
    # change all six feeds.  Minting here leaves every existing ref exactly where it was.
    vendor_refs = _vendor_identifier_rows(plans, settings, refs)
    assert_vendor_rows_are_blind(vendor_refs)

    return GenerationResult(
        plans=tuple(plans),
        records_by_feed=dict(records_by_feed),
        ground_truth=ground_truth,
        leaf_digest=catalogue.digest,
        vendor_refs=tuple(vendor_refs),
    )


def _guard(slices):
    """Assert a batch of slices carries nothing its source system could not know.

    Runs over slice *instances*, not class definitions, because the cheapest way to leak the
    episode id is to put it inside an existing string field — and only looking at values catches
    that.  Nested children are walked too: a claim line buried in a remittance is just as much a
    leak as the remittance itself.
    """
    from recon.generators.contracts import assert_slices_are_blind

    assert_slices_are_blind(slices)
    return slices


def _guard_batches(batches):
    """:func:`_guard` for the batch builders, whose results are tuples of ``(slice, ...)``."""
    items = list(batches)
    _guard([item[0] for item in items])
    return items


# ═══ per-episode slices ═════════════════════════════════════════════════════


def _pharmacy_claim_slices(
    plans: Sequence[EpisodePlan],
    timelines: dict[str, Timeline],
    settings: Settings,
    refs: SliceRefs,
) -> list[PbmClaimSlice]:
    slices: list[PbmClaimSlice] = []
    for index, plan in enumerate(plans):
        if plan.track.value != "PHARMACY":
            continue
        line = timelines[plan.episode_id]
        configuration = plan.configuration
        accepted = configuration.get("ph_adjudication") == "ACCEPTED"

        reversal: PbmReversalSlice | None = None
        post_event = configuration.get("ph_post_event")
        if post_event in {"REVERSAL_PRE_PAY", "REVERSAL_POST_PAY"} and line.reversal_received_at:
            reversal = PbmReversalSlice(
                slice_ref=refs.mint(plan.episode_id, "pbm-reversal"),
                received_at=line.reversal_received_at,
                reversal_reason="RETURN_TO_STOCK"
                if post_event == "REVERSAL_PRE_PAY"
                else "PATIENT_NOT_COLLECTED",
            )

        # The adjudicated promise, which is what the 835 is later measured against.
        ingredient = plan.allowed_cents - _dispensing_fee(plan)
        slices.append(
            PbmClaimSlice(
                slice_ref=refs.mint(plan.episode_id, "pbm-claim"),
                rng_seed=rng_for(settings.master_seed, str(settings.profile), "pbm", index).randrange(
                    2**32
                ),
                received_at=line.claim_received_at,
                pharmacy_npi=plan.pharmacy_npi or "",
                rx_number=plan.rx_number or "",
                fill_number=plan.fill_number or "00",
                date_of_service=plan.date_of_service.isoformat(),
                ndc11=plan.ndc11,
                quantity_milli=plan.quantity_milli,
                days_supply=plan.quantity_milli // 1_000,
                prescriber_npi=plan.prescriber_npi,
                pbm_id=plan.pbm_id or "",
                cardholder_id=plan.cardholder_id,
                person_code=plan.person_code,
                accepted=accepted,
                reject_canonical=None if accepted else _reject_canonical(plan, settings),
                ingredient_cost_paid_cents=ingredient if accepted else None,
                dispensing_fee_paid_cents=_dispensing_fee(plan) if accepted else None,
                patient_pay_amount_cents=plan.patient_responsibility_cents if accepted else None,
                is_340b_flagged=plan.is_340b_flagged,
                reversal=reversal,
            )
        )
    return slices


def _medical_submission_slices(
    plans: Sequence[EpisodePlan],
    timelines: dict[str, Timeline],
    settings: Settings,
    refs: SliceRefs,
) -> list[MedicalSubmissionSlice]:
    slices: list[MedicalSubmissionSlice] = []
    for index, plan in enumerate(plans):
        if plan.track.value != "MEDICAL":
            continue
        line = timelines[plan.episode_id]
        configuration = plan.configuration
        accepted = configuration.get("md_clearinghouse") == "ACCEPTED"
        drug = drugs.by_ndc(plan.ndc11)
        units_per_billing = drug.ctp_units_per_billing_unit or 1
        ctp_quantity = plan.quantity_milli // 1_000
        svc_units = max(1, ctp_quantity // units_per_billing)

        acknowledgment = None
        if line.acknowledgment_received_at is not None:
            acknowledgment = MedicalAcknowledgmentSlice(
                slice_ref=refs.mint(plan.episode_id, "med-277"),
                received_at=line.acknowledgment_received_at,
                accepted=accepted,
                stc01_composite="A1:19" if accepted else _rejection_stc(plan, settings),
                stc12_free_form=(
                    "ACCEPTED FOR PROCESSING" if accepted else "MISSING OR INVALID SUBSCRIBER ID"
                ),
                assigns_payer_claim_control_number=accepted,
            )

        seed = rng_for(settings.master_seed, str(settings.profile), "med", index).randrange(
            2**32
        )
        common = dict(
            rng_seed=seed,
            clm01=plan.clm01 or "",
            billing_provider_npi=plan.billing_provider_npi or "",
            rendering_provider_npi=plan.prescriber_npi,
            date_of_service=plan.date_of_service.isoformat(),
            ndc11=plan.ndc11,
            hcpcs_j_code=drug.hcpcs_j_code or "",
            svc05_units=svc_units,
            ctp04_quantity=ctp_quantity,
            ctp05_uom_qualifier="ML" if drug.unit_basis.value == "ML" else "UN",
            charge_cents=plan.charge_cents,
            payer_id=plan.medical_payer_id or "",
        )

        # The original submission always exists.  Every medical episode begins with one, and
        # it is the record that brings the episode into existence — so emitting only a
        # replacement would leave the episode with no anchor, its acknowledgment resolving to
        # nothing, and its whole history unreachable.
        slices.append(
            MedicalSubmissionSlice(
                slice_ref=refs.mint(plan.episode_id, "med-837"),
                received_at=line.claim_received_at,
                frequency_code="1",
                ref_f8_original_icn=None,
                acknowledgment=acknowledgment,
                drops_a_service_line=False,
                **common,
            )
        )

        # An appeal resolved by reprocessing is a *second* document about the same claim:
        # frequency 7, a brand-new ICN, a REF*F8 pointing back at the original.  X12 has no
        # field anywhere that says "this is an appeal", so on the wire it is indistinguishable
        # from an ordinary correction — which is precisely why the connector has to recognise
        # the pattern rather than look for a flag.
        # A filed appeal leaves a trace on the wire whatever its outcome: a frequency-7
        # resubmission of the same claim.  Emitting one only for a *won* appeal would make
        # B-07 (appeal pending) and B-11 indistinguishable from B-06/B-10 (never filed), and
        # B-09/B-13 (appeal lost) indistinguishable from them too — four verdicts collapsing
        # into two because the generator withheld the only evidence that separates them.
        appeal = configuration.get("md_appeal")
        if appeal in {"PENDING", "WON", "LOST"}:
            slices.append(
                MedicalSubmissionSlice(
                    slice_ref=refs.mint(plan.episode_id, "med-837-replacement"),
                    received_at=stamp(
                        line.appeal_resolved_on
                        or ((line.remittance_due_on or plan.date_of_service) + timedelta(days=21)),
                        hour=9,
                    ),
                    frequency_code="7",
                    ref_f8_original_icn=_prior_icn(plan),
                    acknowledgment=None,
                    # A frequency-7 is a full replacement, not a patch: a line the original
                    # carried and this one omits is deleted, not preserved.
                    drops_a_service_line=True,
                    **common,
                )
            )
    return slices


def _tpa_dispense_slices(
    plans: Sequence[EpisodePlan],
    timelines: dict[str, Timeline],
    settings: Settings,
    refs: SliceRefs,
) -> list[TpaDispenseSlice]:
    slices: list[TpaDispenseSlice] = []
    for index, plan in enumerate(plans):
        if not plan.has_rebate_track:
            continue
        line = timelines[plan.episode_id]
        configuration = plan.configuration
        is_pharmacy = plan.track.value == "PHARMACY"
        manufacturer = entities.manufacturer_by_id(plan.manufacturer_id or "")

        qualification = configuration.get("r_qualification")
        qualification_status = (
            qualification if qualification in {"QUALIFIED", "NOT_QUALIFIED"} else None
        )
        # A pending qualification still has to be *visible*, or verdict C-01 is unreachable.
        # The dispense was sent to the TPA; the TPA has not ruled.  That is a row in the export
        # with a null status — an "in review" line — and without it there is nothing on the wire
        # to distinguish "awaiting the first gate" from "not a 340B dispense at all".  Exactly
        # the argument that puts the 277CA in the medical feed (Decision 39).
        emit_qualification = qualification is not None
        manufacturer_status = configuration.get("r_manufacturer")
        if manufacturer_status not in {"APPROVED", "REJECTED"}:
            manufacturer_status = None

        # An APPROVED decision may be emitted as its own record or implied by the dispense
        # later appearing in a payment batch.  Both are real vendor behaviours; a rejection
        # cannot ride inside a payment batch, so it always gets its own record.
        emit_decision = manufacturer_status == "REJECTED" or (
            manufacturer_status == "APPROVED" and configuration.get("r_payment") in {None, "NONE"}
        )

        slices.append(
            TpaDispenseSlice(
                slice_ref=refs.mint(plan.episode_id, "tpa"),
                rng_seed=rng_for(
                    settings.master_seed, str(settings.profile), "tpa", index
                ).randrange(2**32),
                rx_number=plan.rendered_rx(plan.rx_rendering_tpa) if is_pharmacy else None,
                pharmacy_npi=plan.pharmacy_npi if is_pharmacy else None,
                provider_npi=None if is_pharmacy else plan.billing_provider_npi,
                ndc11=plan.ndc11,
                fill_date=plan.date_of_service.isoformat(),
                prescriber_npi=plan.prescriber_npi,
                covered_entity_id=plan.covered_entity_id or "",
                hin=plan.hin or "",
                wholesaler_invoice_number=plan.wholesaler_invoice_number or "",
                manufacturer_short_name=manufacturer.short_name,
                qualification_status=qualification_status,
                qualification_received_at=(
                    line.qualification_received_at
                    if line.qualification_received_at
                    else (stamp(plan.date_of_service + timedelta(days=3), hour=10)
                          if emit_qualification else None)
                ),
                disqualification_reason=_disqualification_reason(plan, settings),
                request_received_at=line.request_received_at,
                submission_date=(
                    line.submission_date.isoformat() if line.submission_date else None
                ),
                decision_received_at=line.decision_received_at if emit_decision else None,
                manufacturer_status=manufacturer_status if emit_decision else None,
                rejection_reason=_rejection_reason(plan, settings)
                if manufacturer_status == "REJECTED"
                else None,
                reversal_received_at=line.rebate_reversal_received_at,
                reversal_quantity_milli=(
                    -plan.quantity_milli if line.rebate_reversal_received_at else None
                ),
                reversal_reason="RETURN_TO_STOCK" if line.rebate_reversal_received_at else None,
            )
        )
    return slices


# ═══ batch composition — orchestrator machinery ═════════════════════════════


def _cycle_key(day: date) -> str:
    """A payment cycle: the ISO week the remittance was due.

    Weekly cycles are what produce realistically lumpy batches — a couple of dozen claims
    settling together — which is what makes allocation real work rather than a 1:1 join.
    """
    iso = day.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _pharmacy_remittance_batches(
    plans: Sequence[EpisodePlan],
    timelines: dict[str, Timeline],
    settings: Settings,
    refs: SliceRefs,
) -> list[tuple[PbmRemittanceSlice, str]]:
    grouped: dict[tuple[str, str, str], list[EpisodePlan]] = defaultdict(list)
    recoupments: dict[tuple[str, str], list[EpisodePlan]] = defaultdict(list)

    for plan in plans:
        if plan.track.value != "PHARMACY":
            continue
        line = timelines[plan.episode_id]
        if line.remittance_due_on is None:
            continue
        outcome = plan.configuration.get("cash_reimb_in") or "MATCHED"
        # Grouped by dispensing NPI as well as payer, because ``payee_npi`` is a file-level
        # field on the 835 and it has to be the NPI that actually dispensed.  A batch mixing
        # the main site and the satellite would give every satellite claim a payee that
        # contradicts its own claim event — and the NCPDP key the connector rebuilds from
        # CLP01 takes its NPI from exactly that field, so the crosswalk would miss for a
        # reason the dataset never intended.
        site = plan.pharmacy_npi or ""
        grouped[(plan.pbm_id or "", site, _cycle_key(line.remittance_due_on), outcome)].append(
            plan
        )
        # A-17: two payment events for one claim.  The second is a separate line in a
        # separate batch, because that is what a duplicate payment actually looks like.
        if plan.configuration.get("ph_payment") == "DUPLICATE":
            later = line.remittance_due_on + timedelta(days=9)
            grouped[(plan.pbm_id or "", site, _cycle_key(later), outcome)].append(plan)
        # A recoupment and a post-payment reversal both return money, and the PBM recovers both
        # the same way: netted out of a later batch through a PLB WO.  A standalone ACH reversal
        # exists in reality but is rare; netting is the common case and the one that hides the
        # money movement from the bank entirely, which is the behaviour worth modelling.
        recovery_due = line.recoupment_due_on
        if recovery_due is None and plan.configuration.get("ph_post_event") == "REVERSAL_POST_PAY":
            recovery_due = (line.remittance_due_on or plan.date_of_service) + timedelta(days=16)
        if recovery_due is not None:
            # Keyed by the episode's own ``cash_reimb_out``, because that dimension is precisely a
            # statement about the *batch* the clawback rides: MATCHED means it was netted out of a
            # deposit that arrived, ABSENT means no deposit it could have been netted out of ever
            # landed.  Routing it to a batch with the wrong fate makes A-12 and A-13 depend on
            # iteration order rather than on the data.
            recovery_outcome = plan.configuration.get("cash_reimb_out") or "MATCHED"
            recoupments[
                (plan.pbm_id or "", site, _cycle_key(recovery_due), recovery_outcome)
            ].append(plan)

    batches: list[tuple[PbmRemittanceSlice, str]] = []
    # A dense, monotonic batch number.  The generator renders it into ``record_id``, and the
    # connector's idempotency key IS the source record id -- so this number existing and being
    # unique is what stops a second batch being discarded as a duplicate delivery.
    batch_number = 0
    for group_key, members in sorted(grouped.items()):
        pbm_id, site_npi, cycle, outcome = group_key
        for chunk_index, chunk in enumerate(_chunked(members, MAX_CLAIMS_PER_REMITTANCE)):
            slice_ref = f"pbm-835:{pbm_id}:{site_npi}:{cycle}:{outcome}:{chunk_index}"
            rng = rng_for(settings.master_seed, str(settings.profile), "pbm835", slice_ref)
            effective = _cycle_effective_date(cycle, rng)
            claim_lines = tuple(
                _pharmacy_claim_line(plan, settings, refs) for plan in chunk
            )
            # A recoupment may only be netted into a batch whose deposit actually arrives.
            #
            # Netting is how the money physically comes back: the payer pays less, and the
            # shortfall is the clawback.  If the PLB rides a batch whose deposit never lands
            # (``cash_reimb_in = ABSENT``), there is no cash movement for the clawback to be part
            # of, so it can never be tied to one — and A-12 ("recouped, netted and matched")
            # silently degrades into A-13 ("cannot confirm the clawback happened").  An earlier
            # version popped the recoupment into whichever group came first, which made the
            # distinction depend on batch iteration order rather than on the data.
            #
            # Recoupments with nowhere matched to go fall through to the dedicated batch below.
            plb = tuple(
                _pharmacy_plb_entries(
                    recoupments.pop((pbm_id, site_npi, cycle, outcome), []),
                    chunk,
                    rng,
                    settings,
                    refs,
                )
            )
            batches.append(
                (
                    PbmRemittanceSlice(
                        slice_ref=slice_ref,
                        sequence=(batch_number := batch_number + 1),
                        rng_seed=rng.randrange(2**32),
                        received_at=stamp(effective + timedelta(days=1), hour=6),
                        payment_effective_date=effective.isoformat(),
                        pbm_id=pbm_id,
                        payee_npi=site_npi,
                        reassociation_trace_number=_mint_trace_number(slice_ref),
                        claim_lines=claim_lines,
                        provider_level_adjustments=plb,
                    ),
                    outcome,
                )
            )

    # A recoupment whose cycle produced no claims of its own still has to appear: the
    # clawback happened.  It rides a minimal remittance carrying PLB and nothing else,
    # which is a real shape — a cycle where the payer took back more than it paid.
    for (pbm_id, site_npi, cycle, recovery_outcome), members in sorted(recoupments.items()):
        slice_ref = f"pbm-835:{pbm_id}:{site_npi}:{cycle}:RECOUP-{recovery_outcome}:0"
        rng = rng_for(settings.master_seed, str(settings.profile), "pbm835", slice_ref)
        effective = _cycle_effective_date(cycle, rng)
        batches.append(
            (
                PbmRemittanceSlice(
                    slice_ref=slice_ref,
                    sequence=(batch_number := batch_number + 1),
                    rng_seed=rng.randrange(2**32),
                    received_at=stamp(effective + timedelta(days=1), hour=6),
                    payment_effective_date=effective.isoformat(),
                    pbm_id=pbm_id,
                    payee_npi=site_npi,
                    reassociation_trace_number=_mint_trace_number(slice_ref),
                    claim_lines=(),
                    provider_level_adjustments=tuple(
                        _pharmacy_plb_entries(members, (), rng, settings, refs)
                    ),
                ),
                recovery_outcome,
            )
        )
    return batches


def _pharmacy_claim_line(
    plan: EpisodePlan, settings: Settings, refs: SliceRefs
) -> PbmClaimLineSlice:
    """One claim's payment line, with adjustments that balance — or deliberately do not.

    The invariant is ``clp03 = clp04 + Σ(adjustments)``, with ``clp05 = Σ(PR adjustments)``.
    The single sanctioned exception is the pharmacy underpayment defect, where ``clp04``
    falls short with a ``CO-45`` that does not explain the whole gap — leaving a positive
    unexplained residual, which is exactly what makes it detectable as underpayment rather
    than as a contractual write-down.
    """
    contractual = plan.charge_cents - plan.allowed_cents
    patient = plan.patient_responsibility_cents
    payment = plan.paid_reimbursement_cents
    configuration = plan.configuration
    payment_kind = configuration.get("ph_payment")

    # A-17's second line is the same payment again, not a doubled one.
    if payment_kind == "DUPLICATE":
        payment = plan.expected_reimbursement_cents

    is_underpayment = payment_kind == "PARTIAL" and configuration.get("ph_post_event") != "ADJUSTMENT"

    if payment_kind == "OVER":
        # The payer allowed more than the contract says, so the contractual write-down is
        # correspondingly smaller.  The line still balances; the overpayment is visible as
        # clp04 exceeding what the contract supports, which is a refund liability.
        contractual = max(0, contractual - (payment - plan.expected_reimbursement_cents))
    adjustments: list[AdjustmentSlice] = []
    if contractual > 0:
        adjustments.append(AdjustmentSlice("CO", "45", contractual))
    if patient > 0:
        adjustments.append(AdjustmentSlice("PR", "3", patient))

    # An adjustment only changes the verdict when the payment is FULL or PARTIAL: an overpayment
    # reports A-09 and a duplicate A-17 regardless of any adjustment, so reducing those would
    # break their verdicts to no purpose.
    if configuration.get("ph_post_event") == "ADJUSTMENT" and payment_kind in {
        "FULL",
        "PARTIAL",
    }:
        # The adjustment has to be worth something, or it is not detectable.  One that reduced the
        # claim by zero is indistinguishable from no adjustment at all, and A-14/A-15 would
        # silently collapse into A-04/A-05.
        payment = min(payment, plan.expected_reimbursement_cents - _ADJUSTMENT_REDUCTION(plan))
        # A contractual adjustment that explains the shortfall: expected drops to E', the payer
        # pays E', and there is no residual to chase (A-14).  When the cash then fails to match,
        # the residual reappears as A-15.
        #
        # It gets its own CARC rather than being folded into CO-45, and that is the whole point.
        # CO-45 ("exceeds fee schedule") sits on virtually every paid claim, so it carries no
        # information — inflating it to swallow the shortfall would make A-14 arithmetically
        # identical to A-04 and A-15 identical to A-05, and four verdicts would collapse into
        # two.  CO-97 ("bundled into another service") is a *specific* contractual reduction,
        # which is exactly the signal the engine needs to tell "explained" from "unexplained".
        # The residual the adjustments do not yet cover.  ``patient`` is already represented by
        # the PR line inside ``adjustments``; subtracting it again here was double-counting it,
        # which drove the CO-97 amount to zero whenever the copay exceeded the reduction — and a
        # zero-amount adjustment is no adjustment at all.
        explained = plan.charge_cents - payment - sum(a.amount_cents for a in adjustments)
        if explained > 0:
            adjustments.append(AdjustmentSlice("CO", "97", explained))

    # An overpayment can exceed what reducing CO-45 to zero is able to absorb: when the allowed
    # amount is already close to the charge, clipping the write-down at zero leaves CLP04 above
    # CLP03 and the line no longer balances.  X12 permits a negative adjustment for exactly
    # this, and OA-94 ("processed in excess of charges") is what it is for.
    #
    # Only a *negative* residual is corrected here.  A positive one is the deliberate pharmacy
    # underpayment defect, and balancing that away would delete the verdict.
    residual = plan.charge_cents - payment - sum(a.amount_cents for a in adjustments)
    if residual < 0 and not is_underpayment:
        adjustments.append(AdjustmentSlice("OA", "94", residual))

    return PbmClaimLineSlice(
        slice_ref=refs.mint(plan.episode_id, "pbm-line"),
        rx_number=plan.rendered_rx(plan.rx_rendering_pharmacy_835) or "",
        fill_number=plan.fill_number or "00",
        ndc11=plan.ndc11,
        date_of_service=plan.date_of_service.isoformat(),
        authorization_number=_authorization_for(plan, settings),
        clp02_status_code=_pharmacy_clp02(plan),
        charge_cents=plan.charge_cents,
        payment_cents=payment,
        patient_responsibility_cents=patient,
        quantity_milli=plan.quantity_milli,
        adjustments=tuple(adjustments),
        is_underpayment_defect=is_underpayment,
    )


def _pharmacy_clp02(plan: EpisodePlan) -> str:
    """Settlement rides CLP02, and this is the binding read (Decision 40 / C13).

    ``"1"`` means processed as primary and final — settlement confirmed.  ``"19"`` (forwarded
    to an additional payer) and the rarer ``"25"`` (predetermination) mean money moved but
    the receivable is not closed, and **no later ``"1"`` line exists for that CLP01**.

    The engine's A-06 rule must mirror this exactly.  Get it wrong and A-06 silently never
    fires, which is the quietest possible failure: a verdict that is simply never reached.
    """
    if plan.configuration.get("ph_settlement") == "MISSING":
        return codes.CLP02_FORWARDED
    return codes.CLP02_PAID_PRIMARY


def _pharmacy_plb_entries(
    recouped: Sequence[EpisodePlan],
    chunk: Sequence[EpisodePlan],
    rng: random.Random,
    settings: Settings,
    refs: SliceRefs,
) -> list[PlbEntrySlice]:
    entries: list[PlbEntrySlice] = []
    for plan in recouped:
        entries.append(
            PlbEntrySlice(
                reason_code="WO",
                amount_cents=plan.negative_reimbursement_cents,
                # The reference is always present, because a payer always says which claim it is
                # recovering against.  A-13 is not "we do not know which claim" — it is "the
                # recoupment line exists and cannot be tied to any bank movement".  That
                # distinction is carried by whether the batch's deposit arrived; hiding the
                # reference instead would make an untraceable clawback indistinguishable from an
                # unexplained forward balance (D-7), which is a different defect with a different
                # owner.
                reference_id=_authorization_for(plan, settings),
                slice_ref=refs.mint(plan.episode_id, "plb"),
            )
        )
    # D-7: an unreferenced forward balance on an otherwise-clean remittance.  The residual
    # has to be tracked as unexplained rather than silently absorbed.
    if chunk and rng.randint(0, 9_999) < FB_RESIDUAL_BPS:
        entries.append(
            PlbEntrySlice(
                reason_code="FB",
                amount_cents=rng.randrange(1_500, 48_000),
                reference_id=None,
            )
        )
    return entries


def _medical_remittance_batches(
    plans: Sequence[EpisodePlan],
    timelines: dict[str, Timeline],
    settings: Settings,
    refs: SliceRefs,
) -> list[tuple[MedicalRemittanceSlice, str]]:
    # Each entry is ``(plan, leg)``.  ``ORIGINAL`` is the payer's first response; ``APPEAL`` is
    # the second 835 that follows a resubmission.
    #
    # An appeal is irreducibly a *two-payment path* and collapsing it into one line destroys four
    # verdicts.  B-12 is "denied, appeal won, paid" — on the wire that is a denial followed by a
    # corrected remittance.  Emitting only the final paid line makes it indistinguishable from
    # B-04, "paid in full first time", and the entire appeal disappears from the record.  B-08,
    # B-09 and B-13 go the same way.  The doc says as much for B-08: "second 835 covers the
    # balance; cash allocation must handle both".
    grouped: dict[tuple[str, str, str], list[tuple[EpisodePlan, str]]] = defaultdict(list)
    recoupments: dict[tuple[str, str], list[EpisodePlan]] = defaultdict(list)

    for plan in plans:
        if plan.track.value != "MEDICAL":
            continue
        line = timelines[plan.episode_id]
        if line.remittance_due_on is None:
            continue
        outcome = plan.configuration.get("cash_reimb_in") or "MATCHED"
        payer = plan.medical_payer_id or ""
        appeal = plan.configuration.get("md_appeal")

        # The original response lands on its own cycle.  For an appealed claim that cycle is
        # where the denial or the short payment appears, not where the money eventually does.
        original_cycle = _cycle_key(line.remittance_due_on)
        if appeal == "WON" and line.appeal_resolved_on is not None:
            # remittance_due_on was moved forward to the appeal date when the appeal was won, so
            # recover the cycle the *first* response would have landed in.
            original_cycle = _cycle_key(line.remittance_due_on - timedelta(days=21))
        grouped[(payer, original_cycle, outcome)].append((plan, "ORIGINAL"))

        if appeal in {"WON", "LOST"}:
            resolved = line.appeal_resolved_on or (line.remittance_due_on + timedelta(days=30))
            # Strictly after the frequency-7 resubmission it answers.  The engine reads "the
            # payer has responded" as a claim line arriving later than the replacement, so a
            # response landing in the same cycle as the submission would be unreadable — and a
            # lost appeal (B-09/B-13) would be indistinguishable from a pending one.
            grouped[(payer, _cycle_key(resolved + timedelta(days=21)), outcome)].append(
                (plan, "APPEAL")
            )

        if plan.configuration.get("md_remittance") == "DUPLICATE_835":
            later = line.remittance_due_on + timedelta(days=11)
            grouped[(payer, _cycle_key(later), outcome)].append((plan, "ORIGINAL"))
        if line.recoupment_due_on is not None:
            recovery_outcome = plan.configuration.get("cash_reimb_out") or "MATCHED"
            recoupments[
                (payer, _cycle_key(line.recoupment_due_on), recovery_outcome)
            ].append(plan)

    batches: list[tuple[MedicalRemittanceSlice, str]] = []
    batch_number = 0
    for group_key, members in sorted(grouped.items()):
        payer_id, cycle, outcome = group_key
        for chunk_index, chunk in enumerate(_chunked(members, MAX_CLAIMS_PER_REMITTANCE)):
            slice_ref = f"med-835:{payer_id}:{cycle}:{outcome}:{chunk_index}"
            rng = rng_for(settings.master_seed, str(settings.profile), "med835", slice_ref)
            effective = _cycle_effective_date(cycle, rng)
            claim_lines = tuple(
                _medical_claim_line(plan, rng, refs, leg) for plan, leg in chunk
            )
            plb: list[PlbEntrySlice] = []
            for plan in recoupments.pop((payer_id, cycle, outcome), []):
                plb.append(
                    PlbEntrySlice(
                        reason_code="WO",
                        amount_cents=plan.negative_reimbursement_cents,
                        reference_id=plan.clm01,
                        slice_ref=refs.mint(plan.episode_id, "plb"),
                    )
                )
            # An appeal won by PLB credit rather than by reprocessing: reason code RA,
            # carried negative because it increases the payment.  Both conventions are real
            # and a connector has to recognise each rather than look for an "appeal" flag
            # that does not exist anywhere in X12.
            for plan, leg in chunk:
                if (
                    leg == "APPEAL"
                    and plan.configuration.get("md_appeal") == "WON"
                    and rng.randint(0, 1)
                ):
                    plb.append(
                        PlbEntrySlice(
                            reason_code="RA",
                            amount_cents=-apply_bps(plan.expected_reimbursement_cents, 1_000),
                            reference_id=plan.clm01,
                            slice_ref=refs.mint(plan.episode_id, "plb-appeal"),
                        )
                    )
            batches.append(
                (
                    MedicalRemittanceSlice(
                        slice_ref=slice_ref,
                        sequence=(batch_number := batch_number + 1),
                        rng_seed=rng.randrange(2**32),
                        received_at=stamp(effective + timedelta(days=1), hour=6),
                        eft_effective_date=effective.isoformat(),
                        payer_id=payer_id,
                        trace_number=_mint_trace_number(slice_ref),
                        claim_lines=claim_lines,
                        provider_level_adjustments=tuple(plb),
                    ),
                    outcome,
                )
            )

    # A takeback whose cycle produced no claims of its own still happened, and the medical side
    # needs this loop exactly as the pharmacy side does.  Without it a recoupment landing in a
    # cycle with no matching (payer, cycle, cash-fate) group is silently dropped, and B-15 becomes
    # unreachable for those episodes — the clawback simply never appears on any feed.
    for (payer_id, cycle, recovery_outcome), members in sorted(recoupments.items()):
        slice_ref = f"med-835:{payer_id}:{cycle}:RECOUP-{recovery_outcome}:0"
        rng = rng_for(settings.master_seed, str(settings.profile), "med835", slice_ref)
        effective = _cycle_effective_date(cycle, rng)
        batches.append(
            (
                MedicalRemittanceSlice(
                    slice_ref=slice_ref,
                    sequence=(batch_number := batch_number + 1),
                    rng_seed=rng.randrange(2**32),
                    received_at=stamp(effective + timedelta(days=1), hour=6),
                    eft_effective_date=effective.isoformat(),
                    payer_id=payer_id,
                    trace_number=_mint_trace_number(slice_ref),
                    claim_lines=(),
                    provider_level_adjustments=tuple(
                        PlbEntrySlice(
                            reason_code="WO",
                            amount_cents=plan.negative_reimbursement_cents,
                            reference_id=plan.clm01,
                            slice_ref=refs.mint(plan.episode_id, "plb"),
                        )
                        for plan in members
                    ),
                ),
                recovery_outcome,
            )
        )
    return batches


def _medical_claim_line(
    plan: EpisodePlan, rng: random.Random, refs: SliceRefs, leg: str = "ORIGINAL"
) -> MedicalClaimLineSlice:
    """A medical claim line, which **always** balances.

    The medical dispute is about the *reason* for a reduction, never the arithmetic — so
    where a pharmacy underpayment leaves an unexplained residual, a short medical payment
    carries an adjustment that accounts for it.  Which adjustment is the dispute.
    """
    drug = drugs.by_ndc(plan.ndc11)
    configuration = plan.configuration
    remittance = configuration.get("md_remittance")
    appeal = configuration.get("md_appeal")

    # What the payer said the *first* time, before any appeal.  This is the figure the original
    # leg carries, and it is deliberately not the episode's final total.
    original_payment = {
        "PAID_FULL": plan.expected_reimbursement_cents,
        "PARTIAL": plan.expected_reimbursement_cents
        - underpayment_shortfall_cents(plan.expected_reimbursement_cents),
        "DENIED": 0,
        "DUPLICATE_835": plan.expected_reimbursement_cents,
    }.get(remittance, 0)

    if leg == "APPEAL":
        # A won appeal pays the balance; a lost one responds and pays nothing.  Either way the
        # response is what makes the appeal's outcome readable from the feeds at all.
        payment = (
            plan.expected_reimbursement_cents - original_payment if appeal == "WON" else 0
        )
        denied_leg = appeal == "LOST"
    else:
        payment = original_payment
        denied_leg = remittance == "DENIED"

    patient = plan.patient_responsibility_cents if payment > 0 else 0
    contractual = plan.charge_cents - plan.allowed_cents

    adjustments: list[AdjustmentSlice] = []
    if denied_leg:
        payment = 0
        patient = 0
        adjustments.append(AdjustmentSlice("CO", "50", plan.charge_cents))
        status = codes.CLP02_DENIED
    else:
        status = codes.CLP02_PAID_PRIMARY
        if contractual > 0:
            adjustments.append(AdjustmentSlice("CO", "45", contractual))
        if patient > 0:
            adjustments.append(AdjustmentSlice("PR", "2", patient))
        residual = plan.charge_cents - payment - sum(a.amount_cents for a in adjustments)
        if residual > 0:
            # The shortfall is *explained*, and the explanation is what gets appealed.
            # CO-197 (prior authorisation absent) is very common on specialty J-codes, where
            # the drug is administered before the paperwork clears, so it is paired with
            # RARC N522 for machine-readable detail.
            adjustments.append(AdjustmentSlice("CO", "197", residual, rarc="N522"))
        elif residual < 0:
            adjustments.append(AdjustmentSlice("OA", "94", -residual))

    units_per_billing = drug.ctp_units_per_billing_unit or 1
    return MedicalClaimLineSlice(
        slice_ref=refs.mint(plan.episode_id, f"med-line-{leg.lower()}"),
        clm01=plan.clm01 or "",
        # A reprocessed claim gets a brand-new ICN while CLM01 never changes.
        assign_new_icn=leg == "APPEAL",
        clp02_status_code=status,
        charge_cents=plan.charge_cents,
        payment_cents=payment,
        patient_responsibility_cents=patient,
        hcpcs_j_code=drug.hcpcs_j_code or "",
        svc05_units=max(1, (plan.quantity_milli // 1_000) // units_per_billing),
        ndc11=plan.ndc11,
        adjustments=tuple(adjustments),
        administration_line_count=rng.randint(0, 2),
    )


def _rebate_batches(
    plans: Sequence[EpisodePlan],
    timelines: dict[str, Timeline],
    settings: Settings,
    refs: SliceRefs,
) -> list[tuple[RebateBatchSlice, str, str | None]]:
    grouped: dict[tuple[str, str, str], list[EpisodePlan]] = defaultdict(list)
    for plan in plans:
        if not plan.has_rebate_track:
            continue
        line = timelines[plan.episode_id]
        if line.rebate_batch_due_on is None:
            continue
        outcome = plan.configuration.get("cash_rebate_in") or "MATCHED"
        manufacturer = entities.manufacturer_by_id(plan.manufacturer_id or "")
        grouped[(manufacturer.short_name, _cycle_key(line.rebate_batch_due_on), outcome)].append(
            plan
        )
        if plan.configuration.get("r_payment") == "DUPLICATE":
            later = line.rebate_batch_due_on + timedelta(days=13)
            grouped[(manufacturer.short_name, _cycle_key(later), outcome)].append(plan)

    batches: list[tuple[RebateBatchSlice, str, str | None]] = []
    batch_number = 0
    for group_key, members in sorted(grouped.items()):
        short_name, cycle, outcome = group_key
        for chunk_index, chunk in enumerate(_chunked(members, MAX_DISPENSES_PER_REBATE_BATCH)):
            slice_ref = f"rebate:{short_name}:{cycle}:{outcome}:{chunk_index}"
            rng = rng_for(settings.master_seed, str(settings.profile), "rebate", slice_ref)
            effective = _cycle_effective_date(cycle, rng)
            lines = tuple(
                RebateDispenseLineSlice(
                    slice_ref=refs.mint(plan.episode_id, "rebate-line"),
                    rx_number=(
                        plan.rendered_rx(plan.rx_rendering_tpa)
                        if plan.track.value == "PHARMACY"
                        else None
                    ),
                    pharmacy_npi=plan.pharmacy_npi if plan.track.value == "PHARMACY" else None,
                    provider_npi=(
                        None if plan.track.value == "PHARMACY" else plan.billing_provider_npi
                    ),
                    ndc11=plan.ndc11,
                    fill_date=plan.date_of_service.isoformat(),
                    covered_entity_id=plan.covered_entity_id or "",
                    manufacturer_status="APPROVED",
                    rebate_amount_cents=(
                        plan.expected_rebate_cents
                        if plan.configuration.get("r_payment") == "DUPLICATE"
                        else plan.paid_rebate_cents
                    ),
                )
                for plan in chunk
            )
            # A clawback leaves the bank as a genuine debit.  Every member of a clawed-back
            # batch shares that fate, which is why the grouping key carries the outcome.
            reversal = (
                chunk[0].configuration.get("cash_rebate_out")
                if chunk and chunk[0].configuration.get("r_payment") == "CLAWED_BACK"
                else None
            )
            batches.append(
                (
                    RebateBatchSlice(
                        slice_ref=slice_ref,
                        sequence=(batch_number := batch_number + 1),
                        rng_seed=rng.randrange(2**32),
                        received_at=stamp(effective + timedelta(days=1), hour=8),
                        payment_effective_date=effective.isoformat(),
                        manufacturer_short_name=short_name,
                        allocation_code=_mint_allocation_code(slice_ref, effective),
                        dispense_lines=lines,
                    ),
                    outcome,
                    reversal,
                )
            )
    return batches


# ═══ delivery-level defects ═════════════════════════════════════════════════


def _apply_delivery_defects(
    records: Sequence[Record],
    *,
    feed_file: str,
    plans: Sequence[EpisodePlan],
    master_seed: int,
    profile_name: str,
    curated: bool,
) -> list[Record]:
    """D-1 duplicate delivery and D-2 late arrival.

    Both are properties of *ingestion* rather than of a claim, which is why they are applied
    here over finished records rather than folded into any episode's configuration.  They
    can co-occur with any episode state, and they multiply nothing.

    A duplicate is the *same payload* arriving twice with a later ``received_at`` on the
    second copy — not two real events.  An idempotency key must collapse them rather than
    sum them, and a connector that sums them double-counts real money.

    A late arrival shifts ``received_at`` only.  Event dates are untouched, because the
    event did not happen later — we merely heard about it later, and that distinction is the
    whole reason ``received_at`` exists.
    """
    rng = rng_for(master_seed, profile_name, "delivery", feed_file)
    out: list[Record] = []
    for index, record in enumerate(records):
        late = (index % 12 == 7) if curated else rng.randint(0, 9_999) < LATE_ARRIVAL_BPS
        emitted = record
        if late and "received_at" in record.payload:
            shifted = _shift_received_at(
                str(record.payload["received_at"]), rng.randint(*LATE_ARRIVAL_DAYS)
            )
            emitted = Record(
                feed_file=record.feed_file,
                payload={**record.payload, "received_at": shifted},
                slice_ref=record.slice_ref,
            )
        out.append(emitted)

        duplicate = (index % 33 == 16) if curated else rng.randint(0, 9_999) < DUPLICATE_DELIVERY_BPS
        if duplicate and "received_at" in emitted.payload:
            out.append(
                Record(
                    feed_file=emitted.feed_file,
                    payload={
                        **emitted.payload,
                        "received_at": _shift_received_at(
                            str(emitted.payload["received_at"]), rng.randint(1, 3)
                        ),
                    },
                    slice_ref=emitted.slice_ref,
                    is_duplicate_delivery=True,
                )
            )
    return out


def _shift_received_at(received_at: str, days: int) -> str:
    day = date.fromisoformat(received_at[:10]) + timedelta(days=days)
    return f"{day.isoformat()}{received_at[10:]}"


def _defect_directives(
    leaf: dict[str, Any],
    *,
    sequence: int,
    master_seed: int,
    profile_name: str,
    curated: bool,
) -> dict[str, Any]:
    """Assign identifier-drift directives to **exactly one feed** per episode.

    Drift is assigned rather than sampled per generator, because a generator that drifted
    independently would produce a defect nobody could reproduce.  The canonical case from
    the feed spec is the pharmacy 835 rendering the Rx number zero-padded while the claim
    event renders it bare.

    The resulting crosswalk miss is D-6, and Decision A23 forbids the engine from
    normalising it away: the claim is not missing, the *mapping* failed, and those are
    different problems with different owners.
    """
    rng = rng_for(master_seed, profile_name, "drift", sequence)
    has_rebate = leaf["curated_rebate_state"] != "C-00"

    if curated:
        drift = sequence % 17 == 3
        truncate = sequence % 41 == 9
    else:
        drift = rng.randint(0, 9_999) < RX_DRIFT_BPS
        truncate = rng.randint(0, 9_999) < RX_TRUNCATION_BPS

    directives: dict[str, Any] = {}
    if truncate:
        directives["rx_rendering_tpa"] = "TRUNCATED"
    elif drift:
        # Exactly one feed drifts.  The pharmacy 835 is the canonical carrier; the TPA feed
        # takes it when there is a rebate track, since that is the bridge with no fallback.
        if has_rebate and rng.randint(0, 1):
            directives["rx_rendering_tpa"] = "ZERO_PADDED"
        else:
            directives["rx_rendering_pharmacy_835"] = "ZERO_PADDED"

    # ── vendor disagreement ────────────────────────────────────────────────────────
    #
    # Verity and Craneware are formatted from one shared source object, so without this
    # they report the same qualification for the same dispense, always. That makes the
    # vendor layer incapable of the one failure a reconciliation engine exists to catch,
    # and "both vendors agree" is then a property of the generator rather than a finding.
    #
    # **Only when the TPA rendering is already canonical.** If the feed itself drifted, both
    # vendors read that drifted value off the sidecar and still agree with each other —
    # stacking a second divergence on top would make a vendor disagreement indistinguishable
    # from D-6, which is the one thing this has to be told apart from.
    #
    # A separate modulus from the drift above, and a coprime one, so the two defects do not
    # land on the same episodes by arithmetic coincidence and leave the interaction untested.
    if not directives.get("rx_rendering_tpa") and has_rebate:
        diverge = sequence % 13 == 5 if curated else rng.randint(0, 9_999) < VENDOR_DIVERGENCE_BPS
        if diverge:
            directives["rx_rendering_craneware"] = "ZERO_PADDED"
    return directives


# ═══ ground truth ═══════════════════════════════════════════════════════════


def _build_ground_truth(
    *,
    settings: Settings,
    plans: Sequence[EpisodePlan],
    timelines: dict[str, Timeline],
    declared: Sequence[DeclaredMovement],
    cash_events: Sequence[CashEvent],
    leaf_digest: str,
) -> dict[str, Any]:
    """What the orchestrator knows and the connector is forbidden to read.

    This file exists so the crosswalk can be *scored* rather than assumed.  It records the
    intended verdict pair per episode, every expected amount, and — critically — which links
    are resolvable and which are not, so a test can tell "the connector failed to match
    these" apart from "these were never meant to match".

    ``expected_links`` is where D-6 lives.  When an episode's Rx number drifts in one feed,
    the link across that bridge is recorded as unresolvable *by design*, and a connector
    that resolves it anyway has normalised away the defect (Decision A23).
    """
    # Which medical 340B natural keys are shared by more than one episode.  Those episodes are
    # *genuinely* indistinguishable to the connector — ``{provider_npi, ndc11, service_date}`` is
    # the whole key and it does not separate them — so the correct connector behaviour is to park
    # them as ambiguous.  Recording it here is what lets a test tell that correct behaviour apart
    # from a crosswalk bug, instead of scoring the engine down for refusing to guess.
    # Counted over **every** medical episode, not only the 340B-bearing ones — because an 837
    # publishes the key unconditionally, so a non-340B episode sharing the triple is just as much
    # a second candidate, and the resolution is just as ambiguous.  Counting only rebate-bearing
    # episodes understates the ambiguity and leaves genuinely unresolvable cases scored as though
    # the engine had got them wrong.
    medical_340b_triples: dict[tuple[str, str, str], int] = defaultdict(int)
    for plan in plans:
        if plan.track.value == "MEDICAL":
            medical_340b_triples[
                (
                    plan.billing_provider_npi or "",
                    plan.ndc11,
                    plan.date_of_service.isoformat(),
                )
            ] += 1

    episodes: list[dict[str, Any]] = []
    for plan in plans:
        line = timelines[plan.episode_id]
        episodes.append(
            {
                "episode_id": plan.episode_id,
                "case_id": plan.case_id,
                "configuration": plan.configuration,
                "intended_reimbursement_verdict": plan.reimbursement_verdict,
                "intended_rebate_verdict": plan.rebate_verdict,
                "coherence": plan.coherence,
                "cross_track_flags": list(plan.cross_track_flags),
                "reimbursement_track": plan.track.value,
                "ndc11": plan.ndc11,
                "date_of_service": plan.date_of_service.isoformat(),
                "quantity_milli": plan.quantity_milli,
                "natural_keys": _natural_keys(plan),
                "expected_links": _expected_links(plan, medical_340b_triples),
                "money": {
                    "charge_cents": plan.charge_cents,
                    "allowed_cents": plan.allowed_cents,
                    "patient_responsibility_cents": plan.patient_responsibility_cents,
                    "expected_reimbursement_cents": plan.expected_reimbursement_cents,
                    "paid_reimbursement_cents": plan.paid_reimbursement_cents,
                    "reimbursement_variance_cents": plan.reimbursement_variance_cents,
                    "expected_rebate_cents": plan.expected_rebate_cents,
                    "paid_rebate_cents": plan.paid_rebate_cents,
                    "rebate_variance_cents": plan.rebate_variance_cents,
                    "negative_reimbursement_cents": plan.negative_reimbursement_cents,
                    "negative_rebate_cents": plan.negative_rebate_cents,
                },
                "timeline": {
                    "claim_received_at": line.claim_received_at,
                    "remittance_due_on": (
                        line.remittance_due_on.isoformat() if line.remittance_due_on else None
                    ),
                    "qualification_received_at": line.qualification_received_at,
                    "decision_received_at": line.decision_received_at,
                    "rebate_batch_due_on": (
                        line.rebate_batch_due_on.isoformat()
                        if line.rebate_batch_due_on
                        else None
                    ),
                },
            }
        )

    # The intended allocation split, recorded so a test can assert the splitter
    # independently rather than trusting it (Decision A17's stated cost).
    allocations = [
        {
            "movement_ref": item.movement.slice_ref,
            "declared_amount_cents": item.movement.amount_cents,
            "payer_reference": item.movement.payer_reference,
            "cash_outcome": item.outcome,
            "entry_description": item.movement.entry_description,
        }
        for item in declared
    ]

    pair_counts: dict[str, int] = defaultdict(int)
    for plan in plans:
        pair_counts[f"{plan.reimbursement_verdict}|{plan.rebate_verdict}"] += 1

    return {
        "schema": "recon.ground_truth/1",
        "profile": str(settings.profile),
        "master_seed": settings.master_seed,
        "window": {
            "start": settings.window_start.isoformat(),
            "end": settings.window_end.isoformat(),
        },
        "reference_fingerprint": reference_fingerprint(),
        "decision_tree_digest": leaf_digest,
        "episode_count": len(plans),
        "verdict_pair_counts": dict(sorted(pair_counts.items())),
        "distinct_verdict_pairs": len(pair_counts),
        "episodes": episodes,
        "declared_movements": allocations,
        "bank_row_count": len(cash_events),
        "orphan_bank_rows": sum(1 for event in cash_events if event.movement_ref is None),
        "addenda_dropped_rows": sum(
            1 for event in cash_events if event.payer_reference is None
        ),
    }


def _natural_keys(plan: EpisodePlan) -> dict[str, str | None]:
    """The canonical key forms this episode's records *should* resolve to.

    Built through :mod:`recon.crosswalk.keys` so ground truth and the connector cannot
    disagree about spelling — if they did, every assertion would be measuring the key
    builder rather than the crosswalk.
    """
    out: dict[str, str | None] = {}
    if plan.track.value == "PHARMACY" and plan.pharmacy_npi and plan.rx_number:
        _, value = keys.ncpdp_claim(
            plan.pharmacy_npi,
            plan.rx_number,
            plan.fill_number or "00",
            plan.date_of_service.isoformat(),
        )
        out["NCPDP_CLAIM"] = value
        if plan.has_rebate_track:
            _, natural = keys.natural_340b_pharmacy(
                plan.pharmacy_npi,
                plan.rx_number,
                plan.ndc11,
                plan.date_of_service.isoformat(),
            )
            out["NATURAL_340B_PHARMACY"] = natural
    if plan.track.value == "MEDICAL" and plan.clm01:
        _, value = keys.medical_clm01(plan.clm01)
        out["MEDICAL_CLM01"] = value
        if plan.has_rebate_track and plan.billing_provider_npi:
            _, natural = keys.natural_340b_medical(
                plan.billing_provider_npi, plan.ndc11, plan.date_of_service.isoformat()
            )
            out["NATURAL_340B_MEDICAL"] = natural
    return out


def _expected_links(
    plan: EpisodePlan, medical_340b_triples: dict[tuple[str, str, str], int] | None = None
) -> dict[str, Any]:
    """Which bridges should resolve, and which are broken on purpose.

    A connector that resolves a link marked unresolvable has normalised drift away and deleted
    the D-6 exception; a connector that fails to resolve one marked resolvable has a real bug.
    Recording both is what makes the distinction testable rather than a matter of opinion.

    Two mechanisms break a link, and they are different in kind:

    * **identifier drift** — one feed spells the Rx number differently, so the key genuinely does
      not match (D-6);
    * **key ambiguity** — the medical 340B natural key cannot separate two same-day
      administrations of the same drug at the same site, so the correct behaviour is to park both
      rather than attribute money to a coin flip.
    """
    drifted_feeds = [
        name
        for name, rendering in (
            ("pbm_remittance_835", plan.rx_rendering_pharmacy_835),
            ("tpa_340b_events", plan.rx_rendering_tpa),
        )
        if rendering != "CANONICAL"
    ]

    ambiguous_340b_key = False
    if plan.track.value == "MEDICAL" and plan.has_rebate_track and medical_340b_triples:
        triple = (
            plan.billing_provider_npi or "",
            plan.ndc11,
            plan.date_of_service.isoformat(),
        )
        ambiguous_340b_key = medical_340b_triples.get(triple, 0) > 1

    rebate_resolvable = plan.rx_rendering_tpa == "CANONICAL" and not ambiguous_340b_key
    return {
        "claim_to_remittance_resolvable": plan.rx_rendering_pharmacy_835 == "CANONICAL",
        "claim_to_340b_resolvable": rebate_resolvable,
        "drifted_feeds": drifted_feeds,
        "ambiguous_340b_key": ambiguous_340b_key,
        # True when the dataset *intends* the connector to fail to resolve something for this
        # episode.  A verdict test must treat these separately: the engine reading a smaller
        # picture is the exception working, not the engine being wrong.
        "expected_crosswalk_miss": bool(drifted_feeds) or ambiguous_340b_key,
    }


# ═══ writing ════════════════════════════════════════════════════════════════


def write_outputs(settings: Settings, result: GenerationResult) -> dict[str, str]:
    """Write the six feed files, ground truth and the reproducibility manifest.

    Returns the per-file SHA-256 map, which is also what goes into the manifest: same seed,
    same window, same reference data, same hashes.  That is the testable form of
    "reproducible", as opposed to the claimed form.
    """
    feeds_dir = settings.feeds_dir()
    truth_dir = settings.truth_dir()
    feeds_dir.mkdir(parents=True, exist_ok=True)
    truth_dir.mkdir(parents=True, exist_ok=True)

    hashes: dict[str, str] = {}
    for feed_file in config.FEED_FILENAMES:
        records = result.records_by_feed.get(feed_file, [])
        if feed_file == config.BANK_TRANSACTIONS_FILE:
            text = bank_gen.write_bank_csv(records)
        else:
            text = "".join(
                json.dumps(record.payload, separators=(",", ":"), sort_keys=False) + "\n"
                for record in _sorted_by_arrival(records)
            )
        path = feeds_dir / feed_file
        path.write_text(text, encoding="utf-8", newline="")
        hashes[feed_file] = hashlib.sha256(text.encode("utf-8")).hexdigest()

    truth_path = settings.ground_truth_path()
    truth_text = json.dumps(result.ground_truth, indent=1, sort_keys=False) + "\n"
    truth_path.write_text(truth_text, encoding="utf-8", newline="")

    # The vendor identifier sidecar (requirement M1).  Written beside the feeds, never into
    # them: ``load_feeds`` iterates ``FEED_FILENAMES`` and never globs, so this file is
    # invisible to ingestion, to ``raw_record``, and to every agent tool -- which is what
    # keeps the six feed hashes, and therefore every recorded agent prompt, unchanged.
    vendor_dir = settings.vendor_dir()
    vendor_dir.mkdir(parents=True, exist_ok=True)
    vendor_text = "".join(
        json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n" for row in result.vendor_refs
    )
    settings.vendor_identifiers_path().write_text(vendor_text, encoding="utf-8", newline="")

    manifest = {
        "schema": "recon.manifest/1",
        "profile": str(settings.profile),
        "master_seed": settings.master_seed,
        # The seed alone stopped identifying a demo dataset the moment the curated spine
        # became a choice: two runs can agree on seed, window and reference data and still
        # differ in composition. Recorded so the manifest still answers "what reproduces
        # these bytes" with everything, rather than with everything except one field.
        "curated_spine": str(settings.curated_spine),
        "window": {
            "start": settings.window_start.isoformat(),
            "end": settings.window_end.isoformat(),
        },
        "reference_fingerprint": reference_fingerprint(),
        "decision_tree_digest": result.leaf_digest,
        "episode_count": result.episode_count,
        "feed_sha256": hashes,
        "ground_truth_sha256": hashlib.sha256(truth_text.encode("utf-8")).hexdigest(),
    }
    settings.manifest_path().write_text(
        json.dumps(manifest, indent=1) + "\n", encoding="utf-8", newline=""
    )
    return hashes


def _sorted_by_arrival(records: Sequence[Record]) -> list[Record]:
    """Records land in the file in the order they arrived.

    A feed file is a delivery log, so arrival order is the only order that makes sense —
    and it is what makes the cursor's ``received_at <= cursor`` filter correspond to a
    prefix of the file.
    """
    ordered = sorted(
        enumerate(records),
        key=lambda pair: (str(pair[1].payload.get("received_at", "")), pair[0]),
    )
    return [record for _, record in ordered]


# ═══ small helpers ══════════════════════════════════════════════════════════


def _chunked(items: Sequence[Any], size: int) -> Iterable[Sequence[Any]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _cycle_effective_date(cycle: str, rng: random.Random) -> date:
    """The banking day a cycle's payment is instructed to settle on."""
    year_text, week_text = cycle.split("-W")
    monday = date.fromisocalendar(int(year_text), int(week_text), 1)
    return calendar.add_banking_days(monday, rng.randint(1, 4))


def _date_of_service(settings: Settings, *, sequence: int, profile_name: str) -> date:
    """A dispense date inside the window, with room for the downstream timeline.

    The tail of the window is deliberately left clear: an episode dispensed on the last day
    would have its remittance and rebate fall outside the generated window, so it could
    never reach a settled verdict and would misrepresent itself as perpetually pending.
    """
    rng = rng_for(settings.master_seed, profile_name, "dos", sequence)
    span = (settings.window_end - settings.window_start).days
    # 150 days of headroom covers the longest chain: remittance, appeal, then recoupment.
    usable = max(1, span - 150)
    return settings.window_start + timedelta(days=rng.randrange(usable))


#: A contractual adjustment reduces the claim by 8% of the expected amount.  Small enough to be
#: plausible as a bundling or fee-schedule reduction, large enough to be unmistakable against
#: zero-tolerance integer-cent matching.
def _ADJUSTMENT_REDUCTION(plan) -> int:
    return apply_bps(plan.expected_reimbursement_cents, 800)


#: How often a medical 340B episode is deliberately given a triple that already exists, so the
#: connector's ambiguous-match path is exercised at a known rate instead of a chance one.
MEDICAL_340B_COLLISION_BPS = 400


def _place_medical_340b_date(
    preferred: date,
    *,
    ndc11: str,
    used: set[tuple[str, str]],
    settings: Settings,
    rng: random.Random,
    curated: bool,
) -> date:
    """Give a medical 340B episode a service date that makes its natural key unique — mostly.

    ``used`` accumulates ``(ndc11, service_date)`` pairs; the provider NPI is constant, so
    those two fields are the whole key.  A deliberate quota reuses an existing pair, which is
    the ambiguous-match case the connector must park rather than resolve.

    Walks forward from the preferred date rather than resampling, so the episode stays in
    roughly the part of the window it was drawn for and the dataset keeps its even spread.
    """
    deliberate_collision = (
        bool(used)
        and not curated
        and rng.randint(0, 9_999) < MEDICAL_340B_COLLISION_BPS
    )
    if deliberate_collision:
        existing = sorted(pair for pair in used if pair[0] == ndc11)
        if existing:
            return date.fromisoformat(existing[rng.randrange(len(existing))][1])

    candidate = preferred
    limit = settings.window_end
    while (ndc11, candidate.isoformat()) in used and candidate < limit:
        candidate += timedelta(days=1)
    used.add((ndc11, candidate.isoformat()))
    return candidate


def _dispensing_fee(plan: EpisodePlan) -> int:
    from recon.reference.pricing import contract_terms

    return contract_terms(plan.payer_id, plan.ndc11).dispensing_fee_cents


def _reject_canonical(plan: EpisodePlan, settings: Settings) -> str:
    rng = rng_for(settings.master_seed, str(settings.profile), "reject", plan.episode_id)
    return stable_choice(
        rng,
        [
            "PRIOR_AUTH_REQUIRED",
            "NOT_COVERED",
            "PLAN_LIMIT_EXCEEDED",
            "REFILL_TOO_SOON",
            "PATIENT_NOT_COVERED",
        ],
    )


def _rejection_stc(plan: EpisodePlan, settings: Settings) -> str:
    rng = rng_for(settings.master_seed, str(settings.profile), "stc", plan.episode_id)
    return stable_choice(rng, ["A3:21", "A3:33", "A3:187"])


def _disqualification_reason(plan: EpisodePlan, settings: Settings) -> str | None:
    """Re-derive the reason the sampler bound entities to satisfy.

    Recomputed from the same seed path the sampler used rather than threaded through the
    plan, so the two cannot drift apart: if they did, the feed would assert a reason the
    reference data contradicts.
    """
    if plan.configuration.get("r_qualification") != "NOT_QUALIFIED":
        return None
    from recon.generators.sampling import DISQUALIFICATION_BINDINGS

    sequence = int(plan.episode_id.split("-")[1])
    rng = rng_for(settings.master_seed, str(settings.profile), "bind", sequence)
    return stable_choice(rng, sorted(DISQUALIFICATION_BINDINGS))


def _rejection_reason(plan: EpisodePlan, settings: Settings) -> str | None:
    if plan.configuration.get("r_manufacturer") != "REJECTED":
        return None
    sequence = int(plan.episode_id.split("-")[1])
    rng = rng_for(settings.master_seed, str(settings.profile), "bind", sequence)
    if plan.configuration.get("r_qualification") == "NOT_QUALIFIED":
        from recon.generators.sampling import DISQUALIFICATION_BINDINGS

        stable_choice(rng, sorted(DISQUALIFICATION_BINDINGS))
    return stable_choice(rng, ["CONTRACT_PHARMACY_RESTRICTED", "NON_CONFORMING_45_DAY"])


def _authorization_for(plan: EpisodePlan, settings: Settings) -> str:
    """The PBM authorization number, as the PBM generator minted it.

    Recomputed here from the same seed so the 835's ``ref_authorization_number`` matches the
    adjudication's — a value that genuinely crosses the two PBM systems, and therefore one
    the orchestrator is entitled to hand to both (Decision 44's crossing matrix).
    """
    index = int(plan.episode_id.split("-")[1]) - 1
    seed = rng_for(settings.master_seed, str(settings.profile), "pbm", index).randrange(2**32)
    rng = random.Random(seed)
    return f"AUTH{rng.randint(1_000_000, 9_999_999):07d}{chr(65 + rng.randint(0, 25))}"


def _prior_icn(plan: EpisodePlan) -> str:
    """A stable stand-in for the ICN a prior remittance assigned.

    A replacement or void points at this through ``REF*F8``.  It is deliberately *not*
    guaranteed to match any ICN actually minted on an earlier 835 — in reality a provider
    copies back whatever the payer last told them, and that value can be stale or simply
    wrong.  The connector therefore must not depend on it: a replacement resolves by
    ``CLM01``, which is the provider's own key and is stable for the whole life of the claim,
    and ``REF*F8`` is corroboration rather than identity.
    """
    return f"{20_260_610_000_000 + derive_seed(_MINT_NAMESPACE, 'icn', plan.episode_id) % 9_999_999:014d}"


def _mint_trace_number(slice_ref: str) -> str:
    """The payer's reassociation trace number.

    Minted by the orchestrator because it genuinely crosses two systems: the payer writes it
    on the 835 and the bank may carry it in the CCD+ addenda.  Handing the same value to two
    generators is legitimate exactly where reality does it too.

    Derived with ``blake2b`` rather than a character sum.  That is not fussiness: a character
    sum over structurally similar slice refs collides often, and a collided trace number makes
    two different remittances indistinguishable to every deposit that references either —
    which silently attributes one batch's cash to another batch's claims.
    """
    return f"{8_000_000_000 + derive_seed(_MINT_NAMESPACE, 'trn02', slice_ref) % 999_999_999:010d}"


def _mint_allocation_code(slice_ref: str, effective: date) -> str:
    """The rebate batch reference, which rides to the bank in the ``trn02`` column.

    Wide enough not to collide.  An earlier version used ``sum(ord(...)) % 100``, which gave
    two batches settling in the same week a better-than-even chance of sharing a code.
    """
    suffix = derive_seed(_MINT_NAMESPACE, "alloc", slice_ref) % 100_000
    return f"RBT-{effective.strftime('%Y%m%d')}-{suffix:05d}"


# ═══ vendor-layer identifiers (requirement M1) ══════════════════════════════
#
# Minted here for the same reason ``trn02`` and ``allocation_code`` are: each is a value two
# independent systems must agree on, so exactly one component may decide it.  A mock that
# minted its own Beacon ID would be inventing a fact the rest of the system then has to
# accept on faith -- and the connector that later joins on it would be testing the mock's
# imagination rather than the crosswalk.
#
# All three formats are INVENTED and declared as such.  ``BEACON-013`` establishes that
# Beacon IDs exist and are persisted against the claim; the pages that would have given
# their *shape* are ``BEACON-001`` through ``BEACON-007``, every one of them a 403.
# ``VERITY-006`` establishes that a Split Transaction specification exists without
# publishing it.  Inventing a format is the honest response to that; inventing one and
# calling it SPEC would not be.


def _mint_beacon_id(slice_ref: str) -> str:
    """Beacon's own identifier for a submitted claim.

    Opaque on purpose.  Beacon assigns this, so it must carry no structure we could have
    derived ourselves -- if a Beacon ID encoded the NDC or the fill date, a connector could
    "resolve" an episode without the crosswalk ever running, and the test that proves the
    join works would prove nothing.
    """
    return f"BCN-{derive_seed(_MINT_NAMESPACE, 'beacon', slice_ref) % 1_000_000_000_000:012d}"


def _mint_accumulation_id(slice_ref: str) -> str:
    """Verity's reference for the accumulation a qualified dispense contributes to."""
    return f"ACC-{derive_seed(_MINT_NAMESPACE, 'verity-acc', slice_ref) % 100_000_000:08d}"


def _mint_invoice_number(slice_ref: str) -> str:
    """Verity's reference for the replenishment invoice a dispense lands on."""
    return f"VINV-{derive_seed(_MINT_NAMESPACE, 'verity-inv', slice_ref) % 10_000_000:07d}"


#: Fields a vendor identifier row may never carry.  The sidecar is read by the mocks, and a
#: mock that could see an episode id, a verdict or an expected amount would be able to tell
#: a consistent story without the generator's blind slice ever being consulted -- which is
#: the guarantee ``contracts.assert_slice_is_blind`` exists to hold.  Same prohibition, one
#: layer out, because the sidecar crosses the same boundary by a different road.
FORBIDDEN_VENDOR_REF_FIELDS: frozenset[str] = frozenset(
    {
        "episode_id",
        "case_id",
        "verdict",
        "reimbursement_verdict_code",
        "rebate_verdict_code",
        "cross_track_flags",
        "coherence",
        "disposition",
        "expected_reimbursement_cents",
        "expected_rebate_cents",
        "rebate_amount",
        "amount_cents",
    }
)


def _vendor_identifier_rows(
    plans: Sequence[EpisodePlan],
    settings: Settings,
    refs: SliceRefs,
) -> list[dict[str, Any]]:
    """One row per rebate-track dispense: the minted ids, plus the keys to join them on.

    The join keys are rendered **exactly as the TPA feed renders them**, drift included.  An
    episode carrying the D-6 identifier defect writes a drifted ``rx_number`` to
    ``tpa_340b_events.jsonl``, and a sidecar that quietly wrote the clean value would hand
    the vendor layer a working join the real feed does not have -- repairing the defect by
    accident and deleting the crosswalk-miss exception it exists to produce.
    """
    rows: list[dict[str, Any]] = []
    for plan in plans:
        if not plan.has_rebate_track:
            continue
        is_pharmacy = plan.track.value == "PHARMACY"
        slice_ref = refs.mint(plan.episode_id, "vendor")
        rows.append(
            {
                "beacon_id": _mint_beacon_id(slice_ref),
                "accumulation_id": _mint_accumulation_id(slice_ref),
                "invoice_number": _mint_invoice_number(slice_ref),
                # -- join keys, spelled as the TPA feed spells them --
                #
                # ``ndc_11`` with the underscore, and ``fill_date`` through
                # ``iso_date_to_wire``, because that is what ``tpa.py`` writes.  A sidecar
                # spelling these its own way would look correct in review and join to
                # nothing at runtime.
                "rx_number": plan.rendered_rx(plan.rx_rendering_tpa) if is_pharmacy else None,
                # Craneware's spelling of the same Rx, which is usually the same string and
                # sometimes deliberately is not.
                #
                # **A new column rather than a changed one**, and that is load-bearing.
                # ``mocks.source.load_source`` joins the TPA events onto this row on exactly
                # five fields, ``rx_number`` among them, so diverting the existing column
                # would not produce a vendor disagreement — it would silently unjoin the
                # dispense and hand both vendors a row with no events at all.
                "rx_number_craneware": (
                    plan.rendered_rx(plan.rx_rendering_craneware) if is_pharmacy else None
                ),
                "pharmacy_npi": plan.pharmacy_npi if is_pharmacy else None,
                "provider_npi": None if is_pharmacy else plan.billing_provider_npi,
                "ndc_11": plan.ndc11,
                "fill_date": keys.iso_date_to_wire(plan.date_of_service.isoformat()),
                "covered_entity_id": plan.covered_entity_id or "",
                "manufacturer": entities.manufacturer_by_id(
                    plan.manufacturer_id or ""
                ).short_name,
            }
        )
    return rows


def assert_vendor_rows_are_blind(rows: Sequence[dict[str, Any]]) -> None:
    """The sidecar's counterpart to :func:`contracts.assert_slice_is_blind`."""
    for row in rows:
        offenders = sorted(set(row) & FORBIDDEN_VENDOR_REF_FIELDS)
        if offenders:
            raise AssertionError(
                f"vendor identifier row declares forbidden field(s) {offenders}. "
                "The mocks read this file; a mock that can see the answer is not a "
                "formatter, and the crosswalk it feeds stops being a measurement."
            )
        for name, value in row.items():
            if isinstance(value, str) and value.startswith(("EP-", "EPISODE-", "CASE-")):
                raise AssertionError(
                    f"vendor identifier row field {name!r} = {value!r} looks like an "
                    "episode id; episode identity is the orchestrator's alone."
                )
