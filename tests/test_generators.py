"""Tests for the four generators and the orchestrator that drives them.

Organised around the properties the design notes call load-bearing: verdict-pair
coverage (Decision 48), determinism (Decision C4), slice blindness (Decision 10/11),
the 835 arithmetic, crosswalk mechanics, feed-format conformance, the no-future-dates
discipline (Decision 16/17), confidentiality (Decision C10) and module isolation.

Both profiles are generated exactly once, as session-scoped fixtures, and every test
below reads from that one generation rather than paying for its own.
"""

from __future__ import annotations

import ast
import csv
import dataclasses
import json
import re
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from recon import config
from recon.config import ProfileSelection, load_settings
from recon.crosswalk import keys
from recon.domain import verdicts
from recon.domain.enums import SourceSystem
from recon.generators import bank as bank_gen
from recon.generators import orchestrator as orch
from recon.generators.contracts import FORBIDDEN_SLICE_FIELDS, assert_slices_are_blind
from recon.generators.orchestrator import generate, write_outputs
from recon.generators.plan import EpisodePlan
from recon.generators.sampling import (
    CURATED_PAIRS,
    bind_entities,
    load_leaves,
    select_configurations,
    select_curated,
)
from recon.generators.timeline import build_timeline
from recon.money import to_cents
from recon.rng import rng_for


# ═══ fixtures — both profiles generated exactly once ════════════════════════════════


@pytest.fixture(scope="session")
def demo_settings(tmp_path_factory) -> config.Settings:
    return load_settings("demo", data_dir=tmp_path_factory.mktemp("demo_data"))


@pytest.fixture(scope="session")
def demo_result(demo_settings):
    return generate(demo_settings)


@pytest.fixture(scope="session")
def full_settings(tmp_path_factory) -> config.Settings:
    return load_settings("full", data_dir=tmp_path_factory.mktemp("full_data"))


@pytest.fixture(scope="session")
def full_result(full_settings):
    return generate(full_settings)


@pytest.fixture(scope="session")
def full_feeds_dir(full_settings, full_result) -> Path:
    """The six feed files, written once, shared by every test that reads the wire."""
    write_outputs(full_settings, full_result)
    return full_settings.feeds_dir()


@pytest.fixture(scope="session")
def full_feed_records(full_feeds_dir) -> dict[str, list[dict]]:
    """Every ``.jsonl`` feed, parsed once with ``parse_float=Decimal`` and cached."""
    return {
        filename: _read_jsonl(full_feeds_dir / filename)
        for filename in config.FEED_FILENAMES
        if filename != config.BANK_TRANSACTIONS_FILE
    }


@pytest.fixture(scope="session")
def full_bank_rows(full_feeds_dir) -> list[dict]:
    return _read_bank_csv(full_feeds_dir / config.BANK_TRANSACTIONS_FILE)


# ═══ A. Coverage — the oracle (Decision 48) ═════════════════════════════════════════


def test_full_profile_reaches_all_372_reachable_verdict_pairs(full_result):
    """The single most important test in this file.

    Decision 48 turns coverage from a hope into an arithmetic guarantee: the ``full``
    profile reserves one episode per verdict pair before it fills the remainder.  If a
    pair is ever missing, stratified sampling itself has broken, which would quietly
    leave whole states of the reconciliation engine untested.
    """
    produced: set[tuple[str, str]] = set()
    for key in full_result.ground_truth["verdict_pair_counts"]:
        reimbursement, rebate = key.split("|")
        produced.add((reimbursement, rebate))

    missing = verdicts.REACHABLE_PAIRS - produced
    assert not missing, (
        f"missing {len(missing)} of the 372 reachable verdict pairs: "
        f"{sorted(missing)[:10]}"
    )
    assert produced == set(verdicts.REACHABLE_PAIRS)


def test_full_profile_produces_every_reimbursement_and_every_rebate_code(full_result):
    """Pair coverage alone does not guarantee every individual code fired at least once.

    372 = 31 x 12 is a product; a generator could in principle hit all 372 pairs while
    some reimbursement code only ever appears alongside one particular rebate code and
    nothing is amiss — but the fixture design means every one of the 31 reimbursement
    codes and all 12 rebate codes must independently appear for the pairs to close out.
    """
    episodes = full_result.ground_truth["episodes"]
    reimbursement_seen = {ep["intended_reimbursement_verdict"] for ep in episodes}
    rebate_seen = {ep["intended_rebate_verdict"] for ep in episodes}

    assert reimbursement_seen == set(verdicts.REIMBURSEMENT_CODES), (
        f"missing reimbursement codes: "
        f"{set(verdicts.REIMBURSEMENT_CODES) - reimbursement_seen}"
    )
    assert rebate_seen == set(verdicts.REBATE_CODES), (
        f"missing rebate codes: {set(verdicts.REBATE_CODES) - rebate_seen}"
    )


def test_demo_profile_covers_at_least_fifty_five_pairs_and_its_curated_spine(
    demo_result, demo_settings
):
    """``demo`` is curated rather than exhaustive, but the curated spine is a promise.

    :data:`CURATED_PAIRS` is ordered by the story a walkthrough tells, and
    ``select_curated`` truncates it at ``episode_count``.  Every pair that fits inside
    the demo's 60 episodes must actually appear, or the walkthrough silently loses a
    state it claims to demonstrate.
    """
    episodes = demo_result.ground_truth["episodes"]
    seen_pairs = {
        (ep["intended_reimbursement_verdict"], ep["intended_rebate_verdict"])
        for ep in episodes
    }
    assert len(seen_pairs) >= 55

    expected_spine = set(CURATED_PAIRS[: demo_settings.episode_count])
    missing = expected_spine - seen_pairs
    assert not missing, (
        f"demo profile dropped curated pairs that fit inside "
        f"{demo_settings.episode_count} episodes: {sorted(missing)}"
    )


