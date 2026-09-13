---
title: Generate combinatorial state spaces exhaustively; validate with an independent second pass
date: 2026-09-12
last_refreshed: 2026-09-13
category: docs/solutions/best-practices
module: reconciliation-state-space
problem_type: best_practice
component: tooling
severity: high
applies_when:
  - "Documenting or specifying a combinatorial state space by hand (cross-product of independent dimensions or tracks)"
  - "A design doc enumerates valid states that a downstream implementation will be built against"
  - "Two or more independently-varying tracks are combined into a joint outcome table"
  - "A validator is written to check generated output against hand-authored rules"
symptoms:
  - "Hand-counted state space (34 x 15 = 510) diverged from the generated space (33 x 16 = 528) by four states"
  - "Two figures in this doc were themselves never measured when written: a claimed 60x frequency spread was actually 252x, and a case called unique was 36 configurations"
  - "Two documented states were structurally unreachable and never actually generated"
  - "Two valid in-flight states were silently missing due to inconsistent SLA-timing coverage across pipeline stages"
  - "One state was miscategorized across the coherence split, skewing the anomalous-pair count from 36 to 52"
  - "An independently-written validator flagged 15 false-positive violations by assuming claim rejection precludes all downstream financial activity"
related_components:
  - documentation
  - development_workflow
  - testing_framework
tags:
  - state-space
  - decision-tree
  - combinatorics
  - exhaustive-generation
  - validation
  - reconciliation
  - 340b
  - spec-verification
---

# Generate combinatorial state spaces exhaustively; validate with an independent second pass

## Context

A design doc for a post-claim pharmacy financial reconciliation engine hand-enumerated its state space as `34 × 15 = 510` combinations (`docs/reconciliation_state_space.md`): 34 pharmacy/medical reimbursement verdicts crossed with 15 rebate-track verdicts. The request was to build it programmatically instead — as a decision tree where every choice at every depth is validated against the choices already made above it, so only valid paths are ever produced, never a cross-product filtered down after the fact.

The implementation (`decision_tree/spec.py`, `build_tree.py`, `classify.py`, `verify.py`) generated **7,046** valid configuration paths out of **326,517,350,400** naive unconstrained combinations — 99.999998% eliminated as structurally impossible: settlements on unpaid claims, rebates the manufacturer never approved, cash verification for money nobody claimed to send, appeals on fully-paid claims.

Collapsing those 7,046 configurations onto the hand-curated verdict names exposed four errors in the original hand-count and corrected the total to `33 × 16 = 528` (476 coherent + 52 anomalous). The implementation estimate did not move at all — the errors redistributed states across categories without changing how much code there was to write.

> **Figures superseded, and the lesson strengthened.** A later design decision removed SLA thresholds from the model entirely, which changed every number above. The current tree is **4,224** configurations from **12,093,235,200** unconstrained, resolving to **31 × 12 = 372** verdict pairs (336 wholly coherent, 36 wholly anomalous, none mixed) and **50** rules. The guidance in this document is unchanged — only its illustrations moved.
>
> Two figures here were also **wrong when written**, and neither came from the generator. The spread was asserted as ~60× and measured later at **252×** (min 1, max 252 configurations per pair, with **99 of 372 pairs holding exactly one**). The compliance case called "1 configuration out of 7,046" is in fact **36 configurations across 16 pairs**; the genuinely rarest flags are X-6 at 8 configurations and X-4 at 9. A document arguing that hand-derived numbers drift had two hand-derived numbers in it. The fix is the same one it recommends: measure, do not assert.

## Guidance

### Model the space as a decision tree with per-depth domain functions

Not as a cross-product you filter afterward. Each decision variable gets a function `dom_x(partial_assignment) -> list[choice]` that inspects everything decided above it and returns only the choices still legal. An empty return means the variable does not exist on that path — the branch simply ends early, which yields variable-depth leaves for free instead of padding every path to a fixed length with `N/A` values.

