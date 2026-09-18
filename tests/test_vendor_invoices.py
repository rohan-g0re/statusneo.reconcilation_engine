"""``verity_invoices`` — the one vendor dataset that reports money, and what it may say.

For two waves this dataset was mapped, contract-checked and refused at the adapter with "it
does not describe a qualification decision".  True, and it left the rebate money unreachable
through the vendor door.  This file is the resolution and, more importantly, the record of
**why the obvious resolution was not available**.

The obvious one is a ``REBATE_BATCH`` parent with ``REBATE_DISPENSE_LINE`` children, matching
what ``_adapt_rebate_batch`` builds from the 340B feed.  The system refuses it outright:
``connectors.authority._REBATE_STATUS`` lists both kinds with ``authoritative = {BEACON,
MANUFACTURER_REBATE}``, and DOC2-004 is the reason.  A TPA's invoice is a fact Verity owns —
this is what we billed, against this batch — about a payment decision Verity did not make.
So the first test here asks the authority table directly, because the design is downstream of
that answer and a reader who does not see the refusal cannot tell this kind apart from
over-engineering.

Three separate mechanisms would each have made a bucketed kind wrong, and every one fails
quietly:

* ``REBATE_BATCH`` is in ``pipeline._RESOLUTION_ROOTS``, where ``_attach`` returns before it
  reads ``looks_up`` — an invoice line modelled as a batch resolves to no episode at all.
* One invoice row per batch means N parents publishing one ``ALLOCATION_CODE``.  A second
  publisher does not produce a wrong answer, it produces *no* answer: every deposit carrying
  that code parks as ``AMBIGUOUS_KEY_MATCH``.
* ``REBATE_DISPENSE_LINE`` is the C-14 guard in a new place.  ``verity_export`` formats the
  same generated dispense the 340B feed already reports, so the invoice amount *is* the rebate
  line amount — one payment through two doors stamps "duplicate rebate payment" on every paid
  episode.

Counts are read off the export, never typed in.
"""

from __future__ import annotations

import csv
import io
import sqlite3
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Sequence

import pytest

from recon.config import CuratedSpine, load_settings
from recon.connectors import authority, registry, vendors
from recon.connectors.transport import Document, LocalDirectoryTransport
from recon.domain.enums import RecordKind, SourceSystem
from recon.engine import dimensions, run as engine_run
from recon.generators import orchestrator
from recon.ingest import pipeline
from recon.mocks import coverage

SOURCE_ID = "verity_invoices"

#: Matches ``tests/test_vendor_connector_leg.py``; nothing real is in 2099, so a row that
#: silently inherited the fetch stamp instead of its own column is visible rather than
#: plausible.
FETCHED_AT = "2099-01-02T03:04:05Z"


@pytest.fixture(scope="module")
def vendor_files(tmp_path_factory) -> Path:
    settings = load_settings(
        "demo",
        data_dir=tmp_path_factory.mktemp("vendor-invoices"),
        curated_spine=CuratedSpine.RECORDED,
    )
    orchestrator.write_outputs(settings, orchestrator.generate(settings))
    coverage.write_all(settings)
    return settings.vendor_dir()


# ═══ the file, read independently of the pipeline ═══


def _path(vendor_dir: Path) -> Path:
    mapping = vendors.mapping_for(SOURCE_ID)
    for path in sorted(vendor_dir.rglob("*.csv")):
        if path.name.startswith(mapping.filename_prefix):
            return path
    pytest.fail(f"no export starting {mapping.filename_prefix!r} in {vendor_dir}")


def _detail_rows(vendor_dir: Path) -> list[dict[str, str]]:
    """The DETAIL rows, parsed here rather than through the reader under test."""
    text = _path(vendor_dir).read_text(encoding="utf-8")
    return [
        row
        for row in csv.DictReader(io.StringIO(text, newline=""))
        if row.get("record_type") == "DETAIL"
    ]


def _source(vendor_dir: Path) -> registry.Source:
    path = _path(vendor_dir)
    return registry.vendor_sources(
        {SOURCE_ID: str(path.parent)},
        filenames={SOURCE_ID: (path.name,)},
        transport=LocalDirectoryTransport(path.parent),
        enabled=True,
    )[0]


def _run(conn: sqlite3.Connection, sources: Sequence[registry.Source]) -> pipeline.IngestStats:
    stats = pipeline.load_from_sources(conn, sources, fetched_at=FETCHED_AT)
    pipeline.ingest(conn, stats=stats)
    return stats


# ═══ why the kind is what it is ═══


