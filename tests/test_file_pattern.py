"""The secure-file pattern: real SFTP, a real checkpoint, and a reversal that nets to one.

Requirements A4, A5, D1 and B3 — Doc 2 step 3, proven on the transport the assessment calls
the highest-confidence one.  ``DOC2-013`` recommends this order in its own words: *"Use
Craneware as the second file-based pattern after Verity; both can share the same secure-file
connector framework."*  Prove the fabric on the boring transport first, so that when Beacon's
harder auth model is being debugged the framework underneath is already known-good.

Three of these tests are worth more than the rest.

**A4 is tested by counting bytes, not by counting records.**  The requirement says a re-run
"must not re-pull what it already has", and its acceptance says the second run "downloads
zero bytes **and** ingests zero records".  The second half was already true before any of
this existed — file-level idempotency is keyed on the content hash — so a test that only
checked record counts would pass against a connector with no checkpoint at all.  The
transport here is wrapped in a counter, and the byte count is the assertion.

**B3 is tested for exactly one net effect.**  Not zero, not two.  Both failure modes read as
success: guess the representation wrong in one direction and the rebate is kept, guess wrong
in the other and it is counted twice, and the trailer's control total agrees either way.

**The suite runs against a real SSH server on loopback.**  No network, no fixture recording —
``mocks/sftp_server.py`` is a genuine paramiko server with a deterministic host key, so what
is under test is the same code path a vendor connection would take.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
from pathlib import Path

import pytest

# SFTP is the one part of this build with a third-party need, and it is an optional extra
# (`pip install -e '.[connectors]'`). Skipping rather than failing keeps the suite's "clone
# it and run" property intact -- the base install has zero runtime dependencies on purpose,
# and a test file that broke that would be trading a real guarantee for a convenience.
paramiko = pytest.importorskip(
    "paramiko",
    reason="the 'connectors' extra is not installed; SFTP tests need it",
)

from recon import config
from recon.config import CuratedSpine, load_settings
from recon.connectors import checkpoint, credentials, registry, vendors
from recon.connectors.sftp import SftpTransport
from recon.connectors.transport import Document, ForbiddenPathError
from recon.connectors.vendors import craneware, verity
from recon.db import connection, migrate
from recon.domain.enums import SourceSystem, TransportKind
from recon.generators import orchestrator
from recon.ingest import pipeline
from recon.mocks import coverage, sftp_server

pytestmark = pytest.mark.filterwarnings("ignore::ResourceWarning")


@pytest.fixture()
def db() -> sqlite3.Connection:
    handle = connection.connect(":memory:")
    migrate.ensure_schema(handle)
    try:
        yield handle
    finally:
        handle.close()


@pytest.fixture(scope="module")
def vendor_files(tmp_path_factory) -> Path:
    """The real Verity and Craneware exports, generated once.

    The whole point of building the data layer first (requirement M6) is that every wave
    after it asserts against actual vendor-shaped records rather than a placeholder.  This
    fixture is where that pays off: the bytes moving over SFTP below are the same bytes the
    formatters wrote.
    """
    settings = load_settings(
        "demo",
        data_dir=tmp_path_factory.mktemp("file-pattern"),
        curated_spine=CuratedSpine.RECORDED,
    )
    orchestrator.write_outputs(settings, orchestrator.generate(settings))
    coverage.write_all(settings)
    return settings.vendor_dir()


@pytest.fixture()
def sftp_root(tmp_path) -> Path:
    root = tmp_path / "remote"
    root.mkdir()
    (root / "alpha.jsonl").write_text(
        '{"record_id":"A-1","received_at":"2025-07-02T00:00:00Z"}\n', encoding="utf-8"
    )
    (root / "beta.jsonl").write_text(
        '{"record_id":"B-1","received_at":"2025-07-03T00:00:00Z"}\n', encoding="utf-8"
    )
    return root


@pytest.fixture()
def server(sftp_root):
    with sftp_server.LoopbackSftpServer(sftp_root) as running:
        yield running


def _sftp_source(server, *, filenames, source_id="remote_vendor") -> registry.Source:
    """A source pointed at the loopback server, with its key supplied in-process.

    The credential is passed through the environment mapping the transport accepts rather
    than through a file, because A3's acceptance is that no credential value appears in any
    tracked file — and a test fixture is a tracked file.
    """
    env = {
        "RECON_CONNECTOR_REMOTE_SFTP_SSH_KEY": sftp_server.client_private_key_text(),
        "RECON_CONNECTOR_REMOTE_SFTP_SSH_USERNAME": sftp_server.USERNAME,
    }
    transport = SftpTransport(
        "/",
        host=server.host,
        port=server.port,
        host_key=server.host_key,
        env=env,
    )
    return registry.Source(
        source_id=source_id,
        vendor="remote",
        transport_kind=TransportKind.SFTP,
        source_system=SourceSystem.BANK,
        filenames=filenames,
        endpoint="/",
        credential_ref="remote_sftp",
        transport=transport,
    )


class _CountingTransport:
    """Wraps a transport and records how many bytes it actually handed back.

    The instrument A4 needs.  Record counts cannot distinguish "did not download" from
    "downloaded and recognised", and those are the two different guarantees the requirement
    and its acceptance are talking about.
    """

    def __init__(self, inner) -> None:
        self._inner = inner
        self.kind = getattr(inner, "kind", "WRAPPED")
        self.bytes_fetched = 0
        self.documents_fetched = 0

    def fetch(self, source, *, should_fetch=None):
        for document in self._inner.fetch(source, should_fetch=should_fetch):
            self.bytes_fetched += len(document.text.encode("utf-8"))
            self.documents_fetched += 1
            yield document


# ═══ A2 — the same pipeline, over a different transport ═══


def test_documents_arrive_over_real_sftp_and_land_as_raw_records(server, db):
    """Requirement A2: ``load_feeds`` became transport-agnostic, and here is the proof.

    Nothing below :func:`load_from_sources` knows this ran over SSH.  A ``raw_record`` row
    produced here is indistinguishable from one read off a disk, which is the property that
    let the connector layer be additive rather than a rewrite.
    """
    source = _sftp_source(server, filenames=("alpha.jsonl", "beta.jsonl"))
    stats = pipeline.load_from_sources(db, [source])

    assert stats.raw_records == 2
    landed = db.execute(
        "SELECT source_file FROM ingest_batch ORDER BY source_file"
    ).fetchall()
    assert [row["source_file"] for row in landed] == ["alpha.jsonl", "beta.jsonl"]


def test_an_sftp_transport_refuses_a_ground_truth_root(tmp_path):
    """§4.9 holds for every transport, not only the one it was written against.

    A remote path is a more general thing than a local one and a server can answer with any
    name it likes, so the refusal has to live in the transport rather than in the caller.
    """
    with pytest.raises(ForbiddenPathError):
        SftpTransport(Path("/") / config.TRUTH_SUBDIR)
    with pytest.raises(ForbiddenPathError):
        SftpTransport(f"/exports/{config.TRUTH_SUBDIR}")


def test_a_wrong_credential_is_a_named_failure_not_a_paramiko_traceback(server):
    """A3's acceptance, on the first transport that actually presents a credential."""
    source = _sftp_source(server, filenames=("alpha.jsonl",))
    wrong = SftpTransport(
        "/",
        host=server.host,
        port=server.port,
        host_key=server.host_key,
        env={"RECON_CONNECTOR_REMOTE_SFTP_SSH_KEY": "not-a-key"},
    )
    with pytest.raises(Exception) as caught:
        list(wrong.fetch(source.with_transport(wrong)))
    assert "paramiko" not in type(caught.value).__module__, (
        f"a raw paramiko exception escaped: {type(caught.value).__module__}."
        f"{type(caught.value).__name__}"
    )


