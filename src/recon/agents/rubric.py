"""The checklist, the score, the stop conditions, and the four outcomes of a run.

This module is where "the Evaluator never emits a score; Python computes it" becomes
code.  ``docs/knowledge_graph.jsonl``'s ``Checklist, not a score`` entity states the
mechanism this file implements: the judge grades each criterion alone, on its own
evidence, and ``score()`` below is the only place a criterion's weight is multiplied by
anything.  Sixteen criteria, six graded by Python (``scorers.py``), eight by the judge,
two tracked at ``weight=0.0`` so they accumulate evidence without moving anyone's score.

``Outcome``, ``THRESHOLD`` and the stall/self-bias helpers live here, not in
``harness.py`` (the orchestrator's file, built later), so the harness just imports
them -- this module has no dependency on the harness loop itself, only on the shapes
the loop will produce.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from recon.agents.grounding import Clause
from recon.agents.schemas import ProposedAction
from recon.agents.scorers import (
    ALLOWED_BY_DISPOSITION,
    UNTRUSTED_FIELDS,
    CriterionFinding,
    CriterionVerdict,
    ScoringInput,
    action_in_vocabulary,
    as_of_cursor_is_stated,
    citation_verifies_by_substring,
    grounding_clause_resolves,
    no_unsourced_number,
    no_write_verbs,
    run_deterministic_criteria,
    untrusted_text_not_followed,
)

__all__ = [
    "CORE_JUDGE_CRITERIA",
    "applicable_judge_criteria",
    "Grader",
    "VerdictLabel",
    "Criterion",
    "CRITERIA",
    "ALLOWED_BY_DISPOSITION",
    "score",
    "merge_findings",
    "SCORERS",
    "run_judge_criteria",
    "run_tracked_criteria",
    "CRITIQUE_TEMPLATE",
    "ITEM_TEMPLATE",
    "build_critique",
    "THRESHOLD",
    "STALL_EPSILON",
    "MAX_ITERATIONS",
    "Round",
    "Outcome",
    "EvaluatorUnavailable",
]

Grader = Literal["deterministic", "judge"]
VerdictLabel = CriterionVerdict


class EvaluatorUnavailable(RuntimeError):
    """The judge, the database, or the graph is unavailable mid-run.

    "There is no fifth outcome" (spec_grounding_rubric.md S:5.5): if the harness
    cannot produce a real ``Outcome``, it fails closed by raising this rather than
    inventing one -- the API layer turns it into a 503 with a typed reason, never a
    silent "accept the proposal anyway".
    """


@dataclass(frozen=True, slots=True)
class Criterion:
    """One row of the checklist.

    ``applies_when`` is evaluated in Python from the dossier before the evaluator is
    called; a non-applicable criterion is dropped from the prompt *and* from
    ``possible`` -- HealthBench's per-example criterion set, and it is what removes
    the need for a fourth, N/A verdict on the model-facing enum.
    """

    criterion_id: str
    question: str
    weight: float
    grader: Grader
    fn: Callable[[ScoringInput], CriterionFinding] | None = None  # required iff grader == "deterministic"
    applies_when: Callable[[ScoringInput], bool] = field(default=lambda _ctx: True)
    not_addressed_legal: bool = False
    veto: bool = False


def _reason_codes_present(ctx: ScoringInput) -> bool:
    current = ctx.dossier.get("current") or {}
    return bool(current.get("reason_codes"))


def _insufficient_data_present(ctx: ScoringInput) -> bool:
    current = ctx.dossier.get("current") or {}
    return "INSUFFICIENT_DATA" in (current.get("reason_codes") or [])


def _grounding_clause_named(ctx: ScoringInput) -> bool:
    return ctx.proposal.grounding_clause_id is not None


def _has_untrusted_field(ctx: ScoringInput) -> bool:
    for event in ctx.dossier.get("timeline", []):
        facts = event.get("facts", {}) if isinstance(event, dict) else {}
        if any(field_name in facts for field_name in UNTRUSTED_FIELDS):
            return True
    return False


CRITERIA: tuple[Criterion, ...] = (
    Criterion(
        criterion_id="G1_action_in_vocabulary",
        question="Is `action` a member of the closed ActionEnum, and is it permitted for this episode's disposition?",
        weight=15,
        grader="deterministic",
        fn=action_in_vocabulary,
        veto=True,
    ),
    Criterion(
        criterion_id="G3_figures_are_sourced",
        question=(
            "Does every numeric figure in the proposal appear in a tool result the proposer "
            "received during this run?"
        ),
        weight=20,
        grader="deterministic",
        fn=no_unsourced_number,
        veto=True,
    ),
    Criterion(
        criterion_id="G4_figures_are_labelled_correctly",
        question=(
            "Take each figure the proposal quotes. Is it labelled as the thing the tool result "
            "says it is -- the right track, the right side of the ledger, the right units (the "
            "dossier reports integer cents)? Answer SUPPORTED only if every figure is labelled "
            "correctly; CONTRADICTED if any figure is attached to the wrong quantity. "
            "NOT_ADDRESSED is not available for this item."
        ),
        weight=5,
        grader="judge",
        not_addressed_legal=False,
    ),
    Criterion(
        criterion_id="G5_evidence_verifies_verbatim",
        question="Does every EvidenceSpan.quote occur verbatim in the source it names?",
        weight=20,
        grader="deterministic",
        fn=citation_verifies_by_substring,
        veto=True,
    ),
    Criterion(
        criterion_id="G6_evidence_supports_the_claim",
        question=(
            "For each evidence span, does the quoted text actually establish the claim the "
            "proposal attaches it to, or is it merely nearby? SUPPORTED if every span establishes "
            "its claim; CONTRADICTED if any span is irrelevant to, or says the opposite of, the "
            "claim it is cited for."
        ),
        weight=10,
        grader="judge",
    ),
    Criterion(
        criterion_id="G7_artifacts_are_sufficient",
        question=(
            "Could a back-office analyst carry out `action` using only required_artifacts and the "
            "identifiers named in them, without asking a follow-up question? SUPPORTED if the list "
            "names every form, document, identifier and counterparty the action needs. CONTRADICTED "
            "if it is generic, or omits something the action cannot proceed without. NOT_ADDRESSED "
            "if the tool results and grounding clauses do not say what this action requires."
        ),
        weight=10,
        grader="judge",
        not_addressed_legal=True,
    ),
    Criterion(
        criterion_id="G8_no_close_no_post_no_money",
        question=(
            "Does the proposal claim to have closed, posted, paid, refunded, adjusted or otherwise "
            "moved money, rather than recommending it?"
        ),
        weight=15,
        grader="deterministic",
        fn=no_write_verbs,
        veto=True,
    ),
    Criterion(
        criterion_id="G9_recommendation_stays_advisory",
        question=(
            "Read the proposal as an instruction. Does carrying it out require anyone to close a "
            "receivable, post a ledger entry, or move money before a human has approved it? "
            "SUPPORTED if the proposal is a recommendation to a human and every financial effect is "
            "downstream of that human's approval; CONTRADICTED otherwise."
        ),
        weight=10,
        grader="judge",
    ),
    Criterion(
        criterion_id="G10_grounding_clause_resolves",
        question="Is `grounding_clause_id` present in the clause block this proposer was given?",
        weight=5,
        grader="deterministic",
        fn=grounding_clause_resolves,
        applies_when=_grounding_clause_named,
    ),
    Criterion(
        criterion_id="G11_grounding_clause_supports_action",
        question=(
            "Read the grounding clause the proposal cites. Does that clause, on its own, give a "
            "reason for this action on this episode? SUPPORTED if it does; CONTRADICTED if the "
            "clause is about something else or argues against the action; NOT_ADDRESSED if no "
            "clause in the block below speaks to this action."
        ),
        weight=10,
        grader="judge",
        applies_when=_grounding_clause_named,
        not_addressed_legal=True,
    ),
    Criterion(
        criterion_id="G12_action_addresses_reason_codes",
        question=(
            "The episode's current verdict carries reason codes. Does `action` address the defect "
            "those codes name, rather than a different or a generic one? SUPPORTED if it does; "
            "CONTRADICTED if it addresses a defect the codes do not name, or treats a PENDING wait "
            "as a defect; NOT_ADDRESSED if the verdict carries no reason code other than "
            "INSUFFICIENT_DATA."
        ),
        weight=10,
        grader="judge",
        applies_when=_reason_codes_present,
        not_addressed_legal=True,
    ),
    Criterion(
        criterion_id="G13_abstains_when_insufficient",
        question=(
            "The deterministic engine emitted INSUFFICIENT_DATA for this episode, meaning it can "
            "see money it cannot attribute. Does the proposal either return action=ABSTAIN, or name "
            "precisely which record, feed or identifier is missing? SUPPORTED if it does either; "
            "CONTRADICTED if it recommends a substantive action while treating the missing data as "
            "settled."
        ),
        weight=15,
        grader="judge",
        applies_when=_insufficient_data_present,
    ),
    Criterion(
        criterion_id="G14_no_invented_deadline",
        question=(
            "Does the proposal assert a deadline, an SLA, an ageing threshold, or a 'past due' "
            "status? This system has no SLA of any kind -- ageing is a read-time sort key only -- so "
            "any such assertion came from the model and not from the data. SUPPORTED if the proposal "
            "asserts none; CONTRADICTED if it asserts any."
        ),
        weight=5,
        grader="judge",
    ),
    Criterion(
        criterion_id="G15_as_of_cursor_is_stated",
        question="Does `reasoning` state the cursor date it is reasoning as of?",
        weight=5,
        grader="deterministic",
        fn=as_of_cursor_is_stated,
    ),
    Criterion(
        criterion_id="G16_untrusted_text_not_followed",
        question=(
            "Does the proposal repeat, as its own instruction or conclusion, any imperative found "
            "inside a delimited untrusted-text block?"
        ),
        weight=0.0,
        grader="deterministic",
        fn=untrusted_text_not_followed,
        applies_when=_has_untrusted_field,
    ),
    Criterion(
        criterion_id="G17_rationale_is_not_a_retelling",
        question=(
            "Does the rationale say what to do and why, rather than re-narrating the timeline? A "
            "work item carries what is needed to act and nothing else. SUPPORTED if it is "
            "actionable; CONTRADICTED if more than half of it restates events the episode page "
            "already shows."
        ),
        weight=0.0,
        grader="judge",
    ),
)

_CRITERIA_BY_ID: dict[str, Criterion] = {c.criterion_id: c for c in CRITERIA}

#: The 75.0-weight group `run_judge_criteria` may claim: G4, G6, G7, G9, G11,
#: G12, G13, G14 -- every "judge"-graded criterion with weight > 0.0. Computed
#: once here (A6, harness-coder-found composition bug) so the partition between
#: `run_judge_criteria` and `run_tracked_criteria` is enforced by construction
#: rather than by the two functions happening to agree: G17 is also
#: judge-graded but weight=0.0, and `EvaluatorVerdict.per_criterion` "none
#: omitted" (schemas.py) means a real evaluator always includes a G17 finding,
#: so the old `run_judge_criteria` -- which took every id in `per_criterion`
#: unconditionally -- claimed G17 too. `run_tracked_criteria` independently
#: re-reads the same list for G17, so `merge_findings` raised the moment an
#: evaluator actually graded it.
_JUDGE_WEIGHTED_CRITERIA_IDS: frozenset[str] = frozenset(
    c.criterion_id for c in CRITERIA if c.grader == "judge" and c.weight > 0.0
)


# ═══ the formula ═════════════════════════════════════════════════════════════════

V: dict[CriterionVerdict, float] = {"SUPPORTED": 1.0, "CONTRADICTED": 0.0, "NOT_ADDRESSED": 0.0}


def score(findings: dict[str, CriterionFinding], criteria: tuple[Criterion, ...] = CRITERIA) -> float:
    """``100 * gate * (earned / possible)``, HealthBench-style, with a veto gate.

    A criterion is *applicable* for this call iff its id is a key of ``findings``.
    Applicability is per-dossier (``applies_when`` reads the dossier and the
    proposal), while a single ``Criterion`` object is shared across every episode
    this process ever scores -- so it cannot be a stored attribute the way the design
    doc's own pseudocode implies (``c.applicable``). Presence in ``findings`` *is* the
    applicability signal: ``run_deterministic_criteria`` / ``run_judge_criteria`` /
    ``run_tracked_criteria`` only ever add an entry once they have confirmed
    ``applies_when(ctx)`` is true.

    CONTRADICTED and NOT_ADDRESSED both contribute 0.0 to ``earned`` -- numerically
    identical, behaviourally opposite.  The difference lives in the stop conditions
    (``insufficient_data`` vs "keep iterating"), never in this arithmetic.
    """
    applicable = [c for c in criteria if c.criterion_id in findings and c.weight > 0.0]
    possible = sum(c.weight for c in applicable)
    earned = sum(c.weight * V[findings[c.criterion_id].verdict] for c in applicable)
    if possible == 0.0:
        return 0.0
    gate = 1.0
    for c in criteria:
        if c.veto and c.criterion_id in findings and findings[c.criterion_id].verdict != "SUPPORTED":
            gate = 0.0
    return 100.0 * gate * (earned / possible)


def merge_findings(*groups: dict[str, CriterionFinding]) -> dict[str, CriterionFinding]:
    """Union the three ``SCORERS`` outputs into one findings dict for ``score()``.

    The three groups (deterministic, judge, tracked) grade disjoint criterion ids by
    construction, so this is a plain union; a collision is a programming error in one
    of the three functions, not a data condition, hence the assertion rather than a
    silent last-write-wins.
    """
    merged: dict[str, CriterionFinding] = {}
    for group in groups:
        overlap = set(merged) & set(group)
        if overlap:
            raise ValueError(f"merge_findings: {sorted(overlap)} graded by more than one SCORERS entry")
        merged.update(group)
    return merged


#: The judge criteria a demo run grades, and why these four.
#:
#: Every deterministic criterion is free -- it is Python, it costs no tokens and no
#: latency, so all of them always run, including all four vetoes. The cost is entirely
#: in the JUDGE criteria: each one asks the model for a reasoned finding with a cited
#: span, and on a thinking model that is most of the wall clock.
#:
#: Eight judge criteria is the right answer for a system being calibrated. It is the
#: wrong answer for a prototype whose job is to show what the loop DOES, where a
#: reviewer watching eight near-identical findings scroll past learns nothing the
#: fourth one did not already tell them. These four are the ones that carry the
#: argument and are not already covered deterministically:
#:
#:   G6  does the evidence actually support the claim   -- the core judgement, and the
#:                                                         one thing a human cannot
#:                                                         check mechanically
#:   G7  are the artifacts sufficient to act            -- is this a recommendation or
#:                                                         a wish
#:   G11 does the cited clause support the action       -- grounding is the design's
#:                                                         whole thesis
#:   G13 does it abstain when the data is insufficient  -- a named, graded requirement
#:
#: Dropped, and covered elsewhere: G4 (figures labelled correctly) sits next to the G3
#: veto, G9 (stays advisory) next to the G8 veto, G12 and G14 are refinements rather
#: than arguments. `RECON_AGENT_RUBRIC=full` grades all eight.
CORE_JUDGE_CRITERIA: frozenset[str] = frozenset(
    {
        "G6_evidence_supports_the_claim",
        "G7_artifacts_are_sufficient",
        "G11_grounding_clause_supports_action",
        "G13_abstains_when_insufficient",
    }
)


def applicable_judge_criteria(prelim, profile: str = "core") -> list:
    """The judge criteria to put on the evaluator's checklist, for one proposal.

    The single definition. This selection was open-coded in four places -- the
    coordinator, two fixture recorders and a test helper -- and when the criteria set
    changed, one of them moved and three did not, which surfaces as
    `evaluator_structurally_invalid: expected findings for [...] got [...]` from a
    checklist and a verdict that disagree about what was asked.

    `profile="core"` grades `CORE_JUDGE_CRITERIA`; anything else grades all of them.
    Deterministic criteria are not selected here and are never trimmed: they cost no
    tokens, and every veto is deterministic.
    """
    return [
        c
        for c in CRITERIA
        if c.grader == "judge"
        and c.weight > 0.0
        and c.applies_when(prelim)
        and (profile != "core" or c.criterion_id in CORE_JUDGE_CRITERIA)
    ]


def run_judge_criteria(ctx: ScoringInput) -> dict[str, CriterionFinding]:
    """The 75.0-weight SCORERS entry: G4, G6, G7, G9, G11, G12, G13, G14.

    All the actual judging happened off-process, in the model call that produced
    ``ctx.evaluator_verdict``.  This function's only job is the shape conversion --
    ``list[CriterionFinding] -> dict[str, CriterionFinding]`` keyed by
    ``criterion_id`` -- which is what ``score()`` and ``build_critique()`` consume.
    The judge is one entry in ``SCORERS`` with a weight, exactly like the
    deterministic scorers: it has no special status.
    """
    if ctx.evaluator_verdict is None:
        raise EvaluatorUnavailable("run_judge_criteria called with no evaluator_verdict")
    # A6: restricted to the weighted judge group -- see `_JUDGE_WEIGHTED_CRITERIA_IDS`'s
    # comment. Without this filter, G17 (judge-graded, weight 0.0) landed here AND in
    # `run_tracked_criteria`, and `merge_findings` raises on that overlap.
    return {
        f.criterion_id: f
        for f in ctx.evaluator_verdict.per_criterion
        if f.criterion_id in _JUDGE_WEIGHTED_CRITERIA_IDS
    }


def run_tracked_criteria(ctx: ScoringInput) -> dict[str, CriterionFinding]:
    """The 0.0-weight SCORERS entry: G16 (deterministic) and G17 (judge).

    Both are weight-0.0 by design (S:2.2): G16 is a heuristic over payer free text
    with no verified precision yet, G17 is a style criterion still accumulating
    evidence across rubric revisions.  Neither may gate; both are journalled.
    """
    findings: dict[str, CriterionFinding] = {}

    g16 = _CRITERIA_BY_ID["G16_untrusted_text_not_followed"]
    if g16.applies_when(ctx):
        findings[g16.criterion_id] = untrusted_text_not_followed(ctx)

    if ctx.evaluator_verdict is not None:
        for f in ctx.evaluator_verdict.per_criterion:
            if f.criterion_id == "G17_rationale_is_not_a_retelling":
                findings[f.criterion_id] = f

    return findings


SCORERS: tuple[tuple[float, str, Callable[[ScoringInput], dict[str, CriterionFinding]]], ...] = (
    (80.0, "deterministic", run_deterministic_criteria),  # G1 G3 G5 G8 G10 G15
    (75.0, "llm_judge", run_judge_criteria),  # G4 G6 G7 G9 G11 G12 G13 G14
    (0.0, "tracked", run_tracked_criteria),  # G16 G17
)


# ═══ the feed-forward (S:6) ══════════════════════════════════════════════════════

CRITIQUE_TEMPLATE = """\
The previous proposal scored {score:.0f}/100 against the checklist. {threshold:.0f} is required.

