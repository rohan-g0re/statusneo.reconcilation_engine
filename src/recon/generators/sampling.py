"""Choosing which configurations to generate, and binding entities to them.

Two jobs, both of which are easy to get subtly wrong.

═══ 1. Sampling stratifies over verdicts, never over configurations ════════════════

Configuration density across the 372 verdict pairs spans **252x** (min 1, max 252), and
**99 of the 372 pairs sit on exactly one configuration**.  Sampling uniformly over the
4,224 configurations would very plausibly miss every single-configuration pair in a
1,500-episode run — and those are the rarest and most valuable cases, which is precisely
backwards (Decision 15).

So the ``full`` profile guarantees **one episode per verdict pair first**, then fills the
remainder.  Coverage stops being a hope and becomes arithmetic: 372 pairs, 372 reserved
slots, and a test that fails if any pair is unproduced (Decision 48).

═══ 2. Entity binding has to be *consistent* with the configuration ════════════════

This is the part that would otherwise quietly ruin the dataset.  If a TPA record says
``NOT_QUALIFIED`` for ``PRESCRIBER_NOT_AFFILIATED``, then the prescriber actually bound to
that episode must actually be unaffiliated with the covered entity.  Otherwise the feed
asserts something the reference data contradicts, and anyone tracing the claim end to end
finds a dataset that does not hang together.

The same applies to ``UNREGISTERED_LOCATION`` (must be the satellite pharmacy),
``CONTRACT_PHARMACY_RESTRICTED`` (must be a manufacturer that restricts) and
``NON_CONFORMING_45_DAY`` (the submission date must genuinely be more than 45 days after
the fill).  :func:`bind_entities` resolves the reason *first* and then picks entities that
make it true, rather than picking entities and hoping.
"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Sequence

from recon.domain.enums import BenefitType
from recon.reference import drugs, entities, patients
from recon.rng import derive_seed, rng_for, stable_choice

__all__ = [
    "LeafCatalogue",
    "load_leaves",
    "select_configurations",
    "bind_entities",
    "DISQUALIFICATION_BINDINGS",
]


# ═══ the frozen artefact ════════════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class LeafCatalogue:
    """``leaves_classified.json``, plus the digest that proves which build it came from.

    ``decision_tree/`` is frozen (Decision 48).  The digest is recorded in the run
    manifest so a dataset can always be traced to the exact state-space enumeration that
    produced it — if someone re-runs the generator after the tree moves, the manifests
    differ and the change is visible rather than silent.
    """

    leaves: tuple[dict[str, Any], ...]
    digest: str

    @property
    def pairs(self) -> tuple[tuple[str, str], ...]:
        seen: dict[tuple[str, str], None] = {}
        for leaf in self.leaves:
            seen[(leaf["curated_reimbursement_state"], leaf["curated_rebate_state"])] = None
        return tuple(sorted(seen))

    def by_pair(self) -> dict[tuple[str, str], list[dict[str, Any]]]:
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for leaf in self.leaves:
            key = (leaf["curated_reimbursement_state"], leaf["curated_rebate_state"])
            grouped.setdefault(key, []).append(leaf)
        return grouped


def load_leaves(decision_tree_dir: Path) -> LeafCatalogue:
    """Read and digest the classified leaves.

    Raises:
        FileNotFoundError: with the rebuild command, because a missing oracle is a
            broken build rather than a reason to generate something approximate.
    """
    path = Path(decision_tree_dir) / "leaves_classified.json"
    if not path.exists():
        raise FileNotFoundError(
            f"{path} is missing. It is the generator's configuration source and a frozen "
            "artefact; rebuild it with `python decision_tree/classify.py`."
        )
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    leaves = json.loads(raw.decode("utf-8"))
    if not leaves:
        raise ValueError(f"{path} is empty")
    return LeafCatalogue(leaves=tuple(leaves), digest=digest)


# ═══ selection ══════════════════════════════════════════════════════════════


#: The ``demo`` profile's hand-stratified spine (Decision 14).  Ordered by the story a
#: walkthrough tells, not by verdict code, because its entire job is to be read end to end.
#:
#: Every pair here is deliberate.  The assignment's data requirement names eight edge cases
#: — *fully reconciled, partial payment, underpayment, reversal/recoupment, unmatched cash
#: or rebate, denial, duplicate event, late-arriving status* — and each one appears below at
#: least once on each reimbursement track where it is representable.  The cross-track
#: compliance cases are included because X-1 and X-2 are the strongest demo material in the
#: whole dataset: both are **invisible to any single-track system**, which is the clearest
#: possible argument for reconciling at the episode level rather than the claim level.
CURATED_PAIRS: tuple[tuple[str, str], ...] = (
    # --- the happy paths, one per reimbursement track ----------------------
    ("A-04", "C-00"),   # fully reconciled, no 340B
    ("A-04", "C-08"),   # fully reconciled on both tracks — the best case in the system
    ("B-04", "C-00"),
    ("B-04", "C-08"),
    # --- settlement, the verdict that hides in CLP02 -----------------------
    ("A-06", "C-00"),
    ("A-06", "C-08"),
    # --- underpayment and partial payment ---------------------------------
    ("A-07", "C-00"),   # underpaid, cash matched the short amount
    ("A-08", "C-00"),   # underpaid AND no cash: two independent defects, one track
    ("B-06", "C-00"),   # partial, no appeal filed — the window may still be open
    ("B-07", "C-08"),   # partial, appeal pending
    ("B-08", "C-00"),   # partial, appeal won, balance paid through a second payment
    ("B-09", "C-00"),   # partial, appeal lost — a write-off decision, not a receivable
    # --- unmatched cash: paperwork and money disagree ---------------------
    ("A-05", "C-00"),
    ("B-05", "C-00"),
    ("A-05", "C-09"),   # X-5: no cash on BOTH tracks -> one correlated root cause
    # --- unmatched rebate --------------------------------------------------
    ("A-04", "C-09"),   # the TPA's ledger and the bank disagree
    ("A-04", "C-10"),   # partial rebate — usually a unit or price dispute
    # --- denial ------------------------------------------------------------
    ("A-01", "C-00"),   # rejected at the point of sale; terminal
    ("B-10", "C-00"),   # denied, nobody has decided appeal-or-write-off yet
    ("B-11", "C-00"),   # denied, appeal pending
    ("B-12", "C-00"),   # denied, appeal won, fully recovered
    ("B-13", "C-00"),   # denied, appeal lost — terminal loss, cost of goods unrecovered
    ("B-14", "C-00"),   # appeal won and STILL no money: the highest-value chase
    ("B-01", "C-00"),   # clearinghouse rejection: the payer never saw it
    ("B-01", "C-08"),   # ...and the drug was still administered, so 340B is legitimate
    # --- reversal and recoupment ------------------------------------------
    ("A-10", "C-00"),   # reversed, money returned, net zero
    ("A-11", "C-00"),   # reversal recorded, money NOT returned — we hold cash we shouldn't
    ("A-16", "C-00"),   # reversed before payment; expected falls to zero
    ("A-12", "C-00"),   # recouped, netted into a later batch, offset traced
    ("A-13", "C-00"),   # recoupment untraceable to any deposit -> INSUFFICIENT_DATA
    ("B-15", "C-00"),   # medical takeback
    # --- duplicate events --------------------------------------------------
    ("A-17", "C-00"),   # two payment events for one claim
    ("B-16", "C-00"),   # two remittances for one claim
    ("A-04", "C-14"),   # two rebate payments for one dispense
    # --- contractual adjustment, explained and unexplained ----------------
    ("A-14", "C-00"),   # adjustment explains the shortfall; remainder settled
    ("A-15", "C-00"),   # adjustment applied, variance still unexplained
    ("A-09", "C-00"),   # overpayment: a refund liability, not a windfall
    # --- the 340B chain, gate by gate -------------------------------------
    ("A-04", "C-01"),   # qualification pending at the TPA
    ("A-04", "C-02"),   # not qualified — a correct outcome, not a failure
    ("A-04", "C-03"),   # qualified, request not yet submitted
    ("A-04", "C-05"),   # submitted, manufacturer silent
    ("A-04", "C-07"),   # X-4: manufacturer rejected. Reimbursement is clean -> NOT a flag
    ("A-04", "C-11"),   # approved, awaiting payment
    ("A-04", "C-13"),   # rebate clawed back
    ("B-04", "C-01"),
    ("B-04", "C-07"),
    ("B-04", "C-13"),
    # --- cross-track compliance: the cases a single-track system cannot see -
    ("A-01", "C-08"),   # X-1: payer refused the claim, we are holding rebate money
    ("B-10", "C-08"),   # X-1 on the medical track
    ("A-10", "C-08"),   # X-2: drug never reached the patient, rebate still standing
    ("A-11", "C-08"),   # X-2 again: reversal recorded, money not returned, rebate live
    ("A-16", "C-08"),   # X-2 pre-payment
    ("A-12", "C-08"),   # X-3: recoupment undermines the facts qualification relied on
    ("A-01", "C-07"),   # X-6: total loss — no remaining collection path anywhere
    ("B-13", "C-07"),   # X-6 on the medical track
    ("B-12", "C-13"),   # X-7: appeal won AFTER the rebate was clawed back -> re-request
    # --- awaiting states, so the pending queue is not empty ---------------
    ("A-02", "C-00"),
    ("A-02", "C-01"),
    ("B-02", "C-00"),
    ("B-02", "C-05"),
)


def select_curated(catalogue: LeafCatalogue, *, episode_count: int) -> list[dict[str, Any]]:
    """The ``demo`` profile: hand-placed verdict pairs, in narrative order.

    Deliberately **not** random and deliberately not stratified.  The debrief asks for one
    claim traced end to end, and nobody does that against a 1,500-episode file — so this
    profile exists purely to be legible.  Its spine is :data:`CURATED_PAIRS`.

    Within a pair the shallowest configuration is chosen, because the shortest decision path
    is the clearest worked example of that verdict — which is what someone reading by hand
    actually needs.

    Raises:
        ValueError: if a curated pair is unreachable, which would mean
            :data:`CURATED_PAIRS` has drifted from the state space.  Failing loudly beats
            silently generating 59 episodes and calling it 60.
    """
    by_pair = catalogue.by_pair()
    chosen: list[dict[str, Any]] = []
    for pair in CURATED_PAIRS:
        candidates = by_pair.get(pair)
        if not candidates:
            raise ValueError(
                f"curated pair {pair} is not reachable in the state space. CURATED_PAIRS has "
                "drifted from decision_tree/leaves_classified.json."
            )
        chosen.append(min(candidates, key=lambda leaf: (leaf["depth"], leaf["case_id"])))
        if len(chosen) == episode_count:
            break
    return chosen


def select_configurations(
    catalogue: LeafCatalogue,
    *,
    episode_count: int,
    master_seed: int,
    profile_name: str,
) -> list[dict[str, Any]]:
    """Pick ``episode_count`` configurations, covering every verdict pair first.

    The ordering of the result is deterministic and deliberately *not* grouped by pair:
    episodes are shuffled so that a remittance batch ends up containing a realistic mix of
    claims rather than 252 consecutive copies of the same verdict.

    Raises:
        ValueError: if ``episode_count`` is below the number of reachable pairs, because
            the coverage guarantee would be arithmetically impossible and silently
            producing 372-minus-something pairs is worse than refusing.
    """
    by_pair = catalogue.by_pair()
    pairs = sorted(by_pair)
    if episode_count < len(pairs):
        raise ValueError(
            f"episode_count={episode_count} cannot cover {len(pairs)} reachable verdict "
            "pairs. Coverage is an assertion, not an aspiration (Decision 48): raise the "
            "count or use the demo profile, which is curated rather than exhaustive."
        )

    rng = rng_for(master_seed, profile_name, "select")
    chosen: list[dict[str, Any]] = []

    # One per pair, first.  Within a pair, prefer the configuration with the fewest
    # dimensions set: the shortest path is the clearest worked example of that verdict,
    # which matters when someone traces one of these by hand.
    for pair in pairs:
        candidates = sorted(by_pair[pair], key=lambda leaf: (leaf["depth"], leaf["case_id"]))
        chosen.append(candidates[0])

    # Fill the remainder by walking the pairs round-robin, so the extra volume spreads
    # evenly instead of piling onto whichever pair happens to be dense.
    remaining = episode_count - len(chosen)
    if remaining > 0:
        cursors = {pair: 1 for pair in pairs}
        order = list(pairs)
        rng.shuffle(order)
        index = 0
        while remaining > 0:
            pair = order[index % len(order)]
            candidates = sorted(by_pair[pair], key=lambda leaf: (leaf["depth"], leaf["case_id"]))
            position = cursors[pair]
            if position < len(candidates):
                chosen.append(candidates[position])
                cursors[pair] = position + 1
                remaining -= 1
            index += 1
            # Every pair exhausted and still short: reuse configurations rather than
            # refuse, since a repeated configuration is a legitimate second episode.
            if index > len(order) * (max(len(v) for v in by_pair.values()) + 2):
                extra = stable_choice(rng, sorted(by_pair[pair], key=lambda l: l["case_id"]))
                chosen.append(extra)
                remaining -= 1

    rng.shuffle(chosen)
    return chosen


# ═══ entity binding ═════════════════════════════════════════════════════════

#: Which reference-data fact each disqualification reason requires to be true.  Resolving
#: the reason first and then binding entities to satisfy it is what keeps the feed
#: consistent with the reference universe.
DISQUALIFICATION_BINDINGS: dict[str, str] = {
    "NO_QUALIFYING_ENCOUNTER": "any",
    "PRESCRIBER_NOT_AFFILIATED": "unaffiliated_prescriber",
    "UNREGISTERED_LOCATION": "satellite_pharmacy",
    "MEDICAID_DUPLICATE_DISCOUNT": "any",
}


def _unaffiliated_prescribers() -> tuple[str, ...]:
    """Prescribers affiliated with neither covered entity, plus the decoy-only one.

    Both make ``PRESCRIBER_NOT_AFFILIATED`` true relative to *our* covered entity, which
    is the one doing the qualifying.
    """
    own = entities.own_covered_entity()
    return tuple(
        p.npi for p in entities.prescribers() if p.npi not in own.affiliated_prescriber_npis
    )


def _affiliated_prescribers() -> tuple[str, ...]:
    own = entities.own_covered_entity()
    return tuple(
        p.npi for p in entities.prescribers() if p.npi in own.affiliated_prescriber_npis
    )


def _restricting_ndcs(benefit: BenefitType) -> tuple[str, ...]:
    """NDCs whose manufacturer restricts contract pharmacies."""
    pool = drugs.pharmacy_benefit() if benefit is BenefitType.PHARMACY_BENEFIT else drugs.medical_benefit()
    return tuple(
        d.ndc11
        for d in pool
        if entities.manufacturer_for_ndc(d.ndc11).restricts_contract_pharmacy
    )


def bind_entities(
    leaf: dict[str, Any],
    *,
    sequence: int,
    master_seed: int,
    profile_name: str,
) -> dict[str, Any]:
    """Choose the entities, identifiers and defect directives for one episode.

    Deterministic per ``(master_seed, profile_name, sequence)``: the seed path includes
    the episode's sequence number and nothing about what any other episode chose, so
    inserting an episode does not reshuffle the dataset (see :mod:`recon.rng`).
    """
    configuration = {**leaf["reimbursement_track"], **leaf["rebate_track"]}
    rebate_verdict = leaf["curated_rebate_state"]
    is_pharmacy = configuration["reimb_type"] == "PHARMACY"
    benefit = BenefitType.PHARMACY_BENEFIT if is_pharmacy else BenefitType.MEDICAL_BENEFIT

    rng = rng_for(master_seed, profile_name, "bind", sequence)

    rebate_present = rebate_verdict != "C-00"
    qualification = configuration.get("r_qualification")
    manufacturer_status = configuration.get("r_manufacturer")

    # --- resolve the 340B reason BEFORE picking entities ------------------
    disqualification_reason: str | None = None
    rejection_reason: str | None = None
    if qualification == "NOT_QUALIFIED":
        disqualification_reason = stable_choice(rng, sorted(DISQUALIFICATION_BINDINGS))
    if manufacturer_status == "REJECTED":
        rejection_reason = stable_choice(
            rng, ["CONTRACT_PHARMACY_RESTRICTED", "NON_CONFORMING_45_DAY"]
        )

    # --- drug, constrained by the reason where the reason demands it ------
    if rebate_present and rejection_reason == "CONTRACT_PHARMACY_RESTRICTED":
        restricting = _restricting_ndcs(benefit)
        ndc11 = stable_choice(rng, restricting) if restricting else stable_choice(
            rng, [d.ndc11 for d in (drugs.pharmacy_benefit() if is_pharmacy else drugs.medical_benefit())]
        )
    else:
        pool = drugs.pharmacy_benefit() if is_pharmacy else drugs.medical_benefit()
        ndc11 = stable_choice(rng, [d.ndc11 for d in pool])
    drug = drugs.by_ndc(ndc11)

    # --- pharmacy site, constrained by UNREGISTERED_LOCATION --------------
    if disqualification_reason == "UNREGISTERED_LOCATION":
        site = entities.satellite_pharmacy()
    else:
        site = entities.pharmacy()

    # --- prescriber, constrained by PRESCRIBER_NOT_AFFILIATED -------------
    if disqualification_reason == "PRESCRIBER_NOT_AFFILIATED":
        candidates = _unaffiliated_prescribers()
    elif rebate_present:
        # A qualifying dispense needs an affiliated prescriber, or the TPA's own rules
        # would have disqualified it for a different reason than the one we intend.
        candidates = _affiliated_prescribers()
    else:
        candidates = tuple(p.npi for p in entities.prescribers())
    prescriber_npi = stable_choice(rng, candidates or tuple(p.npi for p in entities.prescribers()))

    # --- patient, on the right benefit ------------------------------------
    patient = stable_choice(rng, patients.for_benefit_type(benefit))

    # --- payer -------------------------------------------------------------
    if is_pharmacy:
        pbm = stable_choice(rng, entities.pbms())
        payer_id = pbm.pbm_id
    else:
        payer = stable_choice(rng, entities.medical_payers())
        payer_id = payer.payer_id

    quantity_units = _quantity_units(rng, drug)

    bindings: dict[str, Any] = {
        "ndc11": ndc11,
        "payer_id": payer_id,
        "prescriber_npi": prescriber_npi,
        "patient_id": patient.patient_id,
        "cardholder_id": patient.cardholder_id,
        "person_code": patient.person_code,
        "quantity_units": quantity_units,
        "disqualification_reason": disqualification_reason,
        "rejection_reason": rejection_reason,
    }

    if is_pharmacy:
        bindings.update(
            pharmacy_npi=site.npi,
            rx_number=_mint_rx_number(master_seed, profile_name, sequence),
            fill_number=f"{rng.randint(0, 3):02d}",
            pbm_id=payer_id,
        )
    else:
        provider = entities.billing_provider()
        bindings.update(
            clm01=_mint_clm01(master_seed, profile_name, sequence),
            billing_provider_npi=provider.npi,
            medical_payer_id=payer_id,
        )

    if rebate_present:
        own = entities.own_covered_entity()
        manufacturer = entities.manufacturer_for_ndc(ndc11)
        bindings.update(
            covered_entity_id=own.covered_entity_id,
            hin=own.hin,
            manufacturer_id=manufacturer.manufacturer_id,
            wholesaler_invoice_number=f"WI-{88_000_000 + sequence * 37:08d}",
        )

    return bindings


def _quantity_units(rng: random.Random, drug) -> int:
    """A plausible dispensed quantity for this drug's unit basis.

    Oral specialty is dispensed in 28/30/90-day packs; an infusion is a vial count turned
    into mL.  Both matter only in that they make the money realistic — a 30-capsule
    ibrutinib fill landing near $14,000 reads like a specialty claim, which is the point.
    """
    if drug.unit_basis.value == "EACH":
        return stable_choice(rng, (28, 30, 60, 90))
    vials = stable_choice(rng, (1, 2, 3, 4))
    per_vial = drug.ctp_units_per_billing_unit or 1
    return vials * per_vial


def _run_offset(master_seed: int, profile_name: str, namespace: str, span: int) -> int:
    """A per-run base for an identifier series, derived from the seed.

    Claim identifiers have to satisfy two things at once, and an earlier version satisfied only the
    first: **unique within a run**, and **different between runs with different seeds**.

    ``ENC-{88000 + sequence}-01`` and ``7_000_000 + sequence * 7`` were both functions of the
    sequence number alone, so every seed produced the same claim numbers. Two supposedly independent
    datasets shared claim identities — which would make any comparison between them quietly
    meaningless, and would be baffling to anyone who regenerated expecting fresh data.

    Offsetting the whole series by a seed-derived base fixes that without touching uniqueness: the
    within-run spacing still guarantees no two episodes collide.
    """
    return derive_seed(master_seed, profile_name, namespace) % span


def _mint_rx_number(master_seed: int, profile_name: str, sequence: int) -> str:
    """A pharmacy-assigned Rx number: numeric, <= 12 digits, unique per episode.

    Minted by the orchestrator rather than the PBM generator because it is a value that
    genuinely crosses systems — the pharmacy assigns it, and both the PBM and the TPA
    quote it back (Decision 44's crossing matrix).

    Spaced seven apart with up to six of jitter, so no two sequences can land on the same number
    however the jitter falls.
    """
    base = 100_000_000 + _run_offset(master_seed, profile_name, "rx-run", 800_000_000)
    rng = rng_for(master_seed, profile_name, "rx", sequence)
    return str(base + sequence * 7 + rng.randint(0, 6))


def _mint_clm01(master_seed: int, profile_name: str, sequence: int) -> str:
    """A provider-assigned patient control number: alphanumeric, <= 20 chars."""
    base = _run_offset(master_seed, profile_name, "clm01-run", 900_000)
    return f"ENC-{100_000 + base:06d}-{sequence:05d}"
