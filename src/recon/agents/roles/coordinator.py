"""The Workflow Coordinator: wires a Proposer session and an Evaluator call into
`harness.run_until`.

Two models, two very different structured-output paths (`.agents/specs/spec_contracts.md`
S:2): the Proposer (`deepseek-chat`) accepts a forced `tool_choice`, so structured output
is unconditional there; the Evaluator (`deepseek-v4-pro`, thinking) returns HTTP 400 on a
forced `tool_choice` and must be driven with `"auto"` plus a one-shot transcription
repair (`spec_prompts_roles.md` S:C.4). `_emit_structured` below is the one function that
knows how to get either model to reliably produce one specific tool call, branching on
`client.supports_forced_tool_choice(model)` rather than on a hard-coded model name -- so
a future model swap changes nothing here.

**The Evaluator never sees the Proposer's `reasoning`.** `EvaluatorInput` (this module)
has no `reasoning` field at all -- unrepresentable, not filtered, matching
`spec_prompts_roles.md` S:C.2's own load-bearing exclusion and its own prescribed test
shape ("`EvaluatorInput` has no `reasoning` field -- unrepresentable, not filtered").
`render_evaluator_user_turn` builds the request from `EvaluatorInput` alone, every
iteration, from scratch -- there is no code path from the Proposer's conversation into
the Evaluator's.

**A work item is proposed, never written, here.** Every tool schema this module wires
for either role excludes `create_mock_work_item` by construction
(`_READ_TOOL_NAMES`, shared with `roles/investigator.py`'s own exclusion) -- the model
is never even offered the write tool, so it cannot call it regardless of what it
decides. The one write path in the whole agent layer is the UI's *Add to-do* button
(design S:8.6), which is outside this module entirely.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from recon.agents.client import LLMClient, LLMResponse, ToolCall, supports_forced_tool_choice
from recon.agents.config import AgentSettings
from recon.agents.envelope import err as err_envelope
from recon.agents.grounding import Clause, select_clauses
from recon.agents.harness import Evaluate, HarnessContext, Propose, RunBudgets, run_until
from recon.agents.journal import Journal
from recon.agents.prompts import evaluator as evaluator_prompt
from recon.agents.prompts import proposer as proposer_prompt
from recon.agents.rubric import Outcome, applicable_judge_criteria
from recon.agents.schemas import EvaluatorVerdict, ProposedAction, SchemaError
from recon.agents.scorers import RecordedToolResult, ScoringInput, render_tool_result
from recon.agents.tools import ToolContext, dispatch, tool_names, wire_schemas

__all__ = [
    "EvaluatorInput",
    "render_evaluator_user_turn",
    "run_coordinator",
]

#: Shared with `roles/investigator.py`: the write tool is never offered to a model.
_READ_TOOL_NAMES: tuple[str, ...] = tuple(n for n in tool_names() if n != "create_mock_work_item")

#: `spec_prompts_roles.md` S:B.1, "Per-iteration tool budget: 3 rounds ..." -- no
#: `AgentSettings` field carries a round count (only a call count -- see below), so
#: this stays a module constant, reset fresh on every harness iteration.
#: Two rounds, not three. The proposer now asks for every tool it needs in one turn
#: (see prompts/proposer.py), so a third round only ever buys a follow-up on an id
#: discovered in the first batch -- rare, and not worth a round-trip on every run.
_PROPOSER_MAX_ROUNDS = 2

#: Ceiling on the escalation call to the thinking model.
#:
#: The fallback exists to rescue a run the cheap model could not encode, not to think
#: about the episode from scratch. Uncapped it does the latter: measured on one live
#: run, two escalations produced 53,670 and 33,873 reasoning tokens and turned a
#: 20-second Decide into 355 seconds. A checklist verdict is a few thousand tokens of
#: output; anything past this ceiling is the model reasoning for its own sake, and
#: cutting it off costs a rescue attempt rather than a correct answer.
_FALLBACK_MAX_TOKENS = 8_000

#: Ceiling on any structured-output call, on any model.
#:
#: A `ProposedAction` runs to roughly 2,000 output tokens and an `EvaluatorVerdict` to
#: about the same; the rest of a thinking model's budget goes on reasoning nobody
#: reads. Measured on `deepseek-v4-pro` with no ceiling: one proposer emit produced
#: 12,182 output tokens of which 11,246 were reasoning, and took 149 seconds -- for a
#: payload of eight fields. Three of those in a two-iteration run is most of a
#: ten-minute wait.
#:
#: This bounds thinking, not the answer. A verdict that genuinely needs more than this
#: is not a verdict, and a truncated tool call fails closed anyway: `client.py`'s
#: parser turns unparseable arguments into `{}` and the schema rejects it.
_EMIT_MAX_TOKENS = 6_000

#: The evaluator needs more room than the proposer, and the difference is structural.
#:
#: A `ProposedAction` is eight fields about one action. An `EvaluatorVerdict` is a
#: reasoned finding for every applicable criterion -- thirteen of them on a typical
#: episode, each with its own reasoning and cited span. On a thinking model the cap
#: covers reasoning AND the emitted call from one budget, so a ceiling tight enough to
#: bound the proposer leaves the evaluator having spent everything thinking with
#: nothing left to answer with: measured, it hit the cap and produced no tool call at
#: all, twice, and failed the run closed.
#:
#: Failing closed was the correct behaviour. The ceiling was the bug.
_EVALUATOR_MAX_TOKENS = 16_000


def _emit_ceiling(model: str, base: int) -> int:
    """The output ceiling for a structured emit, adjusted for how the model spends it.

    On a non-thinking model the whole budget goes to the answer, and `base` is
    generous for one. On a thinking model the SAME budget covers reasoning and the
    emitted call together, and the reasoning goes first -- so a ceiling sized for the
    answer alone gets spent entirely on thinking and the tool call arrives truncated.

    Measured both ways on `deepseek-v4-pro`: uncapped, one proposer emit produced
    12,182 output tokens of which 11,246 were reasoning, for an eight-field payload.
    Capped at 6,000, it returned a `ProposedAction` with `reasoning` missing outright
    -- the cap had landed mid-object. Neither is the behaviour anyone wants, and the
    answer is not a single number: it is that a thinking model needs headroom the
    ceiling was never accounting for.

    Fails closed either way. A truncated tool call parses to `{}` in `client.py` and
    the schema rejects it; it never becomes a half-formed proposal.
    """
    return base if supports_forced_tool_choice(model) else max(base, _EVALUATOR_MAX_TOKENS)


_PROPOSER_REPAIR_MESSAGE = (
    "That reply contained no emit_proposed_action tool call, so nothing was recorded.\n\n"
    "Do not reconsider the proposal. Transcribe the action, evidence, artifacts and "
    "reasoning you already settled on into a single emit_proposed_action call. Change "
    "nothing. Add no text before or after the call."
)

EMIT_PROPOSED_ACTION_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "emit_proposed_action",
        "description": (
            "Record the proposed action and its evidence. This is the only way a "
            "proposal is recorded -- a proposal written as prose is discarded unread."
        ),
        "parameters": ProposedAction.json_schema(),
    },
}

EMIT_EVALUATION_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "emit_evaluation",
        "description": evaluator_prompt.EMIT_EVALUATION_TOOL_DESCRIPTION,
        "parameters": EvaluatorVerdict.json_schema(),
    },
}


# ═══ the one function that gets either model to reliably emit one tool call ═════════


def _required_of(tool_spec: dict[str, Any]) -> tuple[str, ...]:
    """The required field names of a tool schema, for the coercion guard below."""
    return tuple((tool_spec.get("function", {}).get("parameters", {}) or {}).get("required", ()) or ())


def _find_tool_call(response: LLMResponse, name: str) -> ToolCall | None:
    for call in response.tool_calls:
        if call.name == name:
            return call
    return None


def _coerce_sole_forced_call(
    response: LLMResponse, forced_name: str, required_fields: tuple[str, ...] = ()
) -> ToolCall | None:
    """Accept a single tool call under the wrong name when exactly one name was legal.

    Measured against DeepSeek on 2026-09-13: with `tool_choice` naming
    `emit_proposed_action` and a `tools` array containing only that one function, the
    model returned a well-formed `ProposedAction` payload -- correct `action`, correct
    `grounding_clause_id`, full `reasoning` -- under the name `create_mock_work_item`,
    a function that was *not in the array at all*. It had leaked in from the prompt's
    description of the eventual write path.

    Rejecting that outright throws away a good proposal over a label. Accepting any
    mismatched name would be worse. The narrow rule: coerce only when the request
    forced exactly one function AND the response carried exactly one tool call, so
    there is no ambiguity about what the model meant -- the target was the only legal
    one. The arguments are validated by `ProposedAction.parse` immediately afterwards
    either way, so a genuinely wrong payload still fails; only the name is forgiven.

    This is emphatically NOT a route to the write tool. The write is not reachable
    from a model at all: `create_mock_work_item` is never in any `tools` array a role
    sends (`_READ_TOOL_NAMES` excludes it), and the returned name is discarded here
    rather than dispatched. What the model produced was a proposal wearing the wrong
    label, not a write.
    """
    if len(response.tool_calls) != 1:
        return None
    call = response.tool_calls[0]
    if call.name == forced_name:
        return call
    # The name alone is not enough evidence. Measured: the model sometimes answers a
    # forced emit by calling a READ tool instead -- `get_raw_record(raw_id=63)` -- and
    # renaming that produced a `ProposedAction` with no `reasoning`, turning a
    # recoverable provider hiccup into a SchemaError one layer later. So coerce only
    # when the payload is actually the thing we asked for: every required field of the
    # target schema present. A read tool's arguments never satisfy that, so it falls
    # through to the repair turn, which is the right handling for "it called the wrong
    # thing" as opposed to "it labelled the right thing wrongly".
    required = required_fields or ()
    if required and not all(field in call.arguments for field in required):
        return None
    return ToolCall(id=call.id, name=forced_name, arguments=call.arguments, raw_arguments=call.raw_arguments)


def _emit_structured(
    *,
    client: LLMClient,
    model: str,
    messages: list[dict[str, Any]],
    tool_spec: dict[str, Any],
    forced_name: str,
    repair_message: str,
    journal: Journal,
    agent: str,
    iteration: int,
    max_tokens: int | None = None,
) -> ToolCall:
    """Get exactly one call to `forced_name`, branching on
    `client.supports_forced_tool_choice(model)` (`.agents/specs/spec_contracts.md` S:2).

    On a forcible model: one call, forced. A forced call that still comes back without
    the named tool call would be a provider bug, not a modelled case in the spec, so
    that raises immediately rather than entering the repair path built for the other
    model.

    On a non-forcible model (`deepseek-v4-pro`): `tool_choice="auto"` first. If that
    reply contains no call to `forced_name`, exactly one repair turn runs --
    `repair_message`, a transcription instruction, never a re-ask -- at temperature 0
    regardless of the first call's temperature (`spec_prompts_roles.md` S:C.4: "The
    obvious repair wording ... produces a second, different evaluation. That is
    intrinsic self-correction between two attempts by the same model" -- Huang et al.,
    95.5 -> 91.5 -> 89.0 on GSM8K). A second failure raises `SchemaError`; the caller
    (`_make_evaluate`) lets it propagate, and `harness._evaluate_or_fail_closed` turns
    it into `EvaluatorUnavailable` one layer up -- this function does not know or care
    which typed exception the harness wants, only that it must not return a fabricated
    tool call.
    """
    forced = supports_forced_tool_choice(model)
    tool_choice: str | dict[str, Any] = {"type": "function", "function": {"name": forced_name}} if forced else "auto"

    response = client.complete(
        agent=agent, iteration=iteration, messages=messages, model=model,
        tools=[tool_spec], tool_choice=tool_choice, max_tokens=max_tokens,
    )
    call = _find_tool_call(response, forced_name)
    if call is not None:
        return call

    if forced:
        # The provider did not honour the forced name. If it nonetheless produced
        # exactly one call, the target was unambiguous -- see _coerce_sole_forced_call.
        coerced = _coerce_sole_forced_call(response, forced_name, _required_of(tool_spec))
        if coerced is not None:
            journal.event(
                "forced_tool_name_coerced", agent=agent, iteration=iteration,
                model=response.model, returned_name=response.tool_calls[0].name,
                coerced_to=forced_name,
            )
            return coerced
        # Fall through to the repair turn rather than raising.
        #
        # This branch used to raise immediately, on the reasoning that a forced call
        # coming back without its tool is a provider bug and not a modelled case. It
        # is a provider bug -- and it happens often enough to matter: measured in the
        # browser, `deepseek-chat` stopped honouring a forced `tool_choice` on 2 of 3
        # interactive runs once the conversation already carried several tool calls.
        # Raising turned that into "Run failed", which is the one outcome the design
        # does not have a name for. The loop has four honest terminal states and a
        # provider hiccup should land in one of them, so the same transcription repair
        # the thinking model gets is offered here too, and only a second failure
        # raises.
        journal.event(
            "forced_tool_name_coerced", agent=agent, iteration=iteration,
            model=response.model, returned_name="<none>", coerced_to=forced_name,
            note="forced tool_choice ignored; attempting one repair turn",
        )

    # spec_prompts_roles.md S:C.4: the prose assistant turn stays in history --
    # deleting it would make "transcribe what you already wrote" meaningless.
    messages.append({"role": "assistant", "content": response.content or ""})
    messages.append({"role": "user", "content": repair_message})
    response = client.complete(
        agent=agent, iteration=iteration, messages=messages, model=model,
        tools=[tool_spec], tool_choice="auto", temperature=0.0, max_tokens=max_tokens,
    )
    call = _find_tool_call(response, forced_name) or _coerce_sole_forced_call(response, forced_name, _required_of(tool_spec))
    if call is None:
        raise SchemaError(
            f"evaluator_no_tool_call: {forced_name} produced no tool call on {model!r} even after "
            "one repair turn.",
            repair_message=repair_message,
        )
    return call


def _assistant_tool_call_message_from(call: ToolCall) -> dict[str, Any]:
    """The assistant turn for a single tool call, so a rejected one stays in history.

    A repair that says "that was malformed, emit it again" only means anything if the
    thing being repaired is still on the transcript.
    """
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {"id": call.id, "type": "function",
             "function": {"name": call.name, "arguments": call.raw_arguments}}
        ],
    }


def _assistant_tool_call_message(response: LLMResponse) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": response.content,
        "tool_calls": [
            {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.raw_arguments}}
            for c in response.tool_calls
        ],
    }


def _withheld_reasoning_tool_call_message(call: ToolCall, proposal: ProposedAction) -> dict[str, Any]:
    """B.2: the previous `emit_proposed_action` call stays in history with its
    `reasoning` argument replaced by `"[withheld]"` -- action, evidence and artifacts
    are kept so the proposer sees what it proposed, but it cannot re-read its own
    argument to defend it."""
    withheld_args = dict(call.arguments)
    withheld_args["reasoning"] = "[withheld]"
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"id": call.id, "type": "function", "function": {"name": "emit_proposed_action", "arguments": json.dumps(withheld_args, default=str)}}
        ],
    }


# ═══ the Proposer session -- one instance per run, callable across every iteration ═══


class _ProposerSession:
    """Persistent conversation across harness iterations for one episode
    (`spec_prompts_roles.md` S:B.2). Implements `harness.Propose`.

    Unlike the Evaluator, the Proposer keeps its own history: only its previous
    `reasoning` argument is withheld (see `_withheld_reasoning_tool_call_message`), so
    it does not have to re-discover the episode from scratch on every iteration the way
    "researches again from the documents" is enforced structurally for the *Evaluator*.
    """

    def __init__(
        self,
        *,
        client: LLMClient,
        model: str,
        tool_ctx: ToolContext,
        episode_id: str,
        cursor: str,
        verdict_glossary: str,
        grounding_clause_index: str,
        journal: Journal,
        max_calls_per_iteration: int,
    ) -> None:
        self._client = client
        self._model = model
        self._tool_ctx = tool_ctx
        self._episode_id = episode_id
        self._cursor = cursor
        self._journal = journal
        self._max_calls = max_calls_per_iteration
        self._system_prompt = proposer_prompt.render(
            episode_id=episode_id, cursor=cursor, verdict_glossary=verdict_glossary,
            grounding_clause_index=grounding_clause_index, fence_nonce=tool_ctx.nonce,
        )
        self._messages: list[dict[str, Any]] = []

    def __call__(self, ctx: HarnessContext, critique: str | None, iteration: int) -> ProposedAction:
        if not self._messages:
            self._messages = [
                {"role": "system", "content": self._system_prompt},
                {
                    "role": "user",
                    "content": proposer_prompt.render_iteration1_user_turn(episode_id=self._episode_id, cursor=self._cursor),
                },
            ]
        else:
            if critique is None:
                raise ValueError("every iteration after the first must carry a critique (spec_grounding_rubric.md S:6.1)")
            # spec_grounding_rubric.md S:6 ("this IS the harness spec") builds the
            # feed-forward as rubric.build_critique's single string, appended verbatim
            # as the next user turn. prompts/proposer.py's own richer REPROMPT_TEMPLATE
            # / render_not_addressed_entry / render_contradicted_entry
            # (spec_prompts_roles.md S:B.2) is a different document's version of the
            # same idea, built around a per-finding not_addressed/contradicted split
            # `harness.Propose`'s single `critique: str` cannot carry -- it is not used
            # here. See this coder's report.
            self._messages.append({"role": "user", "content": critique})

        # The emitter is offered ALONGSIDE the read tools, not only in the forced call
        # that follows. Measured live: with only read tools in the array, the model
        # finished researching and called `emit_proposed_action` anyway -- a name that
        # was not on offer -- because the prompt tells it to, and the round budget then
        # cut the iteration short. Giving it the legitimate exit it was already reaching
        # for turns a phantom call into the normal path, and the forced call below
        # becomes the fallback it was meant to be rather than the only route.
        read_tools = wire_schemas(_READ_TOOL_NAMES)
        loop_tools = [*read_tools, EMIT_PROPOSED_ACTION_TOOL]
        calls_made = 0
        emitted: ToolCall | None = None
        for _round in range(_PROPOSER_MAX_ROUNDS):  # per-iteration budget, reset every call (S:B.1)
            response = self._client.complete(
                agent="proposer", iteration=iteration, messages=self._messages,
                model=self._model, tools=loop_tools, tool_choice="auto",
            )
            if not response.tool_calls:
                self._messages.append({"role": "assistant", "content": response.content or ""})
                break

            emitted = _find_tool_call(response, "emit_proposed_action")
            if emitted is not None:
                # Done researching, and it said so in the sanctioned way. Note the
                # assistant turn is NOT appended here: `_withheld_reasoning_tool_call_message`
                # below is what goes into history, so the proposer's own reasoning
                # never re-enters a later context verbatim.
                break

            self._messages.append(_assistant_tool_call_message(response))
            budget_hit = False
            for call in response.tool_calls:
                if calls_made >= self._max_calls:
                    # Every declared tool_call still gets a reply. Breaking out here
                    # without one leaves an assistant message announcing N calls
                    # followed by fewer than N tool messages, which DeepSeek rejects
                    # outright: "An assistant message with 'tool_calls' must be
                    # followed by tool messages responding to each 'tool_call_id'."
                    # Measured -- it 400s the whole run. The budget refusal is also
                    # better information than silence: it tells the model the call was
                    # declined rather than that the data does not exist.
                    budget_hit = True
                    self._messages.append({
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": render_tool_result(
                            err_envelope(
                                "not_permitted",
                                f"this iteration's tool-call budget of {self._max_calls} is spent; "
                                "propose from what you already have, or say what is missing.",
                            )
                        ),
                    })
                    continue
                calls_made += 1
                envelope = dispatch(self._tool_ctx, call.name, call.arguments, iteration=iteration)
                ctx.tool_results.append(RecordedToolResult(name=call.name, arguments=call.arguments, envelope=envelope))
                # Reviewer finding 18: same renderer the citation scorer verifies
                # against, so a verbatim quote of what the model saw cannot fail a veto.
                self._messages.append({"role": "tool", "tool_call_id": call.id, "content": render_tool_result(envelope)})
            if budget_hit or calls_made >= self._max_calls:
                break

        call = emitted if emitted is not None else _emit_structured(
            client=self._client, model=self._model, messages=self._messages, tool_spec=EMIT_PROPOSED_ACTION_TOOL,
            forced_name="emit_proposed_action", repair_message=_PROPOSER_REPAIR_MESSAGE,
            journal=self._journal, agent="proposer", iteration=iteration,
            max_tokens=_emit_ceiling(self._model, _EMIT_MAX_TOKENS),
        )
        try:
            proposal = ProposedAction.parse(call.arguments)
        except SchemaError as exc:
            # One repair turn on a schema violation, aimed at the exact field -- the
            # same treatment `_make_evaluate` already gives the evaluator, and for the
            # same reason, which a live run made expensive to keep ignoring.
            #
            # Measured (2026-09-22, run 0dd212af): the proposer spent 27,246 reasoning
            # tokens and five minutes producing a complete, correct ESCALATE proposal
            # whose `blocked_reason` was the string "null" rather than the literal.
            # `parse` rejected it, the SchemaError escaped this method, `run_until` has
            # no handler for a failing `propose` (only for a failing `evaluate`), and
            # the whole run ended as a bare "error" -- the one outcome design S2 does
            # not have a name for. The unpaired `try` was the bug, not the model.
            #
            # `schemas._require_nullable_str` now reads that particular spelling as
            # absence, so this path is the backstop rather than the fix: any OTHER slip
            # -- a missing artifact on a non-ABSTAIN action, an out-of-vocabulary
            # action -- gets one precisely-worded retry instead of killing the run. A
            # second failure still raises, and still fails closed.
            self._journal.event(
                "proposal", iteration=iteration, model=self._model,
                schema_repair_attempted=True, error=str(exc),
            )
            self._messages.append(_assistant_tool_call_message_from(call))
            self._messages.append({"role": "tool", "tool_call_id": call.id, "content": "rejected"})
            self._messages.append({"role": "user", "content": exc.repair_message})
            call = _emit_structured(
                client=self._client, model=self._model, messages=self._messages,
                tool_spec=EMIT_PROPOSED_ACTION_TOOL, forced_name="emit_proposed_action",
                repair_message=_PROPOSER_REPAIR_MESSAGE, journal=self._journal,
                agent="proposer", iteration=iteration,
                max_tokens=_emit_ceiling(self._model, _EMIT_MAX_TOKENS),
            )
            proposal = ProposedAction.parse(call.arguments)
        self._messages.append(_withheld_reasoning_tool_call_message(call, proposal))
        self._messages.append({"role": "tool", "tool_call_id": call.id, "content": "recorded"})
        return proposal


# ═══ the Evaluator call -- fresh trace, every iteration ═════════════════════════════


@dataclass(frozen=True, slots=True)
class EvaluatorInput:
    """Exactly the sections `spec_prompts_roles.md` S:C.2 puts in the user turn.

    Deliberately has **no `reasoning` field** -- unrepresentable, not filtered, which
    is the load-bearing exclusion S:C.2 itself names as the property a test must prove
    structurally rather than by a runtime check that could be forgotten.
    """

    episode: dict[str, Any]
    tool_results: tuple[RecordedToolResult, ...]
    proposal_view: dict[str, Any]
    named_clause: Clause | None
    mapped_clauses: tuple[Clause, ...]
    #: Every clause selected for this episode. Only the cited and mapped ones are
    #: rendered in full; the rest appear as a name-and-id index, so the evaluator can
    #: still see that a better clause existed without paying for all of their bodies.
    all_clauses: tuple[Clause, ...]
    checklist: tuple[tuple[str, str], ...]  # (criterion_id, question)


def _norm(s: str) -> str:
    """Deliberately duplicates `scorers._norm`'s two-line body rather than importing a
    private symbol across the module boundary (that function is not exported, and this
    coder does not own `scorers.py`). This `verified` flag is informational only, shown
    to the Evaluator as a hint -- the *authoritative* G5 check is still
    `scorers.citation_verifies_by_substring`, run by the harness after the Evaluator
    replies."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", s)).strip()