```python
def dom_ph_adjudication(p):
    if p.get("reimb_type") != "PHARMACY":
        return []
    return ["REJECTED", "ACCEPTED"]

def dom_ph_post_event(p):
    if p.get("ph_adjudication") != "ACCEPTED":
        return []
    pay = p.get("ph_payment")
    if pay == "NONE":
        # Cannot claw back money never sent -> no RECOUPMENT, no post-pay reversal.
        return ["NONE", "REVERSAL_PRE_PAY", "ADJUSTMENT"]
    return ["NONE", "REVERSAL_POST_PAY", "RECOUPMENT", "ADJUSTMENT"]

def dom_ph_settlement(p):
    if p.get("ph_adjudication") != "ACCEPTED":
        return []
    if p.get("ph_payment") == "NONE":
        return []                                  # nothing paid, nothing to settle
    if p.get("ph_post_event") == "REVERSAL_POST_PAY":
        return []                                  # claim withdrawn, settlement moot
    return ["CONFIRMED", "MISSING"]
```

Every empty-return branch carries an inline comment explaining *why* it is dead. That comment is what a reviewer — and the independent validator below — checks against later.

The generator is then a plain DFS over the variable list in dependency order: at each index, call the domain function against the partial assignment, skip variables returning empty, recurse on every legal choice. When no variable applies, that is a leaf.

### Write the validator as a genuinely independent second pass

Re-derive every constraint from the finished leaf. Never let the validator call into or trust the generator's logic. If both are typed by the same hand in the same sitting, they share the same blind spot. Point them in opposite directions: the generator builds forward from nothing, the validator checks backward from a completed leaf using its own restatement of the rules.

```python
def validate(leaves):
    """Re-check every leaf against the rules, independently of generation.

    If generation and validation disagree, one of them is wrong and we want to
    know immediately rather than trust a number.
    """
    ...
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
```

### Verify from three orthogonal angles

Each catches a different failure mode:

1. **Pruning contrast** — compare the generated count against the naive cross-product of per-variable domain sizes. A tiny survival percentage confirms the constraints are doing real work rather than decoration.
2. **Named-case existence** — assert that specific scenarios you know must be reachable actually appear. A gap here means the generator is *too restrictive*.
3. **Impossible-case absence** — assert that specific invalid combinations never appear. A hit here means the generator is *too permissive*.

Add a fourth whenever the space has more than one independent dimension: verify that `reachable_pairs == len(reachable_A) * len(reachable_B)`. If the product is exact, the dimensions are independent at the verdict level and no cross-dimension validity table is needed — every cross-cutting rule is an annotation, not a prohibition.

## Why This Matters

### The validator caught a bug in itself

First run: 15 flagged violations, all "cash verification on a rejected claim." The instinct is to trust the check that fires and go fix the generator.

The check was wrong. It had assumed a point-of-sale-rejected claim can carry no cash at all — but the 340B rebate track runs off the *dispense* record, independent of the claim's adjudication. A rebate paid on a claim the payer rejected is real cash requiring verification, and that exact combination turned out to be the highest-value compliance case in the system — asserted at the time as a single configuration in 7,046, measured later at 36 configurations across 16 verdict pairs.

The validator had made the precise mistake the whole architecture exists to catch: assuming a claim's fate determines everything downstream of it. Because it was written independently, disagreeing with the generator surfaced the question at all. Had one person written both passes in one sitting, the shared assumption would have been invisible to both.

### Hand enumeration fails in specific, recurring ways

All four showed up here, and none are exotic:

| Failure mode | What it looked like |
|---|---|
| Distinct-looking state that is functionally identical to one already counted | `B-17` — same financial outcome as `B-04`, so an attribute, not a verdict |
| State that structurally cannot belong to the category it was filed under | `C-12` — cash with no attributable episode, filed as an episode-level state |
| Asymmetry invisible to the eye because it only appears across many rows | Two pipeline stages got both SLA-timing halves, two got only one; a human skips the "boring" in-flight states |
| Single item mis-bucketed at the tail of a long table | `A-11` mis-split across the coherence boundary, changing 36 anomalous pairs to 52 |

These are the default failure modes of enumerating anything long by hand, in any domain.

### The number that mattered for planning did not move

The doc-visible headline (510 → 528, +18) makes the estimate look significantly wrong. It was not. `34 + 15 = 49` and `33 + 16 = 49` are the same sum; plus 7 cross-cutting rules, that is 56 rules either way. The errors redistributed *which* states existed without changing *how many rules* implement them. (The later SLA removal did move it, deliberately, to 50 — a scope decision rather than a correction.)

