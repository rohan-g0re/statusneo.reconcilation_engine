"""The connector fabric: how the bytes get here, and whether we were allowed.

Requirement groups A and B of ``docs/connectivity_layer_requirements.md`` — Doc 2 steps 2
and 3.  Three properties in this file matter more than the rest, because each of them is a
guarantee that was *structural* before the refactor and is only a *test* afterwards:

**The ground-truth prohibition (§4.9).**  ``load_feeds`` used to take a feeds directory and
could not reach ``truth/`` even by accident.  That fact is why crosswalk accuracy is a
measurement rather than a claim.  Generalising a directory into a ``Transport`` threatens it,
because a transport is a more general thing than a path — so every implementation refuses a
ground-truth path and is tested for it, one test per refusal route.

**The seam (§4.12).**  The existing suite must pass *unedited*.  If a pre-existing test ever
needs changing to accommodate this package, the seam was cut in the wrong place, and the
right response is to move the seam rather than to edit the test.

**Credentials never reach a tracked file (A3).**  Enforced by scanning the repository, not by
reviewing the diff — because the diff is exactly what a reviewer stops reading at line 400.
"""

from __future__ import annotations

import ast
import copy
import hashlib
import json
import pickle
import re
import sqlite3
import traceback
from pathlib import Path

import pytest

from recon import config
from recon.config import load_settings
from recon.connectors import credentials, registry, schema_registry, transport
from recon.connectors.transport import Document, ForbiddenPathError, LocalDirectoryTransport
from recon.connectors.vendors import beacon
from recon.crosswalk import keys
from recon.db import connection, migrate, repository
from recon.domain.enums import KeyType, QuarantineReason, SourceSystem, TransportKind
from recon.generators.orchestrator import generate, write_outputs
from recon.ingest import pipeline

REPO_ROOT = Path(__file__).resolve().parents[1]
CONNECTORS_DIR = REPO_ROOT / "src" / "recon" / "connectors"
CONTRACTS_DIR = CONNECTORS_DIR / "contracts"

#: ``| 1 | `field` | kind | yes |`` — the machine-checkable half of requirement B2.
_CONTRACT_ROW = re.compile(
    r"^\|\s*(\d+)\s*\|\s*`([A-Za-z0-9_]+)`\s*\|\s*([a-z_]+)\s*\|\s*(yes|no)\s*\|",
    re.MULTILINE,
)


@pytest.fixture()
def db() -> sqlite3.Connection:
    handle = connection.connect(":memory:")
    migrate.ensure_schema(handle)
    try:
        yield handle
    finally:
        handle.close()


@pytest.fixture(scope="session")
def generated_feeds_dir(tmp_path_factory) -> Path:
    """The six generated feeds, written once, under a temp directory.

    Generated rather than read out of ``data/``, for the reason the root ``settings``
    fixture gives: no test touches the committed dataset.  ``demo`` because this is the
    real path's smallest honest instance — six files, both 340B source systems, a
    delimited feed — and nothing here scales with record count.
    """
    settings = load_settings("demo", data_dir=tmp_path_factory.mktemp("connector_feeds"))
    write_outputs(settings, generate(settings))
    return settings.feeds_dir()


# ═══ §4.9 — no transport may reach ground truth ═══


def test_a_transport_cannot_be_rooted_inside_the_ground_truth_directory(tmp_path):
    """The first of three refusal routes: pointing the root at ``truth/`` outright."""
    truth = tmp_path / config.TRUTH_SUBDIR
    truth.mkdir()
    with pytest.raises(ForbiddenPathError, match="ground-truth"):
        LocalDirectoryTransport(truth)


@pytest.mark.parametrize(
    "name",
    ["../truth/ground_truth.json", "truth/ground_truth.json", "../secrets.txt", "a/b.jsonl"],
)
def test_a_transport_refuses_a_document_name_that_traverses(tmp_path, name: str):
    """The second route: a source whose filename walks out of its own directory.

    Checked on the name *before* it is joined.  A check that ran afterwards would already
    have built the path it was trying to forbid, and a rejected-but-constructed path is one
    ``open()`` away from being read.
    """
    source = registry.Source(
        source_id="hostile",
        vendor="test",
        transport_kind=TransportKind.LOCAL_DIRECTORY,
        source_system=SourceSystem.BANK,
        filenames=(name,),
        endpoint=str(tmp_path),
        transport=LocalDirectoryTransport(tmp_path),
    )
    with pytest.raises(ForbiddenPathError):
        list(source.require_transport().fetch(source))


def test_no_transport_implementation_can_resolve_a_path_under_truth(tmp_path):
    """The third route, and the one that generalises: every implementation, not just this one.

    Parametrised over whatever ``connectors`` currently ships rather than over a hardcoded
    list, so an SFTP or HTTP transport added later is covered the day it lands instead of the
    day someone remembers to extend this.
    """
    implementations = [
        value
        for value in vars(transport).values()
        if isinstance(value, type)
        and value.__module__ == transport.__name__
        and hasattr(value, "fetch")
        and not getattr(value, "_is_protocol", False)
    ]
    assert implementations, "no transport implementations found to check"

    truth = tmp_path / config.TRUTH_SUBDIR
    truth.mkdir()
    for implementation in implementations:
        with pytest.raises(ForbiddenPathError):
            implementation(truth)


def test_a_document_carries_no_filesystem_handle():
    """The design that makes §4.9 hold rather than being restated.

    A consumer holding a :class:`Document` cannot walk anywhere from it — there is no path,
    no directory and no open file on the object.  That is what survives the move from "the
    loader takes a feeds directory" to "the loader takes a transport".
    """
    fields = set(Document.__dataclass_fields__)
    pathlike = sorted(
        name for name in fields if any(token in name for token in ("path", "dir", "root", "handle", "file"))
    )
    assert not pathlike, f"Document grew a filesystem reference: {pathlike}"
    assert "text" in fields and "name" in fields


# ═══ A1 — sources are data, not code ═══


def test_pipeline_names_no_vendor_and_no_feed_filename():
    """Requirement A1's acceptance, stated as the property that makes it true.

    "Adding a seventh source is a config row plus a mapping module, with no edit to
    ``pipeline.py``" stays true exactly as long as ``pipeline.py`` never *branches* on a
    vendor.  The moment it does, onboarding becomes an edit again.

    Two exclusions, and both are the difference between the property and a proxy for it:

    **Docstrings are not code.**  A scan that reads them punishes the module for explaining
    the rule it obeys, and the cheapest way to go green is then to delete the explanation —
    which is precisely backwards.  This build has now made that mistake three times, so the
    exclusion is done by node identity rather than by hoping no prose mentions a vendor.

    **A column name is not a vendor branch.**  ``normalized_record.beacon_id`` is a column
    every record has and almost every record leaves NULL; writing it is schema plumbing, not
    a decision about who the source is.  The allowance is drawn from the repository's own
    column tuples rather than hardcoded, so a future ``verity_status`` column is allowed for
    the same reason and a bare ``"verity"`` still is not.
    """
    from recon.db.repository import _EPISODE_COLUMNS, _NORM_COLUMNS

    source_text = (REPO_ROOT / "src" / "recon" / "ingest" / "pipeline.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source_text)

    docstrings = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
            docstrings.add(id(body[0].value))

    allowed = set(_NORM_COLUMNS) | set(_EPISODE_COLUMNS)
    literals = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    }
    offenders = sorted(
        literal
        for literal in literals
        if literal not in allowed
        and (
            literal in set(config.FEED_FILENAMES)
            or any(vendor in literal.lower() for vendor in ("beacon", "verity", "craneware"))
        )
    )
    assert not offenders, f"pipeline.py names a source directly: {offenders}"


