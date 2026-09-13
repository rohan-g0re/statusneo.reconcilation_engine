"""Hand-authored feed scenarios, and a harness that traces one through every stage.

The generator tests prove the pipeline agrees with the generator.  That is necessary and it is
not sufficient: both sides could share a misreading and the suite would never notice.  These
scenarios are written by hand, record by record, against ``docs/feed_formats.md`` — so they are
an *independent* statement of what each feed looks like and what the engine should conclude.

They also check something the end-to-end score cannot: the **intermediate states**.  A scenario
asserts the raw row, the normalized tree, the keys published, the park and the unpark, the cash
allocation and its basis, the derived dimensions, the verdict, the disposition, the reason codes
and the lineage citations.  A verdict that is right for the wrong reason fails here.

Amounts are computed through :mod:`recon.reference.pricing` rather than hardcoded, deliberately:
hardcoding them would make every scenario break when a contracted rate changes, and would test
the literal rather than the behaviour.  Structure is asserted with literals; money is asserted
against the one authority both sides share.
"""

from __future__ import annotations

import csv
import io
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from recon import config
from recon.config import Settings, load_settings
from recon.crosswalk import keys
from recon.db import connection, migrate
from recon.engine import run as engine
from recon.engine.dimensions import derive_dimensions, gather_evidence
from recon.generators import bank as bank_gen
from recon.ingest import pipeline
from recon.money import format_amount
from recon.reference import drugs, entities, pricing

__all__ = [
    "Scenario",
    "ScenarioRun",
    "run_scenario",
    "PH_NPI",
    "SAT_NPI",
    "PROVIDER_NPI",
    "PRESCRIBER_NPI",
    "PBM",
    "PAYER",
    "PH_DRUG",
    "MD_DRUG",
    "CE",
    "pharmacy_money",
    "medical_money",
    "b1",
    "b2",
    "pbm_835",
    "claim_line",
    "med_837",
    "med_277",
    "med_835",
    "med_claim_line",
    "tpa_event",
    "rebate_batch",
    "bank_row",
]

# ═══ the fixed cast, so every scenario reads the same way ═══════════════════

PH_NPI = entities.pharmacy().npi
SAT_NPI = entities.satellite_pharmacy().npi
PROVIDER_NPI = entities.billing_provider().npi
#: Affiliated with our covered entity, so a qualifying 340B dispense is coherent.
PRESCRIBER_NPI = sorted(entities.own_covered_entity().affiliated_prescriber_npis)[0]
PBM = entities.pbm_by_id("PBM_MERIDIAN")
PAYER = entities.payer_by_id("PAY_BLUEHARBOR")
#: An oral specialty: pharmacy benefit, no J-code.
PH_DRUG = drugs.by_ndc("00071015523")
#: An infused J-coded drug: medical benefit.
MD_DRUG = drugs.by_ndc("50242007923")
CE = entities.own_covered_entity()

PH_QTY_UNITS = 30
PH_QTY_MILLI = PH_QTY_UNITS * 1_000
MD_VIALS = 4
MD_QTY_UNITS = MD_VIALS * (MD_DRUG.ctp_units_per_billing_unit or 1)
MD_QTY_MILLI = MD_QTY_UNITS * 1_000


@dataclass(frozen=True, slots=True)
class Money:
    charge: int
    allowed: int
    patient: int
    expected: int
    rebate: int


def pharmacy_money() -> Money:
    """Every figure the pharmacy scenarios measure against, from the shared authority."""
    ndc, payer = PH_DRUG.ndc11, PBM.pbm_id
    allowed = pricing.contracted_allowed_cents(ndc, payer, PH_QTY_MILLI)
    patient = pricing.patient_responsibility_cents(payer, allowed)
    return Money(
        charge=pricing.charge_cents(ndc, PH_QTY_MILLI),
        allowed=allowed,
        patient=patient,
        expected=pricing.expected_reimbursement_cents(ndc, payer, PH_QTY_MILLI),
        rebate=pricing.expected_rebate_cents(ndc, PH_QTY_MILLI),
    )


