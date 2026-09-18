"""A vendor directory must describe the run that is actually on disk, and only that one.

Verity is the only writer in :mod:`recon.mocks` whose filenames come from the data rather
than from a constant — ``DOC2-010`` asks an export's name to retain the vendor, the export
type and the generated timestamp, and :func:`~recon.mocks.verity_export.generated_stamp`
obliges by deriving the stamp from the latest ``received_at`` in the source.  That is the
right call and it buys reproducibility: identical data lands an identical name, so a
connector's file-level idempotency is testable at all.

The corollary was never written down.  **Different data lands a different name, and the
previous name is not overwritten.**  Craneware and Beacon are immune because they write to
constants; Verity accumulates, silently, one generation per run.

This file exists because that actually happened.  Three runs of the demo profile left three
generations side by side — ``20260314T080000Z``, ``20260409T130000Z`` and
``20260417T080000Z`` — and only the last described the feeds beside it.  Of the 37 rebate
allocation codes in the two older invoice exports, zero appeared in
``tpa_340b_events.jsonl``.

What that costs is a wrong answer rather than an untidy directory, and the last test here is
the one that says so.  A reader cannot know a data-derived name in advance, so it matches
``SecureFileMapping.filename_prefix`` — and all three files matched ``verity_invoices_``.
Take the first and you read the oldest run; take them all and you ingest 37 rebate lines for
batches that never existed, which park as unmatched and are indistinguishable from a
crosswalk defect.  One superseded file did not clutter, it manufactured a finding: the
measurement that sent this work looking for batch/line semantics — ``RBT-20251031-44202``'s
lines summing to 26,500.00 against a declared 30,094.00 — was read out of a stale export, and
in the live one every batch's lines sum to its declared total exactly.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from recon.config import load_settings
from recon.generators import orchestrator
from recon.mocks import coverage, verity_export
from recon.mocks import source as source_module

#: Two seeds, because one seed cannot reproduce the defect.  The stamp follows the data, so
#: only a genuinely different dataset lands a genuinely different filename — which is the
#: whole mechanism under test.
_SEED_A = 20260314
_SEED_B = 20260409


def _generate(data_dir: Path, *, master_seed: int):
    """One full generation into ``data_dir``, returning its settings and source."""
    settings = load_settings("demo", data_dir=data_dir, master_seed=master_seed)
    orchestrator.write_outputs(settings, orchestrator.generate(settings))
    return settings, source_module.load_source(settings)


def _names(directory: Path, dataset: str) -> list[str]:
    """Every export in ``directory`` a prefix-matching reader would accept for ``dataset``."""
    prefix = f"{verity_export.VENDOR_NAME}_{dataset}_"
    return sorted(path.name for path in directory.glob("*.csv") if path.name.startswith(prefix))


@pytest.fixture(scope="module")
def two_generations(tmp_path_factory) -> Path:
    """One data directory that has been generated into twice, with different data each time.

    Exactly the sequence that left three generations in the repository's own
    ``data/generated/demo/vendor/verity/``, minus one run.
    """
    data_dir = tmp_path_factory.mktemp("verity-supersession")
    settings_a, _ = _generate(data_dir, master_seed=_SEED_A)
    coverage.write_all(settings_a)
    settings_b, _ = _generate(data_dir, master_seed=_SEED_B)
    coverage.write_all(settings_b)
    return settings_b.vendor_dir() / verity_export.VENDOR_NAME


def test_a_second_generation_leaves_exactly_one_export_per_dataset(two_generations: Path) -> None:
    """The property a prefix-matching reader depends on, asserted directly.

    ``SecureFileMapping.filename_prefix`` identifies a dataset, not a delivery.  It can only
    identify a file too if there is exactly one — and before the sweep existed there were
    three, so the prefix resolved to whichever the caller's sort happened to reach first.
    """
    for dataset in verity_export.DATASETS:
        found = _names(two_generations, dataset)
        assert len(found) == 1, (
            f"{dataset!r} matches {len(found)} exports, so a reader matching the prefix "
            f"{verity_export.VENDOR_NAME}_{dataset}_ cannot tell which run it is reading: {found}"
        )


def test_the_superseded_stamp_is_gone_rather_than_merely_outranked(
    tmp_path_factory,
) -> None:
    """Removed, not shadowed — and named, so the caller can say what it dropped."""
    data_dir = tmp_path_factory.mktemp("verity-superseded-stamp")
    settings_a, source_a = _generate(data_dir, master_seed=_SEED_A)
    first = verity_export.write(settings_a, source_a)
    stamp_a = verity_export.generated_stamp(source_a)

    settings_b, source_b = _generate(data_dir, master_seed=_SEED_B)
    stamp_b = verity_export.generated_stamp(source_b)
    assert stamp_a != stamp_b, (
        "the two seeds produced data with the same latest received_at, so this test is not "
        "exercising supersession at all -- pick two seeds whose windows differ"
    )

    verity_export.write(settings_b, source_b)
    directory = settings_b.vendor_dir() / verity_export.VENDOR_NAME
    survivors = sorted(path.name for path in directory.glob("*.csv"))
    for path in first.values():
        assert path.name not in survivors, (
            f"{path.name} is a run that no longer happened and is still readable; every "
            "prefix-matching reader in the fabric will offer it as a candidate"
        )
    assert all(stamp_b in name for name in survivors), survivors


def test_rewriting_identical_data_removes_nothing(tmp_path_factory) -> None:
    """Reproducibility is the reason the stamp is data-derived, and it must survive the sweep.

    The same source written twice must land the same names with the same bytes and drop
    nothing — otherwise the sweep would have traded an accumulating directory for a
    directory that churns, and the connector's file-level idempotency (which keys on a
    content hash) would have nothing stable to key on.
    """
    data_dir = tmp_path_factory.mktemp("verity-idempotent")
    settings, source = _generate(data_dir, master_seed=_SEED_A)

    first = verity_export.write(settings, source)
    before = {path.name: path.read_bytes() for path in first.values()}
    second = verity_export.write(settings, source)
    after = {path.name: path.read_bytes() for path in second.values()}

    assert before == after
    directory = settings.vendor_dir() / verity_export.VENDOR_NAME
    assert sorted(path.name for path in directory.glob("*.csv")) == sorted(before)


def test_the_sweep_reaches_only_the_names_this_module_writes(tmp_path_factory) -> None:
    """Scoped to ``verity_<dataset>_*.csv``, so nothing else in the directory is at risk.

    A vendor drop directory is not ours alone — a real one holds whatever the vendor and the
    operator put there.  A sweep that deleted by directory rather than by name would be a
    generator with the power to delete a file nobody generated.
    """
    data_dir = tmp_path_factory.mktemp("verity-scope")
    settings, source = _generate(data_dir, master_seed=_SEED_A)
    verity_export.write(settings, source)

    directory = settings.vendor_dir() / verity_export.VENDOR_NAME
    bystanders = {
        # Not a dataset this module knows, though it starts with the vendor name.
        "verity_manifest_20260101T000000Z.csv": "kept\n",
        # The right dataset, the wrong extension: not an export.
        "verity_invoices_20260101T000000Z.csv.bak": "kept\n",
        # Another vendor entirely.
        "craneware_claims_report.csv": "kept\n",
        "README.md": "kept\n",
    }
    for name, text in bystanders.items():
        (directory / name).write_text(text, encoding="utf-8")

    settings_b, source_b = _generate(data_dir, master_seed=_SEED_B)
    verity_export.write(settings_b, source_b)

    for name in bystanders:
        assert (directory / name).exists(), f"the sweep deleted {name}, which it did not write"


def test_no_export_describes_a_rebate_batch_the_feeds_do_not_have(two_generations: Path) -> None:
    """The harm, stated as harm rather than as a file count.

    This is the assertion that would have failed in the repository before the sweep existed,
    and it fails for the reason that matters: an invoice line names the batch that paid it,
    and a line naming a batch no feed ever announced is a rebate payment against a payment
    that did not happen.  Ingested, it resolves to nothing and parks — arriving in the
    exception queue as an unmatched rebate, which is a real defect class (D-4), so it is
    read as a finding about the data rather than as dirt on the disk.
    """
    feeds = two_generations.parent.parent / "feeds" / "tpa_340b_events.jsonl"
    announced = {
        event["allocation_code"]
        for event in map(json.loads, feeds.read_text(encoding="utf-8").splitlines())
        if event.get("event_type") == "REBATE_PAYMENT_BATCH"
    }
    assert announced, "no rebate batches in the feed; this test would pass vacuously"

    # Every invoices export a prefix-matching reader would accept, not just one of them.
    # Unpacking a single file here would fail on the file *count* when the sweep is absent,
    # which is the mechanism rather than the harm -- and the harm is the point, because it is
    # what a reader would see if it ingested every match instead of the first.
    orphans: dict[str, list[str]] = {}
    for name in _names(two_generations, "invoices"):
        rows = csv.DictReader((two_generations / name).read_text(encoding="utf-8").splitlines())
        unannounced = sorted(
            {
                row["rebate_allocation_code"]
                for row in rows
                if row.get("record_type") == "DETAIL"
                and row["rebate_allocation_code"] not in announced
            }
        )
        if unannounced:
            orphans[name] = unannounced

    assert not orphans, (
        "invoice lines bill against rebate batches the feeds never announced: "
        + "; ".join(f"{name} has {len(codes)} ({codes[:3]})" for name, codes in orphans.items())
        + ". Every one resolves to nothing and parks as an unmatched rebate (D-4), which "
        "reads as a crosswalk failure rather than as a superseded file."
    )