def test_the_six_generated_feeds_register_as_sources_in_feed_order(tmp_path):
    """Order is not cosmetic: a batch is per file, and batch ids are asserted on elsewhere."""
    sources = registry.local_sources(tmp_path)
    assert tuple(source.filenames[0] for source in sources) == config.FEED_FILENAMES
    assert all(source.transport_kind is TransportKind.LOCAL_DIRECTORY for source in sources)
    assert all(source.enabled for source in sources)


def test_a_seventh_source_needs_no_edit_to_the_pipeline(tmp_path, db):
    """The acceptance test as written: a config row is enough to ingest something new."""
    # Deliberately does NOT declare its own source_system.  That is the case that used to
    # raise KeyError out of a filename lookup, which made A1's "no edit to pipeline.py"
    # false for exactly the situation A1 exists to cover.
    payload = {"record_id": "X-1", "received_at": "2025-07-02T17:23:01Z"}
    (tmp_path / "seventh.jsonl").write_text(
        json.dumps(payload) + "\n", encoding="utf-8", newline=""
    )
    seventh = registry.Source(
        source_id="seventh",
        vendor="a-new-vendor",
        transport_kind=TransportKind.LOCAL_DIRECTORY,
        source_system=SourceSystem.BANK,
        filenames=("seventh.jsonl",),
        endpoint=str(tmp_path),
        transport=LocalDirectoryTransport(tmp_path),
    )
    stats = pipeline.load_from_sources(db, [seventh])
    assert stats.raw_records == 1
    stored = db.execute("SELECT source_id, source_file FROM ingest_batch").fetchone()
    assert (stored["source_id"], stored["source_file"]) == ("seventh", "seventh.jsonl")
    attributed = db.execute("SELECT source_system FROM raw_record").fetchone()
    assert attributed["source_system"] == str(SourceSystem.BANK), (
        "a record that declares no source system must be attributed from its source row"
    )


# ═══ A1 — attribution comes from the source row, in every payload format ═══


def test_a_delimited_source_is_attributed_to_its_own_system_not_to_the_first_ones(tmp_path, db):
    """The half-applied fix: one branch honoured the source row and the other did not.

    ``_read_rows`` learned to take its fallback attribution as an argument, and only the
    line-delimited branch started using it — the delimited branch went on naming a source
    system outright.  So a delimited source outside the original six was attributed to a
    system its own registry row does not mention.

    That is the expensive kind of defect, because it does not fail.  It writes a clean raw
    row, picks a different adapter on the way through, and reads identically to a correct
    attribution in every report afterwards — while the batch header stored beside it says
    something else entirely.
    """
    (tmp_path / "tpa_export.csv").write_text(
        "record_id,amount,received_at\nT-1,10.00,2025-07-02T17:23:01Z\n",
        encoding="utf-8",
        newline="",
    )
    source = registry.Source(
        source_id="tpa_delimited",
        vendor="a-new-vendor",
        transport_kind=TransportKind.LOCAL_DIRECTORY,
        source_system=SourceSystem.TPA_PORTAL,
        filenames=("tpa_export.csv",),
        # Supplied as the string a config row would carry, not as the member, so this also
        # pins that a valid string is accepted and stored as the member it names.
        payload_format="bank_csv",
        endpoint=str(tmp_path),
        transport=LocalDirectoryTransport(tmp_path),
    )
    assert source.payload_format is registry.PayloadFormat.BANK_CSV

    assert pipeline.load_from_sources(db, [source]).raw_records == 1
    stored = db.execute(
        "SELECT b.source_system AS declared, r.source_system AS attributed"
        "  FROM raw_record r JOIN ingest_batch b ON b.batch_id = r.batch_id"
    ).fetchone()
    assert stored["declared"] == str(SourceSystem.TPA_PORTAL)
    assert stored["attributed"] == str(SourceSystem.TPA_PORTAL), (
        "a delimited source was attributed to a system its own registry row does not name"
    )


def test_the_six_generated_feeds_attribute_exactly_as_they_always_have(generated_feeds_dir, db):
    """The guard on the other side of the same fix: the real path must not have moved.

    Making the delimited branch consult the source row is exactly the sort of change that
    silently re-attributes the feed it was already getting right, and a wrong attribution
    here would be invisible — every count still balances, every report still renders.

    Asserted as the set of *(document, attributed system)* pairs rather than as counts, so
    it says the thing worth saying: five feeds attribute wholly from their registry row, and
    the 340B feed alone splits in two because a manufacturer's payment batch and a TPA's
    qualification decision genuinely come from different systems exported together.
    """
    stats = pipeline.load_feeds(db, generated_feeds_dir)
    assert stats.raw_records > 0, "nothing loaded, so this would pass vacuously"

    census = {
        (row["source_file"], row["attributed"])
        for row in db.execute(
            "SELECT b.source_file AS source_file, r.source_system AS attributed"
            "  FROM raw_record r JOIN ingest_batch b ON b.batch_id = r.batch_id"
            " GROUP BY 1, 2"
        ).fetchall()
    }
    assert census == {
        (config.PBM_CLAIM_EVENTS_FILE, str(SourceSystem.PBM_ADJUDICATION)),
        (config.PBM_REMITTANCE_835_FILE, str(SourceSystem.PBM_REMITTANCE)),
        (config.MEDICAL_837_SUBMISSIONS_FILE, str(SourceSystem.CLEARINGHOUSE_837)),
        (config.MEDICAL_835_REMITTANCE_FILE, str(SourceSystem.MEDICAL_REMITTANCE)),
        (config.TPA_340B_EVENTS_FILE, str(SourceSystem.TPA_PORTAL)),
        (config.TPA_340B_EVENTS_FILE, str(SourceSystem.MANUFACTURER_REBATE)),
        (config.BANK_TRANSACTIONS_FILE, str(SourceSystem.BANK)),
    }

    bank_rows = db.execute(
        "SELECT COUNT(*) FROM raw_record WHERE source_system = ?", (str(SourceSystem.BANK),)
    ).fetchone()[0]
    assert bank_rows > 0, "the delimited feed contributed nothing, so its branch went unchecked"


