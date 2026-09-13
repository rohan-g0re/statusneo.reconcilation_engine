"""Every closed vocabulary the four groups share.

``StrEnum`` throughout (3.11+), so a member serialises straight to TEXT for SQLite
and to JSON for the API with no adapter in between.

Two rules govern what belongs here:

* A vocabulary is *closed* if anything outside it must be quarantined rather than
  coerced (Decision 33 §3).  Closed vocabularies get a SQL ``CHECK`` as well.
* ``ReasonCode`` is the one open vocabulary.  It is validated in Python at the
  repository boundary, never by a SQL ``CHECK``, because Group 3 adds reason codes
  as it implements rules and a ``CHECK`` would make each addition a migration.

There is no SLA, deadline, due-by or threshold member anywhere in this module, and
a test asserts it (Decision 19).  Aging is ``cursor - date_of_service`` computed at
read time; it is a sort key, never a verdict input.
"""

from __future__ import annotations

from enum import StrEnum

__all__ = [
    "Disposition",
    "ReimbursementTrack",
    "TrackScope",
    "BenefitType",
    "SourceSystem",
    "RecordKind",
    "KeyType",
    "ParkReason",
    "QuarantineReason",
    "CrossTrackFlag",
    "EvidenceRole",
    "ReasonCode",
    "AllocationBasis",
    "UnitBasis",
    "ContractBasis",
]


class Disposition(StrEnum):
    """Exactly three.  Reopened is a flag on the verdict row, never a fourth value.

    Rollup across tracks and up to the episode takes the worst:
    ``EXCEPTION > PENDING > CLOSED`` (Decision 27).
    """

    CLOSED = "CLOSED"        # nothing to do
    PENDING = "PENDING"      # waiting on an external party; no defect
    EXCEPTION = "EXCEPTION"  # a defect exists; work it


class ReimbursementTrack(StrEnum):
    """An episode has exactly one.  Never both, never neither (Decision 1)."""

    PHARMACY = "PHARMACY"
    MEDICAL = "MEDICAL"


class TrackScope(StrEnum):
    """Which track a reason code or disposition is talking about (Decision 27)."""

    REIMBURSEMENT = "REIMBURSEMENT"
    REBATE = "REBATE"
    CROSS_TRACK = "CROSS_TRACK"


class BenefitType(StrEnum):
    """The drug-level partition that drives the episode XOR.

    A drug travels exactly one road: an oral specialty is billed through the
    pharmacy benefit, an infused J-coded drug through the medical benefit.
    """

    PHARMACY_BENEFIT = "PHARMACY_BENEFIT"
    MEDICAL_BENEFIT = "MEDICAL_BENEFIT"


class SourceSystem(StrEnum):
    """Verbatim from ``docs/feed_formats.md``.  These strings appear on the wire."""

    PBM_ADJUDICATION = "PBM_ADJUDICATION"
    PBM_REMITTANCE = "PBM_REMITTANCE"
    TPA_PORTAL = "TPA_PORTAL"
    MANUFACTURER_REBATE = "MANUFACTURER_REBATE"
    CLEARINGHOUSE_837 = "CLEARINGHOUSE_837"
    MEDICAL_REMITTANCE = "MEDICAL_REMITTANCE"
    BANK = "BANK"


