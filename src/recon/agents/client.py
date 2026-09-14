"""The wire client: one seam between three agent roles and any OpenAI-compatible
chat-completions API.

Provider-agnostic on purpose (design doc S8.5) -- the provider is a ``base_url`` and
a model string, never a vendor SDK. That is what lets the proposer and the evaluator
run on different providers (the documented mitigation for self-preference bias)
without a second integration, and it is what makes ``ReplayClient`` possible: a
recorded trace and a live client satisfy the exact same ``LLMClient`` protocol, so
the eval set runs in CI with no key and no network.

Every call is journaled unconditionally, before and after, because the design's
invariant is *"model-visible means logged"* (design doc S6) -- a request that never
gets a response still has to leave a readable trace of what was sent.

═══ Provider facts, measured this session -- encoded here, not re-probed ══════════

Two DeepSeek models sit behind this client: ``deepseek-chat`` (serves
``deepseek-flash``, non-thinking) and ``deepseek-v4-pro`` (thinking, returns
``reasoning_content``). Measured against the live API:

* ``tool_choice={"type": "function", ...}`` (forced tool-use) -- works on
  ``deepseek-chat``; HTTP 400 ``"Thinking mode does not support this tool_choice"``
  on ``deepseek-v4-pro``.
* ``response_format={"type": "json_schema"}`` -- HTTP 400 ``"This response_format
  type is unavailable now"`` on *both* models.
* ``response_format={"type": "json_object"}`` -- works, but does not honour an
  enum (a ``Literal`` field came back as free text).

So forced tool-use is the only structured-output path available at all, and it only
works on the non-thinking model. :func:`supports_forced_tool_choice` encodes that so
callers branch on capability, not on a hard-coded model name.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Protocol

import httpx

from recon.agents.journal import Journal

__all__ = [
    "ToolCall",
    "LLMResponse",
    "LLMClient",
    "LLMError",
    "ReplayMiss",
    "OpenAICompatClient",
    "RecordingClient",
    "ReplayClient",
    "supports_forced_tool_choice",
    "request_digest",
]


@dataclass(frozen=True, slots=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]  # already json.loads'd
    raw_arguments: str  # the untouched string, for when the parse itself is evidence


@dataclass(frozen=True, slots=True)
class LLMResponse:
    content: str | None
    tool_calls: tuple[ToolCall, ...]
    reasoning: str | None  # deepseek-v4-pro's `reasoning_content`; absent elsewhere
    finish_reason: str
    model: str  # as reported by the provider, not as requested
    usage: dict[str, int]
    raw: dict[str, Any]  # the whole response body, for the journal


class LLMClient(Protocol):
    def complete(
        self,
        *,
        agent: str,
        # reviewer finding (spec_fixes_round1.md C1): the role issuing this call
        # ("proposer", "evaluator", "investigator", ...) -- threaded straight into
        # the `llm_request`/`llm_response`/`llm_error` journal events so proposer/
        # evaluator independence (design doc S1) is verifiable after the fact
        # instead of assumed. Required, not defaulted: a call that cannot name its
        # agent should not silently journal unlabeled.
        messages: list[dict[str, Any]],
        model: str,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        iteration: int | None = None,
    ) -> LLMResponse: ...


class LLMError(Exception):
    """Raised when :class:`OpenAICompatClient` exhausts its retry budget."""

    def __init__(self, message: str, *, status: int | None, body: str, attempts: int) -> None:
        super().__init__(message)
        self.status = status
        self.body = body
        self.attempts = attempts


class ReplayMiss(Exception):
    """Raised by :class:`ReplayClient` when a request has no recorded match."""

    def __init__(self, message: str, *, digest: str, nearest: str | None) -> None:
        super().__init__(message)
        self.digest = digest
        self.nearest = nearest


def supports_forced_tool_choice(model: str) -> bool:
    """Whether a forced ``tool_choice={"type": "function", ...}`` works on ``model``.

    Measured this session: ``deepseek-chat`` accepts it, ``deepseek-v4-pro`` returns
    HTTP 400 ``"Thinking mode does not support this tool_choice"``. Every thinking
    model DeepSeek ships carries the ``v4-pro`` marker, so that substring is the
    capability test rather than an exact-match model list that would need updating
    every time a new thinking model ships.
    """
    return "v4-pro" not in model


def request_digest(
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    tool_choice: str | dict[str, Any] | None,
) -> str:
    """A stable digest of the four fields that define "the same request".

    ``blake2b``, never the builtin ``hash()`` -- ``hash()`` is salted per process by
    ``PYTHONHASHSEED`` and would not reproduce across the record and replay
    processes (the same ban as ``recon.rng``; see START_HERE.md S8).
    """
    canonical = json.dumps(
        {"model": model, "messages": messages, "tools": tools, "tool_choice": tool_choice},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.blake2b(canonical.encode("utf-8"), digest_size=16).hexdigest()


def _parse_tool_call(raw: dict[str, Any]) -> ToolCall:
    function = raw.get("function") or {}
    raw_arguments = function.get("arguments") or ""
    try:
        arguments = json.loads(raw_arguments) if raw_arguments else {}
    except json.JSONDecodeError:
        # A malformed argument string is evidence, not a crash: `raw_arguments`
        # keeps it verbatim so a role can see exactly what the model produced.
        arguments = {}
    return ToolCall(id=raw.get("id", ""), name=function.get("name", ""), arguments=arguments, raw_arguments=raw_arguments)


def _parse_response(payload: dict[str, Any]) -> LLMResponse:
    choices = payload.get("choices") or [{}]
    choice = choices[0]
    message = choice.get("message") or {}
    tool_calls = tuple(_parse_tool_call(tc) for tc in (message.get("tool_calls") or []))
    return LLMResponse(
        content=message.get("content"),
        tool_calls=tool_calls,
        reasoning=message.get("reasoning_content"),
        finish_reason=choice.get("finish_reason", ""),
        model=payload.get("model", ""),
        usage=payload.get("usage") or {},
        raw=payload,
    )


_MAX_ATTEMPTS = 3
#: 0.5s / 2s / 6s, indexed by (attempt number - 1). Honoured only when the provider
#: does not send `Retry-After` (spec_contracts.md S2).
_BACKOFF_SECONDS: tuple[float, ...] = (0.5, 2.0, 6.0)


def _retry_delay(attempt: int, retry_after: str | None) -> float:
    if retry_after:
        try:
            return float(retry_after)
        except ValueError:
            pass
    return _BACKOFF_SECONDS[attempt - 1]


class OpenAICompatClient:
    """``httpx`` POST to ``{base_url}/chat/completions``, journaled unconditionally.

    Retries 429 and 5xx up to three attempts total with 0.5s/2s/6s backoff,
    honouring ``Retry-After`` when the provider sends one. Anything else -- a
    non-retryable 4xx, or attempts exhausted -- raises :class:`LLMError` carrying
    ``status``, ``body`` and ``attempts``.

    ``client`` and ``sleep`` are injectable so tests exercise the retry state
    machine against a fake transport with no real waiting and no network.
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None,
        journal: Journal,
        timeout_s: float = 120.0,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._journal = journal
        self._owns_client = client is None
        self._client = client if client is not None else httpx.Client(timeout=timeout_s)
        self._sleep = sleep
        # reviewer finding 13 (spec_fixes_round1.md C3): `Journal`'s own `_SECRET_PATTERN`
        # only catches keys shaped exactly "sk-" + alnum, so "sk-proj-...", "sk-or-v1-..."
        # and a non-"sk" gateway token all passed through unredacted. Registering the
        # actual configured key here scrubs it byte-for-byte regardless of shape.
        self._journal.redact_literal(api_key)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def _headers(self) -> dict[str, str]:
        if not self._api_key:
            return {}
        return {"Authorization": f"Bearer {self._api_key}"}

    @staticmethod
    def _build_body(
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        tool_choice: str | dict[str, Any] | None,
        max_tokens: int | None,
        temperature: float | None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"model": model, "messages": messages}
        if tools is not None:
            body["tools"] = tools
        if tool_choice is not None:
            body["tool_choice"] = tool_choice
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        if temperature is not None:
            body["temperature"] = temperature
        return body

    def _post_with_retry(self, body: dict[str, Any]) -> dict[str, Any]:
        attempt = 0
        while True:
            attempt += 1
            try:
                response = self._client.post(
                    f"{self._base_url}/chat/completions", json=body, headers=self._headers(),
                )
            except httpx.HTTPError as exc:
                if attempt >= _MAX_ATTEMPTS:
                    raise LLMError(
                        f"transport error contacting {self._base_url}: {exc}",
                        status=None, body=str(exc), attempts=attempt,
                    ) from exc
                self._sleep(_BACKOFF_SECONDS[attempt - 1])
                continue

            if response.status_code == 200:
                return response.json()

            retryable = response.status_code == 429 or response.status_code >= 500
            if retryable and attempt < _MAX_ATTEMPTS:
                self._sleep(_retry_delay(attempt, response.headers.get("Retry-After")))
                continue

            raise LLMError(
                f"{self._base_url}/chat/completions returned {response.status_code}: {response.text}",
                status=response.status_code, body=response.text, attempts=attempt,
            )

    def complete(
        self,
        *,
        agent: str,
        messages: list[dict[str, Any]],
        model: str,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        iteration: int | None = None,
    ) -> LLMResponse:
        body = self._build_body(model, messages, tools, tool_choice, max_tokens, temperature)
        digest = request_digest(model, messages, tools, tool_choice)

        # `agent` on every event, not only `model` -- reviewer finding (spec_fixes_round1.md
        # C1): without it `derive_state.models` cannot be keyed per role, so
        # proposer/evaluator independence was assumed rather than checkable. Logged
        # before the call, with the exact outgoing body, so a crash mid-request still
        # leaves a byte-reconstructable request in the journal (design doc S6).
        #
        # `iteration` exists so this can be the ONLY journaller of llm_* events. The
        # roles used to emit their own agent-tagged pair alongside this one, back when
        # `agent` did not exist here; that logged the messages a role had assembled
        # rather than the bytes that actually went out, and double-counted every call
        # in `derive_state`. One journaller, sited where the request leaves the
        # process, is what makes "model-visible means logged" literally true.
        self._journal.event(
            "llm_request", agent=agent, iteration=iteration, digest=digest, model=model, body=body
        )

        result: LLMResponse | None = None
        failure: LLMError | None = None
        try:
            payload = self._post_with_retry(body)
            result = _parse_response(payload)
            return result
        except LLMError as exc:
            failure = exc
            raise
        finally:
            # Unconditional: llm_response/llm_error is journaled either way, which is
            # what makes "model-visible means logged" true even on the failure path.
            if failure is not None:
                self._journal.event(
                    "llm_error", agent=agent, iteration=iteration, digest=digest, model=model,
                    status=failure.status, body=failure.body, attempts=failure.attempts,
                )
            elif result is not None:
                self._journal.event(
                    "llm_response", agent=agent, iteration=iteration, digest=digest,
                    model=result.model, finish_reason=result.finish_reason,
                    usage=result.usage, raw=result.raw,
                )


