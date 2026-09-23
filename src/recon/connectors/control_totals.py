"""What the vendor said arrived, against what actually did.

Requirement F2, which is Doc 2 step 5's second clause.  Every other check in this repository
looks at a record and asks whether it is well formed.  This one cannot: **the failure it
exists to catch parses cleanly.**  A file cut off halfway through transfer is still valid
JSONL, every surviving record is well formed, every amount on it is internally consistent,
and the only evidence that half the day is missing is a number the vendor wrote down before
sending.  Nothing at record level can see it, because at record level there is nothing wrong.

So the acceptance sentence is not "detect the shortfall".  It is *a feed whose trailer
declares 500 records when 450 arrived fails the batch and records the discrepancy, rather
than ingesting 450 successfully* — and the second half is the hard part.  450 clean records
are genuinely tempting to keep.

═══ Why the persistence path is also the failing path ══════════════════════════════

There is exactly one function here that touches the database, :func:`reconcile_or_fail`, and
it raises on a mismatch after it has written the evidence.  That is a deliberate shape
rather than a convenience: there is no ``record()`` that merely files the discrepancy and
hands back a boolean, because a boolean is ignorable and an exception is not.  A caller that
genuinely wants the 450 rows has to catch :class:`ControlTotalMismatch` by name, which is one
grep away from anyone auditing why a short feed landed.

The order inside it matters too.  The row is written *first* and committed, then the failure
is raised.  ``control_total`` carries no foreign key onto ``ingest_batch`` for exactly this
reason — the batch is never created, so there is nothing for the evidence to point at, and
writing the evidence inside the batch's own transaction would roll it back along with the
batch.  A failure with no record of why is the state this whole table exists to prevent.

═══ Money is compared, never computed ══════════════════════════════════════════════

Not one arithmetic operation on money happens in this module.  ``observed_cents`` is an
argument, supplied by the caller from wherever the amounts already live, and all this module
does with it is ``!=``.  It does not sum, convert, round or reformat, and it names no money
helper — so there is no way for a reconciliation to disagree with the ledger it is
reconciling, which would make the check a measurement of itself.

A declared figure is likewise carried across as the integer cents it is.  A vendor that
declares a money total as a decimal string is out of scope here on purpose: turning
``"6948.00"`` into cents is a money conversion, that belongs to :mod:`recon.money`, and none
of the four formats in this build declares a sum at all (see :func:`declared_in`).

═══ Declaring nothing is not failing ═══════════════════════════════════════════════

Most sources declare nothing.  The six generated feeds carry no trailer, and a JSON response
body has no concept of one.  Those reconcile **vacuously** — :attr:`Declaration.declares_nothing`
is true, ``declared_count`` is stored NULL, and the check passes.

That is a different state from a vendor declaring zero, and the two must not collapse into
each other.  ``0`` is a claim: *I sent you nothing today.*  NULL is the absence of a claim.
Compared against an empty ingest they agree; compared against an ingest of 40 records, the
first is a mismatch and the second is silence.  If a missing trailer returned ``0`` the
system would report agreement with every empty file it ever failed to read.

═══ ``checked_at`` ═════════════════════════════════════════════════════════════════

A required keyword argument with no default, for the reason :mod:`recon.connectors.checkpoint`
gives about ``fetched_at``.  §4.11 permits a wall-clock in ``connector_checkpoint`` and in
logs, and this is the one further place the schema grants it — ``control_total.checked_at``
is explicitly an operational stamp and not a domain date.  But permitted is not unavoidable:
every random stream in this system is seeded from a canonical string and
``ingest_batch.loaded_at`` is pinned to the epoch so that replay equals reality.  Reading a
clock in here would put a real one inside a code path the pipeline replays, and the first
symptom would be a suite that passes until midnight.  So the caller supplies it, tests supply
a fixed one, and nothing downstream reads it back.

═══ No statement text lives here ═══════════════════════════════════════════════════

Same rule as :mod:`recon.connectors.checkpoint`: §4.12 forbids this package from writing to
any table other than its own, and ``tests/test_connectors.py`` enforces something stricter
by asserting that no query text appears anywhere under ``connectors/``.  The one write below
is a call into :mod:`recon.db.repository`, which is where an audit of "what mutates this
database" already looks.
"""

from __future__ import annotations

import csv
import io
import sqlite3
from dataclasses import dataclass