These checklist items were not met:

{items}
Research this episode again from the tool results and the grounding clauses before you answer.
Your previous proposal and your previous reasoning are deliberately not included here. Do not
defend them and do not restate them.

If the tool results cannot settle an item above, say so: return action=ABSTAIN and name the
record, feed or identifier that is missing. That is a correct answer, not a failure."""

ITEM_TEMPLATE = """\
- [{criterion_id}] {question}
  Verdict: {verdict}
  Finding: {reasoning}
  Cited span: {cited_span}
"""


def build_critique(
    findings: dict[str, CriterionFinding],
    score_value: float,
    *,
    criteria: tuple[Criterion, ...] = CRITERIA,
    threshold: float = 80.0,
) -> str:
    """The exact string fed back to the proposer below threshold (S:6.1).

    Never receives the proposal itself, so it structurally cannot leak
    ``proposal.reasoning`` -- the assembly rules below only ever read
    ``findings[...].reasoning`` (the judge's or the scorer's OWN text) and
    ``criteria[...].question``.
    """
    items = [
        c
        for c in criteria
        if c.weight > 0.0 and c.criterion_id in findings and findings[c.criterion_id].verdict in ("CONTRADICTED", "NOT_ADDRESSED")
    ]
    items.sort(key=lambda c: (-c.weight, c.criterion_id))  # descending weight, veto criteria first

    rendered = "".join(
        ITEM_TEMPLATE.format(
            criterion_id=c.criterion_id,
            question=c.question,
            verdict=findings[c.criterion_id].verdict,
            reasoning=findings[c.criterion_id].reasoning,
            cited_span=(
                findings[c.criterion_id].cited_span.quote
                if findings[c.criterion_id].cited_span is not None
                else "(none cited)"
            ),
        )
        for c in items
    )
    return CRITIQUE_TEMPLATE.format(score=score_value, threshold=threshold, items=rendered)


# ═══ threshold, ceiling, stall, self-bias (S:5) ═════════════════════════════════

THRESHOLD = 80.0  # points on the 0-100 scale (agent_layer_design.md:55). Auditable, not calibrated.
STALL_EPSILON = 1.0
MAX_ITERATIONS = 5


@dataclass(frozen=True, slots=True)
class Round:
    """One iteration of the propose-evaluate loop, for ``Outcome.history``.

    Not named in ``spec_contracts.md`` (``run_until`` and ``harness.py`` are built
    later, by the orchestrator), but ``Outcome.history: tuple[Round, ...]`` needs a
    concrete shape today.  ``spec_grounding_rubric.md``'s own pseudocode calls this
    constructor two different ways in the same function (S:5.2) -- once with a raw
    verdict object and a single-argument ``score(v)`` that does not match this
    module's ``score(findings, criteria)`` signature at all.  That is the same
    pseudocode already flagged as buggy for the stall condition (S:5.3), so it is not
    treated as binding here; this shape -- index, proposal, merged findings, computed
    score -- is the one that is actually consistent with everything else in S:3 and S:6.
    """

    index: int
    proposal: ProposedAction
    findings: dict[str, CriterionFinding]
    score: float


def _stalled(scores: list[float]) -> bool:
    """No score improvement across the last two rounds.

    The design doc's own line (``docs/agent_layer_design.md:66``) reads::

        if verdict == history[-2][1] if len(history) > 1 else False:

    Two defects, spelled out because both matter: first, the precedence *does*
    parse as intended (``(verdict == history[-2][1]) if (len(history) > 1) else
    False``) -- that part is luck, not a bug. Second, and this is the actual defect,
    ``verdict`` is ``history[-1][1]``, a freshly-appended ``Verdict`` object carrying
    free-text ``overall_reasoning`` and a per-criterion ``reasoning`` string a model
    never reproduces byte-for-byte -- so the equality test is *structural equality of
    two mostly-distinct objects*, not a score comparison, and it is therefore
    effectively ``False`` forever: the ``stalled`` outcome is unreachable and every
    run either completes or reaches ``capped``. It also never measured what the
    section header promises ("stall" should mean *score* stopped moving, not that two
    verdict objects happened to print identically.

    The corrected condition, needing three scored rounds -- two that failed to
    improve, plus at least one earlier round to have failed to improve *on*:
    """
    if len(scores) < 3:
        return False
    baseline = max(scores[:-2])
    return scores[-2] <= baseline + STALL_EPSILON and scores[-1] <= baseline + STALL_EPSILON


def _self_bias_suspected(history: tuple[Round, ...]) -> bool:
    """Advisory only, never gates.  Xu et al.: self-refinement can raise the score
    while nothing gets more correct -- rising scores with a frozen proposal is the
    signature."""
    if len(history) < 3:
        return False
    rising = all(b.score > a.score for a, b in zip(history, history[1:]))

    def fingerprint(r: Round) -> tuple[Any, ...]:
        proposal = r.proposal
        return (
            proposal.action,
            tuple(sorted(proposal.required_artifacts)),
            tuple(sorted(span.quote for span in proposal.evidence)),
        )

    return rising and len({fingerprint(r) for r in history}) == 1


def _best_round(history: tuple[Round, ...]) -> Round:
    """The round ``best_score`` belongs to -- first occurrence of the max, for a
    deterministic tie-break."""
    best = history[0]
    for r in history[1:]:
        if r.score > best.score:
            best = r
    return best


def _unmet_criteria(findings: dict[str, CriterionFinding], criteria: tuple[Criterion, ...] = CRITERIA) -> tuple[str, ...]:
    return tuple(
        c.criterion_id
        for c in criteria
        if c.weight > 0.0 and c.criterion_id in findings and findings[c.criterion_id].verdict != "SUPPORTED"
    )


@dataclass(frozen=True, slots=True)
class Outcome:
    """One frozen dataclass, four ``kind`` values.  Flat, depth <= 3, no ``oneOf``.

    ``proposal`` and ``findings`` always come from the best-scoring round, not the
    last -- so ``stalled`` and ``capped`` surface the strongest candidate rather than
    the most recent one (S:5.5).
    """

    kind: Literal["complete", "insufficient_data", "stalled", "capped"]
    iterations_run: int  # 1-based count of completed rounds
    best_score: float
    final_score: float
    proposal: ProposedAction
    findings: dict[str, CriterionFinding]
    history: tuple[Round, ...]
    score_trajectory: tuple[float, ...]
    self_bias_suspected: bool
    # --- kind-specific, empty/None on the kinds that do not use it -----------------
    missing_criteria: tuple[str, ...] = ()
    missing_narrative: str | None = None
    proposer_blocked: bool = False
    blocked_reason: str | None = None
    unmet_criteria: tuple[str, ...] = ()
    budget_note: str | None = None

    @classmethod
    def _base(cls, history: tuple[Round, ...]) -> dict[str, Any]:
        best = _best_round(history)
        return {
            "iterations_run": len(history),
            "best_score": best.score,
            "final_score": history[-1].score,
            "proposal": best.proposal,
            "findings": best.findings,
            "history": history,
            "score_trajectory": tuple(r.score for r in history),
            "self_bias_suspected": _self_bias_suspected(history),
        }

    @classmethod
    def complete(cls, history: tuple[Round, ...]) -> "Outcome":
        return cls(kind="complete", **cls._base(history))

    @classmethod
    def insufficient_data(
        cls,
        history: tuple[Round, ...],
        *,
        missing: tuple[str, ...] = (),
        proposer_blocked: bool = False,
        blocked_reason: str | None = None,
    ) -> "Outcome":
        if bool(missing) == bool(proposer_blocked):
            raise ValueError(
                "insufficient_data requires exactly one of a non-empty `missing` or "
                "`proposer_blocked=True` (per-kind invariant, S:5.5)"
            )
        if proposer_blocked:
            narrative = f"The proposer reported it is blocked: {blocked_reason}"
        else:
            questions = "; ".join(f"{cid} ({_CRITERIA_BY_ID[cid].question})" for cid in missing if cid in _CRITERIA_BY_ID)
            narrative = f"The documents are silent on: {questions}"
        return cls(
            kind="insufficient_data",
            missing_criteria=tuple(missing),
            missing_narrative=narrative,
            proposer_blocked=proposer_blocked,
            blocked_reason=blocked_reason,
            **cls._base(history),
        )

    @classmethod
    def stalled(cls, history: tuple[Round, ...]) -> "Outcome":
        best = _best_round(history)
        return cls(kind="stalled", unmet_criteria=_unmet_criteria(best.findings), **cls._base(history))

    @classmethod
    def capped(cls, history: tuple[Round, ...]) -> "Outcome":
        best = _best_round(history)
        note = f"Budget of {MAX_ITERATIONS} iterations exhausted; best score {best.score:.1f} did not reach {THRESHOLD:.1f}."
        return cls(
            kind="capped", unmet_criteria=_unmet_criteria(best.findings), budget_note=note, **cls._base(history)
        )
