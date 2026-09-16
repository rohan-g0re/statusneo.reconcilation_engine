# Reconciliation State Space — Brute-Force Enumeration

> **Vocabulary:** *claim*, *episode*, *track*, *record*, *verdict*, *disposition* and *reason code* are defined once in [`glossary.md`](glossary.md). That file wins wherever this one is loose.

**Purpose.** Enumerate every reconciliation outcome the deterministic engine can encounter, so we know the real scale of the problem before writing code. Every possibility listed here must also be narratable by the agent layer.

**Scope.** Happy path + all failure modes within the assignment's four feeds. Multi-tenancy and multi-source scale are deliberately excluded and parked in Section 7.

**Headline numbers.**

> **Verified programmatically.** Every figure below was confirmed by exhaustive
> decision-tree generation in [`../decision_tree/`](../decision_tree/REPORT.md),
> which builds all 4,224 valid configuration paths and validates each choice
> against the choices above it. Two passes changed this document from its
> original hand-count: an exhaustive-generation pass that corrected three
> hand-count errors, and a later pass that removed SLA thresholds from the
> design entirely as a deliberate architectural decision, not a correction.
> Both are logged in Section 9.

| Measure                                                            | Count               |
| ------------------------------------------------------------------- | ------------------- |
| Unconstrained cross-product                                        | 12,093,235,200       |
| Valid configurations                                               | **4,224**       |
| — logically coherent                                              | 3,860                |
| — compliance anomalies (data that should not exist)               | 364                  |
| Eliminated as impossible                                           | 12,093,230,976 (survived: 0.00003%) |
| Reachable reimbursement verdicts                                   | **31**          |
| Reachable rebate verdicts                                          | **12**          |
| Reachable verdict pairs (31 x 12, exact product, all reachable)   | **372**         |
| Per-track states the engine actually branches on                  | **43**          |
| Cross-track compliance rules                                       | **7**           |
| Feed-level data-integrity exceptions (episode-independent)        | **7**           |
| **Total deterministic rules to implement**                  | **50**          |
| If-checks evaluated per case (min / average / max)                 | 10 / 21.0 / 32       |

The 372 is a *composition*, not a switch statement. Read Section 5 before panicking. The old 9-value status enum is also gone — see Section 0a for what replaced it.

---

## 0. How an episode is shaped

An episode is one filed claim for one dispense or administered service. Hanging off it:

```
EPISODE (one filed claim)
  ├── Reimbursement track — EXACTLY ONE of: pharmacy | medical    (XOR, mandatory)
  ├── 340B rebate track   — present | absent                      (optional)
  └── Cash verification   — NOT a track. An attribute of every expected
                            money movement: matched | partial | absent | orphan
```

### Why reimbursement is an XOR, not two independent flags

The four source feeds tempt you into modelling pharmacy and medical as two independent optional components. They are not independent. A drug travels exactly one billing road — pills and self-administered drugs go to the PBM, infused and clinician-administered drugs go to the medical payer. Billing the same dispense down both roads is duplicate billing. That is fraud, not an exception, and the model should make it unrepresentable rather than flag it after the fact.

Two free booleans would give 17 × 16 = 272 reimbursement configurations (each track either off or in one of its states). The XOR gives 31 (16 + 15, mutually exclusive so states sum rather than multiply). The difference is entirely states that cannot legally exist.

### Why the reimbursement track is mandatory, and 340B is not

The assignment settles this; it is not a modelling choice.

**Reimbursement is mandatory.** Section 1 opens with *"The prototype begins after a claim has been filed. Upstream activities such as patient intake, benefits investigation, prior authorization and claim creation are outside the assignment."* A filed claim is the precondition for an episode existing. Reinforced by "claim-centric object," by "at least 50 **claim** financial episodes," and by the framing "Once a claim has been filed, the operator needs to understand…". Every episode therefore carries exactly one reimbursement track.

**340B is optional.** Stated directly: *"A single claim **may** create pharmacy-benefit reimbursement, **a** 340B rebate, **or** a medical-benefit payment / denial pathway."* The rebate track is present or absent, which is what C-00 encodes.

| Reimbursement configuration  | States       |
| ---------------------------- | ------------ |
| Pharmacy benefit (Section 1) | 16           |
| Medical benefit (Section 2)  | 15           |
| **Total**              | **31** |

**Out of scope, deliberately: the no-claim dispense.** A drug dispensed with no claim ever filed — cash-pay patient, or a submission that failed silently — is real revenue leakage, and a 340B track can still be live off the dispense record. Detecting it requires anchoring episodes on dispenses rather than on claims, which contradicts the assignment's stated boundary and its claim-centric business object. Excluded here; carried as a one-line assumption in the design note instead.

### Why cash/bank is not a fourth dimension

The bank feed is always present as a *feed*. A bank record per episode is not — and its absence is the single most common exception in the system. The assignment names it twice in the pathways table: "remittance with no cash" and "unmatched deposit." A model in which cash is guaranteed present cannot represent A-05, B-05 or C-09, which are three of the most valuable exceptions available.

Structurally, cash is also not a peer of the other three. Pharmacy, medical and 340B are *claims on money*. Bank is *verification that a claim on money resolved*. That verification is already encoded inside every track state in Sections 1–3 — A-04 is "paid, cash matched," A-05 is "paid, no cash." Same payment event, different cash verdict. Multiplying cash in as a separate dimension would double-count what the track states already carry.

The one genuinely episode-external cash case is the orphan deposit — money with no attributable claim. That is a feed-level exception (D-3), not an episode state, because by definition it has no episode to attach to.

