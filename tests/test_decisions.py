"""Tests that guard specific ratified decisions.

Each test here exists because a decision is cheap to violate silently and expensive to
discover later.  They are named after the decision rather than after the function, so a
failure says *which ruling* broke.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from recon.crosswalk import keys
from recon.db import migrate
from recon.domain import verdicts
from recon.domain.enums import Disposition, KeyType
from recon.engine import verdicts as engine_verdicts


# ═══ A23 — no identifier normalisation on the primary match path ════════════


def test_a23_no_normalized_rx_column_anywhere():
    """A normalized twin of ``rx_number`` would delete the D-6 exception.

    The generators inject identifier drift on purpose; it is *meant* to miss, because
    the miss is the crosswalk-failure exception.  A column holding a zero-stripped Rx
    number would silently resolve it.
    """
    schema = migrate.schema_sql()
    assert "rx_number_as_given" not in schema
    assert "rx_number_normalized" not in schema

    from recon.db import repository

    assert "rx_number_as_given" not in repository._NORM_COLUMNS

    from recon.domain.models import NormalizedRecord

    assert not hasattr(NormalizedRecord, "rx_number_as_given")


def test_a23_key_builders_do_not_normalise():
    """Drift must survive key construction and produce a genuine miss."""
    _, bare = keys.ncpdp_claim("1234567893", "7845102", "00", "2026-03-02")
    _, padded = keys.ncpdp_claim("1234567893", "07845102", "00", "2026-03-02")
    assert bare != padded, (
        "'7845102' and '07845102' collapsed onto one key. That is the injected "
        "identifier-drift defect being normalised away, which deletes D-6."
    )
    assert "07845102" in padded


def test_a23_clp01_parse_preserves_leading_zeros():
    rx, fill = keys.parse_clp01_pharmacy("07845102FILL00")
    assert rx == "07845102"
    assert fill == "00"


@pytest.mark.parametrize(
    "rx,fill", [("7845102", "00"), ("07845102", "01"), ("123", "12")]
)
def test_clp01_round_trips(rx: str, fill: str):
    """The generator's write side and the connector's read side cannot disagree."""
    assert keys.parse_clp01_pharmacy(keys.format_clp01_pharmacy(rx, fill)) == (rx, fill)


def test_clp01_without_marker_is_refused():
    """An unparseable CLP01 must park, never fall through to a partial key."""
    with pytest.raises(keys.MalformedKeyComponentError, match="FILL"):
        keys.parse_clp01_pharmacy("7845102")


# ═══ A24 — exactly eight key types ══════════════════════════════════════════

A24_KEY_TYPES = frozenset(
    {
        "NCPDP_CLAIM",
        "MEDICAL_CLM01",
        "PAYER_ICN",
        "TRN02",
        "ALLOCATION_CODE",
        "NATURAL_340B_PHARMACY",
        "NATURAL_340B_MEDICAL",
        "PBM_AUTH",
    }
)


#: Key types the connector layer added (requirement §4.7 of
#: ``docs/connectivity_layer_requirements.md``).
#:
#: **This is the only pre-existing test in this file that the connectivity build edited,
#: and the edit is deliberately an addition rather than a relaxation.**  The original
#: assertion was set equality against ``A24_KEY_TYPES``, which no new key type can satisfy;
#: the requirement explicitly adds four.  Rather than widening the constant and losing the
#: Decision A24 pin, the two sets are now asserted separately — so the eight are still
#: pinned exactly, the four are pinned exactly, and adding a ninth "A24" key type or a fifth
#: connector key type both still fail.
#:
#: What was checked before is still checked. What is checked now is strictly more.
CONNECTOR_KEY_TYPES = frozenset(
    {"BEACON_ID", "COVERED_ENTITY_340B", "HCPCS", "PAYMENT_REFERENCE"}
)


def test_a24_enum_still_has_exactly_the_eight_named_key_types():
    """The eight are a ratified decision and none of them moved.

    Asserted as a subset-plus-difference rather than as equality, because equality would
    silently become a test of the connector layer too — and then a mistake in one would read
    as a mistake in the other.
    """
    values = {member.value for member in KeyType}
    assert A24_KEY_TYPES <= values, f"a Decision A24 key type vanished: {A24_KEY_TYPES - values}"
    assert values - A24_KEY_TYPES == CONNECTOR_KEY_TYPES, (
        "a key type was added or removed without being declared: "
        f"{(values - A24_KEY_TYPES) ^ CONNECTOR_KEY_TYPES}"
    )


def test_the_connector_layer_added_exactly_four_key_types():
    """Requirement §4.7, pinned the same way Decision A24 is.

    ``BEACON_ID`` is C5; the other three are E1, E2 and E3. They were declared together so
    that the schema's two ``key_type`` CHECK lists move exactly once rather than twice.
    """
    values = {member.value for member in KeyType}
    assert CONNECTOR_KEY_TYPES <= values
    assert not (CONNECTOR_KEY_TYPES & A24_KEY_TYPES), (
        "a connector key type collides with a Decision A24 one, which would make the two "
        "pins above agree for the wrong reason"
    )


def test_a24_schema_check_constraints_match_the_enum():
    """Two CHECK lists mention key types; both must equal the enum, exactly.

    A drifted CHECK means an insert the Python layer believes is valid gets rejected by
    SQLite halfway through an ingest — or worse, a key type the enum dropped is still
    insertable.
    """
    schema = migrate.schema_sql()
    blocks = re.findall(r"key_type\s+TEXT\s+NOT NULL\s+CHECK\s*\(\s*key_type IN \(([^)]*)\)", schema)
    assert len(blocks) == 2, (
        f"expected key_type CHECK constraints on crosswalk_key and parked_record_key, "
        f"found {len(blocks)}"
    )
    for block in blocks:
        listed = set(re.findall(r"'([A-Z0-9_]+)'", block))
        assert listed == A24_KEY_TYPES | CONNECTOR_KEY_TYPES, (
            "a CHECK list drifted from the enum. The union is asserted rather than the "
            "enum's own membership, so a key type present in Python and absent from SQL "
            "still fails — which is the direction that breaks an ingest halfway through."
        )


def test_a24_ach_trace_is_not_a_crosswalk_key():
    """Banking plumbing with no business content resolves to nothing on its own.

    It stays a column on the bank record for D-1 detection.  Promoting it to a key type
    would imply a bank line can be tied to a claim without the two-hop route, which is
    the error A21 exists to forbid.
    """
    assert "ACH_TRACE" not in {member.value for member in KeyType}
    assert "ach_trace_number" in migrate.schema_sql()


def test_a24_both_hot_lookups_are_indexed_on_key_type_key_value():
    """Both pools are point seeks, never scans."""
    schema = migrate.schema_sql()
    assert "ix_crosswalk_lookup" in schema
    assert re.search(
        r"ix_crosswalk_lookup\s*\n?\s*ON crosswalk_key\(key_type, key_value", schema
    ), "the crosswalk lookup index must lead with (key_type, key_value)"
    assert re.search(
        r"ix_parked_key_lookup ON parked_record_key\(key_type, key_value\)", schema
    ), "the parked backward re-check must be indexed, or it is a full scan per arrival"


# ═══ A4 / A5 / Decision 19 — aging is never stored ══════════════════════════

_AGING_NAMES = re.compile(
    r"\b(sla|deadline|due_by|due_date|age_days|days_open|overdue|breach|threshold)\b",
    re.IGNORECASE,
)


def test_aging_is_never_a_stored_column():
    """Aging is ``cursor - date_of_service``, computed at read time.

    A stored age column would mean an untouched episode changes disposition as the clock
    moves, which breaks the property that makes event-driven processing provably
    complete (A4).
    """
    schema = migrate.schema_sql()
    # Strip comments: the schema *discusses* these words deliberately.
    sql_only = "\n".join(
        line.split("--")[0] for line in schema.splitlines()
    )
    offenders = sorted(set(_AGING_NAMES.findall(sql_only)))
    assert not offenders, f"schema declares aging/SLA identifiers: {offenders}"


def test_age_days_exists_only_as_a_read_time_projection():
    """``age_days`` is computed in SQL, on the read, from the cursor.

    It lives on ``QueueRow`` — a projection — and nowhere else, which is what keeps it a
    sort key rather than a verdict input.
    """
    import inspect

    from recon.db import repository
    from recon.domain.models import QueueRow

    assert "age_days" in QueueRow.__dataclass_fields__
    assert "julianday" in inspect.getsource(repository._queue), (
        "age_days must be derived in the query; a stored column would let an untouched "
        "episode change disposition as the clock moves"
    )


# ═══ A8 / A25 — append-only, and no update/delete surface ═══════════════════


def test_repository_exposes_no_update_or_delete():
    from recon.db import repository

    forbidden = [
        name
        for name in repository.__all__
        if name.startswith(("update_", "delete_", "remove_", "overwrite_"))
    ]
    assert not forbidden, f"mutating repository functions: {forbidden}"


@pytest.mark.parametrize(
    "table",
    ["raw_record", "normalized_record", "episode", "verdict", "verdict_reason", "work_item"],
)
def test_immutability_triggers_exist(table: str):
    schema = migrate.schema_sql()
    assert f"BEFORE UPDATE ON {table}" in schema
    assert f"BEFORE DELETE ON {table}" in schema


def test_immutability_triggers_actually_fire(conn):
    """A trigger that exists in the file but not in the database is decoration."""
    import sqlite3

    conn.execute(
        "INSERT INTO ingest_batch(source_file, source_system, file_sha256, record_count, loaded_at)"
        " VALUES ('f.jsonl','BANK','sha-1',1,'2026-01-01T00:00:00Z')"
    )
    conn.execute(
        "INSERT INTO raw_record(batch_id, source_system, source_record_id, source_line_no,"
        " payload, payload_sha256, received_at)"
        " VALUES (1,'BANK','R-1',1,'{}','p-1','2026-01-01T00:00:00Z')"
    )
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        conn.execute("UPDATE raw_record SET payload = '{\"x\":1}' WHERE raw_id = 1")
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        conn.execute("DELETE FROM raw_record WHERE raw_id = 1")


# ═══ E1 — work_item: the agent's only write target has to be append-only too ═══
#
# Reviewer finding 6: work_item was the one mutable-looking table in this file with
# no append-only triggers, while the tool description the model reads
# (``src/recon/agents/tools.py:1386-1387``) asserts "the table it writes to has no
# update and no delete". These tests reproduce the gap directly against the schema,
# the same way ``test_immutability_triggers_actually_fire`` does for ``raw_record``.


def _insert_work_item_lineage(conn) -> None:
    """Minimal episode + verdict lineage so a work_item row can be inserted.

    Mirrors the chain ``test_xor_is_a_check_not_a_convention`` already builds above;
    a verdict row is layered on top because ``work_item.from_verdict_id`` requires one.
    """
    conn.executescript(
        "INSERT INTO ingest_batch(source_file, source_system, file_sha256, record_count, loaded_at)"
        " VALUES ('f.jsonl','PBM_ADJUDICATION','sha-w',1,'2026-01-01T00:00:00Z');"
        "INSERT INTO raw_record(batch_id, source_system, source_record_id, source_line_no,"
        " payload, payload_sha256, received_at)"
        " VALUES (1,'PBM_ADJUDICATION','R-1',1,'{}','p-w','2026-01-01T00:00:00Z');"
        "INSERT INTO normalized_record(raw_id, record_kind, source_system, adapter_version,"
        " received_at, idempotency_key, canonical)"
        " VALUES (1,'PHARMACY_CLAIM','PBM_ADJUDICATION','1.0.0','2026-01-01T00:00:00Z','k-w','{}');"
        "INSERT INTO episode(episode_id, reimbursement_track, anchor_norm_id, ndc11,"
        " date_of_service, quantity_milli, pharmacy_npi, rx_number, fill_number, clm01,"
        " billing_provider_npi, created_from_received_at)"
        " VALUES ('EP-W1','PHARMACY',1,'00071015523','2026-01-01',30000,'1234567893',"
        "'7845102','00',NULL,NULL,'2026-01-01T00:00:00Z');"
        "INSERT INTO verdict(episode_id, cursor_at, computed_at, engine_version,"
        " reference_fingerprint, episode_disposition, reimbursement_disposition,"
        " rebate_disposition, reimbursement_verdict_code, rebate_verdict_code)"
        " VALUES ('EP-W1','2026-01-01T23:59:59Z','2026-01-01T00:00:00Z','1.0.0','fp-1',"
        "'PENDING','PENDING',NULL,'A-01','C-00');"
        "INSERT INTO work_item(episode_id, created_at, created_by, at_cursor,"
        " from_verdict_id, summary, recommended_action)"
        " VALUES ('EP-W1','2026-01-01T00:00:00Z','agent:test@model','2026-01-01T23:59:59Z',"
        " 1,'a summary long enough to satisfy the twenty-char minimum','ABSTAIN');"
    )


def test_work_item_immutability_triggers_actually_fire(conn):
    """``work_item`` must be append-only in the database, not only in the tool's prose."""
    import sqlite3

    _insert_work_item_lineage(conn)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("UPDATE work_item SET summary = 'changed after the fact' WHERE work_item_id = 1")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("DELETE FROM work_item WHERE work_item_id = 1")


