# Architecture Decisions — Post-Claim Pharmacy Financial Reconciliation

**Purpose.** This is the decision record for the take-home prototype. It captures every architectural decision reached during design, with the reasoning behind it, so none of it has to be re-derived and so each one can be defended standalone in a 20-30 minute technical walkthrough. Companion documents: [`reconciliation_state_space.md`](./reconciliation_state_space.md) (the state-space enumeration these decisions produced) and [`feed_formats.md`](./feed_formats.md) (what each generator actually emits).

**Headline numbers, as of this decision set.**

| Measure | Value |
|---|---|
| Unconstrained cross-product of every dimension | 12,093,235,200 |
| Valid configurations | 4,224 |
| — logically coherent | 3,860 |
| — compliance anomalies (data that should not exist) | 364 |
| Reachable reimbursement verdicts | 31 |
| Reachable rebate verdicts | 12 |
| Reachable verdict pairs (31 × 12, exact product) | 372, all 372 reachable |
| Deterministic rules | 50 (43 track + 7 cross-track) |
| If-checks evaluated per claim | ~21 average (min 10, max 32) |

The 372 verdict pairs being an *exact product* of 31 and 12 means the two tracks are independent at the verdict level — every cross-track rule (Section 4 of the state-space doc) is an annotation on top of an already-complete pair, never a gate that prunes it. See [`decision_tree/REPORT.md`](../decision_tree/REPORT.md) for the exhaustive-generation methodology that produced these figures, including the corrections log where hand-counts were checked and, in one case, a design decision (dropping SLA thresholds) deliberately moved them.

---

## A. Scope and domain

### 1. Reimbursement track is mandatory, exactly one per episode (pharmacy XOR medical)

**Decided.** Every episode carries exactly one reimbursement track — pharmacy-benefit (PBM) or medical-benefit — never both, never neither.

**Why.** The assignment scopes the prototype to begin "after a claim has been filed"; claim creation is explicitly upstream and out of scope, so a filed claim is the precondition for an episode existing at all. And a drug only ever travels one billing road: pills and self-administered drugs go to the PBM, infused and clinician-administered drugs go to the medical payer. Billing the same dispense down both roads is duplicate billing — that's fraud, not an exception, and the model should make it unrepresentable rather than discover it downstream as a flag.

**Rejected.** Two independent optional booleans (`has_pharmacy_claim`, `has_medical_claim`). That gives 17 × 16 = 272 reimbursement configurations, versus the XOR's 31 (16 + 15, mutually exclusive so states sum instead of multiply). The 241-configuration gap is entirely states that cannot legally exist — a design that can represent "both tracks active" is a design that has to be told, elsewhere, never to trust that representation.

### 2. 340B track is optional

**Decided.** The 340B rebate track is present-or-absent per episode, independent of which reimbursement track is active.

**Why.** The assignment says a claim "may" create a 340B rebate — stated directly, not inferred. C-00 ("no 340B track") is the encoding of "absent."

### 3. Out of scope, deliberately: the no-claim dispense

**Decided.** A drug dispensed with no claim ever filed is excluded from the model.

**Why.** This is real revenue leakage — a cash-pay patient, or a submission that failed silently — and 340B can still be live off the dispense record even with no claim. But detecting it requires anchoring episodes on dispenses rather than on claims, which contradicts the assignment's stated boundary and its claim-centric business object. Carried as a one-line assumption in the design note rather than solved.

### 4. Rebate model, not replenishment

**Decided.** 340B discount reaches the operator as cash back (rebate), not as discounted replacement stock (replenishment).

**Why.** Replenishment is the dominant real-world 340B mechanism: the pharmacy buys at wholesale, dispenses, then "flips" a bottle to the 340B account once the dispense is confirmed to qualify, earning a discount on its *next* wholesale purchase. No cash moves after the fact — it reconciles against wholesaler invoices, producing an accounts-payable credit, not a receivable. There is no bank leg and nothing to reconcile. The assignment's own language — "manufacturer payment," "unmatched rebate" — describes the rebate model, not replenishment, so that's what gets built.

**Simplification, stated explicitly.** Replenishment is real and common; it is out of scope here. Production would need to model both mechanisms, and replenishment would reconcile against wholesaler invoice lines rather than bank deposits — a structurally different verification path, not a variant of the one built here.

### 5. Rebates arrive batched

**Decided.** One rebate payment covers many dispenses, tied together by an `allocation_code` that is the same kind of shared code the bank feed already needs for lump-sum matching.

**Why.** This is how the real manufacturer rebate process works, and it reuses machinery (batch allocation, residual tracking) the design already needs elsewhere rather than inventing a second pattern.