### The state space

```
(pharmacy 16 + medical 15) × (340B 12, including "absent")
= 31 × 12
= 372
```

Exhaustive generation reached **372 of a possible 372** verdict pairs — every
combination is achievable. The two tracks are therefore fully independent at the
verdict level, and all seven cross-track rules in Section 4 are *annotations*,
never *prohibitions*. The engine needs no cross-track validity table.

Three orthogonal overlays sit on top and are *not* multiplied in — they are feed-level, not episode-level: duplicate delivery, late arrival, orphan records. Section 6.

---

## 0a. The disposition model — and why aging is not a verdict input

An earlier draft of this document gave every "awaiting X" state two halves — within SLA and past SLA — and treated the SLA breach itself as a distinct verdict. That is gone. Aging is no longer a **verdict** dimension; it is a **read-time sort key** over the pending and exception lists. A-02 does not become a different state at day 31 than it was at day 29. It stays "awaiting payment" for as long as no new inbound record arrives, and the operator sorts that list oldest-first.

**Why this is the right cut, not a shortcut.**

- **It makes incremental processing provably complete, not merely convenient.** If disposition can only change in response to a new inbound record, then a claim with no new record since the last run cannot have a different disposition now. That is not an optimisation you hope is safe — it is a property you can state and prove: the set of claims that need recomputing on any given run is exactly the set touched by new events. Nothing else can have moved. That is the real payoff of dropping SLA from the verdict layer, and it is worth more than any threshold tuning.
- **It removes a category of number to defend.** "Past SLA" requires picking 30 days, or 45, or something else, for every source and pathway, and then justifying it in review. A sort key needs no such number — age is displayed, not adjudicated.
- **It is a deliberate exclusion, not an oversight.** Real timing rules exist in the industry: Medicare's 30-day payment ceiling, state prompt-pay statutes (commonly 30 or 45 days), Iowa's 20-day PBM-specific rule, CAQH CORE Operating Rule 370's ±3-business-day remittance window, and the 340B pilot program's 45-day submission window with a 10-day manufacturer-payment window. They are real, and they are **policy** — never a field on a claim record. If a future requirement (SLA-breach alerting, an escalation timer, a contractual penalty calculation) reintroduces them, they belong in engine configuration, looked up per tenant/source/pathway, never as a literal threshold inside a verdict rule. Section 7 covers the config-vs-literal distinction in full.

### Three dispositions, and only three

Status answers one question — "what do I do with this claim?" — and there are exactly three answers:

| Disposition  | Meaning                                    |
| ------------ | ------------------------------------------- |
| `CLOSED`    | Nothing to do.                              |
| `PENDING`   | Waiting on an external party. No defect. Ranked by age. |
| `EXCEPTION` | A defect exists. Work it.                   |

That is the entire enum. Everything else — what kind of defect, how much money, which track — lives one layer down.

### Reason codes carry the detail, and they are a list

A claim can be underpaid **and** missing settlement **and** show a cash gap at the same time, and with two tracks in play it can be broken on one and merely waiting on the other. A single reason field cannot hold that; a list can:

```
disposition = EXCEPTION
reasons = [UNDERPAID, CASH_MISMATCH]
```

`INSUFFICIENT_DATA` is a first-class reason code, not an error path. It fires when the engine deterministically cannot decide — an unmatched recoupment (A-13), rebate cash with no attributable episode (D-4), or a correlated no-cash finding on both tracks at once (X-5) are the three worked examples elsewhere in this document. That code is exactly what should drive the agent layer's required behaviour of saying "I cannot determine this from the available feeds, and here is precisely what is missing" instead of guessing.

### Reopened is a flag, not a fourth disposition

A reopened claim still either waits or gets worked — it does not need a fourth bucket. What it needs is provenance:

```
reopened_from            # the disposition it was reopened out of
previously_closed_at
reopened_on
```

These live as attributes on the exception (or the pending record), not as a parallel queue. Two queues would mean two workflows, and claims bouncing between them on every reconciliation run. One queue, one workflow, an attribute that says how it got there.

The flag also drives priority, deterministically: reopened-from-closed outranks never-paid, because in the reopened case the money was already recognised and is now at risk of being un-recognised — a stronger claim on attention than money that was simply never collected. And reopening is not exclusively a fall from `CLOSED`: a claim can be reopened out of `PENDING` too — for example, a 340B rebate clawed back while the reimbursement side is still legitimately in flight. The flag records where it came from either way.

### Status exists at two levels

Per track, and per episode as a rollup where the worst status wins:

```
EXCEPTION > PENDING > CLOSED
```

An episode can sit in the exception queue because one track is broken while the other track is legitimately still waiting — the rollup does not average the two tracks or hide the healthy one, it surfaces the worse of the two, because that is the one that needs a human.

### Disposition and reasons are computed together, in one pass

Not two passes where a first rule decides EXCEPTION and a second rule figures out why. The rule that decides is the rule that knows why, because it is the same rule:

```
if received < expected:
    disposition = EXCEPTION
    reason = UNDERPAID
    variance = expected - received
```

One rule, three outputs. Splitting "decide" from "explain" into separate passes would let them drift out of sync — a defect flagged without a reason, or a reason attached to a claim the first pass called clean.

### Nothing is written back to source records

Pharmacy, medical, 340B and bank records are immutable — they are what actually happened, as reported by an external system, and this design does not get to edit them. Disposition, reasons and variance are computed fields on a **derived verdict**, recomputed per (claim, cursor) on every run, never stored as truth on the raw rows. Re-running reconciliation over the same source data must always produce the same verdict; that is only possible if the verdict is a pure function of the immutable source, not a mutation of it.

