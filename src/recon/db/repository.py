"""Named queries, one per thing the other groups need.

Plain functions taking a connection first.  No repository classes, no unit of work, no
query builder — those abstractions earn their keep when there are many storage
backends or many transaction shapes, and here there is one of each.

Three properties of this surface are deliberate.

**There is no update and no delete.**  Not "we avoid them": there is no function whose
name begins with ``update_`` or ``delete_``, and a test asserts it by introspection.
The raw, normalized, episode and verdict tables have triggers that abort such a
statement anyway; this is the same rule stated where a caller would look for it.

**The cursor predicate is mandatory on every derived lookup.**  ``resolve_keys`` and
``parked_matching`` take ``cursor`` as a required positional argument.  Omitting it
would let a crosswalk key established on 18 March resolve a lookup performed at
cursor 10 March, so "what did we believe on 10 March" would come back built on
evidence we did not have.  That is not an optimisation; it is the difference between
replay and fiction.

**``append_verdict`` writes four tables in one transaction.**  A verdict row whose
reasons failed to insert is a defect with no explanation attached — exactly the
decide-then-explain drift the design exists to prevent, reappearing at the
persistence layer.  Enum-valued arguments are validated *before* any SQL runs, so a
bad reason code cannot leave a partial write behind.

Aging appears only here, computed in SQL as ``cursor - date_of_service``.  It is
never a column.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Iterator, Sequence

from recon.domain import verdicts as verdict_codes
from recon.domain.enums import (
    AllocationBasis,
    CrossTrackFlag,
    Disposition,
    EvidenceRole,
    KeyType,
    ParkReason,
    QuarantineReason,
    ReasonCode,
    RecordKind,
    ReimbursementTrack,
    SourceSystem,
    TrackScope,
)
from recon.domain.models import (
    CashAllocation,
    CrosswalkEntry,
    Episode,
    EvidenceRow,
    NormalizedRecord,
    ParkedRecord,
    QuarantinedRecord,
    QueueRow,
    RawRecord,
    Resolution,
    Verdict,
    VerdictReason,
    WorkItem,
)

__all__ = [
    # writes
    "insert_ingest_batch",
    "insert_raw_records",
    "insert_normalized_record",
    "insert_crosswalk_keys",
    "insert_episode",
    "append_verdict",
    "park_record",
    "resolve_parked",
    "quarantine_record",
    "insert_cash_allocations",
    "create_work_item",
    "write_meta",
    # hot queries
    "resolve_keys",
    "latest_verdict",
    "latest_verdicts",
    "parked_matching",
    # reads
    "exception_queue",
    "pending_queue",
    "episode_detail",
    "verdict_history",
    "evidence_for_verdict",
    "raw_record",
    "normalized_record",
    "crosswalk_for_episode",
    "cash_allocations_for_episode",
    "work_items_for_episode",
    # feed-level exceptions, as queries rather than stored flags
    "orphan_deposits",
    "orphan_rebates",
    "allocation_residuals",
    "duplicate_deliveries",
    "quarantined",
    # meta
    "read_meta",
    "batch_for_file_sha256",
    "QUEUE_ORDERINGS",
]


# --- internals -------------------------------------------------------------


@contextmanager
def _atomic(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Run a write inside a transaction, joining one the caller already opened.

    ``connection.transaction`` is strict about nesting because user code that nests it
    is almost always a mistake.  Repository writes are the legitimate exception: they
    are the inner scope, and a caller batching several of them owns the outer one.
    """
    if conn.in_transaction:
        yield conn
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def _enum(enum_cls, value, field_name: str):
    """Coerce and validate an enum-valued argument, naming the field on failure."""
    try:
        return enum_cls(value)
    except ValueError:
        allowed = ", ".join(str(member.value) for member in enum_cls)
        raise ValueError(
            f"{field_name}={value!r} is not a valid {enum_cls.__name__}; expected one of {allowed}"
        ) from None


def _placeholders(count: int) -> str:
    return ",".join("?" * count)


#: Whitelisted queue orderings.  A free-form ``ORDER BY`` string from a caller would
#: be an injection point, and prioritisation is a deterministic sort rather than a
#: judgement, so the set of legitimate sorts is small and closed.
QUEUE_ORDERINGS: dict[str, str] = {
    "age_desc": "age_days DESC, episode_id",
    "age_asc": "age_days ASC, episode_id",
    "variance_desc": (
        "(ABS(reimbursement_variance_cents) + ABS(rebate_variance_cents)) DESC, episode_id"
    ),
    # Reopened-from-closed outranks never-paid: a claim that was settled and came
    # undone is a different kind of problem from one that never settled.
    "reopened_first": (
        "CASE reopened_from WHEN 'CLOSED' THEN 0 WHEN 'PENDING' THEN 1 ELSE 2 END, "
        "age_days DESC, episode_id"
    ),
    "episode_id": "episode_id",
}

