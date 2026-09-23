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

import csv
import re
import sqlite3
from pathlib import Path

import pytest

httpx = pytest.importorskip("httpx", reason="the 'agent' extra provides httpx")

from recon.config import CuratedSpine, load_settings
from recon.connectors import registry
from recon.connectors.http import HttpApiTransport
from recon.connectors.transport import ForbiddenPathError
from recon.connectors.vendors import beacon
from recon.crosswalk import keys
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


# ═══ C2 / M1 — the round trip the connector has claimed since it was written ═════════
#
# ``beacon._template_body``'s docstring says its field names *"match what
# ``recon.mocks.beacon_payloads.submission`` writes, and the two are kept in step by a
# round-trip test rather than by an import."*  There was no such test.  Every live call
# above POSTs ``beacon_payloads.submission(dispense)`` — the mock's own output, to the
# mock's own index — so the connector's copy of the template never touched the wire and was
# free to drift.  It drifted twice, and both are recorded in the graph as the trap *"a wire
# format agreed in two places and matched in neither"*: the date went out ISO where the
# mock indexes on ``CCYYMMDD``, and the declared endpoint paths (``/v1/claims/pharmacy``,
# ``/v1/claims/medical``) 404ed against our own router.
#
# These six tests are the second reader that docstring assumes.  Two properties make them
# worth more than a comparison of two literals:
#
# * the published column list is **parsed out of Beacon's own downloaded template files**
#   rather than typed here, so a rename on either side is checked against the vendor's
#   bytes instead of against a third copy that can drift with the first two;
# * the claim is built through :meth:`beacon.TpaQualifiedClaim.from_canonical` on a record
#   in ``normalized_record``'s spelling, because that is the path the defects live on.  A
#   hand-built claim already carrying a wire date would hide the one assertion that matters.

#: Beacon's two downloadable templates, saved byte-for-byte on 2026-09-17.  These files are
#: the authority for the column names, and this test is a separate reader of them from
#: :mod:`recon.mocks.beacon_payloads` — which is the property ``MOCK_FIELDS.md``'s own
#: provenance test relies on.
_TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "docs" / "vendor_evidence" / "raw" / "templates"
_TEMPLATE_FILES = {
    beacon.TEMPLATE_PHARMACY: "beacon_pharmacy_template.csv",
    beacon.TEMPLATE_MEDICAL: "beacon_medical_template.csv",
}

#: The six keys on a submission that are **ours**.  Beacon names none of them, they appear
#: in neither template file, and they are written before the template block — so pinning
#: them here is what lets the rest of the body be compared to the file position by position.
_ENVELOPE_KEYS = (
    "payload_kind",
    "direction",
    "template",
    "manufacturer",
    "submission_date",
    "qualification_status",
)

#: Published columns this fabric structurally cannot populate, so they go out ``None`` on
#: every claim rather than being omitted.  Named per template because the two are different
#: decisions: the pharmacy four are a prescriber's writing date, a refill ordinal and
#: Beacon's two cash-payer sentinels; the medical eight are three surplus modifier slots,
#: two more sentinels, the second NPI we do not hold, and a quantity in a unit basis Beacon
#: never published.  A change that starts filling any of them is a change that started
#: asserting a fact we do not have, and it should have to edit this tuple to land.
_ALWAYS_NULL = {
    beacon.TEMPLATE_PHARMACY: ("date_prescribed", "fill_number", "rx_bin", "rx_pcn"),
    beacon.TEMPLATE_MEDICAL: (
        "hcpcs_code_modifier_2",
        "hcpcs_code_modifier_3",
        "hcpcs_code_modifier_4",
        "health_plan_name",
        "health_plan_id",
        "rendering_physician_id",
        "quantity",
        "unit_of_measure",
    ),
}

#: ``normalized_record.quantity_milli`` for the record below.  Stated rather than read: the
#: column exists on the canonical model and the 340B sidecar does not carry a dispensed
#: quantity at all (``beacon_payloads.submission`` emits ``quantity_dispensed`` as ``None``
#: and says why).  Thousandths of a unit — NCPDP's three implied decimals — so 30 units.
_QUANTITY_MILLI = 30_000


