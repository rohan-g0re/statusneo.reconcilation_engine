---
title: "RECONCILIATION — rulings across G1–G4"
type: decision-record
status: binding
date: 2026-09-12
authority: reconciliation pass over plans/G1..G4, docs/, decision_tree/
---

# Reconciliation of the four-group plans

Four planning groups worked independently; this document rules on every conflict, records what
was edited where, defines the one contract module every group builds against, and fixes the
implementation order. Where a ruling changed a binding document, the document was edited too
(`feed_formats.md`, `architecture_decisions.md` Decisions 37–48, `decision_tree/REPORT.md`).
Every numeric claim used in a ruling was re-verified against the `decision_tree/` artifacts
before being accepted.

---

## 1. Rulings, C1–C20

| # | Ruling | One-line rationale |
|---|---|---|
| **C1** | **The TPA feed carries medical-episode dispenses; the medical 340B join is `{provider_npi, ndc11, service_date}` with `rx_number: null`** (Decision 37; `feed_formats.md` §2 gains the record shape, a worked example, and the §5 resolution row; key type `NATURAL_340B_MEDICAL`). | A clinic-administered drug has no prescription and therefore no Rx number; without a medical join key, ~half of the 372 pairs are unreachable and G2's coverage assertion fails. The key is deliberately weaker (same-drug-same-site-same-day is ambiguous) — the connector parks ambiguity, and that hazard is realism, not a defect. |
| **C2** | **All generated payer/PBM/manufacturer names are fictional** (Decision 38): PBMs `MERIDIANRX`/`CASCADERX`, payers `BLUE HARBOR HEALTH`/`GRANITE PEAK HEALTH`, manufacturers `VERION`/`ALDEBARAN`/`CORVANE`/`TALVEX`/`SAGEPOINT`/`HALCYON`; `feed_formats.md` examples corrected (incl. BIN `610014`→`604211`, PCN `MEDDPRIME`→`SPECRX`); real names allowed in prose only. Generic drug names and public J-codes stay; all prices invented. | The assignment's confidentiality clause is unambiguous ("no client names"), and it binds the generated data; the *shape* (truncation, uppercase, formats) is what carries the realism, not the trademark. |
| **C3** | **G3 FLAG-1 accepted: 277CA acknowledgment records ride in `medical_837_submissions.jsonl`** (Decision 39; §3 now has two worked examples, codes `A1:19`, `A3:21/33/187`). | B-01 vs B-02 is otherwise wire-indistinguishable; the 277CA is the real standard transaction on the same rail, and the alternative (a non-native `clearinghouse_status` field) violates Decision 16. |
| **C4** | **340B feed is JSONL — `tpa_340b_events.jsonl`** (Decision 13 reaffirmed with an explicit note; G4-D1 upheld). | The rebate batch is irreducibly nested, the §2 worked examples are the connector's spec, and the feed's poverty is about identifiers, not envelope — the task brief's "vendor CSV" was descriptive prose. |
| **C5** | **One contract module: `src/recon/generators/contracts.py`, content owned by G2, place-in-tree owned by G1** (Decision 44). G2's two-level design (per-episode slices + cross-episode batch slices, seven entry points) is the skeleton; G4's `MoneyMovement`/`CashEvent` are adopted verbatim; G4's `Dispense340BSlice` merges into `TpaDispenseSlice`; G3's assumed `PharmacySlice`/`MedicalSlice` are superseded. G2's "no config field value" rule is restated as "no verdict codes, episode/case IDs, coherence tags or cross-track flags — payer-visible behavioural facts are fine" (G2's own slices carry `adjudication`). | Batching is irreducibly cross-episode, so the two-level shape is forced; three groups referencing one module beats three private copies drifting. |
| **C6** | **Declare → realize → format survives, refined**: the orchestrator composes batches and attaches PLB entries (netting ledger stays in G2); the generator computes each batch's net (`BPR = Σ(CLP04) − Σ(PLB, signed)`) — batch slices carry **no precomputed total** — and declares it as `MoneyMovement[]`; G2's realizer applies `MATCHED`/`PARTIAL`/`ABSENT`, injects orphans and reversal debits; `gen_bank` formats `CashEvent[]` only. The orchestrator *asserts* declared amounts against its own expectation (verification, not a second wire computation). | The isolation property holds — the bank never sees an expected figure — while the wire arithmetic exists exactly once, in the generator that formats it, and the orchestrator keeps enough knowledge to write accurate ground truth without back-channels. |
| **C7** | **The `_remittance_manifest.jsonl` back-channel is disallowed; `data/generated/_internal/` is dropped.** Generators return `(records, movements)`; the orchestrator takes lineage from returned `Record` payloads and cash facts from `MoneyMovement[]`. | The information was legitimately the orchestrator's, but a *file* between two generators erodes "no generator sees another's output" and creates an ordering dependency a return value doesn't; the orchestrator invoked the generators and already holds their returns. |
| **C8** | **G4-D2 accepted: the rebate deposit's `allocation_code` rides the bank `trn02` column** when addenda survive (Decision 42; §2 and §4 made self-consistent — §4's rebate example row is one of the ~20% drops). | `trn02` is semantically "the payer-assigned business reference from the CCD+ addenda"; this makes D-4 emerge from the same 80/20 mechanism as claim payments instead of being hand-injected, with zero schema change. |
| **C9** | **G4-D3 accepted: `REBATE_REQUEST` (with `submission_date`) and `MANUFACTURER_DECISION` added to §2** (Decision 43). | Without them C-03/C-05 are wire-identical, C-07 has no record (a rejection cannot ride a payment batch), and `NON_CONFORMING_45_DAY` is unrepresentable. |
| **C10** | **G2's measurements verified against the artifacts and adopted; `REPORT.md` corrected.** Verified: spread **252x** at pair level (min 1, max 252), **99/372** single-configuration pairs, X-1 = **36 configs / 16 pairs**, rarest flags **X-6 (8)** and **X-4 (9)**, coherence partition **36 wholly anomalous** ({A-01,A-10,A-11,A-16} × {C-01,C-03,C-05,C-07,C-08,C-09,C-10,C-11,C-14}) / **336 wholly coherent / 0 mixed**; also depth 3–14, cash arity 63/564/1,473/1,440/684, B-15 = 924. `REPORT.md` §5 and headline now carry the measured figures; `architecture_decisions.md` Decision 15's "~60x" corrected. (`NOTES.md` and the §3/§1 historical sections are labeled pre-SLA logs and stand as history.) | The stale "60x" and "X-1 = 1 config" were pre-SLA-removal numbers; a stratifier tuned to them would floor the wrong flags. |
| **C11** | **G3 FLAG-2 accepted: appeal-credit `RA` is negative** (Decision 41; §3 PLB list, sign-convention block, and defect table corrected). | The doc's own identity `BPR02 = Σ(CLP04) − Σ(PLB)` makes a positive PLB reduce payment, and its own `L6` note proves credits carry negative; the "positive" wording was the error. |
| **C12** | **Pharmacy 835 worked example 1 fixed: claim 1 gains `CO-45` 50.00** (2791.75 + 50 + 50 = 2891.75); balance identities documented as binding with the pharmacy-underpayment defect as the sole deliberate exception. | Adding the missing adjustment preserves every other number in the example (batch totals, clp05) — correcting `clp03` instead would have rippled. |
| **C13** | **G3 FLAG-3 accepted: `ph_settlement` encodes via CLP02** — `"1"` = CONFIRMED; `"19"`/`"25"` with no later `"1"` for the same CLP01 = MISSING (Decision 40; now binding in §1, with an explicit note that the engine's A-06 rule must mirror the read). | It is the only native, per-claim encoding available; every alternative is either file-level, collides with another defect, or invents a record. |
| **C14** | **Two covered entities** — own `DSH…` + decoy `PED…` (Decision 45), each with affiliated-prescriber and registered-pharmacy sets. | `feed_formats.md` already shows two IDs, and `PRESCRIBER_NOT_AFFILIATED` / `UNREGISTERED_LOCATION` need an "outside" to point at. |
| **C15** | **Entity universe fixed (Decision 45): 12 drugs (7/5), 2 pharmacies (main + unregistered satellite), 1 billing provider, 6 prescribers (≥1 unaffiliated), 2 PBMs, 2 medical payers, 2 covered entities, 6 manufacturers, 40 patients.** G1's proposal wins with two amendments (2nd pharmacy added for `UNREGISTERED_LOCATION`; manufacturer table confirmed); G2's 6/4/5/6/3 is superseded. | Smallest universe in which every defect and disqualification reason is generatable; scale-invariance is demonstrated by config rows (Decision 33), not by headcount, and batching realism comes from claim volume per (payer, cycle), not payer count. |
| **C16** | **Defect rates fixed (Decision 46), adopting G2's table**: TRN drop 20% (sourced, decided by the realizer); D-1 3% of records; D-2 8% of episodes; D-3 ~2% of bank rows; D-4 ~2% of rebate lines; D-5 0.5% `full`-only; D-6 emergent; D-7 = unreferenced `FB` on 5% of clean remittances + 100% of recoupments netted; Rx drift 6% (one feed per episode), truncation 2%; truncation/CLP07/unit-mismatch always-on. All in config; `demo` hand-places each D-code exactly once. | G2's was the only complete table and nothing in it contradicts a source; making it config keeps every number a review conversation. |
| **C17** | **`feed_formats.md` names win; the two unnamed files are fixed as `tpa_340b_events.jsonl` and `bank_transactions.csv`** (Decision 47), under `data/generated/<profile>/feeds/` with `truth/` beside it and `manifest.json` at the profile root. G1's tree corrected (`tpa_events.jsonl` was wrong). | The doc is the designated primary spec; directory separation is what makes "forbidden to read ground truth" mechanical. |
| **C18** | **Identifier drift is orchestrator-directed**: the defect planner assigns `rx_number_rendering` to exactly one feed per episode (pharmacy-835 zero-padding is the canonical case, per G3 FLAG-9); generators render as directed and never drift independently. | Two generators drifting the same key independently could cancel or double, silently changing link resolvability that ground truth must record. |
| **C19** | **D-5 malformed records are injected centrally, post-write, by the orchestrator** (G3 OQ-6 as recommended; `full` only, never `demo`). | A malformed record is a transport accident, not payer output; central corruption keeps every generator provably well-formed and the quarantine path tested against any feed. |
| **C20** | **`decision_tree/pairs.json` is a live test oracle, owned solely by G1's U2** (Decision 48): U2 asserts `pairs.json` ≡ `recon.domain.verdicts.REACHABLE_PAIRS` (372, exact product); G2's coverage test asserts against `REACHABLE_PAIRS`, not the file. `decision_tree/` stays frozen; the orchestrator consumes `leaves_classified.json` behind a digest check. | One ownership chain — artifact proves vocabulary, vocabulary proves dataset — and exactly one test breaks if the state space ever moves. |

**Also ruled while reconciling (G3's remaining flags):** FLAG-5 accepted (medical 835
`service_lines` is an array; doc updated) · FLAG-6 accepted (`MD_VOID` is an orchestrator overlay,
no state-space change) · FLAG-8 accepted (835 `originating_company_id` ≠ bank `company_id`; G1
carries both fields) · FLAG-10 accepted (D-1 duplicate = identical payload, same `record_id`,
later `received_at`) · FLAG-11 noted (payment=NONE + ADJUSTMENT has no wire encoding; classifies
to A-02 either way) · FLAG-12 accepted (A-12/A-13 decided by PLB traceability, no bank line;
A-10/A-11 decided by a realizer-produced bank debit).

---

## 2. The unified contract module

`src/recon/generators/contracts.py` — frozen dataclasses, stdlib only, no defaults on semantic
fields. **G2 owns the content; canonical field lists live in `plans/G2-orchestrator.md`
(as amended); this is the authoritative inventory.**

**Envelope (every generator returns):**

```
Record { record_id, feed, received_at, payload, slice_ref }
```

`slice_ref` is opaque; the orchestrator alone maps it to an episode. No slice anywhere carries
`episode_id`, a verdict code, a case ID, a coherence tag, or a cross-track flag.

**Slices (orchestrator → generators).** The orchestrator supplies all facts, all dates
(`received_at` included), all per-line amounts, and batch membership; batch slices carry **no
precomputed net totals**.

| Type | Level | Consumer | Notes |
|---|---|---|---|
| `PbmClaimSlice` | per-episode | gen_pbm | B1 accept/reject facts, amounts, optional `PbmReversal`, `is_340b_tagged`, `rx_number_rendering` |
| `PbmRemittanceSlice` | batch | gen_pbm | claim lines + `PlbLine[]` + orchestrator-minted `trn02`; generator computes BPR |
| `MedicalSubmissionSlice` | per-episode | gen_medical | 837 facts + `clearinghouse_status`/`clearinghouse_reject_code`/`ack_received_at` for the 277CA (Decision 39) |
| `MedicalRemittanceSlice` | batch | gen_medical | claim lines (each may `reuse_prior_icn` or force a fresh CLP07) + PLB + `trn02`; generator computes BPR |
| `TpaDispenseSlice` | per-episode | gen_340b | pharmacy XOR medical identity (Decision 37: nullable `rx_number`/`pharmacy_npi`, `provider_npi`); sub-objects `TpaQualification`, `TpaRebateRequest`, `TpaMfrDecision`, `TpaReversal`, each with its own `received_at` |
| `RebateBatchSlice` | batch | gen_340b | dispense lines + orchestrator-minted `allocation_code`; generator sums the total |

**Declarations (payer-side generators → orchestrator), return values, never files:**

```
MoneyMovement { movement_id, direction, amount_cents (NET, PLB applied), channel,
                originator_key, payer_reference (trn02 | allocation_code),
                file_creation_date, effective_entry_date, reverses_movement_id }
```

**Realized cash (orchestrator realizer → gen_bank):**

```
CashEvent { event_id, rng_seed, direction, amount_cents, channel, originator_key,
            payer_reference (None = the ~20% addenda drop, decided HERE),
            effective_entry_date, reverses_event_id,
            posting_lag_days, arrival_lag_hours, duplicate_row }
```

**Seven generator entry points**, all whole-dataset calls:
`generate_pbm_claims`, `generate_pbm_remittances`, `generate_medical_submissions`,
`generate_medical_remittances`, `generate_tpa_events`, `generate_rebate_batches` (batch
generators return `(Record[], MoneyMovement[])`; event generators return `Record[]`), and
`generate_bank(CashEvent[]) -> Record[]`. The settlement realizer is orchestrator-internal, not
a generator entry point. Movements are sorted `(effective_entry_date, channel, originator_key,
movement_id)` before realization.

**Who mints what (final):** orchestrator — natural keys (both kinds), `clm01`, `cardholder_id`,
`trn02`, `allocation_code`, all dates; generators — `record_id`, `authorization_number`,
`clp07`, `ach_trace_number`. A value crosses two generators only where it crosses those systems
in reality: the NCPDP natural key and the medical 340B natural key, `trn02`, `allocation_code`.

**Barrier tests (G2-owned, land before any generator):** no forbidden field names on slices; no
`episode_id`/verdict string in any emitted file; pairwise shared-value sets ⊆ the crossing
matrix; double-invocation purity; `CashEvent` claim-identifier denylist.

---

## 3. Implementation order and unblock points

```
G1 U1 (skeleton + layering guard)
  → G1 U2/U3 (domain vocabulary + oracle parity; money)     [parallel]
  → G1 U4 (config)  → G1 U5 (rng module)  → G1 U6 (reference + pricing + calendar)
        ●— UNBLOCKS G2 (orchestrator internals) ————————————→
  → G2 publishes contracts.py stubs + barrier tests  (needs only U1–U4)
        ●— UNBLOCKS G3 and G4 generator work in parallel ———→
  → G1 U7–U9 (schema → connection → repository)   [parallel with generator work;
        ●— UNBLOCKS future ingest/engine work and G1 U11 fixtures]
  → G3 emitters/batch formatters + G4 gen_340b     [parallel, against contract stubs]
  → G2 stratifier → timelines → money → batch composition → defect planner
  → G2 realizer + G4 gen_bank                      [bank runs last by design]
  → G2 arrival sequencer + writers → ground truth writer
  → joint acceptance: G2 coverage/determinism suite, G3 validate.py sweeps,
    G4 cash-consistency bijection, G1 U5's slow reproducibility matrix
```

Explicit unblock points: **(1)** G1 U6 unblocks all orchestrator money work; **(2)** the
contracts.py stub commit (G2, day one of its work) unblocks G3/G4 entirely — it needs no
orchestrator internals; **(3)** the realizer is the only thing gen_bank waits on; **(4)** nothing
in Set A waits on G1 U7–U9 except the fixtures — the DB layer serves the *next* phase, so it can
trail the generator track without blocking it.

Cross-group acceptance gates: PLB identity holds on every emitted 835 (G3 sweep); declared-
movement ↔ bank-row bijection modulo orphan/suppressed sets (G4 Unit 8); all 372 pairs among
non-truncated `full` episodes (G2, asserting against `REACHABLE_PAIRS`); byte-identical
regeneration across processes, `PYTHONHASHSEED` values, clock changes, and input-order shuffles
(all groups, shared helper from G1 U5).

---

## 4. Still genuinely open

- **Mock workflow / Epic feed** — still leaning yes; if it lands it is an additive slice type
  under Decision 44 and an additive feed file under Decision 47. Decide before the agent-layer
  phase, not before Set A.
- **Agent framework choice** — plain tool-calling loop still favoured; out of Set A scope.
- **α for the S6 tempered fill (0.4), exact template day-offset ranges, `demo` filler
  composition, `ground_truth.json` splitting** — G2 implementation-time calibration knobs,
  deliberately not ruled here.
- **Per-vendor TPA field-name variation** (G4 deferred item) — attractive realism, not required;
  revisit only after the core is green.
