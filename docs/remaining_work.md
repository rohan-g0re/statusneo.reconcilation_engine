# Remaining work

*Functionality first, then hardening, then the write-up. Every "have" below was verified in running code on 2026-09-13, not assumed. Section 1 is what I need from you before I can start; everything after it is blocked or shaped by those answers.*

**Where we are** *(as of writing; the agent layer has since been built — see §0)*. The deterministic layer is complete and measured: four generators, connector, crosswalk, engine, API, front end. 214 tests. 372/372 reachable verdict pairs, 4,224/4,224 configurations matching the oracle, 99.6% verdict fidelity on the full profile. The agent layer is the only unbuilt component of the assignment.

---

## 0. Status: the queue below is closed

*This document was a decision queue. Every decision in it has been made and the work it
gated is built. Kept intact rather than deleted, because how a question was framed is
worth as much as the answer.*

| # | Asked | Resolved |
|---|---|---|
| D1 | Which **two** roles? | **Three.** Investigator, Coordinator and Portfolio Analyst. The binary framing was overtaken — the Analyst turned out cheap once the tool surface existed |
| D2 | Framework? | No framework. Custom harness. *User decided, after asking for the options ranked* |
| D3 | Model and provider | DeepSeek over an OpenAI-compatible client; 120k token budget per run as the cost ceiling |
| D4 | Live in the demo? | Live with a key, and a plain 503 explaining itself without one. The eval set replays offline |
| D5 | Eval grading split | Tool-path primary, outcome-shape secondary, no LLM judge in the eval set |
| D6 | How far do secure and scalable go? | Option 2, minimal hardening plus document. Prompt-injection fencing and PHI redaction built; auth and tenancy deliberately not |
| D7 | Where does the agent surface? | A panel. `/analyse/:episodeId`, plus the Analyst on the dashboard |
| D8 | `recommended_action` vocabulary | Approved, plus `ABSTAIN` |
| D9 | May I update the three stale docs? | Partially. `START_HERE.md` was updated; the ledger and the architecture decisions keep their no-touch rule, and their staleness is recorded in the knowledge graph instead |

**Still genuinely open**, and not made to look done: tenant isolation, rate limiting and
connector onboarding (§5), and recording which records *drove* each verdict rather than
which were merely visible (§6.1) — the Derivation lineage tier, still unbuilt.

---

## 1. Decisions I need from you *(historical — see §0)*

Ordered by what blocks the most. The first four block all agent work.

### D1. Which two agent roles? — **blocks everything in Phase 1**

The assignment requires at least two of three. Exception Investigator is effectively mandatory: it is the role the follow-up interview exercises (*"trace one claim end-to-end, show how an agent obtained its evidence"*), and the dossier was built for it.

The second is a real choice:

| Option | Case for | Case against |
|---|---|---|
| **Workflow Coordinator** | Demonstrates the safe-action boundary, which the rubric names explicitly. Uses the one write capability. `work_item` table already exists | More moving parts: needs an action vocabulary and a confirmation tool |
| **Ops / Portfolio Analyst** | Cheapest — queue rows already carry `age_days`, `absolute_variance_cents`, `reason_codes`, so ranking is a deterministic sort the agent only explains | Shows less. No write path, so "safe action boundaries" goes ungraded |

My lean: **Workflow Coordinator**, because "safe action boundaries and failure handling" is a named rubric line and the Analyst cannot demonstrate it.

### D2. Agent framework — **blocks Phase 1**

Ledger C20 is deferred with a stated lean toward a plain tool-calling loop over LangGraph/CrewAI, on the grounds that a framework would obscure the tool boundary the assignment is testing. The assignment agrees: *"Simpler is better if well reasoned."* I need this ratified, not assumed — it is exactly the kind of agent default the ledger exists to catch.

### D3. Model, provider, and who pays — **blocks Phase 1**

Which model, is there an API key available, and is there a cost ceiling I should design to. Also: does the eval set run against the live model in CI, or once with results committed?

### D4. Does the agent run live in the demo? — **blocks Phase 1 and the README**

