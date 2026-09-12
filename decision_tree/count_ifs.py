"""
How many if-checks does ONE case actually go through to get a final answer?

Not "how many rules exist" -- how many conditions get evaluated, in order, for a
single episode. The classifiers are early-exit chains, so most cases touch only a
fraction of the rules.

Method: re-run the real classifier logic with a counter incremented on every
condition evaluated.
"""

import json
import os
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))


class Counted:
    """Counts every condition evaluated on the way to a verdict."""

    def __init__(self):
        self.n = 0

    def t(self, cond):
        self.n += 1
        return cond


def pharmacy(r, c, k):
    adj, pay = r.get("ph_adjudication"), r.get("ph_payment")
    post, settle = r.get("ph_post_event"), r.get("ph_settlement")
    cash_in = c.get("cash_reimb_in")
    cash_out = c.get("cash_reimb_out")

    if k.t(adj == "REJECTED"):
        return "A-01"
    if k.t(post == "REVERSAL_PRE_PAY"):
        return "A-16"
    if k.t(post == "REVERSAL_POST_PAY"):
        return "A-10" if k.t(cash_out == "MATCHED") else "A-11"
    if k.t(post == "RECOUPMENT"):
        return "A-12" if k.t(cash_out == "MATCHED") else "A-13"
    if k.t(pay == "NONE"):
        return "A-02"
    if k.t(pay == "DUPLICATE"):
        return "A-17"
    if k.t(pay == "OVER"):
        return "A-09"
    if k.t(pay == "PARTIAL"):
        if k.t(post == "ADJUSTMENT"):
            return "A-14" if k.t(cash_in == "MATCHED") else "A-15"
        if k.t(cash_in == "ABSENT"):
            return "A-08"
        return "A-07"
    if k.t(post == "ADJUSTMENT"):
        return "A-14" if k.t(cash_in == "MATCHED") else "A-15"
    if k.t(cash_in == "ABSENT"):
        return "A-05"
    if k.t(cash_in == "PARTIAL"):
        return "A-07"
    if k.t(settle == "MISSING"):
        return "A-06"
    return "A-04"


def medical(r, c, k):
    ch, rem = r.get("md_clearinghouse"), r.get("md_remittance")
    appeal, post = r.get("md_appeal"), r.get("md_post_event")
    cash_in = c.get("cash_reimb_in")

    if k.t(ch == "REJECTED"):
        return "B-01"
    if k.t(post == "RECOUPMENT"):
        return "B-15"
    if k.t(rem == "NONE"):
        return "B-02"
    if k.t(rem == "DUPLICATE_835"):
        return "B-16"
    if k.t(rem == "PAID_FULL"):
        if k.t(cash_in == "ABSENT"):
            return "B-05"
        if k.t(cash_in == "PARTIAL"):
            return "B-06"
        return "B-04"
    # PARTIAL or DENIED
    if k.t(appeal == "NOT_FILED"):
        return "B-06" if k.t(rem == "PARTIAL") else "B-10"
    if k.t(appeal == "PENDING"):
        return "B-07" if k.t(rem == "PARTIAL") else "B-11"
    if k.t(appeal == "WON"):
        if k.t(cash_in == "ABSENT"):
            return "B-14"
        return "B-08" if k.t(rem == "PARTIAL") else "B-12"
    return "B-09" if k.t(rem == "PARTIAL") else "B-13"


def rebate(b, c, k):
    if k.t(b.get("r_present") == "ABSENT"):
        return "C-00"
    qual, req = b.get("r_qualification"), b.get("r_request")
    mfr, pay = b.get("r_manufacturer"), b.get("r_payment")
    cash_in = c.get("cash_rebate_in")

    if k.t(qual == "PENDING"):
        return "C-01"
    if k.t(qual == "NOT_QUALIFIED"):
        return "C-02"
    if k.t(req == "NOT_SUBMITTED"):
        return "C-03"
    if k.t(mfr == "PENDING"):
        return "C-05"
    if k.t(mfr == "REJECTED"):
        return "C-07"
    if k.t(pay == "NONE"):
        return "C-11"
    if k.t(pay == "CLAWED_BACK"):
        return "C-13"
    if k.t(pay == "DUPLICATE"):
        return "C-14"
    if k.t(pay == "PARTIAL"):
        return "C-10"
    if k.t(cash_in == "ABSENT"):
        return "C-09"
    if k.t(cash_in == "PARTIAL"):
        return "C-10"
    return "C-08"


CROSS_TRACK_CHECKS = 7          # X-1 .. X-7, all evaluated, none short-circuit


def main():
    with open(os.path.join(HERE, "leaves.json")) as f:
        leaves = json.load(f)

    totals, stage1, stage2 = Counter(), Counter(), Counter()

    for leaf in leaves:
        r, b, c = (leaf["reimbursement_track"], leaf["rebate_track"],
                   leaf["cash_verification"])
        k1 = Counted()
        if r.get("reimb_type") == "PHARMACY":
            pharmacy(r, c, k1)
        else:
            medical(r, c, k1)
        k2 = Counted()
        rebate(b, c, k2)

        # +1 for the pharmacy-vs-medical branch at the top
        total = 1 + k1.n + k2.n + CROSS_TRACK_CHECKS
        totals[total] += 1
        stage1[k1.n] += 1
        stage2[k2.n] += 1

    n = len(leaves)
    avg = sum(v * c for v, c in totals.items()) / n

    print("=" * 62)
    print("IF-CHECKS EVALUATED PER CASE (not rules written -- rules RUN)")
    print("=" * 62)
    print(f"cases measured : {n}")
    print(f"minimum        : {min(totals)}")
    print(f"maximum        : {max(totals)}")
    print(f"average        : {avg:.1f}")
    print()
    print("distribution:")
    for v in sorted(totals):
        bar = "#" * max(1, totals[v] * 40 // max(totals.values()))
        print(f"  {v:>3} checks : {totals[v]:>5}  {bar}")
    print()
    print("-" * 62)
    print("per stage:")
    print(f"  stage 0  route pharmacy vs medical :  1 check, always")
    print(f"  stage 1  reimbursement verdict     : {min(stage1)}-{max(stage1)} checks "
          f"(avg {sum(v*c for v,c in stage1.items())/n:.1f})")
    print(f"  stage 2  rebate verdict            : {min(stage2)}-{max(stage2)} checks "
          f"(avg {sum(v*c for v,c in stage2.items())/n:.1f})")
    print(f"  stage 3  cross-track flags         : {CROSS_TRACK_CHECKS} checks, always")
    print("-" * 62)


if __name__ == "__main__":
    main()
