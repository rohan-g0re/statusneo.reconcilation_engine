"""Forty patients, carrying nothing but tokens.

No name, no date of birth, no address, no diagnosis.  This is not an omission for
convenience: none of the four feeds carries demographics either, so the reference
table has nothing to hold, and a reconciliation prototype that invents PHI it does
not need has made itself harder to talk about for no gain.

A patient is a cardholder id (what the PBM knows them by), a person code (which
dependent on the policy), and which benefit their therapy is billed under.  The
last one exists so a generator picking a patient for a pharmacy episode does not
accidentally pick someone whose therapy is infused.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType

from recon.domain.enums import BenefitType
from recon.reference.errors import UnknownEntityError

__all__ = ["Patient", "all_patients", "by_cardholder_id", "for_benefit_type", "by_patient_id"]

_PH = BenefitType.PHARMACY_BENEFIT
_MD = BenefitType.MEDICAL_BENEFIT


@dataclass(frozen=True, slots=True)
class Patient:
    patient_id: str
    cardholder_id: str
    person_code: str
    benefit_type: BenefitType


_PATIENTS: tuple[Patient, ...] = (
    Patient("PT_01", "W884210097", "01", _PH),
    Patient("PT_02", "W112039884", "01", _PH),
    Patient("PT_03", "W587526717", "01", _PH),
    Patient("PT_04", "W261603966", "02", _PH),
    Patient("PT_05", "W390901587", "01", _PH),
    Patient("PT_06", "W117533249", "01", _PH),
    Patient("PT_07", "W186440346", "03", _PH),
    Patient("PT_08", "W302606057", "01", _PH),
    Patient("PT_09", "W845121776", "01", _PH),
    Patient("PT_10", "W086473080", "02", _PH),
    Patient("PT_11", "W778444543", "01", _PH),
    Patient("PT_12", "W017855525", "01", _PH),
    Patient("PT_13", "W320888919", "01", _PH),
    Patient("PT_14", "W061267140", "02", _PH),
    Patient("PT_15", "W075311821", "01", _PH),
    Patient("PT_16", "W973420263", "01", _PH),
    Patient("PT_17", "W427432311", "01", _PH),
    Patient("PT_18", "W497645236", "02", _PH),
    Patient("PT_19", "W614704352", "01", _PH),
    Patient("PT_20", "W571326428", "01", _PH),
    Patient("PT_21", "W360015536", "01", _PH),
    Patient("PT_22", "W660193080", "02", _PH),
    Patient("PT_23", "W911018057", "01", _PH),
    Patient("PT_24", "W600907338", "01", _PH),
    Patient("PT_25", "W970099450", "01", _MD),
    Patient("PT_26", "W403045026", "01", _MD),
    Patient("PT_27", "W588187337", "02", _MD),
    Patient("PT_28", "W459213032", "01", _MD),
    Patient("PT_29", "W795528087", "01", _MD),
    Patient("PT_30", "W255372717", "01", _MD),
    Patient("PT_31", "W933892705", "02", _MD),
    Patient("PT_32", "W824234439", "01", _MD),
    Patient("PT_33", "W031143645", "01", _MD),
    Patient("PT_34", "W356126233", "01", _MD),
    Patient("PT_35", "W859552680", "02", _MD),
    Patient("PT_36", "W118485761", "01", _MD),
    Patient("PT_37", "W474266038", "01", _MD),
    Patient("PT_38", "W296274915", "01", _MD),
    Patient("PT_39", "W779498641", "02", _MD),
    Patient("PT_40", "W533669632", "01", _MD),
)

_BY_CARDHOLDER = MappingProxyType({p.cardholder_id: p for p in _PATIENTS})
_BY_ID = MappingProxyType({p.patient_id: p for p in _PATIENTS})


def all_patients() -> tuple[Patient, ...]:
    return _PATIENTS


def by_cardholder_id(cardholder_id: str) -> Patient:
    try:
        return _BY_CARDHOLDER[cardholder_id]
    except KeyError:
        raise UnknownEntityError("patient cardholder id", cardholder_id) from None


def by_patient_id(patient_id: str) -> Patient:
    try:
        return _BY_ID[patient_id]
    except KeyError:
        raise UnknownEntityError("patient", patient_id) from None


def for_benefit_type(benefit_type: BenefitType) -> tuple[Patient, ...]:
    benefit_type = BenefitType(benefit_type)
    return tuple(p for p in _PATIENTS if p.benefit_type is benefit_type)