@pytest.mark.parametrize("kind", [RecordKind.REBATE_BATCH, RecordKind.REBATE_DISPENSE_LINE])
def test_a_tpa_source_may_not_emit_the_rebate_kinds_at_all(kind: RecordKind) -> None:
    """The constraint the whole design follows from, asked of the authority table itself.

    Not a restatement of the adapter's choice — the opposite direction.  If this ever returns
    no breach, ``TPA_INVOICE_LINE`` has stopped being necessary and someone should be told,
    because the simpler modelling would then be available and better.
    """
    for source_system in (SourceSystem.TPA_VERITY, SourceSystem.TPA_CRANEWARE):
        found = authority.breaches(source_system, kind, frozenset())
        assert found, (
            f"{source_system.value} is now permitted to emit {kind.value}; DOC2-004 gave "
            "rebate status to Beacon and the manufacturer, so this is either a deliberate "
            "change to the authority table or a hole in it"
        )
    assert not authority.breaches(SourceSystem.MANUFACTURER_REBATE, kind, frozenset()), (
        "the manufacturer must still be able to emit its own rebate kinds, or the 340B feed "
        "stops ingesting"
    )


def test_the_invoice_kind_is_cited_but_moves_no_dimension() -> None:
    """Gathered and visible, contributing to nothing — the Beacon wave's arrangement, reused.

    Both halves matter and they fail in opposite directions.  Absent from ``_ROLE_BY_KIND``
    the kind is not refused, it is silently filed as the episode's *pharmacy adjudication*
    evidence, because ``_evidence_rows`` reads ``.get(kind, ADJUDICATION)``.  Present in
    ``_KIND_BUCKETS`` it would start moving verdicts.
    """
    assert RecordKind.TPA_INVOICE_LINE not in dimensions._KIND_BUCKETS, (
        "an invoice line now feeds a dimension; if that bucket is 'rebate_lines' then "
        "_read_rebate_lines sums the vendor's relayed figure alongside the manufacturer's "
        "own, which is C-14 on every paid episode"
    )
    assert (
        engine_run._ROLE_BY_KIND.get(str(RecordKind.TPA_INVOICE_LINE))
        is not None
    ), "unmapped, so every invoice line is cited as the episode's pharmacy adjudication"


# ═══ what lands ═══


def test_every_detail_row_becomes_exactly_one_invoice_line(conn, vendor_files) -> None:
    """One row in, one record out — no batch parent, no children, nothing collapsed."""
    rows = _detail_rows(vendor_files)
    assert rows, "the export has no detail rows, so everything below would pass vacuously"

    stats = _run(conn, [_source(vendor_files)])
    assert stats.quarantined == 0
    assert stats.normalized_records == len(rows)

    kinds = Counter(
        row["record_kind"]
        for row in conn.execute("SELECT record_kind FROM normalized_record").fetchall()
    )
    assert kinds == {str(RecordKind.TPA_INVOICE_LINE): len(rows)}, (
        f"expected {len(rows)} invoice lines and nothing else, got {dict(kinds)}"
    )
    parents = conn.execute(
        "SELECT COUNT(*) FROM normalized_record WHERE parent_norm_id IS NOT NULL"
    ).fetchone()[0]
    assert parents == 0, "an invoice line acquired a parent; there is no batch record here"


def test_the_line_amount_is_carried_and_the_batch_total_is_never_the_row_s_amount(
    conn, vendor_files
) -> None:
    """Both figures survive verbatim, and neither becomes money the engine can sum.

    The fragmentation failure has a shape worth naming: every row repeats
    ``batch_total_rebate_amount``, so a row that claimed it would multiply the batch by its
    line count.  On this export that is invisible for most batches, because most carry a
    single line and the two figures are equal — which is exactly why the assertion is written
    against a batch with more than one line wherever the data has one.
    """
    rows = _detail_rows(vendor_files)
    _run(conn, [_source(vendor_files)])

    landed = {
        row["idempotency_key"].split("|", 1)[1]: row
        for row in conn.execute(
            "SELECT idempotency_key, amount_cents, canonical FROM normalized_record"
        ).fetchall()
    }
    import json

    for row in rows:
        record = landed[row["invoice_number"]]
        canonical = json.loads(record["canonical"])
        assert record["amount_cents"] is None
        assert canonical["relayed_invoice_line_amount"] == row["invoice_line_amount"]
        assert canonical["relayed_batch_total_amount"] == row["batch_total_rebate_amount"]

    multi = [
        code
        for code, count in Counter(row["rebate_allocation_code"] for row in rows).items()
        if count > 1
    ]
    if not multi:  # pragma: no cover - depends on the spine the generator drew
        pytest.skip("no batch in this export carries more than one line")
    for code in multi:
        lines = [row for row in rows if row["rebate_allocation_code"] == code]
        totals = {row["batch_total_rebate_amount"] for row in lines}
        assert len(totals) == 1, f"{code} declares two different batch totals: {totals}"
        assert any(
            Decimal(row["invoice_line_amount"]) != Decimal(row["batch_total_rebate_amount"])
            for row in lines
        ), (
            f"{code} has {len(lines)} lines and every one equals the batch total, so this "
            "test cannot tell a carried line amount from a repeated batch total"
        )