def test_an_unknown_source_system_on_the_wire_names_the_source_document_and_value(tmp_path, db):
    """``SourceSystem(declared)`` raised a bare ``ValueError`` naming nothing.

    Not the source, not the document, not the line, not the value — and it aborted a
    multi-source load from somewhere inside a generator.  ``connectors/`` says repeatedly
    that a wrong input produces a named failure rather than a stack trace, and this was the
    one place in the load path that did not.

    It still aborts, and this test is also the case for that.  ``_read_rows`` runs before
    any ``raw_record`` exists, so there is nothing for a quarantine row to reference and
    ``raw_record.source_system`` could not store the offending value anyway — its ``CHECK``
    lists the seven known systems.  So the abort is made clean and resumable instead: the
    failing document lands nothing, the documents ahead of it in the same load keep their
    batches, and the re-run skips those on the file hash and picks up where it stopped.
    """
    good = {"record_id": "G-1", "received_at": "2025-07-02T00:00:00Z"}
    (tmp_path / "good.jsonl").write_text(
        json.dumps(good) + "\n", encoding="utf-8", newline=""
    )
    # A real export's mistake, not a nonsense string: one letter missing from a system that
    # does exist, which is precisely the value a reviewer's eye slides over.
    bad = {
        "record_id": "B-1",
        "source_system": "PBM_ADJUDICATON",
        "received_at": "2025-07-02T00:00:00Z",
    }
    bad_path = tmp_path / "eighth.jsonl"
    # A leading blank line, so "line 2" can only have come from counting the document's own
    # lines rather than the records yielded from it.
    bad_path.write_text("\n" + json.dumps(bad) + "\n", encoding="utf-8", newline="")

    sources = [
        registry.Source(
            source_id=source_id,
            vendor="a-new-vendor",
            transport_kind=TransportKind.LOCAL_DIRECTORY,
            source_system=SourceSystem.BANK,
            filenames=(f"{source_id}.jsonl",),
            endpoint=str(tmp_path),
            transport=LocalDirectoryTransport(tmp_path),
        )
        for source_id in ("good", "eighth")
    ]

    with pytest.raises(pipeline.UnknownSourceSystemError) as caught:
        pipeline.load_from_sources(db, sources)

    message = str(caught.value)
    assert "eighth.jsonl" in message, "the message must name the document"
    assert "'eighth'" in message, "the message must name the source"
    assert "PBM_ADJUDICATON" in message, "the message must quote the offending value"
    assert "line 2" in message, "the message must name the line"
    assert str(SourceSystem.PBM_ADJUDICATION) in message, (
        "the message must list the permitted values, or it says what is wrong without "
        "saying what would be right"
    )
    assert (caught.value.source_id, caught.value.document) == ("eighth", "eighth.jsonl")
    assert (caught.value.line_no, caught.value.value) == (2, "PBM_ADJUDICATON")
    assert str(SourceSystem.PBM_ADJUDICATION) in caught.value.permitted

    landed = dict(
        db.execute(
            "SELECT b.source_file, COUNT(*) FROM raw_record r"
            "  JOIN ingest_batch b ON b.batch_id = r.batch_id GROUP BY 1"
        ).fetchall()
    )
    assert landed == {"good.jsonl": 1}, (
        "the failing document must land nothing, and the one ahead of it must keep its batch"
    )

    # Resumable, which is what makes aborting the right answer rather than merely the
    # available one: correct the export, re-run, and only the corrected document loads.
    bad["source_system"] = str(SourceSystem.PBM_ADJUDICATION)
    bad_path.write_text(json.dumps(bad) + "\n", encoding="utf-8", newline="")
    assert pipeline.load_from_sources(db, sources).raw_records == 1
    assert db.execute("SELECT COUNT(*) FROM ingest_batch").fetchone()[0] == 2


@pytest.mark.parametrize("declared", ["csv", "CSV", "JSONL", "ndjson", "bank-csv", "", None])
def test_an_unknown_payload_format_is_refused_when_the_source_row_is_written(tmp_path, declared):
    """``payload_format`` was a free string, and free was the whole problem.

    The loader branched on one known value and treated everything else as line-delimited
    JSON, so a typo did not raise — it misread the entire document and landed whatever
    survived.  A registry row is configuration, and configuration that is wrong has to fail
    where it is declared, not one transport and one parse later.
    """
    with pytest.raises(registry.UnknownPayloadFormatError) as caught:
        registry.Source(
            source_id="typo",
            vendor="a-new-vendor",
            transport_kind=TransportKind.LOCAL_DIRECTORY,
            source_system=SourceSystem.BANK,
            filenames=("x.csv",),
            payload_format=declared,
            endpoint=str(tmp_path),
        )
    message = str(caught.value)
    assert "'typo'" in message, "the message must name the source whose row is wrong"
    assert repr(declared) in message, "the message must quote the offending value"
    for known in registry.PayloadFormat:
        assert str(known) in message, "the message must list the permitted values"


def test_a_disabled_source_is_not_fetched(tmp_path, db):
    """A disabled source stays in the registry rather than being deleted.

    "We onboarded this vendor and turned it off" and "we never onboarded this vendor" are
    different facts, and the readiness report in requirement F3 has to tell them apart.
    """
    (tmp_path / "off.jsonl").write_text("", encoding="utf-8", newline="")
    source = registry.Source(
        source_id="off",
        vendor="v",
        transport_kind=TransportKind.LOCAL_DIRECTORY,
        source_system=SourceSystem.BANK,
        filenames=("off.jsonl",),
        endpoint=str(tmp_path),
        enabled=False,
        transport=LocalDirectoryTransport(tmp_path),
    )
    assert pipeline.load_from_sources(db, [source]).raw_records == 0
    assert registry.enabled([source]) == ()


# ═══ A5 — fetch-level idempotency ═══


def test_loading_the_same_documents_twice_ingests_nothing_the_second_time(tmp_path, db):
    """Requirement A5, and the reason a re-run is safe to just do.

    File-level idempotency is keyed on the content hash, so the second pass recognises the
    bytes rather than the filename — which is what makes it survive a vendor re-delivering
    the same export under a new name.
    """
    (tmp_path / "a.jsonl").write_text(
        json.dumps({"record_id": "A", "source_system": "BANK", "received_at": "2025-07-02T00:00:00Z"})
        + "\n",
        encoding="utf-8",
        newline="",
    )
    sources = [
        registry.Source(
            source_id="a",
            vendor="v",
            transport_kind=TransportKind.LOCAL_DIRECTORY,
            source_system=SourceSystem.BANK,
            filenames=("a.jsonl",),
            endpoint=str(tmp_path),
            transport=LocalDirectoryTransport(tmp_path),
        )
    ]
    first = pipeline.load_from_sources(db, sources)
    state = _database_fingerprint(db)
    second = pipeline.load_from_sources(db, sources)

    assert first.raw_records == 1
    assert second.raw_records == 0
    assert _database_fingerprint(db) == state, "a replayed fetch changed the database"


def _database_fingerprint(conn: sqlite3.Connection) -> str:
    """Row counts and a content hash, which is how A5's acceptance is worded."""
    parts: list[str] = []
    for table in ("ingest_batch", "raw_record", "quarantined_record"):
        rows = conn.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
        parts.append(f"{table}:{len(rows)}:" + "|".join(repr(tuple(row)) for row in rows))
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


# ═══ B1 — a schema mismatch quarantines, and does not normalize ═══


def test_an_unregistered_source_validates_vacuously():
    """The property that keeps the existing 585 tests passing unedited.

    The six generated feeds have no registered contract, and must not acquire one by
    accident — a contract on our own generator is a contract we agreed to on both sides,
    which proves nothing about a file someone else can change without asking.
    """
    assert schema_registry.check("pbm_claim_events", {"anything": 1}) is None
    assert schema_registry.check(None, {"anything": 1}) is None


