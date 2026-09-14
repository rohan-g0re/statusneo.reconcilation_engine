"""Tests for `recon.agents.roles.analyst` and `recon.agents.prompts.analyst`.

No network anywhere in this file. `ScriptedClient` is the exact same fake `LLMClient`
`tests/test_agents_roles.py` uses for the Investigator and the Coordinator -- a stub
that matches the `LLMClient` `Protocol` in `client.py` exactly, keyword-for-keyword.
`.agents/specs/spec_analyst.md`'s own house rule is explicit about why this matters:
"Test doubles must match the `LLMClient` Protocol exactly. A stub looser than the
Protocol is how a real bug shipped here before" -- the roles once called
`client.complete()` without the `agent` keyword the real client had just made required,
and every test stayed green because every stub's `complete()` accepted whatever it was
given. Redefined here (rather than imported from `test_agents_roles.py`) so this file
has no dependency on another coder's test module.

`FakeDispatch` plays the same role for `recon.agents.tools.dispatch`: `get_portfolio_
overview` and `get_exception_queue` are Agent 1's tools, landed in `tools.py` by the
time this file runs, but this suite does not drive them through a real seeded
database. Monkeypatching `analyst.dispatch` with a scripted, order-controlled stub is
what makes "construct a tool result whose order differs from any natural sort" (this
role's central grading criterion) a two-line fixture instead of a database seeding
exercise, and it keeps this file's tests independent of exactly how Agent 1's SQL is
written -- `tests/test_agents_tools.py` is where that gets exercised for real. What
this file's `test_every_tool_named_in_the_analyst_prompt_is_a_tool_that_exists` does
still check against the real, live `tools.tool_names()` registry, which is the
integration point that actually matters for a prompt: that every tool name it tells
the model to call is one the model can really call.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from recon.agents.client import LLMResponse, ToolCall
from recon.agents.envelope import ok
from recon.agents.journal import Journal
from recon.agents.prompts import _shared
from recon.agents.prompts import analyst as analyst_prompt
from recon.agents.roles import analyst as analyst_role
from recon.agents.schemas import AnalystReport, CitationKind
from recon.agents.tools import CallLog, ToolContext, tool_names

CURSOR = "2026-07-01T23:59:59Z"

# ═══ shared fixtures -- same pattern as tests/test_agents_roles.py ═══════════════


def _clock():
    ticks = iter(range(100_000))

    def _now() -> str:
        n = next(ticks)
        return f"2026-01-01T{n // 3600:02d}:{(n // 60) % 60:02d}:{n % 60:02d}Z"

    return _now


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


def _make_tool_ctx(journal: Journal) -> ToolContext:
    """No real schema needed -- `dispatch` is monkeypatched in every test below, so
    `conn` is never actually queried; it exists only to satisfy `ToolContext`'s type."""
    return ToolContext(
        conn=sqlite3.connect(":memory:"),
        cursor=CURSOR,
        role="analyst",
        model_id="deepseek-chat",
        run_id="run",
        now="2026-01-01T00:00:00Z",
        nonce="9f2c41ab",
        write_token=None,
        call_log=CallLog(),
        journal=journal,
    )


class ScriptedClient:
    """A fake `LLMClient`: pops one scripted `LLMResponse` per call, in order, and
    records every call's keyword arguments. Raises if a test scripts too few
    responses, rather than silently returning `None`. Identical to
    `test_agents_roles.ScriptedClient` -- see this module's docstring for why it is
    redefined here rather than imported."""

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
    import json

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


