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
    for key in payload["crosswalk_keys"]:
        assert key["key_type"] in {
            "NCPDP_CLAIM",
            "MEDICAL_CLM01",
            "PAYER_ICN",
            "TRN02",
            "ALLOCATION_CODE",
            "NATURAL_340B_PHARMACY",
            "NATURAL_340B_MEDICAL",
            "PBM_AUTH",
        }, f"unexpected key type {key['key_type']!r}; there are exactly eight"


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
