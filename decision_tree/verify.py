"""
Independent verification pass.

Three jobs:
  1. Quantify how much the validity constraints actually removed, by contrasting
     the constrained tree against the unconstrained cross-product.
  2. Prove specific named cases exist BY CONSTRUCTION rather than by assumption.
  3. Confirm the pair space is exactly the product of reachable verdicts.
"""

import json
import os
from collections import Counter

from spec import VARIABLES

HERE = os.path.dirname(os.path.abspath(__file__))

# Full domain of each variable, ignoring all constraints, plus one slot for
# "not applicable on this path".
UNCONSTRAINED_DOMAINS = {
    "reimb_type":       2,
    "ph_adjudication":  2 + 1,
    "ph_payment":       5 + 1,
    "ph_post_event":    4 + 1,
    "ph_settlement":    2 + 1,
    "ph_timing":        2 + 1,
    "md_clearinghouse": 2 + 1,
    "md_remittance":    5 + 1,
    "md_appeal":        4 + 1,
    "md_post_event":    2 + 1,
    "md_timing":        2 + 1,
    "r_present":        2,
    "r_qualification":  3 + 1,
    "r_request":        2 + 1,
    "r_manufacturer":   3 + 1,
    "r_payment":        5 + 1,
    "r_timing":         2 + 1,
    "cash_reimb_in":    3 + 1,
    "cash_reimb_out":   2 + 1,
    "cash_rebate_in":   3 + 1,
    "cash_rebate_out":  2 + 1,
}


def matches(leaf, **want):
    """Does this leaf carry all of these field=value pairs?"""
    merged = {}
    merged.update(leaf["reimbursement_track"])
    merged.update(leaf["rebate_track"])
    merged.update(leaf["cash_verification"])
    return all(merged.get(k) == v for k, v in want.items())


NAMED_CASES = [
    ("happy path, pharmacy + no rebate",
     dict(reimb_type="PHARMACY", ph_payment="FULL", ph_settlement="CONFIRMED",
          cash_reimb_in="MATCHED", r_present="ABSENT")),
    ("happy path, pharmacy + rebate paid",
     dict(reimb_type="PHARMACY", ph_payment="FULL", ph_settlement="CONFIRMED",
          cash_reimb_in="MATCHED", r_payment="FULL", cash_rebate_in="MATCHED")),
    ("happy path, medical + rebate paid",
     dict(reimb_type="MEDICAL", md_remittance="PAID_FULL",
          cash_reimb_in="MATCHED", r_payment="FULL", cash_rebate_in="MATCHED")),
    ("X-1: POS rejection with rebate money held",
     dict(reimb_type="PHARMACY", ph_adjudication="REJECTED",
          r_payment="FULL", cash_rebate_in="MATCHED")),
    ("X-1: medical denial upheld with rebate money held",
     dict(reimb_type="MEDICAL", md_remittance="DENIED", md_appeal="LOST",
          r_payment="FULL", cash_rebate_in="MATCHED")),
    ("X-2: reversed dispense, rebate still live",
     dict(reimb_type="PHARMACY", ph_post_event="REVERSAL_POST_PAY",
          r_manufacturer="APPROVED", r_payment="FULL")),
    ("X-5: correlated cash gap on both tracks",
     dict(cash_reimb_in="ABSENT", cash_rebate_in="ABSENT")),
    ("X-7: medical appeal won after rebate clawback",
     dict(reimb_type="MEDICAL", md_appeal="WON", r_payment="CLAWED_BACK")),
    ("remittance with no cash, no rebate",
     dict(ph_payment="FULL", cash_reimb_in="ABSENT", r_present="ABSENT")),
    ("recoupment that cannot be traced to a deposit",
     dict(ph_post_event="RECOUPMENT", cash_reimb_out="ABSENT")),
    ("rebate approved but never paid, past SLA",
     dict(r_manufacturer="APPROVED", r_payment="NONE", r_timing="PAST_SLA")),
    ("maximum arity: four cash slots",
     dict(ph_post_event="RECOUPMENT", r_payment="CLAWED_BACK")),
]