def test_lines_that_disagree_with_their_declared_batch_total_still_ingest(
    conn, vendor_files, tmp_path
) -> None:
    """The adapter never checks the two against each other, and must not start.

    The inversion plan recorded a measured disagreement — ``RBT-20251031-44202``'s lines
    summing to 26,500.00 against a declared 30,094.00 — and it turned out to have been read
    out of a superseded export that ``verity_export.remove_superseded`` now deletes.  In the
    live data every batch agrees.

    That makes this test *more* necessary rather than less.  The handling never depended on
    the disagreement being real, and a real vendor's export is entitled to one: an invoice
    shows the lines billed to us, and the batch total is the manufacturer's whole payment,
    which may cover covered entities we are not.  So the disagreement is constructed here
    rather than waited for.  Reconciling a declared total against arrivals is
    ``connectors.control_totals``' job and needs the whole document; an adapter holds one row.
    """
    path = _path(vendor_files)
    text = path.read_text(encoding="utf-8")
    reader = csv.DictReader(io.StringIO(text, newline=""))
    fieldnames = list(reader.fieldnames or ())
    rows = list(reader)

    touched = 0
    for row in rows:
        if row.get("record_type") == "DETAIL":
            # Declare a batch total that no set of lines could sum to.
            row["batch_total_rebate_amount"] = "999999.99"
            touched += 1
    assert touched, "no detail rows to disagree with"

    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)
    divergent = tmp_path / path.name
    divergent.write_text(buffer.getvalue(), encoding="utf-8", newline="")

    source = registry.vendor_sources(
        {SOURCE_ID: str(tmp_path)},
        filenames={SOURCE_ID: (divergent.name,)},
        transport=LocalDirectoryTransport(tmp_path),
        enabled=True,
    )[0]
    stats = _run(conn, [source])
    assert stats.quarantined == 0, (
        "a batch total that disagrees with its lines quarantined the rows; the adapter is "
        "deciding a control total one row at a time, without the file"
    )
    assert stats.normalized_records == touched


def test_a_reversed_invoice_line_emits_no_reversal_child(conn, vendor_files) -> None:
    """The reversal is carried and not re-declared, and the difference is a verdict.

    ``_adapt_vendor_export`` gives a reversed accumulation row a ``TPA_REVERSAL`` child, and
    that is right: the accumulation is where this vendor reports the qualification, so it is
    where the un-qualification belongs.  The invoice repeats the same four reversal columns
    about the same dispense.  Landing a second ``TPA_REVERSAL`` would not be redundant
    bookkeeping — ``dimensions`` reads ``clawed_back = bool(evidence.tpa_reversals) and paid >
    0``, so it moves episodes to C-13 "clawed back" through a door that only relays.
    """
    rows = _detail_rows(vendor_files)
    reversed_rows = [row for row in rows if row.get("reversal_status") == "REVERSED"]
    assert reversed_rows, "no reversed invoice line in this export; the test proves nothing"

    _run(conn, [_source(vendor_files)])
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM normalized_record WHERE record_kind = ?",
            (str(RecordKind.TPA_REVERSAL),),
        ).fetchone()[0]
        == 0
    ), "the invoice door emitted a reversal, which flips paid episodes to C-13"

    import json

    landed = {
        row["idempotency_key"].split("|", 1)[1]: json.loads(row["canonical"])
        for row in conn.execute(
            "SELECT idempotency_key, canonical FROM normalized_record"
        ).fetchall()
    }
    for row in reversed_rows:
        canonical = landed[row["invoice_number"]]
        assert canonical["reversal_status"] == "REVERSED"
        assert canonical["reversal_reason"] == row["reversal_reason"]
        # Copied, never re-signed. Negating an already-negative figure a second time once put
        # a reversal on the wire as a positive quantity.
        assert canonical["reversal_quantity"] == row["reversal_quantity"]


# ═══ relayed, never asserted ═══


