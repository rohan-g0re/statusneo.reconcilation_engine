# Start Here

Everything a fresh session needs to pick this project up and build it. Written because the design was settled across a long conversation, and a new session can only see files.

**Read this file first. Then follow the reading order in §4.**

---

## 1. What this is

A take-home assignment: **Post-Claim Pharmacy Financial Reconciliation**, for an AI Solution Engineer role. The brief is `docs/Assignment_doc.pdf` — read it, it is short.

The shape of it: four independent synthetic data feeds get ingested, crosswalked into claim-level episodes, reconciled by deterministic code, and then surfaced to an LLM agent layer **that never computes a number**.

That last constraint is the whole test. The assignment says it three times. Every expected amount, every payment allocation, every status comes from Python. The agent explains what the code decided and recommends what a human should do. If an agent ever produces a dollar figure, the submission has failed its main point.

**Rubric:** architecture 20%, reconciliation correctness 20%, agent design 20%, domain understanding 15%, engineering quality 15%, communication 10%. Timebox 3–4 days, 12–16 hours. Scope discipline is explicitly graded — *"a smaller, correct solution is preferred over a broad but incomplete implementation."*

---

## 2. Domain primer

This took several hours of conversation to establish. Condensed.

### The operator and the money

The **operator** is a health-system specialty pharmacy — the business that dispensed the drug and is owed money. The user of this system is their back-office finance team.

Money arrives from up to three places, and a fourth feed proves it landed:

| Source | What it pays for |
|---|---|
| **PBM** | Pills and self-administered drugs picked up at a counter |
| **Medical payer** | Drugs administered by a clinician — infusions, injections |
| **Manufacturer, via a 340B TPA** | A rebate on the drug's cost, when the dispense qualifies |
| **Bank** | Not a source. Proof that any of the above actually arrived |

### The two-track model

A drug travels **exactly one** billing road. Pills go to the PBM; infusions go to the medical payer. Billing both is duplicate billing, i.e. fraud — so the model makes it unrepresentable rather than detecting it afterwards.

340B sits **on top of** either road, optionally.

```
ONE DISPENSE
  └── ONE EPISODE                       the thing we reconcile
        ├── reimbursement track          pharmacy XOR medical, always exactly one
        └── 340B rebate track            optional
```

### How 340B actually works

The pharmacy buys a drug at, say, $70 and gets reimbursed $100 regardless. If the dispense qualifies for 340B, the 340B price for that drug might be $25, so the pharmacy is owed $45 back. **340B changes only what the drug cost, retroactively.** The patient pays the same, the payer pays the same, nobody on the revenue side knows anything happened.

Two independent qualification layers: the hospital must be an enrolled covered entity (standing status), and each individual dispense must pass a patient-definition test (per-claim, decided by the TPA).

Two independent decision points on the money: the **TPA** decides qualification, the **manufacturer** decides approval and pays. "Qualified but never paid" is a real and valuable exception.

**We model the rebate model** (cash comes back), not replenishment (discounted restock, no cash). Replenishment is what actually dominates in the real world, and it is carried as a documented simplification — the assignment's own wording ("manufacturer payment", "unmatched rebate") describes the rebate model, and it is the only version where 340B has a bank leg to reconcile.

### Why the crosswalk is the hard part

**There is no universal claim identifier.** There are three separate identifier universes, and the pharmacy and medical feeds share literally nothing — different standards bodies, different rails, different vocabularies.

