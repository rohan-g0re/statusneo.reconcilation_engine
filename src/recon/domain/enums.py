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
    "TransportKind",
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

    # ── named vendors, added by the connector layer (requirement §4.7) ───────
    #
    # ``TPA_PORTAL`` deliberately stays as it is and is NOT repurposed. It attributes the
    # existing generated 340B feed, which is a *generic* TPA export; re-pointing it at Verity
    # would silently reclassify every record already stored under it, and a source system is
    # how a reader answers "who told us this".
    #
    # Beacon is its own system rather than a flavour of ``MANUFACTURER_REBATE`` because
    # ``DOC2-004`` gives it its own row in the source-of-truth table: it is authoritative for
    # rebate submission identifiers, validation outcomes and rebate status, which is a
    # different authority from the manufacturer's own payment.
    BEACON = "BEACON"
    TPA_VERITY = "TPA_VERITY"
    TPA_CRANEWARE = "TPA_CRANEWARE"


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

    #: Beacon's three inbound shapes, added by the connector layer (``DOC2-007``).
    #:
    #: Beacon sends four payloads and only three are here, because ``rebate_status`` is the
    #: manufacturer's decision under another name and already has a kind —
    #: ``TPA_MANUFACTURER_DECISION``.  A second kind meaning the same event would have made
    #: the engine's evidence map depend on which door the fact arrived through.
    #:
    #: ``BEACON_VALIDATION_OUTCOME`` is separate from that decision and is *not* a
    #: near-duplicate of it.  Beacon validating a submission and a manufacturer refusing to
    #: pay are different judgements by different parties: a Beacon format rejection filed as
    #: a manufacturer decision would be C-07 attributed to the wrong party.
    #:
    #: ``BEACON_PAYMENT_REFERENCE`` is a leaf and deliberately **not** ``REBATE_BATCH`` or
    #: ``REBATE_DISPENSE_LINE``.  ``REBATE_BATCH`` sits in ``pipeline._RESOLUTION_ROOTS``,
    #: where ``_attach`` returns before it processes ``looks_up`` — the Beacon ID lookup
    #: would be silently dropped.  ``REBATE_DISPENSE_LINE`` is worse: ``_read_rebate_lines``
    #: sums across rebate lines, and Beacon's line amount *is* the 340B feed's amount, so
    #: reading one payment through two doors would stamp C-14 "duplicate rebate payment" on
    #: every paid episode in the profile.
    BEACON_ACKNOWLEDGMENT = "BEACON_ACKNOWLEDGMENT"
    BEACON_VALIDATION_OUTCOME = "BEACON_VALIDATION_OUTCOME"
    BEACON_PAYMENT_REFERENCE = "BEACON_PAYMENT_REFERENCE"

    #: One line of a TPA's own rebate invoice -- ``verity_invoices``, the only vendor dataset
    #: that reports money rather than a decision.
    #:
    #: **Not ``REBATE_DISPENSE_LINE``, and the reason is the authority table rather than a
    #: preference.**  ``connectors.authority._REBATE_STATUS`` lists ``REBATE_BATCH`` and
    #: ``REBATE_DISPENSE_LINE`` among its ``record_kinds`` with ``authoritative =
    #: {BEACON, MANUFACTURER_REBATE}``, so a ``TPA_VERITY`` source emitting either is refused
    #: at the kind check before a single field is read: *"a TPA_VERITY source may not assert
    #: rebate status: a REBATE_DISPENSE_LINE record is the claim itself."*  DOC2-004 gives
    #: rebate status to Beacon and the manufacturer, and a TPA's invoice is that TPA telling
    #: us what it billed -- which is a real fact, owned by Verity, about a decision Verity did
    #: not make.
    #:
    #: Both failure modes ``BEACON_PAYMENT_REFERENCE`` names apply here verbatim, which is why
    #: this kind is shaped the same way.  ``REBATE_BATCH`` sits in
    #: ``pipeline._RESOLUTION_ROOTS``, where ``_attach`` returns before it reaches
    #: ``looks_up``, so an invoice line modelled as a batch would resolve to no episode at all.
    #: ``REBATE_DISPENSE_LINE`` is worse: ``dimensions._read_rebate_lines`` sums across rebate
    #: lines, and ``recon.mocks.verity_export`` formats the same generated dispense the 340B
    #: feed already reports -- so one payment read through two doors stamps C-14 "duplicate
    #: rebate payment" on every paid episode.
    #:
    #: Deliberately absent from ``dimensions._KIND_BUCKETS`` and present in
    #: ``engine.run._ROLE_BY_KIND``: gathered, cited, visible on the trace, contributing to no
    #: dimension.
    TPA_INVOICE_LINE = "TPA_INVOICE_LINE"


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

    # ── added by the connector layer (requirement §4.7) ──────────────────────
    #
    # The eight above are Decision A24 and are unchanged; these four are additive, and
    # ``tests/test_decisions.py`` now asserts the two sets separately so that adding one can
    # never quietly redefine the other. Existing members keep their string values, so no
    # stored row changes meaning.
    #
    # Beacon assigns BEACON_ID on submission (``BEACON-013``, ``DOC2-007``), and it is what
    # joins our episode to the manufacturer's rebate decision and that decision to the
    # payment reference on the bank leg (requirement C5).
    BEACON_ID = "BEACON_ID"
    # Beacon permissions are granted per 340B ID (``BEACON-012``, ``DOC2-008``), which is why
    # the covered entity has to be a key and not just a column nobody populates (E1).
    COVERED_ENTITY_340B = "COVERED_ENTITY_340B"
    # ``DOC2-002`` step 4 reads "NDC/HCPCS": a medical-benefit drug is billed by J-code, and
    # modelling only NDC loses it (E2).
    HCPCS = "HCPCS"
    # Named explicitly in ``DOC2-002`` step 4. Today a payment reference exists only as
    # ``TRN02`` or ``allocation_code``, neither of which a manufacturer's own reference is (E3).
    PAYMENT_REFERENCE = "PAYMENT_REFERENCE"