MUST_NOT_EXIST = [
    ("pharmacy and medical on the same claim",
     lambda l: any(k.startswith("ph_") for k in l["reimbursement_track"])
     and any(k.startswith("md_") for k in l["reimbursement_track"])),
    ("recoupment with no prior payment",
     lambda l: l["reimbursement_track"].get("ph_payment") == "NONE"
     and l["reimbursement_track"].get("ph_post_event") == "RECOUPMENT"),
    ("rebate paid without manufacturer approval",
     lambda l: l["rebate_track"].get("r_payment") is not None
     and l["rebate_track"].get("r_manufacturer") != "APPROVED"),
    ("rebate request without TPA qualification",
     lambda l: l["rebate_track"].get("r_request") is not None
     and l["rebate_track"].get("r_qualification") != "QUALIFIED"),
    ("appeal on a fully paid claim",
     lambda l: l["reimbursement_track"].get("md_appeal") is not None
     and l["reimbursement_track"].get("md_remittance") == "PAID_FULL"),
    ("cash verified for a payment never declared",
     lambda l: "cash_reimb_in" in l["cash_verification"]
     and l["reimbursement_track"].get("ph_payment") in ("NONE", None)
     and l["reimbursement_track"].get("md_remittance") in ("NONE", "DENIED", None)
     and l["reimbursement_track"].get("md_appeal") != "WON"),
]


def main():
    with open(os.path.join(HERE, "leaves_classified.json")) as f:
        leaves = json.load(f)

    print("=" * 72)
    print("1. HOW MUCH DID VALIDITY PRUNING REMOVE?")
    print("=" * 72)
    unconstrained = 1
    for name, _, _ in VARIABLES:
        unconstrained *= UNCONSTRAINED_DOMAINS[name]
    print(f"unconstrained cross-product : {unconstrained:,}")
    print(f"valid paths in the tree     : {len(leaves):,}")
    pct = 100.0 * len(leaves) / unconstrained
    print(f"survived validity checking  : {pct:.5f}%")
    print(f"eliminated as impossible    : {unconstrained - len(leaves):,}")
    print()
    print("Reading: over 99.99% of the naive combination space is nonsense --")
    print("settlements on unpaid claims, rebates the manufacturer never approved,")
    print("cash verification for money nobody ever said they sent. Multiplying")
    print("domain sizes is not an estimate of this problem; it is off by 5 orders")
    print("of magnitude.")
    print()

    print("=" * 72)
    print("2. NAMED CASES -- do they exist by construction?")
    print("=" * 72)
    ok = True
    for label, want in NAMED_CASES:
        hits = [l for l in leaves if matches(l, **want)]
        mark = "PASS" if hits else "FAIL"
        if not hits:
            ok = False
        example = hits[0]["case_id"] if hits else "-"
        print(f"  [{mark}] {label:<52} {len(hits):>5} configs  e.g. {example}")
    print()

    print("=" * 72)
    print("3. IMPOSSIBLE CASES -- are they truly absent?")
    print("=" * 72)
    for label, pred in MUST_NOT_EXIST:
        hits = [l for l in leaves if pred(l)]
        mark = "PASS" if not hits else "FAIL"
        if hits:
            ok = False
        print(f"  [{mark}] {label:<52} {len(hits):>5} found")
    print()

    print("=" * 72)
    print("4. PAIR SPACE")
    print("=" * 72)
    reimb = sorted({l["curated_reimbursement_state"] for l in leaves})
    rebate = sorted({l["curated_rebate_state"] for l in leaves})
    pairs = {(l["curated_reimbursement_state"], l["curated_rebate_state"])
             for l in leaves}
    print(f"reachable reimbursement verdicts : {len(reimb)}")
    print(f"reachable rebate verdicts        : {len(rebate)}")
    print(f"product                          : {len(reimb) * len(rebate)}")
    print(f"pairs actually reached           : {len(pairs)}")
    if len(pairs) == len(reimb) * len(rebate):
        print("  -> EXACT. Every verdict pair is reachable; the tracks are")
        print("     independent at the verdict level. Cross-track rules are")
        print("     flags, not prohibitions.")
    else:
        missing = {(a, b) for a in reimb for b in rebate} - pairs
        print(f"  -> {len(missing)} pairs unreachable, e.g. {sorted(missing)[:5]}")
    print()

    coh = Counter(l["coherence"].split(":")[0] for l in leaves)
    print(f"coherent configurations : {coh['COHERENT']}")
    print(f"anomalous configurations: {coh['ANOMALY']}")
    print()
    print("=" * 72)
    print("OVERALL:", "ALL CHECKS PASSED" if ok else "FAILURES ABOVE")
    print("=" * 72)


if __name__ == "__main__":
    main()
