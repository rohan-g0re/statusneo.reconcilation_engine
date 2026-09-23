"""F2 — the vendor's declared figures against what actually arrived.

The acceptance sentence is *a feed whose trailer declares 500 records when 450 arrived fails
the batch and records the discrepancy, rather than ingesting 450 successfully*, and the
second clause is the one worth testing hard.  Detecting the shortfall is easy.  Refusing 450
clean, well-formed, individually-correct records is the part a system gets wrong.

So the headline test does not assert on a boolean.  It runs the two statements in the order
the loader runs them — reconcile, then create the batch — and asserts that the second one
never happened.
"""

from __future__ import annotations

import csv
import io
import sqlite3

import pytest

from recon.connectors import control_totals
from recon.connectors.control_totals import ControlTotalMismatch
from recon.db import repository
from recon.domain.enums import QuarantineReason
from recon.mocks import craneware_export, verity_export

#: A fixed operational stamp.  ``checked_at`` is one of the few wall-clocks §4.11 permits,
#: and the module takes it as an argument precisely so a test can pin it — see the module
#: docstring on why a ``datetime.now()`` default would be a suite that passes until midnight.
CHECKED_AT = "1970-01-01T00:00:00Z"

SOURCE_ID = "verity-accumulations"
SOURCE_FILE = "verity_accumulations_20260317.csv"
FILE_SHA = "f" * 64


# ═══ building documents in the shapes the vendors actually write ═══
#
# Assembled from the mock modules' own exported constants rather than from strings typed
# here.  A hand-typed "TRAILER" would keep passing after a vendor module renamed its control
# column, which is exactly the drift the generic parse is supposed to be caught by.


def _csv(columns, rows) -> str:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(
        buffer, fieldnames=list(columns), lineterminator="\n", extrasaction="raise", restval=""
    )
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buffer.getvalue()


def verity_shaped(detail_rows: int, declared: int | None) -> str:
    """A Verity export: control columns in front, ``record_count`` on the trailer only."""
    columns = list(verity_export.CONTROL_COLUMNS) + ["dispense_id"]
    rows = [
        {"record_type": verity_export.DETAIL_ROW, "record_count": "", "dispense_id": f"D-{n}"}
        for n in range(detail_rows)
    ]
    if declared is not None:
        rows.append({"record_type": verity_export.TRAILER_ROW, "record_count": str(declared)})
    return _csv(columns, rows)


def craneware_shaped(detail_rows: int, declared: int | None) -> str:
    """A Craneware report: two control columns in front, the declared count at the back."""
    columns = (
        list(craneware_export.CONTROL_COLUMNS)
        + ["claim_id"]
        + [craneware_export.TRAILER_COLUMN]
    )
    rows = [
        {
            "record_type": craneware_export.DETAIL_RECORD_TYPE,
            "schema_version": craneware_export.SCHEMA_VERSION,
            "claim_id": f"C-{n}",
            craneware_export.TRAILER_COLUMN: "",
        }
        for n in range(detail_rows)
    ]
    if declared is not None:
        rows.append(
            {
                "record_type": craneware_export.TRAILER_RECORD_TYPE,
                "schema_version": craneware_export.SCHEMA_VERSION,
                craneware_export.TRAILER_COLUMN: str(declared),
            }
        )
    return _csv(columns, rows)


def declaring_cents(count: int, cents: int) -> str:
    """A trailer that declares a money total as well as a count.

    Nothing in this build writes one — both delimited vendors declare a count and no sum on
    purpose — so this shape exists only here, in the column
    ``control_totals.AMOUNT_CENTS_COLUMNS`` names for it.
    """
    amount_column = control_totals.AMOUNT_CENTS_COLUMNS[0]
    columns = ["record_type", "record_count", "dispense_id", amount_column]
    rows = [
        {"record_type": "DETAIL", "dispense_id": f"D-{n}"} for n in range(count)
    ]
    rows.append(
        {
            "record_type": control_totals.TRAILER_RECORD_TYPE,
            "record_count": str(count),
            amount_column: str(cents),
        }
    )
    return _csv(columns, rows)