def _latest_cte(episode_filter: str = "") -> str:
    """The "latest verdict per episode at a cursor" window, as a CTE.

    ``ix_verdict_latest`` is ordered ``(episode_id, cursor_at DESC, verdict_id DESC)``,
    which is exactly the window's partition-and-order, so SQLite walks the index
    instead of sorting the table.
    """
    return f"""
WITH latest AS (
  SELECT v.*,
         ROW_NUMBER() OVER (
           PARTITION BY v.episode_id
           ORDER BY v.cursor_at DESC, v.verdict_id DESC
         ) AS rn
    FROM verdict v
   WHERE v.cursor_at <= :cursor{episode_filter}
)
"""


_LATEST_CTE = _latest_cte()


# --- writes ----------------------------------------------------------------


def insert_ingest_batch(
    conn: sqlite3.Connection,
    *,
    source_file: str,
    source_system: SourceSystem | str,
    file_sha256: str,
    record_count: int,
    loaded_at: str,
) -> int:
    system = _enum(SourceSystem, source_system, "source_system")
    with _atomic(conn) as tx:
        cursor = tx.execute(
            "INSERT INTO ingest_batch(source_file, source_system, file_sha256, record_count, loaded_at)"
            " VALUES (?,?,?,?,?)",
            (source_file, str(system), file_sha256, record_count, loaded_at),
        )
        return int(cursor.lastrowid)


def insert_raw_records(conn: sqlite3.Connection, rows: Sequence[dict]) -> int:
    """Bulk-insert raw rows.  One ``executemany`` inside one transaction.

    Raw rows are never deduplicated: the raw layer stores exactly what arrived, twice
    if it arrived twice.
    """
    if not rows:
        return 0
    payload = [
        (
            row["batch_id"],
            str(_enum(SourceSystem, row["source_system"], "source_system")),
            row.get("source_record_id"),
            row["source_line_no"],
            row["payload"],
            row["payload_sha256"],
            row["received_at"],
        )
        for row in rows
    ]
    with _atomic(conn) as tx:
        tx.executemany(
            "INSERT INTO raw_record"
            " (batch_id, source_system, source_record_id, source_line_no, payload,"
            "  payload_sha256, received_at)"
            " VALUES (?,?,?,?,?,?,?)",
            payload,
        )
    return len(payload)


_NORM_COLUMNS = (
    "raw_id",
    "parent_norm_id",
    "record_kind",
    "source_system",
    "adapter_version",
    "received_at",
    "idempotency_key",
    "pharmacy_npi",
    "provider_npi",
    # Verbatim only.  There is deliberately no normalized twin (Decision A23).
    "rx_number",
    "fill_number",
    "ndc11",
    "date_of_service",
    "clm01",
    "clp07",
    "trn02",
    "ach_trace_number",
    "allocation_code",
    "authorization_number",
    "payer_id",
    "amount_cents",
    "quantity_milli",
    "status_code",
    "canonical",
)


def insert_normalized_record(conn: sqlite3.Connection, **fields) -> int:
    unknown = set(fields) - set(_NORM_COLUMNS)
    if unknown:
        raise ValueError(f"unknown normalized_record columns: {', '.join(sorted(unknown))}")
    fields["record_kind"] = str(_enum(RecordKind, fields["record_kind"], "record_kind"))
    fields["source_system"] = str(_enum(SourceSystem, fields["source_system"], "source_system"))
    values = [fields.get(column) for column in _NORM_COLUMNS]
    with _atomic(conn) as tx:
        cursor = tx.execute(
            f"INSERT INTO normalized_record ({','.join(_NORM_COLUMNS)})"
            f" VALUES ({_placeholders(len(_NORM_COLUMNS))})",
            values,
        )
        return int(cursor.lastrowid)


def insert_crosswalk_keys(conn: sqlite3.Connection, rows: Sequence[dict]) -> int:
    """Register resolved keys.

    ``first_seen_at`` must be the ``received_at`` of the record that established the
    key, never the time of the run.  It is what the cursor predicate filters on.
    """
    if not rows:
        return 0
    payload = []
    for row in rows:
        key_type = _enum(KeyType, row["key_type"], "key_type")
        if row.get("episode_id") is None and row.get("remittance_norm_id") is None:
            raise ValueError(
                f"crosswalk key {key_type}/{row['key_value']!r} resolves to neither an "
                "episode nor a remittance; one of the two is required"
            )
        payload.append(
            (
                str(key_type),
                row["key_value"],
                row.get("episode_id"),
                row.get("remittance_norm_id"),
                row["resolved_from_norm_id"],
                row["first_seen_at"],
            )
        )
    with _atomic(conn) as tx:
        tx.executemany(
            "INSERT INTO crosswalk_key"
            " (key_type, key_value, episode_id, remittance_norm_id, resolved_from_norm_id,"
            "  first_seen_at)"
            " VALUES (?,?,?,?,?,?)",
            payload,
        )
    return len(payload)