#: The assignment's eight named edge cases, each mapped to a predicate over one
#: ground-truth episode.  Named in the assertion message so a failure says which
#: edge case the demo profile lost, not just which predicate evaluated false.
_EDGE_CASES: tuple[tuple[str, object], ...] = (
    (
        "fully reconciled (A-04 or B-04)",
        lambda ep: ep["intended_reimbursement_verdict"] in {"A-04", "B-04"},
    ),
    (
        "partial payment (B-06/B-07/B-08/B-09)",
        lambda ep: ep["intended_reimbursement_verdict"]
        in {"B-06", "B-07", "B-08", "B-09"},
    ),
    (
        "underpayment (A-07 or A-08)",
        lambda ep: ep["intended_reimbursement_verdict"] in {"A-07", "A-08"},
    ),
    (
        "reversal (A-10/A-11/A-16)",
        lambda ep: ep["intended_reimbursement_verdict"] in {"A-10", "A-11", "A-16"},
    ),
    (
        "recoupment (A-12/A-13/B-15)",
        lambda ep: ep["intended_reimbursement_verdict"] in {"A-12", "A-13", "B-15"},
    ),
    (
        "unmatched cash (A-05/B-05)",
        lambda ep: ep["intended_reimbursement_verdict"] in {"A-05", "B-05"},
    ),
    ("unmatched rebate (C-09)", lambda ep: ep["intended_rebate_verdict"] == "C-09"),
    (
        "denial (A-01/B-10/B-13)",
        lambda ep: ep["intended_reimbursement_verdict"] in {"A-01", "B-10", "B-13"},
    ),
    (
        "duplicate event (A-17/B-16/C-14)",
        lambda ep: ep["intended_reimbursement_verdict"] in {"A-17", "B-16"}
        or ep["intended_rebate_verdict"] == "C-14",
    ),
)


@pytest.mark.parametrize(
    "edge_case,predicate", _EDGE_CASES, ids=[name for name, _ in _EDGE_CASES]
)
def test_demo_profile_reaches_every_named_edge_case(demo_result, edge_case, predicate):
    """The debrief walks through one episode per edge case; each must exist.

    A 60-episode curated demo that silently dropped "reversal" or "denial" would still
    run end to end, which is exactly why this is asserted rather than trusted.
    """
    episodes = demo_result.ground_truth["episodes"]
    assert any(predicate(ep) for ep in episodes), (
        f"demo ground truth has no episode for the '{edge_case}' edge case"
    )


# ═══ B. Determinism — Decision C4 / what the manifest promises ═════════════════════


def test_demo_generation_is_byte_identical_across_two_runs(tmp_path):
    """The manifest's hash map is only meaningful if two runs of the same seed agree.

    If they did not, "reproducible" would be a claim the manifest makes rather than a
    property anyone could check.
    """
    settings_a = load_settings("demo", data_dir=tmp_path / "run-a")
    settings_b = load_settings("demo", data_dir=tmp_path / "run-b")

    hashes_a = write_outputs(settings_a, generate(settings_a))
    hashes_b = write_outputs(settings_b, generate(settings_b))

    assert hashes_a == hashes_b


def test_generation_is_stable_within_one_process(tmp_path):
    """Insertion-independence, checked the way it actually matters.

    Two separate calls to :func:`generate` in the same process — not two separate
    interpreter invocations — must still agree on every file hash and on the
    fingerprints that identify which reference data and decision tree produced the
    run.  A shared mutable RNG or an un-reset module-level cache would show up here.
    """
    settings_a = load_settings("demo", data_dir=tmp_path / "first")
    settings_b = load_settings("demo", data_dir=tmp_path / "second")

    result_a = generate(settings_a)
    result_b = generate(settings_b)

    assert result_a.ground_truth["reference_fingerprint"] == (
        result_b.ground_truth["reference_fingerprint"]
    )
    assert result_a.ground_truth["decision_tree_digest"] == (
        result_b.ground_truth["decision_tree_digest"]
    )

    hashes_a = write_outputs(settings_a, result_a)
    hashes_b = write_outputs(settings_b, result_b)
    assert hashes_a == hashes_b


def test_a_different_master_seed_changes_the_feed_hashes(tmp_path):
    """Proof the seed is actually wired through every generator, not quietly ignored.

    If changing ``master_seed`` left the hashes unchanged, every generator would be
    drawing from some other, unseeded source of randomness and the "reproducible"
    claim would be vacuous in the other direction: nothing would ever change either.
    """
    settings_a = load_settings("demo", data_dir=tmp_path / "seed-a", master_seed=20_250_701)
    settings_b = load_settings("demo", data_dir=tmp_path / "seed-b", master_seed=20_250_702)

    hashes_a = write_outputs(settings_a, generate(settings_a))
    hashes_b = write_outputs(settings_b, generate(settings_b))

    assert hashes_a != hashes_b


# ═══ C. Slice blindness — Decision 10/11, the property the design rests on ═════════


def test_all_slices_are_blind_on_a_full_generation_pass(demo_settings):
    """No slice a generator receives may carry a fact only the orchestrator should know.

    Run over every slice kind the orchestrator actually builds — not just the
    dataclass *definitions* — because the cheapest way to leak an episode id is to
    stuff it into an existing string field at construction time, and only inspecting
    real instances catches that.
    """
    plans, timelines = _build_plans_and_timelines(demo_settings)
    # ``slice_ref`` is an opaque handle minted on the orchestrator's side of the boundary.
    # A fresh registry per builder is fine: blindness is a property of the slices, and the
    # reverse mapping only matters to the orchestrator's own attribution.
    refs = orch.SliceRefs()

    assert_slices_are_blind(orch._pharmacy_claim_slices(plans, timelines, demo_settings, refs))
    assert_slices_are_blind(
        orch._medical_submission_slices(plans, timelines, demo_settings, refs)
    )
    assert_slices_are_blind(orch._tpa_dispense_slices(plans, timelines, demo_settings, refs))
    assert_slices_are_blind(
        [
            batch
            for batch, _ in orch._pharmacy_remittance_batches(
                plans, timelines, demo_settings, refs
            )
        ]
    )
    assert_slices_are_blind(
        [
            batch
            for batch, _ in orch._medical_remittance_batches(
                plans, timelines, demo_settings, refs
            )
        ]
    )
    assert_slices_are_blind(
        [batch for batch, _, _ in orch._rebate_batches(plans, timelines, demo_settings, refs)]
    )


