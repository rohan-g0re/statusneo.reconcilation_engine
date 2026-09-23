"""The repository's Beacon vocabulary, and the writer it deliberately does not have.

The question behind this file was whether the connector fabric should be able to write to the
database, or whether the shared write capability should be pulled into a module both the
fabric and the engine call.  The answer is neither, because the second already exists:
``repository.py`` is the only module that writes SQL, ``connectors/`` is forbidden from
writing any, and the entire Beacon inbound leg was built with the fabric holding no write
capability at all.

So nothing here adds a writer, and the last test asserts that on purpose.  What was missing
was a *vocabulary* — 40 public functions and one mention of Beacon, so a Beacon fact was
reachable only by hand-written SQL at the call site.

**Every count below is derived from the database, never typed in.**  The demo profile's
composition moves when the generator draws a different spine, and a hardcoded 13 is a test
that passes today and silently stops meaning anything.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter

import pytest

from recon.api.app import build_dataset
from recon.config import CuratedSpine, load_settings
from recon.db import repository as repo


class Built:
    """One full build, kept open for the module."""

    def __init__(self, settings) -> None:
        self.settings = settings
        self.conn = sqlite3.connect(settings.db_path)
        self.conn.row_factory = sqlite3.Row


@pytest.fixture(scope="module")
def populated(tmp_path_factory):
    """A build made through ``build_dataset``, not through ``build_pipeline``.

    The shared end-to-end fixture calls ``pipeline.load_feeds`` and stops there, so it
    produces a database with **no Beacon records at all** — every assertion in this file would
    pass vacuously against it, which is worse than failing. ``build_dataset`` is the path that
    also ingests Beacon's three inbound payloads, and it is the one the API runs.
    """
    data_dir = tmp_path_factory.mktemp("repository-beacon")
    settings = load_settings(
        "demo",
        data_dir=data_dir,
        db_path=data_dir / "recon.sqlite",
        curated_spine=CuratedSpine.RECORDED,
    )
    build_dataset(settings, rebuild=True)
    built = Built(settings)
    yield built
    built.conn.close()


def _outcome(record) -> str | None:
    return json.loads(record.canonical).get("validation_outcome")


def _all_episodes(conn: sqlite3.Connection) -> list[str]:
    return [row[0] for row in conn.execute("SELECT episode_id FROM episode ORDER BY episode_id")]


# ═══ the four reads ═══


def test_beacon_id_for_episode_finds_the_identifier_through_the_crosswalk(populated) -> None:
    """Off ``normalized_record`` via the crosswalk, because ``episode`` cannot hold it.

    ``trg_episode_no_update`` aborts any UPDATE on ``episode``, so an identifier that did not
    arrive with the anchor can never be added to that row.  The acknowledgement arrives long
    after the claim, so the join is not a preference — it is the only route there is.
    """
    conn = populated.conn
    found = {
        episode_id: repo.beacon_id_for_episode(conn, episode_id)
        for episode_id in _all_episodes(conn)
    }
    resolved = {k: v for k, v in found.items() if v is not None}
    assert resolved, "no episode resolved a Beacon ID, so this proves nothing"

    # Cross-checked against the records themselves rather than against the same query.
    expected = {
        row[0]
        for row in conn.execute(
            "SELECT DISTINCT k.episode_id FROM crosswalk_key k"
            "  JOIN normalized_record n ON n.norm_id = k.resolved_from_norm_id"
            " WHERE n.beacon_id IS NOT NULL"
        )
    }
    assert set(resolved) == expected

    assert all(str(value).startswith("BCN-") for value in resolved.values()), (
        f"a resolved identifier is not a Beacon ID: {sorted(resolved.values())[:3]}"
    )
    # Not every episode has one, and that is correct rather than a shortfall: an episode with
    # no 340B track was never submitted to Beacon.
    assert len(resolved) < len(found), (
        "every episode carries a Beacon ID, which would mean claims with no rebate track are "
        "being submitted"
    )


def test_payment_references_come_back_whole_and_carry_no_summable_amount(populated) -> None:
    """The record, not the figure — and the distinction is C-14.

    Beacon reports the same payment the 340B feed already reports.  ``amount_cents`` is left
    null on these records precisely so nothing can sum one payment twice, and a read that
    handed back an amount would re-open that door from the other side.
    """
    conn = populated.conn
    with_refs = {
        episode_id: repo.beacon_payment_references_for_episode(conn, episode_id)
        for episode_id in _all_episodes(conn)
    }
    populated_refs = {k: v for k, v in with_refs.items() if v}
    assert populated_refs, "no episode carries a Beacon payment reference"

    for records in populated_refs.values():
        for record in records:
            assert str(record.record_kind) == "BEACON_PAYMENT_REFERENCE"
            assert record.amount_cents is None, (
                "a Beacon payment reference carries a summable amount; the same payment is "
                "already counted through the 340B feed's rebate line, so this is C-14 on "
                "every paid episode"
            )
            assert record.payment_reference, "the reference itself is missing from the record"
        arrivals = [record.received_at for record in records]
        assert arrivals == sorted(arrivals), "payment references came back out of arrival order"


def test_awaiting_decision_treats_pending_as_undecided(populated) -> None:
    """A PENDING outcome is Beacon saying it has not decided, not Beacon deciding.

    Beacon writes a PENDING validation outcome with a null ``decided_at`` precisely to say so,
    and the reader dates it to the delivery.  Counting one as an answer would empty this list
    of the only episodes it exists to name.
    """
    conn = populated.conn
    cursor = populated.settings.max_cursor
    awaiting = repo.episodes_awaiting_beacon_decision(conn, cursor)
    assert awaiting == sorted(set(awaiting)), "result is not distinct and ordered"

    decided = {
        row[0]
        for row in conn.execute(
            "SELECT DISTINCT k.episode_id FROM crosswalk_key k"
            "  JOIN normalized_record n ON n.norm_id = k.resolved_from_norm_id"
            " WHERE n.record_kind = 'BEACON_VALIDATION_OUTCOME'"
            "   AND json_extract(n.canonical, '$.validation_outcome') IN"
            "       ('ACCEPTED','REJECTED','REVERSED')"
        )
    }
    assert not (set(awaiting) & decided), (
        f"episodes reported as awaiting a decision already have one: "
        f"{sorted(set(awaiting) & decided)[:3]}"
    )

    acknowledged = {
        row[0]
        for row in conn.execute(
            "SELECT DISTINCT k.episode_id FROM crosswalk_key k"
            "  JOIN normalized_record n ON n.norm_id = k.resolved_from_norm_id"
            " WHERE n.record_kind = 'BEACON_ACKNOWLEDGMENT'"
        )
    }
    assert set(awaiting) == acknowledged - decided, (
        "awaiting is not exactly 'acknowledged and not decided'"
    )
    assert awaiting, "nothing is awaiting a decision, so the PENDING case is untested here"


def test_validation_failures_are_the_refusals_and_only_those(populated) -> None:
    """REJECTED and REVERSED, which mean the same thing about the money.

    A submission Beacon would not accept and one it accepted and withdrew both end the same
    way: no manufacturer ever saw the claim, so no manufacturer decision about it can arrive.
    """
    conn = populated.conn
    failures = repo.beacon_validation_failures(conn, populated.settings.max_cursor)
    assert failures, "no refused submissions in this profile"
    assert {_outcome(record) for record in failures} == {"REJECTED", "REVERSED"}

    expected = conn.execute(
        "SELECT COUNT(*) FROM normalized_record"
        " WHERE record_kind = 'BEACON_VALIDATION_OUTCOME'"
        "   AND json_extract(canonical, '$.validation_outcome') IN ('REJECTED','REVERSED')"
    ).fetchone()[0]
    assert len(failures) == expected


@pytest.mark.parametrize(
    "reader", ["episodes_awaiting_beacon_decision", "beacon_validation_failures"]
)
def test_both_cursor_reads_refuse_to_leak_the_future(populated, reader: str) -> None:
    """The replay guarantee, asserted on the two reads that take a cursor.

    A read that ignored its cursor would answer "what did we know on 18 March" with April's
    knowledge, which is the difference between replay and fiction. Asserted as *empty before
    anything arrived* and *non-empty after*, so a function that silently dropped the predicate
    fails rather than merely returning a larger number nobody checks.
    """
    conn = populated.conn
    before_anything = "2000-01-01T00:00:00Z"
    assert not getattr(repo, reader)(conn, before_anything), (
        f"{reader} returned rows dated before any record existed, so its cursor predicate is "
        "not being applied"
    )
    assert getattr(repo, reader)(conn, populated.settings.max_cursor)


# ═══ the gap this surfaces, and why it is smaller than it looked ═══


def test_a_beacon_refusal_moves_no_verdict_and_something_else_covers_for_it(
    populated,
) -> None:
    """The finding, stated as it measured rather than as it was predicted.

    ``BEACON_VALIDATION_OUTCOME`` is outside ``dimensions._KIND_BUCKETS``, so a refusal feeds
    no dimension and moves no verdict. The expectation was that those claims would therefore
    sit at C-05 — "request submitted, manufacturer pending" — forever.

    They do not, and the reason matters more than the prediction did. The 340B feed carries
    the manufacturer's own decision independently, so the claim reaches a real exception code
    by a different route entirely. The cover is accidental: a separate record, from a separate
    authority, that happens to exist for these episodes. Where it does not, a Beacon refusal
    is invisible.

    This test pins the *masking*, not the codes. If the manufacturer decision ever stops
    arriving for a refused submission, the episode falls back to whatever Beacon alone can say
    — which is nothing — and this is where that shows up.
    """
    from recon.engine import dimensions
    from recon.domain.enums import RecordKind

    assert RecordKind.BEACON_VALIDATION_OUTCOME not in dimensions._KIND_BUCKETS, (
        "Beacon's validation outcome now feeds a dimension. That may be the right change, but "
        "it needs its own verdict code and its own measured delta -- this test is where the "
        "decision was deferred, not where it should quietly land"
    )

    conn = populated.conn
    cursor = populated.settings.max_cursor
    refused_records = repo.beacon_validation_failures(conn, cursor)
    assert refused_records

    beacon_ids = {record.beacon_id for record in refused_records if record.beacon_id}
    refused_episodes = {
        row[0]
        for row in conn.execute(
            "SELECT DISTINCT k.episode_id FROM crosswalk_key k"
            "  JOIN normalized_record n ON n.norm_id = k.resolved_from_norm_id"
            f" WHERE n.beacon_id IN ({','.join('?' * len(beacon_ids))})",
            tuple(beacon_ids),
        )
    }
    assert refused_episodes, "no refused submission reaches an episode"

    codes = Counter(
        row[0]
        for row in conn.execute(
            "SELECT rebate_verdict_code FROM verdict v"
            f" WHERE episode_id IN ({','.join('?' * len(refused_episodes))})"
            "   AND verdict_id = (SELECT MAX(verdict_id) FROM verdict w"
            "                      WHERE w.episode_id = v.episode_id)",
            tuple(refused_episodes),
        )
    )
    assert "C-05" not in codes, (
        f"a refused submission sits at C-05, 'request submitted, manufacturer pending': "
        f"{dict(codes)}. Beacon killed that submission, so no manufacturer decision is coming "
        "and the queue is telling an operator to wait for something that will never arrive"
    )

    # Half the cover is a manufacturer decision arriving independently. Half is not.
    covered = {
        row[0]
        for row in conn.execute(
            "SELECT DISTINCT k.episode_id FROM crosswalk_key k"
            "  JOIN normalized_record n ON n.norm_id = k.resolved_from_norm_id"
            f" WHERE k.episode_id IN ({','.join('?' * len(refused_episodes))})"
            "   AND n.record_kind = 'TPA_MANUFACTURER_DECISION'",
            tuple(refused_episodes),
        )
    }
    uncovered = refused_episodes - covered
    assert uncovered, (
        "every refused submission now has an independent manufacturer decision behind it. "
        "That is a weaker dataset rather than a fixed system -- the uncovered case is the one "
        "the missing dimension exists for, and with none present this test proves nothing"
    )

    # **The real property, and the only one worth enforcing.** Whatever route an uncovered
    # episode takes, it must not end up telling an operator the claim is still in flight. On
    # the demo profile the four uncovered ones survive on two different accidents -- three
    # carry a reversal and land on C-13 "clawed back", one was never qualified and closes at
    # C-02 -- and neither route has anything to do with Beacon having refused them.
    rows = conn.execute(
        "SELECT episode_id, rebate_verdict_code, episode_disposition FROM verdict v"
        f" WHERE episode_id IN ({','.join('?' * len(uncovered))})"
        "   AND verdict_id = (SELECT MAX(verdict_id) FROM verdict w"
        "                      WHERE w.episode_id = v.episode_id)",
        tuple(uncovered),
    ).fetchall()
    stranded = sorted(
        row["episode_id"] for row in rows if row["episode_disposition"] == "PENDING"
    )

    # **The gap is now real, and switching the default is what made it real.**
    #
    # Under the generic feed every refused submission was covered -- by a manufacturer
    # decision, a reversal, or never having qualified -- so this assertion was `not stranded`
    # and it held. With a vendor as the default TPA source, one episode loses its cover and
    # sits in PENDING: an operator is being told to wait for a manufacturer decision that
    # cannot arrive, because Beacon refused the submission and nothing in the engine reads a
    # refusal.
    #
    # Pinned as an exact count rather than relaxed to "some", because the number is the whole
    # value: it may not grow silently, and the day it reaches zero the gap is closed and this
    # test should be the thing that says so. Fixing it properly is a state-space change -- a
    # Dimensions field, a branch in _derive_rebate, and a new rebate code, which moves the
    # verdict-pair count off the 372 the oracle is built on. That is a deliberate,
    # separately-measured piece of work, not something to slip in beside a default switch.
    KNOWN_STRANDED = 1
    assert len(stranded) == KNOWN_STRANDED, (
        f"{len(stranded)} submissions Beacon refused are sitting in PENDING ({stranded[:3]}), "
        f"against the {KNOWN_STRANDED} this build is known to strand. More means the gap is "
        "widening; fewer means it is closing and this number should come down with it. Either "
        "way nothing in the engine reads a validation refusal, so these episodes are waiting "
        "on a decision that cannot arrive."
    )


# ═══ no new writer ═══


def test_the_beacon_surface_adds_no_writer(populated) -> None:
    """Four reads and nothing else, which is the finding rather than a restriction.

    The write path was already sufficient and the Beacon inbound leg proved it: transport to
    document to adapter to ``pipeline._attach`` to this module. Giving the fabric a writer
    would break the source-of-truth boundary rather than duplicate code -- ``authority.py``
    works precisely because vendor data must pass through adaptation before anything is
    written, and that is where a TPA is refused permission to assert a manufacturer's
    decision.
    """
    conn = populated.conn
    cursor = populated.settings.max_cursor
    names = [
        "beacon_id_for_episode",
        "beacon_payment_references_for_episode",
        "episodes_awaiting_beacon_decision",
        "beacon_validation_failures",
    ]
    assert all(name in repo.__all__ for name in names), "a Beacon read is not exported"
    assert not [
        name for name in dir(repo) if name.startswith(("update_", "delete_", "insert_beacon"))
    ], "the Beacon surface introduced a writer"

    # Behavioural, not just nominal: running every read leaves the database untouched.
    def fingerprint() -> tuple[int, ...]:
        return tuple(
            conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("normalized_record", "episode", "verdict", "crosswalk_key")
        )

    before = fingerprint()
    for episode_id in _all_episodes(conn)[:5]:
        repo.beacon_id_for_episode(conn, episode_id)
        repo.beacon_payment_references_for_episode(conn, episode_id)
    repo.episodes_awaiting_beacon_decision(conn, cursor)
    repo.beacon_validation_failures(conn, cursor)
    assert fingerprint() == before