def load_document(
    conn,
    text: str,
    *,
    observed_count: int,
    observed_cents: int | None = None,
    file_sha256: str = FILE_SHA,
):
    """The two statements the loader runs, in the loader's order.

    This is the shape ``load_from_sources`` is meant to take: reconcile first, create the
    batch second.  Writing it out here is what lets a test assert that the second statement
    never ran — a return value could be ignored, and asserting on one would not prove the
    450 records were actually refused.
    """
    control_totals.reconcile_or_fail(
        conn,
        source_id=SOURCE_ID,
        source_file=SOURCE_FILE,
        file_sha256=file_sha256,
        text=text,
        observed_count=observed_count,
        observed_cents=observed_cents,
        checked_at=CHECKED_AT,
    )
    return repository.insert_ingest_batch(
        conn,
        source_file=SOURCE_FILE,
        source_system="TPA_PORTAL",
        file_sha256=file_sha256,
        record_count=observed_count,
        loaded_at="1970-01-01T00:00:00Z",
    )


def batches(conn) -> int:
    return conn.execute("SELECT COUNT(*) AS n FROM ingest_batch").fetchone()["n"]


# ═══ the acceptance sentence, literally ═══


def test_a_trailer_declaring_500_when_450_arrived_fails_the_batch(conn):
    """F2's acceptance, word for word: 500 declared, 450 arrived.

    The assertion that matters is the last one.  A system that detected the shortfall,
    recorded it and then landed the 450 anyway would pass every other check in this file and
    would have failed the requirement completely — the 450 records are individually perfect,
    which is the entire reason this control exists.
    """
    text = verity_shaped(detail_rows=450, declared=500)

    with pytest.raises(ControlTotalMismatch) as raised:
        load_document(conn, text, observed_count=450)

    assert "declared 500 records, 450 arrived" in str(raised.value)
    assert "50 short" in str(raised.value)
    assert batches(conn) == 0, (
        "the batch was created despite a 50-record shortfall; 450 clean records landed "
        "successfully and nothing downstream can ever tell that half a file is missing"
    )


def test_the_discrepancy_is_recorded_even_though_the_batch_never_exists(conn):
    """"...and records the discrepancy."  The evidence has to outlive the failure.

    ``control_total`` carries no foreign key onto ``ingest_batch`` for exactly this reason:
    the batch is never created, so there would be nothing for the evidence to point at.  A
    row written inside the failing batch's transaction would be rolled back with it, and the
    operator would be left with a load that stopped for no recorded reason.
    """
    with pytest.raises(ControlTotalMismatch):
        load_document(conn, verity_shaped(detail_rows=450, declared=500), observed_count=450)

    rows = repository.control_totals(conn)
    assert len(rows) == 1
    row = rows[0]
    assert row["reconciled"] == 0
    assert row["declared_count"] == 500
    assert row["observed_count"] == 450
    assert "50 short" in row["detail"]
    assert batches(conn) == 0


def test_the_failure_names_the_condition_the_enum_reserves_for_it(conn):
    """The reason code is ``CONTROL_TOTAL_MISMATCH`` and not one of the "cannot read it" five.

    The bytes are fine here.  The operator's next action is a conversation with the vendor
    about a truncated transfer, not a trip to read a malformed file — a different fix with a
    different owner, which is why the enum gives it a member of its own.
    """
    with pytest.raises(ControlTotalMismatch) as raised:
        load_document(conn, verity_shaped(detail_rows=450, declared=500), observed_count=450)

    assert raised.value.reason_code == str(QuarantineReason.CONTROL_TOTAL_MISMATCH)
    assert raised.value.control_total.declared_count == 500


def test_more_records_than_declared_also_fails(conn):
    """A file longer than its own trailer says is just as wrong, and reads as a duplicate run."""
    with pytest.raises(ControlTotalMismatch) as raised:
        load_document(conn, verity_shaped(detail_rows=12, declared=10), observed_count=12)

    assert "2 more than declared" in str(raised.value)
    assert batches(conn) == 0