When reporting a correction like this, separate "the enumerated count changed" from "the implementation cost changed." They are usually not the same claim, and only exhaustive generation lets you distinguish them.

### Uniform sampling over a generated space is not neutral

Frequency is wildly non-uniform. Measured on the current tree, configurations per verdict pair range from **1 to 252** — a **252×** spread, with **99 of 372 pairs holding exactly one configuration**. The rarest cross-track flags are X-6 (8 configurations) and X-4 (9). Sampling 50 cases uniformly would miss most of the tail entirely.

Any test-data or QA-sampling strategy built on an enumerated space must stratify over the *outcome* (verdict or state), not over raw configuration count, or the rarest and most important cases systematically vanish from every sample.

## When to Apply

Reach for exhaustive constraint-validated generation — rather than hand enumeration, spreadsheets, or a filtered cross-product — when most of these hold:

- The space is a genuine cross-product of 2+ dimensions, each with a handful of legal values, where legal values at one position depend on choices made earlier (gating, short-circuits, mutual exclusion).
- The unconstrained cross-product is large enough that a human cannot plausibly check every combination against every rule above it. Here: 3.3 × 10¹¹ unconstrained versus 7,046 valid — five-plus orders of magnitude apart.
- The downstream artifact (rules engine, permission matrix, pricing table, test suite) needs a definitive count, a guarantee that specific cases exist, or a guarantee that specific cases are impossible. A hand count cannot back any of those with confidence.
- You need to know whether dimensions are truly independent before deciding whether to build a cross-dimension validity table. Building that table speculatively is wasted work in either direction.
- Test data will be sampled from the space and correctness depends on covering rare-but-critical combinations.

**Overkill when:** the space is small enough to verify by inspection (a handful of booleans); the dimensions are genuinely independent and everyone already agrees; there is no gating between variables at all (a true cross-product, where naive enumeration already is exhaustive); or the only consumer is a one-off document nobody will implement against. Writing spec, generator, validator, classifier and verifier as five artifacts costs real time — it paid for itself here because the state space fed directly into a 56-rule implementation and a compliance-sensitive review.

## Examples

The same five-artifact shape applies to any combinatorial space with dependent choices. The reusable part is the structure, not the pharmacy domain logic.

**Permission matrices.** `dom_can_delete(p)` returns `[]` unless `p["role"] in ("admin", "owner")` and `p["resource_state"] != "ARCHIVED"`. Role and resource lifecycle gate which permission-actions even exist as choices — exactly as `ph_adjudication` gates `ph_post_event`.

**Workflow state machines.** A ticket's available `next_status` values are a function of `current_status` plus assignee. Model each transition as a depth rather than a static adjacency table, and let the independent validator re-derive "no transition into CLOSED without a resolution reason" from the finished path.

**Pricing tiers.** `dom_discount_code(p)` returns `[]` if `p["plan"] == "ENTERPRISE"` (negotiated, not coded) or if `p["billing_cycle"] == "MONTHLY"` and the code is annual-only. The variable-arity cash-slot pattern maps directly onto line-item and add-on slots that exist only when a triggering purchase happened upstream.

**Feature-flag interactions.** Treat each flag as a depth whose legal values depend on flags already resolved — `dom_flag_b(p)` returns `[]` if `p["flag_a"] == "OFF"` and B requires A. Then run the same three checks: pruning contrast to show how much of the naive 2ᴺ space is reachable, named-case checks for combinations QA must cover, impossible-case checks for combinations config validation should prevent from ever shipping.

In every case the shape to reuse is: per-depth domain functions with empty-return short-circuiting, a DFS generator, an independently-derived validator that never imports the generator's assumptions, a classifier collapsing raw leaves onto human-facing names, and a verifier asserting pruning ratio, required-case presence and forbidden-case absence as three separate checks.

## Related

- `decision_tree/REPORT.md` — full findings from this run
- `decision_tree/NOTES.md` — chunk-by-chunk work log, including the validator bug as it was caught
- `docs/reconciliation_state_space.md` — Section 9 is the corrections log produced by this exercise
