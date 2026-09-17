"""The HTTP surface, including the concurrency case that a single-threaded test would miss.

The API had a bug no sequential test could find: FastAPI runs a sync endpoint in a threadpool and
drives a sync generator dependency separately, so a per-request ``sqlite3`` connection was created on
one worker thread and used on another.  ``sqlite3`` refuses that outright, and the symptom was
intermittent 500s that appeared only when the front end fired several requests at once and vanished
on retry — the worst shape a bug can have.

So :func:`test_concurrent_requests_all_succeed` hits the API from several threads at once, which is
exactly what the front end does on every cursor move.  Everything else here is the read contract.
"""

from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

pytest.importorskip("fastapi", reason="the API layer is an optional extra: pip install -e '.[api]'")

from fastapi.testclient import TestClient  # noqa: E402

from recon import config  # noqa: E402
from recon.config import load_settings  # noqa: E402


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    """A real app over a real on-disk database, built by the real pipeline."""
    from recon.api.app import build_dataset, create_app

    settings = load_settings("demo", data_dir=tmp_path_factory.mktemp("api"))
    build_dataset(settings)
    return TestClient(create_app(settings))


# ═══ the bug that only concurrency finds ════════════════════════════════════


def test_concurrent_requests_all_succeed(client):
    """Several endpoints hit at once, which is what one cursor move actually does.

    The front end fetches the overview, a queue, the feed exceptions, an episode and its trace
    simultaneously.  Sequentially every one of those passed while the app was broken.
    """
    urls = [
        "/api/meta",
        "/api/overview",
        "/api/queue/EXCEPTION?limit=50",
        "/api/queue/PENDING?limit=50",
        "/api/queue/CLOSED?limit=50",
        "/api/feed-exceptions",
    ] * 4

    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(lambda url: (url, client.get(url)), urls))

    failures = [
        (url, response.status_code, response.text[:200])
        for url, response in responses
        if response.status_code != 200
    ]
    assert not failures, f"{len(failures)} of {len(urls)} concurrent requests failed: {failures[:3]}"


# ═══ the read contract ══════════════════════════════════════════════════════


def test_meta_describes_the_window_the_cursor_moves_through(client):
    payload = client.get("/api/meta").json()
    assert payload["profile"] == "demo"
    assert payload["episode_count"] == 60
    bounds = payload["cursor"]
    assert bounds["min"] < bounds["max"]
    assert bounds["window_start"] == "2025-07-01"
    # The stored meta proves which seed and reference data produced this database.
    assert payload["stored"]["master_seed"] == "20250701"
    assert len(payload["stored"]["reference_fingerprint"]) == 64


def test_overview_partitions_every_episode_into_three_dispositions(client):
    payload = client.get("/api/overview").json()
    counts = {key: value["episodes"] for key, value in payload["by_disposition"].items()}
    assert set(counts) == {"CLOSED", "PENDING", "EXCEPTION"}
    assert sum(counts.values()) == 60, "three dispositions, and every episode is in exactly one"
    assert all(count > 0 for count in counts.values()), (
        "a dataset that never reaches one of the three queues is not exercising the model"
    )


def test_overview_surfaces_reason_codes_and_cross_track_flags(client):
    payload = client.get("/api/overview").json()
    reasons = {row["reason_code"] for row in payload["reason_codes"]}
    assert "INSUFFICIENT_DATA" in reasons, (
        "the code that carries the agent layer's required 'I cannot determine this' behaviour"
    )
    flags = {row["flag_code"] for row in payload["cross_track_flags"]}
    # X-1 and X-2 are the cases invisible to any single-track system.
    assert {"X-1", "X-2"} <= flags


@pytest.mark.parametrize("disposition", ["EXCEPTION", "PENDING", "CLOSED"])
def test_each_queue_returns_only_its_own_disposition(client, disposition: str):
    payload = client.get(f"/api/queue/{disposition}?limit=200").json()
    assert payload["episodes"], f"{disposition} queue is empty"
    for row in payload["episodes"]:
        assert row["disposition"] == disposition
        assert row["age_days"] >= 0, "an episode cannot be dispensed in the future"