# ═══ agreement: declared == observed ═══


def test_a_file_whose_trailer_agrees_reconciles_and_the_batch_lands(conn):
    batch_id = load_document(conn, verity_shaped(detail_rows=34, declared=34), observed_count=34)

    assert batch_id > 0
    assert batches(conn) == 1
    row = repository.control_totals(conn)[0]
    assert row["reconciled"] == 1
    assert row["declared_count"] == 34
    assert row["observed_count"] == 34
    assert row["detail"] is None, "a clean reconciliation has nothing to explain"


def test_a_reconciled_check_is_recorded_too_not_only_a_failing_one(conn):
    """The table is the evidence F3's readiness report reads.

    "Control totals reconciled" is a per-source claim, and a table that only ever held
    failures could not distinguish a source that reconciles cleanly every day from one that
    has never been checked at all.
    """
    load_document(conn, verity_shaped(detail_rows=3, declared=3), observed_count=3)
    assert len(repository.control_totals(conn, SOURCE_ID)) == 1


# ═══ declaring nothing vs declaring zero ═══


def test_a_source_that_declares_nothing_reconciles_vacuously(conn):
    """Most sources declare nothing, and that is not a failure.

    The six generated feeds are line-delimited JSON with no trailer and the bank export is a
    plain CSV, so if silence failed, nothing in this repository would load at all.
    """
    jsonl = '{"record_id":"R-1","received_at":"2026-03-17T00:00:00Z"}\n'

    declaration = control_totals.declared_in(jsonl)
    assert declaration is control_totals.NOTHING_DECLARED
    assert declaration.declares_nothing

    batch_id = load_document(conn, jsonl, observed_count=40)
    assert batch_id > 0

    row = repository.control_totals(conn)[0]
    assert row["reconciled"] == 1
    assert row["declared_count"] is None
    assert row["observed_count"] == 40


def test_declaring_nothing_is_stored_differently_from_declaring_zero(conn):
    """NULL is the absence of a claim; ``0`` is the claim that nothing was sent.

    Against an empty ingest the two agree, which is precisely why they have to be
    distinguishable in the row — otherwise a missing trailer read as ``0`` would report
    agreement with every file it failed to read.
    """
    load_document(conn, "irrelevant,columns\n1,2\n", observed_count=0, file_sha256="a" * 64)
    load_document(
        conn, verity_shaped(detail_rows=0, declared=0), observed_count=0, file_sha256="b" * 64
    )

    silent, declared_zero = repository.control_totals(conn)
    assert silent["declared_count"] is None
    assert declared_zero["declared_count"] == 0
    assert silent["reconciled"] == 1 and declared_zero["reconciled"] == 1


def test_declaring_zero_against_a_non_empty_ingest_fails(conn):
    """The half of the distinction that has teeth.  Silence would have passed here."""
    with pytest.raises(ControlTotalMismatch) as raised:
        load_document(conn, verity_shaped(detail_rows=0, declared=0), observed_count=7)

    assert "declared 0 records, 7 arrived" in str(raised.value)
    assert batches(conn) == 0


def test_a_format_that_declares_control_rows_with_no_trailer_is_not_silence(conn):
    """A trailer missing from a file that should carry one is the end of a cut-off transfer.

    Treating it as "declares nothing" would let the signature failure mode straight through
    the one check built for it.  It is recorded as a mismatch rather than raised out of the
    extractor, so the discrepancy is written down instead of merely stopping the run.
    """
    text = verity_shaped(detail_rows=450, declared=None)

    declaration = control_totals.declared_in(text)
    assert not declaration.declares_nothing
    assert declaration.count is None
    assert "TRAILER" in declaration.unreadable

    with pytest.raises(ControlTotalMismatch):
        load_document(conn, text, observed_count=450)
    assert repository.control_totals(conn)[0]["reconciled"] == 0
    assert batches(conn) == 0


# ═══ money: compared, never computed ═══


