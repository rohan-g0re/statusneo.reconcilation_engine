"""Connection management.

Every connection is configured identically and transactions are explicit.

**Thread rule, stated once:** one connection per unit of work.  Never share a
connection across FastAPI requests — the API layer gets a per-request dependency, not
a module global.  ``sqlite3`` connections are not safe to hand between threads, and
the failure mode is a corrupted read rather than an exception.

Two pragmas deserve a note.  ``foreign_keys`` is **per connection and off by
default**, so forgetting it on one connection silently disables every foreign key in
the schema; it is applied here so no caller has to remember.  ``journal_mode=WAL`` is
persisted in the database file rather than per connection, but setting it every time
is harmless and makes a freshly created file behave like an existing one.

There is deliberately no ``detect_types`` and no ``register_adapter`` for ``date``,
``datetime`` or ``Decimal``.  Dates are TEXT by our own convention and money is
``int``; the standard library's date adapters are deprecated from Python 3.12 and we
have no use for them.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

__all__ = ["connect", "transaction", "open_db", "MIN_SQLITE_VERSION"]

#: ``STRICT`` tables landed in SQLite 3.37.
MIN_SQLITE_VERSION = (3, 37, 0)


def _require_sqlite_version() -> None:
    if sqlite3.sqlite_version_info < MIN_SQLITE_VERSION:
        required = ".".join(str(part) for part in MIN_SQLITE_VERSION)
        raise RuntimeError(
            f"this schema uses STRICT tables and needs SQLite >= {required}; "
            f"this interpreter is linked against {sqlite3.sqlite_version}"
        )


def connect(path: Path | str) -> sqlite3.Connection:
    """Open a configured connection.

    ``:memory:`` is accepted and gets the same pragmas minus WAL, which a temporary
    database does not support.

    Raises:
        RuntimeError: if the linked SQLite is too old for ``STRICT`` tables, or if the
            parent directory does not exist — a clear message beats
            ``unable to open database file``.
    """
    _require_sqlite_version()

    in_memory = str(path) == ":memory:"
    if not in_memory:
        path = Path(path)
        if not path.parent.exists():
            raise RuntimeError(
                f"cannot open database {path}: the directory {path.parent} does not exist. "
                "Create it, or point RECON_DB_PATH somewhere that does."
            )

    conn = sqlite3.connect(str(path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if not in_memory:
        conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Explicit transaction: ``BEGIN IMMEDIATE``, then commit or roll back.

    ``IMMEDIATE`` takes the write lock up front instead of starting as a reader and
    failing to upgrade halfway through, which under concurrency is the difference
    between a clean retry and a half-written verdict.

    Nesting raises rather than silently joining the outer scope.  SQLite has no nested
    transactions without savepoints, and a silent partial commit in an append-only log
    is exactly the bug this codebase cannot afford.
    """
    if conn.in_transaction:
        raise RuntimeError(
            "transaction() is already open on this connection. SQLite has no nested "
            "transactions; a nested block would commit the outer scope on exit. "
            "Restructure so the outermost caller owns the transaction."
        )
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def open_db(settings, profile=None) -> sqlite3.Connection:
    """Resolve the database path from settings, connect, and ensure the schema."""
    from recon.db import migrate

    path = settings.db_path if profile is None else settings.for_profile(profile).db_path
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    migrate.ensure_schema(conn)
    return conn
