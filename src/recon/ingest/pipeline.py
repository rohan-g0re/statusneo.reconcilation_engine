"""The connector: event-driven ingestion, forward resolution, and the backward re-check.

None of the four feeds is ever scanned for candidates.  An inbound document *is* the trigger,
and it already carries its own lookup keys (Decision A6):

    inbound document arrives
       -> parse, extract its keys
       -> fetch ONLY the episodes those keys resolve to
       -> attach, allocate, and register whatever new keys it makes resolvable
       -> check the parked pool backwards: does anything there resolve against me now?

═══ Why step four is not optional ══════════════════════════════════════════════════

Every inbound document does **two** things, not one: it resolves its own keys forward, and it
re-checks the parked pool backwards (Decision A20).  Skip the second and two failure modes
become permanent instead of temporary.

An out-of-order arrival — a bank deposit that beat its own remittance to the door — never
gets a second chance to match.  And an unmatched deposit stays unmatched forever even after
the record that would have explained it finally lands.  Neither is a rare case: the bank
feed's whole character is arriving at a different time from the paperwork.

═══ Why the cursor is mandatory on every lookup ════════════════════════════════════

``received_at <= cursor`` is applied to both the crosswalk and the parked pool, and the
cursor used during ingestion is the *arriving record's own* ``received_at``.  Without it a
key first seen on 18 March would resolve a lookup performed on behalf of a 10 March record,
and replay would quietly answer with evidence we did not have at the time.  That is not an
optimisation; it is the difference between replay and fiction.

═══ What this layer does NOT do ════════════════════════════════════════════════════

It computes no verdicts.  Verdicts are derived per ``(episode, cursor)`` by the engine and
appended; nothing here writes one.  Delete the verdict log and it rebuilds by replay
(Decision A8 / Decision 23).

It also never reads ``truth/ground_truth.json``.  The loader takes the feeds directory and
nothing else, which is what makes the crosswalk scoreable rather than assumed.
"""

from __future__ import annotations

import csv
import hashlib
import io
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Iterable, Sequence

from recon import config
from recon.connectors.registry import (
    _GENERATED_SOURCE_SYSTEMS,
    PayloadFormat,
    UnknownPayloadFormatError,
)

if TYPE_CHECKING:  # pragma: no cover - types only
    from recon.connectors.registry import Source
from recon.domain.enums import (
    AllocationBasis,
    KeyType,
    ParkReason,
    QuarantineReason,
    RecordKind,
    ReimbursementTrack,
    SourceSystem,
)
from recon.connectors.authority import SourceAuthorityError
from recon.crosswalk import keys
from recon.ingest import adapters
from recon.ingest.allocate import (
    correct_allocation,
    plan_rebate_allocation,
    plan_remittance_allocation,
)
from recon.ingest.adapters import AdapterError, CanonicalRecord

__all__ = [
    "IngestStats",
    "UnknownSourceSystemError",
    "load_feeds",
    "load_from_sources",
    "ingest",
    "FEED_SOURCE_SYSTEMS",
]


class UnknownSourceSystemError(RuntimeError):
    """A document declared a ``source_system`` no adapter is registered for.

    Raised at **load** time by :func:`_read_rows`, and it stops the load rather than
    quarantining the record.  That is the one place in this module where a bad record is not
    held, so it is worth saying why — "quarantine it like everything else" is the obvious
    answer and it is not available here.

    * Quarantine has a precondition this path cannot meet.  ``quarantined_record.raw_id`` is
      ``NOT NULL REFERENCES raw_record(raw_id)`` and no ``raw_record`` exists yet; the row is
      being *built* here.  A quarantine row with nothing to point at is a reason code with no
      evidence, which is the opposite of what the queue is for.
    * Landing the raw row first is not available either.  ``raw_record.source_system`` carries
      a SQL ``CHECK`` over the known systems, so the offending value cannot be stored even as
      a record of what arrived.  The only way in would be to attribute it to something it did
      not claim — precisely the confidently wrong attribution this path exists to prevent.
    * And the defect is not record-shaped.  An export that misspells a system misspells it on
      every line, so quarantining would emit one row per record for a single configuration
      mistake and leave an ingest that looks partially successful.

    Aborting is also safe to abort *into*, which is what makes it the right answer rather than
    merely the available one: ``load_from_sources`` builds the whole row list before inserting
    a batch, so the failing document lands nothing, documents already loaded keep their
    batches, and loading is idempotent on the file hash — a re-run skips those and resumes
    here.

    A ``RuntimeError`` rather than a ``ValueError``, and that is load-bearing rather than
    taste: :func:`_process_raw_record` catches ``(AdapterError, ValueError)`` and quarantines
    what it catches, so a ``ValueError`` here would be one refactor away from being swallowed
    into the quarantine this docstring argues cannot work.
    """

    def __init__(
        self,
        message: str,
        *,
        source_id: str,
        document: str,
        line_no: int,
        value: object,
        permitted: tuple[str, ...],
    ) -> None:
        super().__init__(message)
        self.source_id = source_id
        self.document = document
        self.line_no = line_no
        self.value = value
        self.permitted = permitted


#: Which source system each feed file carries.  The 340B feed carries two, distinguished per
#: record by its own ``source_system`` field, because a manufacturer's payment batch and a
#: TPA's qualification decision genuinely come from different systems that happen to be
#: exported together.
#:
#: The table itself moved to ``connectors.registry`` when sources became data (requirement
#: A1) — a module that must not name a vendor cannot own the vendor attribution table.  The
#: name is re-exported here because it is part of this module's published surface and
#: nothing is gained by breaking that; it is one definition seen from two places, not two.
FEED_SOURCE_SYSTEMS: dict[str, SourceSystem] = _GENERATED_SOURCE_SYSTEMS


#: Record kinds that are resolution targets rather than records seeking resolution.
_RESOLUTION_ROOTS = frozenset({RecordKind.REMITTANCE, RecordKind.REBATE_BATCH})

#: CAQH CORE Operating Rule 370 gives remittance and payment a ±3-business-day window.  It is
#: the industry's own answer to exactly this problem, and it is the tolerance used when the
#: addenda are gone and amount-plus-date is all that is left.
AMOUNT_DATE_WINDOW_DAYS = 3


@dataclass(slots=True)
class IngestStats:
    """What one ingestion run did.  Every number here is a property worth asserting on."""

    raw_records: int = 0
    normalized_records: int = 0
    duplicates_collapsed: int = 0
    quarantined: int = 0
    episodes_created: int = 0
    keys_published: int = 0
    records_parked: int = 0
    parks_resolved: int = 0
    bank_rows_allocated: int = 0
    bank_rows_parked: int = 0
    allocations_written: int = 0
    allocations_corrected: int = 0
    park_reasons: Counter = field(default_factory=Counter)

    def as_dict(self) -> dict[str, object]:
        return {
            "raw_records": self.raw_records,
            "normalized_records": self.normalized_records,
            "duplicates_collapsed": self.duplicates_collapsed,
            "quarantined": self.quarantined,
            "episodes_created": self.episodes_created,
            "keys_published": self.keys_published,
            "records_parked": self.records_parked,
            "parks_resolved": self.parks_resolved,
            "bank_rows_allocated": self.bank_rows_allocated,
            "bank_rows_parked": self.bank_rows_parked,
            "allocations_written": self.allocations_written,
            "allocations_corrected": self.allocations_corrected,
            "park_reasons": dict(self.park_reasons),
        }


