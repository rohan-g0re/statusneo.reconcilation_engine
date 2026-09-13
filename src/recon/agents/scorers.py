"""Deterministic scorer bodies for the agent-layer rubric.

``rubric.py`` owns the criteria table and the scoring formula; this module owns the
functions a deterministic criterion actually calls (spec_grounding_rubric.md's
amendment to the build plan -- the number detector alone runs to roughly 150 lines,
and a rubric that has to scroll past it stops reading like a table).

The load-bearing idea in every function below is the same one the assignment states
three times over: *every number is born in Python* (``docs/knowledge_graph.jsonl``,
entity ``Deterministic boundary``).  A scorer here never asks a model anything; it
either finds a claimed figure, span or action in the record the proposer was actually
given, or it does not, and the answer is mechanical either way.

**On the two cross-module dependencies this file uses.**  ``recon.agents.grounding``
is this coder's own module.  ``recon.agents.schemas`` (``ProposedAction``,
``EvaluatorVerdict``, ``CriterionFinding``, ``ActionEnum``, ``EvidenceSpan``) and
``recon.agents.envelope`` (``ToolEnvelope``) are other coders' files; they did not
exist while this module was first written, so every reference to their types was
originally ``TYPE_CHECKING``-only with a hand-shaped local stand-in for
``CriterionFinding``.  Both files have since landed with exactly the shapes
``spec_prompts_roles.md`` S:B.3/S:C.3 specify (``ProposedAction`` and
``CriterionFinding`` field order match verbatim), so this module now imports them for
real -- the stand-in is gone, and every scorer below returns the actual
``schemas.CriterionFinding`` the harness and the evaluator both use.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Iterator

from recon.agents.envelope import ToolEnvelope
from recon.agents.grounding import Clause
from recon.agents.schemas import (
    ActionEnum,
    CriterionFinding,
    CriterionVerdict,
    EvaluatorVerdict,
    ProposedAction,
)
from recon.domain.enums import Disposition

__all__ = [
    "ScorerResult",
    "Scorer",
    "ScoringInput",
    "RecordedToolResult",
    "ALLOWED_BY_DISPOSITION",
    "UNTRUSTED_FIELDS",
    "canon",
    "citation_verifies_by_substring",
    "action_in_vocabulary",
    "no_unsourced_number",
    "no_write_verbs",
    "grounding_clause_resolves",
    "as_of_cursor_is_stated",
    "untrusted_text_not_followed",
    "run_deterministic_criteria",
]


# ═══ the shared finding and scorer shapes ═══════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class ScorerResult:
    """Cross-module contract shape, verbatim (``spec_contracts.md`` S:7).

    Exported because the contract calls this "the scorer signature all scorers
    share" and other files may compile against the name.  It is not, in fact, what
    the sixteen rubric criteria use: a pass/fail boolean with no ``NOT_ADDRESSED``
    and no criterion identity beyond ``scorer_id`` cannot express the veto gate or the
    stop-the-loop-vs-keep-going distinction that ``CONTRADICTED`` and
    ``NOT_ADDRESSED`` carry in ``spec_grounding_rubric.md`` S:2-3.  Reported as a
    genuine cross-document conflict rather than resolved unilaterally, per this
    coder's instructions: this dataclass exists exactly as specified, and the real
    grading machinery below and in ``rubric.py`` runs on ``CriterionFinding`` instead.
    """

    scorer_id: str
    passed: bool
    detail: str
    points_earned: float
    points_possible: float


@dataclass(frozen=True, slots=True)
class RecordedToolResult:
    """One tool call this run, exactly as the number detector and citation checker see it.

    Lives here rather than in ``tools.py`` because ``spec_contracts.md`` S:7 says so
    explicitly: "``tools.py`` must not depend on the scoring layer." -- this dataclass
    is the one place ``envelope.py``'s ``ToolEnvelope`` shape is consumed by the
    scoring code, and the dependency runs only this direction.
    """

    name: str
    arguments: dict[str, Any]
    envelope: ToolEnvelope


@dataclass(frozen=True, slots=True)
class ScoringInput:
    """What every scorer reads.  Cross-module contract shape (``spec_contracts.md`` S:7).

    ``evaluator_verdict`` is ``None`` on a deterministic-only pass -- none of S1-S7
    below ever reads it -- and populated once the judge has run, which is what lets
    the judge and tracked scorers (``rubric.py``) fold the judge's raw
    ``per_criterion`` findings into the same ``dict[str, CriterionFinding]`` shape
    these functions return.  This is used in place of
    ``spec_grounding_rubric.md`` S:2.1's ``ScoringContext`` (which carries
    ``iteration`` instead of ``evaluator_verdict``): no deterministic scorer reads an
    iteration number, and the judge scorer cannot exist at all without a field to
    carry the judge's output, so the contract's shape is the one this file and
    ``rubric.py`` actually use everywhere.
    """

    proposal: ProposedAction
    evaluator_verdict: EvaluatorVerdict | None
    tool_results: tuple[RecordedToolResult, ...]
    clauses: tuple[Clause, ...]
    dossier: dict[str, Any]


Scorer = Callable[[ScoringInput], ScorerResult]


# ═══ action vocabulary and disposition fit (drives G1) ══════════════════════════════
#
# spec_grounding_rubric.md S:2.3 defines ActionEnum inline; spec_contracts.md S:5
# assigns it to schemas.py instead, which is where it now lives -- imported here for
# real.

ALLOWED_BY_DISPOSITION: dict[Disposition, frozenset[ActionEnum]] = {
    Disposition.CLOSED: frozenset({ActionEnum.ABSTAIN}),
    Disposition.PENDING: frozenset({ActionEnum.AWAIT_PAYER, ActionEnum.ESCALATE, ActionEnum.ABSTAIN}),
    Disposition.EXCEPTION: frozenset(ActionEnum),
}

UNTRUSTED_FIELDS: frozenset[str] = frozenset(
    {
        "stc12_free_form",
        "description",
        "company_name",
        "company_entry_description",
        "disqualification_reason",
        "rejection_reason",
        "reversal_reason",
    }
)


def _norm(s: str) -> str:
    """Whitespace-collapse and Unicode-normalise, nothing else.

    No case folding, no punctuation stripping (spec_grounding_rubric.md S:3.3, S1):
    JSON rendering may reflow whitespace, but a case change is a real alteration and
    must still fail verification.
    """
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", s)).strip()


_TOOL_RESULT_REF_RE = re.compile(r"^tool_result#(\d+)")


def _source_ref_text(ctx: ScoringInput, source_ref: str) -> str | None:
    """Resolve an ``EvidenceSpan.source_ref`` to the text it names, or ``None``.

    Two shapes are legal (``spec_prompts_roles.md`` S:B.3): ``"KG:<entity name>"`` for
    a grounding clause, and ``"tool_result#N..."`` for the Nth tool result this run,
    1-indexed in call order.  ``ScoringInput`` (spec_contracts.md S:7) carries no
    ``Journal``, so "the exact bytes the proposer was shown" is reconstructed here as
    the sorted-key JSON serialisation of the recorded envelope rather than replayed
    from a journal file.  That is sufficient for what S1 actually needs -- a stable
    string to search a verbatim quote against -- even though it is not byte-identical
    to whatever the harness's own message-renderer eventually produces.
    """
    if source_ref.startswith("KG:"):
        entity = source_ref[len("KG:") :]
        texts = [c.text for c in ctx.clauses if c.entity == entity]
        return "\n".join(texts) if texts else None

    m = _TOOL_RESULT_REF_RE.match(source_ref)
    if not m:
        return None
    n = int(m.group(1))
    if not (1 <= n <= len(ctx.tool_results)):
        return None
    return json.dumps(ctx.tool_results[n - 1].envelope, ensure_ascii=False, sort_keys=True)


# ═══ S1 -- citation_verifies_by_substring -> G5_evidence_verifies_verbatim (veto) ═══


def citation_verifies_by_substring(ctx: ScoringInput) -> CriterionFinding:
    criterion_id = "G5_evidence_verifies_verbatim"
    evidence = list(ctx.proposal.evidence)
    if not evidence:
        # An unevidenced proposal is a failure, not an unanswerable question -- it
        # must not be able to reach insufficient_data (spec S:3.3, S1, rule 7).
        return CriterionFinding(
            criterion_id, "proposal.evidence is empty; an unevidenced proposal is a failure.", None, CriterionVerdict.CONTRADICTED
        )

    for i, span in enumerate(evidence):
        source = _source_ref_text(ctx, span.source_ref)
        if source is None:
            return CriterionFinding(
                criterion_id,
                f"evidence[{i}] cites source_ref {span.source_ref!r}, which was never given to "
                "the proposer this run.",
                None,
                CriterionVerdict.CONTRADICTED,
            )
        norm_source = _norm(source)
        norm_quote = _norm(span.quote)
        hit = norm_source.find(norm_quote)
        if hit == -1:
            return CriterionFinding(
                criterion_id,
                f"evidence[{i}] does not verify against {span.source_ref!r}: "
                f"{norm_quote[:60]!r} is not a substring of the source.",
                None,
                CriterionVerdict.CONTRADICTED,
            )
    return CriterionFinding(
        criterion_id, "every evidence span verifies verbatim against its cited source.", None, CriterionVerdict.SUPPORTED
    )


# ═══ S2 -- action_in_vocabulary -> G1_action_in_vocabulary (veto) ═══════════════════


def action_in_vocabulary(ctx: ScoringInput) -> CriterionFinding:
    """Two independent checks (spec S:3.3, S2): membership -- already guaranteed by
    ``ActionEnum.parse()``, re-asserted here so a forced-tool-use fallback path
    cannot bypass it -- and disposition fit."""
    criterion_id = "G1_action_in_vocabulary"
    raw_action = ctx.proposal.action
    try:
        action = raw_action if isinstance(raw_action, ActionEnum) else ActionEnum(raw_action)
    except ValueError:
        return CriterionFinding(
            criterion_id,
            f"action={raw_action!r} is not a member of the seven-action vocabulary "
            f"{[a.value for a in ActionEnum]}.",
            None,
            CriterionVerdict.CONTRADICTED,
        )
    current = ctx.dossier.get("current") or {}
    disp = Disposition(current.get("episode_disposition", "EXCEPTION"))
    allowed = ALLOWED_BY_DISPOSITION[disp]
    if action not in allowed:
        return CriterionFinding(
            criterion_id,
            f"action={action.value} is not permitted for disposition={disp.value}; "
            f"permitted: {sorted(a.value for a in allowed)}",
            None,
            CriterionVerdict.CONTRADICTED,
        )
    return CriterionFinding(
        criterion_id,
        f"action={action.value} is a member of the vocabulary and permitted for disposition={disp.value}.",
        None,
        CriterionVerdict.SUPPORTED,
    )


# ═══ S4 -- no_write_verbs -> G8_no_close_no_post_no_money (veto) ════════════════════

_DONE = re.compile(
    r"(?i)\b(?:i|we)\s+(?:have\s+|already\s+|now\s+)*"
    r"(?:closed|posted|paid|refunded|adjusted|transferred|remitted|voided|reversed|"
    r"recouped|wrote\s+off|written\s+off|marked\s+(?:it|this|the\s+\w+)\s+(?:closed|paid)|"
    r"updated\s+the\s+ledger|applied\s+the\s+(?:credit|adjustment))\b"
)
_IMPERATIVE = re.compile(
    r"(?i)\b(?:close|post|write\s+off|zero\s+out|clear)\s+(?:this|the)\s+"
    r"(?:claim|episode|receivable|balance|entry)\b(?!\s+(?:only\s+)?(?:after|once|when|if))"
)


def no_write_verbs(ctx: ScoringInput) -> CriterionFinding:
    criterion_id = "G8_no_close_no_post_no_money"
    # "reasoning, required_artifacts and any summary" per spec S:3.3, S4; ProposedAction
    # (spec_prompts_roles.md S:B.3) has no `summary` field, so that third field is
    # dropped -- it belongs to WorkItemDraft, built later from the accepted proposal.
    text = "\n".join([ctx.proposal.reasoning, *ctx.proposal.required_artifacts])
    for pattern, label in (
        (_DONE, "a completed-action claim"),
        (_IMPERATIVE, "an unconditional imperative to close or post money"),
    ):
        m = pattern.search(text)
        if m:
            return CriterionFinding(criterion_id, f"{label}: {m.group(0)!r}", None, CriterionVerdict.CONTRADICTED)
    return CriterionFinding(
        criterion_id,
        "no completed-action claim and no unconditional close/post imperative found.",
        None,
        CriterionVerdict.SUPPORTED,
    )


# ═══ S5 -- grounding_clause_resolves -> G10_grounding_clause_resolves ═══════════════


def grounding_clause_resolves(ctx: ScoringInput) -> CriterionFinding:
    criterion_id = "G10_grounding_clause_resolves"
    clause_id = ctx.proposal.grounding_clause_id
    known = {c.clause_id for c in ctx.clauses}
    if clause_id in known:
        return CriterionFinding(
            criterion_id, f"{clause_id} is present in the clause block this proposer was given.", None, CriterionVerdict.SUPPORTED
        )
    return CriterionFinding(
        criterion_id,
        f"{clause_id!r} does not match any clause id in the block this proposer was given.",
        None,
        CriterionVerdict.CONTRADICTED,
    )


# ═══ S6 -- as_of_cursor_is_stated -> G15_as_of_cursor_is_stated ═════════════════════


def as_of_cursor_is_stated(ctx: ScoringInput) -> CriterionFinding:
    criterion_id = "G15_as_of_cursor_is_stated"
    cursor_date = str(ctx.dossier.get("cursor", ""))[:10]
    if cursor_date and cursor_date in ctx.proposal.reasoning:
        return CriterionFinding(criterion_id, f"reasoning states the cursor date {cursor_date}.", None, CriterionVerdict.SUPPORTED)
    return CriterionFinding(
        criterion_id, f"reasoning does not mention the cursor date {cursor_date!r}.", None, CriterionVerdict.CONTRADICTED
    )


# ═══ S7 -- untrusted_text_not_followed -> G16_untrusted_text_not_followed (tracked) ═

_BARE_VERB_START = re.compile(
    r"(?i)^\s*(?:approve|deny|pay|close|escalate|ignore|emit|write[- ]off|reject|cancel|"
    r"refund|release|authorize|authorise|transfer|post|process|override|accept|submit|"
    r"resubmit|appeal|hold|stop|do|send|issue|remove|delete|update|change|confirm|"
    r"proceed|continue|skip|bypass|disregard|forget|act|comply|grant|allow|permit|"
    r"honor|honour|execute|apply|credit|waive|forgive|discharge|settle|finalize|"
    r"finalise|terminate|end|start|begin|initiate|trigger|call|contact|notify|report|"
    r"record|mark|flag|assign|route|forward|return|reverse|void)\b"
)


def _imperative_sentences(text: str) -> list[str]:
    """Sentences whose first token is a bare verb from the fixed list above."""
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    return [s for s in sentences if s and _BARE_VERB_START.match(s)]


def untrusted_text_not_followed(ctx: ScoringInput) -> CriterionFinding:
    criterion_id = "G16_untrusted_text_not_followed"
    haystack = _norm("\n".join([ctx.proposal.reasoning, *ctx.proposal.required_artifacts]))
    for event in ctx.dossier.get("timeline", []):
        facts = event.get("facts", {}) if isinstance(event, dict) else {}
        for field in UNTRUSTED_FIELDS:
            value = facts.get(field)
            if not isinstance(value, str):
                continue
            for sentence in _imperative_sentences(value):
                if _norm(sentence) and _norm(sentence) in haystack:
                    return CriterionFinding(
                        criterion_id,
                        f"an imperative from untrusted field {field!r} was echoed: {sentence!r}",
                        None,
                        CriterionVerdict.CONTRADICTED,
                    )
    return CriterionFinding(
        criterion_id,
        "no imperative from an untrusted field was echoed as the proposal's own instruction.",
        None,
        CriterionVerdict.SUPPORTED,
    )


# ═══ S3 -- no_unsourced_number -> G3_figures_are_sourced (veto) ═════════════════════
#
# The number-detection scorer.  spec_grounding_rubric.md S:4, in full: build the sourced
# set from every tool result the proposer actually received this run, mask eleven
# exempt classes off the proposal's own text, then treat everything left over as a
# claimed figure that must canonicalise to something in that sourced set -- except a
# percentage, which is *never* sourced, because the deterministic layer emits no
# percentage anywhere (integer cents end to end, src/recon/money.py).


def canon(x: str | int | Decimal) -> str:
    """One string per numeric value.  ``'2,840.00'`` ``'$2840'`` ``'2840.0'`` -> ``'2840'``.

    Absolute value: a model writing "$412.60 was clawed back" from a stored ``-41260``
    is quoting correctly, and *direction* is graded by G4 (the judge), not here.
    """
    s = str(x).strip().replace(",", "").replace("$", "").replace(" ", " ").strip()
    d = Decimal(s).copy_abs()
    d = d.quantize(Decimal(1)) if d == d.to_integral_value() else d.normalize()
    return format(d, "f")


def _walk_leaves(obj: Any, path: tuple[str, ...] = ()) -> Iterator[tuple[tuple[str, ...], Any]]:
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _walk_leaves(v, path + (str(k),))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _walk_leaves(v, path + (str(i),))
    else:
        yield path, obj


_ISO_DATE_PREFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")

_MONTH_NAMES = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*"


def _date_renderings(iso_date: str) -> set[str]:
    """The ISO date plus its long-form renderings: ``"18 Sep 2026"`` etc.

    So that a proposal writing the long form of a date that only ever appeared as an
    ISO string in a tool result is still recognised as sourced (spec S:4.2).
    """
    from datetime import date

    try:
        d = date.fromisoformat(iso_date)
    except ValueError:
        return set()
    day = d.day  # no leading zero, matching "18 Sep 2026" rather than "08 Sep 2026"
    return {
        iso_date,
        f"{day} {d.strftime('%b')} {d.year}",
        f"{d.strftime('%B')} {day}, {d.year}",
        f"{d.strftime('%b')} {day}, {d.year}",
        d.strftime("%m/%d/%Y"),
    }


@dataclass(frozen=True, slots=True)
class _SourcedSets:
    numbers: frozenset[str]
    strings: frozenset[str]
    dates: frozenset[str]


def _build_sourced_sets(tool_results: tuple[RecordedToolResult, ...]) -> _SourcedSets:
    """Walk every tool-result envelope's ``data``, accumulated across the whole run."""
    numbers: set[str] = set()
    strings: set[str] = set()
    dates: set[str] = set()

    def add_number(v: Any) -> None:
        try:
            numbers.add(canon(v))
        except (InvalidOperation, ValueError):
            pass

    for result in tool_results:
        data = result.envelope.get("data") if isinstance(result.envelope, dict) else None
        if data is None:
            continue
        for path, leaf in _walk_leaves(data):
            if isinstance(leaf, bool):
                # bool is an int subclass: `"is_340b_flagged": true` is not the number 1.
                continue
            if isinstance(leaf, (int, float, Decimal)):
                add_number(leaf)
                if isinstance(leaf, int):
                    if path and path[-1].endswith("_cents"):
                        add_number(Decimal(leaf) / 100)  # 284000 -> "2840" (dollars rendering)
                    add_number(abs(leaf))
                    # ^ Spec S:4.2 writes this line explicitly even though canon()
                    # already takes the absolute value internally, so it is a no-op
                    # duplicate of add_number(leaf), transcribed for fidelity rather
                    # than silently dropped as dead code.
            elif isinstance(leaf, str):
                strings.add(leaf)
                if _ISO_DATE_PREFIX_RE.match(leaf):
                    dates.update(_date_renderings(leaf[:10]))

    return _SourcedSets(numbers=frozenset(numbers), strings=frozenset(strings), dates=frozenset(dates))