def medical_money() -> Money:
    ndc, payer = MD_DRUG.ndc11, PAYER.payer_id
    allowed = pricing.contracted_allowed_cents(ndc, payer, MD_QTY_MILLI)
    patient = pricing.patient_responsibility_cents(payer, allowed)
    return Money(
        charge=pricing.charge_cents(ndc, MD_QTY_MILLI),
        allowed=allowed,
        patient=patient,
        expected=pricing.expected_reimbursement_cents(ndc, payer, MD_QTY_MILLI),
        rebate=pricing.expected_rebate_cents(ndc, MD_QTY_MILLI),
    )


# ═══ record builders — one per wire shape in feed_formats.md ════════════════


def b1(
    *,
    record_id: str,
    received_at: str,
    rx: str,
    fill: str = "00",
    dos: str,
    npi: str = PH_NPI,
    accepted: bool = True,
    reject_code: str | None = None,
    is_340b: bool = False,
    auth: str | None = "AUTH0098231A",
    money: Money | None = None,
) -> dict[str, Any]:
    """An NCPDP B1 adjudication."""
    amounts = money or pharmacy_money()
    payload: dict[str, Any] = {
        "record_id": record_id,
        "source_system": "PBM_ADJUDICATION",
        "transaction_code": "B1",
        "received_at": received_at,
        "service_provider_id": npi,
        "service_provider_id_qualifier": "01",
        "prescription_ref_number": rx,
        "fill_number": fill,
        "date_of_service": keys.iso_date_to_wire(dos),
        "product_service_id": PH_DRUG.ndc11,
        "product_service_id_qualifier": "03",
        "quantity_dispensed": f"{PH_QTY_MILLI:06d}",
        "days_supply": f"{PH_QTY_UNITS:03d}",
        "prescriber_id": PRESCRIBER_NPI,
        "prescriber_id_qualifier": "01",
        "bin": PBM.bin,
        "pcn": PBM.pcn,
        "group_id": PBM.group_id,
        "cardholder_id": "W884210097",
        "person_code": "01",
    }
    if is_340b:
        payload["submission_clarification_code"] = "20"
    if accepted:
        fee = pricing.contract_terms(PBM.pbm_id, PH_DRUG.ndc11).dispensing_fee_cents
        ingredient = amounts.allowed - fee
        payload.update(
            response_status="P",
            authorization_number=auth,
            ingredient_cost_paid=format_amount(ingredient),
            dispensing_fee_paid=format_amount(fee),
            patient_pay_amount=format_amount(amounts.patient),
            total_amount_paid=format_amount(ingredient + fee - amounts.patient),
            reject_codes=[],
        )
    else:
        payload.update(
            response_status="R",
            authorization_number=None,
            ingredient_cost_paid=None,
            dispensing_fee_paid=None,
            patient_pay_amount=None,
            total_amount_paid=None,
            reject_codes=[reject_code or "75"],
        )
    return payload


def b2(
    *, record_id: str, received_at: str, rx: str, fill: str = "00", dos: str,
    npi: str = PH_NPI, reason: str = "RETURN_TO_STOCK", auth: str | None = "AUTH0098231A",
) -> dict[str, Any]:
    """An NCPDP B2 reversal.  Carries no identity of its own — repeats the transaction key."""
    return {
        "record_id": record_id,
        "source_system": "PBM_ADJUDICATION",
        "transaction_code": "B2",
        "received_at": received_at,
        "service_provider_id": npi,
        "prescription_ref_number": rx,
        "fill_number": fill,
        "date_of_service": keys.iso_date_to_wire(dos),
        "product_service_id": PH_DRUG.ndc11,
        "bin": PBM.bin,
        "pcn": PBM.pcn,
        "response_status": "P",
        "authorization_number": auth,
        "reversal_reason": reason,
    }