**Rejected.** A 1:1 rebate-per-prescription model. Simpler, but unrealistic, and it would require a distinct code path (single-claim matching) alongside the batch-allocation path for no actual benefit — the batch case has to be handled regardless, since it's the real-world default.

### 6. The insurer behind the PBM is out of scope

**Decided.** The insurer that ultimately backs the PBM's adjudication never appears as an entity, never emits an event, never appears in a feed. The PBM is modelled as the reimbursing counterparty in full.

**Why.** Nothing in the four source feeds surfaces the insurer as a distinct actor — the PBM is the only party the pharmacy or the pipeline ever transacts with directly. Carried as a one-line assumption.

---

## B. Identifiers and the crosswalk

### 7. There is no universal claim identifier, and the generators must not invent one

**Decided.** No shared `claim_id` exists anywhere in the design. Each source system's identifiers stay exactly as native as that system would produce them.

**Why.** This is the single most important decision in the design, and it's backed by research into the actual identifier universes, which turn out to be three separate, non-overlapping systems:

| Domain | Identifiers | Assigned by |
|---|---|---|
| Pharmacy / NCPDP | Transaction key {pharmacy NPI (201-B1), Rx number (402-D2), fill number (403-D3), date of service (401-D1)}, NDC (407-D7) as tie-breaker; separately, the PBM's own Authorization Number (503-F3) | Pharmacy (transaction key); PBM (auth number) |
| Medical / X12 | CLP01 (provider-assigned, echoed from 837 CLM01); CLP07 (payer-assigned ICN) | Provider (CLP01); Payer (CLP07) |
| Bank | 15-digit ACH trace number (plumbing only); TRN02 (payer-assigned reassociation number, present only if CCD+ addenda survived) | Banking network (trace number); Payer (TRN02) |

Neither CLP01 nor CLP07 exists in NCPDP. The medical feed and the pharmacy feed share literally nothing — separate standards bodies, separate rails, separate vocabularies. A generator that invents a shared claim ID papers over the exact problem the connector is supposed to solve.

### 8. Three bridges, each with a documented failure mode

**Decided.** The crosswalk is three separate joins, not one, and each has a named, real failure mode rather than being treated as a reliable foreign key.

| Bridge | Join key | Failure mode |
|---|---|---|
| PBM ↔ 340B | Natural key only: {pharmacy NPI, Rx#, NDC, fill date} — the PBM's authorization number never reaches the TPA. What flags a claim as 340B-related at adjudication is NCPDP Submission Clarification Code 420-DK = 20, a **flag**, not an identifier. | TPA lag, retroactive eligibility flips, reversal after scoring |
| 837 ↔ 835 | CLP01 round-trip | ICN reassignment during reprocessing, claim splitting, clearinghouse truncation |
| 835 ↔ bank | TRN02, per CAQH CORE Rule 370 | Breaks constantly — most bank CSV exports drop the addenda entirely |

**Why.** Naming the failure mode up front is what turns "the crosswalk sometimes misses" from a bug report into a designed-for case with a designed-for handling path (parking, Section D).

### 9. The 340B feed is deliberately the structurally poorest

**Decided.** The 340B/TPA feed has no 835, no trace number, no standard underneath it — a vendor-specific CSV, format varying TPA to TPA.

**Why.** This is not a modelling shortcut; it's the real state of the 340B TPA market today. Real TPAs ship vendor-specific exports with nothing standardized between them. It's also *why* "unmatched rebate" is one of the assignment's own named exceptions — a feed with no shared identifier space is structurally prone to losing the thread, and that fragility is the point being tested.

---

## C. Generator architecture

### 10. Four independent generators plus an orchestrator

**Decided.** One generator per source system (PBM, medical/837-835, 340B/TPA, bank), each receiving only the slice its real-world source system would actually know, each emitting its own native format with its own identifiers. No generator ever sees another's output.

**Why.** This is what makes the crosswalk problem real instead of assumed. A generator that can see the whole picture will, even accidentally, leak a shared key across feeds.

### 11. The orchestrator holds the only complete picture

**Decided.** The orchestrator alone knows the full episode — which verdict it lands on, what each feed should contain — and writes `ground_truth.json`. The connector under test is never allowed to read that file.

**Why.** This is how the crosswalk actually gets scored rather than assumed correct. The connector has to resolve identifiers the hard way, on the same information a real operator would have, and the test harness checks its output against ground truth it never saw.

**Rejected.** Designing the generator backwards from the canonical model — i.e., generate the ground-truth episode first, then stamp a shared `claim_id` onto every feed's version of it. That would give every feed a convenient common key and reduce the connector to a no-op join, testing nothing about the actual crosswalk problem.

### 12. Two files per reimbursement channel, not one

**Decided.** PBM: NCPDP claim events file + 835 remittance file, kept separate. Medical: 837 submission file + 835 remittance file, kept separate.

**Why.** In reality these are different systems on different rails, arriving days to weeks apart. Merging them into one file would delete the reassociation problem entirely — the payment would arrive pre-attached to the claim, and the crosswalk layer (Decision 8) would have nothing to do.

### 13. JSON output, except the bank feed

**Decided.** All generators emit JSON (JSONL, one record per line) except the bank feed, which is CSV.

**Why.** A real bank export genuinely is flat CSV — banks were never designed to carry claims data, and forcing JSON onto it would misrepresent the feed it's modelling. Having one structurally poorer feed in the mix (CSV, no addenda, no trace number reliability) is itself part of the point (Decision 9's sibling on the cash side).

