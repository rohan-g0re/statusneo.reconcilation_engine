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
from recon.domain.enums import SourceSystem

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


#: The Beacon payloads this build ingests, and the one it deliberately does not.
#:
#: ``beacon_rebate_status`` is held out, and the reason has been measured rather than
#: assumed — the assumption was wrong and is worth recording.
#:
#: This comment used to say landing it would turn C-05 into C-07 "wherever the 340B feed was
#: silent and Beacon says rejected". **Beacon is never saying anything the feed did not.**
#: ``beacon_payloads.rebate_status`` reads ``manufacturer_decision`` off the very
#: ``MANUFACTURER_DECISION`` event — or the ``REBATE_PAYMENT_BATCH`` line — that the engine
#: already ingests. Checked across the demo profile: Beacon's decision agrees with the feed
#: on 30 of 30 dispenses. Where the feed is silent, ``_manufacturer_decision`` returns
#: ``None`` and Beacon is silent too, so the C-05 population it was supposed to rescue does
#: not exist.
#:
#: Landing it would therefore add 30 duplicate ``TPA_MANUFACTURER_DECISION`` records carrying
#: a decision already present, through a second door, for no informational gain — the same
#: hazard ``BEACON_PAYMENT_REFERENCE`` is shaped to avoid on the money side.
#:
#: This is a limit of the **mock**, not of the architecture. DOC2-004 genuinely makes Beacon
#: authoritative here; our Beacon is a re-dressing of the 340B feed, so it can only restate
#: it. Making this demonstrable needs Beacon to be able to *disagree* with the feed — the
#: same deliberate divergence the two TPAs now carry — at which point Beacon winning is a
#: real demonstration instead of a duplicate row.
#:
#: ``beacon_submissions`` is outbound and fetches nothing.
#:
#: ``beacon_submissions`` is outbound and fetches nothing.
_BEACON_INBOUND = ("beacon_acknowledgements", "beacon_validation_outcomes",
                   "beacon_payment_references")


def _beacon_inbound_sources(settings: Settings) -> tuple[Any, ...]:
    """Beacon's inbound payloads, read as files off the directory the build just wrote.

    **Files rather than HTTP, and the reason is the demo rather than laziness.**  The loopback
    Beacon server exists and is well tested, but it is instantiated in exactly one place in
    the repository — a test. Wiring the demo to it would give the demo a port to bind, a
    process to start and stop, and a new way to fail, for a build whose defining property is
    that the same seed produces the same bytes. ``LocalDirectoryTransport`` also carries the
    ground-truth refusal, which an HTTP client does not need and would not have.

    Requirement A2's claim is that a document is a document however it arrived, so proving
    the Beacon leg over files and the HTTP transport separately is a stronger demonstration
    than coupling them — and ``tests/test_api_pattern.py`` already proves the HTTP half
    against a real socket.
    """
    from recon.connectors import registry
    from recon.connectors.transport import LocalDirectoryTransport

    beacon_dir = settings.vendor_dir() / "beacon"
    return registry.beacon_sources(
        {source_id: str(beacon_dir) for source_id in _BEACON_INBOUND},
        transport=LocalDirectoryTransport(beacon_dir),
        enabled=True,
    )


#: The TPA source that reads ``tpa_340b_events.jsonl`` — today's behaviour, and the default.
#:
#: A name rather than ``None`` because "generic" is a real, nameable choice with a real
#: drawback: that feed's shape is ours, invented under Doc 1, and no TPA ships anything like
#: it. ``None`` would read as "unset", which is the one thing it is not.
GENERIC_TPA_SOURCE = "generic"

#: What a build reads when the caller does not say — **a vendor, not the generic feed**.
#:
#: In production there is no generic TPA feed. There is Verity's export, or Craneware's, or a
#: sixth TPA's, and a prototype whose default is the one shape no vendor ships is
#: demonstrating the wrong thing. The generic feed remains available and remains *generated*
#: in every mode, because the vendor formatters read it.
#:
#: **Craneware rather than Verity**, and the reason is the state space rather than a
#: preference. ``verity_accumulations`` is a population selected on ``qualification_status``,
#: so it cannot express a disqualification at all: four episodes lose their 340B track
#: entirely and read as "never 340B" rather than "refused". Craneware's Claims Report is a
#: report of claims, a non-qualifying claim is still a claim, and every disqualification
#: survives — one episode differs from the generic baseline instead of four.
#:
#: Verity is one query parameter away and exercises ``TPA_INVOICE_LINE``, which Craneware has
#: no dataset for. Neither is "the" answer; that is the point of the switch.
DEFAULT_TPA_SOURCE = "craneware"

