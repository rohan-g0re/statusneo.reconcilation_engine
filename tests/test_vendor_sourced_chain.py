"""The rest of the chain on vendor-sourced data: crosswalk, cash, queues, reopening.

``tests/test_vendor_sourced_parity.py`` accounts for the verdicts.  Rows landing and verdicts
reconciling is not the same as the system behaving, and this file covers the four stages
between them that a row count cannot see.

Three of the four are quiet and that is the finding: **cash allocation, quarantine and the
verdict log are identical whichever TPA is configured.**  Allocation runs off the
manufacturer's ``REBATE_BATCH`` and the payers' remittances, none of which a TPA export
touches, so a difference there would have meant the switch reached somewhere it had no
business reaching.

The fourth is not quiet, and it is the one the plan predicted would bite.

═══ Arrival order moves, and reopening moves with it ═══════════════════════════════

The engine evaluates at a cursor, so *when* a record arrives decides what a verdict could
have known.  Each source dates its rows differently:

* the generic feed carries a per-event ``received_at`` — 33 distinct stamps on the demo
  profile, spread across the whole window;
* ``verity_accumulations`` carries ``qualification_received_at``, its own per-row stamp — 41
  distinct, a comparable spread;
* **``craneware_claims_report`` carries no arrival column at all.**  Its rows inherit the
  delivery stamp, so a whole report lands at one instant: **4 distinct stamps**, one per drop.

So through the Craneware door a dispense's qualification does not arrive when the TPA decided
it, it arrives when the next report was cut — weeks later, in a lump with everything else in
that report.  Measured consequence on the demo profile: Craneware reopens **64** previously
closed verdicts against the generic feed's 34, and it reopens a **different set of
episodes** — four the generic feed never reopens at all.

That is not a defect in the connector.  It is a true fact about what Craneware ships, and the
reason it belongs in a test is that nothing else in the suite would have noticed: the rows
parse, the contract passes, the control total agrees, the crosswalk resolves, the final
exception count is the same 25 either way.  The only visible trace is *when* each verdict was
reached and how many times it had to be revised.

Verity behaves differently and the asymmetry is the point: its reopened episodes are a strict
**subset** of the generic feed's.  It carries its own arrival times, so it moves no verdict
earlier or later than the fact justified; it simply carries fewer facts.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from recon.api.app import GENERIC_TPA_SOURCE, build_dataset
from recon.config import CuratedSpine, load_settings

MODES = (GENERIC_TPA_SOURCE, "verity", "craneware")

#: The kinds that arrive through whichever TPA door is configured, and therefore the ones
#: whose arrival spread differs by vendor.  Named rather than "everything the vendor sent",
#: because the point is the comparison against the generic feed's equivalents.
_TPA_KINDS = ("TPA_QUALIFICATION", "TPA_REVERSAL", "TPA_INVOICE_LINE", "TPA_REBATE_REQUEST")


@pytest.fixture(scope="module")
def dbs(tmp_path_factory) -> dict[str, sqlite3.Connection]:
    root = tmp_path_factory.mktemp("tpa-source-chain")
    made: dict[str, sqlite3.Connection] = {}
    for mode in MODES:
        data_dir = root / mode
        settings = load_settings(
            "demo",
            data_dir=data_dir,
            db_path=data_dir / "recon.sqlite",
            curated_spine=CuratedSpine.RECORDED,
        )
        build_dataset(settings, rebuild=True, tpa_source=mode)
        conn = sqlite3.connect(settings.db_path)
        conn.row_factory = sqlite3.Row
        made[mode] = conn
    yield made
    for conn in made.values():
        conn.close()


def _one(conn: sqlite3.Connection, sql: str, *args):
    return conn.execute(sql, args).fetchone()[0]


def _reopened_episodes(conn: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in conn.execute(
            "SELECT DISTINCT episode_id FROM verdict WHERE reopened_from IS NOT NULL"
        )
    }


# ═══ the three stages the switch must not reach ═══


def test_cash_allocation_is_identical_whichever_tpa_is_configured(dbs) -> None:
    """The bank leg is the manufacturer's and the payers', and no TPA export touches it.

    This is the assertion that would cost money if it failed: a build whose allocated cash
    depended on which TPA was configured would be reconciling against a different ledger per
    vendor, and every variance downstream would be an artefact of configuration.

    Asserted on the totals and not only the row count, because the same number of allocation
    rows carrying different amounts is the failure that a count would pass.
    """
    figures = {
        mode: (
            _one(conn, "SELECT COUNT(*) FROM cash_allocation"),
            _one(conn, "SELECT COALESCE(SUM(allocated_cents), 0) FROM cash_allocation"),
            _one(
                conn,
                "SELECT COALESCE(SUM(a.allocated_cents), 0) FROM cash_allocation a"
                "  JOIN normalized_record n ON n.norm_id = a.remittance_norm_id"
                " WHERE n.record_kind = 'REBATE_BATCH'",
            ),
        )
        for mode, conn in dbs.items()
    }
    assert len(set(figures.values())) == 1, f"allocation depends on the TPA source: {figures}"
    rows, total, rebate = figures[GENERIC_TPA_SOURCE]
    assert rows > 0 and total > 0 and rebate > 0, "nothing was allocated; the test is vacuous"


def test_no_vendor_door_quarantines_anything(dbs) -> None:
    """Every row either becomes a record or is deliberately refused, and none is refused.

    Worth its own test because a quarantine is how this leg has failed before: nine Craneware
    rows on SOURCE_AUTHORITY_BREACH when the adapter let a TPA author ``manufacturer_status``,
    and 35 more on a bad delivery stamp. Both looked like a slightly smaller ingest.
    """
    for mode, conn in dbs.items():
        reasons = dict(
            conn.execute("SELECT reason_code, COUNT(*) FROM quarantined_record GROUP BY 1")
        )
        assert not reasons, f"{mode} quarantined rows: {reasons}"


def test_the_crosswalk_resolves_no_worse_through_a_vendor_door(dbs) -> None:
    """Parks are accounted for rather than merely counted.

    ``NO_KEYS_PRESENT`` must be identical: it means a record carried no key at all, which is a
    property of the records the *other* feeds supply and nothing a TPA source can change.

    ``NO_KEY_MATCH`` is allowed to fall and not to rise. It is the D-6 crosswalk miss, and a
    vendor export carries fewer TPA rows than the generic feed, so fewer rows can miss. A rise
    would mean vendor rows are failing to resolve to dispenses the generic feed reached --
    which is the spelling failure this leg exists to catch (``ndc_11`` against ``ndc11``) and
    would be silent in every other measure.
    """
    baseline = dict(
        dbs[GENERIC_TPA_SOURCE].execute(
            "SELECT park_reason, COUNT(*) FROM parked_record GROUP BY 1"
        )
    )
    assert baseline.get("NO_KEYS_PRESENT"), "no keyless parks in the baseline; test is vacuous"

    for mode in ("verity", "craneware"):
        parks = dict(
            dbs[mode].execute("SELECT park_reason, COUNT(*) FROM parked_record GROUP BY 1")
        )
        assert parks.get("NO_KEYS_PRESENT") == baseline.get("NO_KEYS_PRESENT"), (
            f"{mode} changed how many records arrived with no key at all: "
            f"{parks.get('NO_KEYS_PRESENT')} against {baseline.get('NO_KEYS_PRESENT')}"
        )
        assert parks.get("NO_KEY_MATCH", 0) <= baseline.get("NO_KEY_MATCH", 0), (
            f"{mode} parks MORE records as unmatched than the generic feed "
            f"({parks.get('NO_KEY_MATCH')} against {baseline.get('NO_KEY_MATCH')}). Vendor "
            "rows are failing to reach dispenses the generic feed reached, which is what a "
            "wrong column spelling looks like from here"
        )
        assert "AMBIGUOUS_KEY_MATCH" not in parks, (
            f"{mode} produced an ambiguous match; a vendor row is resolving to two episodes"
        )


# ═══ the stage that does move ═══


def test_craneware_lands_a_whole_report_at_one_instant_and_verity_does_not(dbs) -> None:
    """The mechanism behind the reopening difference, measured before its consequence.

    Craneware's Claims Report declares no arrival column, so ``vendors.received_at`` falls
    back to the delivery stamp and every row in one report shares it. Verity stamps each
    accumulation with ``qualification_received_at``.

    The assertion is on the *spread* rather than on a count of reports, because that is what
    the engine is sensitive to: a cursor asks what was known by a moment, and a source that
    answers in four lumps tells it something different from one that answers continuously.
    """
    placeholders = ",".join("?" * len(_TPA_KINDS))
    spreads = {}
    for mode, conn in dbs.items():
        stamps, records = conn.execute(
            f"SELECT COUNT(DISTINCT received_at), COUNT(*) FROM normalized_record"
            f" WHERE record_kind IN ({placeholders})",
            _TPA_KINDS,
        ).fetchone()
        spreads[mode] = (stamps, records, stamps / records if records else 0.0)

    # Distinct stamps PER RECORD, not the raw count. The raw counts are not comparable across
    # modes and the first version of this test compared them anyway: the generic feed also
    # carries TPA_REBATE_REQUEST, which no vendor door produces, so it holds more stamps for
    # having more kinds rather than for dating them better. The ratio asks the question that
    # actually matters -- does a row carry its own time, or does it share one?
    assert spreads["craneware"][2] < 0.2, (
        f"Craneware's rows no longer arrive in lumps: {spreads}. If its report gained a "
        "per-row arrival column this is good news and the reopening test below should be "
        "revisited -- but check the delivery-stamp fallback has not silently been replaced "
        "by something plausible, which is the defect that once dated 35 rows to 1970"
    )
    assert spreads["verity"][2] > 0.5, (
        f"Verity's arrival stamps have stopped being per-row: {spreads}. Its accumulations "
        "carry qualification_received_at, so most rows should hold a distinct moment"
    )
    assert spreads["craneware"][1] > spreads["craneware"][0] * 3, (
        f"Craneware is not actually lumping many rows into few moments: {spreads}"
    )


def test_reopening_follows_arrival_order_and_so_differs_by_vendor(dbs) -> None:
    """The consequence, pinned as a finding rather than smoothed into a parity claim.

    Reopening is a verdict moving backwards — CLOSED or PENDING becoming something else when
    later evidence lands. It is driven entirely by *when* records arrive, so a source that
    changes arrival order changes it, and nothing about the content has to differ.

    Verity's reopened episodes are a strict subset of the generic feed's: it carries its own
    arrival times, so it moves nothing earlier or later than the fact justified and simply
    carries fewer facts.

    Craneware reopens episodes the generic feed never reopens, because a dispense qualified in
    August arrives in the report cut in November alongside everything else. That is a true
    statement about the vendor rather than a bug, and it is exactly the kind of thing that is
    invisible everywhere else: the final exception count is the same either way.
    """
    baseline = _reopened_episodes(dbs[GENERIC_TPA_SOURCE])
    assert baseline, "nothing reopens in the baseline, so this proves nothing"

    verity = _reopened_episodes(dbs["verity"])
    assert verity <= baseline, (
        "Verity reopened episodes the generic feed did not: "
        f"{sorted(verity - baseline)[:5]}. Its rows carry their own arrival stamps, so a new "
        "reopening means arrival order shifted for a reason other than the data"
    )

    craneware = _reopened_episodes(dbs["craneware"])
    introduced = sorted(craneware - baseline)
    assert introduced, (
        "Craneware no longer introduces reopenings. If its rows gained per-row arrival times "
        "that is the explanation and this file's central finding has been fixed rather than "
        "broken -- confirm against the arrival-spread test above before relaxing this."
    )