_EPISODE_COLUMNS = (
    "episode_id",
    "reimbursement_track",
    "anchor_norm_id",
    "pharmacy_npi",
    "ndc11",
    "date_of_service",
    "quantity_milli",
    "prescriber_npi",
    "rx_number",
    "fill_number",
    "pbm_id",
    "cardholder_id",
    "clm01",
    "medical_payer_id",
    "billing_provider_npi",
    "is_340b_flagged",
    "covered_entity_id",
    "created_from_received_at",
)


def insert_episode(conn: sqlite3.Connection, **fields) -> str:
    unknown = set(fields) - set(_EPISODE_COLUMNS)
    if unknown:
        raise ValueError(f"unknown episode columns: {', '.join(sorted(unknown))}")
    fields["reimbursement_track"] = str(
        _enum(ReimbursementTrack, fields["reimbursement_track"], "reimbursement_track")
    )
    fields["is_340b_flagged"] = int(bool(fields.get("is_340b_flagged", 0)))
    values = [fields.get(column) for column in _EPISODE_COLUMNS]
    with _atomic(conn) as tx:
        tx.execute(
            f"INSERT INTO episode ({','.join(_EPISODE_COLUMNS)})"
            f" VALUES ({_placeholders(len(_EPISODE_COLUMNS))})",
            values,
        )
    return fields["episode_id"]


def append_verdict(
    conn: sqlite3.Connection,
    *,
    episode_id: str,
    cursor_at: str,
    computed_at: str,
    engine_version: str,
    reference_fingerprint: str,
    episode_disposition: Disposition | str,
    reimbursement_disposition: Disposition | str,
    rebate_disposition: Disposition | str | None,
    reimbursement_verdict_code: str,
    rebate_verdict_code: str,
    expected_reimbursement_cents: int = 0,
    received_reimbursement_cents: int = 0,
    reimbursement_variance_cents: int = 0,
    expected_rebate_cents: int = 0,
    received_rebate_cents: int = 0,
    rebate_variance_cents: int = 0,
    reopened_from: Disposition | str | None = None,
    previously_closed_at: str | None = None,
    reopened_on: str | None = None,
    reasons: Sequence[tuple[ReasonCode | str, TrackScope | str]] = (),
    cross_track_flags: Sequence[CrossTrackFlag | str] = (),
    evidence: Sequence[tuple[int, int, EvidenceRole | str]] = (),
) -> int:
    """Append one verdict and all of its children, atomically.

    Args:
        reasons: ``(reason_code, track)`` pairs, in the order they should be read.
        cross_track_flags: ``CrossTrackFlag`` members or their ``"X-1"`` wire form.
        evidence: ``(norm_id, raw_id, role)`` triples.

    Returns:
        The new ``verdict_id``.

    Raises:
        ValueError: on any invalid vocabulary value, or if ``rebate_disposition`` and
            ``rebate_verdict_code`` disagree about whether the rebate track exists.
            Raised before any statement runs.
    """
    episode_disp = _enum(Disposition, episode_disposition, "episode_disposition")
    reimbursement_disp = _enum(Disposition, reimbursement_disposition, "reimbursement_disposition")
    rebate_disp = (
        None
        if rebate_disposition is None
        else _enum(Disposition, rebate_disposition, "rebate_disposition")
    )

    if reimbursement_verdict_code not in verdict_codes.REIMBURSEMENT_CODES:
        raise ValueError(
            f"reimbursement_verdict_code={reimbursement_verdict_code!r} is not one of the "
            f"{len(verdict_codes.REIMBURSEMENT_CODES)} live reimbursement codes"
        )
    if rebate_verdict_code not in verdict_codes.REBATE_CODES:
        raise ValueError(
            f"rebate_verdict_code={rebate_verdict_code!r} is not one of the "
            f"{len(verdict_codes.REBATE_CODES)} live rebate codes"
        )

    # C-00 means the rebate track is absent, which is not the same as the track being
    # finished.  NULL disposition, not CLOSED.
    track_absent = rebate_verdict_code == verdict_codes.REBATE_TRACK_ABSENT
    if track_absent and rebate_disp is not None:
        raise ValueError(
            "rebate_verdict_code='C-00' means the 340B track is absent, so "
            f"rebate_disposition must be NULL; got {rebate_disp!r}"
        )
    if not track_absent and rebate_disp is None:
        raise ValueError(
            f"rebate_verdict_code={rebate_verdict_code!r} means the 340B track exists, "
            "so rebate_disposition must not be NULL"
        )

    reopened = None if reopened_from is None else _enum(Disposition, reopened_from, "reopened_from")
    if reopened is Disposition.EXCEPTION:
        raise ValueError("reopened_from must be CLOSED or PENDING; an EXCEPTION was never closed")

    reason_rows = [
        (
            ordinal,
            str(_enum(ReasonCode, code, "reason_code")),
            str(_enum(TrackScope, track, "track")),
        )
        for ordinal, (code, track) in enumerate(reasons)
    ]
    flag_rows = [
        _enum(CrossTrackFlag, str(flag).replace("-", "_"), "flag_code") for flag in cross_track_flags
    ]
    evidence_rows = [
        (ordinal, norm_id, raw_id, str(_enum(EvidenceRole, role, "role")))
        for ordinal, (norm_id, raw_id, role) in enumerate(evidence)
    ]

    with _atomic(conn) as tx:
        cursor = tx.execute(
            "INSERT INTO verdict"
            " (episode_id, cursor_at, computed_at, engine_version, reference_fingerprint,"
            "  episode_disposition, reimbursement_disposition, rebate_disposition,"
            "  reimbursement_verdict_code, rebate_verdict_code,"
            "  expected_reimbursement_cents, received_reimbursement_cents,"
            "  reimbursement_variance_cents, expected_rebate_cents, received_rebate_cents,"
            "  rebate_variance_cents, reopened_from, previously_closed_at, reopened_on)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                episode_id,
                cursor_at,
                computed_at,
                engine_version,
                reference_fingerprint,
                str(episode_disp),
                str(reimbursement_disp),
                None if rebate_disp is None else str(rebate_disp),
                reimbursement_verdict_code,
                rebate_verdict_code,
                expected_reimbursement_cents,
                received_reimbursement_cents,
                reimbursement_variance_cents,
                expected_rebate_cents,
                received_rebate_cents,
                rebate_variance_cents,
                None if reopened is None else str(reopened),
                previously_closed_at,
                reopened_on,
            ),
        )
        verdict_id = int(cursor.lastrowid)
        if reason_rows:
            tx.executemany(
                "INSERT INTO verdict_reason(verdict_id, ordinal, reason_code, track)"
                " VALUES (?,?,?,?)",
                [(verdict_id, ordinal, code, track) for ordinal, code, track in reason_rows],
            )
        if flag_rows:
            tx.executemany(
                "INSERT INTO verdict_cross_track_flag(verdict_id, flag_code) VALUES (?,?)",
                [(verdict_id, flag.code) for flag in flag_rows],
            )
        if evidence_rows:
            tx.executemany(
                "INSERT INTO verdict_evidence(verdict_id, ordinal, norm_id, raw_id, role)"
                " VALUES (?,?,?,?,?)",
                [
                    (verdict_id, ordinal, norm_id, raw_id, role)
                    for ordinal, norm_id, raw_id, role in evidence_rows
                ],
            )
    return verdict_id


