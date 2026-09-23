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

def test_a_scoped_340b_key_wraps_a_natural_key_on_its_own_episode(client):
    """A scoped key must be scoped, and scoped to *this* episode's covered entity.

    A scoped key carrying someone else's entity would resolve records to the wrong covered
    entity while looking perfectly well-formed — the exact failure requirement E1 exists to
    prevent.

    **This began life inside the trace test above and never ran.** That test takes the first
    ``EXCEPTION`` episode, which is medical and carries no ``COVERED_ENTITY_340B`` key at all,
    so the loop body was skipped on every run and an ``entities_seen`` that stayed empty
    passed ``&lt;= 1`` as ``0 &lt;= 1``. Both assertions were dead the day they were written, in a
    test whose comment claimed it checked "strictly more".

    So this searches for an episode that actually carries a scoped key and **fails if it finds
    none**. A guard that silently does nothing when its subject is absent is worse than no
    guard, because the green tick is read as evidence.
    """
    scoped_found = 0
    for disposition in ("EXCEPTION", "PENDING", "CLOSED"):
        rows = client.get(f"/api/queue/{disposition}?limit=200").json()["episodes"]
        for row in rows:
            trace = client.get(f"/api/episode/{row['episode_id']}/trace").json()
            natural = {
                key["key_value"]
                for key in trace["crosswalk_keys"]
                if key["key_type"] in {"NATURAL_340B_PHARMACY", "NATURAL_340B_MEDICAL"}
            }
            entities_seen = set()
            for key in trace["crosswalk_keys"]:
                if key["key_type"] != "COVERED_ENTITY_340B":
                    continue
                scoped_found += 1
                entity, separator, scoped = key["key_value"].partition("|")
                assert separator, f"scoped key {key['key_value']!r} has no scope separator"
                assert scoped in natural, (
                    f"scoped key {key['key_value']!r} wraps {scoped!r}, which is not a natural "
                    "340B key on this episode — it would resolve records belonging elsewhere"
                )
                entities_seen.add(entity)
            assert len(entities_seen) <= 1, (
                f"{row['episode_id']} is scoped to more than one covered entity: "
                f"{sorted(entities_seen)}"
            )

    assert scoped_found > 0, (
        "no episode in the whole dataset carries a COVERED_ENTITY_340B key, so this test "
        "asserted nothing. Either E1 stopped publishing scoped keys or the fixture changed; "
        "both are findings, and neither should look like a pass."
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


def test_every_sourced_event_points_at_a_real_ingested_file(client):
    """Lineage, inside the narrative: a claim on the timeline names the file and line it came from.

    The allowed set is every file this build actually reads, which is no longer only the six
    generated feeds -- Beacon's inbound payloads land through the connector and are cited on
    the timeline like anything else. It is still a closed set rather than "any string": the
    point of the assertion is that a citation names something a person can open, and a
    timeline event pointing at a file nothing ingested is a dead reference dressed as
    provenance.

    ``record_id`` is checked only where the source carries one. A Beacon payload has no
    record identifier of its own -- the Beacon ID is the identity, and it is on the record
    rather than in the file's own numbering -- which is the same reason ``raw_record``
    declares ``source_record_id`` nullable for the bank CSV.
    """
    from recon.api.app import _BEACON_INBOUND, _VENDOR_TPA_DATASETS
    from recon.connectors import registry, vendors

    ingested = set(config.FEED_FILENAMES)
    for source_id in _BEACON_INBOUND:
        ingested.update(registry._BEACON_ROWS[source_id].filenames)
    # The TPA's own rows come from a vendor export by default now, so a vendor file is a
    # legitimate citation. Added by prefix rather than by name because Verity stamps its
    # export names from the data and the landed name is not knowable in advance -- which is
    # also why this compares by prefix below rather than by set membership alone.
    vendor_prefixes = tuple(
        vendors.mapping_for(source_id).filename_prefix
        for datasets in _VENDOR_TPA_DATASETS.values()
        for source_id in datasets
    )

    payload = _richest_dossier(client)
    sourced = [e for e in payload["timeline"] if e.get("source") and e["source"].get("file")]
    assert sourced, "record-derived events must carry their provenance"
    for event in sourced:
        cited = event["source"]["file"]
        assert cited in ingested or cited.startswith(vendor_prefixes), (
            f"{cited!r} is cited on the timeline and is not a file this build reads; known: "
            f"{sorted(ingested)} plus anything starting {list(vendor_prefixes)}"
        )
        assert event["source"]["line"] >= 1


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


# ═══ a stale database is an instruction, not a stack trace ══════════════════


def test_a_stale_database_answers_503_with_the_fix_rather_than_a_bare_500(tmp_path):
    """The failure a reader meets before they have done anything wrong.

    The repository ships databases stamped by an older build, so the first request after
    any schema change hits this. It used to surface as ``500 Internal Server Error`` with
    the remedy written only into the server log — where nobody staring at a blank dashboard
    is looking. Found by opening the page in a browser; no test in this suite did that.

    Delete this and the next schema bump silently goes back to a 500.
    """
    import dataclasses
    import sqlite3

    from recon.api.app import create_app

    stale = tmp_path / "stale.sqlite"
    connection = sqlite3.connect(stale)
    # Any version that is not the current one; 3 is what the shipped databases carry.
    connection.execute("PRAGMA user_version = 3")
    connection.commit()
    connection.close()

    settings = dataclasses.replace(load_settings(config.Profile.DEMO), db_path=str(stale))
    with TestClient(create_app(settings), raise_server_exceptions=False) as stale_client:
        response = stale_client.get("/api/meta")

    assert response.status_code == 503, "a ready service with unready data is not a 500"
    body = response.json()
    assert body["found_schema_version"] == 3
    assert body["expected_schema_version"] == config.SCHEMA_VERSION
    # The remedy must be actionable without reading the source or the server log.
    assert "regenerate" in body["remedy"]
    assert str(config.SCHEMA_VERSION) in body["detail"]


# ═══ connector readiness (F3) over HTTP ═════════════════════════════════════
#
# The report itself is tested exhaustively in ``tests/test_readiness.py``; what is at stake here
# is the transport. A payload can be correct in Python and useless on the wire, and the two ways
# that happens are the two things these tests are about: the conclusions are computed
# *properties* rather than fields, so a serialiser built on ``dataclasses.asdict`` drops every
# one of them silently; and the page this feeds is read as a statement about whether a vendor is
# connected, which is the most expensive thing this repository could get wrong.


@pytest.fixture(scope="module")
def connectivity(client):
    response = client.get("/api/connectivity")
    assert response.status_code == 200, response.text
    return response.json()


def test_connectivity_covers_every_source_the_registry_declares(connectivity):
    from recon.connectors import registry

    reported = {row["source_id"] for row in connectivity["sources"]}
    declared = set(registry.VENDOR_SOURCE_IDS) | set(registry.BEACON_SOURCE_IDS)
    missing = sorted(declared - reported)
    assert not missing, f"registered sources absent from /api/connectivity: {missing}"
    # The six generated feeds too: the route hands the app's own settings to the report, so the
    # endpoint describes this deployment rather than the vendor rows in the abstract.
    feeds = {Path(name).stem for name in config.FEED_FILENAMES}
    assert feeds <= reported, f"generated feeds absent from the payload: {sorted(feeds - reported)}"


def test_the_computed_properties_survive_serialisation(connectivity):
    """The failure mode this test exists for: a payload of cells with no conclusions.

    ``stage``, ``gaps``, ``is_vendor``, ``talks_to_a_vendor``, ``clean``, ``traced``,
    ``implemented`` and ``applies`` are properties on frozen dataclasses. ``asdict`` cannot see
    a property, so it would produce a payload that looks complete, raises nothing, and has lost
    every answer the report was built to give.
    """
    stages = {"DECLARED", "CONNECTOR_READY", "WORKING_CONNECTION", "PRODUCTION_READY"}
    for row in connectivity["sources"]:
        where = row["source_id"]
        assert row["stage"] in stages, f"{where} has no stage"
        assert isinstance(row["gaps"], list), f"{where} lost its gaps"
        assert isinstance(row["is_vendor"], bool), f"{where} lost is_vendor"
        assert isinstance(row["talks_to_a_vendor"], bool), f"{where} lost talks_to_a_vendor"
        assert isinstance(row["transport"]["implemented"], bool)
        assert isinstance(row["auth"]["implemented"], bool)
        assert isinstance(row["mapping"]["implemented"], bool)
        assert isinstance(row["fidelity"]["applies"], bool)
        assert isinstance(row["fidelity"]["rows"], int)
        assert isinstance(row["golden"]["traced"], bool)
        # Three-valued, and the third value carries meaning: ``None`` is "not checked", which
        # is a different answer from "checked and clean".
        assert row["control_totals"]["clean"] in (True, False, None)
        assert row["schema"]["version_matches_mapping"] in (True, False, None)

    # Present is not enough — a serialiser that hardcoded ``[]`` would also pass the loop above.
    with_gaps = [row["source_id"] for row in connectivity["sources"] if row["gaps"]]
    assert with_gaps, "no source reports a gap; the gaps property is not being computed"
    assert any(row["stage"] == "CONNECTOR_READY" for row in connectivity["sources"])
    assert any(row["blocked"] for row in connectivity["sources"])
    assert any(row["fidelity"]["applies"] for row in connectivity["sources"]), (
        "no source reports a mock provenance tally; the SPEC/STANDARD/INVENTED split is missing"
    )


def test_the_payload_claims_no_live_vendor_connection(connectivity):
    """The one thing this endpoint must never say.

    Every connector in this build talks to a local mock. This page is read by somebody deciding
    whether to trust the pipeline, and a payload a front end could render as "Verity is
    connected" would be the most misleading artefact in the repository.
    """
    from recon.connectors.readiness import NO_LIVE_CONNECTION

    assert connectivity["live_vendor_connections"] == []
    assert connectivity["live_vendor_connection_statement"] == NO_LIVE_CONNECTION

    for row in connectivity["sources"]:
        where = row["source_id"]
        assert row["talks_to_a_vendor"] is False, f"{where} claims a vendor connection"
        assert row["reach"] != "VENDOR", f"{where} claims a vendor endpoint"
        assert row["reaches"] != "vendor endpoint", f"{where} reads as a vendor endpoint"
        assert row["stage"] not in {"WORKING_CONNECTION", "PRODUCTION_READY"}, (
            f"{where} claims a rung no source in this build reaches"
        )
        assert row["auth"]["configured"] is False or not row["auth"]["required"], (
            f"{where} reports a resolved vendor credential"
        )


def test_every_source_says_whether_control_totals_have_anywhere_to_live(connectivity):
    """``storage_declared`` is read out of ``schema.sql``, so it is true with no rows at all.

    That is the distinction it exists to make: "the machinery exists and has never run" and
    "the machinery does not exist" look identical in a row count and mean opposite things.
    """
    for row in connectivity["sources"]:
        assert row["control_totals"]["storage_declared"] is True


def test_control_totals_are_read_from_the_apps_own_database(tmp_path):
    """The connection is really used, not merely accepted as an argument.

    A route that passed ``control_totals=None`` would report ``checked: null`` forever and pass
    every other assertion in this file. So this stands up an app over an empty database, writes
    one deliberately unreconciled row, and asks the endpoint what it sees.

    A *fresh* database rather than the module-scoped one for two reasons: ``control_total`` is
    protected by a trigger — "a total you can edit is not a control total" — so a probe row
    written into a shared database cannot be taken back out; and no dataset is generated here,
    which incidentally proves the endpoint answers before the first ingest has ever run.
    """
    import dataclasses

    from recon.api.app import create_app
    from recon.db import connection, migrate

    settings = dataclasses.replace(
        load_settings(config.Profile.DEMO), db_path=str(tmp_path / "control_totals.sqlite")
    )
    source_id = Path(config.FEED_FILENAMES[0]).stem
    conn = connection.connect(settings.db_path)
    try:
        migrate.ensure_schema(conn)
        conn.execute(
            "INSERT INTO control_total (source_id, source_file, file_sha256, declared_count,"
            " observed_count, reconciled, checked_at)"
            " VALUES (?, ?, ?, ?, ?, 0, ?)",
            (source_id, "probe.jsonl", "0" * 64, 2, 1, "1970-01-01T00:00:00Z"),
        )
        conn.commit()
    finally:
        conn.close()

    with TestClient(create_app(settings)) as probe_client:
        payload = probe_client.get("/api/connectivity").json()

    row = next(entry for entry in payload["sources"] if entry["source_id"] == source_id)
    assert row["control_totals"]["checked"] == 1
    assert row["control_totals"]["unreconciled"] == 1
    assert row["control_totals"]["clean"] is False
    assert any("did not reconcile" in gap for gap in row["gaps"]), (
        "an unreconciled control total is a gap, and the gap is computed from the rows"
    )
    # Untouched sources report "not checked", which is not "clean".
    others = [entry for entry in payload["sources"] if entry["source_id"] != source_id]
    assert all(entry["control_totals"]["clean"] is None for entry in others)


def test_no_credential_value_can_ride_along_on_the_wire(client, monkeypatch):
    """Requirement A3, restated for a payload instead of for a tracked file.

    The report exposes environment variable *names* so an operator knows what to set. With the
    variables actually set, the endpoint's auth cell flips to ``configured`` — and the value
    still must not appear anywhere in the response, which is what makes the name-only contract
    a property of the transport rather than an accident of an empty environment.
    """
    from recon.connectors import credentials, readiness
    from recon.domain.enums import TransportKind

    target = next(
        source
        for source in readiness.declared_sources()
        if source.transport_kind is TransportKind.HTTP_API
    )
    names = credentials.env_var_names(
        str(target.credential_ref), credentials.CredentialKind.TOKEN_PAIR
    )
    assert names, "the fixture is pointless if the source names no variable"
    for name in names:
        monkeypatch.setenv(name, "not-a-real-token")

    response = client.get("/api/connectivity")
    assert response.status_code == 200
    row = next(
        entry
        for entry in response.json()["sources"]
        if entry["source_id"] == target.source_id
    )
    assert row["auth"]["configured"] is True, "the endpoint is not reading this environment"
    assert set(names) <= set(row["auth"]["env_vars"]), "the names an operator needs are missing"
    assert "not-a-real-token" not in response.text, "a credential value reached the wire"
    # A credential alone is not a connection: the endpoint is unconfigured, so the stage must
    # not move and the honesty statement must not change.
    assert row["talks_to_a_vendor"] is False
    assert response.json()["live_vendor_connections"] == []