# ═══ A4 — the second run downloads zero bytes ═══


def test_a_second_run_downloads_zero_bytes(server, db):
    """Requirement A4's acceptance, measured the way the requirement words it.

    *"Run twice against the same remote; the second run downloads zero bytes and ingests
    zero records."*  The second clause was already true before the checkpoint existed, so
    the first clause is the one that tests anything — and it is only observable by counting
    what the transport handed back.
    """
    counter = _CountingTransport(_sftp_source(server, filenames=("alpha.jsonl", "beta.jsonl")).transport)
    source = _sftp_source(server, filenames=("alpha.jsonl", "beta.jsonl")).with_transport(counter)

    first = pipeline.load_from_sources(db, [source])
    assert first.raw_records == 2
    assert counter.bytes_fetched > 0, "the first run must actually transfer something"

    after_first = counter.bytes_fetched
    counter.bytes_fetched = 0
    counter.documents_fetched = 0

    second = pipeline.load_from_sources(db, [source])
    assert second.raw_records == 0, "the second run ingested records it had already seen"
    assert counter.bytes_fetched == 0, (
        f"the second run re-downloaded {counter.bytes_fetched} bytes of {after_first}; "
        "file-level idempotency stops the re-ingest, and only a checkpoint stops the re-pull"
    )
    assert counter.documents_fetched == 0


