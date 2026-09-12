"""
Decision-tree specification for the post-claim reconciliation state space.

The tree is traversed depth-first. At each depth we choose a value for one
decision variable. The set of legal values at that depth is a FUNCTION of every
choice made above it -- that is the whole point. We are generating valid paths,
not taking a cross-product.

Three bands, in order:

    depth band 1 : reimbursement track   (pharmacy XOR medical)
    depth band 2 : 340B rebate track
    depth band 3 : cash verification

A variable whose domain function returns an EMPTY LIST is not part of that path
at all. The path simply ends earlier. This is how branches terminate at
different depths -- see VARIABLE_DEPTH_NOTES at the bottom.
"""

# ---------------------------------------------------------------------------
# Band 1a -- pharmacy benefit reimbursement
# ---------------------------------------------------------------------------

def dom_reimb_type(p):
    """Root. Exactly one billing road per claim -- never both, never neither.

    Mandatory because the assignment scopes the prototype to begin *after* a
    claim has been filed.
    """
    return ["PHARMACY", "MEDICAL"]


def dom_ph_adjudication(p):
    if p.get("reimb_type") != "PHARMACY":
        return []
    return ["REJECTED", "ACCEPTED"]


def dom_ph_payment(p):
    # A real-time POS rejection is terminal. Nothing downstream can exist.
    if p.get("ph_adjudication") != "ACCEPTED":
        return []
    return ["NONE", "PARTIAL", "FULL", "OVER", "DUPLICATE"]


def dom_ph_post_event(p):
    if p.get("ph_adjudication") != "ACCEPTED":
        return []
    pay = p.get("ph_payment")
    if pay == "NONE":
        # Cannot claw back money never sent -> no RECOUPMENT, no post-pay reversal.
        return ["NONE", "REVERSAL_PRE_PAY", "ADJUSTMENT"]
    # Money moved, so every post-payment event is reachable.
    return ["NONE", "REVERSAL_POST_PAY", "RECOUPMENT", "ADJUSTMENT"]


def dom_ph_settlement(p):
    if p.get("ph_adjudication") != "ACCEPTED":
        return []
    if p.get("ph_payment") == "NONE":
        return []                                  # nothing paid, nothing to settle
    if p.get("ph_post_event") == "REVERSAL_POST_PAY":
        return []                                  # claim withdrawn, settlement moot
    return ["CONFIRMED", "MISSING"]


def dom_ph_timing(p):
    """Aging is only a decision where it changes the verdict."""
    if p.get("ph_adjudication") != "ACCEPTED":
        return []
    if p.get("ph_post_event") in ("REVERSAL_PRE_PAY", "REVERSAL_POST_PAY"):
        return []                                  # closed by our own action
    if p.get("ph_payment") == "NONE":
        return ["WITHIN_SLA", "PAST_SLA"]          # A-02 vs A-03
    if p.get("ph_settlement") == "MISSING":
        return ["WITHIN_SLA", "PAST_SLA"]          # A-06 exception or not
    return []                                      # paid + settled: aging irrelevant


# ---------------------------------------------------------------------------
# Band 1b -- medical benefit reimbursement
# ---------------------------------------------------------------------------

def dom_md_clearinghouse(p):
    if p.get("reimb_type") != "MEDICAL":
        return []
    return ["REJECTED", "ACCEPTED"]


def dom_md_remittance(p):
    # 837 never reached the payer -> no 835 will ever exist.
    if p.get("md_clearinghouse") != "ACCEPTED":
        return []
    return ["NONE", "PAID_FULL", "PARTIAL", "DENIED", "DUPLICATE_835"]


def dom_md_appeal(p):
    # An appeal only exists downstream of a denial or a short payment.
    if p.get("md_remittance") not in ("PARTIAL", "DENIED"):
        return []
    return ["NOT_FILED", "PENDING", "WON", "LOST"]


def _md_money_received(p):
    if p.get("md_remittance") in ("PAID_FULL", "PARTIAL", "DUPLICATE_835"):
        return True
    return p.get("md_appeal") == "WON"


def dom_md_post_event(p):
    if p.get("reimb_type") != "MEDICAL":
        return []
    if not _md_money_received(p):
        return []                                  # nothing to take back
    return ["NONE", "RECOUPMENT"]


def dom_md_timing(p):
    if p.get("md_clearinghouse") != "ACCEPTED":
        return []
    if p.get("md_remittance") == "NONE":
        return ["WITHIN_SLA", "PAST_SLA"]          # B-02 vs B-03
    if p.get("md_appeal") in ("PENDING", "WON"):
        return ["WITHIN_SLA", "PAST_SLA"]          # B-11, and B-14 if won-but-unpaid
    return []


# ---------------------------------------------------------------------------
# Band 2 -- 340B rebate track
# ---------------------------------------------------------------------------

def dom_r_present(p):
    """Optional per the assignment: a claim *may* create a 340B rebate."""
    return ["ABSENT", "PRESENT"]