def claim_line(
    *,
    rx: str,
    fill: str = "00",
    dos: str,
    charge: int,
    paid: int,
    patient: int,
    adjustments: list[tuple[str, str, int]],
    clp02: str = "1",
    clp07: str = "20260610044821",
    auth: str | None = "AUTH0098231A",
) -> dict[str, Any]:
    """One claim payment inside a pharmacy 835.

    ``CLP01`` is built through :func:`recon.crosswalk.keys.format_clp01_pharmacy`, so the scenario
    cannot accidentally disagree with the connector about NCPDP's ``<rx>FILL<nn>`` convention.
    """
    return {
        "clp01_patient_control_number": keys.format_clp01_pharmacy(rx, fill),
        "clp02_claim_status_code": clp02,
        "clp03_total_charge": format_amount(charge),
        "clp04_payment_amount": format_amount(paid),
        "clp05_patient_responsibility": format_amount(patient),
        "clp06_claim_filing_indicator": "CI",
        "clp07_payer_claim_control_number": clp07,
        "ref_authorization_number": auth,
        "service_line": {
            "product_id_qualifier": "N4",
            "product_id": PH_DRUG.ndc11,
            "charge_amount": format_amount(charge),
            "paid_amount": format_amount(paid),
            "quantity": str(PH_QTY_UNITS),
            "date_of_service": keys.iso_date_to_wire(dos),
        },
        "adjustments": [
            {"group_code": group, "reason_code": reason, "amount": format_amount(amount)}
            for group, reason, amount in adjustments
        ],
    }


def pbm_835(
    *,
    record_id: str,
    received_at: str,
    effective: str,
    trace: str,
    lines: list[dict[str, Any]],
    plb: list[tuple[str, str | None, int]] | None = None,
    npi: str = PH_NPI,
) -> dict[str, Any]:
    """A pharmacy 835.  ``BPR02`` is computed here, exactly as the wire identity requires."""
    from recon.reference import codes

    claim_total = sum(
        int(round(float(line["clp04_payment_amount"]) * 100)) for line in lines
    )
    plb_entries = plb or []
    signed_total = sum(
        amount * codes.plb_semantics(reason).sign for reason, _, amount in plb_entries
    )
    payload: dict[str, Any] = {
        "record_id": record_id,
        "source_system": "PBM_REMITTANCE",
        "received_at": received_at,
        "bpr": {
            "transaction_handling_code": "I",
            "total_actual_provider_payment": format_amount(claim_total - signed_total),
            "credit_debit_flag": "C",
            "payment_method_code": "ACH",
            "payment_effective_date": keys.iso_date_to_wire(effective),
        },
        "trn": {
            "trace_type_code": "1",
            "reassociation_trace_number": trace,
            "originating_company_id": PBM.originating_company_id,
        },
        "payer_name": PBM.payer_name,
        "payee_npi": npi,
        "claim_payments": lines,
    }
    if plb_entries:
        payload["provider_level_adjustments"] = [
            {
                "reason_code": reason,
                "reference_id": reference,
                "amount": format_amount(amount * codes.plb_semantics(reason).sign),
            }
            for reason, reference, amount in plb_entries
        ]
    return payload


