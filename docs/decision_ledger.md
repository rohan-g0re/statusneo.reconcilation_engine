# Decision Ledger

> **Vocabulary:** *claim*, *episode*, *track*, *record*, *verdict*, *disposition* and *reason code* are defined once in [`glossary.md`](glossary.md). That file wins wherever this one is loose.

A running record of what is actually settled, what is proposed but unconfirmed, and what has never been discussed. Maintained so that scope cannot drift by accident and so that nobody has to re-derive a decision from the conversation.

**Three sections:**

- **A — Settled.** Decided explicitly. Safe to build against.
- **B — Proposed, unconfirmed.** Written into the design documents and assumed by the implementation plans, but never actually agreed. Each needs a yes or no.
- **C — Not discussed.** Some carry a default chosen by an agent rather than by a person. Those are marked and need ratifying or overruling.

Status legend: **✅ settled** · **🟡 proposed, awaiting confirmation** · **🔴 open** · **⚙️ agent default, needs ratifying**

**Nothing is ever deleted from this file.** Items that move between sections keep a row showing where they went, so the trail stays readable.

---

## Next actions

Shortest path back into the work.

| Order | Action | Why now |
|---|---|---|
| 1 | **Ratify or overrule C.1 and C.2** | Seventeen agent-made decisions currently baked into the plans. C10 (fictional names) is not optional — the assignment forbids real client names in generated data |
| 2 | **Redo the database design pass** | A26 made this a first-class task, and A23/A24 landed after the plans were written. The schema needs the eight key types, the partial indexes and the read/write split |
| 3 | **Then implement Set A** | Foundation, orchestrator, four generators. The untracked code on disk is a starting point, not a finished one |

**C.3 is deliberately deferred.** The deterministic layer and the workflow get built first, so that a working object exists and it is known exactly what data is available at each step. The agent layer is designed against that reality rather than against a guess — which also means C19–C22 cannot sensibly be answered yet.

The exception is **C23 (the design note)**, which carries roughly 40% of the grade and has no dependency on any code. It can be drafted in parallel at any time.

---

## Section A — Settled

Decided explicitly. Committed into `docs/architecture_decisions.md`, `docs/feed_formats.md` and `docs/reconciliation_state_space.md`. The implementation plans may rely on these.

### A.1 — Domain and scope

| # | Decision | Notes |
|---|---|---|
| A1 | ✅ **340B uses the rebate model** — manufacturer pays cash, which lands in the bank | Replenishment (discounted restock, no cash) is the dominant real-world mechanism and is carried as a documented simplification |
| A17 | ✅ **Rebates arrive batched** — one rebate payment covers many dispenses; the connector splits it back across them | *Was B1.* Payment-level batching is certain — a manufacturer settles a batch rather than wiring each dispense. Claim-level accumulation is defensible rather than forced. The real win is that the allocator gets a second consumer, since the bank feed forces one anyway. **Cost:** verdicts C-08/09/10 become downstream of the splitter, so ground truth must record the intended split for independent assertion |
| A13 | ✅ **Pharmacy and medical are separate generators**, not one reimbursement track | |
| A14 | ✅ **Date window: 2025-07-01 to 2026-07-01** | |

### A.2 — Time, replay and processing

| # | Decision | Notes |
|---|---|---|
| A4 | ✅ **No SLA thresholds.** Aging is never a verdict dimension | It is a read-time sort key. The payoff: an episode with no new inbound record cannot change disposition, which makes event-driven incremental processing provably complete |
| A5 | ✅ **Days-open is never stored.** Derived at read time only | |
| A6 | ✅ **Event-driven ingestion.** The inbound document is the trigger and carries its own lookup keys | No scanning, no time-window sweep |
| A7 | ✅ **Only affected records are pulled**, never the whole pending/closed/exception pile | |
| A8 | ✅ **Append status, never overwrite.** The latest appended row is the current status | |
| A18 | ✅ **Claim and payment are separate records arriving weeks apart** | *Was B2, withdrawn as mis-framed.* Not a standalone decision — a consequence of A6, A8 and A14. File count follows from it and is an implementation detail |
| A19 | ✅ **`received_at` is the single system-added field** on every record | *Was B3.* Every other date on a record is native to its format and points backward. Without this field there is no cursor, and two of the assignment's named edge cases — late-arriving status and duplicate delivery — cannot be represented |

### A.3 — Crosswalk and matching