**Reaffirmed at reconciliation (2026-09-12), against a "vendor CSV export" reading of the 340B feed.** The 340B feed is `tpa_340b_events.jsonl` — JSONL — because the rebate payment batch is irreducibly nested (one total, N dispense lines) and the worked examples in `feed_formats.md` §2 are field-level JSON specifications the connector is written against. The 340B feed's structural *poverty* (Decision 9) is about identifiers and linkage, not the envelope; JSONL preserves it in full.

### 14. Two profiles from one generator and one seed

**Decided.**

| Profile | Episodes | Purpose |
|---|---|---|
| `demo` | ~60, hand-stratified | The walkthrough. Curated to include both happy paths, every required edge case, and the strongest cross-track compliance cases. Small enough to be readable end to end. |
| `full` | ~1,500 | What the test suite asserts against. Large enough to hit all 372 verdict pairs at least once. |

**Why.** The debrief requires tracing *one* claim end to end, and nobody does that against a 1,500-episode file. `demo` exists purely to make the walkthrough legible; `full` exists purely to make coverage an assertion rather than a hope.

### 15. Sampling is stratified over verdicts, never over configurations

**Decided.** The `full` profile's episode generation is stratified so that every reachable verdict (and ideally every verdict pair) is represented, rather than sampling uniformly over the underlying 4,224 configurations.

**Why.** Configuration frequency is wildly non-uniform: measured on the current 4,224-configuration tree, the spread is **252x at verdict-pair level** (min 1, max 252 configurations per pair), and **99 of the 372 pairs sit on exactly one configuration**. The rarest cross-track flags are X-6 (8 configurations) and X-4 (9); the X-1 compliance case is 36 configurations across 16 pairs. (An earlier draft cited a "~60x" spread and "X-1 = 1 configuration" — both were figures from the pre-SLA-removal 7,046-path run and are stale; `decision_tree/REPORT.md` §5 carries the correction.) Uniform sampling would very plausibly miss the single-configuration pairs entirely in a 1,500-episode run — stratifying over verdicts is what makes the coverage guarantee in Decision 14 actually hold.

---

## D. Time, replay and processing

### 16. One added field: `received_at`

**Decided.** Every generator adds exactly one field that is not native to any of the four real-world formats: `received_at`, the timestamp a record entered the pipeline.

**Why.** Every date native to the real formats is backward-looking — `date_of_service`, `payment_effective_date`, `fill_date`, posting dates — each one describes something that already happened. No record carries a promise about the future. `received_at` is the one field that records the order records *showed up* in, as distinct from the order the underlying events happened in, which is what makes replay meaningful.

**Rejected.** A separate `event_date` field. The native formats already carry their own domain dates as ordinary content (date of service, fill date, and so on); adding a generic `event_date` on top would be double-counting the same information under a second name.

### 17. One documented exception to no-future-dates

**Decided.** The NACHA CCD+ batch header's Effective Entry Date is modelled precisely as a genuine forward-looking date.

**Why.** It is not a promise about an uncertain outcome — it's a settlement instruction about a decision already made and already transmitted to the ACH network ("this committed money lands on Wednesday," not "we'll decide by Wednesday"). Pretending it doesn't exist, or forcing it backward to satisfy a blanket rule, would misrepresent a real field. By the time the value reaches our side of the pipeline it has already resolved to a `posting_date` anyway — even this one exception is backward-looking by the time it becomes a row we ingest.

### 18. The generator has no concept of "now"

**Decided.** The generator writes the entire timeline start to finish with no run-date parameter anywhere. The cursor lives entirely at read time: `process(records where received_at <= cursor)`.

**Why.** Moving the cursor forward folds in later-arriving records and changes verdicts; moving it backward reproduces an earlier answer — same code path both directions, no separate "replay mode." This gives the audit answer for free: "what did we believe on 31 March" is just a cursor value, not a feature that has to be built.

