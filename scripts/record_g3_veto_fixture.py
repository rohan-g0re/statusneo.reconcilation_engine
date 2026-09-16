"""Generate one hand-scripted, offline fixture: the eval set's deliberate failure case.

Unlike ``agent_smoke.py --record``, this never touches the network. The "model" is a
fixed, two-response script -- a plausible-looking proposal followed by an evaluator
that approves everything it is asked to judge -- wrapped in the same
``recon.agents.client.RecordingClient`` the live smoke script uses, so it produces a
fixture with the exact on-disk shape ``ReplayClient`` replays. That is what makes this
reproducible without an API key: run it once, commit the fixture, and
``tests/test_agents_evals.py`` replays it forever.

**What it demonstrates.** The proposal makes zero tool calls -- it reasons entirely
from a grounding clause -- and states one dollar figure no tool result backs. Every
other criterion is scripted to pass: the evidence span verifies verbatim, the
grounding clause resolves, the action fits the episode's disposition, the cursor date
is stated, and the judge approves every criterion it is asked about. None of that
matters. G3 (``no_unsourced_number``) is a veto: one unsourced figure zeroes the
score outright (``rubric.score``'s ``gate`` term), so the run reaches ``capped``,
never ``complete``, no matter how sound everything else looks. See
``docs/eval_g3_veto_failure_case.md`` for the production-hardening note this fixture
exists to support.

    python scripts/record_g3_veto_fixture.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from recon.agents.client import LLMResponse, RecordingClient, ToolCall, _parse_response  # noqa: E402
from recon.agents.config import load_agent_settings  # noqa: E402
from recon.agents.grounding import render_clause_index, select_clauses  # noqa: E402
from recon.agents.harness import RunBudgets  # noqa: E402
from recon.agents.journal import Journal  # noqa: E402
from recon.agents.roles.coordinator import run_coordinator  # noqa: E402
from recon.agents.rubric import applicable_judge_criteria  # noqa: E402
from recon.agents.schemas import ProposedAction  # noqa: E402
from recon.agents.scorers import ScoringInput  # noqa: E402
from recon.agents.tools import CallLog, ToolContext  # noqa: E402
from recon.api import dossier as dossier_module  # noqa: E402
from recon.config import CuratedSpine, load_settings  # noqa: E402
from recon.db.connection import open_db  # noqa: E402
from recon.domain import verdicts as verdict_vocab  # noqa: E402

# Pinned, not inherited. A fixture is matched at replay time by a digest over
# (model, messages, tools, tool_choice), so the model ids a trace was recorded
# against are part of that trace's identity. `AgentSettings`' evaluator default has
# since moved off `deepseek-v4-pro` on latency grounds (config.py), and inheriting
# whatever the default happens to be would silently invalidate every committed
# fixture the next time it changes. tests/test_agents_evals.py pins the same pair.
_FIXTURE_MODELS = {"proposer_model": "deepseek-chat", "evaluator_model": "deepseek-chat"}


EPISODE_ID = "E-000006"
NONCE = "f00dfeed"
RUN_ID = "eval-g3-veto-failure"
NOW = "2026-07-02T00:00:00Z"


def _glossary() -> str:
    codes = (*verdict_vocab.REIMBURSEMENT_CODES, *verdict_vocab.REBATE_CODES)
    return "\n".join(f"{code}: {verdict_vocab.describe(code)}" for code in codes)


def _tool_call_response(name: str, arguments: dict[str, Any], *, model: str) -> LLMResponse:
    """A response carrying exactly one forced-shaped tool call, built through the
    real client parser (`_parse_response`) so `.raw` and the parsed fields can never
    drift apart -- the same guarantee a live response gets for free."""
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
                            "id": "call-1",
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


def main() -> int:
    # Pinned for the same reason the model ids above are: the demo profile's composition is
    # drawn from the seed by default, and a fixture recorded against one composition cannot
    # be replayed against another. tests/test_agents_evals.py builds its dataset with the
    # same spine.
    settings = load_settings("demo", curated_spine=CuratedSpine.RECORDED)
    agent_settings = load_agent_settings(**_FIXTURE_MODELS)  # api_key irrelevant: nothing here calls the network
    conn = open_db(settings)
    cursor = settings.max_cursor
    dossier = dossier_module.build_dossier(conn, EPISODE_ID, cursor)
    if dossier is None:
        print(f"{EPISODE_ID} does not exist at {cursor}", file=sys.stderr)
        return 2

    clauses = select_clauses(dossier)
    clause = clauses[0]
    quote = clause.text if len(clause.text) <= 100 else clause.text[:100]

    proposal_args: dict[str, Any] = {
        "reasoning": (
            f"As of {cursor[:10]}, this claim was denied by the payer and, per {clause.entity!r}, "
            "the compliance pattern here is a denial that does not disturb an already-paid 340B "
            "rebate. No further recovery is realistic on the reimbursement side: the residual "
            "exposure after the denial is $412.60, and pursuing it further is not worth the "
            "effort against a payer that has already refused the claim once. This should be "
            "written off."
        ),
        "evidence": [{"quote": quote, "source_kind": "grounding_clause", "source_ref": f"KG:{clause.entity}"}],
        "grounding_clause_id": clause.clause_id,
        "action": "WRITE_OFF",
        "required_artifacts": ["AR write-off approval citing the payer's denial reason"],
        "missing_evidence": [],
        "blocked": False,
        "blocked_reason": None,
    }
    proposal = ProposedAction.parse(proposal_args)

    # The real checklist this proposal would actually face -- computed the same way
    # `roles/coordinator.py._make_evaluate` does, with `tool_results=()` because the
    # scripted proposer (below) calls no tool at all. Getting this list right matters:
    # `EvaluatorVerdict.per_criterion` must match it exactly, in order, or the harness
    # raises `evaluator_structurally_invalid` instead of the veto failure this fixture
    # exists to demonstrate.
    prelim = ScoringInput(proposal=proposal, evaluator_verdict=None, tool_results=(), clauses=clauses, dossier=dossier)
    applicable = applicable_judge_criteria(prelim)
    print("judge checklist for this proposal:", [c.criterion_id for c in applicable])

    evaluation_args: dict[str, Any] = {
        "per_criterion": [
            {
                "criterion_id": c.criterion_id,
                "reasoning": "The cited clause and the proposal's own reasoning are consistent with this criterion.",
                "cited_span": {"quote": quote, "source_kind": "grounding_clause", "source_ref": f"KG:{clause.entity}"},
                "verdict": "SUPPORTED",
            }
            for c in applicable
        ],
        "overall_reasoning": (
            "Every judged criterion is supported by the cited grounding clause; nothing here "
            "raises a compliance concern on its face."
        ),
    }

    proposer_response = _tool_call_response("emit_proposed_action", proposal_args, model=agent_settings.proposer_model)
    evaluator_response = _tool_call_response("emit_evaluation", evaluation_args, model=agent_settings.evaluator_model)

    fixture_dir = agent_settings.fixture_dir
    fixture_path = fixture_dir / f"{RUN_ID}.jsonl"
    if fixture_path.exists():
        fixture_path.unlink()  # a stale fixture from a previous run must not be appended to
    recording_client = RecordingClient(_ScriptedClient([proposer_response, evaluator_response]), fixture_dir, RUN_ID)

    journal = Journal(agent_settings.journal_dir / f"{RUN_ID}.jsonl", RUN_ID, now=lambda: NOW)
    ctx = ToolContext(
        conn=conn, cursor=cursor, role="workflow_coordinator", model_id=agent_settings.proposer_model,
        run_id=RUN_ID, now=NOW, nonce=NONCE, write_token=None, call_log=CallLog(), journal=journal,
    )
    clause_index = render_clause_index(clauses)
    journal.event("run_started", role="workflow_coordinator", episode_id=EPISODE_ID, cursor=cursor, model=agent_settings.proposer_model)
    try:
        outcome = run_coordinator(
            client=recording_client, tool_ctx=ctx, settings=agent_settings, dossier=dossier,
            verdict_glossary=_glossary(), grounding_clause_index=clause_index, journal=journal,
            # A one-iteration budget: with the veto gate at 0.0, the harness has nothing left
            # to do after round 1 but end the run, which is the point -- this fixture is
            # about the veto firing, not about how many rounds it takes to give up.
            budgets=RunBudgets(max_iterations=1, threshold=agent_settings.threshold),
        )
    finally:
        journal.event("run_finished", role="workflow_coordinator")
        journal.close()

    print(f"outcome    {outcome.kind}")
    print(f"score      final={outcome.final_score} best={outcome.best_score}")
    print(f"findings   {[(cid, f.verdict.value if hasattr(f.verdict, 'value') else f.verdict) for cid, f in outcome.findings.items()]}")

    manifest = {
        "episode_id": EPISODE_ID,
        "profile": "demo",
        "cursor": cursor,
        "run_id": RUN_ID,
        "nonce": NONCE,
        "now": NOW,
        "budgets": {"max_iterations": 1, "threshold": agent_settings.threshold},
        "expected_outcome_kind": outcome.kind,
    }
    (fixture_dir / f"{RUN_ID}.manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"\nfixture    {fixture_path}")
    print(f"manifest   {fixture_dir / f'{RUN_ID}.manifest.json'}")
    return 0 if outcome.kind == "capped" else 1


if __name__ == "__main__":
    raise SystemExit(main())
