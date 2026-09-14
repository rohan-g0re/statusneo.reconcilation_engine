"""Generate four hand-scripted, offline fixtures for the eval set -- no network, no
API key, reproducible by construction. Companion to
``scripts/record_g3_veto_fixture.py`` (that one script per deliberate-failure
fixture; this one covers the remaining scripted scenarios in one place because they
share all of their plumbing and none of their content).

Each scenario below drives the real ``run_coordinator`` against a real generated
database with a scripted ``LLMClient`` standing in for both models, wrapped in
``RecordingClient`` so the exact request/response bytes land in
``tests/fixtures/agent_traces/<run_id>.jsonl`` in the shape ``ReplayClient`` expects.
Where a scenario's proposal cites a tool result, the tool is dispatched for real
against the database -- only the two model calls are scripted, never the
deterministic layer underneath them.

    python scripts/record_scripted_eval_fixtures.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from recon.agents.client import LLMResponse, RecordingClient, _parse_response  # noqa: E402
from recon.agents.config import load_agent_settings  # noqa: E402
from recon.agents.grounding import render_clause_index, select_clauses  # noqa: E402
from recon.agents.harness import RunBudgets  # noqa: E402
from recon.agents.journal import Journal  # noqa: E402
from recon.agents.roles.coordinator import run_coordinator  # noqa: E402
from recon.agents.rubric import CRITERIA  # noqa: E402
from recon.agents.schemas import ProposedAction  # noqa: E402
from recon.agents.scorers import RecordedToolResult, ScoringInput  # noqa: E402
from recon.agents.tools import CallLog, ToolContext, dispatch  # noqa: E402
from recon.api import dossier as dossier_module  # noqa: E402
from recon.config import load_settings  # noqa: E402
from recon.db.connection import open_db  # noqa: E402
from recon.domain import verdicts as verdict_vocab  # noqa: E402

# Pinned, not inherited. A fixture is matched at replay time by a digest over
# (model, messages, tools, tool_choice), so the model ids a trace was recorded
# against are part of that trace's identity. `AgentSettings`' evaluator default has
# since moved off `deepseek-v4-pro` on latency grounds (config.py), and inheriting
# whatever the default happens to be would silently invalidate every committed
# fixture the next time it changes. tests/test_agents_evals.py pins the same pair.
_FIXTURE_MODELS = {"proposer_model": "deepseek-chat", "evaluator_model": "deepseek-chat"}


NOW = "2026-07-02T00:00:00Z"


def _glossary() -> str:
    codes = (*verdict_vocab.REIMBURSEMENT_CODES, *verdict_vocab.REBATE_CODES)
    return "\n".join(f"{code}: {verdict_vocab.describe(code)}" for code in codes)


def _tool_call_response(name: str, arguments: dict[str, Any], *, model: str, call_id: str = "call-1") -> LLMResponse:
    raw = {
        "id": "chatcmpl-fixture",
        "model": model,
        "choices": [
            {
                "index": 0,
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {"name": name, "arguments": json.dumps(arguments, default=str)},
                        }
                    ],
                },
            }
        ],
        "usage": {"prompt_tokens": 400, "completion_tokens": 120, "total_tokens": 520},
    }
    return _parse_response(raw)


class _ScriptedClient:
    """Pops one scripted `LLMResponse` per call, in order. No network, ever."""

    def __init__(self, responses: list[LLMResponse]) -> None:
        self._responses = list(responses)

    def complete(self, *, agent, messages, model, tools=None, tool_choice=None, max_tokens=None, temperature=None, iteration=None) -> LLMResponse:  # noqa: E501
        del agent, messages, model, tools, tool_choice, max_tokens, temperature, iteration
        if not self._responses:
            raise AssertionError("scripted client exhausted -- the recorded run made more LLM calls than expected")
        return self._responses.pop(0)


def _judge_checklist(proposal: ProposedAction, tool_results: tuple[RecordedToolResult, ...], clauses, dossier) -> list:
    prelim = ScoringInput(proposal=proposal, evaluator_verdict=None, tool_results=tool_results, clauses=clauses, dossier=dossier)
    return [c for c in CRITERIA if c.grader == "judge" and c.weight > 0.0 and c.applies_when(prelim)]


def _all_supported_evaluation(checklist, *, quote: str, source_ref: str, source_kind: str) -> dict[str, Any]:
    return {
        "per_criterion": [
            {
                "criterion_id": c.criterion_id,
                "reasoning": "The proposal's own reasoning and cited evidence are consistent with this criterion.",
                "cited_span": {"quote": quote, "source_kind": source_kind, "source_ref": source_ref},
                "verdict": "SUPPORTED",
            }
            for c in checklist
        ],
        "overall_reasoning": "Every judged criterion is supported by the cited material.",
    }


def _record_run(
    *,
    run_id: str,
    nonce: str,
    episode_id: str,
    profile: str,
    proposer_responses: list[LLMResponse],
    evaluator_response: LLMResponse,
    max_iterations: int = 1,
) -> None:
    # Every scenario here is a "one full round, see what happens" construction, and
    # every scripted response list above has exactly enough entries for exactly one
    # round. `max_iterations=1` is what makes that match reality: a proposal that
    # does not clear the threshold on its own single round would otherwise send the
    # harness looking for a second round of scripted responses that were never
    # written, which fails as a confusing `AssertionError` deep inside the harness
    # rather than as a clear "budget" decision. A `complete` outcome (the happy-path
    # and ABSTAIN scenarios) returns on iteration 0 regardless of this ceiling, since
    # `run_until` checks the threshold before it ever checks the ceiling.
    settings = load_settings(profile)
    agent_settings = load_agent_settings(**_FIXTURE_MODELS)
    conn = open_db(settings)
    cursor = settings.max_cursor
    dossier = dossier_module.build_dossier(conn, episode_id, cursor)
    if dossier is None:
        raise SystemExit(f"{episode_id} does not exist at {cursor} in profile {profile!r}")
    clauses = select_clauses(dossier)
    clause_index = render_clause_index(clauses)

    fixture_dir = agent_settings.fixture_dir
    fixture_path = fixture_dir / f"{run_id}.jsonl"
    if fixture_path.exists():
        fixture_path.unlink()
    scripted = _ScriptedClient([*proposer_responses, evaluator_response])
    recording_client = RecordingClient(scripted, fixture_dir, run_id)

    journal = Journal(agent_settings.journal_dir / f"{run_id}.jsonl", run_id, now=lambda: NOW)
    ctx = ToolContext(
        conn=conn, cursor=cursor, role="workflow_coordinator", model_id=agent_settings.proposer_model,
        run_id=run_id, now=NOW, nonce=nonce, write_token=None, call_log=CallLog(), journal=journal,
    )
    try:
        outcome = run_coordinator(
            client=recording_client, tool_ctx=ctx, settings=agent_settings, dossier=dossier,
            verdict_glossary=_glossary(), grounding_clause_index=clause_index, journal=journal,
            budgets=RunBudgets(max_iterations=max_iterations, threshold=agent_settings.threshold),
        )
    finally:
        journal.close()

    print(f"\n=== {run_id} ({episode_id}, profile={profile}) ===")
    print(f"  outcome  {outcome.kind}  score={outcome.final_score}")
    print(f"  action   {outcome.proposal.action.value}")
    print("  findings " + ", ".join(f"{cid}={f.verdict.value}" for cid, f in outcome.findings.items()))
    manifest = {
        "episode_id": episode_id, "profile": profile, "cursor": cursor, "run_id": run_id, "nonce": nonce, "now": NOW,
        "expected_outcome_kind": outcome.kind,
    }
    (fixture_dir / f"{run_id}.manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


# ═══ scenario 1: the happy path -- call a tool, then cite exactly what it returned ═

def scenario_happy_path_cites_a_tool_result() -> None:
    episode_id, profile, run_id, nonce = "E-000006", "demo", "eval-happy-path-cites-tool-result", "11112222"
    settings = load_settings(profile)
    agent_settings = load_agent_settings(**_FIXTURE_MODELS)
    conn = open_db(settings)
    cursor = settings.max_cursor
    dossier = dossier_module.build_dossier(conn, episode_id, cursor)
    clauses = select_clauses(dossier)
    clause = clauses[0]

    # Dispatched for real, exactly as the harness would when the proposer calls it --
    # this is what makes the evidence quote below verifiable rather than assumed.
    journal_probe = Journal(Path(agent_settings.journal_dir) / f"{run_id}-probe.jsonl", f"{run_id}-probe", now=lambda: NOW)
    probe_ctx = ToolContext(
        conn=conn, cursor=cursor, role="workflow_coordinator", model_id=agent_settings.proposer_model,
        run_id=run_id, now=NOW, nonce=nonce, write_token=None, call_log=CallLog(), journal=journal_probe,
    )
    envelope = dispatch(probe_ctx, "calculate_reconciliation", {"claim_id": episode_id})
    journal_probe.close()
    tool_result = RecordedToolResult(name="calculate_reconciliation", arguments={"claim_id": episode_id}, envelope=envelope)

    proposal_args: dict[str, Any] = {
        "reasoning": (
            f"As of {cursor[:10]}, calculate_reconciliation#1 shows this MEDICAL claim was denied "
            '(reimbursement.verdict_meaning "B-10 — Denied, no appeal filed") with an 8424000-cent '
            "variance and no appeal on file, while the 340B rebate on the same episode was approved, "
            "paid and cash-matched (C-08) — the compliance pattern this clause names. The claim should "
            "be appealed rather than written off: the denial reason is one payers commonly reverse on "
            "a documented first appeal, and no appeal has been attempted yet."
        ),
        "evidence": [
            {
                "quote": '"variance_cents": 8424000',
                "source_kind": "tool_result",
                "source_ref": "calculate_reconciliation#1",
            },
            {"quote": clause.text if len(clause.text) <= 100 else clause.text[:100], "source_kind": "grounding_clause", "source_ref": f"KG:{clause.entity}"},
        ],
        "grounding_clause_id": clause.clause_id,
        "action": "APPEAL",
        "required_artifacts": ["the payer's denial letter", "a first-level appeal packet citing the medical necessity documentation on file"],
        "missing_evidence": [],
        "blocked": False,
        "blocked_reason": None,
    }
    proposal = ProposedAction.parse(proposal_args)
    checklist = _judge_checklist(proposal, (tool_result,), clauses, dossier)
    evaluation_args = _all_supported_evaluation(checklist, quote='"variance_cents": 8424000', source_ref="calculate_reconciliation#1", source_kind="tool_result")

    proposer_call_response = _tool_call_response("calculate_reconciliation", {"claim_id": episode_id}, model=agent_settings.proposer_model, call_id="call-1")
    proposer_emit_response = _tool_call_response("emit_proposed_action", proposal_args, model=agent_settings.proposer_model, call_id="call-2")
    evaluator_response = _tool_call_response("emit_evaluation", evaluation_args, model=agent_settings.evaluator_model)

    _record_run(
        run_id=run_id, nonce=nonce, episode_id=episode_id, profile=profile,
        proposer_responses=[proposer_call_response, proposer_emit_response], evaluator_response=evaluator_response,
    )


# ═══ scenario 2: a completed-action claim trips the write-verb veto (G8) ═══════════

def scenario_g8_write_verb_veto() -> None:
    episode_id, profile, run_id, nonce = "E-000032", "demo", "eval-g8-write-verb-veto", "33334444"
    settings = load_settings(profile)
    agent_settings = load_agent_settings(**_FIXTURE_MODELS)
    conn = open_db(settings)
    cursor = settings.max_cursor
    dossier = dossier_module.build_dossier(conn, episode_id, cursor)
    clauses = select_clauses(dossier)
    clause = clauses[0]
    quote = clause.text if len(clause.text) <= 100 else clause.text[:100]

    proposal_args: dict[str, Any] = {
        "reasoning": (
            f"As of {cursor[:10]}, per {clause.entity!r}, no cash arrived for this claim despite an "
            "expected payment, which matches a bank-side reconciliation gap rather than a denial. "
            "I have already closed this claim in our own system and escalated it to the payer "
            "relations team for their own follow-up, so no further action is needed here."
        ),
        "evidence": [{"quote": quote, "source_kind": "grounding_clause", "source_ref": f"KG:{clause.entity}"}],
        "grounding_clause_id": clause.clause_id,
        "action": "ESCALATE",
        "required_artifacts": ["a copy of the escalation ticket sent to payer relations"],
        "missing_evidence": [],
        "blocked": False,
        "blocked_reason": None,
    }
    proposal = ProposedAction.parse(proposal_args)
    checklist = _judge_checklist(proposal, (), clauses, dossier)
    evaluation_args = _all_supported_evaluation(checklist, quote=quote, source_ref=f"KG:{clause.entity}", source_kind="grounding_clause")

    proposer_response = _tool_call_response("emit_proposed_action", proposal_args, model=agent_settings.proposer_model)
    evaluator_response = _tool_call_response("emit_evaluation", evaluation_args, model=agent_settings.evaluator_model)
    _record_run(run_id=run_id, nonce=nonce, episode_id=episode_id, profile=profile, proposer_responses=[proposer_response], evaluator_response=evaluator_response)


# ═══ scenario 3: an action outside the vocabulary permitted for CLOSED (G1) ════════

def scenario_g1_disposition_mismatch() -> None:
    episode_id, profile, run_id, nonce = "E-000002", "demo", "eval-g1-disposition-mismatch", "55556666"
    settings = load_settings(profile)
    agent_settings = load_agent_settings(**_FIXTURE_MODELS)
    conn = open_db(settings)
    cursor = settings.max_cursor
    dossier = dossier_module.build_dossier(conn, episode_id, cursor)
    clauses = select_clauses(dossier)
    clause = clauses[0]
    quote = clause.text if len(clause.text) <= 100 else clause.text[:100]

    proposal_args: dict[str, Any] = {
        "reasoning": (
            f"As of {cursor[:10]}, per {clause.entity!r}, this claim's 340B rebate was rejected. "
            "Even though the episode is closed, the rebate should be resubmitted with corrected "
            "qualification paperwork, since the underlying dispense still looks 340B-eligible."
        ),
        "evidence": [{"quote": quote, "source_kind": "grounding_clause", "source_ref": f"KG:{clause.entity}"}],
        "grounding_clause_id": clause.clause_id,
        "action": "RESUBMIT",  # not permitted for CLOSED (only ABSTAIN is) -- the point of this fixture
        "required_artifacts": ["corrected TPA qualification paperwork"],
        "missing_evidence": [],
        "blocked": False,
        "blocked_reason": None,
    }
    proposal = ProposedAction.parse(proposal_args)
    checklist = _judge_checklist(proposal, (), clauses, dossier)
    evaluation_args = _all_supported_evaluation(checklist, quote=quote, source_ref=f"KG:{clause.entity}", source_kind="grounding_clause")

    proposer_response = _tool_call_response("emit_proposed_action", proposal_args, model=agent_settings.proposer_model)
    evaluator_response = _tool_call_response("emit_evaluation", evaluation_args, model=agent_settings.evaluator_model)
    _record_run(run_id=run_id, nonce=nonce, episode_id=episode_id, profile=profile, proposer_responses=[proposer_response], evaluator_response=evaluator_response)


# ═══ scenario 4: ABSTAIN is correct for a CLOSED episode, and the harness accepts it ═

def scenario_abstain_completes() -> None:
    """The mirror image of ``eval-g3-veto-failure``: a proposal that also states the
    cursor date and cites no dollar figure, but -- unlike that fixture -- calls a
    real tool first (`get_episode`), whose response echoes the dossier's own
    `cursor` field verbatim. That is what sources the date this time (see
    ``docs/eval_g3_veto_failure_case.md``'s note on the G15/G3 interaction): a
    proposal that does its one bit of homework clears every criterion and the run
    completes, in contrast to the deliberate-failure fixture that skips it."""
    episode_id, profile, run_id, nonce = "E-000002", "demo", "eval-abstain-completes", "77778888"
    settings = load_settings(profile)
    agent_settings = load_agent_settings(**_FIXTURE_MODELS)
    conn = open_db(settings)
    cursor = settings.max_cursor
    dossier = dossier_module.build_dossier(conn, episode_id, cursor)
    clauses = select_clauses(dossier)
    clause = clauses[0]
    quote = clause.text if len(clause.text) <= 100 else clause.text[:100]

    journal_probe = Journal(Path(agent_settings.journal_dir) / f"{run_id}-probe.jsonl", f"{run_id}-probe", now=lambda: NOW)
    probe_ctx = ToolContext(
        conn=conn, cursor=cursor, role="workflow_coordinator", model_id=agent_settings.proposer_model,
        run_id=run_id, now=NOW, nonce=nonce, write_token=None, call_log=CallLog(), journal=journal_probe,
    )
    envelope = dispatch(probe_ctx, "get_episode", {"episode_id": episode_id})
    journal_probe.close()
    tool_result = RecordedToolResult(name="get_episode", arguments={"episode_id": episode_id}, envelope=envelope)

    proposal_args: dict[str, Any] = {
        "reasoning": (
            f"get_episode#1 confirms this episode is CLOSED as of {cursor[:10]}: the reimbursement "
            f"settled and, per {clause.entity!r}, the 340B rebate request was rejected with no "
            "further recourse on file. A closed episode with no new inbound record cannot change "
            "disposition, so there is nothing actionable to recommend here beyond noting the rebate "
            "rejection for the record."
        ),
        "evidence": [
            {"quote": quote, "source_kind": "grounding_clause", "source_ref": f"KG:{clause.entity}"},
        ],
        "grounding_clause_id": clause.clause_id,
        "action": "ABSTAIN",
        "required_artifacts": [],
        "missing_evidence": ["a payer response indicating any new appeal path exists for the rejected rebate"],
        "blocked": False,
        "blocked_reason": None,
    }
    proposal = ProposedAction.parse(proposal_args)
    checklist = _judge_checklist(proposal, (tool_result,), clauses, dossier)
    evaluation_args = _all_supported_evaluation(checklist, quote=quote, source_ref=f"KG:{clause.entity}", source_kind="grounding_clause")

    proposer_call_response = _tool_call_response("get_episode", {"episode_id": episode_id}, model=agent_settings.proposer_model, call_id="call-1")
    proposer_emit_response = _tool_call_response("emit_proposed_action", proposal_args, model=agent_settings.proposer_model, call_id="call-2")
    evaluator_response = _tool_call_response("emit_evaluation", evaluation_args, model=agent_settings.evaluator_model)
    _record_run(
        run_id=run_id, nonce=nonce, episode_id=episode_id, profile=profile,
        proposer_responses=[proposer_call_response, proposer_emit_response], evaluator_response=evaluator_response,
    )


SCENARIOS: list[Callable[[], None]] = [
    scenario_happy_path_cites_a_tool_result,
    scenario_g8_write_verb_veto,
    scenario_g1_disposition_mismatch,
    scenario_abstain_completes,
]


def main() -> int:
    for scenario in SCENARIOS:
        scenario()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
