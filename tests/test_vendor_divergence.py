"""Verity and Craneware must be *able* to disagree, and the engine must catch it when they do.

Until this existed the vendor layer could not produce the one failure a reconciliation engine
exists for.  Both exports are formatted from a single ``VendorSource``, so they reported the
same qualification, for the same dispense, with the same identifiers, always — and "the two
vendors agree" was a property of the generator rather than a finding about the data.

The generator already solves this exact problem for defect D-6: identifier drift is applied to
exactly one feed per episode, decided centrally and rendered per consumer, "never by letting
generators drift independently, which would make the defect unreproducible".  Vendor
divergence follows that pattern to the letter, and has to: ``recon.mocks`` is forbidden from
importing ``recon.generators``, so a formatter could not decide this even if it wanted to.

Decide in ``_defect_directives`` → freeze onto ``EpisodePlan`` → render into a **new** sidecar
column → each formatter reads its own.  The column is new rather than diverted because
``mocks.source.load_source`` joins TPA events onto the sidecar on five fields including
``rx_number``; changing that one would not produce a disagreement, it would unjoin the
dispense and hand both vendors a row with no events at all.

The second test here is a regression from building this, and it is the more valuable of the
two.
"""

from __future__ import annotations

import csv
import io
import sqlite3
from pathlib import Path

import pytest

from recon.api.app import build_dataset
from recon.config import CuratedSpine, load_settings
from recon.connectors import vendors
from recon.generators import orchestrator
from recon.mocks import coverage
from recon.mocks import source as source_module


@pytest.fixture(scope="module")
def generated(tmp_path_factory):
    """One generation, kept: the plans, the source object and the written exports."""
    data_dir = tmp_path_factory.mktemp("vendor-divergence")
    settings = load_settings(
        "demo",
        data_dir=data_dir,
        db_path=data_dir / "recon.sqlite",
        curated_spine=CuratedSpine.RECORDED,
    )
    generation = orchestrator.generate(settings)
    orchestrator.write_outputs(settings, generation)
    coverage.write_all(settings)
    return settings, generation, source_module.load_source(settings)


def _diverged(source) -> list:
    return [
        dispense
        for dispense in source.dispenses
        if dispense.rx_number and dispense.rx_number != dispense.rx_number_craneware
    ]


def test_the_two_vendors_can_disagree_about_one_dispense(generated) -> None:
    """The capability itself, asserted on the files rather than on the plan.

    Read out of the exports the formatters actually wrote, because the whole point is a
    disagreement a *connector* could encounter.  A divergence visible only on an in-memory
    plan would prove the generator changed and nothing else.
    """
    settings, _generation, source = generated
    diverged = _diverged(source)
    assert diverged, (
        "no dispense is spelled differently by the two vendors, so the vendor layer still "
        "cannot produce a disagreement and this whole file is vacuous"
    )

    verity_path = next(
        (settings.vendor_dir() / "verity").glob(
            vendors.mapping_for("verity_accumulations").filename_prefix + "*"
        )
    )
    craneware_path = (
        settings.vendor_dir()
        / "craneware"
        / vendors.mapping_for("craneware_claims_report").filename_prefix
    )

    def rx_values(path: Path) -> set[str]:
        return {
            row["rx_number"]
            for row in csv.DictReader(io.StringIO(path.read_text(encoding="utf-8"), newline=""))
            if row.get("record_type") == "DETAIL" and row.get("rx_number")
        }

    verity_rx, craneware_rx = rx_values(verity_path), rx_values(craneware_path)
    for dispense in diverged:
        assert dispense.rx_number in verity_rx, (
            f"{dispense.rx_number} is Verity's spelling and is not in Verity's export"
        )
        assert dispense.rx_number_craneware in craneware_rx, (
            f"{dispense.rx_number_craneware} is Craneware's spelling and is not in its report"
        )
        assert dispense.rx_number not in craneware_rx, (
            f"{dispense.rx_number} appears in BOTH exports, so they do not actually disagree"
        )


