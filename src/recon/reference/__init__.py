"""Reference data: the fixed, synthetic universe everything else is drawn from.

Python literals behind an accessor layer.  Literals because they are typed,
greppable, need no parse step and cannot fail to load — which is what lets the
generator run with no database and no filesystem at all (Decision 36).  An accessor
layer anyway because Decision 33 says scale is rows rather than rules: when prices
become a configuration table, this package changes and no caller does.

Everything here is pure.  Zero I/O, zero randomness, zero database.

Sub-modules
-----------
``drugs``       twelve-drug formulary and the benefit-type partition
``entities``    pharmacies, provider, prescribers, PBMs, payers, covered entities, manufacturers
``patients``    forty tokenised patients
``pricing``     contract terms and the expected-amount accessors both G2 and G3 import
``codes``       wire codes to canonical semantics, and the PLB sign convention
``calendar``    banking days over the generation window
``sites``       contract-pharmacy sites, the identity below the NPI (E4)
``fingerprint`` SHA-256 of the whole universe, for drift detection

``sites`` is the one sub-module the generators never see.  It is read on the ingest and
connector side only, and :mod:`recon.reference.fingerprint` deliberately does not hash
it: the fingerprint's job is to catch a dataset generated against one universe being
scored against another, and a table no generator can reach cannot cause that drift.
"""

from __future__ import annotations

from recon.domain.enums import BenefitType
from recon.reference import calendar, codes, drugs, entities, patients, pricing, sites
from recon.reference.errors import (
    NoContractTermsError,
    ReferenceError,
    UnknownEntityError,
    UnknownNdcError,
)
from recon.reference.fingerprint import fingerprint

__all__ = [
    "drugs",
    "entities",
    "patients",
    "pricing",
    "codes",
    "calendar",
    "sites",
    "fingerprint",
    "validate",
    "ReferenceError",
    "UnknownNdcError",
    "UnknownEntityError",
    "NoContractTermsError",
]

_HRSA_PREFIXES = ("DSH", "CAH", "CAN", "PED", "RRC", "SCH", "CH", "FQHC", "HM", "RW")


class ReferenceDataError(AssertionError):
    """Raised by :func:`validate` when a reference-data invariant is broken."""