def park_record(
    conn: sqlite3.Connection,
    *,
    norm_id: int,
    raw_id: int,
    record_kind: RecordKind | str,
    received_at: str,
    park_reason: ParkReason | str,
    keys: Sequence[tuple[KeyType | str, str]] = (),
) -> int:
    """Park an unresolvable document, recording the keys it carried.

    The keys matter as much as the park: every later arrival re-checks the pool
    backwards, and it does so by key.
    """
    kind = _enum(RecordKind, record_kind, "record_kind")
    reason = _enum(ParkReason, park_reason, "park_reason")
    key_rows = [(str(_enum(KeyType, key_type, "key_type")), value) for key_type, value in keys]
    with _atomic(conn) as tx:
        cursor = tx.execute(
            "INSERT INTO parked_record(norm_id, raw_id, record_kind, received_at, park_reason)"
            " VALUES (?,?,?,?,?)",
            (norm_id, raw_id, str(kind), received_at, str(reason)),
        )
        parked_id = int(cursor.lastrowid)
        if key_rows:
            tx.executemany(
                "INSERT INTO parked_record_key(parked_id, key_type, key_value) VALUES (?,?,?)",
                [(parked_id, key_type, value) for key_type, value in key_rows],
            )
    return parked_id


def resolve_parked(
    conn: sqlite3.Connection,
    *,
    parked_id: int,
    resolved_by_norm_id: int,
    resolved_by_received_at: str,
    resolved_to_episode_id: str | None = None,
    resolved_to_remittance_norm_id: int | None = None,
) -> None:
    """Record that a later arrival cleared a park.

    This appends a resolution row; it never deletes the park row.  "Was this parked on
    10 March?" and "was it parked on 20 March?" must both be answerable, and deleting
    the evidence would make the first question unanswerable.
    """
    if resolved_to_episode_id is None and resolved_to_remittance_norm_id is None:
        raise ValueError(
            "a park resolution must point at an episode or a remittance; both were None"
        )
    with _atomic(conn) as tx:
        tx.execute(
            "INSERT INTO parked_record_resolution"
            " (parked_id, resolved_by_norm_id, resolved_by_received_at, resolved_to_episode_id,"
            "  resolved_to_remittance_norm_id)"
            " VALUES (?,?,?,?,?)",
            (
                parked_id,
                resolved_by_norm_id,
                resolved_by_received_at,
                resolved_to_episode_id,
                resolved_to_remittance_norm_id,
            ),
        )


