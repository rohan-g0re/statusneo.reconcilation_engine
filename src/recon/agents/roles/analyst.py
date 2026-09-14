"""The Portfolio Analyst: single pass, tool-calling loop, cited prose out.

`.agents/specs/spec_analyst.md`, "Role (Agent 2 owns)": "Single pass with tool calls,
the same shape as `roles/investigator.py` -- **not** the propose/evaluate loop. There
is no action being proposed, so there is nothing to gate." This module mirrors
`roles/investigator.py` structurally on purpose -- same budget-as-module-constants
pattern, same `_call` that leaves journalling to the client, same
`scorers.render_tool_result` for every tool message, same one-repair-turn unsourced-
figure check -- because the two roles differ only in *what* they describe (one claim
vs. the whole book), not in *how* a single tool-calling pass works.

**Budgets are agent defaults, not a frozen spec table.** `spec_prompts_roles.md` §A.2
gives the Investigator an explicit five-number table; `spec_analyst.md` gives this role
no equivalent. `MAX_TOOL_ROUNDS`/`MAX_TOOL_CALLS` below are this coder's own judgement
call (a portfolio overview plus one ranked queue plus a little headroom to drill into a
top row), reported here rather than presented as a transcribed spec figure.

**No hard-fail path, unlike the Investigator's `episode_not_found`.** The Investigator's
one hard fail is "the first `get_episode` call 404s" -- a permanent condition, since
`episode_id` is fixed for the whole run and a claim that does not exist will never start
existing partway through a pass. Neither `get_portfolio_overview` (no arguments at all)
nor `get_exception_queue` (`disposition`/`order_by`/`limit`, each independently
recoverable via a retried call with a corrected argument) has an equivalent
un-recoverable failure mode -- there is no episode-shaped identifier here that a bad
first call could 404 on once and for all. `AnalystOutcome.typed_reason` is therefore
carried for interface parity with `InvestigatorOutcome` (and whatever `AnalystOutcome`
consumer in Agent 3's API layer branches on the same "exactly one of report/typed_reason"
shape) but nothing in this module ever sets it to a non-`None` value. Flagged here
because a reader who has just read `investigator.py`'s hard-fail branch will look for
this module's equivalent and should find this paragraph instead of a silent gap.

**The ranking rule, enforced in two places, not one.** `prompts/analyst.py` states
"Ranking is a sort, not a judgement" operationally (call `get_exception_queue` with an
explicit `order_by`, present its rows in the order it returned them, name the sort in
every "biggest"/"most" claim). This module adds nothing that could undo that on the
Python side: no line here sorts, reverses, groups or re-orders a tool result before it
reaches `render_tool_result`, and the post-hoc section split below (`_split_sections`)
copies each heading's body text out verbatim, in the order the model wrote it, exactly
as `investigator._split_sections` does. There is no "reorder-detector" repair loop
mirroring the unsourced-figure one, deliberately: the spec's explicit instruction was to
mirror *the unsourced-figure check specifically*, and inventing a second, structurally
different repair mechanism the spec never asked for is exactly the kind of
un-requested feature this project's own house style (see `docs/decision_ledger.md`)
flags as scope the coder invented rather than was asked to build.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from recon.agents.client import LLMClient, LLMResponse
from recon.agents.envelope import ToolEnvelope, err as err_envelope
from recon.agents.prompts import analyst as analyst_prompt
from recon.agents.schemas import AnalystReport, Citation, CitationKind
from recon.agents.scorers import render_tool_result
from recon.agents.tools import ToolContext, dispatch, tool_names, wire_schemas

__all__ = ["AnalystOutcome", "run_analyst"]

#: This coder's own judgement call -- see the module docstring's "Budgets are agent
#: defaults" paragraph. Smaller than the Investigator's 5 rounds / 12 calls: the
#: expected path is exactly two calls (`get_portfolio_overview`, `get_exception_queue`)
#: plus headroom for a drill-down or a second differently-sorted queue read, not a
#: single claim's full four-feed reconstruction.
MAX_TOOL_ROUNDS = 4
MAX_TOOL_CALLS = 10
#: Wall clock is not enforced here, same as `investigator.py` -- see that module's
#: docstring on `AgentSettings` carrying no per-role wall-clock field; nothing below
#: reads a clock (house style, `.agents/specs/spec_contracts.md` §8).

#: Every read tool, same set `investigator.py` builds -- the Analyst may drill into
#: one episode named at the top of a queue with `get_episode`, `get_raw_record` or
#: `calculate_reconciliation` exactly as the Investigator would, so it is not limited
#: to only the two portfolio-level tools.
_READ_TOOL_NAMES: tuple[str, ...] = tuple(n for n in tool_names() if n != "create_mock_work_item")

#: `prompts/analyst.py`'s "Citations" section, verbatim grammar: the Investigator's
#: four kinds plus `overview`/`queue` (`schemas.CitationKind`'s own docstring explains
#: why these are new members rather than a repurposing of `calc`/`raw`).
_CITATION_RE = re.compile(r"\[\[(raw|event|calc|verdict|overview|queue):([A-Za-z0-9_.\-]+)\]\]")
#: Same post-verification step as `investigator._DIGIT_RUN_RE`: every digit-run of
#: length >= 2 in the prose must appear as a substring of some tool result this pass.
_DIGIT_RUN_RE = re.compile(r"\d{2,}")

#: The key `tools.get_exception_queue` puts its ranked rows under (`tools.py` §2.9:
#: `data["episodes"] = episodes`). Read here, not re-derived, so a rename there is a
#: loud `KeyError`-free no-op here (`.get` on a missing key) rather than a silent
#: divergence -- see `_queue_episode_ids` below.
_QUEUE_ROWS_KEY = "episodes"
_QUEUE_EPISODE_ID_KEY = "episode_id"

_SECTION_HEADINGS: tuple[str, ...] = (
    "State of the book",
    "What is concentrated",
    "What I could not determine",
    "Where to look first",
)
# One or two hashes, and the trailing colon some models add. Measured live: the model
# was asked for "## State of the book" and wrote "# State of the book" -- an h1 rather
# than an h2. The prose was perfect and every figure was sourced, and the whole report
# parsed to four empty strings because of one missing character.
#
# A parser that rejects a correct answer over its heading level is not enforcing
# anything; the headings are a transport convention, not a requirement on the content.
# Be strict about what the sections MEAN and liberal about how they are marked.
_SECTION_RE = re.compile(
    r"^#{1,3}\s+(" + "|".join(re.escape(h) for h in _SECTION_HEADINGS) + r")\s*:?\s*$",
    re.MULTILINE | re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class AnalystOutcome:
    """What one pass produced, or why it stopped early.

    `.agents/specs/spec_analyst.md`'s frozen shape, verbatim: `report` is non-`None`
    on the normal and cap paths; `typed_reason` exists for the same "exactly one of
    the two, never both, never neither" contract `InvestigatorOutcome` uses, though
    (per this module's docstring) nothing here ever populates it.
    """

    report: AnalystReport | None
    typed_reason: str | None
    unsourced_figures: tuple[str, ...] = ()


def run_analyst(
    *,
    client: LLMClient,
    tool_ctx: ToolContext,
    model: str,
    cursor: str,
    question: str | None,
    verdict_glossary: str,
) -> AnalystOutcome:
    """Run one Analyst pass. No network of its own -- `client` is injected."""
    system = analyst_prompt.render(cursor=cursor, verdict_glossary=verdict_glossary, fence_nonce=tool_ctx.nonce)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": analyst_prompt.render_iteration1_user_turn(cursor=cursor, question=question)},
    ]
    tool_schemas = wire_schemas(_READ_TOOL_NAMES)

    seen_raw_ids: set[str] = set()
    seen_queue_episode_ids: set[str] = set()
    tool_texts: list[str] = []
    total_calls = 0

    for round_index in range(MAX_TOOL_ROUNDS):  # ceiling first, unconditional -- same construction as investigator.py
        response = _call(client, model, messages, round_index, tools=tool_schemas, tool_choice="auto")

        if not response.tool_calls:
            prose, unsourced = _finalize_prose(
                client=client, model=model, messages=messages,
                prose=response.content or "", tool_texts=tool_texts, next_iteration=round_index + 1,
            )
            return _build_outcome(prose, unsourced, seen_raw_ids, seen_queue_episode_ids)

        messages.append(_assistant_tool_call_message(response))
        hit_call_budget = False
        for call in response.tool_calls:
            if total_calls >= MAX_TOOL_CALLS:
                # Every declared tool_call gets a reply even when the budget is spent --
                # see investigator.py's identical guard for the DeepSeek 400 this avoids.
                hit_call_budget = True
                messages.append({
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": render_tool_result(
                        err_envelope(
                            "not_permitted",
                            f"the tool-call budget of {MAX_TOOL_CALLS} is spent; write your "
                            "description from what you already have, and name what is missing.",
                        )
                    ),
                })
                continue
            total_calls += 1
            envelope = dispatch(tool_ctx, call.name, call.arguments, iteration=round_index)

            _track_citable_ids(call.name, envelope, seen_raw_ids, seen_queue_episode_ids)

            # Reviewer finding 18 (see investigator.py): the citation scorer verifies a
            # quote against `render_tool_result(envelope)`. One renderer, both sides.
            text = render_tool_result(envelope)
            tool_texts.append(text)
            messages.append({"role": "tool", "tool_call_id": call.id, "content": text})

        if hit_call_budget or total_calls >= MAX_TOOL_CALLS:
            break

    # Cap: round MAX_TOOL_ROUNDS completed (or the call budget ran out) with tool calls
    # still outstanding. One repair turn, tool_choice="none", which cannot call tools
    # and therefore always terminates -- same shape as investigator.py's cap.
    messages.append({"role": "user", "content": analyst_prompt.TOOL_BUDGET_CAP_MESSAGE})
    response = _call(client, model, messages, MAX_TOOL_ROUNDS, tools=tool_schemas, tool_choice="none")
    prose, unsourced = _finalize_prose(
        client=client, model=model, messages=messages,
        prose=response.content or "", tool_texts=tool_texts, next_iteration=MAX_TOOL_ROUNDS + 1,
    )
    return _build_outcome(prose, unsourced, seen_raw_ids, seen_queue_episode_ids)


def _call(
    client: LLMClient,
    model: str,
    messages: list[dict[str, Any]],
    iteration: int,
    *,
    tools: list[dict[str, Any]],
    tool_choice: str,
) -> LLMResponse:
    """One model call, agent-tagged `"analyst"`. The client journals it, not this
    function -- see `investigator._call`'s docstring for why a role must never
    journal its own `llm_*` events."""
    return client.complete(
        agent="analyst",
        iteration=iteration,
        messages=messages,
        model=model,
        tools=tools,
        tool_choice=tool_choice,
    )


def _assistant_tool_call_message(response: LLMResponse) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": response.content,
        "tool_calls": [
            {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.raw_arguments}}
            for c in response.tool_calls
        ],
    }


def _track_citable_ids(
    name: str, envelope: ToolEnvelope, seen_raw_ids: set[str], seen_queue_episode_ids: set[str],
) -> None:
    """Populate the two sets `_verified_citations` checks a `[[raw:R]]`/`[[queue:E]]`
    token against.

    Mirrors `investigator._track_citable_ids` for `raw`/`event`-shaped tool results
    (`get_raw_record`, `get_episode`'s `timeline`) -- a known simplification carried
    over unchanged: a raw id embedded in `get_remittance_detail`'s `claim_lines` or
    `get_cash_match`'s `bank_transactions` rather than `get_episode`'s merged timeline
    is not tracked here, so a `[[raw:R]]` citing one is dropped as unverified rather
    than trusted. That degrades gracefully (`schemas.py`'s own documented tradeoff for
    `calc`/`verdict`), and the realistic drill-down path -- `get_episode` on one
    top-of-queue episode -- is covered.

    Adds `queue`: every `episode_id` in the most recent `get_exception_queue` result's
    `episodes` rows, so `[[queue:E-000812]]` verifies iff that episode actually
    appeared in a queue this pass returned -- the same "cite only what you were shown"
    property `raw:R` gets for `get_raw_record`.
    """
    if envelope["status"] != "ok" or not isinstance(envelope["data"], dict):
        return
    data = envelope["data"]
    if name == "get_raw_record" and data.get("raw_id") is not None:
        seen_raw_ids.add(str(data["raw_id"]))
    for event in data.get("timeline", []) or []:
        source = event.get("source") or {}
        if source.get("raw_id") is not None:
            seen_raw_ids.add(str(source["raw_id"]))
    if name == "get_exception_queue":
        for row in data.get(_QUEUE_ROWS_KEY, []) or []:
            episode_id = row.get(_QUEUE_EPISODE_ID_KEY)
            if episode_id is not None:
                seen_queue_episode_ids.add(str(episode_id))


def _unsourced_digit_runs(prose: str, tool_texts: list[str]) -> list[str]:
    """Identical rule to `investigator._unsourced_digit_runs`: every digit-run of
    length >= 2 in the prose must appear as a substring of some tool result received
    this pass. See that function's docstring for why no exemption list is needed --
    an episode id or a reason code the model quotes is already sourced, because the
    tool result that supplied it is a JSON blob containing that same digit run
    verbatim.
    """
    haystack = "\n".join(tool_texts)
    violations: list[str] = []
    for m in _DIGIT_RUN_RE.finditer(prose):
        token = m.group(0)
        if token not in haystack:
            violations.append(token)
    return violations


def _finalize_prose(
    *,
    client: LLMClient,
    model: str,
    messages: list[dict[str, Any]],
    prose: str,
    tool_texts: list[str],
    next_iteration: int,
) -> tuple[str, tuple[str, ...]]:
    """One repair turn on an unsourced figure, never silently dropped on a second
    failure -- identical shape to `investigator._finalize_prose`."""
    violations = _unsourced_digit_runs(prose, tool_texts)
    if not violations:
        return prose, ()

    messages.append({"role": "assistant", "content": prose})
    messages.append({"role": "user", "content": analyst_prompt.render_figure_repair(figures=", ".join(violations))})
    response = _call(client, model, messages, next_iteration, tools=[], tool_choice="none")
    repaired = response.content or ""

    still_unsourced = tuple(_unsourced_digit_runs(repaired, tool_texts))
    if still_unsourced:
        # Its own event kind, not a field on an llm_response -- see investigator.py's
        # identical reasoning: this is the line a reviewer greps for.
        return _strike_through(repaired, still_unsourced), still_unsourced
    return repaired, ()


def _strike_through(text: str, figures: tuple[str, ...]) -> str:
    for figure in figures:
        text = text.replace(figure, f"~~{figure}~~")
    return text


def _extract_citations(prose: str) -> tuple[Citation, ...]:
    """Every `[[kind:ref]]` token, parsed -- no verification yet (that is
    `_verified_citations`). An unparseable bracket form simply does not match this
    regex and renders as plain text."""
    return tuple(Citation(kind=CitationKind(m.group(1)), ref=m.group(2)) for m in _CITATION_RE.finditer(prose))


def _verified_citations(
    prose: str, seen_raw_ids: set[str], seen_queue_episode_ids: set[str],
) -> tuple[Citation, ...]:
    """Mirrors `investigator._verified_citations`'s two cheaply-checkable kinds, `raw`
    and (there) `event`; here, `raw` and `queue`. `event`, `calc`, `verdict` and
    `overview` are accepted unverified, the same tradeoff `investigator.py` documents
    for `calc`/`verdict`: a full state-space cross-check for those would need a
    dependency this module does not otherwise carry, and an unverified *citation*
    (unlike an unverified *figure*, which the digit-run check above still catches
    regardless of which token sits next to it) degrades gracefully -- a broken link is
    a UI annoyance, not a fabricated number reaching a reader unchallenged.

    Order-preserving: this walks `_extract_citations(prose)` in the order the tokens
    appear in the model's own text and only ever drops entries, so the position of a
    `[[queue:E]]` token that survives is exactly where the model put it -- nothing
    here re-sorts a citation list into, say, tool-return order or episode-id order.
    That is deliberate: "which order the rows come back in" is the model's job to
    report accurately (`prompts/analyst.py`'s ranking rule), not this function's job
    to enforce by rewriting the citations it verifies.
    """
    verified = []
    for citation in _extract_citations(prose):
        if citation.kind is CitationKind.RAW:
            if citation.ref in seen_raw_ids:
                verified.append(citation)
        elif citation.kind is CitationKind.QUEUE:
            if citation.ref in seen_queue_episode_ids:
                verified.append(citation)
        else:
            verified.append(citation)
    return tuple(verified)


def _split_sections(prose: str) -> dict[str, str]:
    matches = list(_SECTION_RE.finditer(prose))
    sections: dict[str, str] = {}
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(prose)
        sections[m.group(1)] = prose[start:end].strip()
    return sections


def _build_outcome(
    prose: str, unsourced: tuple[str, ...], seen_raw_ids: set[str], seen_queue_episode_ids: set[str],
) -> AnalystOutcome:
    sections = _split_sections(prose)
    report = AnalystReport(
        state_of_the_book=sections.get("State of the book", ""),
        what_is_concentrated=sections.get("What is concentrated", ""),
        what_i_could_not_determine=sections.get("What I could not determine", ""),
        where_to_look_first=sections.get("Where to look first", ""),
        citations=_verified_citations(prose, seen_raw_ids, seen_queue_episode_ids),
    )
    return AnalystOutcome(report=report, typed_reason=None, unsourced_figures=unsourced)