#: Which datasets carry a TPA's own account of a dispense, per vendor.
#:
#: Verity contributes two because it ships the qualification and the rebate invoice as
#: separate exports; Craneware's Claims Report carries the qualification and Craneware
#: publishes no invoice dataset we hold a shape for.  That asymmetry is the vendors', not
#: ours, and it is one of the differences the parity measurement has to explain rather than
#: smooth over.
_VENDOR_TPA_DATASETS: dict[str, tuple[str, ...]] = {
    "verity": ("verity_accumulations", "verity_invoices"),
    "craneware": ("craneware_claims_report",),
}


def vendor_tpa_sources(settings: Settings, vendor: str) -> tuple[Any, ...]:
    """The registry rows that make one vendor's export the TPA's voice in this build.

    **One vendor at a time, deliberately.**  The claim worth making is not "the engine reads
    vendor data" — it is "the engine reads *either* vendor's data and reaches the same
    answer".  A mode that blended both would prove neither, and would hide exactly the
    disagreement a reconciliation engine exists to catch.

    ``enabled=True`` because the registry rows ship disabled behind DOC2-016's Week 3 access
    gate.  Reading a directory this build just wrote is not reaching a vendor's SFTP server,
    and the caller saying so explicitly is the honest version of that distinction.

    The filenames are resolved by prefix because Verity stamps its export names from the data
    (``DOC2-010``), so the landed name is not knowable in advance.  That resolution is
    single-valued only because ``verity_export.remove_superseded`` keeps one generation on
    disk; before it existed this would have picked whichever superseded run sorted first.

    Raises:
        KeyError: an unknown vendor name, rather than a silently empty source list — which
            would load nothing, reach every episode with no 340B evidence at all, and read as
            a dataset in which nothing qualified.
    """
    from recon.connectors import registry, vendors
    from recon.connectors.transport import LocalDirectoryTransport

    directory = settings.vendor_dir() / vendor
    landed: dict[str, tuple[str, ...]] = {}
    for source_id in _VENDOR_TPA_DATASETS[vendor]:
        prefix = vendors.mapping_for(source_id).filename_prefix
        names = sorted(
            path.name for path in directory.glob("*.csv") if path.name.startswith(prefix)
        )
        if not names:
            raise FileNotFoundError(
                f"no export starting {prefix!r} in {directory}; the vendor files are written "
                "by coverage.write_all during the build, so this means generation did not run"
            )
        landed[source_id] = tuple(names)

    return registry.vendor_sources(
        {source_id: str(directory) for source_id in landed},
        filenames=landed,
        transport=LocalDirectoryTransport(directory),
        enabled=True,
    )


