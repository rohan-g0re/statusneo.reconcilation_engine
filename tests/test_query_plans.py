"""Every hot query must be an index seek, and this test is what keeps it that way.

``EXPLAIN QUERY PLAN`` is the only honest way to check this.  "It is indexed" is an assertion about
the schema; "SQLite chose the index" is an assertion about reality, and the two come apart more
often than is comfortable — a partial index whose predicate the planner cannot prove, a join driven
from the wrong side, an ``ORDER BY`` that forces a sort.  All three happened in this codebase and
all three are pinned below.

═══ Why a test rather than a one-off audit ═════════════════════════════════════════

An audit tells you the plans were good once.  Index choice is a *consequence* of the schema, the
query text and the statistics, so any of those changing can silently turn a seek into a scan with
no test failing and no behaviour changing — until the dataset is large enough to matter.  Pinning
the plans means a regression shows up as a failing test rather than as a slow demo.

The queue functions get their own tests for a blunter reason: they were **crashing**, for every
ordering, with "ambiguous column name: episode_id" — and nothing noticed, because nothing called
them. The exception queue is the operator's primary read path.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from conftest_pipeline import build_pipeline  # noqa: E402

from recon.db import repository  # noqa: E402
from recon.domain.enums import KeyType, RecordKind  # noqa: E402


@pytest.fixture(scope="module")
def populated(tmp_path_factory):
    """A full-profile database with statistics, so the planner decides on real numbers."""
    run = build_pipeline("full", tmp_path_factory.mktemp("plans"))
    run.conn.execute("ANALYZE")
    return run


def plan_for(conn, sql: str, params) -> str:
    rows = conn.execute(f"EXPLAIN QUERY PLAN {sql}", params).fetchall()
    return "\n".join(row["detail"] for row in rows)


def assert_no_scan(plan: str, *, table: str, context: str) -> None:
    """Fail if the plan scans ``table``.

    ``SCAN`` on a subquery or a CTE is not the same thing and is not flagged — only a scan of the
    named base table, which is what turns a point lookup into work proportional to the dataset.
    """
    offending = [
        line
        for line in plan.splitlines()
        if line.strip().startswith("SCAN") and table in line
    ]
    assert not offending, f"{context} scans {table}:\n{plan}"


# ═══ the two hottest paths: both run on every inbound document ══════════════


def test_key_resolution_is_a_covering_index_seek(populated):
    """Forward resolution. A document publishing 48 keys must do 48 seeks, not 48 scans."""
    row = populated.conn.execute(
        "SELECT key_type, key_value FROM crosswalk_key LIMIT 1"
    ).fetchone()
    plan = plan_for(
        populated.conn,
        "SELECT key_type, key_value, episode_id, remittance_norm_id, first_seen_at,"
        "       resolved_from_norm_id"
        "  FROM crosswalk_key"
        " WHERE key_type = ? AND key_value IN (?) AND first_seen_at <= ?",
        (row["key_type"], row["key_value"], populated.settings.max_cursor),
    )
    assert_no_scan(plan, table="crosswalk_key", context="resolve_keys")
    assert "ix_crosswalk_lookup" in plan
    assert "COVERING" in plan, (
        "the lookup index carries the cursor column and both payload columns so the probe needs "
        "no table fetch; losing that makes every resolution two reads instead of one"
    )


def test_the_parked_backward_recheck_is_a_seek(populated):
    """The backward half of the crosswalk, run on every single arrival.

    Without an index leading on ``(key_type, key_value)`` this is a full walk of the parked pool
    per inbound document — the difference between an ingest that finishes and one that does not.
    """
    row = populated.conn.execute(
        "SELECT key_type, key_value FROM parked_record_key LIMIT 1"
    ).fetchone()
    plan = plan_for(
        populated.conn,
        "SELECT p.parked_id FROM parked_record_key k"
        "  JOIN parked_record p ON p.parked_id = k.parked_id"
        "  LEFT JOIN parked_record_resolution r"
        "    ON r.parked_id = p.parked_id AND r.resolved_by_received_at <= ?"
        " WHERE k.key_type = ? AND k.key_value IN (?) AND p.received_at <= ?"
        "   AND r.parked_id IS NULL",
        (
            populated.settings.max_cursor,
            row["key_type"],
            row["key_value"],
            populated.settings.max_cursor,
        ),
    )
    assert_no_scan(plan, table="parked_record_key", context="parked_matching")
    assert "ix_parked_key_lookup" in plan


def test_the_allocator_finds_an_episode_by_seek_not_scan(populated):
    """Asked once per claim line, PLB entry and rebate dispense line during every allocation.

    An audit caught this one scanning the entire crosswalk — roughly 12,000 rows — for each of
    several thousand children, which is quadratic work hiding inside a single-row lookup.
    """
    norm_id = populated.conn.execute(
        "SELECT resolved_from_norm_id FROM crosswalk_key WHERE episode_id IS NOT NULL LIMIT 1"
    ).fetchone()["resolved_from_norm_id"]
    plan = plan_for(
        populated.conn,
        "SELECT episode_id FROM crosswalk_key"
        " WHERE resolved_from_norm_id = ? AND episode_id IS NOT NULL LIMIT 1",
        (norm_id,),
    )
    assert_no_scan(plan, table="crosswalk_key", context="_episode_for_child")
    assert "ix_crosswalk_resolved_from" in plan


def test_the_amount_and_date_rescue_is_a_seek(populated):
    """The ~20% of deposits whose CCD+ addenda were lost.

    This one is the reason the index is **not** partial.  A partial index predicated on
    ``record_kind IN ('REMITTANCE','REBATE_BATCH')`` reads as tighter, but the call site binds
    ``record_kind`` as a parameter and SQLite cannot prove a placeholder satisfies a literal
    predicate — so it declined the index and walked every remittance instead. A scan wearing a
    seek's clothes.
    """
    plan = plan_for(
        populated.conn,
        "SELECT norm_id FROM normalized_record"
        " WHERE record_kind IN (?, ?) AND amount_cents = ?"
        "   AND received_at <= ?"
        "   AND ABS(julianday(date_of_service) - julianday(?)) <= ?"
        " LIMIT 2",
        (
            str(RecordKind.REMITTANCE),
            str(RecordKind.REBATE_BATCH),
            123_456,
            populated.settings.max_cursor,
            "2026-01-01",
            3,
        ),
    )
    assert_no_scan(plan, table="normalized_record", context="_resolve_bank_by_amount_and_date")
    assert "ix_norm_amount_match" in plan


def test_the_keyless_deposit_backward_pass_is_a_seek(populated):
    """Finding a parked deposit with no business reference, by amount and date.

    Driven from ``amount_cents``, not from ``trn02 IS NULL`` — the latter matches about 90% of the
    table and is useless as a driving predicate.
    """
    plan = plan_for(
        populated.conn,
        "SELECT p.parked_id FROM parked_record p"
        "  JOIN normalized_record n ON n.norm_id = p.norm_id"
        "  LEFT JOIN parked_record_resolution r ON r.parked_id = p.parked_id"
        " WHERE p.record_kind = ? AND r.parked_id IS NULL AND n.trn02 IS NULL"
        "   AND n.amount_cents = ? AND p.received_at <= ?"
        "   AND ABS(julianday(n.date_of_service) - julianday(?)) <= ?"
        " LIMIT 2",
        (
            str(RecordKind.BANK_TRANSACTION),
            123_456,
            populated.settings.max_cursor,
            "2026-01-01",
            3,
        ),
    )
    assert "ix_norm_keyless_amount" in plan or "ix_parked_kind_received" in plan, (
        f"the keyless rescue must be driven by an index, not by a 90%-selective IS NULL:\n{plan}"
    )


def test_idempotency_check_is_a_unique_index_probe(populated):
    """Run once per record, and it is what collapses a duplicate delivery."""
    row = populated.conn.execute(
        "SELECT record_kind, idempotency_key FROM normalized_record LIMIT 1"
    ).fetchone()
    plan = plan_for(
        populated.conn,
        "SELECT norm_id FROM normalized_record WHERE record_kind = ? AND idempotency_key = ?",
        (row["record_kind"], row["idempotency_key"]),
    )
    assert_no_scan(plan, table="normalized_record", context="_find_by_idempotency")
    assert "ux_norm_idempotency" in plan


def test_latest_verdict_walks_the_index_not_the_table(populated):
    """The other hot read: what stood for this episode at this cursor."""
    episode_id = populated.conn.execute("SELECT episode_id FROM episode LIMIT 1").fetchone()[
        "episode_id"
    ]
    plan = plan_for(
        populated.conn,
        "SELECT * FROM verdict WHERE episode_id = ? AND cursor_at <= ?"
        " ORDER BY cursor_at DESC, verdict_id DESC LIMIT 1",
        (episode_id, populated.settings.max_cursor),
    )
    assert_no_scan(plan, table="verdict", context="latest_verdict")
    assert "ix_verdict_latest" in plan
    assert "TEMP B-TREE" not in plan, (
        "the index order is exactly the ORDER BY, so there should be nothing left to sort"
    )


def test_evidence_lookup_is_a_seek(populated):
    """Lineage: the query that answers "trace this number back to the source record"."""
    verdict_id = populated.conn.execute("SELECT verdict_id FROM verdict LIMIT 1").fetchone()[
        "verdict_id"
    ]
    plan = plan_for(
        populated.conn,
        "SELECT ev.norm_id FROM verdict_evidence ev"
        "  JOIN raw_record r ON r.raw_id = ev.raw_id"
        "  JOIN ingest_batch b ON b.batch_id = r.batch_id"
        " WHERE ev.verdict_id = ? ORDER BY ev.ordinal",
        (verdict_id,),
    )
    assert_no_scan(plan, table="verdict_evidence", context="evidence_for_verdict")
    assert_no_scan(plan, table="raw_record", context="evidence_for_verdict")


def test_the_reporting_queries_are_indexed(populated):
    """D-4 and D-7 reporting. Not per-document, but they drive the front end's exception view."""
    cursor = populated.settings.max_cursor
    residuals = plan_for(
        populated.conn,
        "SELECT * FROM cash_allocation WHERE basis = 'RESIDUAL' AND caused_by_received_at <= ?",
        (cursor,),
    )
    assert_no_scan(residuals, table="cash_allocation", context="allocation_residuals")
    assert "ix_alloc_residual" in residuals

    orphan_rebates = plan_for(
        populated.conn,
        "SELECT p.parked_id FROM parked_record p"
        "  LEFT JOIN parked_record_resolution r"
        "    ON r.parked_id = p.parked_id AND r.resolved_by_received_at <= ?"
        " WHERE p.record_kind IN ('REBATE_DISPENSE_LINE','REBATE_BATCH')"
        "   AND p.received_at <= ? AND r.parked_id IS NULL",
        (cursor, cursor),
    )
    assert_no_scan(orphan_rebates, table="parked_record", context="orphan_rebates")