class FakeDispatch:
    """Replaces `recon.agents.roles.analyst.dispatch` for one test.

    Returns a scripted `ToolEnvelope` per tool name (each name is called at most once
    per scenario in this file) and records every call's name/arguments/iteration --
    which is what lets a test assert on the exact arguments the role passed through,
    e.g. that `order_by` was explicit rather than silently omitted.
    """

    def __init__(self, envelopes: dict[str, Any]) -> None:
        self._envelopes = envelopes
        self.calls: list[dict[str, Any]] = []

    def __call__(self, ctx: ToolContext, name: str, arguments: dict[str, Any], *, iteration: int = 0) -> Any:
        self.calls.append({"name": name, "arguments": arguments, "iteration": iteration})
        if name not in self._envelopes:
            raise AssertionError(f"FakeDispatch has no scripted envelope for {name!r}")
        return self._envelopes[name]


# ═══ tool-result fixtures, shaped like the real tools.py envelopes ═══════════════


def _overview_envelope(**overrides: Any) -> Any:
    data: dict[str, Any] = {
        "_untrusted_nonce": "9f2c41ab",
        "cursor": CURSOR,
        "by_disposition": {
            "EXCEPTION": {
                "episodes": 5, "expected_cents": 500000, "received_cents": 100000,
                "variance_cents": 400000, "reopened": 1,
                "expected_usd": "$5,000.00", "received_usd": "$1,000.00", "variance_usd": "$4,000.00",
            },
        },
        "verdict_pairs": [],
        "reason_codes": [],
        "cross_track_flags": [],
        "engine_version": "test",
    }
    data.update(overrides)
    return ok(data)


def _queue_row(episode_id: str, absolute_variance_cents: int) -> dict[str, Any]:
    usd = f"${absolute_variance_cents / 100:,.2f}"
    return {
        "episode_id": episode_id,
        "track": "PHARMACY",
        "date_of_service": "2026-06-01",
        "ndc11": None,
        "age_days": 30,
        "disposition": "EXCEPTION",
        "reimbursement_verdict": "A-05",
        "rebate_verdict": "C-00",
        "reimbursement_variance_cents": -absolute_variance_cents,
        "rebate_variance_cents": 0,
        "total_variance_cents": -absolute_variance_cents,
        "absolute_variance_cents": absolute_variance_cents,
        "reimbursement_variance_usd": f"-{usd}",
        "rebate_variance_usd": "$0.00",
        "total_variance_usd": f"-{usd}",
        "absolute_variance_usd": usd,
        "reopened_from": None,
        "reason_codes": ["A-05"],
    }


def _queue_envelope(rows: list[dict[str, Any]], *, order_by: str = "variance_desc") -> Any:
    data = {
        "_untrusted_nonce": "9f2c41ab",
        "cursor": CURSOR,
        "disposition": "EXCEPTION",
        "order_by": order_by,
        "limit": 10,
        "count": len(rows),
        "episodes": rows,
    }
    return ok(data)


_REPORT_TEMPLATE = (
    "## State of the book\nThe book stands as get_portfolio_overview described.\n\n"
    "## What is concentrated\n{concentrated}\n\n"
    "## What I could not determine\n"
    "Nothing -- every question this portfolio raises is answered by the records above.\n\n"
    "## Where to look first\nCheck the leading row first.\n"
)


def _run(client: ScriptedClient, tool_ctx: ToolContext, *, question: str | None = None):
    return analyst_role.run_analyst(
        client=client, tool_ctx=tool_ctx, model="deepseek-chat", cursor=CURSOR,
        question=question, verdict_glossary="A-05: partially covered by benefit",
    )


# ═══ 1. the deterministic-boundary constraint: a sort, never a judgement ═════════