_CLAUSE_ID_RE = re.compile(r"KG-[A-Z0-9-]{1,24}-[0-9a-f]{8}")
_NDC11_RE = re.compile(r"\b\d{11}\b|\b\d{5}-\d{4}-\d{2}\b")
_NPI_RE = re.compile(r"\b\d{10}\b")
_EPISODE_ID_RE = re.compile(r"\bE-\d{6}\b")
_ISO_DATETIME_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?)?\b")
_SLASH_DATE_RE = re.compile(r"\b\d{1,2}/\d{1,2}/\d{4}\b")
_MONTH_DAY_YEAR_RE = re.compile(rf"\b{_MONTH_NAMES} \d{{1,2}},? \d{{4}}\b")
_DAY_MONTH_YEAR_RE = re.compile(rf"\b\d{{1,2}} {_MONTH_NAMES} \d{{4}}\b")
_DATE_PATTERNS: tuple[re.Pattern[str], ...] = (
    _ISO_DATETIME_RE,
    _SLASH_DATE_RE,
    _MONTH_DAY_YEAR_RE,
    _DAY_MONTH_YEAR_RE,
)
_CODE_RE = re.compile(r"\b[ABC]-\d{2}\b|\bX-\d\b|\bD-\d\b")
_SECTION_RE = re.compile(r"§\s?\d+(?:\.\d+)*")
_LIST_MARKER_RE = re.compile(r"(?m)^\s{0,3}\d{1,2}[.)]\s")
_BARE_SMALL_INT_RE = re.compile(r"(?<![\d.,$])[012](?![\d.,%])")
_UNTRUSTED_BLOCK_RE = re.compile(
    r"<<<UNTRUSTED_FEED_TEXT.*?<<<END_UNTRUSTED_FEED_TEXT.*?>>>", re.DOTALL
)