### What this replaces

The earlier design collapsed everything to a flat 9-value status enum (`RECONCILED`, `IN_FLIGHT`, `AWAITING_PAYMENT`, `UNDERPAID`, `OVERPAID`, `DENIED`, `CASH_MISMATCH`, `CLAWBACK`, `COMPLIANCE_FLAG`). That enum conflated two layers that behave differently — disposition (three values, one per claim) and reason (a list, because defects compose). The split above is what replaced it.

---

## 1. Track A — Pharmacy benefit reimbursement (PBM)

Source events: adjudication, payment, reversal, adjustment, settlement.

Track-local constraints:

- Adjudication rejection is real-time. If rejected, nothing downstream can exist.
- Recoupment requires a prior payment. You cannot claw back money never sent.
- Reversal is pharmacy-initiated; recoupment is PBM-initiated. Same numeric shape, different actor, different action.
- Settlement is a separate confirmation event from payment. Payment without settlement is a real state.

| #              | Possibility                                    | What is TRUE                                                                             | What is FALSE                                                                                                     |
| -------------- | ---------------------------------------------- | ---------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------- |
| **A-01** | Rejected at point of sale                      | Adjudication rejected with a reject code; expected = 0; received = 0; drug not dispensed | No payment, no reversal, no recoupment, no adjustment, no settlement, no cash, no 340B qualification should exist |
| **A-02** | Awaiting payment                               | Adjudication accepted; expected = E; received = 0; no defect                             | Not an exception; no remittance yet; no cash — aged at read time for queue sorting, not branched on for verdict   |
| **A-04** | Fully reconciled ✅                            | Payment = E; bank deposit matched; settlement confirmed; outstanding = 0                 | No variance, no reversal, no recoupment, no open action                                                           |
| **A-05** | Remittance with no cash                        | Remittance states paid = E; bank shows no matching deposit; outstanding = E              | Not a denial, not an underpayment — the payer*claims* it paid                                                  |
| **A-06** | Paid and matched, settlement missing           | Payment = E; cash matched; settlement event absent                                       | Money is not in dispute; only the closing confirmation is missing                                                 |
| **A-07** | Underpayment, cash matched                     | Payment = P where 0 < P < E; cash matched to P; variance = E − P                        | Not a denial; not a timing issue; the payer paid and paid short                                                   |
| **A-08** | Underpayment with no cash                      | Remittance states P < E; no deposit found; outstanding = E                               | Compound failure — two independent defects on one track                                                          |
| **A-09** | Overpayment                                    | Payment = P where P > E; cash matched                                                    | Not a duplicate payment (single payment event); liability to refund exists                                        |
| **A-10** | Reversed by pharmacy, money returned           | Pharmacy-initiated reversal; prior payment returned; negative cash matched; net = 0      | Drug was not dispensed to the patient; no 340B rebate should stand                                                |
| **A-11** | Reversal recorded, money not returned          | Reversal event exists; no offsetting negative cash                                       | Books still show cash we are not entitled to                                                                      |
| **A-12** | Recouped after audit, netted and matched       | PBM audit clawback; amount netted out of a later batch; offset traced                    | Not pharmacy error necessarily; often retroactive eligibility termination                                         |
| **A-13** | Recoupment not traceable to a deposit          | Recoupment line exists; cannot tie it to any bank movement                               | Cannot confirm the clawback actually happened; may be double-counted                                              |
| **A-14** | Contractual adjustment, remainder settled      | Adjustment reduces expected to E'; payment = E'; cash matched                            | Not an underpayment — the shortfall is contractually explained                                                   |
| **A-15** | Adjustment applied, variance still unexplained | Adjustment exists; payment ≠ adjusted expected; residual variance                       | The adjustment does not account for the full gap                                                                  |
| **A-16** | Reversed before payment                        | Claim cancelled pre-payment (patient never collected); expected → 0                     | No money ever moved; nothing to claw back                                                                         |
| **A-17** | Duplicate payment                              | Two payment events, same claim, same amount; cash shows both                             | Not an overpayment by contract — it is the same payment twice; distinguish from A-09                             |

**Subtotal: 16 states.**

> **A-03 retired.** "Awaiting payment, past SLA" was the past-SLA half of a pair
> whose within-SLA twin, A-02, now covers both. Aging is no longer a verdict
> dimension (Section 0a) — a claim with no new inbound record stays "awaiting
> payment" indefinitely, aged at read time for sorting, never re-verdicted for
> the passage of time alone.

---

## 2. Track B — Medical benefit reimbursement (835)

Source events: 837 / clearinghouse status, payer response, 835 remittance, denial / appeal status.

Track-local constraints:

- **The drug was already administered.** Unlike pharmacy, a billing failure here does not mean the dispense didn't happen. This asymmetry matters for 340B validity — see Section 4.
- Appeal only exists downstream of a denial or a partial payment.
- Appeal won → a new or corrected 835 is expected, which restarts the payment/cash sequence.