def test_run_analyst_calls_get_exception_queue_with_an_explicit_order_by(tmp_path, monkeypatch):
    fake = FakeDispatch({
        "get_portfolio_overview": _overview_envelope(),
        "get_exception_queue": _queue_envelope([_queue_row("E-000100", 50000)]),
    })
    monkeypatch.setattr(analyst_role, "dispatch", fake)

    report_text = _REPORT_TEMPLATE.format(
        concentrated="Sorted by variance_desc, E-000100 leads [[queue:E-000100]]."
    )
    client = ScriptedClient([
        _tool_call_response("get_portfolio_overview", {}, model="deepseek-chat"),
        _tool_call_response("get_exception_queue", {"order_by": "variance_desc"}, model="deepseek-chat"),
        _prose_response(report_text, model="deepseek-chat"),
    ])
    tool_ctx = _make_tool_ctx(_journal(tmp_path))

    outcome = _run(client, tool_ctx)

    assert outcome.report is not None
    queue_calls = [c for c in fake.calls if c["name"] == "get_exception_queue"]
    assert len(queue_calls) == 1
    assert queue_calls[0]["arguments"].get("order_by") == "variance_desc", (
        "the role must pass order_by through explicitly, never silently drop it and "
        "let the tool's own default stand in unnamed"
    )


def test_run_analyst_never_reorders_the_rows_get_exception_queue_returned(tmp_path, monkeypatch):
    """The central grading criterion, made concrete: a tool result whose row order is
    neither ascending nor descending by episode_id or by variance -- E-000300 (100.00),
    E-000100 (900.00), E-000200 (500.00) -- and the assertion that nothing in the role
    resorts it, either in the citations it verifies or in the prose text a reviewer
    actually reads."""
    rows = [_queue_row("E-000300", 10000), _queue_row("E-000100", 90000), _queue_row("E-000200", 50000)]
    fake = FakeDispatch({
        "get_portfolio_overview": _overview_envelope(),
        "get_exception_queue": _queue_envelope(rows),
    })
    monkeypatch.setattr(analyst_role, "dispatch", fake)

    concentrated = (
        "Sorted by variance_desc: E-000300 leads [[queue:E-000300]], then E-000100 "
        "[[queue:E-000100]], then E-000200 [[queue:E-000200]]."
    )
    client = ScriptedClient([
        _tool_call_response("get_portfolio_overview", {}, model="deepseek-chat"),
        _tool_call_response("get_exception_queue", {"order_by": "variance_desc"}, model="deepseek-chat"),
        _prose_response(_REPORT_TEMPLATE.format(concentrated=concentrated), model="deepseek-chat"),
    ])
    tool_ctx = _make_tool_ctx(_journal(tmp_path))

    outcome = _run(client, tool_ctx)

    queue_citations = [c for c in outcome.report.citations if c.kind is CitationKind.QUEUE]
    assert [c.ref for c in queue_citations] == ["E-000300", "E-000100", "E-000200"], (
        "citations must survive in the order the model wrote them, which here is the "
        "order the tool returned -- nothing in the harness may re-sort them"
    )
    body = outcome.report.what_is_concentrated
    idx_300, idx_100, idx_200 = body.find("E-000300"), body.find("E-000100"), body.find("E-000200")
    assert 0 <= idx_300 < idx_100 < idx_200, "the section text itself must preserve tool order, verbatim"


def test_extract_citations_recognises_the_two_analyst_specific_kinds():
    prose = (
        "Money sits here [[overview:by_disposition.EXCEPTION.variance_usd]] and "
        "the leading row is here [[queue:E-000812]]."
    )
    citations = analyst_role._extract_citations(prose)
    assert [(c.kind.value, c.ref) for c in citations] == [
        ("overview", "by_disposition.EXCEPTION.variance_usd"),
        ("queue", "E-000812"),
    ]


def test_verified_citations_drops_a_queue_reference_to_an_episode_never_returned():
    """The Analyst never invents an identifier either: a `queue:` token naming an
    episode that no `get_exception_queue` result this pass actually returned is
    dropped rather than trusted, the same treatment `investigator._verified_citations`
    gives an unverifiable `raw:` id."""
    prose = "See [[queue:E-000999]] and [[queue:E-000100]]."
    verified = analyst_role._verified_citations(prose, seen_raw_ids=set(), seen_queue_episode_ids={"E-000100"})
    assert [(c.kind.value, c.ref) for c in verified] == [("queue", "E-000100")]


# ═══ 2. an unsourced figure gets exactly one repair attempt ══════════════════════