def _span_source_text(
    source_ref: str, tool_results: tuple[RecordedToolResult, ...], clauses: tuple[Clause, ...]
) -> str | None:
    if source_ref.startswith("KG:"):
        entity = source_ref[len("KG:") :]
        texts = [c.text for c in clauses if c.entity == entity]
        return "\n".join(texts) if texts else None
    m = re.match(r"^tool_result#(\d+)", source_ref)
    if not m or not (1 <= int(m.group(1)) <= len(tool_results)):
        return None
    return render_tool_result(tool_results[int(m.group(1)) - 1].envelope)


def _span_verified(span: Any, tool_results: tuple[RecordedToolResult, ...], clauses: tuple[Clause, ...]) -> bool:
    source = _span_source_text(span.source_ref, tool_results, clauses)
    if source is None:
        return False
    return _norm(source).find(_norm(span.quote)) != -1


def _result_shape(envelope: dict[str, Any]) -> str:
    """A one-line description of a tool result whose body is being omitted.

    Names the top-level keys and the length of any list, so the evaluator can tell
    "eight timeline events and four allocations" from "nothing came back" without
    being handed either in full.
    """
    data = envelope.get("data")
    if not isinstance(data, dict):
        return "empty" if data in (None, [], {}) else "scalar"
    bits: list[str] = []
    for key, value in data.items():
        if key.startswith("_") or key == "cursor":
            continue
        if isinstance(value, list):
            bits.append(f"{key}[{len(value)}]")
        elif isinstance(value, dict):
            bits.append(f"{key}{{{len(value)}}}")
        else:
            bits.append(key)
    return ", ".join(bits[:12]) or "empty"