def dom_r_qualification(p):
    if p.get("r_present") != "PRESENT":
        return []
    return ["PENDING", "NOT_QUALIFIED", "QUALIFIED"]


def dom_r_request(p):
    # A request presupposes the TPA said the dispense qualifies.
    if p.get("r_qualification") != "QUALIFIED":
        return []
    return ["NOT_SUBMITTED", "SUBMITTED"]


def dom_r_manufacturer(p):
    # The manufacturer cannot rule on a request that was never filed.
    if p.get("r_request") != "SUBMITTED":
        return []
    return ["PENDING", "APPROVED", "REJECTED"]


def dom_r_payment(p):
    # Payment presupposes approval. Rejection and payment are mutually exclusive.
    if p.get("r_manufacturer") != "APPROVED":
        return []
    return ["NONE", "PARTIAL", "FULL", "DUPLICATE", "CLAWED_BACK"]


def dom_r_timing(p):
    if p.get("r_present") != "PRESENT":
        return []
    if p.get("r_qualification") == "PENDING":
        return ["WITHIN_SLA", "PAST_SLA"]          # C-01 stuck at the first gate
    if p.get("r_qualification") == "NOT_QUALIFIED":
        return []                                  # terminal, correct outcome
    if p.get("r_request") == "NOT_SUBMITTED":
        return ["WITHIN_SLA", "PAST_SLA"]          # C-03 vs C-04
    if p.get("r_manufacturer") == "PENDING":
        return ["WITHIN_SLA", "PAST_SLA"]          # C-05 vs C-06
    if p.get("r_manufacturer") == "REJECTED":
        return []                                  # terminal
    if p.get("r_payment") == "NONE":
        return ["WITHIN_SLA", "PAST_SLA"]          # C-11 approved-but-unpaid
    return []


# ---------------------------------------------------------------------------
# Band 3 -- cash verification
#
# NOT a track. One slot per *declared money movement*. An episode can declare
# zero, one, two, three or four movements, which is the main source of variable
# path depth.
# ---------------------------------------------------------------------------

def _reimb_positive_movement(p):
    if p.get("reimb_type") == "PHARMACY":
        return p.get("ph_payment") in ("PARTIAL", "FULL", "OVER", "DUPLICATE")
    return _md_money_received(p)


def _reimb_negative_movement(p):
    if p.get("reimb_type") == "PHARMACY":
        return p.get("ph_post_event") in ("REVERSAL_POST_PAY", "RECOUPMENT")
    return p.get("md_post_event") == "RECOUPMENT"


def dom_cash_reimb_in(p):
    """Did the money the payer says it sent actually land?"""
    if not _reimb_positive_movement(p):
        return []
    return ["MATCHED", "PARTIAL", "ABSENT"]


def dom_cash_reimb_out(p):
    """Did the money we say went back actually leave?"""
    if not _reimb_negative_movement(p):
        return []
    return ["MATCHED", "ABSENT"]


def dom_cash_rebate_in(p):
    if p.get("r_payment") not in ("PARTIAL", "FULL", "DUPLICATE", "CLAWED_BACK"):
        return []
    return ["MATCHED", "PARTIAL", "ABSENT"]


def dom_cash_rebate_out(p):
    if p.get("r_payment") != "CLAWED_BACK":
        return []
    return ["MATCHED", "ABSENT"]


# ---------------------------------------------------------------------------
# Depth order
# ---------------------------------------------------------------------------

VARIABLES = [
    ("reimb_type",        dom_reimb_type,        1),
    ("ph_adjudication",   dom_ph_adjudication,   1),
    ("ph_payment",        dom_ph_payment,        1),
    ("ph_post_event",     dom_ph_post_event,     1),
    ("ph_settlement",     dom_ph_settlement,     1),
    ("ph_timing",         dom_ph_timing,         1),
    ("md_clearinghouse",  dom_md_clearinghouse,  1),
    ("md_remittance",     dom_md_remittance,     1),
    ("md_appeal",         dom_md_appeal,         1),
    ("md_post_event",     dom_md_post_event,     1),
    ("md_timing",         dom_md_timing,         1),
    ("r_present",         dom_r_present,         2),
    ("r_qualification",   dom_r_qualification,   2),
    ("r_request",         dom_r_request,         2),
    ("r_manufacturer",    dom_r_manufacturer,    2),
    ("r_payment",         dom_r_payment,         2),
    ("r_timing",          dom_r_timing,          2),
    ("cash_reimb_in",     dom_cash_reimb_in,     3),
    ("cash_reimb_out",    dom_cash_reimb_out,    3),
    ("cash_rebate_in",    dom_cash_rebate_in,    3),
    ("cash_rebate_out",   dom_cash_rebate_out,   3),
]


# ---------------------------------------------------------------------------
# Cross-track coherence
# ---------------------------------------------------------------------------

