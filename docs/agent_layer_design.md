# The agent layer: design

*Grounded in eight parallel research passes over the harness-engineering, LLM-as-judge, abstention, structured-output and healthcare-RCM literature. Every number below is cited. Where sources conflict, the conflict is stated rather than smoothed over. This is the design; the decision queue at the end is what is still owed.*

Three roles, one harness. The Exception Investigator explains, the Portfolio Analyst aggregates, the Workflow Coordinator proposes an action and gates it behind an independent evaluator. The deterministic layer already computes every number any of them will quote.

---

## 1. Why the two-agent split is justified

The honest justification is narrow, and it is not "multi-agent performs better."

Cognition's [*Don't Build Multi-Agents*](https://cognition.com/blog/dont-build-multi-agents) attacks a specific failure: two agents each produce an artifact, each embeds implicit decisions the other never saw, and a merge step must reconcile artifacts built on incompatible premises. Every link in that chain is absent here. Our evaluator produces a score and a critique, not an artifact. Nothing merges. The proposer stays the sole author.

Their own follow-up, [*Multi-Agents: What's Actually Working*](https://cognition.com/blog/multi-agents-working), describes our shape almost exactly — "multi-agent systems work best today when writes stay single-threaded and the additional agents contribute intelligence rather than actions" — and their production review agent catches an average of 2 bugs per PR, 58% of them severe.

The real argument is Kambhampati's: **"significant performance collapse with self-critique and significant performance gains with sound external verification"** ([arXiv:2402.08115](https://arxiv.org/abs/2402.08115)). Collapse, not merely "no gain." Huang et al. measured it — GPT-4 on GSM8K fell 95.5% → 91.5% → 89.0% across intrinsic self-correction rounds ([arXiv:2310.01798](https://arxiv.org/abs/2310.01798)). Models are poor at *detecting* their own errors and fine at *fixing* flagged ones.

What rescues us is that our grounding documents are genuinely external. This is reference-guided grading, the one LLM-judge configuration with consistent research support — not a model asked whether it feels right.

**Do not cite Anthropic's 90.2% multi-agent result in support of this.** Their own data says token usage alone explains 80% of performance variance on that benchmark. That result is largely buying parallel inference on a breadth-first search problem. Ours is not one.

### The evaluator gets the same inputs and a fresh trace

The proposer's reasoning never reaches the evaluator. It receives the proposed action, the grounding documents, the evidence, and the rubric — nothing else.

This is Cognition's measured finding, and it contradicts their own earlier "share full traces" principle: the reviewer "gets to skip this extraneous context, only look at the diff, and re-discover any context it needs," and **"With a shorter context, the improved intelligence naturally leads to increased detection of nuanced issues."** Where the two conflict, the follow-up wins — it is a production result with measured bug yield against a heuristic asserted in a blog post.

**What the shipped default actually does, stated rather than implied.** `proposer_model` and `evaluator_model` both default to `deepseek-chat` — the *same model* — so the self-preference-bias mitigation this section argues for is off unless `RECON_AGENT_EVALUATOR_MODEL` is pointed elsewhere. The reason is measured, not casual: the thinking-model evaluator averaged 466s per call against the proposer's 4.1s and was 96% of every run's wall clock. What survives the change is the part doing the heavier lifting — the evaluator still gets a fresh trace, still never sees the proposer's reasoning, and Python still computes the score from a checklist. What is lost is model diversity, and this document should not keep claiming it by default.

---

## 2. The loop

```
propose → verify structurally → score against grounding docs → gate
```

Bounded three ways, not one:

- **confidence ≥ 80** → accept, surface the recommendation
- **any criterion NOT_ADDRESSED** → declare insufficient data immediately
- **no score improvement across two rounds** → stop and escalate
- **max iterations** → backstop. Five here; the shipped default is 3 (`RECON_AGENT_MAX_ITERATIONS`)

Five is well-chosen. Reflexion capped at 4–12, Self-Refine at 4, Huang et al. tested only 2; gains front-load hard and the tail is where self-bias does its damage.

The shipped default is **3**, and the floor is not arbitrary. `_stalled` needs three scored rounds to fire — two that failed to improve, plus an earlier one to have failed to improve on — so a ceiling of 2 makes `stalled` unreachable and leaves the loop with three of its four terminal states. That was briefly the shipped configuration, as a prototype simplification, and nothing failed: the outcome simply stopped being producible. A test now pins the relationship rather than leaving it to memory.

The no-improvement stop is not an optimisation. [Xu et al. (ACL 2024)](https://aclanthology.org/2024.acl-long.826/) found self-refinement "improves the fluency and understandability of model outputs" while it "further amplifies self-bias" — the loop can converge on something that reads better and scores higher while being no more correct, with confidence climbing the whole way. **Log the score trajectory.** Monotonic rise with no substantive change to the proposal is the signature, and it costs nothing to detect.

Budgets — iteration count, token spend, wall-clock — live in the orchestration code, never in the prompt.

### The hard cap is checked *before* the gate, not inside it

This ordering is not stylistic. CrewAI has an open bug ([#3847](https://github.com/crewAIInc/crewAI/issues/3847)) where `handle_max_iterations_exceeded` prepares a forced answer at the ceiling, but subsequent model calls silently overwrite it and the loop does not reliably stop — because the cap check is not structurally prior to the semantic check. The ceiling must win ties by construction, not by ordering luck.

```python
def run_until(propose, evaluate, *, threshold=80, max_iterations=5):
    history = []
    for i in range(max_iterations):          # hard ceiling FIRST, unconditional
        proposal = propose(history)
        verdict  = evaluate(proposal)        # separate call; proposer's trace not passed
        history.append((proposal, verdict))

        if verdict.not_addressed:
            return Outcome("insufficient_data", i, verdict, history)
        if verdict.score >= threshold:
            return Outcome("complete", i, verdict, history)
        if verdict == history[-2][1] if len(history) > 1 else False:
            return Outcome("stalled", i, verdict, history)
    return Outcome("capped", max_iterations, history[-1][1], history)
```

> **Correction, added after implementation.** The `stalled` line above —
> `if verdict == history[-2][1] ...` — is wrong, and is left here unedited because
> `src/recon/agents/rubric.py` cites it by line as the defect it corrects. It compares two
> whole `Verdict` objects, which carry free-text reasoning a model never reproduces byte for
> byte, so it is effectively `False` forever and `stalled` could never fire. The shipped
> version compares *scores*: `_stalled` needs three scored rounds and returns true when
> neither of the last two beats the best earlier round by more than `STALL_EPSILON` (1.0).
> That in turn makes the outcome a claim about configuration — an iteration ceiling of 2
> leaves it unreachable, which is why the shipped default is 3.

**Four distinct outcomes, never one boolean.** `complete`, `insufficient_data`, `stalled`, `capped` are different facts about the world and each needs a different response from the operator. Collapsing them loses the distinction between "we determined this cannot be resolved from the available documents" and "we ran out of budget" — the first is an answer, the second is a failure.

### Prior art: OpenHands' Goal Completion Loop

[OpenHands](https://docs.openhands.dev/sdk/guides/convo-goal) ships almost exactly this structure, and is the only surveyed system that separates propose, evaluate and gate into three distinct steps:

```
1. Agent runs and calls FinishAction
2. Judge LLM audits transcript → { score, complete, missing }
3. complete → stop · max_iterations → status="capped" · else → re-prompt with `missing`
```

The detail worth copying is `missing`. Their `GoalVerdict` is not a bare score — it names *what was absent*, and that string is what drives the next iteration's prompt. So the loop doesn't just retry, it retries **aimed at a stated gap**. Our `NOT_ADDRESSED` criteria give us the same list for free; feed them forward rather than re-proposing blind.

### An escape hatch for the proposer

Goose's Ralph loop lets the agent write a `RALPH-BLOCKED.md` sentinel to force early exit — an "I'm stuck" signal distinct from "I'm finished." Worth having: the proposer cannot end the loop successfully, but it *should* be able to declare itself blocked, because burning four more iterations on an episode whose evidence does not exist is waste we can avoid. That signal is advisory — the evaluator still scores the final state, and the outcome is recorded as `insufficient_data` with the proposer's stated reason attached.

### Why the model never evaluates the gate

Across every system surveyed, the continuation condition is decided by **code** — Pi's injected `shouldStopAfterTurn(lastTurn)` callback, LangGraph's conditional-edge function, AutoGen's composable termination objects, Claude Code's Stop hook. The shallow variants (Codex, OpenAI Agents SDK, CrewAI's early exit) check output *shape* — "did the model stop emitting tool calls" — which tells you the model thinks it is done, not that it is.

The systems with no guard at all are the cautionary ones. dsh states plainly it has **"no built-in turn budget."** OpenCode's core has none either, and carries documented real infinite-loop bugs ([#7187](https://github.com/anomalyco/opencode/issues/7187), #45442) plus a third-party `opencode-anti-loop` plugin whose entire job is detecting repeated identical calls and forcibly reverting. Even Claude Code's `stop_hook_active` is a *convention* hook authors are asked to honour, not a cap the harness enforces — a badly written hook can block forever.

So: the harness owns the `while`, the condition is a plain function over accumulated state, and the ceiling is checked before the condition runs.

---

## 3. The evaluator never emits a score

This is the load-bearing decision in the whole design.

Verbalised confidence is the weakest signal in every study that measured it. The ranking is consistent: **logprobs > sampling-consistency > verbalised self-rating** ([arXiv:2412.14737](https://arxiv.org/pdf/2412.14737)). [*Wired for Overconfidence*](https://arxiv.org/html/2604.01457v2) locates a "Confidence Mover Circuit" and ties it to RLHF — structural, not a prompting artefact.

> Asking for confidence is asking the model to grade its own homework; asking for a quote is asking it to show the homework.

So the evaluator returns **per-criterion findings**, and Python computes the number. Copying OpenAI's [HealthBench](https://openai.com/index/healthbench/) structurally: each criterion carries a point weight, is graded independently, met earns full points and unmet earns zero, and the score is `earned / possible`. 48,562 criteria across that benchmark — the pattern scales.

Each criterion has **three** legal verdicts, not two:

| Verdict | Meaning | Loop response |
|---|---|---|
| `SUPPORTED` | cited span backs it | counts toward score |
| `CONTRADICTED` | cited span refutes it | lowers score, **keep iterating** — more evidence could change it |
| `NOT_ADDRESSED` | documents are silent | **declare insufficient data** — more iterations cannot manufacture evidence that is not there |

Collapsing the last two into one low number is precisely what makes models guess instead of abstain. They are different failure modes requiring opposite responses.

Scorers compose as a weighted list, borrowed from Prime Intellect's `Rubric`: `reward = Σ wᵢ · fᵢ(...)`, where the LLM judge sits in the same list as a regex check with its own weight and no special status. That keeps us honest about how much the judge actually controls the outcome. Their `weight=0.0` convention — track as a metric, do not gate on it — is how a new criterion ships while the rubric is still drifting.

---

## 4. Schemas

**Reasoning first. Always.** Worth ~60pp on hard tasks; `pydantic.BaseModel` preserves declaration order into `model_json_schema()`.

```python
class ProposedAction(BaseModel):
    reasoning: str                      # FIRST, non-negotiable
    evidence: list[EvidenceSpan]        # verbatim quote + offsets + source id
    grounding_clause_id: str | None
    action: ActionEnum                  # small closed set, includes ABSTAIN
    required_artifacts: list[str]
```

```python
class CriterionFinding(BaseModel):
    criterion_id: str
    reasoning: str                      # before the verdict
    cited_span: Span | None
    verdict: Literal["SUPPORTED", "CONTRADICTED", "NOT_ADDRESSED"]

class Verdict(BaseModel):
    per_criterion: list[CriterionFinding]
    overall_reasoning: str
    # no score field — Python computes it
```

Three rules that fall out of the evidence:

**Every enum needs an escape member.** Without one, constrained decoding *guarantees* a confident label on every input, including inputs that support none. And watch for **enum collapse**: when the decoder masks the model's preferred token it falls back to the most schema-legal common option — one measurement showed a `Literal["low","normal","high","urgent"]` field returning `"normal"` ~11% more often under constraint even when evidence supported `"urgent"`. 100% schema-valid, wrong.

**Optional-in-spirit means `T | None`, not merely omittable.** If a field is required and the source lacks it, the model will hallucinate a value — there is no legal way for it to decline.

**Flat, depth ≤ 3, avoid `oneOf`.** Union support is the weakest area across every provider. If actions need heterogeneous payloads, N strict tools is a better-supported encoding than one `anyOf`.

---

## 5. Tools

Nine, which is comfortably inside the safe zone — measured degradation starts around 15–20 and gets severe past 30–40. (Seven when this was written, for the Investigator and the Coordinator. The Portfolio Analyst added `get_portfolio_overview` and `get_exception_queue`; `spec_tools.md`'s decision D-T4 had excluded a portfolio tool explicitly *because only two roles were being built*, and that reason went away when the third one was.) Our only real risk at this count is *semantic overlap* between tools, so the description effort goes into sharpening boundaries, not managing count.

The concise/detailed pattern Anthropic recommends is **already built**: `essential` is the cheap default, `facts` the drill-down, `get_raw_record(raw_id)` the third level. Concise responses measured at roughly ⅓ the tokens of detailed ones.

Every tool returns one envelope:

```python
{"status": "ok" | "error",
 "data": ...,
 "error_type": "not_found" | "invalid_input" | "ambiguous" | "db_error",
 "message": "...",           # carries the next step
 "retryable": bool}
```

Zero matching episodes is **success with an empty result**, not an error. An unknown episode id *is* an error — and the message carries the recovery: *"episode_id E-000821 not found — did you mean E-000812 or E-000832?"* beats "no rows returned." Never leak `sqlite3.OperationalError`; translate to domain language.

Duplicate-call detection: same tool, same args, inside a window → return `duplicate_call_blocked` rather than re-executing.

**Annotate the read tools too.** MCP's default posture for an *unannotated* tool is the most pessimistic available — assume destructive, non-idempotent, open-world — so leaving the six read tools unmarked makes them *more* gated, not less. And annotations are hints, not a boundary: the spec says clients "MUST consider tool annotations to be untrusted." Enforcement stays in our code.

The write tool takes `dry_run: bool` as a parameter rather than becoming an eighth tool, and is idempotent on `(episode_id, verdict_id, action)` so a retry after a dropped response cannot create a duplicate work item.

---

## 6. What gets logged

DeepSeek Harness's invariant, worth stealing wholesale: **"model-visible means logged."** Anything that reached a model request must be reconstructable from the log — they assert it at dispatch time, byte-matching outgoing messages against the derived projection.

Append-only JSONL, one event per step, `derive_state(log)` to replay. Per iteration: the proposed action, which grounding documents were in context, **every per-criterion verdict with its cited span and point value** (not just the aggregate), the computed score, the threshold comparison, model ids for proposer and evaluator separately — so independence can be verified after the fact rather than assumed — token counts, latency, and **why the loop continued or stopped**.

That single artifact is simultaneously the required tool-call trace, the audit trail, and the replay fixture.

**Keyless replay testing** follows from it: record real traces once, derive a mock model from the fixture, and run the eval set in CI with no API key. Three modes — `replay` / `record` / `refresh`. This is how the eval set stays runnable by a reviewer who has no credentials.

Fail closed with a typed reason. If the evaluator cannot run — database unreachable, judge API down — say so and stop. Never default to allow.

---

## 7. The human gate, and why a checkpoint is not enough

Cigna's PXDX had a human in the loop. It is now being litigated as unlawful rubber-stamping: doctors batch-denied without opening files — *"We literally click and submit. It takes all of 10 seconds to do 50 at a time"* — 300,000+ claims in two months averaging **1.2 seconds each** ([ProPublica](https://www.propublica.org/article/cigna-pxdx-medical-health-insurance-rejection-claims)).

So the design rule is not "add an approval step." It is:

- The reviewer sees the **underlying evidence**, not just the conclusion. Our analysis page does this structurally — the timeline sits beside the recommendation, so *Add to-do* cannot be clicked without the evidence on screen.
- **The recommendation is editable before it is accepted.** This is the difference between a review and a rubber stamp: a reviewer who can only approve or reject is being asked to ratify, while one who can correct the action, strike an artifact, or rewrite the rationale is actually exercising judgment. It also gives us the highest-value training signal in the system — *what the human changed* is a far sharper error signal than whether they clicked yes, and unlike appeal outcomes it arrives immediately. Log the diff between proposed and accepted.
- **Override rate is a first-class metric**, not a defect. A near-100% agreement rate is a signal to audit the gate.
- **Alarm on approval latency.** A recommendation approved faster than the exception could plausibly be read has reconstructed PXDX with extra steps.
- Never measure the reviewer on throughput or agreement-with-model.

And the trap in our own success metrics. UnitedHealth's nH Predict: ~90% of appealed denials were reversed, but only ~0.2% were ever appealed. **Low override rates can be evidence that the friction to challenge is high, not that the system is right.** Any signal built on "was this eventually vindicated" is contestation-biased and systematically misses the cases where we were wrong and nobody noticed. A blind audit of a random sample is the second signal that catches it.

One line in the design note has to survive an adversarial read of our actual logs: *"we don't use AI to make the decision"* is a load-bearing legal claim, not a README sentence. UnitedHealth is being compelled to prove it in discovery now.

Worth noting the architecture has independent industry backing: Candid Health, a well-funded RCM company, deliberately built a **deterministic rules engine** for the financial-correctness path and uses LLMs only to suggest new rules. Same split as ours.

---

## 8. Security

**Treat payer and clearinghouse free text as adversarial.** `stc12_free_form` arrives from outside, is attacker-controllable in production, and flows into agent context. [Nature Communications (2025)](https://www.nature.com/articles/s41467-025-64062-1) demonstrates prompt injection against medical LLMs at up to 99.7% attack success, explicitly naming "insurance claims manipulation." No public RCM incident yet — build as though there will be one. Delimit it, mark it untrusted, and never let it alone trigger a tool call or state change.

**PHI.** Canonical bodies carry `cardholder_id`, prescriber and rendering-provider NPIs, and patient-pay amounts, and the API returns them unredacted. The data is synthetic so there is no live exposure, but the design has no stated position, which is the actual gap. Production needs: a BAA before any PHI reaches a provider (available on Anthropic and OpenAI enterprise/API tiers, **not** consumer tiers), and HIPAA's minimum-necessary standard applied to prompts as to any other disclosure.

**Scope.** Post-claim reconciliation is not a medical-necessity determination, so we sit outside the strictest utilization-review statutes (CA SB 1120 and its successors, which converge on "AI cannot be the sole basis for a denial"). But a recommendation amounting to "write off" or "do not pursue" is adjacent enough that the same discipline — individual-case grounding, human sign-off, reconstructable audit trail — is the defensible posture regardless of exact statutory reach.

---

## 8.5 The programmatic framework: none, and that is the point

**Custom harness, plain Python, OpenAI-compatible wire client.** Roughly 150 lines of loop. The reasoning is not minimalism for its own sake — it is that every alternative costs us something we need.

Four sources were checked for an off-the-shelf loop with an evaluator: Pi, DeepSeek Harness, Prime Intellect's `verifiers`, PyHarness. **None ships one.** dsh's own agent-loop README has no reference to evaluators, critics, judges, scoring or confidence. So the eval loop is ours to write regardless of what sits underneath — the framework choice only decides the plumbing around a thing we are building either way.

Given that, the question becomes: what does each option *cost*?

| Option | Cost |
|---|---|
| Pi / DeepSeek Harness | TypeScript sidecar, RPC to Python for every tool call. Both are coding-agent harnesses — filesystem tools, terminal UIs. dsh warns "nothing about it is stable yet" |
| Pydantic AI | A 0.x dependency at the centre of the graded component, abstracting the tool boundary the assignment is specifically testing |
| Anthropic SDK as the framework | Locks the provider. Loses DeepSeek, GLM, Qwen, Kimi — the cheap tier |
| **Custom loop + OpenAI-compatible client** | We write ~150 lines |

### Why the wire format decides the provider question

The `openai` SDK is the lingua franca, not a vendor commitment. DeepSeek, GLM, Qwen, Kimi and OpenRouter all expose OpenAI-compatible endpoints, so the provider is a `base_url` and a model string:

```python
class LLMClient(Protocol):
    def complete(self, messages, tools, schema) -> Response: ...

OpenAICompatClient(base_url="https://api.deepseek.com", model="deepseek-chat")
OpenAICompatClient(base_url="https://openrouter.ai/api/v1", model="z-ai/glm-4.6")
ReplayClient(fixture="traces/E-000022.jsonl")     # no key, CI
```

That is one seam. It means the proposer and evaluator can run on **different providers** — the documented mitigation for self-preference bias — without a second integration. It means the cheapest capable model wins on price rather than on lock-in. And `ReplayClient` is what makes the eval set runnable in CI by a reviewer with no credentials at all.

### Two things we give up, and how each is covered

**Anthropic's Citations API** returns `cited_text` with character offsets extracted by the parser rather than generated by the model — a structural guarantee against fabricated citations. Going provider-agnostic loses it.

It costs us nothing, because the research says not to trust a model's self-reported citation anyway. The mitigation is the same either way: require a **verbatim** span, then verify it with `str.find()` against the source. That converts citation-hallucination from an ML problem into a substring problem, and it works identically on every provider. Given 700+ court cases involving LLM-fabricated citations that looked perfect, "the model said it cited something" was never going to be sufficient.

**Native strict JSON schema** support varies across OpenAI-compatible providers. The portable encoding is **forced tool-use as structured output** — it works on more models and providers than native structured outputs do, and it sidesteps the weak `oneOf` support that every implementation shares. Where a provider does support strict schemas, we use them; where it does not, the forced-tool path is the fallback and the Pydantic validation layer is identical either way.

### The shape

```
roles/           investigator · analyst · coordinator
  ↓
harness.py       the loop: propose → verify → score → gate
  ↓
rubric.py        weighted scorer list; LLM judge is one entry among deterministic ones
  ↓
tools.py         9 tools over the existing repository layer (7 when written;
                 the Portfolio Analyst's two read models were excluded only
                 because the third role was not being built, and that reason
                 went away when it was)
  ↓
client.py        Protocol + OpenAICompat + Replay
  ↓
journal.py       append-only JSONL; derive_state(log)
```

Every layer is replaceable in isolation and none of them is a dependency we do not control.

---

## 8.6 The surface

The dashboard does not change. Everything below is a separate screen, reached deliberately, because the agent layer is the acceleration path for open and exception cases — not a replacement for the housekeeping view that already works.

### Entry

The episode panel on the dashboard gains one control: **Inspect using AI**. It opens a new tab at `/analyse/{episode_id}`. Nothing about the dashboard's behaviour changes; the button is the only addition.

### The analysis screen

Two columns, same split as the dashboard.

**Left — the same episode timeline**, reusing the existing component. The reviewer must be able to see the evidence while reading the recommendation. This is structural, not cosmetic: it is what stops the approval being a rubber stamp (§7).

**Right — two actions, run in order.**

**`Explain`** runs the Exception Investigator. Single pass with tool calls. Output is prose with inline citations, every factual claim carrying a pointer back to a timeline event or source record. This is the one place the agent layer writes English — and it is the agent's job, which is precisely why the deterministic layer does not do it (§3 of the state-facts learning).

**`Decide next steps`** runs the Workflow Coordinator harness. The loop streams as it runs — iteration number, each tool call, each checklist item as it is ticked, the score, the gate decision. A spinner would hide the one thing worth showing: a reviewer who watches the loop reason is a reviewer who can judge whether to trust it.

The result renders as a proposed work item, and it is **editable** — action, artifacts, rationale. Then **`Add to-do`** commits it.

That button is the human gate. It is the only write path in the entire agent layer.

### The to-do list

Its own section, deliberately not prominent on the dashboard. The dashboard answers "what is the state of the book"; the to-do list answers "what am I doing about it". Different questions, different screens.

A work item carries **what is needed to act, and nothing else**: the action, a short rationale, the concrete artifacts required (form numbers, document names, identifiers to quote), and a link back to the episode. It does not restate the timeline — anyone who wants the story clicks through to the episode that has it.

### What the four outcomes look like

The loop has four terminal states (§2) and each renders differently, because collapsing them into "it worked / it didn't" destroys the distinction that matters most:

| Outcome | What the reviewer sees |
|---|---|
| `complete` | The proposed work item, editable, with the checklist shown |
| `insufficient_data` | **What specifically is missing**, named — the `NOT_ADDRESSED` criteria. Not an apology |
| `stalled` | The best candidate, plus the fact that it stopped improving |
| `capped` | The best candidate, plus the budget note |

`insufficient_data` is a **successful outcome**, not a failure, and the UI should not style it as an error. The assignment requires the agent to say when the data is insufficient; a system that only ever renders confident recommendations has failed that requirement silently.

---

## 9. Deliberately not doing

- **A framework.** Pi, DeepSeek Harness, Claude Code, Codex, Goose are coding-agent harnesses — TypeScript, filesystem tools, terminal UIs. Adopting one means a TS sidecar and RPC to Python for every tool call, for a loop that is ~150 lines. None of them ships an evaluator anyway: four sources checked, four without any scoring abstraction.
- **Cordis.** Revertible effects and coeffect-driven reactivation solve hot-swappable end-user plugins. We are a batch service. Importing an effect-system runtime here is the textbook over-engineering trap.
- **Prime Intellect's `verifiers` v1.** The v0 `Rubric(funcs=..., weights=...)` API is the right complexity level; v1's TOML config and `Trace`/`priority` machinery solves harness-portability problems we do not have.
- **A judge panel.** Nine frontier judges carry roughly two independent votes' worth of signal because their errors correlate. Diversify model families, not model count.

---

## 10. What we owe, honestly

**The 80 is uncalibrated.** Deriving it from a weighted checklist makes it *auditable* — you can see why it was 62 — but not *calibrated*. Real calibration needs 100–200 labelled examples per failure mode, balanced 30–50 pass / 30–50 fail, split train/dev/frozen-test, with the judge measured as a binary classifier reporting **TPR and TNR separately** (aggregate agreement hides a judge blind to the failure you care about: on a 95%-pass set, a judge that always says "pass" scores 95% and catches nothing). Hamel Husain reached >90% on both in three iterations on a production judge. That is a real cost and it should be stated as a gap rather than presented as done.

**Expect criteria drift.** You cannot finish the rubric before seeing outputs, because grading outputs is how you discover what the rubric should say ([Shankar et al.](https://arxiv.org/abs/2404.12272)). Budget 2–3 rubric revisions.

**Reasoning models abstain worse.** [AbstentionBench](https://arxiv.org/abs/2506.09038) (20 datasets, 35k+ queries) found reasoning fine-tuning costs an average **24%** of abstention rate, and scaling model size barely helps. Since "say when the data is insufficient" is a graded requirement, extended thinking on the proposer is a risk, not a free upgrade.

**Model capacity interacts with structure.** Haiku on MATH-Hard dropped 36.2pp under JSON output where Sonnet was neutral (−0.6pp); splitting into free reasoning then cheap extraction recovered 80–87%. So a cheap evaluator is not free — if we run the judge on a small model, either give it a two-call split or expect to pay on hard judgments.

**Judges disagree most on exactly the cases we care about.** A controlled study found LLM-as-judge groundedness saturates near 1.0 (too lenient), DeepEval reads too strict, RAGAS lands between — specifically on abstention cases. We cannot use one judge to verify our own abstention behaviour is correct.

---

*Three threads run through all of it. **The deterministic layer owns every number** — the agent quotes, never computes, and Candid Health independently arrived at the same split. **Verification must be external and structural** — grounding documents the evaluator recomputes from each round, citations verified by substring rather than trusted, a score derived in Python rather than volunteered by a model. And **the honest failure path is a first-class output**, not an apology the model has to improvise: `NOT_ADDRESSED` is a verdict, `ABSTAIN` is an action, and `missing_evidence` is a required field.*