| # | Decision | Notes |
|---|---|---|
| A20 | ✅ **Parked records plus a backward re-check** on every inbound document | *Was B4.* A document whose keys resolve to nothing is held, not discarded. Every inbound document therefore does two things: resolve its own keys forward, and check the parked pool backward |
| A21 | ✅ **The bank line resolves in two hops** — trace number to remittance, remittance to claims | *Was B5.* Not really a choice: a bank record carries no claim identifier at all, so there is no one-hop route available |
| A23 | ✅ **No identifier normalisation on the primary match path.** Injected drift is meant to cause a miss | Normalising drift away would silently resolve the record and **delete the D-6 crosswalk-failure exception we deliberately inject**. The claim is not missing — the mapping failed, which is a different fix with a different owner. A tiered fuzzy fallback flagged as low-confidence is how real cash-posting waterfalls work, but that is an engine decision for later, not Set A |
| A24 | ✅ **Crosswalk and parked pools are both keyed on `(key_type, key_value)`** with a partial index on live rows | Both lookups are point seeks, never scans. A document publishing 48 keys does 48 index seeks. **Eight key types:** `NCPDP_CLAIM`, `MEDICAL_CLM01`, `PAYER_ICN`, `TRN02`, `ALLOCATION_CODE`, `NATURAL_340B_PHARMACY`, `NATURAL_340B_MEDICAL`, `PBM_AUTH` |

### A.4 — Status model

| # | Decision | Notes |
|---|---|---|
| A9 | ✅ **Three dispositions** — `CLOSED`, `PENDING`, `EXCEPTION` — driving the queue | |
| A10 | ✅ **Reason codes as separate metadata**, assigned by the deterministic pass | |
| A22 | ✅ **Reopened is a flag, not a fourth disposition** — same queue, flagged, driving priority | *Was B6.* A reopened episode lands in whichever of the **three** dispositions the recomputation produces. Usually EXCEPTION, sometimes PENDING, occasionally back to CLOSED when a clawback and a compensating payment net out in the same batch. Nothing special-cases it, because the disposition is recomputed rather than assigned |

### A.5 — Architecture and data model

| # | Decision | Notes |
|---|---|---|
| A11 | ✅ **Four separate generators**, orchestrated — pharmacy, medical, 340B, bank | |
| A12 | ✅ **Generators are independent of the connector and of each other.** Never reverse-engineered from the canonical model | The crosswalk must earn the join |
| A25 | ✅ **The object is the episode.** Tracks sit inside it; records sit outside it | One dispense makes one claim object. It holds two tracks; each track points at the records touching it and carries an appended verdict history. **Records live in their own table and are linked by pointer** — one 835 covers 47 claims and cannot sit inside any one of them, which is also why lineage is a link table rather than a field. **Verdicts are not records:** a record is a fact that arrived and is immutable; a verdict is our reading of the facts so far, recomputed at every cursor |
| A26 | ✅ **Database design is a first-class task**, not a by-product of the schema | Three commitments: store *everything* including incomplete, parked, quarantined and orphan rows; design from the query patterns backward rather than the entities forward; and treat read-heavy and write-heavy tables differently. Records are write-once with minimal indexing; crosswalk and parked are read on every inbound and heavily indexed; the verdict log is append-heavy with one hot read |

### A.6 — Stack and conventions

| # | Decision | Notes |
|---|---|---|
| A2 | ✅ **JSON output for the feeds** | |
| A3 | ✅ **High volume** — well past the assignment's minimum of 50 | Enough to exercise every rule and every branch |
| A15 | ✅ **SQLite for persistence** | |
| A16 | ✅ **Stack: Python backend, FastAPI API layer, React front end** | Recorded as Decisions 34–36 |
| A27 | ✅ **`docs/glossary.md` is the vocabulary authority** | Linked from the head of every design document. It wins wherever another document is loose |
| A28 | ✅ **Naming: `episode` in code, "claim financial episode" in prose, `claim` reserved for the payment request** | The object and the "claim object" are the same thing — this is purely naming. But *claim* already means something narrower and real: the 837 or B1 submission, which lives **inside** the episode. Using it for both gives you `claim.reimbursement_track.claim`. Prose keeps the assignment's own term so a reviewer sees familiar vocabulary |
| A29 | ✅ **Episode disposition is a worst-wins rollup** of the two track statuses — `EXCEPTION > PENDING > CLOSED` | *Was B7.* Without it you must nominate one track to represent the episode, and you are wrong whenever the tracks disagree — 340B clawed back while the reimbursement is still in flight being the obvious case |
| A30 | ✅ **`INSUFFICIENT_DATA` is a first-class reason code** | *Was B8.* Less optional than it looks: the assignment requires the agent to "say when the available data is insufficient." Without a deterministic code for it, that judgement moves to the LLM side of the boundary. Fires on unmatched recoupment, unattributable rebate cash, and correlated no-cash on both tracks |
| A31 | ✅ **Lineage and audit trail are distinct mechanisms, both retained** | *Was B10, closed as descriptive rather than decided.* The audit trail is appended to the object over time; lineage is reconstructed by joining across tables, hop by hop, back to the source row. Neither is a choice — A25 put records outside the object linked by pointer, which forces lineage to be a join, and A8 made status append-only, which makes the audit trail the append log itself |