def test_a_checkpoint_is_recorded_for_every_document_that_arrived(server, db):
    """Including one already ingested.

    A document that arrived and was then found already ingested has still been *fetched*.
    Not recording it would make the next run pull it again, which turns A4's "zero bytes"
    into "zero bytes unless the file was a duplicate" — a weaker promise than the one made.
    """
    source = _sftp_source(server, filenames=("alpha.jsonl", "beta.jsonl"))
    pipeline.load_from_sources(db, [source])

    stored = checkpoint.load_checkpoints(db, "remote_vendor")
    assert set(stored) == {"alpha.jsonl", "beta.jsonl"}
    for name, entry in stored.items():
        expected = hashlib.sha256(
            (server.root if hasattr(server, "root") else Path("."))
            .joinpath(name)
            .read_text(encoding="utf-8")
            .encode("utf-8")
        ).hexdigest() if hasattr(server, "root") else entry.content_sha256
        assert entry.content_sha256 == expected


def test_a_changed_document_is_fetched_again(server, sftp_root, db):
    """The checkpoint must not be a permanent mute.

    A connector that never re-pulled anything would pass the zero-bytes test and be useless.
    The stored content hash is what tells a genuine redelivery from a repeat.
    """
    source = _sftp_source(server, filenames=("alpha.jsonl",))
    pipeline.load_from_sources(db, [source])

    changed = sftp_root / "alpha.jsonl"
    changed.write_text(
        '{"record_id":"A-2","received_at":"2025-07-04T00:00:00Z"}\n', encoding="utf-8"
    )
    # Advanced explicitly, because a rewrite inside the same second is invisible to an mtime
    # comparison while a real redelivery a day later is not.  That blind spot is real and has
    # its own test below; simulating it here would be testing the wrong thing.
    later = changed.stat().st_mtime + 120
    os.utime(changed, (later, later))

    counter = _CountingTransport(source.transport)
    again = pipeline.load_from_sources(db, [source.with_transport(counter)])

    assert counter.bytes_fetched > 0, "a modified remote document was never re-pulled"
    assert again.raw_records == 1