def test_changing_one_field_type_quarantines_with_schema_version_mismatch(tmp_path, db):
    """Requirement B1's acceptance, exactly as written.

    *"changing one field type in a fixture produces a quarantined record with the right
    reason code and zero normalized records."*
    """
    source_id, contract = _first_registered_contract()
    good = _conforming_record(contract)
    bad = dict(good)
    victim = next(field for field in contract.fields if field.kind == "date_wire")
    bad[victim.name] = "2026-03-02"  # ISO where the contract says CCYYMMDD

    (tmp_path / "vendor.jsonl").write_text(
        json.dumps(bad) + "\n", encoding="utf-8", newline=""
    )
    source = registry.Source(
        source_id=source_id,
        vendor="craneware",
        transport_kind=TransportKind.LOCAL_DIRECTORY,
        source_system=SourceSystem.TPA_PORTAL,
        filenames=("vendor.jsonl",),
        endpoint=str(tmp_path),
        transport=LocalDirectoryTransport(tmp_path),
    )
    pipeline.load_from_sources(db, [source])
    pipeline.ingest(db)

    quarantined = db.execute("SELECT reason_code, detail FROM quarantined_record").fetchall()
    assert len(quarantined) == 1, "the malformed record was not quarantined"
    assert quarantined[0]["reason_code"] == str(QuarantineReason.SCHEMA_VERSION_MISMATCH)
    assert victim.name in quarantined[0]["detail"], (
        "the quarantine detail must name the offending field, or the queue is unactionable"
    )
    assert db.execute("SELECT COUNT(*) FROM normalized_record").fetchone()[0] == 0


def test_the_enum_member_b1_wires_up_was_previously_unused():
    """``SCHEMA_VERSION_MISMATCH`` shipped in the enum long before anything could emit it.

    Kept as a test rather than a comment because the interesting property is not that the
    member exists — it is that the connector layer is what finally makes it reachable.
    """
    assert schema_registry.QUARANTINE_REASON is QuarantineReason.SCHEMA_VERSION_MISMATCH


def _first_registered_contract():
    for source_id in schema_registry.REGISTRY.source_ids():  # noqa: SLF001 - test introspection
        contract = schema_registry.REGISTRY.get(source_id)
        if contract and any(field.kind == "date_wire" for field in contract.fields):
            return source_id, contract
    pytest.skip("no registered contract carries a date_wire field to corrupt")


def _conforming_record(contract) -> dict:
    samples = {
        "string": "TEXT",
        "integer": 1,
        "integer_text": "1",
        "decimal_text": "10.00",
        "date_wire": "20250702",
        "date_iso": "2025-07-02",
        "timestamp": "2025-07-02T00:00:00Z",
        "array": [],
        "object": {},
    }
    record = {field.name: samples[field.kind] for field in contract.fields}
    record["received_at"] = "2025-07-02T00:00:00Z"
    if "schema_version" in record:
        # Craneware writes its contract version into every row, so the wire names the
        # contract it should be checked against. A generic string sample here would make the
        # record quarantine for an unknown *version* instead of the corrupted *field* —
        # correct behaviour, but not the thing this test is about.
        record["schema_version"] = contract.version
    return record


# ═══ B2 — the documented contract matches the registered one ═══


def test_every_registered_contract_has_an_interface_contract_document():
    registered = schema_registry.REGISTRY.source_ids()
    assert registered, "no contracts are registered, so this test would pass vacuously"
    missing = [
        source_id for source_id in registered if not (CONTRACTS_DIR / f"{source_id}.md").exists()
    ]
    assert not missing, f"registered sources with no interface contract document: {missing}"


def test_the_documented_field_table_matches_the_registered_contract_exactly():
    """Requirement B2's acceptance: *a test asserts the registered schema matches the documented one.*

    Field for field, kind for kind, in order.  A contract document that can drift from the
    code is worse than no document at all, because it reads as verified — a reviewer checking
    a vendor mapping against it would be checking it against fiction.
    """
    problems: list[str] = []
    for source_id in schema_registry.REGISTRY.source_ids():  # noqa: SLF001
        contract = schema_registry.REGISTRY.get(source_id)
        text = (CONTRACTS_DIR / f"{source_id}.md").read_text(encoding="utf-8")

        if f"`{contract.version}`" not in text:
            problems.append(f"{source_id}: document does not state version {contract.version}")

        rows = _CONTRACT_ROW.findall(text)
        if len(rows) != len(contract.fields):
            problems.append(
                f"{source_id}: documents {len(rows)} fields, registry has {len(contract.fields)}"
            )
            continue
        for index, (position, name, kind, required) in enumerate(rows):
            field = contract.fields[index]
            actual = (int(position), name, kind, required == "yes")
            expected = (index + 1, field.name, field.kind, field.required)
            if actual != expected:
                problems.append(f"{source_id} row {index + 1}: {actual} != {expected}")
    assert not problems, problems


# ═══ A3 — credentials never reach a tracked file ═══


@pytest.mark.parametrize("render", [repr, str, lambda s: f"{s}", lambda s: f"{s:>40}", lambda s: "%s" % (s,)])
def test_a_secret_never_renders_its_value(render):
    """A secret that prints itself in a traceback has leaked.

    Enforced on the value rather than at each call site, so it survives being embedded in
    something else — an f-string, a ``%`` format, a log line someone adds in a hurry.
    """
    secret = credentials.Secret("super-secret-value")
    assert "super-secret-value" not in render(secret)


#: The only modules permitted to take a credential out of its wrapper.
#:
#: An allow-list rather than a count, because the useful question is not "how many places
#: reveal a secret" but "is this one of the places that should".  A module joining this list
#: is a deliberate edit with a reviewer attached; a module revealing a secret without joining
#: it fails.  ``sftp.py`` is the first entry — before wave 3 the list was empty and the test
#: said so, which is how the transition stayed visible instead of being absorbed.
CREDENTIAL_USE_SITES = frozenset({"credentials.py", "sftp.py", "http.py"})


def test_revealing_a_secret_is_a_single_greppable_call():
    """One escape hatch, named so the audit is a grep rather than a review.

    The value of ``reveal()`` being the only way out is that the complete list of places
    this process can emit a credential is the list of its call sites.  That is only worth
    anything while the list is short and every entry is intentional.
    """
    assert credentials.Secret("abc").reveal() == "abc"
    offenders = sorted(
        path.name
        for path in CONNECTORS_DIR.rglob("*.py")
        if ".reveal()" in path.read_text(encoding="utf-8")
        and path.name not in CREDENTIAL_USE_SITES
    )
    assert not offenders, (
        f"modules revealing a credential without being declared a use site: {offenders}; "
        "add it to CREDENTIAL_USE_SITES if that is intended, so the next reviewer sees it"
    )


def test_every_declared_credential_use_site_still_reveals_something():
    """The allow-list must not outlive its entries.

    A stale entry is worse than a missing one: it grants permission nobody is using, so the
    day a module starts revealing a secret again it does so with the review already spent.
    """
    revealing = {
        path.name
        for path in CONNECTORS_DIR.rglob("*.py")
        if ".reveal()" in path.read_text(encoding="utf-8")
    }
    stale = sorted(CREDENTIAL_USE_SITES - revealing - {"credentials.py"})
    assert not stale, f"declared credential use sites that no longer reveal anything: {stale}"


