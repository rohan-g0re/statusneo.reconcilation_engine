"""Beacon: the only bidirectional connector in the whole assessment.

Requirements C1–C5 and A2's HTTP half — Doc 2 steps 2 to 4.  Every other source in the
document is a pull; Beacon is the one that submits, which is why it is also the one where
getting configuration wrong costs money rather than data.

Three tests here matter more than the rest.

**Mode B must make the outbound path unreachable, not merely skipped** (C4).  An
``if mode is A:`` guard is one inverted condition away from a second rebate claimed on one
dispense, and ``VERITY-004`` shows this is not hypothetical — a shipping TPA offers the
covered entity exactly this choice in its own words.  So the test does not check that
submission *is not called*; it checks that a submission *cannot be constructed*.

**A rejection reason must survive byte for byte** (C3).  Beacon's real validation vocabulary
is unknown — ``BEACON-003``, the code glossary, is one of seven 403s — so translating our
reason into a Beacon-shaped code would be inventing the one thing we are least able to
check.

**The Beacon ID in a response must be the one the orchestrator minted** (M1, C5).  A mock
that minted its own would be inventing a fact the rest of the system then accepts on faith,
and the crosswalk built on it would be testing the mock's imagination.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

httpx = pytest.importorskip("httpx", reason="the 'agent' extra provides httpx")

from recon.config import CuratedSpine, load_settings
from recon.connectors import registry
from recon.connectors.http import HttpApiTransport
from recon.connectors.transport import ForbiddenPathError
from recon.connectors.vendors import beacon
from recon.db import connection, migrate
from recon.domain.enums import KeyType, ReasonCode
from recon.generators import orchestrator
from recon.mocks import beacon_payloads, beacon_server, source as source_module

pytestmark = pytest.mark.filterwarnings("ignore::ResourceWarning")


@pytest.fixture(scope="module")
def vendor_source(tmp_path_factory):
    settings = load_settings(
        "demo",
        data_dir=tmp_path_factory.mktemp("api-pattern"),
        curated_spine=CuratedSpine.RECORDED,
    )
    orchestrator.write_outputs(settings, orchestrator.generate(settings))
    return source_module.load_source(settings)


@pytest.fixture()
def server(vendor_source):
    with beacon_server.LoopbackBeaconServer(vendor_source) as running:
        yield running


@pytest.fixture()
def db() -> sqlite3.Connection:
    handle = connection.connect(":memory:")
    migrate.ensure_schema(handle)
    try:
        yield handle
    finally:
        handle.close()


def _paid(vendor_source):
    """A dispense that reached a manufacturer decision, so every leg has something to say."""
    for dispense in vendor_source.by_archetype("paid"):
        if dispense.rx_number:  # pharmacy, so the natural key is unambiguous
            return dispense
    pytest.skip("no paid pharmacy dispense in the demo profile")


# ═══ C1 — the mock serves the documented surface ═══


def test_the_mock_requires_both_tokens(server, vendor_source):
    """``BEACON-011``'s two-token model, enforced rather than described.

    Two distinct rejection codes rather than one blanket 401, deliberately: a client that
    sent only the access token would otherwise pass its own tests while being wrong in the
    one way the two-token model exists to prevent.
    """
    creds = server.credentials()
    tokens = httpx.post(f"{server.base_url}{beacon_server.TOKEN_PATH}", json=creds).json()

    only_one = httpx.get(
        f"{server.base_url}/claims/{server.known_beacon_ids()[0]}",
        headers={beacon_server.ACCESS_TOKEN_HEADER: tokens["access_token"]},
    )
    assert only_one.status_code >= 400
    assert only_one.json()["error"] == "missing_private_token"


def test_a_read_only_partner_cannot_submit(server, vendor_source):
    """``BEACON-012``: permission is Read or Read/Write, and it is granted per 340B ID."""
    creds = server.credentials(beacon_server.READ_ONLY_PARTNER_ID)
    tokens = httpx.post(f"{server.base_url}{beacon_server.TOKEN_PATH}", json=creds).json()
    headers = {
        beacon_server.ACCESS_TOKEN_HEADER: tokens["access_token"],
        beacon_server.PRIVATE_TOKEN_HEADER: tokens["private_token"],
    }
    dispense = _paid(vendor_source)
    refused = httpx.post(
        f"{server.base_url}{beacon_server.CLAIMS_PATH}",
        headers=headers,
        json=beacon_payloads.submission(dispense),
    )
    assert refused.status_code >= 400
    assert refused.json()["error"] == "read_only_permission"

    # ...but the same partner may still read, or "Read" would mean nothing.
    allowed = httpx.get(f"{server.base_url}/claims/{dispense.beacon_id}", headers=headers)
    assert allowed.status_code == 200


def test_the_mock_never_puts_a_wall_clock_on_the_wire(server, vendor_source):
    """§4.11. A token that expired on a clock could not be tested at all.

    A fast suite would never reach the expiry and a slow machine would reach it in the middle
    of an unrelated assertion, so the budget is a request counter and ``issued_at`` is the
    same pinned epoch ``ingest_batch.loaded_at`` uses.
    """
    creds = server.credentials()
    response = httpx.post(f"{server.base_url}{beacon_server.TOKEN_PATH}", json=creds)
    assert response.json()["issued_at"] == beacon_server.ISSUED_AT
    assert "Date" not in response.headers and "Server" not in response.headers


def test_every_declared_validation_outcome_is_reachable_over_http(server, vendor_source):
    """C1's acceptance: every Beacon-driven outcome the engine already models.

    Asserted as equality rather than containment — a vocabulary wider than the data can
    produce is a mock that has learned to say something no record supports.
    """
    creds = server.credentials()
    tokens = httpx.post(f"{server.base_url}{beacon_server.TOKEN_PATH}", json=creds).json()
    headers = {
        beacon_server.ACCESS_TOKEN_HEADER: tokens["access_token"],
        beacon_server.PRIVATE_TOKEN_HEADER: tokens["private_token"],
    }
    seen = set()
    for beacon_id in server.known_beacon_ids():
        response = httpx.get(f"{server.base_url}/claims/{beacon_id}/validation", headers=headers)
        if response.status_code == 200:
            seen.add(response.json()["outcome"])
    assert seen == set(beacon_payloads.VALIDATION_OUTCOMES)


# ═══ A2 — the HTTP transport, through the same pipeline seam ═══


def test_the_http_transport_refuses_a_ground_truth_base_url():
    """§4.9 is uniform across transports, including the one where a path is a URL."""
    with pytest.raises(ForbiddenPathError):
        HttpApiTransport("http://127.0.0.1:9/truth")


def test_a_missing_credential_names_the_variable_to_set():
    """A3's acceptance on the third transport: a named failure, never a stack trace."""
    transport = HttpApiTransport("http://127.0.0.1:9", env={})
    source = registry.Source(
        source_id="beacon_status",
        vendor="beacon",
        transport_kind=registry.TransportKind.HTTP_API,
        source_system=registry.SourceSystem.BEACON,
        filenames=("v1/claims/status",),
        endpoint="",
        credential_ref="beacon_http_api",
        transport=transport,
    )
    with pytest.raises(Exception) as caught:
        list(transport.fetch(source))
    message = str(caught.value)
    assert "RECON_CONNECTOR_" in message, "the message must name what to set"
    assert "httpx" not in type(caught.value).__module__


