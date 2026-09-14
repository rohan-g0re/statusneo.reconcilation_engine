"""The agent layer's HTTP surface (design doc S8.6): explain, decide, portfolio, the human gate,
the to-do list, and the run journal made inspectable over HTTP.

**Optional on top of optional.** ``recon.api.app`` is already an optional extra over the
dependency-free deterministic core; this module is optional *again* on top of that -- the
``agent`` extra (``httpx``) can be absent, or present with no API key configured, while the rest
of the API works fine. Every heavy, ``httpx``-dependent import below is wrapped in one
``try/except`` at import time (see ``_agent`` / ``_AGENT_LAYER_ERROR``) so that importing this
module -- which ``recon.api.app.create_app`` always does, to mount the router -- can never raise.
Each handler checks ``_AGENT_LAYER_ERROR`` (and, for the two endpoints that call a model, whether
an API key is configured) and returns a 503 with an actionable message instead. No API key is ever
echoed anywhere in that message or in any other response: only ``AgentSettings.__repr__``'s own
"set"/"unset" convention is used.

**SQLite thread affinity, twice over.** ``recon.api.app``'s ``open_conn`` docstring (~line 143)
records a real bug: a connection created on one worker thread and used on another is refused by
``sqlite3`` outright, and it surfaced as intermittent 500s under concurrency. This module opens a
connection per request the same way -- inside a handler body, never behind a ``Depends``
generator -- and ``/api/agent/decide/{episode_id}`` goes further: because the loop must *stream*
while it runs, the harness executes on a background thread, and that thread opens **its own**
connection from scratch rather than reusing one built on the request-handling thread. Sharing a
connection across those two threads would be the exact same bug wearing a different hat.

**SSE design: poll the journal, do not thread a callback through the harness.** The design note's
own suggestion is "a journal subscriber hook is the obvious route", and the design brief for this
wave floats the same idea. That would mean changing ``journal.py``'s ``Journal.event()`` to invoke
a caller-supplied hook after every write -- but this wave owns only this file, its tests, and one
mount line in ``app.py``; ``journal.py`` belongs to an earlier wave and grounds the rest of the
agent layer, so it stays untouched. ``Journal.events`` is already a property that returns a fresh
tuple snapshot of everything appended so far, and ``Journal.event()`` already flushes every write
to disk immediately. That is sufficient for polling: run the harness on a background thread, and
have the SSE generator loop on the request thread, comparing ``len(journal.events)`` to what it
last emitted and yielding the delta, roughly every 10ms. CPython's GIL makes a list ``append`` in
one thread and a ``tuple(list)`` read in another safe from torn reads without extra locking, which
is what makes this correct without touching ``Journal`` at all. The cost is a small, bounded
polling latency instead of instant push; for a loop whose own step (a model round trip) takes
whole seconds, that cost is invisible. If a future wave does own ``journal.py``, swapping the
polling loop below for a real subscriber callback is a self-contained change to this file alone.

**A client disconnect cannot leave a half-written journal.** The journal's lifecycle is owned
entirely by the background thread's own ``try/finally`` -- opened before the thread starts,
closed inside it once the run ends or raises -- never by the generator that streams it out. If the
HTTP client goes away mid-stream, Starlette stops iterating the generator (or raises
``GeneratorExit`` into it); either way nothing about that touches the worker thread, which keeps
running to completion and closes the journal itself. The run finishes and its journal file is
complete and readable via ``GET /api/agent/runs/{run_id}`` even for a request nobody was left to
receive the response to.

**Portfolio mirrors Explain, not Decide.** ``/api/agent/portfolio`` (``.agents/specs/spec_analyst.md``)
is the Portfolio Analyst's only endpoint -- one request, one response, the same shape as
``/api/agent/explain``, right down to the ``LLMError`` -> 503 handling and the journal
bracketing. It is deliberately **not** SSE: the role is "single pass with tool calls" exactly
like the Investigator, not a propose/evaluate/score/gate loop, so there is no step-by-step
loop state for a client to watch. The one thing Portfolio does not have that Explain does is an
``episode_id`` -- it answers "what is the state of the book", a question about the whole
portfolio, not one episode -- so there is no ``episode_not_found`` branch and no dossier is
built before the run starts; the tools the role calls (``get_portfolio_overview``,
``get_exception_queue``) read straight off ``ctx.cursor``.

**The proposed/accepted diff is not shoehorned into the run journal.** ``journal.Journal.event``
enforces a closed ``kind`` vocabulary this module does not own and must not extend (design's own
"THE GRAPH IS A RUNTIME DEPENDENCY" caution about silent breakage generalises to any frozen
cross-module contract). The highest-value signal design S7 names -- what a human changed before
accepting -- is instead appended to its own file, ``work_item_decisions.jsonl`` under the agent
settings' ``journal_dir``, one line per human-gate submission, in the same never-mutate,
always-append spirit as every other log in this system.
"""