def test_no_slice_dataclass_declares_a_forbidden_field():
    """Belt-and-braces over :func:`test_all_slices_are_blind_on_a_full_generation_pass`.

    That test only sees the slice kinds a particular demo run happens to construct.
    This one walks every dataclass *defined* in ``contracts.py`` regardless of whether
    the run exercised it, so a forbidden field on a rarely-built slice type cannot
    hide behind low sampling probability.
    """
    from recon.generators import contracts

    offenders: dict[str, list[str]] = {}
    for name, obj in vars(contracts).items():
        if (
            isinstance(obj, type)
            and dataclasses.is_dataclass(obj)
            and obj.__module__ == contracts.__name__
        ):
            bad = sorted(set(obj.__dataclass_fields__) & FORBIDDEN_SLICE_FIELDS)
            if bad:
                offenders[name] = bad
    assert not offenders, offenders


#: Verdict-code-shaped values, matched on the *whole* field value (never a substring):
#: a value like "AUTH1234567A" must not be mistaken for a leaked verdict code just
#: because it contains similar characters.
_VERDICT_CODE_SHAPE = re.compile(r"^[ABC]-\d{2}$")


def test_no_emitted_record_value_leaks_episode_case_or_verdict_identity(
    full_feed_records, full_bank_rows
):
    """The leak check that actually matters: the wire itself, not the slice contract.

    ``assert_slice_is_blind`` guards the generator's *inputs*; this guards the
    generator's *outputs* by scanning every string value on every feed, including the
    bank CSV, for an episode id, a case id or a bare verdict code.  Matching is
    substring for ``EP-0``/``CASE-`` (those prefixes are unambiguous identifier
    markers per :func:`recon.generators.contracts._looks_like_an_episode_id`) and
    exact whole-value match for verdict-code shapes, which avoids flagging an opaque
    identifier that merely contains similar characters.
    """
    offenders: list[tuple[str, str, str]] = []

    for filename, records in full_feed_records.items():
        for record in records:
            for path, value in _iter_string_leaves(record):
                if "EP-0" in value or "CASE-" in value:
                    offenders.append((filename, path, value))
                elif _VERDICT_CODE_SHAPE.match(value):
                    offenders.append((filename, path, value))

    for row in full_bank_rows:
        for key, value in row.items():
            if not isinstance(value, str):
                continue
            if "EP-0" in value or "CASE-" in value:
                offenders.append((config.BANK_TRANSACTIONS_FILE, key, value))
            elif _VERDICT_CODE_SHAPE.match(value):
                offenders.append((config.BANK_TRANSACTIONS_FILE, key, value))

    assert not offenders, f"leaked identifier-shaped values: {offenders[:10]}"


# ═══ D. Money and the 835 arithmetic ════════════════════════════════════════════════

#: Any decimal-looking string.  Deliberately broader than "exactly two decimals" so a
#: stray three-decimal value (a real defect ``to_cents`` is built to catch) is found
#: rather than silently skipped by a tighter pattern.
_DECIMAL_SHAPE = re.compile(r"^-?\d+\.\d+$")


def test_every_money_looking_value_on_every_feed_parses_through_to_cents(
    full_feed_records, full_bank_rows
):
    """Integer cents end to end: at most two decimal places, nowhere on the wire.

    A float round-trip or a careless format string could leave a third decimal place
    that reads as a real one-cent variance to anything downstream.  ``to_cents``
    raises on exactly that, so running every decimal-shaped value through it is a
    direct check of the property rather than a proxy for it.
    """
    checked = 0
    for filename, records in full_feed_records.items():
        for record in records:
            for path, value in _iter_string_leaves(record):
                if _DECIMAL_SHAPE.match(value):
                    checked += 1
                    try:
                        to_cents(value)
                    except ValueError as exc:
                        pytest.fail(f"{filename} {path}={value!r} rejected by to_cents: {exc}")

    for row in full_bank_rows:
        for key, value in row.items():
            if isinstance(value, str) and _DECIMAL_SHAPE.match(value):
                checked += 1
                try:
                    to_cents(value)
                except ValueError as exc:
                    pytest.fail(
                        f"{config.BANK_TRANSACTIONS_FILE} {key}={value!r} rejected: {exc}"
                    )

    assert checked > 0, "no money-shaped value was found at all — the scan itself is broken"


def test_pharmacy_835_claim_lines_balance_except_the_sanctioned_underpayment_defect(
    full_feed_records,
):
    """``clp03 = clp04 + sum(adjustments)``, with one sanctioned exception.

    The deliberate pharmacy underpayment defect leaves a positive unexplained
    residual: ``clp04`` falls short of a ``CO-45`` that does not cover the whole gap.
    Both populations — lines that balance, and lines that deliberately do not — must
    be non-empty: an all-balanced dataset has lost the underpayment edge case, and an
    all-unbalanced one is simply broken.
    """
    balanced = 0
    underpaid = 0
    for record in full_feed_records[config.PBM_REMITTANCE_835_FILE]:
        for line in record.get("claim_payments", []):
            charge = to_cents(line["clp03_total_charge"])
            payment = to_cents(line["clp04_payment_amount"])
            adjustment_total = sum(to_cents(a["amount"]) for a in line["adjustments"])
            residual = charge - payment - adjustment_total
            assert residual >= 0, (
                f"{record['record_id']} CLP01={line['clp01_patient_control_number']!r} "
                f"pays more than charge minus adjustments explain (residual={residual})"
            )
            if residual == 0:
                balanced += 1
            else:
                underpaid += 1

    assert balanced > 0, "no pharmacy 835 line balances — the arithmetic itself is broken"
    assert underpaid > 0, "no pharmacy 835 line carries the underpayment defect"


