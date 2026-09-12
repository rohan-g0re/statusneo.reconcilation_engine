"""
Collapse the 7046 generated configurations onto the hand-curated states in
docs/reconciliation_state_space.md (A-01..A-17, B-01..B-17, C-00..C-14).

Purpose: test whether 510 was a wrong count or a different unit of measurement.

If every configuration maps cleanly onto a curated state, then 510 counted
VERDICTS and 7046 counts CONFIGURATIONS -- both correct, different questions.
If some configurations map to nothing, the curated table has real gaps and we
want them listed by name.
"""

import json
import os
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))

UNMAPPED = "UNMAPPED"


# ---------------------------------------------------------------------------
# Pharmacy configuration -> A-xx
# ---------------------------------------------------------------------------

def classify_pharmacy(r, c):
    adj = r.get("ph_adjudication")
    pay = r.get("ph_payment")
    post = r.get("ph_post_event")
    settle = r.get("ph_settlement")
    timing = r.get("ph_timing")
    cash_in = c.get("cash_reimb_in")
    cash_out = c.get("cash_reimb_out")

    if adj == "REJECTED":
        return "A-01"

    # -- post-payment events take precedence: they redefine the verdict ----
    if post == "REVERSAL_PRE_PAY":
        return "A-16"
    if post == "REVERSAL_POST_PAY":
        return "A-10" if cash_out == "MATCHED" else "A-11"
    if post == "RECOUPMENT":
        return "A-12" if cash_out == "MATCHED" else "A-13"

    if pay == "NONE":
        if post == "ADJUSTMENT":
            # Expected reduced but still nothing received: aging still governs.
            return "A-02" if timing == "WITHIN_SLA" else "A-03"
        return "A-02" if timing == "WITHIN_SLA" else "A-03"

    if pay == "DUPLICATE":
        return "A-17"
    if pay == "OVER":
        return "A-09"

    if pay == "PARTIAL":
        if post == "ADJUSTMENT":
            # Shortfall explained by contract -> A-14; otherwise residual -> A-15
            return "A-14" if cash_in == "MATCHED" else "A-15"
        if cash_in == "ABSENT":
            return "A-08"
        return "A-07"

    if pay == "FULL":
        if post == "ADJUSTMENT":
            return "A-14" if cash_in == "MATCHED" else "A-15"
        if cash_in == "ABSENT":
            return "A-05"
        if cash_in == "PARTIAL":
            return "A-07"          # payer said full, bank shows less -> shortfall
        if settle == "MISSING":
            return "A-06"
        return "A-04"

    return UNMAPPED


# ---------------------------------------------------------------------------
# Medical configuration -> B-xx
# ---------------------------------------------------------------------------

def classify_medical(r, c):
    ch = r.get("md_clearinghouse")
    rem = r.get("md_remittance")
    appeal = r.get("md_appeal")
    post = r.get("md_post_event")
    timing = r.get("md_timing")
    cash_in = c.get("cash_reimb_in")
    cash_out = c.get("cash_reimb_out")

    if ch == "REJECTED":
        return "B-01"

    if post == "RECOUPMENT":
        return "B-15"

    if rem == "NONE":
        return "B-02" if timing == "WITHIN_SLA" else "B-03"

    if rem == "DUPLICATE_835":
        return "B-16"

    if rem == "PAID_FULL":
        if cash_in == "ABSENT":
            return "B-05"
        if cash_in == "PARTIAL":
            return "B-06"
        return "B-04"

    if rem == "PARTIAL":
        if appeal == "NOT_FILED":
            return "B-06"
        if appeal == "PENDING":
            return "B-07"
        if appeal == "WON":
            if cash_in == "ABSENT":
                return "B-14"
            return "B-08"
        if appeal == "LOST":
            return "B-09"

    if rem == "DENIED":
        if appeal == "NOT_FILED":
            return "B-10"
        if appeal == "PENDING":
            return "B-11"
        if appeal == "WON":
            if cash_in == "ABSENT":
                return "B-14"
            return "B-12"
        if appeal == "LOST":
            return "B-13"

    return UNMAPPED


# ---------------------------------------------------------------------------
# 340B configuration -> C-xx
# ---------------------------------------------------------------------------

def classify_rebate(b, c):
    if b.get("r_present") == "ABSENT":
        return "C-00"

    qual = b.get("r_qualification")
    req = b.get("r_request")
    mfr = b.get("r_manufacturer")
    pay = b.get("r_payment")
    timing = b.get("r_timing")
    cash_in = c.get("cash_rebate_in")

    if qual == "PENDING":
        if timing == "PAST_SLA":
            return "C-01"
        return "C-GAP-qualification-pending-within-sla"

    if qual == "NOT_QUALIFIED":
        return "C-02"

    if req == "NOT_SUBMITTED":
        return "C-03" if timing == "WITHIN_SLA" else "C-04"

    if mfr == "PENDING":
        return "C-05" if timing == "WITHIN_SLA" else "C-06"
    if mfr == "REJECTED":
        return "C-07"

    if mfr == "APPROVED":
        if pay == "NONE":
            if timing == "PAST_SLA":
                return "C-11"
            return "C-GAP-approved-unpaid-within-sla"
        if pay == "CLAWED_BACK":
            return "C-13"
        if pay == "DUPLICATE":
            return "C-14"
        if pay == "PARTIAL":
            return "C-10"
        if pay == "FULL":
            if cash_in == "ABSENT":
                return "C-09"
            if cash_in == "PARTIAL":
                return "C-10"
            return "C-08"

    return UNMAPPED


