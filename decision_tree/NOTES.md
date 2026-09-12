# Decision Tree — Running Work Log

Written chunk by chunk as the work happens, not reconstructed afterwards.
Each chunk records: what was built, what it produced, what it revealed, what broke.

---

## Chunk 1 — Spec (`spec.py`)

**Built.** Every decision variable, in depth order, each with a *domain function*
that takes the partial assignment made above it and returns the legal choices at
that point. An empty return means the variable does not exist on that path.

Three bands:

| Band | Variables | Purpose |
|---|---|---|
| 1 | `reimb_type`, then `ph_*` (5) or `md_*` (5) | Reimbursement track, pharmacy XOR medical |
| 2 | `r_present`, `r_qualification`, `r_request`, `r_manufacturer`, `r_payment`, `r_timing` | 340B rebate chain |
| 3 | `cash_reimb_in`, `cash_reimb_out`, `cash_rebate_in`, `cash_rebate_out` | Cash verification, one slot per declared money movement |

**Key modelling decisions made here, with reasons:**

- **Aging is only a decision where it changes the verdict.** `ph_timing` is
  offered when payment is absent (A-02 vs A-03) or settlement is missing (A-06),
  and omitted when the claim is paid and settled. Offering it everywhere would
  have doubled the tree with pairs of leaves that reconcile identically.
- **Cash is a repeated slot, not a fixed depth.** One slot per declared movement.
  This is what makes cash genuinely different from the other two bands.
- **Anomalies are generated and tagged, never pruned.** Data that should be
  impossible is the entire reason an exception engine exists.
- **`REVERSAL_POST_PAY` suppresses settlement.** A withdrawn claim has nothing
  to settle, so the variable is dropped rather than given a phantom value.

---

## Chunk 2 — Generator + independent validator (`build_tree.py`)

**Built.** Depth-first walk. At each step the next variable with a non-empty
domain is selected; variables with empty domains are skipped and the path simply
ends earlier. Two outputs: `tree.json` (nested, every node) and `leaves.json`
(flat list of composite case objects).

**Validator written separately from the generator, on purpose.** It re-derives
every constraint from the finished leaf rather than trusting the generation
logic. If the two disagree, one is wrong and we want to know before quoting a
number.

### First run: 15 violations — and the validator was the one that was wrong

```
CASE-00011: cash verification on a rejected claim
... 15 total
```

**Diagnosis.** The validator asserted that a POS-rejected pharmacy claim can
carry no cash slots at all. False. The 340B track runs off the *dispense*
record, not off the claim's adjudication. A rebate paid on a claim the PBM
rejected still produces real rebate cash that must be verified — and that
combination is exactly the X-1 compliance case we most want to surface.

**Fix.** Narrowed the assertion to reimbursement cash only:

```python
if "cash_reimb_in" in c or "cash_reimb_out" in c:
    errors.append(f"{cid}: reimbursement cash on a POS-rejected claim")
```

Worth recording because it is the same mistake the whole architecture is built
to avoid: treating the two tracks as one. The validator had silently assumed
that if the claim failed, nothing financial could follow.

### Second run: clean

```
valid leaf cases        : 7046
edges traversed         : 11722
variables skipped       : 12138
by reimbursement type   : {'PHARMACY': 4524, 'MEDICAL': 2522}
by coherence            : {'COHERENT': 6578, 'ANOMALY': 468}
VALIDATION PASSED -- all 7046 paths satisfy every constraint
```

### Answer to the variable-depth question: yes, definitively

Leaf depth ranges from **3 to 15** decisions.

```
depth  3 :      2 leaves      <- shortest: POS rejection, no 340B
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
depth 15 :    504      <- longest: full chain on both tracks + 4 cash slots
```

Three independent causes, all confirmed in the generated data:

1. **Track short-circuit.** `ph_adjudication=REJECTED` kills payment,
   post_event, settlement and timing at once — four variables gone.