def render_evaluator_user_turn(inp: EvaluatorInput) -> str:
    """`spec_prompts_roles.md` S:C.2's six included sections, in order."""
    parts: list[str] = ["## Episode", json.dumps(inp.episode, sort_keys=True, default=str), ""]

    # Full text for anything the proposal CITED; a one-line receipt for the rest.
    #
    # Measured: this section was 19KB of a 37KB turn, re-sent on every evaluator call
    # (three of them in one iteration, once a schema repair and an escalation fired),
    # and it was the single largest cost in a Decide run. Sending every byte of every
    # envelope treats the evaluator as if it were re-deriving the episode, which is
    # not its job -- it grades the claims the proposal actually made.
    #
    # What it genuinely needs is here: the exact bytes behind every cited span, so a
    # quote can be judged in context, plus proof of what else was looked at, so
    # "there was evidence you ignored" stays visible. What it does not need is the
    # full body of a call nothing cited.
    #
    # Note this cannot weaken citation verification, because that is not done here at
    # all -- `_span_verified` runs in Python before this string is built, and its
    # boolean is already on `proposal_view`. The evaluator is judging whether the
    # evidence SUPPORTS the claim, never whether the quote exists.
    cited_refs = {
        str(e.get("source_ref") or "") for e in (inp.proposal_view.get("evidence") or []) if isinstance(e, dict)
    }

    def _was_cited(index: int, name: str) -> bool:
        return any(ref.startswith(f"tool_result#{index}") or ref.startswith(f"{name}#") for ref in cited_refs)

    parts.append("## Tool results")
    for i, tr in enumerate(inp.tool_results, start=1):
        header = f"### tool_result#{i} — {tr.name}({json.dumps(tr.arguments, sort_keys=True, default=str)})"
        if _was_cited(i, tr.name):
            parts.append(header)
            parts.append(render_tool_result(tr.envelope))
        else:
            envelope = tr.envelope or {}
            status = envelope.get("status", "?")
            detail = envelope.get("error_type") or _result_shape(envelope)
            parts.append(f"{header} → {status}, {detail}. Not cited by this proposal; body omitted.")
    parts.append("")

    # Same principle as the tool results above: full text for the clause the proposal
    # cited, and for the handful mapped to this episode's own reason codes; a name-only
    # index for the rest.
    #
    # G11 asks whether the cited clause supports the action, which needs that clause in
    # full. The others are there so the evaluator can notice a BETTER clause was
    # available -- and an entity name plus its id is enough to notice that, because if
    # it wants one it can say so and the next round will carry it. This section was
    # 11.8KB of a 37KB turn, nearly all of it clauses nobody referenced.
    parts.append("## Grounding clauses")
    seen_ids: set[str] = set()
    detailed = ([inp.named_clause] if inp.named_clause is not None else []) + list(inp.mapped_clauses)
    for c in detailed:
        if c.clause_id in seen_ids:
            continue
        seen_ids.add(c.clause_id)
        parts.append(f"[{c.clause_id}] {c.entity} ({c.entity_type})\n{c.text}")

    remaining = [c for c in inp.all_clauses if c.clause_id not in seen_ids]
    if remaining:
        by_entity: dict[str, list[str]] = {}
        for c in remaining:
            by_entity.setdefault(c.entity, []).append(c.clause_id)
        parts.append(
            # Deliberately does not say "ask for one". The earlier wording did, and the
            # model read it as a promise that a clause-fetching tool existed -- a live
            # run had it calling `search_clauses` and `get_work_item_history`, neither
            # of which is in the registry. A prompt that implies a capability the
            # system does not have costs a whole wasted round.
            "\nAlso available, bodies omitted. Listed so you can see whether a better "
            "clause existed; there is no tool for fetching one, and citing a clause "
            "whose body is omitted here is not wrong for that reason alone:"
        )
        for entity, ids in by_entity.items():
            parts.append(f"- {entity}: {', '.join(ids)}")
    parts.append("")

    parts.append("## Checklist")
    for criterion_id, question in inp.checklist:
        parts.append(f"- [{criterion_id}] {question}")
    parts.append("")

    # The proposal goes LAST, and the ordering is worth a sentence.
    #
    # DeepSeek caches on a token PREFIX, so a request reuses the cache only up to its
    # first differing token. Every section above is stable within a run -- the episode
    # and the clause set do not move, the checklist is fixed, and the tool results only
    # ever grow at the end -- while the proposal is by definition different every
    # iteration. With the proposal sitting in the middle, iteration two invalidated the
    # cache before reaching the clauses and the checklist behind it. Measured before
    # this change: 2,560 cached tokens against a 13,899-token evaluator prompt.
    #
    # It is also the better reading order -- everything the proposal will be judged
    # against is established before the proposal appears -- so the cache alignment
    # costs nothing in clarity. FINAL_LINE still ends the turn, because recency
    # reinforcement (S:C.4) has to be the last thing read.
    parts += ["## The proposal", json.dumps(inp.proposal_view, sort_keys=True, default=str), ""]

    parts.append(evaluator_prompt.FINAL_LINE)  # recency reinforcement, S:C.4
    return "\n".join(parts)