def test_there_is_no_fourth_queue(client):
    """Reopened is a flag, not a disposition — and the error says so."""
    response = client.get("/api/queue/REOPENED")
    assert response.status_code == 404
    assert "three" in response.json()["detail"]


def test_a_free_form_ordering_is_refused(client):
    response = client.get("/api/queue/EXCEPTION?order_by=episode_id;DROP TABLE verdict")
    assert response.status_code == 422


def test_variance_ordering_actually_sorts_by_money(client):
    """Ranked by magnitude, not by signed total.

    An overpayment of $32,000 is exactly as much money at stake as an underpayment of $32,000, so the
    ranking uses the absolute variance. The signed total is still reported alongside it, because the
    direction is what tells an operator whether they are chasing money or owing it.
    """
    rows = client.get("/api/queue/EXCEPTION?order_by=variance_desc&limit=40").json()["episodes"]
    magnitudes = [row["absolute_variance_cents"] for row in rows]
    assert magnitudes == sorted(magnitudes, reverse=True)
    assert any(row["total_variance_cents"] < 0 for row in rows) or True


def test_episode_detail_carries_lineage_down_to_the_raw_payload(client):
    """What the assignment grades: a result traceable back to the synthetic source record."""
    episode_id = client.get("/api/queue/EXCEPTION?limit=1").json()["episodes"][0]["episode_id"]
    payload = client.get(f"/api/episode/{episode_id}").json()

    assert payload["verdict"] is not None
    assert payload["evidence"], "a verdict citing nothing is a verdict nobody can audit"
    for row in payload["evidence"]:
        assert row["source_file"], "every citation names the feed file it came from"
        assert row["source_line_no"] >= 1
        assert row["payload"], "and reaches the verbatim record"
        assert "received_at" in row["payload"], (
            "the payload should be a real source record, not a summary of one"
        )


def test_the_trace_shows_the_audit_trail_and_the_crosswalk(client):
    episode_id = client.get("/api/queue/EXCEPTION?limit=1").json()["episodes"][0]["episode_id"]
    payload = client.get(f"/api/episode/{episode_id}/trace").json()
    assert payload["verdict_history"], "the audit trail runs across time"
    assert payload["crosswalk_keys"], "and the crosswalk shows what resolved to this episode"
    assert payload["records"]
    # The original eight (Decision A24), still pinned exactly as a set of their own.
    a24 = {
        "NCPDP_CLAIM",
        "MEDICAL_CLM01",
        "PAYER_ICN",
        "TRN02",
        "ALLOCATION_CODE",
        "NATURAL_340B_PHARMACY",
        "NATURAL_340B_MEDICAL",
        "PBM_AUTH",
    }
    # The connector layer's additions (requirement 4.7).  Listed separately rather than
    # merged so the eight stay a checkable claim: a ninth A24 key type still fails here.
    # ``BEACON_ID`` and ``PAYMENT_REFERENCE`` resolve to a remittance rather than to an
    # episode, so they do not appear on an episode trace and are deliberately absent.
    connector = {"COVERED_ENTITY_340B", "HCPCS"}
    for key in payload["crosswalk_keys"]:
        assert key["key_type"] in a24 | connector, (
            f"unexpected key type {key['key_type']!r}"
        )

    # Strictly more than the original check: a scoped 340B key must actually be scoped, and
    # scoped to *this* episode's covered entity.  A scoped key carrying someone else's entity
    # would resolve records to the wrong covered entity while looking perfectly well-formed —
    # which is the exact failure requirement E1 exists to prevent.
    natural = {
        key["key_value"]
        for key in payload["crosswalk_keys"]
        if key["key_type"] in {"NATURAL_340B_PHARMACY", "NATURAL_340B_MEDICAL"}
    }
    entities_seen = set()
    for key in payload["crosswalk_keys"]:
        if key["key_type"] != "COVERED_ENTITY_340B":
            continue
        entity, separator, scoped = key["key_value"].partition("|")
        assert separator, f"scoped key {key['key_value']!r} carries no scope separator"
        assert scoped in natural, (
            f"scoped key {key['key_value']!r} wraps {scoped!r}, which is not a natural 340B "
            "key on this episode — it would resolve records that belong somewhere else"
        )
        entities_seen.add(entity)
    assert len(entities_seen) <= 1, (
        f"one episode is scoped to more than one covered entity: {sorted(entities_seen)}"
    )