def test_a_declared_money_total_that_is_one_cent_out_fails(conn):
    """One cent, not a round number.  Variance is this system's whole subject.

    A tolerance here would be indistinguishable from a rounding bug in whatever produced the
    observed figure, and a control total that tolerates a difference is not a control total.
    """
    text = declaring_cents(count=12, cents=694_800)

    with pytest.raises(ControlTotalMismatch) as raised:
        load_document(conn, text, observed_count=12, observed_cents=694_801)

    assert "declared 694800 cents, 694801 cents arrived" in str(raised.value)
    assert batches(conn) == 0

    row = repository.control_totals(conn)[0]
    assert row["declared_cents"] == 694_800
    assert row["observed_cents"] == 694_801
    assert row["declared_count"] == 12, "the count agreed; only the money did not"


def test_a_declared_money_total_that_matches_exactly_reconciles(conn):
    batch_id = load_document(
        conn, declaring_cents(count=12, cents=694_800), observed_count=12, observed_cents=694_800
    )
    assert batch_id > 0
    assert repository.control_totals(conn)[0]["reconciled"] == 1


def test_a_declared_money_total_with_nothing_to_compare_it_against_fails(conn):
    """A declared total nobody checked is a declared total nobody checked."""
    with pytest.raises(ControlTotalMismatch) as raised:
        load_document(conn, declaring_cents(count=12, cents=694_800), observed_count=12)

    assert "nothing was offered to compare it against" in str(raised.value)


def test_no_money_figure_is_ever_computed_in_this_module():
    """Structural: the module names no money helper and does no money arithmetic.

    The same style of check ``tests/test_mocks.py`` applies to the formatters, for the same
    reason: a reconciliation free to compute an amount can disagree with the ledger it is
    reconciling, at which point the check is measuring itself.  ``observed_cents`` is an
    argument and ``!=`` is the only thing done to it.
    """
    import ast
    from pathlib import Path

    forbidden = {"Decimal", "to_cents", "from_cents", "apply_bps", "round_half_up", "sum"}
    source = Path(control_totals.__file__).read_text(encoding="utf-8")
    offenders = sorted(
        {
            node.id
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Name) and node.id in forbidden
        }
    )
    assert not offenders, f"control_totals.py names money arithmetic: {offenders}"


# ═══ the generic parse agrees with the vendors' own readers ═══


@pytest.mark.parametrize("detail_rows", [0, 1, 34])
def test_the_generic_parse_agrees_with_veritys_own_reader(detail_rows: int):
    """One definition of the declared figure, read two ways, agreeing.

    ``verity_export.declared_record_count`` exists so F2 never re-implements the parse.  This
    module reads the trailer generically instead of importing it, because a connector must
    not depend on the mock package — so the guard against the two drifting apart is this
    test rather than a shared import.
    """
    text = verity_shaped(detail_rows=detail_rows, declared=detail_rows)
    assert control_totals.declared_in(text).count == verity_export.declared_record_count(text)


@pytest.mark.parametrize("detail_rows", [0, 1, 39])
def test_the_generic_parse_agrees_with_craneware_own_reader(detail_rows: int):
    """The other spelling.  Craneware's count column is ``declared_record_count``."""
    text = craneware_shaped(detail_rows=detail_rows, declared=detail_rows)
    assert control_totals.declared_in(text).count == craneware_export.declared_record_count(text)


def test_both_vendor_count_columns_are_the_ones_the_mocks_actually_write():
    """If a vendor module renames its control column, this fails before a load does."""
    assert verity_export.CONTROL_COLUMNS[0] == control_totals.RECORD_TYPE_COLUMN
    assert craneware_export.CONTROL_COLUMNS[0] == control_totals.RECORD_TYPE_COLUMN
    assert verity_export.TRAILER_ROW == control_totals.TRAILER_RECORD_TYPE
    assert craneware_export.TRAILER_RECORD_TYPE == control_totals.TRAILER_RECORD_TYPE
    assert verity_export.CONTROL_COLUMNS[1] in control_totals.COUNT_COLUMNS
    assert craneware_export.TRAILER_COLUMN in control_totals.COUNT_COLUMNS