def med_837(
    *,
    record_id: str,
    received_at: str,
    clm01: str,
    dos: str,
    frequency: str = "1",
    original_icn: str | None = None,
    charge: int | None = None,
    drop_line: bool = False,
) -> dict[str, Any]:
    amounts = medical_money()
    service_lines = [
        {
            "line_number": 1,
            "svc01_composite": f"HC:{MD_DRUG.hcpcs_j_code}:JW",
            "svc02_charge_amount": format_amount(charge or amounts.charge),
            "units": MD_VIALS,
        }
    ]
    if not drop_line:
        service_lines.append(
            {
                "line_number": 2,
                "svc01_composite": "HC:96413",
                "svc02_charge_amount": format_amount(0),
                "units": 1,
            }
        )
    return {
        "record_id": record_id,
        "source_system": "CLEARINGHOUSE_837",
        "received_at": received_at,
        "clm01_patient_control_number": clm01,
        "clm05_3_frequency_code": frequency,
        "ref_f8_original_icn": original_icn,
        "billing_provider_npi": PROVIDER_NPI,
        "rendering_provider_npi": PRESCRIBER_NPI,
        "date_of_service": keys.iso_date_to_wire(dos),
        "loop_2010bb": {"nm103_payer_name": PAYER.name, "nm109_payer_id": PAYER.trn03},
        "service_lines": service_lines,
        "loop_2410": {
            "lin02_qualifier": "N4",
            "ndc11": MD_DRUG.ndc11,
            "ctp04_quantity": str(MD_QTY_UNITS),
            "ctp05_uom_qualifier": "ML",
        },
    }


def med_277(
    *, record_id: str, received_at: str, clm01: str, accepted: bool = True,
    icn: str | None = "20260610077213",
) -> dict[str, Any]:
    """A 277CA.  Without it, B-01 is unreachable — see Decision 39."""
    return {
        "record_id": record_id,
        "source_system": "CLEARINGHOUSE_837",
        "record_type": "277CA",
        "received_at": received_at,
        "clm01_patient_control_number": clm01,
        "stc01_composite": "A1:19" if accepted else "A3:21",
        "stc12_free_form": (
            "ACCEPTED FOR PROCESSING" if accepted else "MISSING OR INVALID SUBSCRIBER ID"
        ),
        "payer_claim_control_number": icn if accepted else None,
    }


def med_claim_line(
    *,
    clm01: str,
    charge: int,
    paid: int,
    patient: int,
    adjustments: list[tuple[str, str, int]],
    clp02: str = "1",
    clp07: str = "20260610088410",
) -> dict[str, Any]:
    return {
        "clp01_patient_control_number": clm01,
        "clp02_status_code": clp02,
        "clp03_total_charge": format_amount(charge),
        "clp04_payment_amount": format_amount(paid),
        "clp05_patient_responsibility": format_amount(patient),
        "clp07_payer_claim_control_number": clp07,
        "service_lines": [
            {
                "svc01_composite": f"HC:{MD_DRUG.hcpcs_j_code}:JW",
                "svc02_charge": format_amount(charge),
                "svc03_paid": format_amount(paid),
                "svc05_units": MD_VIALS,
            }
        ],
        "adjustments": [
            {"group_code": group, "reason_code": reason, "amount": format_amount(amount)}
            for group, reason, amount in adjustments
        ],
    }


def med_835(
    *,
    record_id: str,
    received_at: str,
    effective: str,
    trace: str,
    lines: list[dict[str, Any]],
    plb: list[tuple[str, str | None, int]] | None = None,
) -> dict[str, Any]:
    from recon.reference import codes

    claim_total = sum(
        int(round(float(line["clp04_payment_amount"]) * 100)) for line in lines
    )
    plb_entries = plb or []
    signed_total = sum(
        amount * codes.plb_semantics(reason).sign for reason, _, amount in plb_entries
    )
    payload: dict[str, Any] = {
        "record_id": record_id,
        "source_system": "MEDICAL_REMITTANCE",
        "received_at": received_at,
        "bpr": {
            "bpr02_total_payment": format_amount(claim_total - signed_total),
            "bpr04_payment_method": "ACH",
            "bpr16_eft_effective_date": keys.iso_date_to_wire(effective),
        },
        "trn": {"trn01": "1", "trn02_trace_number": trace, "trn03_payer_tin": PAYER.trn03},
        "claim_payments": lines,
    }
    if plb_entries:
        payload["provider_level_adjustments"] = [
            {
                "reason_code": reason,
                "reference_icn": reference,
                "amount": format_amount(amount * codes.plb_semantics(reason).sign),
            }
            for reason, reference, amount in plb_entries
        ]
    return payload