def test_a_missing_credential_is_a_named_failure_naming_what_to_do():
    """A3's acceptance: *a wrong credential produces a clear named failure, not a stack trace.*"""
    with pytest.raises(credentials.MissingCredentialError) as caught:
        credentials.resolve_token_pair("beacon_partner", env={})
    message = str(caught.value)
    assert "beacon_partner" in message
    assert "RECON_CONNECTOR_" in message, "the message must name the variable to set"
    assert "BEACON-011" in message, "the two-token model is second-hand and must cite its source"


def test_no_credential_value_appears_in_any_tracked_file():
    """Scanned rather than reviewed, because a diff is what a reviewer stops reading.

    The connector registry is committed, so the thing that keeps it safe is that it holds
    credential *names* and never values — ``credential_ref`` is a lookup key, and this is
    what stops it quietly becoming a place to paste a token.
    """
    suspicious = re.compile(
        r"(?i)(access_token|private_token|credential_ref|password|passphrase|secret)"
        r"\s*[:=]\s*[\"'][^\"'{}$<>]{8,}[\"']"
    )
    offenders: list[str] = []
    for path in sorted(CONNECTORS_DIR.rglob("*.py")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if suspicious.search(line) and "redacted" not in line.lower():
                offenders.append(f"{path.name}:{number}: {line.strip()[:90]}")
    assert not offenders, f"possible credential literals in tracked files: {offenders}"


def test_the_registry_holds_credential_names_never_values(tmp_path):
    for source in registry.local_sources(tmp_path):
        assert source.credential_ref is None or isinstance(source.credential_ref, str)
        assert not isinstance(source.credential_ref, credentials.Secret)


# ═══ A3 — the three routes the value found out anyway ═══
#
# Everything above this line tests the front door: a secret is asked to render itself and
# refuses.  An adversarial pass over `credentials.py` found three ways the value left the
# module without ever being asked — an error message that echoed what it was complaining
# about, a serialisation hook Python supplies for free, and an exception chain that kept a
# reference to the file it failed to parse.  None of them goes through `__repr__`, which
# is why none of them was caught by the test above.

#: A plausible pasted private key.  Shape matters, not content: multi-line, PEM-headed and
#: far longer than a path, which is exactly what the module now describes instead of prints.
_PASTED_KEY_MATERIAL = (
    "-----BEGIN OPENSSH PRIVATE KEY-----\n"
    + "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMwAAAAtzc2gt\n" * 20
    + "-----END OPENSSH PRIVATE KEY-----\n"
)

#: The value every test below asserts is absent from whatever it produced.
_VALUE = "super-secret-value"


def test_key_material_pasted_into_the_path_variable_is_named_but_never_echoed(tmp_path):
    """The most available mistake in the whole module, and it used to answer with the key.

    ``..._SSH_KEY`` takes inline material, ``..._SSH_KEY_PATH`` takes a path, and both
    exist on purpose — which makes pasting the key into the path variable the confusion the
    two-variable design itself invites.  The "does not exist" check then printed the path
    it had looked for: correct for a path, and the entire private key in a log line for a
    key.  A3's own *a wrong credential produces a clear named failure* case, answered by
    leaking the credential.

    The fix is a rule, not a filter: name the variable that was wrong, describe the value's
    shape, never repeat its content.
    """
    with pytest.raises(credentials.MalformedCredentialError) as caught:
        credentials.resolve_ssh_key(
            "verity_sftp",
            env={"RECON_CONNECTOR_VERITY_SFTP_SSH_KEY_PATH": _PASTED_KEY_MATERIAL},
            secrets_file=tmp_path / "absent.json",
        )
    message = str(caught.value)

    leaked = [line for line in _PASTED_KEY_MATERIAL.splitlines() if line and line in message]
    assert not leaked, f"the error message repeats the key material: {leaked[0][:40]}..."
    assert "RECON_CONNECTOR_VERITY_SFTP_SSH_KEY_PATH" in message, (
        "a named failure must name the variable that was wrong"
    )
    assert "RECON_CONNECTOR_VERITY_SFTP_SSH_KEY" in message, (
        "and the variable the value belonged in, or the operator repeats the mistake"
    )
    assert str(len(_PASTED_KEY_MATERIAL)) in message and "contains newlines" in message, (
        "the shape is what makes the message actionable without the content"
    )


def test_key_material_cannot_reach_the_one_field_that_renders_in_the_clear():
    """The same leak one layer down, where ``resolve`` is not standing in front of it.

    ``SshKeyCredential.key_path`` is deliberately not a :class:`Secret` — a path is not a
    credential and the generated repr should show it. That makes it the single field on the
    object where pasted key material would print, so the refusal is stated on the type as
    well as on the resolution path, and holds for an object built by hand.
    """
    with pytest.raises(credentials.MalformedCredentialError) as caught:
        credentials.SshKeyCredential(
            credential_ref="verity_sftp", key_path=Path(_PASTED_KEY_MATERIAL)
        )
    message = str(caught.value)
    leaked = [line for line in _PASTED_KEY_MATERIAL.splitlines() if line and line in message]
    assert not leaked, f"the error message repeats the key material: {leaked[0][:40]}..."
    assert "key_path" in message and "key_material" in message


def test_a_path_that_is_merely_wrong_is_still_printed_in_full(tmp_path):
    """The other half of the rule, kept honest: a path is not a secret.

    A check that refused to print anything would be safe and useless — *which file did it
    look for* is the entire question when a key fails to load.  Only values shaped like key
    material are described rather than shown.
    """
    with pytest.raises(credentials.MalformedCredentialError) as caught:
        credentials.resolve_ssh_key(
            "verity_sftp",
            env={"RECON_CONNECTOR_VERITY_SFTP_SSH_KEY_PATH": str(tmp_path / "id_ed25519")},
            secrets_file=tmp_path / "absent.json",
        )
    assert "id_ed25519" in str(caught.value)


def test_a_secret_has_no_serialised_form_not_even_the_one_python_supplies_for_free():
    """``__reduce__`` was blocked. Python 3.11 then added a ``__getstate__`` that was not.

    On a slotted class the default returns ``(None, {"_value": <the value>})`` — the raw
    credential, out of a method nobody wrote and no ``.reveal()`` grep would ever find.
    Generic serialisers reach for it by name: ``json.dumps(obj, default=lambda o:
    o.__getstate__())`` printed the token in full.  The lesson is that blocking *the* hook
    is not the same as blocking the hooks, so all five are refused, and the refusal itself
    must not quote what it is refusing.
    """
    secret = credentials.Secret(_VALUE)

    with pytest.raises(credentials.CredentialError) as caught:
        secret.__getstate__()
    assert _VALUE not in str(caught.value), "the refusal quoted the thing it refused"

    with pytest.raises(credentials.CredentialError):
        json.dumps(secret, default=lambda o: o.__getstate__())

    # The routes that were already closed stay closed.
    for route in (
        lambda: secret.__reduce__(),
        lambda: secret.__reduce_ex__(pickle.HIGHEST_PROTOCOL),
        lambda: pickle.dumps(secret),
        lambda: copy.copy(secret),
        lambda: copy.deepcopy(secret),
    ):
        with pytest.raises(credentials.CredentialError):
            route()

    assert secret.reveal() == _VALUE, "reveal() is still the one escape hatch"


def test_a_malformed_secrets_file_cannot_be_reached_from_the_error_it_raises(tmp_path):
    """``raise ... from None`` clears ``__cause__`` and leaves ``__context__`` untouched.

    The context was the ``json.JSONDecodeError``, and its ``.doc`` attribute is the *whole
    file* — every credential in it, including refs this caller never asked for.  Nothing
    prints that by default, which is why it survived: it surfaces in an error reporter that
    walks exception attributes, or in frame locals captured by one.  So the parse failure is
    now carried out of the handler as a string and raised after the handler has exited,
    leaving no exception in flight to chain to.
    """
    other_ref_token = "NOT-THE-REF-THE-CALLER-ASKED-FOR"
    secrets_file = tmp_path / "connector_secrets.json"
    secrets_file.write_text(
        '{"beacon_partner": {"access_token": "' + other_ref_token + '",,}',
        encoding="utf-8",
        newline="",
    )

    with pytest.raises(credentials.MalformedCredentialError) as caught:
        credentials.resolve_token_pair("some_other_source", env={}, secrets_file=secrets_file)

    chain: list[BaseException] = []
    current: BaseException | None = caught.value
    while current is not None and not any(current is seen for seen in chain):
        chain.append(current)
        rendered = " ".join(
            repr(part)
            for part in (current, str(current), current.args, getattr(current, "doc", None))
        )
        assert other_ref_token not in rendered, (
            f"{type(current).__name__} in the chain still carries the file's contents"
        )
        current = current.__cause__ or current.__context__
    assert len(chain) == 1, f"the parser's exception is still chained: {chain}"

    # And not in the frames either, which is where an error reporter looks next.
    frame_tb = caught.value.__traceback__
    while frame_tb is not None:
        frame = frame_tb.tb_frame
        if frame.f_globals.get("__name__") == credentials.__name__:
            for name, value in frame.f_locals.items():
                assert other_ref_token not in repr(value), (
                    f"the file survives in {frame.f_code.co_name}() local {name!r}"
                )
        frame_tb = frame_tb.tb_next

    assert str(secrets_file) in str(caught.value), "the failure still names the file to fix"


def _inside_a_credential_dataclass(secret: credentials.Secret) -> str:
    """The generated repr of the real credential type, which is how this leaks in practice."""
    return repr(
        credentials.TokenPairCredential(
            credential_ref="beacon_partner", access_token=secret, private_token=secret
        )
    )


def _inside_a_rendered_traceback(secret: credentials.Secret) -> str:
    """A secret interpolated into an exception message, then printed by the default handler."""
    try:
        raise RuntimeError(f"authenticating with {secret}")
    except RuntimeError:
        return traceback.format_exc()


#: Every route from an object to text that this codebase can plausibly take. Swept in one
#: test rather than checked where each is used, because the leak is never at the call site
#: someone remembered — it is the one they did not.
_RENDER_PATHS = [
    ("repr", repr),
    ("str", str),
    ("f-string", lambda s: f"{s}"),
    ("f-string with a spec", lambda s: f"{s:>40}"),
    ("f-string with !r", lambda s: f"{s!r}"),
    ("f-string with !s", lambda s: f"{s!s}"),
    ("percent-s", lambda s: "%s" % (s,)),
    ("percent-r", lambda s: "%r" % (s,)),
    ("str.format", lambda s: "{}".format(s)),  # noqa: UP032 -- the point is this path
    ("str.format with !r", lambda s: "{!r}".format(s)),
    ("format builtin", format),
    ("format builtin with a spec", lambda s: format(s, ">40")),
    ("json.dumps with default=str", lambda s: json.dumps({"token": s}, default=str)),
    ("nested in a list", lambda s: repr([s])),
    ("nested in a dict", lambda s: repr({"token": s})),
    ("nested in a tuple", lambda s: repr((s,))),
    ("nested two deep", lambda s: repr({"creds": [{"token": (s,)}]})),
    ("a dataclass repr", _inside_a_credential_dataclass),
    ("a rendered traceback", _inside_a_rendered_traceback),
]


@pytest.mark.parametrize("render", [pytest.param(fn, id=name) for name, fn in _RENDER_PATHS])
def test_no_way_of_turning_an_object_into_text_reaches_the_value(render):
    """One sweep over every render path, because the leak is always the unswept one.

    Each of these bottoms out in ``__repr__``, ``__str__`` or ``__format__``, which is the
    whole reason the redaction lives on the value instead of at the call sites: a new log
    line, a new dataclass field or a new serialiser is covered the day it is written rather
    than the day someone remembers to redact it.
    """
    rendered = render(credentials.Secret(_VALUE))
    assert _VALUE not in rendered
    assert credentials.REDACTED in rendered, (
        "redacted is not enough — the placeholder must be visible, or a reader "
        "cannot tell a hidden value from an absent one"
    )


# ═══ §4.12 — the connector package writes only its own tables ═══


def test_the_connector_package_writes_no_sql_at_all():
    """§4.12 says ``connectors/`` must not write to any table other than its own four.

    Today it writes to none, and that is stronger.  Persistence of the registry and the
    contracts is a later concern; while the fabric is in-memory Python data, the absence of
    any mutating verb here is the cheapest possible proof of the rule.
    """
    offenders: list[str] = []
    for path in sorted(CONNECTORS_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                upper = node.value.upper()
                if any(verb in upper for verb in ("INSERT INTO", "UPDATE ", "DELETE FROM")):
                    offenders.append(f"{path.name}: {node.value[:60]!r}")
    assert not offenders, offenders


def test_the_connector_package_never_reads_ground_truth():
    """Evaluated code only, so a module stays free to document the prohibition.

    ``transport.py``'s docstring exists precisely to explain why a transport must not reach
    ``truth/``. A raw text scan would fail on that sentence, and the cheapest way to make it
    pass would be to delete the explanation rather than the dependency. Same reasoning, and
    the same helper shape, as ``tests/test_mocks.py``.
    """
    offenders: list[str] = []
    for path in sorted(CONNECTORS_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docstrings = {
            id(node.body[0].value)
            for node in ast.walk(tree)
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and getattr(node, "body", None)
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)
        }
        for node in ast.walk(tree):
            name = None
            if isinstance(node, ast.Name):
                name = node.id
            elif isinstance(node, ast.Attribute):
                name = node.attr
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                name = None if id(node) in docstrings else node.value
            if name and ("ground_truth" in name or "truth_dir" in name):
                offenders.append(f"{path.name}: {name}")
    assert not offenders, f"connector modules evaluating ground truth: {offenders}"


def test_no_module_on_the_ingest_path_evaluates_ground_truth():
    """``src/recon/ingest/__init__.py`` promises this scan exists. Now it does.

    The promise was there before the scan was, which is the worst arrangement of the two:
    an auditor reads "enforced by a test that scans this package", believes it, and stops
    looking. ``tests/test_generators.py`` covers ``db/`` and ``crosswalk/`` only.

    The stakes rose with requirement A2. The prohibition used to be structural — the loader
    took a feeds directory and could not reach ``truth/`` even by accident — and a
    ``Transport`` is a more general thing than a path. ``api/`` is included because it is the
    only production caller of ``load_feeds``, and a guarantee that holds everywhere except at
    its own entry point is not a guarantee.

    Evaluated code only, for the same reason as every other scan in this repository: these
    modules have to stay free to document the prohibition, and the cheap way to pass a raw
    text scan is to delete the sentence rather than the dependency.
    """
    offenders: list[str] = []
    for package in ("ingest", "engine", "api"):
        for path in sorted((REPO_ROOT / "src" / "recon" / package).rglob("*.py")):
            for name in _evaluated_names(path.read_text(encoding="utf-8")):
                if "ground_truth" in name or "truth_dir" in name:
                    offenders.append(f"{package}/{path.name}: {name}")
    assert not offenders, (
        f"modules on the ingest path evaluating ground truth: {offenders}; "
        "the crosswalk stops being a measurement the moment one of them can read the answer"
    )


def _evaluated_names(source: str) -> set[str]:
    """Identifiers and non-docstring string literals the module actually evaluates."""
    tree = ast.parse(source)
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and getattr(node, "body", None)
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    }
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in docstrings:
                names.add(node.value)
    return names


def test_no_http_error_chains_an_exception_that_could_carry_a_token():
    """httpx masks two header names, and ours is not one of them.

    ``httpx._utils._obfuscate_sensitive_headers`` replaces the value of ``authorization`` and
    ``proxy-authorization`` with ``[secure]`` in a ``Headers`` repr — and nothing else. Beacon's
    header names are INVENTED (``BEACON-011`` establishes only that two tokens exist; the
    exchange itself is one of the eight items ``beacon.md`` lists as UNKNOWN), so ours are
    ``X-Beacon-*`` and render in full.

    ``httpx.RequestError`` carries ``.request``, and ``request.headers`` holds both tokens. So a
    chained httpx exception puts both on any traceback that prints it. ``raise ... from None``
    does not help: it clears ``__cause__`` and leaves ``__context__``.

    ``sftp.py`` *does* chain its connection errors, and that is fine there — paramiko's text
    holds nothing secret. The argument does not survive the move to HTTP, which is why the two
    sibling modules deliberately differ.
    """
    httpx = pytest.importorskip("httpx")

    # The premise, verified rather than asserted from memory — if httpx ever masks by default
    # this test should be reconsidered, not silently kept.
    rendered = repr(httpx.Headers({"X-Beacon-Access-Token": "tok-abc-123"}))
    assert "tok-abc-123" in rendered, (
        "httpx now masks non-standard headers; the reason http.py refuses to chain may have "
        "gone away, and the docstring above should be revisited rather than left stale"
    )

    tree = ast.parse((CONNECTORS_DIR / "http.py").read_text(encoding="utf-8"))

    # Only raises inside a function are interesting. The module-level guard that turns a
    # missing httpx into an actionable ImportError chains deliberately, and it runs at import
    # time when no request — and therefore no token — exists yet. Flagging it would have
    # bought nothing and cost the one chained traceback in the file that genuinely helps.
    inside_functions = [
        node
        for parent in ast.walk(tree)
        if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef))
        for node in ast.walk(parent)
        if isinstance(node, ast.Raise)
    ]
    chained = [
        node.lineno
        for node in inside_functions
        if node.cause is not None
        # `raise X from None` is the safe form and parses as a Constant None cause.
        and not (isinstance(node.cause, ast.Constant) and node.cause.value is None)
    ]
    assert not chained, (
        f"http.py chains an exception at line(s) {chained}; an httpx error reached that way "
        "carries request.headers, and both Beacon tokens render in full inside them"
    )