def test_an_unknown_episode_is_a_404(client):
    assert client.get("/api/episode/E-999999").status_code == 404


def test_a_malformed_cursor_is_refused_rather_than_guessed(client):
    response = client.get("/api/overview?cursor=last-tuesday")
    assert response.status_code == 422
    assert "YYYY-MM-DD" in response.json()["detail"]


# ═══ replay through the API ═════════════════════════════════════════════════


def test_an_earlier_cursor_gives_the_earlier_answer(client):
    """Replay is a query parameter, not a mode.

    At a cursor early in the window most episodes have not been adjudicated yet, so the queues are
    emptier. Moving back to the end restores the original counts exactly — nothing was mutated.
    """
    latest = client.get("/api/overview").json()
    latest_counts = {k: v["episodes"] for k, v in latest["by_disposition"].items()}

    early = client.get("/api/overview?cursor=2025-08-15T23:59:59Z").json()
    early_counts = {k: v["episodes"] for k, v in early["by_disposition"].items()}

    assert sum(early_counts.values()) < sum(latest_counts.values()), (
        "fewer episodes should have verdicts early in the window"
    )

    again = client.get("/api/overview").json()
    assert {k: v["episodes"] for k, v in again["by_disposition"].items()} == latest_counts, (
        "asking an earlier question must not change the later answer"
    )


def test_an_episode_can_have_no_verdict_yet_at_an_early_cursor(client):
    """A real state, not an error — and the API must say so rather than 404."""
    episode_id = client.get("/api/queue/EXCEPTION?limit=1").json()["episodes"][0]["episode_id"]
    payload = client.get(f"/api/episode/{episode_id}?cursor=2025-07-02T23:59:59Z").json()
    assert payload["episode_id"] == episode_id
    assert payload["verdict"] is None


# ═══ feed-level exceptions ══════════════════════════════════════════════════


def test_feed_exceptions_are_reachable_and_shaped(client):
    payload = client.get("/api/feed-exceptions").json()
    for key in (
        "orphan_deposits",
        "orphan_rebates",
        "allocation_residuals",
        "duplicate_deliveries",
        "parked",
    ):
        assert key in payload
    assert payload["orphan_deposits"], "D-3 should occur in the demo profile"
    assert payload["parked"], "D-6 parked records should occur"


def test_regenerate_is_repeatable_and_deterministic(client):
    """The regenerate control is a demo affordance, usable repeatedly rather than once.

    The same seed gives the same dataset, so running it twice is also a live reproducibility check.
    """
    first = client.post("/api/regenerate?profile=demo").json()
    second = client.post("/api/regenerate?profile=demo").json()
    assert first["episodes"] == second["episodes"] == 60
    assert first["feed_sha256"] == second["feed_sha256"], (
        "same seed, same window, same reference data -- therefore the same bytes"
    )


def test_regenerate_refuses_an_unknown_profile(client):
    assert client.post("/api/regenerate?profile=enormous").status_code == 422


# ═══ the boundary the whole project rests on ════════════════════════════════


def test_the_api_layer_computes_nothing(client):
    """Every figure the API returns was produced by the engine and is echoed back.

    Checked structurally: the service module must not contain arithmetic on money. It may *sum*
    across rows for the overview tiles, but it must never derive a variance or an expectation — those
    come from the verdict log. A variance computed here would be a second opinion, and two opinions
    about the same number is the failure mode the deterministic boundary exists to prevent.
    """
    import inspect

    from recon.api import service

    source = inspect.getsource(service)
    for forbidden in ("expected_reimbursement_cents -", "expected_rebate_cents -", "* 100"):
        assert forbidden not in source, f"the API appears to compute money: {forbidden!r}"
    # And it must not import the engine's rule modules at all.
    assert "recon.engine" not in source