# ═══ the queue functions, which were crashing ══════════════════════════════


@pytest.mark.parametrize("ordering", sorted(repository.QUEUE_ORDERINGS))
def test_every_queue_ordering_actually_runs(populated, ordering: str):
    """Each whitelisted ordering must execute.

    All of them raised "ambiguous column name: episode_id" — ``episode_id`` exists on both
    ``episode`` and ``verdict``, and the ``ORDER BY`` did not say which. No test called these
    functions, so the exception queue was broken for every sort and the suite was green.
    """
    cursor = populated.settings.max_cursor
    exceptions = repository.exception_queue(populated.conn, cursor, order_by=ordering, limit=25)
    pending = repository.pending_queue(populated.conn, cursor, order_by=ordering, limit=25)
    assert exceptions, "the exception queue should not be empty on the full profile"
    assert pending, "the pending queue should not be empty on the full profile"
    for row in exceptions:
        assert row.episode_disposition.value == "EXCEPTION"
    for row in pending:
        assert row.episode_disposition.value == "PENDING"


def test_an_unknown_ordering_is_refused(populated):
    """A free-form ORDER BY from a caller would be an injection point."""
    with pytest.raises(ValueError, match="unknown queue ordering"):
        repository.exception_queue(populated.conn, populated.settings.max_cursor, order_by="; DROP")