| #              | Possibility                                           | What is TRUE                                                                                  | What is FALSE                                                                                                                                      |
| -------------- | ----------------------------------------------------- | --------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| **B-01** | Clearinghouse rejection                               | 837 rejected before reaching the payer; no claim on file with payer; expected uncollected = E | Not a denial — the payer never saw it; no appeal path; resubmission is the action. Drug**was** administered, so 340B may legitimately exist |
| **B-02** | Submitted, awaiting 835                               | Accepted by clearinghouse; no payer response; no defect                                       | Not an exception — aged at read time for queue sorting, not branched on for verdict                                                                |
| **B-04** | Paid in full, cash matched ✅                         | 835 shows paid = E; deposit matched; outstanding = 0                                          | No adjustment, no appeal, no open action                                                                                                           |
| **B-05** | 835 paid, no cash                                     | 835 shows paid = E; no matching deposit                                                       | Payer paperwork and payer money disagree                                                                                                           |
| **B-06** | Partial payment, no appeal filed                      | 835 shows P < E with adjustment reason codes; cash matched                                    | Not a denial; appeal window may still be open and unused                                                                                           |
| **B-07** | Partial payment, appeal pending                       | P < E; appeal filed; outcome unknown                                                          | Balance is neither collectible nor written off yet                                                                                                 |
| **B-08** | Partial, appeal won, balance paid                     | Second 835 covers the balance; cash matched; outstanding = 0                                  | Closed, but through a two-payment path — cash allocation must handle both                                                                         |
| **B-09** | Partial, appeal lost                                  | Appeal denied; residual is uncollectible                                                      | Not an open receivable — it is a write-off decision                                                                                               |
| **B-10** | Denied, no appeal filed                               | 835 denial with a reason code; received = 0; appeal window open                               | Not a timing issue; someone must decide appeal vs write-off                                                                                        |
| **B-11** | Denied, appeal pending                                | Denial + appeal filed; outcome unknown                                                        | Not closed; not collectible yet                                                                                                                    |
| **B-12** | Denied, appeal won, paid and matched                  | Corrected 835; payment = E; cash matched                                                      | Fully recovered                                                                                                                                    |
| **B-13** | Denied, appeal lost                                   | Appeal exhausted; expected → 0                                                               | Terminal loss; the cost of goods is unrecovered                                                                                                    |
| **B-14** | Appeal won, payment never arrived                     | Appeal upheld; corrected 835 issued; no cash                                                  | Payer agreed and still did not pay — highest-value chase                                                                                          |
| **B-15** | Paid then payer takeback                              | Post-payment recoupment on the medical side; may or may not be traceable to a deposit         | Same shape as A-12/A-13 but a different counterparty and dispute process                                                                           |
| **B-16** | Duplicate 835 for the same claim                      | Two remittance records, same claim, same payment                                              | Only one payment actually occurred — double-counting risk if not deduplicated                                                                     |

**Subtotal: 15 states.**

> **B-17 removed.** "Paid and matched, posting date far outside the window" was
> in an earlier draft as a 17th state. Exhaustive generation never produced it,
> because financially it is identical to B-04 — expected equals received, cash
> matched, nothing outstanding. It is an informational attribute on a settled
> claim, not a reconciliation verdict. Carry it as a `days_to_settle` field, not
> as a status.

> **B-03 retired.** Same reasoning as A-03: "Submitted, 835 missing, past SLA"
> was the past-SLA half of a pair whose within-SLA twin, B-02, now covers both.
> See Section 0a.

---

## 3. Track C — 340B rebate

Source events: TPA qualification, rebate request / status, manufacturer payment.

Track-local constraints:

- Two decision-makers: the **TPA** decides qualification, the **manufacturer** decides approval and pays. Two independent failure points.
- Request requires qualification. Manufacturer decision requires a submitted request. Payment requires approval.
- Rejection and payment are mutually exclusive — if both appear, the data is anomalous.

| #              | Possibility                                         | What is TRUE                                                                                                       | What is FALSE                                                            |
| -------------- | --------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------ |
| **C-00** | No 340B track                                       | Dispense not 340B-eligible, or entity is not a covered entity; expected rebate = 0                                 | Nothing to reconcile on this track; absence is not an exception          |
| **C-01** | Qualification pending                               | Dispense sent to TPA; no qualification decision; no defect                                                         | Not an exception — aged at read time for sorting; stuck at the first gate is a reason code, not a timer            |
| **C-02** | Not qualified                                       | TPA evaluated and declined (patient definition, prescriber affiliation, or drug not eligible); expected rebate = 0 | No request, no manufacturer involvement; correct outcome, not a failure  |
| **C-03** | Qualified, request not yet submitted                | Qualification confirmed; expected rebate = R; request queued; no defect                                            | Not an exception — aged at read time for sorting                         |
| **C-05** | Request submitted, manufacturer pending              | Request filed; awaiting decision; no defect                                                                        | Not an exception — aged at read time for sorting                         |
| **C-07** | Manufacturer rejected                               | Explicit rejection with a reason; expected rebate → 0 unless corrected and resubmitted                            | Not a TPA problem; the qualification stood, the manufacturer disputed it |
| **C-08** | Approved, paid, cash matched ✅                     | Approval + payment = R; deposit matched; outstanding = 0                                                           | Fully recovered on the rebate side                                       |
| **C-09** | Approved and paid per TPA, no cash                  | TPA reports paid; no matching deposit                                                                              | The TPA's ledger and the bank disagree                                   |
| **C-10** | Approved, partial rebate paid                       | Payment P < R; cash matched; variance = R − P                                                                     | Usually a unit/price dispute, not a qualification dispute                |
| **C-11** | Approved, awaiting payment                          | Approval on record; no payment yet; no defect                                                                      | Not an exception — aged at read time for sorting; manufacturer agreed, timing alone is not a defect                |
| **C-13** | Rebate clawed back                                  | Rebate previously paid, then reversed by the manufacturer (duplicate-discount finding, audit, or dispute)          | Net rebate = 0; the original qualification is now contested              |
| **C-14** | Duplicate rebate payment                            | Two rebate payments for one dispense                                                                               | Refund liability; distinguish from a legitimate two-part payment         |

