---
module: agent_layer
tags: [hardening, client, coordinator, deepseek, measured]
problem_type: known-limitation
---

# `deepseek-chat` does not reliably comply with a forced `tool_choice`

## What this is

A production-hardening note for a failure mode discovered by **live testing**
against the real DeepSeek API while finishing the agent layer (task 4: driving the
real app with Playwright, and recording the eval set's live fixtures with
`scripts/agent_smoke.py --record`). Not a hypothetical: measured on 3 of 5 required
eval-set episodes, and on 2 of 2 interactive Playwright attempts against
`E-000006`/`E-000032` before a third attempt on a different episode finally
completed a round.

## The mechanism

`roles/coordinator.py._ProposerSession.__call__` gives the proposer up to
`_PROPOSER_MAX_ROUNDS = 3` rounds of `tool_choice="auto"` tool-calling before
falling back to `_emit_structured`, which **forces** `tool_choice` to
`emit_proposed_action` on models that support it (`deepseek-chat` does; see
`client.supports_forced_tool_choice`). On a forced call, if the response's tool
call doesn't match the forced name, `_coerce_sole_forced_call` accepts it anyway
**only if it is the response's one and only tool call** — an intentionally narrow
rule (a response with more than one call is ambiguous about which one the model
meant). Otherwise `_emit_structured` raises `SchemaError` immediately: **there is
no repair turn on the forced path.** (Contrast the *non-forced* path, used for
`deepseek-v4-pro`, which gets exactly one repair turn —
`_PROPOSER_REPAIR_MESSAGE` / `REPAIR_TRANSCRIPTION_TURN` — before it, too, gives
up.)

## What was measured

After spending its 3-round budget on real tool calls, `deepseek-chat` was forced
to call `emit_proposed_action` and, instead of complying, answered with:

| Episode | What it returned |
|---|---|
| `E-000032` (recorded fixture) | `get_bank_transactions`, `get_crosswalk` — neither ever offered |
| `E-000032` (Playwright, live) | `get_payer_appeal_routes`, `list_work_items` — neither ever offered |
| `E-000032` (Playwright, live retry) | `get_bank_transactions`, `search_bank_transactions` — neither ever offered |
| `E-000040` (recorded fixture) | `get_bank_transactions`, `search_records` ×3 — none ever offered |
| `E-000825` (recorded fixture) | A call to `emit_proposed_action` itself, but with the required `reasoning` field missing entirely |

Every one of these is a **hard failure**: `run_coordinator` raises `SchemaError`,
which propagates out of `run_until` uncaught (only `evaluate()` calls are wrapped
in `_evaluate_or_fail_closed`; a `propose()` failure is not). Over HTTP, this
becomes the `"status": "error"` SSE outcome — styled distinctly (red, "Run failed")
by the `/analyse/:episodeId` screen, never confused with `insufficient_data`.

Two of five recordings — `E-000006` and `E-000002` — did comply and produced a
real `Outcome`, both `capped` on the token budget rather than failing this way.
Both are consistent with a real capability limit: it is not that `deepseek-chat`
*never* complies with the forced call, but that it does so unreliably once the
conversation already contains several real tool results.

## Why this matters more than an ordinary flaky test

The evaluator role (`deepseek-v4-pro`, non-forced) has a repair turn precisely
because the design anticipated a model failing to emit structured output on the
first try. The proposer role's forced path was built assuming a forced call is
close to unconditional compliance on a model that supports it — measured here to
be false often enough (3 of 5 real recordings) that it changes how reliable
"decide next steps" is in practice.

## Hardening options, in order of how much they change

1. **Cheapest: give the forced path a repair turn too**, mirroring the non-forced
   path's `_PROPOSER_REPAIR_MESSAGE`. On the first forced-call failure, append a
   transcription-repair user turn ("call `emit_proposed_action` with what you
   already decided; change nothing") and force the call exactly once more before
   raising. This is the same shape of fix the non-forced path already has, applied
   symmetrically — a small, contained change to `_emit_structured`'s forced
   branch, not attempted here because it changes the harness's own retry
   contract and deserves its own test pass rather than a rushed one under this
   task's time budget.
2. **If failures cluster specifically after heavy tool use:** cap the *forced*
   call's context by summarizing or dropping the oldest tool results before the
   forced attempt, on the theory that a long tool-result-laden context is what is
   confusing the model into "helpfully" trying another tool instead of answering.
   Not verified here — plausible from the pattern (all five failures happened only
   *after* the 3-round tool budget was already spent) but not isolated from
   "forced tool choice is just unreliable on this model" as the more general
   explanation.
3. **If (1) is not enough in practice:** fall back to the non-forced path's
   coercion logic for the LAST forced attempt too — accept a single, unambiguous
   tool call under the wrong name (`_coerce_sole_forced_call` already does this;
   the failures observed here all had *multiple* invalid calls in one response, so
   this alone would not have saved any of the five measured failures, but it is
   cheap insurance against the single-invalid-call case).

None of these are implemented here — this note exists to make the limitation
visible and actionable, per this task's instruction to report what actually
happened rather than reshape the eval set until it looks clean. See
`tests/test_agents_evals.py::test_e000032_no_cash`,
`::test_e000040_insufficient_data`, and `::test_e000825_crosswalk_miss` for where
this is exercised as a `pytest.raises(SchemaError)`, and the Playwright walkthrough
section of the final report for the interactive reproduction.