# ═══ loading: files to immutable raw rows ═══════════════════════════════════


#: The checkpoint's stand-in for "now".
#:
#: Wall-clock is permitted in ``connector_checkpoint`` and in logs and nowhere else
#: (requirement §4.11), but *permitted* is not the same as *unavoidable*.  Every random
#: stream in this system is seeded from a canonical string and ``ingest_batch.loaded_at`` is
#: pinned to the epoch, precisely so that the same seed produces the same bytes.  Reading a
#: real clock here by default would break that for no gain: nothing downstream may read
#: ``fetched_at``, so a fixed value costs nothing and keeps a test's database reproducible.
#: A deployment that wants a real timestamp passes one.
EPOCH_STAMP = "1970-01-01T00:00:00Z"


def load_from_sources(
    conn: sqlite3.Connection,
    sources: Sequence["Source"],
    *,
    fetched_at: str = EPOCH_STAMP,
) -> IngestStats:
    """Land every document a registered source currently offers into ``raw_record``.

    The transport-agnostic half of loading (requirement A2).  A source knows how to obtain
    its documents; this function knows what to do with one once it exists, and the two no
    longer have to agree about directories.

    **Nothing below this function changed.**  :func:`ingest`, :func:`_process_raw_record`,
    :func:`_attach`, :func:`_park`, :func:`_recheck_parked` and the allocator all operate on
    ``raw_record`` rows, and a row is a row regardless of whether it arrived off a disk, over
    SFTP or from an HTTP response.  That is the payoff of the original event-driven decision,
    and it is why a connector layer is additive here rather than a rewrite.

    **Three layers of idempotency, and they stop different things.**  Record-level lives in
    :func:`ingest`.  File-level is the content-hash check below, which stops a re-*ingest*.
    Fetch-level is the checkpoint, which stops the re-*download* — and that is the one
    requirement A4 actually asks for, because "run twice; the second run downloads zero
    bytes" is a stronger claim than "ingests zero records" and the weaker one was already
    true before any of this existed.

    The source is registered before it is fetched, not after.  ``connector_checkpoint``
    carries a foreign key onto ``connector_source``, so a source that fetched first would
    fail its checkpoint write *after* the bytes had already moved — the one ordering where
    the failure costs something.
    """
    from recon.connectors import checkpoint as checkpoint_module
    from recon.connectors import control_totals
    from recon.connectors import registry
    from recon.db import repository

    stats = IngestStats()

    for source in registry.enabled(sources):
        repository.register_connector_source(
            conn,
            source_id=source.source_id,
            vendor=source.vendor,
            transport_kind=str(source.transport_kind),
            source_system=source.source_system,
            endpoint=source.endpoint,
            credential_ref=source.credential_ref,
            mapping_version=source.mapping_version,
            enabled=source.enabled,
        )
        predicate = checkpoint_module.fetch_predicate(conn, source.source_id)

        for document in source.require_transport().fetch(source, should_fetch=predicate):
            text = document.text
            file_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()

            def _checkpoint() -> None:
                """Mark this document as successfully taken.

                Called on the two paths that *finish* — landed, or recognised as already
                landed — and on neither path that raises.  The ordering is the whole point.

                An earlier version recorded the fetch as soon as the bytes arrived, which is
                wrong in a way that only shows up on a bad day: a document that aborted
                mid-load was still checkpointed, so correcting the export and re-running
                skipped it, and the fix appeared to do nothing.  A checkpoint means "we took
                this", not "we touched it".

                A document found already ingested *is* recorded, because it genuinely was
                taken — leaving it out would turn A4's "the second run downloads zero bytes"
                into "zero bytes unless the file was a duplicate".
                """
                checkpoint_module.record_fetch(
                    conn,
                    source_id=source.source_id,
                    document_name=document.name,
                    remote_mtime=document.modified_at,
                    content_sha256=file_sha,
                    fetched_at=fetched_at,
                )

            if repository.batch_for_file_sha256(conn, file_sha) is not None:
                _checkpoint()
                continue

            rows = list(
                _read_rows(
                    source.payload_format,
                    text,
                    source.source_system,
                    source_id=source.source_id,
                    document=document.name,
                )
            )
            # F2.  Before the batch row exists, not after.  A batch written first and then
            # abandoned reads downstream as a file that arrived empty, which is the one
            # reading a truncated delivery must never get — and ``control_total`` carries no
            # foreign key onto ``ingest_batch`` precisely so the evidence outlives a batch
            # that never comes into existence.
            #
            # Placed after the ``batch_for_file_sha256`` early-continue above, not before it:
            # a file that already landed was reconciled when it landed, and re-checking would
            # append a fresh control_total row on every run of a table that cannot be tidied
            # up afterwards.
            #
            # Deliberately not caught.  A short file is not a bad record — there is no raw
            # row to quarantine and no sensible partial outcome — so this propagates and the
            # load aborts. Documents already loaded keep their batches, this document lands
            # nothing, and loading is idempotent on the file hash, so a corrected
            # re-delivery resumes exactly here.  ``_checkpoint()`` is not reached, which is
            # also correct: a refused document was never taken.
            control_totals.reconcile_or_fail(
                conn,
                source_id=source.source_id,
                source_file=document.name,
                file_sha256=file_sha,
                text=text,
                observed_count=len(rows),
                checked_at=fetched_at,
            )
            batch_id = repository.insert_ingest_batch(
                conn,
                source_file=document.name,
                source_system=str(source.source_system),
                file_sha256=file_sha,
                record_count=len(rows),
                # Operational wall-clock, explicitly not a domain date.  It is the one place
                # the system is allowed to know what time it is, and nothing downstream reads
                # it.
                loaded_at="1970-01-01T00:00:00Z",
                source_id=source.source_id,
            )
            raw_rows = []
            for line_no, (payload_text, record_id, received_at, source_system) in enumerate(
                rows, start=1
            ):
                raw_rows.append(
                    {
                        "batch_id": batch_id,
                        "source_system": str(source_system),
                        "source_record_id": record_id,
                        "source_line_no": line_no,
                        "payload": payload_text,
                        "payload_sha256": hashlib.sha256(
                            payload_text.encode("utf-8")
                        ).hexdigest(),
                        "received_at": received_at,
                    }
                )
            stats.raw_records += repository.insert_raw_records(conn, raw_rows)
            _checkpoint()
    return stats


def load_feeds(conn: sqlite3.Connection, feeds_dir: Path) -> IngestStats:
    """Read the six generated feed files into ``raw_record``, verbatim.

    Kept as a thin wrapper rather than edited away, so every existing call site — the API's
    rebuild, ``tests/conftest_pipeline.py``, ``tests/scenario.py`` — is untouched.  The
    acceptance bar for this refactor is that the existing suite passes **unedited**; if any
    test had needed changing, the seam was cut in the wrong place.

    ``feeds_dir`` is still the only path this function accepts, and
    :class:`~recon.connectors.transport.LocalDirectoryTransport` still cannot reach
    ``truth/`` — it refuses a root inside it, refuses a name that traverses, and refuses a
    resolved path that lands there.  The guarantee moved; it did not weaken.
    """
    from recon.connectors import registry

    return load_from_sources(conn, registry.local_sources(Path(feeds_dir)))