def _make_evaluate(
    *, client: LLMClient, model: str, journal: Journal, fence_nonce: str,
    fallback_model: str | None = None, rubric_profile: str = "core",
) -> Evaluate:
    def evaluate(ctx: HarnessContext, proposal: ProposedAction, iteration: int) -> EvaluatorVerdict:
        # Fresh trace, every iteration: no history from any previous round, and no
        # channel into the Proposer's own conversation -- design S:1, "the reviewer
        # gets to skip this extraneous context ... and re-discover any context it
        # needs." Only the current proposal and the current, freshly-read dossier are
        # ever assembled into `EvaluatorInput`.
        prelim = ScoringInput(
            proposal=proposal, evaluator_verdict=None, tool_results=tuple(ctx.tool_results),
            clauses=ctx.clauses, dossier=ctx.dossier,
        )
        # `weight > 0.0` excludes G17_rationale_is_not_a_retelling even though it is
        # grader=="judge": rubric.run_judge_criteria takes every finding in
        # EvaluatorVerdict.per_criterion unconditionally, and rubric.run_tracked_criteria
        # separately re-reads the SAME list looking for a G17 entry -- so a verdict that
        # actually graded G17 would make rubric.merge_findings see it in both groups and
        # raise on the overlap. rubric.py's own SCORERS comment lists exactly eight ids
        # next to run_judge_criteria ("# G4 G6 G7 G9 G11 G12 G13 G14") and G17 separately
        # next to run_tracked_criteria ("# G16 G17"), so this exclusion matches that
        # comment's intent even though neither function's actual code enforces the split
        # itself. Net effect, reported rather than silently accepted: G17 is never
        # actually graded through this composition as built -- see this coder's report.
        # Deterministic criteria are free and all of them always run, vetoes
        # included. The judge criteria are the ones that cost a reasoned finding
        # each, so the profile trims THOSE -- see rubric.CORE_JUDGE_CRITERIA for
        # which four carry the argument and why the other four are covered
        # elsewhere. Nothing about the safety story changes with the profile.
        applicable = applicable_judge_criteria(prelim, rubric_profile)
        checklist = tuple((c.criterion_id, c.question) for c in applicable)
        if not checklist:
            # An empty checklist means there is nothing to ask a judge, which can
            # happen when every judge criterion is either inapplicable to this
            # proposal or trimmed by the profile. Calling the model anyway asks it to
            # grade nothing and gets a structurally-invalid verdict back; skipping it
            # silently is worse, because the run then ends with no `evaluation` event
            # and the journal shows a proposal that was never checked at all.
            #
            # Neither is acceptable, so this is a typed failure. The deterministic
            # criteria still ran -- every veto is deterministic -- but a gate that
            # consulted no judge is not the gate this design claims to have.
            raise SchemaError(
                "no judge criterion applies to this proposal under the "
                f"{rubric_profile!r} rubric profile, so there is nothing for the "
                "evaluator to grade; set RECON_AGENT_RUBRIC=full or widen "
                "CORE_JUDGE_CRITERIA.",
                repair_message="internal error: an empty checklist was assembled.",
            )

        named = next((c for c in ctx.clauses if c.clause_id == proposal.grounding_clause_id), None)
        current = ctx.dossier.get("current") or {}
        episode_view = {
            "episode_id": ctx.dossier.get("episode_id"),
            "cursor": ctx.dossier.get("cursor"),
            "track": (ctx.dossier.get("identity") or {}).get("track"),
            "episode_disposition": current.get("episode_disposition"),
            "reimbursement_verdict": current.get("reimbursement_verdict"),
            "rebate_verdict": current.get("rebate_verdict"),
            "reason_codes": current.get("reason_codes", []),
            "cross_track_flags": current.get("cross_track_flags", []),
        }
        proposal_view = {
            # NOTE: no "reasoning" key -- the load-bearing exclusion (S:C.2).
            "evidence": [
                {
                    "quote": e.quote,
                    "source_kind": e.source_kind.value,
                    "source_ref": e.source_ref,
                    "verified": _span_verified(e, tuple(ctx.tool_results), ctx.clauses),
                }
                for e in proposal.evidence
            ],
            "grounding_clause_id": proposal.grounding_clause_id,
            "action": proposal.action.value,
            "required_artifacts": list(proposal.required_artifacts),
            "missing_evidence": list(proposal.missing_evidence),
            "blocked": proposal.blocked,
            "blocked_reason": proposal.blocked_reason,
        }
        # `mapped_clauses` used to be handed every clause selected for the episode,
        # which meant the "render these in full" set and the "index these" set were the
        # same set and the split below did nothing. Full text now goes to the clause the
        # proposal cited and its entity-mates -- the ones that could plausibly have been
        # cited instead, which is what G11 needs to judge the choice -- and everything
        # else is indexed by name and id.
        mapped = tuple(c for c in ctx.clauses if named is not None and c.entity == named.entity)
        inp = EvaluatorInput(
            episode=episode_view, tool_results=tuple(ctx.tool_results), proposal_view=proposal_view,
            named_clause=named, mapped_clauses=mapped, all_clauses=ctx.clauses, checklist=checklist,
        )

        system = evaluator_prompt.render(
            episode_id=str(episode_view["episode_id"]), cursor=str(episode_view["cursor"]), fence_nonce=fence_nonce,
        )
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": render_evaluator_user_turn(inp)},
        ]

        call = _emit_structured(
            client=client, model=model, messages=messages, tool_spec=EMIT_EVALUATION_TOOL,
            forced_name="emit_evaluation", repair_message=evaluator_prompt.REPAIR_TRANSCRIPTION_TURN,
            journal=journal, agent="evaluator", iteration=iteration,
            max_tokens=_emit_ceiling(model, _EVALUATOR_MAX_TOKENS),
        )
        try:
            verdict = EvaluatorVerdict.parse(call.arguments)
        except SchemaError as exc:
            # One repair turn on a schema violation, aimed at the exact field.
            #
            # `SchemaError` has carried a `repair_message` from the start, written to be
            # sendable straight back to the model as a user turn -- it was simply never
            # wired to anything. Measured once the evaluator moved to the non-thinking
            # model: it returns a well-formed verdict that omits `cited_span` on a
            # SUPPORTED finding often enough to kill a run, and killing the run turns a
            # formatting slip into `EvaluatorUnavailable`, which fails the whole gate
            # closed. The slip is worth one retry; a second identical failure is not,
            # and still fails closed.
            #
            # This is a repair, not a re-ask: the message names the offending field and
            # asks for the same judgement re-emitted correctly. It must never say
            # anything about what the verdict should BE.
            journal.event(
                "evaluation", iteration=iteration, agent="evaluator", model=model,
                schema_repair_attempted=True, error=str(exc),
            )
            messages.append(_assistant_tool_call_message_from(call))
            messages.append({"role": "tool", "tool_call_id": call.id, "content": "rejected"})
            messages.append({"role": "user", "content": exc.repair_message})
            call = _emit_structured(
                client=client, model=model, messages=messages, tool_spec=EMIT_EVALUATION_TOOL,
                forced_name="emit_evaluation", repair_message=evaluator_prompt.REPAIR_TRANSCRIPTION_TURN,
                journal=journal, agent="evaluator", iteration=iteration,
            max_tokens=_emit_ceiling(model, _EVALUATOR_MAX_TOKENS),
            )
            try:
                verdict = EvaluatorVerdict.parse(call.arguments)
            except SchemaError:
                # Twice is a capacity problem, not a slip. Escalate to the stronger
                # model rather than failing the gate closed.
                #
                # Design doc S10 predicted this exactly: "Model capacity interacts with
                # structure... splitting into free reasoning then cheap extraction
                # recovered 80-87%. So a cheap evaluator is not free." Measured here:
                # the non-thinking evaluator returned a verdict with `per_criterion`
                # missing outright, twice in a row, on a thirteen-criterion nested
                # checklist. The thinking model does not have that problem -- it is
                # simply slow, which is why it is not the default.
                #
                # So the cheap model handles the common case and pays for itself, and
                # the expensive one is the backstop for the hard one. The escalation is
                # journalled because "which model actually produced this verdict" is
                # part of what a reviewer is entitled to know.
                if not fallback_model or fallback_model == model:
                    raise
                journal.event(
                    "evaluation", iteration=iteration, agent="evaluator", model=model,
                    escalated_to=fallback_model,
                    reason="two schema failures on the primary evaluator model",
                )
                call = _emit_structured(
                    client=client, model=fallback_model, messages=messages,
                    tool_spec=EMIT_EVALUATION_TOOL, forced_name="emit_evaluation",
                    repair_message=evaluator_prompt.REPAIR_TRANSCRIPTION_TURN,
                    journal=journal, agent="evaluator", iteration=iteration,
                    max_tokens=_emit_ceiling(fallback_model, _FALLBACK_MAX_TOKENS),
                )
                verdict = EvaluatorVerdict.parse(call.arguments)

        expected_ids = [cid for cid, _q in checklist]
        actual_ids = [f.criterion_id for f in verdict.per_criterion]
        if actual_ids != expected_ids:
            # S:C.3: "any failure is a STRUCTURAL failure ... it never falls back to
            # accepting the proposal." A mismatched or reordered checklist is exactly
            # such a failure and gets no repair attempt -- only a missing tool call
            # does (handled inside _emit_structured).
            raise SchemaError(
                f"evaluator_structurally_invalid: expected findings for {expected_ids} in order, "
                f"got {actual_ids}.",
                repair_message="internal error: the checklist sent does not match the findings returned.",
            )
        return verdict

    return evaluate