If yes, a reviewer needs an API key to see anything, and the README must say so. If no, we record transcripts and ship them. The assignment allows the second explicitly: *"If something cannot be run locally, provide screenshots or a short recording and explain why."*

### D5. Eval grading: tool-path or expected-answer? — **blocks Phase 2**

Tool-path assertions are deterministic and need no LLM judge; expected-answer needs one or manual grading. `START_HERE.md` already leans tool-path. Probably a mix — tool-path for most, a few answer checks for the ones where the wording is the point. Need your call on the split.

### D6. How far do "secure" and "scalable" actually go?

This is the one I would push back on. The assignment says *"We are not looking for a production deployment"*, lists *"enterprise IAM buildout"* under what is **not** required, and grades scope discipline as a criterion in its own right. Its own stretch goal is *"a short production roadmap"* — a document, not an implementation.

Three honest options:

1. **Document only.** State the positions in the design note, build nothing. Cheapest, matches the stated scope, risks looking like hand-waving.
2. **Minimal hardening plus document.** Do the two or three things that are cheap and visible — redact PHI at the API edge, treat feed free-text as untrusted before it reaches a prompt — and write the rest up. My lean.
3. **Build it properly.** Auth, tenancy, rate limits. Against the brief and likely to cost the agent layer its polish.

### D7. Where does the agent surface?

A panel in the existing React app, or a CLI / notebook. The assignment says *"A lightweight CLI, notebook, Streamlit page or simple web interface is sufficient"* — so a CLI is enough, and the web app already exists. A panel is nicer to demo and costs more.

### D8. `recommended_action` vocabulary

The `work_item.recommended_action` column is free text today. A closed set makes agent output assertable in the eval set. Proposed: `RESUBMIT`, `APPEAL`, `WRITE_OFF`, `ESCALATE`, `INVESTIGATE_CROSSWALK`, `AWAIT_PAYER`. Approve, amend, or leave it free text.

### D9. The three stale design documents

`docs/decision_ledger.md`, `START_HERE.md` and `docs/architecture_decisions.md` assert things that are now false — "Implementation: Not started", "Application code: Zero committed", open questions that were closed. They carry a standing no-touch rule from an earlier instruction. May I update them? `START_HERE.md` matters most: it is the handoff entry point, so a fresh session currently reads "zero code committed" against a finished layer.

---

## 2. Phase 1 — finish the prototype

Nothing here is research. Every tool below reads data that already exists and is already reachable in one query.

| # | Task | Blocked by | Notes |
|---|---|---|---|
| 1.1 | Tool layer: episode retrieval, TPA/rebate status, remittance/denial, bank match, `calculate_reconciliation(claim_id)` | D2, D3 | The assignment's named minimum. All five are wrappers over existing endpoints |
| 1.2 | `get_raw_record(raw_id)` tool | D2 | Endpoint shipped in `24cf0b1`. Gives the agent verbatim source on demand without inlining payloads into every call |
| 1.3 | `create_mock_work_item(...)` writer | D1, D8 | Table exists, append-only, FK to `verdict_id`, **0 rows, no writer**. The agent's only write capability |
| 1.4 | Deterministic confirmation tool | D1 | The assignment forbids closing or posting *"without deterministic confirmation"*. Must be a tool the agent calls, never a judgement it makes |
| 1.5 | Agent role 1 — Exception Investigator | D1–D4 | |
| 1.6 | Agent role 2 | D1–D4 | |
| 1.7 | Tool-call trace log | D2 | Name, args, result, timestamp per call. Required deliverable content |

**Already done, do not rebuild:** the episode dossier is one call and returns identity, current verdict, economics, the full timeline, the verdict log, crosswalk keys and unresolved records. `INSUFFICIENT_DATA` is genuinely emitted by the engine (`dispositions.py`, A-13 and X-5), so the agent has a deterministic "I cannot determine this" signal rather than having to infer one. Every timeline event carries `source` with file, line and `raw_id`, and an `essential` key list so the agent can read the short view or the full one.

## 3. Phase 2 — evaluation and failure handling

