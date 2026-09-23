"""Structured payloads the agent layer trades with a model, without pydantic.

Why not pydantic: it is an optional extra used only by FastAPI (see the `api` extra in
`pyproject.toml`), and the agent layer must import cleanly on a base install that has
neither `pydantic` nor `fastapi`.  This module is the leaf of `src/recon/agents/` --
`client.py`, `journal.py`, `config.py`, `tools.py`, `envelope.py`, `grounding.py`,
`rubric.py` and `scorers.py` all import *from* here, never the reverse
(`.agents/specs/spec_contracts.md` §5) -- so anything it depends on becomes a dependency
of the whole package.  Plain frozen dataclasses plus hand-written JSON Schema dicts get
the same job done with zero new dependency.

Three rules from the design doc (`docs/agent_layer_design.md` §4) are load-bearing here,
not stylistic:

**Reasoning first.**  One measurement puts this at ~60 percentage points on hard tasks,
because a decoder that commits to `action` (or `verdict`) before it has written a word of
`reasoning` is rationalising a token it already emitted, not reasoning toward it.
`json_schema()` walks `_FIELD_SCHEMAS`, an ordered dict built by hand in declaration
order, and `required` is emitted in that same order -- never `list(dict.keys())` run
through a set or a sort, either of which would silently throw the order away.

**Every enum needs an escape member.**  Constrained decoding guarantees a schema-valid
label on every input, including inputs that support none of the substantive options; an
enum with no way to decline manufactures false confidence.  `ActionEnum.ABSTAIN` is the
escape member for the six real actions.  `CriterionVerdict.NOT_ADDRESSED` plays the same
role for the evaluator's three-way grade -- it is the answer for "the material does not
settle this," which is a real and expected outcome, not a fallback.  `SourceKind` and
`CitationKind` are the one place this module does *not* add an escape member: both are
exhaustive by construction (a quote is mechanically either from a tool result or a
grounding clause; a citation token is mechanically one of the four the regex in
`prompts/investigator.py` recognises), so there is no third case for a model to decline
into -- adding one would be inventing a value nothing in the system ever produces.

**Optional-in-spirit means `T | None`, not merely omittable.**  Every property below is
listed in its schema's `required` array -- including the nullable ones -- so the model
must always state a position, sometimes as `null`, and can never solve "I don't know" by
quietly dropping the key. A required key with no legal way to hold `null` is what
manufactures a hallucinated value.

**Flat, depth <= 3, no `oneOf`/`anyOf`.**  Nullable nested objects (`cited_span: EvidenceSpan
| None`) are encoded as `{"type": ["object", "null"], "properties": ...}` rather than a
union schema, because union support is the weakest area across every provider this
project targets (design §4). Depth is counted in object layers, not scalar fields: e.g.
`EvaluatorVerdict` (1) -> `CriterionFinding` via `per_criterion[]` (2) -> `EvidenceSpan`
via `cited_span` (3) is the deepest path in this module and sits exactly at the ceiling.

One more thing worth flagging for whoever reads this next: `.agents/specs/spec_prompts_roles.md`
§B.3/§C.3 -- the frozen source for these field lists -- itself declares two *deliberate*
exceptions to "reasoning first," each with its own rationale table entry: `CriterionFinding`
puts `criterion_id` first (a label, not a judgment -- it stops the decoder from grading a
criterion it has not yet named) and `EvaluatorVerdict` puts `overall_reasoning` *last*
(because `per_criterion` already *is* the reasoning, distributed one judgment at a time --
writing a global `overall_reasoning` first would be exactly the "one impression wearing six
hats" failure the grading instructions exist to prevent). `EvidenceSpan` and `Citation` have
no `reasoning` field at all: they are mechanically-produced or mechanically-verified value
records, not decisions, so a `reasoning` field on them would be manufactured filler. This
module follows the frozen spec's actual field order rather than a blanket "position zero,
always" reading of the same principle; see the test module's docstring for how the test
suite verifies each of these three shapes without contradicting itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, ClassVar, Iterable, Self

__all__ = [
    "SchemaError",
    "ActionEnum",
    "CriterionVerdict",
    "SourceKind",
    "CitationKind",
    "EvidenceSpan",
    "ProposedAction",
    "CriterionFinding",
    "EvaluatorVerdict",
    "Citation",
    "WorkItemDraft",
    "InvestigatorReport",
    "AnalystReport",
]


class SchemaError(ValueError):
    """A model's payload cannot be trusted as-is.

    `repair_message` is text a role can send straight back to the model as a plain user
    turn -- the pattern every repair loop in `spec_prompts_roles.md` uses (B.2's
    NOT-ADDRESSED/CONTRADICTED re-prompt, C.4's transcription repair). It always names
    the offending field and, for a closed vocabulary, every legal value -- "the action
    was CLOSE_CLAIM; legal values are ..." rather than "invalid action" -- because a
    repair turn that does not name the fix wastes the round it was meant to save.
    """

    def __init__(self, message: str, *, repair_message: str) -> None:
        super().__init__(message)
        self.repair_message = repair_message


class ActionEnum(StrEnum):
    """The Workflow Coordinator's closed action set (`spec_prompts_roles.md` §B.1/§B.3).

    Exactly seven, `ABSTAIN` is the escape member, and the SQL/db layer never sees this
    enum -- `work_item.recommended_action` is unconstrained `TEXT` at the schema level
    (`.agents/specs/spec_tools.md` §2.7), so this Python enum is the only place the set
    is closed at all.
    """

    RESUBMIT = "RESUBMIT"
    APPEAL = "APPEAL"
    WRITE_OFF = "WRITE_OFF"
    ESCALATE = "ESCALATE"
    INVESTIGATE_CROSSWALK = "INVESTIGATE_CROSSWALK"
    AWAIT_PAYER = "AWAIT_PAYER"
    ABSTAIN = "ABSTAIN"


class CriterionVerdict(StrEnum):
    """The evaluator's three-way grade (`spec_prompts_roles.md` §C.1 "How to grade").

    `NOT_ADDRESSED` is this vocabulary's escape member -- "the material is silent" is
    always an available, correct answer, which is exactly what keeps the evaluator from
    being forced to manufacture a `SUPPORTED` or `CONTRADICTED` verdict it cannot back
    with a quote.
    """

    SUPPORTED = "SUPPORTED"
    CONTRADICTED = "CONTRADICTED"
    NOT_ADDRESSED = "NOT_ADDRESSED"


class SourceKind(StrEnum):
    """Where an `EvidenceSpan.quote` came from (`spec_prompts_roles.md` §B.3).

    Deliberately has no escape member: a span's source is mechanically one of these two
    things by construction (the harness only ever hands the model tool results and
    grounding clauses to quote from), so there is no third case to decline into.
    """

    TOOL_RESULT = "tool_result"
    GROUNDING_CLAUSE = "grounding_clause"


class CitationKind(StrEnum):
    """The Investigator's citation-token kinds (`spec_prompts_roles.md` §0 grammar),
    plus two the Portfolio Analyst adds for its own tool shapes.

    ``\\[\\[(raw|event|calc|verdict):...\\]\\]`` -- the regex itself is the closed
    vocabulary. A bracket pair using any other word is not a citation at all and never
    reaches this enum; the harness renders it as plain, unresolved text instead of
    constructing a `Citation`. That is why there is no escape member here either.

    `OVERVIEW` and `QUEUE` are additions from `roles/analyst.py` (`.agents/specs/
    spec_analyst.md`, this coder's slice). The Analyst never sees a single episode's
    raw records or timeline (`raw`/`event` do not fit its tool shapes), but it does
    read two aggregate tool results with no equivalent in the Investigator's grammar:
    a field of `get_portfolio_overview` (the aggregate analogue of `calc:F` naming a
    field of `calculate_reconciliation`) and a row of `get_exception_queue`, referenced
    by the episode_id that row carries (the aggregate analogue of `raw:R` naming an
    immutable source row). Extending the enum rather than repurposing `calc`/`raw` for
    a different meaning keeps each kind's semantics singular -- a reader of a `[[calc:]]`
    token should never have to ask which of two tools it might refer to. Still
    exhaustive by construction, the same reason the original four carry no escape
    member: each of the six is mechanically one tool shape or another, by the fixed
    regex `roles/analyst.py` matches against, so there is no seventh case to decline
    into.
    """

    RAW = "raw"
    EVENT = "event"
    CALC = "calc"
    VERDICT = "verdict"
    OVERVIEW = "overview"
    QUEUE = "queue"


# ═══ shared validation helpers ═══════════════════════════════════════════════
#
# Small and explicit on purpose -- the house style this package matches
# (`src/recon/db/repository.py:1-5`) is "plain functions, no ORM, no generic
# reflection machinery." Six hand-written `parse()` methods calling these are
# easier to audit than one generic parser driven by `dataclasses.fields()` and
# `typing.get_type_hints()`, and they let each class's cross-field rules (e.g.
# "blocked_reason is non-null iff blocked") read as a short, visible `if`.


def _require_object(payload: Any, *, schema_name: str) -> None:
    if not isinstance(payload, dict):
        raise SchemaError(
            f"{schema_name} payload must be a JSON object, got {type(payload).__name__}.",
            repair_message=(
                f"{schema_name} must be a single JSON object argument, not a "
                f"{type(payload).__name__}. Resend the call with an object."
            ),
        )


def _require_keys(payload: dict[str, Any], keys: Iterable[str], *, schema_name: str) -> None:
    keys = list(keys)
    missing = [k for k in keys if k not in payload]
    if missing:
        field_name = missing[0]
        raise SchemaError(
            f"{schema_name} payload is missing required field '{field_name}'.",
            repair_message=(
                f"Your reply was missing the required field `{field_name}`. "
                f"{schema_name} requires all of: {', '.join(keys)}. "
                f"Resend the call with `{field_name}` filled in -- use `null` "
                f"if it genuinely does not apply, never omit the key."
            ),
        )


def _require_str(payload: dict[str, Any], field_name: str, *, schema_name: str) -> str:
    value = payload[field_name]
    if not isinstance(value, str):
        raise SchemaError(
            f"{schema_name}.{field_name} must be a string, got {type(value).__name__}.",
            repair_message=(
                f"field `{field_name}` must be a JSON string; you sent a "
                f"{type(value).__name__}. Resend the call with `{field_name}` as a string."
            ),
        )
    return value


#: The ways a model writes "no value" into a slot that wanted the JSON literal `null`.
#: Compared after `strip().casefold()`, so `"Null"` and `" null "` are covered too.
_NULL_IN_A_STRING: frozenset[str] = frozenset({"", "null", "none", "nil", "n/a", "na"})


def _require_nullable_str(payload: dict[str, Any], field_name: str, *, schema_name: str) -> str | None:
    """A nullable string field, with the four characters `null` read as `null`.

    Measured on a live Decide run (2026-09-22, run 0dd212af): `deepseek-v4-pro`
    returned a complete, correct `ProposedAction` whose `blocked_reason` was the
    *string* `"null"` alongside `blocked: false`. The cross-field rule below
    ("blocked_reason must be null when blocked is false") then rejected the whole
    proposal, `_ProposerSession.__call__` raised out of the harness, and the run died
    with no outcome at all -- five minutes of thinking thrown away over a quoted
    keyword. A thinking model emitting JSON as text inside a tool argument writes the
    word rather than the literal often enough that this cannot stay a fatal slip.

    Coercing is safe because none of these strings is ever a legal *value* here: the
    two nullable string fields in this module are `grounding_clause_id` (a
    `KG:<entity>` clause id) and `blocked_reason` (a sentence naming a missing
    record). "null", "none", "n/a" and the empty string all mean the absence of one,
    whichever way the model chose to spell it, so reading them as absence preserves
    the model's intent rather than overriding it.
    """
    value = payload[field_name]
    if value is not None and not isinstance(value, str):
        raise SchemaError(
            f"{schema_name}.{field_name} must be a string or null, got {type(value).__name__}.",
            repair_message=(
                f"field `{field_name}` must be a JSON string or null; you sent a "
                f"{type(value).__name__}. Resend the call with `{field_name}` as a string, "
                f"or null if it does not apply."
            ),
        )
    if isinstance(value, str) and value.strip().casefold() in _NULL_IN_A_STRING:
        return None
    return value


def _require_bool(payload: dict[str, Any], field_name: str, *, schema_name: str) -> bool:
    value = payload[field_name]
    if not isinstance(value, bool):
        raise SchemaError(
            f"{schema_name}.{field_name} must be a boolean, got {type(value).__name__}.",
            repair_message=(
                f"field `{field_name}` must be a JSON boolean (true or false); you sent "
                f"a {type(value).__name__}. Resend the call with `{field_name}` as true "
                f"or false -- there is no default, state your intent explicitly."
            ),
        )
    return value


def _require_list(payload: dict[str, Any], field_name: str, *, schema_name: str) -> list[Any]:
    value = payload[field_name]
    if not isinstance(value, list):
        raise SchemaError(
            f"{schema_name}.{field_name} must be an array, got {type(value).__name__}.",
            repair_message=(
                f"field `{field_name}` must be a JSON array; you sent a "
                f"{type(value).__name__}. Resend the call with `{field_name}` as a list, "
                f"or an empty list if there is nothing to put in it."
            ),
        )
    return value


def _require_list_of_str(payload: dict[str, Any], field_name: str, *, schema_name: str) -> list[str]:
    items = _require_list(payload, field_name, schema_name=schema_name)
    for index, item in enumerate(items):
        if not isinstance(item, str):
            raise SchemaError(
                f"{schema_name}.{field_name}[{index}] must be a string, got {type(item).__name__}.",
                repair_message=(
                    f"every entry in `{field_name}` must be a string; entry {index} was a "
                    f"{type(item).__name__}. Resend the call with every entry a JSON string."
                ),
            )
    return items


def _require_enum(value: Any, enum_cls: type[StrEnum], *, field_name: str, schema_name: str) -> Any:
    try:
        return enum_cls(value)
    except ValueError:
        legal = ", ".join(member.value for member in enum_cls)
        raise SchemaError(
            f"{schema_name}.{field_name} was {value!r}; legal values are {legal}.",
            repair_message=(
                f"field `{field_name}` was {value!r}; legal values are {legal}. "
                f"Re-emit the call with `{field_name}` set to exactly one of these strings."
            ),
        ) from None


def _require_nullable_nested(
    payload: dict[str, Any], field_name: str, nested_cls: type, *, schema_name: str
) -> Any | None:
    value = payload[field_name]
    if value is None:
        return None
    if not isinstance(value, dict):
        raise SchemaError(
            f"{schema_name}.{field_name} must be an object or null, got {type(value).__name__}.",
            repair_message=(
                f"field `{field_name}` must be a JSON object or null; you sent a "
                f"{type(value).__name__}. Resend the call with `{field_name}` as an "
                f"object, or null."
            ),
        )
    return nested_cls.parse(value)


# ═══ JSON Schema fragment builders ═══════════════════════════════════════════
#
# Deliberately dumb string-in, dict-out helpers -- no schema inference from
# type hints, so there is never a mismatch between what a field's *type*
# permits and what the model was actually told in `description`.


def _string(description: str) -> dict[str, Any]:
    return {"type": "string", "description": description}


def _nullable_string(description: str) -> dict[str, Any]:
    return {"type": ["string", "null"], "description": description}


def _boolean(description: str) -> dict[str, Any]:
    return {"type": "boolean", "description": description}


def _string_enum(values: Iterable[str], description: str) -> dict[str, Any]:
    return {"type": "string", "enum": list(values), "description": description}


def _array_of_strings(description: str) -> dict[str, Any]:
    return {"type": "array", "items": {"type": "string"}, "description": description}


def _array_of(item_schema: dict[str, Any], description: str) -> dict[str, Any]:
    return {"type": "array", "items": item_schema, "description": description}


def _nullable_object(item_schema: dict[str, Any], description: str) -> dict[str, Any]:
    """Nullable nested object, WITHOUT `anyOf`/`oneOf` (design §4: avoid unions)."""
    merged = dict(item_schema)
    merged["type"] = [item_schema["type"], "null"]
    merged["description"] = description
    return merged


# ═══ EvidenceSpan ═════════════════════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class EvidenceSpan:
    """A verbatim quote from either a tool result or a grounding clause.

    Shared by the Proposer's `evidence` list and the Evaluator's `cited_span`
    (`spec_prompts_roles.md` §B.3 names this shape `EvidenceSpan`; §C.3 independently
    names a leaner two-field `Span` for `cited_span` alone -- see this module's
    docstring and the test module's docstring for why this file unifies them into one
    class rather than carrying two near-identical citation shapes).

    `start`/`end` are **not** part of the model-facing schema. Per §B.3: "Offsets are
    not model-supplied ... a model-generated offset is one more thing to fabricate and
    nothing downstream needs the model's version of it." A verifier locates the quote
    with `str.find()` against the named source and attaches offsets afterward with
    `dataclasses.replace(span, start=..., end=...)` -- which is why this dataclass is
    frozen rather than merely read-only by convention, and why `start`/`end` default to
    `None` and never appear in `json_schema()`.
    """

    quote: str
    source_kind: SourceKind
    source_ref: str
    start: int | None = None
    end: int | None = None

    _FIELD_SCHEMAS: ClassVar[dict[str, dict[str, Any]]] = {
        "quote": _string(
            "A verbatim substring of the source named in source_ref. Copied, not "
            "paraphrased, reflowed or corrected -- it is checked with an exact string "
            "search against that source, and a span not found there fails the whole "
            "proposal, not just this span."
        ),
        "source_kind": _string_enum(
            [member.value for member in SourceKind],
            "Where the quote came from: a tool result received this run, or a "
            "grounding clause supplied in the conversation.",
        ),
        "source_ref": _string(
            "Exactly as the source gave it, e.g. "
            "'tool_result#3.timeline[4].facts.payment_cents' or 'KG:PLB netting'."
        ),
    }

    @classmethod
    def json_schema(cls) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": dict(cls._FIELD_SCHEMAS),
            "required": list(cls._FIELD_SCHEMAS),
            "additionalProperties": False,
        }

    @classmethod
    def parse(cls, payload: dict[str, Any]) -> Self:
        name = "EvidenceSpan"
        _require_object(payload, schema_name=name)
        _require_keys(payload, cls._FIELD_SCHEMAS, schema_name=name)
        quote = _require_str(payload, "quote", schema_name=name)
        source_kind = _require_enum(
            payload["source_kind"], SourceKind, field_name="source_kind", schema_name=name
        )
        source_ref = _require_str(payload, "source_ref", schema_name=name)
        return cls(quote=quote, source_kind=source_kind, source_ref=source_ref)


# ═══ ProposedAction ═══════════════════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class ProposedAction:
    """The Workflow Coordinator's proposal for one episode (`spec_prompts_roles.md` §B.3).

    Field order is the frozen contract: `reasoning` first (~60pp, see this module's
    docstring), then everything the reasoning is meant to justify, ending with the
    "I am stuck" pair `blocked`/`blocked_reason` -- which is advisory, never a way to
    signal "finished" (§B.1 "You cannot end this loop").
    """

    reasoning: str
    evidence: tuple[EvidenceSpan, ...]
    grounding_clause_id: str | None
    action: ActionEnum
    required_artifacts: tuple[str, ...]
    missing_evidence: tuple[str, ...]
    blocked: bool
    blocked_reason: str | None

    _FIELD_SCHEMAS: ClassVar[dict[str, dict[str, Any]]] = {
        "reasoning": _string(
            "Work the problem here before anything below it is decided. Cite what you "
            "read. This is not a summary of a conclusion you have already reached."
        ),
        "evidence": _array_of(
            EvidenceSpan.json_schema(),
            "At least one span unless action is ABSTAIN. Each quote is verified "
            "verbatim against the source it names.",
        ),
        "grounding_clause_id": _nullable_string(
            "The KG:<entity name> clause that covers this fact pattern, or null with "
            "the reason stated in `reasoning` if no supplied clause fits. A clause id "
            "stretched to fit a pattern it does not cover is graded as a fabrication."
        ),
        "action": _string_enum(
            [member.value for member in ActionEnum],
            "Exactly one, from the closed set. ABSTAIN is not a fallback for a hard "
            "call -- only for an unanswerable one.",
        ),
        "required_artifacts": _array_of_strings(
            "What a person needs in hand to execute the action, and nothing else. "
            "Empty iff action is ABSTAIN."
        ),
        "missing_evidence": _array_of_strings(
            "Always present, even when empty. One entry per thing absent that would "
            "have changed or firmed up the proposal, named as a record and a feed."
        ),
        "blocked": _boolean(
            "True when another round cannot help because the missing evidence does "
            "not exist in this system. Not a difficulty escape -- see blocked_reason."
        ),
        "blocked_reason": _nullable_string(
            "The specific missing record. Non-null iff blocked is true."
        ),
    }

    @classmethod
    def json_schema(cls) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": dict(cls._FIELD_SCHEMAS),
            "required": list(cls._FIELD_SCHEMAS),
            "additionalProperties": False,
        }

    @classmethod
    def parse(cls, payload: dict[str, Any]) -> Self:
        name = "ProposedAction"
        _require_object(payload, schema_name=name)
        _require_keys(payload, cls._FIELD_SCHEMAS, schema_name=name)

        reasoning = _require_str(payload, "reasoning", schema_name=name)
        evidence = tuple(
            EvidenceSpan.parse(item)
            for item in _require_list(payload, "evidence", schema_name=name)
        )
        grounding_clause_id = _require_nullable_str(payload, "grounding_clause_id", schema_name=name)
        action = _require_enum(payload["action"], ActionEnum, field_name="action", schema_name=name)
        required_artifacts = tuple(_require_list_of_str(payload, "required_artifacts", schema_name=name))
        missing_evidence = tuple(_require_list_of_str(payload, "missing_evidence", schema_name=name))
        blocked = _require_bool(payload, "blocked", schema_name=name)
        blocked_reason = _require_nullable_str(payload, "blocked_reason", schema_name=name)

        # §B.1 "If action is ABSTAIN, required_artifacts is empty and missing_evidence is not."
        if action is ActionEnum.ABSTAIN:
            if required_artifacts:
                raise SchemaError(
                    f"{name}.required_artifacts must be empty when action is ABSTAIN.",
                    repair_message=(
                        "action is ABSTAIN, so required_artifacts must be an empty "
                        "list -- an abstention names no artifact to act on. Empty the "
                        "list, or change the action."
                    ),
                )
            if not missing_evidence:
                raise SchemaError(
                    f"{name}.missing_evidence must be non-empty when action is ABSTAIN.",
                    repair_message=(
                        "action is ABSTAIN, so missing_evidence must name at least one "
                        "thing that is absent -- that is what makes the abstention a "
                        "recorded finding rather than a shrug. Add an entry, or change "
                        "the action."
                    ),
                )
        else:
            if len(evidence) < 1:
                raise SchemaError(
                    f"{name}.evidence must contain at least one span unless action is ABSTAIN.",
                    repair_message=(
                        f"action is {action.value}, so evidence must contain at least "
                        "one EvidenceSpan. Attach the span this action rests on, or "
                        "change the action to ABSTAIN."
                    ),
                )
            if not required_artifacts:
                raise SchemaError(
                    f"{name}.required_artifacts must be non-empty unless action is ABSTAIN.",
                    repair_message=(
                        f"action is {action.value}, so required_artifacts must list at "
                        "least one concrete thing a person needs to execute it. Add an "
                        "entry, or change the action to ABSTAIN."
                    ),
                )

        if blocked and blocked_reason is None:
            raise SchemaError(
                f"{name}.blocked_reason is required when blocked is true.",
                repair_message=(
                    "blocked is true, so blocked_reason must name the specific "
                    "missing record. Fill it in, or set blocked to false."
                ),
            )
        if not blocked and blocked_reason is not None:
            raise SchemaError(
                f"{name}.blocked_reason must be null when blocked is false.",
                repair_message=(
                    "blocked is false, so blocked_reason must be null. Clear it, or "
                    "set blocked to true if that is what you mean."
                ),
            )

        return cls(
            reasoning=reasoning,
            evidence=evidence,
            grounding_clause_id=grounding_clause_id,
            action=action,
            required_artifacts=required_artifacts,
            missing_evidence=missing_evidence,
            blocked=blocked,
            blocked_reason=blocked_reason,
        )


# ═══ CriterionFinding / EvaluatorVerdict ═════════════════════════════════════


@dataclass(frozen=True, slots=True)
class CriterionFinding:
    """One graded checklist criterion (`spec_prompts_roles.md` §C.3).

    `criterion_id` sits first without breaking reasoning-first: per §C.3's own
    rationale table, it "is not a reasoning-first violation -- it is an index into the
    checklist, not a judgment. Putting it first stops the decoder from grading a
    criterion it has not yet named." `reasoning` comes before `verdict`, which is
    last -- "the label is the output of the reasoning and the quote, not the input to
    them."
    """

    criterion_id: str
    reasoning: str
    cited_span: EvidenceSpan | None
    verdict: CriterionVerdict

    _FIELD_SCHEMAS: ClassVar[dict[str, dict[str, Any]]] = {
        "criterion_id": _string("The checklist criterion this finding grades. A label, not a judgment."),
        "reasoning": _string(
            "Settle this criterion alone, on its own evidence, before naming the "
            "verdict. Do not let an impression formed elsewhere carry over."
        ),
        "cited_span": _nullable_object(
            EvidenceSpan.json_schema(),
            "The span that backs this finding. Null iff verdict is NOT_ADDRESSED -- "
            "always available, and never wrong when the material genuinely says "
            "nothing about this criterion.",
        ),
        "verdict": _string_enum(
            [member.value for member in CriterionVerdict],
            "SUPPORTED: a span backs the proposal. CONTRADICTED: a span cuts against "
            "it. NOT_ADDRESSED: the material is silent.",
        ),
    }

    @classmethod
    def json_schema(cls) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": dict(cls._FIELD_SCHEMAS),
            "required": list(cls._FIELD_SCHEMAS),
            "additionalProperties": False,
        }

    @classmethod
    def parse(cls, payload: dict[str, Any]) -> Self:
        name = "CriterionFinding"
        _require_object(payload, schema_name=name)
        _require_keys(payload, cls._FIELD_SCHEMAS, schema_name=name)

        criterion_id = _require_str(payload, "criterion_id", schema_name=name)
        reasoning = _require_str(payload, "reasoning", schema_name=name)
        cited_span = _require_nullable_nested(payload, "cited_span", EvidenceSpan, schema_name=name)
        verdict = _require_enum(payload["verdict"], CriterionVerdict, field_name="verdict", schema_name=name)

        if verdict is CriterionVerdict.NOT_ADDRESSED and cited_span is not None:
            raise SchemaError(
                f"{name}.cited_span must be null when verdict is NOT_ADDRESSED.",
                repair_message=(
                    "verdict is NOT_ADDRESSED, so cited_span must be null -- "
                    "NOT_ADDRESSED means nothing here settles it either way. Clear "
                    "cited_span, or change the verdict to SUPPORTED or CONTRADICTED."
                ),
            )
        if verdict is not CriterionVerdict.NOT_ADDRESSED and cited_span is None:
            raise SchemaError(
                f"{name}.cited_span is required when verdict is {verdict.value}.",
                repair_message=(
                    f"verdict is {verdict.value}, so cited_span must quote the span "
                    "that supports it. Add cited_span, or change the verdict to "
                    "NOT_ADDRESSED."
                ),
            )

        return cls(criterion_id=criterion_id, reasoning=reasoning, cited_span=cited_span, verdict=verdict)


@dataclass(frozen=True, slots=True)
class EvaluatorVerdict:
    """The Evaluator's full grade (`spec_prompts_roles.md` §C.3).

    `per_criterion` first, `overall_reasoning` last -- the documented exception to
    "reasoning first" (see this module's docstring): `per_criterion` *is* the
    reasoning, distributed one judgment at a time, and `overall_reasoning` written
    first would be exactly the global-impression-then-distribute-verdicts failure
    §C.1 "How to grade" warns against. There is deliberately no `score` field --
    "Python computes it" from `per_criterion` and the checklist's weights.
    """

    per_criterion: tuple[CriterionFinding, ...]
    overall_reasoning: str

    _FIELD_SCHEMAS: ClassVar[dict[str, dict[str, Any]]] = {
        "per_criterion": _array_of(
            CriterionFinding.json_schema(),
            "One finding per checklist criterion, in checklist order, none omitted, "
            "none merged, none added.",
        ),
        "overall_reasoning": _string(
            "A summary of the grades already made above -- not a preamble to making "
            "them, and not a score in prose."
        ),
    }

    @classmethod
    def json_schema(cls) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": dict(cls._FIELD_SCHEMAS),
            "required": list(cls._FIELD_SCHEMAS),
            "additionalProperties": False,
        }

    @classmethod
    def parse(cls, payload: dict[str, Any]) -> Self:
        name = "EvaluatorVerdict"
        _require_object(payload, schema_name=name)
        _require_keys(payload, cls._FIELD_SCHEMAS, schema_name=name)

        raw_findings = _require_list(payload, "per_criterion", schema_name=name)
        if not raw_findings:
            raise SchemaError(
                f"{name}.per_criterion must contain at least one finding.",
                repair_message=(
                    "per_criterion must have one finding per checklist criterion. "
                    "Call emit_evaluation again with every criterion represented, in "
                    "checklist order."
                ),
            )
        per_criterion = tuple(CriterionFinding.parse(item) for item in raw_findings)
        overall_reasoning = _require_str(payload, "overall_reasoning", schema_name=name)
        return cls(per_criterion=per_criterion, overall_reasoning=overall_reasoning)


# ═══ Citation / InvestigatorReport ════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class Citation:
    """One resolved `[[kind:ref]]` token (`spec_prompts_roles.md` §0 citation grammar).

    Mechanically extracted by the harness from the Investigator's markdown -- the
    Investigator "does not emit structured output" (§A.2), so no model ever fills this
    shape directly. No `reasoning` field for the same reason `EvidenceSpan` has none:
    this is a parsed value, not a judgment.
    """

    kind: CitationKind
    ref: str

    _FIELD_SCHEMAS: ClassVar[dict[str, dict[str, Any]]] = {
        "kind": _string_enum(
            [member.value for member in CitationKind],
            "raw: an immutable source row. event: a dossier timeline index. calc: a "
            "field of a calculate_reconciliation result. verdict: a verdict or reason "
            "code.",
        ),
        "ref": _string("The identifier inside the brackets, e.g. '48213', '7', "
                        "'reimbursement_variance_cents', 'A-05'."),
    }

    @classmethod
    def json_schema(cls) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": dict(cls._FIELD_SCHEMAS),
            "required": list(cls._FIELD_SCHEMAS),
            "additionalProperties": False,
        }

    @classmethod
    def parse(cls, payload: dict[str, Any]) -> Self:
        name = "Citation"
        _require_object(payload, schema_name=name)
        _require_keys(payload, cls._FIELD_SCHEMAS, schema_name=name)
        kind = _require_enum(payload["kind"], CitationKind, field_name="kind", schema_name=name)
        ref = _require_str(payload, "ref", schema_name=name)
        return cls(kind=kind, ref=ref)


@dataclass(frozen=True, slots=True)
class InvestigatorReport:
    """The Investigator's answer, structured after the fact.

    Not covered by `spec_prompts_roles.md` §B.3/§C.3 -- those sections define only the
    two roles that reply through forced tool-use. The Investigator instead "replies in
    prose. Four sections, in this order, with exactly these headings" (§A.1 "Output"),
    and the harness extracts `list[Citation]` from it afterward (§A.2 "Final output
    shape"). This class is the harness's typed container for that parsed result --
    useful for the journal and the API response -- built by the harness, never filled
    by the model via a tool call. `episode_id` sits first as a label for the same
    reason `CriterionFinding.criterion_id` does; there is no model `reasoning` field to
    put first because the model never reasons *into* this shape, it reasons into free
    prose that this shape is parsed from.
    """

    episode_id: str
    cursor: str
    what_happened: str
    why_it_is_open: str
    what_i_could_not_determine: str
    what_a_human_should_check_first: str
    citations: tuple[Citation, ...]

    _FIELD_SCHEMAS: ClassVar[dict[str, dict[str, Any]]] = {
        "episode_id": _string("The episode this report explains, e.g. 'E-000812'."),
        "cursor": _string("The as-of date every statement in the report is relative to."),
        "what_happened": _string("## What happened -- the story in the order it was learned."),
        "why_it_is_open": _string(
            "## Why it is open -- the two track verdicts, the disposition and the "
            "reason codes turned into an explanation of the defect."
        ),
        "what_i_could_not_determine": _string(
            "## What I could not determine -- always present. The exact sentence "
            "'Nothing -- every question this episode raises is answered by the "
            "records above.' when nothing is missing."
        ),
        "what_a_human_should_check_first": _string(
            "## What a human should check first -- one to three concrete checks."
        ),
        "citations": _array_of(
            Citation.json_schema(),
            "Every [[kind:ref]] token found in the four sections above, resolved "
            "against the tool results this pass received.",
        ),
    }

    @classmethod
    def json_schema(cls) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": dict(cls._FIELD_SCHEMAS),
            "required": list(cls._FIELD_SCHEMAS),
            "additionalProperties": False,
        }

    @classmethod
    def parse(cls, payload: dict[str, Any]) -> Self:
        name = "InvestigatorReport"
        _require_object(payload, schema_name=name)
        _require_keys(payload, cls._FIELD_SCHEMAS, schema_name=name)

        episode_id = _require_str(payload, "episode_id", schema_name=name)
        cursor = _require_str(payload, "cursor", schema_name=name)
        what_happened = _require_str(payload, "what_happened", schema_name=name)
        why_it_is_open = _require_str(payload, "why_it_is_open", schema_name=name)
        what_i_could_not_determine = _require_str(payload, "what_i_could_not_determine", schema_name=name)
        what_a_human_should_check_first = _require_str(
            payload, "what_a_human_should_check_first", schema_name=name
        )
        citations = tuple(
            Citation.parse(item) for item in _require_list(payload, "citations", schema_name=name)
        )
        return cls(
            episode_id=episode_id,
            cursor=cursor,
            what_happened=what_happened,
            why_it_is_open=why_it_is_open,
            what_i_could_not_determine=what_i_could_not_determine,
            what_a_human_should_check_first=what_a_human_should_check_first,
            citations=citations,
        )


# ═══ AnalystReport ════════════════════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class AnalystReport:
    """The Portfolio Analyst's answer, structured after the fact.

    `.agents/specs/spec_analyst.md` "Role (Agent 2 owns)": four sections, "reasoning-first
    ordering preserved," plus `citations` -- the same shape `InvestigatorReport` takes for
    the same reason (see that class's docstring): the model "replies in prose ... and the
    harness extracts [citations] from it afterward," never filled by the model via a
    forced tool call, so there is no model `reasoning` field to put first here either.

    Deliberately has no `episode_id` (there is none -- this report is portfolio-wide) and,
    per the frozen field list in `spec_analyst.md`, no `cursor` either, unlike
    `InvestigatorReport`. That is a real asymmetry with the Investigator's shape, not an
    oversight: the frozen contract names exactly `state_of_the_book`, `what_is_concentrated`,
    `what_i_could_not_determine`, `where_to_look_first` and `citations`, and this class
    follows that list literally rather than adding a field the spec did not ask for. A
    caller that wants the "as of" date for display already has it -- `run_analyst`'s own
    `cursor` parameter -- without needing it duplicated onto the report.

    `what_is_concentrated` is the section the deterministic-boundary constraint this role
    is graded on lands on: `get_exception_queue`'s rows, presented in the order the tool
    returned them, with the sort that produced that order named in prose. Nothing here
    re-derives or checks that ordering structurally -- the four fields are plain strings,
    the same as `InvestigatorReport`'s -- because the enforcement is upstream, in the
    prompt (`prompts/analyst.py`) and in what the harness does and does not do to the
    model's prose (`roles/analyst.py`: no step anywhere resorts a section's text).
    """

    state_of_the_book: str
    what_is_concentrated: str
    what_i_could_not_determine: str
    where_to_look_first: str
    citations: tuple[Citation, ...]

    _FIELD_SCHEMAS: ClassVar[dict[str, dict[str, Any]]] = {
        "state_of_the_book": _string(
            "## State of the book -- counts and money by disposition, the verdict-pair "
            "distribution and reason-code frequencies, drawn from get_portfolio_overview."
        ),
        "what_is_concentrated": _string(
            "## What is concentrated -- where the money and the risk sit, presented in "
            "the order get_exception_queue returned its rows, with the order_by that "
            "produced that order named explicitly. A sort, never a judgement: no ranking "
            "here may come from anywhere but that one named tool call."
        ),
        "what_i_could_not_determine": _string(
            "## What I could not determine -- always present. The exact sentence "
            "'Nothing -- every question this portfolio raises is answered by the "
            "records above.' when nothing is missing."
        ),
        "where_to_look_first": _string(
            "## Where to look first -- one to three concrete checks, each naming the "
            "sort or tool call that surfaced it."
        ),
        "citations": _array_of(
            Citation.json_schema(),
            "Every [[kind:ref]] token found in the four sections above, resolved "
            "against the tool results this pass received.",
        ),
    }

    @classmethod
    def json_schema(cls) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": dict(cls._FIELD_SCHEMAS),
            "required": list(cls._FIELD_SCHEMAS),
            "additionalProperties": False,
        }

    @classmethod
    def parse(cls, payload: dict[str, Any]) -> Self:
        name = "AnalystReport"
        _require_object(payload, schema_name=name)
        _require_keys(payload, cls._FIELD_SCHEMAS, schema_name=name)

        state_of_the_book = _require_str(payload, "state_of_the_book", schema_name=name)
        what_is_concentrated = _require_str(payload, "what_is_concentrated", schema_name=name)
        what_i_could_not_determine = _require_str(payload, "what_i_could_not_determine", schema_name=name)
        where_to_look_first = _require_str(payload, "where_to_look_first", schema_name=name)
        citations = tuple(
            Citation.parse(item) for item in _require_list(payload, "citations", schema_name=name)
        )
        return cls(
            state_of_the_book=state_of_the_book,
            what_is_concentrated=what_is_concentrated,
            what_i_could_not_determine=what_i_could_not_determine,
            where_to_look_first=where_to_look_first,
            citations=citations,
        )


# ═══ WorkItemDraft ════════════════════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class WorkItemDraft:
    """The reviewer-editable draft behind design §7's "Add to-do" button.

    Not covered by §B.3/§C.3 either -- those describe what a *model* emits; this
    describes the human-editable object built from an accepted `ProposedAction` that
    becomes the arguments to the `create_mock_work_item` tool
    (`.agents/specs/spec_tools.md` §2.7) once a reviewer accepts or edits it. Design §7:
    "the recommendation is editable before it is accepted... a reviewer who can correct
    the action, strike an artifact, or rewrite the rationale is actually exercising
    judgment" -- `reasoning` is carried through (edited or not) as the audit trail for
    exactly that diff. `reasoning` is first with no documented exception here, since
    this class is not one of §B.3/§C.3's two forced-tool-use shapes.

    Deliberately thin validation: the authoritative checks for `summary` length, artifact
    count and untrusted-text markers live at the tool boundary in `tools.py`
    (`.agents/specs/spec_tools.md` §2.7 resolution steps) -- this class checks shape and
    the closed action vocabulary, not tool-specific numeric limits that would drift out
    of sync with the one place that actually enforces them.
    """

    reasoning: str
    episode_id: str
    recommended_action: ActionEnum
    summary: str
    required_artifacts: tuple[str, ...]
    grounding_clause_id: str | None
    dry_run: bool

    _FIELD_SCHEMAS: ClassVar[dict[str, dict[str, Any]]] = {
        "reasoning": _string(
            "Why this action, carried from the accepted proposal (or the reviewer's "
            "own words if they changed it) -- the audit trail for what a human changed."
        ),
        "episode_id": _string("The claim this work item is about, e.g. 'E-000812'."),
        "recommended_action": _string_enum(
            [member.value for member in ActionEnum],
            "The action a human reviewer is being asked to take, or ABSTAIN if none "
            "is recommended.",
        ),
        "summary": _string(
            "What the reviewer needs in order to act, and nothing else: the action, "
            "why, and the identifiers to quote."
        ),
        "required_artifacts": _array_of_strings(
            "Concrete things needed to carry out the action: form numbers, document "
            "names, reference numbers to quote."
        ),
        "grounding_clause_id": _nullable_string(
            "The KG:<entity name> clause this draft rests on, or null."
        ),
        "dry_run": _boolean(
            "true produces the draft without writing. false attempts a real commit "
            "and requires prior human confirmation. No default -- state intent "
            "explicitly."
        ),
    }

    @classmethod
    def json_schema(cls) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": dict(cls._FIELD_SCHEMAS),
            "required": list(cls._FIELD_SCHEMAS),
            "additionalProperties": False,
        }

    @classmethod
    def parse(cls, payload: dict[str, Any]) -> Self:
        name = "WorkItemDraft"
        _require_object(payload, schema_name=name)
        _require_keys(payload, cls._FIELD_SCHEMAS, schema_name=name)

        reasoning = _require_str(payload, "reasoning", schema_name=name)
        episode_id = _require_str(payload, "episode_id", schema_name=name)
        recommended_action = _require_enum(
            payload["recommended_action"], ActionEnum, field_name="recommended_action", schema_name=name
        )
        summary = _require_str(payload, "summary", schema_name=name)
        required_artifacts = tuple(_require_list_of_str(payload, "required_artifacts", schema_name=name))
        grounding_clause_id = _require_nullable_str(payload, "grounding_clause_id", schema_name=name)
        dry_run = _require_bool(payload, "dry_run", schema_name=name)

        return cls(
            reasoning=reasoning,
            episode_id=episode_id,
            recommended_action=recommended_action,
            summary=summary,
            required_artifacts=required_artifacts,
            grounding_clause_id=grounding_clause_id,
            dry_run=dry_run,
        )
