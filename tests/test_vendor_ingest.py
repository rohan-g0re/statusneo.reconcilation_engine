"""The vendor leg at the unit level: the arrival stamp, the lookup, and the payload shape.

The end-to-end ingest of a vendor export is asserted elsewhere.  What is tested here is the
half of the leg that has no database in it — :func:`recon.connectors.vendors.received_at`,
:func:`recon.connectors.vendors.mapping_for`, and the ``VENDOR_CSV`` branch of
``ingest.pipeline._read_rows`` — because every defect in that half is one that reads as
success downstream.

Three of these are worth more than the rest.

**The arrival stamp is a per-dataset declaration and not a column name a reader can guess.**
A vendor export publishes no column called ``received_at``: Verity's accumulations carry
``qualification_received_at``, its invoices carry ``batch_received_at``, and Craneware's
Claims Report carries no timestamp at all — only dates.  The pipeline orders everything it
holds by arrival and evaluates at a cursor, so a row stamped from the wrong column is not a
row with a cosmetic error; it is a row placed somewhere else on the timeline, which is a
verdict reached on evidence that had or had not arrived.  ``None`` is therefore a real answer
and the fallback has to be returned *verbatim*, which is why the sentinel below is not a
timestamp: a stray real stamp leaking through would look exactly like a pass.

**A reversed row takes the LATER of its two stamps, and later is the conservative direction.**
Taking the earlier one would place a row before it was knowable, and a cursor-based model
cannot recover from that — it answers a question with evidence it did not have at the time.

**The payload lands in the VENDOR's spelling, never the canonical rename.**  The contract
registered for a source id describes the file as the vendor writes it — ``ndc_11`` on Verity,
``ndc11`` on Craneware — and ``_process_raw_record`` checks the payload against that contract
*before* an adapter is allowed to assign meaning to it.  A payload renamed on the way in would
be checked against a shape it no longer has, which does not fail loudly: it fails as a
quarantine row on every record of every file, or worse, passes and adapts into the wrong slot.
:func:`test_read_rows_yields_the_vendors_own_spelling_not_the_canonical_rename` asserts both
directions of that — the raw payload validates, and the renamed one does not.
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import replace
from pathlib import Path

import pytest

from recon.config import CuratedSpine, load_settings
from recon.connectors import schema_registry, vendors
from recon.connectors.registry import PayloadFormat
from recon.connectors.transport import Document
from recon.connectors.vendors import craneware, verity
from recon.domain.enums import SourceSystem
from recon.generators import orchestrator
from recon.ingest import pipeline
from recon.mocks import coverage

#: The delivery's own fetch stamp, deliberately **not** a timestamp.
#:
#: ``received_at`` returns this verbatim when a mapping names no arrival column, and the whole
#: value of that test is telling "the fallback came through" from "some other timestamp on the
#: row came through".  A plausible-looking ``"2026-01-01T00:00:00Z"`` would pass by accident
#: the day the reader started reaching for a column it should not read.
FETCH_SENTINEL = "FETCH-STAMP-SENTINEL"

#: Every secure-file source this build registers, and the system its registry row attributes
#: it to.  Assembled by hand rather than imported from ``registry._VENDOR_ROWS`` because a
#: test that derived its expectations from the table under test could only ever agree with it.
SOURCE_SYSTEMS: dict[str, SourceSystem] = {
    "verity_accumulations": SourceSystem.TPA_VERITY,
    "verity_invoices": SourceSystem.TPA_VERITY,
    "craneware_claims_report": SourceSystem.TPA_CRANEWARE,
}

SOURCE_IDS = tuple(SOURCE_SYSTEMS)


@pytest.fixture(scope="module")
def vendor_files(tmp_path_factory) -> Path:
    """The real Verity and Craneware exports, generated once.

    The same fixture idiom ``tests/test_file_pattern.py`` uses, and for the same reason: the
    bytes read below are the bytes the formatters wrote, so nothing here asserts against a
    placeholder that agrees with the reader by construction.
    """
    settings = load_settings(
        "demo",
        data_dir=tmp_path_factory.mktemp("vendor-ingest"),
        curated_spine=CuratedSpine.RECORDED,
    )
    orchestrator.write_outputs(settings, orchestrator.generate(settings))
    coverage.write_all(settings)
    return settings.vendor_dir()


# ═══ helpers ════════════════════════════════════════════════════════════════


def _locate(vendor_dir: Path, mapping) -> Path:
    """Find the export a mapping describes, by the prefix the mapping itself declares.

    ``DOC2-010`` asks the landed name to retain the vendor, the export type and a generated
    timestamp, so the full name is not knowable in advance — only its prefix is.  That is
    exactly what ``SecureFileMapping.filename_prefix`` is for, and using it here is the same
    lookup a listing transport performs.
    """
    for path in sorted(vendor_dir.rglob("*.csv")):
        if path.name.startswith(mapping.filename_prefix):
            return path
    found = sorted(path.name for path in vendor_dir.rglob("*.csv"))
    pytest.fail(
        f"no export starting {mapping.filename_prefix!r} for {mapping.source_id}; "
        f"on disk: {found}"
    )


def _document(vendor_dir: Path, mapping) -> Document:
    path = _locate(vendor_dir, mapping)
    return Document(name=path.name, text=path.read_text(encoding="utf-8"))


def _header(text: str) -> tuple[str, ...]:
    """The vendor's own column names, read off the file rather than off the mapping."""
    return tuple(csv.DictReader(io.StringIO(text, newline="")).fieldnames or ())