### 19. SLA thresholds dropped

**Decided.** Aging (`age_days = cursor - date_of_service`) is a read-time sort key over pending/exception queues, never a verdict input. No record carries an SLA field, and no rule branches on elapsed time.

**Why.** The payoff is provable, not just convenient: if disposition can only change in response to a new inbound record, then a claim with no new record since the last run cannot have a different disposition now. That makes event-driven incremental processing correct by construction — "only recompute claims touched by new events" is a property you can state and prove, not an optimization you hope is safe. It also removes an entire category of number to defend in review (why 30 days and not 45?).

Real timing rules do exist in the industry — Medicare's 30-day payment ceiling, state prompt-pay statutes (commonly 30 or 45 days), Iowa's 20-day PBM-specific rule, CAQH CORE Operating Rule 370's ±3-business-day remittance window, the 340B pilot program's 45-day submission window and 10-day manufacturer-payment window — and dropping them is a deliberate exclusion, not an oversight. **If reintroduced, they belong in engine configuration, looked up per tenant/source/pathway, never as a literal threshold inside a verdict rule.** `if age_days > 30` forks a rule per payer; `if age_days > sla_for(tenant, source, pathway)` doesn't.

### 20. Event-driven ingestion, not scan-driven

**Decided.** The inbound document is the trigger. It carries its own lookup keys; the pipeline parses it, extracts those keys, fetches only the claims they resolve to, recomputes those, and appends a status row.

**Why.** With key resolution, the age of the claim being resolved is irrelevant — a 200-day-old claim is exactly as cheap to resolve as one from this morning, because resolution goes key → claim, not claim → does-a-new-record-exist-for-it.

**Rejected.** Scanning claims under a time window and checking each for new activity. This is brute force: to guarantee catching a genuinely stale claim you have to widen the window indefinitely, and correctness depends entirely on the window being wide enough — a bound you can never fully trust.

### 21. The bank line resolves in two hops

**Decided.** A bank deposit is never matched directly to a claim. `trn02` (or amount+date, when TRN is missing) resolves to a *remittance*; the remittance holds the claim list.

**Why.** A bank line carries no claim identifier at all — it can't, a bank has no concept of a claim. One bank line can plausibly touch 47 claims through a single remittance, and the only correct path to them is through the remittance it points at, never by scanning claims for one that might match the deposit amount.

### 22. Parked records and the backward re-check

**Decided.** A document whose keys resolve to nothing yet is parked, not discarded. Every inbound document does two things: resolve its own keys forward against existing claims, and check the parked pool backward — does anything sitting there now resolve against *this* document?

**Why.** Skip the backward check and two failure modes become permanent instead of temporary: an out-of-order arrival (a bank deposit that beat its own remittance to the door) never gets a second chance to match, and an unmatched deposit stays unmatched forever even after the record that would explain it finally lands.

### 23. Recompute, never mutate

**Decided.** Verdicts are derived per (claim, cursor) and appended to an append-only log — never edited in place. The exception queue is a query over that log, not a place records get moved into.

**Why.** The verdict log records what was derived, not the source of truth; delete it and it rebuilds by replay against the immutable source records. This is also what gives `reopened_from` (Decision 26) any meaning — a claim can't be meaningfully "reopened" from a state that was overwritten rather than preserved.

---

## E. Status model

### 24. Three dispositions — CLOSED, PENDING, EXCEPTION

**Decided.** Status answers exactly one question — "what do I do with this claim?" — and there are exactly three answers: `CLOSED` (nothing to do), `PENDING` (waiting on an external party, no defect, ranked by age), `EXCEPTION` (a defect exists, work it).

**Why.** Anything finer-grained than this belongs one layer down, in reason codes, not in the disposition itself.

### 25. Reason codes are a list, not a single value

**Decided.** `reasons` is a list on the verdict (e.g. `[UNDERPAID, CASH_MISMATCH]`), including `INSUFFICIENT_DATA` as a first-class code.

**Why.** A claim can be underpaid *and* missing settlement *and* show a cash gap simultaneously, and with two tracks in play it can be broken on one track while merely waiting on the other. A single reason field cannot hold that; a list can. `INSUFFICIENT_DATA` fires specifically when the engine deterministically cannot decide (an unmatched recoupment, rebate cash with no attributable episode, a correlated no-cash finding on both tracks at once are the worked examples). That code is exactly what drives the agent layer's required "I cannot determine this from the available feeds, and here is precisely what is missing" behavior instead of guessing.

### 26. Reopened is a flag, not a fourth disposition

**Decided.** `reopened_from`, `previously_closed_at`, `reopened_on` are attributes on the exception or pending record, not a fourth queue.