# ═══ C2 / C5 — outbound, and the Beacon ID as a key ═══


def test_a_submitted_claim_comes_back_with_the_id_the_orchestrator_minted(server, vendor_source):
    """M1 and C2 together, which is the pair that makes the crosswalk meaningful.

    If the mock minted its own identifier the join downstream would still "work" — it would
    simply be joining the mock to itself.
    """
    creds = server.credentials()
    tokens = httpx.post(f"{server.base_url}{beacon_server.TOKEN_PATH}", json=creds).json()
    headers = {
        beacon_server.ACCESS_TOKEN_HEADER: tokens["access_token"],
        beacon_server.PRIVATE_TOKEN_HEADER: tokens["private_token"],
    }
    dispense = _paid(vendor_source)
    response = httpx.post(
        f"{server.base_url}{beacon_server.CLAIMS_PATH}",
        headers=headers,
        json=beacon_payloads.submission(dispense),
    )
    assert response.status_code == 200
    assert response.json()["beacon_id"] == dispense.beacon_id


def test_resubmitting_the_same_claim_is_idempotent(server, vendor_source):
    """The same claim submitted twice must not become two rebates.

    The server says which it was in a header rather than by status code, so a client never
    has to branch on 200-versus-201 to find out whether it just double-claimed.
    """
    creds = server.credentials()
    tokens = httpx.post(f"{server.base_url}{beacon_server.TOKEN_PATH}", json=creds).json()
    headers = {
        beacon_server.ACCESS_TOKEN_HEADER: tokens["access_token"],
        beacon_server.PRIVATE_TOKEN_HEADER: tokens["private_token"],
    }
    dispense = _paid(vendor_source)
    body = beacon_payloads.submission(dispense)
    url = f"{server.base_url}{beacon_server.CLAIMS_PATH}"

    first = httpx.post(url, headers=headers, json=body)
    second = httpx.post(url, headers=headers, json=body)
    assert first.json()["beacon_id"] == second.json()["beacon_id"]
    assert second.headers[beacon_server.SUBMISSION_REPEAT_HEADER] == "true"