def tpa_event(
    *,
    record_id: str,
    received_at: str,
    event_type: str,
    rx: str | None,
    dos: str,
    ndc: str | None = None,
    npi: str | None = PH_NPI,
    provider_npi: str | None = None,
    qualification: str | None = None,
    disqualification_reason: str | None = None,
    manufacturer_status: str | None = None,
    rejection_reason: str | None = None,
    submission_date: str | None = None,
    quantity: int | None = None,
    source_system: str = "TPA_PORTAL",
) -> dict[str, Any]:
    drug_ndc = ndc or (PH_DRUG.ndc11 if rx else MD_DRUG.ndc11)
    manufacturer = entities.manufacturer_for_ndc(drug_ndc)
    payload: dict[str, Any] = {
        "record_id": record_id,
        "source_system": source_system,
        "event_type": event_type,
        "received_at": received_at,
        "rx_number": rx,
        "pharmacy_npi": npi if rx else None,
        "provider_npi": provider_npi,
        "ndc_11": drug_ndc,
        "fill_date": keys.iso_date_to_wire(dos),
        "prescriber_npi": PRESCRIBER_NPI,
        "covered_entity_id": CE.covered_entity_id,
        "hin": CE.hin,
        "wholesaler_invoice_number": "WI-88213340",
        "manufacturer": manufacturer.short_name,
    }
    if event_type == "QUALIFICATION_DECISION":
        payload["qualification_status"] = qualification
        payload["disqualification_reason"] = disqualification_reason
    if event_type == "REBATE_REQUEST":
        payload["submission_date"] = keys.iso_date_to_wire(submission_date or dos)
    if event_type == "MANUFACTURER_DECISION":
        payload["manufacturer_status"] = manufacturer_status
        payload["rejection_reason"] = rejection_reason
    if event_type == "DISPENSE_REVERSAL":
        payload["quantity_dispensed"] = quantity if quantity is not None else -PH_QTY_UNITS
        payload["reversal_reason"] = "RETURN_TO_STOCK"
    return payload


def rebate_batch(
    *,
    record_id: str,
    received_at: str,
    effective: str,
    allocation_code: str,
    dispenses: list[dict[str, Any]],
    ndc: str | None = None,
) -> dict[str, Any]:
    drug_ndc = ndc or PH_DRUG.ndc11
    manufacturer = entities.manufacturer_for_ndc(drug_ndc)
    total = sum(int(round(float(d["rebate_amount"]) * 100)) for d in dispenses)
    return {
        "record_id": record_id,
        "source_system": "MANUFACTURER_REBATE",
        "event_type": "REBATE_PAYMENT_BATCH",
        "received_at": received_at,
        "manufacturer": manufacturer.short_name,
        "allocation_code": allocation_code,
        "total_rebate_amount": format_amount(total),
        "payment_effective_date": keys.iso_date_to_wire(effective),
        "dispenses": dispenses,
    }


def dispense_line(
    *, rx: str | None, dos: str, amount: int, ndc: str | None = None,
    npi: str | None = PH_NPI, provider_npi: str | None = None,
) -> dict[str, Any]:
    return {
        "rx_number": rx,
        "pharmacy_npi": npi if rx else None,
        "provider_npi": provider_npi,
        "ndc_11": ndc or (PH_DRUG.ndc11 if rx else MD_DRUG.ndc11),
        "fill_date": keys.iso_date_to_wire(dos),
        "covered_entity_id": CE.covered_entity_id,
        "manufacturer_status": "APPROVED",
        "rebate_amount": format_amount(amount),
    }


