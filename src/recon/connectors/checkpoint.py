"""What a source already gave us, so a re-run does not pull it again.

Requirements A4 and A5.  Two kinds of idempotency already exist and neither one is this
one: :func:`~recon.ingest.pipeline.ingest` deduplicates records, and
:func:`~recon.ingest.pipeline.load_from_sources` skips a document whose file hash it has
already landed.  Both fire *after* the bytes have crossed the network.  A4's acceptance
measures the other half — *the second run downloads zero bytes and ingests zero records* —
and only a decision taken before the transfer can satisfy the first clause of that sentence.

**The decision is taken before the bytes exist, which is what makes it worth taking and what
makes it fallible.**  A transport consults the predicate while it is listing, so the only
evidence available is a name and whatever the remote chose to say about modification time.
That is enough to skip on.  It is not enough to be certain.

**Modified time decides when the remote reports one; content hash decides when it does
not.**  A remote that reports no mtime is normal rather than broken — an HTTP endpoint
handing back a JSON body has no such concept — which is why
``connector_checkpoint.remote_mtime`` is nullable.  With no mtime on either side there is
nothing to compare until the bytes arrive, so :func:`should_fetch` says yes and
:func:`content_changed` rules on the result afterwards.  The transfer happens; the re-ingest
does not.  That is a weaker guarantee than A4 asks for, and it is the true one for a remote
that will not tell us when it last wrote.

**The trap, named because it is the price of the mtime path rather than a defect to fix
later.**  An mtime-only decision misses a file rewritten inside one tick of the remote's
timestamp granularity — SFTP reports whole seconds, so a vendor regenerating a feed twice
within the same second presents changed content under an unchanged mtime, and we skip it.
Storing ``content_sha256`` alongside is what keeps that miss *recoverable*: the next
transfer that does happen compares content, so the divergence surfaces as a hash that no
longer matches instead of as a file we go on believing we hold.  Dropping the hash column
would not simplify this module; it would make that failure permanent and invisible.

**``fetched_at`` is a wall-clock, and containing it is the whole point.**  §4.11 permits one
in ``connector_checkpoint`` and in logs and nowhere else, because every random stream in this
system is seeded from a canonical path string and ``ingest_batch.loaded_at`` is pinned to the
epoch, precisely so replay equals reality.  So no function here reads a clock: ``fetched_at``
is a required keyword argument the caller supplies.  A ``datetime.now()`` default would put a
real clock inside a code path the pipeline replays, and the first symptom would be a suite
that passes until midnight.  Nothing here branches on the stored value either — ``received_at``
remains the only temporal field the pipeline honours.

**No statement text lives in this module.**  §4.12 forbids this package from writing to any
table other than its own, and ``tests/test_connectors.py`` enforces something stricter still
by asserting that no query text appears anywhere under ``connectors/``.  Every table access
below is a call into :mod:`recon.db.repository`, which is where an audit of "what mutates
this database" already looks.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Callable, Mapping

from recon.db import repository

__all__ = [
    "Checkpoint",
    "FetchPredicate",
    "content_changed",
    "fetch_predicate",
    "load_checkpoints",
    "record_fetch",
    "should_fetch",
]

#: The closure a transport accepts: given a listing entry's name and whatever modification
#: time the remote reported, say whether the bytes are worth transferring.  Named so the
#: transport can annotate its parameter without importing anything about checkpoints.
FetchPredicate = Callable[[str, str | None], bool]


@dataclass(frozen=True, slots=True)
class Checkpoint:
    """The last recorded fetch of one document from one source.

    A read model over one ``connector_checkpoint`` row.  Frozen because a checkpoint is a
    fact about a run that already finished, and nothing in this module has cause to edit one
    in place — a later fetch writes a new row over it through
    :func:`recon.db.repository.record_connector_fetch`, which is the only path that changes
    the table.

    ``fetched_at`` is carried because the row carries it and is read by no logic here or
    downstream — see the module docstring.  It answers "when did this last come in" for a
    human looking at a source that has gone quiet, which is an operational question, not a
    domain one.
    """

    source_id: str
    document_name: str
    remote_mtime: str | None
    content_sha256: str
    fetched_at: str


def load_checkpoints(conn: sqlite3.Connection, source_id: str) -> dict[str, Checkpoint]:
    """Every checkpoint this source holds, keyed by document name.

    One query per source rather than one per document, and the cost is not the reason.  A
    per-document lookup would let the stored state move underneath a listing, so two
    documents in the same run could be judged against different snapshots of what we already
    have.  Reading once means a run makes one consistent set of decisions or none.
    """
    return {
        row["document_name"]: Checkpoint(
            source_id=row["source_id"],
            document_name=row["document_name"],
            remote_mtime=row["remote_mtime"],
            content_sha256=row["content_sha256"],
            fetched_at=row["fetched_at"],
        )
        for row in repository.connector_checkpoints(conn, source_id)
    }


def should_fetch(
    checkpoints: Mapping[str, Checkpoint],
    document_name: str,
    remote_mtime: str | None,
) -> bool:
    """Is this remote document worth transferring?

    ``True`` whenever we cannot prove it is unchanged, which is the safe direction of a
    genuinely asymmetric error.  A needless transfer costs bandwidth and is then absorbed by
    the file-hash check in ``load_from_sources``; a wrongly skipped one costs records that
    never arrive and never show up as an error, because nothing anywhere is looking for a
    file that was not asked for.

    So the answer is ``False`` only when all three of these hold: we have seen this name
    before, the remote reported an mtime this time, and we stored an mtime to compare it
    against.  A remote that has stopped reporting mtimes, or had not started when we last
    looked, leaves nothing to compare and gets a transfer.

    Inequality rather than "newer than", deliberately.  A vendor restoring a feed from backup
    presents an *earlier* mtime than the one on file, and a ``>`` comparison would read that
    as nothing to do — which is precisely the case where the bytes are most likely to have
    moved.  Different is different.
    """
    mark = checkpoints.get(document_name)
    if mark is None:
        return True
    if remote_mtime is None or mark.remote_mtime is None:
        return True
    return remote_mtime != mark.remote_mtime


def content_changed(checkpoints: Mapping[str, Checkpoint], content_sha256: str) -> bool:
    """Do these bytes differ from everything this source has already delivered?

    The half of the decision :func:`should_fetch` is structurally unable to make.  When a
    remote reports no modification time there is nothing to compare before the transfer, so
    the transfer happens and this rules on what came back: the download is not avoided, the
    re-ingest is.

    Deliberately blind to the document name, which is the one property a hash has that an
    mtime does not.  A vendor re-delivering identical content as ``claims_2026_03_17_v2.jsonl``
    is new to :func:`should_fetch`, which has only ever seen names, and is plainly a
    re-delivery to this — that is what the schema means by ``content_sha256`` being "what
    makes a re-delivery under a new name a no-op".

    Scoped to one source, because a checkpoint set is per source.  Two vendors shipping
    byte-identical files are two deliveries, not one, and the global backstop for that case
    is ``load_from_sources``' file-hash check, which spans every batch ever landed.
    """
    return all(mark.content_sha256 != content_sha256 for mark in checkpoints.values())


def record_fetch(
    conn: sqlite3.Connection,
    *,
    source_id: str,
    document_name: str,
    remote_mtime: str | None,
    content_sha256: str,
    fetched_at: str,
) -> None:
    """Remember that this document came in, so the next run can skip it.

    Called after a transfer succeeds and never before one.  A checkpoint written ahead of the
    bytes landing makes a failed fetch look like a completed one, and the next run then skips
    the file it never actually received — the single ordering mistake available here that
    produces silent data loss instead of a loud failure.

    ``fetched_at`` is required and has no default.  See the module docstring: reading the
    clock is the caller's business, never this function's.

    A second call for the same ``(source_id, document_name)`` replaces the stored row rather
    than appending to a history, because ``connector_checkpoint`` records where a fetch
    cursor currently stands and not everywhere it has stood.  The upsert itself, and the
    reasoning for why it is allowed in an otherwise append-only schema, live in
    :func:`recon.db.repository.record_connector_fetch`.
    """
    repository.record_connector_fetch(
        conn,
        source_id=source_id,
        document_name=document_name,
        remote_mtime=remote_mtime,
        content_sha256=content_sha256,
        fetched_at=fetched_at,
    )


def fetch_predicate(conn: sqlite3.Connection, source_id: str) -> FetchPredicate:
    """Build the ``(document_name, remote_mtime) -> bool`` closure a transport accepts.

    This is the seam, and the shape of it is the requirement A2 argument repeated one level
    down.  A transport must not know what a checkpoint is: it knows how to list and how to
    transfer, and the claim that everything downstream is untouched by which transport ran
    only holds while a transport has no opinions about persistence.  Handing it a predicate
    keeps the skip decision here, in a plain function testable with no remote at all, and
    leaves the transport one question it can answer from a listing entry.

    The snapshot is taken once, when the closure is built, for the reason given in
    :func:`load_checkpoints`.  A predicate therefore describes what we held at the start of
    the run: a document fetched and recorded mid-run does not change the answer for a later
    one in the same listing, which is what makes A5's "replaying a captured fetch sequence
    twice produces identical database state" a property of the code rather than of the order
    the remote happened to list things in.

    ``remote_mtime`` defaults to ``None`` so a transport with no concept of modification time
    can call the predicate with a name alone and get the conservative answer.
    """
    checkpoints = load_checkpoints(conn, source_id)

    def predicate(document_name: str, remote_mtime: str | None = None) -> bool:
        return should_fetch(checkpoints, document_name, remote_mtime)

    return predicate