class RecordKind(StrEnum):
    """Canonical normalized shapes.

    Parent/child split is real: an 835 is one ``REMITTANCE`` parent with N
    ``REMITTANCE_CLAIM_LINE`` children, and a rebate batch is one ``REBATE_BATCH``
    parent with N ``REBATE_DISPENSE_LINE`` children.  The parent carries the batch
    total, the children carry the claim-level money.

    The ``_LINE`` in ``REMITTANCE_CLAIM_LINE`` means *claim within a batch*, NOT
    *service line within a claim*.  Service lines are embedded arrays on both the
    submission and the remittance side, never their own rows.  There is deliberately
    no ``MEDICAL_SUBMISSION_LINE``: an 837 arrives one-per-claim with nothing
    batching above it, so there is no parent total to split.  An earlier version
    carried that member anyway -- copied from the two genuine parent/child pairs --
    and no adapter ever produced one.

    This list is mirrored by a ``CHECK`` constraint in ``db/schema.sql``; a test
    parses the SQL and asserts the two stay in lockstep.
    """

    PHARMACY_CLAIM = "PHARMACY_CLAIM"
    PHARMACY_REVERSAL = "PHARMACY_REVERSAL"
    REMITTANCE = "REMITTANCE"
    REMITTANCE_CLAIM_LINE = "REMITTANCE_CLAIM_LINE"
    PROVIDER_LEVEL_ADJUSTMENT = "PROVIDER_LEVEL_ADJUSTMENT"
    MEDICAL_SUBMISSION = "MEDICAL_SUBMISSION"
    MEDICAL_ACKNOWLEDGMENT = "MEDICAL_ACKNOWLEDGMENT"
    TPA_QUALIFICATION = "TPA_QUALIFICATION"
    TPA_REBATE_REQUEST = "TPA_REBATE_REQUEST"
    TPA_MANUFACTURER_DECISION = "TPA_MANUFACTURER_DECISION"
    TPA_REVERSAL = "TPA_REVERSAL"
    REBATE_BATCH = "REBATE_BATCH"
    REBATE_DISPENSE_LINE = "REBATE_DISPENSE_LINE"
    BANK_TRANSACTION = "BANK_TRANSACTION"


class KeyType(StrEnum):
    """The eight crosswalk key types (Decision A24), one per bridge in
    ``feed_formats.md`` §5.

    Canonical string forms, pipe-separated, so a key is one indexed lookup on
    ``(key_type, key_value)``:

    ``NCPDP_CLAIM``            ``pharmacy_npi|rx_number|fill_number|date_of_service``
    ``NATURAL_340B_PHARMACY``  ``pharmacy_npi|rx_number|ndc11|fill_date``
    ``NATURAL_340B_MEDICAL``   ``provider_npi|ndc11|service_date`` (Decision 37)
    ``MEDICAL_CLM01`` / ``PAYER_ICN`` / ``TRN02`` / ``ALLOCATION_CODE`` / ``PBM_AUTH``
        the single identifier, verbatim

    Eight, not nine.  The ACH trace number is deliberately **not** a crosswalk key:
    it is banking plumbing with zero business content, always present, and it
    resolves to nothing on its own.  It lives as a column on the bank record, where
    it serves duplicate-delivery (D-1) detection.  A bank line's only business link
    is ``TRN02`` (``feed_formats.md`` §4), which is exactly why losing the addenda
    is fatal rather than inconvenient.

    There is no ``CLP01_PARSED`` either.  A pharmacy 835 carries ``payee_npi`` and
    the service line's ``date_of_service`` alongside ``CLP01``, so parsing
    ``"7845102FILL00"`` apart yields the **whole** ``NCPDP_CLAIM`` key rather than a
    weaker two-field variant.  One key type, one canonical form.

    ``NATURAL_340B_MEDICAL`` is deliberately weaker than its pharmacy sibling: a
    clinic-administered drug has no prescription, so two same-day administrations of
    the same drug at the same site are genuinely ambiguous.  The connector parks the
    ambiguity rather than guessing.
    """

    NCPDP_CLAIM = "NCPDP_CLAIM"
    MEDICAL_CLM01 = "MEDICAL_CLM01"
    PAYER_ICN = "PAYER_ICN"
    TRN02 = "TRN02"
    ALLOCATION_CODE = "ALLOCATION_CODE"
    NATURAL_340B_PHARMACY = "NATURAL_340B_PHARMACY"
    NATURAL_340B_MEDICAL = "NATURAL_340B_MEDICAL"
    PBM_AUTH = "PBM_AUTH"