---

## Section B — Proposed but never confirmed

I proposed each of these and you did not object, so they were written into the design documents and every implementation plan now assumes them. That is a real risk: the docs read as though these are settled when they are not.

### B.1 — Resolved, kept here for the trail

| # | Proposal | Outcome |
|---|---|---|
| B1 | Rebates arrive batched | ✅ **Accepted** → now **A17** |
| B2 | Two files per reimbursement channel | ↩️ **Withdrawn as mis-framed** → substance recorded as **A18**. It dressed an implementation detail (file count) as an architectural decision; the real content was already covered by A6, A8 and A14 |
| B3 | `received_at` as the single system-added field | ✅ **Accepted** → now **A19** |
| B4 | Parked records plus a backward re-check | ✅ **Accepted** → now **A20**, with the indexed design added as **A24** |
| B5 | Bank line resolves in two hops | ✅ **Accepted** → now **A21** |
| B6 | Reopened is a flag, not a fourth disposition | ✅ **Accepted** → now **A22** |
| B7 | Worst-wins rollup of track statuses | ✅ **Accepted** → now **A29** |
| B8 | `INSUFFICIENT_DATA` as a reason code | ✅ **Accepted** → now **A30** |
| B10 | Lineage and audit trail are distinct | ↩️ **Closed as descriptive** → recorded as **A31**. Not a decision; both follow from A25 and A8 |

### B.2 — Still outstanding

| # | Proposal | Status |
|---|---|---|
| B9 | ⏸️ **Two profiles from one seed** — `demo` ~60 hand-stratified episodes, `full` ~1,500 covering all 372 verdict pairs | **Deferred, not blocking.** A parameter (how many episodes per profile), not a design decision. Revisit when the generator exists and the real cost of each profile is known |

**Section B is otherwise clear.** Nine of ten resolved: six accepted into Section A, two closed as descriptions of consequences rather than decisions, one deferred.

---

## Section C — Never discussed

Nothing here was ever raised in conversation. Items marked ⚙️ were decided by a planning or reconciliation agent on its own authority — they are defaults, not agreements, and they are currently baked into the committed plans.

### C.1 — Parameters (agent defaults, easily changed)

| # | Item | Current default | Set by |
|---|---|---|---|
| C1 | ⚙️ **Entity universe** | 12 drugs, 2 pharmacies (second unregistered), 2 PBMs, 2 payers, 2 covered entities, 6 manufacturers, 6 prescribers, 40 patients | Decision 45 |
| C2 | ⚙️ **Drug price table** | Per-NDC acquisition cost, contracted rate and 340B ceiling, with contract terms in a separate `(payer, ndc)` table | G1 plan |
| C3 | ⚙️ **Defect injection rates** | ~20% of bank rows drop the trace number (the only sourced figure); all others proposed | Decision 46 |
| C4 | ⚙️ **Seeding scheme** | `blake2b(master, *path)` derived per record; builtin `hash()` banned as it is salted per process | G1 plan, all groups |
| C5 | ⚙️ **Repo layout** | `src/recon/...` with generators as a separate entry point that never imports the DB layer | Decision 36, G1 plan |
| C6 | ⚙️ **Money representation** | Integer cents end to end; floats only at format time; one shared `round_half_up` | G1 plan |
| C7 | ⚙️ **Allocation logic** | Many-to-one splitting shared between the bank feed and the 340B rebate batches | G4 plan |
| C8 | ⚙️ **Canonical filenames** | Six files under `feeds/`, ground truth under `truth/` | Decision 47 |

