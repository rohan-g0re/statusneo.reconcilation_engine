"""Turning the project knowledge graph into clauses an agent can be graded against.

``CLAUDE.md`` ("THE GRAPH IS A RUNTIME DEPENDENCY") makes ``docs/knowledge_graph.jsonl``
an input the agent layer reads at process start, not a document a person reads.  This
module is where that dependency is paid: it loads the graph, validates it against every
routing table below, and derives one immutable :class:`Clause` per observation.

Two decisions this module encodes and does not re-litigate per call:

**Fail loud at import, not at call time.**  A graph rebuild that drops an entity a
routing table still references is exactly the "a docstring is not a test" trap the graph
itself records (``docs/knowledge_graph.jsonl``, entity ``A docstring is not a test``): a
prose claim that nothing tests will silently stop being true.  Every guard in
``_validate_graph`` below runs once, at module import, and raises
:class:`GroundingContractError` with the offending name and the routing key that
references it, so the failure is a stack trace at process start rather than a proposal
that cites a clause id nobody can resolve.

**Selection is a lookup, never a similarity search.**  Every trigger key in
``TRACK_ROUTES`` / ``REASON_CODE_ROUTES`` / ``FLAG_ROUTES`` / ``STRUCTURAL_ROUTES`` is a
member of a closed vocabulary the deterministic engine already wrote onto the verdict
row (a ``ReasonCode``, a ``CrossTrackFlag``, a ``ReimbursementTrack``, or a dossier
predicate).  There is nothing to embed and nothing to tune: ``select_clauses`` is pure
Python control flow over data the caller already has, so the same dossier always
produces the byte-identical clause list (T10, below).
"""

from __future__ import annotations

from collections.abc import Sequence

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from recon.domain.enums import CrossTrackFlag, Disposition, ReasonCode, ReimbursementTrack

__all__ = [
    "Clause",
    "GroundingContractError",
    "UNIVERSAL",
    "TRACK_ROUTES",
    "REBATE_ENTITIES",
    "REASON_CODE_ROUTES",
    "FLAG_ROUTES",
    "STRUCTURAL_ROUTES",
    "REQUIRED_ENTITY_NAMES",
    "MAX_ENTITIES",
    "MAX_CLAUSES",
    "GRAPH_PATH",
    "ALL_CLAUSES",
    "select_clauses",
    "render_clause_index",
    "load_graph",
]


class GroundingContractError(RuntimeError):
    """The knowledge graph no longer satisfies what the agent layer grounds against."""


@dataclass(frozen=True, slots=True)
class Clause:
    """One observation, addressable by a content-derived id.

    ``text`` is never paraphrased, truncated or reflowed — it is the observation string,
    byte-for-byte, exactly as ``docs/build_knowledge_graph.py`` wrote it.  A model citing
    a clause is citing *this* sentence, not a summary of it (T8 asserts the byte
    equality).
    """

    clause_id: str  # "KG-DETERMINISTIC-BOUNDARY-3f2a91c7"
    entity: str  # "Deterministic boundary" -- verbatim graph name
    entity_type: str  # "Decision" -- verbatim entityType
    text: str  # the observation string, byte-for-byte from the file
    index: int  # position within the entity's observations, for ordering only


# ═══ clause_id minting -- content-addressed, stable across graph rebuilds ═══════════
#
# spec_grounding_rubric.md S:1.3 pins hashlib.sha256 for this, not blake2b.  That is a
# deliberate departure from the seeding convention in src/recon/rng.py ("Seeding must
# use blake2b, never builtin hash()", START_HERE.md S:8): blake2b there stands in for a
# PRNG seed, which needs no cryptographic property at all, just reproducibility across
# machines.  A clause id has a different job -- it is a content address a journal row
# can cite forever -- and the spec's worked example (KG-DETERMINISTIC-BOUNDARY-3f2a91c7)
# is only reproducible if sha256 is what actually ran.  Both choices satisfy the one
# real constraint they share: never Python's builtin hash(), which PYTHONHASHSEED salts
# per process and would mint a different id every run.

_SLUG_RE = re.compile(r"[^A-Z0-9]+")


def _slug(name: str) -> str:
    s = _SLUG_RE.sub("-", name.upper()).strip("-")
    return s[:24].rstrip("-")


