"""The 340B TPA vendor leg, end to end: a real export off disk into ``normalized_record``.

Everything under ``connectors/vendors`` can be exercised on a string.  This file deliberately
cannot: each test fetches a file the formatters actually wrote, through
:func:`~recon.ingest.pipeline.load_from_sources`, through the schema check, through
``_adapt_vendor_export``, and then asserts on the rows that landed.  The unit-level reader and
mapping behaviour lives in ``tests/test_vendor_ingest.py`` and is not repeated here.

The reason a separate end-to-end file is worth its runtime is that every defect this wave
actually produced was invisible one layer up.  The mapping was right, the reader was right,
the reversal rule was right — and nine rows still quarantined, because the *adapter* wrote a
field name ``connectors/authority.py`` governs.  Nothing below the pipeline could have seen
that, and nothing above it would have reported anything worse than a slightly smaller ingest.

**Counts are derived from the file, never typed in.**  Every expected number here is read out
of the export that was just generated — detail rows, reversed rows, the relayed columns that
are populated.  A hardcoded ``34`` is a test that passes for the demo profile and silently
stops meaning anything the moment the generator draws a different spine.  What is asserted is
the *relationship*: one ``TPA_QUALIFICATION`` per DETAIL row, one ``TPA_REVERSAL`` per flagged
row, nothing for the trailer, nothing twice.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Mapping, Sequence

import pytest

from recon.config import CuratedSpine, load_settings
from recon.connectors import authority, registry, schema_registry, vendors
from recon.connectors.registry import PayloadFormat
from recon.connectors.transport import Document, LocalDirectoryTransport
from recon.connectors.vendors import ParsedDocument, SecureFileMapping
from recon.domain.enums import QuarantineReason, RecordKind, SourceSystem
from recon.generators import orchestrator
from recon.ingest import adapters, pipeline
from recon.mocks import coverage

#: The fetch stamp handed to :func:`~recon.ingest.pipeline.load_from_sources`.
#:
#: Deliberately a date no generator would ever draw.  Craneware's Claims Report declares no
#: arrival column, so its rows inherit this — and if the constant were plausible, a row that
#: had quietly picked up ``fill_date`` or ``rebate_payment_date`` instead could pass the
#: inheritance test by coincidence.  Nothing real is in 2099.
FETCHED_AT = "2099-01-02T03:04:05Z"

#: The two datasets that describe a qualification decision, and therefore the two the adapter
#: will adapt.  Named by source id and looked up through :func:`recon.connectors.vendors.
#: mapping_for`, so this list cannot drift from the mapping modules.
QUALIFICATION_SOURCE_IDS = ("verity_accumulations", "craneware_claims_report")

#: Mapped, contract-checked, readable — and not a qualification.  See
#: :func:`test_verity_invoices_is_readable_and_still_refuses_to_adapt`.
REBATE_SOURCE_ID = "verity_invoices"


@pytest.fixture(scope="module")
def vendor_files(tmp_path_factory) -> Path:
    """The real Verity and Craneware exports, generated once into a temp directory.

    The same fixture ``tests/test_file_pattern.py`` uses, for the same reason: the bytes these
    tests push through the pipeline are the bytes the formatters wrote, not a hand-typed
    stand-in that would agree with whatever the connector happened to do.  ``RECORDED`` pins
    the hand-placed spine, so the *composition* of the dataset is stable even though no test
    here depends on its size.
    """
    settings = load_settings(
        "demo",
        data_dir=tmp_path_factory.mktemp("vendor-leg"),
        curated_spine=CuratedSpine.RECORDED,
    )
    orchestrator.write_outputs(settings, orchestrator.generate(settings))
    coverage.write_all(settings)
    return settings.vendor_dir()


# ═══ reading the file back as the expectation ═══
#
# Every helper below reads the export directly and never through the pipeline.  That
# separation is the whole point: the pipeline produces the rows under test, and the file is
# what those rows are checked against.  A helper that called into the loader to build its own
# expectation would be asserting that the code agrees with itself.


def _locate(vendor_dir: Path, mapping: SecureFileMapping) -> Path:
    """Find the export a mapping describes, by the prefix the mapping itself declares.

    Verity stamps its export names from the data (``DOC2-010`` asks the name to retain vendor,
    export type and generated timestamp), so the landed name is not knowable in advance — only
    its prefix is.  That is exactly what ``SecureFileMapping.filename_prefix`` is for.
    """
    prefix = mapping.filename_prefix
    for path in sorted(vendor_dir.rglob("*.csv")):
        if path.name.startswith(prefix):
            return path
    found = sorted(p.name for p in vendor_dir.rglob("*.csv"))
    pytest.fail(f"no export starting {prefix!r} for {mapping.source_id}; on disk: {found}")


def _export(vendor_dir: Path, source_id: str) -> tuple[SecureFileMapping, Path, ParsedDocument]:
    """The mapping, the file on disk, and the file split into detail/trailer/unrecognised."""
    mapping = vendors.mapping_for(source_id)
    path = _locate(vendor_dir, mapping)
    parsed = vendors.read(mapping, Document(name=path.name, text=path.read_text(encoding="utf-8")))
    return mapping, path, parsed


def _source(vendor_dir: Path, source_id: str) -> registry.Source:
    """A registry row for one vendor dataset, bound to the directory its file landed in.

    ``enabled=True`` because the rows ship disabled — ``DOC2-016``'s Week 3 access gate — and
    a test standing in for a cleared gate is the one caller entitled to say so.  The filename
    is passed explicitly rather than listed, which is what reading a local stand-in directory
    does: the transport reads names, it does not glob.
    """
    mapping, path, _parsed = _export(vendor_dir, source_id)
    directory = path.parent
    return registry.vendor_sources(
        {mapping.source_id: str(directory)},
        filenames={mapping.source_id: (path.name,)},
        transport=LocalDirectoryTransport(directory),
        enabled=True,
    )[0]


def _run(
    conn: sqlite3.Connection, sources: Sequence[registry.Source]
) -> pipeline.IngestStats:
    """Land the documents and process them, on one stats object, at a pinned fetch stamp."""
    stats = pipeline.load_from_sources(conn, sources, fetched_at=FETCHED_AT)
    pipeline.ingest(conn, stats=stats)
    return stats


def _natural(mapping: SecureFileMapping, row: Mapping[str, str]) -> tuple[str, ...]:
    """The five natural-key components of a vendor row, gaps kept, from the field map.

    Spelled out here rather than delegated to ``vendors.natural_key`` so that the idempotency
    test below is checking the adapter's key against the *file*, not against the same helper
    the adapter called.
    """
    record = vendors.canonical(mapping, row)
    return tuple((record.get(name) or "") for name in mapping.natural_key_fields)


def _idempotency_key(
    mapping: SecureFileMapping, row: Mapping[str, str], *, document: str, line_no: int
) -> str:
    """Identity for one vendor row, assembled from the *file* rather than from the adapter.

    Two shapes, because vendors differ on whether they stamp a row id at all:

    * ``<source_id>|<row id>`` when the mapping declares a ``row_id_column``. Verity does —
      ``accumulation_id`` on accumulations, ``invoice_number`` on invoices — and both are
      unique across every row on disk.
    * ``<source_id>|<natural key>|<document>#<line>`` when it does not. Craneware stamps no
      usable row id on any of its 22 columns, so identity falls back to the claim's own
      facts plus where the row sat in the delivery.

    The fallback's weakness is real and worth naming: a snapshot row that shifts line
    between deliveries re-lands as a new record. That is the lesser evil. Keying on the
    natural key alone collapsed two genuinely different rows into one — a partial fill and
    its completion on the same Rx, same NDC, same day — with no quarantine, no park and no
    control-total shortfall, the only trace being a counter. A visible duplicate can be
    found; a silent deletion cannot.
    """
    vendor_row_id = row.get(mapping.row_id_column) if mapping.row_id_column else None
    if vendor_row_id:
        return f"{mapping.source_id}|{vendor_row_id}"
    return f"{mapping.source_id}|" + "|".join(_natural(mapping, row)) + f"|{document}#{line_no}"


def _kind_counts(conn: sqlite3.Connection) -> dict[tuple[str, str], int]:
    return {
        (row["source_system"], row["record_kind"]): row["n"]
        for row in conn.execute(
            "SELECT source_system, record_kind, COUNT(*) AS n FROM normalized_record"
            " GROUP BY 1, 2"
        )
    }


def _canonicals(conn: sqlite3.Connection, record_kind: RecordKind) -> list[dict[str, object]]:
    return [
        json.loads(row["canonical"])
        for row in conn.execute(
            "SELECT canonical FROM normalized_record WHERE record_kind = ?",
            (str(record_kind),),
        )
    ]


# ═══ D1 — one reader, one adapter, two vendors ═══


def test_both_vendors_land_the_same_record_kinds_through_one_reader_and_one_adapter(
    conn, vendor_files
):
    """Requirement D1's acceptance, measured on the rows rather than on the diff.

    ``tests/test_file_pattern.py`` proves the *source text* of the two vendor modules holds no
    parsing.  That is the structural half.  This is the observable half: run two files that
    share nothing but a shape through the fabric and the canonical vocabulary that comes out
    is identical — same record kinds, one ``TPA_QUALIFICATION`` per DETAIL row on each side.

    If this fails, the vendor difference has stopped being confined to a mapping module.  The
    likely shape is a second adapter, or a branch inside the one adapter, which is the branch
    the whole package exists to delete — and its cost is that the two vendors drift, so the
    engine's evidence map starts depending on which door a fact came through.
    """
    assert (
        adapters._DISPATCH[SourceSystem.TPA_VERITY]
        is adapters._DISPATCH[SourceSystem.TPA_CRANEWARE]
    ), "the two vendors dispatch to different adapters; D1's 'nothing else' has been spent"

    sources = [_source(vendor_files, source_id) for source_id in QUALIFICATION_SOURCE_IDS]
    assert {source.payload_format for source in sources} == {PayloadFormat.VENDOR_CSV}, (
        "both vendors must declare the same payload format, or they are not sharing a reader"
    )

    stats = _run(conn, sources)
    counts = _kind_counts(conn)

    kinds_by_system: dict[str, set[str]] = {}
    for (source_system, record_kind), _n in counts.items():
        kinds_by_system.setdefault(source_system, set()).add(record_kind)
    assert (
        kinds_by_system[str(SourceSystem.TPA_VERITY)]
        == kinds_by_system[str(SourceSystem.TPA_CRANEWARE)]
        == {str(RecordKind.TPA_QUALIFICATION), str(RecordKind.TPA_REVERSAL)}
    ), f"the two vendors produced different canonical vocabularies: {kinds_by_system}"

    expected_raw = 0
    for source in sources:
        mapping, _path, parsed = _export(vendor_files, source.source_id)
        detail = parsed.parsed_record_count
        expected_raw += detail
        landed = counts[(str(source.source_system), str(RecordKind.TPA_QUALIFICATION))]
        assert landed == detail, (
            f"{mapping.source_id}: {detail} DETAIL rows in {_locate(vendor_files, mapping).name} "
            f"became {landed} qualifications. One row is one qualification decision; a "
            "mismatch is either a dropped dispense or a row counted twice."
        )
    assert stats.raw_records == expected_raw
    assert stats.quarantined == 0


# ═══ B1 / F2 — a clean file lands clean, and the trailer is not a claim ═══


@pytest.mark.parametrize("source_id", QUALIFICATION_SOURCE_IDS)
def test_a_clean_export_quarantines_nothing(conn, vendor_files, source_id: str):
    """Nothing in a well-formed vendor export belongs in the quarantine queue.

    The queue is only useful if everything in it is worth reading.  A connector that parked one
    known-good row per file, per drop, forever would teach the people who work it to skim —
    and the first genuinely broken row would be skimmed past with the rest.

    If this fails, look at ``schema_violation`` before the adapter: requirement B1 validates
    the row in the **vendor's** spelling before adaptation, so a contract that drifted from the
    field map quarantines every row of the file with one message.
    """
    stats = _run(conn, [_source(vendor_files, source_id)])
    quarantined = conn.execute(
        "SELECT reason_code, detail FROM quarantined_record"
    ).fetchall()
    assert stats.quarantined == 0 and not quarantined, (
        f"{source_id} quarantined {len(quarantined)} rows of a clean file: "
        f"{[(row['reason_code'], row['detail'][:120]) for row in quarantined]}"
    )


@pytest.mark.parametrize("source_id", QUALIFICATION_SOURCE_IDS)
def test_the_trailer_never_becomes_a_record(conn, vendor_files, source_id: str):
    """The control row describes the file, so it must not land as a dispense.

    Both failure directions are quiet.  Land it and there is one phantom qualification per
    delivery carrying no NDC and no key — which either quarantines forever or parks forever,
    and in both cases the count the trailer declares no longer equals the count that arrived.
    Drop it *and* drop the detail rows with it and the shortfall is invisible, because the
    declared figure was read off the row that was discarded.

    So this asserts the split three ways: the file really does carry a trailer, the trailer's
    declared count equals the DETAIL rows, and exactly that many raw records landed — none of
    them carrying ``record_type = TRAILER``.
    """
    mapping, path, parsed = _export(vendor_files, source_id)
    assert parsed.trailers, f"{path.name} carries no trailer, so this test proves nothing"
    assert parsed.declared_record_count == parsed.parsed_record_count, (
        f"{path.name} is internally inconsistent before ingest ever runs: the trailer declares "
        f"{parsed.declared_record_count} and {parsed.parsed_record_count} DETAIL rows are "
        "present. Fix the generator; the leg cannot be tested against a short file."
    )
    assert not parsed.unrecognised, (
        f"{path.name} holds rows that are neither DETAIL nor TRAILER, which land as records "
        "and fail the contract — a different test from this one"
    )

    stats = _run(conn, [_source(vendor_files, source_id)])
    assert stats.raw_records == parsed.parsed_record_count, (
        f"{parsed.parsed_record_count} DETAIL rows and {stats.raw_records} raw records: the "
        "trailer was landed as a claim, or a detail row went missing with it"
    )
    landed_types = {
        json.loads(row["payload"]).get(mapping.record_type_column)
        for row in conn.execute("SELECT payload FROM raw_record")
    }
    assert landed_types == {mapping.detail_record_type}, (
        f"raw_record holds row types {sorted(str(t) for t in landed_types)}; only "
        f"{mapping.detail_record_type!r} rows are claims"
    )


# ═══ B3 — exactly one reversal record per reversed claim ═══


@pytest.mark.parametrize("source_id", QUALIFICATION_SOURCE_IDS)
def test_a_reversed_row_produces_exactly_one_reversal_record(conn, vendor_files, source_id: str):
    """Requirement B3's *"not zero, not two"*, arriving as a row rather than as a count.

    Both vendors declare a **flag** representation: the dispense and its reversal ride the one
    row, and there is no second, correcting row anywhere in the file.  So the adapter emits one
    ``TPA_REVERSAL`` child per flagged row, and the two ways to get this wrong are both silent.

    Zero — a reader that went looking for a correcting row, found none, and concluded nothing
    was reversed — keeps the rebate, and the trailer's control total agrees.  Two — a reader
    that emitted a child *and* let a second pass net the row again — loses it, and the trailer
    agrees with that too.  Neither shows up in any report downstream, which is why this is a
    test and not a code review.

    Asserted as a set equality over idempotency keys rather than as a count, so an un-reversed
    claim that somehow grew a reversal fails here as loudly as a missing one.
    """
    mapping, path, parsed = _export(vendor_files, source_id)
    reversed_keys = {
        f"{_idempotency_key(mapping, row, document=parsed.document, line_no=line_no)}|REVERSAL"
        for line_no, row in parsed.rows
        if vendors.is_reversed(mapping, row)
    }
    assert reversed_keys, (
        f"{path.name} carries no reversed row under {mapping.reversal.column!r} = "
        f"{mapping.reversal.reversed_value!r}, so this test asserts nothing"
    )

    _run(conn, [_source(vendor_files, source_id)])
    landed = [
        row["idempotency_key"]
        for row in conn.execute(
            "SELECT idempotency_key FROM normalized_record WHERE record_kind = ?",
            (str(RecordKind.TPA_REVERSAL),),
        )
    ]
    assert len(landed) == len(set(landed)), f"a reversal landed twice: {sorted(landed)}"
    assert set(landed) == reversed_keys, (
        f"{len(reversed_keys)} rows carry the reversal flag and {len(landed)} reversal records "
        f"landed. Missing: {sorted(reversed_keys - set(landed))}; "
        f"unexpected: {sorted(set(landed) - reversed_keys)}"
    )

    orphans = conn.execute(
        "SELECT COUNT(*) AS n FROM normalized_record"
        " WHERE record_kind = ? AND parent_norm_id IS NULL",
        (str(RecordKind.TPA_REVERSAL),),
    ).fetchone()["n"]
    assert orphans == 0, (
        f"{orphans} reversals landed with no qualification above them; a reversal is a child "
        "of the dispense it reverses, and one standing alone reverses nothing"
    )


# ═══ D-1 — a re-delivered export collapses instead of doubling ═══


@pytest.mark.parametrize("source_id", QUALIFICATION_SOURCE_IDS)
def test_redelivering_the_same_export_ingests_nothing_new(conn, vendor_files, source_id: str):
    """A vendor re-sending yesterday's file must collapse, not double every rebate in it.

    Both vendors deliver a **full file** on every drop, so re-delivery is the normal case
    rather than the failure case — and the vendor assigns no row id this fabric can key on
    (Craneware stamps nothing at all).  Identity is therefore the dataset plus the natural key,
    which is stable across redeliveries of the same rows.

    Two layers are asserted, because they stop different things and only one of them is about
    the vendor leg.  The file hash stops the **re-ingest** — the second
    :func:`~recon.ingest.pipeline.load_from_sources` lands zero raw records.  Re-running
    :func:`~recon.ingest.pipeline.ingest` over the raw rows that are already there bypasses
    that entirely and exercises the record-level key directly, which is the one this adapter
    chose.  Without the second half, a vendor leg with no idempotency key at all would pass.

    The stored keys are also checked against the file, so "dataset plus natural key" is a
    property somebody can read rather than a sentence in a docstring.
    """
    mapping, _path, parsed = _export(vendor_files, source_id)
    expected_keys = {
        _idempotency_key(mapping, row, document=parsed.document, line_no=line_no)
        for line_no, row in parsed.rows
    }
    # Distinct by construction now, and that is the point of the fix rather than a fact
    # about this dataset: a declared row id is unique because the vendor made it so, and the
    # fallback carries the line number, so two rows cannot share a key however alike their
    # claims are. This assertion used to read "two DETAIL rows share a natural key, so
    # collapsing them is correct and this test cannot tell a collapse from a duplicate" --
    # which excused the silent-collapse defect instead of catching it.
    assert len(expected_keys) == parsed.parsed_record_count, (
        f"{len(expected_keys)} distinct keys for {parsed.parsed_record_count} detail rows: "
        "two rows still collapse onto one identity"
    )

    source = _source(vendor_files, source_id)
    first = _run(conn, [source])
    assert first.normalized_records > 0
    landed = conn.execute("SELECT COUNT(*) AS n FROM normalized_record").fetchone()["n"]

    stored = {
        row["idempotency_key"]
        for row in conn.execute(
            "SELECT idempotency_key FROM normalized_record WHERE record_kind = ?",
            (str(RecordKind.TPA_QUALIFICATION),),
        )
    }
    assert stored == expected_keys, (
        "the qualification idempotency key is not '<source_id>|<natural key>'. "
        f"Missing: {sorted(expected_keys - stored)[:3]}; unexpected: "
        f"{sorted(stored - expected_keys)[:3]}"
    )

    second = _run(conn, [source])
    assert second.raw_records == 0, "the same bytes landed twice; file-level idempotency failed"
    assert second.normalized_records == 0

    replayed = pipeline.ingest(conn)
    assert replayed.normalized_records == 0, (
        f"replaying the raw rows produced {replayed.normalized_records} new records. The "
        "record-level key did not recognise rows it had already normalized, so a vendor "
        "re-sending yesterday's export doubles every qualification in it."
    )
    assert replayed.duplicates_collapsed == parsed.parsed_record_count
    assert conn.execute("SELECT COUNT(*) AS n FROM normalized_record").fetchone()["n"] == landed


# ═══ the dataset that is mapped and still refuses to adapt ═══


def test_verity_invoices_is_readable_and_still_refuses_to_adapt(conn, vendor_files):
    """Rebate money is not a qualification, and the adapter says so by name.

    ``verity_invoices`` is fully wired up to the point of adaptation: a mapping module claims
    it, a schema contract is registered for it, and every row passes that contract.  It is left
    out of ``_QUALIFICATION_DATASETS`` on purpose.  The dataset carries
    ``invoice_line_amount`` against ``batch_total_rebate_amount`` — a ``REBATE_BATCH`` parent
    with ``REBATE_DISPENSE_LINE`` children — and pushing it through this adapter would produce
    a qualification whose status is null and whose money is nowhere.

    The refusal is the assertion, and so is the message.  A quarantine reading *"could not be
    adapted"* sends somebody looking for a malformed file that is in fact perfectly well
    formed; one naming the dataset says the thing that is actually true, which is that nobody
    has decided how an invoice row relates to the batch above it.

    If this ever starts passing silently — rows landing as qualifications — the leg has begun
    inventing meaning, and the first visible symptom is rebate money on the ledger twice: once
    from the invoice row and once from the accumulation that is the same dispense.
    """
    mapping, path, parsed = _export(vendor_files, REBATE_SOURCE_ID)
    assert schema_registry.REGISTRY.get(REBATE_SOURCE_ID) is not None
    assert parsed.parsed_record_count > 0, f"{path.name} has no rows to refuse"
    violations = [
        detail
        for _line_no, row in parsed.rows
        if (detail := vendors.schema_violation(mapping, row)) is not None
    ]
    assert not violations, (
        f"{path.name} fails its own contract, so the refusal below would be the contract "
        f"talking rather than the adapter: {violations[:2]}"
    )
    assert REBATE_SOURCE_ID not in adapters._QUALIFICATION_DATASETS

    stats = _run(conn, [_source(vendor_files, REBATE_SOURCE_ID)])
    assert stats.raw_records == parsed.parsed_record_count, "the rows must land before refusal"
    assert stats.normalized_records == 0, (
        f"{stats.normalized_records} invoice rows were adapted as qualification decisions"
    )
    rows = conn.execute("SELECT reason_code, detail FROM quarantined_record").fetchall()
    assert len(rows) == parsed.parsed_record_count == stats.quarantined
    assert {row["reason_code"] for row in rows} == {str(QuarantineReason.UNPARSEABLE)}
    assert all(REBATE_SOURCE_ID in row["detail"] for row in rows), (
        "the quarantine detail does not name the dataset, so an operator reading the queue "
        f"cannot tell which file this was: {rows[0]['detail'][:160]!r}"
    )


# ═══ E5 / DOC2-004 — relayed, never asserted ═══


def test_a_vendor_row_relays_the_manufacturers_answer_and_never_asserts_it(conn, vendor_files):
    """The regression guard for the defect that cost this wave its first ingest.

    Both vendors print the manufacturer's answer in their own export — Craneware under
    ``manufacturer_status``, Verity under ``batch_line_manufacturer_status``.  Printing it is
    not the breach.  Writing it into the canonical ``manufacturer_status`` key is, because
    ``DOC2-004`` gives rebate status to Beacon and the manufacturer, and
    ``connectors/authority.py`` enforces that over the ``(source_system, field)`` pair.

    The first version of this adapter wrote the plain key.  Every Craneware row carrying a
    manufacturer decision — nine of them, on the committed profile — quarantined as
    ``SOURCE_AUTHORITY_BREACH``, and each took its perfectly valid *qualification* down with
    it, because a record is refused whole.  So the cost of the wrong field name was not a
    missing status; it was losing the fact the TPA actually owns.

    Under a prefixed name the row keeps both: the qualification it is authoritative for, and a
    record of what the TPA says it heard — which a dossier can show and no verdict can rest on.
    Asserted three ways: the governed names are absent everywhere, the relayed names are
    present on exactly the rows whose file cell was populated, and the authority table still
    agrees that the governed spelling would have breached.
    """
    # The table has not quietly stopped governing these, which is what would make the rest of
    # this test green for the wrong reason.
    assert [
        breach.field
        for breach in authority.breaches(
            SourceSystem.TPA_CRANEWARE,
            RecordKind.TPA_QUALIFICATION,
            {"manufacturer_status", "rejection_reason"},
        )
    ] == ["manufacturer_status", "rejection_reason"]
    assert not authority.breaches(
        SourceSystem.TPA_CRANEWARE,
        RecordKind.TPA_QUALIFICATION,
        {"relayed_manufacturer_status", "relayed_rejection_reason"},
    )

    expected: dict[str, int] = {"relayed_manufacturer_status": 0, "relayed_rejection_reason": 0}
    for source_id in QUALIFICATION_SOURCE_IDS:
        mapping, _path, parsed = _export(vendor_files, source_id)
        for _line_no, row in parsed.rows:
            record = vendors.canonical(mapping, row)
            if record.get("manufacturer_decision_status"):
                expected["relayed_manufacturer_status"] += 1
            if record.get("rejection_reason"):
                expected["relayed_rejection_reason"] += 1
    assert min(expected.values()) > 0, (
        "no row in either export carries a manufacturer answer, so nothing here would be "
        f"relayed and the guard is vacuous: {expected}"
    )

    stats = _run(
        conn, [_source(vendor_files, source_id) for source_id in QUALIFICATION_SOURCE_IDS]
    )
    assert stats.quarantined == 0, (
        "a vendor row was refused; if the reason is SOURCE_AUTHORITY_BREACH the adapter has "
        "gone back to writing a governed field name: "
        f"{[tuple(r) for r in conn.execute('SELECT reason_code, COUNT(*) FROM quarantined_record GROUP BY 1')]}"
    )

    qualifications = _canonicals(conn, RecordKind.TPA_QUALIFICATION)
    assert qualifications
    for name in ("manufacturer_status", "rejection_reason"):
        offenders = [record for record in qualifications if name in record]
        assert not offenders, (
            f"{len(offenders)} qualification records carry the governed key {name!r}. DOC2-004 "
            "gives that fact to Beacon and the manufacturer; a TPA may relay it and may not "
            "author it."
        )
    for name, count in expected.items():
        landed = sum(1 for record in qualifications if record.get(name))
        assert landed == count, (
            f"{count} rows across both exports carry a value for {name!r} and {landed} landed "
            "with one. The relay is how the fact survives without being asserted, so a drop "
            "here is evidence a dossier can no longer show."
        )


# ═══ the arrival time each vendor does or does not publish ═══


def test_craneware_rows_inherit_the_fetch_stamp_because_the_report_declares_no_timestamp(
    conn, vendor_files
):
    """The Claims Report publishes four temporal columns and not one of them is an arrival.

    ``fill_date``, ``reversal_date``, ``rebate_submitted_date``, ``rebate_payment_date`` — all
    dates, none of them a statement about when Craneware told us anything.  Promoting one
    would date a row to the day the drug left the shelf and hide every day of TPA lag behind
    it, which is the lag a 340B reconciliation exists to measure.  So these rows inherit the
    delivery's own fetch stamp, which is honestly weaker and is the honest answer.

    The pipeline orders everything it holds by ``received_at`` and evaluates at a cursor, so a
    row placed at the wrong time is not a cosmetic defect: placing one *earlier* than it was
    knowable lets the engine reach a verdict on evidence that had not arrived.

    The reversed rows are the exception and are asserted as one rather than skipped.  A
    reversed row legitimately carries its own ``reversal_date``, and
    ``vendors.received_at`` takes the later of the stamps a row holds — so those rows, and
    their reversal children, sit at the reversal rather than at the fetch.
    """
    mapping = vendors.mapping_for("craneware_claims_report")
    assert mapping.received_at_column is None, (
        "the Claims Report now declares an arrival column, so this test is describing an "
        "older file than the one on disk"
    )

    _mapping, _path, parsed = _export(vendor_files, "craneware_claims_report")
    inherits: set[str] = set()
    own: dict[str, str] = {}
    for line_no, row in parsed.rows:
        key = _idempotency_key(mapping, row, document=parsed.document, line_no=line_no)
        if vendors.is_reversed(mapping, row):
            # ``reversal_date`` is Craneware's spelling and it is a full timestamp despite the
            # name; the field map is what turns it into ``reversal_received_at``.
            own[key] = row["reversal_date"]
            own[f"{key}|REVERSAL"] = row["reversal_date"]
        else:
            inherits.add(key)
    assert inherits and own, (
        "this export has no mix of reversed and un-reversed rows, so one half of the rule "
        "below is untested"
    )
    assert FETCHED_AT not in set(own.values()), "the fixture stamp collides with real data"

    _run(conn, [_source(vendor_files, "craneware_claims_report")])
    landed = {
        row["idempotency_key"]: row["received_at"]
        for row in conn.execute("SELECT idempotency_key, received_at FROM normalized_record")
    }
    assert set(landed) == inherits | set(own)
    for key in sorted(inherits):
        assert landed[key] == FETCHED_AT, (
            f"{key} landed at {landed[key]!r} rather than the delivery's fetch stamp. The "
            "report declares no arrival column, so a real-looking timestamp here means a date "
            "column was promoted to an arrival time — most likely fill_date, which hides the "
            "TPA lag the reconciliation is measuring."
        )
    for key, stamp in sorted(own.items()):
        assert landed[key] == stamp, (
            f"{key} landed at {landed[key]!r}; the row carries its own reversal timestamp "
            f"{stamp!r} and a reversed row is true as of its reversal"
        )


def test_verity_rows_carry_their_own_arrival_time_and_never_inherit_the_fetch_stamp(
    conn, vendor_files
):
    """Verity's accumulations declare ``qualification_received_at``, so nothing falls back.

    The qualification decision is the event this dataset reports, so the moment it was decided
    is the moment the row became true.  The alternative on this file is ``fill_date``, which is
    when the drug was dispensed — days to weeks earlier, and not a fact about when Verity told
    us anything.

    A reversed row takes the **later** of its two stamps, and on this export the reversal
    genuinely postdates the qualification by months, so the rule is doing work rather than
    picking the only value present.  Later rather than earlier is the conservative direction:
    placing a row before it was knowable is the one ordering mistake a cursor-based model
    cannot recover from.

    If this fails with every row sitting at :data:`FETCHED_AT`, the declaration has been lost
    and every Verity row is being dated to the delivery — which collapses months of decision
    history onto one instant and makes replay meaningless.
    """
    mapping = vendors.mapping_for("verity_accumulations")
    assert mapping.received_at_column == "qualification_received_at"

    _mapping, _path, parsed = _export(vendor_files, "verity_accumulations")
    expected: dict[str, str] = {}
    later_than_qualification = 0
    for line_no, row in parsed.rows:
        key = _idempotency_key(mapping, row, document=parsed.document, line_no=line_no)
        stamp = row["qualification_received_at"]
        if vendors.is_reversed(mapping, row):
            reversal = row["reversal_received_at"]
            stamp = max(stamp, reversal)
            later_than_qualification += reversal > row["qualification_received_at"]
            expected[f"{key}|REVERSAL"] = stamp
        expected[key] = stamp
    assert later_than_qualification, (
        "no reversed row postdates its own qualification, so taking the later of the two "
        "stamps is indistinguishable from taking the first one"
    )

    _run(conn, [_source(vendor_files, "verity_accumulations")])
    landed = {
        row["idempotency_key"]: row["received_at"]
        for row in conn.execute("SELECT idempotency_key, received_at FROM normalized_record")
    }
    assert landed == expected, (
        "Verity rows did not land at the arrival time they publish. Rows at the fetch stamp: "
        f"{sorted(key for key, stamp in landed.items() if stamp == FETCHED_AT)[:3]}"
    )
    assert FETCHED_AT not in set(landed.values())