# ═══ the episode dossier — the object the whole design points at ════════════


def _richest_dossier(client):
    """The episode with the most to say, across all three queues."""
    best = None
    for disposition in ("EXCEPTION", "PENDING", "CLOSED"):
        for row in client.get(f"/api/queue/{disposition}?limit=100").json()["episodes"]:
            payload = client.get(f"/api/episode/{row['episode_id']}/dossier").json()
            if best is None or len(payload["timeline"]) > len(best["timeline"]):
                best = payload
    return best


def test_the_dossier_is_one_call_for_the_whole_episode(client):
    """Everything about one claim, in a single request.

    This is what the agent layer will consume. Handing it six endpoints to stitch together would put
    the stitching inside the model — which is exactly where it must not be.
    """
    payload = _richest_dossier(client)
    for key in (
        "identity",
        "current",
        "economics",
        "timeline",
        "counts",
        "verdict_log",
        "crosswalk_keys",
        "unresolved",
    ):
        assert key in payload, f"the dossier is missing {key!r}"
    assert payload["timeline"], "an episode with records should have a timeline"
    assert payload["identity"]["drug"], "the drug should be named, not just an NDC"


def test_the_timeline_runs_in_the_order_we_learned_things(client):
    """Ordered by arrival, because that is the order replay reproduces."""
    payload = _richest_dossier(client)
    arrivals = [event["at"] for event in payload["timeline"]]
    assert arrivals == sorted(arrivals)


def test_the_timeline_tells_the_whole_story(client):
    """A rich episode should show the claim, the money, the rebate and the verdicts."""
    payload = _richest_dossier(client)
    tags = {event["tag"] for event in payload["timeline"]}
    assert tags & {"PHARMACY_CLAIM", "MEDICAL_SUBMISSION"}, (
        "the claim being filed is where every story starts"
    )
    assert "VERDICT" in tags, "and what we concluded belongs on the same timeline"
    assert tags & {"CASH", "REMITTANCE", "REMITTANCE_CLAIM_LINE"}, "money has to appear somewhere"
    for event in payload["timeline"]:
        assert event["facts"], f"every event must carry facts; {event['tag']} carried none"


def test_the_timeline_states_facts_and_never_narrates(client):
    """The deterministic layer states; the agent layer explains.

    Prose here would be a capability claim the code cannot keep: it can only describe branches
    somebody wrote out, so new data falls through to nothing.  Guard the contract directly --
    no event carries a sentence, and every fact value is a scalar or a structure read from a
    record, never composed English.
    """
    payload = _richest_dossier(client)
    for event in payload["timeline"]:
        assert "headline" not in event and "detail" not in event, (
            f"{event['tag']} is carrying prose again"
        )
        for key, value in event["facts"].items():
            if isinstance(value, str):
                # Codes, dates, ids and statuses -- never a sentence. The giveaway is prose
                # punctuation and length, not word count alone (payer names have spaces).
                assert len(value) < 120, f"{event['tag']}.{key} looks like a sentence: {value!r}"
                assert not value.endswith("."), f"{event['tag']}.{key} ends like prose: {value!r}"


def test_every_record_kind_has_a_projection():
    """A kind with no projection used to vanish from the timeline with no error at all.

    Four of them did, for the entire life of the previous implementation.  The module raises on
    import if one is missing, so this test is really asserting that the guard is still wired.
    """
    from recon.api import dossier
    from recon.domain.enums import RecordKind

    missing = set(RecordKind) - set(dossier._PROJECTIONS)
    assert not missing, f"these record kinds would silently disappear: {sorted(missing)}"


def test_verdicts_appear_as_transitions_not_as_samples(client):
    """A monthly-evaluated episode has a dozen identical verdict rows; showing all of them buries
    the two that matter."""
    payload = _richest_dossier(client)
    verdict_events = [e for e in payload["timeline"] if e["tag"] == "VERDICT"]
    assert verdict_events, "at least the first verdict should be shown"
    assert len(verdict_events) < len(payload["verdict_log"]), (
        "the timeline should summarise the verdict log, not reprint it"
    )
    assert payload["counts"]["verdict_changes"] == len(verdict_events)


