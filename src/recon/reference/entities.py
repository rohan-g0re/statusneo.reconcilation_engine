"""The fixed entity universe (Decision 45).

Two pharmacies, one billing provider, six prescribers, two PBMs, two medical payers,
two covered entities, six manufacturers.  It is the smallest universe in which every
defect and every disqualification reason is *generatable*:

* the satellite pharmacy is not registered with our covered entity, so
  ``UNREGISTERED_LOCATION`` has something to point at;
* one prescriber is affiliated with neither covered entity and one is affiliated only
  with the decoy, so ``PRESCRIBER_NOT_AFFILIATED`` is reachable two ways;
* two manufacturers restrict contract pharmacies, so
  ``CONTRACT_PHARMACY_RESTRICTED`` is reachable;
* two PBMs with two different reject-code vocabularies demonstrate the claim that
  scale adds configuration rows, not rules (Decision 33).

**Every name is fictional** (Decision 38).  The assignment's confidentiality clause
binds the generated data; realism here comes from the *shape* of the identifiers —
16-character NACHA truncation, six-digit BINs, Luhn-valid NPIs — not from anyone's
trademark.

Payers and manufacturers carry **two** company identifiers, deliberately different:

* ``originating_company_id`` is what appears in the 835 ``TRN03`` segment;
* ``company_id`` is what appears in the bank CSV ``company_id`` column.

In reality these are issued by different systems and do not agree, which is exactly
why ``TRN02`` is the only intended 835-to-bank link.  A connector that "helpfully"
matches on company id is matching on a coincidence, and here it will not even find
one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType

from recon.reference.drugs import by_ndc
from recon.reference.errors import UnknownEntityError

__all__ = [
    "Pharmacy",
    "BillingProvider",
    "Prescriber",
    "PbmPlan",
    "MedicalPayer",
    "CoveredEntity",
    "Manufacturer",
    "pharmacies",
    "pharmacy",
    "pharmacy_by_npi",
    "satellite_pharmacy",
    "billing_provider",
    "prescribers",
    "prescriber_by_npi",
    "pbms",
    "pbm_by_id",
    "pbm_by_bin",
    "pbm_plan",
    "medical_payers",
    "payer_by_id",
    "covered_entities",
    "own_covered_entity",
    "covered_entity_by_id",
    "manufacturers",
    "manufacturer_by_id",
    "manufacturer_for_ndc",
    "all_payer_ids",
    "npi_check_digit",
    "is_valid_npi",
]


# --- identifiers -----------------------------------------------------------


def npi_check_digit(first_nine: str) -> str:
    """NPPES check digit: Luhn over ``"80840" + first_nine``.

    Cheap, and it is the difference between "ten random digits" and an identifier a
    reviewer recognises as realistically formatted.
    """
    if len(first_nine) != 9 or not first_nine.isdigit():
        raise ValueError(f"expected nine digits, got {first_nine!r}")
    total = 0
    for index, char in enumerate(reversed("80840" + first_nine)):
        digit = int(char)
        if index % 2 == 0:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return str((10 - total % 10) % 10)


def is_valid_npi(npi: str) -> bool:
    """Ten digits, a leading ``1`` or ``2``, and a correct NPPES check digit."""
    return (
        len(npi) == 10
        and npi.isdigit()
        and npi[0] in "12"
        and npi_check_digit(npi[:9]) == npi[9]
    )


# --- dataclasses -----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Pharmacy:
    pharmacy_id: str
    npi: str
    name: str
    is_primary: bool


@dataclass(frozen=True, slots=True)
class BillingProvider:
    provider_id: str
    npi: str
    name: str
    tin: str


@dataclass(frozen=True, slots=True)
class Prescriber:
    prescriber_id: str
    npi: str
    name: str
    specialty: str


@dataclass(frozen=True, slots=True)
class PbmPlan:
    """A PBM and the single plan we bill it under.

    ``copay_cents`` is flat per plan: in this model patient responsibility on the
    pharmacy benefit is a *plan parameter*, not a claim fact, which is why it lives
    in reference data rather than being read off the adjudication response.
    """

    pbm_id: str
    name: str
    bin: str
    pcn: str
    group_id: str
    payer_name: str
    originating_company_id: str
    company_id: str
    ach_routing_prefix: str
    copay_cents: int
    reject_vocabulary: str


@dataclass(frozen=True, slots=True)
class MedicalPayer:
    """``coinsurance_bps`` is the medical analogue of the PBM's flat copay: patient
    responsibility is proportional to the allowed amount and set by the plan, and it
    renders on the 835 as a ``PR-2`` adjustment."""

    payer_id: str
    name: str
    tin: str
    originating_company_id: str
    company_id: str
    ach_routing_prefix: str
    coinsurance_bps: int
    carc_vocabulary: str

    @property
    def trn03(self) -> str:
        """835 ``TRN03`` renders as ``"1" + TIN``."""
        return "1" + self.tin


@dataclass(frozen=True, slots=True)
class CoveredEntity:
    covered_entity_id: str
    name: str
    hin: str
    is_own_entity: bool
    affiliated_prescriber_npis: frozenset[str] = field(default_factory=frozenset)
    registered_pharmacy_npis: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True, slots=True)
class Manufacturer:
    manufacturer_id: str
    name: str
    short_name: str
    labeler_codes: frozenset[str]
    restricts_contract_pharmacy: bool
    company_id: str
    ach_routing_prefix: str


# --- the universe ----------------------------------------------------------

_PHARMACIES: tuple[Pharmacy, ...] = (
    Pharmacy("PH_MAIN", "1234567893", "HARBORVIEW SPECIALTY PHARMACY", True),
    # Registered with neither covered entity.  Its existence is what makes
    # UNREGISTERED_LOCATION a state the generator can actually produce.
    Pharmacy("PH_SAT", "1044723199", "HARBORVIEW NORTHSIDE SATELLITE", False),
)

_BILLING_PROVIDER = BillingProvider(
    "BP_MAIN", "1493387025", "HARBORVIEW INFUSION CENTER", "846100273"
)

_PRESCRIBERS: tuple[Prescriber, ...] = (
    Prescriber("PR_01", "1932268406", "MAYA ELLSWORTH MD", "MEDICAL ONCOLOGY"),
    Prescriber("PR_02", "1805591736", "RAFAEL SANTINO MD", "HEMATOLOGY"),
    Prescriber("PR_03", "1720044662", "PRIYA NARAYAN MD", "RHEUMATOLOGY"),
    Prescriber("PR_04", "1638812903", "THOMAS KEARNEY MD", "NEUROLOGY"),
    # Affiliated with the decoy covered entity only.
    Prescriber("PR_05", "1550274181", "INES VARGAS MD", "MEDICAL ONCOLOGY"),
    # Affiliated with neither.
    Prescriber("PR_06", "1379055241", "GORDON PYLE MD", "INTERNAL MEDICINE"),
)

_PBMS: tuple[PbmPlan, ...] = (
    PbmPlan(
        pbm_id="PBM_MERIDIAN",
        name="MERIDIANRX",
        bin="604211",
        pcn="SPECRX",
        group_id="RXGRP0042",
        payer_name="MERIDIANRX",
        originating_company_id="1043251982",
        company_id="1911234567",
        ach_routing_prefix="07100030",
        copay_cents=5_000,
        reject_vocabulary="NCPDP_NUMERIC",
    ),
    PbmPlan(
        pbm_id="PBM_CASCADE",
        name="CASCADERX",
        bin="610591",
        pcn="CASCSPC",
        group_id="RXGRP0117",
        payer_name="CASCADERX",
        originating_company_id="1055810433",
        company_id="1944021775",
        ach_routing_prefix="04400003",
        copay_cents=4_500,
        reject_vocabulary="CASCADE_ALPHA",
    ),
)

_PAYERS: tuple[MedicalPayer, ...] = (
    MedicalPayer(
        payer_id="PAY_BLUEHARBOR",
        name="BLUE HARBOR HEALTH",
        tin="954002211",
        originating_company_id="1067110245",
        company_id="1622109834",
        ach_routing_prefix="07300019",
        coinsurance_bps=2_000,
        carc_vocabulary="STANDARD",
    ),
    MedicalPayer(
        payer_id="PAY_GRANITE",
        name="GRANITE PEAK HEALTH",
        tin="870114523",
        originating_company_id="1072330918",
        company_id="1655048219",
        ach_routing_prefix="02100002",
        coinsurance_bps=1_500,
        carc_vocabulary="STANDARD",
    ),
)

_COVERED_ENTITIES: tuple[CoveredEntity, ...] = (
    CoveredEntity(
        covered_entity_id="DSH310074",
        name="HARBORVIEW REGIONAL MEDICAL CENTER",
        hin="HN3021998",
        is_own_entity=True,
        affiliated_prescriber_npis=frozenset(
            {"1932268406", "1805591736", "1720044662", "1638812903"}
        ),
        registered_pharmacy_npis=frozenset({"1234567893"}),
    ),
    CoveredEntity(
        covered_entity_id="PED045210A",
        name="CEDAR GROVE CHILDRENS HOSPITAL",
        hin="HN4471029",
        is_own_entity=False,
        affiliated_prescriber_npis=frozenset({"1550274181"}),
        registered_pharmacy_npis=frozenset(),
    ),
)

_MANUFACTURERS: tuple[Manufacturer, ...] = (
    Manufacturer(
        "MFR_VERION", "VERION PHARMA", "VERION", frozenset({"00071"}), False,
        "1837765021", "07100030",
    ),
    Manufacturer(
        "MFR_ALDEBARAN", "ALDEBARAN THERAPEUTICS", "ALDEBARAN", frozenset({"00002"}), False,
        "1810442309", "02600001",
    ),
    Manufacturer(
        "MFR_CORVANE", "CORVANE BIOSCIENCES", "CORVANE", frozenset({"00074"}), False,
        "1866013774", "12100002",
    ),
    # Restricts contract pharmacies -- CONTRACT_PHARMACY_RESTRICTED needs a source.
    Manufacturer(
        "MFR_TALVEX", "TALVEX LABS", "TALVEX", frozenset({"50242"}), True,
        "1829930155", "06100001",
    ),
    Manufacturer(
        "MFR_SAGEPOINT", "SAGEPOINT BIO", "SAGEPOINT", frozenset({"00078"}), False,
        "1877204618", "11100002",
    ),
    Manufacturer(
        "MFR_HALCYON", "HALCYON BIOLOGICS", "HALCYON", frozenset({"59676"}), True,
        "1843370892", "05300007",
    ),
)

_PHARMACY_BY_NPI = MappingProxyType({p.npi: p for p in _PHARMACIES})
_PRESCRIBER_BY_NPI = MappingProxyType({p.npi: p for p in _PRESCRIBERS})
_PBM_BY_ID = MappingProxyType({p.pbm_id: p for p in _PBMS})
_PBM_BY_BIN = MappingProxyType({p.bin: p for p in _PBMS})
_PAYER_BY_ID = MappingProxyType({p.payer_id: p for p in _PAYERS})
_CE_BY_ID = MappingProxyType({c.covered_entity_id: c for c in _COVERED_ENTITIES})
_MFR_BY_ID = MappingProxyType({m.manufacturer_id: m for m in _MANUFACTURERS})
_MFR_BY_LABELER = MappingProxyType(
    {labeler: m for m in _MANUFACTURERS for labeler in m.labeler_codes}
)


# --- accessors -------------------------------------------------------------


def pharmacies() -> tuple[Pharmacy, ...]:
    return _PHARMACIES


def pharmacy() -> Pharmacy:
    """The operator's main dispensing site."""
    return _PHARMACIES[0]


