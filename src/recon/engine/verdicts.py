"""The 43 track rules and the 7 cross-track rules.  50 rules, 372 outcomes.

This is a deliberate, faithful port of ``decision_tree/classify.py`` — the classifier that
exhaustive enumeration ran over all 4,224 valid configurations to establish that 372 of a
possible 372 verdict pairs are reachable.  The port is line-for-line where it can be, and the
reason is blunt: the oracle proves the *classifier*, so a reimplementation that "cleans up"
the branch order is no longer the thing that was proved.

``tests`` run this module against ``decision_tree/leaves_classified.json`` and assert it
reproduces the classification of every one of the 4,224 configurations.  Any divergence is a
failure here, not a stale oracle.

═══ Why this is 50 rules and not 372 branches ══════════════════════════════════════

The engine resolves each track independently, then annotates:

    episode = compose(reimbursement_verdict, rebate_verdict, cross_track_flags)

16 pharmacy rules + 15 medical rules + 12 rebate rules = 43 track rules, plus 7 cross-track
rules.  Those 50 generate all 372 pairs compositionally, evaluated as 10–32 if-checks per
episode (averaging 21).  That compositional property is the difference between an architecture
and a switch statement, and it is only available because the tracks turned out to be fully
independent at the verdict level — which is a *measured* result, not an assumption.

═══ Why the cross-track rules annotate rather than prohibit ════════════════════════

Because all 372 pairs are reachable, no combination is invalid, so there is no validity table
to enforce and nothing for a cross-track rule to forbid.  What they do instead is say *why a
pair matters* — and X-4 exists specifically to say that one common pair matters **not at all**,
so the engine does not over-report it.
"""

from __future__ import annotations

from typing import Mapping

__all__ = [
    "classify_reimbursement",
    "classify_pharmacy",
    "classify_medical",
    "classify_rebate",
    "cross_track_flags",
    "dispense_did_not_happen",
    "coherence",
]


# ═══ Track A — pharmacy benefit (16 rules) ══════════════════════════════════


def classify_pharmacy(configuration: Mapping[str, str]) -> str:
    """Pharmacy reimbursement verdict, A-01..A-17 (A-03 retired).

    Branch order is load-bearing.  Post-payment events are tested **before** the payment
    amount, because a reversal or a recoupment *redefines* the verdict: a claim that was
    underpaid and then recouped is A-12, not A-07.  Reordering these would silently reclassify
    whole populations of episodes.
    """
    adjudication = configuration.get("ph_adjudication")
    payment = configuration.get("ph_payment")
    post = configuration.get("ph_post_event")
    settlement = configuration.get("ph_settlement")
    cash_in = configuration.get("cash_reimb_in")
    cash_out = configuration.get("cash_reimb_out")

    # A POS rejection is real-time and terminal.  Nothing downstream can exist.
    if adjudication == "REJECTED":
        return "A-01"

    # Post-payment events take precedence: they redefine the verdict.
    if post == "REVERSAL_PRE_PAY":
        return "A-16"
    if post == "REVERSAL_POST_PAY":
        # Did the money we say went back actually leave?  A-11 means the books still show cash
        # we are not entitled to.
        return "A-10" if cash_out == "MATCHED" else "A-11"
    if post == "RECOUPMENT":
        # A-13 cannot confirm the clawback happened at all, so it may be double-counted —
        # which is why it carries INSUFFICIENT_DATA rather than a money figure.
        return "A-12" if cash_out == "MATCHED" else "A-13"

    if payment == "NONE":
        # Aged at read time for queue ordering, never re-verdicted for the passage of time.
        return "A-02"

    if payment == "DUPLICATE":
        return "A-17"
    if payment == "OVER":
        return "A-09"

    if payment == "PARTIAL":
        if post == "ADJUSTMENT":
            # The shortfall is contractually explained (A-14); when the cash then fails to
            # match, the residual reappears as A-15.
            return "A-14" if cash_in == "MATCHED" else "A-15"
        if cash_in == "ABSENT":
            return "A-08"
        return "A-07"

    if payment == "FULL":
        if post == "ADJUSTMENT":
            return "A-14" if cash_in == "MATCHED" else "A-15"
        if cash_in == "ABSENT":
            return "A-05"
        if cash_in == "PARTIAL":
            # The payer said it paid in full and the bank shows less.  That is a shortfall,
            # whatever the remittance claims.
            return "A-07"
        if settlement == "MISSING":
            return "A-06"
        return "A-04"

    raise ValueError(f"unclassifiable pharmacy configuration: {dict(configuration)!r}")