def test_run_analyst_repairs_an_unsourced_figure_once_then_succeeds(tmp_path, monkeypatch):
    fake = FakeDispatch({
        "get_portfolio_overview": _overview_envelope(),
        "get_exception_queue": _queue_envelope([_queue_row("E-000100", 50000)]),
    })
    monkeypatch.setattr(analyst_role, "dispatch", fake)

    bad = _REPORT_TEMPLATE.format(concentrated="The queue carries a shortfall of 999999 cents in total.")
    good = _REPORT_TEMPLATE.format(
        concentrated="The queue carries a shortfall not summed by any tool result [[queue:E-000100]]."
    )
    client = ScriptedClient([
        _tool_call_response("get_portfolio_overview", {}, model="deepseek-chat"),
        _tool_call_response("get_exception_queue", {"order_by": "variance_desc"}, model="deepseek-chat"),
        _prose_response(bad, model="deepseek-chat"),
        _prose_response(good, model="deepseek-chat"),
    ])
    tool_ctx = _make_tool_ctx(_journal(tmp_path))

    outcome = _run(client, tool_ctx)

    assert outcome.unsourced_figures == ()
    assert "999999" not in outcome.report.what_is_concentrated
    assert len(client.calls) == 4  # overview, queue, draft, one repair -- never a second


def test_run_analyst_never_silently_drops_a_figure_that_survives_the_repair_turn(tmp_path, monkeypatch):
    fake = FakeDispatch({
        "get_portfolio_overview": _overview_envelope(),
        "get_exception_queue": _queue_envelope([_queue_row("E-000100", 50000)]),
    })
    monkeypatch.setattr(analyst_role, "dispatch", fake)

    bad = _REPORT_TEMPLATE.format(concentrated="Total exposure across the queue is 999999 cents.")
    still_bad = _REPORT_TEMPLATE.format(concentrated="Total exposure across the queue is 999999 cents, confirmed.")
    client = ScriptedClient([
        _tool_call_response("get_portfolio_overview", {}, model="deepseek-chat"),
        _tool_call_response("get_exception_queue", {"order_by": "variance_desc"}, model="deepseek-chat"),
        _prose_response(bad, model="deepseek-chat"),
        _prose_response(still_bad, model="deepseek-chat"),
    ])
    tool_ctx = _make_tool_ctx(_journal(tmp_path))

    outcome = _run(client, tool_ctx)

    assert outcome.unsourced_figures == ("999999",)
    assert "~~999999~~" in outcome.report.what_is_concentrated  # struck through, never silently removed


# ═══ 3. AnalystOutcome: exactly one of report/typed_reason ═══════════════════════


def test_run_analyst_never_sets_typed_reason(tmp_path, monkeypatch):
    """Unlike the Investigator's `episode_not_found`, no tool this role calls takes an
    episode-shaped identifier that can permanently 404 -- see `roles/analyst.py`'s own
    module docstring. `typed_reason` exists for interface parity; this locks in that
    nothing here actually populates it."""
    fake = FakeDispatch({
        "get_portfolio_overview": _overview_envelope(),
        "get_exception_queue": _queue_envelope([_queue_row("E-000100", 50000)]),
    })
    monkeypatch.setattr(analyst_role, "dispatch", fake)
    report_text = _REPORT_TEMPLATE.format(
        concentrated="Sorted by variance_desc, E-000100 leads [[queue:E-000100]]."
    )
    client = ScriptedClient([
        _tool_call_response("get_portfolio_overview", {}, model="deepseek-chat"),
        _tool_call_response("get_exception_queue", {"order_by": "variance_desc"}, model="deepseek-chat"),
        _prose_response(report_text, model="deepseek-chat"),
    ])
    tool_ctx = _make_tool_ctx(_journal(tmp_path))

    outcome = _run(client, tool_ctx)

    assert outcome.typed_reason is None
    assert outcome.report is not None