def quarantine_record(
    conn: sqlite3.Connection,
    *,
    raw_id: int,
    received_at: str,
    reason_code: QuarantineReason | str,
    detail: str | None = None,
) -> int:
    reason = _enum(QuarantineReason, reason_code, "reason_code")
    with _atomic(conn) as tx:
        cursor = tx.execute(
            "INSERT INTO quarantined_record(raw_id, received_at, reason_code, detail)"
            " VALUES (?,?,?,?)",
            (raw_id, received_at, str(reason), detail),
        )
        return int(cursor.lastrowid)


def insert_cash_allocations(conn: sqlite3.Connection, rows: Sequence[dict]) -> int:
    if not rows:
        return 0
    payload = [
        (
            row["bank_norm_id"],
            row.get("remittance_norm_id"),
            row.get("episode_id"),
            row["allocated_cents"],
            str(_enum(AllocationBasis, row["basis"], "basis")),
            row["caused_by_received_at"],
        )
        for row in rows
    ]
    with _atomic(conn) as tx:
        tx.executemany(
            "INSERT INTO cash_allocation"
            " (bank_norm_id, remittance_norm_id, episode_id, allocated_cents, basis,"
            "  caused_by_received_at)"
            " VALUES (?,?,?,?,?,?)",
            payload,
        )
    return len(payload)


def create_work_item(
    conn: sqlite3.Connection,
    *,
    episode_id: str,
    from_verdict_id: int,
    at_cursor: str,
    created_by: str,
    created_at: str,
    summary: str,
    recommended_action: str,
) -> int:
    """The agent's only write.  Append-only: there is no close and no update.

    The agent flags a human for attention; it never moves money and never writes a
    ledger entry.
    """
    with _atomic(conn) as tx:
        cursor = tx.execute(
            "INSERT INTO work_item"
            " (episode_id, created_at, created_by, at_cursor, from_verdict_id, summary,"
            "  recommended_action)"
            " VALUES (?,?,?,?,?,?,?)",
            (
                episode_id,
                created_at,
                created_by,
                at_cursor,
                from_verdict_id,
                summary,
                recommended_action,
            ),
        )
        return int(cursor.lastrowid)