2. **Gated chains.** The 340B chain is strict: qualification gates request,
   request gates manufacturer, manufacturer gates payment. `NOT_QUALIFIED`
   stops after two decisions; a clawed-back rebate runs all five.
3. **Variable cash arity.** Cash slots per case range 0–4:

```
0 slot(s) :    143 leaves     nothing was ever declared paid
1 slot(s) :   1239
2 slot(s) :   2712
3 slot(s) :   2016
4 slot(s) :    936            payment + recoupment + rebate + clawback
```

`12138` variable-skips across the tree is the direct measure of this. If depth
were uniform, that number would be zero.

### Cross-track flags fired

```
X-1 :     36     denied/rejected reimbursement + rebate money held
X-2 :    286     reversed dispense + live rebate
X-3 :   1584     recoupment undermining 340B qualification
X-4 :      9     rebate-only failure (explicitly NOT escalated)
X-5 :    430     correlated cash gap on both tracks
X-6 :      8     total loss
X-7 :    216     appeal won after rebate clawback
```

X-3 dominating at 1584 is expected — `ph_post_event=RECOUPMENT` combines freely
with the entire qualified-340B subtree, so it multiplies out.

---

## Chunk 3 — Reconciling against 510 (`classify.py`)

**Hypothesis going in.** 7046 ≠ 510 because they measure different things: 510
counted *verdicts* (hand-curated terminal outcomes), the tree counts
*configurations*. If sound, collapsing 7046 leaves onto the curated A/B/C states
should land near 510.

**Hypothesis confirmed on the unit question, and it exposed three real errors in
the doc.**

```
configurations classified   : 7046
unmapped (classifier holes) : 0          <- every config maps to a named verdict

distinct reimbursement verdicts reached : 33   (doc claims 34)
distinct rebate verdicts reached        : 14   (doc claims 15)
distinct (reimb, rebate) PAIRS reached  : 528  (doc claims 510)
```

`0 unmapped` matters: the curated vocabulary is complete for everything the tree
can build. No configuration exists that the doc has no word for.

### Error 1 — B-17 is not a state (doc over-counts by 1)

B-17 ("paid and matched, posting date far outside the expected window") was
**never produced**. It is not a distinct reconciliation verdict — financially it
is identical to B-04. It is an *informational attribute* on a settled claim, not
an outcome the engine branches on. Modelling it as a state would have doubled
every settled-medical leaf for zero reconciliation difference.

### Error 2 — C-12 is a feed-level exception, not an episode state (over-counts by 1)

C-12 ("unmatched rebate") was **never produced**, and cannot be. An unmatched
rebate is cash with no attributable episode — by definition it has no episode to
be a state *of*. The doc already classifies orphans correctly in Section 6 as
D-3/D-4; C-12 is the same thing filed twice, once in the wrong table.

### Error 3 — two rebate states missing from the doc (under-counts by 2)

The tree produced two verdicts the curated table has no entry for:

| Generated state | Configs | Why the doc missed it |
|---|---|---|
| `qualification-pending-within-SLA` | 271 | Doc has C-01 for pending **past** SLA, but no within-SLA counterpart |
| `approved-unpaid-within-SLA` | 271 | Doc has C-11 for approved-unpaid **past** SLA, but no within-SLA counterpart |

The doc was inconsistent with itself: it gives both timing halves for C-03/C-04
(request not submitted) and C-05/C-06 (manufacturer pending), but only the
past-SLA half for qualification-pending and approved-unpaid. These are ordinary
in-flight states — not exceptions — which is presumably why they got skipped
when writing by hand. The tree does not skip things.

### Corrected arithmetic

| | Doc | Tree | Delta |
|---|---|---|---|
| Reimbursement verdicts | 34 | **33** | −1 (B-17 removed) |
| Rebate verdicts | 15 | **16** | −1 (C-12 removed), +2 (SLA gaps added) |
| Product | 510 | **528** | +18 |