# ═══ the row is immutable ═══


def test_a_recorded_control_total_cannot_be_rewritten(conn):
    """A total you can edit is not a total.

    The declared figure is worth something only because somebody else fixed it before the
    data arrived.  A process that could reconcile a mismatch by revising the declaration has
    reconciled nothing, so the table refuses the statement outright rather than relying on
    nobody writing one.
    """
    load_document(conn, verity_shaped(detail_rows=3, declared=3), observed_count=3)

    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        conn.execute("UPDATE control_total SET declared_count = 3 WHERE control_id = 1")


def test_a_recorded_control_total_cannot_be_removed(conn):
    """The other half.  A failure you can erase is a failure that did not happen."""
    with pytest.raises(ControlTotalMismatch):
        load_document(conn, verity_shaped(detail_rows=450, declared=500), observed_count=450)

    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        conn.execute("DELETE FROM control_total WHERE control_id = 1")

    assert len(repository.control_totals(conn)) == 1


def test_the_module_offers_no_way_to_record_without_being_stopped(conn):
    """``check`` decides and writes nothing; the one function that writes also raises.

    The shape is the requirement.  A ``record()`` that filed the discrepancy and returned a
    flag would make "check, then land the 450 anyway" the shorter code path, and F2's whole
    acceptance is that the short feed does not land.
    """
    verdict = control_totals.check(
        source_id=SOURCE_ID,
        source_file=SOURCE_FILE,
        file_sha256=FILE_SHA,
        declaration=control_totals.Declaration(count=500),
        observed_count=450,
        checked_at=CHECKED_AT,
    )
    assert verdict.reconciled is False
    assert repository.control_totals(conn) == [], "check() reached the database"

    writers = [name for name in control_totals.__all__ if name.islower() and "fail" in name]
    assert writers == ["reconcile_or_fail"]


# ═══ round trip ═══


def test_what_is_written_is_what_reads_back(conn):
    """Every field of the verdict survives the trip through the table unchanged."""
    text = declaring_cents(count=12, cents=694_800)
    with pytest.raises(ControlTotalMismatch) as raised:
        load_document(conn, text, observed_count=11, observed_cents=694_801)

    written = raised.value.control_total
    read_back = repository.control_totals(conn)[0]

    assert read_back["source_id"] == written.source_id == SOURCE_ID
    assert read_back["source_file"] == written.source_file == SOURCE_FILE
    assert read_back["file_sha256"] == written.file_sha256 == FILE_SHA
    assert read_back["declared_count"] == written.declared_count == 12
    assert read_back["observed_count"] == written.observed_count == 11
    assert read_back["declared_cents"] == written.declared_cents == 694_800
    assert read_back["observed_cents"] == written.observed_cents == 694_801
    assert read_back["reconciled"] == 0 and written.reconciled is False
    assert read_back["checked_at"] == written.checked_at == CHECKED_AT
    assert read_back["detail"] == written.detail
    assert "1 short" in read_back["detail"]
    assert "694801 cents arrived" in read_back["detail"]


def test_a_reconciled_round_trip_stores_the_boolean_as_one(conn):
    """``reconciled`` is an INTEGER 0/1 under a SQL CHECK; the boolean is coerced for callers."""
    load_document(conn, verity_shaped(detail_rows=2, declared=2), observed_count=2)
    assert repository.control_totals(conn)[0]["reconciled"] == 1


def test_two_checks_of_the_same_file_append_rather_than_replace(conn):
    """Append-only, like every other evidence table here.

    A second check is a second fact about a second run.  Overwriting the first would destroy
    the record of a source that failed on Monday and reconciled on Tuesday, which is the
    history an operator is actually asking about.
    """
    with pytest.raises(ControlTotalMismatch):
        load_document(conn, verity_shaped(detail_rows=450, declared=500), observed_count=450)
    load_document(conn, verity_shaped(detail_rows=500, declared=500), observed_count=500)

    rows = repository.control_totals(conn, SOURCE_ID)
    assert [row["reconciled"] for row in rows] == [0, 1]