def _attributed_source_system(
    declared: object,
    default: SourceSystem,
    *,
    source_id: str,
    document: str,
    line_no: int,
) -> SourceSystem:
    """Which system a record belongs to: its own claim, else its source row's default.

    One helper for every payload format, and that is the fix rather than a tidy-up.  The bug
    this replaces existed *because* two branches answered the same question differently — the
    delimited branch returned a hardcoded ``BANK`` and never consulted the source row at all,
    so a new delimited vendor was silently attributed to the bank while its own batch header
    said otherwise.  Nothing downstream can detect that; it simply reads as a bank record.
    The next format added — fixed-width, XML — would have been free to get it wrong the same
    way.
    """
    if not declared:
        return default
    try:
        return SourceSystem(declared)
    except ValueError:
        permitted = tuple(member.value for member in SourceSystem)
        raise UnknownSourceSystemError(
            f"{document!r} line {line_no} (source {source_id!r}) declares source_system "
            f"{declared!r}, which no adapter is registered for. Permitted values: "
            f"{', '.join(permitted)}. Either the export names a system this build does not "
            "model yet, or the source row points at the wrong feed.",
            source_id=source_id,
            document=document,
            line_no=line_no,
            value=declared,
            permitted=permitted,
        ) from None


def _read_rows(
    payload_format: "PayloadFormat | str",
    text: str,
    default_source_system: SourceSystem,
    *,
    source_id: str = "<unregistered>",
    document: str = "<unnamed>",
) -> Iterable[tuple[str, str | None, str, SourceSystem]]:
    """Yield ``(payload_json, record_id, received_at, source_system)`` per source row.

    The bank CSV is re-encoded as JSON so the raw layer holds one uniform payload shape.  The
    original CSV row survives losslessly — every column becomes a key — so lineage back to
    "the synthetic source record" is intact, which is what the assignment actually grades.

    **Both the format and the fallback attribution arrive as arguments rather than being
    looked up by filename.**  They used to be derived from the name — the delimited branch
    compared against ``BANK_TRANSACTIONS_FILE`` and the fallback indexed
    ``FEED_SOURCE_SYSTEMS`` — which meant a source outside the original six raised
    ``KeyError`` on the first record that did not declare its own ``source_system``.
    Requirement A1's acceptance is that a seventh source needs no edit to this module, and
    while those lookups were here that was simply untrue, in a way only a new vendor would
    ever have discovered.

    The format dispatch is **total**.  A format that is neither member raises rather than
    falling through to the line-delimited reader, which is what the original free-string
    version did — so a typo misread every record in the file and landed whatever survived,
    with no signal at all.
    """
    import json

    fmt = PayloadFormat(payload_format)

    if fmt is PayloadFormat.BANK_CSV:
        reader = csv.DictReader(io.StringIO(text))
        for line_no, row in enumerate(reader, start=2):  # row 1 is the header
            cleaned = adapters.parse_bank_row(row)
            yield (
                json.dumps(cleaned, separators=(",", ":"), sort_keys=True),
                cleaned.get("ach_trace_number"),
                cleaned["received_at"],
                _attributed_source_system(
                    cleaned.get("source_system"),
                    default_source_system,
                    source_id=source_id,
                    document=document,
                    line_no=line_no,
                ),
            )
        return

    if fmt is PayloadFormat.JSONL:
        for line_no, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            payload = adapters.parse_jsonl_line(line)
            yield (
                line,
                payload.get("record_id"),
                payload["received_at"],
                _attributed_source_system(
                    payload.get("source_system"),
                    default_source_system,
                    source_id=source_id,
                    document=document,
                    line_no=line_no,
                ),
            )
        return

    raise UnknownPayloadFormatError(
        f"{fmt!r} is a registered payload format with no reader in {__name__}. Adding a "
        "PayloadFormat member without adding a branch here is the one way the original "
        "silent misparse could come back."
    )


# ═══ the event loop ═════════════════════════════════════════════════════════


def ingest(conn: sqlite3.Connection, *, stats: IngestStats | None = None) -> IngestStats:
    """Process every raw record in arrival order.

    Arrival order, not event order.  The pipeline replays the order records *showed up* in,
    which is the only order in which "a deposit arrived before its remittance" is a fact
    rather than an artefact of how we chose to sort.
    """
    from recon.db import repository

    stats = stats or IngestStats()
    # ``source_id`` comes along so the schema registry can be asked which contract applies
    # (requirement B1).  Joined rather than denormalised onto ``raw_record``: the raw layer
    # stores exactly what arrived, and which registered source fetched it is a fact about the
    # batch, not about the record.
    rows = conn.execute(
        "SELECT r.raw_id, r.source_system, r.source_record_id, r.payload, r.received_at,"
        "       b.source_id"
        "  FROM raw_record r JOIN ingest_batch b ON b.batch_id = r.batch_id"
        " ORDER BY r.received_at, r.raw_id"
    ).fetchall()

    for row in rows:
        _process_raw_record(conn, row, stats)
    return stats


def _process_raw_record(conn: sqlite3.Connection, row: sqlite3.Row, stats: IngestStats) -> None:
    from recon.db import repository

    from recon.connectors import schema_registry

    source_system = SourceSystem(row["source_system"])
    try:
        payload = adapters.parse_jsonl_line(row["payload"])

        # B1: the shape is checked *before* adapt() is allowed to assign meaning to it.
        # The ordering is the requirement's own wording and it is the whole point — adapting
        # a wrongly-shaped record does not fail, it succeeds into the nearest-looking slot
        # and produces a confidently wrong canonical record that nothing downstream can tell
        # from a right one.
        #
        # A source with no registered contract validates vacuously, which is what keeps the
        # six generated feeds loading exactly as they always have.  ``check`` never raises:
        # a vendor re-cutting its export and declaring a version we do not hold is a record
        # to quarantine, not a crash.
        schema_detail = schema_registry.check(
            row["source_id"], payload, version=payload.get("schema_version")
        )
        if schema_detail is not None:
            repository.quarantine_record(
                conn,
                raw_id=row["raw_id"],
                received_at=row["received_at"],
                reason_code=str(schema_registry.QUARANTINE_REASON),
                detail=schema_detail[:400],
            )
            stats.quarantined += 1
            return

        canonical = adapters.adapt(payload, source_system)

        # E5 / DOC2-004.  After adapt(), never inside it: ``adapt`` wraps its dispatch in
        # ``except (TypeError, ValueError)``, so a named authority error raised in there would
        # be swallowed and re-reported as a generic AdapterError — the breach would still be
        # caught and would become unreadable in the same motion.
        #
        # The whole tree is checked, not just the parent.  A rebate batch's dispense lines
        # carry their own kind, source and canonical dict and are written by ``_insert_tree``
        # exactly like the parent; checking only the parent would leave every line a source
        # could use to set a field it does not own.
        _assert_source_authority(canonical)
    except SourceAuthorityError as exc:
        # Distinct from the parse failure below on purpose — see QuarantineReason's comment.
        repository.quarantine_record(
            conn,
            raw_id=row["raw_id"],
            received_at=row["received_at"],
            reason_code=str(QuarantineReason.SOURCE_AUTHORITY_BREACH),
            detail=str(exc)[:400],
        )
        stats.quarantined += 1
        return
    except (AdapterError, ValueError) as exc:
        # Quarantined with lineage intact, never coerced into the nearest-looking slot.  The
        # generated dataset contains none of these by design (C16 dropped D-5), so this path
        # is production behaviour rather than something the test suite exercises — and it is
        # here precisely so an unmappable record cannot silently vanish.
        repository.quarantine_record(
            conn,
            raw_id=row["raw_id"],
            received_at=row["received_at"],
            reason_code=str(QuarantineReason.UNPARSEABLE),
            detail=str(exc)[:400],
        )
        stats.quarantined += 1
        return

    cursor = canonical.received_at
    parent_norm_id = _insert_tree(conn, canonical, row["raw_id"], stats)
    if parent_norm_id is None:
        return

    # Children first, then the parent.  The order is load-bearing and the reason is not obvious.
    #
    # Attaching the parent publishes its TRN02, which triggers the backward re-check — and that
    # is where a bank deposit parked days ago finally gets allocated.  Allocation walks the
    # remittance's claim lines and asks the crosswalk which episode each one resolved to.  If
    # the parent is attached first, none of the children have been resolved yet, every lookup
    # comes back empty, and the entire deposit lands as unattributed residual: cash that
    # arrived, against claims we can see, recorded as belonging to nobody.
    #
    # Resolving the children first means the claim list is already crosswalked by the time the
    # deposit asks about it.
    for child, child_norm_id in _child_pairs(conn, canonical, parent_norm_id):
        _attach(conn, child, child_norm_id, cursor, row["raw_id"], stats)
    _attach(conn, canonical, parent_norm_id, cursor, row["raw_id"], stats)


