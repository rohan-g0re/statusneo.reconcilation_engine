"""Wire codes, and what they canonically mean.

This module is Decision 33 made concrete.  MERIDIANRX rejects a non-covered drug with
NCPDP ``70``; CASCADERX rejects the same thing with ``A1``.  Both resolve here to the
same canonical semantics, so the engine fires ``A-01`` from one branch rather than one
branch per PBM.  Adding a third PBM adds rows to these tables and changes no rule —
that is the whole claim, and this is where it is either true or it is not.

The PLB sign convention is the load-bearing part of the file.  Under the 835's own
identity::

    BPR02 = sum(CLP04) - sum(PLB, signed)

a **positive** PLB reduces the deposit.  So an overpayment recovery (``WO``) is
positive, and interest owed to the provider (``L6``) is negative because it increases
the deposit.  An appeal credit carried as ``RA`` is likewise negative, for the same
reason and in the same direction.  Getting this backwards inverts the arithmetic on
every remittance, which is why the sign lives in a table rather than in each rule.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType

__all__ = [
    "RejectCode",
    "CarcCode",
    "RarcCode",
    "PlbCode",
    "reject_semantics",
    "reject_codes_for",
    "carc_semantics",
    "carc_codes_for",
    "rarc_semantics",
    "plb_semantics",
    "all_plb_codes",
    "disqualification_reasons",
    "manufacturer_rejection_reasons",
    "clp02_settlement_confirmed",
    "clp02_settlement_unconfirmed",
    "CLP02_PAID_PRIMARY",
    "CLP02_FORWARDED",
    "CLP02_PREDETERMINATION",
    "CLP02_DENIED",
    "CLP02_REVERSAL",
]


# --- NCPDP reject codes ----------------------------------------------------


@dataclass(frozen=True, slots=True)
class RejectCode:
    code: str
    canonical: str
    description: str


#: Canonical reject semantics.  Two PBM vocabularies map onto this one set.
_REJECT_CANONICAL = (
    "NOT_COVERED",
    "PRIOR_AUTH_REQUIRED",
    "PLAN_LIMIT_EXCEEDED",
    "REFILL_TOO_SOON",
    "DUR_REJECT",
    "PHARMACY_NOT_CONTRACTED",
    "PATIENT_NOT_COVERED",
    "INVALID_PRODUCT",
    "INVALID_PRESCRIBER",
)

_NCPDP_NUMERIC: tuple[RejectCode, ...] = (
    RejectCode("70", "NOT_COVERED", "Product/service not covered - plan exclusion"),
    RejectCode("75", "PRIOR_AUTH_REQUIRED", "Prior authorization required"),
    RejectCode("76", "PLAN_LIMIT_EXCEEDED", "Plan limitations exceeded"),
    RejectCode("79", "REFILL_TOO_SOON", "Refill too soon"),
    RejectCode("88", "DUR_REJECT", "DUR reject error"),
    RejectCode("40", "PHARMACY_NOT_CONTRACTED", "Pharmacy not contracted on date of service"),
    RejectCode("65", "PATIENT_NOT_COVERED", "Patient is not covered"),
    RejectCode("21", "INVALID_PRODUCT", "Product/service not on file"),
    RejectCode("25", "INVALID_PRESCRIBER", "Missing/invalid prescriber id"),
)

#: The second PBM's vocabulary.  Different strings on the wire, identical semantics.
_CASCADE_ALPHA: tuple[RejectCode, ...] = (
    RejectCode("A1", "NOT_COVERED", "Not a covered benefit under this plan"),
    RejectCode("A4", "PRIOR_AUTH_REQUIRED", "Authorization required before dispensing"),
    RejectCode("A7", "PLAN_LIMIT_EXCEEDED", "Quantity or duration limit reached"),
    RejectCode("B2", "REFILL_TOO_SOON", "Early refill"),
    RejectCode("B9", "DUR_REJECT", "Clinical review reject"),
    RejectCode("C3", "PHARMACY_NOT_CONTRACTED", "Non-network pharmacy"),
    RejectCode("C8", "PATIENT_NOT_COVERED", "Member not eligible on date of service"),
    RejectCode("D1", "INVALID_PRODUCT", "Unrecognised product identifier"),
    RejectCode("D6", "INVALID_PRESCRIBER", "Prescriber identifier not valid"),
)

_REJECT_VOCABULARIES = MappingProxyType(
    {
        "NCPDP_NUMERIC": MappingProxyType({r.code: r for r in _NCPDP_NUMERIC}),
        "CASCADE_ALPHA": MappingProxyType({r.code: r for r in _CASCADE_ALPHA}),
    }
)


def _reject_table(pbm_id: str):
    from recon.reference.entities import pbm_by_id

    return _REJECT_VOCABULARIES[pbm_by_id(pbm_id).reject_vocabulary]


def reject_semantics(pbm_id: str, code: str) -> RejectCode:
    """Resolve a PBM's reject code to canonical semantics.

    Raises:
        KeyError: if the PBM does not use that code.  An unrecognised code is a
            quarantine case, not something to coerce (Decision 33 §3).
    """
    table = _reject_table(pbm_id)
    try:
        return table[code]
    except KeyError:
        raise KeyError(f"PBM {pbm_id!r} has no reject code {code!r}") from None


def reject_codes_for(pbm_id: str) -> tuple[RejectCode, ...]:
    return tuple(_reject_table(pbm_id).values())


# --- CARC / RARC -----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CarcCode:
    group_code: str
    reason_code: str
    canonical: str
    description: str

    @property
    def wire(self) -> str:
        return f"{self.group_code}-{self.reason_code}"

    @property
    def is_patient_responsibility(self) -> bool:
        return self.group_code == "PR"


_CARC_STANDARD: tuple[CarcCode, ...] = (
    CarcCode("CO", "45", "CONTRACTUAL_ADJUSTMENT", "Charge exceeds fee schedule"),
    CarcCode("CO", "97", "BUNDLED", "Bundled into another service"),
    CarcCode("CO", "16", "MISSING_INFORMATION", "Claim lacks information (pair with an RARC)"),
    CarcCode("CO", "18", "DUPLICATE", "Duplicate claim or service"),
    CarcCode("CO", "50", "NOT_MEDICALLY_NECESSARY", "Not deemed a medical necessity"),
    CarcCode("CO", "151", "FREQUENCY_EXCEEDED", "Frequency or quantity exceeds limit"),
    CarcCode("CO", "197", "PRIOR_AUTH_ABSENT", "Precertification/authorization absent"),
    CarcCode("PR", "1", "DEDUCTIBLE", "Deductible amount"),
    CarcCode("PR", "2", "COINSURANCE", "Coinsurance amount"),
    CarcCode("PR", "3", "COPAY", "Copayment amount"),
    CarcCode("PR", "204", "NOT_COVERED", "Not covered under the patient's plan"),
)

_CARC_VOCABULARIES = MappingProxyType(
    {"STANDARD": MappingProxyType({(c.group_code, c.reason_code): c for c in _CARC_STANDARD})}
)

#: Group codes seen on an 835 adjustment.
GROUP_CODES = MappingProxyType(
    {
        "CO": "Contractual obligation - not billable to the patient",
        "PR": "Patient responsibility",
        "OA": "Other adjustment",
        "PI": "Payer-initiated reduction",
        "CR": "Correction or reversal",
    }
)


def _carc_table(payer_id: str):
    from recon.reference.entities import payer_by_id

    return _CARC_VOCABULARIES[payer_by_id(payer_id).carc_vocabulary]


def carc_semantics(payer_id: str, group_code: str, reason_code: str) -> CarcCode:
    table = _carc_table(payer_id)
    try:
        return table[(group_code, reason_code)]
    except KeyError:
        raise KeyError(
            f"payer {payer_id!r} has no adjustment code {group_code}-{reason_code}"
        ) from None


def carc_codes_for(payer_id: str) -> tuple[CarcCode, ...]:
    return tuple(_carc_table(payer_id).values())


@dataclass(frozen=True, slots=True)
class RarcCode:
    code: str
    description: str


_RARC: tuple[RarcCode, ...] = (
    RarcCode("N130", "Consult plan benefit documents for information about restrictions"),
    RarcCode("N362", "The number of days or units of service exceeds the acceptable maximum"),
    RarcCode("N522", "Duplicate of a claim processed or in process as a crossover claim"),
    RarcCode("M15", "Separately billed services have been bundled as they are considered components"),
    RarcCode("N54", "Claim information is inconsistent with pre-certified/authorized services"),
    RarcCode("N56", "Procedure code billed is not correct for the services billed"),
)

_RARC_BY_CODE = MappingProxyType({r.code: r for r in _RARC})


def rarc_semantics(code: str) -> RarcCode:
    try:
        return _RARC_BY_CODE[code]
    except KeyError:
        raise KeyError(f"unknown RARC code {code!r}") from None


# --- PLB -------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PlbCode:
    code: str
    sign: int  # +1 reduces the deposit, -1 increases it
    description: str
    traceable: bool  # does it carry a usable reference id back to a claim?

    @property
    def increases_payment(self) -> bool:
        return self.sign < 0


_PLB: tuple[PlbCode, ...] = (
    PlbCode("WO", +1, "Overpayment recovery", True),
    # FB deliberately carries no usable reference: it is the D-7 allocation residual.
    PlbCode("FB", +1, "Forward balance carried to the next cycle", False),
    PlbCode("L6", -1, "Interest owed to the provider", False),
    PlbCode("CS", +1, "Adjustment", True),
    PlbCode("72", +1, "Authorized return", True),
    PlbCode("RA", -1, "Retroactive adjustment; an appeal credit is carried negative", True),
)

_PLB_BY_CODE = MappingProxyType({p.code: p for p in _PLB})


def plb_semantics(code: str) -> PlbCode:
    try:
        return _PLB_BY_CODE[code]
    except KeyError:
        raise KeyError(f"unknown PLB code {code!r}") from None


def all_plb_codes() -> tuple[PlbCode, ...]:
    return _PLB


# --- CLP02, the settlement encoding (Decision 40) --------------------------

CLP02_PAID_PRIMARY = "1"
CLP02_FORWARDED = "19"
CLP02_PREDETERMINATION = "25"
CLP02_DENIED = "4"
CLP02_REVERSAL = "22"


def clp02_settlement_confirmed() -> str:
    """The CLP02 value that means settlement is confirmed for a pharmacy claim.

    ``ph_settlement`` has no native transaction of its own, so it rides CLP02 on the
    paying claim line.  The engine's ``A-06`` rule must read exactly this encoding —
    a second, divergent reading would make settlement a matter of opinion.
    """
    return CLP02_PAID_PRIMARY


def clp02_settlement_unconfirmed() -> tuple[str, ...]:
    """CLP02 values that leave settlement unconfirmed until a later ``"1"`` appears."""
    return (CLP02_FORWARDED, CLP02_PREDETERMINATION)


# --- 340B vocabularies -----------------------------------------------------


def disqualification_reasons() -> tuple[str, ...]:
    """Why a TPA declines to qualify a dispense for 340B pricing."""
    return (
        "NO_QUALIFYING_ENCOUNTER",
        "PRESCRIBER_NOT_AFFILIATED",
        "UNREGISTERED_LOCATION",
        "MEDICAID_DUPLICATE_DISCOUNT",
    )


def manufacturer_rejection_reasons() -> tuple[str, ...]:
    """Why a manufacturer rejects an otherwise-qualified rebate request."""
    return ("CONTRACT_PHARMACY_RESTRICTED", "NON_CONFORMING_45_DAY")