**Why.** A reopened claim still either waits or gets worked — it doesn't need a separate bucket, only provenance. The flag drives deterministic priority: reopened-from-closed outranks never-paid, because money already recognized and now at risk of being un-recognized is a stronger claim on attention than money simply never collected. And reopening isn't exclusively a fall from `CLOSED` — a claim can be reopened out of `PENDING` too (e.g., a 340B rebate clawed back while reimbursement is still legitimately in flight); the flag records where it came from either way.

### 27. Status exists at two levels

**Decided.** Status is computed per track, and per episode as a rollup where the worst status wins (`EXCEPTION > PENDING > CLOSED`).

**Why.** An episode can sit in the exception queue because one track is broken while the other is legitimately still waiting. The rollup surfaces the worse of the two rather than averaging them or hiding the healthy one, because the worse one is what needs a human.

### 28. Disposition and reasons are computed in one pass

**Decided.** The same rule that decides `EXCEPTION` also produces the reason and the variance, in one pass — not a first pass that decides and a second pass that explains.

**Why.** Splitting "decide" from "explain" into separate passes would let them drift out of sync: a defect flagged with no reason attached, or a reason attached to a claim the first pass called clean. The rule that decides is the rule that knows why, because it's the same rule.

---

## F. The deterministic/agent boundary

### 29. Every number is born in Python

**Decided.** Expected amounts, payment allocation, reconciliation status and ledger updates are all deterministic and auditable, computed in Python. The LLM may investigate, synthesize, classify, prioritize and recommend, but never compute a reconciliation number itself. `calculate_reconciliation(claim_id)` is a tool the agent *calls* rather than logic it reproduces in a prompt.

**Why.** This makes the boundary mechanical rather than a matter of prompting discipline — the agent cannot drift into computing a number itself because the number-producing logic isn't reachable except through the tool call.

### 30. Prioritization is a sort, not a judgement

**Decided.** Ranking exceptions by value, age, and category is a deterministic sort over verdict attributes (including the reopened-from-closed precedence rule, Decision 26). The agent explains a ranking; it never produces one.

**Why.** Same reasoning as Decision 29 — a ranking that could plausibly vary by phrasing or model run is not something an operator can trust to be reproducible.

### 31. One write tool only

**Decided.** The agent has exactly one write capability: creating a work item in the mock workflow queue. It never writes a ledger entry.

**Why.** Keeps the agent's blast radius to "flags a human," never "moves money" or "changes a recorded fact" — consistent with Decision 29's boundary.

### 32. Lineage and audit trail are different things, both needed

**Decided.** Both are built, and kept conceptually separate.

- **Lineage runs downward.** Every computed number points back to the exact raw source row that produced it. This is what the assignment actually grades ("preserve source identifiers so an investigator can trace a result back to the synthetic source record") and it's what makes agent citations real rather than plausible-sounding.
- **Audit trail runs across time.** The append-only log of every verdict a claim has held, in order (Decision 23). It falls out for free once the system is append-only, and it's what gives `reopened_from` any meaning at all — a claim can't be reopened from a state that was overwritten.

**Why they're distinct.** They answer different questions: lineage answers "where did this number come from," audit trail answers "what did we believe, and when did we stop believing it." Building only one leaves the other question unanswerable.

---

## G. Scale

### 33. Rule count is invariant under scale

**Decided.** Adding new PBMs, TPAs, payers, tenants or bank accounts adds configuration rows, never new rules.

**Why.** None of those dimensions is an input to the reconciliation *question* — the engine only ever asks what was expected, what arrived, and whether the difference has an explanation. Which PBM sent the money changes the vocabulary the answer arrives in, not the shape of the question. A rule is invariant under a scale dimension exactly when that dimension appears in the rule only as a lookup, never as a branch: PBM A rejects with code `70`, PBM B with `A1`, both mean rejected, both resolve to A-01 — the mapping table gains a row, `if adjudication == REJECTED` is untouched.

**Three stated limits, not hidden:**

1. **Rules must be parameterized, or the claim is false in practice.** A literal threshold (`if age_days > 30`) forks per payer the moment a second payer needs a different value; the same rule expressed as a config lookup (`sla_for(tenant, source, pathway)`) does not. This is what makes SLA removal (Decision 19) not merely a modelling preference but a scaling precondition.
2. **Row growth is large and is the highest-maintenance surface.** A real 835 carries hundreds of CARC/RARC codes per payer. "Configuration rather than one-off code" is a claim that config rows are cheap to add and reviewable in bulk, not a claim that the config itself is trivial.
3. **A genuinely new event *semantics* does add states.** Adding a fifth PBM is rows; adding a payer that does capitation or bundled payments is not, because fee-for-service's expected-per-claim frame doesn't apply to either. The containment mechanism is that adapters map into a *closed* canonical event vocabulary, and anything that doesn't map is quarantined (as a D-5 malformed-record exception) with lineage intact, never coerced into the nearest-looking slot. New codes are configuration; new semantics are a canonical-model change, and the system should refuse to guess which one it's looking at.