class ParkReason(StrEnum):
    """Why an inbound document resolved to nothing (Decision 22)."""

    NO_KEY_MATCH = "NO_KEY_MATCH"            # keys present, none resolve
    AMBIGUOUS_KEY_MATCH = "AMBIGUOUS_KEY_MATCH"  # keys resolve to more than one target
    NO_KEYS_PRESENT = "NO_KEYS_PRESENT"      # the document carries no usable key at all


class QuarantineReason(StrEnum):
    """Why a raw record could not be normalized at all (D-5, Decision 33 §3)."""

    MISSING_REQUIRED_FIELD = "MISSING_REQUIRED_FIELD"
    BAD_TYPE = "BAD_TYPE"
    SCHEMA_VERSION_MISMATCH = "SCHEMA_VERSION_MISMATCH"
    UNKNOWN_EVENT_SEMANTICS = "UNKNOWN_EVENT_SEMANTICS"
    UNPARSEABLE = "UNPARSEABLE"


class CrossTrackFlag(StrEnum):
    """X-1..X-7.  Annotations on a verdict pair, never prohibitions.

    All 372 verdict pairs are reachable regardless of which flags fire; the flags say
    *why a pair matters*, not whether it is legal.
    """

    X_1 = "X_1"  # reimbursement denied/rejected + rebate paid -> net negative position
    X_2 = "X_2"  # dispense never happened + rebate paid/approved -> rebate is invalid
    X_3 = "X_3"  # recoupment for retroactive ineligibility + rebate qualified
    X_4 = "X_4"  # reimbursement settled + rebate rejected -> explicitly NOT a compliance flag
    X_5 = "X_5"  # no-cash on both tracks -> one correlated root cause, not two failures
    X_6 = "X_6"  # reimbursement lost + rebate rejected -> total loss, write-off signal
    X_7 = "X_7"  # medical appeal won + rebate previously clawed back -> re-request available

    @property
    def code(self) -> str:
        """The wire/display form: ``X_1`` -> ``"X-1"``."""
        return self.value.replace("_", "-")

    @classmethod
    def from_code(cls, code: str) -> "CrossTrackFlag":
        """Inverse of :attr:`code`: ``"X-1"`` -> ``CrossTrackFlag.X_1``."""
        return cls(code.replace("-", "_"))


class EvidenceRole(StrEnum):
    """What a cited record contributed to a verdict (Decision 32, lineage downward)."""

    ADJUDICATION = "ADJUDICATION"
    REVERSAL = "REVERSAL"
    REMITTANCE_CLAIM_LINE = "REMITTANCE_CLAIM_LINE"
    PROVIDER_LEVEL_ADJUSTMENT = "PROVIDER_LEVEL_ADJUSTMENT"
    BANK_CREDIT = "BANK_CREDIT"
    BANK_DEBIT = "BANK_DEBIT"
    TPA_QUALIFICATION = "TPA_QUALIFICATION"
    REBATE_LINE = "REBATE_LINE"
    MEDICAL_SUBMISSION = "MEDICAL_SUBMISSION"


