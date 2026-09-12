"""
Depth-first construction of every VALID path through the reconciliation
decision tree.

No cross-product anywhere. At each depth the legal choices are recomputed from
the partial assignment above, so an invalid combination is never generated in
the first place -- it is not generated and then filtered.

Outputs (written next to this file):
    tree.json    nested decision tree, every internal node and leaf
    leaves.json  flat list of leaf cases (the composite JSON per case)
"""

import json
import os
from collections import Counter

from spec import VARIABLES, coherence, cross_track_flags, dispense_did_not_happen

HERE = os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------------------
# Traversal
# ---------------------------------------------------------------------------

def walk(index, partial, path, node, stats):
    """Advance to the next variable that has a non-empty domain.

    A variable whose domain is empty is skipped entirely -- it is not part of
    this path, which is precisely how sibling branches end at different depths.
    """
    # Find the next applicable variable.
    while index < len(VARIABLES):
        name, domain_fn, band = VARIABLES[index]
        choices = domain_fn(partial)
        if choices:
            break
        stats["skipped_variables"] += 1
        index += 1
    else:
        # No applicable variables remain -> this is a leaf.
        return finalize(partial, path, node, stats)

    node["variable"] = name
    node["band"] = band
    node["children"] = {}

    for value in choices:
        stats["edges"] += 1
        child_partial = dict(partial)
        child_partial[name] = value
        child_node = {}
        node["children"][value] = child_node
        walk(index + 1, child_partial, path + [(name, value)], child_node, stats)

    return None


def finalize(partial, path, node, stats):
    coh = coherence(partial)
    flags = cross_track_flags(partial)

    leaf = {
        "case_id": f"CASE-{stats['leaves'] + 1:05d}",
        "depth": len(path),
        "coherence": coh,
        "cross_track_flags": flags,
        "reimbursement_track": {k: v for k, v in partial.items()
                                if k.startswith(("reimb_type", "ph_", "md_"))},
        "rebate_track": {k: v for k, v in partial.items() if k.startswith("r_")},
        "cash_verification": {k: v for k, v in partial.items() if k.startswith("cash_")},
        "decision_path": [{"variable": n, "value": v} for n, v in path],
    }

    node["leaf"] = leaf["case_id"]
    node["depth"] = leaf["depth"]
    node["coherence"] = coh

    stats["leaves"] += 1
    stats["by_depth"][leaf["depth"]] += 1
    stats["by_coherence"][coh.split(":")[0]] += 1
    for f in flags:
        stats["by_flag"][f.split(":")[0]] += 1
    stats["cash_slots"][len(leaf["cash_verification"])] += 1
    stats["by_reimb_type"][partial["reimb_type"]] += 1

    stats["leaf_list"].append(leaf)
    return None


def build():
    stats = {
        "leaves": 0,
        "edges": 0,
        "skipped_variables": 0,
        "by_depth": Counter(),
        "by_coherence": Counter(),
        "by_flag": Counter(),
        "cash_slots": Counter(),
        "by_reimb_type": Counter(),
        "leaf_list": [],
    }
    root = {}
    walk(0, {}, [], root, stats)
    return root, stats


# ---------------------------------------------------------------------------
# Self-validation -- prove every generated path obeys the constraints
# ---------------------------------------------------------------------------

