"""Schema application: one SQL file, one version stamp, rebuild instead of ALTER.

The database is a derived artefact.  It is gitignored, it carries load wall-clocks
that make it non-reproducible byte for byte anyway, and every row in it can be
reconstructed by replaying the immutable feeds.  Writing reversible migration scripts
for a throwaway artefact is ceremony; a real deployment would need them, and that gap
is named in the README rather than pretended away.

So: if ``PRAGMA user_version`` is 0, apply ``schema.sql``.  If it matches, no-op.  If
it is behind, refuse and name the rebuild command.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from recon import config

__all__ = ["SCHEMA_PATH", "schema_sql", "ensure_schema", "rebuild", "stamp_meta", "read_meta_row"]

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


def schema_sql() -> str:
    return SCHEMA_PATH.read_text(encoding="utf-8")


def _user_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


class SchemaVersionMismatch(RuntimeError):
    """The database on disk was stamped by a different build than this one.

    A ``RuntimeError`` subclass rather than a new exception hierarchy, so every existing
    ``except RuntimeError`` keeps catching it exactly as before — this only makes the case
    *nameable*, it does not reclassify it.

    Naming it is worth doing because this is the one failure a person meets before they
    have done anything wrong: the database is a derived artefact and the repository ships
    with stale ones, so the first run after any schema change lands here.  The message has
    always said precisely how to fix it; the API had no way to tell this apart from a bug
    and answered ``500 Internal Server Error``, so the reader saw none of it.
    """

    def __init__(self, found: int, expected: int) -> None:
        self.found = found
        self.expected = expected
        super().__init__(
            f"database is at schema version {found}, this build expects {expected}. "
            "The database is a derived artefact: delete it and re-load the feeds "
            "(recon.db.migrate.rebuild, or `recon load --rebuild`)."
        )


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Apply the schema to a fresh database, or verify an existing one.

    Raises:
        SchemaVersionMismatch: if the database was stamped by an older (or newer) schema
            version, naming the rebuild path.  A silent mismatch would show up later
            as a missing column halfway through an ingest.
        RuntimeError: if ``schema.sql`` and ``config.SCHEMA_VERSION`` disagree.
    """
    version = _user_version(conn)
    if version == config.SCHEMA_VERSION:
        return
    if version != 0:
        raise SchemaVersionMismatch(version, config.SCHEMA_VERSION)
    conn.executescript(schema_sql())
    applied = _user_version(conn)
    if applied != config.SCHEMA_VERSION:
        raise RuntimeError(
            f"schema.sql stamped user_version={applied} but config.SCHEMA_VERSION is "
            f"{config.SCHEMA_VERSION}; the two must be bumped together."
        )


def rebuild(path: Path | str) -> None:
    """Delete a database and its WAL siblings.

    The ``-wal`` and ``-shm`` files matter: removing only the ``.sqlite`` leaves a
    write-ahead log that SQLite will happily replay into the new file.
    """
    path = Path(path)
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(str(path) + suffix)
        if candidate.exists():
            candidate.unlink()


def stamp_meta(conn: sqlite3.Connection, settings, reference_fingerprint: str) -> None:
    """Record what produced this database, so drift is detectable rather than silent."""
    rows = {
        "schema_version": str(config.SCHEMA_VERSION),
        "master_seed": str(settings.master_seed),
        "profile": str(settings.profile),
        "reference_fingerprint": reference_fingerprint,
        "window_start": settings.window_start.isoformat(),
        "window_end": settings.window_end.isoformat(),
        "adapter_version": settings.adapter_version,
        "engine_version": settings.engine_version,
    }
    conn.executemany(
        "INSERT INTO meta(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        sorted(rows.items()),
    )


def read_meta_row(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return None if row is None else row["value"]