def test_pharmacy_835_patient_responsibility_equals_the_pr_adjustments(full_feed_records):
    """``clp05`` must equal the sum of the line's own ``PR`` adjustments, not a guess."""
    checked = 0
    for record in full_feed_records[config.PBM_REMITTANCE_835_FILE]:
        for line in record.get("claim_payments", []):
            clp05 = to_cents(line["clp05_patient_responsibility"])
            pr_total = sum(
                to_cents(a["amount"]) for a in line["adjustments"] if a["group_code"] == "PR"
            )
            assert clp05 == pr_total, (
                f"{record['record_id']} CLP01={line['clp01_patient_control_number']!r}: "
                f"clp05={clp05} but PR adjustments sum to {pr_total}"
            )
            checked += 1
    assert checked > 0


def test_medical_835_claim_lines_always_balance_with_no_exception(full_feed_records):
    """The medical dispute is about *why* a line was reduced, never about the sum.

    Unlike the pharmacy track, ``PbmClaimLineSlice.is_underpayment_defect`` has no
    medical counterpart: every medical claim line must satisfy
    ``clp03 = clp04 + sum(adjustments)`` exactly, always.
    """
    checked = 0
    for record in full_feed_records[config.MEDICAL_835_REMITTANCE_FILE]:
        for line in record.get("claim_payments", []):
            charge = to_cents(line["clp03_total_charge"])
            payment = to_cents(line["clp04_payment_amount"])
            adjustment_total = sum(to_cents(a["amount"]) for a in line["adjustments"])
            assert charge == payment + adjustment_total, (
                f"{record['record_id']} CLP01={line['clp01_patient_control_number']!r} "
                f"does not balance: charge={charge} payment={payment} "
                f"adjustments={adjustment_total}"
            )
            checked += 1
    assert checked > 0


@pytest.mark.parametrize(
    "feed_file,bpr_path,plb_reference_field",
    [
        (config.PBM_REMITTANCE_835_FILE, ("bpr", "total_actual_provider_payment"), "reference_id"),
        (config.MEDICAL_835_REMITTANCE_FILE, ("bpr", "bpr02_total_payment"), "reference_icn"),
    ],
    ids=["pharmacy", "medical"],
)
def test_bpr_identity_recomputed_from_the_wire(
    full_feed_records, feed_file, bpr_path, plb_reference_field
):
    """``BPR = sum(CLP04) - sum(PLB, signed)``, re-derived from bytes the generator wrote.

    The PLB amount on the wire is already signed — the generator applied
    :func:`recon.reference.codes.plb_semantics` before formatting it — so this is a
    direct subtraction, not a re-application of the sign table.  The two feeds spell
    the BPR total and the PLB reference field differently (``reference_id`` on
    pharmacy, ``reference_icn`` on medical), which is exactly why the field names are
    parametrised rather than assumed shared.
    """
    checked = 0
    for record in full_feed_records[feed_file]:
        bpr = record
        for key in bpr_path[:-1]:
            bpr = bpr[key]
        bpr_total = to_cents(bpr[bpr_path[-1]])

        clp04_total = sum(
            to_cents(line["clp04_payment_amount"]) for line in record.get("claim_payments", [])
        )
        plb_total = sum(
            to_cents(entry["amount"])
            for entry in record.get("provider_level_adjustments", [])
        )
        assert bpr_total == clp04_total - plb_total, (
            f"{record['record_id']}: bpr={bpr_total} but "
            f"sum(clp04)={clp04_total} - sum(plb)={plb_total} "
            f"= {clp04_total - plb_total}"
        )
        checked += 1
    assert checked > 0


def test_rebate_batch_total_equals_the_sum_of_its_dispense_lines(full_feed_records):
    """``total_rebate_amount`` is computed once, by the generator that formats it.

    The slice carries no precomputed total for exactly this reason — there is no
    second copy on the wire free to disagree with the dispense lines underneath it.
    """
    checked = 0
    for record in full_feed_records[config.TPA_340B_EVENTS_FILE]:
        if record.get("event_type") != "REBATE_PAYMENT_BATCH":
            continue
        total = to_cents(record["total_rebate_amount"])
        line_total = sum(to_cents(d["rebate_amount"]) for d in record["dispenses"])
        assert total == line_total, (
            f"{record['record_id']}: total_rebate_amount={total} but "
            f"dispense lines sum to {line_total}"
        )
        checked += 1
    assert checked > 0, "no REBATE_PAYMENT_BATCH record was found at all"


def test_plb_entries_exist_both_with_and_without_a_traceable_reference(full_feed_records):
    """D-7 (untraceable offset, A-13) and A-12 (traced) are two different mechanisms.

    An absent ``reference_id`` is not sloppiness on the generator's part — it is the
    entire difference between a recoupment that can be tied to a deposit and one that
    cannot.  Both populations must exist, or one of the two verdicts has gone unbuilt.
    """
    references = [
        entry.get("reference_id")
        for record in full_feed_records[config.PBM_REMITTANCE_835_FILE]
        for entry in record.get("provider_level_adjustments", [])
    ]
    assert references, "no pharmacy PLB entries were emitted at all"
    assert any(ref is None for ref in references), "no untraceable (D-7/A-13) PLB entry exists"
    assert any(ref is not None for ref in references), "no traceable (A-12) PLB entry exists"


