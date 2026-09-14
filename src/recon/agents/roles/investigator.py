"""The Exception Investigator: single pass, tool-calling loop, cited prose out.

Runs on `AgentSettings.investigator_model` (design S:8.6: "`Explain` runs the Exception
Investigator. Single pass with tool calls."). This module does **not** use
`harness.run_until` -- there is no propose/evaluate/score/gate here, no checklist, no
threshold. `spec_prompts_roles.md` S:A.2's own "Shape" line says it plainly: "Single
pass. One system message, one user message, then tool rounds, then prose." This module
is exactly that shape.

**Budgets are hard-coded module constants, not read from `AgentSettings`.** That
dataclass's `max_tool_rounds` (default 8) is the *Proposer's* per-iteration figure
(`spec_prompts_roles.md` S:B.1: "Per-iteration tool budget: 3 rounds, 8 calls" -- note
even there the config field's default of 8 matches the spec's *call* count, not its
*round* count of 3, which this coder's report flags as a naming ambiguity in Coder 1's
file). The Investigator's own table (S:A.2) is a different five numbers entirely: 5 tool
rounds, 12 total calls, 90s wall clock, 1 repair turn. Reusing one config field for two
roles' different budgets would silently couple them, so these five live here instead --
still "budgets ... live in the orchestration code, never in the prompt" (design S:2),
just not shared config.

**Tool name mismatch, reported rather than silently patched over.** `spec_prompts_roles.md`
S:0's tool roster and this prompt's own text (`prompts/investigator.py`'s
`ITERATION_1_USER_TURN`: "Start with get_episode_dossier.") both name a tool
`get_episode_dossier`. The tool `tools.py` (Coder 2) actually registers is named
`get_episode`; the spec's `get_bank_match` is registered as `get_cash_match`. Neither
`tools.py` nor `prompts/investigator.py` is this coder's file, and the frozen registry
in `tools.TOOLS` is what a model can actually call regardless of what any prompt calls
it, so this module wires the *real* tool schemas (which carry the real names and
descriptions) and lets the model discover the correct name from the `tools` array it is
given -- exactly the channel a real tool-calling model uses, prose notwithstanding. See
this coder's report for the two-name list in full.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from recon.agents.client import LLMClient, LLMResponse
from recon.agents.envelope import ToolEnvelope
from recon.agents.prompts import investigator as investigator_prompt
from recon.agents.schemas import Citation, CitationKind, InvestigatorReport
from recon.agents.scorers import render_tool_result
from recon.agents.tools import ToolContext, dispatch, tool_names, wire_schemas

__all__ = ["InvestigatorOutcome", "run_investigator"]

#: `spec_prompts_roles.md` S:A.2, "Budgets -- in code, never in the prompt."
MAX_TOOL_ROUNDS = 5
MAX_TOOL_CALLS = 12
#: Wall clock (90s) is not enforced here -- see this module's docstring on
#: `AgentSettings` carrying no per-role wall-clock field. A future caller wanting it
#: enforced can wrap `run_investigator` with its own deadline; nothing below reads a
#: clock (house style, `.agents/specs/spec_contracts.md` S8: "no wall-clock reads
#: inside library code").

_READ_TOOL_NAMES: tuple[str, ...] = tuple(n for n in tool_names() if n != "create_mock_work_item")

#: `spec_prompts_roles.md` S:0, the citation grammar, verbatim.
_CITATION_RE = re.compile(r"\[\[(raw|event|calc|verdict):([A-Za-z0-9_.\-]+)\]\]")
#: S:A.2 post-verification step 3: "every digit-run of length >= 2."
_DIGIT_RUN_RE = re.compile(r"\d{2,}")

_SECTION_HEADINGS: tuple[str, ...] = (
    "What happened",
    "Why it is open",
    "What I could not determine",
    "What a human should check first",
)
_SECTION_RE = re.compile(
    r"^##\s+(" + "|".join(re.escape(h) for h in _SECTION_HEADINGS) + r")\s*$",
    re.MULTILINE,
)


@dataclass(frozen=True, slots=True)
class InvestigatorOutcome:
    """What one pass produced, or why it stopped early.

    Mirrors the three ways a pass can end (`spec_prompts_roles.md` S:A.2 "What ends the
    pass"): `report` is non-`None` on the normal and cap paths; `typed_reason` is
    non-`None` only on the hard-fail path (item 3, `episode_not_found`) -- exactly one
    of the two is set, never both, never neither.
    """

    report: InvestigatorReport | None
    typed_reason: str | None
    unsourced_figures: tuple[str, ...] = ()


def run_investigator(
    *,
    client: LLMClient,
    tool_ctx: ToolContext,
    model: str,
    episode_id: str,
    cursor: str,
    verdict_glossary: str,
) -> InvestigatorOutcome:
    """Run one Investigator pass. No network of its own -- `client` is injected."""
    system = investigator_prompt.render(
        episode_id=episode_id, cursor=cursor, verdict_glossary=verdict_glossary, fence_nonce=tool_ctx.nonce,
    )
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": investigator_prompt.render_iteration1_user_turn(episode_id=episode_id, cursor=cursor)},
    ]
    tool_schemas = wire_schemas(_READ_TOOL_NAMES)

    seen_raw_ids: set[str] = set()
    dossier_len = 0
    tool_texts: list[str] = []
    total_calls = 0

    for round_index in range(MAX_TOOL_ROUNDS):  # ceiling first, unconditional -- same construction as harness.run_until (CrewAI #3847)
        response = _call(client, model, messages, round_index, tools=tool_schemas, tool_choice="auto")

        if not response.tool_calls:
            prose, unsourced = _finalize_prose(
                client=client, tool_ctx=tool_ctx, model=model, messages=messages,
                prose=response.content or "", tool_texts=tool_texts, next_iteration=round_index + 1,
            )
            return _build_outcome(prose, unsourced, seen_raw_ids, dossier_len, episode_id, cursor)

        messages.append(_assistant_tool_call_message(response))
        hit_call_budget = False
        for call in response.tool_calls:
            if total_calls >= MAX_TOOL_CALLS:
                hit_call_budget = True
                break
            total_calls += 1
            envelope = dispatch(tool_ctx, call.name, call.arguments, iteration=round_index)

            if (
                call.name == "get_episode"
                and dossier_len == 0
                and envelope["status"] == "error"
                and envelope["error_type"] == "not_found"
            ):
                # S:A.2 item 3: the FIRST get_episode_dossier (here, get_episode) call
                # failing not_found is a hard fail, closed -- no prose is generated, and
                # the UI renders the tool's own recovery message. Never a narrated guess.
                return InvestigatorOutcome(report=None, typed_reason="episode_not_found")

            _track_citable_ids(call.name, envelope, seen_raw_ids)
            if call.name == "get_episode" and envelope["status"] == "ok":
                dossier_len = len(envelope["data"].get("timeline", []) or [])

            # Reviewer finding 18: the citation scorer verifies a quote against
            # `render_tool_result(envelope)`. If the model is shown different bytes
            # here, an honest verbatim quote fails a VETO criterion. One renderer,
            # both sides.
            text = render_tool_result(envelope)
            tool_texts.append(text)
            messages.append({"role": "tool", "tool_call_id": call.id, "content": text})

        if hit_call_budget or total_calls >= MAX_TOOL_CALLS:
            break

    # Cap: round MAX_TOOL_ROUNDS completed (or the call budget ran out) with tool calls
    # still outstanding. One repair turn, tool_choice="none", which cannot call tools
    # and therefore always terminates (S:A.2 item 2: "There is no second cap.").
    messages.append({"role": "user", "content": investigator_prompt.TOOL_BUDGET_CAP_MESSAGE})
    response = _call(client, model, messages, MAX_TOOL_ROUNDS, tools=tool_schemas, tool_choice="none")
    prose, unsourced = _finalize_prose(
        client=client, tool_ctx=tool_ctx, model=model, messages=messages,
        prose=response.content or "", tool_texts=tool_texts, next_iteration=MAX_TOOL_ROUNDS + 1,
    )
    return _build_outcome(prose, unsourced, seen_raw_ids, dossier_len, episode_id, cursor)


def _call(
    client: LLMClient,
    model: str,
    messages: list[dict[str, Any]],
    iteration: int,
    *,
    tools: list[dict[str, Any]],
    tool_choice: str,
) -> LLMResponse:
    """One model call, agent-tagged `"investigator"`.

    The client journals it, not this function. An earlier revision emitted its own
    `llm_request`/`llm_response` pair here because `complete()` had no `agent`
    parameter and `derive_state`'s per-role `models` dict could not otherwise
    populate. That workaround logged the messages this role had assembled rather
    than the bytes that actually went out, and double-counted every call. `agent`
    and `iteration` are parameters now, so the single journaller sits where the
    request leaves the process -- which is the only place "model-visible means
    logged" (design doc S6) can be literally true.
    """
    return client.complete(
        agent="investigator",
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


def _track_citable_ids(name: str, envelope: ToolEnvelope, seen_raw_ids: set[str]) -> None:
    if envelope["status"] != "ok" or not isinstance(envelope["data"], dict):
        return
    data = envelope["data"]
    if name == "get_raw_record" and data.get("raw_id") is not None:
        seen_raw_ids.add(str(data["raw_id"]))
    for event in data.get("timeline", []) or []:
        source = event.get("source") or {}
        if source.get("raw_id") is not None:
            seen_raw_ids.add(str(source["raw_id"]))


def _unsourced_digit_runs(prose: str, tool_texts: list[str]) -> list[str]:
    """S:A.2 post-verification step 3, literally: every digit-run of length >= 2 in the
    prose must appear as a substring of some tool result received this pass.

    Unlike the Proposer's `scorers.no_unsourced_number` (masking eleven exempt classes
    -- NDC, NPI, episode id, dates, ...), the spec gives the Investigator no such
    exemption list, so none is invented here. In practice an episode id or an NDC the
    model quotes is *already* sourced, because the tool result that supplied it is a
    JSON blob containing that same digit run verbatim -- the exemption the Proposer
    needs (to avoid re-deriving those classes from scratch) falls out for free here
    from checking against raw tool-result text rather than a canonicalised number set.
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
    tool_ctx: ToolContext,
    model: str,
    messages: list[dict[str, Any]],
    prose: str,
    tool_texts: list[str],
    next_iteration: int,
) -> tuple[str, tuple[str, ...]]:
    """One repair turn on an unsourced figure (S:A.2 post-verification step 3), never
    silently dropped on a second failure."""
    violations = _unsourced_digit_runs(prose, tool_texts)
    if not violations:
        return prose, ()

    messages.append({"role": "assistant", "content": prose})
    messages.append({"role": "user", "content": investigator_prompt.render_figure_repair(figures=", ".join(violations))})
    response = _call(client, model, messages, next_iteration, tools=[], tool_choice="none")
    repaired = response.content or ""

    still_unsourced = tuple(_unsourced_digit_runs(repaired, tool_texts))
    if still_unsourced:
        # "It is never silently dropped -- that would hide the exact failure the
        # assignment is testing for." `unsourced_figure` is now its own event kind,
        # because this is the one line a reviewer auditing the never-compute-a-number
        # claim would grep for, and it should not be a field on some other event.
        tool_ctx.journal.event(
            "unsourced_figure", iteration=next_iteration, agent="investigator",
            model=response.model, figures=list(still_unsourced),
        )
        for figure in still_unsourced:
            repaired = repaired.replace(figure, f"~~{figure}~~")
    return repaired, still_unsourced