class ReasonCode(StrEnum):
    """Why a disposition is what it is.  A verdict carries a *list* (Decision 25).

    This is the project's one deliberately open vocabulary.  Group 3 extends it as it
    implements the fifty deterministic rules; the repository validates against this
    enum before any insert, so a typo cannot reach the database, but adding a member
    is a one-line change rather than a schema migration.
    """

    # --- reimbursement -----------------------------------------------------
    REJECTED_AT_POS = "REJECTED_AT_POS"
    AWAITING_REMITTANCE = "AWAITING_REMITTANCE"
    AWAITING_CASH = "AWAITING_CASH"
    UNDERPAID = "UNDERPAID"
    OVERPAID = "OVERPAID"
    DUPLICATE_PAYMENT = "DUPLICATE_PAYMENT"
    CASH_MISMATCH = "CASH_MISMATCH"
    NO_CASH = "NO_CASH"
    SETTLEMENT_MISSING = "SETTLEMENT_MISSING"
    REVERSAL_CASH_NOT_RETURNED = "REVERSAL_CASH_NOT_RETURNED"
    RECOUPMENT_UNTRACEABLE = "RECOUPMENT_UNTRACEABLE"
    ADJUSTMENT_RESIDUAL = "ADJUSTMENT_RESIDUAL"
    CLEARINGHOUSE_REJECTED = "CLEARINGHOUSE_REJECTED"
    DENIED = "DENIED"
    APPEAL_PENDING = "APPEAL_PENDING"
    APPEAL_LOST = "APPEAL_LOST"
    APPEAL_WON_NO_CASH = "APPEAL_WON_NO_CASH"
    DUPLICATE_REMITTANCE = "DUPLICATE_REMITTANCE"

    # --- rebate ------------------------------------------------------------
    REBATE_AWAITING_QUALIFICATION = "REBATE_AWAITING_QUALIFICATION"
    REBATE_NOT_QUALIFIED = "REBATE_NOT_QUALIFIED"
    REBATE_AWAITING_SUBMISSION = "REBATE_AWAITING_SUBMISSION"
    REBATE_AWAITING_MANUFACTURER = "REBATE_AWAITING_MANUFACTURER"
    REBATE_REJECTED = "REBATE_REJECTED"
    REBATE_AWAITING_PAYMENT = "REBATE_AWAITING_PAYMENT"
    REBATE_UNDERPAID = "REBATE_UNDERPAID"
    REBATE_NO_CASH = "REBATE_NO_CASH"
    REBATE_CLAWED_BACK = "REBATE_CLAWED_BACK"
    DUPLICATE_REBATE = "DUPLICATE_REBATE"

    # --- cross-track -------------------------------------------------------
    REBATE_ON_UNDISPENSED_CLAIM = "REBATE_ON_UNDISPENSED_CLAIM"
    DENIED_WITH_REBATE_PAID = "DENIED_WITH_REBATE_PAID"
    QUALIFICATION_UNDERMINED_BY_RECOUPMENT = "QUALIFICATION_UNDERMINED_BY_RECOUPMENT"
    CORRELATED_CASH_GAP = "CORRELATED_CASH_GAP"
    TOTAL_LOSS = "TOTAL_LOSS"
    REBATE_RE_REQUEST_AVAILABLE = "REBATE_RE_REQUEST_AVAILABLE"

    # --- feed-level --------------------------------------------------------
    DUPLICATE_DELIVERY = "DUPLICATE_DELIVERY"
    ORPHAN_DEPOSIT = "ORPHAN_DEPOSIT"
    ORPHAN_REBATE = "ORPHAN_REBATE"
    CROSSWALK_MISS = "CROSSWALK_MISS"
    MALFORMED_RECORD = "MALFORMED_RECORD"
    ALLOCATION_RESIDUAL = "ALLOCATION_RESIDUAL"

    # --- universal ---------------------------------------------------------
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class AllocationBasis(StrEnum):
    """How a bank line was tied to a remittance or an episode (Decision 21).

    ``RESIDUAL`` is the D-7 case: money that arrived and could not be attributed to
    anything more specific than the account it landed in.
    """

    TRN02 = "TRN02"
    ACH_TRACE = "ACH_TRACE"
    ALLOCATION_CODE = "ALLOCATION_CODE"
    AMOUNT_DATE = "AMOUNT_DATE"
    RESIDUAL = "RESIDUAL"


class UnitBasis(StrEnum):
    """The dispensing unit a drug's prices are quoted in."""

    EACH = "EACH"
    ML = "ML"
    MG = "MG"


class ContractBasis(StrEnum):
    """How a contracted allowed amount is derived from list price.

    One member today.  It is a closed vocabulary rather than a free string so that
    adding ``AWP_MINUS_BPS`` later is a visible, reviewed change (Decision 33 §3).
    """

    WAC_MINUS_BPS = "WAC_MINUS_BPS"