---

## H. Technology stack

**34. Python for the backend, FastAPI for the API layer, React for the front end.**

The assignment permits any reasonable stack and asks only that the choice be justified and that production gaps be named.

| Layer | Choice | Reason |
|---|---|---|
| Generators, ingestion, reconciliation engine | Python, standard library first | The deterministic core is arithmetic and rule evaluation. No framework earns its weight here, and a dependency-light core is easier for a reviewer to read end to end. |
| Persistence | SQLite | Stdlib, no service to run, and the exception queue becomes a real query rather than a list comprehension. Lineage joins are expressible in SQL, which makes the evidence trail demonstrable instead of asserted. |
| API | FastAPI | Typed request/response models via Pydantic give the cursor and verdict contracts a schema for free. Async is not needed for the prototype but does not cost anything. |
| Front end | React | Needed for the cursor control described below. |

**35. The front end exists to make the cursor visible.**

The reconciliation answer is a function of `(claim, cursor)`. A static page cannot show that. The interface advances a date cursor across the 1 July 2025 – 1 July 2026 window and re-renders the queues, so the same claim can be watched moving `PENDING → CLOSED → EXCEPTION` as later records arrive. That is the clearest possible demonstration of the replay model and it costs one control.

**36. Layering discipline: the engine must not import the API.**

The deterministic core is a library. FastAPI depends on it; it never depends on FastAPI. The generators are a separate entry point that writes files and touches neither. This keeps the core independently testable and means the walkthrough can be driven from a CLI if the front end is not running.

**Production gaps to name in the design note:** SQLite gives way to Postgres once there is more than one writer; the in-process engine becomes a worker consuming an ingestion queue; the React app needs real authentication and tenant scoping, none of which the prototype implements.

---

## I. Reconciliation decisions (2026-09-12)

Rulings made when the four parallel implementation plans were reconciled. Full rationale per conflict lives in `plans/RECONCILIATION.md`; these are the binding outcomes.

### 37. The medical 340B join is a natural key with no Rx number

**Decided.** The 340B track attaches to medical episodes through the TPA feed. A medically-administered drug is bought and infused at the covered entity's clinic — there is no prescription, so there is no Rx number. Medical-benefit TPA records carry `rx_number: null`, `pharmacy_npi: null`, and `provider_npi` (the 837 billing provider NPI); `fill_date` carries the administration/service date. The join key is **{provider NPI, NDC, service date}** — registered as crosswalk key type `NATURAL_340B_MEDICAL`.

**Why.** `reconciliation_state_space.md` §5 makes all 15 medical verdicts 340B-compatible; without a medical join key, roughly half the 372 verdict pairs are unreachable and the coverage assertion fails. The key is deliberately weaker than the pharmacy key (two same-day administrations of the same drug at the same site are ambiguous) — the connector parks ambiguity rather than guessing, which is a real hazard worth modelling, not a defect of the design.

### 38. All generated entity names are fictional

**Decided.** The assignment's confidentiality clause ("synthetic data only… no client names") binds the *generated data*: every payer, PBM and manufacturer name in feeds and worked examples is invented (`MERIDIANRX`, `CASCADERX`, `BLUE HARBOR HEALTH`, `GRANITE PEAK HEALTH`, `VERION`, `ALDEBARAN`, `CORVANE`, `TALVEX`, `SAGEPOINT`, `HALCYON`), with realistic *shape* preserved (≤16-char NACHA truncation, uppercase, 6-digit BINs). Real names may appear in prose as domain context only. Generic drug names and public HCPCS J-codes are public clinical reference data and may be kept; all prices are invented.

### 39. The 277CA clearinghouse acknowledgment is part of the medical submissions file

**Decided.** `medical_837_submissions.jsonl` carries a second record type, `record_type: "277CA"`, with `stc01_composite` accept/reject codes (`feed_formats.md` §3). Without it, B-01 (clearinghouse rejection) is indistinguishable on the wire from B-02 (accepted, awaiting 835). The 277CA is the real, standard acknowledgment on the same rail — realism, not invention — and it preserves Decision 16 (`received_at` remains the only non-native field). The rejected alternative, a non-native `clearinghouse_status` field on the 837, violates Decision 16.

### 40. Settlement is encoded via CLP02, and the engine must mirror the read