def _extract_citations(prose: str) -> tuple[Citation, ...]:
    """Every `[[kind:ref]]` token, parsed -- no verification yet (that is
    `_verified_citations`). An unparseable bracket form simply does not match this
    regex and renders as plain text, per S:A.2's own "Post-verification" step 1."""
    return tuple(Citation(kind=CitationKind(m.group(1)), ref=m.group(2)) for m in _CITATION_RE.finditer(prose))


def _verified_citations(prose: str, seen_raw_ids: set[str], dossier_len: int) -> tuple[Citation, ...]:
    """S:A.2 post-verification step 2, for the two kinds this module can check cheaply:
    `raw:R` against the raw ids this pass actually saw, `event:N` against the dossier's
    own timeline length. `calc:F` and `verdict:C` are accepted unverified -- the spec's
    own risk framing for this step is explicit that an unverified *citation* (unlike an
    unverified *figure*) "degrades gracefully ... a broken link is a UI annoyance," so a
    full state-space/field cross-check for those two kinds is not worth the added
    dependency on `recon.reference`/`recon.domain` this coder's file does not otherwise
    need. A citation that fails verification is simply dropped -- silently rendering as
    plain text is the harness's job upstream, not this function's.
    """
    verified = []
    for citation in _extract_citations(prose):
        if citation.kind is CitationKind.RAW:
            if citation.ref in seen_raw_ids:
                verified.append(citation)
        elif citation.kind is CitationKind.EVENT:
            if citation.ref.isdigit() and 0 <= int(citation.ref) < dossier_len:
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
    prose: str, unsourced: tuple[str, ...], seen_raw_ids: set[str], dossier_len: int, episode_id: str, cursor: str,
) -> InvestigatorOutcome:
    sections = _split_sections(prose)
    report = InvestigatorReport(
        episode_id=episode_id,
        cursor=cursor,
        what_happened=sections.get("What happened", ""),
        why_it_is_open=sections.get("Why it is open", ""),
        what_i_could_not_determine=sections.get("What I could not determine", ""),
        what_a_human_should_check_first=sections.get("What a human should check first", ""),
        citations=_verified_citations(prose, seen_raw_ids, dossier_len),
    )
    return InvestigatorOutcome(report=report, typed_reason=None, unsourced_figures=unsourced)
