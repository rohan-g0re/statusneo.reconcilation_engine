# Decision Tree — Findings

Programmatic construction of every valid path through the reconciliation state
space. Every choice at every depth is validated against the choices above it, so
invalid combinations are **never generated** rather than generated and filtered.

Run it yourself:

```
python build_tree.py     # generate + self-validate  -> tree.json, leaves.json
python classify.py       # collapse onto curated verdicts -> leaves_classified.json, pairs.json
python verify.py         # independent verification
```

---

## Headline

| Measure | Value |
|---|---|
| Unconstrained cross-product | 326,517,350,400 |
| **Valid paths (leaf cases)** | **7,046** |
| Eliminated as impossible | 326,517,343,354 (99.9999978%) |
| Coherent configurations | 6,578 |
| Anomalous configurations (tagged, not pruned) | 468 |
| Leaf depth range | **3 to 15 decisions** |
| Cash verification slots per case | **0 to 4** |
| Reachable reimbursement verdicts | 33 |
| Reachable rebate verdicts | 16 |
| **Reachable verdict pairs** | **528** |
| Previously published figure | 510 |

---

## 1. Yes — branches terminate at different depths

You asked whether this could happen. It does, and by a wide margin: **3 to 15**
decisions from root to leaf.

```
depth  3 :      2 leaves      shortest -- POS rejection, no 340B track
depth  4 :      2
depth  5 :      9
depth  6 :     23
depth  7 :     92
depth  8 :    229
depth  9 :    376
depth 10 :    565
depth 11 :    768
depth 12 :   1254
depth 13 :   1686
depth 14 :   1536
depth 15 :    504      longest -- full chain both tracks + 4 cash slots
```

12,138 variable-skips occurred across the tree. If depth were uniform that number
would be zero.

### Three independent causes

**1. Track short-circuit.** `ph_adjudication = REJECTED` is terminal. A real-time
POS rejection kills payment, post-event, settlement and timing in one stroke —
four variables gone, and the reimbursement cash slots with them.

**2. Gated chains.** The 340B track is a strict dependency chain:

```
qualification -> request -> manufacturer decision -> payment
```

`NOT_QUALIFIED` stops after two decisions. A clawed-back rebate runs all five.
Nothing downstream of a gate can be chosen until the gate opens.

**3. Variable cash arity — the big one.** Cash verification is not a fixed depth.
It is **one slot per declared money movement**, and an episode declares between
zero and four:

```
0 slots :    143 cases   nothing was ever declared paid
1 slot  :  1,239 cases
2 slots :  2,712 cases
3 slots :  2,016 cases
4 slots :    936 cases   payment + recoupment + rebate + rebate clawback
```

This is the structural reason cash is not a peer of the other two bands. Bands 1
and 2 each contribute *one verdict*. Band 3 contributes *a variable-length list*
whose length is determined by what bands 1 and 2 decided.

---

## 2. Multiple selections at one depth — resolved as arity, not multi-select

You asked whether a depth can pick more than one option. It can, but the clean
way to model it is not multi-select at a single node; it is **repeated slots**.

Cash is the case in point. A case with a payment *and* a recoupment *and* a paid
rebate *and* a clawback needs four independent cash verdicts. Modelled as
multi-select you get an unordered set and lose which verdict attaches to which
movement. Modelled as four typed slots — `cash_reimb_in`, `cash_reimb_out`,
`cash_rebate_in`, `cash_rebate_out` — every verdict stays bound to its movement,
and the slot simply does not exist when the movement does not.

Same outcome, no ambiguity, and it keeps the leaf JSON directly usable as the
composite case object.

---

## 3. It does not line up with 510. It is 528, and the doc was wrong three ways

The gap is not noise. It decomposes into three separate errors that partially
cancelled.

### Error 1 — B-17 was never a state (doc over-counted by 1)

B-17, "paid and matched but posting date far outside the expected window," was
**never produced by the tree**. Financially it is identical to B-04: expected
equals received, cash matched, nothing outstanding. It is an informational
attribute on a settled claim, not a reconciliation verdict. Treating it as a
state would double every settled-medical leaf while changing no arithmetic.

### Error 2 — C-12 belongs in the feed-level table (over-counted by 1)

C-12, "unmatched rebate," was never produced **and cannot be**. An unmatched
rebate is cash with no attributable episode — by definition there is no episode
for it to be a state of. The doc already handles orphans correctly in its
feed-level section as D-3/D-4. C-12 was the same exception filed twice, once in
the wrong table.

### Error 3 — two rebate states were missing (under-counted by 2)

The tree produced two verdicts the curated table has no name for:

| State | Configs |
|---|---|
| `qualification-pending-within-SLA` | 271 |
| `approved-unpaid-within-SLA` | 271 |