# ---------------------------------------------------------------------------

def main():
    with open(os.path.join(HERE, "leaves.json")) as f:
        leaves = json.load(f)

    reimb_states = Counter()
    rebate_states = Counter()
    pairs = set()
    pair_counts = Counter()
    unmapped = []
    gaps = defaultdict(list)

    for leaf in leaves:
        r = leaf["reimbursement_track"]
        b = leaf["rebate_track"]
        c = leaf["cash_verification"]

        if r.get("reimb_type") == "PHARMACY":
            rs = classify_pharmacy(r, c)
        else:
            rs = classify_medical(r, c)
        bs = classify_rebate(b, c)

        leaf["curated_reimbursement_state"] = rs
        leaf["curated_rebate_state"] = bs

        reimb_states[rs] += 1
        rebate_states[bs] += 1
        pairs.add((rs, bs))
        pair_counts[(rs, bs)] += 1

        if rs == UNMAPPED or bs == UNMAPPED:
            unmapped.append(leaf["case_id"])
        if rs.startswith(("A-GAP", "B-GAP")) or bs.startswith("C-GAP"):
            key = rs if rs.startswith(("A-GAP", "B-GAP")) else bs
            gaps[key].append(leaf["case_id"])

    with open(os.path.join(HERE, "leaves_classified.json"), "w") as f:
        json.dump(leaves, f, indent=1)

    curated_reimb = sorted(s for s in reimb_states if s.startswith(("A-", "B-"))
                           and "GAP" not in s)
    curated_rebate = sorted(s for s in rebate_states if s.startswith("C-")
                            and "GAP" not in s)

    print("=" * 72)
    print("CLASSIFICATION: 7046 configurations -> curated verdicts")
    print("=" * 72)
    print(f"configurations classified   : {len(leaves)}")
    print(f"unmapped (classifier holes) : {len(unmapped)}")
    print()
    print(f"distinct reimbursement verdicts reached : {len(curated_reimb)}  "
          f"(doc claims 34)")
    print(f"distinct rebate verdicts reached        : {len(curated_rebate)}  "
          f"(doc claims 15)")
    print(f"distinct (reimb, rebate) PAIRS reached  : {len(pairs)}")
    print()

    print("-- reimbursement verdict frequency " + "-" * 37)
    for s in sorted(reimb_states, key=lambda x: (x.startswith("A-GAP"), x)):
        print(f"    {s:<45} {reimb_states[s]:>6}")
    print()
    print("-- rebate verdict frequency " + "-" * 44)
    for s in sorted(rebate_states):
        print(f"    {s:<45} {rebate_states[s]:>6}")
    print()

    missing_reimb = ([f"A-{i:02d}" for i in range(1, 18)]
                     + [f"B-{i:02d}" for i in range(1, 18)])
    missing_reimb = [s for s in missing_reimb if s not in reimb_states]
    missing_rebate = [f"C-{i:02d}" for i in range(0, 15)]
    missing_rebate = [s for s in missing_rebate if s not in rebate_states]

    print("-- curated states NEVER produced by the tree " + "-" * 27)
    print(f"    reimbursement : {missing_reimb or 'none'}")
    print(f"    rebate        : {missing_rebate or 'none'}")
    print()
    print("-- states the tree produced that the doc DOES NOT contain " + "-" * 14)
    if gaps:
        for k in sorted(gaps):
            print(f"    {k:<50} {len(gaps[k]):>6} configs")
    else:
        print("    none")
    print()

    if unmapped:
        print(f"-- UNMAPPED sample: {unmapped[:10]}")
        print()

    print("=" * 72)
    print("RECONCILIATION AGAINST THE HAND-COUNTED 510")
    print("=" * 72)
    all_reimb = sorted(reimb_states)
    all_rebate = sorted(rebate_states)          # includes the two GAP states
    product = len(all_reimb) * len(all_rebate)
    print(f"reachable reimbursement verdicts : {len(all_reimb)}   (hand-count: 34)")
    print(f"reachable rebate verdicts        : {len(all_rebate)}   (hand-count: 15)")
    print(f"product                          : {len(all_reimb)} x "
          f"{len(all_rebate)} = {product}")
    print(f"pairs actually reached           : {len(pairs)}")
    print(f"hand-counted figure              : 510")
    print()
    if product == len(pairs):
        print("  Product is EXACT -- every verdict pair is reachable, so the two")
        print("  tracks are independent at the verdict level and all cross-track")
        print("  rules are annotations rather than prohibitions.")
    print()
    print("  Hand-count corrections:")
    print("    -1 reimbursement : B-17 is not a distinct verdict (never generated)")
    print("    -1 rebate        : C-12 is feed-level, not an episode state")
    print("    +2 rebate        : within-SLA halves of C-01 and C-11 were missing")
    print(f"    34 x 15 = 510  ->  {len(all_reimb)} x {len(all_rebate)} = {product}")
    print("=" * 72)

    with open(os.path.join(HERE, "pairs.json"), "w") as f:
        json.dump(
            [{"reimbursement": a, "rebate": b, "configurations": pair_counts[(a, b)]}
             for a, b in sorted(pairs)],
            f, indent=1)


if __name__ == "__main__":
    main()