# ═══ 4. the prompt: shared rules, real tool names, placeholder discipline ════════


def test_the_analyst_prompt_embeds_the_three_shared_rules_byte_identically():
    assert _shared.NO_ARITHMETIC_RULE in analyst_prompt.ANALYST_SYSTEM_PROMPT
    assert _shared.UNTRUSTED_TEXT_RULE in analyst_prompt.ANALYST_SYSTEM_PROMPT
    assert _shared.NO_SELF_FENCE_RULE in analyst_prompt.ANALYST_SYSTEM_PROMPT


def test_every_tool_named_in_the_analyst_prompt_is_a_tool_that_exists():
    """A prompt telling the model to call a tool that does not exist is an error the
    model discovers at runtime and the developer never sees -- same check
    `test_agents_integration.py` runs for the Investigator/Proposer/Evaluator prompts,
    scoped here to this role's own module."""
    known = tool_names()
    pattern = re.compile(r"\b(?:get|calculate|create|emit|list|fetch)_[a-z_]+\b")
    text = "\n".join(
        str(getattr(analyst_prompt, name))
        for name in dir(analyst_prompt)
        if name.isupper() and isinstance(getattr(analyst_prompt, name), str)
    )
    mentioned = set(pattern.findall(text))
    assert mentioned, "expected at least one tool name in the analyst prompt"
    for name in mentioned:
        assert name in known, f"analyst prompt names {name!r}, which is not a real tool"


def test_render_raises_when_a_required_placeholder_is_missing():
    with pytest.raises(TypeError):
        analyst_prompt.render(cursor=CURSOR, verdict_glossary="")  # fence_nonce omitted


def test_render_fills_every_placeholder_with_no_brace_left_behind():
    rendered = analyst_prompt.render(cursor=CURSOR, verdict_glossary="A-05: x", fence_nonce="9f2c41ab")
    assert "{cursor}" not in rendered
    assert "{verdict_glossary}" not in rendered
    assert "{fence_nonce}" not in rendered
    assert CURSOR in rendered
    assert "9f2c41ab" in rendered


def test_render_iteration1_user_turn_includes_the_question_only_when_present():
    without = analyst_prompt.render_iteration1_user_turn(cursor=CURSOR, question=None)
    assert "reviewer also asked" not in without

    with_question = analyst_prompt.render_iteration1_user_turn(
        cursor=CURSOR, question="What does the PBM track look like?"
    )
    assert "What does the PBM track look like?" in with_question
    assert "reviewer also asked" in with_question


def test_prompt_version_is_a_stable_short_digest():
    assert isinstance(analyst_prompt.PROMPT_VERSION, str)
    assert len(analyst_prompt.PROMPT_VERSION) == 16  # blake2b digest_size=8 -> 16 hex chars
    assert analyst_prompt.PROMPT_VERSION == analyst_prompt.PROMPT_VERSION  # deterministic, not re-hashed per call


# ═══ 5. AnalystReport: declaration order preserved into json_schema() ════════════


def test_analyst_report_json_schema_preserves_declaration_order():
    schema = AnalystReport.json_schema()
    expected = [
        "state_of_the_book",
        "what_is_concentrated",
        "what_i_could_not_determine",
        "where_to_look_first",
        "citations",
    ]
    assert list(schema["properties"]) == expected
    assert schema["required"] == expected


def test_analyst_report_parse_round_trips_a_well_formed_payload():
    payload = {
        "state_of_the_book": "5 episodes are open exceptions.",
        "what_is_concentrated": "Sorted by variance_desc, E-000100 leads.",
        "what_i_could_not_determine": "Nothing -- every question this portfolio raises is answered by the records above.",
        "where_to_look_first": "Check E-000100 first.",
        "citations": [{"kind": "queue", "ref": "E-000100"}],
    }
    report = AnalystReport.parse(payload)
    assert report.citations == (analyst_role.Citation(kind=CitationKind.QUEUE, ref="E-000100"),)