# ═══ E. Crosswalk mechanics ══════════════════════════════════════════════════════════


def test_clp01_round_trips_and_the_fill_component_is_exactly_two_digits(full_feed_records):
    """Every pharmacy 835 CLP01 must parse, and the convention is ``<rx>FILL<2 digits>``.

    ``parse_clp01_pharmacy`` raising on any emitted value would mean the generator's
    write side and the crosswalk's read side have drifted out of the one convention
    they are both supposed to share.
    """
    checked = 0
    for record in full_feed_records[config.PBM_REMITTANCE_835_FILE]:
        for line in record.get("claim_payments", []):
            clp01 = line["clp01_patient_control_number"]
            rx_number, fill_number = keys.parse_clp01_pharmacy(clp01)
            assert re.fullmatch(r"\d{2}", fill_number), (
                f"CLP01={clp01!r} parsed fill component {fill_number!r}, not exactly 2 digits"
            )
            assert rx_number, f"CLP01={clp01!r} parsed an empty rx number"
            checked += 1
    assert checked > 0


def test_identifier_drift_is_generated_not_merely_claimed(full_result, full_feeds_dir):
    """D-6 must be a property of the bytes, not just a flag ``expected_links`` sets.

    Finds one pharmacy episode whose 835 rendering drifted, then independently
    confirms from the actual written files that the canonical spelling is what
    ``pbm_claim_events.jsonl`` carries and the drifted spelling is what
    ``pbm_remittance_835.jsonl`` carries — two different files, read back from disk,
    not the in-memory plan asserting itself.
    """
    drifted_plan = next(
        (
            plan
            for plan in full_result.plans
            if plan.track.value == "PHARMACY" and plan.rx_rendering_pharmacy_835 != "CANONICAL"
        ),
        None,
    )
    assert drifted_plan is not None, (
        "no pharmacy episode exercised the 835 Rx-drift defect (D-6) at all"
    )

    canonical_rx = drifted_plan.rx_number
    drifted_rx = drifted_plan.rendered_rx(drifted_plan.rx_rendering_pharmacy_835)
    assert canonical_rx != drifted_rx, "the rendering directive fired but produced no drift"

    wire_dos = keys.iso_date_to_wire(drifted_plan.date_of_service.isoformat())

    claim_records = _read_jsonl(full_feeds_dir / config.PBM_CLAIM_EVENTS_FILE)
    found_canonical = any(
        record.get("service_provider_id") == drifted_plan.pharmacy_npi
        and record.get("prescription_ref_number") == canonical_rx
        and record.get("date_of_service") == wire_dos
        for record in claim_records
    )
    assert found_canonical, (
        "the canonical Rx spelling never appears on pbm_claim_events.jsonl for this episode"
    )

    remittance_records = _read_jsonl(full_feeds_dir / config.PBM_REMITTANCE_835_FILE)
    found_drifted = any(
        keys.parse_clp01_pharmacy(line["clp01_patient_control_number"])[0] == drifted_rx
        and line["service_line"]["product_id"] == drifted_plan.ndc11
        and line["service_line"]["date_of_service"] == wire_dos
        for record in remittance_records
        for line in record.get("claim_payments", [])
    )
    assert found_drifted, (
        "the drifted Rx spelling never appears on pbm_remittance_835.jsonl for this episode"
    )


def test_addenda_loss_rate_on_full_profile_is_neither_zero_nor_total(full_bank_rows):
    """~20% of CCD+ addenda are lost on purpose; the rate must land in a believable band.

    The target is 20%; the assertion allows sampling slack either side but, crucially,
    rules out the two failure modes that would actually matter: nobody ever loses the
    addenda (TRN02 matching would never be exercised) or everybody does (amount+date
    matching would be the only path, which is not what the design claims).
    """
    credits = [row for row in full_bank_rows if row["type"] == "CREDIT"]
    assert credits, "no CREDIT rows in the bank feed at all"
    empty = sum(1 for row in credits if row["trn02"] == "")
    rate = empty / len(credits)
    assert 0.10 <= rate <= 0.32, (
        f"addenda loss rate {rate:.3f} ({empty}/{len(credits)}) is outside [0.10, 0.32]"
    )


def test_tpa_medical_dispense_shape_is_consistent_with_no_prescription(full_feed_records):
    """A clinic-infused drug has no Rx number, so it must have no pharmacy NPI either.

    Checked on every TPA record shape — the four per-dispense event types plus the
    nested dispense lines inside a ``REBATE_PAYMENT_BATCH`` — because the asymmetry
    between ``rx_number``/``pharmacy_npi`` and ``provider_npi`` is the entire weakness
    the medical 340B natural key is built on.
    """
    shapes: list[dict] = []
    for record in full_feed_records[config.TPA_340B_EVENTS_FILE]:
        if "rx_number" in record:
            shapes.append(record)
        for dispense in record.get("dispenses", []):
            shapes.append(dispense)

    assert shapes, "no TPA dispense-shaped record was found at all"
    for shape in shapes:
        rx_number = shape.get("rx_number")
        pharmacy_npi = shape.get("pharmacy_npi")
        provider_npi = shape.get("provider_npi")
        if rx_number is None:
            assert pharmacy_npi is None, shape
            assert provider_npi is not None, shape
        else:
            assert pharmacy_npi is not None, shape


def test_rebate_deposits_and_claim_payments_use_different_nacha_entry_descriptions(
    full_bank_rows,
):
    """A naive "claim payments only" filter on ``HCCLAIMPMT`` silently skips every rebate.

    Both codes must appear in the same dataset, or the named defect is not actually
    exercisable.
    """
    descriptions = {row["company_entry_description"] for row in full_bank_rows}
    assert "HCCLAIMPMT" in descriptions, "no claim-payment (HCCLAIMPMT) deposit exists"
    assert "CCD" in descriptions, "no rebate (CCD) deposit exists"