| # | Task | Blocked by | Notes |
|---|---|---|---|
| 2.1 | Eval set, 10+ scenarios | D5, and Phase 1 | 372 reachable pairs and `pairs.json` make known-answer cases cheap to pick |
| 2.2 | At least one failure case | Phase 1 | A D-6 crosswalk-miss episode is the natural choice — the dataset already contains deliberate ones, and `unresolved` surfaces them per episode |
| 2.3 | Production-hardening note for that failure | 2.2 | Required alongside the failure case |
| 2.4 | Result summary | 2.1 | The core numbers currently live only in commit messages |

## 4. Phase 3 — security

Scope depends entirely on **D6**. Listed so the decision is informed.

| # | Finding | State | Cheap fix? |
|---|---|---|---|
| 3.1 | **Prompt injection through feed data.** `stc12_free_form` is free text that arrives from a payer and flows into the agent's context. Today it says "ACCEPTED FOR PROCESSING"; in production a hostile or malformed value lands directly in a prompt | Not addressed | Yes — delimit and mark untrusted before it reaches a prompt. This is the one I would fix regardless of D6 |
| 3.2 | **PHI/PII returned unredacted.** Canonical bodies carry `cardholder_id`, prescriber and rendering-provider NPIs, and patient-pay amounts; the API returns them as-is | Not addressed. The data is synthetic, so there is no live exposure — but the *design* has no position | Yes at the API edge |
| 3.3 | **No authentication on any endpoint** | Not addressed | Cheap to add, explicitly not required by the brief |
| 3.4 | **Agent write boundary is convention, not enforcement.** Nothing structurally prevents an agent from calling a write it should not | Partly: `work_item` is append-only with no update or close, and `raw_record` has `RAISE(ABORT)` triggers | Enforce by only exposing one write tool |
| 3.5 | Secrets handling for the model API key | Not addressed | Env var, documented |

## 5. Phase 4 — scale and operability

Also gated on **D6**. Much of this is already true and only needs stating.

**Already true:** query plans are pinned by tests that assert no table scan, so an index regression fails a test rather than costing latency. Ingest runs in 1.23s. The dataset is a derived artefact rebuildable from a seed, which is both the cheapest clean state and a continuous proof of reproducibility. The append-only design means history never needs migrating, and the cursor makes any past answer reproducible without a separate audit feature.

**Not addressed:** tenant isolation, connector onboarding for a real source, rate limiting and batching of model calls, and what happens when a feed arrives twice at scale rather than at demo size.

## 6. Optional — worth doing only if Phases 1–2 land early

| # | Task | Why it might matter |
|---|---|---|
| 6.1 | Record which records actually **drove** each verdict | `verdict_evidence` currently links a verdict to every record *visible* at that cursor (`dimensions.py:65`), not the ones that caused it. Measured: only ~30% of verdict changes have a single new record behind them. Reason codes narrow it a lot, so this is a real but narrow gap. The engine already knows which record fired the rule and discards it — one flag where it already branches |
| 6.2 | Housekeeping | Git remote still points at the pre-rename URL; the memory MCP server needs a full restart to pick up the absolute path fixed in `b200fd0`; the running browser tab holds stale Vite modules |

---

## 7. Deliverables, after the above

Not started, and deliberately last per your ordering — though note the design note has no code dependency and `decision_ledger.md:31` estimates it at *"roughly 40% of the grade"*.

- **Design note** (4–5 pages): domain, assumptions, architecture, data model, agent/tool design, security, what's next.
- **README**: architecture diagram, prerequisites, run instructions, assumptions, limitations, trade-offs. Must include `uvicorn recon.api.app:create_app --factory` — the module exports `create_app`, not `app`, and the default invocation fails.
- **Known limitations to state honestly**: 5 of 1,354 full-profile episodes do not reproduce their intended verdict; three projections are defensive and unexercised; `verdict_evidence` records what the engine saw rather than what it used.

---

*Shortest path: answer D1–D4, and Phase 1 starts immediately. D5 can wait until the first role works. D6 decides whether Phases 3 and 4 are a weekend or a paragraph — and the assignment's own scope-discipline criterion argues for the paragraph.*