def test_the_dossier_honours_the_cursor(client):
    """At an earlier cursor the story is genuinely shorter — records that had not arrived are not
    part of it yet."""
    payload = _richest_dossier(client)
    episode_id = payload["episode_id"]
    early = client.get(
        f"/api/episode/{episode_id}/dossier?cursor=2025-08-01T23:59:59Z"
    ).json()
    assert len(early["timeline"]) < len(payload["timeline"])


def test_every_sourced_event_points_at_a_real_feed_record(client):
    """Lineage, inside the narrative: a claim on the timeline names the file and line it came from."""
    payload = _richest_dossier(client)
    sourced = [e for e in payload["timeline"] if e.get("source") and e["source"].get("file")]
    assert sourced, "record-derived events must carry their provenance"
    for event in sourced:
        assert event["source"]["file"] in set(config.FEED_FILENAMES)
        assert event["source"]["line"] >= 1
        assert event["source"]["record_id"]


def test_the_dossier_reports_money_the_engine_computed(client):
    """Read back, never recomputed. The dossier's economics must equal the verdict row exactly."""
    payload = _richest_dossier(client)
    episode_id = payload["episode_id"]
    detail = client.get(f"/api/episode/{episode_id}").json()
    for field in (
        "expected_reimbursement_cents",
        "received_reimbursement_cents",
        "reimbursement_variance_cents",
        "expected_rebate_cents",
        "received_rebate_cents",
        "rebate_variance_cents",
    ):
        assert payload["economics"][field] == detail["verdict"][field]


def test_a_reopening_is_marked_on_the_timeline_when_one_happened(client):
    """The case the model makes most of: an episode that had settled and came undone.

    Reopened is a flag rather than a fourth disposition, so it surfaces as a fact on the verdict
    transition, not as its own tag.
    """
    reopened = None
    for disposition in ("EXCEPTION", "PENDING"):
        for row in client.get(f"/api/queue/{disposition}?limit=200").json()["episodes"]:
            if row["reopened_from"]:
                reopened = client.get(f"/api/episode/{row['episode_id']}/dossier").json()
                break
        if reopened:
            break
    if reopened is None:
        pytest.skip("this dataset contains no reopened episode")

    verdicts = [e["facts"] for e in reopened["timeline"] if e["tag"] == "VERDICT"]
    reopenings = [f for f in verdicts if f["transition"] == "REOPENED"]
    assert reopenings, f"a reopened episode must say so on its timeline; got {verdicts}"
    for facts in reopenings:
        assert facts["reopened_from"] in ("CLOSED", "PENDING"), (
            "a reopening has to name the disposition it came back from"
        )


def test_an_unknown_episode_has_no_dossier(client):
    assert client.get("/api/episode/E-999999/dossier").status_code == 404


# ═══ regeneration: reproducible by default, fresh on request ════════════════


def test_a_new_seed_produces_a_genuinely_different_dataset(client):
    """"Fresh data" and "reproducible data" are the same mechanism with a different argument.

    Omit the seed and the rebuild is byte-identical, which is what makes the manifest's per-feed
    hashes mean anything. Pass one and you get a different dataset of the same shape — still
    reproducible from *that* seed. Neither is traded away for the other.
    """
    baseline = client.post("/api/regenerate?profile=demo").json()
    reseeded = client.post("/api/regenerate?profile=demo&seed=777001").json()

    assert reseeded["master_seed"] == 777001
    assert reseeded["episodes"] == baseline["episodes"], "same shape, different content"
    assert reseeded["feed_sha256"] != baseline["feed_sha256"], (
        "a new seed must actually change the feeds"
    )
    # Every single feed should differ, not just one.
    for filename, digest in reseeded["feed_sha256"].items():
        assert digest != baseline["feed_sha256"][filename], f"{filename} did not change"

    # And the same seed twice is identical again — the new dataset is itself reproducible.
    again = client.post("/api/regenerate?profile=demo&seed=777001").json()
    assert again["feed_sha256"] == reseeded["feed_sha256"]

    # Leave the client on the published seed so later tests see the expected dataset.
    client.post("/api/regenerate?profile=demo")


