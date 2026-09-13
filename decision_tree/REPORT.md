# Decision Tree — Findings

> **Vocabulary:** *claim*, *episode*, *track*, *record*, *verdict*, *disposition* and *reason code* are defined once in [`glossary.md`](../docs/glossary.md). That file wins wherever this one is loose.

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
| Unconstrained cross-product | 12,093,235,200 |
| **Valid paths (leaf cases)** | **4,224** |
| Eliminated as impossible | 12,093,230,976 (99.99997%) |
| Coherent configurations | 3,860 |
| Anomalous configurations (tagged, not pruned) | 364 |
| Leaf depth range | **3 to 14 decisions** (remeasured 2026-09-12 on the current tree; was 3 to 15 pre-SLA-removal*) |
| Cash verification slots per case | **0 to 4** (current histogram: 63 / 564 / 1,473 / 1,440 / 684) |
| Configurations per verdict pair | min 1, max 252 — **252x spread**; 99 of 372 pairs have exactly one configuration (remeasured 2026-09-12) |
| Reachable reimbursement verdicts | 31 |
| Reachable rebate verdicts | 12 |
| **Reachable verdict pairs** | **372** |
| Deterministic rules (track + cross-track) | 43 + 7 = **50** |
| If-checks per case (min / avg / max) | 10 / 21.0 / 32 |

\* the per-depth histogram and per-arity counts in §1 are from the
pre-SLA-removal run (7,046 paths). The depth range and cash-arity histogram in
the headline table above were remeasured on the current 4,224-path tree
(2026-09-12); the three structural causes of variable depth are unaffected by
SLA removal and still hold.

**Running history of the headline count:** 510 (hand-counted) → 528 (exhaustive
generation corrected four hand-counting errors) → **372** (SLA thresholds
removed by design decision — see §7). The first change was error correction.
The second was a deliberate scope decision, not a fix.

---

## 1. Yes — branches terminate at different depths

*Figures in this section are from the original 7,046-path run, before SLA
thresholds were removed (§7). They are kept here because they are what
established the phenomenon and its three causes below, and both the
phenomenon and the causes hold unchanged on the current 4,224-path tree — only
the totals moved. The section was not re-run to produce a fresh histogram.*

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

*This section documents the error-correction pass only — the numbers above
(33, 16, 528) are what the tree proved at that point in time. A later,
separate change removed SLA thresholds entirely by design decision and moved
the totals again, to 31, 12 and 372; see §7. The two `within-SLA` rebate
states surfaced by Error 3 above (`qualification-pending-within-SLA`,
`approved-unpaid-within-SLA`) no longer exist as distinct states after §7 —
they were merged back into C-01 and C-11 once the SLA qualifier was retired.
That is a scope decision layered on top of an error correction, not a reversal
of it.*

---

## 4. The tracks are independent at the verdict level — proven, not assumed

The tree reached **372 of a possible 372** verdict pairs (31 × 12). Not one
combination is unreachable. This is the same finding as before SLA removal
(528 of 528) — the product stayed exact when the totals moved, which means the
independence result was never an artifact of the specific verdict count. It is
reconfirmed, not just carried over.

Every cross-track rule we wrote turns out to be a **flag, not a prohibition**.
X-1 through X-7 mark combinations as anomalous, correlated or explicitly-benign,
but none of them makes a pair impossible.

So the constraint pressure is not where the earlier document implied. It sits one
level down:

```
configuration level : heavily constrained  ->  4,224 valid of 12 billion
verdict level       : completely free      ->  31 × 12 = 372, all reachable
```

That is a genuinely useful architectural result. It means the engine can resolve
each track in isolation and compose, with no cross-track validity table to
maintain. Cross-track logic is a post-pass that annotates, never a gate that
rejects.

---

## 5. Consequences for the build

### The generator must sample verdicts, not configurations

Configuration frequency is wildly non-uniform. **Remeasured 2026-09-12 on the
current 4,224-path tree** (an earlier revision of this section quoted a "~60×"
spread and "X-1 = 1 configuration in 7,046" — both were figures from the
pre-SLA-removal run and are superseded by the counts below):

