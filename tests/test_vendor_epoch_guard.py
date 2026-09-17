"""The delivery stamp a vendor export inherits, and the guard that refuses the epoch.

``load_from_sources`` defaults ``fetched_at`` to :data:`~recon.ingest.pipeline.EPOCH_STAMP`,
and on the ``VENDOR_CSV`` branch that default is the arrival time of *every row* of any
dataset whose :class:`~recon.connectors.vendors.SecureFileMapping` declares no
``received_at_column``.  Craneware's Claims Report declares none — it publishes four temporal
columns and all four are dates — so under the default its whole delivery was dated to 1970,
sorted to the very front of the timeline, and asked to resolve before any episode existed.

**Nothing reported it.**  No exception, no quarantine, no control-total shortfall: just a park
queue full of ``NO_KEY_MATCH`` rows that reads as a crosswalk problem and sends somebody to fix
a mapping that was never wrong.  On the ``demo`` profile at the ``RECORDED`` spine, 27 of the
33 records Craneware lands parked under the epoch and 1 parks under a real delivery stamp —
and that 1 is the identifier drift the generator injects on purpose, which is the answer this
leg is supposed to produce.  26 of the 27 were an artefact of the date alone.

:class:`~recon.ingest.pipeline.MissingDeliveryStampError` now refuses that call, and this file
is its only coverage.  ``tests/test_vendor_connector_leg.py`` and ``tests/test_file_pattern.py``
both pass an explicit stamp on every call, so between them they exercise the happy path and
nothing else — a refactor that deleted the guard would leave the whole suite green and the
original defect back.

**Five things are asserted, and the middle three are what stop the guard from being useless in
the opposite direction.**  It fires; it stays narrow, so a dataset that publishes its own
arrival time still loads on the default; the stamp a caller passes is the stamp the rows
actually land with; the parks the guard exists to prevent really are prevented, measured
against episodes built from the six feeds first; and a caller who passes the epoch by hand is
refused exactly as one who omits it, because the guard checks the outcome and not the intent.

**Counts are read out of the export, never typed in**, following
``tests/test_vendor_connector_leg.py``: what is asserted is the *shape* — under a real stamp a
small minority of rows park and the rest reach an episode — which survives the generator
drawing a different spine.  A hardcoded ``1`` would not.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Sequence

import pytest

from recon.config import CuratedSpine, Settings, load_settings
from recon.connectors import checkpoint, registry, vendors
from recon.connectors.transport import Document, LocalDirectoryTransport
from recon.connectors.vendors import ParsedDocument, SecureFileMapping, verity
from recon.generators import orchestrator
from recon.ingest import pipeline
from recon.mocks import coverage

#: The dataset that publishes no arrival time, and therefore the only one the guard can fire
#: on.  Named by source id and resolved through :func:`recon.connectors.vendors.mapping_for`,
#: so it cannot drift from the mapping module.
CRANEWARE_SOURCE_ID = "craneware_claims_report"

#: Every Verity dataset with a mapping, taken from the vendor module rather than typed out —
#: the module is the authority on what exists, and a hand-written list is a second place to
#: forget one.  Each declares a ``received_at_column``, which is what makes them the control
#: group: their rows never reach the fallback, so the default stamp is harmless to them.
VERITY_SOURCE_IDS: tuple[str, ...] = tuple(verity.MAPPINGS)

#: The delivery stamp handed to :func:`~recon.ingest.pipeline.load_from_sources`.
#:
#: A date no generator would ever draw, for the reason the sibling file gives: Craneware's rows
#: inherit this, so a plausible constant could be matched by a row that had quietly picked up
#: ``fill_date`` or ``rebate_payment_date`` instead and the inheritance test would pass by
#: coincidence.  Nothing real is in 2099, and the odd time-of-day makes it unmistakable in a
#: failure message.
DELIVERED_AT = "2099-03-04T05:06:07Z"

#: The largest share of a vendor delivery that may park before the delivery is suspect.
#:
#: The two regimes this separates are not close together, which is why a fraction is honest
#: here and a hardcoded count is not.  Under a real stamp the only rows that park are the ones
#: the generator deliberately broke — 1 of 33 on the committed profile, 3% — because identifier
#: drift is injected on a small fraction of claims by design.  Under the epoch, 27 of 33 park,
#: 82%, because no episode exists yet for any of them to resolve against.  A fifth sits
#: between the two with room on both sides, so this fails on the defect and not on the
#: generator drawing a slightly different spine.
MAX_PARKED_SHARE = 5


@pytest.fixture(scope="module")
def generated(tmp_path_factory) -> Settings:
    """The six feeds and the real vendor exports, generated once into a temp directory.

    Both halves, unlike the fixture in ``tests/test_vendor_connector_leg.py``, because the
    consequence test below needs episodes to exist before the vendor rows arrive — and
    episodes come from the feeds.  ``RECORDED`` pins the hand-placed spine so the composition
    of the dataset is stable.

    No test in this file touches ``data/``: everything is written under ``tmp_path_factory``
    and read back from there.
    """
    settings = load_settings(
        "demo",
        data_dir=tmp_path_factory.mktemp("vendor-epoch-guard"),
        curated_spine=CuratedSpine.RECORDED,
    )
    orchestrator.write_outputs(settings, orchestrator.generate(settings))
    coverage.write_all(settings)
    return settings


# ═══ reading the export back as the expectation ═══


def _locate(vendor_dir: Path, mapping: SecureFileMapping) -> Path:
    """Find the export a mapping describes, by the prefix the mapping itself declares.

    Verity stamps its export names from the data, so the landed name is not knowable in
    advance — only its prefix is, which is what ``SecureFileMapping.filename_prefix`` is for.
    """
    prefix = mapping.filename_prefix
    for path in sorted(vendor_dir.rglob("*.csv")):
        if path.name.startswith(prefix):
            return path
    found = sorted(p.name for p in vendor_dir.rglob("*.csv"))
    pytest.fail(f"no export starting {prefix!r} for {mapping.source_id}; on disk: {found}")


def _export(
    settings: Settings, source_id: str
) -> tuple[SecureFileMapping, Path, ParsedDocument]:
    """The mapping, the file on disk, and the file split into detail/trailer/unrecognised.

    Read directly rather than through the pipeline, which is the separation that makes the
    assertions mean anything: the pipeline produces the rows under test and the file is what
    they are checked against.
    """
    mapping = vendors.mapping_for(source_id)
    path = _locate(settings.vendor_dir(), mapping)
    parsed = vendors.read(
        mapping, Document(name=path.name, text=path.read_text(encoding="utf-8"))
    )
    return mapping, path, parsed


def _source(settings: Settings, source_id: str) -> registry.Source:
    """A registry row for one vendor dataset, bound to the directory its file landed in.

    ``enabled=True`` because the rows ship disabled — ``DOC2-016``'s Week 3 access gate — and
    a test standing in for a cleared gate is the one caller entitled to say so.
    """
    mapping, path, _parsed = _export(settings, source_id)
    directory = path.parent
    return registry.vendor_sources(
        {mapping.source_id: str(directory)},
        filenames={mapping.source_id: (path.name,)},
        transport=LocalDirectoryTransport(directory),
        enabled=True,
    )[0]


def _count(conn: sqlite3.Connection, sql: str, params: Sequence[object] = ()) -> int:
    """One scalar out of a ``COUNT(*) AS n`` query."""
    return conn.execute(sql, tuple(params)).fetchone()["n"]


def _batch_rows(conn: sqlite3.Connection, source_id: str, table: str) -> int:
    """How many rows of ``table`` trace back to the batch this source landed.

    Every count in the consequence test is scoped this way rather than taken globally: the six
    feeds are already in the database by then and park records of their own, and a global
    count would mix the two populations together.
    """
    return _count(
        conn,
        f"SELECT COUNT(*) AS n FROM {table} t"
        "  JOIN raw_record r ON r.raw_id = t.raw_id"
        "  JOIN ingest_batch b ON b.batch_id = r.batch_id"
        " WHERE b.source_id = ?",
        (source_id,),
    )


# ═══ 1. the guard fires ═══


def test_a_dataset_with_no_arrival_column_refuses_the_default_fetch_stamp(conn, generated):
    """Craneware loaded with no delivery stamp must raise, not quietly date itself to 1970.

    This is the whole guard.  If it fails, ``load_from_sources`` has gone back to accepting
    the epoch for a dataset that publishes no arrival time of its own, and the failure is
    silent by construction: the rows land, they sort to the front of the timeline, they
    resolve against a database where no episode exists yet, and they park as ``NO_KEY_MATCH``
    with nothing anywhere reporting that the date was the cause.

    The message is asserted as well as the type, because an error nobody can act on is barely
    better than the silent bug it replaced.  A caller who has just turned on their first
    vendor source needs to be told *which* source refused and why, and the exception carries
    the source id, the document and the dataset as attributes so a caller can branch on them
    without parsing prose.

    Nothing may land, which is the second half.  The guard runs before the control total is
    reconciled and before ``insert_ingest_batch``, so a refused delivery leaves no batch, no
    raw row and no checkpoint — and the missing checkpoint is what lets a corrected re-run
    resume exactly here rather than skipping the file as already taken.
    """
    mapping, path, _parsed = _export(generated, CRANEWARE_SOURCE_ID)
    assert mapping.received_at_column is None, (
        "the Claims Report now declares an arrival column, so its rows never reach the "
        "fallback and this guard can no longer fire — this test is describing an older file "
        "than the one on disk"
    )

    with pytest.raises(pipeline.MissingDeliveryStampError) as caught:
        pipeline.load_from_sources(conn, [_source(generated, CRANEWARE_SOURCE_ID)])

    error = caught.value
    assert error.source_id == CRANEWARE_SOURCE_ID
    assert error.document == path.name
    assert error.dataset == mapping.dataset
    message = str(error)
    assert CRANEWARE_SOURCE_ID in message, (
        "the message does not name the source that refused, so an operator running several "
        f"vendor sources cannot tell which one to fix: {message!r}"
    )
    assert pipeline.EPOCH_STAMP in message, (
        "the message does not say what the offending value was, which is the one fact that "
        f"turns this from 'something is wrong' into 'pass the delivery time': {message!r}"
    )

    assert _count(conn, "SELECT COUNT(*) AS n FROM ingest_batch") == 0
    assert _count(conn, "SELECT COUNT(*) AS n FROM raw_record") == 0
    assert not checkpoint.load_checkpoints(conn, CRANEWARE_SOURCE_ID), (
        "the refused document was checkpointed as taken, so a corrected re-run would skip it "
        "and the fix would appear to do nothing"
    )


# ═══ 2. the guard is narrow ═══


@pytest.mark.parametrize("source_id", VERITY_SOURCE_IDS)
def test_a_dataset_that_publishes_its_own_arrival_time_still_loads_on_the_default(
    conn, generated, source_id: str
):
    """Verity keeps loading with no ``fetched_at``, because its rows never reach the fallback.

    This is the test that proves the guard discriminates rather than banning the default
    outright, and the distinction is the entire design.  The epoch is harmless to a dataset
    that declares a ``received_at_column`` — every row carries its own stamp and the fallback
    is simply unused — so refusing the default for *all* vendor datasets would break two
    working legs to fix a third.

    If this fails, the check has been widened from "this dataset has no arrival column" to
    "this caller passed no stamp", and every existing Verity caller now raises on a call that
    was always correct.
    """
    mapping, _path, parsed = _export(generated, source_id)
    assert mapping.received_at_column is not None, (
        f"{source_id} no longer declares an arrival column, so it belongs on the other side "
        "of this guard and this test is asserting the opposite of what it should"
    )

    stats = pipeline.load_from_sources(conn, [_source(generated, source_id)])

    assert stats.raw_records == parsed.parsed_record_count, (
        f"{parsed.parsed_record_count} DETAIL rows and {stats.raw_records} raw records landed "
        "on the default fetch stamp"
    )
    landed = {row["received_at"] for row in conn.execute("SELECT received_at FROM raw_record")}
    assert pipeline.EPOCH_STAMP not in landed, (
        f"a {source_id} row landed at the epoch despite the dataset declaring "
        f"{mapping.received_at_column!r}. The declaration has been lost, and the guard that "
        "would have caught it does not apply to this dataset by design."
    )


# ═══ 3. the happy path ═══


def test_craneware_rows_land_at_exactly_the_delivery_stamp_the_caller_passed(conn, generated):
    """A real stamp is inherited verbatim by every row that carries no timestamp of its own.

    The guard is only worth having if the value it demands is actually used.  A guard that
    refused the epoch and then dated the rows to something else — a promoted ``fill_date``,
    the batch's ``loaded_at`` — would pass its own test and reintroduce the defect one column
    over, since ``fill_date`` is when the drug left the shelf and hides every day of TPA lag
    behind it.

    :data:`DELIVERED_AT` is in 2099 precisely so that cannot pass by accident: no generated
    date collides with it, so an equality against it is an equality against the caller's
    argument and nothing else.

    Reversed rows are the one exception and are asserted rather than skipped.  A reversed row
    legitimately carries its own ``reversal_date``, and ``vendors.received_at`` takes the
    later of the stamps a row holds — so those rows sit at the reversal.  Skipping them would
    let a fallback that had quietly started overwriting real timestamps pass unnoticed.
    """
    mapping, _path, parsed = _export(generated, CRANEWARE_SOURCE_ID)
    expected: dict[int, str] = {}
    reversed_rows = 0
    for line_no, row in parsed.rows:
        if vendors.is_reversed(mapping, row):
            # Craneware's spelling; it is a full timestamp despite the name, and the field map
            # is what turns it into ``reversal_received_at``.
            expected[line_no] = row["reversal_date"]
            reversed_rows += 1
        else:
            expected[line_no] = DELIVERED_AT
    assert reversed_rows and reversed_rows < len(expected), (
        "this export has no mix of reversed and un-reversed rows, so one half of the rule "
        "below is untested"
    )
    assert DELIVERED_AT not in {
        row["reversal_date"] for _line_no, row in parsed.rows if row["reversal_date"]
    }, "the sentinel stamp collides with real data, so inheritance cannot be told from a hit"

    pipeline.load_from_sources(
        conn, [_source(generated, CRANEWARE_SOURCE_ID)], fetched_at=DELIVERED_AT
    )

    # Keyed on the file's own line numbers. ``source_line_no`` is the raw layer's 1-based
    # position within the delivery, and the reader yields detail rows in file order, so the
    # two orders are the same one — which is what lets a landed row be checked against the
    # file row it came from rather than against a count of them.
    landed = {
        row["source_line_no"]: row["received_at"]
        for row in conn.execute("SELECT source_line_no, received_at FROM raw_record")
    }
    assert len(landed) == len(expected), (
        f"{len(expected)} DETAIL rows and {len(landed)} raw records"
    )
    for position, (line_no, stamp) in enumerate(sorted(expected.items()), start=1):
        assert landed[position] == stamp, (
            f"line {line_no} landed at {landed[position]!r} rather than {stamp!r}. For an "
            "un-reversed row that means the delivery stamp the caller passed was not the one "
            "the rows inherited — most likely a date column was promoted to an arrival time, "
            "which hides the TPA lag the reconciliation is measuring."
        )


# ═══ 4. the consequence the guard exists to prevent ═══


def test_craneware_rows_reach_episodes_instead_of_parking_when_the_stamp_is_real(
    conn, generated
):
    """The defect itself, measured: with episodes in place and a real stamp, almost nothing parks.

    This is the test that would have caught the original bug, and the only one in the suite
    that asserts a vendor row ever reaches an episode at all.  Everything else about the
    vendor leg — the reader, the mapping, the reversal rule, the adapter — was already correct
    while 26 of 27 parks were an artefact of the delivery date, because every one of those
    layers is observable without a crosswalk hit and none of them changes when the date does.

    The ordering is the experiment.  The six generated feeds are ingested **first**, so 60
    episodes exist and the crosswalk has something to resolve against; only then does the
    Craneware export arrive, dated 2099, which is after everything.  Dated to the epoch it
    would instead sort to the front and be asked to resolve against an empty database — and
    that is exactly what :class:`~recon.ingest.pipeline.MissingDeliveryStampError` now
    refuses, so this test is the guard's reason for existing rather than the guard itself.

    The expectation is a **share**, not a number: at most one row in
    :data:`MAX_PARKED_SHARE` may park.  The rows that legitimately park are the ones whose
    identifiers the generator deliberately drifted, which is a small fraction by design; the
    epoch regime parks four rows in five.  Nothing is hardcoded, so a different spine moves
    the counts and not the claim.
    """
    mapping, _path, parsed = _export(generated, CRANEWARE_SOURCE_ID)

    feed_stats = pipeline.load_feeds(conn, generated.feeds_dir())
    pipeline.ingest(conn, stats=feed_stats)
    episodes = _count(conn, "SELECT COUNT(*) AS n FROM episode")
    assert episodes > 0, (
        "the six feeds created no episodes, so the vendor rows below would have nothing to "
        "resolve against and this test would pass for the wrong reason"
    )

    stats = pipeline.load_from_sources(
        conn, [_source(generated, CRANEWARE_SOURCE_ID)], fetched_at=DELIVERED_AT
    )
    assert stats.raw_records == parsed.parsed_record_count
    pipeline.ingest(conn, stats=stats)

    landed = _batch_rows(conn, CRANEWARE_SOURCE_ID, "normalized_record")
    parked = _batch_rows(conn, CRANEWARE_SOURCE_ID, "parked_record")
    assert landed > 0, "no Craneware record normalized, so there is nothing to park or attach"

    attached = _count(
        conn,
        "SELECT COUNT(DISTINCT c.resolved_from_norm_id) AS n FROM crosswalk_key c"
        "  JOIN normalized_record n ON n.norm_id = c.resolved_from_norm_id"
        "  JOIN raw_record r ON r.raw_id = n.raw_id"
        "  JOIN ingest_batch b ON b.batch_id = r.batch_id"
        " WHERE b.source_id = ? AND c.episode_id IS NOT NULL",
        (CRANEWARE_SOURCE_ID,),
    )
    reasons = {
        row["park_reason"]: row["n"]
        for row in conn.execute(
            "SELECT p.park_reason, COUNT(*) AS n FROM parked_record p"
            "  JOIN raw_record r ON r.raw_id = p.raw_id"
            "  JOIN ingest_batch b ON b.batch_id = r.batch_id"
            " WHERE b.source_id = ? GROUP BY 1",
            (CRANEWARE_SOURCE_ID,),
        )
    }

    assert attached > 0, (
        f"not one of {landed} Craneware records reached an episode. The vendor leg reads the "
        "file, adapts it and lands it, and every one of those steps can be correct while the "
        f"crosswalk resolves nothing — park reasons: {reasons}"
    )
    assert parked * MAX_PARKED_SHARE <= landed, (
        f"{parked} of {landed} Craneware records parked ({reasons}), which is the shape the "
        "epoch produces rather than the shape a real delivery stamp does. The rows that are "
        "supposed to park are the ones whose identifiers the generator drifted on purpose — a "
        "near-total park means they are all resolving against a timeline position where no "
        f"episode exists yet. Delivery stamp used: {DELIVERED_AT!r}."
    )
    assert attached * MAX_PARKED_SHARE >= landed * (MAX_PARKED_SHARE - 1), (
        f"only {attached} of {landed} Craneware records resolved to an episode while {parked} "
        "parked. The two do not have to add up — a record can resolve to a remittance and "
        "never reach an episode — so a shortfall here is records that neither attached nor "
        "were held, which is the one outcome the park queue exists to make impossible."
    )
    assert stats.quarantined == 0, (
        "a Craneware row was refused outright, which is a different defect from a park and "
        "would make the counts above meaningless"
    )


# ═══ 5. the guard checks the outcome, not the caller's intent ═══


def test_passing_the_epoch_by_hand_is_refused_exactly_as_omitting_it_is(conn, generated):
    """A stamp that only looks deliberate is still 1970, and the guard must not be fooled.

    The distinction matters because the two calls are indistinguishable downstream: a caller
    who writes ``fetched_at=EPOCH_STAMP`` — or reads a delivery manifest that came back empty
    and fell back to a constant — produces exactly the rows a caller who passed nothing does.
    If the guard were implemented as "was an argument supplied", it would check the caller's
    intent rather than the outcome, and the whole defect would be one keyword argument away
    from returning with nothing raised.

    The value is taken from :data:`~recon.ingest.pipeline.EPOCH_STAMP` rather than retyped, so
    a build that moves the constant cannot leave this test passing against a string nothing
    uses any more.
    """
    with pytest.raises(pipeline.MissingDeliveryStampError) as caught:
        pipeline.load_from_sources(
            conn,
            [_source(generated, CRANEWARE_SOURCE_ID)],
            fetched_at=pipeline.EPOCH_STAMP,
        )
    assert caught.value.source_id == CRANEWARE_SOURCE_ID
    assert _count(conn, "SELECT COUNT(*) AS n FROM raw_record") == 0