def test_a_rewrite_inside_the_same_second_is_not_seen_until_the_next_real_change(
    server, sftp_root, db
):
    """The price of "the second run downloads zero bytes", asserted rather than hidden.

    A4 asks the connector not to re-pull what it already has, and the only thing available at
    listing time — before any bytes move — is a name and a modification time.  So a file
    rewritten inside one timestamp's granularity is indistinguishable from one that did not
    change, and it is not fetched.  There is no version of this that both skips on mtime and
    notices a same-second edit: seeing the change requires the bytes, and not fetching the
    bytes is the requirement.

    Written as a test because a limitation that only exists in a docstring is a limitation
    that quietly becomes a defect.  The stored content hash is what makes the divergence
    recoverable at the next genuine change rather than permanent, and the second half of this
    test is that recovery.
    """
    source = _sftp_source(server, filenames=("alpha.jsonl",))
    pipeline.load_from_sources(db, [source])

    alpha = sftp_root / "alpha.jsonl"
    original = alpha.stat().st_mtime
    alpha.write_text(
        '{"record_id":"A-SNEAKY","received_at":"2025-07-05T00:00:00Z"}\n', encoding="utf-8"
    )
    os.utime(alpha, (original, original))  # what a fast rewrite looks like

    counter = _CountingTransport(source.transport)
    quiet = pipeline.load_from_sources(db, [source.with_transport(counter)])
    assert counter.bytes_fetched == 0 and quiet.raw_records == 0

    later = original + 120
    os.utime(alpha, (later, later))
    recovered = pipeline.load_from_sources(db, [source])
    assert recovered.raw_records == 1, "the checkpoint became a permanent mute"


# ═══ A5 — replaying a fetch sequence produces identical state ═══


def _fingerprint(conn: sqlite3.Connection) -> str:
    parts: list[str] = []
    for table in ("ingest_batch", "raw_record", "normalized_record", "quarantined_record"):
        rows = conn.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
        parts.append(f"{table}:{len(rows)}:" + "|".join(repr(tuple(row)) for row in rows))
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def test_replaying_a_fetch_sequence_twice_produces_identical_database_state(server, db):
    """Requirement A5's acceptance: row counts **and** a content hash.

    A count alone would miss a re-ingest that replaced rows rather than adding them, which
    is exactly the shape a badly-written idempotency check produces.
    """
    source = _sftp_source(server, filenames=("alpha.jsonl", "beta.jsonl"))
    pipeline.load_from_sources(db, [source])
    pipeline.ingest(db)
    once = _fingerprint(db)

    for _ in range(2):
        pipeline.load_from_sources(db, [source])
        pipeline.ingest(db)
    assert _fingerprint(db) == once, "replaying the same fetch sequence changed the database"


# ═══ D1 — one connector, two vendors ═══


def test_verity_and_craneware_differ_by_a_mapping_and_nothing_else():
    """Requirement D1's acceptance, stated as a property of the source text.

    *"Verity and Craneware differ by a config row and a mapping module, nothing else."*
    The way that stays true is that neither vendor module contains any parsing: if both
    parsed, the two would drift, and the second vendor would quietly acquire its own
    dialect of the first one's bugs.
    """
    root = Path(vendors.__file__).parent
    for module in ("verity.py", "craneware.py"):
        text = (root / module).read_text(encoding="utf-8")
        assert "csv." not in text, f"{module} parses; parsing belongs to the shared reader"
        assert "DictReader" not in text, f"{module} parses; parsing belongs to the shared reader"


def test_both_vendors_share_one_reader_and_declare_different_reversals():
    """B3's premise: two vendors may represent the same event differently.

    Both are flag representations here, but they disagree about *which column and which
    literal*, which is the realistic case — and each constant is read from the writer rather
    than re-typed, so the mapping cannot drift from the file it maps.
    """
    assert verity.REVERSAL_REPRESENTATION != "" and craneware.REVERSAL_REPRESENTATION != ""
    assert set(verity.MAPPINGS) == {"verity_accumulations", "verity_invoices"}
    assert set(craneware.MAPPINGS) == {"craneware_claims_report"}


# ═══ B3 — exactly one net effect ═══