def bank_row(
    *,
    posting: str,
    amount: int,
    trn02: str | None,
    company: str = "MERIDIANRX",
    company_id: str | None = None,
    entry: str = "HCCLAIMPMT",
    received_at: str | None = None,
    direction: str = "CREDIT",
    trace_suffix: int = 1,
) -> dict[str, str]:
    """One bank CSV row.  ``trn02`` of ``None`` models the ~20% CCD+ addenda loss."""
    return {
        "posting_date": posting,
        "description": "ACH CREDIT" if direction == "CREDIT" else "ACH DEBIT",
        "ach_trace_number": f"{PBM.ach_routing_prefix}{trace_suffix:07d}",
        "trn02": trn02 or "",
        "company_name": company[:16],
        "company_id": company_id or PBM.company_id,
        "company_entry_description": entry,
        "amount": format_amount(amount if direction == "CREDIT" else -abs(amount)),
        "type": direction,
        "running_balance": format_amount(124_000_000 + amount),
        "received_at": received_at or f"{posting}T17:50:00Z",
    }


# ═══ the scenario and its harness ══════════════════════════════════════════


@dataclass
class Scenario:
    """A hand-authored dataset: exactly the records the case needs, and nothing else."""

    name: str
    pbm_claims: list[dict[str, Any]] = field(default_factory=list)
    pbm_remittances: list[dict[str, Any]] = field(default_factory=list)
    med_submissions: list[dict[str, Any]] = field(default_factory=list)
    med_remittances: list[dict[str, Any]] = field(default_factory=list)
    tpa_events: list[dict[str, Any]] = field(default_factory=list)
    bank_rows: list[dict[str, str]] = field(default_factory=list)

    def feeds(self) -> dict[str, str]:
        """Render the scenario as the six feed files, byte for byte as the loader will read them."""
        def jsonl(records: list[dict[str, Any]]) -> str:
            return "".join(
                json.dumps(record, separators=(",", ":")) + "\n" for record in records
            )

        buffer = io.StringIO(newline="")
        writer = csv.DictWriter(
            buffer, fieldnames=list(bank_gen.BANK_CSV_COLUMNS), lineterminator="\n"
        )
        writer.writeheader()
        for row in self.bank_rows:
            writer.writerow(row)

        return {
            config.PBM_CLAIM_EVENTS_FILE: jsonl(self.pbm_claims),
            config.PBM_REMITTANCE_835_FILE: jsonl(self.pbm_remittances),
            config.MEDICAL_837_SUBMISSIONS_FILE: jsonl(self.med_submissions),
            config.MEDICAL_835_REMITTANCE_FILE: jsonl(self.med_remittances),
            config.TPA_340B_EVENTS_FILE: jsonl(self.tpa_events),
            config.BANK_TRANSACTIONS_FILE: buffer.getvalue(),
        }