def _clause_id(entity_name: str, observation: str) -> str:
    payload = f"{entity_name}\x1f{observation}".encode("utf-8")
    return f"KG-{_slug(entity_name)}-{hashlib.sha256(payload).hexdigest()[:8]}"


# ═══ Tier 0 -- UNIVERSAL, always selected ═══════════════════════════════════════════

UNIVERSAL: tuple[str, ...] = (
    "Deterministic boundary",
    "Disposition",
    "Reason code",
    "No SLA thresholds",
    "A human gate can be worthless",
)

# ═══ Tier 1 -- keyed on ReimbursementTrack ══════════════════════════════════════════

TRACK_ROUTES: dict[ReimbursementTrack, tuple[str, ...]] = {
    ReimbursementTrack.PHARMACY: ("Reimbursement track", "PBM", "PBM feed"),
    ReimbursementTrack.MEDICAL: ("Reimbursement track", "Medical payer", "Medical feed"),
}

# ═══ Tier 2 -- selected when current.rebate_verdict not in (None, "C-00") ═══════════

REBATE_ENTITIES: tuple[str, ...] = ("340B", "340B rebate track", "340B TPA", "Manufacturer", "340B feed")

# ═══ Tier 3 -- exhaustive over every ReasonCode member ══════════════════════════════

REASON_CODE_ROUTES: dict[ReasonCode, tuple[str, ...]] = {
    # --- reimbursement --------------------------------------------------
    ReasonCode.REJECTED_AT_POS: ("PBM feed",),
    ReasonCode.AWAITING_REMITTANCE: ("No SLA thresholds",),
    ReasonCode.AWAITING_CASH: ("Cash verification", "Bank feed"),
    ReasonCode.UNDERPAID: ("Verdict",),
    ReasonCode.OVERPAID: ("Verdict",),
    ReasonCode.DUPLICATE_PAYMENT: ("received_at",),
    ReasonCode.CASH_MISMATCH: ("Cash verification", "Two-hop bank resolution", "Amount-and-date fallback"),
    ReasonCode.NO_CASH: ("Cash verification", "Bank feed", "TRN02"),
    ReasonCode.SETTLEMENT_MISSING: ("Verdict",),
    ReasonCode.REVERSAL_CASH_NOT_RETURNED: ("Reversal versus recoupment", "Cash verification"),
    ReasonCode.RECOUPMENT_UNTRACEABLE: (
        "Reversal versus recoupment",
        "PLB netting",
        "Two-hop bank resolution",
    ),
    ReasonCode.ADJUSTMENT_RESIDUAL: ("PLB netting",),
    ReasonCode.CLEARINGHOUSE_REJECTED: ("Medical feed",),
    ReasonCode.DENIED: ("Medical feed", "Medical payer"),
    ReasonCode.APPEAL_PENDING: ("Medical payer", "No SLA thresholds"),
    ReasonCode.APPEAL_LOST: ("Medical payer",),
    ReasonCode.APPEAL_WON_NO_CASH: ("Medical payer", "Cash verification", "Bank feed"),
    ReasonCode.DUPLICATE_REMITTANCE: ("received_at", "PLB netting"),
    # --- rebate ----------------------------------------------------------
    ReasonCode.REBATE_AWAITING_QUALIFICATION: ("340B TPA", "No SLA thresholds"),
    ReasonCode.REBATE_NOT_QUALIFIED: ("340B TPA", "340B"),
    ReasonCode.REBATE_AWAITING_SUBMISSION: ("340B rebate track",),
    ReasonCode.REBATE_AWAITING_MANUFACTURER: ("Manufacturer", "No SLA thresholds"),
    ReasonCode.REBATE_REJECTED: ("Manufacturer", "340B TPA"),
    ReasonCode.REBATE_AWAITING_PAYMENT: ("Manufacturer", "Rebate model"),
    ReasonCode.REBATE_UNDERPAID: ("Rebate model", "340B"),
    ReasonCode.REBATE_NO_CASH: ("Rebate model", "Bank feed", "Cash verification"),
    ReasonCode.REBATE_CLAWED_BACK: ("Manufacturer", "340B", "Reopened"),
    ReasonCode.DUPLICATE_REBATE: ("received_at", "340B feed"),
    # --- cross-track -------------------------------------------------------
    ReasonCode.REBATE_ON_UNDISPENSED_CLAIM: ("Reversal versus recoupment", "340B"),
    ReasonCode.DENIED_WITH_REBATE_PAID: ("X-1 compliance case", "340B"),
    ReasonCode.QUALIFICATION_UNDERMINED_BY_RECOUPMENT: ("Reversal versus recoupment", "340B TPA"),
    ReasonCode.CORRELATED_CASH_GAP: ("Cash verification", "Bank feed", "Two-hop bank resolution"),
    ReasonCode.TOTAL_LOSS: ("Operator", "340B"),
    ReasonCode.REBATE_RE_REQUEST_AVAILABLE: ("Manufacturer", "340B rebate track"),
    # --- feed-level ---------------------------------------------------------
    ReasonCode.DUPLICATE_DELIVERY: ("received_at", "Crosswalk"),
    ReasonCode.ORPHAN_DEPOSIT: ("Bank feed", "TRN02", "Two-hop bank resolution"),
    ReasonCode.ORPHAN_REBATE: ("340B feed", "Crosswalk"),
    ReasonCode.CROSSWALK_MISS: ("D-6 crosswalk failure", "Crosswalk", "No identifier normalisation"),
    ReasonCode.MALFORMED_RECORD: (),  # D-5 is not generated (state space S:6)
    ReasonCode.ALLOCATION_RESIDUAL: ("Amount-and-date fallback", "Bank feed"),
    # --- universal ----------------------------------------------------------
    ReasonCode.INSUFFICIENT_DATA: ("Reason code", "Deterministic boundary", "Reasoning models abstain worse"),
}