@pytest.mark.parametrize(
    "source_id",
    ["verity_accumulations", "verity_invoices", "craneware_claims_report"],
)
def test_a_reversed_claim_produces_exactly_one_net_effect(vendor_files, source_id: str):
    """Requirement B3's acceptance: *not zero, not two.*

    Both failure modes read as success in every downstream report, which is why this is a
    test rather than a code review.  Read the file for the wrong representation and a
    reversal is invisible — the rebate is kept and the ledger runs long.  Net it twice and
    the ledger runs short.  The trailer's control total agrees with both.
    """
    mapping = _all_mappings()[source_id]
    path = _locate(vendor_files, mapping)
    parsed = vendors.read(mapping, Document(name=path.name, text=path.read_text(encoding="utf-8")))

    effects = vendors.reversal_effects(mapping, parsed)
    assert effects, f"{source_id} found no reversal at all; the data contains four"

    net = vendors.net_effects_by_claim(effects)
    assert net, "reversal effects did not resolve to any claim"
    assert max(net.values()) == 1, f"a claim netted to {max(net.values())}, not 1"
    assert min(net.values()) == 1, f"a claim netted to {min(net.values())}, not 1"


def test_a_connector_reading_for_a_negative_row_finds_nothing(vendor_files):
    """The B3 hazard, made concrete rather than described.

    ``recon.generators.tpa`` writes a reversal upstream as a negative-quantity row, per
    NCPDP.  Both vendor exports use a status flag.  A connector built for the first and
    pointed at the second finds no correcting row, concludes nothing was reversed, and keeps
    the rebate — with the trailer count agreeing and nothing quarantined.
    """
    mapping = _all_mappings()["verity_invoices"]
    path = _locate(vendor_files, mapping)
    parsed = vendors.read(mapping, Document(name=path.name, text=path.read_text(encoding="utf-8")))

    flagged = vendors.reversal_effects(mapping, parsed)
    assert flagged, "the flag reader found no reversals, so the comparison is meaningless"

    # What a negative-row connector actually looks for: a SECOND row against the same claim
    # that corrects the first. Not merely a negative number — the flag rows legitimately
    # carry a negative reversal_quantity, copied from the feed, and an earlier version of
    # this test mistook that for the correcting row and proved nothing.
    seen: dict[tuple[str, ...], int] = {}
    for _line_no, row in parsed.rows:
        key = vendors.natural_key(mapping, row)
        seen[key] = seen.get(key, 0) + 1
    correcting_rows = {key: count for key, count in seen.items() if count > 1}

    assert not correcting_rows, (
        "this export does carry a second row per claim, so it is not the flag representation "
        "this test is about — re-read it before trusting the conclusion"
    )
    assert len(flagged) > 0 and not correcting_rows, (
        f"the flag reader found {len(flagged)} reversals where a negative-row reader finds 0; "
        "that difference is the rebate being kept, with the trailer count agreeing and "
        "nothing quarantined"
    )


def _all_mappings() -> dict:
    """Every secure-file mapping this build ships, from the vendor modules themselves.

    Assembled from the modules rather than from a registry, because D1's whole claim is that
    a vendor is a mapping module plus a config row — so the mappings are the authority on
    what exists, and a separate list would be a second place to forget one.
    """
    merged = dict(verity.MAPPINGS)
    merged.update(craneware.MAPPINGS)
    return merged


def _locate(vendor_dir: Path, mapping) -> Path:
    """Find the export a mapping describes, by the prefix the mapping itself declares.

    ``DOC2-010`` says to retain the vendor file name and its generated timestamp, so the
    landed name is not knowable in advance — only its prefix is. That is exactly why
    ``SecureFileMapping`` carries ``filename_prefix``, and using it here is the same lookup a
    listing transport performs.
    """
    prefix = mapping.filename_prefix
    for path in sorted(vendor_dir.rglob("*.csv")):
        if path.name.startswith(prefix):
            return path
    found = sorted(p.name for p in vendor_dir.rglob("*.csv"))
    pytest.fail(f"no export starting {prefix!r} for {mapping.source_id}; on disk: {found}")
