"""The twelve-drug formulary.

Seven pharmacy-benefit drugs (oral or self-administered) and five medical-benefit
drugs (infused, J-coded).  ``benefit_type`` is the partition that drives the episode
XOR: a drug travels exactly one road, so a single dispense cannot legitimately appear
on both a pharmacy claim and a medical claim.

Generic drug names and HCPCS J-codes are real — they are public clinical reference
data.  **Every price here is invented** and bears no relation to any real contract
(Decision 38).

Three fields exist for reasons that are not obvious:

``ceiling_340b_cents_per_unit`` sits below ``acquisition_cost_cents_per_unit``, which
sits below ``wac_cents_per_unit``.  That ordering is the 340B economics: the rebate is
what the pharmacy over-paid relative to the ceiling.  If it ever inverts,
``expected_rebate_cents`` goes negative and the whole rebate track produces nonsense,
so :func:`recon.reference.validate` asserts it.

``ctp_units_per_billing_unit`` exists so the SVC05-versus-CTP04 mismatch is produced
*consistently*.  On a medical claim the same vial is described twice on different
bases — J-code billing units in SVC05, drug product quantity in CTP04 — and the two
are legitimately different numbers.  Putting the ratio in reference data means the
generator emits a coherent pair and the engine knows the pair is legitimate, rather
than the generator emitting a random mismatch that the engine flags as a defect.

``unit_basis`` names what one dispensing unit is, and all prices are quoted per that
unit.  Quantities are carried as **milli-units** (quantity x 1000) end to end,
matching NCPDP field 442-E7's three implied decimals, so that the implied-decimal
format is parsed once at the adapter boundary and never re-derived.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType

from recon.domain.enums import BenefitType, UnitBasis
from recon.reference.errors import UnknownNdcError

__all__ = [
    "Drug",
    "all_drugs",
    "by_ndc",
    "pharmacy_benefit",
    "medical_benefit",
    "by_j_code",
    "ndc_exists",
]


@dataclass(frozen=True, slots=True)
class Drug:
    ndc11: str
    name: str
    strength: str
    dosage_form: str
    benefit_type: BenefitType
    hcpcs_j_code: str | None
    unit_basis: UnitBasis
    wac_cents_per_unit: int
    acquisition_cost_cents_per_unit: int
    ceiling_340b_cents_per_unit: int
    ctp_units_per_billing_unit: int | None

    @property
    def labeler_code(self) -> str:
        """First five digits of the NDC; joins to the manufacturer table.

        Derived rather than stored so it cannot drift from the NDC it describes.
        """
        return self.ndc11[:5]


_DRUGS: tuple[Drug, ...] = (
    # --- pharmacy benefit: oral / self-administered, dispensed against a prescription
    Drug(
        ndc11="00071015523",
        name="IBRUTINIB",
        strength="140 MG",
        dosage_form="CAPSULE",
        benefit_type=BenefitType.PHARMACY_BENEFIT,
        hcpcs_j_code=None,
        unit_basis=UnitBasis.EACH,
        wac_cents_per_unit=47_250,
        acquisition_cost_cents_per_unit=40_125,
        ceiling_340b_cents_per_unit=28_350,
        ctp_units_per_billing_unit=None,
    ),
    Drug(
        ndc11="00002143380",
        name="DULAGLUTIDE",
        strength="1.5 MG/0.5 ML",
        dosage_form="PEN INJECTOR",
        benefit_type=BenefitType.PHARMACY_BENEFIT,
        hcpcs_j_code=None,
        unit_basis=UnitBasis.EACH,
        wac_cents_per_unit=23_980,
        acquisition_cost_cents_per_unit=20_380,
        ceiling_340b_cents_per_unit=14_390,
        ctp_units_per_billing_unit=None,
    ),
    Drug(
        ndc11="00074433902",
        name="LENALIDOMIDE",
        strength="25 MG",
        dosage_form="CAPSULE",
        benefit_type=BenefitType.PHARMACY_BENEFIT,
        hcpcs_j_code=None,
        unit_basis=UnitBasis.EACH,
        wac_cents_per_unit=92_640,
        acquisition_cost_cents_per_unit=78_745,
        ceiling_340b_cents_per_unit=55_585,
        ctp_units_per_billing_unit=None,
    ),
    Drug(
        ndc11="00078052515",
        name="NILOTINIB",
        strength="200 MG",
        dosage_form="CAPSULE",
        benefit_type=BenefitType.PHARMACY_BENEFIT,
        hcpcs_j_code=None,
        unit_basis=UnitBasis.EACH,
        wac_cents_per_unit=18_420,
        acquisition_cost_cents_per_unit=15_660,
        ceiling_340b_cents_per_unit=11_050,
        ctp_units_per_billing_unit=None,
    ),
    Drug(
        ndc11="59676022530",
        name="ABIRATERONE ACETATE",
        strength="250 MG",
        dosage_form="TABLET",
        benefit_type=BenefitType.PHARMACY_BENEFIT,
        hcpcs_j_code=None,
        unit_basis=UnitBasis.EACH,
        wac_cents_per_unit=12_875,
        acquisition_cost_cents_per_unit=10_940,
        ceiling_340b_cents_per_unit=7_725,
        ctp_units_per_billing_unit=None,
    ),
    Drug(
        ndc11="00071022030",
        name="PALBOCICLIB",
        strength="125 MG",
        dosage_form="CAPSULE",
        benefit_type=BenefitType.PHARMACY_BENEFIT,
        hcpcs_j_code=None,
        unit_basis=UnitBasis.EACH,
        wac_cents_per_unit=51_330,
        acquisition_cost_cents_per_unit=43_630,
        ceiling_340b_cents_per_unit=30_800,
        ctp_units_per_billing_unit=None,
    ),
    Drug(
        ndc11="00002432280",
        name="TOFACITINIB",
        strength="11 MG",
        dosage_form="TABLET, EXTENDED RELEASE",
        benefit_type=BenefitType.PHARMACY_BENEFIT,
        hcpcs_j_code=None,
        unit_basis=UnitBasis.EACH,
        wac_cents_per_unit=27_640,
        acquisition_cost_cents_per_unit=23_495,
        ceiling_340b_cents_per_unit=16_585,
        ctp_units_per_billing_unit=None,
    ),
    # --- medical benefit: infused in a clinic, billed on an 837, no prescription
    Drug(
        ndc11="50242007923",
        name="BEVACIZUMAB",
        strength="400 MG/16 ML",
        dosage_form="INJECTION",
        benefit_type=BenefitType.MEDICAL_BENEFIT,
        hcpcs_j_code="J9035",
        unit_basis=UnitBasis.ML,
        wac_cents_per_unit=15_000,
        acquisition_cost_cents_per_unit=12_750,
        ceiling_340b_cents_per_unit=9_000,
        ctp_units_per_billing_unit=100,
    ),
    Drug(
        ndc11="00078060795",
        name="OCRELIZUMAB",
        strength="300 MG/10 ML",
        dosage_form="INJECTION",
        benefit_type=BenefitType.MEDICAL_BENEFIT,
        hcpcs_j_code="J2350",
        unit_basis=UnitBasis.ML,
        wac_cents_per_unit=135_000,
        acquisition_cost_cents_per_unit=114_750,
        ceiling_340b_cents_per_unit=81_000,
        ctp_units_per_billing_unit=25,
    ),
    Drug(
        ndc11="59676031001",
        name="INFLIXIMAB",
        strength="100 MG/10 ML",
        dosage_form="INJECTION",
        benefit_type=BenefitType.MEDICAL_BENEFIT,
        hcpcs_j_code="J1745",
        unit_basis=UnitBasis.ML,
        wac_cents_per_unit=12_000,
        acquisition_cost_cents_per_unit=10_200,
        ceiling_340b_cents_per_unit=7_200,
        ctp_units_per_billing_unit=10,
    ),
    Drug(
        ndc11="00074433851",
        name="RITUXIMAB",
        strength="500 MG/50 ML",
        dosage_form="INJECTION",
        benefit_type=BenefitType.MEDICAL_BENEFIT,
        hcpcs_j_code="J9312",
        unit_basis=UnitBasis.ML,
        wac_cents_per_unit=9_600,
        acquisition_cost_cents_per_unit=8_160,
        ceiling_340b_cents_per_unit=5_760,
        ctp_units_per_billing_unit=50,
    ),
    Drug(
        ndc11="00002754901",
        name="PEMBROLIZUMAB",
        strength="100 MG/4 ML",
        dosage_form="INJECTION",
        benefit_type=BenefitType.MEDICAL_BENEFIT,
        hcpcs_j_code="J9271",
        unit_basis=UnitBasis.ML,
        wac_cents_per_unit=132_500,
        acquisition_cost_cents_per_unit=112_625,
        ceiling_340b_cents_per_unit=79_500,
        ctp_units_per_billing_unit=20,
    ),
)

_BY_NDC = MappingProxyType({drug.ndc11: drug for drug in _DRUGS})
_BY_J_CODE = MappingProxyType(
    {drug.hcpcs_j_code: drug for drug in _DRUGS if drug.hcpcs_j_code is not None}
)


def all_drugs() -> tuple[Drug, ...]:
    return _DRUGS


def by_ndc(ndc11: str) -> Drug:
    try:
        return _BY_NDC[ndc11]
    except KeyError:
        raise UnknownNdcError(ndc11) from None


def ndc_exists(ndc11: str) -> bool:
    return ndc11 in _BY_NDC


def pharmacy_benefit() -> tuple[Drug, ...]:
    return tuple(d for d in _DRUGS if d.benefit_type is BenefitType.PHARMACY_BENEFIT)


def medical_benefit() -> tuple[Drug, ...]:
    return tuple(d for d in _DRUGS if d.benefit_type is BenefitType.MEDICAL_BENEFIT)


def by_j_code(hcpcs_j_code: str) -> Drug:
    try:
        return _BY_J_CODE[hcpcs_j_code]
    except KeyError:
        raise UnknownNdcError(hcpcs_j_code) from None
