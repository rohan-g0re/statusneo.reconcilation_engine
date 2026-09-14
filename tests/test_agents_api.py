"""Tests for the agent layer's HTTP surface (``src/recon/agents/api.py``).

No network anywhere in this file. Every test that drives the Investigator or the Workflow
Coordinator injects a ``ScriptedClient`` -- the same "fake ``LLMClient`` returning scripted
responses, in order" pattern ``tests/test_agents_roles.py`` uses -- through
``agents_api.build_router``'s ``client_factory`` seam, so the model never leaves this process.

Two fixtures anchor everything: ``demo_settings``/``demo_client`` build one real, on-disk "demo"
dataset (same recipe as ``tests/test_api.py:31-38`` and ``tests/test_agents_tools.py``'s
``demo_settings``) so tool calls resolve against genuine data; ``_build_test_app`` wires
``agents_api.build_router`` into a bare ``FastAPI`` app with test-controlled
``agent_settings_factory``/``client_factory`` overrides, bypassing ``create_app`` entirely for
the tests that need a scripted client. One test (``test_router_is_actually_mounted_by_create_app``)
goes through the real ``create_app`` to prove the one authorised edit to ``app.py`` works.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

pytest.importorskip("fastapi", reason="the API layer is an optional extra: pip install -e '.[api]'")
pytest.importorskip("httpx", reason="the agent layer is an optional extra: pip install -e '.[agent]'")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from recon.agents import api as agents_api  # noqa: E402
from recon.agents.client import LLMError, LLMResponse, ToolCall  # noqa: E402
from recon.agents.config import load_agent_settings  # noqa: E402
from recon.agents.grounding import select_clauses  # noqa: E402
from recon.agents.rubric import applicable_judge_criteria, Outcome, Round  # noqa: E402
from recon.agents.schemas import ActionEnum, CriterionFinding, CriterionVerdict, ProposedAction  # noqa: E402
from recon.agents.scorers import ScoringInput  # noqa: E402
from recon.api import dossier as dossier_module  # noqa: E402
from recon.config import load_settings  # noqa: E402
from recon.db import connection as db_connection  # noqa: E402
from recon.db import repository  # noqa: E402

EPISODE_ID = "E-000001"


# ═══ a real dataset, built once ══════════════════════════════════════════════════════


@pytest.fixture(scope="module")
def demo_settings(tmp_path_factory):
    from recon.api.app import build_dataset

    data_dir = tmp_path_factory.mktemp("agents_api_demo")
    settings = load_settings("demo", data_dir=data_dir)
    build_dataset(settings)
    return settings


@pytest.fixture()
def demo_conn(demo_settings):
    handle = db_connection.connect(demo_settings.db_path)
    try:
        yield handle
    finally:
        handle.close()


# ═══ a scripted, no-network LLMClient ════════════════════════════════════════════════


class ScriptedClient:
    """Pops one scripted ``LLMResponse`` per call, in order. See
    ``tests/test_agents_roles.py``'s identical class for the rationale."""

    def __init__(self, responses: list[LLMResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def complete(self, *, agent, messages, model, tools=None, tool_choice=None,
                 max_tokens=None, temperature=None, iteration=None) -> LLMResponse:
        self.calls.append({"agent": agent, "model": model, "messages": messages})
        if not self._responses:
            raise AssertionError(f"ScriptedClient exhausted after {len(self.calls)} calls")
        return self._responses.pop(0)


def _tool_call_response(name: str, arguments: dict[str, Any], *, model: str, call_id: str = "call-1") -> LLMResponse:
    return LLMResponse(
        content=None,
        tool_calls=(ToolCall(id=call_id, name=name, arguments=arguments, raw_arguments=json.dumps(arguments, default=str)),),
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


# ═══ wiring the router without going through create_app ═════════════════════════════


def _build_test_app(settings=None, *, agent_settings_factory=None, client_factory=None) -> TestClient:
    app = FastAPI()
    if settings is not None:
        app.state.settings = settings
    app.include_router(
        agents_api.build_router(agent_settings_factory=agent_settings_factory, client_factory=client_factory)
    )
    return TestClient(app)


def _agent_settings(tmp_path: Path, **overrides: Any):
    base: dict[str, Any] = {
        "api_key": "test-key",
        "journal_dir": tmp_path / "runs",
        "fixture_dir": tmp_path / "fixtures",
    }
    base.update(overrides)
    return load_agent_settings(**base)


# ═══ /api/agent/explain ══════════════════════════════════════════════════════════════


def test_explain_happy_path_returns_report_citations_trace_and_tokens(demo_settings, tmp_path):
    agent_settings = _agent_settings(tmp_path)
    prose = (
        f"## What happened\n{EPISODE_ID} was retrieved as of the current cursor [[verdict:A-05]].\n\n"
        "## Why it is open\nThe verdict on record explains the disposition [[verdict:A-05]].\n\n"
        "## What I could not determine\n"
        "Nothing — every question this episode raises is answered by the records above.\n\n"
        "## What a human should check first\nConfirm the verdict code against the glossary provided."
    )
    scripted = ScriptedClient(
        [
            _tool_call_response("get_episode", {"episode_id": EPISODE_ID}, model="deepseek-chat"),
            _prose_response(prose, model="deepseek-chat"),
        ]
    )
    client = _build_test_app(
        demo_settings,
        agent_settings_factory=lambda: agent_settings,
        client_factory=lambda settings, journal: scripted,
    )

    resp = client.post(f"/api/agent/explain/{EPISODE_ID}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["episode_id"] == EPISODE_ID
    assert body["run_id"]
    report = body["report"]
    assert set(report) == {"what_happened", "why_it_is_open", "what_i_could_not_determine", "what_a_human_should_check_first"}
    assert body["citations"] == [{"kind": "verdict", "ref": "A-05"}, {"kind": "verdict", "ref": "A-05"}]
    assert body["unsourced_figures"] == []
    # ScriptedClient does not wrap a Journal the way OpenAICompatClient does, so no
    # llm_request/llm_response pair is ever journalled here and token_usage is empty --
    # this asserts the shape derive_state always returns, not a number this stub cannot produce.
    assert body["token_usage"] == {}
    assert len(body["tool_calls"]) == 1
    assert body["tool_calls"][0]["name"] == "get_episode"
    assert body["tool_calls"][0]["status"] == "ok"

    # The run is now inspectable over HTTP.
    run_resp = client.get(f"/api/agent/runs/{body['run_id']}")
    assert run_resp.status_code == 200
    run_body = run_resp.json()
    assert run_body["role"] == "exception_investigator"
    assert run_body["episode_id"] == EPISODE_ID
    assert len(run_body["tool_calls"]) == 1


def test_explain_episode_not_found_is_a_typed_200_not_an_error(demo_settings, tmp_path):
    agent_settings = _agent_settings(tmp_path)
    scripted = ScriptedClient(
        [_tool_call_response("get_episode", {"episode_id": "E-999999"}, model="deepseek-chat")]
    )
    client = _build_test_app(
        demo_settings,
        agent_settings_factory=lambda: agent_settings,
        client_factory=lambda settings, journal: scripted,
    )

    resp = client.post("/api/agent/explain/E-999999")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "episode_not_found"
    assert body["episode_id"] == "E-999999"


# ═══ unavailability: the extra missing, and no API key ══════════════════════════════


def test_agent_layer_unavailable_returns_503_with_actionable_message(monkeypatch):
    monkeypatch.setattr(agents_api, "_AGENT_LAYER_ERROR", "ModuleNotFoundError: No module named 'httpx'")
    client = _build_test_app()
    resp = client.get("/api/agent/work-items")
    assert resp.status_code == 503
    detail = resp.json()["detail"]
    assert "pip install" in detail
    assert "httpx" in detail


def test_no_api_key_returns_503_and_never_leaks_a_key(demo_settings, tmp_path):
    agent_settings = _agent_settings(tmp_path, api_key=None)
    client = _build_test_app(demo_settings, agent_settings_factory=lambda: agent_settings)

    resp = client.post(f"/api/agent/explain/{EPISODE_ID}")
    assert resp.status_code == 503
    detail = resp.json()["detail"]
    assert "RECON_AGENT_API_KEY" in detail
    assert "test-key" not in json.dumps(resp.json())  # nothing about a real key ever appears


def test_router_is_actually_mounted_by_create_app(demo_settings):
    """The one authorised edit to app.py, proven end to end: a plain FastAPI app built the
    normal way already serves /api/agent/* -- this hits an endpoint that needs no model call
    (so it works regardless of whether this machine happens to have a real API key set)."""
    from recon.api.app import create_app

    app_client = TestClient(create_app(demo_settings))
    resp = app_client.get("/api/agent/work-items")
    assert resp.status_code == 200
    assert resp.json() == {"work_items": []}


# ═══ /api/agent/portfolio ═════════════════════════════════════════════════════════════
#
# `run_analyst` (Coder 2's `recon.agents.roles.analyst`) is single-pass with tool calls,
# the same shape as `run_investigator` -- so these tests script it exactly the way
# `test_explain_happy_path_returns_report_citations_trace_and_tokens` above scripts the
# Investigator: one tool-call response naming a real, Coder-1-registered tool
# (`get_portfolio_overview`), then a prose response. Unlike the Explain tests, the happy
# path below does not assert the *content* the model wrote into each report section --
# that prose-to-`AnalystReport` parsing is Coder 2's own module and is pinned by
# `tests/test_agents_analyst.py`, not this file. What this file owns is the HTTP wiring:
# the four frozen `AnalystReport` section keys arrive intact, the tool-call trace names
# the real tool that ran, and the run is inspectable afterward -- exactly the same
# contract `explain` already proves, now for a portfolio-wide, episode-less run.


class ErrorClient:
    """Matches the `LLMClient` Protocol exactly (house rule: a stub looser than the
    Protocol is how a real bug shipped here before) and raises on the first call --
    scripting `_agent.client.LLMError` -> 503 without needing a live provider."""

    def complete(self, *, agent, messages, model, tools=None, tool_choice=None,
                 max_tokens=None, temperature=None, iteration=None) -> LLMResponse:
        raise LLMError("simulated provider stall", status=500, body="upstream exploded", attempts=3)


def test_portfolio_happy_path_returns_report_and_tool_call_trace(demo_settings, tmp_path):
    agent_settings = _agent_settings(tmp_path)
    prose = (
        "## State of the book\n"
        "As of the current cursor, the portfolio overview names the totals by disposition "
        "[[calc:total_variance_cents]].\n\n"
        "## What is concentrated\n"
        "The exception queue, already sorted by the tool's own order_by, surfaces which "
        "episodes carry the most money [[verdict:A-05]].\n\n"
        "## What I could not determine\n"
        "Nothing -- every question this summary raises is answered by the records above.\n\n"
        "## Where to look first\n"
        "Start with the first row of the variance-sorted exception queue."
    )
    scripted = ScriptedClient(
        [
            _tool_call_response("get_portfolio_overview", {}, model="deepseek-v4-pro"),
            _prose_response(prose, model="deepseek-v4-pro"),
        ]
    )
    client = _build_test_app(
        demo_settings,
        agent_settings_factory=lambda: agent_settings,
        client_factory=lambda settings, journal: scripted,
    )

    resp = client.post(
        "/api/agent/portfolio",
        json={"cursor": None, "question": "which reason code carries the most money?"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["run_id"]
    assert body["cursor"] == demo_settings.max_cursor
    report = body["report"]
    assert set(report) == {
        "state_of_the_book",
        "what_is_concentrated",
        "what_i_could_not_determine",
        "where_to_look_first",
    }
    assert isinstance(body["citations"], list)
    assert isinstance(body["unsourced_figures"], list)
    assert len(body["tool_calls"]) == 1
    assert body["tool_calls"][0]["name"] == "get_portfolio_overview"
    assert body["tool_calls"][0]["status"] == "ok"

    # Inspectable over HTTP afterward, same as an Explain run, and correctly labelled
    # with the role this endpoint runs under.
    run_resp = client.get(f"/api/agent/runs/{body['run_id']}")
    assert run_resp.status_code == 200
    run_body = run_resp.json()
    assert run_body["role"] == "portfolio_analyst"
    assert len(run_body["tool_calls"]) == 1


def test_portfolio_agent_layer_unavailable_returns_503(monkeypatch):
    monkeypatch.setattr(agents_api, "_AGENT_LAYER_ERROR", "ModuleNotFoundError: No module named 'httpx'")
    client = _build_test_app()
    resp = client.post("/api/agent/portfolio", json={"cursor": None, "question": None})
    assert resp.status_code == 503
    detail = resp.json()["detail"]
    assert "pip install" in detail
    assert "httpx" in detail


def test_portfolio_upstream_llm_error_is_a_503_naming_the_provider(demo_settings, tmp_path):
    agent_settings = _agent_settings(tmp_path)
    client = _build_test_app(
        demo_settings,
        agent_settings_factory=lambda: agent_settings,
        client_factory=lambda settings, journal: ErrorClient(),
    )

    resp = client.post("/api/agent/portfolio", json={"cursor": None, "question": None})
    assert resp.status_code == 503
    detail = resp.json()["detail"]
    # Says which side failed -- the same "upstream of the agent layer, deterministic
    # views unaffected" message `explain` gives, not a generic 500.
    assert "upstream of the agent layer" in detail
    assert "deterministic views" in detail
    assert "3 attempts" in detail


def test_portfolio_bad_cursor_is_a_422(demo_settings, tmp_path):
    agent_settings = _agent_settings(tmp_path)
    client = _build_test_app(
        demo_settings,
        agent_settings_factory=lambda: agent_settings,
        client_factory=lambda settings, journal: ScriptedClient([]),
    )

    resp = client.post("/api/agent/portfolio", json={"cursor": "not-a-real-cursor", "question": None})
    assert resp.status_code == 422


def test_portfolio_empty_body_defaults_cursor_and_question_to_null(demo_settings, tmp_path):
    """The frozen body shape is `{"cursor": str | null, "question": str | null}`, but a
    caller sending an empty object still gets a well-formed run at the max cursor with
    no question -- the same "missing means null" leniency the rest of this HTTP surface
    extends to an absent `cursor` query parameter."""
    agent_settings = _agent_settings(tmp_path)
    scripted = ScriptedClient(
        [
            _tool_call_response("get_portfolio_overview", {}, model="deepseek-v4-pro"),
            _prose_response("## State of the book\nNothing outstanding to report.", model="deepseek-v4-pro"),
        ]
    )
    client = _build_test_app(
        demo_settings,
        agent_settings_factory=lambda: agent_settings,
        client_factory=lambda settings, journal: scripted,
    )

    resp = client.post("/api/agent/portfolio", json={})
    assert resp.status_code == 200
    assert resp.json()["cursor"] == demo_settings.max_cursor


# ═══ /api/agent/decide (SSE) ═════════════════════════════════════════════════════════


def _applicable_judge_criteria_ids(dossier: dict[str, Any], proposal: ProposedAction) -> list[str]:
    """Exactly what ``coordinator._make_evaluate`` computes as its checklist, replicated
    here so the scripted ``emit_evaluation`` response names the right criteria -- a
    real evaluator's ``per_criterion`` must match this list exactly, in order, or the
    harness raises ``evaluator_structurally_invalid`` (``roles/coordinator.py``)."""
    clauses = select_clauses(dossier)
    prelim = ScoringInput(proposal=proposal, evaluator_verdict=None, tool_results=(), clauses=clauses, dossier=dossier)
    return [c.criterion_id for c in applicable_judge_criteria(prelim)]


def _evaluation_args(criterion_ids: list[str]) -> dict[str, Any]:
    return {
        "per_criterion": [
            {
                "criterion_id": cid,
                "reasoning": "Stub grading for a scripted test.",
                "cited_span": {"quote": "stub", "source_kind": "tool_result", "source_ref": "tool_result#1"},
                "verdict": "SUPPORTED",
            }
            for cid in criterion_ids
        ],
        "overall_reasoning": "Every checklist item is supported in this scripted evaluation.",
    }


_BLOCKED_PROPOSAL_ARGS: dict[str, Any] = {
    "reasoning": "As of the current cursor, the settlement record needed to confirm this claim does not exist in this dataset.",
    "evidence": [],
    "grounding_clause_id": None,
    "action": "ABSTAIN",
    "required_artifacts": [],
    "missing_evidence": ["the bank settlement record for this claim"],
    "blocked": True,
    "blocked_reason": "the settlement record does not exist in this dataset",
}


def _parse_sse(text: str) -> list[tuple[str, dict[str, Any]]]:
    events = []
    for frame in text.strip("\n").split("\n\n"):
        if not frame.strip():
            continue
        kind = None
        data = None
        for line in frame.splitlines():
            if line.startswith("event:"):
                kind = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data = json.loads(line[len("data:"):].strip())
        assert kind is not None and data is not None, f"malformed SSE frame: {frame!r}"
        events.append((kind, data))
    return events


def test_decide_streams_events_in_order_and_terminates_with_insufficient_data(demo_conn, demo_settings, tmp_path):
    cursor = demo_settings.max_cursor
    dossier = dossier_module.build_dossier(demo_conn, EPISODE_ID, cursor)
    proposal = ProposedAction.parse(_BLOCKED_PROPOSAL_ARGS)
    checklist_ids = _applicable_judge_criteria_ids(dossier, proposal)

    agent_settings = _agent_settings(tmp_path)
    scripted = ScriptedClient(
        [
            _tool_call_response("emit_proposed_action", _BLOCKED_PROPOSAL_ARGS, model=agent_settings.proposer_model),
            _tool_call_response("emit_evaluation", _evaluation_args(checklist_ids), model=agent_settings.evaluator_model),
        ]
    )
    client = _build_test_app(
        demo_settings,
        agent_settings_factory=lambda: agent_settings,
        client_factory=lambda settings, journal: scripted,
    )

    resp = client.post(f"/api/agent/decide/{EPISODE_ID}")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")

    events = _parse_sse(resp.text)
    assert events, "the stream produced no events at all"

    kinds = [kind for kind, _ in events]
    # Streamed in the order the harness actually journals them, ending in exactly one outcome.
    assert kinds[0] == "run_started"
    assert kinds[-1] == "outcome"
    assert kinds.count("outcome") == 1
    assert "proposal" in kinds
    assert "evaluation" in kinds
    assert "score" in kinds
    assert "gate" in kinds

    outcome_kind, outcome_data = events[-1]
    assert outcome_data["status"] == "insufficient_data"
    assert outcome_data["proposer_blocked"] is True
    assert outcome_data["blocked_reason"] == _BLOCKED_PROPOSAL_ARGS["blocked_reason"]
    assert outcome_data["proposal"]["action"] == "ABSTAIN"
    assert outcome_data["run_id"]

    # And the run this stream just drove is now readable back over HTTP, journal intact.
    run_resp = client.get(f"/api/agent/runs/{outcome_data['run_id']}")
    assert run_resp.status_code == 200
    assert run_resp.json()["outcome"] == "insufficient_data"


def test_decide_episode_not_found_emits_a_single_terminal_event(demo_settings, tmp_path):
    agent_settings = _agent_settings(tmp_path)
    client = _build_test_app(
        demo_settings,
        agent_settings_factory=lambda: agent_settings,
        client_factory=lambda settings, journal: ScriptedClient([]),
    )

    resp = client.post("/api/agent/decide/E-999999")
    assert resp.status_code == 200
    events = _parse_sse(resp.text)
    assert len(events) == 1
    kind, data = events[0]
    assert kind == "outcome"
    assert data["status"] == "episode_not_found"


# ═══ all four outcome kinds render distinctly (direct unit test of the serializer) ═══


def _fake_proposal() -> ProposedAction:
    return ProposedAction(
        reasoning="test", evidence=(), grounding_clause_id=None, action=ActionEnum.ABSTAIN,
        required_artifacts=(), missing_evidence=("x",), blocked=False, blocked_reason=None,
    )


def _fake_finding(cid: str = "G4_figures_are_labelled_correctly") -> CriterionFinding:
    return CriterionFinding(criterion_id=cid, reasoning="ok", cited_span=None, verdict=CriterionVerdict.SUPPORTED)


def test_outcome_payload_renders_all_four_kinds_distinctly():
    round_ = Round(index=0, proposal=_fake_proposal(), findings={"G4_figures_are_labelled_correctly": _fake_finding()}, score=85.0)

    complete = agents_api._outcome_payload(Outcome.complete((round_,)))
    insufficient = agents_api._outcome_payload(
        Outcome.insufficient_data((round_,), missing=("G13_abstains_when_insufficient",))
    )
    stalled = agents_api._outcome_payload(Outcome.stalled((round_, round_, round_)))
    capped = agents_api._outcome_payload(Outcome.capped((round_,)))

    statuses = {complete["status"], insufficient["status"], stalled["status"], capped["status"]}
    assert statuses == {"complete", "insufficient_data", "stalled", "capped"}, "all four kinds must be distinct"

    assert insufficient["missing_criteria"] == ["G13_abstains_when_insufficient"]
    assert insufficient["missing_narrative"] is not None
    assert capped["budget_note"] is not None
    assert stalled["unmet_criteria"] == []  # the one finding scripted above is SUPPORTED
    for payload in (complete, insufficient, stalled, capped):
        assert "proposal" in payload and payload["proposal"]["action"] == "ABSTAIN"
        assert "findings" in payload and set(payload["findings"]) == {"G4_figures_are_labelled_correctly"}


# ═══ /api/agent/work-item: the human gate, idempotent, and the diff ═════════════════


def _current_verdict_id(conn, episode_id: str, cursor: str) -> int:
    verdict = repository.latest_verdict(conn, episode_id, cursor, with_children=False)
    assert verdict is not None, f"{episode_id} has no verdict at {cursor}; pick a different fixture episode"
    return verdict.verdict_id


def test_work_item_create_is_idempotent_and_records_the_proposed_accepted_diff(demo_conn, demo_settings, tmp_path):
    agent_settings = _agent_settings(tmp_path)
    client = _build_test_app(demo_settings, agent_settings_factory=lambda: agent_settings)
    verdict_id = _current_verdict_id(demo_conn, EPISODE_ID, demo_settings.max_cursor)

    body = {
        "episode_id": EPISODE_ID,
        "from_verdict_id": verdict_id,
        "recommended_action": "ESCALATE",
        "summary": "The reviewer escalated this to a specialist after editing the draft.",
        "required_artifacts": ["form 1234"],
        "proposed": {
            "recommended_action": "APPEAL",
            "summary": "The agent proposed an appeal based on the remittance denial reason.",
            "required_artifacts": [],
        },
        "run_id": "some-run-id",
    }

    first = client.post("/api/agent/work-item", json=body)
    assert first.status_code == 200
    first_data = first.json()
    assert first_data["status"] == "ok"
    assert first_data["created"] is True
    assert first_data["dry_run"] is False
    work_item_id = first_data["work_item_id"]
    assert work_item_id is not None

    diff = first_data["diff"]
    assert diff["has_proposed"] is True
    assert set(diff["changed_fields"]) == {"recommended_action", "summary", "required_artifacts"}
    assert diff["fields"]["recommended_action"] == {"proposed": "APPEAL", "accepted": "ESCALATE"}

    # The diff landed in its own durable log, not discarded.
    log_path = agent_settings.journal_dir / "work_item_decisions.jsonl"
    assert log_path.exists()
    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    recorded = json.loads(lines[0])
    assert recorded["episode_id"] == EPISODE_ID
    assert recorded["diff"]["changed_fields"] == diff["changed_fields"]
    assert recorded["work_item_id"] == work_item_id

    # Same episode/verdict/action again: idempotent, no second row, no second log line required
    # to prove idempotency -- the tool's own response says so.
    second = client.post("/api/agent/work-item", json=body)
    assert second.status_code == 200
    second_data = second.json()
    assert second_data["created"] is False
    assert second_data["work_item_id"] == work_item_id

    listed = client.get(f"/api/agent/work-items/{EPISODE_ID}").json()
    assert len(listed["work_items"]) == 1
    assert listed["work_items"][0]["work_item_id"] == work_item_id
    assert listed["work_items"][0]["required_artifacts"] == ["form 1234"]

    all_items = client.get("/api/agent/work-items").json()
    assert any(item["work_item_id"] == work_item_id for item in all_items["work_items"])


def test_work_item_refuses_a_token_that_does_not_match_the_draft(demo_conn, demo_settings, tmp_path):
    agent_settings = _agent_settings(tmp_path)
    client = _build_test_app(demo_settings, agent_settings_factory=lambda: agent_settings)
    verdict_id = _current_verdict_id(demo_conn, "E-000002", demo_settings.max_cursor)

    stale_token = agents_api._agent.tools.mint_write_token(
        episode_id="E-000002", from_verdict_id=verdict_id,
        recommended_action="RESUBMIT", summary="a completely different draft that was never shown",
    )

    resp = client.post(
        "/api/agent/work-item",
        json={
            "episode_id": "E-000002",
            "from_verdict_id": verdict_id,
            "recommended_action": "WRITE_OFF",
            "summary": "This draft does not match the summary the stale token was minted for.",
            "write_token": stale_token,
        },
    )
    assert resp.status_code == 409
    assert "does not authorise this exact draft" in resp.json()["detail"]

    # Nothing was written.
    listed = client.get("/api/agent/work-items/E-000002").json()
    assert listed["work_items"] == []


def test_work_item_missing_fields_is_a_422(demo_settings, tmp_path):
    agent_settings = _agent_settings(tmp_path)
    client = _build_test_app(demo_settings, agent_settings_factory=lambda: agent_settings)
    resp = client.post("/api/agent/work-item", json={"episode_id": EPISODE_ID})
    assert resp.status_code == 422