def test_craneware_never_repairs_a_drift_the_feed_already_has(generated) -> None:
    """The regression this work produced, and the one worth keeping.

    The first version defaulted ``rx_rendering_craneware`` to ``CANONICAL``.  That reads as
    the safe default and is the opposite: where D-6 had already drifted the feed's Rx, Verity
    reported the drifted spelling and Craneware reported the *correct* one — ``22105567``
    against ``221055677`` on the demo profile.

    A mock that repairs a defect is worse than one that adds a wrong value, because it hands
    the connector a join the real feed does not have.  The D-6 crosswalk miss would simply
    vanish through Craneware's door, the episode would resolve, and every count downstream
    would agree with itself.  ``mocks/source.py`` already warned about exactly this — the
    sidecar stores the key "spelled the way the TPA feed spells it, drift included".

    Craneware reports what it was sent.  It diverges only when the generator says so.
    """
    _settings, generation, _source = generated
    drifted = [plan for plan in generation.plans if plan.rx_rendering_tpa != "CANONICAL"]
    assert drifted, "no episode carries a drifted TPA rendering, so this proves nothing"

    repaired = [
        plan
        for plan in drifted
        if plan.rx_rendering_craneware != plan.rx_rendering_tpa
    ]
    assert not repaired, (
        "Craneware renders a different Rx from the feed on an episode whose Rx had already "
        f"drifted: {[(p.episode_id, p.rx_rendering_tpa, p.rx_rendering_craneware) for p in repaired[:3]]}. "
        "If the difference is a repair, the D-6 miss disappears through this vendor's door "
        "and the crosswalk silently gets a join the real feed does not have."
    )


def test_the_divergence_is_decided_by_the_generator_and_not_by_a_formatter(generated) -> None:
    """Where the decision lives, asserted structurally.

    ``recon.mocks`` may not import ``recon.generators`` — a separate test enforces that — so a
    formatter physically cannot make this call.  What it *could* do is invent one locally, and
    that is what this rules out: the two spellings both come off the plan, so the same seed
    produces the same disagreement on the same episodes every time.
    """
    _settings, generation, source = generated
    planned = {
        plan.episode_id
        for plan in generation.plans
        if plan.rx_rendering_craneware != plan.rx_rendering_tpa
    }
    assert len(planned) == len(_diverged(source)), (
        f"{len(planned)} episodes were planned to diverge but {len(_diverged(source))} "
        "dispenses actually do. A formatter is diverging on its own, or a planned divergence "
        "is not reaching the sidecar."
    )


def test_the_engine_catches_the_disagreement_through_the_craneware_door(
    tmp_path_factory,
) -> None:
    """The payoff: a divergence has to be *detectable*, not merely present in a file.

    The same dispense, ingested through each vendor in turn.  Through Verity its Rx is the one
    the rest of the system knows and it resolves to its episode.  Through Craneware the Rx is
    spelled differently, matches no published key, and parks — which is precisely D-6, the
    crosswalk miss, arriving from a vendor rather than from a feed.

    Asserted as *more parks through Craneware than through Verity for the same dispense*
    rather than as a total, because the two doors already differ in how many rows they carry
    and a raw count would be measuring that instead.
    """
    root = tmp_path_factory.mktemp("divergence-engine")
    parked_rx: dict[str, set[str]] = {}
    for mode in ("verity", "craneware"):
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
        parked_rx[mode] = {
            row[0]
            for row in conn.execute(
                "SELECT n.rx_number FROM parked_record p"
                "  JOIN normalized_record n ON n.norm_id = p.norm_id"
                " WHERE p.park_reason = 'NO_KEY_MATCH' AND n.rx_number IS NOT NULL"
            )
        }
        if mode == "craneware":
            source = source_module.load_source(settings)
            diverged = _diverged(source)
        conn.close()

    assert diverged, "nothing diverged in this build, so there is nothing to detect"
    craneware_spellings = {dispense.rx_number_craneware for dispense in diverged}
    caught = craneware_spellings & parked_rx["craneware"]
    assert caught, (
        "a dispense Craneware spells differently from everyone else resolved anyway. Either "
        f"the divergence is not reaching ingest, or a key is matching that should not: "
        f"craneware spellings {sorted(craneware_spellings)}, parked {sorted(parked_rx['craneware'])[:6]}"
    )
    assert not (caught & parked_rx["verity"]), (
        "the same Rx parks through both doors, so the park is not caused by the disagreement"
    )
