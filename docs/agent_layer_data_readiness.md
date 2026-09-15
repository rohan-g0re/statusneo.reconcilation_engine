# What the agent layer needs, and what it already has

*Measured against `docs/Assignment_doc.pdf` p.3–5 and the live dossier object on 2026-09-13. Every "have" below was read out of running code, not assumed. Read the three tables; the last one is the only work left.*

The short version: **the data is done, the plumbing is not.** Every fact the three agent roles need already exists and is already reachable in one query. What does not exist is the tool layer that hands it over, the write path, and the eval set.

---

## 1. What the agent already gets — `GET /api/episode/{id}/dossier`

One call, one episode, its whole history. This is the object; there is no second shape to assemble.

| Block | Fields | Answers the question |
|---|---|---|
| `identity` | track, drug, ndc11, quantity, date_of_service, payer, pharmacy_npi, rx_number, fill_number, clm01, billing_provider_npi, is_340b_flagged, opened_at | What was dispensed, to whom, billed how |
| `current` | reimbursement_verdict, rebate_verdict, episode_disposition, reason_codes, cross_track_flags, reopened_from, as_of | **Why is it open** — the Exception Investigator's core question, pre-answered deterministically |
| `economics` | expected/received/variance × reimbursement, expected/received/variance × rebate (integer cents) | How much is at stake, and on which track |
| `timeline` | ordered events, each `tag` + `facts` + `source` + `at` / `occurred_on` + `late` | What happened, in the order we learned it |
| `timeline[].source` | file, line, record_id, norm_id, raw_id | **Citations** — every fact traces to a raw feed row |
| `verdict_log` | every evaluation: cursor_at, disposition, both verdicts, reason_codes, reopened_from | How the answer moved over time |
| `crosswalk_keys` | key_type, key_value, first_seen_at | Which identifiers resolved here |
| `unresolved` | parked records: record_kind, park_reason, key_value, received_at | **"Identify missing information"** — records that tried to reach this claim and failed |
| `counts` | records, cash_allocations, verdicts_written, verdict_changes | Shape of the episode at a glance |

Two things here are worth more than they look. `unresolved` is the assignment's *"identify missing information"* requirement already satisfied — and it distinguishes "no data" from "data arrived but did not resolve," which is a different finding with a different owner. And `source` is what makes a citation real rather than plausible-sounding: the agent can quote a file and line number it did not invent.

Portfolio-level data is also live. `GET /api/queue/{disposition}` rows already carry `age_days`, `absolute_variance_cents`, `total_variance_cents` and `reason_codes` — so ranking by **value, age or category** is a deterministic sort the agent reads, never computes. `GET /api/overview` carries the aggregates: counts by disposition, verdict-pair distribution, reason-code and cross-track-flag frequencies.

And `INSUFFICIENT_DATA` is genuinely emitted by the engine — `dispositions.py` raises it on A-13 and X-5, where money moved that the engine cannot attribute. The agent has a deterministic signal for *"I cannot determine this"* rather than having to infer that judgment itself.

---

## 2. Per role: covered vs missing

| Role | Needs | Have | Missing |
|---|---|---|---|
| **Exception Investigator** | related claim / remittance / rebate / bank records; why it is open; cited evidence; missing info | All four tracks in `timeline`; `current.reason_codes`; `source` per event; `unresolved`; `INSUFFICIENT_DATA` | Retrieval **tools** wrapping the four surfaces |
| **Workflow Coordinator** | denial detail; routing identity; next action; mock work item; deterministic confirmation before closing | Denial reason + CARC detail in `REMITTANCE_CLAIM_LINE` facts; payer + ids in `identity`; `work_item` table exists (append-only, `from_verdict_id` FK) | `create_mock_work_item` **writer**; a closed vocabulary for `recommended_action`; a confirmation tool |
| **Ops / Portfolio Analyst** | aggregate answers; exceptions ranked by value, age, category | `age_days`, `absolute_variance_cents`, `reason_codes` on every queue row; `/api/overview` aggregates | A portfolio **tool** over the queue endpoint |

---

## 3. What still needed crafting — all of it now built

*Kept as written, because the predictions are worth grading. Every row below was true
when this was written and is false now.*

| # | Predicted | What actually happened |
|---|---|---|
| 1 | Tool layer: none | Nine tools. Five for the episode, two for the portfolio, one drill-down, one write |
| 2 | `create_mock_work_item`: no writer | Built, and gated harder than proposed — see row 7 |
| 3 | Eval set: none | Ten scenarios, replayed offline with no API key |
| 4 | Tool-call trace: none | The journal. One JSONL per run; trace, audit trail and replay fixture at once |
| 5 | Failure case: none | Two — a veto-triggered zero score, and a real D-6 crosswalk miss |
| 6 | `recommended_action` free text | Closed set of seven. The six proposed, plus `ABSTAIN`, because every enum needs an escape member |
| 7 | Confirmation tool | **Built, but not as predicted.** Not a tool the agent calls: the UI mints a digest over the exact human-approved draft and the write tool recomputes and `hmac.compare_digest`s it. The model cannot forge it, which is stronger than asking it to call something |

The one prediction that generalised further than its author expected: this document
scoped the tool surface to two roles. A third arrived, and needed no new *kind* of
access — just the same envelope over two read models that already existed.

## 3b. Original text of section 3

Ranked. The first three are the whole job.

| # | Item | State | Note |
|---|---|---|---|
| 1 | **Tool layer** | **None.** 8 HTTP endpoints exist, none tool-shaped | The assignment names the minimum: episode retrieval, TPA/rebate status, remittance/denial, bank match, `calculate_reconciliation(claim_id)`. All five read from data that already exists — this is wrapping, not building |
| 2 | **`create_mock_work_item`** | Table built, **0 rows, no writer** | The agent's *only* write capability. Schema already enforces the boundary: it references `verdict_id`, is append-only, has no close and no update |
| 3 | **Eval set** | **None** | ≥10 scenarios. Prefer tool-path assertions over free-text — deterministic to check, no LLM judging. `pairs.json` and the 372 reachable pairs make known-answer cases cheap to pick |
| 4 | **Tool-call trace** | None | Required deliverable: name, args, result per call |
| 5 | **Failure case + hardening note** | None | A D-6 crosswalk-miss episode is the natural choice — the data already contains deliberate ones |
| 6 | **`recommended_action` vocabulary** | Column is free text | A closed set (RESUBMIT / APPEAL / WRITE_OFF / ESCALATE / INVESTIGATE_CROSSWALK) keeps the agent's output assertable in the eval set |
| 7 | **Confirmation tool** | None | The assignment forbids closing or posting "without deterministic confirmation." Needs to be a tool the agent *calls*, never a judgment it makes |

---

*Three threads run through all of it. **Lineage** is what turns a citation into evidence — it is already wired from every computed number to its raw source row. **The boundary** is that every figure the agent surfaces comes from a tool, never from model arithmetic; the deterministic layer already computes all of them. And **the episode is the object** — one query returns everything, so the tool layer is a thin wrapper rather than an assembly job. The work remaining is items 1, 2 and 3; everything else is an afternoon.*