@dataclass
class ScenarioRun:
    """Everything the harness observed, stage by stage, so a test can assert on any of it."""

    scenario: Scenario
    settings: Settings
    conn: sqlite3.Connection
    stats: pipeline.IngestStats
    results: list[engine.EngineResult]

    # -- stage accessors ----------------------------------------------------

    def raw(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM raw_record ORDER BY raw_id"
        ).fetchall()

    def normalized(self, kind: str | None = None) -> list[sqlite3.Row]:
        if kind is None:
            return self.conn.execute(
                "SELECT * FROM normalized_record ORDER BY norm_id"
            ).fetchall()
        return self.conn.execute(
            "SELECT * FROM normalized_record WHERE record_kind = ? ORDER BY norm_id", (kind,)
        ).fetchall()

    def episodes(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM episode ORDER BY episode_id").fetchall()

    def only_episode(self) -> sqlite3.Row:
        rows = self.episodes()
        assert len(rows) == 1, f"{self.scenario.name}: expected one episode, got {len(rows)}"
        return rows[0]

    def crosswalk(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM crosswalk_key ORDER BY crosswalk_id"
        ).fetchall()

    def key_types(self) -> set[str]:
        return {row["key_type"] for row in self.crosswalk()}

    def parked(self, *, unresolved_only: bool = True) -> list[sqlite3.Row]:
        sql = (
            "SELECT p.*, r.resolved_by_norm_id FROM parked_record p"
            " LEFT JOIN parked_record_resolution r ON r.parked_id = p.parked_id"
        )
        if unresolved_only:
            sql += " WHERE r.parked_id IS NULL"
        return self.conn.execute(sql + " ORDER BY p.parked_id").fetchall()

    def allocations(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM cash_allocation ORDER BY allocation_id"
        ).fetchall()

    def allocated_to_episode(self, episode_id: str) -> int:
        row = self.conn.execute(
            "SELECT COALESCE(SUM(allocated_cents), 0) AS total FROM cash_allocation"
            " WHERE episode_id = ?",
            (episode_id,),
        ).fetchone()
        return row["total"]

    def verdict_row(self, episode_id: str) -> sqlite3.Row:
        return self.conn.execute(
            "SELECT * FROM verdict WHERE episode_id = ? ORDER BY verdict_id DESC LIMIT 1",
            (episode_id,),
        ).fetchone()

    def reasons(self, episode_id: str) -> list[str]:
        return [
            row["reason_code"]
            for row in self.conn.execute(
                "SELECT r.reason_code FROM verdict_reason r"
                "  JOIN verdict v ON v.verdict_id = r.verdict_id"
                " WHERE v.episode_id = ? ORDER BY r.ordinal",
                (episode_id,),
            )
        ]

    def flags(self, episode_id: str) -> set[str]:
        return {
            row["flag_code"]
            for row in self.conn.execute(
                "SELECT f.flag_code FROM verdict_cross_track_flag f"
                "  JOIN verdict v ON v.verdict_id = f.verdict_id"
                " WHERE v.episode_id = ?",
                (episode_id,),
            )
        }

    def citations(self, episode_id: str) -> list[sqlite3.Row]:
        from recon.db import repository

        verdict = self.verdict_row(episode_id)
        return repository.evidence_for_verdict(self.conn, verdict["verdict_id"])

    def result(self) -> engine.EngineResult:
        assert len(self.results) == 1, (
            f"{self.scenario.name}: expected one result, got {len(self.results)}"
        )
        return self.results[0]

    def dimensions_at(self, cursor: str) -> dict[str, str]:
        """Re-derive the dimensions at an arbitrary cursor, for replay assertions."""
        episode = self.only_episode()
        evidence = gather_evidence(self.conn, episode, cursor)
        expected_reimbursement, expected_rebate = engine.expected_amounts(episode)
        return derive_dimensions(
            evidence,
            expected_reimbursement_cents=expected_reimbursement,
            expected_rebate_cents=expected_rebate,
        ).as_configuration()

    def verdict_at(self, cursor: str) -> tuple[str, str]:
        episode = self.only_episode()
        result = engine.evaluate_episode(self.conn, episode, cursor)
        return result.reimbursement_verdict, result.rebate_verdict


def run_scenario(scenario: Scenario, tmp_path: Path, *, cursor: str | None = None) -> ScenarioRun:
    """Write the scenario's feeds, ingest them, and reconcile — the real pipeline, start to end.

    Nothing is stubbed.  The same loader, the same adapters, the same crosswalk and the same engine
    the generated profiles use, so a scenario that passes here is a statement about the shipping
    code rather than about a test harness.
    """
    settings = load_settings("demo", data_dir=tmp_path)
    feeds_dir = settings.feeds_dir()
    feeds_dir.mkdir(parents=True, exist_ok=True)
    for filename, text in scenario.feeds().items():
        (feeds_dir / filename).write_text(text, encoding="utf-8", newline="")

    conn = connection.connect(":memory:")
    migrate.ensure_schema(conn)
    stats = pipeline.load_feeds(conn, feeds_dir)
    pipeline.ingest(conn, stats=stats)
    results = engine.run_all(conn, cursor or settings.max_cursor)
    return ScenarioRun(
        scenario=scenario, settings=settings, conn=conn, stats=stats, results=results
    )