| Feed | Its identifiers |
|---|---|
| PBM / pharmacy | `{pharmacy NPI, Rx number, fill number, date of service}` + an NDC. Separately, a PBM-assigned authorization number |
| Medical | `CLP01` (provider-assigned, echoed from the 837) and `CLP07` (the payer's own ICN, which changes on reprocessing) |
| 340B TPA | The pharmacy natural key, or `{provider_npi, ndc11, service_date}` for medical episodes. Never the PBM's authorization number |
| Bank | A 15-digit ACH trace (pure plumbing, no business content) and `TRN02` (the payer's reassociation reference, present only if the addenda survived) |

Three bridges, each with a documented failure mode:

- **PBM ↔ 340B** — natural key only. Breaks on TPA lag and retroactive eligibility flips.
- **837 ↔ 835** — `CLP01` round-trip. Breaks when the payer reassigns an ICN on reprocessing.
- **835 ↔ bank** — `TRN02`. Breaks constantly, because most bank exports drop the addenda entirely. An industry operating rule (CAQH CORE 370) exists purely to make this work, and it still fails in practice.

**The bank line resolves in two hops.** It carries no claim identifier at all: trace number → remittance → the remittance's claim list. One bank line can touch 47 claims.

### The traps worth knowing

**A netted recoupment is invisible to the bank.** The payer pays less in a later batch and the explanation lives only in the 835's `PLB` segment. So `sum(claim payments) − PLB = what hits the bank`, and the bank shows only the net. This is the best reconciliation trap in the dataset.

**One deposit covers hundreds of claims.** Allocation is real work, and the same splitter serves the 340B rebate batches.

**`CLP01` on a pharmacy 835 is `"7845102FILL00"`** — the Rx number with the fill number glued on after the literal word `FILL`. That is NCPDP's documented convention. Parsing it apart is a real crosswalk step.

---

## 3. Current state

**Design: complete. Code: none committed.**

| | |
|---|---|
| Decisions | All closed. Sections A, B and C of `docs/decision_ledger.md` |
| Design documents | 6, committed |
| Implementation plans | 4 group plans + a reconciliation, committed |
| Decision tree | Built, runs, self-validates |
| **Application code** | **Zero committed.** ~5,300 untracked, stale lines under `src/` |

### The untracked code

A planning agent wrote it despite being told plan-only. It was deliberately kept out of the commits because it implements decisions that had not been ratified at the time, and it **predates decisions A23, A24 and A26** — so it has no eight-key-type crosswalk, no partial indexes, and no read/write split. Treat it as a reference, not a baseline. Rewriting is likely cheaper than repairing.

### The state space, measured not asserted

`decision_tree/` exhaustively generates every valid path and validates each choice against the choices above it.

```
unconstrained cross-product   12,093,235,200
valid configurations                   4,224
verdict pairs                 31 × 12 =  372     all 372 reachable
coherent / anomalous             3,860 / 364
deterministic rules            43 track + 7 cross-track = 50
if-checks per episode          min 10, avg 21, max 32
```

**All 372 pairs are reachable**, which proves the two tracks are independent at the verdict level — so every cross-track rule is an annotation, never a prohibition, and the engine needs no cross-track validity table.

`decision_tree/pairs.json` is loaded as a **live test oracle**: the coverage test fails if any pair is unproduced.

---

## 4. Reading order

1. **`docs/Assignment_doc.pdf`** — the brief. Short.
2. **`docs/glossary.md`** — vocabulary. *claim*, *episode*, *track*, *record*, *verdict*, *disposition*. These words are used precisely everywhere and the distinctions are load-bearing.
3. **`docs/decision_ledger.md`** — every decision, with provenance. Says which were approved by the user, which were delegated, and which are agent defaults.
4. **`docs/architecture_decisions.md`** — 48 numbered decisions with rationale and rejected alternatives.
5. **`docs/feed_formats.md`** — all four feeds, field by field, with worked examples. This is the generator specification.
6. **`docs/reconciliation_state_space.md`** — verdicts, dispositions, reason codes, the 372 pairs.
7. **`decision_tree/REPORT.md`** — how the state space was derived and verified.
8. **`plans/RECONCILIATION.md`** then the four `plans/G*.md` — implementation plans. **Caveat: written before A23, A24 and A26.** Read them for structure, not as gospel.
9. **`docs/solutions/best-practices/`** — four compounded learnings, generalisable beyond this project.

---

## 5. The decisions that shape everything

Full detail in the ledger. These are the ones that change the most downstream.

**Aging is never a verdict.** SLA thresholds were dropped deliberately. Aging is a read-time sort key. The payoff: an episode with no new inbound record **cannot** change disposition, which makes event-driven incremental processing provably complete rather than an optimisation you hope is safe. Real industry deadlines exist (Medicare's 30-day ceiling, state prompt-pay laws, CAQH CORE 370's ±3 business days) but they are policy, never fields on a record.

**`received_at` is the only system-added field.** Every native date on every record points backward — no record promises anything about the future. `received_at` is when a record entered the pipeline, and it is what makes replay possible. One documented exception: the NACHA CCD+ Effective Entry Date, a near-term settlement instruction.

**The cursor lives at read time.** The generator writes the entire timeline and has no concept of "now". The engine processes `records where received_at <= cursor`. Forward and backward use the same code path, which makes the audit answer free — *"what did we believe on 31 March?"* is just a cursor value.

**Event-driven, never scan-driven.** The inbound document is the trigger and carries its own lookup keys. Parse, extract keys, fetch only the episodes they resolve to, recompute those, append a status row. A 200-day-old episode is touched the instant a document referencing it arrives — no window to tune.

**Recompute, never mutate.** Verdicts are derived per `(episode, cursor)` and appended. The exception queue is not a place things move into — it is a query. Delete the whole verdict log and it rebuilds by replay.

**No identifier normalisation on the primary match path.** Injected drift is *meant* to miss, because the miss is the D-6 crosswalk-failure exception. Normalising it away would be the engine quietly covering up the thing it exists to surface.

**Generators are independent of the connector and of each other.** Each emits what its real source system would know — its own identifiers, format and timing. The orchestrator holds the only complete picture and writes `truth/ground_truth.json`, **which the ingestion layer is forbidden to read.** That is what makes the crosswalk scoreable rather than assumed.

**Three dispositions only** — `CLOSED`, `PENDING`, `EXCEPTION` — because "what do I do with this?" has three answers. Reason codes are a separate list-valued field. Reopened is a flag, never a fourth disposition.

---

## 6. Build order

```
Wave 1   Foundation + schema      config, seeded RNG, reference data,
                                  SQLite schema, DB layer
Wave 2   Four generators          real JSON files you can open
Wave 3   Ingestion                cursor, crosswalk, parked pool
Wave 4   Reconciliation engine    50 rules, 372 pairs covered
Wave 5   FastAPI + React          cursor scrubber, queues, regenerate
Wave 6   Agent layer              deferred by design — see §7
```

### Two passes owed before Wave 4

**Database design.** A26 made it a first-class task and A23/A24 postdate all four plans. Design from the query patterns backward: forward key resolution and the backward parked re-check both run on *every* inbound document, so both must be point seeks on `(key_type, key_value)` with a partial index on live rows. Eight key types: `NCPDP_CLAIM`, `MEDICAL_CLM01`, `PAYER_ICN`, `TRN02`, `ALLOCATION_CODE`, `NATURAL_340B_PHARMACY`, `NATURAL_340B_MEDICAL`, `PBM_AUTH`.

**Record → dimension mapping.** The missing bridge. `decision_tree/spec.py` defines verdicts in terms of abstract dimensions (`ph_settlement = MISSING`); `feed_formats.md` defines records. Nothing says how to derive one from the other. One instance is already decided — settlement reads from `CLP02`, where `"1"` means settled and `"19"`/`"25"` mean money moved but the receivable is not closed. Roughly twenty more such questions exist and each may need a ruling.

---

## 7. Deliberately deferred

**The agent layer.** The deterministic layer and workflow get built first, so the agent is designed against a working object rather than a guess — you will know exactly what data exists at each step. Leaning toward a plain tool-calling loop over LangGraph or CrewAI, because the logic is simple and a framework would obscure the tool boundary the assignment is grading.

The agent's write target — a work-item table, never a ledger entry — is parked with it.

**The evaluation set** depends on the agent layer. The assignment requires 10+ scenarios with an expected answer *or an expected tool path*. Tool-path assertions are the cheap win: deterministic to check, no LLM judging needed.

---

## 8. Traps

**The plans predate three decisions.** A23, A24 and A26 all landed after `plans/G*.md` were written. The schema in those plans is wrong.

**The 340B feed is deliberately the poorest.** No standard, no trace number, a vendor export. That weakness is the real state of the industry and it is why "unmatched rebate" is a named exception — do not improve it.

**All generated names must be fictional.** The assignment forbids real client names in generated data. Real names are fine in prose as domain context.

**Sampling must stratify over verdicts, not configurations.** Configuration frequency spans 252×, and 99 of the 372 pairs have exactly one configuration. Uniform sampling would miss the rarest and most valuable cases entirely.

**Seeding must use `blake2b`, never builtin `hash()`** — it is salted per process by `PYTHONHASHSEED` and would make output irreproducible. Seeds must be order-independent *and* insertion-independent.

**Money is integer cents end to end.** Float arithmetic gives every claim a phantom one-cent variance indistinguishable from real underpayment.

**Verdict A-06 depends on the `CLP02` read.** Get it wrong and it silently never fires.

---

## 9. What is still open

| | |
|---|---|
| Profile sizes | `demo` ~60, `full` ~1,500. Deferred as a parameter |
| Defect injection rates | Only the ~20% trace-number drop is sourced. Retune after the first full run |
| Agent layer, evals | Deferred by design |
| Test suite, README, design note | Work, not decisions |

Everything else is settled. **Nothing is waiting on a human.**