from recon.db import repository
from recon.domain.enums import QuarantineReason

__all__ = [
    "AMOUNT_CENTS_COLUMNS",
    "COUNT_COLUMNS",
    "ControlTotal",
    "ControlTotalMismatch",
    "DETAIL_LIMIT",
    "Declaration",
    "NOTHING_DECLARED",
    "RECORD_TYPE_COLUMN",
    "TRAILER_RECORD_TYPE",
    "check",
    "declared_in",
    "reconcile_or_fail",
]

#: The column a delimited export uses to say what kind of row this is.  Both delimited
#: vendors in this build lead with it — ``recon.mocks.verity_export.CONTROL_COLUMNS`` and
#: ``recon.mocks.craneware_export.CONTROL_COLUMNS`` both begin here — because a *positional*
#: trailer that reuses the first data column reads as a covered entity id to a
#: :class:`csv.DictReader`, which is how a control total quietly becomes a business field.
#:
#: Its presence in the header is also the test for whether a document declares anything at
#: all.  A format with no such column is not broken; it simply makes no claim.
RECORD_TYPE_COLUMN = "record_type"

#: The value of :data:`RECORD_TYPE_COLUMN` that marks the declaring row.
TRAILER_RECORD_TYPE = "TRAILER"

#: Where a trailer's declared record count is found, in the order tried.  Two spellings
#: because two vendors chose two, and inventing a third for them to agree on would be this
#: module deciding what a vendor's file looks like.  Verity writes ``record_count``;
#: Craneware writes ``declared_record_count``.
#:
#: Read from the trailer rather than reconstructed by counting lines: a line count agrees
#: with the declaration every single day except the one the file was cut short, which is the
#: only day the number is worth anything.
COUNT_COLUMNS: tuple[str, ...] = ("record_count", "declared_record_count")

#: Where a declared money total is found, if a format ever carries one.  **Nothing in this
#: build writes it.**  Both delimited vendors declare a count and no sum, deliberately — a
#: mock that could add two amounts could disagree with the ledger — so this is the column a
#: real vendor's trailer would land in, named in one place rather than guessed at the call
#: site.  Integer cents, never a decimal string; see the module docstring.
AMOUNT_CENTS_COLUMNS: tuple[str, ...] = ("declared_amount_cents",)

#: How much of a discrepancy is kept.  The same cap ``quarantine_record``'s callers apply:
#: the column is free text and the field is for a human deciding what to do next, not a log
#: of every differing row.
DETAIL_LIMIT = 400


class ControlTotalMismatch(RuntimeError):
    """What the vendor declared and what arrived do not agree, so the batch fails.

    A ``RuntimeError`` rather than a ``ValueError``, and that is load-bearing rather than
    taste — the same reasoning
    :class:`recon.ingest.pipeline.UnknownSourceSystemError` gives.  ``_process_raw_record``
    catches ``(AdapterError, ValueError)`` and quarantines what it catches, so a
    ``ValueError`` raised on the loading path would be one refactor away from being swallowed
    into a per-record quarantine — which is precisely the wrong shape here.  A short file is
    not a bad record; every record on it is fine.  The defect is file-shaped, so the failure
    has to be file-shaped too.

    Carries the :class:`ControlTotal` that was already written, so a caller reporting the
    failure does not have to go and read it back.
    """

    def __init__(self, control_total: "ControlTotal") -> None:
        super().__init__(
            f"{control_total.source_file!r} from source {control_total.source_id!r} failed "
            f"its control total: {control_total.detail}. The batch is refused rather than "
            "landed short — a truncated file parses cleanly, so every record on it would "
            "otherwise be ingested successfully and the shortfall would be invisible."
        )
        self.control_total = control_total
        #: The named condition this maps to, for a caller that files it (requirement F2).
        self.reason_code = str(QuarantineReason.CONTROL_TOTAL_MISMATCH)


