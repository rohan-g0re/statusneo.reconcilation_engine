# Decision Ledger

A running record of what is actually settled, what is proposed but unconfirmed, and what has never been discussed. Maintained so that scope cannot drift by accident and so that nobody has to re-derive a decision from the conversation.

**Three sections:**

- **A — Settled.** Decided explicitly. Safe to build against.
- **B — Proposed, unconfirmed.** Written into the design documents and assumed by the implementation plans, but never actually agreed. Each needs a yes or no.
- **C — Not discussed.** Some now carry a default chosen by an agent rather than by a person. Those are marked and need ratifying or overruling.

Status legend: **✅ settled** · **🟡 proposed, awaiting confirmation** · **🔴 open** · **⚙️ agent default, needs ratifying**

---

## Section A — Settled

Decided explicitly. These are committed into `docs/architecture_decisions.md`, `docs/feed_formats.md` and `docs/reconciliation_state_space.md`, and the implementation plans may rely on them.

| # | Decision | Notes |
|---|---|---|
| A1 | ✅ **340B uses the rebate model** — manufacturer pays cash, which lands in the bank | Replenishment (discounted restock, no cash) is the dominant real-world mechanism and is carried as a documented simplification |
| A2 | ✅ **JSON output for the feeds** | |
| A3 | ✅ **High volume** — well past the assignment's minimum of 50 | Enough to exercise every rule and every branch |
| A4 | ✅ **No SLA thresholds.** Aging is never a verdict dimension | It is a read-time sort key. The payoff: a claim with no new inbound record cannot change disposition, which makes event-driven incremental processing provably complete |
| A5 | ✅ **Days-open is never stored.** Derived at read time only | |
| A6 | ✅ **Event-driven ingestion.** The inbound document is the trigger and carries its own lookup keys | No scanning, no time-window sweep |
| A7 | ✅ **Only affected records are pulled**, never the whole pending/closed/exception pile | |
| A8 | ✅ **Append status, never overwrite.** The latest appended row is the current status | |
| A9 | ✅ **Three dispositions** — `CLOSED`, `PENDING`, `EXCEPTION` — driving the queue | |
| A10 | ✅ **Reason codes as separate metadata**, assigned by the deterministic pass | |
| A11 | ✅ **Four separate generators**, orchestrated — pharmacy, medical, 340B, bank | |
| A12 | ✅ **Generators are independent of the connector and of each other.** Never reverse-engineered from the canonical model | The crosswalk must earn the join |
| A13 | ✅ **Pharmacy and medical are separate generators**, not one reimbursement track | |
| A14 | ✅ **Date window: 2025-07-01 to 2026-07-01** | |
| A15 | ✅ **SQLite for persistence** | |
| A16 | ✅ **Stack: Python backend, FastAPI API layer, React front end** | Recorded as Decisions 34–36 |

---

## Section B — Proposed but never confirmed

I proposed each of these and you did not object, so they were written into the design documents and every implementation plan now assumes them. That is a real risk: the docs read as though these are settled when they are not.

**Each needs an explicit yes or no.** Where a "no" would be expensive, the cost is stated.

| # | Proposal | Where it already appears | Cost of reversing |
|---|---|---|---|
| B1 | 🟡 **Rebates arrive batched** — one rebate payment covers many dispenses, and the connector splits it back across them | `feed_formats.md` §2, Decision 5, G2 and G4 plans | Moderate. Reverting to 1:1 removes the allocation exercise from the 340B side but leaves it on the bank side |
| B2 | 🟡 **Two files per reimbursement channel** — PBM emits NCPDP claim events *and* an 835; medical emits an 837 *and* an 835 | `feed_formats.md` §1 and §3, all four plans | High. Merging them deletes the reassociation problem entirely |
| B3 | 🟡 **`received_at` is the single system-added field** on every record | Decision 16, every generator plan | High. Without it, out-of-order arrival and duplicate delivery — two of the assignment's named edge cases — cannot be represented |
| B4 | 🟡 **Parked records plus a backward re-check** on every inbound document | Decision 22, G1 schema | Moderate. Without it, out-of-order arrivals never recover |
| B5 | 🟡 **The bank line resolves in two hops** — trace number to remittance, remittance to claims | Decision 21, G4 plan | Low to reverse conceptually, but there is no alternative: a bank record genuinely carries no claim identifier |
| B6 | 🟡 **Reopened is a flag, not a fourth disposition** — same queue, flagged, driving priority | Decision 26, G1 schema | Low |
| B7 | 🟡 **Episode status is a worst-wins rollup** of the two track statuses | Decision 27 | Low |
| B8 | 🟡 **`INSUFFICIENT_DATA` is a first-class reason code** | Decision 25 | Low. But it is what drives the agent's required "here is precisely what is missing" behaviour |
| B9 | 🟡 **Two profiles from one seed** — `demo` ~60 hand-stratified episodes, `full` ~1,500 covering all 372 verdict pairs | Decision 14, G2 plan | Low |
| B10 | 🟡 **Lineage and audit trail are different things**, both kept — lineage runs downward to source rows, audit trail runs across time | Decision 32 | Low |

---

## Section C — Never discussed

Nothing here was ever raised in conversation. Items marked ⚙️ were decided by a planning or reconciliation agent on its own authority during the planning phase — they are defaults, not agreements, and they are currently baked into the committed plans.

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

These were forced by conflicts between the plans. Each is defensible, none was agreed with a person.

| # | Item | Ruling | Why it was forced |
|---|---|---|---|
| C9 | ⚙️ **Medical 340B join key** | `{provider_npi, ndc11, service_date}` — a clinic-infused drug has no prescription, so no Rx number exists | Without it roughly half the 372 verdict pairs are unreachable |
| C10 | ⚙️ **All generated names are fictional** | Real payer, PBM and manufacturer names removed from the documents | The assignment forbids real client names in generated data. This one is not optional |
| C11 | ⚙️ **A `277CA` acknowledgment record is added** to the medical submissions file | Accepted | Verdict B-01 is otherwise indistinguishable on the wire from B-02 |
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
| Implementation | **Not started.** Held pending the Section B and C walkthrough |

The plans as written assume every Section B item and every Section C default. Building them as-is would mean implementing unconfirmed decisions, which is why implementation is paused.