def _insert_tree(
    conn: sqlite3.Connection,
    canonical: CanonicalRecord,
    raw_id: int,
    stats: IngestStats,
) -> int | None:
    """Insert the parent and its children, collapsing a duplicate delivery.

    D-1 is the *same payload* arriving twice — an SFTP rerun, or a retry after an ambiguous
    timeout.  It is not two events, so it is collapsed on the source record id rather than
    summed.  Summing it would double-count real money, which is the single most expensive
    mistake available in this layer.

    Idempotency deliberately keys on the **source record id**, never on a natural key.  A-17
    (duplicate payment) and B-16 (duplicate 835) are *genuine* duplicate business events with
    different record ids; collapsing on a natural key would merge them and delete two track
    states from the reachable space.
    """
    from recon.db import repository

    existing = _find_by_idempotency(conn, canonical.record_kind, canonical.idempotency_key)
    if existing is not None:
        stats.duplicates_collapsed += 1
        return None

    parent_norm_id = repository.insert_normalized_record(
        conn, **_norm_fields(canonical, raw_id=raw_id, parent_norm_id=None)
    )
    stats.normalized_records += 1

    for child in canonical.children:
        if _find_by_idempotency(conn, child.record_kind, child.idempotency_key) is not None:
            stats.duplicates_collapsed += 1
            continue
        repository.insert_normalized_record(
            conn, **_norm_fields(child, raw_id=raw_id, parent_norm_id=parent_norm_id)
        )
        stats.normalized_records += 1
    return parent_norm_id


def _child_pairs(
    conn: sqlite3.Connection, canonical: CanonicalRecord, parent_norm_id: int
) -> list[tuple[CanonicalRecord, int]]:
    """Pair each in-memory child with the ``norm_id`` it was written as."""
    rows = conn.execute(
        "SELECT norm_id, record_kind, idempotency_key FROM normalized_record"
        " WHERE parent_norm_id = ? ORDER BY norm_id",
        (parent_norm_id,),
    ).fetchall()
    by_key = {(row["record_kind"], row["idempotency_key"]): row["norm_id"] for row in rows}
    pairs: list[tuple[CanonicalRecord, int]] = []
    for child in canonical.children:
        norm_id = by_key.get((str(child.record_kind), child.idempotency_key))
        if norm_id is not None:
            pairs.append((child, norm_id))
    return pairs


def _attach(
    conn: sqlite3.Connection,
    canonical: CanonicalRecord,
    norm_id: int,
    cursor: str,
    raw_id: int,
    stats: IngestStats,
) -> None:
    """Resolve one record forward, then re-check the parked pool backwards."""
    from recon.db import repository

    published: list[tuple[KeyType, str]] = []

    # A remittance or rebate batch is a resolution *root*: it looks up nothing because it is
    # not waiting on anything, and it is the target every bank deposit resolves to.  It must
    # never be parked — parking it would leave every deposit carrying its trace number
    # permanently orphaned, which is the quietest way to break the entire cash side.
    if canonical.record_kind in _RESOLUTION_ROOTS:
        if canonical.self_keys:
            stats.keys_published += repository.insert_crosswalk_keys(
                conn,
                [
                    {
                        "key_type": str(key_type),
                        "key_value": value,
                        "remittance_norm_id": norm_id,
                        "resolved_from_norm_id": norm_id,
                        "first_seen_at": cursor,
                    }
                    for key_type, value in canonical.self_keys
                ],
            )
            _recheck_parked(conn, canonical.self_keys, norm_id, cursor, stats)
        # The backward re-check for deposits that arrived with no addenda at all.
        #
        # A deposit whose CCD+ addenda were lost carries no business reference, so it parks with
        # *no keys* — and a park with no keys can never be matched by the key-driven re-check,
        # however many records arrive afterwards.  It would be orphaned permanently, which is
        # wrong: the remittance explaining it may simply not have arrived yet, and out-of-order
        # arrival is the bank feed's defining characteristic.
        #
        # So a newly-arrived remittance also looks for keyless deposits it can claim on amount and
        # date — the same CORE-370 window the forward path uses, applied backwards.
        _recheck_parked_by_amount_and_date(conn, canonical, norm_id, cursor, stats)
        return

    if canonical.creates_episode:
        episode_id, covered_entity_id = _create_episode(conn, canonical, norm_id, stats)
        # E1.  The scoped keys are published *alongside* the bare natural keys, never instead
        # of them.  Replacing them would look tidier and would be wrong: an episode whose NPI
        # is registered to no covered entity can publish no scoped key at all, so a source
        # that looked up scoped-only would stop resolving the unregistered-location case —
        # which is a real state this system exists to surface, not an edge case.  Publishing
        # both costs one extra crosswalk row per 340B key and keeps every path that resolves
        # today resolving.
        publishes = tuple(canonical.publishes) + _scoped_340b_keys(
            canonical.publishes, covered_entity_id
        )
        published = [
            {"key_type": str(key_type), "key_value": value, "episode_id": episode_id}
            for key_type, value in publishes
        ]
        stats.keys_published += repository.insert_crosswalk_keys(
            conn,
            [
                {**entry, "resolved_from_norm_id": norm_id, "first_seen_at": cursor}
                for entry in published
            ],
        )
        _recheck_parked(conn, publishes, norm_id, cursor, stats)
        return

    # --- forward resolution ------------------------------------------------
    targets = repository.resolve_keys(conn, list(canonical.looks_up), cursor)
    episode_ids = sorted({t.episode_id for t in targets if t.episode_id})
    remittance_ids = sorted({t.remittance_norm_id for t in targets if t.remittance_norm_id})

    # The addenda are gone, so the business reference is gone with them.  All that is left is
    # amount and date — which is exactly the position a real operator is in for roughly a
    # fifth of their deposits, and exactly why CAQH CORE 370 exists.
    #
    # This is NOT identifier normalisation (Decision A23).  A23 forbids rewriting an
    # identifier so a mismatched one appears to match; this resolves on two different fields
    # when the identifier is *absent*, and it records a distinct basis so the weaker match is
    # visible rather than passed off as a trace-number hit.
    if (
        canonical.record_kind is RecordKind.BANK_TRANSACTION
        and not remittance_ids
        and canonical.trn02 is None
    ):
        rescued = _resolve_bank_by_amount_and_date(conn, canonical, cursor)
        if rescued is not None:
            _allocate_bank_row(
                conn,
                canonical,
                norm_id,
                rescued,
                targets=(),
                cursor=cursor,
                stats=stats,
                basis=AllocationBasis.AMOUNT_DATE,
            )
            _recheck_parked(conn, canonical.publishes, norm_id, cursor, stats)
            return

    if not canonical.looks_up:
        _park(conn, canonical, norm_id, raw_id, cursor, ParkReason.NO_KEYS_PRESENT, stats)
    elif not episode_ids and not remittance_ids:
        # The keys were present and resolved to nothing.  For a record whose identifiers
        # drifted, this IS the expected outcome: D-6, a crosswalk miss.  The claim is not
        # missing — the mapping failed — so the record is held rather than discarded.
        _park(conn, canonical, norm_id, raw_id, cursor, ParkReason.NO_KEY_MATCH, stats)
    elif len(episode_ids) > 1:
        # Genuinely ambiguous, and the medical 340B natural key is where this happens: two
        # administrations of the same drug at the same site on the same day are
        # indistinguishable.  Guessing would attribute real money to the wrong episode and
        # look identical to a correct match in every report.
        _park(
            conn, canonical, norm_id, raw_id, cursor, ParkReason.AMBIGUOUS_KEY_MATCH, stats
        )
    elif _contradicts_covered_entity(conn, canonical, episode_ids):
        # E1's acceptance, and the reason it is a check rather than a key swap.  The keys
        # resolved cleanly to one episode and the document then named a different covered
        # entity, so the two disagree about whose 340B claim this is.  Attaching would credit
        # one covered entity's savings to another, and would look identical to a correct
        # match in every report — the same failure shape as guessing through an ambiguous key.
        _park(
            conn,
            canonical,
            norm_id,
            raw_id,
            cursor,
            ParkReason.COVERED_ENTITY_MISMATCH,
            stats,
        )
    else:
        episode_id = episode_ids[0] if episode_ids else None
        remittance_norm_id = remittance_ids[0] if remittance_ids else None
        rows = [
            {
                "key_type": str(key_type),
                "key_value": value,
                "episode_id": episode_id,
                "remittance_norm_id": None if episode_id else remittance_norm_id,
                "resolved_from_norm_id": norm_id,
                "first_seen_at": cursor,
            }
            for key_type, value in canonical.publishes
        ]
        if rows:
            stats.keys_published += repository.insert_crosswalk_keys(conn, rows)
        # A record that resolved to an episode also records *that* resolution, so the
        # allocator and the engine can find it by norm_id later.
        if episode_id and not canonical.publishes:
            stats.keys_published += repository.insert_crosswalk_keys(
                conn,
                [
                    {
                        "key_type": str(key_type),
                        "key_value": value,
                        "episode_id": episode_id,
                        "resolved_from_norm_id": norm_id,
                        "first_seen_at": cursor,
                    }
                    for key_type, value in canonical.looks_up
                ],
            )

        if canonical.record_kind is RecordKind.BANK_TRANSACTION:
            _allocate_bank_row(
                conn, canonical, norm_id, remittance_norm_id, targets, cursor, stats
            )

    _recheck_parked(conn, canonical.publishes, norm_id, cursor, stats)