#: Key types the connector layer adds (requirement §4.7).
#:
#: Kept visually separate from Decision A24's original eight above, because the eight are a
#: ratified decision about what a *claim* joins on and these four are about what a *connector*
#: joins on — and ``tests/test_decisions.py`` asserts each set independently so that adding
#: one can never quietly redefine the other.
#:
#: ``BEACON_ID`` is requirement C5: Beacon assigns it on submission, and it is what joins our
#: episode to the manufacturer's rebate decision and that decision to the payment reference on
#: the bank leg.  The other three are wave 5's (E1, E2, E3) and are declared here rather than
#: added later, so the schema's two CHECK lists move exactly once.


class ParkReason(StrEnum):
    """Why an inbound document resolved to nothing (Decision 22)."""

    NO_KEY_MATCH = "NO_KEY_MATCH"            # keys present, none resolve
    AMBIGUOUS_KEY_MATCH = "AMBIGUOUS_KEY_MATCH"  # keys resolve to more than one target
    NO_KEYS_PRESENT = "NO_KEYS_PRESENT"      # the document carries no usable key at all

    #: The keys resolved cleanly to exactly one episode, and the document then contradicted
    #: it: it named a different 340B covered entity (requirement E1).
    #:
    #: A separate reason from ``NO_KEY_MATCH`` because it is a different fact about the
    #: world and a different person's problem.  A key miss means the mapping failed and the
    #: two records are probably the same claim.  This means the mapping *worked* and the two
    #: records disagree about whose 340B claim it is — which is an entitlement question, and
    #: attaching anyway would credit one covered entity's savings to another.
    COVERED_ENTITY_MISMATCH = "COVERED_ENTITY_MISMATCH"


class QuarantineReason(StrEnum):
    """Why a raw record could not be normalized at all (D-5, Decision 33 §3)."""

    MISSING_REQUIRED_FIELD = "MISSING_REQUIRED_FIELD"
    BAD_TYPE = "BAD_TYPE"
    SCHEMA_VERSION_MISMATCH = "SCHEMA_VERSION_MISMATCH"
    UNKNOWN_EVENT_SEMANTICS = "UNKNOWN_EVENT_SEMANTICS"
    UNPARSEABLE = "UNPARSEABLE"

    #: The record parsed and adapted perfectly, and then set a field its source system does
    #: not own (requirement E5, DOC2-004's source-of-truth boundary).
    #:
    #: The file's own declared record count or total did not match what arrived (F2).
    #:
    #: The batch fails, rather than every surviving record being ingested successfully.  That
    #: is the whole point: a truncated feed parses cleanly, so every individual record passes
    #: every other check here and the shortfall is invisible at record level.  It is only
    #: visible as a number the vendor wrote down before sending.
    CONTROL_TOTAL_MISMATCH = "CONTROL_TOTAL_MISMATCH"

    #: Its own reason because the other five all mean "we could not read this", and the
    #: operator's next action for those is to go and look at the bytes.  Here the bytes are
    #: fine and the next action is a conversation about which system is the system of record
    #: — a different fix with a different owner.  Folding it into ``UNPARSEABLE`` would send
    #: someone to read a file that turns out to be perfectly well-formed.
    SOURCE_AUTHORITY_BREACH = "SOURCE_AUTHORITY_BREACH"


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

    #: What a Beacon inbound record was cited as.  Its own role rather than a borrowed one,
    #: because ``engine.run._evidence_rows`` reads ``_ROLE_BY_KIND.get(kind, ADJUDICATION)``
    #: — a kind left out of that table is not rejected, it is silently filed as the pharmacy
    #: adjudication evidence on the episode, which is a wrong citation rather than a missing
    #: one.  Unlike ``record_kind`` this has no SQL ``CHECK`` behind it; ``schema.sql`` says
    #: the column is "validated against domain.enums.EvidenceRole in Python".
    REBATE_SUBMISSION = "REBATE_SUBMISSION"

    #: What a ``TPA_INVOICE_LINE`` was cited as.  Its own role rather than ``REBATE_LINE``,
    #: which belongs to ``REBATE_DISPENSE_LINE`` and means *this is money the engine counted*.
    #: An invoice line is money the engine deliberately did not count -- what the TPA says it
    #: billed, shown beside what the manufacturer actually paid -- and borrowing the rebate
    #: role would put the two on a trace under one name and invite exactly the reading the
    #: separate kind exists to prevent.
    TPA_INVOICE = "TPA_INVOICE"


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


class TransportKind(StrEnum):
    """How a connector gets the bytes.

    Doc 2's page-9 design implication names four production patterns, of which two are
    transports we build (``DOC2-001``): API/SDK, and SFTP/structured files.  The third and
    fourth — healthcare EDI and banking — are *payload* patterns that arrive over one of
    these, which is why this enum has three members rather than four.

    ``LOCAL_DIRECTORY`` is the one that already existed without being named: the generated
    feeds on disk.  Naming it is what turns "the loader reads a directory" into "the loader
    uses a transport, and one of them happens to be a directory" — and it is the member that
    keeps every existing test on the path it was written for.

    Deliberately absent: a ``FetchOutcome`` companion.  That would exist to populate a
    per-fetch history table, and per-fetch history is observability, which is Doc 2 step 6.
    """

    LOCAL_DIRECTORY = "LOCAL_DIRECTORY"
    SFTP = "SFTP"
    HTTP_API = "HTTP_API"
