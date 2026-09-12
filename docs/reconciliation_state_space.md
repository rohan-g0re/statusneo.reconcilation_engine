# Reconciliation State Space — Brute-Force Enumeration

**Purpose.** Enumerate every reconciliation outcome the deterministic engine can encounter, so we know the real scale of the problem before writing code. Every possibility listed here must also be narratable by the agent layer.

**Scope.** Happy path + all failure modes within the assignment's four feeds. Multi-tenancy and multi-source scale are deliberately excluded and parked in Section 7.

**Headline numbers.**

> **Verified programmatically.** Every figure below was confirmed by exhaustive
> decision-tree generation in [`../decision_tree/`](../decision_tree/REPORT.md),
> which builds all 7,046 valid configuration paths and validates each choice
> against the choices above it. That pass corrected three errors in an earlier
> hand-count of this document; see Section 9.

| Measure                                                    | Count         |
| ---------------------------------------------------------- | ------------- |
| Total representable episode combinations                   | **528** |
| — logically coherent                                      | 476           |
| — compliance anomalies (data that should not exist)       | 52            |
| Underlying valid configurations                            | **7,046** |
| Per-track states the engine actually branches on           | **49**  |
| Cross-track compliance rules                               | **7**   |
| Feed-level data-integrity exceptions (episode-independent) | **7**   |
| **Total deterministic rules to implement**           | **56**  |
| Distinct status enum values                                | **9**   |

The 528 is a *composition*, not a switch statement. Read Section 5 before panicking.

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

Two free booleans would give 18 × 18 = 324 reimbursement configurations. The XOR gives 33. The difference is entirely states that cannot legally exist.

### Why the reimbursement track is mandatory, and 340B is not

The assignment settles this; it is not a modelling choice.

**Reimbursement is mandatory.** Section 1 opens with *"The prototype begins after a claim has been filed. Upstream activities such as patient intake, benefits investigation, prior authorization and claim creation are outside the assignment."* A filed claim is the precondition for an episode existing. Reinforced by "claim-centric object," by "at least 50 **claim** financial episodes," and by the framing "Once a claim has been filed, the operator needs to understand…". Every episode therefore carries exactly one reimbursement track.

**340B is optional.** Stated directly: *"A single claim **may** create pharmacy-benefit reimbursement, **a** 340B rebate, **or** a medical-benefit payment / denial pathway."* The rebate track is present or absent, which is what C-00 encodes.

| Reimbursement configuration  | States       |
| ---------------------------- | ------------ |
| Pharmacy benefit (Section 1) | 17           |
| Medical benefit (Section 2)  | 16           |
| **Total**              | **33** |

**Out of scope, deliberately: the no-claim dispense.** A drug dispensed with no claim ever filed — cash-pay patient, or a submission that failed silently — is real revenue leakage, and a 340B track can still be live off the dispense record. Detecting it requires anchoring episodes on dispenses rather than on claims, which contradicts the assignment's stated boundary and its claim-centric business object. Excluded here; carried as a one-line assumption in the design note instead.

### Why cash/bank is not a fourth dimension

The bank feed is always present as a *feed*. A bank record per episode is not — and its absence is the single most common exception in the system. The assignment names it twice in the pathways table: "remittance with no cash" and "unmatched deposit." A model in which cash is guaranteed present cannot represent A-05, B-05 or C-09, which are three of the most valuable exceptions available.

Structurally, cash is also not a peer of the other three. Pharmacy, medical and 340B are *claims on money*. Bank is *verification that a claim on money resolved*. That verification is already encoded inside every track state in Sections 1–3 — A-04 is "paid, cash matched," A-05 is "paid, no cash." Same payment event, different cash verdict. Multiplying cash in as a separate dimension would double-count what the track states already carry.

The one genuinely episode-external cash case is the orphan deposit — money with no attributable claim. That is a feed-level exception (D-3), not an episode state, because by definition it has no episode to attach to.

### The state space

```
(pharmacy 17 + medical 16) × (340B 16, including "absent")
= 33 × 16
= 528
```