class RecordingClient:
    """Delegates to ``inner``, and mirrors every exchange onto a fixture file.

    ``inner`` does its own journaling (if it is an :class:`OpenAICompatClient`);
    this class's only job is writing the ``{request, response}`` pairs that
    :class:`ReplayClient` later reads with no key and no network.
    """

    def __init__(self, inner: LLMClient, fixture_dir: Path, run_id: str) -> None:
        self._inner = inner
        self._path = Path(fixture_dir) / f"{run_id}.jsonl"
        self._path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def path(self) -> Path:
        return self._path

    def complete(
        self,
        *,
        agent: str,
        messages: list[dict[str, Any]],
        model: str,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        iteration: int | None = None,
    ) -> LLMResponse:
        # Forwarded, never journaled here -- `inner` (if it is an OpenAICompatClient)
        # is what stamps `agent` onto the llm_request/llm_response pair; this class's
        # only job is mirroring the exchange onto the fixture file.
        response = self._inner.complete(
            agent=agent, messages=messages, model=model, tools=tools, tool_choice=tool_choice,
            max_tokens=max_tokens, temperature=temperature,
        )
        record = {
            "request": {"model": model, "messages": messages, "tools": tools, "tool_choice": tool_choice},
            "response": response.raw,
        }
        with self._path.open("a", encoding="utf-8", newline="") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        return response