```
33 × 16 = 528   and the tree reached exactly 528 distinct pairs
```

### The product is exact — and that itself is a finding

The tree reached **528 of a possible 528** pairs. Not one combination of
(reimbursement verdict, rebate verdict) is unreachable.

That is the empirical proof that **the two tracks are genuinely independent at
the verdict level**. Every cross-track constraint we identified turned out to be
a *flag*, not a *prohibition* — X-1 through X-7 mark combinations as anomalous or
correlated, but none of them makes a pair impossible.

So the pruning does not happen where the doc implied. It happens one level down:

```
configuration level : heavily constrained -> 7046 valid paths
verdict level       : completely free     -> 33 × 16 = 528, all reachable
```

The doc's claim that 36 combinations are "anomalous but representable" holds up —
468 anomaly *configurations* were generated and tagged, none pruned.

### Verdict frequency is wildly non-uniform

```
A-12 / A-13 (recoupment matched / untraceable)  936 configs each
B-15 (medical takeback)                        1560 configs
A-01 (POS rejection)                             26 configs
```

A 60× spread. This matters for the generator: sampling configurations uniformly
would flood the dataset with recoupment cases and produce roughly one POS
rejection. Sampling must be done over **verdicts**, not configurations.

---

## Chunk 4 — Independent verification (`verify.py`)

Four checks, all passing.

### 4.1 How much did validity pruning actually remove?

```
unconstrained cross-product : 326,517,350,400
valid paths in the tree     :           7,046
eliminated as impossible    : 326,517,343,354
survived                    : 0.0000022%
```

Multiplying domain sizes overstates this problem by **five orders of magnitude**.
What got eliminated: settlements on unpaid claims, rebates the manufacturer never
approved, appeals on claims that were paid in full, cash verification for money
nobody ever claimed to send. This is the concrete answer to "we are not just
doing a simple multiplication."

### 4.2 Named cases exist by construction

Twelve cases asserted to exist, all found — not assumed, located by predicate in
the generated data:

```
[PASS] happy path, pharmacy + no rebate                    4 configs
[PASS] happy path, pharmacy + rebate paid                  4 configs
[PASS] happy path, medical + rebate paid                   3 configs
[PASS] X-1: POS rejection with rebate money held           1 config
[PASS] X-1: medical denial upheld with rebate money held   1 config
[PASS] X-2: reversed dispense, rebate still live          72 configs
[PASS] X-5: correlated cash gap on both tracks           430 configs
[PASS] X-7: medical appeal won after rebate clawback     216 configs
[PASS] remittance with no cash, no rebate                 14 configs
[PASS] recoupment that cannot be traced to a deposit     936 configs
[PASS] rebate approved but never paid, past SLA          271 configs
[PASS] maximum arity: four cash slots                    432 configs
```

Note the X-1 rows: **1 configuration each.** The single most valuable compliance
case in the system is one path out of 7046. Random sampling would miss it; the
generator has to place it deliberately.

### 4.3 Impossible cases are genuinely absent

```
[PASS] pharmacy and medical on the same claim        0 found
[PASS] recoupment with no prior payment              0 found
[PASS] rebate paid without manufacturer approval     0 found
[PASS] rebate request without TPA qualification      0 found
[PASS] appeal on a fully paid claim                  0 found
[PASS] cash verified for a payment never declared    0 found
```

Zero on every one. Constraints are enforced during generation, so these were
never built rather than built-and-filtered.

### 4.4 Pair space is exact

```
reachable reimbursement verdicts : 33
reachable rebate verdicts        : 16
product                          : 528
pairs actually reached           : 528   <- EXACT
```

---

## Chunk 5 — Report and doc corrections

`REPORT.md` written. Three corrections pushed back into
`docs/reconciliation_state_space.md`, since the tree proved its 510 wrong:
remove B-17, move C-12 to the feed-level table, add the two missing within-SLA
rebate states, restate the total as 528.