def test_the_beacon_id_is_published_as_a_crosswalk_key():
    """C5. ``KeyType.BEACON_ID`` exists and the adapter builds a key from it."""
    key_type, key_value = beacon.beacon_id_key("BCN-000000000001")
    assert key_type is KeyType.BEACON_ID
    assert key_value == "BCN-000000000001"


# ═══ C3 — the vendor's reason, verbatim ═══


def test_a_rejection_keeps_the_vendors_reason_byte_for_byte(vendor_source):
    """C3's acceptance, and the reason it is worded that way.

    ``BEACON-003`` — the pharmacy claims validation code glossary — is one of seven Beacon
    articles that returns HTTP 403. We do not know Beacon's code vocabulary, so mapping our
    reason onto a Beacon-shaped one would be inventing precisely the thing we cannot check.
    """
    rejected = [
        dispense
        for dispense in vendor_source.by_archetype("rejected")
        if dispense.rejection_reason
    ]
    if not rejected:
        pytest.skip("no rejected dispense carries a reason in this profile")

    dispense = rejected[0]
    payload = beacon_payloads.validation_outcome(dispense)
    assert payload["reason_code"] == dispense.rejection_reason, (
        "the reason was translated somewhere between the feed and the Beacon payload"
    )
    assert beacon.REJECTION_REASON_CODE is ReasonCode.REBATE_REJECTED


# ═══ C4 — mode B is unreachable, not skipped ═══


def test_mode_b_makes_the_outbound_path_unconstructable(vendor_source):
    """C4's acceptance as written: *a test asserts no submission call can be constructed.*

    Not "is not called" — *cannot be built*. An ``if`` is one inverted condition or one new
    call site away from claiming the same rebate twice, and ``DOC2-014`` names duplicate
    submission as the specific harm. ``VERITY-004`` is the first-hand corroboration that a
    covered entity really is offered this choice.
    """
    dispense = _paid(vendor_source)
    entity = dispense.covered_entity_id

    policy = beacon.SubmissionOwnershipPolicy(
        {entity: beacon.SubmissionOwnership.TPA_MANAGED}
    )
    authority = policy.route(entity)

    # Stronger than a missing attribute: reaching for it raises a named domain error that
    # says what the harm would be. `hasattr` therefore propagates rather than returning
    # False, which is why this is a `raises` and not an `assert not hasattr`.
    with pytest.raises(beacon.SubmissionNotOursError) as caught:
        authority.submission
    assert "twice" in str(caught.value), (
        "the refusal should name the harm — a dispense claimed for a rebate twice — not "
        "merely report an absent attribute"
    )


def test_mode_a_can_construct_a_submission(vendor_source):
    """The mirror, so the test above is not passing because nothing works at all.

    Mode A needs a registry row and mode B does not, which is the right asymmetry: mode A
    posts to an endpoint under a named credential and both live on the source row, while a
    mode-B entity has no endpoint to configure and asking for one would be asking an
    operator to set up a leg that must not exist.
    """
    dispense = _paid(vendor_source)
    entity = dispense.covered_entity_id
    outbound = registry.by_id(
        registry.beacon_sources({registry.BEACON_SUBMISSION_SOURCE_ID: "https://example.invalid"}),
        registry.BEACON_SUBMISSION_SOURCE_ID,
    )
    policy = beacon.SubmissionOwnershipPolicy(
        {entity: beacon.SubmissionOwnership.SHIELDS_DIRECT}
    )
    authority = policy.route(entity, outbound)
    assert hasattr(authority, "submission")


def test_an_entity_with_no_configured_ownership_is_refused(vendor_source):
    """No default, and that is the decision.

    Defaulting to mode A submits on behalf of a covered entity nobody decided about.
    Defaulting to mode B submits nothing and looks perfectly healthy. Neither is a safe
    thing to guess, so it is not guessed.
    """
    policy = beacon.SubmissionOwnershipPolicy({})
    with pytest.raises(Exception):
        policy.route("DSH999999")