def test_age_is_computed_at_read_time_and_sorts(populated):
    """Aging is a sort key, never a verdict input — and the queue is where it is allowed to exist."""
    rows = repository.exception_queue(
        populated.conn, populated.settings.max_cursor, order_by="age_desc", limit=50
    )
    ages = [row.age_days for row in rows]
    assert ages == sorted(ages, reverse=True)
    assert all(age >= 0 for age in ages), "an episode cannot be dispensed in the future"


def test_reopened_episodes_sort_ahead_of_the_rest(populated):
    """Reopened-from-closed outranks never-paid, deterministically.

    Money that was recognised and is now at risk of being un-recognised is a stronger claim on
    attention than money that was simply never collected. The agent explains the ranking; it does
    not produce it.
    """
    rows = repository.exception_queue(
        populated.conn, populated.settings.max_cursor, order_by="reopened_first", limit=200
    )
    ranks = [
        {"CLOSED": 0, "PENDING": 1}.get(
            row.reopened_from.value if row.reopened_from else None, 2
        )
        for row in rows
    ]
    assert ranks == sorted(ranks)


def test_queue_variance_ordering_sorts_by_money(populated):
    rows = repository.exception_queue(
        populated.conn, populated.settings.max_cursor, order_by="variance_desc", limit=40
    )
    totals = [abs(row.reimbursement_variance_cents) + abs(row.rebate_variance_cents) for row in rows]
    assert totals == sorted(totals, reverse=True)


def test_the_exception_queue_is_a_query_not_a_table(populated):
    """Nothing is ever *moved into* the exception queue.

    There is no queue table, no status column to update, and no row that says "this is an
    exception". The queue is a question asked of the verdict log at a cursor, which is what makes
    it rebuildable by replay.
    """
    tables = {
        row["name"]
        for row in populated.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert not any("queue" in name for name in tables)


def test_the_queues_partition_the_episodes(populated):
    """Three dispositions, and every episode is in exactly one of them at a given cursor."""
    cursor = populated.settings.max_cursor
    exceptions = repository.exception_queue(populated.conn, cursor, limit=None)
    pending = repository.pending_queue(populated.conn, cursor, limit=None)
    closed = populated.conn.execute(
        "SELECT COUNT(*) AS n FROM verdict WHERE episode_disposition = 'CLOSED'"
    ).fetchone()["n"]

    episode_ids = {row.episode_id for row in exceptions} | {row.episode_id for row in pending}
    assert len(episode_ids) == len(exceptions) + len(pending), "an episode is in one queue only"
    assert len(exceptions) + len(pending) + closed == populated.settings.episode_count