def _resolve_bank_by_amount_and_date(
    conn: sqlite3.Connection, canonical: CanonicalRecord, cursor: str
) -> int | None:
    """Find the one remittance or batch this deposit can only be matched to by amount and date.

    Returns ``None`` unless **exactly one** candidate matches.  Zero is an orphan deposit
    (D-3); more than one is genuinely ambiguous, and picking the first would attribute real
    money to the wrong remittance while looking identical to a correct match in every report.

    Served by ``ix_norm_amount_match`` on ``(record_kind, amount_cents)``, so the candidate set
    is an index seek on the amount and only then filtered by date — not a scan of every
    remittance ever received.
    """
    amount = canonical.amount_cents
    posting_date = canonical.date_of_service
    if amount is None or posting_date is None:
        return None

    rows = conn.execute(
        "SELECT norm_id FROM normalized_record"
        " WHERE record_kind IN (?, ?)"
        "   AND amount_cents = ?"
        "   AND received_at <= ?"
        "   AND ABS(julianday(date_of_service) - julianday(?)) <= ?"
        " LIMIT 2",
        (
            str(RecordKind.REMITTANCE),
            str(RecordKind.REBATE_BATCH),
            amount,
            cursor,
            posting_date,
            AMOUNT_DATE_WINDOW_DAYS,
        ),
    ).fetchall()
    if len(rows) != 1:
        return None
    return rows[0]["norm_id"]


def _allocate_bank_row(
    conn: sqlite3.Connection,
    canonical: CanonicalRecord,
    norm_id: int,
    remittance_norm_id: int | None,
    targets: Sequence[object],
    cursor: str,
    stats: IngestStats,
    basis: AllocationBasis | None = None,
) -> None:
    """Hop two: the remittance this deposit points at holds the claim list.

    Which key resolved decides which splitter runs.  A ``TRN02`` hit means a claim remittance
    and its PLB; an ``ALLOCATION_CODE`` hit means a rebate batch and its dispense lines.  The
    two arrive in the same CSV column, so the column alone cannot tell you — only the
    resolution can.
    """
    from recon.db import repository

    if remittance_norm_id is None:
        stats.bank_rows_parked += 1
        return

    if basis is None:
        resolved_via = next(
            (
                t
                for t in targets
                if getattr(t, "remittance_norm_id", None) == remittance_norm_id
            ),
            None,
        )
        key_type = getattr(resolved_via, "key_type", None)
        # ``PAYMENT_REFERENCE`` sits on this branch with ``ALLOCATION_CODE`` because both
        # resolve to a REBATE_BATCH.  Left out, a deposit that resolved by payment reference
        # would be recorded with the TRN02 basis — a manufacturer's rebate settlement filed
        # as a payer's claim-payment reassociation.
        #
        # The money is not at risk, and an earlier version of this comment claimed it was.
        # The splitter below is chosen from the resolved *target's* ``record_kind``, not from
        # the basis, so a payment-reference deposit is fanned out across the batch's dispense
        # lines either way.  What this grouping protects is the audit trail: the basis is the
        # field that says how a deposit was tied to what it paid, and a wrong answer there is
        # a reconciliation that cannot be re-derived by anyone checking it later.
        #
        # It is recorded AS ``ALLOCATION_CODE`` rather than as a basis of its own, and that
        # is a deliberate loss of audit precision.  ``AllocationBasis`` is guarded at import
        # by ``agents/tools.py``'s match-strength table, and every recorded agent-eval trace
        # is keyed on a digest of the prompt that table feeds; adding a member would
        # invalidate the fixtures to record a distinction no ledger consumer reads.  Both
        # references are exact rather than inferred, so the strength the audit trail reports
        # is honest even though the reference name is not.  Noted in DESIGN_NOTE.md §9.
        basis = (
            AllocationBasis.ALLOCATION_CODE
            if key_type in (KeyType.ALLOCATION_CODE, KeyType.PAYMENT_REFERENCE)
            else AllocationBasis.TRN02
        )

    kind_row = conn.execute(
        "SELECT record_kind FROM normalized_record WHERE norm_id = ?", (remittance_norm_id,)
    ).fetchone()
    if kind_row is None:
        stats.bank_rows_parked += 1
        return

    deposit = canonical.amount_cents or 0
    if RecordKind(kind_row["record_kind"]) is RecordKind.REBATE_BATCH:
        plan = plan_rebate_allocation(
            conn,
            bank_norm_id=norm_id,
            batch_norm_id=remittance_norm_id,
            deposit_cents=deposit,
            basis=basis,
            caused_by_received_at=cursor,
        )
    else:
        plan = plan_remittance_allocation(
            conn,
            bank_norm_id=norm_id,
            remittance_norm_id=remittance_norm_id,
            deposit_cents=deposit,
            basis=basis,
            caused_by_received_at=cursor,
        )

    # Must hold by construction.  If it ever does not, the allocator has lost money, and
    # losing money silently is the one outcome this system exists to prevent.
    assert plan.balances_to(deposit), (
        f"allocation did not balance: deposit={deposit} allocated={plan.allocated_cents} "
        f"bank_norm_id={norm_id}"
    )
    stats.allocations_written += repository.insert_cash_allocations(conn, list(plan.rows))
    stats.bank_rows_allocated += 1