def _published_columns(template: str) -> tuple[str, ...]:
    """The header row of Beacon's own template file, parsed rather than transcribed.

    Reading the file here is the whole point of the pin: a test carrying its own copy of the
    column list is a third place the contract is written down, and three copies drift the
    same way two did.
    """
    path = _TEMPLATE_DIR / _TEMPLATE_FILES[template]
    with path.open(encoding="utf-8-sig", newline="") as handle:
        header = next(csv.reader(handle))
    return tuple(name.strip() for name in header)


def _canonical_record(dispense) -> dict[str, object]:
    """One dispense in ``normalized_record``'s spelling, which is what the connector maps.

    The column names and the formats are the canonical model's, not the feed's: ``ndc11``
    and ``date_of_service`` rather than ``ndc_11`` and ``fill_date``, and both dates in the
    ``YYYY-MM-DD`` the table stores rather than the ``CCYYMMDD`` the feed wrote.
    ``ingest/adapters.py`` converts ``submission_date`` through
    :func:`recon.crosswalk.keys.wire_date_to_iso` on the way in, so a record that reached
    this connector would already be ISO on both.

    Identifiers are copied untouched, drift included.  Defect D-6 drifts one identifier per
    episode in one feed, and a test that repaired it here would be asserting against a join
    the real feed does not have.
    """
    qualification = dispense.event("QUALIFICATION_DECISION")
    return {
        "covered_entity_id": dispense.covered_entity_id,
        "manufacturer": dispense.manufacturer,
        "ndc11": dispense.ndc_11,
        "date_of_service": keys.wire_date_to_iso(dispense.fill_date),
        "qualification_status": dispense.qualification_status,
        "submission_date": (
            None if dispense.submitted_at is None else keys.wire_date_to_iso(dispense.submitted_at)
        ),
        "rx_number": dispense.rx_number,
        "pharmacy_npi": dispense.pharmacy_npi,
        "prescriber_npi": None if qualification is None else qualification.get("prescriber_npi"),
        "quantity_milli": _QUANTITY_MILLI,
        "provider_npi": dispense.provider_npi,
    }


def _claim_for(dispense) -> beacon.TpaQualifiedClaim:
    """The dispense as the connector sees it, through the only entry point that exists.

    :meth:`TpaQualifiedClaim.from_canonical` had **no call site anywhere** in the repository
    before this test; it is the documented way in, so it is the way in used here rather than
    a keyword-by-keyword construction that would quietly skip whatever it does.
    """
    return beacon.TpaQualifiedClaim.from_canonical(_canonical_record(dispense))


def _dispense_on(vendor_source, template: str):
    """A dispense of one template, picked by the null pattern both sides classify on.

    Read the same way ``beacon_payloads._template`` and ``TpaQualifiedClaim.template`` read
    it — the pharmacy pair first — so this helper cannot disagree with the thing it selects
    for.
    """
    for dispense in vendor_source.dispenses:
        pharmacy = dispense.rx_number is not None or dispense.pharmacy_npi is not None
        if pharmacy == (template == beacon.TEMPLATE_PHARMACY):
            return dispense
    pytest.skip(f"no {template} dispense in the demo profile")


def _headers(server) -> dict[str, str]:
    """An authenticated Read/Write partner's two tokens.  ``BEACON-011``'s pair, exchanged."""
    creds = server.credentials()
    tokens = httpx.post(f"{server.base_url}{beacon_server.TOKEN_PATH}", json=creds).json()
    return {
        beacon_server.ACCESS_TOKEN_HEADER: tokens["access_token"],
        beacon_server.PRIVATE_TOKEN_HEADER: tokens["private_token"],
    }


_TEMPLATES = [beacon.TEMPLATE_PHARMACY, beacon.TEMPLATE_MEDICAL]