def build_dataset(
    settings: Settings,
    *,
    rebuild: bool = False,
    tpa_source: str = DEFAULT_TPA_SOURCE,
) -> dict[str, Any]:
    """Generate the feeds, load them, reconcile, and return what happened.

    This is the regenerate control's implementation, and it is deliberately the *whole* pipeline
    rather than a reset: the dataset is a derived artefact, so rebuilding it from the seed is both
    the cheapest way to get a clean state and a continuous proof that it is reproducible.

    ``tpa_source`` chooses where the TPA's own account of a dispense comes from:
    ``"generic"`` (the default, and today's behaviour) reads it from
    ``tpa_340b_events.jsonl``; ``"verity"`` or ``"craneware"`` reads it from that vendor's
    export instead.  In production there is no generic TPA feed — there is Verity's export,
    or Craneware's, or a sixth TPA's — so the vendor modes are the shape the engine would
    really run in, and the generic one is the synthetic convenience.

    **The generic feed is still read in every mode**, because it is not only the TPA's.  Its
    rows declare their own author and 35 of them are the *manufacturer's* — rebate payment
    batches and manufacturer decisions, which DOC2-004 puts outside a TPA's authority
    entirely and which no vendor export could carry.  Switching source swaps 78 rows, not a
    file.
    """
    from recon.db import connection, migrate
    from recon.engine import run as engine
    from recon.generators import orchestrator
    from recon.ingest import pipeline
    from recon.mocks import coverage

    if rebuild:
        migrate.rebuild(settings.db_path)

    generation = orchestrator.generate(settings)
    hashes = orchestrator.write_outputs(settings, generation)

    # The vendor exports are written here rather than inside ``write_outputs`` because they are
    # *derived from* the feeds it just wrote: ``coverage.write_all`` re-reads the six feed files
    # and the identifier sidecar off disk and re-dresses them in each vendor's own layout. Putting
    # the call in ``write_outputs`` would make ``recon.generators`` import ``recon.mocks``, and the
    # mocks are downstream of generation by design — they format what the generators decided and
    # decide nothing themselves.
    #
    # Until this line existed the vendor layer was reachable only from the test suite, so
    # ``data/generated/<profile>/vendor/`` held the identifier sidecar and nothing else: every
    # Verity dataset and Craneware report was real code with no output a human could open. A
    # connector whose payloads only exist inside a pytest tmp dir cannot be demonstrated.
    coverage.write_all(settings)

    Path(settings.db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = connection.connect(settings.db_path)
    migrate.ensure_schema(conn)
    from recon.reference.fingerprint import fingerprint

    migrate.stamp_meta(conn, settings, fingerprint())
    try:
        # The TPA's own rows come out of the generic feed or out of a vendor's export, never
        # both: ingesting each dispense's qualification twice would not merely duplicate
        # evidence, it would make ``_read_rebate_lines``-style counting tests meaningless and
        # leave the crosswalk resolving one dispense through two doors.
        exclude = (
            frozenset({SourceSystem.TPA_PORTAL})
            if tpa_source != GENERIC_TPA_SOURCE
            else frozenset()
        )
        stats = pipeline.load_feeds(
            conn, settings.feeds_dir(), exclude_source_systems=exclude
        )
        if tpa_source != GENERIC_TPA_SOURCE:
            stats.raw_records += pipeline.load_from_sources(
                conn,
                vendor_tpa_sources(settings, tpa_source),
                fetched_at=settings.max_cursor,
            ).raw_records
        # Loaded after the feeds and before ingest, which is the only order that works: a
        # Beacon acknowledgement resolves against an episode, and the episodes do not exist
        # until the anchors from the six feeds have been adapted. Landing raw rows first and
        # adapting everything once keeps that ordering the arrival cursor's business rather
        # than this function's.
        # ``fetched_at`` is the end of the generation window rather than a wall clock. It is
        # used for exactly one thing — a PENDING validation outcome, whose ``decided_at`` is
        # null because Beacon has not decided — and it says the true thing about such a row:
        # as of the end of the window, no decision had arrived. A real clock here would make
        # the same seed produce different bytes on two different days.
        stats.raw_records += pipeline.load_from_sources(
            conn, _beacon_inbound_sources(settings), fetched_at=settings.max_cursor
        ).raw_records
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

    from recon.db import migrate as migrate_errors

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

    @app.exception_handler(migrate_errors.SchemaVersionMismatch)
    def _stale_database(request, exc):  # noqa: ANN001 - framework signature
        """Answer a stale database with an instruction instead of a stack trace.

        Found by actually opening the dashboard in a browser, which nothing in the test
        suite does: the repository ships databases stamped by an older build, so the very
        first request after any schema change raised here and FastAPI turned it into a bare
        ``500 Internal Server Error``.  The message naming the one-line fix went to the
        server log, where the person staring at the blank page was not looking.

        ``503`` rather than ``500`` because the distinction is real and worth making: the
        service is fine and its data is not ready.  ``Retry-After`` is deliberately absent —
        waiting does not fix this, and advertising a retry would suggest it might.
        """
        from fastapi.responses import JSONResponse

        return JSONResponse(
            status_code=503,
            content={
                "error": "stale_database",
                "detail": str(exc),
                "found_schema_version": exc.found,
                "expected_schema_version": exc.expected,
                "remedy": "POST /api/regenerate?profile=demo",
            },
        )

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

    @app.get("/api/record/{raw_id}")
    def raw_record(raw_id: int) -> dict[str, Any]:
        """The verbatim source line a timeline event came from.

        Lineage has three levels and they are not the same thing.  A *pointer* says where a fact came
        from — file, line, record id — and every timeline event already carries one.  This endpoint is
        the second level: the bytes themselves, exactly as they arrived, plus the hashes that prove
        they are unaltered.

        It is a separate call on purpose.  A raw feed line runs several hundred characters, and an
        episode has a dozen of them; inlining every payload into the dossier would multiply the cost
        of every agent call to carry something almost no question needs.  Real systems reference
        documents and fetch them on demand rather than embedding them, and the pointer in each event
        is what makes the fetch one hop.

        ``payload_sha256`` is over the stored line and ``file_sha256`` over the whole feed, so a
        reader can verify both the record and the file it claims to come from.
        """
        with open_conn() as conn:
            row = conn.execute(
                "SELECT r.raw_id, r.source_system, r.source_record_id, r.source_line_no,"
                "       r.payload, r.payload_sha256, r.received_at,"
                "       b.source_file, b.file_sha256, b.loaded_at"
                "  FROM raw_record r"
                "  JOIN ingest_batch b ON b.batch_id = r.batch_id"
                " WHERE r.raw_id = ?",
                (raw_id,),
            ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail=f"no source record {raw_id}")
        return {
            "raw_id": row["raw_id"],
            "source_file": row["source_file"],
            "source_line_no": row["source_line_no"],
            "source_record_id": row["source_record_id"],
            "source_system": row["source_system"],
            "received_at": row["received_at"],
            "payload": row["payload"],
            "payload_sha256": row["payload_sha256"],
            "file_sha256": row["file_sha256"],
            "loaded_at": row["loaded_at"],
        }

    @app.get("/api/feed-exceptions")
    def feed_exceptions(
        cursor: str | None = Query(None, description="Replay cursor, ISO8601 UTC"),
    ) -> dict[str, Any]:
        with open_conn() as conn:
            return service.feed_exceptions(conn, resolve_cursor(cursor))

    @app.get("/api/connectivity")
    def connectivity() -> dict[str, Any]:
        """Requirement F3's connector-readiness report, derived at the moment it is asked for.

        Nothing is cached and nothing is read from a generated file, which is the requirement
        rather than an oversight: the report is produced by inspecting the registry, the
        transport classes, the schema contracts, the provenance tables and the evidence index
        *as they are now*, so a connector deleted five minutes ago is missing from the next
        response. A cached copy would be a hand-maintained table with a timestamp on it.

        The database connection is handed in so the control-total column is read from rows that
        actually landed rather than reported as "not checked". ``build_report`` queries it
        defensively and tolerates the table being absent, so a fresh deployment answers this
        endpoint before its first ingest.

        **What this endpoint must never be read as saying.** Every connector in this build talks
        to a local mock. ``live_vendor_connections`` is the payload's first key and is expected
        to be empty; ``live_vendor_connection_statement`` is the sentence that goes with it, and
        both are computed by :mod:`recon.connectors.readiness` rather than by the client, so no
        front end can render a connection this build has not made.

        **The adapter module is handed in, and this route is the only place that could do it.**
        The report has to say whether a source's rows become canonical records — the difference
        between "we read this file" and "we read this file and deliberately stop at the rows" —
        and that fact lives in :mod:`recon.ingest.adapters`, on the far side of a seam the
        report keeps deliberately: ``readiness`` imports nothing from ``recon.ingest``, and a
        test asserts it on the import graph. An API route is the composition root that owns both
        layers, so the wiring belongs here and the rule stays over there. No rule is written in
        this function; :func:`recon.connectors.readiness.adapter_coverage` reads the answer off
        the module by shape, and omitting the argument would report "not measured" rather than
        "no".
        """
        from recon.connectors import readiness
        from recon.ingest import adapters

        with open_conn() as conn:
            report = readiness.build_report(
                control_totals=conn,
                settings=app.state.settings,
                adapters=adapters,
            )
        return readiness.as_dict(report)

    @app.post("/api/regenerate")
    def regenerate(
        profile: str = Query("demo"),
        seed: int | None = Query(
            None,
            description=(
                "Master seed. Omit to rebuild the published dataset byte for byte; pass a new "
                "value for a genuinely different one."
            ),
        ),
        tpa_source: str = Query(
            DEFAULT_TPA_SOURCE,
            description=(
                "Where the TPA's own account of a dispense comes from: 'generic' reads "
                "tpa_340b_events.jsonl, 'verity' or 'craneware' reads that vendor's export "
                "instead. The manufacturer's rows are read from the feed in every mode."
            ),
        ),
    ) -> dict[str, Any]:
        """Wipe and rebuild the whole dataset.

        Always a real rebuild — the feeds are regenerated, reloaded, re-crosswalked and
        re-reconciled from nothing. What ``seed`` controls is whether the result is *new*.

        **Omit it** and the rebuild is byte-identical to the last one. That is the point rather
        than a limitation: ``manifest.json`` publishes a SHA-256 per feed, and those hashes only
        mean something if the same seed, window and reference data reproduce the same bytes. Run
        it twice, compare the manifests, and you have checked reproducibility rather than claimed
        it.

        **Pass one** and you get a different dataset of the same shape — different claims,
        different amounts, different defects landing on different episodes — which is itself
        reproducible from that seed. So "fresh data" and "reproducible data" are the same
        mechanism with a different argument, not a trade-off.

        "Same shape" means the guarantees, not the numbers. Both profiles keep their episode
        count; ``full`` still covers all 372 verdict pairs and ``demo`` still contains every
        named edge case. What moves on ``demo`` is the mix around those guarantees, queue
        counts included, because only part of its sixty is hand-placed (see
        :class:`~recon.config.CuratedSpine`). The dashboard's rebuild buttons therefore mint
        a fresh seed each press and offer replaying the current one as a separate action.

        The seed arrives as an *input*, deliberately. Nothing inside the generator reads a clock
        (Decision 18): a generator that invented its own seed could never be replayed, and the
        cursor's whole meaning depends on the timeline being fixed before anything reads it.
        """
        try:
            target = Profile(profile)
        except ValueError:
            raise HTTPException(
                status_code=422, detail=f"unknown profile {profile!r}; expected demo or full"
            ) from None
        if seed is not None and not 0 < seed < 2**63:
            raise HTTPException(
                status_code=422, detail=f"seed must be a positive integer, got {seed}"
            )
        # Validated against the table that actually serves it, so a fourth vendor becomes
        # reachable here by adding a mapping rather than by editing this handler. Rejected
        # loudly rather than falling back to generic: a typo that silently rebuilt the
        # default would answer "which TPA is this?" with the wrong TPA, and the whole point
        # of the control is that the answer differs.
        if tpa_source != GENERIC_TPA_SOURCE and tpa_source not in _VENDOR_TPA_DATASETS:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"unknown tpa_source {tpa_source!r}; expected {GENERIC_TPA_SOURCE!r} or "
                    f"one of {sorted(_VENDOR_TPA_DATASETS)}"
                ),
            )

        current: Settings = app.state.settings
        rebuilt = current.for_profile(target)
        if seed is not None:
            from dataclasses import replace

            rebuilt = replace(rebuilt, master_seed=seed)
        app.state.settings = rebuilt

        result = build_dataset(app.state.settings, rebuild=True, tpa_source=tpa_source)
        result["master_seed"] = app.state.settings.master_seed
        # Echoed back because the rest of the payload cannot be read without it. Two rebuilds
        # of the same profile and seed legitimately produce different verdicts depending on
        # which TPA supplied the qualifications, so a response that did not say which one it
        # used would be an unattributable measurement.
        result["tpa_source"] = tpa_source
        return result

    # The agent layer's HTTP surface (docs/agent_layer_design.md S8.6). Always mounted:
    # recon.agents.api guards its own heavy (httpx-dependent) imports internally and returns a
    # 503 with an actionable message per request when the 'agent' extra is missing or no API key
    # is configured, rather than this module needing to know or care which.
    from recon.agents import api as agents_api

    app.include_router(agents_api.build_router())

    return app