# ═══ E1, E2, E3 — the connector key builders, and the one place they are built ═══
#
# Every assertion below pins the *rendered string*, not just the key type.  A crosswalk
# lookup is a point seek on ``(key_type, key_value)``, so a builder that returns the right
# enum member and a differently-spelled value fails in the quietest way this system has:
# nothing raises, nothing logs, and every record that used it parks as ``NO_KEY_MATCH``.


def test_beacon_id_renders_the_identifier_untouched():
    """C5's key. Single component, so the canonical form *is* the component."""
    key_type, key_value = keys.beacon_id("BCN-000000000001")
    assert key_type is KeyType.BEACON_ID
    assert key_value == "BCN-000000000001"


def test_payment_reference_renders_the_reference_untouched():
    """E3's key. Same two-hop shape as ``TRN02`` and ``ALLOCATION_CODE``, different issuer."""
    key_type, key_value = keys.payment_reference("BCN-PAY-000042")
    assert key_type is KeyType.PAYMENT_REFERENCE
    assert key_value == "BCN-PAY-000042"


def test_payment_reference_is_a_different_key_type_from_the_same_string_as_trn02():
    """The distinction E3 exists for, asserted rather than described.

    A rebate payment reference and a payer trace number can legitimately be the same
    characters. If they shared a key type they would resolve to each other's targets — a
    rebate batch answering a bank deposit's lookup, which reads as a successful match.
    """
    shared = "0000012345"
    assert keys.payment_reference(shared)[1] == keys.trn02(shared)[1]
    assert keys.payment_reference(shared)[0] is not keys.trn02(shared)[0]
    assert keys.payment_reference(shared)[0] is not keys.allocation_code(shared)[0]