@pytest.mark.parametrize("template", _TEMPLATES)
def test_the_connector_and_the_mock_agree_on_the_submission_field_names(vendor_source, template):
    """Equality in both directions, per template — not containment.

    A subset check passes while one side quietly grows a field the other never sends, which
    is how a wire format ends up agreed in two places and matched in neither. The failure it
    has to catch is symmetric, so the assertion is.
    """
    dispense = _dispense_on(vendor_source, template)
    claim = _claim_for(dispense)
    assert claim.template == template, "the two template classifiers disagree about this claim"

    connector = beacon._template_body(claim, template)
    mock = beacon_payloads.submission(dispense)

    assert set(connector) == set(mock), (
        "the connector's typed-out copy of the Beacon template and the mock's have drifted; "
        f"connector only: {sorted(set(connector) - set(mock))}, "
        f"mock only: {sorted(set(mock) - set(connector))}"
    )


@pytest.mark.parametrize("template", _TEMPLATES)
def test_the_published_columns_are_pinned_against_beacons_own_file(vendor_source, template):
    """Both sides against the vendor's bytes, in published order.

    The column list is parsed out of the downloaded CSV rather than written here, so this
    test is a genuinely independent reader of the contract — the property ``MOCK_FIELDS.md``
    relies on when it claims a published spelling is checked rather than asserted.

    Order matters and is asserted: a header row is positional, and a template whose columns
    are right but shuffled is a file Beacon's parser reads as a different template.
    """
    published = _published_columns(template)
    dispense = _dispense_on(vendor_source, template)

    connector = tuple(beacon._template_body(_claim_for(dispense), template))
    mock = tuple(beacon_payloads.submission(dispense))

    for name, body in (("connector", connector), ("mock", mock)):
        assert body[: len(_ENVELOPE_KEYS)] == _ENVELOPE_KEYS, (
            f"the {name}'s six envelope keys are ours and come first, so the template block "
            "after them lines up with the file"
        )
        assert body[len(_ENVELOPE_KEYS) :] == published, (
            f"the {name}'s {template} block does not match the header row of "
            f"{_TEMPLATE_FILES[template]}"
        )


@pytest.mark.parametrize("template", _TEMPLATES)
def test_every_published_column_is_emitted_even_when_it_is_null(vendor_source, template):
    """Presence, asserted separately from value.

    Twelve published columns across the two templates are deliberately ``None`` — a gap this
    fabric cannot fill. The honesty of the exercise is that the gap travels on the wire where
    somebody can close it, so the interesting failure is not a wrong value but a **missing
    key**, and a test that only read values would never see it.

    The mirror is asserted too: a column that starts carrying a value has to edit
    :data:`_ALWAYS_NULL` to land, which is the review this fabric wants on the day somebody
    decides to write Beacon's ``999999`` cash-payer sentinel into ``rx_bin``.
    """
    published = _published_columns(template)
    body = beacon._template_body(_claim_for(_dispense_on(vendor_source, template)), template)

    missing = [column for column in published if column not in body]
    assert not missing, (
        f"{missing} are published {template} columns and were omitted rather than emitted "
        "null; an absent field is a gap nobody can see on the wire"
    )

    for column in _ALWAYS_NULL[template]:
        assert column in body, f"{column!r} is published and must be emitted"
        assert body[column] is None, (
            f"{column!r} is a published column this fabric holds no fact for, and it now "
            "carries a value -- a near-miss validates where a null is visibly missing"
        )


def test_the_date_of_service_goes_out_in_wire_form():
    """The single assertion that would have caught the original defect.

    ``normalized_record`` stores ``YYYY-MM-DD``; every feed, every other adapter and the
    Beacon mock's own claim index speak ``CCYYMMDD``. The connector sent the ISO string
    unconverted, so a connector-built submission matched nothing and came back
    ``404 unknown_claim`` — and nothing went red, because no test had ever put a
    connector-built body on the wire.

    Asserted three ways on purpose: the shape, the conversion function, and the literal. The
    shape alone would pass on any eight digits, and the function alone would pass if
    ``iso_date_to_wire`` itself were the thing that broke.
    """
    claim = beacon.TpaQualifiedClaim.from_canonical(
        {
            "covered_entity_id": "DSH123456",
            "ndc11": "00002143380",
            "date_of_service": "2025-12-16",
            "rx_number": "RX0000001",
            "pharmacy_npi": "1234567890",
        }
    )
    body = beacon._template_body(claim, beacon.TEMPLATE_PHARMACY)

    assert re.fullmatch(r"\d{8}", body["date_of_service"]), (
        f"date_of_service went out as {body['date_of_service']!r}; Beacon's wire is CCYYMMDD "
        "and an ISO date here joins to nothing, which is a claim silently lost"
    )
    assert body["date_of_service"] == keys.iso_date_to_wire("2025-12-16")
    assert body["date_of_service"] == "20251216"