def _counts_by_record_type(text: str, mapping) -> dict[str, int]:
    """DETAIL / TRAILER / anything else, counted by parsing the file in the test.

    Derived rather than hardcoded, so this stays true when the generator's volume moves.  It
    is deliberately a second, independent parse: the assertion is that ``_read_rows`` and a
    plain ``DictReader`` agree about how many claims are in the file, and a count taken from
    the reader under test would agree with itself no matter what it did.
    """
    counts = {mapping.detail_record_type: 0, mapping.trailer_record_type: 0, "other": 0}
    for row in csv.DictReader(io.StringIO(text, newline="")):
        kind = row.get(mapping.record_type_column)
        counts[kind if kind in counts else "other"] += 1
    return counts


def _read(document: Document, mapping) -> list[tuple[str, str | None, str, SourceSystem]]:
    """``_read_rows`` under ``VENDOR_CSV``, called the way ``load_from_sources`` calls it."""
    return list(
        pipeline._read_rows(
            PayloadFormat.VENDOR_CSV,
            document.text,
            SOURCE_SYSTEMS[mapping.source_id],
            source_id=mapping.source_id,
            document=document.name,
            fetched_at=FETCH_SENTINEL,
        )
    )


def _detail_rows(document: Document, mapping) -> list[dict[str, str]]:
    return [dict(row) for _line_no, row in vendors.read(mapping, document).rows]


def _first_unreversed(document: Document, mapping) -> dict[str, str]:
    """A detail row carrying no reversal flag, so only the primary stamp is in play."""
    for row in _detail_rows(document, mapping):
        if not vendors.is_reversed(mapping, row):
            return row
    pytest.fail(f"every detail row in {document.name} is reversed; the export is not the shape "
                "this test is about — re-read it before trusting the conclusion")


def _reversed_verity_row(*, primary: str, reversal: str) -> dict[str, str]:
    """A Verity accumulations row, built by hand so the two stamps cannot be confused.

    Only the three cells :func:`vendors.received_at` reads, on purpose.  Lifting a real row
    and editing it would leave the ordering hostage to whatever the generator happened to
    write into the other seventeen columns, and the whole point of this row is that which of
    the two stamps is later is not in question.
    """
    return {
        verity.ACCUMULATIONS.reversal.column: verity.REVERSED_STATUS,
        verity.ACCUMULATIONS.received_at_column: primary,
        verity.ACCUMULATIONS.reversal.received_at_column: reversal,
    }


# ═══ received_at: the column a dataset declares ═════════════════════════════


def test_received_at_reads_the_column_the_mapping_names(vendor_files):
    """Verity's accumulations date a row by when the qualification was decided.

    If this fails the rows still land — with the delivery's fetch stamp on them instead of
    their own arrival time, so every accumulation in a drop lands at one instant and the TPA
    lag this reconciliation exists to measure has been flattened to zero.  Nothing errors and
    no queue shows it.
    """
    mapping = verity.ACCUMULATIONS
    assert mapping.received_at_column == "qualification_received_at"

    row = _first_unreversed(_document(vendor_files, mapping), mapping)
    stamp = vendors.received_at(mapping, row, fallback=FETCH_SENTINEL)

    assert stamp == row["qualification_received_at"]
    assert stamp != FETCH_SENTINEL, "the declared column was never read"


def test_received_at_returns_the_fallback_verbatim_when_the_mapping_names_none(vendor_files):
    """Craneware's Claims Report publishes no timestamp, and ``None`` is the honest answer.

    Its four temporal columns are all *dates* — ``fill_date``, ``reversal_date``,
    ``rebate_submitted_date``, ``rebate_payment_date`` — and none of them says when Craneware
    delivered the row.  Promoting one would date a dispense to the day the drug left the shelf
    and hide every day of TPA lag behind it.

    The sentinel is not a timestamp precisely so this cannot pass by accident: a reader that
    quietly reached for ``fill_date`` would return something that looks like an arrival time,
    and an assertion against a plausible stamp would not notice.
    """
    mapping = craneware.CLAIMS_REPORT
    assert mapping.received_at_column is None, (
        "the Claims Report was given an arrival column; every row in it would then be dated "
        "by something that is not when Craneware sent it"
    )

    row = _first_unreversed(_document(vendor_files, mapping), mapping)
    assert vendors.received_at(mapping, row, fallback=FETCH_SENTINEL) == FETCH_SENTINEL