def test_hcpcs_medical_puts_the_jcode_where_the_ndc_goes():
    """E2's key, rendered ``provider_npi|hcpcs|service_date``.

    The argument order leads with the J-code and the component order does not. Pinned
    literally because a transposition would build a plausible-looking key that resolves to
    nothing, and no exception would be raised on the way.
    """
    key_type, key_value = keys.hcpcs_medical("J9035", "1730123456", "2026-03-02")
    assert key_type is KeyType.HCPCS
    assert key_value == "1730123456|J9035|2026-03-02"


def test_hcpcs_medical_is_its_sibling_with_the_jcode_substituted():
    """E2 is ``natural_340b_medical`` with the J-code standing in for the NDC.

    Asserted structurally so the two cannot drift apart: if someone reorders one builder's
    components, this fails even though both builders still return a well-formed string.
    """
    _, medical = keys.natural_340b_medical("1730123456", "00093721410", "2026-03-02")
    _, hcpcs = keys.hcpcs_medical("J9035", "1730123456", "2026-03-02")
    assert medical == "1730123456|00093721410|2026-03-02"
    assert hcpcs == medical.replace("00093721410", "J9035")


def test_covered_entity_340b_scopes_an_already_canonical_key_value():
    """E1's key: the covered entity joined to a whole key value, not a bare entity id."""
    _, natural = keys.natural_340b_pharmacy(
        "1730123456", "7845102", "00093721410", "2026-03-02"
    )
    key_type, key_value = keys.covered_entity_340b("DSH123456", natural)
    assert key_type is KeyType.COVERED_ENTITY_340B
    assert key_value == "DSH123456|1730123456|7845102|00093721410|2026-03-02"
    # The failure this design exists to prevent: a bare entity id as the key value, which
    # every episode of that entity would collide onto.
    assert key_value != "DSH123456"
    assert key_value.startswith("DSH123456|")


