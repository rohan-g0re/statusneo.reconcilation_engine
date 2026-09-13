"""A SHA-256 over the entire reference universe.

Written into the generation manifest, stamped into the database ``meta`` table, and
carried on every verdict row.

The point is drift detection.  A verdict computed against one price table and
compared with a dataset generated under another is a genuinely confusing bug — the
numbers are all plausible and all slightly wrong.  With a fingerprint on both sides it
is a one-line assertion instead of an afternoon.

Serialization is canonical: sorted keys, fixed separators, every value rendered as
text.  Adding a field to a reference dataclass changes the fingerprint, which is
correct — the reference data *did* change.
"""

from __future__ import annotations

import hashlib
import json

from recon.reference import calendar, codes, drugs, entities, patients, pricing

__all__ = ["fingerprint", "canonical_reference_document"]


def canonical_reference_document() -> dict:
    """Every reference table, as plain JSON-able data, in a fixed order."""
    return {
        "drugs": [
            {
                "ndc11": d.ndc11,
                "name": d.name,
                "strength": d.strength,
                "dosage_form": d.dosage_form,
                "benefit_type": str(d.benefit_type),
                "hcpcs_j_code": d.hcpcs_j_code,
                "unit_basis": str(d.unit_basis),
                "wac_cents_per_unit": d.wac_cents_per_unit,
                "acquisition_cost_cents_per_unit": d.acquisition_cost_cents_per_unit,
                "ceiling_340b_cents_per_unit": d.ceiling_340b_cents_per_unit,
                "ctp_units_per_billing_unit": d.ctp_units_per_billing_unit,
            }
            for d in sorted(drugs.all_drugs(), key=lambda d: d.ndc11)
        ],
        "contract_terms": [
            {
                "payer_id": t.payer_id,
                "ndc11": t.ndc11,
                "basis": str(t.basis),
                "rate_bps": t.rate_bps,
                "dispensing_fee_cents": t.dispensing_fee_cents,
            }
            for t in pricing.all_contract_terms()
        ],
        "pharmacies": [
            {"pharmacy_id": p.pharmacy_id, "npi": p.npi, "name": p.name, "is_primary": p.is_primary}
            for p in entities.pharmacies()
        ],
        "billing_provider": {
            "provider_id": entities.billing_provider().provider_id,
            "npi": entities.billing_provider().npi,
            "name": entities.billing_provider().name,
            "tin": entities.billing_provider().tin,
        },
        "prescribers": [
            {
                "prescriber_id": p.prescriber_id,
                "npi": p.npi,
                "name": p.name,
                "specialty": p.specialty,
            }
            for p in entities.prescribers()
        ],
        "pbms": [
            {
                "pbm_id": p.pbm_id,
                "name": p.name,
                "bin": p.bin,
                "pcn": p.pcn,
                "group_id": p.group_id,
                "payer_name": p.payer_name,
                "originating_company_id": p.originating_company_id,
                "company_id": p.company_id,
                "ach_routing_prefix": p.ach_routing_prefix,
                "copay_cents": p.copay_cents,
                "reject_vocabulary": p.reject_vocabulary,
            }
            for p in entities.pbms()
        ],
        "medical_payers": [
            {
                "payer_id": p.payer_id,
                "name": p.name,
                "tin": p.tin,
                "originating_company_id": p.originating_company_id,
                "company_id": p.company_id,
                "ach_routing_prefix": p.ach_routing_prefix,
                "coinsurance_bps": p.coinsurance_bps,
                "carc_vocabulary": p.carc_vocabulary,
            }
            for p in entities.medical_payers()
        ],
        "covered_entities": [
            {
                "covered_entity_id": c.covered_entity_id,
                "name": c.name,
                "hin": c.hin,
                "is_own_entity": c.is_own_entity,
                "affiliated_prescriber_npis": sorted(c.affiliated_prescriber_npis),
                "registered_pharmacy_npis": sorted(c.registered_pharmacy_npis),
            }
            for c in entities.covered_entities()
        ],
        "manufacturers": [
            {
                "manufacturer_id": m.manufacturer_id,
                "name": m.name,
                "short_name": m.short_name,
                "labeler_codes": sorted(m.labeler_codes),
                "restricts_contract_pharmacy": m.restricts_contract_pharmacy,
                "company_id": m.company_id,
                "ach_routing_prefix": m.ach_routing_prefix,
            }
            for m in entities.manufacturers()
        ],
        "patients": [
            {
                "patient_id": p.patient_id,
                "cardholder_id": p.cardholder_id,
                "person_code": p.person_code,
                "benefit_type": str(p.benefit_type),
            }
            for p in patients.all_patients()
        ],
        "plb_codes": [
            {"code": p.code, "sign": p.sign, "traceable": p.traceable}
            for p in codes.all_plb_codes()
        ],
        "disqualification_reasons": list(codes.disqualification_reasons()),
        "manufacturer_rejection_reasons": list(codes.manufacturer_rejection_reasons()),
        "federal_holidays": sorted(d.isoformat() for d in calendar.FEDERAL_HOLIDAYS),
    }


def fingerprint() -> str:
    """Stable SHA-256 hex digest of the reference universe."""
    document = json.dumps(
        canonical_reference_document(), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    return hashlib.sha256(document.encode("utf-8")).hexdigest()
