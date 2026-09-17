"""Requirements E1–E5 — business mapping, at the grain the acceptance criteria are worded.

Doc 2 step 4 is one line — *"Business mapping: Crosswalk 340B IDs, NDC/HCPCS, claim/Rx/fill
IDs, provider/site, payer/PBM, manufacturer, transaction and payment references"* — and group
E is the list of things on it that the pre-connector build did not have.  Each of the five has
an acceptance sentence, and this file is those five sentences turned into assertions.

═══ What is tested here and what is tested elsewhere ═══════════════════════════════════

The reference tables and the authority table already have their own suites:
``tests/test_sites.py`` covers E4's site table in isolation — two sites under one NPI, and
``resolve_site`` refusing to choose between them — and ``tests/test_authority.py`` diffs
``DOC2_004_AUTHORITY`` against the document row by row.  Neither is repeated here.  What
neither covers is the **wiring**: whether the pipeline consults those tables at the one moment
it can, and what it does with the answer.  That gap is this file.

═══ Why most of these records have to be constructed ═══════════════════════════════════

Three of the five acceptance criteria describe a record the six generated feeds never
produce — a TPA export naming the wrong covered entity, a 340B record identifying its drug by
J-code alone, a TPA row setting a field Beacon owns.  That is not a hole in the dataset: the
generators draw Doc 2's boundary correctly, so every record they write is *entitled* to what
it says and names the entity it belongs to.  A feed that contained these would be a feed
modelling a vendor behaving badly, and the generators deliberately do not.

So the records are crafted and ingested against an already-built pipeline, which is the only
way to put a hostile record next to a real episode.  Each crafted record is ingested into an
isolated **copy** of the built database (``sqlite3.Connection.backup``), so no test can see
another's writes and the order they run in cannot matter.

The ``full`` profile rather than ``demo``, for one reason: only ``full`` anchors episodes at
the satellite NPI ``1044723199``.  Without those seventeen episodes, E1's unregistered-location
case and E4's unambiguous-site case have no subject at all, and both would pass vacuously.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, Callable, Sequence

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from conftest_pipeline import build_pipeline  # noqa: E402
from recon.connectors import registry  # noqa: E402
from recon.connectors.authority import SourceAuthorityError  # noqa: E402
from recon.connectors.transport import LocalDirectoryTransport  # noqa: E402
from recon.crosswalk import keys  # noqa: E402
from recon.db import connection  # noqa: E402
from recon.domain.enums import (  # noqa: E402
    AllocationBasis,
    KeyType,
    ParkReason,
    QuarantineReason,
    RecordKind,
    SourceSystem,
)
from recon.domain.models import Resolution  # noqa: E402
from recon.ingest import adapters, pipeline  # noqa: E402
from recon.reference import drugs, sites  # noqa: E402

#: The covered entity that registered the main pharmacy NPI, and the decoy that did not.
#: Spelled here rather than read from ``reference.entities`` on purpose: a crafted record is
#: a *vendor's* claim about whose 340B dispense this is, and a vendor does not read our
#: reference tables.  If the reference ids ever move, this file should fail loudly rather
#: than follow them and keep asserting a mismatch that is no longer a mismatch.
OUR_ENTITY = "DSH310074"
DECOY_ENTITY = "PED045210A"

#: The registered main-pharmacy NPI (two contract-pharmacy sites bill under it) and the
#: satellite NPI (one site, registered with neither covered entity of ours).
SHARED_NPI = "1234567893"
SATELLITE_NPI = "1044723199"

#: Later than every ``received_at`` in the generated feeds, so a crafted record arrives after
#: everything it could resolve against.  ``resolve_keys`` filters ``first_seen_at <= cursor``,
#: so a record dated before its episode's keys were established would resolve to nothing and
#: the test would pass for the wrong reason.
LATE = "2026-06-30T00:00:00Z"


# ═══ fixtures and helpers ═══════════════════════════════════════════════════


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    return build_pipeline("full", tmp_path_factory.mktemp("mapping"))


@pytest.fixture()
def fresh(run) -> Callable[[], sqlite3.Connection]:
    """A private copy of the fully-ingested database, per call.

    ``backup`` rather than a second ``build_pipeline``: the crafted-record tests mutate what
    they read, and four independent four-second rebuilds to get four independent databases
    would be paying in wall-clock for something SQLite does in memory.
    """
    handles: list[sqlite3.Connection] = []

    def _copy() -> sqlite3.Connection:
        handle = connection.connect(":memory:")
        run.conn.backup(handle)
        handles.append(handle)
        return handle

    yield _copy
    for handle in handles:
        handle.close()


def _ingest(conn: sqlite3.Connection, payloads: Sequence[dict], tmp_path: Path, tag: str):
    """Land crafted payloads through the real transport seam, then run the real ingest.

    Through ``load_from_sources`` rather than by inserting ``raw_record`` rows directly,
    because the thing under test in three of these requirements is what ``_process_raw_record``
    does — and a test that hand-writes the row ``_process_raw_record`` reads has skipped the
    half of the path where the decision is made.

    Re-running ``ingest`` over the whole table is safe and is not a second pass: every record
    already normalized collapses on its idempotency key and returns before it can attach
    again.
    """
    root = tmp_path / tag
    root.mkdir()
    (root / "crafted.jsonl").write_text(
        "".join(json.dumps(payload) + "\n" for payload in payloads),
        encoding="utf-8",
        newline="",
    )
    source = registry.Source(
        source_id=f"crafted_{tag}",
        vendor="crafted",
        transport_kind=registry.TransportKind.LOCAL_DIRECTORY,
        source_system=SourceSystem.TPA_PORTAL,
        filenames=("crafted.jsonl",),
        endpoint=str(root),
        transport=LocalDirectoryTransport(root),
    )
    pipeline.load_from_sources(conn, [source])
    return pipeline.ingest(conn)


def _tpa_event(record_id: str, **fields: Any) -> dict[str, Any]:
    """One 340B qualification record in the shape ``generators/tpa.py`` writes them."""
    payload: dict[str, Any] = {
        "record_id": record_id,
        "source_system": "TPA_PORTAL",
        "event_type": "QUALIFICATION_DECISION",
        "received_at": LATE,
        "rx_number": None,
        "pharmacy_npi": None,
        "provider_npi": None,
        "ndc_11": None,
        "fill_date": None,
        "covered_entity_id": None,
        "qualification_status": "QUALIFIED",
    }
    payload.update(fields)
    return payload


def _norm_id(conn: sqlite3.Connection, record_id: str) -> int | None:
    row = conn.execute(
        "SELECT norm_id FROM normalized_record WHERE idempotency_key = ?", (record_id,)
    ).fetchone()
    return None if row is None else row["norm_id"]


def _park_reason(conn: sqlite3.Connection, norm_id: int) -> str | None:
    row = conn.execute(
        "SELECT park_reason FROM parked_record WHERE norm_id = ?", (norm_id,)
    ).fetchone()
    return None if row is None else row["park_reason"]


def _attachments(conn: sqlite3.Connection, norm_id: int) -> list[tuple[str, str | None]]:
    """Every crosswalk row this record caused — the observable form of "it attached"."""
    return [
        (row["key_type"], row["episode_id"])
        for row in conn.execute(
            "SELECT key_type, episode_id FROM crosswalk_key"
            " WHERE resolved_from_norm_id = ? ORDER BY key_type, key_value",
            (norm_id,),
        )
    ]


def _episode_with_an_unambiguous_key(
    conn: sqlite3.Connection, where: str, keyfn: Callable[[sqlite3.Row], tuple[KeyType, str]]
) -> sqlite3.Row:
    """The first episode, by id, whose key resolves to it and to nothing else.

    A crafted record has to aim at an episode the crosswalk can reach *uniquely*, or the park
    under test would be ``AMBIGUOUS_KEY_MATCH`` and the assertion would be about the wrong
    thing.  Selected by walking rather than hardcoded, because episode ids are minted by the
    connector and would move under any change to arrival order.
    """
    for episode in conn.execute(f"SELECT * FROM episode WHERE {where} ORDER BY episode_id"):
        key_type, value = keyfn(episode)
        hits = {
            row["episode_id"]
            for row in conn.execute(
                "SELECT episode_id FROM crosswalk_key WHERE key_type = ? AND key_value = ?",
                (str(key_type), value),
            )
            if row["episode_id"]
        }
        if hits == {episode["episode_id"]}:
            return episode
    raise AssertionError(
        f"no episode matching {where!r} publishes a key that resolves only to itself, so a "
        "crafted record has nothing unambiguous to aim at and this test would be vacuous"
    )


def _pharmacy_natural_key(episode: sqlite3.Row) -> tuple[KeyType, str]:
    return keys.natural_340b_pharmacy(
        episode["pharmacy_npi"],
        episode["rx_number"],
        episode["ndc11"],
        episode["date_of_service"],
    )


def _hcpcs_key(episode: sqlite3.Row) -> tuple[KeyType, str]:
    return keys.hcpcs_medical(
        episode["hcpcs"], episode["billing_provider_npi"], episode["date_of_service"]
    )


# ═══ E1 — the 340B covered entity is a real field and a real key ════════════
#
# "episodes carry a covered entity; a TPA record for the wrong covered entity does not
# resolve."


def test_an_episode_carries_the_covered_entity_that_registered_its_npi(run):
    """E1's first half, and the reason the NULLs in that column are answers rather than gaps.

    ``_create_episode`` wrote ``covered_entity_id=None`` unconditionally before E1, so the
    column existed and said nothing.  Asserting only that *some* episode carries an entity
    would be satisfied by a build that populated one row, so both populations are pinned:
    every episode at the registered NPI carries our entity, and every episode at the satellite
    NPI carries none.

    The second half is the one worth deleting this test to break.  A NULL there is the
    unregistered-location case — a real contract pharmacy that our covered entity never
    registered — not a lookup that quietly failed, and ``_covered_entity_for`` is asked
    directly so the claim is checked rather than inferred from the column.
    """
    populated = run.conn.execute(
        "SELECT DISTINCT covered_entity_id FROM episode WHERE pharmacy_npi = ?",
        (SHARED_NPI,),
    ).fetchall()
    assert [row["covered_entity_id"] for row in populated] == [OUR_ENTITY]

    satellite = run.conn.execute(
        "SELECT COUNT(*) AS n, COUNT(covered_entity_id) AS entities FROM episode"
        " WHERE pharmacy_npi = ?",
        (SATELLITE_NPI,),
    ).fetchone()
    assert satellite["n"] > 0, (
        f"no episode is anchored at {SATELLITE_NPI}, so the unregistered-location case has "
        "no subject and the NULL below proves nothing"
    )
    assert satellite["entities"] == 0
    assert pipeline._covered_entity_for(pharmacy_npi=SATELLITE_NPI) is None, (
        "the NULL must be the registration answer — no covered entity registered this NPI — "
        "and not a value that failed to reach the insert"
    )


def test_a_tpa_record_naming_the_wrong_covered_entity_parks_and_never_attaches(
    fresh, tmp_path
):
    """**This is E1's acceptance criterion.**  Nothing in the six feeds produces it.

    The keys resolve cleanly to exactly one episode and the record then names a *different*
    covered entity, so two systems that both claim to know disagree about whose 340B dispense
    this is.  Attaching would credit one covered entity's savings to another and would look
    identical to a correct match in every downstream report — which is why the assertion is
    two-sided: the park reason alone would still pass if the record had been attached first
    and parked afterwards.
    """
    conn = fresh()
    episode = _episode_with_an_unambiguous_key(
        conn, f"pharmacy_npi = '{SHARED_NPI}'", _pharmacy_natural_key
    )
    stats = _ingest(
        conn,
        [
            _tpa_event(
                "CRAFTED-WRONG-ENTITY",
                rx_number=episode["rx_number"],
                pharmacy_npi=episode["pharmacy_npi"],
                ndc_11=episode["ndc11"],
                fill_date=keys.iso_date_to_wire(episode["date_of_service"]),
                covered_entity_id=DECOY_ENTITY,
            )
        ],
        tmp_path,
        "wrong-entity",
    )

    norm_id = _norm_id(conn, "CRAFTED-WRONG-ENTITY")
    assert norm_id is not None, "the record must be normalized and held, not dropped"
    assert _park_reason(conn, norm_id) == str(ParkReason.COVERED_ENTITY_MISMATCH)
    assert _attachments(conn, norm_id) == [], (
        f"the record resolved to {episode['episode_id']} and was attached anyway; a decoy "
        "entity's rebate is now recorded against our covered entity's dispense"
    )
    assert stats.park_reasons[str(ParkReason.COVERED_ENTITY_MISMATCH)] == 1


def test_the_same_record_with_the_right_covered_entity_attaches(fresh, tmp_path):
    """The mirror, without which the test above would pass on a pipeline that parks everything.

    One field differs between the two records.  If both park, the guard is not a guard — it is
    a crosswalk that stopped working — and the difference is invisible from the park table.
    """
    conn = fresh()
    episode = _episode_with_an_unambiguous_key(
        conn, f"pharmacy_npi = '{SHARED_NPI}'", _pharmacy_natural_key
    )
    _ingest(
        conn,
        [
            _tpa_event(
                "CRAFTED-RIGHT-ENTITY",
                rx_number=episode["rx_number"],
                pharmacy_npi=episode["pharmacy_npi"],
                ndc_11=episode["ndc11"],
                fill_date=keys.iso_date_to_wire(episode["date_of_service"]),
                covered_entity_id=OUR_ENTITY,
            )
        ],
        tmp_path,
        "right-entity",
    )

    norm_id = _norm_id(conn, "CRAFTED-RIGHT-ENTITY")
    assert _park_reason(conn, norm_id) is None
    assert _attachments(conn, norm_id) == [
        (str(KeyType.NATURAL_340B_PHARMACY), episode["episode_id"])
    ]


def test_a_record_naming_an_entity_the_episode_lacks_still_attaches(fresh, tmp_path):
    """Silence is not contradiction, and this is the case that makes the asymmetry matter.

    An episode at the satellite NPI has no covered entity, because no covered entity of ours
    registered that location.  A TPA record naming one there is not disagreeing with us — it is
    the *only* statement of entity anybody has, and it is how the unregistered location becomes
    visible at all.  Parking it would destroy the one record that could surface the problem, so
    ``_contradicts_covered_entity`` fires only when both sides state one and they differ.
    """
    conn = fresh()
    episode = _episode_with_an_unambiguous_key(
        conn, f"pharmacy_npi = '{SATELLITE_NPI}'", _pharmacy_natural_key
    )
    assert episode["covered_entity_id"] is None

    _ingest(
        conn,
        [
            _tpa_event(
                "CRAFTED-SILENT-EPISODE",
                rx_number=episode["rx_number"],
                pharmacy_npi=episode["pharmacy_npi"],
                ndc_11=episode["ndc11"],
                fill_date=keys.iso_date_to_wire(episode["date_of_service"]),
                covered_entity_id=DECOY_ENTITY,
            )
        ],
        tmp_path,
        "silent-episode",
    )

    norm_id = _norm_id(conn, "CRAFTED-SILENT-EPISODE")
    assert _park_reason(conn, norm_id) is None, (
        "an episode with no covered entity has no opinion to contradict; parking here would "
        "treat absence as disagreement and hide every unregistered location"
    )
    assert _attachments(conn, norm_id) == [
        (str(KeyType.NATURAL_340B_PHARMACY), episode["episode_id"])
    ]


# ═══ E2 — HCPCS alongside NDC ═══════════════════════════════════════════════
#
# "a medical record carrying only a J-code resolves to the right episode."


def test_hcpcs_is_carried_by_medical_episodes_and_by_no_pharmacy_one(run):
    """The column split, and the reference fact that makes the pharmacy NULLs correct.

    A medical-benefit drug is billed by J-code and a pharmacy-benefit drug is not — that is a
    property of the drug, not of the feed — so a pharmacy episode with an ``hcpcs`` would mean
    the adapter had invented one.  The reference table is asked directly for every NDC actually
    dispensed on the pharmacy side, so this fails if a J-coded drug ever reaches that track
    rather than silently agreeing with a column that has stopped being written.
    """
    counts = run.conn.execute(
        "SELECT reimbursement_track, COUNT(*) AS n, COUNT(hcpcs) AS coded"
        "  FROM episode GROUP BY reimbursement_track"
    ).fetchall()
    by_track = {row["reimbursement_track"]: row for row in counts}
    assert by_track["MEDICAL"]["coded"] == by_track["MEDICAL"]["n"] > 0
    assert by_track["PHARMACY"]["coded"] == 0 and by_track["PHARMACY"]["n"] > 0

    for row in run.conn.execute(
        "SELECT DISTINCT ndc11 FROM episode WHERE reimbursement_track = 'PHARMACY'"
    ):
        assert drugs.by_ndc(row["ndc11"]).hcpcs_j_code is None, (
            f"{row['ndc11']} is dispensed on the pharmacy track but the reference gives it a "
            "J-code; either the drug table or the track assignment is wrong"
        )


def test_a_medical_record_carrying_only_a_j_code_resolves_to_its_episode(fresh, tmp_path):
    """**This is E2's acceptance criterion, literally.**

    ``ndc_11`` was a required field until E2, so this record did not park — it failed to adapt
    at all and was quarantined as unparseable, when in fact it was perfectly well formed and
    simply described its drug the way the medical benefit describes it.  The assertion names
    the key type as well as the episode: resolving by the right answer through the wrong key
    would be luck, and would stop working the moment a second drug shared the date.
    """
    conn = fresh()
    episode = _episode_with_an_unambiguous_key(conn, "hcpcs IS NOT NULL", _hcpcs_key)
    payload = _tpa_event(
        "CRAFTED-J-CODE-ONLY",
        provider_npi=episode["billing_provider_npi"],
        hcpcs=episode["hcpcs"],
        fill_date=keys.iso_date_to_wire(episode["date_of_service"]),
    )
    assert payload["ndc_11"] is None, "the whole point is that this record names no NDC"

    _ingest(conn, [payload], tmp_path, "j-code-only")

    norm_id = _norm_id(conn, "CRAFTED-J-CODE-ONLY")
    assert _park_reason(conn, norm_id) is None
    assert _attachments(conn, norm_id) == [(str(KeyType.HCPCS), episode["episode_id"])]


def test_a_record_carrying_both_an_ndc_and_a_j_code_keys_on_the_ndc(run):
    """The J-code key is tried only when the NDC is absent, never alongside it.

    An NDC identifies one manufacturer's presentation; a J-code spans every manufacturer's, so
    the J-code resolves a superset.  Offering both would give a record that already had an
    exact answer a second, vaguer hit — and two hits park as ``AMBIGUOUS_KEY_MATCH``, trading a
    correct match for no match at all.  The exact key is asserted rather than the count,
    because one key of the wrong type is still one key.
    """
    episode = run.conn.execute(
        "SELECT * FROM episode WHERE hcpcs IS NOT NULL ORDER BY episode_id"
    ).fetchone()
    both = adapters.adapt(
        _tpa_event(
            "CRAFTED-BOTH-CODES",
            provider_npi=episode["billing_provider_npi"],
            ndc_11=episode["ndc11"],
            hcpcs=episode["hcpcs"],
            fill_date=keys.iso_date_to_wire(episode["date_of_service"]),
        ),
        SourceSystem.TPA_PORTAL,
    )
    assert both.looks_up == (
        keys.natural_340b_medical(
            episode["billing_provider_npi"], episode["ndc11"], episode["date_of_service"]
        ),
    )
    assert both.hcpcs == episode["hcpcs"], (
        "the J-code must still be recorded on the row; it is barred from being a key here, "
        "not barred from being a fact"
    )


def test_a_cpt_administration_code_produces_no_hcpcs_key(run):
    """``96413`` is the act of infusing, not the drug, and it parses exactly as cleanly.

    Keying on it would collapse every drug one provider infused on one day onto a single key
    value, and every one of those records would then resolve ambiguously and park — a whole
    day's medical 340B lost to a code that looked like a J-code.  The column may hold it
    honestly; the key may not, which is the asymmetry ``_is_known_drug_j_code`` enforces.
    """
    episode = run.conn.execute(
        "SELECT * FROM episode WHERE hcpcs IS NOT NULL ORDER BY episode_id"
    ).fetchone()
    administration = adapters.adapt(
        _tpa_event(
            "CRAFTED-CPT-CODE",
            provider_npi=episode["billing_provider_npi"],
            hcpcs="96413",
            fill_date=keys.iso_date_to_wire(episode["date_of_service"]),
        ),
        SourceSystem.TPA_PORTAL,
    )
    assert administration.looks_up == (), (
        "a CPT administration code became a crosswalk key; it describes the infusion, not "
        "the drug, so every drug infused that day would collide onto it"
    )
    assert not adapters._is_known_drug_j_code("96413")
    assert adapters._is_known_drug_j_code(episode["hcpcs"])


# ═══ E3 — the payment reference as a key of its own ═════════════════════════
#
# "a manufacturer rebate payment resolves by its own payment reference."


def test_a_payment_reference_is_its_own_key_type_and_keeps_its_value_verbatim():
    """E3's key exists and is not a re-spelling of ``ALLOCATION_CODE`` or ``TRN02``.

    A single-component key, so the canonical form *is* the component — which makes the
    interesting assertion the one about the type.  Beacon's reference for a settlement and the
    manufacturer's batch reference are two different facts that happen to agree today, and
    publishing them under one key type would make every bank deposit that looks one up a
    two-hit the crosswalk parks.
    """
    key_type, value = keys.payment_reference("BCN-PAY-000000000001")
    assert key_type is KeyType.PAYMENT_REFERENCE
    assert value == "BCN-PAY-000000000001"
    assert keys.SEPARATOR not in value

    with pytest.raises(keys.MalformedKeyComponentError):
        keys.payment_reference("")


def test_a_deposit_resolving_by_payment_reference_allocates_on_the_rebate_basis(fresh):
    """E3's acceptance at the branch that decides where the money goes.

    ``_allocate_bank_row`` is called directly rather than through a whole synthetic
    bank-deposit flow.  The flow would need a Beacon batch landed as a ``normalized_record``,
    and the Beacon connector's ``InboundRecord`` path does not reach ``pipeline.ingest`` in
    this build — so constructing it would be constructing a second ingest path, not testing
    this one.  What that branch decides is still exactly what is asserted: the resolution's
    key type, and the ``AllocationBasis`` written on every ledger row it produces.

    ``PAYMENT_REFERENCE`` is grouped with ``ALLOCATION_CODE`` on purpose.  Left out, it falls
    through to the ``TRN02`` basis, and the audit trail then reports a manufacturer's rebate
    settlement as a payer's claim-payment reassociation — cash allocated correctly and
    explained wrongly, which no downstream report can tell from the truth.
    """
    conn = fresh()
    batch = conn.execute(
        "SELECT p.norm_id, p.amount_cents, COUNT(c.norm_id) AS lines"
        "  FROM normalized_record p"
        "  JOIN normalized_record c"
        "    ON c.parent_norm_id = p.norm_id AND c.record_kind = ?"
        " WHERE p.record_kind = ?"
        " GROUP BY p.norm_id HAVING lines > 1 ORDER BY lines DESC, p.norm_id LIMIT 1",
        (str(RecordKind.REBATE_DISPENSE_LINE), str(RecordKind.REBATE_BATCH)),
    ).fetchone()
    assert batch is not None, "no rebate batch has more than one dispense line to split across"
    bank_norm_id = conn.execute(
        "SELECT norm_id FROM normalized_record WHERE record_kind = ? ORDER BY norm_id LIMIT 1",
        (str(RecordKind.BANK_TRANSACTION),),
    ).fetchone()["norm_id"]

    deposit = adapters.CanonicalRecord(
        record_kind=RecordKind.BANK_TRANSACTION,
        source_system=SourceSystem.BANK,
        received_at=LATE,
        idempotency_key="CRAFTED-REBATE-DEPOSIT",
        canonical={},
        amount_cents=batch["amount_cents"],
    )
    resolution = Resolution(
        key_type=KeyType.PAYMENT_REFERENCE,
        key_value="BCN-PAY-000000000001",
        episode_id=None,
        remittance_norm_id=batch["norm_id"],
        first_seen_at=LATE,
        resolved_from_norm_id=batch["norm_id"],
    )
    pipeline._allocate_bank_row(
        conn, deposit, bank_norm_id, batch["norm_id"], [resolution], LATE, pipeline.IngestStats()
    )

    written = conn.execute(
        "SELECT basis, remittance_norm_id FROM cash_allocation WHERE caused_by_received_at = ?",
        (LATE,),
    ).fetchall()
    # One row per dispense line is the rebate splitter's signature: the claim-payment splitter
    # walks ``REMITTANCE_CLAIM_LINE`` children, of which a rebate batch has none.
    assert len(written) == batch["lines"]
    assert {row["remittance_norm_id"] for row in written} == {batch["norm_id"]}
    assert {row["basis"] for row in written} <= {
        str(AllocationBasis.ALLOCATION_CODE),
        str(AllocationBasis.RESIDUAL),
    }
    assert str(AllocationBasis.TRN02) not in {row["basis"] for row in written}


def test_a_deposit_resolving_by_trn02_records_the_other_basis(fresh):
    """The mirror, so the test above is not passing because every basis is the same one.

    Same batch, same deposit, same splitter — only the key type that resolved differs, and
    that is the one input the grouping reads.  Without this, a build that hardcoded
    ``ALLOCATION_CODE`` would pass the test above and lose the distinction entirely.
    """
    conn = fresh()
    batch = conn.execute(
        "SELECT norm_id, amount_cents FROM normalized_record WHERE record_kind = ?"
        " ORDER BY norm_id LIMIT 1",
        (str(RecordKind.REBATE_BATCH),),
    ).fetchone()
    bank_norm_id = conn.execute(
        "SELECT norm_id FROM normalized_record WHERE record_kind = ? ORDER BY norm_id LIMIT 1",
        (str(RecordKind.BANK_TRANSACTION),),
    ).fetchone()["norm_id"]

    deposit = adapters.CanonicalRecord(
        record_kind=RecordKind.BANK_TRANSACTION,
        source_system=SourceSystem.BANK,
        received_at=LATE,
        idempotency_key="CRAFTED-CLAIM-DEPOSIT",
        canonical={},
        amount_cents=batch["amount_cents"],
    )
    resolution = Resolution(
        key_type=KeyType.TRN02,
        key_value="TRN-000000000001",
        episode_id=None,
        remittance_norm_id=batch["norm_id"],
        first_seen_at=LATE,
        resolved_from_norm_id=batch["norm_id"],
    )
    pipeline._allocate_bank_row(
        conn, deposit, bank_norm_id, batch["norm_id"], [resolution], LATE, pipeline.IngestStats()
    )

    bases = {
        row["basis"]
        for row in conn.execute(
            "SELECT basis FROM cash_allocation WHERE caused_by_received_at = ?", (LATE,)
        )
    }
    assert str(AllocationBasis.ALLOCATION_CODE) not in bases
    assert bases <= {str(AllocationBasis.TRN02), str(AllocationBasis.RESIDUAL)}


# ═══ E4 — site identity below the NPI ═══════════════════════════════════════
#
# "two contract pharmacies under the same NPI-holding entity are distinguishable."
#
# The site table itself is ``tests/test_sites.py``'s subject and is not re-tested here.  What
# that file cannot see is whether ingest consults it, and what it writes when the answer is
# "there is no single answer".


def test_an_episode_carries_a_site_only_where_the_npi_holds_exactly_one(run):
    """E4 at the grain the pipeline actually works on: an anchor record with an NPI and nothing finer.

    Both directions are the requirement.  The satellite NPI holds one site, so the identity is
    recoverable and is recovered.  The registered NPI holds two, so a core feed cannot say
    which dispensed — and the column stays NULL rather than taking the first candidate.  Delete
    this and ``_site_for`` could start guessing without any test noticing, because a guessed
    site and a correct one are the same shape of string.
    """
    by_npi = {
        row["pharmacy_npi"]: row
        for row in run.conn.execute(
            "SELECT pharmacy_npi, COUNT(*) AS n, COUNT(site_id) AS sited FROM episode"
            " WHERE pharmacy_npi IS NOT NULL GROUP BY pharmacy_npi"
        )
    }
    satellite = by_npi[SATELLITE_NPI]
    assert satellite["sited"] == satellite["n"] > 0
    assert sites.resolve_site(SATELLITE_NPI).is_unique

    shared = by_npi[SHARED_NPI]
    assert shared["n"] > 0 and shared["sited"] == 0
    assert sites.resolve_site(SHARED_NPI).is_ambiguous, (
        f"the NULL site on {shared['n']} episodes is only honest while {SHARED_NPI} genuinely "
        "holds more than one site; if it stopped being ambiguous the NULL became a bug"
    )


def test_no_episode_is_assigned_one_of_two_candidate_sites(run):
    """The refusal, transposed from the reference table onto the data it produced.

    ``resolve_site`` makes choosing inexpressible — you have to index into ``candidates`` on
    purpose — but that is a property of one function, and ingest could reach around it.  This
    asserts the outcome instead: no site that shares its NPI with another ever reaches an
    ``episode`` row, so a guess would have to show up here.
    """
    ambiguous = {
        site.site_id
        for site in sites.sites()
        if len(sites.sites_for_npi(site.npi)) > 1
    }
    assert len(ambiguous) > 1, "no NPI holds two sites, so there is nothing to refuse to choose"

    landed = {
        row["site_id"]
        for row in run.conn.execute("SELECT DISTINCT site_id FROM episode WHERE site_id IS NOT NULL")
    }
    assert not (landed & ambiguous), (
        f"episodes carry {sorted(landed & ambiguous)}, which bill under an NPI shared with "
        "another site; the anchor record cannot know which, so this is a guess"
    )


# ═══ E5 — the source-of-truth boundary, enforced at ingest ══════════════════
#
# "a TPA record attempting to set rebate-payment status is rejected, not silently accepted."
#
# ``tests/test_authority.py`` already diffs DOC2-004 against the document and exercises
# ``check_authority`` in isolation.  Only the ingest wiring is tested here: where the check is
# called from, what it does to the record, and whether it reaches a record's children.


def test_a_tpa_record_setting_rebate_status_is_quarantined_with_its_lineage_intact(
    fresh, tmp_path
):
    """**This is E5's acceptance criterion.**  A TPA row that sets a field Beacon owns.

    Three outcomes would all be wrong and only one of them looks wrong: normalizing it anyway
    (the vendor's opinion becomes the canonical fact and is indistinguishable afterwards from a
    true one), logging it and moving on, or dropping the raw row. So the assertions are that
    no ``normalized_record`` exists, that the ``raw_record`` still does, and that the
    quarantine row points back at it.

    Note the record is otherwise impeccable — it parses, it adapts, its keys resolve.  That is
    why ``SOURCE_AUTHORITY_BREACH`` is its own reason rather than ``UNPARSEABLE``: the bytes
    are fine and the next action is a conversation about which system is the system of record.
    """
    conn = fresh()
    episode = _episode_with_an_unambiguous_key(
        conn, f"pharmacy_npi = '{SHARED_NPI}'", _pharmacy_natural_key
    )
    before = conn.execute("SELECT COUNT(*) AS n FROM quarantined_record").fetchone()["n"]
    stats = _ingest(
        conn,
        [
            _tpa_event(
                "CRAFTED-AUTHORITY-BREACH",
                rx_number=episode["rx_number"],
                pharmacy_npi=episode["pharmacy_npi"],
                ndc_11=episode["ndc11"],
                fill_date=keys.iso_date_to_wire(episode["date_of_service"]),
                covered_entity_id=OUR_ENTITY,
                # Beacon's, per DOC2-004. A TPA is authoritative for qualification and for its
                # own claim status, and for neither of the manufacturer's answers.
                manufacturer_status="PAID",
            )
        ],
        tmp_path,
        "authority-breach",
    )

    assert _norm_id(conn, "CRAFTED-AUTHORITY-BREACH") is None, (
        "the record was normalized; a rebate status the TPA is not entitled to assert is now "
        "a canonical fact that nothing downstream can tell from a true one"
    )
    quarantined = conn.execute(
        "SELECT q.reason_code, q.raw_id FROM quarantined_record q"
        "  JOIN raw_record r ON r.raw_id = q.raw_id"
        " WHERE r.source_record_id = 'CRAFTED-AUTHORITY-BREACH'"
    ).fetchall()
    assert len(quarantined) == 1
    assert quarantined[0]["reason_code"] == str(QuarantineReason.SOURCE_AUTHORITY_BREACH)
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM raw_record WHERE raw_id = ?", (quarantined[0]["raw_id"],)
        ).fetchone()[0]
        == 1
    ), "the raw row is gone, so the quarantine queue points at nothing an operator can read"
    assert stats.quarantined == before + 1


def test_the_quarantine_detail_names_the_field_and_who_owns_it(fresh, tmp_path):
    """A quarantine queue nobody can action is a slower version of dropping the record.

    The operator's next step is a conversation with a vendor, so the row has to say which
    field, which source sent it, and which system DOC2-004 makes authoritative — not "record
    rejected".  Asserted on the stored ``detail`` rather than on ``AuthorityBreach.describe``,
    because the message is truncated to 400 characters on the way in and a message that says
    everything but loses the field name past the cut is the same failure.
    """
    conn = fresh()
    episode = _episode_with_an_unambiguous_key(
        conn, f"pharmacy_npi = '{SHARED_NPI}'", _pharmacy_natural_key
    )
    _ingest(
        conn,
        [
            _tpa_event(
                "CRAFTED-BREACH-DETAIL",
                rx_number=episode["rx_number"],
                pharmacy_npi=episode["pharmacy_npi"],
                ndc_11=episode["ndc11"],
                fill_date=keys.iso_date_to_wire(episode["date_of_service"]),
                manufacturer_status="PAID",
            )
        ],
        tmp_path,
        "breach-detail",
    )

    detail = conn.execute(
        "SELECT q.detail FROM quarantined_record q JOIN raw_record r ON r.raw_id = q.raw_id"
        " WHERE r.source_record_id = 'CRAFTED-BREACH-DETAIL'"
    ).fetchone()["detail"]
    assert "manufacturer_status" in detail, "the offending field is not named"
    assert "Beacon" in detail, "the system that does own it is not named"
    assert str(SourceSystem.TPA_PORTAL) in detail, "the source that overstepped is not named"


def test_a_child_of_a_record_tree_is_checked_too(run):
    """The check walks the tree, so a dispense line cannot carry what its batch may not.

    A rebate batch and its dispense lines are written by ``_insert_tree`` alike, each with its
    own kind, source and canonical dict.  Checking only the parent would leave every line as a
    field a source could use to set something it does not own, and the parent would still look
    clean — which is precisely the shape of breach that survives review.

    Built in memory rather than as a payload, and the reason is worth recording:
    ``_adapt_rebate_batch`` hardcodes ``SourceSystem.MANUFACTURER_REBATE`` on the parent *and*
    on every child, and the child's canonical dict is a fixed three keys, none of which
    MANUFACTURER_REBATE is barred from setting.  No ``REBATE_PAYMENT_BATCH`` payload can
    therefore express a dispense-line breach today.  The tree walk in
    ``_assert_source_authority`` is what would have to hold the day a vendor mapping puts a
    different source system on a child, so it is tested at the level where it is reachable.
    """
    line = adapters.CanonicalRecord(
        record_kind=RecordKind.REBATE_DISPENSE_LINE,
        source_system=SourceSystem.MANUFACTURER_REBATE,
        received_at=LATE,
        idempotency_key="CRAFTED-BATCH|0",
        # The TPA's field, on the manufacturer's record. DOC2-004 gives 340B qualification to
        # the TPA, and a manufacturer asserting a dispense was qualified is deciding its own
        # eligibility to be billed.
        canonical={"manufacturer_status": "PAID", "qualification_status": "QUALIFIED"},
    )
    batch = adapters.CanonicalRecord(
        record_kind=RecordKind.REBATE_BATCH,
        source_system=SourceSystem.MANUFACTURER_REBATE,
        received_at=LATE,
        idempotency_key="CRAFTED-BATCH",
        canonical={"event_type": "REBATE_PAYMENT_BATCH", "manufacturer": "ACME"},
    )

    # The parent alone is legitimate, which is the half that makes the other half meaningful:
    # if the batch breached too, a parent-only check would pass this test.
    pipeline._assert_source_authority(batch)

    batch.children.append(line)
    with pytest.raises(SourceAuthorityError) as caught:
        pipeline._assert_source_authority(batch)
    assert [breach.field for breach in caught.value.breaches] == ["qualification_status"]
    assert caught.value.breaches[0].record_kind is RecordKind.REBATE_DISPENSE_LINE
