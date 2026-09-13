"""When every record arrives.

``received_at`` is the single field no source format carries and every generator adds
(Decision 16).  Every *other* date in this dataset is native and points backward:
``date_of_service`` is when the drug was dispensed, ``payment_effective_date`` is when a
payer committed money to move, a posting date is when a deposit already landed.  None of
them is a promise about the future.

``received_at`` records the order records *showed up* in, as distinct from the order the
underlying events happened in — and that distinction is the only thing that makes replay
meaningful.  It is also why this module exists separately from the generators: arrival
order is a fact about our pipeline, so the orchestrator owns it (Decision 44), and a
generator is handed a timestamp rather than inventing one.

**There is no "now" anywhere in here** (Decision 18).  The timeline is written start to
finish from ``date_of_service``; the cursor lives entirely at read time.  Nothing in this
module reads a clock, and a test asserts it.

═══ The one forward-looking date ═══════════════════════════════════════════════════

The NACHA CCD+ Effective Entry Date is genuinely forward-looking, and it is modelled
rather than pretended away (Decision 17).  It is not a forecast about an uncertain
outcome — it is a settlement instruction about a decision already made and already
transmitted to the ACH network: "this committed money lands on Wednesday", not "we'll
decide by Wednesday".  Commitment, not prediction.

By the time it reaches our side it has resolved anyway: the bank feed shows only
``posting_date``, the day the deposit actually appeared, on or after the effective date.
Even the one legitimately forward date in the dataset is backward-looking by the time it
becomes a row we ingest.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from recon.reference import calendar

__all__ = [
    "Timeline",
    "build_timeline",
    "stamp",
    "LAG_ADJUDICATION_MINUTES",
    "LAG_REMITTANCE_DAYS",
    "LAG_BANK_SETTLEMENT_DAYS",
]

#: A pharmacy adjudication response comes back seconds after the dispense — it is a
#: real-time transaction, which is exactly why a POS rejection is terminal.
LAG_ADJUDICATION_MINUTES = (2, 240)
#: A batched remittance arrives days to weeks later, on its own cycle.
LAG_REMITTANCE_DAYS = (12, 34)
#: ACH settles one to two banking days after the file is built.
LAG_BANK_SETTLEMENT_DAYS = (1, 2)
#: The clearinghouse acknowledges an 837 within about a day.
LAG_ACKNOWLEDGMENT_HOURS = (4, 30)
#: The 340B chain: qualification, then request, then the manufacturer's decision.
LAG_QUALIFICATION_DAYS = (2, 9)
LAG_REQUEST_DAYS = (3, 12)
LAG_DECISION_DAYS = (11, 30)
LAG_REBATE_BATCH_DAYS = (6, 21)
#: A pharmacy reversal happens within days; a payer recoupment lands a cycle or two later.
LAG_REVERSAL_DAYS = (1, 9)
#: An appeal takes a long time, because appeals do.
LAG_APPEAL_DAYS = (25, 70)


def stamp(day: date, *, hour: int = 9, minute: int = 0, second: int = 0) -> str:
    """Render a date as the fixed-width UTC timestamp the whole system uses.

    Fixed width with no offset, so lexicographic order *is* chronological order — which is
    what lets the cursor predicate be a plain string comparison in SQL rather than a date
    function SQLite would have to evaluate per row.
    """
    moment = datetime.combine(day, time(hour=hour, minute=minute, second=second))
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True, slots=True)
class Timeline:
    """Every arrival time for one episode, derived from its configuration.

    ``None`` means the record does not exist for this configuration — an episode awaiting
    its 835 has ``remittance_received_at = None``, and that absence is the *whole content*
    of verdict A-02.  There is no separate "is pending" flag anywhere, because the absence
    of a record is the fact.
    """

    date_of_service: date

    # reimbursement
    claim_received_at: str
    acknowledgment_received_at: str | None
    remittance_due_on: date | None
    reversal_received_at: str | None
    appeal_resolved_on: date | None
    recoupment_due_on: date | None

    # 340B
    qualification_received_at: str | None
    request_received_at: str | None
    submission_date: date | None
    decision_received_at: str | None
    rebate_batch_due_on: date | None
    rebate_reversal_received_at: str | None

    @property
    def date_of_service_iso(self) -> str:
        return self.date_of_service.isoformat()


def build_timeline(
    plan_configuration: dict[str, str],
    *,
    date_of_service: date,
    verdicts: tuple[str, str],
    rejection_reason: str | None,
    rng: random.Random,
) -> Timeline:
    """Derive the arrival schedule for one configuration.

    ``verdicts`` is ``(reimbursement, rebate)`` and is used only to decide *whether* a
    record exists, never to label one — no generator receives this.
    """
    reimbursement_verdict, rebate_verdict = verdicts
    is_pharmacy = plan_configuration["reimb_type"] == "PHARMACY"

    claim_at = stamp(
        date_of_service,
        hour=rng.randint(8, 18),
        minute=rng.randint(0, 59),
        second=rng.randint(0, 59),
    )

    # --- reimbursement -----------------------------------------------------
    acknowledgment_at: str | None = None
    if not is_pharmacy:
        ack_day = date_of_service + timedelta(days=1)
        acknowledgment_at = stamp(ack_day, hour=rng.randint(6, 12), minute=rng.randint(0, 59))

    remittance_due: date | None = None
    if _remittance_exists(plan_configuration, is_pharmacy):
        remittance_due = date_of_service + timedelta(days=rng.randint(*LAG_REMITTANCE_DAYS))

    appeal_resolved: date | None = None
    if plan_configuration.get("md_appeal") in {"WON", "LOST"}:
        base = remittance_due or date_of_service
        appeal_resolved = base + timedelta(days=rng.randint(*LAG_APPEAL_DAYS))
        if plan_configuration.get("md_appeal") == "WON":
            # A won appeal produces a corrected or supplementary 835, which restarts the
            # payment-and-cash sequence rather than amending the original in place.
            remittance_due = appeal_resolved

    reversal_at: str | None = None
    post_event = plan_configuration.get("ph_post_event")
    if post_event in {"REVERSAL_PRE_PAY", "REVERSAL_POST_PAY"}:
        anchor = remittance_due if post_event == "REVERSAL_POST_PAY" else date_of_service
        reversal_day = (anchor or date_of_service) + timedelta(days=rng.randint(*LAG_REVERSAL_DAYS))
        reversal_at = stamp(reversal_day, hour=rng.randint(8, 17), minute=rng.randint(0, 59))

    recoupment_due: date | None = None
    if post_event == "RECOUPMENT" or plan_configuration.get("md_post_event") == "RECOUPMENT":
        # A recoupment is netted out of a *later* batch.  That later-ness is the trap: the
        # bank shows only the smaller net, and the explanation lives in that batch's PLB.
        anchor = remittance_due or date_of_service
        recoupment_due = anchor + timedelta(days=rng.randint(*LAG_REMITTANCE_DAYS))

    # --- 340B --------------------------------------------------------------
    qualification_at: str | None = None
    request_at: str | None = None
    submission_on: date | None = None
    decision_at: str | None = None
    rebate_batch_due: date | None = None
    rebate_reversal_at: str | None = None

    if rebate_verdict != "C-00":
        qualification = plan_configuration.get("r_qualification")
        qualification_day = date_of_service + timedelta(days=rng.randint(*LAG_QUALIFICATION_DAYS))
        if qualification in {"QUALIFIED", "NOT_QUALIFIED"}:
            qualification_at = stamp(qualification_day, hour=rng.randint(8, 16))

        if plan_configuration.get("r_request") == "SUBMITTED":
            if rejection_reason == "NON_CONFORMING_45_DAY":
                # The reason has to be *true*: the submission genuinely lands outside the
                # 45-day window, rather than the feed merely asserting that it did.
                submission_on = date_of_service + timedelta(days=rng.randint(46, 75))
            else:
                submission_on = qualification_day + timedelta(days=rng.randint(*LAG_REQUEST_DAYS))
            request_at = stamp(submission_on + timedelta(days=1), hour=rng.randint(8, 14))

        manufacturer = plan_configuration.get("r_manufacturer")
        if manufacturer in {"APPROVED", "REJECTED"}:
            decision_day = (submission_on or qualification_day) + timedelta(
                days=rng.randint(*LAG_DECISION_DAYS)
            )
            decision_at = stamp(decision_day, hour=rng.randint(6, 12))
            if plan_configuration.get("r_payment") not in {None, "NONE"}:
                rebate_batch_due = decision_day + timedelta(days=rng.randint(*LAG_REBATE_BATCH_DAYS))

        if plan_configuration.get("r_payment") == "CLAWED_BACK":
            anchor = rebate_batch_due or date_of_service
            rebate_reversal_at = stamp(
                anchor + timedelta(days=rng.randint(14, 45)), hour=rng.randint(8, 15)
            )

    return Timeline(
        date_of_service=date_of_service,
        claim_received_at=claim_at,
        acknowledgment_received_at=acknowledgment_at,
        remittance_due_on=remittance_due,
        reversal_received_at=reversal_at,
        appeal_resolved_on=appeal_resolved,
        recoupment_due_on=recoupment_due,
        qualification_received_at=qualification_at,
        request_received_at=request_at,
        submission_date=submission_on,
        decision_received_at=decision_at,
        rebate_batch_due_on=rebate_batch_due,
        rebate_reversal_received_at=rebate_reversal_at,
    )


def _remittance_exists(configuration: dict[str, str], is_pharmacy: bool) -> bool:
    """Whether any 835 claim line exists for this configuration.

    A pharmacy POS rejection is terminal — nothing downstream can exist — and an 837
    rejected by the clearinghouse never reached the payer, so no 835 will ever be issued.
    Both are absences of a record rather than records saying "no".
    """
    if is_pharmacy:
        if configuration.get("ph_adjudication") != "ACCEPTED":
            return False
        return configuration.get("ph_payment") not in {None, "NONE"}
    if configuration.get("md_clearinghouse") != "ACCEPTED":
        return False
    return configuration.get("md_remittance") not in {None, "NONE"}


def settlement_posting_date(effective_date: date, rng: random.Random) -> date:
    """When a deposit actually appears, given the effective date it was instructed for.

    On or after the effective date, never before, and always a banking day: ACH does not
    settle at weekends or on federal holidays, so money instructed for a Friday with a
    holiday behind it lands on the Tuesday.
    """
    drift = rng.randint(0, 1)
    return calendar.add_banking_days(effective_date, drift) if drift else calendar.add_banking_days(
        effective_date, 0
    )