# ═══ F. Feed-format conformance ══════════════════════════════════════════════════════

_RECEIVED_AT_SHAPE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

_JSONL_FEEDS = tuple(f for f in config.FEED_FILENAMES if f != config.BANK_TRANSACTIONS_FILE)


@pytest.mark.parametrize("filename", _JSONL_FEEDS)
def test_every_jsonl_line_is_valid_json_with_a_well_formed_received_at(
    full_feed_records, filename
):
    """Every record on every feed must carry the one field no source format supplies.

    ``received_at`` is what the orchestrator adds on top of the native wire shape
    (Decision 16); a record missing it, or carrying it malformed, cannot be ordered
    into a replay.
    """
    records = full_feed_records[filename]
    assert records, f"{filename} produced no records at all"
    for record in records:
        received_at = record.get("received_at")
        assert received_at, f"{filename}: record {record.get('record_id')} has no received_at"
        assert _RECEIVED_AT_SHAPE.match(received_at), (
            f"{filename}: malformed received_at {received_at!r}"
        )


@pytest.mark.parametrize("filename", _JSONL_FEEDS)
def test_every_jsonl_record_carries_a_known_source_system(full_feed_records, filename):
    """``source_system`` must be drawn from the closed vocabulary, verbatim from the feed spec."""
    valid = {member.value for member in SourceSystem}
    for record in full_feed_records[filename]:
        assert record.get("source_system") in valid, (
            f"{filename}: record {record.get('record_id')} has source_system "
            f"{record.get('source_system')!r}, not one of {sorted(valid)}"
        )


def test_bank_csv_columns_and_line_endings_are_exact(full_feeds_dir):
    """A positional CSV reader, and the reproducibility manifest, both depend on this.

    Column order must match :data:`recon.generators.bank.BANK_CSV_COLUMNS` exactly,
    and line endings must be ``\\n`` rather than the Windows-default ``\\r\\n`` — the
    same seed must hash identically on every platform, or the manifest is a lie.
    """
    raw = (full_feeds_dir / config.BANK_TRANSACTIONS_FILE).read_bytes()
    assert b"\r\n" not in raw, "bank CSV uses \\r\\n line endings"
    assert b"\n" in raw

    header_line = raw.split(b"\n", 1)[0].decode("utf-8")
    assert header_line.split(",") == list(bank_gen.BANK_CSV_COLUMNS)


def test_837_submissions_and_277ca_acknowledgments_differ_only_by_record_type(
    full_feed_records,
):
    """Without ``record_type``, a rejection and a claim awaiting payment are identical.

    An ordinary 837 must never carry ``record_type``; every 277CA must carry exactly
    ``"277CA"``.  Both shapes must exist, or verdict B-01 (clearinghouse rejection) is
    unreachable on the wire.
    """
    saw_837 = False
    saw_277ca = False
    for record in full_feed_records[config.MEDICAL_837_SUBMISSIONS_FILE]:
        if record.get("record_type") == "277CA":
            saw_277ca = True
        else:
            assert "record_type" not in record, (
                f"ordinary 837 record {record.get('record_id')} carries record_type"
            )
            saw_837 = True
    assert saw_837, "no ordinary 837 submission was found"
    assert saw_277ca, "no 277CA acknowledgment was found"


# ═══ G. No-future-dates discipline (Decision 16/17) ═════════════════════════════════

#: Fields eligible for the one documented exception: a payment's *effective* date is a
#: genuine settlement instruction, already transmitted to the ACH network, and may sit
#: up to three banking-adjacent days after the record announcing it arrives.
_ACH_EFFECTIVE_DATE_EXCEPTION_DAYS = 3


def _collect_wire_dates(feeds_dir: Path) -> list[tuple[str, str, str, date, date, int]]:
    """Every native CCYYMMDD date on every feed, paired with its own record's date.

    Restricted to the known CCYYMMDD field *names* (by key, not by a generic "looks
    like 8 digits" scan) because some identifiers — a drifted, zero-padded Rx number,
    for instance — are themselves 8 numeric characters and would otherwise be
    misread as dates.

    Returns ``(filename, record_id, field, wire_date, received_date, allowed_forward_days)``.
    """
    entries: list[tuple[str, str, str, date, date, int]] = []

    def add(filename: str, record_id: str, field: str, wire_value, received_at: str, allowed: int = 0):
        if wire_value is None:
            return
        wire_date = date.fromisoformat(keys.wire_date_to_iso(wire_value))
        received_date = date.fromisoformat(received_at[:10])
        entries.append((filename, record_id, field, wire_date, received_date, allowed))

    for record in _read_jsonl(feeds_dir / config.PBM_CLAIM_EVENTS_FILE):
        add(
            config.PBM_CLAIM_EVENTS_FILE,
            record.get("record_id", ""),
            "date_of_service",
            record.get("date_of_service"),
            record["received_at"],
        )

    for record in _read_jsonl(feeds_dir / config.PBM_REMITTANCE_835_FILE):
        add(
            config.PBM_REMITTANCE_835_FILE,
            record.get("record_id", ""),
            "bpr.payment_effective_date",
            record["bpr"]["payment_effective_date"],
            record["received_at"],
            allowed=_ACH_EFFECTIVE_DATE_EXCEPTION_DAYS,
        )
        for line in record.get("claim_payments", []):
            add(
                config.PBM_REMITTANCE_835_FILE,
                record.get("record_id", ""),
                "service_line.date_of_service",
                line["service_line"]["date_of_service"],
                record["received_at"],
            )

    for record in _read_jsonl(feeds_dir / config.MEDICAL_837_SUBMISSIONS_FILE):
        if "date_of_service" in record:
            add(
                config.MEDICAL_837_SUBMISSIONS_FILE,
                record.get("record_id", ""),
                "date_of_service",
                record.get("date_of_service"),
                record["received_at"],
            )

    for record in _read_jsonl(feeds_dir / config.MEDICAL_835_REMITTANCE_FILE):
        add(
            config.MEDICAL_835_REMITTANCE_FILE,
            record.get("record_id", ""),
            "bpr.bpr16_eft_effective_date",
            record["bpr"]["bpr16_eft_effective_date"],
            record["received_at"],
            allowed=_ACH_EFFECTIVE_DATE_EXCEPTION_DAYS,
        )

    for record in _read_jsonl(feeds_dir / config.TPA_340B_EVENTS_FILE):
        if "fill_date" in record:
            add(
                config.TPA_340B_EVENTS_FILE,
                record.get("record_id", ""),
                "fill_date",
                record.get("fill_date"),
                record["received_at"],
            )
        if "submission_date" in record:
            add(
                config.TPA_340B_EVENTS_FILE,
                record.get("record_id", ""),
                "submission_date",
                record.get("submission_date"),
                record["received_at"],
            )
        if "payment_effective_date" in record:
            add(
                config.TPA_340B_EVENTS_FILE,
                record.get("record_id", ""),
                "payment_effective_date",
                record.get("payment_effective_date"),
                record["received_at"],
            )
        for dispense in record.get("dispenses", []):
            add(
                config.TPA_340B_EVENTS_FILE,
                record.get("record_id", ""),
                "dispenses[].fill_date",
                dispense.get("fill_date"),
                record["received_at"],
            )

    return entries


