"""FastAPI transport.  Thin by design: it parses, delegates, and serialises.

Every endpoint takes a ``cursor`` and nothing stateful happens between requests.  That is the whole
architecture showing through at the HTTP layer — "what did we believe on 31 March?" is a query
parameter, not a feature, so there is no replay mode to build and no snapshot to maintain.

**No endpoint computes anything.**  Amounts, variances, dispositions and priorities are read back
from the verdict log exactly as the deterministic engine wrote them.

The database is opened **per request**.  ``sqlite3`` connections are not safe to hand between
threads and the failure mode is a corrupted read rather than an exception, so a module-level
connection would be a bug waiting for concurrency.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any

from recon import config
from recon.api import service
from recon.config import Profile, Settings, load_settings

__all__ = ["create_app", "build_dataset"]


def _require_fastapi():
    try:
        from fastapi import FastAPI  # noqa: F401
    except ModuleNotFoundError as exc:  # pragma: no cover - environment dependent
        raise RuntimeError(
            "the API layer needs the 'api' extra: pip install -e '.[api]'. The deterministic core "
            "has no third-party dependencies at all, which is why this is an optional extra rather "
            "than a base requirement."
        ) from exc


def build_dataset(settings: Settings, *, rebuild: bool = False) -> dict[str, Any]:
    """Generate the feeds, load them, reconcile, and return what happened.

    This is the regenerate control's implementation, and it is deliberately the *whole* pipeline
    rather than a reset: the dataset is a derived artefact, so rebuilding it from the seed is both
    the cheapest way to get a clean state and a continuous proof that it is reproducible.
    """
    from recon.db import connection, migrate
    from recon.engine import run as engine
    from recon.generators import orchestrator
    from recon.ingest import pipeline

    if rebuild:
        migrate.rebuild(settings.db_path)

    generation = orchestrator.generate(settings)
    hashes = orchestrator.write_outputs(settings, generation)

    Path(settings.db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = connection.connect(settings.db_path)
    migrate.ensure_schema(conn)
    from recon.reference.fingerprint import fingerprint

    migrate.stamp_meta(conn, settings, fingerprint())
    try:
        stats = pipeline.load_feeds(conn, settings.feeds_dir())
        pipeline.ingest(conn, stats=stats)

        # Evaluate at a series of cursors across the window, not only at the end.
        #
        # One evaluation at the final cursor produces one verdict per episode, which is enough to
        # populate the queues and nothing else: the audit trail is a single row and ``reopened_from``
        # is never set, because nothing ever *moved*. Both are real features of the model — an
        # episode whose disposition falls backwards when new information lands is the whole reason
        # reopening is a flag — and a dataset that never exercises them cannot demonstrate them.
        #
        # Monthly steps are enough to catch the transitions that matter (a claim settles, then a
        # clawback arrives) while keeping the rebuild quick enough to sit behind a button.
        cursors = _monthly_cursors(settings)
        for cursor in cursors:
            engine.run_all(conn, cursor)
        results = engine.run_all(conn, settings.max_cursor)
        return {
            "profile": str(settings.profile),
            "episodes": len(results),
            "cursors_evaluated": len(cursors) + 1,
            "feed_sha256": hashes,
            "ingest": stats.as_dict(),
        }
    finally:
        conn.close()


def _monthly_cursors(settings: Settings) -> list[str]:
    """One cursor per month across the generation window, in order.

    Ordered ascending on purpose: the engine compares each new verdict against the episode's previous
    one to decide whether the disposition moved backwards, so evaluating out of order would record
    reopenings that never happened.
    """
    from datetime import date

    cursors: list[str] = []
    year, month = settings.window_start.year, settings.window_start.month
    while True:
        month += 1
        if month > 12:
            year, month = year + 1, 1
        moment = date(year, month, 1)
        if moment >= settings.window_end:
            break
        cursors.append(config.cursor_from_date(moment))
    return cursors


def create_app(settings: Settings | None = None):
    """Build the ASGI app.

    ``settings`` is injectable so tests can point the whole API at a temporary directory without
    environment variables or monkeypatching.
    """
    _require_fastapi()
    from fastapi import FastAPI, HTTPException, Query
    from fastapi.middleware.cors import CORSMiddleware

    resolved = settings or load_settings(Profile.DEMO)

    app = FastAPI(
        title="Post-Claim Pharmacy Financial Reconciliation",
        version=config.ENGINE_VERSION,
        description=(
            "Read-only projections of a deterministic reconciliation engine. Every number here was "
            "computed in Python and is returned verbatim; nothing on this surface calculates."
        ),
    )
    # The front end is served from a different origin in development.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )
    app.state.settings = resolved

    @contextmanager
    def open_conn():
        """One connection per request, opened and closed inside the handler's own thread.

        Deliberately **not** a FastAPI ``Depends`` generator, and the reason is a bug worth
        recording. FastAPI runs a sync endpoint in a threadpool, and drives a sync *generator*
        dependency separately -- so the connection was created on one worker thread and used on
        another, which sqlite3 refuses outright:

            SQLite objects created in a thread can only be used in that same thread

        It surfaced as intermittent 500s that appeared only when the front end issued several
        requests at once, and vanished on retry. Opening the connection inside the handler body
        keeps creation and use on the same thread by construction.

        ``check_same_thread`` stays at its default on purpose. Disabling it would have silenced
        this without fixing it, and that guard is the only reason it was caught at all.
        """
        from recon.db import connection, migrate

        current: Settings = app.state.settings
        Path(current.db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = connection.connect(current.db_path)
        try:
            migrate.ensure_schema(conn)
            yield conn
        finally:
            conn.close()

    def resolve_cursor(cursor: str | None) -> str:
        """Default to "everything has arrived", and validate anything else."""
        current: Settings = app.state.settings
        if cursor is None:
            return current.max_cursor
        try:
            config.cursor_to_date(cursor)
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail=f"cursor must be YYYY-MM-DDTHH:MM:SSZ, got {cursor!r}",
            ) from None
        return cursor

    @app.get("/api/meta")
    def meta() -> dict[str, Any]:
        """What produced this dataset, and the window the cursor moves through."""
        from recon.db import repository

        current: Settings = app.state.settings
        with open_conn() as conn:
            return {
                "profile": str(current.profile),
                "episode_count": current.episode_count,
                "cursor": service.cursor_bounds(current),
                "engine_version": config.ENGINE_VERSION,
                "adapter_version": config.ADAPTER_VERSION,
                "stored": repository.read_meta(conn),
            }

    @app.get("/api/overview")
    def overview(
        cursor: str | None = Query(None, description="Replay cursor, ISO8601 UTC"),
    ) -> dict[str, Any]:
        with open_conn() as conn:
            return service.overview(conn, resolve_cursor(cursor))

    @app.get("/api/queue/{disposition}")
    def queue(
        disposition: str,
        cursor: str | None = Query(None, description="Replay cursor, ISO8601 UTC"),
        order_by: str = Query("reopened_first"),
        limit: int = Query(100, ge=1, le=1000),
    ) -> dict[str, Any]:
        """A queue at a cursor.  ``EXCEPTION``, ``PENDING`` or ``CLOSED`` — there is no fourth."""
        from recon.db import repository

        if disposition.upper() not in {"EXCEPTION", "PENDING", "CLOSED"}:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"unknown disposition {disposition!r}; there are exactly three "
                    "(CLOSED, PENDING, EXCEPTION) because 'what do I do with this?' has three "
                    "answers. Reopened is a flag, not a fourth queue."
                ),
            )
        if order_by not in repository.QUEUE_ORDERINGS:
            raise HTTPException(
                status_code=422,
                detail=f"unknown ordering {order_by!r}; expected one of "
                f"{sorted(repository.QUEUE_ORDERINGS)}",
            )
        resolved_cursor = resolve_cursor(cursor)
        with open_conn() as conn:
            rows = service.queue(
                conn,
                resolved_cursor,
                disposition=disposition.upper(),
                order_by=order_by,
                limit=limit,
            )
        return {"cursor": resolved_cursor, "order_by": order_by, "episodes": rows}

    @app.get("/api/episode/{episode_id}")
    def episode(
        episode_id: str,
        cursor: str | None = Query(None, description="Replay cursor, ISO8601 UTC"),
    ) -> dict[str, Any]:
        with open_conn() as conn:
            detail = service.episode_detail(conn, episode_id, resolve_cursor(cursor))
        if detail is None:
            raise HTTPException(status_code=404, detail=f"no episode {episode_id!r}")
        return detail

    @app.get("/api/episode/{episode_id}/trace")
    def trace(episode_id: str) -> dict[str, Any]:
        """One claim end to end: its whole verdict history and every key that resolved to it."""
        with open_conn() as conn:
            detail = service.episode_detail(conn, episode_id, app.state.settings.max_cursor)
            if detail is None:
                raise HTTPException(status_code=404, detail=f"no episode {episode_id!r}")
            return service.episode_trace(conn, episode_id)

    @app.get("/api/episode/{episode_id}/dossier")
    def dossier(
        episode_id: str,
        cursor: str | None = Query(None, description="Replay cursor, ISO8601 UTC"),
    ) -> dict[str, Any]:
        """The whole episode, in one call: identity, economics, and its entire history.

        This is the endpoint the agent layer will use. One request returns everything known about one
        claim — every record that reached it, every cash movement, every verdict it has held and
        every reason for them, merged into one chronological narrative.

        Handing the agent six endpoints to stitch together would put the stitching *inside* the model,
        which is exactly where it must not be: the narrative is assembled here, deterministically, and
        the agent explains it.
        """
        from recon.api import dossier as dossier_module

        with open_conn() as conn:
            payload = dossier_module.build_dossier(conn, episode_id, resolve_cursor(cursor))
        if payload is None:
            raise HTTPException(status_code=404, detail=f"no episode {episode_id!r}")
        return payload

    @app.get("/api/feed-exceptions")
    def feed_exceptions(
        cursor: str | None = Query(None, description="Replay cursor, ISO8601 UTC"),
    ) -> dict[str, Any]:
        with open_conn() as conn:
            return service.feed_exceptions(conn, resolve_cursor(cursor))

    @app.post("/api/regenerate")
    def regenerate(profile: str = Query("demo")) -> dict[str, Any]:
        """Wipe and rebuild from the seed.

        Usable repeatedly rather than only once, which is what makes it a demo control instead of a
        test fixture (Decision C19). The same seed gives the same dataset, so this doubles as a live
        reproducibility check.
        """
        try:
            target = Profile(profile)
        except ValueError:
            raise HTTPException(
                status_code=422, detail=f"unknown profile {profile!r}; expected demo or full"
            ) from None
        current: Settings = app.state.settings
        app.state.settings = current.for_profile(target)
        return build_dataset(app.state.settings, rebuild=True)

    return app
