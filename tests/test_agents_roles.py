"""Tests for `recon.agents.roles.investigator` and `recon.agents.roles.coordinator`.

No network anywhere in this file. `ScriptedClient` is a fake `LLMClient` (matching the
`Protocol` in `client.py`) that returns one scripted `LLMResponse` per call, in order,
and records every call's keyword arguments -- the same "stub LLMClient returning
scripted responses" pattern `tests/test_agents_transport.py` uses for `client.py`
itself, applied here to the two roles that actually build messages and call it.

Both roles under test accept an injected `client: LLMClient` (never construct one), so
every scenario below -- forced tool-use on a forcible model, `tool_choice: "auto"` plus
one repair turn on a model that returns HTTP 400 on a forced choice, a hard fail on the
first `not_found`, a figure that fails to source and gets one repair attempt -- runs
with no API key and no process boundary.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from recon.agents.client import LLMResponse, ToolCall
from recon.agents.config import load_agent_settings
from recon.agents.harness import HarnessContext
from recon.agents.journal import Journal
from recon.agents.rubric import CRITERIA
from recon.agents.schemas import SchemaError
from recon.agents.scorers import ScoringInput
from recon.agents.tools import CallLog, ToolContext
from recon.agents.roles import coordinator, investigator

# ═══ shared fixtures ═════════════════════════════════════════════════════════════


def _clock():
    ticks = iter(range(100_000))

    def _now() -> str:
        n = next(ticks)
        return f"2026-01-01T{n // 3600:02d}:{(n // 60) % 60:02d}:{n % 60:02d}Z"

    return _now


#: Same convention as `tests/test_agents_harness.py` / `tests/test_agents_tools.py`:
#: journals opened by the helpers below are closed by this autouse fixture, so an
#: unclosed file's `ResourceWarning` (fatal under `filterwarnings = ["error"]`) is
#: self-correcting rather than a per-test chore.
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


def _make_tool_ctx(conn: sqlite3.Connection, journal: Journal, *, run_id: str = "run") -> ToolContext:
    return ToolContext(
        conn=conn,
        cursor="2026-07-01T23:59:59Z",
        role="workflow_coordinator",
        model_id="deepseek-chat",
        run_id=run_id,
        now="2026-01-01T00:00:00Z",
        nonce="9f2c41ab",
        write_token=None,
        call_log=CallLog(),
        journal=journal,
    )


class ScriptedClient:
    """A fake `LLMClient`: pops one scripted `LLMResponse` per call, in order, and
    records every call's keyword arguments. Raises if a test scripts too few
    responses, rather than silently returning `None`."""

    def __init__(self, responses: list[LLMResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def complete(self, *, agent, messages, model, tools=None, tool_choice=None,
                 max_tokens=None, temperature=None, iteration=None) -> LLMResponse:
        self.calls.append(
            {"agent": agent, "iteration": iteration, "messages": messages, "model": model,
             "tools": tools, "tool_choice": tool_choice,
             "max_tokens": max_tokens, "temperature": temperature}
        )
        if not self._responses:
            raise AssertionError(f"ScriptedClient exhausted after {len(self.calls)} calls; the test scripted too few responses")
        return self._responses.pop(0)


def _tool_call_response(name: str, arguments: dict[str, Any], *, model: str, call_id: str = "call-1") -> LLMResponse:
    raw = json.dumps(arguments, default=str)
    return LLMResponse(
        content=None,
        tool_calls=(ToolCall(id=call_id, name=name, arguments=arguments, raw_arguments=raw),),
        reasoning=None,
        finish_reason="tool_calls",
        model=model,
        usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        raw={},
    )


def _prose_response(content: str, *, model: str) -> LLMResponse:
    return LLMResponse(
        content=content, tool_calls=(), reasoning=None, finish_reason="stop", model=model,
        usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}, raw={},
    )


CURSOR = "2026-07-01T23:59:59Z"


def _dossier(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "episode_id": "E-000812",
        "cursor": CURSOR,
        "identity": {"track": "PHARMACY"},
        "current": {
            "episode_disposition": "EXCEPTION",
            "reason_codes": ["INSUFFICIENT_DATA"],
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


def _proposed_action_args(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "reasoning": "The bank record that would settle this is not present as of the cursor.",
        "evidence": [],
        "grounding_clause_id": None,
        "action": "ABSTAIN",
        "required_artifacts": [],
        "missing_evidence": ["the bank record for TRN02 8827441 -- not present in the bank feed as of the cursor"],
        "blocked": True,
        "blocked_reason": "no bank record exists that could settle this; no tool will produce one.",
    }
    base.update(overrides)
    return base


def _judge_applicable_ids(dossier: dict[str, Any], proposal_action_args: dict[str, Any]) -> list[str]:
    """What `coordinator._make_evaluate` will ask, computed the same way it does --
    `weight > 0.0` excludes `G17` for the reason documented in `coordinator.py` (see
    this coder's report: `rubric.run_judge_criteria`/`run_tracked_criteria` both read
    `EvaluatorVerdict.per_criterion` unconditionally, so a verdict grading `G17` too
    would collide in `rubric.merge_findings`)."""
    from recon.agents.schemas import ProposedAction

    proposal = ProposedAction.parse(proposal_action_args)
    prelim = ScoringInput(proposal=proposal, evaluator_verdict=None, tool_results=(), clauses=(), dossier=dossier)
    return [c.criterion_id for c in CRITERIA if c.grader == "judge" and c.weight > 0.0 and c.applies_when(prelim)]


def _evaluator_verdict_args(criterion_ids: list[str], verdict: str = "SUPPORTED") -> dict[str, Any]:
    return {
        "per_criterion": [
            {
                "criterion_id": cid,
                "reasoning": f"test finding for {cid}",
                "cited_span": None if verdict == "NOT_ADDRESSED" else {"quote": "x", "source_kind": "tool_result", "source_ref": "tool_result#1"},
                "verdict": verdict,
            }
            for cid in criterion_ids
        ],
        "overall_reasoning": "test overall reasoning",
    }


# ═══ 1. forced tool-use on the flash/proposer model ═════════════════════════════


def test_emit_structured_forces_tool_choice_on_a_model_that_supports_it(tmp_path):
    args = _proposed_action_args()
    client = ScriptedClient([_tool_call_response("emit_proposed_action", args, model="deepseek-chat")])
    journal = _journal(tmp_path)

    call = coordinator._emit_structured(
        client=client, model="deepseek-chat", messages=[{"role": "user", "content": "propose"}],
        tool_spec=coordinator.EMIT_PROPOSED_ACTION_TOOL, forced_name="emit_proposed_action",
        repair_message="unused on a forcible model", journal=journal, agent="proposer", iteration=0,
    )

    assert call.name == "emit_proposed_action"
    assert len(client.calls) == 1
    assert client.calls[0]["tool_choice"] == {"type": "function", "function": {"name": "emit_proposed_action"}}


def test_the_proposer_session_uses_forced_tool_use_on_deepseek_chat(tmp_path):
    """The `_ProposerSession`'s own final call -- not just the shared `_emit_structured`
    helper in isolation -- is forced, once its read-tool rounds decide there is nothing
    more to gather."""
    args = _proposed_action_args(
        action="ESCALATE",
        evidence=[{"quote": "x", "source_kind": "tool_result", "source_ref": "tool_result#1"}],
        required_artifacts=["Escalate to compliance review"], blocked=False, blocked_reason=None, missing_evidence=[],
    )
    client = ScriptedClient(
        [
            _prose_response("I have everything I need.", model="deepseek-chat"),  # round 0: no tool calls -> break
            _tool_call_response("emit_proposed_action", args, model="deepseek-chat"),  # forced emit
        ]
    )
    journal = _journal(tmp_path)
    tool_ctx = _make_tool_ctx(sqlite3.connect(":memory:"), journal)
    session = coordinator._ProposerSession(
        client=client, model="deepseek-chat", tool_ctx=tool_ctx, episode_id="E-000812", cursor=CURSOR,
        verdict_glossary="B-06: denied", grounding_clause_index="(none in this fixture)", journal=journal,
        max_calls_per_iteration=8,
    )
    ctx = HarnessContext(dossier=_dossier(), clauses=())

    proposal = session(ctx, None, 0)

    assert proposal.action.value == "ESCALATE"
    assert len(client.calls) == 2
    assert client.calls[0]["tool_choice"] == "auto"
    assert client.calls[1]["tool_choice"] == {"type": "function", "function": {"name": "emit_proposed_action"}}


# ═══ 2. auto + one repair turn on the non-forcible evaluator model ══════════════


def test_emit_structured_uses_auto_then_one_repair_turn_on_a_model_that_cannot_be_forced(tmp_path):
    args = _proposed_action_args()
    client = ScriptedClient(
        [
            _prose_response("Here is my evaluation, written out as prose.", model="deepseek-v4-pro"),
            _tool_call_response("emit_evaluation", {"per_criterion": [], "overall_reasoning": "x"}, model="deepseek-v4-pro"),
        ]
    )
    journal = _journal(tmp_path)

    call = coordinator._emit_structured(
        client=client, model="deepseek-v4-pro", messages=[{"role": "user", "content": "evaluate"}],
        tool_spec=coordinator.EMIT_EVALUATION_TOOL, forced_name="emit_evaluation",
        repair_message="Transcribe what you already wrote.", journal=journal, agent="evaluator", iteration=0,
    )

    assert call.name == "emit_evaluation"
    assert len(client.calls) == 2
    assert client.calls[0]["tool_choice"] == "auto"
    # spec_prompts_roles.md S:C.4: the repair turn runs at temperature 0 regardless of
    # the first call's temperature (which this test leaves unset on the first call).
    assert client.calls[1]["tool_choice"] == "auto"
    assert client.calls[1]["temperature"] == 0.0
    assert client.calls[0]["model"] == client.calls[1]["model"] == "deepseek-v4-pro"


def test_a_prose_only_reply_triggers_exactly_one_repair_turn_then_succeeds(tmp_path):
    """Isolates the "exactly one" part: a second prose-only reply on the repair turn
    must raise rather than trying a third time (spec_prompts_roles.md S:C.4: "One
    repair turn ... A second failure ends the run.")."""
    client = ScriptedClient(
        [
            _prose_response("prose, no tool call", model="deepseek-v4-pro"),
            _prose_response("still prose, no tool call", model="deepseek-v4-pro"),
        ]
    )
    journal = _journal(tmp_path)

    with pytest.raises(SchemaError, match="evaluator_no_tool_call"):
        coordinator._emit_structured(
            client=client, model="deepseek-v4-pro", messages=[{"role": "user", "content": "evaluate"}],
            tool_spec=coordinator.EMIT_EVALUATION_TOOL, forced_name="emit_evaluation",
            repair_message="Transcribe what you already wrote.", journal=journal, agent="evaluator", iteration=0,
        )
    assert len(client.calls) == 2  # exactly one repair turn, never a second


def test_the_evaluate_closure_never_forces_tool_choice_and_excludes_the_proposers_reasoning(tmp_path):
    dossier = _dossier()
    proposal_args = _proposed_action_args(
        reasoning="ESCALATION_REASONING_SENTINEL applies to this episode.",
        action="ABSTAIN",
    )
    from recon.agents.schemas import ProposedAction

    proposal = ProposedAction.parse(proposal_args)
    expected_ids = _judge_applicable_ids(dossier, proposal_args)
    verdict_args = _evaluator_verdict_args(expected_ids)

    client = ScriptedClient(
        [
            _prose_response("here is my evaluation in prose", model="deepseek-v4-pro"),
            _tool_call_response("emit_evaluation", verdict_args, model="deepseek-v4-pro"),
        ]
    )
    journal = _journal(tmp_path)
    evaluate = coordinator._make_evaluate(client=client, model="deepseek-v4-pro", journal=journal, fence_nonce="9f2c41ab")
    ctx = HarnessContext(dossier=dossier, clauses=())

    verdict = evaluate(ctx, proposal, 0)

    assert [f.criterion_id for f in verdict.per_criterion] == expected_ids
    assert len(client.calls) == 2
    assert all(call["tool_choice"] == "auto" for call in client.calls)
    for call in client.calls:
        assert "ESCALATION_REASONING_SENTINEL" not in json.dumps(call["messages"], default=str)


# ═══ 3. a work item is proposed, never written, by the loop itself ═════════════


def test_a_full_coordinator_run_never_offers_or_calls_the_write_tool(tmp_path):
    dossier = _dossier()  # INSUFFICIENT_DATA on this episode -- the honest, expected shape for an ABSTAIN/blocked proposal
    proposal_args = _proposed_action_args()  # ABSTAIN, blocked=True -- see _proposed_action_args' own defaults
    expected_ids = _judge_applicable_ids(dossier, proposal_args)
    verdict_args = _evaluator_verdict_args(expected_ids)

    client = ScriptedClient(
        [
            _prose_response("nothing further to check", model="deepseek-chat"),  # proposer round loop: no tool calls
            _tool_call_response("emit_proposed_action", proposal_args, model="deepseek-chat"),  # forced emit
            _tool_call_response("emit_evaluation", verdict_args, model="deepseek-v4-pro"),  # evaluator, first try
        ]
    )
    journal = _journal(tmp_path)
    tool_ctx = _make_tool_ctx(sqlite3.connect(":memory:"), journal)
    settings = load_agent_settings(mode="replay", api_key=None)

    outcome = coordinator.run_coordinator(
        client=client, tool_ctx=tool_ctx, settings=settings, dossier=dossier,
        verdict_glossary="INSUFFICIENT_DATA: money moved that no record accounts for",
        grounding_clause_index="(rendered by select_clauses)", journal=journal,
    )

    assert outcome.kind == "insufficient_data"
    assert outcome.proposer_blocked is True

    offered_names = {t["function"]["name"] for call in client.calls for t in (call["tools"] or [])}
    assert "create_mock_work_item" not in offered_names, "the write tool must never even be offered to a model"
    assert not any(
        ev.kind == "tool_call" and ev.fields.get("name") == "create_mock_work_item" for ev in journal.events
    ), "the write tool must never be called by the loop itself"


# ═══ 4. the Investigator: citation grammar ═══════════════════════════════════════


def test_extract_citations_matches_the_grammar_for_all_four_token_kinds():
    prose = (
        "The remittance states a payment [[raw:48213]], recorded as [[event:2]] on the "
        "timeline. The shortfall is [[calc:reimbursement_variance_cents]], carried under "
        "verdict [[verdict:A-05]]."
    )
    citations = investigator._extract_citations(prose)
    assert [(c.kind.value, c.ref) for c in citations] == [
        ("raw", "48213"),
        ("event", "2"),
        ("calc", "reimbursement_variance_cents"),
        ("verdict", "A-05"),
    ]


def test_an_unparseable_bracket_form_is_not_extracted_as_a_citation():
    prose = "See [[foo:bar]] and (see the remittance) but not a real token."
    assert investigator._extract_citations(prose) == ()


def test_verified_citations_drops_an_unverifiable_raw_id_and_an_out_of_range_event_index():
    """The investigator never invents an identifier: a `raw:` token pointing at a
    raw_id this pass never actually saw, and an `event:` index outside the dossier's
    own timeline length, are both dropped rather than trusted."""
    prose = "See [[raw:999]] and [[event:5]] and also [[raw:111]] and [[event:1]]."
    verified = investigator._verified_citations(prose, seen_raw_ids={"111"}, dossier_len=3)
    assert [(c.kind.value, c.ref) for c in verified] == [("raw", "111"), ("event", "1")]


# ═══ 5. the Investigator: hard fail, closed ══════════════════════════════════════


def test_run_investigator_hard_fails_closed_when_the_first_get_episode_call_is_not_found(tmp_path, conn):
    client = ScriptedClient([_tool_call_response("get_episode", {"episode_id": "E-999999"}, model="deepseek-chat")])
    journal = _journal(tmp_path)
    tool_ctx = _make_tool_ctx(conn, journal)

    outcome = investigator.run_investigator(
        client=client, tool_ctx=tool_ctx, model="deepseek-chat", episode_id="E-999999", cursor=CURSOR, verdict_glossary="",
    )

    assert outcome.typed_reason == "episode_not_found"
    assert outcome.report is None
    assert len(client.calls) == 1  # no narrated guess: the pass ends immediately, no further model calls


# ═══ 6. the Investigator: an unsourced figure gets exactly one repair attempt ═══


_REPORT_TEMPLATE = (
    "## What happened\n{body}\n\n"
    "## Why it is open\nBecause the record says so.\n\n"
    "## What I could not determine\n"
    "Nothing -- every question this episode raises is answered by the records above.\n\n"
    "## What a human should check first\nCheck the remittance.\n"
)


def test_run_investigator_repairs_an_unsourced_figure_once_then_succeeds(tmp_path, conn):
    client = ScriptedClient(
        [
            _tool_call_response("get_raw_record", {"raw_id": 1}, model="deepseek-chat"),  # round 0: one call, not_found on an empty db
            _prose_response(_REPORT_TEMPLATE.format(body="The claim shows a shortfall of 50000 cents."), model="deepseek-chat"),  # round 1: fabricated figure
            _prose_response(_REPORT_TEMPLATE.format(body="The claim shows a shortfall not yet quantified by any tool result."), model="deepseek-chat"),  # repair
        ]
    )
    journal = _journal(tmp_path)
    tool_ctx = _make_tool_ctx(conn, journal)

    outcome = investigator.run_investigator(
        client=client, tool_ctx=tool_ctx, model="deepseek-chat", episode_id="E-000812", cursor=CURSOR, verdict_glossary="",
    )

    assert outcome.report is not None
    assert outcome.unsourced_figures == ()
    assert "50000" not in outcome.report.what_happened
    assert len(client.calls) == 3  # gather, draft, one repair -- never a second


def test_run_investigator_never_silently_drops_a_figure_that_survives_the_repair_turn(tmp_path, conn):
    client = ScriptedClient(
        [
            _tool_call_response("get_raw_record", {"raw_id": 1}, model="deepseek-chat"),
            _prose_response(_REPORT_TEMPLATE.format(body="The shortfall is 50000 cents."), model="deepseek-chat"),
            _prose_response(_REPORT_TEMPLATE.format(body="The shortfall is 50000 cents, confirmed."), model="deepseek-chat"),
        ]
    )
    journal = _journal(tmp_path)
    tool_ctx = _make_tool_ctx(conn, journal)

    outcome = investigator.run_investigator(
        client=client, tool_ctx=tool_ctx, model="deepseek-chat", episode_id="E-000812", cursor=CURSOR, verdict_glossary="",
    )

    assert outcome.unsourced_figures == ("50000",)
    assert "~~50000~~" in outcome.report.what_happened  # struck through, never silently removed
    # Its own event kind, not a field on an llm_response: this is the line a reviewer
    # auditing "the agent never computes a number" greps for.
    unsourced_events = [e for e in journal.events if e.kind == "unsourced_figure"]
    assert unsourced_events, "a figure that survives the repair turn must still be journalled"