**Subtotal: 12 states (including C-00 "absent").**

> **C-01a and C-11a added; C-12 removed.** *(Earlier pass.)* The original table
> gave both timing halves for C-03/C-04 and C-05/C-06 but only the past-SLA half
> for qualification-pending and approved-unpaid — an inconsistency the
> exhaustive generator caught, since both in-flight states were reachable and
> each accounted for 271 configurations. C-12 ("unmatched rebate") moved to
> Section 6 as D-4: cash with no attributable episode cannot be a state *of* an
> episode.

> **C-01a, C-11a, C-04, C-06 retired.** *(SLA-removal pass, this document.)*
> Aging is no longer a verdict dimension (Section 0a). C-01a and C-11a — the
> within-SLA twins added in the correction above — merge back into C-01 and
> C-11, which are now unified, age-agnostic states. C-04 ("qualified, request
> stuck, past SLA") and C-06 ("request submitted, manufacturer silent, past
> SLA") are gone the same way A-03 and B-03 are gone: they were the past-SLA
> halves of C-03 and C-05, whose within-SLA names now cover both. Net for this
> track: 16 states → 12. See Section 9 for the full history.

---

## 4. Cross-track compliance rules

These fire on the *combination* of two track verdicts. They are the reason the episode is the unit of reconciliation rather than the track.

| #             | Combination                                                                 | What is TRUE                                                                                                           | What is FALSE                                                    | Why it matters                                                                      |
| ------------- | --------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------- | ----------------------------------------------------------------------------------- |
| **X-1** | Reimbursement denied/rejected**+** rebate paid                              | Net position is negative (cost of goods out, no reimbursement, rebate held); rebate rests on a claim the payer refused | Not two independent exceptions — one causal story               | Duplicate-discount and eligibility exposure. Repayment risk in audit.               |
| **X-2** | Pharmacy reversal (A-10/A-16)**+** rebate paid or approved            | Drug was never dispensed to the patient; the qualifying event does not exist                                           | The rebate is not merely unmatched — it is invalid              | Rebate must be unwound proactively, not discovered by the manufacturer              |
| **X-3** | Recoupment for retroactive ineligibility (A-12)**+** rebate qualified | Qualification logic consumed claim facts that are now known to be wrong                                                | The 340B track is not independently healthy despite showing paid | Qualification may need re-evaluation and the rebate reversed                        |
| **X-4** | Reimbursement settled**+** rebate rejected                                  | Reimbursement track is clean; only the rebate failed                                                                   | No cross-track implication; do not escalate as compliance        | Common. Explicitly a*non*-flag, so the engine does not over-report                |
| **X-5** | Reimbursement no-cash (A-05/B-05)**+** rebate no-cash (C-09)          | Both tracks show paperwork-without-money on the same episode                                                           | Almost certainly*not* two payers independently failing         | Correlated root cause — likely a bank feed gap or an allocation defect on our side |
| **X-6** | Reimbursement denied/lost**+** rebate rejected                              | Total loss on the episode; cost of goods unrecovered from every channel                                                | No remaining collection path                                     | Write-off decision, and a signal to check upstream eligibility screening            |
| **X-7** | Medical appeal won (B-12)**+** rebate previously clawed back (C-13)   | The claim was valid after all; the basis for the clawback may be void                                                  | The rebate reversal is not necessarily final                     | Re-request opportunity — recoverable money nobody is watching                      |

**Subtotal: 7 cross-track rules.**

---

## 5. The combination count, and why you do not implement 372 branches

### The arithmetic

```
Reimbursement states:  16 (pharmacy) + 15 (medical)   =  31
340B states:           12 (including "absent")
Total representable:   31 × 12                        = 372
```

### Split into coherent vs anomalous

**Four** pharmacy verdicts mean the dispense did not happen: A-01 (rejected at POS), A-10 (reversed after payment, money returned), A-11 (reversal recorded, money not returned) and A-16 (reversed before payment). An earlier hand-count listed only three — it missed A-11, which is a no-dispense state just as much as A-10 is. For those four, the only coherent 340B verdicts are C-00 (absent), C-02 (not qualified) and C-13 (rebate correctly unwound).

Medical is different — the drug was administered before billing, so **all 15 medical states are compatible with a live 340B track**, including B-01.

Generation confirms the split at the underlying-configuration level: of the **4,224** valid configurations, **3,860** are logically coherent and **364** are compliance anomalies — produced entirely by the four no-dispense pharmacy verdicts above paired with a 340B verdict that should not survive them.

Those 364 are not noise to be filtered out. They are the highest-value exceptions in the system — data that should be impossible, which is exactly what an exception engine exists to surface.

### What actually gets built

The engine does **not** enumerate 372 cases. It resolves each track independently, then applies cross-track rules:

```
episode_status = compose(
    reimbursement_verdict,   # one of 31, from 31 track rules
    rebate_verdict,          # one of 12, from 12 track rules
    cross_track_flags        # subset of 7
)
```

| What                                | Count        |
| ----------------------------------- | ------------ |
| Pharmacy track rules                | 16           |
| Medical track rules                 | 15           |
| 340B track rules                    | 12           |
| Cross-track compliance rules        | 7            |
| **Total deterministic rules** | **50** |

50 rules generate 372 outcomes, evaluated as 10-32 if-checks per case (average 21.0 — Section 9 has the stage-by-stage breakdown). That compositional property is worth stating out loud in the design note and in the walkthrough — it is the difference between an architecture and a switch statement.

### Disposition, not a status enum

An earlier draft collapsed everything to a flat 9-value status enum. That conflated two layers that behave differently: *disposition* (what to do — `CLOSED` / `PENDING` / `EXCEPTION`) and *reason* (why — `UNDERPAID`, `CASH_MISMATCH`, `INSUFFICIENT_DATA`, and so on). A claim can carry several reasons at once and only one disposition; a single enum value cannot express "exception, underpaid and cash-mismatched." Section 0a has the full model: three dispositions, a reason-code list, the reopened flag, and the two-level (track / episode) rollup.

### What this means for the agent layer

The agent must be able to narrate any of the 372 verdict pairs, but it never enumerates them either. It reads two track verdicts plus cross-track flags and composes an explanation the same way the engine composes a disposition.

Practical consequences:

- **Eval coverage.** 10 required scenarios cannot cover 372. Cover the shape instead: 2 happy paths (one per reimbursement type), the 6 required edge cases, and 2 cross-track compliance cases. X-1 and X-2 are the strongest demo material because they are invisible to any single-track system.
- **Insufficiency behavior.** A-13, D-4 and X-5 are states where the correct agent answer is "I cannot determine this from the available feeds, and here is precisely what is missing" — the `INSUFFICIENT_DATA` reason code from Section 0a. Section 3 of the assignment requires that behavior — these states are where you demonstrate it.
- **Prioritization is deterministic.** Ranking exceptions by value/age/category is a sort over these verdicts, with the reopened-from-closed rule from Section 0a taking precedence. The agent explains the ranking; it does not produce it.

### What this means for the generator

One generator, one seed, two profiles:

| Profile | Episodes | Purpose |
| ------- | -------- | ------- |
| `demo` | ~60, hand-stratified | The walkthrough. The debrief asks you to trace **one** claim end to end — nobody does that against a 1,500-episode file. Curated to include both happy paths, the required edge cases, and the strongest cross-track compliance cases (X-1, X-2). One episode per edge case is guaranteed whatever the seed; the mix around them is sampled, so the queue counts move between rebuilds (Decision 14, amended). |
| `full` | ~1,500 | What the test suite asserts against. Large enough to hit all 372 verdict pairs at least once. |

Coverage is not a hope here, it is an assertion. A coverage test enumerates the 372 reachable verdict pairs and **fails** if the `full` profile does not produce every one of them. That turns this document from a design reference into a test oracle — the state space stops being a claim about the system and becomes a check on it.

---

## 6. Feed-level data-integrity exceptions

Orthogonal to episode state. These are properties of *ingestion*, not of a claim, which is why they are not multiplied into the 372. They are handled in the source-adapter / normalization band and can co-occur with any episode state.

| #             | Exception                           | What is TRUE                                                                         | What is FALSE                                                                                                  |
| ------------- | ----------------------------------- | ------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------- |
| **D-1** | Duplicate source record delivered   | The same source event arrives twice (SFTP job rerun, retry after ambiguous timeout)  | Not two real events — idempotency key must collapse them, not sum them                                        |
| **D-2** | Late-arriving / out-of-order record | A bank deposit lands before the remittance that explains it                          | Not an unmatched deposit — it becomes matchable once the later record arrives; the engine must be re-runnable |
| **D-3** | Orphan bank deposit                 | Cash received with no linkable claim, rebate or remittance                           | Not revenue we can recognize; may be a crosswalk failure rather than genuinely unattributable                  |
| **D-4** | Orphan / unmatched rebate           | Rebate cash or a rebate event that cannot be tied to any qualified dispense in any other feed | Not a windfall — cash held without provenance is audit exposure. Absorbs what an earlier draft filed as episode state C-12; an unattributable rebate has no episode to be a state *of* |
| **D-5** | Malformed or unparseable record — ⚠️ **not generated** | Schema version mismatch, missing required field, bad type | **Dropped from the dataset (C16).** Checked against the assignment: its data requirement names eight edge cases and malformed records is not among them. The only nearby text is a design-note question about schema versions and retries — prose, not generated data. Quarantine survives as production behaviour described in the design note; nothing generates or tests it here |
| **D-6** | Crosswalk miss                      | Claim exists in one feed; the corresponding identifier cannot be resolved in another | The claim is not necessarily missing — the*mapping* failed. Different fix, different owner                  |
| **D-7** | Lump-deposit allocation residual    | A single ACH covers N claims; allocated amounts do not sum to the deposit total      | The deposit is not wrong; the allocation is incomplete. Residual must be tracked, not absorbed                 |

**Subtotal: 7.**

---

## 7. Scale and tenancy — why the rule count is invariant

Excluded from the counts above, and this section argues why that exclusion is correct rather than convenient. This is Section 2's "Scale and tenancy" box: *how the design extends to additional TPAs, payers, health systems and reporting variants through configuration rather than one-off code.*

### The structural reason

None of the scale dimensions is an **input to the reconciliation question**. The engine only ever asks three things: what was expected, what arrived, and does the difference have an explanation. Which PBM sent the money does not change the shape of that question — it changes the vocabulary the answer arrives in.

A reconciliation rule is invariant under a scale dimension when that dimension appears in the rule only as a *lookup*, never as a *branch*.

| Dimension                          | New rules? | What actually grows                                                                 |
| ---------------------------------- | ---------- | ------------------------------------------------------------------------------------- |
| Multiple health systems / tenants  | No         | Rows, plus one scoping predicate. `tenant_id` on every record; isolation at the query layer |
| Multiple PBMs                      | No         | Reject-code mapping rows; SLA values; field mappings                                  |
| Multiple TPAs                      | No         | Qualification-vocabulary rows. The chain qualification → request → manufacturer → payment gains no link |
| Multiple payers (medical)          | No         | Denial/adjustment reason-code (CARC/RARC) mapping rows; appeal *level* becomes an attribute, not a new verdict       |
| Multiple bank accounts / lockboxes | No         | Allocation scope only. The residual rule (D-7) stays one rule                         |
| Reporting variants                 | No         | Output templates. Zero engine contact                                                 |
| Source schema versions             | No         | Adapter versions; the raw layer preserves the original payload                        |

Worked example: PBM A rejects with code `70`, PBM B with `A1`. Both mean rejected, both resolve to A-01. The mapping table gains a row; `if adjudication == REJECTED` is untouched. Multi-level appeals are the same story — Medicare has five levels where a commercial payer has one, but `PENDING` covers any level and `LOST` means exhausted. Level is a field, not a verdict, because the reconciliation answer (collectible or not) is identical either way.

**50 rules, at any number of tenants, payers, TPAs and banks.**

### Three limits on that claim, stated rather than hidden

**1. Aging is deliberately not a rule input — and if it ever becomes one, it must be config, never a literal.**

An earlier design had `if age_days > 30` gating a verdict. Section 0a explains why that is gone: aging dropped out of the disposition layer entirely and became a read-time sort key instead, which is what makes a claim with no new inbound record provably unable to change disposition. That is not only a scaling concern — it is the property that makes incremental processing correct — but it also happens to remove an entire class of scaling risk before it starts, because a threshold that does not exist cannot fork per payer.

Real timing rules do exist in the industry, and they should not be mistaken for oversight: Medicare's 30-day payment ceiling, state prompt-pay statutes (commonly 30 or 45 days), Iowa's 20-day PBM-specific rule, CAQH CORE Operating Rule 370's ±3-business-day remittance window, and the 340B pilot program's 45-day submission window with a 10-day manufacturer-payment window. If a future requirement — SLA-breach alerting, an escalation timer, a contractual penalty calculation — reintroduces them, they must be config, never a literal buried in a verdict rule:

```python
if age_days > 30:                              # literal -> a verdict rule forks per payer
```

```python
if age_days > sla_for(tenant, source, pathway): # config -> same rule, one new row
```

Values that must be configuration and never literals, if they return at all: SLA/escalation windows per source and pathway, underpayment tolerance (what counts as a variance versus rounding), cash-match tolerance, and aging-bucket boundaries for the read-time sort. Hardcode any of them and rule-count invariance is theoretically true and operationally worthless.

**2. Row growth is large and is the highest-maintenance surface.**

A real 835 carries hundreds of claim-adjustment and remittance-advice reason codes (CARC/RARC), per payer. The mapping tables are where the ongoing work actually lives. "Configuration rather than one-off code" is not a claim that the config is trivial — it is a claim that config rows are cheap to add and reviewable in bulk, whereas code branches are neither.

**3. A genuinely new event *semantics* does add states. Scale is not homogeneous.**

Adding a fifth PBM is rows. Adding a payer that does **capitation** or **bundled payments** is not — neither is fee-for-service, so there is no expected-per-claim amount to reconcile against and the entire expected-vs-actual frame does not apply. Interim payments and split-billing have the same problem.

The containment mechanism: adapters map into a **closed** canonical event vocabulary. An event that does not map is quarantined with its lineage intact, never coerced into the nearest-looking slot — production behaviour described in the design note, not something this prototype generates (C16). New *codes* are configuration; new *semantics* are a canonical-model change, and the system should refuse to guess which it is looking at.

That boundary is worth stating explicitly in the walkthrough. Claiming a design absorbs anything is weaker than showing where it stops and how it fails safe when it gets there.

### One tenancy case that would add a rule

If a shared lockbox could receive a single deposit covering claims from two tenants, cash allocation would need a cross-tenant splitting rule — a genuinely new rule, and one that also breaches isolation. The design forecloses it: bank accounts are tenant-scoped, so allocation never spans a tenant boundary. Worth naming because it is the one place where a scale dimension nearly does become a reconciliation input.

---

## 8. Summary

| Layer                                                   | Count                             |
| ------------------------------------------------------- | --------------------------------- |
| Pharmacy benefit states                                 | 16                                |
| Medical benefit states                                  | 15                                |
| 340B rebate states (incl. absent)                       | 12                                |
| Cross-track compliance rules                            | 7                                 |
| Feed-level data-integrity exceptions                    | 7                                 |
| **Deterministic rules to implement**              | **50**                      |
| Reachable verdict pairs                                 | 372 (31 × 12, exact product, all reachable) |
| Valid configurations                                    | 4,224 (3,860 coherent + 364 anomalous) |
| Unconstrained cross-product                             | 12,093,235,200                    |
| Dispositions                                            | 3 (`CLOSED` / `PENDING` / `EXCEPTION`) — reason codes carry detail, Section 0a |
| Generator profiles                                      | `demo` ~60 episodes (curated walkthrough), `full` ~1,500 (asserts all 372 pairs) |

**Cash/bank is deliberately absent from this table.** It is not a track and not a dimension — it is a verification attribute already encoded inside every state in Sections 1–3, plus two feed-level exceptions (D-3, D-7). See Section 0.

---

## 9. Corrections log — what exhaustive generation changed, and what a later design decision changed

The figures in this document were originally hand-counted. Two separate passes
moved them since, and they are logged here in order because they are different
in kind — the first pass fixed mistakes, the second pass changed the design.

### First pass — exhaustive generation corrected the hand-count

The decision-tree generator in [`../decision_tree/`](../decision_tree/REPORT.md)
built all 7,046 valid configuration paths, validating each choice against the
choices above it, and disagreed with the hand-count in three places. All three
were hand-count errors.

| # | Error | Effect |
|---|---|---|
| 1 | **B-17 was not a distinct verdict.** "Paid and matched, posting date far outside the window" is financially identical to B-04. Never generated. | Medical states 17 → 16 |
| 2 | **C-12 was filed in the wrong table.** An unmatched rebate is cash with no attributable episode, so it cannot be an episode state. Merged into D-4. | 340B states 15 → 14 |
| 3 | **Two in-flight 340B states were missing.** The table gave both timing halves for C-03/C-04 and C-05/C-06 but only the past-SLA half for qualification-pending and approved-unpaid. Added as C-01a and C-11a, 271 configurations each. | 340B states 14 → 16 |
| 4 | **A-11 was miscounted as a dispense-occurred state.** "Reversal recorded, money not returned" is a no-dispense state exactly as A-10 is. | Anomalous pairs 36 → 52 |

Net for this pass: 34 × 15 = 510 became 33 × 16 = **528**.

**The implementation estimate did not move.** 33 + 16 = 49 track rules, the same
as 34 + 15, plus 7 cross-track rules — still **56**. The corrections redistributed
states between tracks without changing how much code there is to write. (The
second pass, below, did move this estimate — deliberately.)

Two results that only exhaustive generation could establish, at the time:

- **All 528 verdict pairs were reachable.** The tracks are independent at the
  verdict level; cross-track rules annotate, they never prohibit.
- **Coherence was a clean partition.** 476 pairs were wholly coherent, 52 wholly
  anomalous, none mixed — so a compliance flag was decidable from the verdict
  pair alone, without inspecting the underlying configuration.

### Second pass — SLA thresholds removed by design decision (this document)

This pass is not a correction. Nothing above was wrong. It is a deliberate
architectural choice: aging stopped being a **verdict** dimension and became a
**read-time sort key** instead. Section 0a covers the reasoning in full; in
short —

- A claim with no new inbound record cannot change disposition. That is what
  makes incremental processing **provably complete** rather than an
  optimisation you hope is safe — the single biggest architectural payoff of
  this change.
- It removes the need to defend arbitrary threshold numbers (why 30 days and
  not 45?) inside the reconciliation engine itself.
- Real timing rules genuinely exist in the industry — Medicare's 30-day payment
  ceiling, state prompt-pay statutes (commonly 30 or 45 days), Iowa's 20-day
  PBM-specific rule, CAQH CORE Operating Rule 370's ±3-business-day remittance
  window, and the 340B pilot program's 45-day submission window with a 10-day
  manufacturer-payment window. They are real, but they are **policy** — never a
  field on a record. This is a deliberate, informed exclusion, not an
  oversight, and if any of them is reintroduced it belongs in engine
  configuration, never as a literal (Section 7).

**States retired.** The past-SLA half of each within-SLA/past-SLA pair is gone,
because its within-SLA twin now covers both ages — a claim ages in place, it
does not jump to a new verdict:

| State | Was | Now |
|---|---|---|
| A-03 | Awaiting payment, past SLA | Retired — covered by A-02, "Awaiting payment" |
| B-03 | Submitted, 835 missing, past SLA | Retired — covered by B-02, "Submitted, awaiting 835" |
| C-04 | Qualified, request stuck, past SLA | Retired — covered by C-03, "Qualified, request not yet submitted" |
| C-06 | Request submitted, manufacturer silent, past SLA | Retired — covered by C-05, "Request submitted, manufacturer pending" |
| C-01a | Qualification pending, within SLA | Retired — merged back into C-01, "Qualification pending" |
| C-11a | Approved, awaiting payment, within SLA | Retired — merged back into C-11, "Approved, awaiting payment" |

C-01a and C-11a existed only because the first pass added them as the
within-SLA twins of C-01 and C-11 (see row 3 above). Removing the SLA axis
removes the reason they were split out in the first place, so they fold back
into the states they were split from.

**Renumbered subtotals:** pharmacy 17 → 16, medical 16 → 15, 340B 16 → 12
(including C-00 absent). Reachable reimbursement verdicts 33 → 31, reachable
rebate verdicts 16 → 12.

**Exact figures from this rerun** — same generator and methodology as the
first pass, applied to the reduced state space:

```
unconstrained cross-product : 12,093,235,200
valid configurations        : 4,224          (was 7,046)
eliminated as impossible    : 12,093,230,976
survived                    : 0.00003%

reachable reimbursement verdicts : 31        (was 33)
reachable rebate verdicts        : 12        (was 16)
verdict pairs                    : 31 x 12 = 372, ALL 372 reachable
                                    (was 528)

coherent configurations   : 3,860
anomalous configurations  : 364

deterministic rules       : 31 + 12 = 43 track rules + 7 cross-track = 50   (was 56)

if-checks evaluated per case: min 10, max 32, average 21.0
  stage 0 route pharmacy vs medical : 1 check always
  stage 1 reimbursement verdict     : 1-12 checks, avg 5.3
  stage 2 rebate verdict            : 1-12 checks, avg 7.6
  stage 3 cross-track flags         : 7 checks always
```

**Running total across both passes:** 510 (hand-count) → 528 (exhaustive
generation) → 372 (SLA removed by design decision). The first arrow is a
correction; the second is a decision. Conflating them would misrepresent this
pass as fixing an error when nothing before it was wrong.
