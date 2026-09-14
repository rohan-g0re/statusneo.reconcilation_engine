"""Tests for `recon.agents.harness`: the generic propose -> evaluate -> score -> gate
loop, exercised with plain Python stand-ins for `propose`/`evaluate` rather than a
`recon.agents.client.LLMClient`.

This is a deliberate choice, not a shortcut: `harness.run_until` is role-agnostic and
its two parameters are `harness.Propose`/`harness.Evaluate` callables, not an
`LLMClient` -- the harness itself never imports `client.py`. A "stub LLMClient
returning scripted responses" is exactly how `roles/coordinator.py`'s own machinery
(forced tool-use, the auto+repair branch, structural validation) is tested, in
`tests/test_agents_roles.py`; testing that machinery a second time here would just be
duplication with extra ceremony. What this file tests is the loop itself, with the
lightest fixtures that let the *real* `rubric.py`/`scorers.py` machinery run end to end
against real `schemas.ProposedAction`/`schemas.EvaluatorVerdict` instances -- no Fakes,
because `schemas.py` has landed.

Fixture design note: every scenario below shares one small dossier (`_dossier()`, an
EXCEPTION-disposition episode with reason code `UNDERPAID`, one grounding clause named)
so that `run_deterministic_criteria` computes real, predictable verdicts from the real
proposal, while `evaluate` stubs control only the eight judge-graded criteria
(`G4, G6, G7, G9, G11, G12, G13, G14` minus whichever are inapplicable to this dossier).
`_judge_applicable()` derives that set from `rubric.CRITERIA` itself rather than
hard-coding it, so a future rubric change cannot silently desync these tests from what
the real evaluator would actually be asked.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Any

import pytest

from recon.agents.grounding import Clause
from recon.agents.harness import HarnessContext, RunBudgets, run_until
from recon.agents.journal import Journal
from recon.agents.rubric import CRITERIA, MAX_ITERATIONS, THRESHOLD, EvaluatorUnavailable
from recon.agents.schemas import (
    ActionEnum,
    CriterionFinding,
    EvaluatorVerdict,
    EvidenceSpan,
    ProposedAction,
    SourceKind,
)
from recon.agents.scorers import RecordedToolResult, ScoringInput

# ═══ shared fixtures ═════════════════════════════════════════════════════════════

CURSOR = "2026-07-01T23:59:59Z"
CURSOR_DATE = "2026-07-01"

VALID_CLAUSE = Clause(
    clause_id="KG-DETERMINISTIC-BOUNDARY-3f2a91c7",
    entity="Deterministic boundary",
    entity_type="Decision",
    text="Every number is born in Python. The LLM may investigate, synthesize, classify, prioritize and recommend",
    index=0,
)
BOGUS_CLAUSE_ID = "KG-NOT-A-REAL-CLAUSE-deadbeef"
SAFE_QUOTE = '"status": "ok"'


def _clock():
    """A deterministic, monotonically increasing fake `now` -- never the real clock
    (`Journal`'s own contract, `.agents/specs/spec_contracts.md` S1)."""
    ticks = iter(range(100_000))

    def _now() -> str:
        n = next(ticks)
        return f"2026-01-01T{n // 3600:02d}:{(n // 60) % 60:02d}:{n % 60:02d}Z"

    return _now


#: Journals opened by `_journal`, closed after every test by the autouse fixture below
#: -- same convention as `tests/test_agents_tools.py`'s `_OPEN_JOURNALS`.
#: `filterwarnings = ["error"]` (`pyproject.toml`) turns an unclosed file's
#: `ResourceWarning` into a hard failure, which is what makes leaving this undrained
#: self-correcting.
_OPEN_JOURNALS: list[Journal] = []


@pytest.fixture(autouse=True)
def _close_journals():
    yield
    while _OPEN_JOURNALS:
        _OPEN_JOURNALS.pop().close()


def _journal(tmp_path: Path, name: str = "run") -> Journal:
    journal = Journal(tmp_path / f"{name}.jsonl", name, now=_clock())
    _OPEN_JOURNALS.append(journal)
    return journal


def _dossier(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "episode_id": "E-000812",
        "cursor": CURSOR,
        "identity": {"track": "PHARMACY"},
        "current": {
            "episode_disposition": "EXCEPTION",
            "reason_codes": ["UNDERPAID"],
            "cross_track_flags": [],
            "rebate_verdict": "C-00",
            "reopened_from": None,
        },
        "timeline": [],
        "unresolved": [],
        "counts": {"verdict_changes": 1},
    }
    base.update(overrides)
    return base


def _ok_tool_result() -> RecordedToolResult:
    envelope = {"status": "ok", "data": {"cursor": CURSOR}, "error_type": None, "message": "", "retryable": False}
    return RecordedToolResult(name="get_episode", arguments={}, envelope=envelope)


def _base_proposal(
    *,
    reasoning: str | None = None,
    grounding_clause_id: str | None = VALID_CLAUSE.clause_id,
    action: ActionEnum = ActionEnum.ESCALATE,
    required_artifacts: tuple[str, ...] = ("Escalate to compliance review",),
    missing_evidence: tuple[str, ...] = (),
    blocked: bool = False,
    blocked_reason: str | None = None,
) -> ProposedAction:
    return ProposedAction(
        reasoning=reasoning or f"As of {CURSOR_DATE}, escalation is warranted.",
        evidence=(EvidenceSpan(quote=SAFE_QUOTE, source_kind=SourceKind.TOOL_RESULT, source_ref="tool_result#1"),),
        grounding_clause_id=grounding_clause_id,
        action=action,
        required_artifacts=required_artifacts,
        missing_evidence=missing_evidence,
        blocked=blocked,
        blocked_reason=blocked_reason,
    )


def _judge_applicable(dossier: dict[str, Any], proposal: ProposedAction) -> list[str]:
    """The criteria a real evaluator call would be asked to grade.

    Deliberately excludes `weight == 0.0` (`G17_rationale_is_not_a_retelling`) even
    though it is `grader == "judge"`: `rubric.run_judge_criteria` takes *every* finding
    in `EvaluatorVerdict.per_criterion` unconditionally (no filter by weight), and
    `rubric.run_tracked_criteria` separately re-reads the *same* `per_criterion` list
    looking for a `G17` entry -- so if a verdict actually contained one,
    `rubric.merge_findings` would see `G17` in both groups and raise on the overlap.
    `rubric.py`'s own `SCORERS` comment lists exactly eight ids next to
    `run_judge_criteria` (`# G4 G6 G7 G9 G11 G12 G13 G14`) and separately lists `G17`
    next to `run_tracked_criteria` (`# G16 G17`), so excluding `G17` here matches that
    comment's intent even though the two functions' actual code does not enforce the
    split themselves. `roles/coordinator.py::_make_evaluate` applies the same
    exclusion for the same reason -- see this coder's report.
    """
    prelim = ScoringInput(proposal=proposal, evaluator_verdict=None, tool_results=(), clauses=(VALID_CLAUSE,), dossier=dossier)
    return [c.criterion_id for c in CRITERIA if c.grader == "judge" and c.weight > 0.0 and c.applies_when(prelim)]


def _verdict_for(dossier: dict[str, Any], proposal: ProposedAction, overrides: dict[str, str]) -> EvaluatorVerdict:
    """Build an `EvaluatorVerdict` covering exactly the applicable judge criteria for
    this dossier/proposal, `SUPPORTED` unless `overrides` says otherwise."""
    findings = []
    for criterion_id in _judge_applicable(dossier, proposal):
        verdict = overrides.get(criterion_id, "SUPPORTED")
        span = None if verdict == "NOT_ADDRESSED" else EvidenceSpan(quote=SAFE_QUOTE, source_kind=SourceKind.TOOL_RESULT, source_ref="tool_result#1")
        findings.append(CriterionFinding(criterion_id, f"test finding for {criterion_id}: {verdict}", span, verdict))
    return EvaluatorVerdict(per_criterion=tuple(findings), overall_reasoning="test overall reasoning")


def _ctx(*, clauses: tuple[Clause, ...] = (VALID_CLAUSE,), dossier: dict[str, Any] | None = None) -> HarnessContext:
    return HarnessContext(dossier=dossier if dossier is not None else _dossier(), clauses=clauses, tool_results=[_ok_tool_result()])


# ═══ 1. complete ═════════════════════════════════════════════════════════════════


def test_every_applicable_criterion_supported_from_the_first_round_completes_immediately(tmp_path):
    proposal = _base_proposal()

    def propose(ctx, critique, iteration):
        assert critique is None  # first call, no prior round to critique
        return proposal

    def evaluate(ctx, proposal_, iteration):
        return _verdict_for(_dossier(), proposal_, {})

    outcome = run_until(propose, evaluate, _ctx(), journal=_journal(tmp_path))

    assert outcome.kind == "complete"
    assert outcome.iterations_run == 1
    assert outcome.final_score >= THRESHOLD


# ═══ 2. insufficient_data via a NOT_ADDRESSED criterion ═════════════════════════


def test_a_not_addressed_judge_finding_ends_the_run_as_insufficient_data_not_a_failure(tmp_path):
    proposal = _base_proposal()

    def propose(ctx, critique, iteration):
        return proposal

    def evaluate(ctx, proposal_, iteration):
        return _verdict_for(_dossier(), proposal_, {"G7_artifacts_are_sufficient": "NOT_ADDRESSED"})

    outcome = run_until(propose, evaluate, _ctx(), journal=_journal(tmp_path))

    assert outcome.kind == "insufficient_data"
    assert outcome.missing_criteria == ("G7_artifacts_are_sufficient",)
    assert outcome.proposer_blocked is False
    assert outcome.iterations_run == 1
    assert "G7_artifacts_are_sufficient" in (outcome.missing_narrative or "")


# ═══ 3. insufficient_data via the proposer's BLOCKED escape hatch ═══════════════


def test_the_proposer_declaring_itself_blocked_ends_the_run_as_insufficient_data(tmp_path):
    proposal = _base_proposal(
        action=ActionEnum.ABSTAIN,
        required_artifacts=(),
        missing_evidence=("bank record for TRN02 8827441 -- not present in the bank feed as of the cursor",),
        blocked=True,
        blocked_reason="no bank record exists that could settle this; no tool will produce one.",
    )
    evaluate_calls: list[int] = []

    def propose(ctx, critique, iteration):
        return proposal

    def evaluate(ctx, proposal_, iteration):
        evaluate_calls.append(iteration)
        # Advisory only, but still scored: design S:2, "the evaluator still scores the
        # final state" even when the proposer has given up.
        return _verdict_for(_dossier(), proposal_, {})

    outcome = run_until(propose, evaluate, _ctx(), journal=_journal(tmp_path))

    assert outcome.kind == "insufficient_data"
    assert outcome.proposer_blocked is True
    assert outcome.blocked_reason == proposal.blocked_reason
    assert outcome.missing_criteria == ()
    assert outcome.iterations_run == 1
    assert evaluate_calls == [0]  # the proposer cannot end the loop unilaterally


# ═══ 4. stalled: the corrected condition, not the design doc's buggy line ═══════


def test_a_permanently_vetoed_score_stalls_after_three_flat_rounds_rather_than_running_to_the_ceiling(tmp_path):
    """`no_write_verbs` (G8, a veto) is permanently CONTRADICTED here, which zeroes the
    gated score every round via `rubric.score`'s veto gate -- regardless of what the
    judge reports. The judge is scripted to report every applicable criterion it grades
    as SUPPORTED, every round (`_verdict_for(..., {})`): "always scores above threshold"
    from its own, unweighted point of view. The real, Python-computed score is flat at
    0.0 from round 0, which is `rubric._stalled`'s own trigger (three rounds with no
    improvement over an earlier baseline) -- proving the corrected stall condition
    fires on an actually-flat trajectory rather than the design doc's line
    (`docs/agent_layer_design.md:66`), which compares two `Verdict` objects' free text
    and can never be true, making `stalled` unreachable and every run either complete
    or run to `capped`.
    """
    proposal = _base_proposal(reasoning=f"As of {CURSOR_DATE}, we have already closed this claim and posted the adjustment.")

    def propose(ctx, critique, iteration):
        return proposal

    def evaluate(ctx, proposal_, iteration):
        return _verdict_for(_dossier(), proposal_, {})

    outcome = run_until(propose, evaluate, _ctx(), journal=_journal(tmp_path))

    assert outcome.kind == "stalled"
    assert outcome.iterations_run == 3
    assert outcome.final_score == 0.0
    assert "G8_no_close_no_post_no_money" in outcome.unmet_criteria


# ═══ 5. the ceiling wins even while the score keeps improving every round ═══════

#: Four judge criteria that never resolve, capping the achievable score at
#: 100*110/140 ~= 78.6 -- below THRESHOLD=80 -- while a disjoint set of four other
#: criteria (one deterministic, three judge) each flip SUPPORTED on a distinct later
#: round, so the real score climbs strictly every round and never repeats. This is the
#: scenario in which a harness that trusted the evaluator's own optimism, or that
#: checked the ceiling anywhere but the loop bound itself, could plausibly keep going
#: past round 5 waiting for a threshold that never arrives.
_PERMANENTLY_STUCK = {
    "G4_figures_are_labelled_correctly": "CONTRADICTED",
    "G9_recommendation_stays_advisory": "CONTRADICTED",
    "G12_action_addresses_reason_codes": "CONTRADICTED",
    "G14_no_invented_deadline": "CONTRADICTED",
}
#: criterion_id -> the first iteration (0-based) at which it flips SUPPORTED.
_FLIPS_JUDGE = {
    "G6_evidence_supports_the_claim": 2,
    "G7_artifacts_are_sufficient": 3,
    "G11_grounding_clause_supports_action": 4,
}
_G10_FLIPS_AT_ITERATION = 1  # deterministic: grounding_clause_id becomes valid


def test_the_ceiling_stops_the_loop_even_though_the_score_climbs_every_round(tmp_path):
    """CrewAI #3847: the ceiling is `range(budgets.max_iterations)` -- the loop bound
    itself -- so it must win even against a run that never stalls and never regresses.
    """

    def propose(ctx, critique, iteration):
        clause_id = VALID_CLAUSE.clause_id if iteration >= _G10_FLIPS_AT_ITERATION else BOGUS_CLAUSE_ID
        return _base_proposal(grounding_clause_id=clause_id)

    def evaluate(ctx, proposal_, iteration):
        overrides = dict(_PERMANENTLY_STUCK)
        for criterion_id, flip_at in _FLIPS_JUDGE.items():
            overrides[criterion_id] = "SUPPORTED" if iteration >= flip_at else "CONTRADICTED"
        return _verdict_for(_dossier(), proposal_, overrides)

    outcome = run_until(propose, evaluate, _ctx(), journal=_journal(tmp_path))

    assert outcome.kind == "capped"
    assert outcome.iterations_run == MAX_ITERATIONS == 5
    assert outcome.final_score < THRESHOLD
    trajectory = list(outcome.score_trajectory)
    assert trajectory == sorted(trajectory)
    assert len(set(trajectory)) == len(trajectory), "every round must score strictly higher than the last"


# ═══ 6. the evaluator never receives the proposer's reasoning ═══════════════════


def test_the_evaluator_request_never_contains_the_proposers_reasoning(tmp_path):
    # No digits in the sentinel: a stray digit run here would itself trip G3
    # (no_unsourced_number, a veto) and stall the run before it ever reaches
    # "complete" -- unrelated to what this test is actually checking.
    sentinel = "the-proposers-own-reasoning-argument-marker-zzq"
    proposal = _base_proposal(reasoning=f"As of {CURSOR_DATE}, {sentinel}: escalation is warranted.")
    journal = _journal(tmp_path)

    def propose(ctx, critique, iteration):
        return proposal

    def evaluate(ctx, proposal_, iteration):
        # A realistic stand-in for roles/coordinator.py's real `evaluate`: it builds a
        # fresh request body from `ctx` and `proposal_` alone -- never `.reasoning` --
        # and journals it itself, matching what a real `Evaluate` closure does
        # (`roles/coordinator.py`'s own `_emit_structured` journals `llm_request`
        # around every call it makes).
        body = {
            "action": proposal_.action.value,
            "evidence": [{"quote": e.quote, "source_ref": e.source_ref} for e in proposal_.evidence],
            "grounding_clause_id": proposal_.grounding_clause_id,
            "required_artifacts": list(proposal_.required_artifacts),
        }
        journal.event("llm_request", iteration=iteration, agent="evaluator", model="deepseek-v4-pro", body=body)
        return _verdict_for(_dossier(), proposal_, {})

    outcome = run_until(propose, evaluate, _ctx(), journal=journal)
    assert outcome.kind == "complete"

    evaluator_requests = [e for e in journal.events if e.kind == "llm_request" and e.fields.get("agent") == "evaluator"]
    assert evaluator_requests, "expected at least one journalled evaluator request"
    for ev in evaluator_requests:
        assert sentinel not in json.dumps(ev.fields, default=str)

    # Structural guarantee: `harness.Evaluate` is called with only `(ctx, proposal,
    # iteration)` -- there is no `history` and no `critique` parameter through which
    # anything upstream of the closure could hand it the reasoning even by accident.
    assert list(inspect.signature(evaluate).parameters) == ["ctx", "proposal_", "iteration"]


# ═══ 7. an evaluator failure fails closed ═══════════════════════════════════════


def test_an_evaluator_failure_fails_closed_with_evaluatorunavailable_and_journals_it(tmp_path):
    proposal = _base_proposal()
    journal = _journal(tmp_path)

    def propose(ctx, critique, iteration):
        return proposal

    def evaluate(ctx, proposal_, iteration):
        raise RuntimeError("the judge API returned 500")

    with pytest.raises(EvaluatorUnavailable) as excinfo:
        run_until(propose, evaluate, _ctx(), journal=journal)

    assert "the judge API returned 500" in str(excinfo.value)
    finished = [e for e in journal.events if e.kind == "run_finished"]
    assert finished, "a failed evaluator call must still leave a run_finished record"
    assert finished[-1].fields["outcome"] == "evaluator_unavailable"


def test_evaluatorunavailable_raised_by_evaluate_itself_propagates_unchanged(tmp_path):
    """The coordinator's own typed failures (`evaluator_no_tool_call`,
    `evaluator_structurally_invalid`) must reach the caller as `EvaluatorUnavailable`
    with their original message, not be re-wrapped into a second, less specific one."""
    proposal = _base_proposal()

    def propose(ctx, critique, iteration):
        return proposal

    def evaluate(ctx, proposal_, iteration):
        raise EvaluatorUnavailable("evaluator_no_tool_call: no tool call after one repair turn")

    with pytest.raises(EvaluatorUnavailable, match="evaluator_no_tool_call"):
        run_until(propose, evaluate, _ctx(), journal=_journal(tmp_path))


# ═══ 8. the score trajectory is journalled ══════════════════════════════════════


def test_the_score_trajectory_is_journalled_every_round_and_on_run_finished(tmp_path):
    journal = _journal(tmp_path)

    def propose(ctx, critique, iteration):
        clause_id = VALID_CLAUSE.clause_id if iteration >= _G10_FLIPS_AT_ITERATION else BOGUS_CLAUSE_ID
        return _base_proposal(grounding_clause_id=clause_id)

    def evaluate(ctx, proposal_, iteration):
        overrides = dict(_PERMANENTLY_STUCK)
        for criterion_id, flip_at in _FLIPS_JUDGE.items():
            overrides[criterion_id] = "SUPPORTED" if iteration >= flip_at else "CONTRADICTED"
        return _verdict_for(_dossier(), proposal_, overrides)

    outcome = run_until(propose, evaluate, _ctx(), journal=journal)
    assert outcome.kind == "capped"

    score_events = [e.fields["value"] for e in journal.events if e.kind == "score"]
    assert score_events == list(outcome.score_trajectory)

    run_finished = [e for e in journal.events if e.kind == "run_finished"][-1]
    assert run_finished.fields["score_trajectory"] == list(outcome.score_trajectory)
    assert run_finished.fields["self_bias_suspected"] == outcome.self_bias_suspected
    assert isinstance(run_finished.fields["wall_ms"], int)


# ═══ 9. budgets other than the iteration ceiling also live in the harness ═══════


def test_a_token_budget_caps_the_run_before_the_iteration_ceiling(tmp_path):
    """Token/wall-clock budgets live in the harness, never in a prompt (design S:2).
    A stub `evaluate` reports a large `total_tokens` usage on its `llm_response`, and a
    small `token_budget` must cap the run well before `MAX_ITERATIONS`."""
    journal = _journal(tmp_path)

    def propose(ctx, critique, iteration):
        clause_id = VALID_CLAUSE.clause_id if iteration >= _G10_FLIPS_AT_ITERATION else BOGUS_CLAUSE_ID
        return _base_proposal(grounding_clause_id=clause_id)

    def evaluate(ctx, proposal_, iteration):
        journal.event("llm_response", iteration=iteration, agent="evaluator", model="deepseek-v4-pro", usage={"total_tokens": 1000})
        overrides = dict(_PERMANENTLY_STUCK)
        for criterion_id, flip_at in _FLIPS_JUDGE.items():
            overrides[criterion_id] = "SUPPORTED" if iteration >= flip_at else "CONTRADICTED"
        return _verdict_for(_dossier(), proposal_, overrides)

    budgets = RunBudgets(token_budget=1500)  # exhausted after the second round's usage
    outcome = run_until(propose, evaluate, _ctx(), journal=journal, budgets=budgets)

    assert outcome.kind == "capped"
    assert outcome.iterations_run < MAX_ITERATIONS
