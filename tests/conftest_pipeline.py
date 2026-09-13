"""Shared end-to-end fixture: generate, ingest, reconcile, and score against ground truth.

Imported by the end-to-end tests rather than living in ``conftest.py``, because building a
``full``-profile dataset and running the whole pipeline over it is expensive enough to want
explicit session scoping and an obvious name.

═══ How scoring works, and why it is partitioned ═══════════════════════════════════

The engine's job is to reach the verdict the generator intended **by reading the feeds**.  It
never sees ground truth, and ground-truth episode ids are not the connector's episode ids — the
connector mints its own, because it has no way to know what the generator called anything.  The
two are matched by **natural key**, which is the same crosswalk problem the system exists to
solve, so the match itself is part of what is being tested.

Episodes are then scored in two populations, and conflating them would make the result
meaningless:

* **Resolvable** — nothing about this episode was designed to fail. The engine must reproduce the
  intended verdict pair exactly. A miss here is a real bug.
* **Intended-miss** — the dataset deliberately broke a link for this episode: identifier drift
  (D-6), or a medical 340B natural key that genuinely cannot separate two same-day
  administrations. The connector is *supposed* to park those records, so the engine sees a
  smaller picture and reaches a different verdict. That is the exception mechanism working, and
  scoring it as a failure would reward a connector that normalised the defect away — which
  Decision A23 exists to forbid.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from recon.config import Settings, load_settings
from recon.crosswalk import keys
from recon.db import connection, migrate
from recon.engine import run as engine
from recon.generators import orchestrator
from recon.ingest import pipeline

__all__ = ["PipelineRun", "build_pipeline", "Scorecard", "score"]


@dataclass
class PipelineRun:
    """One full pass: feeds on disk, a populated database, and computed verdicts."""

    settings: Settings
    generation: orchestrator.GenerationResult
    conn: sqlite3.Connection
    stats: pipeline.IngestStats
    results: list[engine.EngineResult]

    @property
    def ground_truth(self) -> dict[str, Any]:
        return self.generation.ground_truth


def build_pipeline(profile: str, data_dir: Path) -> PipelineRun:
    """Generate, write, load, ingest and reconcile, at the maximum cursor.

    The maximum cursor means "every record has arrived", which is the state the intended verdicts
    in ground truth describe.  Earlier cursors are exercised separately by the replay tests.
    """
    settings = load_settings(profile, data_dir=data_dir)
    generation = orchestrator.generate(settings)
    orchestrator.write_outputs(settings, generation)

    conn = connection.connect(":memory:")
    migrate.ensure_schema(conn)
    stats = pipeline.load_feeds(conn, settings.feeds_dir())
    pipeline.ingest(conn, stats=stats)

    results = engine.run_all(conn, settings.max_cursor)
    return PipelineRun(
        settings=settings,
        generation=generation,
        conn=conn,
        stats=stats,
        results=results,
    )


@dataclass
class Scorecard:
    resolvable_total: int = 0
    resolvable_match: int = 0
    intended_miss_total: int = 0
    intended_miss_match: int = 0
    unmatched_episodes: int = 0
    #: ``(ground_truth_episode_id, expected_pair, derived_pair)`` for resolvable misses only.
    failures: list[tuple[str, tuple[str, str], tuple[str, str]]] = field(default_factory=list)

    @property
    def resolvable_rate(self) -> float:
        if self.resolvable_total == 0:
            return 0.0
        return self.resolvable_match / self.resolvable_total


def score(run: PipelineRun) -> Scorecard:
    """Match engine episodes to ground truth by natural key, then compare verdict pairs."""
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for episode in run.ground_truth["episodes"]:
        for key_type, key_value in episode["natural_keys"].items():
            by_key[(key_type, key_value)] = episode

    card = Scorecard()
    for result in run.results:
        row = run.conn.execute(
            "SELECT * FROM episode WHERE episode_id = ?", (result.episode_id,)
        ).fetchone()
        expected_episode = _match_ground_truth(row, by_key)
        if expected_episode is None:
            card.unmatched_episodes += 1
            continue

        derived = (result.reimbursement_verdict, result.rebate_verdict)
        intended = (
            expected_episode["intended_reimbursement_verdict"],
            expected_episode["intended_rebate_verdict"],
        )
        intended_miss = expected_episode["expected_links"].get("expected_crosswalk_miss", False)

        if intended_miss:
            card.intended_miss_total += 1
            if derived == intended:
                card.intended_miss_match += 1
        else:
            card.resolvable_total += 1
            if derived == intended:
                card.resolvable_match += 1
            else:
                card.failures.append((expected_episode["episode_id"], intended, derived))
    return card


def _match_ground_truth(row, by_key) -> dict[str, Any] | None:
    """Find the ground-truth episode this connector episode corresponds to.

    By natural key, never by id.  The connector's ids are its own.
    """
    if row["rx_number"]:
        _, value = keys.ncpdp_claim(
            row["pharmacy_npi"], row["rx_number"], row["fill_number"], row["date_of_service"]
        )
        return by_key.get(("NCPDP_CLAIM", value))
    _, value = keys.medical_clm01(row["clm01"])
    return by_key.get(("MEDICAL_CLM01", value))