# ═══ Track B — medical benefit (15 rules) ═══════════════════════════════════


def classify_medical(configuration: Mapping[str, str]) -> str:
    """Medical reimbursement verdict, B-01..B-16 (B-03 and B-17 retired)."""
    clearinghouse = configuration.get("md_clearinghouse")
    remittance = configuration.get("md_remittance")
    appeal = configuration.get("md_appeal")
    post = configuration.get("md_post_event")
    cash_in = configuration.get("cash_reimb_in")

    # The payer never saw it, so there is no denial to appeal — resubmission is the action.
    # The drug was still administered, which is why 340B may legitimately exist here.
    if clearinghouse == "REJECTED":
        return "B-01"

    if post == "RECOUPMENT":
        return "B-15"

    if remittance == "NONE":
        return "B-02"

    if remittance == "DUPLICATE_835":
        return "B-16"

    if remittance == "PAID_FULL":
        if cash_in == "ABSENT":
            return "B-05"
        if cash_in == "PARTIAL":
            return "B-06"
        return "B-04"

    if remittance == "PARTIAL":
        if appeal == "NOT_FILED":
            return "B-06"
        if appeal == "PENDING":
            return "B-07"
        if appeal == "WON":
            # The payer agreed and still did not pay: the highest-value chase in the system.
            return "B-14" if cash_in == "ABSENT" else "B-08"
        if appeal == "LOST":
            return "B-09"

    if remittance == "DENIED":
        if appeal == "NOT_FILED":
            return "B-10"
        if appeal == "PENDING":
            return "B-11"
        if appeal == "WON":
            return "B-14" if cash_in == "ABSENT" else "B-12"
        if appeal == "LOST":
            return "B-13"

    raise ValueError(f"unclassifiable medical configuration: {dict(configuration)!r}")


def classify_reimbursement(configuration: Mapping[str, str]) -> str:
    """Route to the right track.  One check, always — stage zero of the 21-check average."""
    if configuration.get("reimb_type") == "PHARMACY":
        return classify_pharmacy(configuration)
    return classify_medical(configuration)


# ═══ Track C — 340B rebate (12 rules) ═══════════════════════════════════════


def classify_rebate(configuration: Mapping[str, str]) -> str:
    """Rebate verdict, C-00..C-14 (C-04, C-06, C-12 retired).

    A strict chain, because the real programme is one: qualification gates the request, the
    request gates the manufacturer's decision, the decision gates payment.  Two independent
    decision-makers sit on that chain — the TPA qualifies, the manufacturer approves and pays —
    and a dispense can be QUALIFIED at the TPA and still REJECTED by the manufacturer.
    """
    if configuration.get("r_present") == "ABSENT":
        # Absence is not an exception.  Plenty of dispenses simply are not 340B.
        return "C-00"

    qualification = configuration.get("r_qualification")
    request = configuration.get("r_request")
    manufacturer = configuration.get("r_manufacturer")
    payment = configuration.get("r_payment")
    cash_in = configuration.get("cash_rebate_in")

    if qualification == "PENDING":
        return "C-01"
    if qualification == "NOT_QUALIFIED":
        # A correct outcome, not a failure: the dispense genuinely did not qualify.
        return "C-02"

    if request == "NOT_SUBMITTED":
        return "C-03"

    if manufacturer == "PENDING":
        return "C-05"
    if manufacturer == "REJECTED":
        # Not a TPA problem — the qualification stood and the manufacturer disputed it.
        return "C-07"

    if manufacturer == "APPROVED":
        if payment == "NONE":
            return "C-11"
        if payment == "CLAWED_BACK":
            return "C-13"
        if payment == "DUPLICATE":
            return "C-14"
        if payment == "PARTIAL":
            return "C-10"
        if payment == "FULL":
            if cash_in == "ABSENT":
                # The TPA's ledger and the bank disagree.
                return "C-09"
            if cash_in == "PARTIAL":
                return "C-10"
            return "C-08"

    raise ValueError(f"unclassifiable rebate configuration: {dict(configuration)!r}")


# ═══ Cross-track (7 rules) ══════════════════════════════════════════════════


def dispense_did_not_happen(configuration: Mapping[str, str]) -> bool:
    """Pharmacy states in which the drug never reached the patient.

    Medical is deliberately excluded, and the asymmetry is real: an infused drug is
    administered *before* billing, so a clearinghouse rejection or a denial does not undo the
    dispense.  That is why all 15 medical states are compatible with a live 340B track and only
    four pharmacy states are not.
    """
    if configuration.get("reimb_type") != "PHARMACY":
        return False
    if configuration.get("ph_adjudication") == "REJECTED":
        return True
    return configuration.get("ph_post_event") in {"REVERSAL_PRE_PAY", "REVERSAL_POST_PAY"}


