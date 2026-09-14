"""Tests for `recon.agents.schemas` and `recon.agents.prompts.*`.

Coder 4's slice of `.agents/specs/spec_contracts.md` §9: run before reporting, paste
the tail. House convention (`.agents/specs/spec_contracts.md` §8, and every existing
file under `tests/`): full-sentence test names.

Two things this file is deliberately careful about, both stemming from the same fact:
`.agents/specs/spec_prompts_roles.md` §B.3/§C.3 -- the frozen source for the schema
field lists -- itself declares *documented, reasoned* exceptions to "reasoning is
always the first field," and this module's tests are written to check the invariant
that is actually true rather than a blanket reading that would contradict the frozen
spec:

1. **Reasoning-first has three shapes, not one.** `ProposedAction` and `WorkItemDraft`
   put `reasoning` literally first (position 0) -- the "always" case. `CriterionFinding`
   puts a *label* (`criterion_id`) first and `reasoning` second, immediately before the
   verdict it justifies -- §C.3's own rationale table calls this out explicitly:
   "First but not a reasoning-first violation -- it is an index into the checklist, not
   a judgment." `EvaluatorVerdict` puts `per_criterion` first and `overall_reasoning`
   *last* -- again per §C.3's rationale table: "`per_criterion` *is* the reasoning,
   distributed. An `overall_reasoning` written first would be a global impression the
   individual grades then rationalise." `test_reasoning_is_first_where_the_spec_says_it_is_first`
   and `test_criterion_finding_and_evaluator_verdict_follow_their_documented_exception`
   check these as two separate, accurately-described invariants instead of one test that
   would have to either ignore the documented exceptions or misreport them as bugs.
   `EvidenceSpan` and `Citation` are mechanically-produced/verified value records with no
   judgment to reason about, so they carry no `reasoning` field at all -- see
   `schemas.py`'s module docstring -- and `test_value_record_schemas_carry_no_reasoning_field`
   checks that this is a deliberate absence, not an oversight.

2. **"Confidence" appears in the evaluator prompt, legitimately, as a prohibition.**
   §C.1 literally says '`Do not emit a score, a confidence, ...`'. A test asserting the
   bare word never appears would fail against the spec's own verbatim, load-bearing
   text. `test_evaluator_prompt_never_instructs_a_score_or_confidence_number` instead
   checks for *instructive* patterns ("give a confidence", "on a scale", "rate ...
   from", "score out of") -- the actual thing anti-pattern #6 in `spec_prompts_roles.md`
   §E is worried about -- rather than banning a word the prompt is required to use in
   order to ban it.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from recon.agents import schemas
from recon.agents import tools as agent_tools
from recon.agents.envelope import UNTRUSTED_CLOSE, UNTRUSTED_OPEN
from recon.agents.prompts import _shared, evaluator, investigator, proposer

# ═══ 1/2/3 — declaration order, shape, and the closed action vocabulary ═══════

#: Schemas where `reasoning` is, without documented exception, field zero.
_REASONING_FIRST_SCHEMAS: tuple[type, ...] = (schemas.ProposedAction, schemas.WorkItemDraft)

#: Every schema class this module owns, for the structural sweeps (oneOf/anyOf, depth).
_ALL_SCHEMAS: tuple[type, ...] = (
    schemas.EvidenceSpan,
    schemas.ProposedAction,
    schemas.CriterionFinding,
    schemas.EvaluatorVerdict,
    schemas.Citation,
    schemas.InvestigatorReport,
    schemas.WorkItemDraft,
)


def test_reasoning_is_first_where_the_spec_says_it_is_first():
    """`ProposedAction` and `WorkItemDraft`: `reasoning` is field zero, no exception."""
    for cls in _REASONING_FIRST_SCHEMAS:
        schema = cls.json_schema()
        properties = list(schema["properties"])
        assert properties[0] == "reasoning", f"{cls.__name__}.properties[0] was {properties[0]!r}"
        assert schema["required"][0] == "reasoning", f"{cls.__name__}.required[0] was not 'reasoning'"


def test_criterion_finding_and_evaluator_verdict_follow_their_documented_exception():
    """The two schemas `spec_prompts_roles.md` §C.3 explicitly carves an exception for.

    `CriterionFinding.criterion_id` is a checklist label, not a judgment, and sits
    before `reasoning`; `reasoning` still sits before the `verdict` it justifies.
    `EvaluatorVerdict.per_criterion` is first (it *is* the distributed reasoning) and
    `overall_reasoning` is last (a summary of grades already made, not a preamble).
    """
    finding_props = list(schemas.CriterionFinding.json_schema()["properties"])
    assert finding_props[0] == "criterion_id"
    assert finding_props.index("reasoning") < finding_props.index("verdict")

    verdict_props = list(schemas.EvaluatorVerdict.json_schema()["properties"])
    assert verdict_props[0] == "per_criterion"
    assert verdict_props[-1] == "overall_reasoning"
    assert "reasoning" not in verdict_props  # only "overall_reasoning" exists at this level


def test_value_record_schemas_carry_no_reasoning_field():
    """`EvidenceSpan` and `Citation` are parsed/verified values, not decisions."""
    for cls in (schemas.EvidenceSpan, schemas.Citation):
        properties = cls.json_schema()["properties"]
        assert "reasoning" not in properties, f"{cls.__name__} should not carry a reasoning field"


def test_required_lists_every_property_in_declaration_order():
    """Every schema's `required` is exactly its `properties`, same order (design §4:

    "optional-in-spirit means T | None, not merely omittable" -- so nothing is ever
    missing from `required`, only nullable.
    """
    for cls in _ALL_SCHEMAS:
        schema = cls.json_schema()
        assert schema["required"] == list(schema["properties"])


def test_json_schema_has_no_union_keyword_at_any_depth():
    """No `oneOf`/`anyOf` anywhere (design §4: "avoid oneOf" -- weakest provider area)."""

    def walk(node: Any) -> bool:
        if isinstance(node, dict):
            if "oneOf" in node or "anyOf" in node:
                return True
            return any(walk(v) for v in node.values())
        if isinstance(node, list):
            return any(walk(v) for v in node)
        return False

    for cls in _ALL_SCHEMAS:
        assert not walk(cls.json_schema()), f"{cls.__name__} contains oneOf/anyOf"


def test_json_schema_nesting_depth_never_exceeds_three():
    """Depth is counted in *object* layers, not scalar fields.

    `EvaluatorVerdict` (1) -> `CriterionFinding` via `per_criterion[]` (2) ->
    `EvidenceSpan` via `cited_span` (3) is the deepest path in this module, and it
    must sit at or under the ceiling, never over it.
    """

    def max_depth(node: dict[str, Any], current: int = 1) -> int:
        depth = current
        for prop_schema in node.get("properties", {}).values():
            candidate = prop_schema
            candidate_type = candidate.get("type")
            is_array = candidate_type == "array" or (
                isinstance(candidate_type, list) and "array" in candidate_type
            )
            if is_array:
                candidate = candidate.get("items", {})
                candidate_type = candidate.get("type")
            is_object = candidate_type == "object" or (
                isinstance(candidate_type, list) and "object" in candidate_type
            )
            if is_object and "properties" in candidate:
                depth = max(depth, max_depth(candidate, current + 1))
        return depth

    for cls in _ALL_SCHEMAS:
        depth = max_depth(cls.json_schema())
        assert depth <= 3, f"{cls.__name__} nests {depth} object layers deep"

    # The ceiling is actually reached, not just never violated -- a depth check that
    # never sees a 3 would not be exercising the interesting case.
    assert max_depth(schemas.EvaluatorVerdict.json_schema()) == 3


def test_action_enum_contains_exactly_the_seven_members_including_abstain():
    names = {member.name for member in schemas.ActionEnum}
    assert names == {
        "RESUBMIT",
        "APPEAL",
        "WRITE_OFF",
        "ESCALATE",
        "INVESTIGATE_CROSSWALK",
        "AWAIT_PAYER",
        "ABSTAIN",
    }
    assert schemas.ActionEnum.ABSTAIN in schemas.ActionEnum


# ═══ 4/5/6 — parse() error behaviour ══════════════════════════════════════════


def _valid_proposed_action_payload() -> dict[str, Any]:
    return {
        "reasoning": "The remittance shows a clearinghouse rejection, not a payer denial.",
        "evidence": [
            {
                "quote": "REJECTED AT CLEARINGHOUSE",
                "source_kind": "tool_result",
                "source_ref": "tool_result#1.timeline[2].facts.reject_codes",
            }
        ],
        "grounding_clause_id": "KG:D-6 crosswalk failure",
        "action": "RESUBMIT",
        "required_artifacts": ["Corrected NDC on the resubmitted claim"],
        "missing_evidence": [],
        "blocked": False,
        "blocked_reason": None,
    }


def test_parse_rejects_an_out_of_vocabulary_action_naming_all_seven_legal_values():
    payload = _valid_proposed_action_payload()
    payload["action"] = "CLOSE_CLAIM"

    with pytest.raises(schemas.SchemaError) as excinfo:
        schemas.ProposedAction.parse(payload)

    repair_message = excinfo.value.repair_message
    for legal_value in (
        "RESUBMIT",
        "APPEAL",
        "WRITE_OFF",
        "ESCALATE",
        "INVESTIGATE_CROSSWALK",
        "AWAIT_PAYER",
        "ABSTAIN",
    ):
        assert legal_value in repair_message, f"{legal_value} missing from repair_message"
    assert "CLOSE_CLAIM" in repair_message


def test_parse_rejects_a_payload_missing_a_required_field_naming_it():
    payload = _valid_proposed_action_payload()
    del payload["blocked_reason"]

    with pytest.raises(schemas.SchemaError) as excinfo:
        schemas.ProposedAction.parse(payload)

    assert "blocked_reason" in excinfo.value.repair_message
    assert "blocked_reason" in str(excinfo.value)


def test_parse_accepts_an_explicit_null_for_a_field_declared_optional():
    payload = _valid_proposed_action_payload()
    payload["grounding_clause_id"] = None  # T | None: null is a legal, present value

    action = schemas.ProposedAction.parse(payload)

    assert action.grounding_clause_id is None
    assert action.action is schemas.ActionEnum.RESUBMIT


# ═══ Cross-field validators the frozen spec names explicitly ═════════════════


def test_parse_requires_non_empty_missing_evidence_and_empty_artifacts_on_abstain():
    payload = _valid_proposed_action_payload()
    payload["action"] = "ABSTAIN"
    payload["evidence"] = []
    # required_artifacts still non-empty -- §B.1: "If action is ABSTAIN,
    # required_artifacts is empty and missing_evidence is not."
    with pytest.raises(schemas.SchemaError, match="required_artifacts"):
        schemas.ProposedAction.parse(payload)

    payload["required_artifacts"] = []
    # missing_evidence still empty -- also forbidden on ABSTAIN.
    with pytest.raises(schemas.SchemaError, match="missing_evidence"):
        schemas.ProposedAction.parse(payload)

    payload["missing_evidence"] = ["No remittance line ties this claim to a bank deposit."]
    parsed = schemas.ProposedAction.parse(payload)
    assert parsed.action is schemas.ActionEnum.ABSTAIN
    assert parsed.required_artifacts == ()


def test_parse_requires_blocked_reason_present_only_when_blocked_is_true():
    payload = _valid_proposed_action_payload()
    payload["blocked"] = True
    payload["blocked_reason"] = None
    with pytest.raises(schemas.SchemaError, match="blocked_reason"):
        schemas.ProposedAction.parse(payload)

    payload["blocked"] = False
    payload["blocked_reason"] = "The bank feed for this TRN02 has not arrived."
    with pytest.raises(schemas.SchemaError, match="blocked_reason"):
        schemas.ProposedAction.parse(payload)


def test_criterion_finding_requires_null_span_iff_not_addressed():
    supported_with_no_span = {
        "criterion_id": "grounding_present",
        "reasoning": "No clause was named and none was checked.",
        "cited_span": None,
        "verdict": "SUPPORTED",
    }
    with pytest.raises(schemas.SchemaError, match="cited_span"):
        schemas.CriterionFinding.parse(supported_with_no_span)

    not_addressed_with_span = dict(supported_with_no_span)
    not_addressed_with_span["verdict"] = "NOT_ADDRESSED"
    not_addressed_with_span["cited_span"] = {
        "quote": "irrelevant",
        "source_kind": "tool_result",
        "source_ref": "tool_result#1",
    }
    with pytest.raises(schemas.SchemaError, match="cited_span"):
        schemas.CriterionFinding.parse(not_addressed_with_span)

    ok = dict(supported_with_no_span)
    ok["verdict"] = "NOT_ADDRESSED"
    parsed = schemas.CriterionFinding.parse(ok)
    assert parsed.cited_span is None


# ═══ 7 — every prompt module renders, and a missing placeholder raises ═══════


def test_investigator_prompt_renders_with_a_full_context():
    rendered = investigator.render(
        episode_id="E-000812",
        cursor="2026-07-01T23:59:59Z",
        verdict_glossary="A-05: awaiting remittance",
        fence_nonce="9f2c41ab",
    )
    assert "E-000812" in rendered
    assert "{" not in rendered  # no stray unrendered placeholder


def test_investigator_prompt_raises_a_named_error_on_a_missing_placeholder():
    with pytest.raises(TypeError):
        investigator.render(episode_id="E-000812", cursor="2026-07-01")  # type: ignore[call-arg]


def test_proposer_prompt_renders_with_a_full_context():
    rendered = proposer.render(
        episode_id="E-000812",
        cursor="2026-07-01T23:59:59Z",
        verdict_glossary="B-01: clearinghouse rejection",
        grounding_clause_index="KG:D-6 crosswalk failure",
        fence_nonce="9f2c41ab",
    )
    assert "E-000812" in rendered
    assert "{episode_id}" not in rendered


def test_proposer_prompt_raises_a_named_error_on_a_missing_placeholder():
    with pytest.raises(TypeError):
        proposer.render(episode_id="E-000812")  # type: ignore[call-arg]


def test_evaluator_prompt_renders_with_a_full_context():
    rendered = evaluator.render(episode_id="E-000812", cursor="2026-07-01T23:59:59Z", fence_nonce="9f2c41ab")
    assert "E-000812" in rendered
    assert "{episode_id}" not in rendered
    assert "{fence_nonce}" not in rendered


def test_evaluator_prompt_raises_a_named_error_on_a_missing_placeholder():
    with pytest.raises(TypeError):
        evaluator.render(episode_id="E-000812")  # type: ignore[call-arg]


# ═══ 8 — the shared safety rules are byte-identical across all three files ═══


def test_all_three_prompts_embed_the_byte_identical_no_arithmetic_rule():
    for name, text in (
        ("investigator", investigator.INVESTIGATOR_SYSTEM_PROMPT),
        ("proposer", proposer.PROPOSER_SYSTEM_PROMPT),
        ("evaluator", evaluator.EVALUATOR_SYSTEM_PROMPT),
    ):
        assert _shared.NO_ARITHMETIC_RULE in text, f"{name} is missing NO_ARITHMETIC_RULE verbatim"


def test_all_three_prompts_embed_the_byte_identical_untrusted_text_rule():
    for name, text in (
        ("investigator", investigator.INVESTIGATOR_SYSTEM_PROMPT),
        ("proposer", proposer.PROPOSER_SYSTEM_PROMPT),
        ("evaluator", evaluator.EVALUATOR_SYSTEM_PROMPT),
    ):
        assert _shared.UNTRUSTED_TEXT_RULE in text, f"{name} is missing UNTRUSTED_TEXT_RULE verbatim"


def test_all_three_prompts_embed_the_byte_identical_no_self_fence_rule():
    """D3 (`spec_fixes_round1.md` Decision 2): only a tool result may ever carry a
    fence, so a fence in a role's own output is a structural failure. New shared
    constant, same "byte-identical in all three" discipline as the two rules above.
    """
    for name, text in (
        ("investigator", investigator.INVESTIGATOR_SYSTEM_PROMPT),
        ("proposer", proposer.PROPOSER_SYSTEM_PROMPT),
        ("evaluator", evaluator.EVALUATOR_SYSTEM_PROMPT),
    ):
        assert _shared.NO_SELF_FENCE_RULE in text, f"{name} is missing NO_SELF_FENCE_RULE verbatim"


# ═══ Round-1 fixes (`.agents/specs/spec_fixes_round1.md`), Fixer D ════════════
#
# D1 -- the prompts named tools (`get_episode_dossier`, `get_bank_match`) that the
#       registry does not define.
# D2 -- all three prompts taught a fence shape (`<<<UNTRUSTED_FEED_TEXT nonce="...">>>`)
#       no tool ever emits; the real one is `envelope.wrap()`'s `⟦UNTRUSTED:...#nonce⟧`.
# D4 -- `prompts/proposer.REPROMPT_TEMPLATE` was a second, unused feed-forward
#       mechanism alongside the one the harness actually calls (`rubric.build_critique`).


def _all_prompt_strings(module: Any) -> str:
    """Every exported *string* constant of a prompt module, concatenated.

    Deliberately covers every user-turn / repair-turn / tool-description template a
    module defines, not just its system prompt -- `ITERATION_1_USER_TURN` is exactly
    where D1's `get_episode_dossier` bug lived in `investigator.py` and `proposer.py`.
    The `isinstance` check skips the function exports (`render`, `render_iteration1_user_turn`, ...).
    """
    return "\n".join(value for name in module.__all__ if isinstance(value := getattr(module, name), str))


#: Forced-function output names: the single call the Proposer/Evaluator must end
#: their turn with (`schemas.ProposedAction`/`EvaluatorVerdict`'s own structured
#: output, wired through `tool_choice`). These follow the same `verb_noun` shape as
#: a real tool but are never entries in `tools.DISPATCH` -- a structurally different
#: mechanism from the read/write registry `tool_names()` reports on, so they are a
#: documented exception rather than a gap in the check below.
_NON_REGISTRY_TOOL_CALLS = frozenset({"emit_proposed_action", "emit_evaluation"})

#: The `verb_noun` shape every real tool (`get_*`/`calculate_*`/`create_*` in
#: `tools.TOOLS`) and every forced-output call (`emit_*`) in this codebase follows.
_TOOL_SHAPED_IDENTIFIER = re.compile(
    r"\b(?:get|calculate|create|emit|list|update|delete|fetch|set|dispatch)_[a-z][a-z0-9_]*\b"
)


def test_every_tool_shaped_identifier_mentioned_in_a_prompt_is_a_real_tool():
    """D1: `investigator.py` and `proposer.py` told the model to call
    `get_episode_dossier`; the registry (`tools.py`) has `get_episode`. Rather than
    re-checking that one literal string (there may be more, and a future edit could
    introduce a different one), grep every prompt module's exported text for
    anything shaped like a tool name and check it against the live registry.
    """
    real_tools = agent_tools.tool_names()
    assert real_tools, "tools.tool_names() returned nothing -- this test would pass vacuously"

    for module in (investigator, proposer, evaluator):
        text = _all_prompt_strings(module)
        mentioned = set(_TOOL_SHAPED_IDENTIFIER.findall(text))
        unknown = mentioned - real_tools - _NON_REGISTRY_TOOL_CALLS
        assert not unknown, f"{module.__name__} names {sorted(unknown)}, which tools.tool_names() does not recognise"


def test_no_prompt_teaches_the_old_untrusted_feed_text_fence_literal():
    """D2: all three prompts used to teach `<<<UNTRUSTED_FEED_TEXT nonce="...">>>` /
    `<<<END_UNTRUSTED_FEED_TEXT ...>>>`, a shape no tool ever emits. The literal
    must not appear anywhere a model can read it.
    """
    for module in (investigator, proposer, evaluator):
        text = _all_prompt_strings(module)
        assert "UNTRUSTED_FEED_TEXT" not in text, f"{module.__name__} still teaches the old fence literal"
    assert "UNTRUSTED_FEED_TEXT" not in _shared.UNTRUSTED_TEXT_RULE
    assert "UNTRUSTED_FEED_TEXT" not in _shared.NONCE_RULE


def test_untrusted_text_rule_and_both_nonce_rules_teach_the_real_envelope_fence():
    """The replacement text names the actual delimiters `envelope.wrap()` emits --
    imported from `envelope.py` (Decision 1's single fence authority), not spelled
    out a second time in this module -- rather than a fence no tool produces.
    """
    open_tag = UNTRUSTED_OPEN + "UNTRUSTED:"
    close_tag = UNTRUSTED_OPEN + "/UNTRUSTED:"

    assert open_tag in _shared.UNTRUSTED_TEXT_RULE
    assert close_tag in _shared.UNTRUSTED_TEXT_RULE
    assert UNTRUSTED_CLOSE in _shared.UNTRUSTED_TEXT_RULE

    assert open_tag in _shared.NONCE_RULE  # the Investigator/Proposer nonce rule
    assert open_tag in evaluator.EVALUATOR_SYSTEM_PROMPT  # the Evaluator's own nonce rule


def test_proposer_module_deleted_the_dead_reprompt_feed_forward_mechanism():
    """D4: two feed-forward mechanisms existed -- `rubric.build_critique`, which
    `roles/coordinator.py` actually calls between iterations, and this module's own
    `REPROMPT_TEMPLATE` / `NOT_ADDRESSED_ENTRY_TEMPLATE` / `CONTRADICTED_ENTRY_TEMPLATE`
    plus their `render_*` functions, which nothing in `src/` or `tests/` called. The
    unused mechanism is deleted rather than kept as a second, silently-dead answer.
    """
    for dead_name in (
        "REPROMPT_TEMPLATE",
        "NOT_ADDRESSED_ENTRY_TEMPLATE",
        "CONTRADICTED_ENTRY_TEMPLATE",
        "render_reprompt",
        "render_not_addressed_entry",
        "render_contradicted_entry",
    ):
        assert not hasattr(proposer, dead_name), f"proposer.{dead_name} should have been deleted (D4)"
        assert dead_name not in proposer.__all__


def test_proposer_prompt_version_digests_only_the_symbols_the_module_still_defines():
    assert proposer.PROMPT_VERSION == _shared.prompt_version(
        proposer.PROPOSER_SYSTEM_PROMPT,
        proposer.ITERATION_1_USER_TURN,
    )


# ═══ 9 — the evaluator never asks for a score or a confidence number ═════════


def test_evaluator_prompt_never_instructs_a_score_or_confidence_number():
    """Checks for *instructive* phrasing, not the bare word "confidence".

    See this module's docstring: the prompt legitimately contains the word
    "confidence" as part of banning it ('Do not emit a score, a confidence, ...').
    What must never appear is language that asks the model to actually produce one.
    """
    text = evaluator.EVALUATOR_SYSTEM_PROMPT.lower()
    assert "score out of" not in text
    assert "rate from" not in text
    instructive_patterns = (
        r"\bgive (a |your |me a )?confidence\b",
        r"\bprovide (a |your )?confidence\b",
        r"\bstate (a |your )?confidence\b",
        r"\bconfidence\s*[:=]\s*\d",
        r"\bon a scale\b",
        r"\brate (it |this |your )?from\b",
        r"\bassign a (score|confidence|rating)\b",
    )
    for pattern in instructive_patterns:
        assert not re.search(pattern, text), f"evaluator prompt matches instructive pattern {pattern!r}"


def test_evaluator_prompt_declares_no_score_field_and_defines_no_score_lexicon_leak():
    assert "score" not in schemas.EvaluatorVerdict.json_schema()["properties"]


# ═══ 10 — PROMPT_VERSION changes when the prompt text changes ════════════════


def test_prompt_version_changes_when_the_prompt_text_changes():
    original = _shared.prompt_version("some prompt text", "a second constant")
    modified = _shared.prompt_version("some prompt TEXT", "a second constant")  # one word cased differently
    unrelated_but_equal = _shared.prompt_version("some prompt text", "a second constant")

    assert original != modified
    assert original == unrelated_but_equal  # same inputs, same digest -- deterministic


def test_each_prompt_modules_version_is_a_digest_of_its_own_text_not_a_constant():
    assert investigator.PROMPT_VERSION == _shared.prompt_version(
        investigator.INVESTIGATOR_SYSTEM_PROMPT,
        investigator.ITERATION_1_USER_TURN,
        investigator.TOOL_BUDGET_CAP_MESSAGE,
        investigator.FIGURE_REPAIR_TEMPLATE,
    )
    # Editing the system prompt (even by one character) must move the version --
    # construct a modified copy rather than asserting against a hard-coded hash,
    # so this test stays meaningful if the prompt text is legitimately edited later.
    mutated = investigator.INVESTIGATOR_SYSTEM_PROMPT + " "
    mutated_version = _shared.prompt_version(
        mutated,
        investigator.ITERATION_1_USER_TURN,
        investigator.TOOL_BUDGET_CAP_MESSAGE,
        investigator.FIGURE_REPAIR_TEMPLATE,
    )
    assert mutated_version != investigator.PROMPT_VERSION

    assert proposer.PROMPT_VERSION != evaluator.PROMPT_VERSION != investigator.PROMPT_VERSION


# ═══ Anti-pattern #12 / E's closing note — the investigator never pre-empts an action,
# and no prompt file leaks the budgets that must live in orchestration code only ═══


def test_investigator_prompt_never_uses_an_action_enum_word_as_a_recommendation():
    # The prompt legitimately *names* the seven tokens once, in the explicit ban
    # ("do not use the words RESUBMIT, APPEAL, ..."). What must never happen is the
    # prompt recommending one outside that single enumerated ban sentence.
    ban_sentence_start = investigator.INVESTIGATOR_SYSTEM_PROMPT.index("Do not recommend a business action")
    ban_sentence_end = investigator.INVESTIGATOR_SYSTEM_PROMPT.index("pre-empt it.") + len("pre-empt it.")
    remainder = (
        investigator.INVESTIGATOR_SYSTEM_PROMPT[:ban_sentence_start]
        + investigator.INVESTIGATOR_SYSTEM_PROMPT[ban_sentence_end:]
    )
    for member in schemas.ActionEnum:
        assert member.value not in remainder, f"{member.value} leaked outside the ban sentence"


@pytest.mark.parametrize(
    "text",
    [
        investigator.INVESTIGATOR_SYSTEM_PROMPT,
        proposer.PROPOSER_SYSTEM_PROMPT,
        evaluator.EVALUATOR_SYSTEM_PROMPT,
    ],
)
def test_no_prompt_file_leaks_a_budget_that_must_live_in_orchestration_code(text: str):
    # spec_prompts_roles.md §E, closing note: "No prompt file may contain 80,
    # threshold, max_iterations, iteration limit, or a criterion weight."
    assert not re.search(r"\bthreshold\b", text, re.IGNORECASE)
    assert not re.search(r"\bmax_iterations\b", text, re.IGNORECASE)
    assert not re.search(r"\biteration limit\b", text, re.IGNORECASE)
    assert not re.search(r"\bcriterion weight", text, re.IGNORECASE)
    assert not re.search(r"\b80\b", text)
