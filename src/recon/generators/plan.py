"""The orchestrator's complete picture of one episode.

This is the only object in the system that knows everything at once: which configuration
the episode realises, which verdict pair that configuration classifies to, which entities
it binds, what every amount is, and when every record arrives.  Nothing below the
orchestrator ever sees it (Decision 11).

═══ Where the amounts come from, and why it matters ════════════════════════════════

Every expected figure is computed through :mod:`recon.reference.pricing` — the *same*
module the reconciliation engine will later import.  That is deliberate and it is the
difference between a testable dataset and a useless one: because the generator subtracts
its underpayment from the same expected figure the engine computes, a short payment in
the data is *exactly* the short payment the engine reports.  If the two sides each did
their own arithmetic, every claim would carry a rounding-shaped variance and nothing
would be reconcilable.

All money is integer cents (Decision C6).  A float round-trip through ``47218.40`` leaves
a residue indistinguishable from a real one-cent underpayment.

═══ How a configuration becomes money ═════════════════════════════════════════════

The decision tree gives abstract dimensions — ``ph_payment = PARTIAL`` — and says nothing
about amounts.  :class:`EpisodePlan` is where that becomes cents, once, so the four
generators and the ground-truth writer all quote the same numbers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

from recon.domain.enums import ReimbursementTrack
from recon.money import apply_bps
from recon.reference import drugs, pricing

__all__ = [
    "EpisodePlan",
    "RxRendering",
    "PHARMACY_NO_DISPENSE_STATES",
    "underpayment_shortfall_cents",
    "overpayment_excess_cents",
]


#: How one feed renders the Rx number for this episode.  Identifier drift is injected by
#: giving **exactly one** feed a non-canonical rendering (Decision 46) — never by letting
#: generators drift independently, which would make the defect unreproducible.
#:
#: The drift is *meant* to cause a crosswalk miss.  That miss is the D-6 exception, and
#: Decision A23 forbids the engine from normalising it away.
RxRendering = Literal["CANONICAL", "ZERO_PADDED", "TRUNCATED"]


#: Pharmacy states in which the drug never reached the patient.  Medical is deliberately
#: excluded: the drug is administered *before* billing, so a clearinghouse rejection does
#: not undo the dispense — which is why all 15 medical states are compatible with a live
#: 340B track and only these four pharmacy states are not.
PHARMACY_NO_DISPENSE_STATES = frozenset({"A-01", "A-10", "A-11", "A-16"})

#: Underpayment is 18% of the expected figure by default — comfortably above any rounding
#: artefact, so a test asserting "the engine found the underpayment" cannot pass by
#: accident.  A named constant rather than a literal, because tolerance-vs-variance is
#: exactly the kind of number Decision 33 says must be configuration.
_UNDERPAYMENT_BPS = 1_800
_OVERPAYMENT_BPS = 1_200
_PARTIAL_CASH_BPS = 6_000
_PARTIAL_REBATE_BPS = 5_500


def underpayment_shortfall_cents(expected_cents: int) -> int:
    """How far short a ``PARTIAL`` payment falls.  One definition, both sides."""
    return apply_bps(expected_cents, _UNDERPAYMENT_BPS)


def overpayment_excess_cents(expected_cents: int) -> int:
    return apply_bps(expected_cents, _OVERPAYMENT_BPS)


@dataclass(frozen=True, slots=True)
class EpisodePlan:
    """One episode, fully decided, before any record is formatted.

    Constructed by :meth:`build`, which is where a decision-tree configuration turns into
    entities, amounts and dates.
    """

    episode_id: str
    case_id: str
    #: The configuration, verbatim from ``leaves_classified.json``: the three dimension
    #: groups flattened into one mapping.  Carried whole so ground truth can record
    #: exactly which of the 4,224 configurations this episode realises.
    configuration: dict[str, str]
    reimbursement_verdict: str
    rebate_verdict: str
    coherence: str
    cross_track_flags: tuple[str, ...]

    track: ReimbursementTrack

    # --- identity ----------------------------------------------------------
    ndc11: str
    date_of_service: date
    quantity_milli: int
    prescriber_npi: str
    patient_id: str
    cardholder_id: str
    person_code: str

    #: Pharmacy identity.  ``None`` on a medical episode.
    pharmacy_npi: str | None
    rx_number: str | None
    fill_number: str | None
    pbm_id: str | None

    #: Medical identity.  ``None`` on a pharmacy episode.
    clm01: str | None
    billing_provider_npi: str | None
    medical_payer_id: str | None

    # --- 340B --------------------------------------------------------------
    is_340b_flagged: bool
    covered_entity_id: str | None
    hin: str | None
    manufacturer_id: str | None
    wholesaler_invoice_number: str | None

    # --- money, all integer cents -----------------------------------------
    charge_cents: int
    allowed_cents: int
    patient_responsibility_cents: int
    expected_reimbursement_cents: int
    #: What the payer actually says it paid.  Differs from expected exactly when the
    #: configuration says it should.
    paid_reimbursement_cents: int
    expected_rebate_cents: int
    paid_rebate_cents: int
    #: The clawback or reversal amount, when the configuration declares one.
    negative_reimbursement_cents: int
    negative_rebate_cents: int

    # --- defect directives ------------------------------------------------
    rx_rendering_pharmacy_835: RxRendering = "CANONICAL"
    rx_rendering_tpa: RxRendering = "CANONICAL"
    #: How **Craneware** spells the Rx where Verity spells it canonically.
    #:
    #: The vendor-layer twin of ``rx_rendering_tpa``, and the reason it has to exist here
    #: rather than in a mock: both vendor exports are formatted from one shared source
    #: object, so on their own they can never disagree — and two sources telling different
    #: stories about one dispense is the single failure a reconciliation engine exists to
    #: catch.  Until this field existed, the vendor layer could not produce one.
    #:
    #: Decided here for the same reason D-6's drift is decided here: a formatter that drifted
    #: on its own would make the defect unreproducible, and ``recon.mocks`` is forbidden from
    #: importing ``recon.generators`` at all.
    rx_rendering_craneware: RxRendering = "CANONICAL"
    late_arrival_days: int = 0
    duplicate_delivery: bool = False

    # --- derived ----------------------------------------------------------

    @property
    def payer_id(self) -> str:
        """The payer on the reimbursement track, whichever track that is."""
        resolved = self.pbm_id if self.track is ReimbursementTrack.PHARMACY else self.medical_payer_id
        assert resolved is not None, "every episode has exactly one reimbursement payer"
        return resolved

    @property
    def has_rebate_track(self) -> bool:
        return self.rebate_verdict != "C-00"

    @property
    def dispense_happened(self) -> bool:
        """Whether the drug actually reached the patient.

        Drives the X-2 compliance rule: a rebate standing on a dispense that never
        happened is not merely unmatched, it is *invalid*, and it has to be unwound
        proactively rather than discovered by the manufacturer later.
        """
        return self.reimbursement_verdict not in PHARMACY_NO_DISPENSE_STATES

    @property
    def reimbursement_variance_cents(self) -> int:
        return self.expected_reimbursement_cents - self.paid_reimbursement_cents

    @property
    def rebate_variance_cents(self) -> int:
        return self.expected_rebate_cents - self.paid_rebate_cents

    def rendered_rx(self, rendering: RxRendering) -> str | None:
        """The Rx number as one particular feed should spell it.

        ``ZERO_PADDED`` is the canonical drift case from ``feed_formats.md``:
        ``"07845102"`` against ``"7845102"``.  The two are *meant* not to match.
        """
        if self.rx_number is None:
            return None
        if rendering == "ZERO_PADDED":
            return self.rx_number.zfill(len(self.rx_number) + 1)
        if rendering == "TRUNCATED":
            return self.rx_number[:-1] if len(self.rx_number) > 1 else self.rx_number
        return self.rx_number

    # --- construction ------------------------------------------------------

    @classmethod
    def build(
        cls,
        *,
        episode_id: str,
        leaf: dict[str, Any],
        bindings: dict[str, Any],
        date_of_service: date,
        quantity_milli: int,
    ) -> "EpisodePlan":
        """Turn one classified decision-tree leaf into a fully-costed plan.

        ``bindings`` carries the entity choices the sampler made (drug, patient, payer,
        covered entity and so on); this method owns turning the configuration's abstract
        dimensions into cents.
        """
        configuration = {
            **leaf["reimbursement_track"],
            **leaf["rebate_track"],
            **leaf["cash_verification"],
        }
        track = ReimbursementTrack(configuration["reimb_type"])
        ndc11 = bindings["ndc11"]
        payer_id = bindings["payer_id"]

        charge = pricing.charge_cents(ndc11, quantity_milli)
        allowed = pricing.contracted_allowed_cents(ndc11, payer_id, quantity_milli)
        patient_resp = pricing.patient_responsibility_cents(payer_id, allowed)
        expected_reimbursement = pricing.expected_reimbursement_cents(
            ndc11, payer_id, quantity_milli
        )

        paid_reimbursement, negative_reimbursement = _reimbursement_money(
            configuration, track, expected_reimbursement
        )

        rebate_present = leaf["curated_rebate_state"] != "C-00"
        expected_rebate = (
            pricing.expected_rebate_cents(ndc11, quantity_milli) if rebate_present else 0
        )
        paid_rebate, negative_rebate = _rebate_money(configuration, expected_rebate)

        return cls(
            episode_id=episode_id,
            case_id=leaf["case_id"],
            configuration=configuration,
            reimbursement_verdict=leaf["curated_reimbursement_state"],
            rebate_verdict=leaf["curated_rebate_state"],
            coherence=leaf["coherence"],
            cross_track_flags=tuple(leaf.get("cross_track_flags", ())),
            track=track,
            ndc11=ndc11,
            date_of_service=date_of_service,
            quantity_milli=quantity_milli,
            prescriber_npi=bindings["prescriber_npi"],
            patient_id=bindings["patient_id"],
            cardholder_id=bindings["cardholder_id"],
            person_code=bindings["person_code"],
            pharmacy_npi=bindings.get("pharmacy_npi"),
            rx_number=bindings.get("rx_number"),
            fill_number=bindings.get("fill_number"),
            pbm_id=bindings.get("pbm_id"),
            clm01=bindings.get("clm01"),
            billing_provider_npi=bindings.get("billing_provider_npi"),
            medical_payer_id=bindings.get("medical_payer_id"),
            is_340b_flagged=rebate_present,
            covered_entity_id=bindings.get("covered_entity_id"),
            hin=bindings.get("hin"),
            manufacturer_id=bindings.get("manufacturer_id"),
            wholesaler_invoice_number=bindings.get("wholesaler_invoice_number"),
            charge_cents=charge,
            allowed_cents=allowed,
            patient_responsibility_cents=patient_resp,
            expected_reimbursement_cents=expected_reimbursement,
            paid_reimbursement_cents=paid_reimbursement,
            expected_rebate_cents=expected_rebate,
            paid_rebate_cents=paid_rebate,
            negative_reimbursement_cents=negative_reimbursement,
            negative_rebate_cents=negative_rebate,
            rx_rendering_pharmacy_835=bindings.get("rx_rendering_pharmacy_835", "CANONICAL"),
            rx_rendering_tpa=bindings.get("rx_rendering_tpa", "CANONICAL"),
            # **Defaults to the TPA feed's rendering, not to CANONICAL.** Craneware reports
            # what it was sent, so where the feed's Rx drifted Craneware's must drift with it.
            # Defaulting to CANONICAL made Craneware silently *repair* a D-6 truncation —
            # ``22105567`` in the feed and Verity, ``221055677`` in Craneware — which would
            # hand the connector a working join the real feed does not have and quietly
            # delete the defect through one vendor's door. Caught by measuring the diverged
            # pairs rather than by a test, because at that point no test looked.
            rx_rendering_craneware=bindings.get(
                "rx_rendering_craneware", bindings.get("rx_rendering_tpa", "CANONICAL")
            ),
            late_arrival_days=bindings.get("late_arrival_days", 0),
            duplicate_delivery=bindings.get("duplicate_delivery", False),
        )


def _reimbursement_money(
    configuration: dict[str, str], track: ReimbursementTrack, expected: int
) -> tuple[int, int]:
    """``(paid, negative)`` on the reimbursement track.

    ``negative`` is money flowing back out of us: a post-payment reversal the pharmacy
    initiated, or a recoupment the payer took.  Same numeric shape, different actor,
    different action — and the distinction is why A-10/A-11 and A-12/A-13 are four
    verdicts rather than two.
    """
    if track is ReimbursementTrack.PHARMACY:
        payment = configuration.get("ph_payment")
        post = configuration.get("ph_post_event")
        paid = {
            None: 0,
            "NONE": 0,
            "PARTIAL": expected - underpayment_shortfall_cents(expected),
            "FULL": expected,
            "OVER": expected + overpayment_excess_cents(expected),
            # Two payment events for one claim: the same amount, twice.
            "DUPLICATE": expected * 2,
        }[payment]
        if post == "REVERSAL_POST_PAY":
            negative = paid
        elif post == "RECOUPMENT":
            negative = paid
        else:
            negative = 0
        return paid, negative

    remittance = configuration.get("md_remittance")
    appeal = configuration.get("md_appeal")
    post = configuration.get("md_post_event")
    paid = {
        None: 0,
        "NONE": 0,
        "PAID_FULL": expected,
        "PARTIAL": expected - underpayment_shortfall_cents(expected),
        "DENIED": 0,
        "DUPLICATE_835": expected * 2,
    }[remittance]
    # A won appeal produces a corrected or supplementary 835 that restores the balance.
    if appeal == "WON":
        paid = expected
    negative = paid if post == "RECOUPMENT" else 0
    return paid, negative


def _rebate_money(configuration: dict[str, str], expected: int) -> tuple[int, int]:
    payment = configuration.get("r_payment")
    paid = {
        None: 0,
        "NONE": 0,
        "PARTIAL": expected - apply_bps(expected, _PARTIAL_REBATE_BPS),
        "FULL": expected,
        "DUPLICATE": expected * 2,
        # Paid, then reversed by the manufacturer.  The gross payment happened; the
        # clawback is the negative leg, and net rebate is zero.
        "CLAWED_BACK": expected,
    }[payment]
    negative = paid if payment == "CLAWED_BACK" else 0
    return paid, negative