def test_a_reversed_row_takes_the_later_of_its_two_stamps():
    """A row under a flag representation holds one dispense's whole story, so it is true as
    of its reversal — and taking the later stamp is what makes that one rule instead of two.

    Both orderings are asserted, because only one of them is a choice.  When the reversal is
    later, the later stamp wins; when the reversal is *earlier*, the primary stamp still wins,
    and that is the conservative direction: placing a row earlier than it was knowable lets
    the engine reach a verdict on evidence that had not arrived, which is the one ordering
    mistake a cursor-based model cannot recover from.
    """
    mapping = verity.ACCUMULATIONS
    primary = "2026-03-01T09:00:00Z"
    later = "2026-04-02T17:30:00Z"
    earlier = "2026-02-01T00:00:00Z"

    reversed_late = _reversed_verity_row(primary=primary, reversal=later)
    assert vendors.is_reversed(mapping, reversed_late), "the hand-built row is not flagged"
    assert vendors.received_at(mapping, reversed_late, fallback=FETCH_SENTINEL) == later

    reversed_early = _reversed_verity_row(primary=primary, reversal=earlier)
    stamp = vendors.received_at(mapping, reversed_early, fallback=FETCH_SENTINEL)
    assert stamp == primary
    assert stamp >= primary, (
        f"a reversed row landed at {stamp}, earlier than its own primary stamp {primary}; "
        "the engine would then be asked about it before the row was knowable"
    )


def test_an_arrival_column_the_mapping_never_reads_is_refused_at_construction():
    """A declaration pointing at a column nobody reads is a dataset with no arrival time.

    Deferred to parse time this costs a whole delivery: ``_cell`` returns ``None`` for a
    column that is not there, every row inherits the fetch stamp, and the file loads clean
    with its timeline flattened.  So it is refused where it is declared, and the message names
    the column, because "which column did I typo" is the only question the reader has.
    """
    with pytest.raises(ValueError) as raised:
        replace(verity.ACCUMULATIONS, received_at_column="delivered_at")

    assert "delivered_at" in str(raised.value), (
        f"the refusal does not name the offending column: {raised.value}"
    )


# ═══ mapping_for: one source id, one mapping ════════════════════════════════


@pytest.mark.parametrize(
    "source_id, expected",
    [
        ("verity_accumulations", verity.ACCUMULATIONS),
        ("verity_invoices", verity.INVOICES),
        ("craneware_claims_report", craneware.CLAIMS_REPORT),
    ],
)
def test_mapping_for_returns_the_mapping_registered_for_each_source_id(source_id, expected):
    """The lookup ``_read_rows`` performs, checked against the mapping modules themselves.

    ``is`` and not ``==``: two mappings that compared equal but were different objects would
    still be two places a correction could be made in only one of.
    """
    mapping = vendors.mapping_for(source_id)
    assert mapping is expected
    assert mapping.source_id == source_id


def test_mapping_for_names_the_ids_it_knows_when_asked_for_one_it_does_not():
    """Adding a vendor is a registry row plus a mapping module; this is the missing half.

    The known ids are in the message because the realistic cause is a spelling — a registry
    row saying ``verity_rebates`` where the mapping module says something else — and a reader
    who is told which three exist has already found it.
    """
    with pytest.raises(vendors.UnmappedSourceError) as raised:
        vendors.mapping_for("verity_rebates")

    message = str(raised.value)
    assert "verity_rebates" in message
    for known in SOURCE_IDS:
        assert known in message, f"the refusal does not list {known!r}: {message}"


# ═══ _read_rows under VENDOR_CSV ════════════════════════════════════════════


@pytest.mark.parametrize("source_id", SOURCE_IDS)
def test_read_rows_drops_the_trailer_and_yields_one_tuple_per_detail_row(
    vendor_files, source_id: str
):
    """The trailer describes the file; every other row is a claim.

    The expected count is derived by parsing the CSV here rather than written down, so this
    stays true when the generator's volume moves — and it is an independent parse, because a
    count taken from the reader under test would agree with itself whatever it did.

    The trailer is the one row that must not land: it is not a dispense, so the contract
    registered for this source fails it correctly and uselessly, and a quarantine queue with a
    permanent known-good entry in it is a queue people learn to skim.
    """
    mapping = vendors.mapping_for(source_id)
    document = _document(vendor_files, mapping)
    counts = _counts_by_record_type(document.text, mapping)

    assert counts[mapping.trailer_record_type] > 0, (
        f"{document.name} carries no trailer, so dropping one proves nothing"
    )
    assert counts["other"] == 0, (
        f"{document.name} carries rows that are neither DETAIL nor TRAILER; they land as "
        "unrecognised and this count would no longer be the detail count"
    )

    rows = _read(document, mapping)
    assert len(rows) == counts[mapping.detail_record_type]

    kinds = {json.loads(payload)[mapping.record_type_column] for payload, *_ in rows}
    assert kinds == {mapping.detail_record_type}, f"a non-detail row landed: {sorted(kinds)}"