# ═══ Tier 4 -- exhaustive over every CrossTrackFlag member ══════════════════════════

FLAG_ROUTES: dict[CrossTrackFlag, tuple[str, ...]] = {
    CrossTrackFlag.X_1: ("X-1 compliance case", "340B", "Decision tree"),
    CrossTrackFlag.X_2: ("Reversal versus recoupment", "340B rebate track"),
    CrossTrackFlag.X_3: ("Reversal versus recoupment", "340B TPA"),
    CrossTrackFlag.X_4: ("Decision tree",),  # explicitly a NON-flag; grounds "do not escalate"
    CrossTrackFlag.X_5: ("Cash verification", "Two-hop bank resolution", "Reason code"),
    CrossTrackFlag.X_6: ("Operator", "340B"),
    CrossTrackFlag.X_7: ("Manufacturer", "Reopened"),
}

# ═══ Tier 5 -- predicates over the dossier, declaration order ═══════════════════════

StructuralPredicate = Callable[[dict[str, Any]], bool]

STRUCTURAL_ROUTES: tuple[tuple[StructuralPredicate, tuple[str, ...]], ...] = (
    (lambda d: bool(d["unresolved"]), ("D-6 crosswalk failure", "Parked records", "Crosswalk")),
    (
        lambda d: (d["current"] or {}).get("reopened_from") is not None,
        ("Reopened", "Recompute never mutate"),
    ),
    (lambda d: any(e["late"] for e in d["timeline"]), ("received_at", "Cursor replay")),
    (
        lambda d: any(e["tag"] == "MEDICAL_ACKNOWLEDGMENT" for e in d["timeline"]),
        ("Medical feed",),
    ),  # stc12_free_form is the untrusted-text surface
    (
        lambda d: d["counts"]["verdict_changes"] > 1,
        ("Recompute never mutate", "Cursor replay"),
    ),
)

MAX_ENTITIES = 16
MAX_CLAUSES = 72


def _all_routed_names() -> set[str]:
    """The union every REQUIRED_ENTITY_NAMES check is derived from.

    Computed from the routing tables themselves rather than transcribed from the
    spec's prose list, so a future edit to a route is automatically reflected here —
    the prose count in spec_grounding_rubric.md S:1.4 ("31 distinct names") undercounts
    against the 36-name list the same section prints; this function is the source of
    truth and REQUIRED_ENTITY_NAMES below is whatever it actually computes.
    """
    names: set[str] = set(UNIVERSAL)
    for tup in TRACK_ROUTES.values():
        names.update(tup)
    names.update(REBATE_ENTITIES)
    for tup in REASON_CODE_ROUTES.values():
        names.update(tup)
    for tup in FLAG_ROUTES.values():
        names.update(tup)
    for _predicate, tup in STRUCTURAL_ROUTES:
        names.update(tup)
    return names


