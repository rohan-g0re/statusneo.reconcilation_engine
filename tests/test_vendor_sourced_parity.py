"""The engine on vendor-shaped data, and an accounting of every verdict that moves.

In production there is no generic TPA feed.  There is Verity's export, or Craneware's, or a
sixth TPA's.  ``build_dataset(tpa_source=...)`` runs the engine the second way, and this file
is the measurement that says what that costs.

**A matching digest would be the suspicious outcome here, not the reassuring one.**  The
Beacon leg could claim "nothing moved" because it added evidence; this *replaces* evidence,
and a vendor ships less than a feed we invented for ourselves.  So the deliverable is a
reconciliation: every episode whose verdict changes has to fall into a bucket with a named
field behind it, and an episode that changes for no nameable reason is the finding.

Two buckets account for all of it, and both are facts about what a vendor's export can say.

**1. No TPA export carries the rebate request, so the track stops at C-03.**
``_derive_rebate`` reads ``request = "SUBMITTED" if evidence.tpa_requests else
"NOT_SUBMITTED"`` and returns immediately on ``NOT_SUBMITTED``, which short-circuits
manufacturer status, payment and cash in one step.  The generic feed carries 33
``REBATE_REQUEST`` events; neither ``verity_accumulations`` nor ``craneware_claims_report``
has a column for "we asked the manufacturer", because asking is not something a TPA's
qualification export reports.

That is a gap with a known filler rather than a dead end, and the fourth test measures it:
**every episode holding a rebate request also holds a Beacon acknowledgement**, and Beacon
reaches more besides.  The acknowledgement is the submission receipt — DOC2-004 makes Beacon
authoritative for the rebate submission identifier — so in the inverted world the "we
submitted" fact belongs to Beacon and not to the TPA's word for it.  Closing it moves
verdicts, so it is a separate measured step, exactly as ``beacon_rebate_status`` was.

**2. Verity's accumulations cannot express a disqualification.**  The dataset is the
dispenses that accumulated, and ``connectors/vendors/verity.py`` says the population is
selected on ``qualification_status`` — it "can only be ``QUALIFIED`` here".  So a
``NOT_QUALIFIED`` decision has nowhere to sit, and those episodes lose the 340B track
entirely rather than recording that it was refused.  Craneware's Claims Report is a report of
claims rather than of accumulations, so a non-qualifying row has a home in it and the
disqualifications survive.

This is the single sharpest argument in the file for why one export is not interchangeable
with another, and it is invisible at the row level: both vendors' files parse, contract-check
and ingest perfectly.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from pathlib import Path

import pytest

from recon.api.app import GENERIC_TPA_SOURCE, build_dataset
from recon.config import CuratedSpine, load_settings
from recon.domain.enums import RecordKind, SourceSystem

MODES = (GENERIC_TPA_SOURCE, "verity", "craneware")

#: The rebate verdict a qualified dispense reaches when nothing says it was ever submitted.
#: Read from the state space rather than asserted as a magic string -- see the first test.
NOT_SUBMITTED_CODE = "C-03"

#: The rebate verdict meaning "no 340B track at all", which is what a dispense reaches when
#: its qualification decision is missing entirely rather than negative.
TRACK_ABSENT_CODE = "C-00"


class Built:
    """One finished build, queried rather than re-derived."""

    def __init__(self, path: Path) -> None:
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row

    def latest_verdicts(self) -> dict[str, sqlite3.Row]:
        return {
            row["episode_id"]: row
            for row in self._conn.execute(
                "SELECT * FROM verdict v WHERE verdict_id ="
                " (SELECT MAX(verdict_id) FROM verdict w WHERE w.episode_id = v.episode_id)"
            )
        }

    def kind_counts(self) -> Counter:
        return Counter(
            dict(
                self._conn.execute(
                    "SELECT record_kind, COUNT(*) FROM normalized_record GROUP BY 1"
                )
            )
        )

    def raw_counts_by_system(self) -> Counter:
        return Counter(
            dict(
                self._conn.execute(
                    "SELECT source_system, COUNT(*) FROM raw_record GROUP BY 1"
                )
            )
        )

    def episodes_reached_by(self, kind: RecordKind) -> set[str]:
        return {
            row[0]
            for row in self._conn.execute(
                "SELECT DISTINCT k.episode_id FROM crosswalk_key k"
                "  JOIN normalized_record n ON n.norm_id = k.resolved_from_norm_id"
                " WHERE n.record_kind = ?",
                (str(kind),),
            )
        }

    def qualification_statuses(self) -> Counter:
        counts: Counter = Counter()
        for row in self._conn.execute(
            "SELECT canonical FROM normalized_record WHERE record_kind = ?",
            (str(RecordKind.TPA_QUALIFICATION),),
        ):
            counts[json.loads(row[0]).get("qualification_status")] += 1
        return counts

    def close(self) -> None:
        self._conn.close()


@pytest.fixture(scope="module")
def builds(tmp_path_factory) -> dict[str, Built]:
    """One full build per TPA source, from one generation each, kept for the module."""
    root = tmp_path_factory.mktemp("tpa-source-parity")
    made: dict[str, Built] = {}
    for mode in MODES:
        data_dir = root / mode
        settings = load_settings(
            "demo",
            data_dir=data_dir,
            db_path=data_dir / "recon.sqlite",
            curated_spine=CuratedSpine.RECORDED,
        )
        build_dataset(settings, rebuild=True, tpa_source=mode)
        made[mode] = Built(settings.db_path)
    yield made
    for built in made.values():
        built.close()


# ═══ what switching the source actually swaps ═══


def test_the_generic_feed_carries_two_authorities_and_only_one_is_the_tpa_s(builds) -> None:
    """``tpa_340b_events.jsonl`` is not one feed, and that is why inversion is possible.

    Every row declares its own author, and the adapter has always honoured that —
    ``_adapt_tpa`` reads ``payload["source_system"]`` rather than the file's.  A vendor export
    replaces what the *TPA* said and cannot replace what the manufacturer said, because
    DOC2-004 puts rebate payment outside a TPA's authority entirely.
    """
    generic = builds[GENERIC_TPA_SOURCE].raw_counts_by_system()
    assert generic[str(SourceSystem.TPA_PORTAL)] > 0
    assert generic[str(SourceSystem.MANUFACTURER_REBATE)] > 0, (
        "the generic feed carries no manufacturer-authored rows, so this file's whole premise "
        "-- that switching source swaps some rows and not others -- is untestable"
    )

    for mode in ("verity", "craneware"):
        swapped = builds[mode].raw_counts_by_system()
        assert swapped[str(SourceSystem.TPA_PORTAL)] == 0, (
            f"{mode} mode still ingested the generic feed's TPA rows, so every dispense now "
            "has its qualification through two doors"
        )
        assert (
            swapped[str(SourceSystem.MANUFACTURER_REBATE)]
            == generic[str(SourceSystem.MANUFACTURER_REBATE)]
        ), (
            "switching the TPA source changed how many manufacturer rows landed. Those are "
            "not the TPA's to replace -- a vendor export carrying them would be refused by "
            "authority.py -- so this means the exclusion is filtering by file, not by author"
        )


def test_the_manufacturer_s_rebate_money_is_untouched_by_the_switch(builds) -> None:
    """The rebate lines are identical in all three modes, because they were never the TPA's.

    Stated separately from the row counts above because this is the one that would cost
    money: if inverting the source changed what the manufacturer paid, the engine would be
    reconciling against a different ledger depending on which TPA happens to be configured.
    """
    counts = {mode: builds[mode].kind_counts() for mode in MODES}
    for kind in (RecordKind.REBATE_BATCH, RecordKind.REBATE_DISPENSE_LINE):
        values = {mode: counts[mode][str(kind)] for mode in MODES}
        assert len(set(values.values())) == 1 and values[GENERIC_TPA_SOURCE] > 0, (
            f"{kind.value} count depends on the TPA source: {values}"
        )


# ═══ difference 1: nobody carries the rebate request ═══


def test_neither_vendor_export_carries_the_rebate_request(builds) -> None:
    """The submission fact has no column in either vendor's qualification export.

    Asserted as zero rather than "fewer", because the shape of the gap matters: this is not a
    vendor reporting less, it is a concept a qualification export does not have.  Asking the
    manufacturer is an event that happens *after* qualification and is reported by whoever
    did the asking.
    """
    generic = builds[GENERIC_TPA_SOURCE].kind_counts()[str(RecordKind.TPA_REBATE_REQUEST)]
    assert generic > 0, "the generic feed has no rebate requests, so there is no gap to find"

    for mode in ("verity", "craneware"):
        assert builds[mode].kind_counts()[str(RecordKind.TPA_REBATE_REQUEST)] == 0


def test_beacon_acknowledgements_already_cover_every_request_they_would_replace(
    builds,
) -> None:
    """The gap above has a filler, and this measures whether it actually reaches.

    A superset, not an approximation.  If a single episode held a rebate request and no
    acknowledgement, reading submission from Beacon would lose that episode's rebate track --
    so the assertion is on the direction of containment, not on the counts.

    Deliberately not yet acted on.  ``BEACON_ACKNOWLEDGMENT`` is outside
    ``dimensions._KIND_BUCKETS`` by decision, and bucketing it moves verdicts on the episodes
    Beacon reaches and the request does not.  That is a real improvement and a separately
    measurable one; folded in here its delta would be indistinguishable from the inversion's.
    """
    built = builds[GENERIC_TPA_SOURCE]
    requests = built.episodes_reached_by(RecordKind.TPA_REBATE_REQUEST)
    acknowledgements = built.episodes_reached_by(RecordKind.BEACON_ACKNOWLEDGMENT)
    assert requests, "no rebate request reaches an episode; nothing below means anything"

    orphans = sorted(requests - acknowledgements)
    assert not orphans, (
        f"{len(orphans)} episodes were submitted according to the TPA and have no Beacon "
        f"acknowledgement: {orphans[:3]}. Beacon cannot stand in for the request on those, so "
        "closing this gap would silently drop their rebate track"
    )
    assert RecordKind.BEACON_ACKNOWLEDGMENT not in __import__(
        "recon.engine.dimensions", fromlist=["_KIND_BUCKETS"]
    )._KIND_BUCKETS, (
        "the Beacon acknowledgement now feeds a dimension. That may well be right, but it is "
        "the step this test is holding open, and its verdict delta has to be measured on its "
        "own rather than inside the inversion's"
    )


# ═══ difference 2: Verity cannot say "not qualified" ═══


def test_verity_cannot_express_a_disqualification_and_craneware_can(builds) -> None:
    """The sharpest difference between the two exports, and it is invisible at the row level.

    Both files parse, contract-check and ingest perfectly.  What differs is what they are:
    ``verity_accumulations`` is the dispenses that *accumulated*, a population selected on
    ``qualification_status`` -- ``connectors/vendors/verity.py`` says it "can only be
    ``QUALIFIED`` here" -- so a refusal has no row to be written on.  Craneware's Claims
    Report is a report of claims, and a claim that did not qualify is still a claim.

    The consequence is not that those dispenses read as qualified.  It is that they lose the
    340B track altogether: with no qualification record of any kind, ``_derive_rebate``'s
    ``has_any_rebate_record`` is false and the verdict is C-00, "track absent" -- which says
    this dispense was never 340B, when in truth it was and was refused.
    """
    statuses = {mode: builds[mode].qualification_statuses() for mode in MODES}
    refused_in_generic = statuses[GENERIC_TPA_SOURCE]["NOT_QUALIFIED"]
    assert refused_in_generic > 0, (
        "the demo profile contains no disqualified dispense, so this asymmetry cannot be "
        "demonstrated on it"
    )

    assert statuses["verity"]["NOT_QUALIFIED"] == 0, (
        "verity_accumulations now reports a disqualification. If the export genuinely gained "
        "a non-qualifying population this test should be updated -- but check first that the "
        "formatter has not started writing rows the dataset does not contain"
    )
    assert statuses["craneware"]["NOT_QUALIFIED"] == refused_in_generic, (
        "Craneware's Claims Report is the export that CAN carry a refusal, and it no longer "
        "carries all of them"
    )


# ═══ the reconciliation ═══


@pytest.mark.parametrize("mode", ["verity", "craneware"])
def test_every_verdict_difference_falls_into_a_named_bucket(builds, mode: str) -> None:
    """The deliverable: no episode may change for a reason this file cannot name.

    Two transitions are accounted for, and nothing else is allowed:

    * ``-> C-03`` — the rebate request is gone, so a qualified dispense stops at "not
      submitted".  Reachable by both vendors.
    * ``-> C-00`` — the qualification record itself is gone, so the track reads as absent.
      Verity only, and only for dispenses the TPA refused.

    An unexplained transition fails with the codes in the message, because the transition
    *is* the finding -- "37 episodes changed" says nothing, and "C-09 became C-11 on four
    episodes" says where to look.
    """
    baseline = builds[GENERIC_TPA_SOURCE].latest_verdicts()
    inverted = builds[mode].latest_verdicts()

    assert set(baseline) == set(inverted), (
        "the two builds produced different episodes, so their verdicts are not comparable. "
        "Episode identity comes from the claim anchors, which no TPA source touches, so this "
        "means the switch reached something it should not have"
    )

    unexplained: Counter = Counter()
    explained: Counter = Counter()
    for episode_id, before in baseline.items():
        after = inverted[episode_id]
        if before["rebate_verdict_code"] == after["rebate_verdict_code"]:
            assert before["reimbursement_verdict_code"] == after["reimbursement_verdict_code"], (
                f"{episode_id} kept its rebate verdict and changed its REIMBURSEMENT one. The "
                "TPA source feeds the 340B track only, so nothing here should be able to move "
                "a claim's payment verdict"
            )
            continue
        transition = (before["rebate_verdict_code"], after["rebate_verdict_code"])
        if after["rebate_verdict_code"] in {NOT_SUBMITTED_CODE, TRACK_ABSENT_CODE}:
            explained[transition] += 1
        else:
            unexplained[transition] += 1

    assert not unexplained, (
        f"{mode}: verdict transitions with no named cause: "
        + ", ".join(f"{b} -> {a} on {n} episodes" for (b, a), n in unexplained.most_common())
    )
    assert explained, f"{mode}: no verdict moved at all, so the vendor door is not being used"


def test_only_verity_loses_the_track_entirely(builds) -> None:
    """The two buckets are not interchangeable, and which vendor reaches which one matters.

    Craneware carries every qualification the generic feed did, so nothing of its should ever
    reach C-00.  If it starts to, the Claims Report has stopped carrying a population it used
    to and the message above would have blamed the request gap for it.
    """
    baseline = builds[GENERIC_TPA_SOURCE].latest_verdicts()
    reached_absent = {
        mode: sum(
            1
            for episode_id, before in baseline.items()
            if before["rebate_verdict_code"] != TRACK_ABSENT_CODE
            and builds[mode].latest_verdicts()[episode_id]["rebate_verdict_code"]
            == TRACK_ABSENT_CODE
        )
        for mode in ("verity", "craneware")
    }
    assert reached_absent["craneware"] == 0, (
        "Craneware lost a 340B track it should have kept; its Claims Report carries "
        "non-qualifying rows, so a dispense should never go missing entirely through that door"
    )
    assert reached_absent["verity"] > 0, (
        "Verity no longer loses any track, so either the accumulations export gained a "
        "disqualified population or the demo profile stopped containing one -- either way the "
        "asymmetry this file documents is no longer being demonstrated"
    )
