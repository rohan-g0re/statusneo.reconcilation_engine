"""The generic propose -> evaluate -> score -> gate loop (design S:2; this module's own
spec is `.agents/specs/spec_grounding_rubric.md` S:5-6, stated verbatim in this coder's
brief as "this IS the harness spec").

Role-agnostic on purpose. This module knows nothing about DeepSeek, forced tool-use,
system prompts, or which model is "the proposer." It is handed two callables --
`propose` and `evaluate` -- and a mutable :class:`HarnessContext` carrying one episode's
dossier, its grounding clauses, and the tool results accumulated so far, and it runs the
loop `rubric.py`'s own module docstring hands off to it: "Outcome, THRESHOLD and the
stall/self-bias helpers live here [rubric.py], not in harness.py ... this module has no
dependency on the harness loop itself, only on the shapes the loop will produce." Every
criterion, every scorer, the score formula, the four `Outcome` classmethods and the
corrected stall condition already exist in `rubric.py` / `scorers.py` (Coder 3's files);
this module's only job is the control flow that calls them in the right order and
journals what happened, plus the fail-closed and budget behaviour the design assigns to
"the orchestration code" rather than to any prompt.

`roles/coordinator.py` supplies the two callables for the real Workflow Coordinator by
wrapping a proposer session and an evaluator call around an injected `LLMClient`.
`tests/test_agents_harness.py` supplies plain Python stand-ins with no `LLMClient` at
all -- `run_until` only ever sees `Propose`/`Evaluate`, so a unit test of the loop does
not need a model, a client, or a network to prove any of its four outcomes.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from recon.agents.grounding import Clause
from recon.agents.journal import Journal
from recon.agents.rubric import (
    CRITERIA,
    MAX_ITERATIONS,
    THRESHOLD,
    EvaluatorUnavailable,
    Outcome,
    Round,
    _stalled,
    build_critique,
    merge_findings,
    run_judge_criteria,
    run_tracked_criteria,
    score,
)
from recon.agents.schemas import EvaluatorVerdict, ProposedAction
from recon.agents.scorers import RecordedToolResult, ScoringInput, run_deterministic_criteria

__all__ = [
    "HarnessContext",
    "RunBudgets",
    "Propose",
    "Evaluate",
    "run_until",
]


@dataclass(slots=True)
class HarnessContext:
    """Everything one run of the loop needs that is not the model calls themselves.

    `dossier` and `clauses` are fixed for the run -- one episode, one cursor, one
    deterministic clause selection (`grounding.select_clauses` is pure and is called
    once, before the loop starts, by whoever builds this object -- `roles/coordinator.py`
    for the real loop, a hand-built dict for a test). `tool_results` accumulates:
    `propose` is responsible for appending to it as its own tool-calling rounds run, so
    a figure fetched in iteration 1 is still sourced when the number scorer runs in
    iteration 4 (spec_grounding_rubric.md S:4.2, "accumulated ... a figure fetched in
    iteration 1 is still sourced in iteration 4"). The harness only ever *reads* this
    list when it builds a `ScoringInput`; it never calls a tool itself -- `tools.py`
    stays the only module in the agent layer that touches the database.
    """

    dossier: dict[str, Any]
    clauses: tuple[Clause, ...]
    tool_results: list[RecordedToolResult] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class RunBudgets:
    """Every number a run can be stopped by. All of them live here, none of them in a
    prompt (design S:2: "Budgets -- iteration count, token spend, wall-clock -- live in
    the orchestration code, never in the prompt.").

    `wall_clock_budget_s` has no counterpart in `AgentSettings` (Coder 1's file): that
    dataclass budgets `request_timeout_s` per HTTP call and `max_tool_rounds` per
    tool-calling pass, but nothing for the propose/evaluate loop's own wall clock.
    Rather than invent a config field nobody asked for, this stays optional and unset by
    default -- `wall_ms` is always *recorded* on `run_finished` (design S:6, "model ids
    ... latency"), it is only *enforced* as a stop condition when a caller opts in.
    """

    max_iterations: int = MAX_ITERATIONS
    threshold: float = THRESHOLD
    token_budget: int = 0  # 0 disables, matching AgentSettings.token_budget's own convention
    wall_clock_budget_s: float | None = None


#: `critique` is `None` on the first call and the previous round's feed-forward string
#: (rubric.build_critique's output, verbatim) on every call after. `iteration` is the
#: 0-based round index, threaded through so a role can tag its own `llm_request`/
#: `llm_response` journal events with the same number the harness uses for `proposal`/
#: `evaluation`/`score`/`gate` -- there is no other channel for a `Propose`/`Evaluate`
#: closure to learn which round it is in.
Propose = Callable[[HarnessContext, "str | None", int], ProposedAction]
Evaluate = Callable[[HarnessContext, ProposedAction, int], EvaluatorVerdict]


def run_until(
    propose: Propose,
    evaluate: Evaluate,
    ctx: HarnessContext,
    *,
    journal: Journal,
    budgets: RunBudgets = RunBudgets(),
    monotonic: Callable[[], float] = time.monotonic,
    proposer_model: str | None = None,
    evaluator_model: str | None = None,
) -> Outcome:
    """propose -> verify structurally -> score against grounding docs -> gate (design S:2).

    Four outcomes, imported from `rubric.py` and never redefined here: `complete`,
    `insufficient_data`, `stalled`, `capped`.

    **The ceiling is structurally first.** `range(budgets.max_iterations)` is the loop
    bound itself, not a check inside the body, so it wins ties by construction rather
    than by ordering luck -- this is CrewAI #3847: `handle_max_iterations_exceeded`
    prepares a forced answer at the ceiling, but a subsequent model call silently
    overwrites it because the cap check was not structurally prior to the semantic one
    (docs/agent_layer_design.md S:2, "The hard cap is checked before the gate, not
    inside it"). `tests/test_agents_harness.py` drives a stub evaluator that reports
    every judge criterion SUPPORTED every round -- "always scores above threshold" from
    its own, naive point of view -- while a permanent veto violation on the
    deterministic side keeps the real, Python-computed score at 0 forever, and asserts
    the loop still stops at `budgets.max_iterations` rather than completing early on the
    evaluator's say-so.

    **The evaluator never receives the proposer's reasoning.** `evaluate` is called
    with only `(ctx, proposal, iteration)` -- never `history`, never the critique that
    was fed to `propose` -- so there is no channel through which a previous round's
    prose, let alone the current round's `proposal.reasoning`, could reach it from this
    function. The actual exclusion of `proposal.reasoning` from the wire request is
    enforced one layer down, in whichever `Evaluate` closure builds the request body
    (`roles/coordinator.py`); this function's contribution is not handing that closure
    anything it could leak in the first place.

    **Below threshold, feed forward the evaluator's REASON** (S:6.1/6.2) via
    `rubric.build_critique`, which reads only `findings` and never accepts a proposal --
    so it is structurally incapable of forwarding `proposal.reasoning` even by mistake.
    **Above threshold, the evaluator's output is discarded** from the *work item*
    (`roles/coordinator.py` builds a `WorkItemDraft` from `Outcome.proposal` alone) --
    it is not discarded from the journal or the `Outcome.findings` this function
    returns, which is what makes the checklist visible to a human reviewer.

    **Any NOT_ADDRESSED criterion (weight > 0, no veto contradicted) -> `insufficient_data`
    immediately.** This is a successful outcome -- `rubric.Outcome.insufficient_data`'s own
    docstring says so -- never surfaced as a failure.

    **The proposer's BLOCKED escape hatch is advisory only** (design S:2, "An escape
    hatch for the proposer"): the evaluator still scores the final state before the run
    is recorded as `insufficient_data` with the proposer's stated reason attached. Note
    this checks `proposal.blocked` (the real `schemas.ProposedAction` field), not the
    design doc's own pseudocode line `if proposal.blocked_reason:` (S:5.2) -- the real
    schema (`spec_prompts_roles.md` S:B.3) validates `blocked_reason is not None` iff
    `blocked is True`, so `blocked` is the correct field to branch on and the design
    doc's line is read as shorthand for it, not followed literally.

    **Fail closed with a typed reason.** If `evaluate` cannot run -- the judge API is
    down, a structural validation failed, a repair turn was exhausted -- this function
    never manufactures a degraded `Outcome`. It raises `rubric.EvaluatorUnavailable`,
    re-raising the coordinator's own typed failure unchanged or wrapping anything else
    that escaped `evaluate` into the same typed exception, so a caller only ever has one
    exception type to catch for "the evaluator did not run" (design S:6, "Never default
    to allow").

    **Token and wall-clock budgets live here**, checked once per completed round --
    never mid-round, since tokens already spent cannot be un-spent. Both stop the loop
    the same way the iteration ceiling does: `Outcome.capped(history)`. Note
    `rubric.Outcome.capped`'s own auto-generated `budget_note` always cites
    `rubric.MAX_ITERATIONS` (a module constant, not `budgets.max_iterations`) and never
    mentions tokens or wall-clock at all -- that classmethod is Coder 3's file, frozen,
    and not parameterised for a non-iteration cause. The *real* reason a token/wall-clock
    cap fired is recorded faithfully in this round's `gate` journal event
    (`decision="capped", budget_note=...`) even though `Outcome.budget_note` itself will
    read as an iteration-budget message in that case; see this coder's report.

    **The score trajectory is journalled** on `run_finished` (`score_trajectory`,
    `self_bias_suspected`) in addition to one `score` event per round -- both are already
    computed by `rubric.Outcome._base` (Xu et al., ACL 2024: self-refinement can raise a
    score while nothing gets more correct; monotonic rise with a frozen proposal is the
    signature). This is advisory and never gates, per `rubric._self_bias_suspected`'s own
    docstring.
    """
    start = monotonic()
    journal.event("run_started", role="workflow_coordinator", episode_id=ctx.dossier.get("episode_id", ""), cursor=ctx.dossier.get("cursor", ""))

    history: list[Round] = []
    critique: str | None = None

    def finish(outcome: Outcome) -> Outcome:
        journal.event(
            "run_finished",
            outcome=outcome.kind,
            final_score=outcome.final_score,
            best_score=outcome.best_score,
            iterations_run=outcome.iterations_run,
            score_trajectory=list(outcome.score_trajectory),
            self_bias_suspected=outcome.self_bias_suspected,
            wall_ms=int((monotonic() - start) * 1000),
        )
        return outcome

    for i in range(budgets.max_iterations):  # hard ceiling FIRST, unconditional (CrewAI #3847)
        journal.event("iteration_started", iteration=i)

        proposal = propose(ctx, critique, i)
        journal.event("proposal", iteration=i, model=proposer_model, **_proposal_fields(proposal))

        if proposal.blocked:
            # Goose's Ralph-loop escape hatch (design S:2): advisory only. The proposer
            # cannot end the loop by declaring victory, but it can declare itself stuck,
            # and the evaluator still scores the final state before the run is recorded.
            verdict = _evaluate_or_fail_closed(evaluate, ctx, proposal, journal, i, evaluator_model)
            scoring_input = _build_scoring_input(ctx, proposal, verdict)
            findings = _merge_all(scoring_input)
            s = score(findings, CRITERIA)
            journal.event("evaluation", iteration=i, model=evaluator_model, **_verdict_fields(verdict))
            # Every finding, not only the judge's. The four vetoes are deterministic --
            # Python, no model call -- so they were never part of the `evaluation` event,
            # and a watcher saw three green SUPPORTED chips sitting directly above
            # "score: 0.000" with nothing to explain the contradiction. Measured in a
            # browser: that is exactly what a reviewer sees, and it reads as a bug in the
            # scorer rather than as a veto doing its job.
            journal.event(
                "score", iteration=i, value=s,
                findings=[
                    {"criterion_id": cid, "verdict": str(f.verdict), "reasoning": f.reasoning}
                    for cid, f in sorted(findings.items())
                ],
            )
            history.append(Round(index=i, proposal=proposal, findings=findings, score=s))
            journal.event("gate", iteration=i, decision="insufficient_data", proposer_blocked=True, score=s)
            journal.event("iteration_finished", iteration=i)
            return finish(
                Outcome.insufficient_data(tuple(history), proposer_blocked=True, blocked_reason=proposal.blocked_reason)
            )

        verdict = _evaluate_or_fail_closed(evaluate, ctx, proposal, journal, i, evaluator_model)
        scoring_input = _build_scoring_input(ctx, proposal, verdict)
        findings = _merge_all(scoring_input)
        s = score(findings, CRITERIA)
        journal.event("evaluation", iteration=i, model=evaluator_model, **_verdict_fields(verdict))
        # Every finding, not only the judge's. The four vetoes are deterministic --
        # Python, no model call -- so they were never part of the `evaluation` event,
        # and a watcher saw three green SUPPORTED chips sitting directly above
        # "score: 0.000" with nothing to explain the contradiction. Measured in a
        # browser: that is exactly what a reviewer sees, and it reads as a bug in the
        # scorer rather than as a veto doing its job.
        journal.event(
            "score", iteration=i, value=s,
            findings=[
                {"criterion_id": cid, "verdict": str(f.verdict), "reasoning": f.reasoning}
                for cid, f in sorted(findings.items())
            ],
        )
        history.append(Round(index=i, proposal=proposal, findings=findings, score=s))

        vetoed = any(
            c.veto and c.criterion_id in findings and findings[c.criterion_id].verdict != "SUPPORTED"
            for c in CRITERIA
        )
        unanswerable = tuple(
            c.criterion_id
            for c in CRITERIA
            if c.criterion_id in findings and c.weight > 0.0 and findings[c.criterion_id].verdict == "NOT_ADDRESSED"
        )

        if unanswerable and not vetoed:  # 1 -- a successful outcome, not a failure
            journal.event("gate", iteration=i, decision="insufficient_data", unanswerable=list(unanswerable), score=s)
            journal.event("iteration_finished", iteration=i)
            return finish(Outcome.insufficient_data(tuple(history), missing=unanswerable))
        if s >= budgets.threshold:  # 2
            journal.event("gate", iteration=i, decision="complete", score=s)
            journal.event("iteration_finished", iteration=i)
            return finish(Outcome.complete(tuple(history)))
        if _stalled([r.score for r in history]):  # 3 -- the corrected condition (rubric.py), not the design doc's buggy line
            journal.event("gate", iteration=i, decision="stalled", score=s)
            journal.event("iteration_finished", iteration=i)
            return finish(Outcome.stalled(tuple(history)))

        budget_note = _budget_exhausted(journal, budgets, monotonic() - start)
        if budget_note is not None:
            journal.event("gate", iteration=i, decision="capped", score=s, budget_note=budget_note)
            journal.event("iteration_finished", iteration=i)
            return finish(Outcome.capped(tuple(history)))

        # BELOW threshold: the evaluator's REASON goes back to the proposer, aimed at
        # the stated gap (S:6.1/6.2) -- never the proposal's own reasoning, which
        # build_critique's signature cannot accept in the first place (see rubric.py).
        journal.event("gate", iteration=i, decision="continue", score=s)
        journal.event("iteration_finished", iteration=i)
        critique = build_critique(findings, s, criteria=CRITERIA, threshold=budgets.threshold)
        journal.event("critique", iteration=i, text=critique)

    return finish(Outcome.capped(tuple(history)))


def _evaluate_or_fail_closed(
    evaluate: Evaluate,
    ctx: HarnessContext,
    proposal: ProposedAction,
    journal: Journal,
    iteration: int,
    evaluator_model: str | None,
) -> EvaluatorVerdict:
    """Fail closed with a typed reason (design S:6): never let `run_until` manufacture
    an `Outcome` when the judge, the database or the graph is unavailable mid-run.

    `EvaluatorUnavailable` raised by `evaluate` itself (the coordinator's own typed
    failures -- e.g. `evaluator_no_tool_call`, `evaluator_structurally_invalid`, both
    per spec_prompts_roles.md S:C.4) is re-raised unchanged. Anything else escaping
    `evaluate` -- an `LLMError`, a bare network exception, a `SchemaError` -- is wrapped
    into the same typed exception, so a caller of `run_until` only ever has one
    exception type to catch for "the evaluator did not run."
    """
    try:
        return evaluate(ctx, proposal, iteration)
    except EvaluatorUnavailable:
        journal.event("run_finished", outcome="evaluator_unavailable", final_score=None, wall_ms=None, iteration=iteration)
        raise
    except Exception as exc:  # noqa: BLE001 -- deliberately broad; see docstring
        journal.event("run_finished", outcome="evaluator_unavailable", final_score=None, wall_ms=None, iteration=iteration)
        raise EvaluatorUnavailable(f"evaluator failed at iteration {iteration}: {exc}") from exc


def _budget_exhausted(journal: Journal, budgets: RunBudgets, elapsed_s: float) -> str | None:
    """Token and wall-clock budgets, checked once per completed round. Returns a
    human-readable reason, or `None` when neither budget is set or exceeded."""
    if budgets.token_budget > 0:
        used = _tokens_used(journal)
        if used >= budgets.token_budget:
            return f"token budget of {budgets.token_budget} exhausted ({used} used)"
    if budgets.wall_clock_budget_s is not None and elapsed_s >= budgets.wall_clock_budget_s:
        return f"wall-clock budget of {budgets.wall_clock_budget_s:.0f}s exhausted ({elapsed_s:.0f}s elapsed)"
    return None


def _tokens_used(journal: Journal) -> int:
    total = 0
    for ev in journal.events:
        if ev.kind == "llm_response":
            usage = ev.fields.get("usage") or {}
            value = usage.get("total_tokens")
            if isinstance(value, int):
                total += value
    return total


def _proposal_fields(proposal: ProposedAction) -> dict[str, Any]:
    """A JSON-safe projection of `ProposedAction` for the journal.

    Hand-rolled rather than `dataclasses.asdict` so every field name is explicit here
    rather than depending on `schemas.py`'s exact shape not changing under us -- this
    coder does not own `schemas.py`.
    """
    return {
        "reasoning": proposal.reasoning,
        "evidence": [
            {"quote": e.quote, "source_kind": e.source_kind.value, "source_ref": e.source_ref}
            for e in proposal.evidence
        ],
        "grounding_clause_id": proposal.grounding_clause_id,
        "action": proposal.action.value,
        "required_artifacts": list(proposal.required_artifacts),
        "missing_evidence": list(proposal.missing_evidence),
        "blocked": proposal.blocked,
        "blocked_reason": proposal.blocked_reason,
    }


def _verdict_fields(verdict: EvaluatorVerdict) -> dict[str, Any]:
    return {
        "per_criterion": [
            {
                "criterion_id": f.criterion_id,
                "reasoning": f.reasoning,
                "cited_span": (
                    {"quote": f.cited_span.quote, "source_kind": f.cited_span.source_kind.value, "source_ref": f.cited_span.source_ref}
                    if f.cited_span is not None
                    else None
                ),
                "verdict": f.verdict.value if hasattr(f.verdict, "value") else str(f.verdict),
            }
            for f in verdict.per_criterion
        ],
        "overall_reasoning": verdict.overall_reasoning,
    }


def _build_scoring_input(ctx: HarnessContext, proposal: ProposedAction, verdict: EvaluatorVerdict) -> ScoringInput:
    return ScoringInput(
        proposal=proposal,
        evaluator_verdict=verdict,
        tool_results=tuple(ctx.tool_results),
        clauses=ctx.clauses,
        dossier=ctx.dossier,
    )


def _merge_all(scoring_input: ScoringInput) -> dict[str, Any]:
    return merge_findings(
        run_deterministic_criteria(scoring_input),
        run_judge_criteria(scoring_input),
        run_tracked_criteria(scoring_input),
    )