def test_read_rows_yields_the_vendors_own_spelling_not_the_canonical_rename(vendor_files):
    """``ndc_11`` on Verity, ``ndc11`` on Craneware — the same concept, two spellings.

    This is what lets the registered contract validate the payload at all.  ``_process_raw_record``
    checks a raw payload against the contract for its source id *before* an adapter assigns
    meaning to it, and that contract describes the file as the vendor writes it.  A payload
    that arrived pre-renamed would be checked against a shape it no longer has — which is why
    the second half of this test asserts the renamed dict **fails** the same contract.  That
    failure is the cost of renaming too early, made visible.

    Renaming is the adapter's job, one step later, once the shape is proven.
    """
    accumulations = verity.ACCUMULATIONS
    verity_doc = _document(vendor_files, accumulations)
    verity_payload = json.loads(_read(verity_doc, accumulations)[0][0])

    assert "ndc_11" in verity_payload
    assert "ndc11" not in verity_payload, (
        "the Verity payload arrived under this fabric's spelling; the registered contract "
        "declares ndc_11 and would quarantine every row of every file"
    )

    claims = craneware.CLAIMS_REPORT
    craneware_doc = _document(vendor_files, claims)
    craneware_payload = json.loads(_read(craneware_doc, claims)[0][0])

    assert "ndc11" in craneware_payload
    assert "ndc_11" not in craneware_payload

    # The point of keeping the vendor's spelling: the payload validates as it stands.
    assert schema_registry.check(accumulations.source_id, verity_payload) is None
    assert (
        schema_registry.check(
            claims.source_id,
            craneware_payload,
            version=craneware_payload.get("schema_version"),
        )
        is None
    )

    # And the renamed shape does not, on either vendor — which is what a pre-renamed payload
    # would have been handed to the same check.
    for mapping, document in ((accumulations, verity_doc), (claims, craneware_doc)):
        renamed = vendors.canonical(mapping, _detail_rows(document, mapping)[0])
        assert schema_registry.check(mapping.source_id, renamed) is not None, (
            f"{mapping.source_id}: the canonical rename still satisfies the contract, so this "
            "test cannot tell a payload that was renamed too early from one that was not"
        )


@pytest.mark.parametrize("source_id", SOURCE_IDS)
def test_read_rows_injects_exactly_three_keys_beyond_the_vendors_own(
    vendor_files, source_id: str
):
    """``received_at``, ``source_id`` and ``dataset`` — and nothing else.

    "Exactly" is the assertion.  The contract ignores fields it does not declare, so an extra
    key travels silently rather than failing anything, and a fourth one added here would ride
    every payload of every vendor into the raw layer with nobody to notice.  The mirror case
    matters just as much: a vendor column *missing* from the payload is a column the contract
    reads as absent, which is a required-field quarantine on a row that was perfectly fine.

    ``received_at`` is the one that unblocked the leg: no vendor publishes a column by that
    name, the pipeline orders everything it holds by it, and resolving it needs the
    per-dataset declaration plus the delivery's fetch stamp — neither of which an adapter
    holding only a payload can see.
    """
    mapping = vendors.mapping_for(source_id)
    document = _document(vendor_files, mapping)
    header = set(_header(document.text))

    payload_text, record_id, stamp, source_system = _read(document, mapping)[0]
    payload = json.loads(payload_text)

    assert set(payload) - header == {"received_at", "source_id", "dataset"}
    assert not header - set(payload), (
        f"{sorted(header - set(payload))} were dropped on the way in; the contract reads a "
        "missing column as an absent field and quarantines the row"
    )

    assert payload["source_id"] == mapping.source_id
    assert payload["dataset"] == mapping.dataset
    assert payload["received_at"] == stamp, (
        "the payload and the raw row disagree about when this record arrived"
    )
    assert stamp == vendors.received_at(
        mapping, _detail_rows(document, mapping)[0], fallback=FETCH_SENTINEL
    )
    assert record_id is None, (
        "a source record id was invented; only Verity stamps one, so identity here is the "
        "payload hash, as it is for every bank row"
    )
    assert source_system is SOURCE_SYSTEMS[source_id]