Exhaustive generation reached **528 of a possible 528** verdict pairs — every
combination is achievable. The two tracks are therefore fully independent at the
verdict level, and all seven cross-track rules in Section 4 are *annotations*,
never *prohibitions*. The engine needs no cross-track validity table.

Three orthogonal overlays sit on top and are *not* multiplied in — they are feed-level, not episode-level: duplicate delivery, late arrival, orphan records. Section 6.

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
| **A-02** | Awaiting payment, within SLA                   | Adjudication accepted; expected = E; received = 0; age < payment SLA                     | Not an exception; no remittance yet; no cash                                                                      |
| **A-03** | Awaiting payment, past SLA                     | Adjudication accepted; expected = E; received = 0; age > SLA                             | No payment record, no denial, no reversal — the silence itself is the problem                                    |
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

**Subtotal: 17 states.**

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
| **B-02** | Submitted, awaiting 835, within SLA                   | Accepted by clearinghouse; no payer response; age < SLA                                       | Not an exception yet                                                                                                                               |
| **B-03** | Submitted, 835 missing, past SLA                      | Accepted; no 835; age > SLA                                                                   | No denial, no payment — payer silence                                                                                                             |
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

**Subtotal: 16 states.**

> **B-17 removed.** "Paid and matched, posting date far outside the window" was
> in an earlier draft as a 17th state. Exhaustive generation never produced it,
> because financially it is identical to B-04 — expected equals received, cash
> matched, nothing outstanding. It is an informational attribute on a settled
> claim, not a reconciliation verdict. Carry it as a `days_to_settle` field, not
> as a status.

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
| **C-01** | Qualification pending, past SLA                     | Dispense sent to TPA; no qualification decision; age > SLA                                                         | No request, no rebate — stuck at the first gate                         |
| **C-01a** | Qualification pending, within SLA | Dispense sent to TPA; no decision yet; age < SLA | Not an exception — normal in-flight at the first gate |
| **C-02** | Not qualified                                       | TPA evaluated and declined (patient definition, prescriber affiliation, or drug not eligible); expected rebate = 0 | No request, no manufacturer involvement; correct outcome, not a failure  |
| **C-03** | Qualified, request not yet submitted, within SLA    | Qualification confirmed; expected rebate = R; request queued                                                       | Not an exception yet                                                     |
| **C-04** | Qualified, request stuck, past SLA                  | Qualification confirmed; no request submitted; age > SLA                                                           | Money is being left on the table by inaction, not by rejection           |
| **C-05** | Request submitted, manufacturer pending, within SLA | Request filed; awaiting decision                                                                                   | Normal in-flight                                                         |
| **C-06** | Request submitted, manufacturer silent, past SLA    | Request filed; no decision; age > SLA                                                                              | No rejection, no approval — manufacturer non-response                   |
| **C-07** | Manufacturer rejected                               | Explicit rejection with a reason; expected rebate → 0 unless corrected and resubmitted                            | Not a TPA problem; the qualification stood, the manufacturer disputed it |
| **C-08** | Approved, paid, cash matched ✅                     | Approval + payment = R; deposit matched; outstanding = 0                                                           | Fully recovered on the rebate side                                       |
| **C-09** | Approved and paid per TPA, no cash                  | TPA reports paid; no matching deposit                                                                              | The TPA's ledger and the bank disagree                                   |
| **C-10** | Approved, partial rebate paid                       | Payment P < R; cash matched; variance = R − P                                                                     | Usually a unit/price dispute, not a qualification dispute                |
| **C-11** | Approved, never paid, past SLA                      | Approval on record; no payment; age > SLA                                                                          | Manufacturer agreed and did not pay — clean, chaseable                  |
| **C-11a** | Approved, awaiting payment, within SLA | Approval on record; no payment yet; age < SLA | Not an exception — the manufacturer still has time to pay |
| **C-13** | Rebate clawed back                                  | Rebate previously paid, then reversed by the manufacturer (duplicate-discount finding, audit, or dispute)          | Net rebate = 0; the original qualification is now contested              |
| **C-14** | Duplicate rebate payment                            | Two rebate payments for one dispense                                                                               | Refund liability; distinguish from a legitimate two-part payment         |