def _reimbursement_failed(configuration: Mapping[str, str]) -> bool:
    if configuration.get("reimb_type") == "PHARMACY":
        return configuration.get("ph_adjudication") == "REJECTED"
    return (
        configuration.get("md_remittance") == "DENIED"
        and configuration.get("md_appeal") in {"NOT_FILED", "LOST"}
    ) or configuration.get("md_clearinghouse") == "REJECTED"


def _reimbursement_settled(configuration: Mapping[str, str]) -> bool:
    if configuration.get("reimb_type") == "PHARMACY":
        return (
            configuration.get("ph_settlement") == "CONFIRMED"
            and configuration.get("cash_reimb_in") == "MATCHED"
            and configuration.get("ph_post_event") in {"NONE", "ADJUSTMENT"}
        )
    return (
        configuration.get("md_remittance") == "PAID_FULL"
        and configuration.get("cash_reimb_in") == "MATCHED"
        and configuration.get("md_post_event") == "NONE"
    )


def cross_track_flags(configuration: Mapping[str, str]) -> list[str]:
    """X-1..X-7.  Seven checks, always, on every episode.

    These fire on the *combination* of two track verdicts, and they are the reason the episode
    rather than the track is the unit of reconciliation.  Each one is a story a single-track
    system cannot tell.
    """
    flags: list[str] = []
    rebate_paid = configuration.get("r_payment") in {"PARTIAL", "FULL", "DUPLICATE"}

    # X-1 — the payer refused the claim and we are holding rebate money against it.  Net
    # position is negative and the rebate rests on a claim the payer would not pay.
    if _reimbursement_failed(configuration) and rebate_paid:
        flags.append("X-1")

    # X-2 — the drug never reached the patient, so the qualifying event does not exist.  The
    # rebate is not merely unmatched, it is *invalid*, and it has to be unwound proactively
    # rather than discovered by the manufacturer in an audit.
    if (
        dispense_did_not_happen(configuration)
        and configuration.get("r_manufacturer") == "APPROVED"
        and configuration.get("r_payment") != "CLAWED_BACK"
    ):
        flags.append("X-2")

    # X-3 — qualification consumed claim facts that are now known to be wrong.
    if (
        configuration.get("ph_post_event") == "RECOUPMENT"
        and configuration.get("r_qualification") == "QUALIFIED"
    ):
        flags.append("X-3")

    # X-4 — explicitly NOT a compliance problem.  The reimbursement side is clean and only the
    # rebate failed; it is common, and flagging it would train the operator to ignore flags.
    if _reimbursement_settled(configuration) and configuration.get("r_manufacturer") == "REJECTED":
        flags.append("X-4")

    # X-5 — paperwork-without-money on both tracks at once.  Almost certainly not two payers
    # independently failing: far more likely a bank feed gap or an allocation defect on our own
    # side, which is why this carries INSUFFICIENT_DATA rather than two separate chases.
    if (
        configuration.get("cash_reimb_in") == "ABSENT"
        and configuration.get("cash_rebate_in") == "ABSENT"
    ):
        flags.append("X-5")

    # X-6 — total loss: cost of goods unrecovered from every available channel.
    if _reimbursement_failed(configuration) and (
        configuration.get("r_manufacturer") == "REJECTED"
        or configuration.get("r_qualification") == "NOT_QUALIFIED"
    ):
        flags.append("X-6")

    # X-7 — the claim was valid after all, so the basis for the clawback may be void.
    # Recoverable money nobody is watching.
    if (
        configuration.get("md_appeal") == "WON"
        and configuration.get("r_payment") == "CLAWED_BACK"
    ):
        flags.append("X-7")

    return flags


def coherence(configuration: Mapping[str, str]) -> str:
    """``COHERENT``, or ``ANOMALY:<reason>``.

    Anomalies are *surfaced*, never pruned.  Data that should be impossible is precisely what
    an exception engine exists to find, and the 364 anomalous configurations are the highest
    value cases in the system rather than noise to filter out.
    """
    if not dispense_did_not_happen(configuration):
        return "COHERENT"
    if configuration.get("r_present") == "ABSENT":
        return "COHERENT"
    if configuration.get("r_qualification") == "NOT_QUALIFIED":
        return "COHERENT"
    if configuration.get("r_payment") == "CLAWED_BACK":
        return "COHERENT"  # the rebate was correctly unwound
    return "ANOMALY:rebate_active_on_undispensed_claim"