def write_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    with _atomic(conn) as tx:
        tx.execute(
            "INSERT INTO meta(key, value) VALUES (?,?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


# --- hot query 1: key resolution ------------------------------------------


def resolve_keys(
    conn: sqlite3.Connection,
    keys: Sequence[tuple[KeyType | str, str]],
    cursor: str,
) -> list[Resolution]:
    """Resolve the keys an inbound document carries, as of ``cursor``.

    One statement per distinct key type, each served by ``ix_crosswalk_lookup`` as a
    covering index.  ``cursor`` is required: a key first seen after the cursor does not
    exist yet, and returning it would leak future knowledge into replay.
    """
    if not keys:
        return []
    grouped: dict[str, list[str]] = {}
    for key_type, key_value in keys:
        grouped.setdefault(str(_enum(KeyType, key_type, "key_type")), []).append(key_value)

    results: list[Resolution] = []
    for key_type, values in grouped.items():
        rows = conn.execute(
            "SELECT key_type, key_value, episode_id, remittance_norm_id, first_seen_at,"
            "       resolved_from_norm_id"
            "  FROM crosswalk_key"
            " WHERE key_type = ?"
            f"   AND key_value IN ({_placeholders(len(values))})"
            "   AND first_seen_at <= ?",
            [key_type, *values, cursor],
        ).fetchall()
        results.extend(Resolution.from_row(row) for row in rows)
    return results


def crosswalk_for_episode(conn: sqlite3.Connection, episode_id: str) -> list[CrosswalkEntry]:
    rows = conn.execute(
        "SELECT * FROM crosswalk_key WHERE episode_id = ? ORDER BY crosswalk_id", (episode_id,)
    ).fetchall()
    return [CrosswalkEntry.from_row(row) for row in rows]


# --- hot query 2: latest verdict ------------------------------------------


def latest_verdict(
    conn: sqlite3.Connection,
    episode_id: str,
    cursor: str,
    *,
    with_children: bool = True,
) -> Verdict | None:
    """The verdict that stood for this episode at this cursor, or ``None``."""
    row = conn.execute(
        "SELECT * FROM verdict"
        " WHERE episode_id = ? AND cursor_at <= ?"
        " ORDER BY cursor_at DESC, verdict_id DESC"
        " LIMIT 1",
        (episode_id, cursor),
    ).fetchone()
    if row is None:
        return None
    if not with_children:
        return Verdict.from_row(row)
    verdict_id = row["verdict_id"]
    return Verdict.from_row(
        row,
        reasons=_reasons_for(conn, verdict_id),
        cross_track_flags=_flags_for(conn, verdict_id),
        evidence=tuple(evidence_for_verdict(conn, verdict_id)),
    )


def latest_verdicts(
    conn: sqlite3.Connection,
    cursor: str,
    episode_ids: Sequence[str] | None = None,
) -> list[Verdict]:
    """One verdict per episode that has one at this cursor.

    Reasons are loaded in a single companion query rather than one per verdict — a
    verdict without its explanation is not a shape this layer hands out, and N+1 for
    fifteen hundred episodes is not a shape it hands out either.  Flags and evidence
    are left off; fetch them per episode with :func:`latest_verdict`.
    """
    params: dict[str, object] = {"cursor": cursor}
    episode_filter = ""
    if episode_ids is not None:
        if not episode_ids:
            return []
        names = [f"e{index}" for index in range(len(episode_ids))]
        episode_filter = " AND v.episode_id IN (" + ",".join(f":{name}" for name in names) + ")"
        params.update(dict(zip(names, episode_ids)))

    cte = _latest_cte(episode_filter)
    rows = conn.execute(
        cte + " SELECT * FROM latest WHERE rn = 1 ORDER BY episode_id", params
    ).fetchall()
    if not rows:
        return []
    reason_rows = conn.execute(
        cte
        + " SELECT r.verdict_id, r.ordinal, r.reason_code, r.track"
        "     FROM verdict_reason r"
        "     JOIN latest l ON l.verdict_id = r.verdict_id AND l.rn = 1"
        " ORDER BY r.verdict_id, r.ordinal",
        params,
    ).fetchall()
    by_verdict: dict[int, list[VerdictReason]] = {}
    for reason_row in reason_rows:
        by_verdict.setdefault(reason_row["verdict_id"], []).append(VerdictReason.from_row(reason_row))
    return [
        Verdict.from_row(row, reasons=tuple(by_verdict.get(row["verdict_id"], ())))
        for row in rows
    ]


def _reasons_for(conn: sqlite3.Connection, verdict_id: int) -> tuple[VerdictReason, ...]:
    rows = conn.execute(
        "SELECT ordinal, reason_code, track FROM verdict_reason"
        " WHERE verdict_id = ? ORDER BY ordinal",
        (verdict_id,),
    ).fetchall()
    return tuple(VerdictReason.from_row(row) for row in rows)


def _flags_for(conn: sqlite3.Connection, verdict_id: int) -> tuple[CrossTrackFlag, ...]:
    rows = conn.execute(
        "SELECT flag_code FROM verdict_cross_track_flag WHERE verdict_id = ? ORDER BY flag_code",
        (verdict_id,),
    ).fetchall()
    return tuple(CrossTrackFlag.from_code(row["flag_code"]) for row in rows)


def verdict_history(conn: sqlite3.Connection, episode_id: str) -> list[Verdict]:
    """Every verdict this episode has held, in order.  The audit trail across time."""
    rows = conn.execute(
        "SELECT * FROM verdict WHERE episode_id = ? ORDER BY cursor_at, verdict_id",
        (episode_id,),
    ).fetchall()
    return [
        Verdict.from_row(
            row,
            reasons=_reasons_for(conn, row["verdict_id"]),
            cross_track_flags=_flags_for(conn, row["verdict_id"]),
        )
        for row in rows
    ]


# --- parked pool ----------------------------------------------------------


def parked_matching(
    conn: sqlite3.Connection,
    keys: Sequence[tuple[KeyType | str, str]],
    cursor: str,
) -> list[ParkedRecord]:
    """Parked records still unresolved at ``cursor`` whose keys match.

    This is the backward half of the crosswalk: every arrival looks forward for its own
    keys and backwards through the parked pool for documents that were waiting for it.
    Served by ``ix_parked_key_lookup``; without that index it is a full scan of the pool
    on every single arrival.

    Resolution is evaluated *at the cursor*, so a record resolved on 18 March still
    reads as parked at cursor 10 March.  Replay runs the same code path in both
    directions.
    """
    if not keys:
        return []
    grouped: dict[str, list[str]] = {}
    for key_type, key_value in keys:
        grouped.setdefault(str(_enum(KeyType, key_type, "key_type")), []).append(key_value)

    found: dict[int, ParkedRecord] = {}
    for key_type, values in grouped.items():
        rows = conn.execute(
            "SELECT p.parked_id, p.norm_id, p.raw_id, p.record_kind, p.received_at, p.park_reason"
            "  FROM parked_record_key k"
            "  JOIN parked_record p ON p.parked_id = k.parked_id"
            "  LEFT JOIN parked_record_resolution r"
            "    ON r.parked_id = p.parked_id AND r.resolved_by_received_at <= ?"
            " WHERE k.key_type = ?"
            f"   AND k.key_value IN ({_placeholders(len(values))})"
            "   AND p.received_at <= ?"
            "   AND r.parked_id IS NULL",
            [cursor, key_type, *values, cursor],
        ).fetchall()
        for row in rows:
            found[row["parked_id"]] = ParkedRecord.from_row(row)
    return [found[key] for key in sorted(found)]


# --- queues ---------------------------------------------------------------


def _queue(
    conn: sqlite3.Connection,
    cursor: str,
    disposition: Disposition,
    order_by: str,
    limit: int | None,
) -> list[QueueRow]:
    if order_by not in QUEUE_ORDERINGS:
        allowed = ", ".join(sorted(QUEUE_ORDERINGS))
        raise ValueError(f"unknown queue ordering {order_by!r}; expected one of {allowed}")
    sql = (
        _LATEST_CTE
        + " SELECT e.episode_id, e.reimbursement_track, e.date_of_service, e.ndc11,"
        "        CAST(julianday(substr(:cursor, 1, 10)) - julianday(e.date_of_service) AS INTEGER)"
        "          AS age_days,"
        "        l.verdict_id, l.cursor_at, l.episode_disposition, l.reimbursement_verdict_code,"
        "        l.rebate_verdict_code, l.reimbursement_variance_cents, l.rebate_variance_cents,"
        "        l.reopened_from"
        "   FROM latest l"
        "   JOIN episode e ON e.episode_id = l.episode_id"
        "  WHERE l.rn = 1 AND l.episode_disposition = :disposition"
        f" ORDER BY {QUEUE_ORDERINGS[order_by]}"
    )
    params: dict[str, object] = {"cursor": cursor, "disposition": str(disposition)}
    if limit is not None:
        sql += " LIMIT :limit"
        params["limit"] = limit
    return [QueueRow.from_row(row) for row in conn.execute(sql, params).fetchall()]


def exception_queue(
    conn: sqlite3.Connection,
    cursor: str,
    order_by: str = "reopened_first",
    limit: int | None = None,
) -> list[QueueRow]:
    """Episodes carrying a defect at this cursor.  The exception queue is a query."""
    return _queue(conn, cursor, Disposition.EXCEPTION, order_by, limit)


def pending_queue(
    conn: sqlite3.Connection,
    cursor: str,
    order_by: str = "age_desc",
    limit: int | None = None,
) -> list[QueueRow]:
    """Episodes waiting on an external party at this cursor.  No defect; ranked by age."""
    return _queue(conn, cursor, Disposition.PENDING, order_by, limit)


# --- detail and lineage ---------------------------------------------------


def episode_detail(conn: sqlite3.Connection, episode_id: str, cursor: str) -> dict | None:
    """An episode plus the verdict standing at ``cursor``, plus its aging."""
    row = conn.execute("SELECT * FROM episode WHERE episode_id = ?", (episode_id,)).fetchone()
    if row is None:
        return None
    episode = Episode.from_row(row)
    age_row = conn.execute(
        "SELECT CAST(julianday(substr(?, 1, 10)) - julianday(date_of_service) AS INTEGER) AS age_days"
        "  FROM episode WHERE episode_id = ?",
        (cursor, episode_id),
    ).fetchone()
    return {
        "episode": episode,
        "verdict": latest_verdict(conn, episode_id, cursor),
        "age_days": age_row["age_days"],
        "cursor": cursor,
    }


def evidence_for_verdict(conn: sqlite3.Connection, verdict_id: int) -> list[EvidenceRow]:
    """Cited records with their verbatim raw payloads.

    One join, because ``verdict_evidence`` carries ``raw_id`` alongside ``norm_id``.
    This is the query that answers "trace this number back to the source record".
    """
    rows = conn.execute(
        "SELECT ev.verdict_id, ev.ordinal, ev.norm_id, ev.raw_id, ev.role,"
        "       r.source_system, r.source_record_id, r.received_at, r.payload,"
        "       r.source_line_no, b.source_file"
        "  FROM verdict_evidence ev"
        "  JOIN raw_record r ON r.raw_id = ev.raw_id"
        "  JOIN ingest_batch b ON b.batch_id = r.batch_id"
        " WHERE ev.verdict_id = ?"
        " ORDER BY ev.ordinal",
        (verdict_id,),
    ).fetchall()
    return [EvidenceRow.from_row(row) for row in rows]


def raw_record(conn: sqlite3.Connection, raw_id: int) -> RawRecord | None:
    row = conn.execute("SELECT * FROM raw_record WHERE raw_id = ?", (raw_id,)).fetchone()
    return None if row is None else RawRecord.from_row(row)


def normalized_record(conn: sqlite3.Connection, norm_id: int) -> NormalizedRecord | None:
    row = conn.execute("SELECT * FROM normalized_record WHERE norm_id = ?", (norm_id,)).fetchone()
    return None if row is None else NormalizedRecord.from_row(row)


def cash_allocations_for_episode(conn: sqlite3.Connection, episode_id: str, cursor: str):
    rows = conn.execute(
        "SELECT * FROM cash_allocation"
        " WHERE episode_id = ? AND caused_by_received_at <= ?"
        " ORDER BY allocation_id",
        (episode_id, cursor),
    ).fetchall()
    return [CashAllocation.from_row(row) for row in rows]


def work_items_for_episode(conn: sqlite3.Connection, episode_id: str) -> list[WorkItem]:
    rows = conn.execute(
        "SELECT * FROM work_item WHERE episode_id = ? ORDER BY work_item_id", (episode_id,)
    ).fetchall()
    return [WorkItem.from_row(row) for row in rows]


# --- feed-level exceptions, as queries -------------------------------------


def orphan_deposits(conn: sqlite3.Connection, cursor: str) -> list[NormalizedRecord]:
    """Bank rows that had arrived by ``cursor`` and are allocated to nothing."""
    rows = conn.execute(
        "SELECT n.* FROM normalized_record n"
        " WHERE n.record_kind = 'BANK_TRANSACTION'"
        "   AND n.received_at <= :cursor"
        "   AND NOT EXISTS ("
        "     SELECT 1 FROM cash_allocation a"
        "      WHERE a.bank_norm_id = n.norm_id AND a.caused_by_received_at <= :cursor)"
        " ORDER BY n.norm_id",
        {"cursor": cursor},
    ).fetchall()
    return [NormalizedRecord.from_row(row) for row in rows]


def orphan_rebates(conn: sqlite3.Connection, cursor: str) -> list[ParkedRecord]:
    """Rebate lines still parked at ``cursor`` — rebate money with no home."""
    rows = conn.execute(
        "SELECT p.parked_id, p.norm_id, p.raw_id, p.record_kind, p.received_at, p.park_reason"
        "  FROM parked_record p"
        "  LEFT JOIN parked_record_resolution r"
        "    ON r.parked_id = p.parked_id AND r.resolved_by_received_at <= :cursor"
        " WHERE p.record_kind IN ('REBATE_DISPENSE_LINE','REBATE_BATCH')"
        "   AND p.received_at <= :cursor"
        "   AND r.parked_id IS NULL"
        " ORDER BY p.parked_id",
        {"cursor": cursor},
    ).fetchall()
    return [ParkedRecord.from_row(row) for row in rows]


def allocation_residuals(conn: sqlite3.Connection, cursor: str) -> list[CashAllocation]:
    """Cash attributed no more precisely than "it landed in the account"."""
    rows = conn.execute(
        "SELECT * FROM cash_allocation"
        " WHERE basis = 'RESIDUAL' AND caused_by_received_at <= ?"
        " ORDER BY allocation_id",
        (cursor,),
    ).fetchall()
    return [CashAllocation.from_row(row) for row in rows]


def duplicate_deliveries(conn: sqlite3.Connection) -> list[tuple[str, str, int]]:
    """``(source_system, payload_sha256, count)`` for payloads delivered more than once.

    Detected by payload hash rather than by record id, because the defect is defined as
    an identical payload arriving twice.  Two payment *events* that happen to share a
    claim are not this; they have different payloads and different record ids.
    """
    rows = conn.execute(
        "SELECT source_system, payload_sha256, COUNT(*) AS n"
        "  FROM raw_record"
        " GROUP BY source_system, payload_sha256"
        " HAVING n > 1"
        " ORDER BY source_system, payload_sha256"
    ).fetchall()
    return [(row["source_system"], row["payload_sha256"], row["n"]) for row in rows]


def quarantined(conn: sqlite3.Connection, cursor: str) -> list[QuarantinedRecord]:
    rows = conn.execute(
        "SELECT * FROM quarantined_record WHERE received_at <= ? ORDER BY quarantine_id",
        (cursor,),
    ).fetchall()
    return [QuarantinedRecord.from_row(row) for row in rows]


# --- meta -----------------------------------------------------------------


def read_meta(conn: sqlite3.Connection, key: str | None = None):
    if key is None:
        rows = conn.execute("SELECT key, value FROM meta ORDER BY key").fetchall()
        return {row["key"]: row["value"] for row in rows}
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return None if row is None else row["value"]


def batch_for_file_sha256(conn: sqlite3.Connection, file_sha256: str):
    """Re-loading a file we already ingested is a no-op; this is how a loader knows."""
    row = conn.execute(
        "SELECT * FROM ingest_batch WHERE file_sha256 = ?", (file_sha256,)
    ).fetchone()
    if row is None:
        return None
    from recon.domain.models import IngestBatch

    return IngestBatch.from_row(row)