@dataclass(frozen=True, slots=True)
class Declaration:
    """What a document says about its own size — which is usually nothing.

    Frozen because a declaration is a fact the vendor fixed before the bytes moved.  A
    process that can revise the declared figure to make it agree has reconciled nothing,
    which is the same argument the table's immutability triggers make one layer down.
    """

    #: Declared record count, or ``None`` when the document declares no count.  ``0`` is a
    #: claim that nothing was sent and is compared like any other number.
    count: int | None = None
    #: Declared money total in integer cents, or ``None``.  Nothing in this build declares
    #: one; see :data:`AMOUNT_CENTS_COLUMNS`.
    cents: int | None = None
    #: Why a document whose format *does* declare control rows could not be read, or
    #: ``None``.  Distinct from declaring nothing: a file that should carry a trailer and
    #: does not was very likely cut short, and treating that as silence would let the
    #: signature failure mode through the one check built for it.
    unreadable: str | None = None

    @property
    def declares_nothing(self) -> bool:
        """True only when the document made no claim, and no claim went missing."""
        return self.count is None and self.cents is None and self.unreadable is None


#: The answer for every source that declares no control figures — which is most of them.
#: A shared singleton so ``declaration is NOTHING_DECLARED`` reads at a call site.
NOTHING_DECLARED = Declaration()


@dataclass(frozen=True, slots=True)
class ControlTotal:
    """One declared-against-observed comparison: the verdict, and the row that records it.

    Every field maps to a ``control_total`` column, so a caller never has to assemble the
    row itself and the verdict and the evidence cannot drift apart.
    """

    source_id: str
    source_file: str
    file_sha256: str
    declared_count: int | None
    observed_count: int
    declared_cents: int | None
    observed_cents: int | None
    reconciled: bool
    checked_at: str
    #: Free text naming the discrepancy, or ``None`` when there is none.
    detail: str | None

    @property
    def declared_nothing(self) -> bool:
        """Did this reconcile vacuously — no claim made, so nothing to disagree with?"""
        return self.declared_count is None and self.declared_cents is None


def declared_in(text: str) -> Declaration:
    """Read whatever a document declares about its own size.

    **Silence is the common answer and the correct one.**  A document with no
    :data:`RECORD_TYPE_COLUMN` in its header makes no claim, and that covers every format
    the pipeline loads today: the six generated feeds are line-delimited JSON with no
    trailer, the bank export is a plain CSV, and a JSON response body has no concept of one.
    Those get :data:`NOTHING_DECLARED` and reconcile vacuously.

    **A format that declares control rows and then has no trailer is a different answer.**
    It gets a :class:`Declaration` marked :attr:`~Declaration.unreadable`, which reconciles
    as a mismatch.  Both vendor readers this mirrors —
    ``recon.mocks.verity_export.declared_record_count`` and its Craneware twin — raise in
    that case, and raising is right for a reader used on its own.  It is wrong here: an
    exception out of the extractor would abort the load before anything could be written
    down, and the acceptance sentence asks for the discrepancy to be *recorded*, not merely
    to stop the run.  A trailer missing from a file that should have one is, after all, the
    exact shape of a transfer cut off at the end.

    The parse is generic rather than a call into either vendor module: the count is read
    from the trailer the file carries, not reconstructed, so there is no second definition
    of the declared figure to drift from the first.  ``tests/test_control_totals.py`` holds
    it to agreeing with both vendor readers on their own output.
    """
    reader = csv.DictReader(io.StringIO(text, newline=""))
    header = reader.fieldnames or ()
    if RECORD_TYPE_COLUMN not in header:
        return NOTHING_DECLARED

    trailer: dict[str, str | None] | None = None
    for row in reader:
        if row.get(RECORD_TYPE_COLUMN) == TRAILER_RECORD_TYPE:
            trailer = row
    if trailer is None:
        return Declaration(
            unreadable=(
                f"the document declares a {RECORD_TYPE_COLUMN!r} column but carries no "
                f"{TRAILER_RECORD_TYPE} row, so the figure it was supposed to declare is "
                "missing — which is what the end of a cut-off transfer looks like"
            )
        )

    count = _first_int(trailer, COUNT_COLUMNS)
    cents = _first_int(trailer, AMOUNT_CENTS_COLUMNS)
    if count is None and cents is None:
        return Declaration(
            unreadable=(
                f"the {TRAILER_RECORD_TYPE} row declares nothing readable; expected one of "
                f"{', '.join(COUNT_COLUMNS)} to carry a count"
            )
        )
    return Declaration(count=count, cents=cents)