def satellite_pharmacy() -> Pharmacy:
    """The site not registered with any covered entity."""
    return _PHARMACIES[1]


def pharmacy_by_npi(npi: str) -> Pharmacy:
    try:
        return _PHARMACY_BY_NPI[npi]
    except KeyError:
        raise UnknownEntityError("pharmacy", npi) from None


def billing_provider() -> BillingProvider:
    return _BILLING_PROVIDER


def prescribers() -> tuple[Prescriber, ...]:
    return _PRESCRIBERS


def prescriber_by_npi(npi: str) -> Prescriber:
    try:
        return _PRESCRIBER_BY_NPI[npi]
    except KeyError:
        raise UnknownEntityError("prescriber", npi) from None


def pbms() -> tuple[PbmPlan, ...]:
    return _PBMS


def pbm_by_id(pbm_id: str) -> PbmPlan:
    try:
        return _PBM_BY_ID[pbm_id]
    except KeyError:
        raise UnknownEntityError("PBM", pbm_id) from None


def pbm_plan(pbm_id: str) -> PbmPlan:
    """Alias for :func:`pbm_by_id`, named for the pricing side of the boundary."""
    return pbm_by_id(pbm_id)


def pbm_by_bin(bin_number: str) -> PbmPlan:
    try:
        return _PBM_BY_BIN[bin_number]
    except KeyError:
        raise UnknownEntityError("PBM BIN", bin_number) from None