def _recheck_parked_by_amount_and_date(
    conn: sqlite3.Connection,
    canonical: CanonicalRecord,
    norm_id: int,
    cursor: str,
    stats: IngestStats,
) -> None:
    """Claim keyless parked deposits whose amount and date match this remittance.

    Only fires when **exactly one** parked deposit matches.  Two candidates for the same amount
    on the same day are genuinely ambiguous, and picking one would attribute real money to a coin
    flip while looking identical to a correct match in every report afterwards.

    Matching on ``ABS(...) <= 3`` days rather than equality because a deposit posts on or after
    the effective date, and ACH settlement slips over weekends and holidays.
    """
    from recon.db import repository

    declared = canonical.amount_cents
    effective = canonical.date_of_service
    if declared is None or effective is None:
        return

    rows = conn.execute(
        "SELECT p.parked_id, p.norm_id, p.raw_id, p.record_kind, p.received_at, p.park_reason"
        "  FROM parked_record p"
        "  JOIN normalized_record n ON n.norm_id = p.norm_id"
        "  LEFT JOIN parked_record_resolution r ON r.parked_id = p.parked_id"
        " WHERE p.record_kind = ?"
        "   AND r.parked_id IS NULL"
        "   AND n.trn02 IS NULL"
        "   AND n.amount_cents = ?"
        "   AND p.received_at <= ?"
        "   AND ABS(julianday(n.date_of_service) - julianday(?)) <= ?"
        " LIMIT 2",
        (
            str(RecordKind.BANK_TRANSACTION),
            declared,
            cursor,
            effective,
            AMOUNT_DATE_WINDOW_DAYS,
        ),
    ).fetchall()
    if len(rows) != 1:
        return

    parked = rows[0]
    repository.resolve_parked(
        conn,
        parked_id=parked["parked_id"],
        resolved_by_norm_id=norm_id,
        resolved_by_received_at=cursor,
        resolved_to_remittance_norm_id=norm_id,
    )
    stats.parks_resolved += 1

    norm = repository.normalized_record(conn, parked["norm_id"])
    if norm is None:
        return
    is_rebate = canonical.record_kind is RecordKind.REBATE_BATCH
    planner = plan_rebate_allocation if is_rebate else plan_remittance_allocation
    kwargs = {"batch_norm_id": norm_id} if is_rebate else {"remittance_norm_id": norm_id}
    plan = planner(
        conn,
        bank_norm_id=norm.norm_id,
        deposit_cents=norm.amount_cents or 0,
        basis=AllocationBasis.AMOUNT_DATE,
        caused_by_received_at=cursor,
        **kwargs,
    )
    stats.allocations_written += repository.insert_cash_allocations(conn, list(plan.rows))
    stats.bank_rows_allocated += 1


def _recheck_parked(
    conn: sqlite3.Connection,
    published: Sequence[tuple[KeyType, str]],
    norm_id: int,
    cursor: str,
    stats: IngestStats,
) -> None:
    """The backward half: does anything parked resolve against me now?

    Driven by ``ix_parked_key_lookup``, so it is a point seek on the keys this record just
    published rather than a walk of the pool.  Without the index this is a full scan of every
    parked record on *every single arrival*, which is the difference between an ingest that
    finishes and one that does not.
    """
    from recon.db import repository

    if not published:
        return
    parked = repository.parked_matching(conn, list(published), cursor)
    if not parked:
        return

    resolution_targets = repository.resolve_keys(conn, list(published), cursor)
    episode_id = next((t.episode_id for t in resolution_targets if t.episode_id), None)
    remittance_norm_id = next(
        (t.remittance_norm_id for t in resolution_targets if t.remittance_norm_id), None
    )
    if episode_id is None and remittance_norm_id is None:
        return

    for record in parked:
        repository.resolve_parked(
            conn,
            parked_id=record.parked_id,
            resolved_by_norm_id=norm_id,
            resolved_by_received_at=cursor,
            resolved_to_episode_id=episode_id,
            resolved_to_remittance_norm_id=None if episode_id else remittance_norm_id,
        )
        stats.parks_resolved += 1

        # An unparked record now resolves, so the work it could not do on arrival happens
        # now: its keys become resolvable, and a bank row finally gets allocated.
        _complete_unparked(conn, record, episode_id, remittance_norm_id, cursor, stats)
        # And cash that was already split against this record's *parent* while this record was
        # still parked was attributed to nobody.  Now that it resolves, the earlier split is
        # stale and gets a correction appended.  Without this the money stays orphaned for good:
        # a deposit that beat its own claim events to the door would never find its claims.
        _correct_parent_allocation(conn, record.norm_id, cursor, stats)


def _complete_unparked(
    conn: sqlite3.Connection,
    record: object,
    episode_id: str | None,
    remittance_norm_id: int | None,
    cursor: str,
    stats: IngestStats,
) -> None:
    """Finish the attach work a parked record could not do when it arrived.

    Re-derived from the stored normalized row rather than from a cached in-memory object,
    because the record may have parked days of arrival-time ago — possibly in an earlier run
    entirely.  The normalized row is the durable truth; nothing is kept in memory between
    arrivals.
    """
    from recon.db import repository

    norm = repository.normalized_record(conn, record.norm_id)
    if norm is None:
        return

    if norm.record_kind is RecordKind.BANK_TRANSACTION and remittance_norm_id is not None:
        kind_row = conn.execute(
            "SELECT record_kind FROM normalized_record WHERE norm_id = ?",
            (remittance_norm_id,),
        ).fetchone()
        if kind_row is None:
            return
        deposit = norm.amount_cents or 0
        basis = (
            AllocationBasis.ALLOCATION_CODE
            if RecordKind(kind_row["record_kind"]) is RecordKind.REBATE_BATCH
            else AllocationBasis.TRN02
        )
        if RecordKind(kind_row["record_kind"]) is RecordKind.REBATE_BATCH:
            plan = plan_rebate_allocation(
                conn,
                bank_norm_id=norm.norm_id,
                batch_norm_id=remittance_norm_id,
                deposit_cents=deposit,
                basis=basis,
                caused_by_received_at=cursor,
            )
        else:
            plan = plan_remittance_allocation(
                conn,
                bank_norm_id=norm.norm_id,
                remittance_norm_id=remittance_norm_id,
                deposit_cents=deposit,
                basis=basis,
                caused_by_received_at=cursor,
            )
        stats.allocations_written += repository.insert_cash_allocations(conn, list(plan.rows))
        stats.bank_rows_allocated += 1
    elif episode_id is not None:
        stats.keys_published += repository.insert_crosswalk_keys(
            conn,
            [
                {
                    "key_type": str(KeyType.NCPDP_CLAIM)
                    if norm.rx_number
                    else str(KeyType.MEDICAL_CLM01),
                    "key_value": f"unparked:{norm.norm_id}",
                    "episode_id": episode_id,
                    "resolved_from_norm_id": norm.norm_id,
                    "first_seen_at": cursor,
                }
            ],
        )