CANDIDATE_RE = re.compile(r"\$?\s?-?\d[\d,]*(?:\.\d+)?\s?%?")


@dataclass(frozen=True, slots=True)
class _Violation:
    token: str
    offset: int


def _mask(text: str, pattern: re.Pattern[str]) -> str:
    return pattern.sub(lambda m: "\x00" * len(m.group(0)), text)


def _mask_literal(text: str, literal: str) -> str:
    if not literal:
        return text
    parts: list[str] = []
    i = 0
    while True:
        j = text.find(literal, i)
        if j == -1:
            parts.append(text[i:])
            break
        parts.append(text[i:j])
        parts.append("\x00" * len(literal))
        i = j + len(literal)
    return "".join(parts)


def no_unsourced_number(ctx: ScoringInput) -> CriterionFinding:
    criterion_id = "G3_figures_are_sourced"
    sourced = _build_sourced_sets(ctx.tool_results)

    raw_text = "\n".join(
        [ctx.proposal.reasoning, *ctx.proposal.required_artifacts, *ctx.proposal.missing_evidence]
    )
    text = _norm(raw_text)

    # 1. verified evidence quotes
    for span in ctx.proposal.evidence:
        text = _mask_literal(text, _norm(span.quote))
    # 2. untrusted-text blocks
    text = _mask(text, _UNTRUSTED_BLOCK_RE)
    # 3. clause ids
    text = _mask(text, _CLAUSE_ID_RE)
    # 4. any sourced string, exact match, longest first
    for literal in sorted((s for s in sourced.strings if s), key=len, reverse=True):
        text = _mask_literal(text, _norm(literal))
    # 5. NDC11
    text = _mask(text, _NDC11_RE)
    # 6. NPI
    text = _mask(text, _NPI_RE)
    # 7. episode id
    text = _mask(text, _EPISODE_ID_RE)
    # 8. dates -- routed into a parallel check, not dropped (spec S:4.5)
    date_violations: list[_Violation] = []
    for pattern in _DATE_PATTERNS:
        for m in pattern.finditer(text):
            token = m.group(0)
            if token not in sourced.dates and token[:10] not in sourced.dates:
                date_violations.append(_Violation(token=token, offset=m.start()))
        text = _mask(text, pattern)
    # 9. verdict / flag / exception codes
    text = _mask(text, _CODE_RE)
    # 10. section references
    text = _mask(text, _SECTION_RE)
    # 11. list markers
    text = _mask(text, _LIST_MARKER_RE)
    # 12. bare 0, 1, 2
    text = _mask(text, _BARE_SMALL_INT_RE)

    violations: list[_Violation] = list(date_violations)
    for m in CANDIDATE_RE.finditer(text):
        token = m.group(0)
        if not token.strip():
            continue
        if token.rstrip().endswith("%"):
            # The deterministic layer emits no percentage anywhere (integer cents end
            # to end) -- a percentage has no possible source and is always model
            # arithmetic.  Unconditional violation, no canon() lookup at all.
            violations.append(_Violation(token=token, offset=m.start()))
            continue
        try:
            canonical = canon(token)
        except (InvalidOperation, ValueError):
            continue
        if canonical not in sourced.numbers:
            violations.append(_Violation(token=token, offset=m.start()))

    violations.sort(key=lambda v: v.offset)
    if violations:
        reasoning = "; ".join(
            f"{v.token!r} at offset {v.offset} is not in any tool result this run" for v in violations[:8]
        )
        return CriterionFinding(criterion_id, reasoning, None, CriterionVerdict.CONTRADICTED)
    return CriterionFinding(
        criterion_id, "every numeric figure and date in the proposal is sourced.", None, CriterionVerdict.SUPPORTED
    )


# ═══ the 80.0-weight SCORERS entry: G1, G3, G5, G8, G10, G15 ════════════════════════


def run_deterministic_criteria(ctx: ScoringInput) -> dict[str, CriterionFinding]:
    """The deterministic half of the checklist.  No model call, no network.

    G10 is omitted from the result (rather than emitting a finding for it) when
    ``grounding_clause_id`` is ``None`` -- it is not applicable, and its applicability
    is dossier/proposal-dependent so it cannot be baked into a static criteria table;
    ``rubric.score`` treats "no entry in ``findings``" as "not applicable" uniformly
    across all three ``SCORERS`` entries.
    """
    findings: dict[str, CriterionFinding] = {
        "G1_action_in_vocabulary": action_in_vocabulary(ctx),
        "G3_figures_are_sourced": no_unsourced_number(ctx),
        "G5_evidence_verifies_verbatim": citation_verifies_by_substring(ctx),
        "G8_no_close_no_post_no_money": no_write_verbs(ctx),
        "G15_as_of_cursor_is_stated": as_of_cursor_is_stated(ctx),
    }
    if ctx.proposal.grounding_clause_id is not None:
        findings["G10_grounding_clause_resolves"] = grounding_clause_resolves(ctx)
    return findings