**Subtotal: 16 states (including C-00 "absent").**

> **C-01a and C-11a added; C-12 removed.** The original table gave both timing
> halves for C-03/C-04 and C-05/C-06 but only the past-SLA half for
> qualification-pending and approved-unpaid — an inconsistency the exhaustive
> generator caught, since both in-flight states are reachable and each accounts
> for 271 configurations. C-12 ("unmatched rebate") moved to Section 6 as D-4:
> cash with no attributable episode cannot be a state *of* an episode.

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

## 5. The combination count, and why you do not implement 528 branches

### The arithmetic

```
Reimbursement states:  17 (pharmacy) + 16 (medical)   =  33
340B states:           16 (including "absent")
Total representable:   33 × 16                        = 528
```

### Split into coherent vs anomalous

**Four** pharmacy verdicts mean the dispense did not happen: A-01 (rejected at POS), A-10 (reversed after payment, money returned), A-11 (reversal recorded, money not returned) and A-16 (reversed before payment). An earlier hand-count listed only three — it missed A-11, which is a no-dispense state just as much as A-10 is. For those four, the only coherent 340B verdicts are C-00 (absent), C-02 (not qualified) and C-13 (rebate correctly unwound).

Generation confirms the split is **clean**: of the 528 pairs, 476 contain only coherent configurations and 52 contain only anomalous ones. Zero pairs are mixed, which means coherence is decidable from the verdict pair alone — the engine never has to inspect the underlying configuration to know whether a compliance flag is warranted.

Medical is different — the drug was administered before billing, so **all 17 medical states are compatible with a live 340B track**, including B-01.

| Segment                                   | Calculation | Count                                |
| ----------------------------------------- | ----------- | ------------------------------------ |
| Pharmacy, dispense occurred (13 verdicts) | 13 × 16    | 208                                  |
| Pharmacy, no dispense, coherent 340B      | 4 × 3      | 12                                   |
| Medical (all verdicts)                    | 16 × 16    | 256                                  |
| **Logically coherent total**        |             | **476**                        |
| Pharmacy, no dispense, 340B active anyway | 4 × 13     | **52** ← compliance anomalies |
| **Total representable**             |             | **528**                        |

Those 52 are not noise to be filtered out. They are the highest-value exceptions in the system — data that should be impossible, which is exactly what an exception engine exists to surface.

### What actually gets built

The engine does **not** enumerate 528 cases. It resolves each track independently, then applies cross-track rules:

```
episode_status = compose(
    reimbursement_verdict,   # one of 34, from ~34 track rules
    rebate_verdict,          # one of 15, from ~15 track rules
    cross_track_flags        # subset of 7
)
```

| What                                | Count        |
| ----------------------------------- | ------------ |
| Pharmacy track rules                | 17           |
| Medical track rules                 | 17           |
| 340B track rules                    | 15           |
| Cross-track compliance rules        | 7            |
| **Total deterministic rules** | **56** |

56 rules generate 528 outcomes. That compositional property is worth stating out loud in the design note and in the walkthrough — it is the difference between an architecture and a switch statement.

### Status enum

The 528 combinations collapse to a small closed set of statuses. Detail lives in the reason codes, not the enum.

| Status               | Meaning                                                 |
| -------------------- | ------------------------------------------------------- |
| `RECONCILED`       | Every track settled, cash matched, outstanding = 0      |
| `IN_FLIGHT`        | Open, within SLA, no defect                             |
| `AWAITING_PAYMENT` | Past SLA, no payer response                             |
| `UNDERPAID`        | Money received, less than expected, variance quantified |
| `OVERPAID`         | Money received in excess; refund liability              |
| `DENIED`           | Explicit refusal on a track; action required            |
| `CASH_MISMATCH`    | Remittance and bank disagree in either direction        |
| `CLAWBACK`         | Money previously received has been taken back           |
| `COMPLIANCE_FLAG`  | A cross-track rule fired; requires human review         |

**9 values.** Each carries: `track`, `reason_code`, `variance_amount`, `age_days`, `source_record_ids[]`.