def dispense_did_not_happen(p):
    """Pharmacy-side states in which the drug never reached the patient.

    Medical is deliberately excluded: the drug is administered *before* billing,
    so a clearinghouse rejection does not undo the dispense.
    """
    if p.get("reimb_type") != "PHARMACY":
        return False
    if p.get("ph_adjudication") == "REJECTED":
        return True
    return p.get("ph_post_event") in ("REVERSAL_PRE_PAY", "REVERSAL_POST_PAY")


def coherence(p):
    """Return 'COHERENT' or 'ANOMALY:<reason>'.

    Anomalies are generated, not pruned. Data that should be impossible is
    exactly what an exception engine exists to surface.
    """
    if not dispense_did_not_happen(p):
        return "COHERENT"
    if p.get("r_present") == "ABSENT":
        return "COHERENT"
    if p.get("r_qualification") == "NOT_QUALIFIED":
        return "COHERENT"
    if p.get("r_payment") == "CLAWED_BACK":
        return "COHERENT"                          # rebate correctly unwound
    return "ANOMALY:rebate_active_on_undispensed_claim"


# ---------------------------------------------------------------------------
# Cross-track compliance rules (X-1 .. X-7)
# ---------------------------------------------------------------------------

def _reimb_failed(p):
    if p.get("reimb_type") == "PHARMACY":
        return p.get("ph_adjudication") == "REJECTED"
    return (p.get("md_remittance") == "DENIED"
            and p.get("md_appeal") in ("NOT_FILED", "LOST")) \
        or p.get("md_clearinghouse") == "REJECTED"


def _reimb_settled(p):
    if p.get("reimb_type") == "PHARMACY":
        return (p.get("ph_settlement") == "CONFIRMED"
                and p.get("cash_reimb_in") == "MATCHED"
                and p.get("ph_post_event") in ("NONE", "ADJUSTMENT"))
    return (p.get("md_remittance") == "PAID_FULL"
            and p.get("cash_reimb_in") == "MATCHED"
            and p.get("md_post_event") == "NONE")


def _rebate_money_held(p):
    return p.get("r_payment") in ("PARTIAL", "FULL", "DUPLICATE") \
        and p.get("cash_reimb_in") is not None or \
        p.get("r_payment") in ("PARTIAL", "FULL", "DUPLICATE")


def cross_track_flags(p):
    flags = []

    # X-1 reimbursement refused, rebate money held
    if _reimb_failed(p) and p.get("r_payment") in ("PARTIAL", "FULL", "DUPLICATE"):
        flags.append("X-1:denied_with_rebate_paid")

    # X-2 dispense reversed, rebate still standing
    if dispense_did_not_happen(p) and p.get("r_manufacturer") == "APPROVED" \
            and p.get("r_payment") != "CLAWED_BACK":
        flags.append("X-2:reversed_dispense_with_live_rebate")

    # X-3 recoupment for retroactive ineligibility, qualification rests on bad facts
    if p.get("ph_post_event") == "RECOUPMENT" and p.get("r_qualification") == "QUALIFIED":
        flags.append("X-3:recoupment_undermines_qualification")

    # X-4 reimbursement clean, rebate rejected -- explicitly NOT a compliance flag
    if _reimb_settled(p) and p.get("r_manufacturer") == "REJECTED":
        flags.append("X-4:rebate_only_failure_no_escalation")

    # X-5 both tracks paperwork-without-money -> correlated root cause
    if p.get("cash_reimb_in") == "ABSENT" and p.get("cash_rebate_in") == "ABSENT":
        flags.append("X-5:correlated_cash_gap")

    # X-6 total loss on the episode
    if _reimb_failed(p) and (p.get("r_manufacturer") == "REJECTED"
                             or p.get("r_qualification") == "NOT_QUALIFIED"):
        flags.append("X-6:total_loss")

    # X-7 appeal won after the rebate was clawed back -> re-request opportunity
    if p.get("md_appeal") == "WON" and p.get("r_payment") == "CLAWED_BACK":
        flags.append("X-7:appeal_won_after_clawback")

    return flags


VARIABLE_DEPTH_NOTES = """
Branches terminate at different depths. Three independent causes:

1. Track short-circuit. A pharmacy POS rejection (ph_adjudication=REJECTED) kills
   payment, post_event, settlement and timing in one stroke. That path is 4
   variables shorter than an accepted claim's.

2. Gated chains. The 340B track is a strict chain -- qualification gates request,
   request gates manufacturer, manufacturer gates payment. NOT_QUALIFIED stops at
   depth 2 of the band; a paid rebate runs the full 5.

3. Variable cash arity. Cash verification is one slot per DECLARED MONEY
   MOVEMENT, and an episode declares between 0 and 4 of them:
       0 -- rejected claim, no rebate            (nothing to verify)
       1 -- paid claim, no rebate
       2 -- paid claim + paid rebate
       3 -- paid claim + recoupment + paid rebate
       4 -- + rebate clawback
   Cash is therefore not a fixed depth at all. It is a repeated slot whose count
   is determined by bands 1 and 2.
"""
