"""The read model behind the API: plain functions over a connection, no framework in sight.

Kept separate from :mod:`recon.api.app` so the whole read surface is testable without HTTP, and so
FastAPI stays a transport rather than a place where logic accumulates.

**Nothing here computes a number.**  Every amount, variance, disposition, reason code and priority
was produced by the deterministic engine and is read back verbatim.  The API's job is to project
what the engine decided into the shape a screen needs — and aging, the one value derived at read
time, is computed in SQL as ``cursor − date_of_service`` and used only to sort.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Sequence

from recon import config
from recon.db import repository
from recon.domain.enums import Disposition

__all__ = [
    "overview",
    "queue",
    "episode_detail",
    "episode_trace",
    "feed_exceptions",
    "cursor_bounds",
]


def cursor_bounds(settings) -> dict[str, str]:
    """The window the scrubber moves through.

    ``min`` is the day before the window opens — a valid, empty state, and a useful one: it shows
    that an empty dataset is a cursor position rather than a separate mode.
    """
    return {
        "min": settings.min_cursor,
        "max": settings.max_cursor,
        "window_start": settings.window_start.isoformat(),
        "window_end": settings.window_end.isoformat(),
    }


def overview(conn: sqlite3.Connection, cursor: str) -> dict[str, Any]:
    """Counts and money by disposition at one cursor, plus the feed-level exception tallies.

    One row per disposition, not a scan per queue: the front end redraws this on every scrubber
    move, so it has to be cheap.
    """
    rows = conn.execute(
        repository._LATEST_CTE
        + " SELECT l.episode_disposition AS disposition,"
        "        COUNT(*) AS episodes,"
        "        SUM(l.expected_reimbursement_cents + l.expected_rebate_cents) AS expected_cents,"
        "        SUM(l.received_reimbursement_cents + l.received_rebate_cents) AS received_cents,"
        "        SUM(ABS(l.reimbursement_variance_cents) + ABS(l.rebate_variance_cents))"
        "          AS variance_cents,"
        "        SUM(CASE WHEN l.reopened_from IS NOT NULL THEN 1 ELSE 0 END) AS reopened"
        "   FROM latest l WHERE l.rn = 1"
        "  GROUP BY l.episode_disposition",
        {"cursor": cursor},
    ).fetchall()

    by_disposition = {
        disposition.value: {
            "episodes": 0,
            "expected_cents": 0,
            "received_cents": 0,
            "variance_cents": 0,
            "reopened": 0,
        }
        for disposition in Disposition
    }
    for row in rows:
        by_disposition[row["disposition"]] = {
            "episodes": row["episodes"],
            "expected_cents": row["expected_cents"] or 0,
            "received_cents": row["received_cents"] or 0,
            "variance_cents": row["variance_cents"] or 0,
            "reopened": row["reopened"] or 0,
        }

    verdict_spread = [
        {
            "reimbursement": row["reimbursement_verdict_code"],
            "rebate": row["rebate_verdict_code"],
            "episodes": row["n"],
        }
        for row in conn.execute(
            repository._LATEST_CTE
            + " SELECT l.reimbursement_verdict_code, l.rebate_verdict_code, COUNT(*) AS n"
            "   FROM latest l WHERE l.rn = 1"
            "  GROUP BY 1, 2 ORDER BY n DESC LIMIT 25",
            {"cursor": cursor},
        )
    ]

    reason_spread = [
        {"reason_code": row["reason_code"], "episodes": row["n"]}
        for row in conn.execute(
            repository._LATEST_CTE
            + " SELECT r.reason_code, COUNT(DISTINCT l.episode_id) AS n"
            "   FROM latest l JOIN verdict_reason r ON r.verdict_id = l.verdict_id"
            "  WHERE l.rn = 1 GROUP BY 1 ORDER BY n DESC",
            {"cursor": cursor},
        )
    ]

    flag_spread = [
        {"flag_code": row["flag_code"], "episodes": row["n"]}
        for row in conn.execute(
            repository._LATEST_CTE
            + " SELECT f.flag_code, COUNT(DISTINCT l.episode_id) AS n"
            "   FROM latest l JOIN verdict_cross_track_flag f ON f.verdict_id = l.verdict_id"
            "  WHERE l.rn = 1 GROUP BY 1 ORDER BY f.flag_code",
            {"cursor": cursor},
        )
    ]

    return {
        "cursor": cursor,
        "by_disposition": by_disposition,
        "verdict_pairs": verdict_spread,
        "reason_codes": reason_spread,
        "cross_track_flags": flag_spread,
        "engine_version": config.ENGINE_VERSION,
    }


def queue(
    conn: sqlite3.Connection,
    cursor: str,
    *,
    disposition: str,
    order_by: str = "reopened_first",
    limit: int = 100,
) -> list[dict[str, Any]]:
    """One queue at one cursor.

    The exception queue is not a table — it is this question asked of the verdict log. Which is why
    moving the cursor back gives the queue as it stood then, with no extra machinery.
    """
    resolved = Disposition(disposition)
    if resolved is Disposition.EXCEPTION:
        rows = repository.exception_queue(conn, cursor, order_by=order_by, limit=limit)
    elif resolved is Disposition.PENDING:
        rows = repository.pending_queue(conn, cursor, order_by=order_by, limit=limit)
    else:
        rows = _closed_queue(conn, cursor, order_by=order_by, limit=limit)

    reasons = _reasons_for_episodes(conn, cursor, [row.episode_id for row in rows])
    return [
        {
            "episode_id": row.episode_id,
            "track": row.reimbursement_track.value,
            "date_of_service": row.date_of_service,
            "ndc11": row.ndc11,
            "age_days": row.age_days,
            "disposition": row.episode_disposition.value,
            "reimbursement_verdict": row.reimbursement_verdict_code,
            "rebate_verdict": row.rebate_verdict_code,
            "reimbursement_variance_cents": row.reimbursement_variance_cents,
            "rebate_variance_cents": row.rebate_variance_cents,
            # Signed, because the direction matters when you read it: a negative total is an
            # overpayment (a refund liability) rather than money owed to us.
            "total_variance_cents": row.total_variance_cents,
            # Unsigned, because that is what ``variance_desc`` ranks by. An overpayment of $32,000
            # is as much money at stake as an underpayment of $32,000, so the ranking uses
            # magnitude -- and exposing it means the client can show the sort it actually got
            # rather than inferring one.
            "absolute_variance_cents": (
                abs(row.reimbursement_variance_cents) + abs(row.rebate_variance_cents)
            ),
            "reopened_from": row.reopened_from.value if row.reopened_from else None,
            "reason_codes": reasons.get(row.episode_id, []),
        }
        for row in rows
    ]


def _closed_queue(conn: sqlite3.Connection, cursor: str, *, order_by: str, limit: int | None):
    """Closed episodes, through the same projection.

    Not a queue anyone works, but the front end shows it so the three dispositions are visibly a
    partition rather than two buckets and a remainder.
    """
    from recon.domain.models import QueueRow

    if order_by not in repository.QUEUE_ORDERINGS:
        raise ValueError(f"unknown queue ordering {order_by!r}")
    sql = (
        repository._LATEST_CTE
        + " SELECT e.episode_id, e.reimbursement_track, e.date_of_service, e.ndc11,"
        "        CAST(julianday(substr(:cursor, 1, 10)) - julianday(e.date_of_service) AS INTEGER)"
        "          AS age_days,"
        "        l.verdict_id, l.cursor_at, l.episode_disposition, l.reimbursement_verdict_code,"
        "        l.rebate_verdict_code, l.reimbursement_variance_cents, l.rebate_variance_cents,"
        "        l.reopened_from"
        "   FROM latest l JOIN episode e ON e.episode_id = l.episode_id"
        "  WHERE l.rn = 1 AND l.episode_disposition = 'CLOSED'"
        f" ORDER BY {repository.QUEUE_ORDERINGS[order_by]}"
    )
    params: dict[str, Any] = {"cursor": cursor}
    if limit is not None:
        sql += " LIMIT :limit"
        params["limit"] = limit
    return [QueueRow.from_row(row) for row in conn.execute(sql, params).fetchall()]


def _reasons_for_episodes(
    conn: sqlite3.Connection, cursor: str, episode_ids: Sequence[str]
) -> dict[str, list[str]]:
    """Reason codes for a page of episodes, in one query rather than one per row."""
    if not episode_ids:
        return {}
    names = [f"e{index}" for index in range(len(episode_ids))]
    params: dict[str, Any] = {"cursor": cursor}
    params.update(dict(zip(names, episode_ids)))
    filter_sql = " AND v.episode_id IN (" + ",".join(f":{name}" for name in names) + ")"
    rows = conn.execute(
        repository._latest_cte(filter_sql)
        + " SELECT l.episode_id, r.reason_code FROM latest l"
        "   JOIN verdict_reason r ON r.verdict_id = l.verdict_id"
        "  WHERE l.rn = 1 ORDER BY l.episode_id, r.ordinal",
        params,
    ).fetchall()
    out: dict[str, list[str]] = {}
    for row in rows:
        out.setdefault(row["episode_id"], []).append(row["reason_code"])
    return out


def episode_detail(conn: sqlite3.Connection, episode_id: str, cursor: str) -> dict[str, Any] | None:
    """One episode as the screen shows it: identity, verdict, money, reasons, flags, lineage."""
    detail = repository.episode_detail(conn, episode_id, cursor)
    if detail is None:
        return None
    episode = detail["episode"]
    verdict = detail["verdict"]

    payload: dict[str, Any] = {
        "episode_id": episode.episode_id,
        "track": episode.reimbursement_track.value,
        "ndc11": episode.ndc11,
        "date_of_service": episode.date_of_service,
        "quantity_milli": episode.quantity_milli,
        "pharmacy_npi": episode.pharmacy_npi,
        "rx_number": episode.rx_number,
        "fill_number": episode.fill_number,
        "clm01": episode.clm01,
        "billing_provider_npi": episode.billing_provider_npi,
        "payer_id": episode.pbm_id or episode.medical_payer_id,
        "is_340b_flagged": episode.is_340b_flagged,
        "age_days": detail["age_days"],
        "cursor": cursor,
        "verdict": None,
    }
    if verdict is None:
        # A real state, not an error: at this cursor the episode exists and has not been evaluated.
        return payload

    payload["verdict"] = {
        "reimbursement_verdict": verdict.reimbursement_verdict_code,
        "rebate_verdict": verdict.rebate_verdict_code,
        "episode_disposition": verdict.episode_disposition.value,
        "reimbursement_disposition": verdict.reimbursement_disposition.value,
        "rebate_disposition": (
            verdict.rebate_disposition.value if verdict.rebate_disposition else None
        ),
        "expected_reimbursement_cents": verdict.expected_reimbursement_cents,
        "received_reimbursement_cents": verdict.received_reimbursement_cents,
        "reimbursement_variance_cents": verdict.reimbursement_variance_cents,
        "expected_rebate_cents": verdict.expected_rebate_cents,
        "received_rebate_cents": verdict.received_rebate_cents,
        "rebate_variance_cents": verdict.rebate_variance_cents,
        "reason_codes": [code.value for code in verdict.reasons],
        "cross_track_flags": [flag.code for flag in verdict.cross_track_flags],
        "reopened_from": verdict.reopened_from.value if verdict.reopened_from else None,
        "previously_closed_at": verdict.previously_closed_at,
        "cursor_at": verdict.cursor_at,
    }
    # Lineage: every citation reaches the verbatim source row it came from.
    payload["evidence"] = [
        {
            "role": row.role.value,
            "source_system": row.source_system.value if row.source_system else None,
            "source_record_id": row.source_record_id,
            "source_file": row.source_file,
            "source_line_no": row.source_line_no,
            "received_at": row.received_at,
            "payload": row.payload,
        }
        for row in verdict.evidence
    ]
    payload["cash_allocations"] = [
        {
            "allocated_cents": row.allocated_cents,
            "basis": row.basis.value,
            "caused_by_received_at": row.caused_by_received_at,
        }
        for row in repository.cash_allocations_for_episode(conn, episode_id, cursor)
    ]
    return payload


def episode_trace(conn: sqlite3.Connection, episode_id: str) -> dict[str, Any]:
    """One claim end to end: every verdict it has held, and every key that resolved to it.

    This is the walkthrough view. The debrief asks for one claim traced end to end, and the audit
    trail across time plus the crosswalk downward is exactly that trace.
    """
    history = [
        {
            "cursor_at": verdict.cursor_at,
            "episode_disposition": verdict.episode_disposition.value,
            "reimbursement_verdict": verdict.reimbursement_verdict_code,
            "rebate_verdict": verdict.rebate_verdict_code,
            "reason_codes": [code.value for code in verdict.reasons],
            "cross_track_flags": [flag.code for flag in verdict.cross_track_flags],
            "reimbursement_variance_cents": verdict.reimbursement_variance_cents,
            "rebate_variance_cents": verdict.rebate_variance_cents,
            "reopened_from": verdict.reopened_from.value if verdict.reopened_from else None,
        }
        for verdict in repository.verdict_history(conn, episode_id)
    ]
    crosswalk = [
        {
            "key_type": row.key_type.value,
            "key_value": row.key_value,
            "first_seen_at": row.first_seen_at,
            "resolved_from_norm_id": row.resolved_from_norm_id,
        }
        for row in repository.crosswalk_for_episode(conn, episode_id)
    ]
    records = [
        {
            "norm_id": row["norm_id"],
            "record_kind": row["record_kind"],
            "source_system": row["source_system"],
            "received_at": row["received_at"],
            "amount_cents": row["amount_cents"],
            "status_code": row["status_code"],
        }
        for row in conn.execute(
            "SELECT DISTINCT n.norm_id, n.record_kind, n.source_system, n.received_at,"
            "       n.amount_cents, n.status_code"
            "  FROM crosswalk_key k JOIN normalized_record n ON n.norm_id = k.resolved_from_norm_id"
            " WHERE k.episode_id = ? ORDER BY n.received_at, n.norm_id",
            (episode_id,),
        )
    ]
    return {
        "episode_id": episode_id,
        "verdict_history": history,
        "crosswalk_keys": crosswalk,
        "records": records,
    }


def feed_exceptions(conn: sqlite3.Connection, cursor: str) -> dict[str, Any]:
    """D-1, D-3, D-4 and D-7 — exceptions that belong to ingestion rather than to any episode.

    They are queries, not stored flags, which is why they can be asked at any cursor.
    """
    return {
        "cursor": cursor,
        "orphan_deposits": [
            {
                "norm_id": row.norm_id,
                "amount_cents": row.amount_cents,
                "received_at": row.received_at,
                "ach_trace_number": row.ach_trace_number,
            }
            for row in repository.orphan_deposits(conn, cursor)
        ],
        "orphan_rebates": [
            {
                "norm_id": row.norm_id,
                "record_kind": row.record_kind.value,
                "received_at": row.received_at,
                "park_reason": row.park_reason.value,
            }
            for row in repository.orphan_rebates(conn, cursor)
        ],
        "allocation_residuals": [
            {
                "allocated_cents": row.allocated_cents,
                "bank_norm_id": row.bank_norm_id,
                "caused_by_received_at": row.caused_by_received_at,
            }
            for row in repository.allocation_residuals(conn, cursor)
        ],
        "duplicate_deliveries": [
            {"source_system": system, "payload_sha256": digest, "deliveries": count}
            for system, digest, count in repository.duplicate_deliveries(conn)
        ],
        "parked": [
            {
                "norm_id": row.norm_id,
                "record_kind": row.record_kind.value,
                "park_reason": row.park_reason.value,
                "received_at": row.received_at,
            }
            for row in _unresolved_parked(conn, cursor)
        ],
    }


def _unresolved_parked(conn: sqlite3.Connection, cursor: str):
    from recon.domain.models import ParkedRecord

    rows = conn.execute(
        "SELECT p.parked_id, p.norm_id, p.raw_id, p.record_kind, p.received_at, p.park_reason"
        "  FROM parked_record p"
        "  LEFT JOIN parked_record_resolution r"
        "    ON r.parked_id = p.parked_id AND r.resolved_by_received_at <= :cursor"
        " WHERE p.received_at <= :cursor AND r.parked_id IS NULL"
        " ORDER BY p.parked_id",
        {"cursor": cursor},
    ).fetchall()
    return [ParkedRecord.from_row(row) for row in rows]