def check(
    *,
    source_id: str,
    source_file: str,
    file_sha256: str,
    declaration: Declaration,
    observed_count: int,
    observed_cents: int | None = None,
    checked_at: str,
) -> ControlTotal:
    """Compare a declaration against what arrived.  Pure: no database, no clock.

    ``observed_count`` is the number of **records** the document yielded, never the number
    of lines in it.  A delimited export's header and trailer are not records, so a naive line
    count reports two for an empty file — the same trap ``recon.mocks.coverage`` names, and
    here it would report a shortfall of two on every file that was in fact complete.

    ``observed_cents`` is supplied rather than derived, which is the whole of this module's
    answer to "money is only ever compared here".  Nothing below adds anything up.  Where a
    money figure is declared and none is offered to compare it against, that is a mismatch
    and not a pass: a declared total nobody checked is a declared total nobody checked.
    """
    reasons: list[str] = []

    if declaration.unreadable is not None:
        reasons.append(declaration.unreadable)

    if declaration.count is not None and declaration.count != observed_count:
        shortfall = declaration.count - observed_count
        direction = f"{shortfall} short" if shortfall > 0 else f"{-shortfall} more than declared"
        reasons.append(
            f"declared {declaration.count} records, {observed_count} arrived ({direction})"
        )

    if declaration.cents is not None:
        if observed_cents is None:
            reasons.append(
                f"declared a total of {declaration.cents} cents and nothing was offered to "
                "compare it against"
            )
        elif declaration.cents != observed_cents:
            reasons.append(
                f"declared {declaration.cents} cents, {observed_cents} cents arrived"
            )

    detail = "; ".join(reasons)[:DETAIL_LIMIT] if reasons else None
    return ControlTotal(
        source_id=source_id,
        source_file=source_file,
        file_sha256=file_sha256,
        declared_count=declaration.count,
        observed_count=observed_count,
        declared_cents=declaration.cents,
        observed_cents=observed_cents,
        reconciled=not reasons,
        checked_at=checked_at,
        detail=detail,
    )


def reconcile_or_fail(
    conn: sqlite3.Connection,
    *,
    source_id: str,
    source_file: str,
    file_sha256: str,
    text: str,
    observed_count: int,
    observed_cents: int | None = None,
    checked_at: str,
) -> ControlTotal:
    """Record what this document declared against what it yielded, and refuse a shortfall.

    The only entry point that touches the database, and the only one that can, which is the
    design rather than an accident of factoring.  There is deliberately no way to file a
    control total without also being stopped by it: a function that recorded the discrepancy
    and returned a flag would make "check, then land the 450 anyway" the shorter of the two
    code paths, and F2's acceptance is specifically that the short feed does *not* land.

    Returns the :class:`ControlTotal` on agreement.  Raises :class:`ControlTotalMismatch`
    otherwise — **after** the row is written and committed, so the evidence survives the
    failure it explains.  See the module docstring on why the table has no foreign key onto
    ``ingest_batch``.

    Call it *before* the batch row is created.  A batch written first and abandoned
    afterwards leaves a batch with no records, which reads downstream as a file that arrived
    empty rather than as one that was refused.
    """
    control_total = check(
        source_id=source_id,
        source_file=source_file,
        file_sha256=file_sha256,
        declaration=declared_in(text),
        observed_count=observed_count,
        observed_cents=observed_cents,
        checked_at=checked_at,
    )
    repository.record_control_total(
        conn,
        source_id=control_total.source_id,
        source_file=control_total.source_file,
        file_sha256=control_total.file_sha256,
        declared_count=control_total.declared_count,
        observed_count=control_total.observed_count,
        declared_cents=control_total.declared_cents,
        observed_cents=control_total.observed_cents,
        reconciled=control_total.reconciled,
        checked_at=control_total.checked_at,
        detail=control_total.detail,
    )
    if not control_total.reconciled:
        raise ControlTotalMismatch(control_total)
    return control_total


def _first_int(row: dict[str, str | None], columns: tuple[str, ...]) -> int | None:
    """The first of ``columns`` this row carries as a readable whole number, else ``None``.

    Blank cells are skipped rather than read as zero.  Both delimited vendors write the
    count column empty on every detail row and populate it only on the trailer, so an empty
    cell means "not this column", and a zero here would be indistinguishable from a vendor
    declaring an empty file.
    """
    for column in columns:
        value = row.get(column)
        if value is None:
            continue
        value = value.strip()
        if not value:
            continue
        try:
            return int(value)
        except ValueError:
            return None
    return None