### What this means for the agent layer

The agent must be able to narrate any of the 528, but it never enumerates them either. It reads two track verdicts plus cross-track flags and composes an explanation the same way the engine composes a status.

Practical consequences:

- **Eval coverage.** 10 required scenarios cannot cover 528. Cover the shape instead: 2 happy paths (one per reimbursement type), the 6 required edge cases, and 2 cross-track compliance cases. X-1 and X-2 are the strongest demo material because they are invisible to any single-track system.
- **Insufficiency behavior.** A-13, D-4 and X-5 are states where the correct agent answer is "I cannot determine this from the available feeds, and here is precisely what is missing." Section 3 of the assignment requires that behavior — these states are where you demonstrate it.
- **Prioritization is deterministic.** Ranking exceptions by value/age/category is a sort over these verdicts. The agent explains the ranking; it does not produce it.

### What this means for the generator

50 episodes against 528 verdict pairs (7,046 underlying configurations) is under 10% coverage at best, and uniform sampling would be wrong anyway. Target roughly:

| Bucket                                                                                                                    | Episodes |
| ------------------------------------------------------------------------------------------------------------------------- | -------- |
| Happy path (A-04, B-04, C-08, and the four episode shapes)                                                                | ~20      |
| Required edge cases (partial, underpayment, reversal/recoupment, unmatched cash/rebate, denial, duplicate, late-arriving) | ~15      |
| Cross-track compliance (X-1, X-2, X-3, X-5, X-7)                                                                          | ~8       |
| Data-integrity overlays (Section 6)                                                                                       | ~7       |

Roughly 25–30 distinct combinations across 50 episodes. Enough variety that the agent has something worth investigating, without pretending to exhaust the space.

---

## 6. Feed-level data-integrity exceptions

Orthogonal to episode state. These are properties of *ingestion*, not of a claim, which is why they are not multiplied into the 528. They are handled in the source-adapter / normalization band and can co-occur with any episode state.

| #             | Exception                           | What is TRUE                                                                         | What is FALSE                                                                                                  |
| ------------- | ----------------------------------- | ------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------- |
| **D-1** | Duplicate source record delivered   | The same source event arrives twice (SFTP job rerun, retry after ambiguous timeout)  | Not two real events — idempotency key must collapse them, not sum them                                        |
| **D-2** | Late-arriving / out-of-order record | A bank deposit lands before the remittance that explains it                          | Not an unmatched deposit — it becomes matchable once the later record arrives; the engine must be re-runnable |
| **D-3** | Orphan bank deposit                 | Cash received with no linkable claim, rebate or remittance                           | Not revenue we can recognize; may be a crosswalk failure rather than genuinely unattributable                  |
| **D-4** | Orphan / unmatched rebate           | Rebate cash or a rebate event that cannot be tied to any qualified dispense in any other feed | Not a windfall — cash held without provenance is audit exposure. Absorbs what an earlier draft filed as episode state C-12; an unattributable rebate has no episode to be a state *of* |
| **D-5** | Malformed or unparseable record     | Schema version mismatch, missing required field, bad type                            | Must be quarantined with lineage intact, never silently dropped                                                |
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

**56 rules, at any number of tenants, payers, TPAs and banks.**

### Three limits on that claim, stated rather than hidden

**1. The rules must be parameterized or the claim is false in practice.**

The real scaling risk is code *shape*, not rule count. This forks on payer #2:

```python
if age_days > 30:                              # literal -> 56 becomes 56 x P
```

This does not:

```python
if age_days > sla_for(tenant, source, pathway):
```

Values that must be configuration and never literals: SLA windows per source and pathway, underpayment tolerance (what counts as a variance versus rounding), cash-match tolerance, and aging-bucket boundaries. Hardcode any of them and rule-count invariance is theoretically true and operationally worthless.

**2. Row growth is large and is the highest-maintenance surface.**

A real 835 carries hundreds of claim-adjustment and remittance-advice reason codes (CARC/RARC), per payer. The mapping tables are where the ongoing work actually lives. "Configuration rather than one-off code" is not a claim that the config is trivial — it is a claim that config rows are cheap to add and reviewable in bulk, whereas code branches are neither.