@pytest.mark.parametrize("template", _TEMPLATES)
def test_a_connector_built_body_resolves_at_the_mock(server, vendor_source, template):
    """The two sides joined on, rather than compared.

    Equal key *sets* are not the claim the connector's docstring makes — it says a
    submission round-trips against the mock. So this builds the mock's own index key out of
    a connector-built body and looks it up in the index the running server built at
    construction. A field name, a template discriminator or a date format that disagrees
    produces a key that is absent, which is exactly the ``404 unknown_claim`` the live path
    would have returned.
    """
    dispense = _dispense_on(vendor_source, template)
    connector = beacon._template_body(_claim_for(dispense), template)

    assert beacon_server._submission_key(connector) == beacon_server._submission_key(
        beacon_payloads.submission(dispense)
    ), "the connector and the mock build different natural 340B keys from the same dispense"

    index = server._state.by_submission_key
    key = beacon_server._submission_key(connector)
    assert key in index, (
        f"the connector's {template} body keys to {key}, which names no claim on a server "
        "indexed from the same dispenses -- the two sides do not join"
    )
    assert [d.beacon_id for d in index[key]] == [dispense.beacon_id]


@pytest.mark.parametrize("template", _TEMPLATES)
def test_a_connector_built_submission_round_trips_to_an_acknowledgement(
    server, vendor_source, template
):
    """The whole outbound leg, end to end, with nothing borrowed from the mock.

    The request is built by :meth:`ShieldsSubmitsDirectly.submission` — so the path, the
    body and the natural key are all the connector's — POSTed to the real loopback server,
    and the response handed back to :func:`beacon.acknowledged` with the request beside it.

    That last part is the second defect's home. The acknowledgement echoes ``date_of_service``
    in wire form and every ``keys.natural_340b_*`` value is built from ISO, so comparing the
    two unconverted made the cross-check fire on every single submission — a receipt for the
    claim we just sent, reported as a receipt for a different claim.
    """
    dispense = _dispense_on(vendor_source, template)
    entity = dispense.covered_entity_id
    claim = _claim_for(dispense)

    outbound = registry.by_id(
        registry.beacon_sources({registry.BEACON_SUBMISSION_SOURCE_ID: server.base_url}),
        registry.BEACON_SUBMISSION_SOURCE_ID,
    )
    authority = beacon.SubmissionOwnershipPolicy(
        {entity: beacon.SubmissionOwnership.SHIELDS_DIRECT}
    ).route(entity, outbound)
    request = authority.submission(claim)

    assert (request.method, request.path) in beacon_server.ENDPOINTS, (
        f"{request.method} {request.path} is not on the mock's surface; the connector's "
        "declared endpoint used to 404 against our own router and no test could see it"
    )

    response = httpx.post(
        f"{server.base_url}{request.path}", headers=_headers(server), json=dict(request.body)
    )
    assert response.status_code == 200, (
        f"a connector-built {template} submission was refused: {response.text}"
    )

    receipt = beacon.acknowledged(response.json(), request=request)
    assert receipt.beacon_id == dispense.beacon_id
    assert receipt.publishes == beacon.beacon_id_key(dispense.beacon_id)
    assert receipt.natural_key == request.natural_key, (
        "the acknowledgement's echoed key and the submitted key are two spellings of one "
        "date rather than two claims"
    )