REQUIRED_ENTITY_NAMES: frozenset[str] = frozenset(_all_routed_names())


def _referencing_keys(name: str) -> list[str]:
    """Every routing key that names ``name``, for a fail-loud error message."""
    refs: list[str] = []
    if name in UNIVERSAL:
        refs.append("UNIVERSAL")
    for track, names in TRACK_ROUTES.items():
        if name in names:
            refs.append(f"TRACK_ROUTES[{track!s}]")
    if name in REBATE_ENTITIES:
        refs.append("REBATE_ENTITIES")
    for code, names in REASON_CODE_ROUTES.items():
        if name in names:
            refs.append(f"ReasonCode.{code.name}")
    for flag, names in FLAG_ROUTES.items():
        if name in names:
            refs.append(f"CrossTrackFlag.{flag.name}")
    for i, (_predicate, names) in enumerate(STRUCTURAL_ROUTES):
        if name in names:
            refs.append(f"STRUCTURAL_ROUTES[{i}]")
    return refs


# ═══ loading and validating the graph, at import ════════════════════════════════════


def _default_graph_path() -> Path:
    return Path(__file__).resolve().parents[3] / "docs" / "knowledge_graph.jsonl"


def _resolve_graph_path() -> Path:
    override = os.environ.get("RECON_KNOWLEDGE_GRAPH")
    if override is None:
        return _default_graph_path()
    path = Path(override)
    if not path.is_absolute():
        # A relative RECON_KNOWLEDGE_GRAPH resolves against the process's cwd, which is
        # exactly the failure recorded on "Knowledge graph is generated, never written
        # to", observation 6 (the .mcp.json MEMORY_FILE_PATH bug). Refuse it outright
        # rather than silently serving whatever happens to sit at that relative path.
        raise GroundingContractError(
            f"RECON_KNOWLEDGE_GRAPH={override!r} is relative; it must be an absolute "
            "path. A relative path resolves against the process's working directory "
            "and can silently serve an empty or unrelated graph."
        )
    return path


def load_graph(path: Path) -> tuple[dict[str, dict[str, Any]], int]:
    """Parse the graph file into ``{entity_name: row}``, plus the relation count.

    Relations are parsed and validated -- a malformed relation row fails the loader --
    but never turned into a :class:`Clause`.  A relation triple such as
    ``("PBM", "pays", "Operator")`` is not an assertion an evaluator can grade a
    proposal against; only an observation is.
    """
    if not path.exists():
        raise GroundingContractError(
            f"knowledge graph not found at {path}; set RECON_KNOWLEDGE_GRAPH"
        )

    entities: dict[str, dict[str, Any]] = {}
    duplicates: set[str] = set()
    relation_count = 0

    with path.open(encoding="utf-8") as f:
        for lineno, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise GroundingContractError(
                    f"{path}:{lineno} is not valid JSON: {exc}"
                ) from exc

            row_type = row.get("type")
            if row_type == "entity":
                # reviewer finding 12 (spec_fixes_round1.md C5): a row missing `name`,
                # `entityType` or `observations` used to raise a bare `KeyError` deep
                # inside `_build_clauses`, far from this line number. Worse: an
                # `observations` value that is a *string* rather than a list raised
                # nothing at all -- `enumerate("abc")` iterates per character, so a
                # string-shaped `observations` silently minted one garbage clause per
                # letter. Both are caught here, at load time, with the offending line.
                for key in ("name", "entityType", "observations"):
                    if key not in row:
                        raise GroundingContractError(
                            f"{path}:{lineno} is an entity row missing {key!r}: {row!r}"
                        )
                name = row["name"]
                if not isinstance(name, str) or not name:
                    raise GroundingContractError(
                        f"{path}:{lineno} has a non-string or empty 'name': {row!r}"
                    )
                if not isinstance(row["entityType"], str) or not row["entityType"]:
                    raise GroundingContractError(
                        f"{path}:{lineno} entity {name!r} has a non-string or empty "
                        f"'entityType': {row!r}"
                    )
                observations = row["observations"]
                if not isinstance(observations, list):
                    raise GroundingContractError(
                        f"{path}:{lineno} entity {name!r} has 'observations' of type "
                        f"{type(observations).__name__}, not a list -- a string would "
                        "iterate per character (enumerate('abc') yields 3 items) and mint "
                        f"one garbage clause per letter with no error at all: {row!r}"
                    )
                if not all(isinstance(obs, str) for obs in observations):
                    raise GroundingContractError(
                        f"{path}:{lineno} entity {name!r} has a non-string element in "
                        f"'observations': {row!r}"
                    )
                if name in entities:
                    duplicates.add(name)
                entities[name] = row
            elif row_type == "relation":
                for key in ("from", "to", "relationType"):
                    if key not in row:
                        raise GroundingContractError(
                            f"{path}:{lineno} is a relation row missing {key!r}: {row!r}"
                        )
                relation_count += 1
            else:
                raise GroundingContractError(
                    f"{path}:{lineno} has an unrecognised type {row_type!r}: {row!r}"
                )

    if not entities:
        # "An empty graph and a missing graph are indistinguishable" is only true if
        # you let it be (Knowledge graph is generated, never written to, obs 6).
        raise GroundingContractError(f"{path} parsed to zero entities; treat it as missing")

    if duplicates:
        raise GroundingContractError(
            f"{path} declares the same entity name more than once: "
            f"{', '.join(sorted(duplicates))}. E() was probably called twice for one "
            "entity; correct the later wave with OBS()/UNOBS() in docs/build_knowledge_graph.py."
        )

    return entities, relation_count