**3. A genuinely new event *semantics* does add states. Scale is not homogeneous.**

Adding a fifth PBM is rows. Adding a payer that does **capitation** or **bundled payments** is not — neither is fee-for-service, so there is no expected-per-claim amount to reconcile against and the entire expected-vs-actual frame does not apply. Interim payments and split-billing have the same problem.

The containment mechanism: adapters map into a **closed** canonical event vocabulary. An event that does not map is quarantined as D-5 with its lineage intact, never coerced into the nearest-looking slot. New *codes* are configuration; new *semantics* are a canonical-model change, and the system should refuse to guess which it is looking at.

That boundary is worth stating explicitly in the walkthrough. Claiming a design absorbs anything is weaker than showing where it stops and how it fails safe when it gets there.

### One tenancy case that would add a rule

If a shared lockbox could receive a single deposit covering claims from two tenants, cash allocation would need a cross-tenant splitting rule — a genuinely new rule, and one that also breaches isolation. The design forecloses it: bank accounts are tenant-scoped, so allocation never spans a tenant boundary. Worth naming because it is the one place where a scale dimension nearly does become a reconciliation input.

---

## 8. Summary

| Layer                                                   | Count                             |
| ------------------------------------------------------- | --------------------------------- |
| Pharmacy benefit states                                 | 17                                |
| Medical benefit states                                  | 16                                |
| 340B rebate states (incl. absent)                       | 16                                |
| Cross-track compliance rules                            | 7                                 |
| Feed-level data-integrity exceptions                    | 7                                 |
| **Deterministic rules to implement**              | **56**                      |
| Representable episode combinations                      | 528 (476 coherent + 52 anomalous) |
| Underlying valid configurations                         | 7,046 of 326,517,350,400 unconstrained |
| Status enum values                                      | 9                                 |
| Distinct combinations to cover in 50 synthetic episodes | ~25–30                           |

**Cash/bank is deliberately absent from this table.** It is not a track and not a dimension — it is a verification attribute already encoded inside every state in Sections 1–3, plus two feed-level exceptions (D-3, D-7). See Section 0.

---

## 9. Corrections log — what exhaustive generation changed

The figures in this document were originally hand-counted. The decision-tree
generator in [`../decision_tree/`](../decision_tree/REPORT.md) built all 7,046
valid configuration paths, validating each choice against the choices above it,
and disagreed with the hand-count in three places. All three were hand-count
errors.

| # | Error | Effect |
|---|---|---|
| 1 | **B-17 was not a distinct verdict.** "Paid and matched, posting date far outside the window" is financially identical to B-04. Never generated. | Medical states 17 → 16 |
| 2 | **C-12 was filed in the wrong table.** An unmatched rebate is cash with no attributable episode, so it cannot be an episode state. Merged into D-4. | 340B states 15 → 14 |
| 3 | **Two in-flight 340B states were missing.** The table gave both timing halves for C-03/C-04 and C-05/C-06 but only the past-SLA half for qualification-pending and approved-unpaid. Added as C-01a and C-11a, 271 configurations each. | 340B states 14 → 16 |
| 4 | **A-11 was miscounted as a dispense-occurred state.** "Reversal recorded, money not returned" is a no-dispense state exactly as A-10 is. | Anomalous pairs 36 → 52 |

Net: 34 × 15 = 510 became 33 × 16 = **528**.

**The implementation estimate did not move.** 33 + 16 = 49 track rules, the same
as 34 + 15, plus 7 cross-track rules — still **56**. The corrections redistributed
states between tracks without changing how much code there is to write.

Two results that only exhaustive generation could establish:

- **All 528 verdict pairs are reachable.** The tracks are independent at the
  verdict level; cross-track rules annotate, they never prohibit.
- **Coherence is a clean partition.** 476 pairs are wholly coherent, 52 wholly
  anomalous, none mixed — so a compliance flag is decidable from the verdict pair
  alone, without inspecting the underlying configuration.
