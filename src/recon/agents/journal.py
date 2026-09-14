"""The agent layer's audit trail: one append-only JSONL file per run, and the only
place that log needs to be replayed from.

The design's invariant is *"model-visible means logged"* (``docs/agent_layer_design.md``
S6): anything that reached a model request must be reconstructable from this file
alone.  That single artifact does three jobs at once -- the required tool-call trace,
the audit trail a reviewer reads before trusting a recommendation, and the fixture a
``ReplayClient`` runs the eval set from with no API key.

Two conventions this module owns, which every later caller (``harness.py``,
``roles/*`` -- built by the orchestrator) must honour, because :func:`derive_state`
has no other way to find them:

* Any event that belongs to one trip around the propose/evaluate loop --
  ``proposal``, ``evaluation``, ``score``, ``gate``, ``iteration_started``,
  ``iteration_finished``, and the ``llm_request``/``llm_response``/``tool_call``/
  ``tool_result`` events raised while handling it -- carries an ``iteration``
  integer field.
* Every ``llm_request``/``llm_response`` carries an ``agent`` field naming which
  internal role issued the call (``"proposer"``, ``"evaluator"``,
  ``"investigator"``, ...).  That is what lets :func:`derive_state` report the
  proposer's and evaluator's model ids separately -- the thing that makes the
  two-agent independence claim (design doc S1) checkable after the fact rather
  than assumed, rather than something the harness has to remember to log twice.

**Redaction lives here too, not only in ``client.py``.**  ``Journal.event`` scrubs
every string value recursively before it reaches disk or the in-memory mirror, so a
stray key pasted into a proposal's ``required_artifacts`` or a payer's adversarial
free-text field (design doc S8) is caught regardless of which caller forgot to
redact it first.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = [
    "JOURNAL_SCHEMA_VERSION",
    "EVENT_KINDS",
    "JournalEvent",
    "Journal",
    "ToolCallRecord",
    "IterationState",
    "RunState",
    "read_journal",
    "derive_state",
]

JOURNAL_SCHEMA_VERSION = 1

#: The closed `kind` vocabulary (spec_contracts.md S1). Anything else is a bug in the
#: caller, not a new kind of event, so it is rejected rather than silently accepted.
EVENT_KINDS: frozenset[str] = frozenset(
    {
        "run_started", "run_finished",
        "llm_request", "llm_response", "llm_error",
        "tool_call", "tool_result",
        "proposal", "evaluation", "score", "gate",
        "iteration_started", "iteration_finished",
        "work_item_written",
        "injection_attempt_recorded",
        # A figure survived the repair turn without appearing in any tool result --
        # the assignment's central constraint failing in the open. It earns its own
        # kind rather than riding on an `llm_response` field: this is the one event a
        # reviewer auditing "the agent never computes a number" would grep for, and
        # burying it inside another kind is how a finding becomes invisible.
        "unsourced_figure",
    }
)

# Keys the wire format reserves for the envelope. A caller field with one of these
# names would silently overwrite `seq`/`at`/`run_id`/`kind` when the dict is
# flattened onto the wire, so it is rejected rather than allowed to collide.
_RESERVED_KEYS: frozenset[str] = frozenset({"seq", "at", "run_id", "kind"})

# Matches the shape of a DeepSeek/OpenAI-style secret key. Long enough (8+ chars
# after the prefix) that it will not eat ordinary words starting "sk-".
#
# reviewer finding 13 (spec_fixes_round1.md C3): this pattern alone is not enough --
# it only catches keys shaped exactly "sk-" + alnum, so "sk-proj-..." and
# "sk-or-v1-..." (both contain hyphens the character class excludes) and any non-"sk"
# gateway token pass through unredacted. `Journal.redact_literal` below closes that
# gap by scrubbing the *actual configured* secret byte-for-byte, whatever its shape,
# in addition to this pattern -- the pattern stays as a catch-all for keys nobody
# told the journal about (e.g. one pasted into a proposal's free text).
_SECRET_PATTERN = re.compile(r"sk-[A-Za-z0-9]{8,}")
_REDACTED = "sk-***REDACTED***"
_REDACTED_LITERAL = "***REDACTED-KEY***"

#: A registered literal secret shorter than this is refused (see `Journal.redact_literal`)
#: rather than accepted and over-applied -- a one- or two-character "key" would redact
#: unrelated ordinary text throughout the journal, which is its own kind of corruption.
_MIN_LITERAL_SECRET_LEN = 8


def _redact(value: Any, literal_secrets: tuple[str, ...] = ()) -> Any:
    """Scrub anything matching an API-key shape, plus any registered literal secret,
    recursively.

    Applied to every event's fields before they are written *or* mirrored in
    memory, so ``Journal.events`` (documented as "the in-memory mirror of what was
    written") never disagrees with the file on disk about what was redacted.
    """
    if isinstance(value, str):
        value = _SECRET_PATTERN.sub(_REDACTED, value)
        for secret in literal_secrets:
            value = value.replace(secret, _REDACTED_LITERAL)
        return value
    if isinstance(value, dict):
        return {key: _redact(item, literal_secrets) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item, literal_secrets) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact(item, literal_secrets) for item in value)
    return value


@dataclass(frozen=True, slots=True)
class JournalEvent:
    seq: int
    at: str  # ISO-8601 UTC, supplied by the caller's `now`, never a clock read inside.
    run_id: str
    kind: str
    fields: dict[str, Any]


class Journal:
    """Appends one JSON object per line to ``path``, flushing after every write.

    A crashed run must leave a readable prefix, which is what the per-line flush
    buys: nothing later than the last completed ``event()`` call is lost, and
    nothing later than that call is half-written either.
    """

    def __init__(self, path: Path, run_id: str, *, now: Callable[[], str]) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._run_id = run_id
        self._now = now
        self._seq = 0
        self._events: list[JournalEvent] = []
        self._literal_secrets: tuple[str, ...] = ()
        # newline="" pins the on-disk line ending to exactly "\n" on every platform
        # (including Windows' default text-mode translation), matching the JSONL
        # convention `read_journal` and the fixture reader in `client.py` assume.
        self._fh = self._path.open("w", encoding="utf-8", newline="")

    def redact_literal(self, secret: str | None) -> None:
        """Register a secret to be scrubbed byte-for-byte, in addition to the
        ``sk-``-shape pattern `_redact` already applies.

        ``OpenAICompatClient`` calls this once, at construction, with its configured
        ``api_key`` -- the fix for reviewer finding 13 (spec_fixes_round1.md C3): the
        regex alone misses ``sk-proj-...``, ``sk-or-v1-...`` and any non-``sk`` gateway
        token, but the journal always knows the literal value the caller must never
        leak, regardless of what shape it happens to be. A falsy or too-short value is
        a silent no-op rather than an error -- ``replay`` mode legitimately has no key
        at all, and a key shorter than `_MIN_LITERAL_SECRET_LEN` would over-redact
        unrelated text.
        """
        if secret and len(secret) >= _MIN_LITERAL_SECRET_LEN:
            self._literal_secrets = (*self._literal_secrets, secret)

    def event(self, kind: str, **fields: Any) -> JournalEvent:
        if kind not in EVENT_KINDS:
            valid = ", ".join(sorted(EVENT_KINDS))
            raise ValueError(f"unknown journal event kind {kind!r}; expected one of {valid}")
        collision = _RESERVED_KEYS.intersection(fields)
        if collision:
            raise ValueError(
                f"event field(s) {sorted(collision)} collide with reserved journal keys "
                f"{sorted(_RESERVED_KEYS)}"
            )

        redacted_fields = {key: _redact(item, self._literal_secrets) for key, item in fields.items()}
        record = JournalEvent(
            seq=self._seq, at=self._now(), run_id=self._run_id, kind=kind, fields=redacted_fields,
        )

        # Keys in this exact order on the wire: seq, at, run_id, kind, then the
        # caller's fields flattened at the top level -- `fields` is only the
        # in-memory grouping (spec_contracts.md S1).
        wire: dict[str, Any] = {"seq": record.seq, "at": record.at, "run_id": record.run_id, "kind": record.kind}
        wire.update(redacted_fields)
        self._fh.write(json.dumps(wire, ensure_ascii=False) + "\n")
        self._fh.flush()

        self._seq += 1
        self._events.append(record)
        return record

    def close(self) -> None:
        if not self._fh.closed:
            self._fh.close()

    @property
    def path(self) -> Path:
        return self._path

    @property
    def events(self) -> tuple[JournalEvent, ...]:
        return tuple(self._events)


def read_journal(path: Path) -> list[JournalEvent]:
    """Parse a journal file back into ``JournalEvent``\\ s.

    The only input :func:`derive_state` needs, so a reviewer holding just this file
    -- no database, no live run -- can reconstruct what happened.
    """
    events: list[JournalEvent] = []
    text = Path(path).read_text(encoding="utf-8")
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        wire = json.loads(line)
        kind = wire.pop("kind")
        if kind not in EVENT_KINDS:
            valid = ", ".join(sorted(EVENT_KINDS))
            raise ValueError(
                f"{path}: unknown journal event kind {kind!r}; expected one of {valid}"
            )
        seq = wire.pop("seq")
        at = wire.pop("at")
        run_id = wire.pop("run_id")
        events.append(JournalEvent(seq=seq, at=at, run_id=run_id, kind=kind, fields=wire))
    return events


@dataclass(frozen=True, slots=True)
class ToolCallRecord:
    """One ``tool_call`` event, as recorded in the iteration it belongs to."""

    iteration: int | None
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True, slots=True)
class IterationState:
    """One trip around the propose/evaluate loop, reconstructed from its events."""

    index: int
    proposal: dict[str, Any] | None
    evaluation: dict[str, Any] | None
    score: float | None
    gate: dict[str, Any] | None


@dataclass(frozen=True, slots=True)
class RunState:
    run_id: str
    role: str
    episode_id: str
    cursor: str
    iterations: tuple[IterationState, ...]
    outcome: str | None
    final_score: float | None
    tool_calls: tuple[ToolCallRecord, ...]
    token_usage: dict[str, int]
    #: Proposer/evaluator (etc.) model ids, keyed by the `agent` tag on the
    #: `llm_request` that used them -- so independence is verifiable after the
    #: fact rather than assumed (design doc S1/S6).
    models: dict[str, str]
    wall_ms: int | None


# Per-iteration event kinds that `derive_state` groups by their `iteration` field.
_ITERATION_KINDS = frozenset({"iteration_started", "iteration_finished", "proposal", "evaluation", "score", "gate"})


def derive_state(events: Sequence[JournalEvent]) -> RunState:
    """Replay a run's journal into the shape a reviewer or a later stage reads.

    Works from ``events`` alone -- typically :func:`read_journal`'s return value --
    with no other input. That is the property that makes the journal a fixture as
    well as a log (design doc S6).
    """
    run_id = ""
    role = ""
    episode_id = ""
    cursor = ""
    outcome: str | None = None
    final_score: float | None = None
    wall_ms: int | None = None
    models: dict[str, str] = {}
    token_usage: dict[str, int] = {}
    tool_calls: list[ToolCallRecord] = []
    iterations_by_index: dict[int, dict[str, Any]] = {}

    for ev in events:
        if ev.kind not in EVENT_KINDS:
            valid = ", ".join(sorted(EVENT_KINDS))
            raise ValueError(f"unknown journal event kind {ev.kind!r}; expected one of {valid}")
        if ev.run_id:
            run_id = ev.run_id

        if ev.kind == "run_started":
            role = ev.fields.get("role", role)
            episode_id = ev.fields.get("episode_id", episode_id)
            cursor = ev.fields.get("cursor", cursor)
        elif ev.kind == "run_finished":
            outcome = ev.fields.get("outcome", outcome)
            final_score = ev.fields.get("final_score", final_score)
            wall_ms = ev.fields.get("wall_ms", wall_ms)
        elif ev.kind == "llm_request":
            agent = ev.fields.get("agent")
            model = ev.fields.get("model")
            if agent and model:
                models[agent] = model
        elif ev.kind == "llm_response":
            for key, value in (ev.fields.get("usage") or {}).items():
                if isinstance(value, int):
                    token_usage[key] = token_usage.get(key, 0) + value
        elif ev.kind == "tool_call":
            tool_calls.append(
                ToolCallRecord(
                    iteration=ev.fields.get("iteration"),
                    id=ev.fields.get("id", ""),
                    name=ev.fields.get("name", ""),
                    arguments=ev.fields.get("arguments", {}),
                )
            )
        elif ev.kind in _ITERATION_KINDS:
            index = ev.fields.get("iteration")
            if index is None:
                continue
            bucket = iterations_by_index.setdefault(index, {"index": index})
            if ev.kind == "proposal":
                bucket["proposal"] = {k: v for k, v in ev.fields.items() if k != "iteration"}
            elif ev.kind == "evaluation":
                bucket["evaluation"] = {k: v for k, v in ev.fields.items() if k != "iteration"}
            elif ev.kind == "score":
                bucket["score"] = ev.fields.get("value")
            elif ev.kind == "gate":
                bucket["gate"] = {k: v for k, v in ev.fields.items() if k != "iteration"}

    iterations = tuple(
        IterationState(
            index=index,
            proposal=data.get("proposal"),
            evaluation=data.get("evaluation"),
            score=data.get("score"),
            gate=data.get("gate"),
        )
        for index, data in sorted(iterations_by_index.items())
    )

    return RunState(
        run_id=run_id,
        role=role,
        episode_id=episode_id,
        cursor=cursor,
        iterations=iterations,
        outcome=outcome,
        final_score=final_score,
        tool_calls=tuple(tool_calls),
        token_usage=token_usage,
        models=models,
        wall_ms=wall_ms,
    )
