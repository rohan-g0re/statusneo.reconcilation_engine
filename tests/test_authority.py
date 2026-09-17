"""Requirement E5: the source-of-truth boundary, enforced in data.

``src/recon/connectors/authority.py`` transcribes Doc 2's page-2 table (``DOC2-004``) as
data.  A transcription is only worth anything if something checks it against the document,
so these tests do three separable jobs and it is worth naming them apart:

1. **The rule works.**  E5's literal acceptance — a TPA record attempting to set
   rebate-payment status is rejected — plus the cases that must *not* be rejected, because a
   table that refuses a TPA's own qualification is not strict, it is wrong.
2. **The table matches the document.**  The ``(system, clause)`` pairs in the module are
   diffed row by row against ``docs/vendor_evidence/assignment_doc2.md``, and every evidence
   id the table cites is joined against ``index.jsonl`` — the same join
   ``tests/test_vendor_evidence.py`` performs for vendor field mappings, for the same
   reason: a citation nobody can follow is decoration.
3. **The table admits reality.**  The six generated feeds are run through the real ingest
   pipeline and every row it writes is checked against the table.  An authority rule that
   quietly forbids the data the system already holds would be discovered in production, or
   here.
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

import pytest

from recon.connectors import authority
from recon.connectors.authority import (
    DOC2_004_AUTHORITY,
    RECORD_KINDS_WITHOUT_DOC2_AUTHORITY,
    SOURCE_SYSTEMS_WITHOUT_DOC2_AUTHORITY,
    SourceAuthorityError,
    check_authority,
)
from recon.domain.enums import RecordKind, SourceSystem

REPO_ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_DIR = REPO_ROOT / "docs" / "vendor_evidence"
INDEX_PATH = EVIDENCE_DIR / "index.jsonl"
DOC2_MARKDOWN = EVIDENCE_DIR / "assignment_doc2.md"

#: A blockquote row of the DOC2-004 table: ``> **TPA** — Authoritative for ...``.  The dash
#: is matched as a single unnamed character rather than written out, so this file needs no
#: opinion about which dash the markdown uses.
_DOC2_ROW = re.compile(r"^> \*\*(?P<system>[^*]+)\*\*\s*.\s*(?P<clause>\S.*)$")


# ═══ fixtures ═══════════════════════════════════════════════════════════════


@pytest.fixture(scope="module")
def evidence_ids() -> frozenset[str]:
    """Every id in the live evidence index, read from the real artefact.

    Mirrors ``tests/test_vendor_evidence.py::_index_rows``: the file itself, never a copy
    checked into ``tests/``, so deleting an entry the authority table cites turns this red.
    """
    assert INDEX_PATH.exists(), (
        f"{INDEX_PATH} is missing; it is the machine-readable half of the evidence "
        "directory and is hand-written, never generated"
    )
    return frozenset(
        json.loads(line)["id"]
        for line in INDEX_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )


@pytest.fixture(scope="module")
def unavailable_ids() -> frozenset[str]:
    return frozenset(
        json.loads(line)["id"]
        for line in INDEX_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip() and json.loads(line).get("status") == "UNAVAILABLE"
    )


@pytest.fixture(scope="module")
def doc2_004_rows() -> dict[str, str]:
    """The page-2 boundary table, parsed out of the evidence markdown.

    Returns ``{system: clause}`` with the line wrapping undone, which is the form the module
    stores and therefore the form the two can be compared in.
    """
    text = DOC2_MARKDOWN.read_text(encoding="utf-8")
    start = text.index("## DOC2-004")
    block = text[start : text.index("## DOC2-005", start)]

    rows: dict[str, str] = {}
    current: str | None = None
    for line in block.splitlines():
        if not line.startswith("> "):
            current = None
            continue
        match = _DOC2_ROW.match(line)
        if match:
            current = match.group("system").strip()
            rows[current] = match.group("clause").strip()
        elif current is not None:
            # A wrapped continuation of the row above.
            rows[current] = f"{rows[current]} {line[2:].strip()}"
    assert rows, f"no DOC2-004 rows parsed out of {DOC2_MARKDOWN}"
    return rows


@pytest.fixture(scope="module")
def ingested(tmp_path_factory) -> sqlite3.Connection:
    """The six generated feeds, ingested — the real boundary, not a hand-built record.

    ``demo`` rather than ``full``: the profiles share every generator and every adapter, so
    the *kinds* of record produced are identical and only the row counts differ.  This
    fixture exists to enumerate what the pipeline can write, not to count it.

    Deliberately stops after ``ingest``.  The engine is not involved in who may say what.
    """
    from recon.db import connection, migrate
    from recon.generators import orchestrator
    from recon.config import load_settings
    from recon.ingest import pipeline

    settings = load_settings("demo", data_dir=tmp_path_factory.mktemp("authority"))
    generation = orchestrator.generate(settings)
    orchestrator.write_outputs(settings, generation)

    conn = connection.connect(":memory:")
    migrate.ensure_schema(conn)
    stats = pipeline.load_feeds(conn, settings.feeds_dir())
    pipeline.ingest(conn, stats=stats)
    try:
        yield conn
    finally:
        conn.close()


# ═══ 1. the rule ════════════════════════════════════════════════════════════


def test_a_tpa_may_not_set_rebate_payment_status():
    """E5's acceptance criterion, verbatim: *rejected, not silently accepted*.

    Asserted on the message and not only the type, because the whole value of a named error
    here is that the person reading it learns **who** was entitled to say this.  An
    exception that says "invalid field" leaves them exactly where a silent accept did.
    """
    with pytest.raises(SourceAuthorityError) as excinfo:
        check_authority(
            SourceSystem.TPA_VERITY,
            RecordKind.TPA_MANUFACTURER_DECISION,
            {"manufacturer_status"},
        )

    message = str(excinfo.value)
    assert "TPA_VERITY" in message
    assert "rebate status" in message, (
        "the error must name the thing in business terms, not the column: "
        f"got {message!r}"
    )
    assert "Beacon" in message, f"the error must name who is authoritative: got {message!r}"
    assert "DOC2-004" in message, f"the error must cite the rule it enforces: got {message!r}"
    assert "manufacturer_status" in message


def test_the_breach_is_reported_structurally_as_well_as_in_prose():
    """A caller that wants to route on the breach should not have to parse the sentence."""
    with pytest.raises(SourceAuthorityError) as excinfo:
        check_authority(
            SourceSystem.TPA_CRANEWARE,
            RecordKind.TPA_QUALIFICATION,
            {"manufacturer_status"},
        )
    error = excinfo.value
    assert error.domain.domain == "REBATE_STATUS_AND_VALIDATION"
    assert [breach.field for breach in error.breaches] == ["manufacturer_status"]
    assert error.breaches[0].source_system is SourceSystem.TPA_CRANEWARE


def test_beacon_may_set_rebate_status():
    check_authority(
        SourceSystem.BEACON,
        RecordKind.TPA_MANUFACTURER_DECISION,
        {"manufacturer_status", "validation_outcome", "rejection_reason"},
    )


def test_a_tpa_may_set_340b_qualification():
    """The other half of the TPA row, and the half a too-eager table gets wrong.

    ``DOC2-004`` makes the TPA authoritative for 340B qualification.  A table that refuses
    this has not enforced the boundary, it has moved it.
    """
    for source in (
        SourceSystem.TPA_PORTAL,
        SourceSystem.TPA_VERITY,
        SourceSystem.TPA_CRANEWARE,
    ):
        check_authority(
            source,
            RecordKind.TPA_QUALIFICATION,
            {"qualification_status", "disqualification_reason"},
        )


def test_beacon_may_not_set_340b_qualification():
    """The boundary is a boundary in both directions.

    ``DOC2-010``: *"Use Verity as qualification/source context; Beacon submission/status can
    remain a separate Shields direct connector."*
    """
    with pytest.raises(SourceAuthorityError) as excinfo:
        check_authority(
            SourceSystem.BEACON, RecordKind.TPA_QUALIFICATION, {"qualification_status"}
        )
    message = str(excinfo.value)
    assert "340B qualification" in message
    assert "the TPA" in message


def test_the_bank_may_set_settled_cash():
    check_authority(
        SourceSystem.BANK, RecordKind.BANK_TRANSACTION, {"ach_trace_number", "running_balance_cents"}
    )


def test_a_non_bank_source_may_not_claim_settled_cash():
    """Both ways a source can claim cash it did not settle.

    By the *kind* — delivering a ``BANK_TRANSACTION`` at all — and by the *field*, slipping
    an ACH trace number onto a record of some other kind.  ``DOC2-007``'s payment-close row
    is the reason the two are distinct: *"Reconcile Beacon rebate/payment reference to bank
    settlement before marking cash received"* — Beacon's reference is not the settlement.
    """
    with pytest.raises(SourceAuthorityError) as by_kind:
        check_authority(SourceSystem.BEACON, RecordKind.BANK_TRANSACTION)
    assert "settled cash" in str(by_kind.value)
    assert "the bank" in str(by_kind.value)

    with pytest.raises(SourceAuthorityError) as by_field:
        check_authority(
            SourceSystem.TPA_VERITY, RecordKind.TPA_QUALIFICATION, {"ach_trace_number"}
        )
    assert "ach_trace_number" in str(by_field.value)
    assert "the bank" in str(by_field.value)


def test_the_bank_may_not_qualify_a_dispense():
    with pytest.raises(SourceAuthorityError) as excinfo:
        check_authority(SourceSystem.BANK, RecordKind.TPA_QUALIFICATION)
    assert "340B qualification" in str(excinfo.value)


def test_only_beacon_may_set_a_beacon_id():
    """``DOC2-007``: *"persist Beacon ID against Shields Claim Financial Episode."*

    The ID is assigned by Beacon on submission, so a source that sets one has not relayed an
    identifier — it has minted one.  ``KeyType.BEACON_ID`` makes it a join key, which is why
    this is worse than it sounds: a fabricated ``beacon_id`` does not fail to resolve, it
    resolves to the wrong episode.

    Checked against **every** ``SourceSystem`` member, including the rebate-status authority
    ``MANUFACTURER_REBATE``, which is entitled to say what the manufacturer decided and is
    still not entitled to mint Beacon's identifier.
    """
    for source in SourceSystem:
        found = [
            breach
            for breach in authority.breaches(source, RecordKind.REBATE_BATCH, {"beacon_id"})
            if breach.field == "beacon_id"
        ]
        if source is SourceSystem.BEACON:
            assert not found, f"{source.value} is Beacon and must be allowed to set beacon_id"
        else:
            assert found, (
                f"{source.value} was allowed to set beacon_id; only BEACON assigns one, and a "
                "fabricated join key resolves to the wrong episode rather than failing"
            )


def test_no_inbound_source_may_set_the_consolidated_picture():
    """``DOC2-004``'s Shields row, which is the one no vendor can ever satisfy.

    The consolidated expected / received / outstanding / exception status is what the engine
    computes from everyone else's facts.  A connector that delivered it would not be a
    source of truth, it would be a second opinion overwriting the first.
    """
    for source in SourceSystem:
        with pytest.raises(SourceAuthorityError) as excinfo:
            check_authority(source, RecordKind.REMITTANCE, {"disposition"})
        message = str(excinfo.value)
        assert "the Shields platform" in message
        assert "no inbound source may set it" in message


def test_a_record_that_asserts_nothing_governed_passes():
    """The table denies what others own; it does not allow-list every field.

    A PBM adjudication carries no DOC2-004 authority at all, and that must not mean its own
    ordinary fields are refused.
    """
    check_authority(
        SourceSystem.PBM_ADJUDICATION,
        RecordKind.PHARMACY_CLAIM,
        {"bin", "pcn", "response_status", "reject_codes", "total_amount_paid_cents"},
    )


def test_every_breach_is_reported_not_just_the_first():
    """A record crossing three boundaries should say so once, not three times over."""
    found = authority.breaches(
        SourceSystem.TPA_VERITY,
        RecordKind.REBATE_BATCH,
        {"beacon_id", "manufacturer_status", "ach_trace_number"},
    )
    assert {breach.field for breach in found} == {
        None,  # the REBATE_BATCH kind itself
        "beacon_id",
        "manufacturer_status",
        "ach_trace_number",
    }


def test_asserted_fields_drops_empties_but_keeps_false_and_zero():
    """The filter that keeps the existing 340B feed legal.

    ``ingest/adapters.py`` builds one canonical dict for all four 340B event types, so a
    ``MANUFACTURER_DECISION`` carries an empty ``qualification_status`` key.  A key with no
    value asserts nothing.  A key with ``False`` or ``0`` asserts something a system
    committed to, and dropping it would let a source say "no" in a field it does not own.
    """
    assert authority.asserted_fields(
        {"a": None, "b": "", "c": "QUALIFIED", "d": False, "e": 0}
    ) == frozenset({"c", "d", "e"})


# ═══ 2. the table against the document ══════════════════════════════════════


def test_every_doc2_004_row_appears_in_the_table(doc2_004_rows):
    """The line-by-line diff, performed mechanically rather than trusted.

    A row silently dropped from the transcription is an authority nobody enforces and
    nobody notices, which is the failure mode this whole module exists to remove.
    """
    transcribed = {
        (domain.doc2_system, domain.doc2_clause) for domain in DOC2_004_AUTHORITY
    }
    from_document = set(doc2_004_rows.items())

    missing = sorted(from_document - transcribed)
    assert not missing, f"DOC2-004 rows absent from the authority table: {missing}"

    invented = sorted(transcribed - from_document)
    assert not invented, (
        f"authority rows that are not in DOC2-004: {invented}; the table may not assert a "
        "boundary the document does not draw"
    )


def test_every_authority_row_cites_an_evidence_id_that_exists(evidence_ids):
    """The same join ``tests/test_vendor_evidence.py`` makes for vendor field mappings.

    An authority assignment citing an id the index does not hold is an invention wearing a
    citation — and an invented authority rule is worse than none, because it will be
    defended in a review by pointing at the citation.
    """
    offenders: list[str] = []
    for domain in DOC2_004_AUTHORITY:
        if not domain.evidence:
            offenders.append(f"{domain.domain} cites no evidence at all")
            continue
        for identifier in sorted(set(domain.evidence) - evidence_ids):
            offenders.append(
                f"{domain.domain} cites {identifier}, which is not in index.jsonl"
            )
    assert not offenders, offenders


def test_every_authority_row_is_anchored_on_doc2_004(evidence_ids):
    """``DOC2-004`` is the only per-field authority statement in the evidence directory.

    A row supported only by corroboration has drifted from being a transcription into being
    a design opinion, and the two need different review.
    """
    offenders = [
        domain.domain for domain in DOC2_004_AUTHORITY if "DOC2-004" not in domain.evidence
    ]
    assert not offenders, (
        f"authority rows not anchored on the boundary table itself: {offenders}"
    )


def test_no_authority_row_cites_a_source_that_was_never_retrieved(unavailable_ids):
    """A 403 cannot support an authority assignment, however real the URL is.

    The mirror of ``test_vendor_evidence.py::test_no_spec_tagged_field_cites_an_unavailable_source``.
    """
    offenders: list[str] = []
    for domain in DOC2_004_AUTHORITY:
        for identifier in sorted(set(domain.evidence) & unavailable_ids):
            offenders.append(
                f"{domain.domain} cites {identifier}, whose source was never retrieved"
            )
    assert not offenders, offenders


def test_every_authority_row_explains_its_mapping():
    """Transcribing the row is half the work; mapping it onto our columns is the other half.

    ``DOC2-004`` says "rebate status"; it does not say ``manufacturer_status``.  That step is
    a judgement, and a judgement with no stated reason cannot be challenged — only accepted
    or reverted.
    """
    offenders = [
        domain.domain
        for domain in DOC2_004_AUTHORITY
        if len(domain.rationale.strip()) < 40 or not domain.subject.strip()
    ]
    assert not offenders, f"authority rows with no usable rationale: {offenders}"


# ═══ 3. no source system or record kind falls through ═══════════════════════


def test_every_source_system_is_either_authoritative_or_declared_out_of_scope():
    """A source that silently falls through the table is an authority hole.

    This is the test that makes adding one *impossible by accident*: a new ``SourceSystem``
    member is red here until somebody decides, in writing, whether it owns anything.
    """
    authoritative = {
        source for domain in DOC2_004_AUTHORITY for source in domain.authoritative
    }
    declared = set(SOURCE_SYSTEMS_WITHOUT_DOC2_AUTHORITY)

    unaccounted = sorted(s.value for s in set(SourceSystem) - authoritative - declared)
    assert not unaccounted, (
        f"source systems the authority table never mentions: {unaccounted}; add them to a "
        "DOC2-004 row or to SOURCE_SYSTEMS_WITHOUT_DOC2_AUTHORITY with a reason"
    )

    both = sorted(s.value for s in authoritative & declared)
    assert not both, (
        f"source systems both authoritative and declared out of scope: {both}; one of the "
        "two statements is wrong"
    )

    stale = sorted(s for s in declared if s not in set(SourceSystem))
    assert not stale, f"declared-out-of-scope entries that are not SourceSystem members: {stale}"


def test_every_out_of_scope_source_system_says_why():
    offenders = [
        source.value
        for source, reason in SOURCE_SYSTEMS_WITHOUT_DOC2_AUTHORITY.items()
        if len(reason.strip()) < 40
    ]
    assert not offenders, (
        f"source systems declared out of scope with no usable reason: {offenders}"
    )


def test_every_record_kind_is_either_governed_or_declared_out_of_scope():
    governed = {kind for domain in DOC2_004_AUTHORITY for kind in domain.record_kinds}
    declared = set(RECORD_KINDS_WITHOUT_DOC2_AUTHORITY)

    unaccounted = sorted(k.value for k in set(RecordKind) - governed - declared)
    assert not unaccounted, (
        f"record kinds the authority table never mentions: {unaccounted}; add them to a "
        "DOC2-004 row or to RECORD_KINDS_WITHOUT_DOC2_AUTHORITY with a reason"
    )

    both = sorted(k.value for k in governed & declared)
    assert not both, f"record kinds both governed and declared out of scope: {both}"


def test_every_out_of_scope_record_kind_says_why():
    offenders = [
        kind.value
        for kind, reason in RECORD_KINDS_WITHOUT_DOC2_AUTHORITY.items()
        if len(reason.strip()) < 20
    ]
    assert not offenders, f"record kinds declared out of scope with no usable reason: {offenders}"


def test_no_field_and_no_record_kind_is_claimed_by_two_domains():
    """Two owners for one fact is no owner: enforcement would depend on iteration order.

    The module raises at import if this is violated; the test states it as a property so the
    failure reads as a rule rather than as a broken module.
    """
    fields: dict[str, str] = {}
    kinds: dict[RecordKind, str] = {}
    for domain in DOC2_004_AUTHORITY:
        for name in domain.fields:
            assert name not in fields, (
                f"{name!r} is claimed by {fields[name]} and {domain.domain}"
            )
            fields[name] = domain.domain
        for kind in domain.record_kinds:
            assert kind not in kinds, (
                f"{kind.value} is claimed by {kinds[kind]} and {domain.domain}"
            )
            kinds[kind] = domain.domain


def test_the_five_requirement_e5_subjects_are_all_governed():
    """E5 names five things by hand; each must resolve to a domain.

    340B qualification, rebate/validation status, the rebate submission identifier, settled
    cash and the consolidated picture.  Naming them here means a refactor that renames a
    field out of the table is caught by the requirement, not only by the transcription.
    """
    assert authority.authority_for_field("qualification_status").domain == (
        "QUALIFICATION_AND_SOURCE_CONTEXT"
    )
    assert authority.authority_for_field("manufacturer_status").domain == (
        "REBATE_STATUS_AND_VALIDATION"
    )
    assert authority.authority_for_field("beacon_id").domain == "REBATE_SUBMISSION_IDENTIFIER"
    assert authority.authority_for_field("ach_trace_number").domain == "SETTLED_CASH"
    assert authority.authority_for_field("disposition").domain == (
        "CONSOLIDATED_FINANCIAL_STATUS"
    )
    assert authority.authority_for_record_kind(RecordKind.BANK_TRANSACTION).domain == (
        "SETTLED_CASH"
    )
    assert authority.authority_for_field("status_code") is None, (
        "the generic status_code column is deliberately ungoverned; its meaning is whatever "
        "the record kind says, and governing the container rather than the fact would refuse "
        "almost every record in the system"
    )
    assert authority.authority_for_field("trn02") is None, (
        "TRN02 is minted by the payer in the 835 and echoed by the bank, so giving it to the "
        "bank's row would forbid every remittance"
    )


# ═══ 4. the table against the data that already exists ══════════════════════


def test_no_record_the_pipeline_writes_breaches_the_authority_table(ingested):
    """The constraint that decides whether this table is usable at all.

    The six feeds are byte-identical by construction and the suite around them is large; a
    rule that forbids what they already produce would not be strict, it would be a
    regression wearing a requirement number.  So every row the real pipeline writes is run
    through the real check.

    It passes, and the reason is worth recording: the generators already drew Doc 2's
    boundary.  ``generators/tpa.py`` flips the source system from ``TPA_PORTAL`` to
    ``MANUFACTURER_REBATE`` at the manufacturer's decision — *"this is no longer the TPA's
    own system of record speaking, it is the manufacturer's"* — which is DOC2-004's TPA row
    and DOC2-004's Beacon row, separated, in data that predates the connector layer.  E5
    enforces a line the build had already drawn and never checked.
    """
    rows = ingested.execute(
        """
        SELECT source_system, record_kind, canonical, ach_trace_number, beacon_id
          FROM normalized_record
        """
    ).fetchall()
    assert rows, "the demo profile produced no normalized records; the fixture is broken"

    offenders: list[str] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        source = SourceSystem(row["source_system"])
        kind = RecordKind(row["record_kind"])
        seen.add((source.value, kind.value))

        fields = set(authority.asserted_fields(json.loads(row["canonical"])))
        # The two governed names that live as columns rather than as canonical keys.
        for column in ("ach_trace_number", "beacon_id"):
            if row[column]:
                fields.add(column)

        for breach in authority.breaches(source, kind, fields):
            offenders.append(breach.describe())

    assert not offenders, (
        "the authority table refuses records the existing feeds already produce:\n"
        + "\n".join(sorted(set(offenders)))
    )

    # Guard against the fixture silently degrading into a handful of rows, which would make
    # the assertion above pass for the wrong reason.  Seven source systems are not expected
    # here: the demo feeds carry six, and BEACON / TPA_VERITY / TPA_CRANEWARE arrive only
    # once the vendor connectors are wired to the pipeline.
    assert len(seen) >= 15, (
        f"only {len(seen)} (source, kind) combinations were exercised: {sorted(seen)}"
    )