# ═══ the entry point: build both roles, hand them to the harness ═══════════════════


def run_coordinator(
    *,
    client: LLMClient,
    tool_ctx: ToolContext,
    settings: AgentSettings,
    dossier: dict[str, Any],
    verdict_glossary: str,
    grounding_clause_index: str,
    journal: Journal,
    budgets: RunBudgets | None = None,
) -> Outcome:
    """Build the Proposer session and the Evaluator closure and hand them to
    `harness.run_until`. The only place in the agent layer that constructs both roles
    for one run -- everything above this function is plumbing that never talks to a
    model by itself.
    """
    clauses = select_clauses(dossier)
    ctx = HarnessContext(dossier=dossier, clauses=clauses)

    session = _ProposerSession(
        client=client, model=settings.proposer_model, tool_ctx=tool_ctx,
        episode_id=str(dossier["episode_id"]), cursor=str(dossier["cursor"]),
        verdict_glossary=verdict_glossary, grounding_clause_index=grounding_clause_index,
        journal=journal, max_calls_per_iteration=settings.max_tool_rounds,
    )
    evaluate = _make_evaluate(
        client=client, model=settings.evaluator_model, journal=journal,
        fence_nonce=tool_ctx.nonce, fallback_model=settings.evaluator_fallback_model,
        rubric_profile=settings.rubric_profile,
    )

    resolved_budgets = budgets or RunBudgets(
        max_iterations=settings.max_iterations, threshold=settings.threshold, token_budget=settings.token_budget,
    )
    propose: Propose = session
    return run_until(
        propose, evaluate, ctx,
        journal=journal, budgets=resolved_budgets,
        proposer_model=settings.proposer_model, evaluator_model=settings.evaluator_model,
    )