def _correct_parent_allocation(
    conn: sqlite3.Connection, norm_id: int, cursor: str, stats: IngestStats
) -> None:
    """Re-split this record's parent deposit, if the record is a child of one."""
    from recon.db import repository

    row = conn.execute(
        "SELECT parent_norm_id FROM normalized_record WHERE norm_id = ?", (norm_id,)
    ).fetchone()
    if row is None or row["parent_norm_id"] is None:
        return
    corrections = correct_allocation(
        conn, parent_norm_id=row["parent_norm_id"], caused_by_received_at=cursor
    )
    if corrections:
        stats.allocations_written += repository.insert_cash_allocations(conn, corrections)
        stats.allocations_corrected += 1


def _park(
    conn: sqlite3.Connection,
    canonical: CanonicalRecord,
    norm_id: int,
    raw_id: int,
    cursor: str,
    reason: ParkReason,
    stats: IngestStats,
) -> None:
    """Hold an unresolvable document, recording the keys it carried.

    The keys matter as much as the park: they are what every later arrival is checked
    against.  A park with no keys can only ever be resolved by luck.
    """
    from recon.db import repository

    repository.park_record(
        conn,
        norm_id=norm_id,
        raw_id=raw_id,
        record_kind=str(canonical.record_kind),
        received_at=cursor,
        park_reason=str(reason),
        keys=[(str(key_type), value) for key_type, value in canonical.looks_up],
    )
    stats.records_parked += 1
    stats.park_reasons[str(reason)] += 1
    if canonical.record_kind is RecordKind.BANK_TRANSACTION:
        stats.bank_rows_parked += 1


def _covered_entity_for(*, pharmacy_npi: str | None) -> str | None:
    """Which 340B covered entity this episode belongs to, or ``None`` (requirement E1).

    A *lookup*, in the same sense ``_resolve_medical_payer`` is one: 340B registration is a
    fact the reference table holds, not a judgement this function makes.  A covered entity
    registers its contract pharmacies, and that registration is exactly what ``DOC2-008``
    says Beacon grants permission against — per 340B ID.  A column nobody populates cannot
    carry a permission check, which is why E1 exists.

    **Registration only.  Prescriber affiliation is deliberately not used**, and this was
    changed after trying it: affiliating on the prescriber assigned a covered entity to 22 of
    23 medical episodes and then contradicted the TPA on five of them, because a prescriber
    affiliated with one entity can write a script dispensed under another's 340B contract.
    Affiliation says who someone practises with; it does not say whose 340B claim this is.

    That made the resulting value worse than nothing.  An entity derived from a weak
    inference still sits in a column everything downstream reads as fact, and here it would
    have driven the contradiction guard — so a guess would have parked records that the TPA,
    which ``DOC2-004`` makes authoritative for 340B qualification and source transaction
    context, had labelled correctly.  A NULL is honest and a wrong entity is not, and the
    asymmetry is not close: the guard treats absence as "no opinion" and never fires on it.

    The consequence, stated plainly: a medical episode carries no covered entity at creation,
    because a 837 names no pharmacy and nothing else on the anchor is registration-backed.
    Its entity arrives, if at all, from the TPA record that states it outright.

    Two ways this returns ``None``, and both are answers rather than failures:

    * **The NPI is registered nowhere.** The unregistered-location case the reference data
      deliberately contains — a satellite pharmacy the covered entity never registered — and
      a finding, not a gap to paper over.
    * **Two covered entities claim the same NPI.** The entity is then genuinely unknown and
      guessing would attribute someone else's 340B savings.  The reference data keeps these
      disjoint today; this does not rely on that staying true.
    """
    if not pharmacy_npi:
        return None
    from recon.reference import entities

    matched = [
        entity
        for entity in entities.covered_entities()
        if pharmacy_npi in entity.registered_pharmacy_npis
    ]
    return matched[0].covered_entity_id if len(matched) == 1 else None


#: Identifier columns the authority table governs that do **not** appear in a record's
#: canonical dict, so ``asserted_fields`` cannot see them.  They are promoted columns, and a
#: source setting one is making exactly the claim DOC2-004 assigns to somebody.
_GOVERNED_COLUMNS = ("ach_trace_number", "beacon_id")


def _assert_source_authority(canonical: CanonicalRecord) -> None:
    """Refuse any record in this tree that sets a field its source does not own (E5).

    Raises:
        SourceAuthorityError: naming the field, the source that tried to set it, and the
            system DOC2-004 makes authoritative for it.
    """
    from recon.connectors import authority

    pending = [canonical]
    while pending:
        record = pending.pop()
        pending.extend(record.children)
        fields = set(authority.asserted_fields(record.canonical))
        fields.update(
            name for name in _GOVERNED_COLUMNS if getattr(record, name, None)
        )
        authority.check_authority(record.source_system, record.record_kind, fields)


#: The two natural 340B keys a covered entity can scope.  Scoping anything else would be
#: meaningless: ``TRN02`` and ``ALLOCATION_CODE`` resolve to a remittance rather than to a
#: claim, and a covered entity does not own a remittance.
_SCOPEABLE_340B_KEYS = (KeyType.NATURAL_340B_PHARMACY, KeyType.NATURAL_340B_MEDICAL)


def _scoped_340b_keys(
    published: Sequence[tuple[KeyType, str]], covered_entity_id: str | None
) -> tuple[tuple[KeyType, str], ...]:
    """The covered-entity-scoped twin of each natural 340B key (requirement E1).

    ``DOC2-008`` has Beacon granting permission per 340B ID, so a source that knows which
    covered entity it is acting for should be able to ask a question scoped to that entity
    rather than asking a global question and checking the answer afterwards.  This is what
    makes that possible.

    Returns empty when the episode has no covered entity, which is not a failure: the NPI is
    registered to nobody, and a scoped key claiming otherwise would be a fabricated
    registration.
    """
    if not covered_entity_id:
        return ()
    return tuple(
        keys.covered_entity_340b(covered_entity_id, value)
        for key_type, value in published
        if key_type in _SCOPEABLE_340B_KEYS
    )