def _nearest_request(
    target: dict[str, Any], requests: Sequence[tuple[str, dict[str, Any]]]
) -> tuple[str, dict[str, Any]] | None:
    """The recorded request whose JSON text is textually closest to ``target``.

    Digests carry no useful notion of distance -- blake2b output is designed to
    look unrelated for related inputs -- so "nearest" compares the request bodies
    themselves, and only the winner's digest is surfaced in the error.
    """
    if not requests:
        return None
    target_text = json.dumps(target, sort_keys=True, default=str)
    best: tuple[str, dict[str, Any]] | None = None
    best_ratio = -1.0
    for digest, request in requests:
        ratio = SequenceMatcher(None, target_text, json.dumps(request, sort_keys=True, default=str)).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best = (digest, request)
    return best


class ReplayClient:
    """Replays a :class:`RecordingClient` fixture. No key, no network.

    Matches a request to a recorded one by :func:`request_digest` over
    ``(model, messages, tools, tool_choice)``. A miss raises :class:`ReplayMiss`
    naming the digest and the nearest recorded request, which is what makes an
    unexpected prompt change visible as a clear failure rather than a silent
    fall-through to live traffic.
    """

    def __init__(self, fixture_path: Path) -> None:
        self._path = Path(fixture_path)
        self._by_digest: dict[str, dict[str, Any]] = {}
        self._requests: list[tuple[str, dict[str, Any]]] = []
        for line in self._path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            request = record["request"]
            digest = request_digest(
                request["model"], request["messages"], request.get("tools"), request.get("tool_choice"),
            )
            self._by_digest[digest] = record["response"]
            self._requests.append((digest, request))

    def complete(
        self,
        *,
        agent: str,
        messages: list[dict[str, Any]],
        model: str,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        iteration: int | None = None,
    ) -> LLMResponse:
        # Accepted for LLMClient signature parity (a role must be able to swap a live
        # client for a replay client with no call-site change) but not part of the
        # match key: `request_digest` is deliberately over (model, messages, tools,
        # tool_choice) only -- "the same request" is a property of what was asked,
        # not of who is asking -- and this class does no journaling of its own to tag.
        del agent
        digest = request_digest(model, messages, tools, tool_choice)
        payload = self._by_digest.get(digest)
        if payload is None:
            target = {"model": model, "messages": messages, "tools": tools, "tool_choice": tool_choice}
            nearest = _nearest_request(target, self._requests)
            nearest_desc = f"{nearest[0]} (model={nearest[1].get('model')!r})" if nearest else "none recorded"
            raise ReplayMiss(
                f"no recorded request in {self._path} matches digest {digest}; "
                f"nearest recorded request: {nearest_desc}",
                digest=digest,
                nearest=nearest[0] if nearest else None,
            )
        return _parse_response(payload)
