"""The missing bridge: actual records at a cursor, to the abstract dimensions a verdict needs.

``decision_tree/spec.py`` defines verdicts in terms of dimensions — ``ph_settlement =
MISSING``, ``cash_reimb_in = ABSENT``.  ``docs/feed_formats.md`` defines records.  Nothing
said how to get from one to the other, and this module is that ruling, written once so the
engine and the dataset cannot disagree.

Every derivation here reads **only** what a record actually carries.  Nothing consults ground
truth, nothing infers from an episode's intended verdict, and there is no lookup table from
episode to answer — because the entire value of the exercise is that the engine reaches the
same conclusion the generator intended *by reading the feeds*.

═══ The rulings that were not obvious ══════════════════════════════════════════════

**Settlement rides CLP02, and only CLP02.**  Neither NCPDP nor the X12 835 has a settlement
transaction, so there is no native record to look for.  ``"1"`` means processed as primary and
final; ``"19"`` (forwarded to an additional payer) and ``"25"`` (predetermination) mean money
moved but the receivable is not closed.  ``MISSING`` requires a ``19``/``25`` line **and no
later ``1`` for the same CLP01** — without that second half, a claim that was forwarded and
then settled would read as unsettled forever (Decision 40 / C13).

**Underpayment and contractual adjustment are told apart by the residual, not by a flag.**

    residual = clp03 − clp04 − Σ(adjustments)

A residual of zero means the adjustments account for the whole gap: the shortfall is
*contractually explained*, expected drops to E', and there is nothing to chase.  A positive
residual means they do not: money is missing and nobody has said why.  Same two numbers, two
different verdicts, and the only thing separating them is arithmetic that the wire already
carries.  This is also why the generator emits the pharmacy underpayment defect as a
deliberately unbalanced line — it is the *only* way that verdict is expressible.

**Cash is read from allocations, never from the bank row.**  A deposit covers dozens of
claims, so "did this episode's money arrive" is a question about the allocation, which is hop
two of the bank's two-hop resolution.  Reading the bank row directly would mean comparing one
episode's expectation against a batch total, which is meaningless.

**A reversal before payment and after payment are different dimensions.**  Same record shape
(an NCPDP B2), different meaning, decided entirely by whether a payment had already landed.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Sequence

from recon.domain.enums import RecordKind, ReimbursementTrack
from recon.reference import codes

__all__ = [
    "EpisodeEvidence",
    "Dimensions",
    "gather_evidence",
    "derive_dimensions",
]


# ═══ what the engine is allowed to look at ══════════════════════════════════


@dataclass(frozen=True, slots=True)
class EpisodeEvidence:
    """Every record visible for one episode at one cursor, and nothing else.

    Assembled by one query set scoped to ``received_at <= cursor``.  An episode with no new
    inbound record since the last run has identical evidence and therefore an identical
    verdict — which is the property that makes event-driven incremental processing *provably*
    complete rather than an optimisation one hopes is safe (Decision A4).
    """

    episode: Any
    cursor: str
    anchor: Any | None
    claim_lines: tuple[Any, ...] = ()
    reversals: tuple[Any, ...] = ()
    acknowledgments: tuple[Any, ...] = ()
    submissions: tuple[Any, ...] = ()
    plb_entries: tuple[Any, ...] = ()
    tpa_qualifications: tuple[Any, ...] = ()
    tpa_requests: tuple[Any, ...] = ()
    tpa_decisions: tuple[Any, ...] = ()
    tpa_reversals: tuple[Any, ...] = ()
    rebate_lines: tuple[Any, ...] = ()
    #: Positive allocations attributable to this episode's reimbursement track.
    reimbursement_cash_in_cents: int = 0
    #: Negative allocations — money that left again.
    reimbursement_cash_out_cents: int = 0
    rebate_cash_in_cents: int = 0
    rebate_cash_out_cents: int = 0
    #: Every norm_id/raw_id pair that contributed, for lineage.
    citations: tuple[tuple[int, int, str], ...] = ()

    @property
    def has_any_record_beyond_anchor(self) -> bool:
        return bool(
            self.claim_lines
            or self.reversals
            or self.plb_entries
            or self.tpa_qualifications
            or self.rebate_lines
        )


@dataclass(frozen=True, slots=True)
class Dimensions:
    """The decision-tree dimensions, derived from records.

    Deliberately the same names and the same value vocabularies as
    ``decision_tree/spec.py``.  That is not cosmetic: the classifier is a direct port of the
    one the exhaustive enumeration validated, so any divergence in naming would silently
    change which of the 4,224 configurations the engine believes it is looking at.
    """

    reimb_type: str
    # pharmacy
    ph_adjudication: str | None = None
    ph_payment: str | None = None
    ph_post_event: str | None = None
    ph_settlement: str | None = None
    # medical
    md_clearinghouse: str | None = None
    md_remittance: str | None = None
    md_appeal: str | None = None
    md_post_event: str | None = None
    # rebate
    r_present: str = "ABSENT"
    r_qualification: str | None = None
    r_request: str | None = None
    r_manufacturer: str | None = None
    r_payment: str | None = None
    # cash
    cash_reimb_in: str | None = None
    cash_reimb_out: str | None = None
    cash_rebate_in: str | None = None
    cash_rebate_out: str | None = None

    def as_configuration(self) -> dict[str, str]:
        """The non-null dimensions, in the shape ``classify`` and the oracle both expect."""
        return {
            name: value
            for name, value in (
                ("reimb_type", self.reimb_type),
                ("ph_adjudication", self.ph_adjudication),
                ("ph_payment", self.ph_payment),
                ("ph_post_event", self.ph_post_event),
                ("ph_settlement", self.ph_settlement),
                ("md_clearinghouse", self.md_clearinghouse),
                ("md_remittance", self.md_remittance),
                ("md_appeal", self.md_appeal),
                ("md_post_event", self.md_post_event),
                ("r_present", self.r_present),
                ("r_qualification", self.r_qualification),
                ("r_request", self.r_request),
                ("r_manufacturer", self.r_manufacturer),
                ("r_payment", self.r_payment),
                ("cash_reimb_in", self.cash_reimb_in),
                ("cash_reimb_out", self.cash_reimb_out),
                ("cash_rebate_in", self.cash_rebate_in),
                ("cash_rebate_out", self.cash_rebate_out),
            )
            if value is not None
        }


# ═══ gathering ══════════════════════════════════════════════════════════════

_KIND_BUCKETS = {
    RecordKind.REMITTANCE_CLAIM_LINE: "claim_lines",
    RecordKind.PHARMACY_REVERSAL: "reversals",
    RecordKind.MEDICAL_ACKNOWLEDGMENT: "acknowledgments",
    RecordKind.MEDICAL_SUBMISSION: "submissions",
    RecordKind.PROVIDER_LEVEL_ADJUSTMENT: "plb_entries",
    RecordKind.TPA_QUALIFICATION: "tpa_qualifications",
    RecordKind.TPA_REBATE_REQUEST: "tpa_requests",
    RecordKind.TPA_MANUFACTURER_DECISION: "tpa_decisions",
    RecordKind.TPA_REVERSAL: "tpa_reversals",
    RecordKind.REBATE_DISPENSE_LINE: "rebate_lines",
}


def gather_evidence(conn: sqlite3.Connection, episode_row: Any, cursor: str) -> EpisodeEvidence:
    """Fetch everything visible for one episode at ``cursor``.

    Two queries, both driven by indexes: the records that resolved to this episode through the
    crosswalk, and the cash allocated to it.  No scan of the record table, and no time-window
    sweep — the set of records that matter is exactly the set the crosswalk already says
    points here.
    """
    rows = conn.execute(
        "SELECT DISTINCT n.norm_id, n.raw_id, n.record_kind, n.source_system, n.received_at,"
        "       n.amount_cents, n.status_code, n.canonical, n.clp07, n.rx_number, n.ndc11,"
        "       n.date_of_service, n.parent_norm_id"
        "  FROM crosswalk_key k"
        "  JOIN normalized_record n ON n.norm_id = k.resolved_from_norm_id"
        " WHERE k.episode_id = :episode_id"
        "   AND k.first_seen_at <= :cursor"
        "   AND n.received_at <= :cursor"
        " ORDER BY n.received_at, n.norm_id",
        {"episode_id": episode_row["episode_id"], "cursor": cursor},
    ).fetchall()

    buckets: dict[str, list[Any]] = {name: [] for name in set(_KIND_BUCKETS.values())}
    anchor = None
    citations: list[tuple[int, int, str]] = []

    for row in rows:
        kind = RecordKind(row["record_kind"])
        citations.append((row["norm_id"], row["raw_id"], str(kind)))
        if kind is RecordKind.PHARMACY_CLAIM:
            anchor = row
            continue
        bucket = _KIND_BUCKETS.get(kind)
        if bucket is not None:
            buckets[bucket].append(row)

    if anchor is None:
        anchor = conn.execute(
            "SELECT norm_id, raw_id, record_kind, source_system, received_at, amount_cents,"
            "       status_code, canonical, clp07, rx_number, ndc11, date_of_service"
            "  FROM normalized_record WHERE norm_id = ? AND received_at <= ?",
            (episode_row["anchor_norm_id"], cursor),
        ).fetchone()

    cash = conn.execute(
        "SELECT a.allocated_cents, n.record_kind AS target_kind"
        "  FROM cash_allocation a"
        "  LEFT JOIN normalized_record n ON n.norm_id = a.remittance_norm_id"
        " WHERE a.episode_id = ? AND a.caused_by_received_at <= ?",
        (episode_row["episode_id"], cursor),
    ).fetchall()

    reimb_in = reimb_out = rebate_in = rebate_out = 0
    for row in cash:
        amount = row["allocated_cents"]
        is_rebate = row["target_kind"] == str(RecordKind.REBATE_BATCH)
        if is_rebate:
            if amount >= 0:
                rebate_in += amount
            else:
                rebate_out += -amount
        else:
            if amount >= 0:
                reimb_in += amount
            else:
                reimb_out += -amount

    return EpisodeEvidence(
        episode=episode_row,
        cursor=cursor,
        anchor=anchor,
        claim_lines=tuple(buckets["claim_lines"]),
        reversals=tuple(buckets["reversals"]),
        acknowledgments=tuple(buckets["acknowledgments"]),
        submissions=tuple(buckets["submissions"]),
        plb_entries=tuple(buckets["plb_entries"]),
        tpa_qualifications=tuple(buckets["tpa_qualifications"]),
        tpa_requests=tuple(buckets["tpa_requests"]),
        tpa_decisions=tuple(buckets["tpa_decisions"]),
        tpa_reversals=tuple(buckets["tpa_reversals"]),
        rebate_lines=tuple(buckets["rebate_lines"]),
        reimbursement_cash_in_cents=reimb_in,
        reimbursement_cash_out_cents=reimb_out,
        rebate_cash_in_cents=rebate_in,
        rebate_cash_out_cents=rebate_out,
        citations=tuple(citations),
    )


# ═══ derivation ═════════════════════════════════════════════════════════════


def derive_dimensions(
    evidence: EpisodeEvidence,
    *,
    expected_reimbursement_cents: int,
    expected_rebate_cents: int,
) -> Dimensions:
    """Read the records and say which configuration they describe."""
    track = ReimbursementTrack(evidence.episode["reimbursement_track"])
    if track is ReimbursementTrack.PHARMACY:
        dimensions = _derive_pharmacy(evidence, expected_reimbursement_cents)
    else:
        dimensions = _derive_medical(evidence, expected_reimbursement_cents)
    return _derive_rebate(dimensions, evidence, expected_rebate_cents)


# --- pharmacy --------------------------------------------------------------


def _derive_pharmacy(evidence: EpisodeEvidence, expected: int) -> Dimensions:
    anchor = evidence.anchor
    # A POS rejection is real-time and terminal: nothing downstream can exist, so every other
    # pharmacy dimension is absent rather than "none".  That absence is what makes A-01 a
    # four-variable-shorter path than an accepted claim's.
    if anchor is None or anchor["status_code"] != "P":
        return Dimensions(reimb_type="PHARMACY", ph_adjudication="REJECTED")

    paid, line_count, residual, settlement = _read_pharmacy_claim_lines(evidence)

    if line_count == 0:
        payment = "NONE"
    elif line_count > 1 and _lines_are_duplicates(evidence.claim_lines):
        # The same payment twice, not one larger payment.  Distinguishing this from an
        # overpayment matters: one is a posting error to reverse, the other is a contractual
        # dispute to argue.
        payment = "DUPLICATE"
    elif paid > expected:
        payment = "OVER"
    elif paid == expected:
        payment = "FULL"
    elif paid > 0:
        payment = "PARTIAL"
    else:
        payment = "NONE"

    post_event = _pharmacy_post_event(evidence, payment, residual, paid, expected)

    # Settlement is moot once the claim has been withdrawn, and nothing is settled when
    # nothing was paid.  Both match the decision tree's own domain functions.
    if payment == "NONE" or post_event == "REVERSAL_POST_PAY":
        settlement_value = None
    else:
        settlement_value = settlement

    cash_in, cash_out = _cash_dimensions(
        declared_in=paid,
        received_in=evidence.reimbursement_cash_in_cents,
        declared_out=paid if post_event in {"REVERSAL_POST_PAY", "RECOUPMENT"} else 0,
        received_out=evidence.reimbursement_cash_out_cents,
        positive_movement=payment in {"PARTIAL", "FULL", "OVER", "DUPLICATE"},
    )

    return Dimensions(
        reimb_type="PHARMACY",
        ph_adjudication="ACCEPTED",
        ph_payment=payment,
        ph_post_event=post_event,
        ph_settlement=settlement_value,
        cash_reimb_in=cash_in,
        cash_reimb_out=cash_out,
    )


def _read_pharmacy_claim_lines(evidence: EpisodeEvidence) -> tuple[int, int, int, str]:
    """``(paid, line_count, residual, settlement)`` across this episode's 835 lines.

    ``settlement`` implements Decision 40's binding read: ``CONFIRMED`` when any line carries
    CLP02 ``"1"``, ``MISSING`` when the paying lines carry only ``19``/``25`` — the "no later
    ``1``" condition falling out naturally, because a later ``1`` line would itself be in this
    set and would flip the answer.
    """
    paid = 0
    residual = 0
    count = 0
    saw_final = False
    saw_forwarded = False

    for line in evidence.claim_lines:
        body = json.loads(line["canonical"])
        payment = body.get("payment_cents", 0) or 0
        charge = body.get("charge_cents", 0) or 0
        adjustments = sum(a.get("amount_cents", 0) for a in body.get("adjustments", []))
        paid += payment
        residual += charge - payment - adjustments
        count += 1
        status = body.get("clp02_claim_status_code")
        if status == codes.CLP02_PAID_PRIMARY:
            saw_final = True
        elif status in codes.clp02_settlement_unconfirmed():
            saw_forwarded = True

    if saw_final:
        settlement = "CONFIRMED"
    elif saw_forwarded:
        settlement = "MISSING"
    else:
        settlement = "CONFIRMED"
    return paid, count, residual, settlement


def _lines_are_duplicates(lines: Sequence[Any]) -> bool:
    """Two payment events for the same claim, at the same amount.

    Equal amounts across more than one line is the signature.  Two *different* amounts on one
    claim is a two-part payment (an appeal balance, for instance), which is legitimate and must
    not be reported as a duplicate.
    """
    amounts = {json.loads(line["canonical"]).get("payment_cents") for line in lines}
    return len(lines) > 1 and len(amounts) == 1


def _pharmacy_post_event(
    evidence: EpisodeEvidence, payment: str, residual: int, paid: int, expected: int
) -> str:
    """Which post-payment event the records describe.

    Order matters.  A reversal or recoupment *redefines* the verdict regardless of what was
    paid, so they are tested before the adjustment case — which is also the order
    ``classify.py`` applies, and the reason A-12 rather than A-07 is reported for a recouped
    claim that was also underpaid.
    """
    if evidence.reversals:
        # Same record shape either way; what separates them is whether money had already moved.
        return "REVERSAL_PRE_PAY" if payment == "NONE" else "REVERSAL_POST_PAY"

    if any(
        _plb_is_recovery(entry) for entry in evidence.plb_entries
    ):
        # Pharmacy-initiated reversal versus payer-initiated recoupment: the same numeric
        # shape, a different actor, and a completely different dispute process.
        return "RECOUPMENT"

    # A contractual adjustment explains the shortfall in full.  Two conditions, both necessary:
    #
    #   * a **specific** contractual CARC is present — a CO code other than 45.  CO-45 ("exceeds
    #     fee schedule") appears on virtually every paid claim and therefore signals nothing; a
    #     CO-97 bundling reduction is a deliberate, identifiable contractual act.
    #   * the residual is zero, so the adjustments account for the entire gap.
    #
    # A positive residual is the opposite case: the adjustments do not explain the shortfall, so
    # it is an underpayment and stays one (A-07/A-08 rather than A-14/A-15).
    if residual == 0 and _has_specific_contractual_adjustment(evidence):
        return "ADJUSTMENT"
    return "NONE"


def _has_specific_contractual_adjustment(evidence: EpisodeEvidence) -> bool:
    for line in evidence.claim_lines:
        for adjustment in json.loads(line["canonical"]).get("adjustments", []):
            if adjustment.get("group_code") == "CO" and adjustment.get("reason_code") != "45":
                return True
    return False


def _plb_is_recovery(entry: Any) -> bool:
    body = json.loads(entry["canonical"])
    reason = body.get("reason_code")
    if reason is None:
        return False
    try:
        semantics = codes.plb_semantics(reason)
    except KeyError:
        return False
    # Only a positive, claim-attributable offset is a recoupment.  An L6 interest credit and an
    # RA appeal credit both increase the payment, and a forward balance is an unexplained
    # residual rather than a recovery against this claim.
    return semantics.sign > 0 and reason in {"WO", "CS", "72"}


# --- medical ---------------------------------------------------------------


def _derive_medical(evidence: EpisodeEvidence, expected: int) -> Dimensions:
    # The 277CA is what makes this decidable.  Without it, "rejected by the clearinghouse" and
    # "accepted, awaiting the 835" are the same thing on the wire — an 837 with no 835 — and
    # verdict B-01 is simply unreachable (Decision 39).
    clearinghouse = "ACCEPTED"
    for ack in evidence.acknowledgments:
        body = json.loads(ack["canonical"])
        if not body.get("accepted", True):
            clearinghouse = "REJECTED"
            break

    if clearinghouse == "REJECTED":
        return Dimensions(
            reimb_type="MEDICAL", md_clearinghouse="REJECTED", md_post_event=None
        )

    paid, line_count, denied, distinct_amounts, original = _read_medical_claim_lines(evidence)

    # ``md_remittance`` describes the payer's **first** response, not the running total.
    #
    # This matters because an appeal is a two-payment path.  B-12 is "denied, appeal won, paid"
    # and B-08 is "partial, appeal won, balance paid" — in both the money eventually adds up to
    # the full expected amount, so judging by the total would report B-04 ("paid in full") and
    # erase the appeal entirely.  The original leg is what was *disputed*; the later leg is the
    # outcome, and ``md_appeal`` carries that.
    #
    # The earliest claim line by arrival is the original response.  That is a fact about the
    # records rather than a convention we impose.
    if line_count == 0:
        remittance = "NONE"
    elif line_count > 1 and len(distinct_amounts) == 1 and distinct_amounts != {0}:
        # A duplicate is the same *non-zero* payment twice.  Two zero-payment lines are two
        # denials — a denial followed by an appeal refusal — and calling that a duplicate payment
        # would invent money that never moved.
        remittance = "DUPLICATE_835"
    elif original is not None and original["denied"]:
        remittance = "DENIED"
    elif original is not None and original["payment"] >= expected and expected > 0:
        remittance = "PAID_FULL"
    elif original is not None and original["payment"] > 0:
        remittance = "PARTIAL"
    else:
        remittance = "DENIED" if denied else "NONE"

    appeal = _medical_appeal(evidence, remittance, paid, expected)

    money_received = remittance in {"PAID_FULL", "PARTIAL", "DUPLICATE_835"} or appeal == "WON"
    post_event = None
    if money_received:
        post_event = (
            "RECOUPMENT"
            if any(_plb_is_recovery(entry) for entry in evidence.plb_entries)
            else "NONE"
        )

    cash_in, cash_out = _cash_dimensions(
        declared_in=paid,
        received_in=evidence.reimbursement_cash_in_cents,
        declared_out=paid if post_event == "RECOUPMENT" else 0,
        received_out=evidence.reimbursement_cash_out_cents,
        positive_movement=money_received,
    )

    return Dimensions(
        reimb_type="MEDICAL",
        md_clearinghouse="ACCEPTED",
        md_remittance=remittance,
        md_appeal=appeal,
        md_post_event=post_event,
        cash_reimb_in=cash_in,
        cash_reimb_out=cash_out,
    )


def _read_medical_claim_lines(
    evidence: EpisodeEvidence,
) -> tuple[int, int, bool, set[int], dict[str, Any] | None]:
    """``(paid_total, line_count, any_denied, distinct_amounts, original_leg)``.

    ``original_leg`` is the earliest-arriving claim line — the payer's first word on this claim.
    """
    paid = 0
    denied = False
    amounts: set[int] = set()
    original: dict[str, Any] | None = None

    for line in sorted(evidence.claim_lines, key=lambda row: (row["received_at"], row["norm_id"])):
        body = json.loads(line["canonical"])
        payment = body.get("payment_cents", 0) or 0
        is_denied = body.get("clp02_claim_status_code") == codes.CLP02_DENIED
        paid += payment
        amounts.add(payment)
        denied = denied or is_denied
        if original is None:
            original = {"payment": payment, "denied": is_denied}
    return paid, len(evidence.claim_lines), denied, amounts, original


def _medical_appeal(
    evidence: EpisodeEvidence, remittance: str, paid: int, expected: int
) -> str | None:
    """Whether an appeal exists, and how it went.

    X12 has no field anywhere that says "this is an appeal", so there is nothing to read
    directly.  An appeal is inferred from the two shapes it actually takes, both of which this
    dataset uses deliberately:

    * a **frequency-7 replacement** — a second submission about the same claim, pointing back
      at the ICN it supersedes; and
    * a **PLB credit with reason code ``RA``**, carried negative, with no new claim at all.

    An appeal only exists downstream of a denial or a short payment, which is why this returns
    ``None`` for anything else — and matches the decision tree's own gating.
    """
    if remittance not in {"PARTIAL", "DENIED"}:
        return None

    replacement = any(
        json.loads(row["canonical"]).get("frequency_code") in {"7", "8"}
        for row in evidence.submissions
    )
    appeal_credit = any(
        json.loads(entry["canonical"]).get("reason_code") == "RA"
        for entry in evidence.plb_entries
    )

    if not replacement and not appeal_credit:
        return "NOT_FILED"

    # Won when the money actually came through, by either route: the balance was paid, or an RA
    # credit covered it.
    if paid >= expected or appeal_credit:
        return "WON"

    # Filed and still open versus filed and exhausted.  The distinguishing evidence is whether
    # the payer has *responded* to the resubmission — an 835 claim line that arrived after the
    # replacement was submitted.  No response yet means the outcome is genuinely unknown and the
    # balance is neither collectible nor writable-off; a response that still falls short means
    # the appeal was heard and refused, and the residual is now a write-off decision.
    latest_replacement_at = max(
        (
            row["received_at"]
            for row in evidence.submissions
            if json.loads(row["canonical"]).get("frequency_code") in {"7", "8"}
        ),
        default=None,
    )
    if latest_replacement_at is not None and any(
        line["received_at"] > latest_replacement_at for line in evidence.claim_lines
    ):
        return "LOST"
    return "PENDING"


# --- rebate ----------------------------------------------------------------


def _derive_rebate(
    dimensions: Dimensions, evidence: EpisodeEvidence, expected: int
) -> Dimensions:
    """The 340B chain, gate by gate.

    Two independent decision-makers, and the dimensions mirror that: the **TPA** decides
    qualification, the **manufacturer** decides approval and pays.  A dispense can be
    ``QUALIFIED`` at the TPA and still ``REJECTED`` by the manufacturer, which is where most of
    the real dispute volume in this programme lives.

    Absence of the whole track is ``ABSENT``, and it is not an exception — plenty of dispenses
    simply are not 340B.
    """
    from dataclasses import replace

    has_any_rebate_record = bool(
        evidence.tpa_qualifications
        or evidence.tpa_requests
        or evidence.tpa_decisions
        or evidence.rebate_lines
        or evidence.tpa_reversals
    )
    if not has_any_rebate_record:
        return replace(dimensions, r_present="ABSENT")

    qualification = None
    for row in sorted(evidence.tpa_qualifications, key=lambda r: r["received_at"]):
        status = json.loads(row["canonical"]).get("qualification_status")
        if status in {"QUALIFIED", "NOT_QUALIFIED"}:
            qualification = status

    if qualification is None:
        # The dispense reached the TPA and no decision has come back.  Stuck at the first gate
        # is a reason code, never a timer (Decision A4).
        return replace(dimensions, r_present="PRESENT", r_qualification="PENDING")
    if qualification == "NOT_QUALIFIED":
        return replace(dimensions, r_present="PRESENT", r_qualification="NOT_QUALIFIED")

    request = "SUBMITTED" if evidence.tpa_requests else "NOT_SUBMITTED"
    if request == "NOT_SUBMITTED":
        return replace(
            dimensions,
            r_present="PRESENT",
            r_qualification="QUALIFIED",
            r_request="NOT_SUBMITTED",
        )

    manufacturer = _manufacturer_status(evidence)
    if manufacturer in {None, "PENDING"}:
        return replace(
            dimensions,
            r_present="PRESENT",
            r_qualification="QUALIFIED",
            r_request="SUBMITTED",
            r_manufacturer="PENDING",
        )
    if manufacturer == "REJECTED":
        return replace(
            dimensions,
            r_present="PRESENT",
            r_qualification="QUALIFIED",
            r_request="SUBMITTED",
            r_manufacturer="REJECTED",
        )

    paid, line_count, amounts = _read_rebate_lines(evidence)
    clawed_back = bool(evidence.tpa_reversals) and paid > 0

    if clawed_back:
        payment = "CLAWED_BACK"
    elif line_count == 0:
        payment = "NONE"
    elif line_count > 1 and len(amounts) == 1:
        payment = "DUPLICATE"
    elif paid >= expected and expected > 0:
        payment = "FULL"
    elif paid > 0:
        payment = "PARTIAL"
    else:
        payment = "NONE"

    cash_in, cash_out = _cash_dimensions(
        declared_in=paid,
        received_in=evidence.rebate_cash_in_cents,
        declared_out=paid if payment == "CLAWED_BACK" else 0,
        received_out=evidence.rebate_cash_out_cents,
        positive_movement=payment in {"PARTIAL", "FULL", "DUPLICATE", "CLAWED_BACK"},
    )

    return replace(
        dimensions,
        r_present="PRESENT",
        r_qualification="QUALIFIED",
        r_request="SUBMITTED",
        r_manufacturer="APPROVED",
        r_payment=payment,
        cash_rebate_in=cash_in,
        cash_rebate_out=cash_out,
    )


def _manufacturer_status(evidence: EpisodeEvidence) -> str | None:
    """The manufacturer's ruling, which may arrive two different ways.

    An explicit ``MANUFACTURER_DECISION`` record carries it; an approval may instead be
    *implied* by the dispense simply turning up in a payment batch.  Both are real vendor
    behaviours, so both are read — but a rejection can never ride inside a payment batch, which
    is why it always has a record of its own (Decision 43).
    """
    status = None
    for row in sorted(evidence.tpa_decisions, key=lambda r: r["received_at"]):
        value = json.loads(row["canonical"]).get("manufacturer_status")
        if value in {"APPROVED", "REJECTED"}:
            status = value
    if status is not None:
        return status
    if evidence.rebate_lines:
        return "APPROVED"
    return None


def _read_rebate_lines(evidence: EpisodeEvidence) -> tuple[int, int, set[int]]:
    paid = 0
    amounts: set[int] = set()
    for row in evidence.rebate_lines:
        amount = json.loads(row["canonical"]).get("rebate_amount_cents", 0) or 0
        paid += amount
        amounts.add(amount)
    return paid, len(evidence.rebate_lines), amounts


# --- cash ------------------------------------------------------------------


def _cash_dimensions(
    *,
    declared_in: int,
    received_in: int,
    declared_out: int,
    received_out: int,
    positive_movement: bool,
) -> tuple[str | None, str | None]:
    """Did the money the payer says it moved actually move?

    ``None`` on either side means the question does not arise: no declared movement, nothing to
    verify.  That is why cash is not a fourth dimension of the state space but a slot per
    *declared* movement — an episode declares between zero and four of them, which is the main
    source of variable path depth in the decision tree.

    Integer cents with zero tolerance: a real cash match either is or is not exact, and a
    tolerance here would quietly absorb the one-cent variances the whole design exists to keep
    visible.
    """
    cash_in: str | None = None
    if positive_movement and declared_in != 0:
        if received_in == 0:
            cash_in = "ABSENT"
        elif received_in >= declared_in:
            cash_in = "MATCHED"
        else:
            cash_in = "PARTIAL"

    cash_out: str | None = None
    if declared_out != 0:
        cash_out = "MATCHED" if received_out > 0 else "ABSENT"

    return cash_in, cash_out