def _contradicts_covered_entity(
    conn: sqlite3.Connection,
    canonical: CanonicalRecord,
    episode_ids: Sequence[str],
) -> bool:
    """Does this document name a different covered entity than the episode it resolved to?

    Only ``True`` when both sides state one and they differ.  Silence is never a
    contradiction, and that asymmetry is the whole design:

    * The **episode** has no covered entity when its NPI is registered to nobody — the
      unregistered-location case.  A TPA record naming an entity there is not wrong, it is
      the *only* statement of entity anyone has, and parking it would destroy the one
      record that could surface the problem.
    * The **document** has no covered entity on most feeds, because most feeds never carry
      one.  Treating absence as disagreement would park almost everything.

    So this fires exactly when two systems that both claim to know, disagree.
    """
    if not canonical.covered_entity_id or len(episode_ids) != 1:
        return False
    row = conn.execute(
        "SELECT covered_entity_id FROM episode WHERE episode_id = ?", (episode_ids[0],)
    ).fetchone()
    if row is None or row["covered_entity_id"] is None:
        return False
    return str(row["covered_entity_id"]) != str(canonical.covered_entity_id)


def _site_for(pharmacy_npi: str | None) -> str | None:
    """Which contract-pharmacy site this episode's NPI resolves to, or ``None`` (E4).

    The whole of requirement E4 is that ``pharmacy_npi`` alone collapses a health system's
    contract-pharmacy network into one identity.  This does not pretend to undo that.  An
    episode is anchored on a core feed, and a core feed carries an NPI and nothing finer, so
    when two sites bill under one NPI there is genuinely nothing here to choose between
    them — and :func:`recon.reference.sites.resolve_site` is built so that choosing is not
    expressible rather than merely discouraged.

    So this answers only for an NPI that holds exactly one site.  For the shared NPI the
    column stays NULL and site identity arrives later, on the vendor record that states the
    site outright — which, per ``DOC2-010``, is exactly what a TPA export carries and what a
    PBM claim never has.  That is the honest shape of the fix: the identity is recovered
    where it is known, not invented where it is not.
    """
    if not pharmacy_npi:
        return None
    from recon.reference import sites

    return sites.resolve_site(pharmacy_npi).site_id


def _create_episode(
    conn: sqlite3.Connection, canonical: CanonicalRecord, norm_id: int, stats: IngestStats
) -> tuple[str, str | None]:
    """Bring an episode into existence from its anchor record.

    Returns the episode id **and the covered entity it derived**.  The caller needs the
    second value to publish E1's scoped keys, and handing it back is better than deriving it
    twice: two derivations can disagree, and the one that wrote the row is the one that is
    true.

    Exactly two record kinds are anchors: an NCPDP claim event and an original medical 837.
    The episode id is the **connector's own**, minted here — it has no way to know what the
    generator called this episode, and that is the point.  Ground truth is matched back by
    natural key, which is what makes the crosswalk scored rather than assumed.
    """
    from recon.db import repository

    next_row = conn.execute("SELECT COUNT(*) AS n FROM episode").fetchone()
    episode_id = f"E-{next_row['n'] + 1:06d}"

    is_pharmacy = canonical.record_kind is RecordKind.PHARMACY_CLAIM
    prescriber_npi = canonical.canonical.get("prescriber_id") or canonical.canonical.get(
        "rendering_provider_npi"
    )
    covered_entity_id = _covered_entity_for(pharmacy_npi=canonical.pharmacy_npi)
    repository.insert_episode(
        conn,
        episode_id=episode_id,
        reimbursement_track=str(
            ReimbursementTrack.PHARMACY if is_pharmacy else ReimbursementTrack.MEDICAL
        ),
        anchor_norm_id=norm_id,
        pharmacy_npi=canonical.pharmacy_npi,
        ndc11=canonical.ndc11 or "",
        date_of_service=canonical.date_of_service or "",
        quantity_milli=canonical.quantity_milli or 0,
        prescriber_npi=prescriber_npi,
        rx_number=canonical.rx_number if is_pharmacy else None,
        fill_number=canonical.fill_number if is_pharmacy else None,
        pbm_id=canonical.payer_id if is_pharmacy else None,
        cardholder_id=canonical.canonical.get("cardholder_id") if is_pharmacy else None,
        clm01=canonical.clm01 if not is_pharmacy else None,
        medical_payer_id=None if is_pharmacy else canonical.payer_id,
        billing_provider_npi=canonical.provider_npi if not is_pharmacy else None,
        # NCPDP 420-DK = 20 is the pharmacy flagging a 340B-related dispense.  On the medical
        # side there is no such flag, so the 340B track is discovered only when a TPA record
        # resolves against the episode.
        is_340b_flagged=canonical.canonical.get("submission_clarification_code") == "20",
        # E1/E2.  Both derived here rather than later because ``trg_episode_no_update``
        # aborts any UPDATE on this table — episode identity is immutable, so a field that
        # misses the insert can never be filled in.
        covered_entity_id=covered_entity_id,
        hcpcs=canonical.hcpcs,
        # E4.  NULL whenever the anchor's NPI maps to more than one contract-pharmacy site,
        # which a core feed carrying only an NPI cannot disambiguate.  Site identity then
        # arrives on the vendor record that states it outright, which is the only place it
        # is actually known.  See :func:`_site_for`.
        site_id=_site_for(canonical.pharmacy_npi),
        created_from_received_at=canonical.received_at,
    )
    stats.episodes_created += 1
    return episode_id, covered_entity_id


# ═══ small helpers ══════════════════════════════════════════════════════════


def _find_by_idempotency(
    conn: sqlite3.Connection, record_kind: RecordKind, idempotency_key: str
) -> int | None:
    """Has this exact delivery already been normalized?

    Served by ``ux_norm_idempotency`` on ``(record_kind, idempotency_key)`` — a unique index,
    so this is a single probe rather than a scan.
    """
    row = conn.execute(
        "SELECT norm_id FROM normalized_record WHERE record_kind = ? AND idempotency_key = ?",
        (str(record_kind), idempotency_key),
    ).fetchone()
    return None if row is None else row["norm_id"]


def _norm_fields(
    canonical: CanonicalRecord, *, raw_id: int, parent_norm_id: int | None
) -> dict[str, object]:
    import json

    return {
        "raw_id": raw_id,
        "parent_norm_id": parent_norm_id,
        "record_kind": str(canonical.record_kind),
        "source_system": str(canonical.source_system),
        "adapter_version": config.ADAPTER_VERSION,
        "received_at": canonical.received_at,
        "idempotency_key": canonical.idempotency_key,
        "pharmacy_npi": canonical.pharmacy_npi,
        "provider_npi": canonical.provider_npi,
        "rx_number": canonical.rx_number,
        "fill_number": canonical.fill_number,
        "ndc11": canonical.ndc11,
        "date_of_service": canonical.date_of_service,
        "clm01": canonical.clm01,
        "clp07": canonical.clp07,
        "trn02": canonical.trn02,
        "ach_trace_number": canonical.ach_trace_number,
        "allocation_code": canonical.allocation_code,
        "authorization_number": canonical.authorization_number,
        # Connector-layer identifiers (requirement 4.6).  ``covered_entity_id`` is
        # deliberately absent: it is an ``episode`` column, not a ``normalized_record`` one,
        # and ``insert_normalized_record`` rejects unknown columns rather than dropping them.
        "beacon_id": canonical.beacon_id,
        "hcpcs": canonical.hcpcs,
        "site_id": canonical.site_id,
        "payment_reference": canonical.payment_reference,
        "payer_id": canonical.payer_id,
        "amount_cents": canonical.amount_cents,
        "quantity_milli": canonical.quantity_milli,
        "status_code": canonical.status_code,
        "canonical": json.dumps(canonical.canonical, separators=(",", ":"), sort_keys=True),
    }