def test_a_tpa_record_for_the_wrong_covered_entity_builds_a_different_string():
    """Requirement E1's acceptance, in miniature.

    *"A TPA record for the wrong covered entity does not resolve."* Nothing compares the two
    entity ids anywhere — the wrong one simply builds a different string, seeks nothing and
    parks as ``NO_KEY_MATCH``. That is the point of scoping rather than checking: there is no
    comparison for a later caller to forget.
    """
    _, natural = keys.natural_340b_pharmacy(
        "1730123456", "7845102", "00093721410", "2026-03-02"
    )
    _, right = keys.covered_entity_340b("DSH123456", natural)
    _, wrong = keys.covered_entity_340b("DSH999999", natural)
    assert right != wrong
    assert wrong == "DSH999999|1730123456|7845102|00093721410|2026-03-02"


def test_covered_entity_ids_and_jcodes_are_not_normalised_either():
    """Decision A23 reaches the new builders too.

    A 340B ID spelled differently in two feeds is identifier drift, which is a defect this
    system exists to *surface*. Case-folding it here would resolve the record and delete the
    exception.
    """
    _, natural = keys.natural_340b_medical("1730123456", "00093721410", "2026-03-02")
    assert (
        keys.covered_entity_340b("DSH123456", natural)[1]
        != keys.covered_entity_340b("dsh123456", natural)[1]
    )
    assert (
        keys.hcpcs_medical("J9035", "1730123456", "2026-03-02")[1]
        != keys.hcpcs_medical("j9035", "1730123456", "2026-03-02")[1]
    )


# ── the refusals ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("absent", [None, ""])
@pytest.mark.parametrize(
    ("builder", "rest"),
    [
        (keys.beacon_id, ()),
        (keys.payment_reference, ()),
        (keys.hcpcs_medical, ("1730123456", "2026-03-02")),
    ],
)
def test_a_missing_leading_component_refuses_to_build_a_key(builder, rest, absent):
    """A key with a missing component is not a weaker key, it is a *different* key.

    Every builder's first argument, absent and empty. The caller must park the record rather
    than receive a partial key that collides with some other record's.
    """
    with pytest.raises(keys.MalformedKeyComponentError):
        builder(absent, *rest)


@pytest.mark.parametrize("absent", [None, ""])
def test_hcpcs_medical_refuses_a_missing_npi_or_service_date(absent):
    """The other two components, so the parametrisation above cannot pass vacuously."""
    with pytest.raises(keys.MalformedKeyComponentError):
        keys.hcpcs_medical("J9035", absent, "2026-03-02")
    with pytest.raises(keys.MalformedKeyComponentError):
        keys.hcpcs_medical("J9035", "1730123456", absent)


@pytest.mark.parametrize("absent", [None, ""])
def test_covered_entity_340b_refuses_a_missing_entity_or_a_missing_scope(absent):
    """Both halves must be present, reached by two deliberately different routes.

    The entity id is checked by ``_component``; the scoped half is checked by an explicit
    branch, because ``_component`` would reject it for containing separators. An unscoped
    covered-entity key is exactly the collision this builder exists to avoid, so it must
    raise rather than degrade into a bare entity id.
    """
    _, natural = keys.natural_340b_medical("1730123456", "00093721410", "2026-03-02")
    with pytest.raises(keys.MalformedKeyComponentError):
        keys.covered_entity_340b(absent, natural)
    with pytest.raises(keys.MalformedKeyComponentError):
        keys.covered_entity_340b("DSH123456", absent)


@pytest.mark.parametrize(
    ("builder", "rest"),
    [
        (keys.beacon_id, ()),
        (keys.payment_reference, ()),
        (keys.hcpcs_medical, ("1730123456", "2026-03-02")),
    ],
)
def test_a_component_carrying_the_separator_refuses_to_build_a_key(builder, rest):
    """``|`` inside a component would collide two distinct keys onto one string."""
    with pytest.raises(keys.MalformedKeyComponentError, match="separator"):
        builder("A|B", *rest)


def test_covered_entity_340b_refuses_a_separator_in_the_entity_id():
    """The entity id is an ordinary component and the ordinary rule applies to it."""
    _, natural = keys.natural_340b_medical("1730123456", "00093721410", "2026-03-02")
    with pytest.raises(keys.MalformedKeyComponentError, match="separator"):
        keys.covered_entity_340b("DSH|123456", natural)


def test_the_scoped_half_is_the_one_exemption_from_the_separator_ban():
    """Pinned so the exemption is visible rather than accidental.

    ``covered_entity_340b`` is the only builder in the module that accepts a value containing
    ``|``, because the value it scopes is *itself* a canonical composite key value. The ban
    exists to stop one component smuggling in a separator; here the separators are the
    deliberate structure of a string another builder in this module produced.

    If this ever starts raising, someone has "tidied up" the scoped half onto ``_component``
    and every scoped key in the system has silently stopped being buildable.
    """
    _, natural = keys.natural_340b_pharmacy(
        "1730123456", "7845102", "00093721410", "2026-03-02"
    )
    assert keys.SEPARATOR in natural

    # The exempt half: accepted, and every separator survives verbatim.
    _, scoped = keys.covered_entity_340b("DSH123456", natural)
    assert scoped.count(keys.SEPARATOR) == natural.count(keys.SEPARATOR) + 1
    assert scoped.endswith(natural)

    # The same string in the *non*-exempt position still raises, which is what makes the
    # exemption a decision about one argument rather than a hole in the builder.
    with pytest.raises(keys.MalformedKeyComponentError, match="separator"):
        keys.covered_entity_340b(natural, natural)


# ── the relocation ──────────────────────────────────────────────────────────


def test_beacons_key_helpers_are_byte_identical_to_the_crosswalks():
    """``beacon.py`` no longer builds a ``key_value``; it delegates.

    The regression this catches is otherwise invisible. Two construction sites for one key
    agree on the day they are written and drift on the day one of them is edited — and the
    symptom is not an exception, it is a lookup that seeks a string nothing ever published.
    """
    for identifier in ("BCN-000000000001", "0000012345", "beacon id with spaces"):
        assert beacon.beacon_id_key(identifier) == keys.beacon_id(identifier)
    for reference in ("BCN-PAY-000042", "0000012345", "  padded  "):
        assert beacon.payment_reference_key(reference) == keys.payment_reference(reference)


@pytest.mark.parametrize("bad", [None, "", "A|B"])
def test_beacons_key_helpers_still_raise_beacon_mapping_error(bad):
    """The relocation moved where the string is built, and nothing a caller can observe.

    ``BeaconMappingError`` is that module's error contract; the crosswalk raises its own
    type. The wrappers validate locally first so the contract is unchanged — otherwise the
    move would be a silent API break dressed up as a refactor.
    """
    with pytest.raises(beacon.BeaconMappingError):
        beacon.beacon_id_key(bad)
    with pytest.raises(beacon.BeaconMappingError):
        beacon.payment_reference_key(bad)