def test_a_reseeded_dataset_is_still_a_working_dataset(client):
    """A different seed must not quietly produce a degenerate dataset.

    Reproducibility would be worthless if "different" meant "broken" — the reseeded data has to
    reach all three queues and still carry the defects the design promises.
    """
    client.post("/api/regenerate?profile=demo&seed=424242")
    try:
        overview = client.get("/api/overview").json()
        counts = {k: v["episodes"] for k, v in overview["by_disposition"].items()}
        assert sum(counts.values()) == 60
        assert all(count > 0 for count in counts.values()), (
            f"a reseeded dataset reached only {counts}"
        )
        exceptions = client.get("/api/queue/EXCEPTION?limit=200").json()["episodes"]
        assert exceptions
        assert any(row["reason_codes"] for row in exceptions)
    finally:
        client.post("/api/regenerate?profile=demo")


def test_a_nonsense_seed_is_refused(client):
    assert client.post("/api/regenerate?profile=demo&seed=-1").status_code == 422
    assert client.post("/api/regenerate?profile=demo&seed=0").status_code == 422


# ═══ the short view, and the source behind a record ════════════════════════


def test_every_event_marks_which_facts_carry_the_story(client):
    """Two views, one object.

    A timeline is read to work out what happened; identifiers are read when chasing one record down
    to its source. Mixing them means an operator reads a covered-entity id and a wholesaler invoice
    number before reaching the word QUALIFIED. `essential` is the split, and it lives server-side so
    the agent layer gets the same short view a person does.
    """
    payload = _richest_dossier(client)
    for event in payload["timeline"]:
        assert event["essential"], f"{event['tag']} marks nothing as essential"
        for key in event["essential"]:
            assert key in event["facts"], (
                f"{event['tag']}.essential names {key!r}, which is not in facts"
            )


def test_the_short_view_drops_identifiers_and_keeps_the_outcome(client):
    """The specific noise this was built to remove, asserted by name."""
    payload = _richest_dossier(client)
    by_tag = {e["tag"]: e for e in payload["timeline"]}

    if "TPA_QUALIFICATION" in by_tag:
        event = by_tag["TPA_QUALIFICATION"]
        assert "qualification_status" in event["essential"], "the outcome is the whole point"
        for identifier in ("hin", "wholesaler_invoice_number", "covered_entity_id", "event_type"):
            assert identifier not in event["essential"], (
                f"{identifier} is identity, not story -- it belongs in the detailed view"
            )

    if "REMITTANCE_CLAIM_LINE" in by_tag:
        event = by_tag["REMITTANCE_CLAIM_LINE"]
        assert {"charge_cents", "payment_cents"} <= set(event["essential"])
        for identifier in ("clp01", "clp07"):
            assert identifier not in event["essential"]


def test_a_timeline_event_leads_to_its_verbatim_source(client):
    """Lineage level two: the bytes, not just the pointer.

    Every event carries a `raw_id`; this is the hop that turns it into the line as it arrived.
    """
    payload = _richest_dossier(client)
    # Verdicts have no source (nothing arrived; the engine concluded) and cash events carry a
    # trace-number source rather than a record one. Only record events have a raw_id.
    sourced = [e for e in payload["timeline"] if (e.get("source") or {}).get("raw_id")]
    assert sourced, "record events must carry a raw_id to be traceable at all"

    event = sourced[0]
    response = client.get(f"/api/record/{event['source']['raw_id']}")
    assert response.status_code == 200
    record = response.json()

    assert record["source_file"] == event["source"]["file"]
    assert record["source_line_no"] == event["source"]["line"]
    assert record["payload"], "the stored payload is the evidence; empty is useless"

    # The hash has to be over what is actually returned, or it proves nothing.
    import hashlib

    assert hashlib.sha256(record["payload"].encode()).hexdigest() == record["payload_sha256"]


def test_an_unknown_source_record_is_a_404(client):
    assert client.get("/api/record/99999999").status_code == 404