def validate(leaves):
    """Re-check every leaf against the rules, independently of generation.

    If generation and validation disagree, one of them is wrong and we want to
    know immediately rather than trust a number.
    """
    errors = []

    for leaf in leaves:
        r = leaf["reimbursement_track"]
        b = leaf["rebate_track"]
        c = leaf["cash_verification"]
        cid = leaf["case_id"]

        # -- XOR on the reimbursement track -------------------------------
        has_ph = any(k.startswith("ph_") for k in r)
        has_md = any(k.startswith("md_") for k in r)
        if has_ph and has_md:
            errors.append(f"{cid}: both pharmacy and medical present")
        if r.get("reimb_type") == "PHARMACY" and has_md:
            errors.append(f"{cid}: pharmacy claim carries medical fields")
        if r.get("reimb_type") == "MEDICAL" and has_ph:
            errors.append(f"{cid}: medical claim carries pharmacy fields")

        # -- pharmacy chain ------------------------------------------------
        if r.get("ph_adjudication") == "REJECTED":
            for k in ("ph_payment", "ph_post_event", "ph_settlement", "ph_timing"):
                if k in r:
                    errors.append(f"{cid}: {k} present after POS rejection")
            # Only REIMBURSEMENT cash is impossible here. Rebate cash is not:
            # the 340B track runs off the dispense record and is independent of
            # the pharmacy claim's adjudication. A rebate paid on a rejected
            # claim is an anomaly to surface, not a generation error.
            if "cash_reimb_in" in c or "cash_reimb_out" in c:
                errors.append(f"{cid}: reimbursement cash on a POS-rejected claim")
        if r.get("ph_payment") == "NONE" and r.get("ph_post_event") in (
                "REVERSAL_POST_PAY", "RECOUPMENT"):
            errors.append(f"{cid}: clawback without a prior payment")
        if r.get("ph_payment") == "NONE" and "ph_settlement" in r:
            errors.append(f"{cid}: settlement without a payment")

        # -- medical chain -------------------------------------------------
        if r.get("md_clearinghouse") == "REJECTED":
            for k in ("md_remittance", "md_appeal", "md_post_event", "md_timing"):
                if k in r:
                    errors.append(f"{cid}: {k} present after clearinghouse rejection")
        if "md_appeal" in r and r.get("md_remittance") not in ("PARTIAL", "DENIED"):
            errors.append(f"{cid}: appeal without a denial or short payment")

        # -- 340B chain ----------------------------------------------------
        if b.get("r_present") == "ABSENT" and len(b) > 1:
            errors.append(f"{cid}: rebate detail on an absent 340B track")
        if "r_request" in b and b.get("r_qualification") != "QUALIFIED":
            errors.append(f"{cid}: rebate request without qualification")
        if "r_manufacturer" in b and b.get("r_request") != "SUBMITTED":
            errors.append(f"{cid}: manufacturer decision without a submitted request")
        if "r_payment" in b and b.get("r_manufacturer") != "APPROVED":
            errors.append(f"{cid}: rebate payment without manufacturer approval")

        # -- cash verification ---------------------------------------------
        paid_reimb = (r.get("ph_payment") in ("PARTIAL", "FULL", "OVER", "DUPLICATE")
                      or r.get("md_remittance") in ("PAID_FULL", "PARTIAL", "DUPLICATE_835")
                      or r.get("md_appeal") == "WON")
        if "cash_reimb_in" in c and not paid_reimb:
            errors.append(f"{cid}: reimbursement cash slot with no declared payment")
        if paid_reimb and "cash_reimb_in" not in c:
            errors.append(f"{cid}: declared payment with no cash verification slot")

        neg = (r.get("ph_post_event") in ("REVERSAL_POST_PAY", "RECOUPMENT")
               or r.get("md_post_event") == "RECOUPMENT")
        if "cash_reimb_out" in c and not neg:
            errors.append(f"{cid}: outbound cash slot with no negative movement")
        if neg and "cash_reimb_out" not in c:
            errors.append(f"{cid}: negative movement with no cash verification slot")

        if "cash_rebate_in" in c and b.get("r_payment") not in (
                "PARTIAL", "FULL", "DUPLICATE", "CLAWED_BACK"):
            errors.append(f"{cid}: rebate cash slot with no rebate payment")
        if "cash_rebate_out" in c and b.get("r_payment") != "CLAWED_BACK":
            errors.append(f"{cid}: rebate clawback cash slot without a clawback")

    return errors


# ---------------------------------------------------------------------------

def main():
    tree, stats = build()
    leaves = stats["leaf_list"]

    errors = validate(leaves)

    with open(os.path.join(HERE, "tree.json"), "w") as f:
        json.dump(tree, f, indent=1)
    with open(os.path.join(HERE, "leaves.json"), "w") as f:
        json.dump(leaves, f, indent=1)

    print("=" * 68)
    print("DECISION TREE BUILD")
    print("=" * 68)
    print(f"valid leaf cases        : {stats['leaves']}")
    print(f"edges traversed         : {stats['edges']}")
    print(f"variables skipped       : {stats['skipped_variables']}  "
          f"(each skip = a branch ending earlier)")
    print()
    print("by reimbursement type   :", dict(stats["by_reimb_type"]))
    print("by coherence            :", dict(stats["by_coherence"]))
    print()
    print("leaf depth distribution (number of decisions on the path):")
    for d in sorted(stats["by_depth"]):
        print(f"    depth {d:>2} : {stats['by_depth'][d]:>6} leaves")
    print()
    print("cash verification slots per case (variable arity):")
    for n in sorted(stats["cash_slots"]):
        print(f"    {n} slot(s) : {stats['cash_slots'][n]:>6} leaves")
    print()
    print("cross-track flags fired :")
    for k in sorted(stats["by_flag"]):
        print(f"    {k:>4} : {stats['by_flag'][k]:>6}")
    print()
    print("=" * 68)
    if errors:
        print(f"VALIDATION FAILED -- {len(errors)} violations")
        for e in errors[:25]:
            print("   ", e)
    else:
        print(f"VALIDATION PASSED -- all {len(leaves)} paths satisfy every constraint")
    print("=" * 68)


if __name__ == "__main__":
    main()
