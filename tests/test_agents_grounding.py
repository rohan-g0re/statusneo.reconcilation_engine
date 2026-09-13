"""Tests for the grounding layer: ``docs/knowledge_graph.jsonl`` as a runtime dependency.

Named and shaped after ``spec_grounding_rubric.md`` S:7's table (T1-T13).  The point of
this file is exactly what ``CLAUDE.md`` says under "THE GRAPH IS A RUNTIME DEPENDENCY":
a graph rebuild that drops an entity a routing table still references must fail a test
here, at CI time, rather than silently break the agent layer the next time an episode
happens to route through the missing entity.
"""

from __future__ import annotations

import ast
import importlib
import importlib.util
import json
import os
import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from conftest_pipeline import build_pipeline  # noqa: E402

from recon.domain.enums import CrossTrackFlag, Disposition, ReasonCode  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
GRAPH_PATH = REPO_ROOT / "docs" / "knowledge_graph.jsonl"
AGENTS_DIR = REPO_ROOT / "src" / "recon" / "agents"
BUILD_SCRIPT = REPO_ROOT / "docs" / "build_knowledge_graph.py"


# ═══ fixtures ════════════════════════════════════════════════════════════════════


@pytest.fixture()
def graph_rows() -> list[dict]:
    """``docs/knowledge_graph.jsonl`` — the live artefact, read directly.

    Mirrors the ``pairs_oracle`` fixture pattern at ``tests/conftest.py:30-46``: this
    reads the generated file itself, with a rebuild hint if it is ever missing,
    rather than a frozen copy checked into ``tests/``.
    """
    assert GRAPH_PATH.exists(), (
        f"{GRAPH_PATH} is missing. It is a generated, committed artefact, not an "
        "optional one; rebuild it with `python docs/build_knowledge_graph.py`."
    )
    return [
        json.loads(line)
        for line in GRAPH_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


@pytest.fixture()
def entity_names(graph_rows: list[dict]) -> set[str]:
    return {row["name"] for row in graph_rows if row["type"] == "entity"}


@pytest.fixture(scope="module")
def full_profile_dossiers(tmp_path_factory) -> list[dict]:
    """Every episode in the ``full`` profile, as a dossier, at the max cursor.

    Reuses ``tests/conftest_pipeline.py`` exactly as ``tests/test_end_to_end.py``
    does: generating this dataset is expensive enough (~2.5s) to want a module-scoped
    fixture rather than rebuilding it per test.
    """
    from recon.api import dossier as dossier_module

    run = build_pipeline("full", tmp_path_factory.mktemp("grounding-full"))
    episode_ids = [row[0] for row in run.conn.execute("SELECT episode_id FROM episode").fetchall()]
    dossiers = []
    for episode_id in episode_ids:
        d = dossier_module.build_dossier(run.conn, episode_id, run.settings.max_cursor)
        if d is not None:
            dossiers.append(d)
    assert len(dossiers) >= 1000, f"expected roughly 1354 full-profile episodes, got {len(dossiers)}"
    return dossiers


def _sample_dossier() -> dict:
    """A hand-built dossier exercising every tier: rebate, reason codes, a flag, and
    all five structural predicates."""
    return {
        "identity": {"track": "PHARMACY"},
        "current": {
            "rebate_verdict": "C-08",
            "reason_codes": ["UNDERPAID", "REBATE_CLAWED_BACK"],
            "cross_track_flags": ["X-1"],
            "reopened_from": "CLOSED",
            "episode_disposition": "EXCEPTION",
        },
        "unresolved": [{"record_kind": "PHARMACY_CLAIM"}],
        "timeline": [
            {"tag": "PHARMACY_CLAIM", "late": True, "facts": {}},
            {"tag": "MEDICAL_ACKNOWLEDGMENT", "late": False, "facts": {"stc12_free_form": "ok"}},
        ],
        "counts": {"verdict_changes": 2},
    }


def _reload_grounding():
    import recon.agents.grounding as grounding

    return importlib.reload(grounding)


def _write_graph_variant(tmp_path: Path, mutate) -> Path:
    rows = [
        json.loads(line)
        for line in GRAPH_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    mutate(rows)
    out = tmp_path / "graph_variant.jsonl"
    out.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    return out


@pytest.fixture()
def restore_grounding_env():
    """Guarantee ``recon.agents.grounding`` is back in its committed-graph state
    after a test that reloads it against a broken fixture, regardless of outcome."""
    old = os.environ.get("RECON_KNOWLEDGE_GRAPH")
    yield
    if old is None:
        os.environ.pop("RECON_KNOWLEDGE_GRAPH", None)
    else:
        os.environ["RECON_KNOWLEDGE_GRAPH"] = old
    _reload_grounding()


# ═══ T1 ══════════════════════════════════════════════════════════════════════════


def test_every_required_entity_exists_in_the_graph(entity_names: set[str]):
    import recon.agents.grounding as grounding

    missing = sorted(name for name in grounding.REQUIRED_ENTITY_NAMES if name not in entity_names)
    assert not missing, "missing from the graph: " + "; ".join(
        f"{n!r} (referenced by {', '.join(grounding._referencing_keys(n))})" for n in missing
    )


# ═══ T2 ══════════════════════════════════════════════════════════════════════════


def test_import_raises_when_a_required_entity_is_missing(tmp_path, restore_grounding_env):
    def drop_deterministic_boundary(rows: list[dict]) -> None:
        rows[:] = [
            r for r in rows if not (r["type"] == "entity" and r["name"] == "Deterministic boundary")
        ]

    bad_path = _write_graph_variant(tmp_path, drop_deterministic_boundary)
    os.environ["RECON_KNOWLEDGE_GRAPH"] = str(bad_path.resolve())

    with pytest.raises(Exception) as excinfo:
        _reload_grounding()

    import recon.agents.grounding as grounding

    assert isinstance(excinfo.value, grounding.GroundingContractError)
    assert "Deterministic boundary" in str(excinfo.value)


# ═══ T3 ══════════════════════════════════════════════════════════════════════════


def test_import_raises_when_a_required_entity_has_no_observations(tmp_path, restore_grounding_env):
    def empty_disposition_observations(rows: list[dict]) -> None:
        for r in rows:
            if r["type"] == "entity" and r["name"] == "Disposition":
                r["observations"] = []

    bad_path = _write_graph_variant(tmp_path, empty_disposition_observations)
    os.environ["RECON_KNOWLEDGE_GRAPH"] = str(bad_path.resolve())

    with pytest.raises(Exception) as excinfo:
        _reload_grounding()

    import recon.agents.grounding as grounding

    assert isinstance(excinfo.value, grounding.GroundingContractError)
    assert "Disposition" in str(excinfo.value)


# ═══ T4 ══════════════════════════════════════════════════════════════════════════


def test_reason_code_routes_cover_every_reason_code():
    import recon.agents.grounding as grounding

    assert set(grounding.REASON_CODE_ROUTES) == set(ReasonCode)


# ═══ T5 ══════════════════════════════════════════════════════════════════════════


def test_flag_routes_cover_every_cross_track_flag():
    import recon.agents.grounding as grounding

    assert set(grounding.FLAG_ROUTES) == set(CrossTrackFlag)


def test_allowed_actions_cover_every_disposition():
    from recon.agents.scorers import ALLOWED_BY_DISPOSITION

    assert set(ALLOWED_BY_DISPOSITION) == set(Disposition)


# ═══ T6 ══════════════════════════════════════════════════════════════════════════


def test_clause_ids_are_unique():
    import recon.agents.grounding as grounding

    ids = [c.clause_id for c in grounding.ALL_CLAUSES]
    assert len(grounding.ALL_CLAUSES) == 366
    assert len(set(ids)) == len(ids)


# ═══ T7 ══════════════════════════════════════════════════════════════════════════


def test_clause_ids_survive_a_graph_rebuild():
    """Rebuild the graph in an isolated module namespace — never writing to the
    committed file — and assert every required entity's clause-id set is unchanged."""
    import recon.agents.grounding as grounding

    spec = importlib.util.spec_from_file_location("_kg_rebuild_for_test", BUILD_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for wave in module.WAVES:
        wave()

    rebuilt_by_entity: dict[str, set[str]] = {
        row["name"]: {grounding._clause_id(row["name"], obs) for obs in row["observations"]}
        for row in module.entities
    }
    committed_by_entity: dict[str, set[str]] = {
        name: {c.clause_id for c in clauses} for name, clauses in grounding._CLAUSES_BY_ENTITY.items()
    }

    for name in grounding.REQUIRED_ENTITY_NAMES:
        assert rebuilt_by_entity.get(name) == committed_by_entity.get(name), (
            f"clause ids for {name!r} changed across a rebuild in this process; the "
            "wave that declares it must have been edited without OBS()/UNOBS()."
        )


# ═══ T8 ══════════════════════════════════════════════════════════════════════════


def test_clause_text_is_verbatim_from_the_graph(graph_rows: list[dict]):
    import recon.agents.grounding as grounding

    by_name = {row["name"]: row for row in graph_rows if row["type"] == "entity"}
    for clause in grounding.ALL_CLAUSES:
        assert clause.text in by_name[clause.entity]["observations"], (
            f"clause {clause.clause_id} text does not appear byte-for-byte among "
            f"{clause.entity!r}'s observations"
        )


# ═══ T9 ══════════════════════════════════════════════════════════════════════════


def test_no_agent_module_names_an_entity_outside_the_routing_tables(entity_names: set[str]):
    import recon.agents.grounding as grounding

    offenders: list[tuple[str, str]] = []
    for path in sorted(AGENTS_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if node.value in entity_names and node.value not in grounding.REQUIRED_ENTITY_NAMES:
                    offenders.append((str(path.relative_to(REPO_ROOT)), node.value))
    assert not offenders, (
        "these agent-layer modules name a graph entity the routing tables do not "
        f"reference (add a route, or stop naming it): {offenders}"
    )


# ═══ T10 ═════════════════════════════════════════════════════════════════════════


def test_selection_is_deterministic():
    import recon.agents.grounding as grounding

    dossier = _sample_dossier()
    results = [grounding.select_clauses(dossier) for _ in range(20)]
    assert all(r == results[0] for r in results)

    original_reason_routes = grounding.REASON_CODE_ROUTES
    original_flag_routes = grounding.FLAG_ROUTES
    try:
        shuffled_reason_items = list(original_reason_routes.items())
        random.Random(7).shuffle(shuffled_reason_items)
        shuffled_flag_items = list(original_flag_routes.items())
        random.Random(11).shuffle(shuffled_flag_items)
        grounding.REASON_CODE_ROUTES = dict(shuffled_reason_items)
        grounding.FLAG_ROUTES = dict(shuffled_flag_items)
        shuffled_result = grounding.select_clauses(dossier)
    finally:
        grounding.REASON_CODE_ROUTES = original_reason_routes
        grounding.FLAG_ROUTES = original_flag_routes

    assert shuffled_result == results[0], (
        "shuffling dict insertion order changed the selection; order must come from "
        "the tier rules, never from dict/set iteration order"
    )


# ═══ T11 ═════════════════════════════════════════════════════════════════════════


def test_selection_respects_the_clause_budget(full_profile_dossiers: list[dict]):
    import recon.agents.grounding as grounding

    clause_counts: list[int] = []
    for d in full_profile_dossiers:
        clauses = grounding.select_clauses(d)
        assert len(clauses) <= grounding.MAX_CLAUSES
        assert len({c.entity for c in clauses}) <= grounding.MAX_ENTITIES
        clause_counts.append(len(clauses))

    clause_counts.sort()
    mean = sum(clause_counts) / len(clause_counts)
    p95 = clause_counts[int(0.95 * (len(clause_counts) - 1))]
    print(
        f"\nselect_clauses over {len(clause_counts)} full-profile episodes: "
        f"mean={mean:.1f} clauses, p95={p95} clauses (ceiling {grounding.MAX_CLAUSES})"
    )


# ═══ T12 ═════════════════════════════════════════════════════════════════════════


def test_every_episode_selects_the_universal_set_and_a_track(full_profile_dossiers: list[dict]):
    import recon.agents.grounding as grounding

    for d in full_profile_dossiers:
        clauses = grounding.select_clauses(d)
        entities = {c.entity for c in clauses}
        assert set(grounding.UNIVERSAL) <= entities
        assert "Reimbursement track" in entities
        current = d["current"] or {}
        if current.get("rebate_verdict") not in (None, "C-00"):
            assert "340B" in entities, f"{d['episode_id']} has a rebate track but no 340B grounding"


# ═══ T13 ═════════════════════════════════════════════════════════════════════════


def test_relations_parse_but_are_not_clauses(graph_rows: list[dict], tmp_path):
    import recon.agents.grounding as grounding

    relation_rows = [r for r in graph_rows if r["type"] == "relation"]
    assert relation_rows, "no relation rows found in the graph"
    for row in relation_rows:
        assert {"from", "to", "relationType"} <= set(row)

    entities, relation_count = grounding.load_graph(grounding.GRAPH_PATH)
    assert relation_count == len(relation_rows) == 90
    assert len(entities) == len(grounding._ENTITIES) == 78
    for clause in grounding.ALL_CLAUSES:
        assert clause.entity in entities  # every clause traces to an entity row

    def drop_relation_target(rows: list[dict]) -> None:
        for r in rows:
            if r["type"] == "relation":
                del r["to"]
                return
        raise AssertionError("no relation row found to corrupt")

    bad_path = _write_graph_variant(tmp_path, drop_relation_target)
    with pytest.raises(grounding.GroundingContractError):
        grounding.load_graph(bad_path)
