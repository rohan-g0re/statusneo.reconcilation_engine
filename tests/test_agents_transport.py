"""Coder 1's tests: journal.py, client.py, config.py (spec_contracts.md S1/S2/S3).

No network calls anywhere in this file. `OpenAICompatClient` is exercised against
`httpx.MockTransport` (a fake transport, not a stub of our own code), and
`ReplayClient`/`RecordingClient` are exercised against a fake `LLMClient` double.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest

from recon.agents.client import (
    LLMError,
    LLMResponse,
    OpenAICompatClient,
    RecordingClient,
    ReplayClient,
    ReplayMiss,
    request_digest,
    supports_forced_tool_choice,
)
from recon.agents.config import load_agent_settings
from recon.agents.journal import EVENT_KINDS, Journal, derive_state, read_journal

# ═══ shared fixtures ═════════════════════════════════════════════════════════


def _clock():
    """A deterministic, monotonically increasing fake `now`. Never the real clock --
    Journal's own contract is "supplied by the caller, never a clock read inside"."""
    ticks = iter(range(100_000))

    def _now() -> str:
        n = next(ticks)
        return f"2026-01-01T{n // 3600:02d}:{(n // 60) % 60:02d}:{n % 60:02d}Z"

    return _now


def _raw_payload(model: str = "deepseek-chat", content: str = "ok") -> dict:
    return {
        "id": "chatcmpl-test",
        "model": model,
        "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
    }


def _sample_response(model: str = "deepseek-chat") -> LLMResponse:
    raw = _raw_payload(model)
    return LLMResponse(
        content="ok", tool_calls=(), reasoning=None, finish_reason="stop",
        model=model, usage=raw["usage"], raw=raw,
    )


class _StubClient:
    """A fake `LLMClient` that returns one canned response, for RecordingClient tests
    that must not touch the network or `OpenAICompatClient`'s retry machinery."""

    def __init__(self, response: LLMResponse) -> None:
        self._response = response

    def complete(self, *, messages, model, tools=None, tool_choice=None, max_tokens=None, temperature=None) -> LLMResponse:
        return self._response


@contextmanager
def _fake_client(handler):
    """A plain `httpx.Client` wired to `httpx.MockTransport` -- no real network,
    and closed on exit so it never trips `filterwarnings = ["error"]` on an
    unclosed-resource warning."""
    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(transport=transport)
    try:
        yield http_client
    finally:
        http_client.close()


# ═══ 1. a recorded exchange replays byte-identically, with no api key ═══════


def test_a_recorded_exchange_replays_byte_identically_through_replayclient_with_no_api_key(tmp_path):
    settings = load_agent_settings(api_key=None, mode="replay")
    assert settings.api_key is None

    fixture_dir = tmp_path / "fixtures"
    recorder = RecordingClient(_StubClient(_sample_response()), fixture_dir, run_id="run-1")
    messages = [{"role": "user", "content": "hi"}]
    original = recorder.complete(messages=messages, model="deepseek-chat")

    replay = ReplayClient(recorder.path)
    replayed = replay.complete(messages=messages, model="deepseek-chat")

    assert replayed == original


# ═══ 2. an unrecorded request raises ReplayMiss naming the digest ═══════════


def test_replayclient_raises_replaymiss_naming_the_digest_on_an_unrecorded_request(tmp_path):
    fixture_dir = tmp_path / "fixtures"
    recorder = RecordingClient(_StubClient(_sample_response()), fixture_dir, run_id="run-2")
    recorder.complete(messages=[{"role": "user", "content": "recorded"}], model="deepseek-chat")

    replay = ReplayClient(recorder.path)
    unseen_messages = [{"role": "user", "content": "never recorded"}]
    expected_digest = request_digest("deepseek-chat", unseen_messages, None, None)

    with pytest.raises(ReplayMiss) as excinfo:
        replay.complete(messages=unseen_messages, model="deepseek-chat")

    assert excinfo.value.digest == expected_digest
    assert expected_digest in str(excinfo.value)


# ═══ 3. derive_state reconstructs run_id, both model ids, iteration count, tool calls