def medical_payers() -> tuple[MedicalPayer, ...]:
    return _PAYERS


def payer_by_id(payer_id: str) -> MedicalPayer:
    try:
        return _PAYER_BY_ID[payer_id]
    except KeyError:
        raise UnknownEntityError("medical payer", payer_id) from None


def covered_entities() -> tuple[CoveredEntity, ...]:
    return _COVERED_ENTITIES


def own_covered_entity() -> CoveredEntity:
    return _COVERED_ENTITIES[0]


def covered_entity_by_id(covered_entity_id: str) -> CoveredEntity:
    try:
        return _CE_BY_ID[covered_entity_id]
    except KeyError:
        raise UnknownEntityError("covered entity", covered_entity_id) from None


def manufacturers() -> tuple[Manufacturer, ...]:
    return _MANUFACTURERS


def manufacturer_by_id(manufacturer_id: str) -> Manufacturer:
    try:
        return _MFR_BY_ID[manufacturer_id]
    except KeyError:
        raise UnknownEntityError("manufacturer", manufacturer_id) from None


def manufacturer_for_ndc(ndc11: str) -> Manufacturer:
    """Resolve a drug to its manufacturer through the NDC labeler code."""
    labeler = by_ndc(ndc11).labeler_code
    try:
        return _MFR_BY_LABELER[labeler]
    except KeyError:
        raise UnknownEntityError("manufacturer for labeler code", labeler) from None


def all_payer_ids() -> tuple[str, ...]:
    """Every id that can appear as ``payer_id`` in the contract-terms table."""
    return tuple(p.pbm_id for p in _PBMS) + tuple(p.payer_id for p in _PAYERS)