def _validate_required_entities(entities: dict[str, dict[str, Any]]) -> None:
    missing = sorted(name for name in REQUIRED_ENTITY_NAMES if name not in entities)
    if missing:
        parts = [
            f"{name!r} (referenced by {', '.join(_referencing_keys(name))})" for name in missing
        ]
        raise GroundingContractError(
            f"knowledge graph is missing {len(missing)} entities the agent layer "
            f"grounds against: {'; '.join(parts)}. Either restore the entity in "
            "docs/build_knowledge_graph.py and rebuild, or remove the route in "
            "src/recon/agents/grounding.py."
        )

    empty = sorted(
        name
        for name in REQUIRED_ENTITY_NAMES
        if not entities[name].get("observations")
    )
    if empty:
        parts = [
            f"{name!r} (referenced by {', '.join(_referencing_keys(name))})" for name in empty
        ]
        raise GroundingContractError(
            f"knowledge graph has {len(empty)} required entities with zero "
            f"observations, so they can never yield a clause: {'; '.join(parts)}. "
            "UNOBS() can empty an entity without removing it -- restore an observation "
            "in docs/build_knowledge_graph.py, or remove the route."
        )


def _validate_route_tables_exhaustive() -> None:
    reason_codes = set(REASON_CODE_ROUTES)
    if reason_codes != set(ReasonCode):
        missing = sorted(c.name for c in ReasonCode if c not in reason_codes)
        extra = sorted(c.name for c in reason_codes if c not in set(ReasonCode))
        raise GroundingContractError(
            "REASON_CODE_ROUTES is not exhaustive over ReasonCode. "
            f"missing: {missing or 'none'}; not a ReasonCode member: {extra or 'none'}. "
            "A gap should fail here, at import, not disappear (src/recon/api/dossier.py:473-478)."
        )

    flags = set(FLAG_ROUTES)
    if flags != set(CrossTrackFlag):
        missing = sorted(f.name for f in CrossTrackFlag if f not in flags)
        extra = sorted(f.name for f in flags if f not in set(CrossTrackFlag))
        raise GroundingContractError(
            "FLAG_ROUTES is not exhaustive over CrossTrackFlag. "
            f"missing: {missing or 'none'}; not a CrossTrackFlag member: {extra or 'none'}."
        )


def _build_clauses(entities: dict[str, dict[str, Any]]) -> tuple[dict[str, tuple[Clause, ...]], tuple[Clause, ...]]:
    by_entity: dict[str, tuple[Clause, ...]] = {}
    all_clauses: list[Clause] = []
    seen_ids: dict[str, str] = {}  # clause_id -> "entity#index" of first sighting

    for name, row in entities.items():
        entity_type = row["entityType"]
        clauses: list[Clause] = []
        for index, text in enumerate(row["observations"]):
            clause_id = _clause_id(name, text)
            if clause_id in seen_ids:
                raise GroundingContractError(
                    f"clause_id collision: {clause_id!r} was minted for both "
                    f"{seen_ids[clause_id]!r} and {name!r}#{index}. That is a genuine "
                    "sha256 collision at 8 hex chars or two byte-identical "
                    "(entity, observation) pairs -- investigate before trusting either clause."
                )
            seen_ids[clause_id] = f"{name}#{index}"
            clause = Clause(clause_id=clause_id, entity=name, entity_type=entity_type, text=text, index=index)
            clauses.append(clause)
            all_clauses.append(clause)
        by_entity[name] = tuple(clauses)

    return by_entity, tuple(all_clauses)