def _write_two_iteration_run(path: Path) -> None:
    journal = Journal(path, "run-3", now=_clock())
    journal.event("run_started", role="workflow_coordinator", episode_id="E-1", cursor="2026-01-01T23:59:59Z")
    for i in range(2):
        journal.event("iteration_started", iteration=i)
        journal.event("llm_request", iteration=i, agent="proposer", model="deepseek-chat")
        journal.event(
            "llm_response", iteration=i, agent="proposer",
            usage={"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6},
        )
        journal.event("tool_call", iteration=i, id=f"call-{i}", name="get_episode_dossier", arguments={"episode_id": "E-1"})
        journal.event("tool_result", iteration=i, id=f"call-{i}", status="ok")
        journal.event("proposal", iteration=i, action="RESUBMIT", reasoning="because")
        journal.event("llm_request", iteration=i, agent="evaluator", model="deepseek-v4-pro")
        journal.event(
            "llm_response", iteration=i, agent="evaluator",
            usage={"prompt_tokens": 7, "completion_tokens": 2, "total_tokens": 9},
        )
        journal.event("evaluation", iteration=i, overall_reasoning="looks fine")
        journal.event("score", iteration=i, value=60.0 + i * 25)
        journal.event("gate", iteration=i, decision="continue" if i == 0 else "complete")
        journal.event("iteration_finished", iteration=i)
    journal.event("run_finished", outcome="complete", final_score=85.0, wall_ms=1234)
    journal.close()


def test_derive_state_reconstructs_run_id_both_model_ids_iteration_count_and_tool_calls_from_the_file_alone(tmp_path):
    path = tmp_path / "run-3.jsonl"
    _write_two_iteration_run(path)

    # From the file alone: no journal instance, no other input.
    events = read_journal(path)
    state = derive_state(events)

    assert state.run_id == "run-3"
    assert state.models == {"proposer": "deepseek-chat", "evaluator": "deepseek-v4-pro"}
    assert len(state.iterations) == 2
    assert len(state.tool_calls) == 2
    assert state.outcome == "complete"
    assert state.final_score == 85.0
    assert state.token_usage["total_tokens"] == 6 + 9 + 6 + 9


# ═══ 4. the journal never writes the api key ════════════════════════════════


def test_the_journal_never_writes_the_api_key(tmp_path):
    path = tmp_path / "redact.jsonl"
    journal = Journal(path, "run-redact", now=_clock())
    secret = "sk-5330c8e36354436d9895f828d571aff3"
    journal.event(
        "llm_request",
        model="deepseek-chat",
        body={"model": "deepseek-chat", "messages": [{"role": "system", "content": f"the key is {secret}"}]},
    )
    journal.close()

    text = path.read_text(encoding="utf-8")
    assert secret not in text
    assert "sk-***REDACTED***" in text

    # The in-memory mirror must agree with the file -- both are redacted the same way.
    events = read_journal(path)
    assert secret not in json.dumps(events[0].fields)


# ═══ 5. repr(AgentSettings) never prints the key ════════════════════════════


def test_repr_of_agentsettings_with_a_key_set_contains_no_key_material():
    settings = load_agent_settings(api_key="sk-5330c8e36354436d9895f828d571aff3")
    assert "sk-" not in repr(settings)
    assert "5330c8e36354436d9895f828d571aff3" not in repr(settings)


# ═══ 6. an unknown journal kind raises ValueError ═══════════════════════════


def test_an_unknown_journal_kind_raises_valueerror(tmp_path):
    path = tmp_path / "bad.jsonl"
    journal = Journal(path, "run-bad", now=_clock())
    with pytest.raises(ValueError):
        journal.event("not_a_real_kind")
    journal.close()

    # Also enforced on the read side, for a file that was not written by Journal.
    assert "not_a_real_kind" not in EVENT_KINDS
    bad_path = tmp_path / "bad_on_disk.jsonl"
    bad_path.write_text(
        json.dumps({"seq": 0, "at": "2026-01-01T00:00:00Z", "run_id": "x", "kind": "not_a_real_kind"}) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        read_journal(bad_path)


# ═══ 7. seq is contiguous from 0 across a multi-event run ═══════════════════


def test_seq_is_contiguous_from_zero_across_a_multi_event_run(tmp_path):
    path = tmp_path / "seq.jsonl"
    journal = Journal(path, "run-seq", now=_clock())
    kinds = ["run_started", "llm_request", "llm_response", "tool_call", "tool_result", "run_finished"]
    for kind in kinds:
        journal.event(kind)
    journal.close()

    events = read_journal(path)
    assert [e.seq for e in events] == list(range(len(kinds)))
    # The in-memory mirror agrees before the file is even re-read.
    assert [e.seq for e in journal.events] == list(range(len(kinds)))


# ═══ 8. supports_forced_tool_choice ══════════════════════════════════════════


def test_supports_forced_tool_choice_is_false_for_v4_pro_and_true_for_chat():
    assert supports_forced_tool_choice("deepseek-v4-pro") is False
    assert supports_forced_tool_choice("deepseek-chat") is True


# ═══ 9. retry behaviour: 429 then 200 succeeds; three 429s raise LLMError ═══


def test_a_429_followed_by_a_200_succeeds_after_retry(tmp_path):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"}, json={"error": "rate limited"})
        return httpx.Response(200, json=_raw_payload())

    journal = Journal(tmp_path / "retry-ok.jsonl", "run-retry-ok", now=_clock())
    with _fake_client(handler) as http_client:
        client = OpenAICompatClient(
            base_url="https://api.deepseek.com", api_key=None, journal=journal,
            client=http_client, sleep=lambda seconds: None,
        )
        result = client.complete(messages=[{"role": "user", "content": "hi"}], model="deepseek-chat")
    journal.close()

    assert result.content == "ok"
    assert calls["n"] == 2

    kinds = [e.kind for e in read_journal(tmp_path / "retry-ok.jsonl")]
    assert kinds == ["llm_request", "llm_response"]


def test_three_429s_raise_llmerror_carrying_attempts_equal_three(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": "rate limited"})

    journal = Journal(tmp_path / "retry-fail.jsonl", "run-retry-fail", now=_clock())
    with _fake_client(handler) as http_client:
        client = OpenAICompatClient(
            base_url="https://api.deepseek.com", api_key=None, journal=journal,
            client=http_client, sleep=lambda seconds: None,
        )
        with pytest.raises(LLMError) as excinfo:
            client.complete(messages=[{"role": "user", "content": "hi"}], model="deepseek-chat")
    journal.close()

    assert excinfo.value.attempts == 3
    assert excinfo.value.status == 429

    kinds = [e.kind for e in read_journal(tmp_path / "retry-fail.jsonl")]
    assert kinds == ["llm_request", "llm_error"]


# ═══ 10. every outgoing request in the journal is byte-reconstructable ══════


def test_every_outgoing_request_body_in_the_journal_round_trips_to_what_was_sent(tmp_path):
    sent_bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent_bodies.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(200, json=_raw_payload())

    journal_path = tmp_path / "roundtrip.jsonl"
    journal = Journal(journal_path, "run-roundtrip", now=_clock())
    with _fake_client(handler) as http_client:
        client = OpenAICompatClient(
            base_url="https://api.deepseek.com", api_key="sk-doesnotmatterlength00000000",
            journal=journal, client=http_client, sleep=lambda seconds: None,
        )
        messages = [{"role": "user", "content": "round trip me"}]
        client.complete(messages=messages, model="deepseek-chat", temperature=0.2, max_tokens=64)
    journal.close()

    assert len(sent_bodies) == 1

    events = read_journal(journal_path)
    request_events = [e for e in events if e.kind == "llm_request"]
    assert len(request_events) == 1

    logged_body = request_events[0].fields["body"]
    assert logged_body == sent_bodies[0]
    # And the reverse: the digest recorded alongside it is recomputable from the
    # logged body alone, with no access to what was actually sent over the wire.
    recomputed_digest = request_digest(
        logged_body["model"], logged_body["messages"], logged_body.get("tools"), logged_body.get("tool_choice"),
    )
    assert recomputed_digest == request_events[0].fields["digest"]