def test_work_item_idempotency_is_a_unique_index_not_a_python_race(conn):
    """``ux_work_item_idempotent`` backs the tool's idempotency promise with a constraint.

    Before this index existed, "calling it twice with the same episode, verdict and
    action does not create a second item" held only because
    ``create_mock_work_item`` happened to SELECT before it INSERTed -- a check-then-act
    race with nothing behind it, which also meant the tool's own
    ``except sqlite3.IntegrityError`` recovery branch was unreachable dead code
    (reviewer finding 6).
    """
    import sqlite3

    _insert_work_item_lineage(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO work_item(episode_id, created_at, created_by, at_cursor,"
            " from_verdict_id, summary, recommended_action)"
            " VALUES ('EP-W1','2026-01-01T00:00:01Z','agent:test@model',"
            " '2026-01-01T23:59:59Z', 1,"
            " 'a second summary long enough to satisfy the minimum','ABSTAIN')"
        )


# ═══ A9 / A22 — three dispositions, reopened is a flag ══════════════════════


def test_exactly_three_dispositions():
    assert [d.value for d in Disposition] == ["CLOSED", "PENDING", "EXCEPTION"]


def test_reopened_is_a_flag_not_a_disposition():
    assert "REOPENED" not in {d.value for d in Disposition}
    schema = migrate.schema_sql()
    assert "reopened_from" in schema
    # Reopened-from-EXCEPTION is meaningless: an exception was never closed.
    assert "reopened_from IN ('CLOSED','PENDING')" in schema


# ═══ C17 / Decision 48 — pairs.json is a live oracle ════════════════════════


def test_verdict_vocabulary_matches_the_oracle(pairs_oracle):
    """31 x 12 = 372, and every code in the oracle is a code we declare."""
    oracle_reimb = sorted({row["reimbursement"] for row in pairs_oracle})
    oracle_rebate = sorted({row["rebate"] for row in pairs_oracle})

    assert oracle_reimb == sorted(verdicts.REIMBURSEMENT_CODES)
    assert oracle_rebate == sorted(verdicts.REBATE_CODES)
    assert len(verdicts.REIMBURSEMENT_CODES) == 31
    assert len(verdicts.REBATE_CODES) == 12


def test_all_372_pairs_are_reachable(pairs_oracle):
    """Exactness is the result that makes the two tracks independent.

    If the product were not exact, cross-track rules would be prohibitions and the
    engine would need a validity table.  It is exact, so they are annotations.
    """
    oracle_pairs = {(row["reimbursement"], row["rebate"]) for row in pairs_oracle}
    assert len(oracle_pairs) == 372
    assert oracle_pairs == set(verdicts.REACHABLE_PAIRS)


def test_retired_codes_never_reappear(pairs_oracle):
    oracle_codes = {row["reimbursement"] for row in pairs_oracle} | {
        row["rebate"] for row in pairs_oracle
    }
    assert not (oracle_codes & verdicts.RETIRED_CODES)
    live = set(verdicts.REIMBURSEMENT_CODES) | set(verdicts.REBATE_CODES)
    assert not (live & verdicts.RETIRED_CODES)


def test_configuration_density_spans_far_enough_to_force_stratified_sampling(pairs_oracle):
    """Why sampling stratifies over verdicts, not configurations (Decision 15).

    Uniform sampling over configurations would floor the rarest verdicts entirely, so
    this asserts the skew that makes that true rather than trusting the prose.
    """
    counts = [row["configurations"] for row in pairs_oracle]
    singletons = [c for c in counts if c == 1]
    assert max(counts) / min(counts) > 100
    assert len(singletons) >= 90, (
        f"only {len(singletons)} pairs have a single configuration; the design note "
        "claims 99, and stratification is justified by that number"
    )


# ═══ recon.engine.verdicts — full port fidelity against the oracle ═════════


def _load_oracle_configurations() -> list[dict]:
    """``decision_tree/leaves_classified.json`` — all 4,224 valid configurations.

    Not copied into ``tests/``: the real artefact the exhaustive generator wrote, and
    the same file the module docstring of ``recon.engine.verdicts`` names by path. Each
    entry carries the oracle's own recorded verdict fields (``curated_reimbursement_state``,
    ``curated_rebate_state``, ``cross_track_flags``, ``coherence``) alongside the
    configuration split across ``reimbursement_track`` / ``rebate_track`` /
    ``cash_verification``. The engine's functions take one flat configuration mapping
    (see ``verify.py``'s own ``matches()`` helper for the oracle's precedent), so the three
    parts are merged here the same way.
    """
    import json

    repo_root = Path(__file__).resolve().parents[1]
    path = repo_root / "decision_tree" / "leaves_classified.json"
    assert path.exists(), (
        f"{path} is missing. It is a live test oracle, not an optional artefact; "
        "rebuild it with `python decision_tree/classify.py`."
    )
    leaves = json.loads(path.read_text(encoding="utf-8"))
    for leaf in leaves:
        configuration = {}
        configuration.update(leaf["reimbursement_track"])
        configuration.update(leaf["rebate_track"])
        configuration.update(leaf["cash_verification"])
        leaf["_configuration"] = configuration
    return leaves


def test_engine_verdicts_reproduces_all_4224_oracle_classifications():
    """The module docstring's claim, made true: a line-for-line port must match, exactly.

    ``recon.engine.verdicts`` is asserted to be a *faithful* port of
    ``decision_tree/classify.py`` — the classifier the exhaustive generator ran to prove
    372 of 372 verdict pairs reachable. If the port diverges from the oracle on even one
    of the 4,224 configurations, it is no longer the thing that was proved, so this is a
    100% match with no threshold: one divergence is a failure.

    The oracle's cross-track flags carry a descriptive suffix (``"X-1:denied_with_rebate_paid"``)
    that the port's ``cross_track_flags`` does not reproduce — the port only returns the bare
    code (``"X-1"``). That is a recorded, intentional narrowing of the port (see its
    docstring: "50 rules... generate all 372 pairs"), not a divergence to catch here, so both
    sides are compared on the code prefix only.
    """
    leaves = _load_oracle_configurations()
    assert len(leaves) == 4224, f"expected 4,224 oracle configurations, found {len(leaves)}"

    divergences: list[dict] = []

    for leaf in leaves:
        configuration = leaf["_configuration"]

        got_reimbursement = engine_verdicts.classify_reimbursement(configuration)
        got_rebate = engine_verdicts.classify_rebate(configuration)
        got_flags = sorted(flag.split(":", 1)[0] for flag in engine_verdicts.cross_track_flags(configuration))
        got_coherence = engine_verdicts.coherence(configuration)

        want_reimbursement = leaf["curated_reimbursement_state"]
        want_rebate = leaf["curated_rebate_state"]
        want_flags = sorted(flag.split(":", 1)[0] for flag in leaf["cross_track_flags"])
        want_coherence = leaf["coherence"]

        mismatches = {}
        if got_reimbursement != want_reimbursement:
            mismatches["reimbursement"] = (want_reimbursement, got_reimbursement)
        if got_rebate != want_rebate:
            mismatches["rebate"] = (want_rebate, got_rebate)
        if got_flags != want_flags:
            mismatches["cross_track_flags"] = (want_flags, got_flags)
        if got_coherence != want_coherence:
            mismatches["coherence"] = (want_coherence, got_coherence)

        if mismatches:
            divergences.append(
                {
                    "case_id": leaf["case_id"],
                    "configuration": configuration,
                    "mismatches": mismatches,
                }
            )

    if divergences:
        preview = "\n".join(
            f"  {d['case_id']}: {d['mismatches']}\n    configuration={d['configuration']}"
            for d in divergences[:5]
        )
        pytest.fail(
            f"recon.engine.verdicts diverged from the oracle on {len(divergences)} of "
            f"{len(leaves)} configurations. First {min(5, len(divergences))}:\n{preview}"
        )


# ═══ C6 — integer cents end to end ══════════════════════════════════════════


def test_no_real_columns_in_the_schema():
    """A float in an amount column produces a residue indistinguishable from variance."""
    schema = migrate.schema_sql()
    sql_only = "\n".join(line.split("--")[0] for line in schema.splitlines())
    assert not re.search(r"\bREAL\b", sql_only, re.IGNORECASE)
    assert not re.search(r"\bFLOAT\b", sql_only, re.IGNORECASE)


def test_money_refuses_float():
    from recon.money import to_cents

    with pytest.raises(TypeError, match="float"):
        to_cents(2840.00)


def test_every_money_column_is_integer_cents():
    schema = migrate.schema_sql()
    money_columns = re.findall(r"^\s*(\w*(?:cents|amount)\w*)\s+(\w+)", schema, re.MULTILINE)
    assert money_columns, "no money columns found — the regex is wrong, not the schema"
    for name, declared_type in money_columns:
        assert declared_type.upper() == "INTEGER", f"{name} is {declared_type}, not INTEGER cents"


# ═══ C4 — seeding is blake2b and order-independent ══════════════════════════


def test_seed_derivation_is_pinned():
    """The canonical string is a published format; changing it invalidates manifests."""
    from recon.rng import canonical_seed_string, derive_seed

    assert canonical_seed_string(20250701, "full", "pbm", 7) == "20250701|full/pbm/7"
    # Pinned so a "tidy-up" of the separator is a failing test, not a silent change.
    assert derive_seed(20250701, "full", "pbm", 7) == derive_seed(20250701, "full", "pbm", 7)


def test_seeds_are_order_and_insertion_independent():
    from recon.rng import rng_for

    first = rng_for(20250701, "full", "pbm", 3).random()
    # Drain an unrelated stream; the first stream must be unaffected.
    for _ in range(100):
        rng_for(20250701, "full", "bank", 99).random()
    assert rng_for(20250701, "full", "pbm", 3).random() == first


def test_builtin_hash_is_not_used_for_seeding():
    """``hash()`` is salted per process by PYTHONHASHSEED and would not reproduce.

    Checked against the AST rather than the text, because the module docstring discusses
    ``hash()`` at length — deliberately, since the reason it is banned is the
    interesting part.
    """
    import ast

    source = Path(__import__("recon.rng", fromlist=["__file__"]).__file__).read_text(
        encoding="utf-8"
    )
    assert "blake2b" in source
    calls = [
        node.func.id
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    ]
    assert "hash" not in calls, "rng.py calls builtin hash(); seeds would not reproduce"


# ═══ C10 — generated names are fictional ════════════════════════════════════

#: Real organisations that must never appear in generated data.  Prose may mention them
#: as domain context; records may not.
REAL_ORG_NAMES = (
    "CAREMARK", "EXPRESS SCRIPTS", "OPTUMRX", "ANTHEM", "AETNA", "CIGNA", "HUMANA",
    "UNITEDHEALTH", "BLUE CROSS", "LILLY", "PFIZER", "SANOFI", "NOVARTIS", "ASTRAZENECA",
    "JOHNSON & JOHNSON", "MERCK", "ABBVIE", "GENENTECH", "AMGEN", "NOVO NORDISK",
)


def test_reference_entity_names_are_fictional():
    from recon.reference import entities

    generated_names = (
        [p.name for p in entities.pharmacies()]
        + [entities.billing_provider().name]
        + [p.name for p in entities.prescribers()]
        + [p.name for p in entities.pbms()]
        + [p.name for p in entities.medical_payers()]
        + [c.name for c in entities.covered_entities()]
        + [m.name for m in entities.manufacturers()]
    )
    for name in generated_names:
        upper = name.upper()
        for real in REAL_ORG_NAMES:
            assert real not in upper, f"generated entity name {name!r} contains {real!r}"


def test_reference_data_is_internally_valid():
    import recon.reference as reference

    reference.validate()


# ═══ Decision 18 — the generator has no concept of "now" ════════════════════


def test_config_reads_no_wall_clock():
    """A generator that reads the clock cannot produce a reproducible dataset."""
    import ast

    source = Path(__import__("recon.config", fromlist=["__file__"]).__file__).read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    banned = {"now", "today", "utcnow", "time", "monotonic"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in banned:
            pytest.fail(f"config.py reads the clock via .{node.attr}()")


def test_cursor_round_trips():
    from recon.config import cursor_from_date, cursor_to_date
    from datetime import date

    day = date(2026, 3, 17)
    assert cursor_from_date(day) == "2026-03-17T23:59:59Z"
    assert cursor_to_date(cursor_from_date(day)) == day


def test_cursor_is_lexicographically_chronological():
    """The cursor filter is a plain string comparison; that only works if this holds."""
    from datetime import date, timedelta

    from recon.config import cursor_from_date

    days = [date(2025, 12, 30) + timedelta(days=n) for n in range(10)]
    cursors = [cursor_from_date(d) for d in days]
    assert cursors == sorted(cursors)


# ═══ A25 — records live outside the episode ═════════════════════════════════


def test_records_are_linked_by_pointer_not_embedded():
    """One 835 covers 47 claims and cannot sit inside any one of them.

    Which is also why lineage is a link table rather than a field.
    """
    schema = migrate.schema_sql()
    assert "CREATE TABLE verdict_evidence" in schema
    assert "norm_id    INTEGER NOT NULL REFERENCES normalized_record(norm_id)" in schema
    # The episode table must not carry a payload or a record list.
    episode_block = schema.split("CREATE TABLE episode")[1].split("CREATE TABLE")[0]
    assert "payload" not in episode_block
    assert "records" not in episode_block


def test_xor_is_a_check_not_a_convention(conn):
    """Duplicate billing must be unrepresentable, not merely discouraged."""
    import sqlite3

    conn.executescript(
        "INSERT INTO ingest_batch(source_file, source_system, file_sha256, record_count, loaded_at)"
        " VALUES ('f.jsonl','PBM_ADJUDICATION','sha-x',1,'2026-01-01T00:00:00Z');"
        "INSERT INTO raw_record(batch_id, source_system, source_record_id, source_line_no,"
        " payload, payload_sha256, received_at)"
        " VALUES (1,'PBM_ADJUDICATION','R-1',1,'{}','p-x','2026-01-01T00:00:00Z');"
        "INSERT INTO normalized_record(raw_id, record_kind, source_system, adapter_version,"
        " received_at, idempotency_key, canonical)"
        " VALUES (1,'PHARMACY_CLAIM','PBM_ADJUDICATION','1.0.0','2026-01-01T00:00:00Z','k1','{}');"
    )
    both_tracks = (
        "INSERT INTO episode(episode_id, reimbursement_track, anchor_norm_id, ndc11,"
        " date_of_service, quantity_milli, pharmacy_npi, rx_number, fill_number, clm01,"
        " billing_provider_npi, created_from_received_at)"
        " VALUES ('EP-1','PHARMACY',1,'00071015523','2026-01-01',30000,'1234567893',"
        "'7845102','00','ENC-1','1493387025','2026-01-01T00:00:00Z')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(both_tracks)