from __future__ import annotations

import json
import re
import secrets
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, Body, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from recon import config
from recon.api import dossier as dossier_module
from recon.config import Settings
from recon.db import repository
from recon.domain.models import WorkItem

__all__ = ["build_router"]


# ═══ the guarded import: everything the 'agent' extra (httpx) actually needs ════════
#
# A namespace object, not a dozen individually-guarded names -- one flag
# (`_AGENT_LAYER_ERROR`) and one place (`_agent.*`) covers every reference below, so a
# handler can never reach a NameError for a name this block failed to bind.


class _AgentModules:
    tools: Any = None
    client: Any = None
    config: Any = None
    grounding: Any = None
    journal: Any = None
    coordinator: Any = None
    investigator: Any = None
    analyst: Any = None
    verdict_vocab: Any = None


_agent = _AgentModules()
_AGENT_LAYER_ERROR: str | None = None

try:
    import httpx  # noqa: F401 -- presence alone is the 'agent' extra's own contract

    from recon.agents import client as _client_mod
    from recon.agents import config as _config_mod
    from recon.agents import grounding as _grounding_mod
    from recon.agents import journal as _journal_mod
    from recon.agents import tools as _tools_mod
    from recon.agents.roles import analyst as _analyst_mod
    from recon.agents.roles import coordinator as _coordinator_mod
    from recon.agents.roles import investigator as _investigator_mod
    from recon.domain import verdicts as _verdict_vocab_mod

    _agent.tools = _tools_mod
    _agent.client = _client_mod
    _agent.config = _config_mod
    _agent.grounding = _grounding_mod
    _agent.journal = _journal_mod
    _agent.coordinator = _coordinator_mod
    _agent.investigator = _investigator_mod
    _agent.analyst = _analyst_mod
    _agent.verdict_vocab = _verdict_vocab_mod
except Exception as exc:  # noqa: BLE001 -- deliberately broad: any import-time failure means "unavailable"
    _AGENT_LAYER_ERROR = f"{type(exc).__name__}: {exc}"


def _import_unavailable_reason() -> str | None:
    if _AGENT_LAYER_ERROR is None:
        return None
    return (
        f"the agent layer is unavailable ({_AGENT_LAYER_ERROR}). Install the optional extra with "
        "`pip install -e '.[agent]'` and retry."
    )


# ═══ shared plumbing ═════════════════════════════════════════════════════════════════