def test_no_invoice_row_asserts_a_fact_another_party_owns(conn, vendor_files) -> None:
    """Every governed field this file touches is relayed, and the check is the real one.

    Two fields on this dataset belong to somebody else.  ``batch_line_manufacturer_status`` is
    the manufacturer's decision, and ``beacon_id`` is Beacon's identifier — whose authority
    domain says so in as many words: *"a Verity export echoes a beacon_id as a lookup handle,
    and a caller resolving a key rather than asserting a fact should not pass it here."*

    The second one bit during this build.  The first version of the adapter wrote ``beacon_id``
    into ``canonical`` and promoted it to the column, and every invoice row breached — the same
    shape as the nine Craneware rows that quarantined when a TPA was let author
    ``manufacturer_status``, one level along.  Asserted against ``authority.breaches`` rather
    than against a list of allowed names, so a governed field added later is caught here
    instead of in a quarantine queue.
    """
    _run(conn, [_source(vendor_files)])
    import json

    rows = conn.execute(
        "SELECT source_system, record_kind, canonical, beacon_id FROM normalized_record"
    ).fetchall()
    assert rows

    for row in rows:
        canonical = json.loads(row["canonical"])
        found = authority.breaches(
            SourceSystem(row["source_system"]),
            RecordKind(row["record_kind"]),
            frozenset(canonical),
        )
        assert not found, f"invoice row asserts a fact it does not own: {found[0].describe()}"
        assert row["beacon_id"] is None, (
            "the governed beacon_id column was filled by a TPA source; pipeline."
            "_GOVERNED_COLUMNS checks it, so this quarantines every row"
        )
        assert canonical["relayed_beacon_id"], "the identifier was dropped rather than relayed"
        assert "manufacturer_status" not in canonical
        assert canonical["relayed_manufacturer_status"]


# ═══ the acceptance property: money reaches the episode exactly once ═══


def test_the_invoice_leg_lands_on_episodes_and_moves_no_verdict(tmp_path_factory) -> None:
    """The whole point, measured the way the Beacon leg was measured.

    Build the dataset twice — once without the invoice source and once with it — and compare
    the verdict table.  The rows must *arrive* (otherwise the leg proves nothing) and the
    verdicts must be *identical* (otherwise a relay has become an assertion).

    This is the C-14 guard stated as an outcome rather than as a property of an enum: if the
    invoice amount ever reaches ``_read_rebate_lines``, the paid episodes in this profile
    acquire a second rebate line carrying the same figure, ``line_count > 1 and len(amounts)
    == 1`` holds, and the digest moves.
    """
    from recon.db import connection, migrate
    from recon.engine import run as engine

    settings = load_settings(
        "demo",
        data_dir=tmp_path_factory.mktemp("invoice-parity"),
        curated_spine=CuratedSpine.RECORDED,
    )
    orchestrator.write_outputs(settings, orchestrator.generate(settings))
    coverage.write_all(settings)
    vendor_dir = settings.vendor_dir()

    def build(*, with_invoices: bool) -> tuple[str, int, int]:
        handle = connection.connect(":memory:")
        try:
            migrate.ensure_schema(handle)
            pipeline.load_feeds(handle, settings.feeds_dir())
            landed = 0
            if with_invoices:
                landed = _run(handle, [_source(vendor_dir)]).normalized_records
            pipeline.ingest(handle)
            engine.run_all(handle, cursor=settings.max_cursor)
            # Dispositions, both verdict codes, and the rebate money. The money columns are
            # not decoration: C-14 is reached through ``received_rebate_cents``, so a digest
            # over codes alone could stay still while the figure behind them doubled.
            verdicts = handle.execute(
                "SELECT episode_id, episode_disposition, reimbursement_verdict_code,"
                "       rebate_verdict_code, rebate_disposition, expected_rebate_cents,"
                "       received_rebate_cents, rebate_variance_cents"
                "  FROM verdict ORDER BY episode_id, verdict_id"
            ).fetchall()
            import hashlib

            digest = hashlib.blake2b(
                "\n".join("|".join(str(value) for value in row) for row in verdicts).encode(),
                digest_size=12,
            ).hexdigest()
            return digest, len(verdicts), landed
        finally:
            handle.close()

    baseline, baseline_count, _ = build(with_invoices=False)
    with_leg, with_count, landed = build(with_invoices=True)

    assert landed > 0, "no invoice rows landed, so an unchanged digest proves nothing"
    assert baseline_count == with_count > 0
    assert with_leg == baseline, (
        f"the invoice leg moved {baseline_count} verdicts (digest {baseline} -> {with_leg}). "
        "A dataset that only relays what the TPA billed must not change a single disposition; "
        "the first place to look is whether TPA_INVOICE_LINE has entered _KIND_BUCKETS or "
        "whether amount_cents stopped being None."
    )