```
verdict frequency (current tree)
  B-15 (medical takeback)                          924 configs
  A-12 / A-13 (recoupment matched / untraceable)   528 configs each
  A-01 (POS rejection)                              22 configs

pair-level skew (current tree)
  configurations per pair : min 1, max 252  ->  252x spread
  pairs with exactly one configuration : 99 of 372

cross-track flag rarity (current tree; configs / distinct pairs)
  X-6 total loss                          8 /  8   <- rarest
  X-4 rebate-only failure                 9 /  6
  X-1 denied with rebate paid            36 / 16
  X-7 appeal won after clawback         108 /  4
  X-2 reversed dispense, live rebate    260 / 20
  X-5 correlated cash gap               305 / 64
  X-3 recoupment undermines qual        912 / 18

coherence partition at pair level (current tree)
  36 pairs wholly anomalous  = {A-01, A-10, A-11, A-16} x
       {C-01, C-03, C-05, C-07, C-08, C-09, C-10, C-11, C-14}
  336 pairs wholly coherent, none mixed
  -> a compliance flag is decidable from the verdict pair alone
```

Sampling 50 episodes uniformly from thousands of configurations yields roughly
a fair number of recoupment cases and, with better than even odds, zero POS
rejections. Ninety-nine pairs sit on a single configuration each, and the
rarest flags are now X-6 (8 configs) and X-4 (9) — a stratifier must target
flags by these measured counts, not by narrative. The X-1 case — reimbursement
refused while rebate money is held — remains the single strongest thing to show
in the walkthrough; at 36 configurations in 4,224 it is still effectively
invisible to uniform sampling.

Sampling must be stratified over the 372 verdict pairs, with named cases placed
deliberately.

### 50 rules is now the implementation estimate (was 56)

The tree does not change what gets built. The engine resolves each track
independently and composes:

```
episode_status = compose(reimbursement_verdict, rebate_verdict, cross_track_flags)
```

Track rules (31 + 12) plus 7 cross-track annotations = 50. The 4,224
configurations and 372 verdict pairs are *outputs* of that composition, not
branches in it.

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

## 7. SLA thresholds removed — a deliberate scope decision, not a correction

Unlike §3, this is not a bug fix. Nothing was wrong with the 528-pair model;
it was decided that it modelled the wrong thing.

### What changed

The three timing variables — `ph_timing`, `md_timing`, `r_timing` — were
removed from the reconciliation model. Their domain functions still exist in
`spec.py` as stubs (`dom_ph_timing`, `dom_md_timing`, `dom_r_timing`), each now
returning `[]` unconditionally with a docstring explaining why, so the
`VARIABLES` list stays stable. `classify.py`, `verify.py` and `count_ifs.py`
were updated to match: the within-SLA/past-SLA split disappears everywhere a
verdict used to branch on it.

### Why

- **Aging stops being a verdict dimension.** It becomes a read-time sort key
  over the pending and exception lists, not a branch the engine takes.
- **The payoff is completeness, not simplicity.** A claim with no new inbound
  record can no longer change disposition. That makes event-driven incremental
  processing **provably complete**, rather than an optimisation you merely hope
  is safe. Under SLA thresholds, an untouched claim could flip overnight from
  within-SLA to past-SLA on the calendar alone, which forces a daily sweep over
  every open claim just in case — exactly the kind of full-table re-scan an
  event-driven design is supposed to make unnecessary.
- **It removes the need to defend arbitrary threshold numbers.** Any SLA
  constant written into the model invites the question "why that number,"
  with no good answer at this layer.
- **Real timing rules do exist — as policy, never as fields on a record.**
  Medicare's 30-day payment ceiling, state prompt-pay laws (commonly 30 or 45
  days), Iowa's 20-day PBM rule, CAQH CORE 370's ±3 business day claim
  acknowledgment window, and the 340B rebate pilot's 45-day submission /
  10-day manufacturer payment windows are all real. None of them belongs in
  this model as a literal. If reintroduced, they belong in engine
  configuration, evaluated at read time, never hard-coded into the decision
  tree. This was an informed exclusion, made with the rules in view, not an
  oversight.

