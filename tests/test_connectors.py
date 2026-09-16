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
import hashlib
import json
import re
import sqlite3
from pathlib import Path

import pytest

from recon import config
from recon.connectors import credentials, registry, schema_registry, transport
from recon.connectors.transport import Document, ForbiddenPathError, LocalDirectoryTransport
from recon.db import connection, migrate, repository
from recon.domain.enums import QuarantineReason, SourceSystem, TransportKind
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
    assert fields == {"name", "text"}, f"Document grew {sorted(fields - {'name', 'text'})}"


# ═══ A1 — sources are data, not code ═══


def test_pipeline_names_no_vendor_and_no_feed_filename():
    """Requirement A1's acceptance, stated as the property that makes it true.

    "Adding a seventh source is a config row plus a mapping module, with no edit to
    ``pipeline.py``" stays true exactly as long as ``pipeline.py`` never names a vendor.  The
    moment it does, onboarding becomes an edit again.
    """
    source_text = (REPO_ROOT / "src" / "recon" / "ingest" / "pipeline.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source_text)
    literals = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    offenders = sorted(
        literal
        for literal in literals
        if literal in set(config.FEED_FILENAMES)
        or any(vendor in literal.lower() for vendor in ("beacon", "verity", "craneware"))
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


def test_revealing_a_secret_is_a_single_greppable_call():
    """One escape hatch, named so the audit is a grep rather than a review."""
    assert credentials.Secret("abc").reveal() == "abc"
    offenders = [
        path.name
        for path in sorted(CONNECTORS_DIR.rglob("*.py"))
        if ".reveal()" in path.read_text(encoding="utf-8") and path.name != "credentials.py"
    ]
    # Nothing in wave 2 transports a credential yet; SFTP and HTTP will be the first, and
    # this list is where that becomes visible rather than a thing someone has to notice.
    assert offenders == [], f"credential use sites (expected once SFTP/HTTP land): {offenders}"


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
        r"(?i)(access_token|private_token|password|passphrase|secret)\s*[:=]\s*[\"'][^\"'{}$<>]{8,}[\"']"
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