# ═══ module-level: run every guard once, at import ══════════════════════════════════
#
# This is the runtime dependency CLAUDE.md ("THE GRAPH IS A RUNTIME DEPENDENCY") and
# spec_grounding_rubric.md S:1.6 describe: a broken graph must fail here, at process
# start, not the first time an episode happens to route through the missing entity.

GRAPH_PATH: Path = _resolve_graph_path()
_ENTITIES, _RELATION_COUNT = load_graph(GRAPH_PATH)
_validate_required_entities(_ENTITIES)
_validate_route_tables_exhaustive()
_CLAUSES_BY_ENTITY, ALL_CLAUSES = _build_clauses(_ENTITIES)


def select_clauses(dossier: dict[str, Any]) -> tuple[Clause, ...]:
    """Deterministic.  Same dossier in, byte-identical clause list out.

    Order is fixed by tier, then by sorted code within a tier -- never by set or dict
    iteration order -- so the prompt bytes this produces are reproducible run to run
    (T10).  Truncation is at the entity boundary (``ordered[:MAX_ENTITIES]``), never
    mid-entity: a half-included entity would present a decision's caveats without its
    claim, which is worse than omitting the entity altogether.
    """
    ordered: list[str] = []

    def push(names: tuple[str, ...]) -> None:
        for n in names:  # tier order preserved
            if n not in ordered:
                ordered.append(n)

    push(UNIVERSAL)  # tier 0
    push(TRACK_ROUTES[ReimbursementTrack(dossier["identity"]["track"])])  # tier 1

    current = dossier["current"] or {}
    if current.get("rebate_verdict") not in (None, "C-00"):  # tier 2
        push(REBATE_ENTITIES)
    for code in sorted(current.get("reason_codes", [])):  # tier 3, sorted
        push(REASON_CODE_ROUTES[ReasonCode(code)])
    for flag in sorted(current.get("cross_track_flags", [])):  # tier 4, sorted
        push(FLAG_ROUTES[CrossTrackFlag.from_code(flag)])
    for predicate, names in STRUCTURAL_ROUTES:  # tier 5, declaration order
        if predicate(dossier):
            push(names)

    # reviewer finding 11 (spec_fixes_round1.md C4): `clauses[:MAX_CLAUSES]` on the
    # flattened list truncated mid-entity whenever an entity's clauses straddled the
    # boundary -- presenting that entity's caveats without its claim (or vice versa),
    # which the docstring above already says is worse than omitting the entity
    # altogether. Whole entities are dropped instead: walk the entity-capped order and
    # stop *before* adding an entity that would push the running total over budget.
    clauses: list[Clause] = []
    for name in ordered[:MAX_ENTITIES]:
        entity_clauses = _CLAUSES_BY_ENTITY[name]
        if len(clauses) + len(entity_clauses) > MAX_CLAUSES:
            break
        clauses.extend(entity_clauses)
    return tuple(clauses)


def render_clause_index(clauses: Sequence[Clause]) -> str:
    """The clauses as the text a prompt actually carries.

    Both roles take ``grounding_clause_index`` as a *string*, and the proposer must
    cite one clause by id -- so the rendering has to make the id quotable and the
    claim readable in the same line. Grouped by entity, because the entity name is
    the retrieval handle a model reasons with ("the 340B rebate track says...") while
    the id is only the citation token.

    This lives here rather than in each caller because a second rendering of the same
    clauses is a second thing to keep in step with the citation scorer, which resolves
    ``"KG:<entity name>"`` against ``Clause.entity``. One renderer, one authority --
    the same lesson as ``render_tool_result`` (reviewer finding 18).
    """
    lines: list[str] = []
    current_entity: str | None = None
    for clause in clauses:
        if clause.entity != current_entity:
            current_entity = clause.entity
            lines.append(f"\n### {clause.entity}  ({clause.entity_type})")
        lines.append(f"- [{clause.clause_id}] {clause.text}")
    return "\n".join(lines).strip()