def test_wire_dates_fall_inside_the_generation_window(full_feeds_dir, full_settings):
    """Every native date belongs inside the published window, with settlement headroom.

    200 days past the window end is the documented allowance for downstream
    settlement (a remittance or rebate batch due near the tail of the window); beyond
    that a date is simply wrong, not merely late.
    """
    entries = _collect_wire_dates(full_feeds_dir)
    assert entries, "no wire dates were collected at all — the scan itself is broken"

    floor = full_settings.window_start
    ceiling = full_settings.window_end + timedelta(days=200)
    offenders = [e for e in entries if not (floor <= e[3] <= ceiling)]
    assert not offenders, (
        f"dates outside [{floor}, {ceiling}]: "
        f"{[(f, r, field, d) for f, r, field, d, _, _ in offenders[:10]]}"
    )


def test_no_native_date_postdates_its_own_records_received_at(full_feeds_dir):
    """An event cannot be reported before it happened — with one documented exception.

    ``payment_effective_date``/``bpr16_eft_effective_date`` on a remittance is a
    genuine settlement instruction already transmitted to the ACH network: "this
    committed money lands on Wednesday" is known and true on the day it is reported,
    even though the money has not landed yet.  That is the one sanctioned
    forward-looking date in the dataset, capped at three days.  Every other native
    date must be on or before the ``received_at`` day of the very record carrying it.
    """
    entries = _collect_wire_dates(full_feeds_dir)
    offenders = [
        (filename, record_id, field, wire_date, received_date)
        for filename, record_id, field, wire_date, received_date, allowed in entries
        if wire_date > received_date + timedelta(days=allowed)
    ]
    assert not offenders, (
        f"dates postdate their own record's received_at beyond the documented ACH "
        f"effective-date exception: {offenders[:10]}"
    )


# ═══ H. Confidentiality (Decision C10) ══════════════════════════════════════════════

#: Real organisations that must never appear in generated data, case-insensitive.
REAL_ORG_NAMES: tuple[str, ...] = (
    "CAREMARK", "EXPRESS SCRIPTS", "OPTUMRX", "ANTHEM", "AETNA", "CIGNA", "HUMANA",
    "UNITEDHEALTH", "BLUE CROSS", "LILLY", "PFIZER", "SANOFI", "NOVARTIS", "ASTRAZENECA",
    "MERCK", "ABBVIE", "GENENTECH", "AMGEN", "NOVO NORDISK",
)


@pytest.mark.parametrize("filename", config.FEED_FILENAMES)
def test_no_feed_contains_a_real_organisation_name(full_feeds_dir, filename):
    """The confidentiality clause binds the generated data, not just the reference module.

    Scanned as raw file text rather than parsed-and-walked values, so a real name
    hiding in an unexpected column or a CSV header could not slip past a narrower
    field-by-field check.
    """
    text = (full_feeds_dir / filename).read_text(encoding="utf-8").upper()
    offenders = [name for name in REAL_ORG_NAMES if name in text]
    assert not offenders, f"{filename} contains real organisation name(s): {offenders}"


# ═══ I. Module isolation ═════════════════════════════════════════════════════════════

_FORBIDDEN_IMPORT_PREFIXES: tuple[str, ...] = (
    "recon.db",
    "recon.generators.orchestrator",
    "recon.generators.plan",
    "recon.generators.sampling",
)


def _imported_module_names(source: str) -> set[str]:
    tree = ast.parse(source)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


@pytest.mark.parametrize("module_name", ["pbm", "medical", "tpa", "bank", "contracts"])
def test_generator_modules_never_import_the_orchestrator_plan_sampling_or_db(
    repo_root, module_name
):
    """A generator that can reach the orchestrator can see the episode.

    Checked with ``ast`` over the source text, never by importing the module and
    inspecting ``sys.modules`` — importing would only prove the module *can* be
    loaded alongside the forbidden ones in the same interpreter, not that it declares
    a dependency on them.
    """
    path = repo_root / "src" / "recon" / "generators" / f"{module_name}.py"
    imported = _imported_module_names(path.read_text(encoding="utf-8"))
    offenders = [
        name
        for name in imported
        if any(name == prefix or name.startswith(prefix + ".") for prefix in _FORBIDDEN_IMPORT_PREFIXES)
    ]
    assert not offenders, f"{module_name}.py imports forbidden module(s): {offenders}"