**Decided.** `ph_settlement` has no native transaction; it is encoded on the paying 835 claim line's CLP02: `"1"` = CONFIRMED; `"19"` (or rarer `"25"`) with no later `"1"` line for the same CLP01 = MISSING. The reconciliation engine's rule for verdict A-06 must read exactly this encoding. Documented in `feed_formats.md` §1.

### 41. PLB sign convention: credits are negative

**Decided.** Under the identity `BPR02 = Σ(CLP04) − Σ(PLB, signed)`, a positive PLB reduces payment. `WO`/`FB`/`CS`/`72` are positive (reduce the deposit); `L6` and appeal-credit `RA` are **negative** (increase it). An earlier `feed_formats.md` draft said appeal credits were positive — self-contradictory with its own identity; corrected.

### 42. The rebate deposit's `allocation_code` rides in the bank `trn02` column

**Decided.** `trn02` is semantically "the payer-assigned business reference from the CCD+ addenda": the 835 trace number for claim payments, the `allocation_code` for manufacturer rebates. Same column, same ~20% addenda-loss rate, different issuer. A rebate deposit that loses its addenda and fails amount+date matching *is* D-4 — the orphan rebate emerges from the mechanism instead of being hand-placed, and the bank CSV needs no rebate-specific column.

### 43. Two additional TPA event types: `REBATE_REQUEST` and `MANUFACTURER_DECISION`

**Decided.** `REBATE_REQUEST` (carrying `submission_date`) separates C-03 from C-05 on the wire and makes `NON_CONFORMING_45_DAY` representable; `MANUFACTURER_DECISION` carries rejections (C-07), which cannot ride inside a payment batch. Both are additive to `feed_formats.md` §2's original four examples.

### 44. One generator contract: two-level slices, declare → realize → format

**Decided.** All four generators build against one contract module, `src/recon/generators/contracts.py`, owned by G2. Its shape:

- **Two-level slices.** Per-episode event slices (`PbmClaimSlice`, `MedicalSubmissionSlice`, `TpaDispenseSlice`) plus cross-episode batch slices (`PbmRemittanceSlice`, `MedicalRemittanceSlice`, `RebateBatchSlice`) — batching is irreducibly cross-episode. Seven generator entry points; every slice carries `slice_ref` (opaque) and `rng_seed`; none carries an episode ID, verdict code, case ID or cross-track flag.
- **Division of labour.** The orchestrator owns all facts, all dates (`received_at` included), all per-claim/per-line amounts, and **batch composition** (which claim payments share a remittance, which PLB entries attach to which later batch, which dispenses share a rebate batch — the netting ledger is orchestrator machinery). Generators own native formatting and their own identifier namespaces (`authorization_number`, `clp07`, `ach_trace_number`, `record_id`), and they own their channel's **wire arithmetic**: each batch generator computes `BPR = Σ(CLP04) − Σ(PLB, signed)` from its slice's lines and totals a rebate batch from its dispense lines. Batch slices carry no precomputed net total; the total exists exactly once, where it is formatted.
- **Money flows declare → realize → format.** Payer-side generators return `MoneyMovement[]` (amount = the net they computed, PLB already applied; `payer_reference` = trn02 or allocation_code). The **orchestrator's realizer** — the only place cash faults live — turns movements into `CashEvent[]` applying `MATCHED`/`PARTIAL`/`ABSENT`, injects D-3 orphans and true-reversal debits, and decides `trn02` presence (the ~20% drop) so ground truth can record link resolvability. `gen_bank` is a pure formatter of `CashEvent[]`: it never learns a deposit is missing because it never sees an expected figure.
- **No file back-channels.** Generators communicate with the orchestrator only through return values (`Record[]`, `MoneyMovement[]`). The previously proposed `_remittance_manifest.jsonl` under `data/generated/_internal/` is disallowed; the orchestrator, which invoked the generators, already holds everything ground truth needs.
- **Minting.** The orchestrator mints all cross-feed values (natural keys, `trn02`, `allocation_code`, `clm01`, `cardholder_id`) per the crossing matrix in `plans/G2-orchestrator.md`; a value may be handed to two generators only where it genuinely crosses those systems in reality (natural keys, TRN02, allocation_code).

### 45. Entity universe sizes (closes the former open question)

**Decided.** The smallest universe in which every disqualification reason and defect is generatable:

| Entity | Count | Note |
|---|---|---|
| Specialty drugs | 12 | 7 pharmacy-benefit, 5 medical-benefit (J-coded) |
| Pharmacies | 2 | the operator's main site + one satellite **not registered** with the covered entity — makes `UNREGISTERED_LOCATION` generatable |
| Billing provider (medical) | 1 | |
| Prescribers | 6 | at least one unaffiliated with either covered entity — makes `PRESCRIBER_NOT_AFFILIATED` generatable |
| PBMs | 2 | two reject-code vocabularies demonstrate Decision 33's mapping-row claim |
| Medical payers | 2 | |
| Covered entities | 2 | the operator's own (`DSH…`) + one decoy (`PED…`), per the two IDs in `feed_formats.md` §2 |
| Manufacturers | 6 | fictional restricting wave (Decision 38); ≥1 with `restricts_contract_pharmacy = True` |
| Patients | 40 | tokenized cardholder IDs, no demographics |

**Why this and not more.** Scale-invariance (Decision 33) is demonstrated by configuration rows, not by universe size; a bigger universe would only make the demo harder to read. Batching realism (one 835 covering dozens of claims) comes from claim volume per (payer, cycle), which two payers per channel provide.

### 46. Defect injection rates (closes the former open question)

**Decided.** All rates live in config as named constants; only the 20% TRN drop is sourced from `feed_formats.md` — the rest are set here:

| Defect | Rate (`full`) | Note |
|---|---|---|
| Dropped TRN02 / addenda on bank credits | **20%** | sourced; applies to rebate deposits too (Decision 42); decided by the orchestrator's realizer, not the bank generator |
| D-1 duplicate delivery | 3% of records | identical payload, same `record_id`, later `received_at` on the second copy |
| D-2 late arrival | 8% of episodes, one record each | `received_at` shifted +7..45 days; event dates untouched |
| D-3 orphan bank deposit | ~2% of bank rows | synthesized by the realizer |
| D-4 orphan rebate | ~2% of rebate dispense lines | plus the emergent Decision 42 path |
| D-5 malformed record | 0.5%, `full` only, never `demo` | injected **centrally, post-write**, by the orchestrator corrupting emitted lines — a malformed record is a transport accident, not something a payer emits; generators stay provably well-formed |
| D-6 crosswalk miss | emergent | from TRN drops + identifier drift; recorded in ground truth `expected_links` |
| D-7 allocation residual | unreferenced `FB` on 5% of otherwise-clean remittances; 100% of recoupments net via PLB | |
| Rx identifier drift | 6% of 340B-bearing episodes | orchestrator assigns a `rx_number_rendering` directive to **exactly one feed** per episode (pharmacy 835 zero-padded is the canonical case); generators render as directed, never drift independently |
| Rx truncation | 2% of episodes | |
| `company_name` truncation, CLP07 reassignment on reprocess, J-code/NDC unit mismatch | always on | field limits and standards behaviour, not defects |

`demo` uses hand-placed defects (each D-code exactly once), never rates.

### 47. Canonical file names and output layout

**Decided.** Feeds: `pbm_claim_events.jsonl`, `pbm_remittance_835.jsonl`, `medical_837_submissions.jsonl`, `medical_835_remittance.jsonl`, `tpa_340b_events.jsonl`, `bank_transactions.csv`, under `data/generated/<profile>/feeds/`. Ground truth: `data/generated/<profile>/truth/ground_truth.json` — a directory the ingestion and engine layers never read (enforced by a lint test plus the loader taking only the feeds directory). Reproducibility manifest: `data/generated/<profile>/manifest.json` (SHA-256 per feed file, seed, window, reference fingerprint). `feed_formats.md` names win wherever a brief and the spec disagreed.

### 48. `decision_tree/pairs.json` is a live test oracle, owned by G1

**Decided.** G1's domain-vocabulary unit loads `pairs.json` in a test and asserts the 372 reachable pairs equal `recon.domain.verdicts.REACHABLE_PAIRS` (31 × 12, exact product). G2's coverage test asserts the `full` profile hits every pair in `REACHABLE_PAIRS` — a single ownership chain: the artifact proves the vocabulary, the vocabulary proves the dataset. `decision_tree/` itself is frozen; the orchestrator consumes `leaves_classified.json` with a digest check.

---

## Open questions

Not yet decided, listed so they aren't silently assumed:

- **Whether the mock workflow/Epic feed gets generated.** Leaning yes — it's the fifth box in the assignment's own architecture diagram, and it's the agent's only write target (Decision 31), so its absence would leave that write path untestable. If it lands it is an additive slice type in the Decision 44 contract; nothing else changes.
- **Agent framework choice.** Leaning toward a plain tool-calling loop over an agent framework, because the agent logic here is simple (investigate, cite, sort, recommend, write one work item) and a framework would obscure exactly the tool boundary (Decision 29) the assignment is testing.

*(Formerly open, now closed: entity universe sizes → Decision 45; defect injection rates → Decision 46; the drug price table → G1's reference/pricing unit, `plans/G1-foundation.md` U6.)*