_RUN_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@contextmanager
def _open_conn(settings: Settings):
    """One sqlite3 connection per call, opened and closed on the caller's own thread.

    Duplicates (rather than imports) ``recon.api.app.create_app``'s private ``open_conn``
    closure -- that closure is not exported, and re-deriving these few lines here is what keeps
    this module's only coupling to ``app.py`` down to the single ``include_router`` line the task
    authorises. See that closure's own docstring for the concurrency bug this shape avoids;
    ``/api/agent/decide`` below leans on the *same* rule a second time, across threads it owns.
    """
    from recon.db import connection, migrate

    Path(settings.db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = connection.connect(settings.db_path)
    try:
        migrate.ensure_schema(conn)
        yield conn
    finally:
        conn.close()


def _resolve_cursor(settings: Settings, cursor: str | None) -> str:
    if cursor is None:
        return settings.max_cursor
    try:
        config.cursor_to_date(cursor)
    except ValueError:
        raise HTTPException(422, f"cursor must be YYYY-MM-DDTHH:MM:SSZ, got {cursor!r}") from None
    return cursor


def _glossary() -> str:
    """The verdict vocabulary, so the model reads a code as a meaning (agent_smoke.py's own
    helper, duplicated here rather than imported from a script)."""
    codes = (*_agent.verdict_vocab.REIMBURSEMENT_CODES, *_agent.verdict_vocab.REBATE_CODES)
    return "\n".join(f"{code}: {_agent.verdict_vocab.describe(code)}" for code in codes)


def _tool_context(conn, *, cursor: str, role: str, model_id: str, run_id: str, journal, write_token: str | None = None):
    return _agent.tools.ToolContext(
        conn=conn,
        cursor=cursor,
        role=role,
        model_id=model_id,
        run_id=run_id,
        now=_now(),
        nonce=secrets.token_hex(4),
        write_token=write_token,
        call_log=_agent.tools.CallLog(),
        journal=journal,
    )


def _default_client_factory(agent_settings, journal):
    return _agent.client.OpenAICompatClient(
        base_url=agent_settings.base_url,
        api_key=agent_settings.api_key,
        journal=journal,
        timeout_s=agent_settings.request_timeout_s,
    )


def _tool_call_trace(events) -> list[dict[str, Any]]:
    """Name, arguments, status per call -- the assignment's tool-call-trace deliverable.

    ``tools.dispatch`` always journals exactly one ``tool_result`` immediately after the
    ``tool_call`` it answers, synchronously, with nothing else journalled in between (every
    dispatch happens inline inside one Python call, never interleaved with another one) -- so
    pairing the two lists positionally is exact, not a heuristic.
    """
    calls = [ev for ev in events if ev.kind == "tool_call"]
    results = [ev for ev in events if ev.kind == "tool_result"]
    trace = []
    for call, result in zip(calls, results):
        trace.append(
            {
                "iteration": call.fields.get("iteration"),
                "name": call.fields.get("name"),
                "arguments": call.fields.get("arguments"),
                "status": result.fields.get("status"),
                "error_type": result.fields.get("error_type"),
            }
        )
    return trace


def _event_payload(ev) -> dict[str, Any]:
    return {"seq": ev.seq, "at": ev.at, "run_id": ev.run_id, **ev.fields}


def _sse(kind: str, payload: dict[str, Any]) -> str:
    return f"event: {kind}\ndata: {json.dumps(payload, default=str, ensure_ascii=False)}\n\n"


def _proposal_payload(proposal) -> dict[str, Any]:
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


def _findings_payload(findings: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for criterion_id, finding in findings.items():
        cited = finding.cited_span
        out[criterion_id] = {
            "reasoning": finding.reasoning,
            "verdict": finding.verdict.value if hasattr(finding.verdict, "value") else str(finding.verdict),
            "cited_span": (
                {"quote": cited.quote, "source_kind": cited.source_kind.value, "source_ref": cited.source_ref}
                if cited is not None
                else None
            ),
        }
    return out


def _outcome_payload(outcome) -> dict[str, Any]:
    """All four outcome kinds (design S2/S8.6), rendered distinctly through one shape.

    ``status`` carries the kind verbatim -- ``complete`` | ``insufficient_data`` | ``stalled`` |
    ``capped`` -- never collapsed to a boolean. ``insufficient_data`` gets no different treatment
    here than the others: same shape, same HTTP success path, because design S8.6 is explicit that
    it "is a successful outcome... the UI should not style it as an error."
    """
    return {
        "status": outcome.kind,
        "iterations_run": outcome.iterations_run,
        "best_score": outcome.best_score,
        "final_score": outcome.final_score,
        "score_trajectory": list(outcome.score_trajectory),
        "self_bias_suspected": outcome.self_bias_suspected,
        "proposal": _proposal_payload(outcome.proposal),
        "findings": _findings_payload(outcome.findings),
        "missing_criteria": list(outcome.missing_criteria),
        "missing_narrative": outcome.missing_narrative,
        "proposer_blocked": outcome.proposer_blocked,
        "blocked_reason": outcome.blocked_reason,
        "unmet_criteria": list(outcome.unmet_criteria),
        "budget_note": outcome.budget_note,
    }


_ERROR_STATUS: dict[str, int] = {
    "not_found": 404,
    "invalid_input": 422,
    "ambiguous": 409,
    "not_permitted": 409,
    "duplicate_call_blocked": 409,
    "db_error": 502,
}


def _error_status(error_type: str | None) -> int:
    return _ERROR_STATUS.get(error_type or "", 500)


def _work_item_payload(row: Any) -> dict[str, Any]:
    """Only ever called from a handler that already checked ``_import_unavailable_reason()``
    first, so ``_agent.tools`` is guaranteed bound here -- no fallback branch to keep in sync
    with that guarantee."""
    item = row if isinstance(row, WorkItem) else WorkItem.from_row(row)
    summary, artifacts = _agent.tools.parse_artifacts(item.summary)
    return {
        "work_item_id": item.work_item_id,
        "episode_id": item.episode_id,
        "created_at": item.created_at,
        "created_by": item.created_by,
        "at_cursor": item.at_cursor,
        "from_verdict_id": item.from_verdict_id,
        "recommended_action": item.recommended_action,
        "summary": summary,
        "required_artifacts": artifacts,
    }


def _diff_draft(proposed: dict[str, Any] | None, accepted: dict[str, Any]) -> dict[str, Any]:
    """design S7: "Log the diff between proposed and accepted" -- the highest-value training
    signal in the system, per that section, so it is computed and recorded unconditionally
    rather than only when something actually changed."""
    if not proposed:
        return {"has_proposed": False, "changed_fields": [], "fields": {}}
    fields: dict[str, Any] = {}
    changed: list[str] = []
    for key in ("recommended_action", "summary", "required_artifacts"):
        before = proposed.get(key)
        after = accepted.get(key)
        if before != after:
            changed.append(key)
        fields[key] = {"proposed": before, "accepted": after}
    return {"has_proposed": True, "changed_fields": changed, "fields": fields}


def _record_human_gate_decision(agent_settings, **record: Any) -> None:
    path = agent_settings.journal_dir / "work_item_decisions.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    line = {"at": _now(), **record}
    with path.open("a", encoding="utf-8", newline="") as fh:
        fh.write(json.dumps(line, ensure_ascii=False, default=str) + "\n")


# ═══ the router ══════════════════════════════════════════════════════════════════════


def build_router(
    *,
    agent_settings_factory: Callable[[], Any] | None = None,
    client_factory: Callable[[Any, Any], Any] | None = None,
) -> APIRouter:
    """Build the ``/api/agent/*`` router. Called once, from ``create_app``.

    ``agent_settings_factory`` and ``client_factory`` are the seams a test needs: the default
    factory (``recon.agents.config.load_agent_settings``) reads real environment variables and a
    real ``.env``, and the default client (``OpenAICompatClient``) makes real HTTP calls. A test
    passes both explicitly -- a fixed :class:`AgentSettings` with a throwaway key and a temp
    ``journal_dir``, and a scripted stub ``LLMClient`` -- so ``build_router`` itself never has to
    know it is under test. Passing neither (the production call from ``create_app``) is what makes
    every endpoint below actually go out over the network.

    Supplying ``client_factory`` also opts an endpoint out of the "no API key configured" 503: a
    caller who hands in their own way of making an ``LLMClient`` has already answered that
    question, and a fixture running against a stub has no business tripping over a real one.
    """

    router = APIRouter()

    def _settings_factory():
        if agent_settings_factory is not None:
            return agent_settings_factory()
        return _agent.config.load_agent_settings()

    def _make_client(agent_settings, journal):
        if client_factory is not None:
            return client_factory(agent_settings, journal)
        return _default_client_factory(agent_settings, journal)

    def _llm_unavailable_reason() -> str | None:
        reason = _import_unavailable_reason()
        if reason is not None:
            return reason
        if client_factory is not None:
            return None
        agent_settings = _settings_factory()
        if not agent_settings.api_key:
            return (
                "the agent layer is unavailable: no RECON_AGENT_API_KEY (or DEEPSEEK_API_KEY) is "
                "configured. Set one in the environment or in a .env file at the repo root."
            )
        return None

    # ─── Explain ─────────────────────────────────────────────────────────────────

    @router.post("/api/agent/explain/{episode_id}")
    def explain(
        episode_id: str,
        request: Request,
        cursor: str | None = Query(None, description="Replay cursor, ISO8601 UTC"),
    ) -> dict[str, Any]:
        reason = _llm_unavailable_reason()
        if reason is not None:
            raise HTTPException(503, reason)

        settings: Settings = request.app.state.settings
        resolved_cursor = _resolve_cursor(settings, cursor)

        with _open_conn(settings) as conn:
            payload = dossier_module.build_dossier(conn, episode_id, resolved_cursor)
            if payload is None:
                return {"status": "episode_not_found", "episode_id": episode_id, "cursor": resolved_cursor}

            agent_settings = _settings_factory()
            run_id = uuid.uuid4().hex
            journal = _agent.journal.Journal(agent_settings.journal_dir / f"{run_id}.jsonl", run_id, now=_now)
            try:
                client = _make_client(agent_settings, journal)
                ctx = _tool_context(
                    conn,
                    cursor=resolved_cursor,
                    role="exception_investigator",
                    model_id=agent_settings.investigator_model,
                    run_id=run_id,
                    journal=journal,
                )
                # `run_investigator` itself journals no run_started/run_finished pair (its own
                # module docstring: it is not `harness.run_until`) -- the caller supplies the
                # bracketing events, exactly as `scripts/agent_smoke.py` does.
                journal.event(
                    "run_started",
                    role="exception_investigator",
                    episode_id=episode_id,
                    cursor=resolved_cursor,
                    model=agent_settings.investigator_model,
                )
                try:
                    outcome = _agent.investigator.run_investigator(
                        client=client,
                        tool_ctx=ctx,
                        model=agent_settings.investigator_model,
                        episode_id=episode_id,
                        cursor=resolved_cursor,
                        verdict_glossary=_glossary(),
                    )
                except _agent.client.LLMError as exc:
                    # Say which side failed. A provider that stalls and then answers
                    # empty is indistinguishable, from the browser, from a hung server
                    # -- and a reviewer watching a spinner has no way to tell "the
                    # model is rate-limited" from "your agent layer is broken". It is
                    # worth one branch to name it.
                    journal.event("run_finished", role="exception_investigator", error=str(exc))
                    raise HTTPException(
                        503,
                        f"the model provider did not answer after {exc.attempts} attempts "
                        f"(HTTP {exc.status}). This is upstream of the agent layer: the "
                        f"deterministic views, the timeline and the to-do list are unaffected. "
                        f"Retry in a few minutes.",
                    ) from exc
                journal.event("run_finished", role="exception_investigator")
            finally:
                journal.close()

            events = journal.events
            state = _agent.journal.derive_state(events)
            trace = _tool_call_trace(events)

            if outcome.report is None:
                return {
                    "status": outcome.typed_reason,
                    "episode_id": episode_id,
                    "cursor": resolved_cursor,
                    "run_id": run_id,
                    "tool_calls": trace,
                    "token_usage": state.token_usage,
                }

            report = outcome.report
            return {
                "status": "ok",
                "episode_id": episode_id,
                "cursor": resolved_cursor,
                "run_id": run_id,
                "report": {
                    "what_happened": report.what_happened,
                    "why_it_is_open": report.why_it_is_open,
                    "what_i_could_not_determine": report.what_i_could_not_determine,
                    "what_a_human_should_check_first": report.what_a_human_should_check_first,
                },
                "citations": [{"kind": c.kind.value, "ref": c.ref} for c in report.citations],
                "unsourced_figures": list(outcome.unsourced_figures),
                "tool_calls": trace,
                "token_usage": state.token_usage,
            }

    # ─── Portfolio ───────────────────────────────────────────────────────────────

    @router.post("/api/agent/portfolio")
    def portfolio(request: Request, body: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
        reason = _llm_unavailable_reason()
        if reason is not None:
            raise HTTPException(503, reason)

        body = body or {}
        question = body.get("question")
        if question is not None and not isinstance(question, str):
            raise HTTPException(422, "question must be a string or null")

        settings: Settings = request.app.state.settings
        resolved_cursor = _resolve_cursor(settings, body.get("cursor"))

        with _open_conn(settings) as conn:
            agent_settings = _settings_factory()
            run_id = uuid.uuid4().hex
            journal = _agent.journal.Journal(agent_settings.journal_dir / f"{run_id}.jsonl", run_id, now=_now)
            try:
                client = _make_client(agent_settings, journal)
                ctx = _tool_context(
                    conn,
                    cursor=resolved_cursor,
                    role="portfolio_analyst",
                    model_id=agent_settings.analyst_model,
                    run_id=run_id,
                    journal=journal,
                )
                # `run_analyst` is single-pass with tool calls, the same shape as
                # `run_investigator` (spec_analyst.md: "not the propose/evaluate loop --
                # there is no action being proposed, so there is nothing to gate") -- it
                # journals no run_started/run_finished pair of its own, exactly like the
                # Investigator, so the caller brackets the run here.
                journal.event(
                    "run_started",
                    role="portfolio_analyst",
                    cursor=resolved_cursor,
                    model=agent_settings.analyst_model,
                )
                try:
                    outcome = _agent.analyst.run_analyst(
                        client=client,
                        tool_ctx=ctx,
                        model=agent_settings.analyst_model,
                        cursor=resolved_cursor,
                        question=question,
                        verdict_glossary=_glossary(),
                    )
                except _agent.client.LLMError as exc:
                    # Identical reasoning to `explain`: this call runs on the thinking
                    # model and can take 1-3 minutes even on success, so a reviewer
                    # watching that wait needs to be told "the provider stalled", not
                    # left guessing whether the agent layer itself hung.
                    journal.event("run_finished", role="portfolio_analyst", error=str(exc))
                    raise HTTPException(
                        503,
                        f"the model provider did not answer after {exc.attempts} attempts "
                        f"(HTTP {exc.status}). This is upstream of the agent layer: the "
                        f"deterministic views, the timeline and the to-do list are unaffected. "
                        f"Retry in a few minutes.",
                    ) from exc
                journal.event("run_finished", role="portfolio_analyst")
            finally:
                journal.close()

            events = journal.events
            state = _agent.journal.derive_state(events)
            trace = _tool_call_trace(events)

            if outcome.report is None:
                return {
                    "status": outcome.typed_reason,
                    "cursor": resolved_cursor,
                    "run_id": run_id,
                    "tool_calls": trace,
                    "token_usage": state.token_usage,
                }

            report = outcome.report
            return {
                "status": "ok",
                "cursor": resolved_cursor,
                "run_id": run_id,
                "report": {
                    "state_of_the_book": report.state_of_the_book,
                    "what_is_concentrated": report.what_is_concentrated,
                    "what_i_could_not_determine": report.what_i_could_not_determine,
                    "where_to_look_first": report.where_to_look_first,
                },
                "citations": [{"kind": c.kind.value, "ref": c.ref} for c in report.citations],
                "unsourced_figures": list(outcome.unsourced_figures),
                "tool_calls": trace,
                "token_usage": state.token_usage,
            }

    # ─── Decide (SSE) ────────────────────────────────────────────────────────────

    def _decide_event_stream(settings: Settings, episode_id: str, cursor: str):
        # The existence probe runs, and closes its connection, entirely on THIS thread before
        # the worker below ever starts -- see this module's docstring on thread affinity. The
        # worker opens a wholly separate connection of its own; the two connections never meet.
        with _open_conn(settings) as probe_conn:
            exists = dossier_module.build_dossier(probe_conn, episode_id, cursor) is not None
        if not exists:
            yield _sse("outcome", {"status": "episode_not_found", "episode_id": episode_id, "cursor": cursor})
            return

        agent_settings = _settings_factory()
        run_id = uuid.uuid4().hex
        journal = _agent.journal.Journal(agent_settings.journal_dir / f"{run_id}.jsonl", run_id, now=_now)

        result: dict[str, Any] = {}

        def _run() -> None:
            try:
                with _open_conn(settings) as conn:  # opened and used entirely on this thread
                    payload = dossier_module.build_dossier(conn, episode_id, cursor)
                    clause_index = _agent.grounding.render_clause_index(_agent.grounding.select_clauses(payload))
                    ctx = _tool_context(
                        conn,
                        cursor=cursor,
                        role="workflow_coordinator",
                        model_id=agent_settings.proposer_model,
                        run_id=run_id,
                        journal=journal,
                    )
                    client = _make_client(agent_settings, journal)
                    result["outcome"] = _agent.coordinator.run_coordinator(
                        client=client,
                        tool_ctx=ctx,
                        settings=agent_settings,
                        dossier=payload,
                        verdict_glossary=_glossary(),
                        grounding_clause_index=clause_index,
                        journal=journal,
                    )
            except Exception as exc:  # noqa: BLE001 -- surfaced as a typed SSE event, never dropped
                result["error"] = exc
            finally:
                # Owned by this thread alone: a disconnected client cannot stop this from
                # running, and cannot leave the file half-written either way.
                journal.close()

        worker = threading.Thread(target=_run, name=f"agent-decide-{run_id}", daemon=True)
        worker.start()

        emitted = 0
        while True:
            events = journal.events
            for ev in events[emitted:]:
                yield _sse(ev.kind, _event_payload(ev))
            emitted = len(events)
            if not worker.is_alive() and emitted == len(journal.events):
                break
            time.sleep(0.01)
        worker.join()

        if "error" in result:
            yield _sse(
                "outcome",
                {
                    "status": "error",
                    "run_id": run_id,
                    "episode_id": episode_id,
                    "cursor": cursor,
                    "detail": str(result["error"]),
                },
            )
        else:
            yield _sse(
                "outcome",
                {**_outcome_payload(result["outcome"]), "run_id": run_id, "episode_id": episode_id, "cursor": cursor},
            )

    @router.post("/api/agent/decide/{episode_id}")
    def decide(
        episode_id: str,
        request: Request,
        cursor: str | None = Query(None, description="Replay cursor, ISO8601 UTC"),
    ) -> StreamingResponse:
        reason = _llm_unavailable_reason()
        if reason is not None:
            raise HTTPException(503, reason)

        settings: Settings = request.app.state.settings
        resolved_cursor = _resolve_cursor(settings, cursor)
        return StreamingResponse(
            _decide_event_stream(settings, episode_id, resolved_cursor),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ─── The human gate: the only write path ────────────────────────────────────

    @router.post("/api/agent/work-item")
    def create_work_item(request: Request, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        reason = _import_unavailable_reason()
        if reason is not None:
            raise HTTPException(503, reason)

        required_fields = ("episode_id", "from_verdict_id", "recommended_action", "summary")
        missing = [k for k in required_fields if k not in body]
        if missing:
            raise HTTPException(422, f"missing required field(s): {', '.join(missing)}")

        episode_id = body["episode_id"]
        from_verdict_id = body["from_verdict_id"]
        recommended_action = body["recommended_action"]
        summary = body["summary"]
        if not isinstance(episode_id, str) or not isinstance(recommended_action, str) or not isinstance(summary, str):
            raise HTTPException(422, "episode_id, recommended_action and summary must all be strings")

        required_artifacts = body.get("required_artifacts") or []
        if not isinstance(required_artifacts, list) or not all(isinstance(a, str) for a in required_artifacts):
            raise HTTPException(422, "required_artifacts must be a list of strings")

        dry_run = bool(body.get("dry_run", False))
        proposed = body.get("proposed")
        origin_run_id = body.get("run_id")
        client_write_token = body.get("write_token")

        settings: Settings = request.app.state.settings
        resolved_cursor = _resolve_cursor(settings, body.get("cursor"))

        # The exact bytes `create_mock_work_item` will independently derive and hash -- artifact
        # marker included -- so a token minted here verifies against what the tool recomputes
        # (`tools.mint_write_token`'s own docstring). Pre-minting is what lets a caller who
        # supplies no `write_token` still get a working commit in one round trip; a caller who
        # DOES supply one (from an earlier, now possibly-stale draft) is checked against it by
        # the tool itself, not by this function.
        stored_summary = summary.strip()
        if required_artifacts:
            stored_summary += _agent.tools.ARTIFACT_MARKER + "; ".join(a.strip() for a in required_artifacts)
        write_token = client_write_token or _agent.tools.mint_write_token(
            episode_id=episode_id,
            from_verdict_id=from_verdict_id,
            recommended_action=recommended_action,
            summary=stored_summary,
        )

        agent_settings = _settings_factory()
        write_run_id = uuid.uuid4().hex
        journal = _agent.journal.Journal(
            agent_settings.journal_dir / f"workitem-{write_run_id}.jsonl", write_run_id, now=_now
        )
        try:
            with _open_conn(settings) as conn:
                ctx = _tool_context(
                    conn,
                    cursor=resolved_cursor,
                    role="human_gate",
                    model_id="human",
                    run_id=write_run_id,
                    journal=journal,
                    write_token=write_token,
                )
                envelope = _agent.tools.create_mock_work_item(
                    ctx,
                    episode_id=episode_id,
                    recommended_action=recommended_action,
                    summary=summary,
                    dry_run=dry_run,
                    required_artifacts=required_artifacts,
                )
        finally:
            journal.close()

        if envelope["status"] == "error":
            raise HTTPException(_error_status(envelope["error_type"]), envelope["message"])

        accepted = {
            "recommended_action": recommended_action,
            "summary": summary,
            "required_artifacts": list(required_artifacts),
        }
        diff = _diff_draft(proposed, accepted)
        _record_human_gate_decision(
            agent_settings,
            episode_id=episode_id,
            from_verdict_id=from_verdict_id,
            run_id=origin_run_id,
            proposed=proposed,
            accepted=accepted,
            diff=diff,
            work_item_id=envelope["data"]["work_item_id"],
            created=envelope["data"]["created"],
            dry_run=envelope["data"]["dry_run"],
        )

        return {
            "status": "ok",
            "work_item_id": envelope["data"]["work_item_id"],
            "created": envelope["data"]["created"],
            "dry_run": envelope["data"]["dry_run"],
            "message": envelope["message"],
            "diff": diff,
            # `create_mock_work_item` derives `from_verdict_id` itself from
            # `repository.latest_verdict(conn, episode_id, cursor)` -- it never reads the
            # caller-supplied `from_verdict_id` above, which exists only to mint (and, on a
            # real commit, to authorise) `write_token`. Nothing on the read side of the HTTP
            # surface exposes the verdict primary key at all (`/api/episode`, `/api/episode/
            # {id}/dossier`, `/api/episode/{id}/trace` all omit it by design -- it is a DB
            # implementation detail, not a domain field), so a browser client has no way to
            # learn the value it must send on `dry_run=false` except by reading it back here.
            # `dry_run=true` skips the token check entirely (see `create_mock_work_item`), so
            # a client can always discover the correct value with a harmless preview call
            # before committing -- exactly what that branch's own message promises: "this is
            # the draft that will be shown to the reviewer for editing before it is accepted."
            "from_verdict_id": envelope["data"]["from_verdict_id"],
            "at_cursor": envelope["data"]["at_cursor"],
        }

    # ─── The to-do list ──────────────────────────────────────────────────────────

    @router.get("/api/agent/work-items")
    def list_work_items(request: Request, limit: int = Query(200, ge=1, le=1000)) -> dict[str, Any]:
        reason = _import_unavailable_reason()
        if reason is not None:
            raise HTTPException(503, reason)
        settings: Settings = request.app.state.settings
        with _open_conn(settings) as conn:
            rows = conn.execute(
                "SELECT * FROM work_item ORDER BY work_item_id DESC LIMIT ?", (limit,)
            ).fetchall()
            items = [_work_item_payload(row) for row in rows]
        return {"work_items": items}

    @router.get("/api/agent/work-items/{episode_id}")
    def list_work_items_for_episode(episode_id: str, request: Request) -> dict[str, Any]:
        reason = _import_unavailable_reason()
        if reason is not None:
            raise HTTPException(503, reason)
        settings: Settings = request.app.state.settings
        with _open_conn(settings) as conn:
            rows = repository.work_items_for_episode(conn, episode_id)
        return {"episode_id": episode_id, "work_items": [_work_item_payload(row) for row in rows]}

    # ─── The run journal, over HTTP ──────────────────────────────────────────────

    @router.get("/api/agent/runs/{run_id}")
    def get_run(run_id: str) -> dict[str, Any]:
        reason = _import_unavailable_reason()
        if reason is not None:
            raise HTTPException(503, reason)
        if not _RUN_ID_RE.match(run_id):
            raise HTTPException(422, f"run_id must match {_RUN_ID_RE.pattern!r}, got {run_id!r}")

        agent_settings = _settings_factory()
        path = agent_settings.journal_dir / f"{run_id}.jsonl"
        if not path.exists():
            # A workitem-{run_id}.jsonl write journal is a legal second place a run_id lives.
            alt = agent_settings.journal_dir / f"workitem-{run_id}.jsonl"
            path = alt if alt.exists() else path
        if not path.exists():
            raise HTTPException(404, f"no run journal for run_id {run_id!r}")

        events = _agent.journal.read_journal(path)
        state = _agent.journal.derive_state(events)
        return {
            "run_id": run_id,
            "role": state.role,
            "episode_id": state.episode_id,
            "cursor": state.cursor,
            "outcome": state.outcome,
            "final_score": state.final_score,
            "wall_ms": state.wall_ms,
            "models": state.models,
            "token_usage": state.token_usage,
            "tool_calls": _tool_call_trace(events),
            "events": [{"seq": ev.seq, "at": ev.at, "kind": ev.kind, **ev.fields} for ev in events],
        }

    return router