def test_bank_generator_never_imports_pricing_or_names_an_expected_amount(repo_root):
    """The bank generator must never learn what was expected; it is a pure formatter.

    Two checks: no import of :mod:`recon.reference.pricing`, and no *identifier*
    (name, attribute, function or argument) containing "expected".  Identifiers only
    — not the module's own docstring, which discusses "the expected figure" at length
    as prose explaining why the generator is built this way.  Scanning the docstring
    text too would make this test fail for explaining itself.
    """
    path = repo_root / "src" / "recon" / "generators" / "bank.py"
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    imported = _imported_module_names(source)
    pricing_imports = [name for name in imported if "pricing" in name]
    assert not pricing_imports, f"bank.py imports pricing: {pricing_imports}"

    identifiers: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            identifiers.add(node.id)
        elif isinstance(node, ast.Attribute):
            identifiers.add(node.attr)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            identifiers.add(node.name)
            identifiers.update(arg.arg for arg in node.args.args)

    offenders = {name for name in identifiers if "expected" in name.lower()}
    assert not offenders, f"bank.py names something 'expected': {offenders}"


# ═══ J. Ground truth is write-only from the pipeline's perspective ══════════════════


def test_db_and_crosswalk_modules_never_reference_ground_truth(repo_root):
    """The ingestion layer and the engine are forbidden to read ``ground_truth``.

    That prohibition is what makes the crosswalk scoreable rather than assumed —
    checked as a plain text scan over every module under ``db/`` and ``crosswalk/``,
    because even a dead or commented-out reference would be a crack the prohibition
    is supposed to have none of.
    """
    offenders: list[str] = []
    for subdir in ("db", "crosswalk"):
        for path in sorted((repo_root / "src" / "recon" / subdir).rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            if "ground_truth" in text or "truth_dir" in text:
                offenders.append(str(path))
    assert not offenders, f"forbidden reference to ground truth found in: {offenders}"


# --- helpers ---


def _read_jsonl(path: Path) -> list[dict]:
    """Parse a ``.jsonl`` feed, reading money as ``Decimal`` rather than ``float``."""
    records: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(json.loads(line, parse_float=Decimal))
    return records


def _read_bank_csv(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _iter_string_leaves(obj, path: str = ""):
    """Yield ``(path, value)`` for every string leaf in a nested JSON-shaped object."""
    if isinstance(obj, dict):
        for key, value in obj.items():
            yield from _iter_string_leaves(value, f"{path}.{key}" if path else str(key))
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            yield from _iter_string_leaves(value, f"{path}[{index}]")
    elif isinstance(obj, str):
        yield path, obj


def _build_plans_and_timelines(settings: config.Settings):
    """Rebuild the plans and timelines :func:`orchestrator.generate` builds internally.

    Needed because :class:`~recon.generators.orchestrator.GenerationResult` does not
    expose timelines, and the private per-episode slice builders need both.  Mirrors
    ``generate()``'s own plan-building loop exactly, calling the same private helpers
    rather than reimplementing their logic, so this can never silently drift from
    what a real generation run does.
    """
    tree_dir = settings.repo_root / "decision_tree"
    catalogue = load_leaves(tree_dir)
    profile_name = str(settings.profile)
    curated = settings.selection is ProfileSelection.CURATED

    if curated:
        leaves = select_curated(catalogue, episode_count=settings.episode_count)
    else:
        leaves = select_configurations(
            catalogue,
            episode_count=settings.episode_count,
            master_seed=settings.master_seed,
            profile_name=profile_name,
        )

    plans = []
    timelines = {}
    for sequence, leaf in enumerate(leaves, start=1):
        episode_id = f"EP-{sequence:06d}"
        bindings = bind_entities(
            leaf, sequence=sequence, master_seed=settings.master_seed, profile_name=profile_name
        )
        bindings.update(
            orch._defect_directives(
                leaf,
                sequence=sequence,
                master_seed=settings.master_seed,
                profile_name=profile_name,
                curated=curated,
            )
        )
        service_day = orch._date_of_service(settings, sequence=sequence, profile_name=profile_name)
        quantity_milli = bindings["quantity_units"] * 1_000

        plan = EpisodePlan.build(
            episode_id=episode_id,
            leaf=leaf,
            bindings=bindings,
            date_of_service=service_day,
            quantity_milli=quantity_milli,
        )
        plans.append(plan)
        timelines[episode_id] = build_timeline(
            plan.configuration,
            date_of_service=service_day,
            verdicts=(plan.reimbursement_verdict, plan.rebate_verdict),
            rejection_reason=bindings.get("rejection_reason"),
            rng=rng_for(settings.master_seed, profile_name, "timeline", sequence),
        )
    return plans, timelines


def test_a_tpa_reversal_goes_on_the_wire_as_a_negative_quantity(full_result):
    """A reversal is a negative line, not a delete — and the sign is the whole point.

    The orchestrator hands the generator an already-negative quantity; an earlier version negated it
    again, putting a *positive* quantity on the wire. The record still said DISPENSE_REVERSAL, so
    nothing looked wrong, but the one property the feed spec insists on had quietly inverted.
    """
    reversals = [
        record.payload
        for record in full_result.records_by_feed["tpa_340b_events.jsonl"]
        if record.payload.get("event_type") == "DISPENSE_REVERSAL"
    ]
    assert reversals, "no TPA reversal was generated"
    for payload in reversals:
        assert payload["quantity_dispensed"] < 0, (
            f"reversal {payload['record_id']} carries a non-negative quantity "
            f"{payload['quantity_dispensed']}"
        )