def validate() -> None:
    """Assert every invariant the rest of the system relies on.

    Called by a test, so bad reference data cannot reach a generation run.  Each
    failure names the offending row: a reference table is data, and a data error
    should read like one.
    """
    problems: list[str] = []

    def check(condition: bool, message: str) -> None:
        if not condition:
            problems.append(message)

    # --- drugs ------------------------------------------------------------
    all_drugs = drugs.all_drugs()
    check(len(all_drugs) == 12, f"expected 12 drugs, found {len(all_drugs)}")
    check(
        len(drugs.pharmacy_benefit()) + len(drugs.medical_benefit()) == len(all_drugs),
        "benefit types do not partition the formulary",
    )
    seen_ndcs: set[str] = set()
    for drug in all_drugs:
        tag = f"drug {drug.ndc11} ({drug.name})"
        check(drug.ndc11 not in seen_ndcs, f"{tag}: duplicate NDC")
        seen_ndcs.add(drug.ndc11)
        check(len(drug.ndc11) == 11 and drug.ndc11.isdigit(), f"{tag}: NDC must be 11 digits")
        check(
            drug.ceiling_340b_cents_per_unit
            < drug.acquisition_cost_cents_per_unit
            < drug.wac_cents_per_unit,
            f"{tag}: requires ceiling < acquisition < wac, got "
            f"{drug.ceiling_340b_cents_per_unit} / {drug.acquisition_cost_cents_per_unit} / "
            f"{drug.wac_cents_per_unit}",
        )
        if drug.benefit_type is BenefitType.MEDICAL_BENEFIT:
            check(drug.hcpcs_j_code is not None, f"{tag}: medical-benefit drug needs a J-code")
            check(
                drug.ctp_units_per_billing_unit is not None
                and drug.ctp_units_per_billing_unit > 1,
                f"{tag}: medical-benefit drug needs ctp_units_per_billing_unit > 1",
            )
        else:
            check(drug.hcpcs_j_code is None, f"{tag}: pharmacy-benefit drug must not carry a J-code")
            check(
                drug.ctp_units_per_billing_unit is None,
                f"{tag}: pharmacy-benefit drug must not carry a CTP ratio",
            )
        try:
            entities.manufacturer_for_ndc(drug.ndc11)
        except UnknownEntityError:
            check(False, f"{tag}: labeler code {drug.labeler_code} resolves to no manufacturer")

    j_codes = [d.hcpcs_j_code for d in drugs.medical_benefit()]
    check(len(set(j_codes)) == len(j_codes), "two medical drugs share a J-code")

    # --- entities ---------------------------------------------------------
    check(len(entities.pharmacies()) == 2, "expected 2 pharmacies")
    check(len(entities.prescribers()) == 6, "expected 6 prescribers")
    check(len(entities.pbms()) == 2, "expected 2 PBMs")
    check(len(entities.medical_payers()) == 2, "expected 2 medical payers")
    check(len(entities.covered_entities()) == 2, "expected 2 covered entities")
    check(len(entities.manufacturers()) == 6, "expected 6 manufacturers")

    npis = (
        [p.npi for p in entities.pharmacies()]
        + [entities.billing_provider().npi]
        + [p.npi for p in entities.prescribers()]
    )
    for npi in npis:
        check(entities.is_valid_npi(npi), f"NPI {npi} fails the NPPES check-digit test")
    check(len(set(npis)) == len(npis), "two entities share an NPI")

    for pbm in entities.pbms():
        tag = f"PBM {pbm.pbm_id}"
        check(len(pbm.bin) == 6 and pbm.bin.isdigit(), f"{tag}: BIN must be 6 digits")
        check(len(pbm.pcn) <= 10 and pbm.pcn.isalnum(), f"{tag}: PCN must be <=10 alphanumerics")
        check(
            pbm.originating_company_id != pbm.company_id,
            f"{tag}: 835 originating company id must differ from the bank company id "
            "(TRN02 is the only intended 835-to-bank link)",
        )
        check(
            len(pbm.ach_routing_prefix) == 8 and pbm.ach_routing_prefix.isdigit(),
            f"{tag}: ACH routing prefix must be 8 digits",
        )
        check(pbm.copay_cents > 0, f"{tag}: copay must be positive")

    for payer in entities.medical_payers():
        tag = f"payer {payer.payer_id}"
        check(len(payer.tin) == 9 and payer.tin.isdigit(), f"{tag}: TIN must be 9 digits")
        check(payer.trn03 == "1" + payer.tin, f"{tag}: TRN03 must render as '1' + TIN")
        check(
            payer.originating_company_id != payer.company_id,
            f"{tag}: 835 originating company id must differ from the bank company id",
        )
        check(
            len(payer.ach_routing_prefix) == 8 and payer.ach_routing_prefix.isdigit(),
            f"{tag}: ACH routing prefix must be 8 digits",
        )
        check(0 < payer.coinsurance_bps < 10_000, f"{tag}: coinsurance must be a fraction")

    own = [c for c in entities.covered_entities() if c.is_own_entity]
    check(len(own) == 1, "exactly one covered entity must be ours")
    for entity in entities.covered_entities():
        tag = f"covered entity {entity.covered_entity_id}"
        identifier = entity.covered_entity_id
        prefix = next((p for p in _HRSA_PREFIXES if identifier.startswith(p)), None)
        check(prefix is not None, f"{tag}: id does not start with an HRSA entity-type prefix")
        if prefix is not None:
            rest = identifier[len(prefix) :]
            check(
                rest[:-1].isdigit() and (rest[-1].isdigit() or rest[-1].isalpha()),
                f"{tag}: id must be prefix + digits + optional child-site letter",
            )
        check(
            len(entity.hin) == 9 and entity.hin.isalnum(),
            f"{tag}: HIN must be 9 alphanumeric characters",
        )

    own_entity = entities.own_covered_entity()
    check(
        entities.satellite_pharmacy().npi not in own_entity.registered_pharmacy_npis,
        "the satellite pharmacy must be unregistered, or UNREGISTERED_LOCATION is ungeneratable",
    )
    affiliated_anywhere: set[str] = set()
    for entity in entities.covered_entities():
        affiliated_anywhere |= entity.affiliated_prescriber_npis
    unaffiliated = [p for p in entities.prescribers() if p.npi not in affiliated_anywhere]
    check(
        len(unaffiliated) >= 1,
        "at least one prescriber must be affiliated with neither covered entity, "
        "or PRESCRIBER_NOT_AFFILIATED is ungeneratable",
    )
    check(
        any(m.restricts_contract_pharmacy for m in entities.manufacturers()),
        "at least one manufacturer must restrict contract pharmacies, "
        "or CONTRACT_PHARMACY_RESTRICTED is ungeneratable",
    )
    labelers = [labeler for m in entities.manufacturers() for labeler in m.labeler_codes]
    check(len(set(labelers)) == len(labelers), "two manufacturers claim the same labeler code")

    company_ids = (
        [p.company_id for p in entities.pbms()]
        + [p.company_id for p in entities.medical_payers()]
        + [m.company_id for m in entities.manufacturers()]
    )
    check(len(set(company_ids)) == len(company_ids), "two originators share a bank company id")

    # --- sites ------------------------------------------------------------
    all_sites = sites.sites()
    check(len(all_sites) >= 2, f"expected at least 2 contract-pharmacy sites, found {len(all_sites)}")
    seen_site_ids: set[str] = set()
    for site in all_sites:
        tag = f"site {site.site_id} ({site.name})"
        check(site.site_id not in seen_site_ids, f"{tag}: duplicate site id")
        seen_site_ids.add(site.site_id)
        try:
            entities.covered_entity_by_id(site.covered_entity_id)
        except UnknownEntityError:
            check(False, f"{tag}: covered entity {site.covered_entity_id} does not exist")
        try:
            entities.pharmacy_by_npi(site.npi)
        except UnknownEntityError:
            check(False, f"{tag}: NPI {site.npi} resolves to no pharmacy")

    npis_with_two_sites = [
        npi for npi in sorted({s.npi for s in all_sites}) if len(sites.sites_for_npi(npi)) > 1
    ]
    check(
        len(npis_with_two_sites) >= 1,
        "no NPI carries two sites, so E4's acceptance criterion — two contract pharmacies "
        "under one NPI-holding entity are distinguishable — is unmeetable",
    )
    npis_with_one_site = [
        npi for npi in sorted({s.npi for s in all_sites}) if len(sites.sites_for_npi(npi)) == 1
    ]
    check(
        len(npis_with_one_site) >= 1,
        "no NPI carries exactly one site, so the unambiguous branch of resolve_site is untestable",
    )
    own_entity_id = entities.own_covered_entity().covered_entity_id
    satellite_sites = sites.sites_for_npi(entities.satellite_pharmacy().npi)
    offending = [s.site_id for s in satellite_sites if s.covered_entity_id == own_entity_id]
    check(
        not offending,
        f"site(s) {offending} put the satellite NPI under our own covered entity, which asserts "
        "the registration UNREGISTERED_LOCATION needs absent",
    )

    # --- patients ---------------------------------------------------------
    everyone = patients.all_patients()
    check(len(everyone) == 40, f"expected 40 patients, found {len(everyone)}")
    cardholder_ids = [p.cardholder_id for p in everyone]
    check(len(set(cardholder_ids)) == len(cardholder_ids), "duplicate cardholder id")
    for patient in everyone:
        tag = f"patient {patient.patient_id}"
        check(
            len(patient.cardholder_id) <= 20 and patient.cardholder_id.isalnum(),
            f"{tag}: cardholder id must be <=20 alphanumerics",
        )
        check(patient.person_code.isdigit(), f"{tag}: person code must be numeric")
    for benefit in BenefitType:
        check(
            len(patients.for_benefit_type(benefit)) > 0,
            f"no patients on the {benefit} benefit",
        )

    # --- contract terms ---------------------------------------------------
    for pbm in entities.pbms():
        for drug in drugs.pharmacy_benefit():
            check(
                pricing.has_contract_terms(pbm.pbm_id, drug.ndc11),
                f"no contract terms for {pbm.pbm_id} x {drug.ndc11}",
            )
    for payer in entities.medical_payers():
        for drug in drugs.medical_benefit():
            check(
                pricing.has_contract_terms(payer.payer_id, drug.ndc11),
                f"no contract terms for {payer.payer_id} x {drug.ndc11}",
            )
    for terms in pricing.all_contract_terms():
        check(
            0 < terms.rate_bps < 10_000,
            f"contract terms {terms.payer_id} x {terms.ndc11}: rate_bps out of range",
        )
        check(
            terms.dispensing_fee_cents >= 0,
            f"contract terms {terms.payer_id} x {terms.ndc11}: negative dispensing fee",
        )

    if problems:
        raise ReferenceDataError(
            "reference data is invalid:\n  - " + "\n  - ".join(problems)
        )
