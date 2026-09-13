"""Pricing: the accessors the generator and the engine both import.

This is the module that makes underpayment a *deliberate delta* rather than a
coincidence.  Because Group 2 subtracts from the same expected figure Group 3 later
computes, a short payment in the dataset is exactly the short payment the engine
reports.  If the two sides each did their own arithmetic, every claim would carry a
rounding-shaped variance and nothing would be reconcilable.

**The boundary, stated so it is testable:** this module owns everything derivable from
*(drug, payer, quantity)* — list price, contracted rate, dispensing fee, and patient
responsibility, because in this model patient responsibility is a **plan parameter**
(a flat copay per PBM plan, a coinsurance rate per medical payer) and not a claim
fact.  Group 3's ``engine/expected.py`` owns everything derivable only from *the
claim*: the adjustments actually present on the 835, reversals, appeals, recoupments,
PLB offsets.

Reference never takes a claim, a record, a ``norm_id`` or an ``episode_id`` as an
argument, and a test asserts that by introspecting the signatures below.  That is the
testable form of the boundary.

Contract terms live in their own ``(payer_id, ndc11)`` table rather than on the drug
row.  Adding a third PBM adds rows there and changes no rule — which is what
Decision 33's scale claim looks like when it is true rather than aspirational.

All arithmetic is integer; all rounding goes through ``money.round_half_up``.
``quantity_milli`` is quantity x 1000 (NCPDP 442-E7's three implied decimals), so
``"030000"`` on the wire is ``30_000`` here and means thirty capsules.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType

from recon.domain.enums import BenefitType, ContractBasis
from recon.money import apply_bps, round_half_up
from recon.reference import entities
from recon.reference.drugs import by_ndc, medical_benefit, pharmacy_benefit
from recon.reference.errors import NoContractTermsError

__all__ = [
    "ContractTerms",
    "contract_terms",
    "all_contract_terms",
    "has_contract_terms",
    "charge_cents",
    "contracted_allowed_cents",
    "patient_responsibility_cents",
    "expected_reimbursement_cents",
    "expected_rebate_cents",
    "MILLI",
]

#: Quantities are carried as milli-units end to end.
MILLI = 1_000


@dataclass(frozen=True, slots=True)
class ContractTerms:
    payer_id: str
    ndc11: str
    basis: ContractBasis
    rate_bps: int  # discount off WAC, in basis points: 1200 == WAC - 12%
    dispensing_fee_cents: int  # pharmacy benefit only; zero on the medical side


# Per-payer defaults, then the handful of negotiated exceptions.  Both are data; the
# table below is materialised from them once, at import, and is the only thing any
# caller sees.
_PAYER_DEFAULTS: dict[str, tuple[int, int]] = {
    # payer_id: (rate_bps, dispensing_fee_cents)
    "PBM_MERIDIAN": (1_200, 175),
    "PBM_CASCADE": (1_350, 150),
    "PAY_BLUEHARBOR": (2_200, 0),
    "PAY_GRANITE": (2_500, 0),
}

_NEGOTIATED_OVERRIDES: dict[tuple[str, str], tuple[int, int]] = {
    ("PBM_MERIDIAN", "00074433902"): (1_000, 175),
    ("PBM_CASCADE", "00071015523"): (1_450, 150),
    ("PAY_BLUEHARBOR", "50242007923"): (1_800, 0),
    ("PAY_GRANITE", "00002754901"): (2_800, 0),
}


def _build_contract_table() -> MappingProxyType:
    table: dict[tuple[str, str], ContractTerms] = {}
    pairs = [
        (pbm.pbm_id, drug.ndc11)
        for pbm in entities.pbms()
        for drug in pharmacy_benefit()
    ] + [
        (payer.payer_id, drug.ndc11)
        for payer in entities.medical_payers()
        for drug in medical_benefit()
    ]
    for payer_id, ndc11 in pairs:
        rate_bps, fee = _NEGOTIATED_OVERRIDES.get((payer_id, ndc11), _PAYER_DEFAULTS[payer_id])
        table[(payer_id, ndc11)] = ContractTerms(
            payer_id=payer_id,
            ndc11=ndc11,
            basis=ContractBasis.WAC_MINUS_BPS,
            rate_bps=rate_bps,
            dispensing_fee_cents=fee,
        )
    return MappingProxyType(table)


_CONTRACT_TERMS = _build_contract_table()


def contract_terms(payer_id: str, ndc11: str) -> ContractTerms:
    try:
        return _CONTRACT_TERMS[(payer_id, ndc11)]
    except KeyError:
        raise NoContractTermsError(payer_id, ndc11) from None


def has_contract_terms(payer_id: str, ndc11: str) -> bool:
    return (payer_id, ndc11) in _CONTRACT_TERMS


def all_contract_terms() -> tuple[ContractTerms, ...]:
    return tuple(_CONTRACT_TERMS[key] for key in sorted(_CONTRACT_TERMS))


def charge_cents(ndc11: str, quantity_milli: int) -> int:
    """Billed charge: list price times quantity.

    This is what lands in ``clp03_total_charge`` on a pharmacy 835 and in
    ``svc02_charge_amount`` on a medical claim line.
    """
    drug = by_ndc(ndc11)
    return round_half_up(drug.wac_cents_per_unit * quantity_milli, MILLI)


def contracted_allowed_cents(ndc11: str, payer_id: str, quantity_milli: int) -> int:
    """What the contract says the claim is worth, before patient responsibility."""
    terms = contract_terms(payer_id, ndc11)
    allowed = apply_bps(charge_cents(ndc11, quantity_milli), 10_000 - terms.rate_bps)
    return allowed + terms.dispensing_fee_cents


def patient_responsibility_cents(payer_id: str, allowed_cents: int) -> int:
    """The patient's share of an allowed amount.

    Flat copay on the pharmacy benefit, proportional coinsurance on the medical
    benefit.  Capped at the allowed amount, because a copay larger than the claim
    would otherwise drive the payer's share negative.
    """
    if payer_id in {p.pbm_id for p in entities.pbms()}:
        share = entities.pbm_by_id(payer_id).copay_cents
    else:
        payer = entities.payer_by_id(payer_id)
        share = apply_bps(allowed_cents, payer.coinsurance_bps)
    return min(share, max(allowed_cents, 0))


def expected_reimbursement_cents(ndc11: str, payer_id: str, quantity_milli: int) -> int:
    """What the payer should pay us.  The figure every variance is measured against."""
    allowed = contracted_allowed_cents(ndc11, payer_id, quantity_milli)
    return allowed - patient_responsibility_cents(payer_id, allowed)


def expected_rebate_cents(ndc11: str, quantity_milli: int) -> int:
    """The 340B rebate: what we over-paid relative to the ceiling price.

    Never negative.  The drug table's ``ceiling < acquisition < wac`` invariant is what
    guarantees that for any positive quantity, and the ``max`` here is the belt to that
    invariant's braces at quantity zero.
    """
    drug = by_ndc(ndc11)
    spread = drug.acquisition_cost_cents_per_unit - drug.ceiling_340b_cents_per_unit
    return max(0, round_half_up(spread * quantity_milli, MILLI))


def _benefit_payers(benefit_type: BenefitType) -> tuple[str, ...]:
    """Which payer ids can legitimately be billed for a drug on this benefit."""
    if benefit_type is BenefitType.PHARMACY_BENEFIT:
        return tuple(p.pbm_id for p in entities.pbms())
    return tuple(p.payer_id for p in entities.medical_payers())