### C.2 — Design decisions taken during reconciliation

Forced by conflicts between the four plans. Each is defensible; none was agreed with a person.

| # | Item | Ruling | Why it was forced |
|---|---|---|---|
| C9 | ⚙️ **Medical 340B join key** | `{provider_npi, ndc11, service_date}` — a clinic-infused drug has no prescription, so no Rx number exists | Without it roughly half the 372 verdict pairs are unreachable |
| C10 | ⚙️ **All generated names are fictional** | Real payer, PBM and manufacturer names removed from the documents | The assignment forbids real client names in generated data. **Not optional** |
| C11 | ⚙️ **A `277CA` acknowledgment record** is added to the medical submissions file | Accepted | Verdict B-01 is otherwise indistinguishable on the wire from B-02 |
| C12 | ⚙️ **The 340B feed stays JSONL**, not CSV | Accepted | Its rebate batch record is irreducibly nested. The feed's poverty is about identifiers and linkage, not the envelope |
| C13 | ⚙️ **Settlement is encoded via CLP02** (`"1"` confirmed, `"19"`/`"25"` missing) | Accepted | Neither NCPDP nor X12 has a settlement transaction, and verdict A-06 needs one |
| C14 | ⚙️ **The `allocation_code` rides in the bank `trn02` column** | Accepted | Two sections of `feed_formats.md` were in tension |
| C15 | ⚙️ **Two new 340B event types** — `REBATE_REQUEST` and `MANUFACTURER_DECISION` | Accepted | Without them C-03 and C-05 are identical on the wire, and a rejection cannot ride inside a payment batch |
| C16 | ⚙️ **Malformed records (D-5) are injected centrally after write**, not by the feed generators | Accepted | A malformed record is a transport accident, not something a payer emits |
| C17 | ⚙️ **`decision_tree/pairs.json` loads as a live test oracle** asserting the 372 pairs | Accepted | Turns the state-space document into a check rather than a reference |

### C.3 — Genuinely open, no default exists

| # | Item | Note |
|---|---|---|
| C18 | 🔴 **The front end** | You described a date cursor advancing day by day. Never designed or scoped. Recorded as Decision 35 in principle only |
| C19 | 🔴 **Mock workflow / Epic feed** | The fifth box in the assignment's diagram and the agent's only write target. Leaning yes, never decided |
| C20 | 🔴 **Agent layer** | Framework choice, tool signatures, evidence format. Leaning a plain tool-calling loop over LangGraph or CrewAI, because the logic is simple and a framework would obscure the tool boundary the assignment is grading |
| C21 | 🔴 **Agent evaluation set** | The assignment requires 10+ scenarios with an expected answer or an expected tool path. Depends on C20 |
| C22 | 🔴 **Tests for the reconciliation core** | What it asserts, and against what oracle |
| C23 | 🔴 **The design note** | 4–5 pages. Carries architecture (20%), most of domain understanding (15%) and communication (10%) — call it 40% of the grade, and it is prose |
| C24 | 🔴 **README** | Architecture diagram, prerequisites, how to run the demo, assumptions, known trade-offs |

---

## Build status

| Phase | State |
|---|---|
| Design documents | Committed — `cfeb73b` |
| Four implementation plans + reconciliation | Committed — `833d19a` |
| Decision ledger | Committed — `18428fd`, updated `be221a4` |
| Glossary | Written, linked from five documents |
| Section B walkthrough | ✅ **Complete.** 9 of 10 resolved, B9 deferred as a parameter |
| Database design pass | **Owed.** A26 made it a first-class task; A23 and A24 postdate the plans |
| Section C walkthrough | **In progress** — C.1 and C.2 next; C.3 deferred until the deterministic layer exists |
| Implementation | **Not started.** Held pending B and C |

**One thing sitting untracked on disk.** A planning agent wrote roughly 5,300 lines of foundation code — config, money, seeded RNG, SQLite schema, DB layer, reference data, pricing — despite being instructed to plan only. It was removed from the commit because it implements C1, C2, C4, C5 and C6, which have not been ratified. It remains on disk under `src/` and `pyproject.toml`, untracked. **It also predates A23, A24 and A26**, so its schema does not carry the eight-key-type crosswalk design, the partial indexes, or the read/write split — it will need revision regardless of how Section C lands.
