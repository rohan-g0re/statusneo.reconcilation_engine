# Agent layer: the build plan

*The design is settled in `docs/agent_layer_design.md`. This is the operating plan that turns it into code — what gets built, in what order, and what has to be true before each part is committed.*

Every decision below is already ratified. Where the design left a parameter open, the choice is stated here with its provenance.

---

## 0. Parameters closed for this build

| Parameter | Value | Provenance |
|---|---|---|
| Framework | None. Custom harness, plain Python, OpenAI-compatible wire client | User decided (graph: *Agent layer*) |
| Provider | DeepSeek, `https://api.deepseek.com`, `deepseek-chat` | User supplied the key this session |
| Key handling | `RECON_AGENT_API_KEY` env var, loaded from a git-ignored `.env`. Never committed | Agent default; §3.5 of `remaining_work.md` |
| Roles built | Exception Investigator + Workflow Coordinator | User decided (graph: *Proposer and Evaluator*) |
| Surface | React screen at `/analyse/{episode_id}` | Design §8.6 |
| Eval grading | Tool-path assertions primary, structural answer checks secondary, no LLM judge | `START_HERE.md` §7 |
| `recommended_action` | Closed set: `RESUBMIT`, `APPEAL`, `WRITE_OFF`, `ESCALATE`, `INVESTIGATE_CROSSWALK`, `AWAIT_PAYER`, `ABSTAIN` | Proposed in `remaining_work.md` D8, plus `ABSTAIN` because §4 requires every enum to have an escape member |
| Security scope | Minimal hardening plus document — untrusted-text delimiting, no key in logs | `remaining_work.md` D6 option 2 |

---

## 1. The shape

```
src/recon/agents/
  config.py      AgentSettings — base_url, model ids, key, budgets. Frozen dataclass, env-read
  client.py      LLMClient Protocol · OpenAICompatClient · ReplayClient · RecordingClient
  journal.py     append-only JSONL, one event per step · derive_state(log)
  envelope.py    the one tool-result envelope, and the error vocabulary
  schemas.py     ProposedAction · CriterionFinding · EvaluatorVerdict · WorkItemDraft
  grounding.py   grounding clauses derived from docs/knowledge_graph.jsonl
  tools.py       7 tools over the existing repository layer, with JSON schemas
  rubric.py      weighted scorer list; the LLM judge is one entry among deterministic ones
  harness.py     the loop: propose → verify → score → gate. Ceiling checked first
  roles/
    investigator.py   Exception Investigator — single pass, tool calls, cited prose
    coordinator.py    Workflow Coordinator — Proposer + Evaluator over the harness
  prompts/            system prompts, one file per role, versioned with the journal
  api.py         FastAPI router: explain, decide (SSE), commit work item, list work items
```

Every layer is replaceable in isolation. `client.py` is the only file that knows a network exists; `tools.py` is the only file that touches the database.

---

## 2. Waves, and the gate on each

A wave is committed only when its gate is green. The gate is not "it runs" — it is the listed assertion.

| Wave | Builds | Gate |
|---|---|---|
| **A** Foundations | `config`, `client`, `journal`, `envelope`, `schemas` | Round-trip: a recorded DeepSeek call replays byte-identically through `ReplayClient` with no key set. `derive_state` reconstructs the run from the log alone |
| **B** Grounding + tools | `grounding`, `tools` | Every grounding clause resolves to a live graph entity. All 7 tools return the envelope on both the happy and the error path. No tool computes a number |
| **C** Rubric + harness | `rubric`, `harness` | All four outcomes — `complete`, `insufficient_data`, `stalled`, `capped` — are produced by a test with a stub client. The ceiling wins a tie against the threshold |
| **D** Roles | `roles/*`, `prompts/*` | Both roles run end to end against live DeepSeek. Every cited span verifies by `str.find()` against its source |
| **E** API | `api.py`, wired into `create_app` | Endpoints answer over HTTP. The decide loop streams. The write path is idempotent on `(episode_id, verdict_id, action)` |
| **F** Front end | `Timeline` extraction, `/analyse/:id`, *Inspect using AI* | Playwright drives the full journey: dashboard → analyse → explain → decide → edit → add to-do |
| **G** Evals | eval set, traces, failure case | ≥10 scenarios pass by tool-path assertion, offline, with no API key |

---

## 3. The rules this build must not break

These are the ones a reviewer will check, and each is enforced by a test rather than by care.

**No agent ever produces a number.** Every figure in agent output must be traceable to a tool result. Enforced structurally: a test asserts the prompt files instruct it, and the eval set asserts quoted figures match the dossier exactly.

**The evaluator never sees the proposer's reasoning.** Enforced by construction — the evaluator is built a fresh message list from the proposal, the grounding clauses and the rubric. A test asserts the proposer's `reasoning` string never appears in any evaluator request body.

**The ceiling is checked before the gate.** A test drives a client that always scores above threshold and asserts the loop still stops at `max_iterations`.

**Citations are verified, not trusted.** Every `EvidenceSpan.quote` is checked with `str.find()` against the named source. A quote that does not appear is a structural failure of that proposal, not a low score.

**Untrusted text is delimited.** Any free-text field arriving from a feed is wrapped and marked before it enters a prompt, and never alone triggers a tool call.

**One write path.** `create_mock_work_item` is the only tool that mutates. It is idempotent, and the `work_item` table has no update and no close.

---

## 4. Testing, and who does it

Three independent loops, deliberately not the same agent:

- **The builder** (this session) writes code and runs `pytest`.
- **An independent reviewer** reads the diff cold against the design document and the assignment brief.
- **Browser and trace agents** drive the running app with Playwright and read the journal and the API traces, checking what the system *did*, not what it claims.

Backend evidence is the journal (`data/agent_runs/*.jsonl`) plus the HTTP traces. Front-end evidence is Playwright snapshots and console logs. A wave is not done because it compiles; it is done because a trace shows it behaving.