The doc was internally inconsistent. It gives both timing halves for
"request not submitted" (C-03/C-04) and "manufacturer pending" (C-05/C-06), but
only the past-SLA half for qualification-pending (C-01) and approved-unpaid
(C-11). These two are ordinary in-flight states rather than exceptions, which is
presumably why they were skipped when writing by hand.

### Corrected

| | Doc | Tree |
|---|---|---|
| Reimbursement verdicts | 34 | **33** |
| Rebate verdicts | 15 | **16** |
| Product | 510 | **528** |

```
33 × 16 = 528, and the tree reached exactly 528 distinct pairs
```

---

## 4. The tracks are independent at the verdict level — proven, not assumed

The tree reached **528 of a possible 528** verdict pairs. Not one combination is
unreachable.

Every cross-track rule we wrote turns out to be a **flag, not a prohibition**.
X-1 through X-7 mark combinations as anomalous, correlated or explicitly-benign,
but none of them makes a pair impossible.

So the constraint pressure is not where the earlier document implied. It sits one
level down:

```
configuration level : heavily constrained  ->  7,046 valid of 326 billion
verdict level       : completely free      ->  33 × 16 = 528, all reachable
```

That is a genuinely useful architectural result. It means the engine can resolve
each track in isolation and compose, with no cross-track validity table to
maintain. Cross-track logic is a post-pass that annotates, never a gate that
rejects.

---

## 5. Consequences for the build

### The generator must sample verdicts, not configurations

Configuration frequency is wildly non-uniform — a 60× spread:

```
B-15 (medical takeback)                        1,560 configs
A-12 / A-13 (recoupment matched / untraceable)   936 configs each
A-01 (POS rejection)                              26 configs
X-1 compliance case                                1 config
```

Sampling 50 episodes uniformly from 7,046 configurations yields roughly eleven
recoupment cases and, with better than even odds, zero POS rejections and zero
X-1. The X-1 case — reimbursement refused while rebate money is held — is the
single strongest thing to show in the walkthrough, and it is **one path in 7,046**.

Sampling must be stratified over the 528 verdict pairs, with named cases placed
deliberately.

### 56 rules still stands as the implementation estimate

The tree does not change what gets built. The engine resolves each track
independently and composes:

```
episode_status = compose(reimbursement_verdict, rebate_verdict, cross_track_flags)
```

Track rules (33 + 16) plus 7 cross-track annotations. The 7,046 configurations
and 528 verdict pairs are *outputs* of that composition, not branches in it.

### The leaf JSON is the composite case object

Each leaf in `leaves.json` is already the shape the engine should emit:

```json
{
  "case_id": "CASE-00014",
  "depth": 5,
  "coherence": "ANOMALY:rebate_active_on_undispensed_claim",
  "cross_track_flags": ["X-1:denied_with_rebate_paid"],
  "reimbursement_track": { "reimb_type": "PHARMACY", "ph_adjudication": "REJECTED" },
  "rebate_track": { "r_present": "PRESENT", "r_qualification": "QUALIFIED",
                    "r_request": "SUBMITTED", "r_manufacturer": "APPROVED",
                    "r_payment": "FULL" },
  "cash_verification": { "cash_rebate_in": "MATCHED" },
  "decision_path": [ ... ]
}
```

`decision_path` is the audit trail: the exact ordered sequence of decisions that
produced this case. That is what the Exception Investigator agent cites as
evidence, and it is generated rather than narrated.

---

## 6. Bug caught during the build, worth recording

The validator's first run reported 15 violations: *"cash verification on a
rejected claim."* The **validator** was wrong, not the generator.

It had asserted that a POS-rejected pharmacy claim can carry no cash slots at
all. False — the 340B track runs off the *dispense* record, independent of the
claim's adjudication. A rebate paid on a claim the PBM rejected produces real
cash that must be verified, and that combination is precisely the X-1 case.

The validator had made the exact mistake the whole architecture exists to
prevent: assuming that because the claim failed, nothing financial could follow.
Recorded because it is the same trap a reviewer will look for in the design.

---

## Files

| File | Contents |
|---|---|
| `spec.py` | Decision variables, domain functions, coherence and cross-track rules |
| `build_tree.py` | DFS generator + independent validator |
| `classify.py` | Collapses configurations onto curated A/B/C verdicts |
| `verify.py` | Pruning contrast, named-case existence, impossible-case absence |
| `tree.json` | Nested decision tree, every node |
| `leaves.json` | 7,046 composite case objects |
| `leaves_classified.json` | Same, annotated with curated verdicts |
| `pairs.json` | 528 verdict pairs with configuration counts |
| `NOTES.md` | Chunk-by-chunk work log, written as the work happened |