### Exact numbers from the rerun

```
unconstrained cross-product : 12,093,235,200      (was 326,517,350,400)
valid paths in the tree     : 4,224               (was 7,046)
eliminated as impossible    : 12,093,230,976
survived validity checking  : 0.00003%

VALIDATION PASSED -- all 4224 paths satisfy every constraint
OVERALL: ALL CHECKS PASSED   (verify.py: all named cases found, all impossible cases absent)

reachable reimbursement verdicts : 31             (was 33)
reachable rebate verdicts        : 12             (was 16)
product                          : 31 x 12 = 372
pairs actually reached           : 372            -> EXACT, still a perfect product
                                                     (was 528)

coherent configurations   : 3,860                 (was 6,578)
anomalous configurations  : 364                   (was 468)

deterministic rules : 31 + 12 = 43 track + 7 cross-track = 50    (was 56)

if-checks per case  : min 10, max 32, average 21.0
  stage 0 route pharmacy vs medical : 1 check always
  stage 1 reimbursement verdict     : 1-12 checks, avg 5.3
  stage 2 rebate verdict            : 1-12 checks, avg 7.6
  stage 3 cross-track flags         : 7 checks always
```

### Verdicts retired

`A-03`, `B-03`, `C-04`, `C-06` — the past-SLA halves of pairs whose
within-SLA twin now covers both cases. Also `C-01a` and `C-11a`, the two
`within-SLA` rebate states that Error 3 in §3 had just added, are now merged
back into `C-01` and `C-11` — the split they existed to name no longer exists.
(`B-17` and `C-12` were already retired in §3 for unrelated reasons; that
reasoning is untouched by this change.)

Reimbursement lost 2 verdicts (33 → 31: `A-03`, `B-03`). Rebate lost 4 (16 →
12: `C-04`, `C-06`, `C-01a`, `C-11a`). 2 + 4 accounts for the entire movement
from 528 to 372 — there is no residual unexplained by the retirement list.

### The independence finding survives unchanged

The pair space is still an **exact product**: 372 of a possible 372 (31 × 12).
This is the same result as §4 reported at 528 of 528 — the two tracks are
independent at the verdict level regardless of what the verdict counts
themselves are, so every cross-track rule is still an annotation, never a
prohibition, and the engine still needs no cross-track validity table.

### The named-case check survives with one wording edit

All twelve named cases in `verify.py` still exist by construction on the
current tree. Eleven are untouched. The twelfth needed one edit: the case
previously asserted as *"rebate approved but never paid, past SLA"* is now
asserted as *"rebate approved but never paid"* — the SLA qualifier was removed
from the assertion because the underlying distinction no longer exists to
qualify.

### Running history

```
510   hand-counted                                        (original doc)
528   exhaustive generation corrected four hand-count errors   (§3, error correction)
372   SLA thresholds removed                                (§7, this pass — a scope decision)
```

The first change corrected mistakes in existing scope. The second removed
scope on purpose. They should not be read as the same kind of event.

---

## Files

| File | Contents |
|---|---|
| `spec.py` | Decision variables, domain functions, coherence and cross-track rules (timing domains retired to stubs) |
| `build_tree.py` | DFS generator + independent validator |
| `classify.py` | Collapses configurations onto curated A/B/C verdicts |
| `verify.py` | Pruning contrast, named-case existence, impossible-case absence |
| `count_ifs.py` | Counts if-checks actually evaluated per case (rules run, not rules written) |
| `tree.json` | Nested decision tree, every node |
| `leaves.json` | 4,224 composite case objects |
| `leaves_classified.json` | Same, annotated with curated verdicts |
| `pairs.json` | 372 verdict pairs with configuration counts |
| `NOTES.md` | Chunk-by-chunk work log, written as the work happened |
