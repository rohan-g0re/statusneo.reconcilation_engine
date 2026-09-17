"""A real HTTP server on 127.0.0.1 that serves the payloads wave 1 already wrote.

Requirement C1: *"A local HTTP service implementing the documented surface: token exchange,
claim submission, submission-status retrieval, validation outcomes, rebate status.
Deterministic and seeded."*  Its acceptance is that the mock *"can produce every
Beacon-driven outcome our engine already models."*  It can, and it produces them by
**replaying** :mod:`recon.mocks.beacon_payloads` — the outcome set on this wire is the set
on those files, which is the set in ``tpa_340b_events.jsonl``, because this module never
computes one.

**This is a server, not a stub.**  ``http.server`` speaks real HTTP over a real socket, so
the transport under test is exercised through its own connection handling, headers, status
codes and JSON parsing.  A fake returning canned objects would prove the code after the
response and nothing before it, and on an API integration everything interesting is before
the response.  ``http.server`` is stdlib, which keeps constraint C7 — a base install with
zero runtime dependencies — intact: no FastAPI, no new dependency, not even the ``httpx``
the *client* half uses.

═══ What is invented here, stated before anything is claimed ════════════════════════

Almost all of it.  ``docs/vendor_evidence/beacon.md`` lists what is genuinely **UNKNOWN**
about Beacon, and items 5 and 8 of that list are exactly this module's subject matter:

    5. **Endpoint paths, HTTP verbs, request and response envelopes.**  ``DOC2-008`` says
       these are not public.  Ours are invented and declared.
    8. **The exact token exchange.**  That two tokens exist is ``BEACON-011``; how they are
       presented on a request — header names, scheme, ordering — is unknown.

``BEACON-001`` and ``BEACON-002`` — the two data-template articles — were retrieved on
2026-09-17 through a text-extraction proxy, and they settle the *submission body's field
names*, which is why :func:`beacon_payloads.submission` now carries Beacon's own spellings
and :func:`_submission_key` reads them.  They settle nothing about the transport: neither
page mentions SFTP, API, portal upload, filename convention or cadence, and both say so by
omission.  ``BEACON-003`` through ``BEACON-007``, including ``BEACON-005`` (the submission
workflow), still return ``HTTP 403``.  They are cited here only as **context for what we
could not read**, never as specification.  So:

* every path in :data:`ENDPOINTS` is **INVENTED**;
* :data:`ACCESS_TOKEN_HEADER` and :data:`PRIVATE_TOKEN_HEADER` are **INVENTED** — that the
  two tokens exist is sourced, how they ride on a request is not;
* the error envelope and the ``/oauth/token`` request body are **INVENTED**;
* the Beacon ID format is **INVENTED** and is not invented *here* — it is minted by the
  orchestrator and only echoed (see below).

What is **not** invented is the shape of the exchange: two tokens (``BEACON-011``,
``DOC2-007``), Read versus Read/Write granted per 340B ID (``BEACON-012``), one outbound
direction and four inbound ones (``DOC2-007``).  Switching this connector to a live Beacon
endpoint is a change of paths, header names and envelope — not a change of design.  That is
the honest claim, and it is the one requirement F3's readiness report will make.

═══ Two tokens, and the rejection is specific ══════════════════════════════════════

``BEACON-011`` is **SECOND_HAND and SINGLE-SOURCED**: it rests on ``DOC2-006``/``DOC2-007``
page 3 — *"Partner onboarding yields an Access Token plus a separate Private Token used to
authenticate API requests"* — with no independent corroboration, because the pages that
would corroborate it are the 403s.  Said plainly so that a reader who later obtains real
Beacon documentation knows exactly which sentence to check.

Both tokens are required on every call but ``/oauth/token``, and the failure is named:
a missing access token, a missing private token, an unknown access token, a private token
that belongs to a different issuance and a spent token are five different ``error`` codes.
One generic ``401`` would let the client half pass its tests while sending only one of the
two tokens, which is the single most likely way a two-token integration is got wrong.

═══ Read versus Read/Write, per 340B ID ════════════════════════════════════════════

``BEACON-012``: a covered entity grants a partner Read or Read/Write, and *"permissions may
need to be repeated across applicable 340B IDs."*  So permission is not a property of the
partner — it is a property of the *pair* (partner, 340B ID), and this server models it that
way.  A Read partner gets ``403 read_only_permission`` on a submission; a partner with no
grant at all for that 340B ID gets ``403 no_permission_for_340b_id``, on reads as well as
writes.

That distinction is requirement E1's justification made executable.  ``_create_episode``
currently writes ``covered_entity_id=None``; against this server a connector that keeps
doing so cannot submit anything at all, because there is no 340B ID to check a grant
against.  The permission model is the thing that turns "populate a column" from tidiness
into a blocker.

═══ No wall clock, anywhere on the wire ════════════════════════════════════════════

§4.11 seeds every random stream from a hashed path string and pins ``ingest_batch.loaded_at``
to ``1970-01-01T00:00:00Z`` so no wall-clock leaks into the data.  An HTTP server wants a
clock in three places, and all three are refused:

1. **Token expiry** is counted in **requests, not seconds** (:data:`DEFAULT_TOKEN_BUDGET`).
   A token is issued with a budget, every authenticated request spends one, and the request
   after the last one fails with ``token_expired``.  A clock-based expiry cannot be tested:
   a fast suite never reaches it, so the refresh path is dead code that looks covered, and a
   slow machine reaches it in the middle of an unrelated assertion, so the suite is flaky for
   a reason nobody would look for in a mock.  A counter makes "the request after the budget
   fails" true on every machine at every speed, which is the property a test needs.
2. **``issued_at``** is the constant :data:`ISSUED_AT`, the same pinned epoch
   ``ingest/pipeline.py`` uses, for the same reason: a field nothing may depend on should
   look obviously fixed rather than plausibly live.
3. **The ``Date`` header** is not sent.  ``BaseHTTPRequestHandler.send_response`` adds
   ``Date`` and ``Server`` automatically, which would put a wall clock in the response bytes
   of a server whose whole claim is that the same request sequence produces the same bytes.
   :meth:`_BeaconHandler._respond` calls ``send_response_only`` and writes its own headers.

Determinism here means what it can mean for a server: **the same source data and the same
sequence of requests produce the same bytes.**  Tokens are ``blake2b`` over the partner id
and the ordinal of the issuance, so the first exchange in a run always yields the same pair
and a re-exchange after expiry always yields a different one.  Nothing is drawn at random
and nothing is read from a clock.

═══ It replays.  It does not adjudicate.  It does not mint. ════════════════════════

Requirement M3, and the rule this module is most able to break.  Every response body is a
dict returned by :mod:`recon.mocks.beacon_payloads`, unwrapped and unmodified — there is no
envelope around it, because an envelope would be a field the vendor never emitted.  One
endpoint returns exactly one payload kind:

    ``POST /claims``                    -> ``acknowledgement``
    ``GET  /claims/{id}``               -> ``acknowledgement``
    ``GET  /claims/{id}/validation``    -> ``validation_outcome``
    ``GET  /claims/{id}/rebate``        -> ``rebate_status``
    ``GET  /payments/{reference}``      -> ``payment_reference`` (a list; see below)

So the outcome vocabulary on this wire is :data:`beacon_payloads.VALIDATION_OUTCOMES` by
construction rather than by agreement, and ``tests/test_mocks.py`` already asserts that set
equals the set the feed contains.  Widening it would require editing wave 1, where the
existing tests would catch it.

**A Beacon ID is looked up, never issued.**  Requirement M1: the id in a mock response is
byte-identical to the one the orchestrator minted.  A submission is therefore a *lookup* —
the natural 340B key on the request body finds the dispense, and the dispense's
``beacon_id`` comes back on the acknowledgement.  A claim this server has never heard of
gets ``404 unknown_claim`` rather than a freshly minted id, which is the only honest answer:
a mock that could mint would hand the crosswalk an identifier no other component agreed to,
and requirement C5's acceptance — a rebate payment carrying only a Beacon ID resolves to the
correct episode — would be testing a coincidence.

**A key that names two claims gets ``409``, not a winner.**  The medical natural key is only
``{provider_npi, ndc_11, fill_date}`` — no prescription number exists to key on — and on the
``full`` profile 19 pairs of dispenses share one.  This was found by building the index and
watching it collide, not predicted; ``tpa.py`` had already written down what to do about it:
*"two administrations of the same drug at the same site on the same day are genuinely
indistinguishable on this feed alone.  A connector has to park that as ambiguous rather than
guess through it — guessing is how a reconciliation tool quietly starts lying."*  So
``ambiguous_claim_key`` is a first-class answer and :meth:`LoopbackBeaconServer.ambiguous_submission_keys`
counts the population up front.  It is a transport refusal, not a Beacon verdict, so the
validation-outcome vocabulary M3 pins is untouched.

**Submission is idempotent**, because a lookup is.  Submitting the same claim twice returns
the same acknowledgement bytes; the repeat is reported in the
:data:`SUBMISSION_REPEAT_HEADER` response header rather than in the body, so requirement
A5's idempotency work has something to assert against without a field appearing on a payload
Beacon never defined.  The status code is ``200`` both times, deliberately: a server that
answered ``201`` then ``200`` would make every client branch on a distinction the body does
not make.

**``GET`` never requires a prior ``POST``.**  Every dispense in the source is a claim Beacon
already knows about, so a read resolves whether or not this partner submitted it.  That is
not laxity — it is requirement C4's mode B, where the *TPA* submits and Shields only consumes
outcomes.  A server that refused to answer for a claim the caller had not submitted would
make mode B untestable against the only Beacon we have.

═══ Loopback, port 0, and a shutdown that leaves nothing behind ════════════════════

``filterwarnings = ["error"]`` is set repository-wide, so a socket left to the garbage
collector surfaces as a ``ResourceWarning`` raised inside whichever unrelated test happens
to trigger the collection.  The same discipline ``sftp_server.py`` follows applies here:
bind ``127.0.0.1`` on port 0 so two suites cannot collide on a number, serve in one thread,
and on :meth:`LoopbackBeaconServer.stop` end the accept loop, join that thread, then
``server_close()`` — which joins every handler thread because ``block_on_close`` is left on
and ``daemon_threads`` is turned off.  ``ThreadingHTTPServer`` sets ``daemon_threads = True``,
which would let a handler outlive the test that made it.

``protocol_version`` stays at HTTP/1.0 so every connection closes after one response.  With
keep-alive a handler thread sits in ``readline`` waiting for a request that will never come,
and ``server_close()`` would then block joining it.  A five second socket timeout bounds the
remaining case — a client that connects and says nothing — so a failure is a failure rather
than a suite that never returns.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import threading
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import TracebackType
from typing import Any, Callable, Mapping
from urllib.parse import unquote, urlsplit

from recon.mocks import beacon_payloads
from recon.mocks.source import Dispense, VendorSource

__all__ = [
    "ACCESS_TOKEN_HEADER",
    "CLAIMS_PATH",
    "DEFAULT_TOKEN_BUDGET",
    "ENDPOINTS",
    "ERROR_CODES",
    "ISSUED_AT",
    "LOG_CHANNEL",
    "PAYMENTS_PATH",
    "PRIVATE_TOKEN_HEADER",
    "READ",
    "READ_WRITE",
    "READ_ONLY_PARTNER_ID",
    "READ_WRITE_PARTNER_ID",
    "SINGLE_ENTITY_PARTNER_ID",
    "SUBMISSION_REPEAT_HEADER",
    "TOKEN_PATH",
    "LoopbackBeaconServer",
    "Partner",
    "client_secret_for",
    "default_partners",
]

#: This server's log channel, with a :class:`logging.NullHandler` on it.
#:
#: The same treatment ``sftp_server.py`` gives paramiko, for the same reason.
#: ``BaseHTTPRequestHandler.log_message`` writes every request line straight to ``stderr``,
#: so a passing test would print a wall of access-log noise that reads like output.  A null
#: handler silences nothing — the records still propagate, so a suite that configures logging
#: in order to debug this server sees every line of it.
LOG_CHANNEL = "recon.mocks.beacon_server"

logging.getLogger(LOG_CHANNEL).addHandler(logging.NullHandler())

# ═══ the invented wire vocabulary ═══════════════════════════════════════════════════

#: Where the Access Token rides.  **INVENTED.**  ``BEACON-011`` establishes that the token
#: exists; ``beacon.md`` item 8 says plainly that how it is presented — header name, scheme,
#: ordering — is unknown.  A bare custom header rather than ``Authorization: Bearer`` because
#: there are two credentials and no evidence about which one, if either, Beacon puts there;
#: guessing ``Bearer`` would dress an invention as a standard.
ACCESS_TOKEN_HEADER = "X-Beacon-Access-Token"

#: Where the Private Token rides.  **INVENTED**, for the same reason and with the same
#: source.  ``DOC2-007``: *"Beacon partner Access Token + Private Token"*.
PRIVATE_TOKEN_HEADER = "X-Beacon-Private-Token"

#: Response header on a submission: ``true`` when this claim was already submitted on this
#: server instance, ``false`` on the first.  **INVENTED**, and deliberately a header rather
#: than a body field — the body is ``beacon_payloads.acknowledgement`` verbatim, and adding
#: a key to it would put a field on a Beacon payload that Beacon never defined.
SUBMISSION_REPEAT_HEADER = "X-Beacon-Submission-Repeat"

#: **INVENTED** paths.  ``DOC2-008`` states the API reference is not public and
#: ``BEACON-005`` — the article describing the submission workflow — is one of the 403s.
TOKEN_PATH = "/oauth/token"
CLAIMS_PATH = "/claims"
PAYMENTS_PATH = "/payments"

#: The whole surface, as ``(method, path template)``.  Exported so the client half can be
#: written against a named constant rather than a string literal it has to keep in step by
#: hand, and so a reader can see the entire API without reading the router.
ENDPOINTS: tuple[tuple[str, str], ...] = (
    ("POST", TOKEN_PATH),
    ("POST", CLAIMS_PATH),
    ("GET", f"{CLAIMS_PATH}/{{beacon_id}}"),
    ("GET", f"{CLAIMS_PATH}/{{beacon_id}}/validation"),
    ("GET", f"{CLAIMS_PATH}/{{beacon_id}}/rebate"),
    ("GET", f"{PAYMENTS_PATH}/{{payment_reference}}"),
)

#: Every ``error`` value this server can put in an error body.  A closed set, for the same
#: reason ``VALIDATION_OUTCOMES`` is one: a client that branches on these needs to know the
#: list is exhaustive, and a reviewer needs to see that "wrong credential" is five distinct
#: answers rather than one shrug.
ERROR_CODES: tuple[str, ...] = (
    "malformed_body",
    "missing_credentials",
    "invalid_credentials",
    "missing_access_token",
    "missing_private_token",
    "unknown_access_token",
    "invalid_private_token",
    "token_expired",
    "no_permission_for_340b_id",
    "read_only_permission",
    "missing_covered_entity_id",
    "unknown_claim",
    "ambiguous_claim_key",
    "unknown_payment_reference",
    "unknown_endpoint",
    "method_not_allowed",
    "internal_error",
)

#: The two permission levels ``BEACON-012`` names, granted per 340B ID.
READ = "READ"
READ_WRITE = "READ_WRITE"

#: What a token's ``issued_at`` says.  The pinned epoch ``ingest/pipeline.py`` uses for
#: ``ingest_batch.loaded_at``, and pinned for the same reason — see the module docstring.
ISSUED_AT = "1970-01-01T00:00:00Z"

#: Authenticated requests one issued token pair is good for.  Large enough that a connector
#: exercising the whole surface does not have to think about it, small enough that a test
#: can spend it deliberately.  Override per server with ``token_budget=1`` to test the
#: refresh path in one call.
DEFAULT_TOKEN_BUDGET = 64

#: Personalisation for the token-derivation hash, so a label used here and a label used by
#: some other seeded stream in this repository cannot derive the same bytes.  Same
#: construction ``sftp_server.deterministic_key`` uses.
_TOKEN_PERSON = b"recon-beacn"

#: Seconds a stopping server waits for its own serving thread, and seconds a handler waits
#: on a silent client.  Bounds on a hang, so a failure reports rather than blocks forever.
_JOIN_TIMEOUT = 5.0
_SOCKET_TIMEOUT = 5.0

#: Default partner ids.  Three, because two would only demonstrate Read versus Read/Write
#: and ``BEACON-012``'s actual claim is that permission is scoped **per 340B ID** — which
#: takes a partner holding Read/Write on one covered entity and nothing on the others.
READ_WRITE_PARTNER_ID = "shields-readwrite"
READ_ONLY_PARTNER_ID = "shields-readonly"
SINGLE_ENTITY_PARTNER_ID = "shields-one-340b-id"


# ═══ partners and their grants ══════════════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class Partner:
    """One onboarded partner and what each covered entity has granted it.

    ``grants`` is keyed by 340B ID rather than being a single level, because ``BEACON-012``
    says permission is granted by the covered entity and *"may need to be repeated across
    applicable 340B IDs."*  A partner is therefore not "a Read partner" — it is a partner
    that holds Read on these 340B IDs, Read/Write on those, and nothing on the rest.
    """

    partner_id: str
    client_secret: str
    #: covered_entity_id -> :data:`READ` or :data:`READ_WRITE`.  Absent means no grant.
    grants: Mapping[str, str]

    def may_read(self, covered_entity_id: str) -> bool:
        return self.grants.get(covered_entity_id) in (READ, READ_WRITE)

    def may_write(self, covered_entity_id: str) -> bool:
        return self.grants.get(covered_entity_id) == READ_WRITE


def client_secret_for(partner_id: str) -> str:
    """The onboarding secret for a default partner — a pure function of its id.

    Not a credential.  It authenticates nothing outside a loopback socket, and it is derived
    rather than stored so no literal in this repository ever looks like a real one.
    """
    return _derive("client-secret", partner_id)


def default_partners(source: VendorSource) -> dict[str, Partner]:
    """Three partners spanning the permission cases, built from the source's own 340B IDs.

    Built from the data rather than hardcoded, because a grant has to name a covered entity
    that actually appears in the feed or every permission check passes vacuously.

    * :data:`READ_WRITE_PARTNER_ID` — Read/Write everywhere.  The ordinary connector.
    * :data:`READ_ONLY_PARTNER_ID` — Read everywhere.  Can pull outcomes, cannot submit.
      This is requirement C4's mode B as a *credential* rather than as a configuration flag:
      even a client with a bug that tries to submit is refused by the other end.
    * :data:`SINGLE_ENTITY_PARTNER_ID` — Read/Write on the first 340B ID and nothing on any
      other, which is the shape ``BEACON-012``'s "repeated across applicable 340B IDs"
      actually produces when somebody forgets to repeat it.
    """
    entities = sorted({d.covered_entity_id for d in source.dispenses if d.covered_entity_id})
    first = entities[:1]
    return {
        READ_WRITE_PARTNER_ID: Partner(
            partner_id=READ_WRITE_PARTNER_ID,
            client_secret=client_secret_for(READ_WRITE_PARTNER_ID),
            grants={entity: READ_WRITE for entity in entities},
        ),
        READ_ONLY_PARTNER_ID: Partner(
            partner_id=READ_ONLY_PARTNER_ID,
            client_secret=client_secret_for(READ_ONLY_PARTNER_ID),
            grants={entity: READ for entity in entities},
        ),
        SINGLE_ENTITY_PARTNER_ID: Partner(
            partner_id=SINGLE_ENTITY_PARTNER_ID,
            client_secret=client_secret_for(SINGLE_ENTITY_PARTNER_ID),
            grants={entity: READ_WRITE for entity in first},
        ),
    }


def _derive(*parts: str) -> str:
    """A deterministic opaque string from a label, via ``blake2b``.

    The same canonical-string-to-hash construction the generators use for every seeded
    stream, so there is one determinism idiom in this repository rather than two.
    """
    material = "|".join(parts).encode("utf-8")
    return hashlib.blake2b(material, digest_size=16, person=_TOKEN_PERSON).hexdigest()


@dataclass(slots=True)
class _IssuedTokens:
    """One token exchange's output, and what is left of its budget."""

    partner_id: str
    access_token: str
    private_token: str
    requests_remaining: int


# ═══ reading a submission back to the dispense it describes ═════════════════════════


def _submission_key(body: Mapping[str, Any]) -> tuple[Any, ...]:
    """The natural 340B key carried by a submission body, under Beacon's published names.

    The exact inverse of :func:`beacon_payloads.submission`, which is why the client can POST
    that function's output unmodified and be understood.  Every name is read from a constant
    in that module rather than re-typed here: a copied string is how a rename on the write
    side turns every ``POST /claims`` into ``404 unknown_claim`` with no test going red.

    **``Service Provider ID`` means two different things and the template says which.**
    ``BEACON-001`` defines it as *"NPI of the pharmacy that filled the prescription"*;
    ``BEACON-002`` defines it as *"the NPI of the healthcare entity where the patient
    received the medication administration"*.  One published name, a pharmacy on one
    template and a site on the other — so it lands in the ``pharmacy_npi`` slot of this key
    on a ``PHARMACY`` body and the ``provider_npi`` slot on a ``MEDICAL`` one.  The
    discriminator is the ``template`` envelope field, which both producers write.  A body
    with a template this does not recognise files its NPI in the medical slot, finds
    nothing, and gets ``404`` — a refusal rather than a wrong match, which is the correct
    way for this to fail.

    The slot the template does not use comes back ``None``, which is what the dispense holds
    for it — ``tpa.py`` writes ``provider_npi`` as null on a pharmacy dispense and the
    pharmacy pair as null on a medical one, so the two never both carry a value and the key
    round trips.

    **The key is not unique, and that is the data telling the truth.**  On the ``full``
    profile 1395 dispenses carry 1375 distinct keys: 19 pairs collide, every one of them
    ``MEDICAL``.  ``tpa.py``'s own docstring says why — *"The resulting join key,
    ``{provider_npi, ndc_11, fill_date}``, is strictly weaker than the pharmacy key: two
    administrations of the same drug at the same site on the same day are genuinely
    indistinguishable on this feed alone.  A connector has to park that as ambiguous rather
    than guess through it."*  So :class:`_BeaconState` indexes a **list** per key and
    ``POST /claims`` answers ``409 ambiguous_claim_key`` rather than picking one.  Beacon's
    real medical template carries a ``Claim Number`` that would have resolved it; ours is
    null because the 837 feed never reaches the 340B sidecar, which is what that 409 costs.
    """
    service_provider_id = body.get(beacon_payloads.FIELD_SERVICE_PROVIDER_ID)
    pharmacy = body.get("template") == beacon_payloads.TEMPLATE_PHARMACY
    return (
        body.get(beacon_payloads.FIELD_RX_NUMBER),
        service_provider_id if pharmacy else None,
        None if pharmacy else service_provider_id,
        body.get(beacon_payloads.FIELD_NDC_11),
        body.get(beacon_payloads.FIELD_DATE_OF_SERVICE),
    )


class _BeaconState:
    """Everything the handlers read, indexed once when the server is constructed.

    Building the claim index by running each dispense through
    :func:`beacon_payloads.submission` and back through :func:`_submission_key` is
    deliberate: it makes "a submission finds its own dispense" true by construction instead
    of by a matching pair of field lists that could drift apart in one edit.

    The claim index maps a key to a **list**, never to one dispense, because the medical
    key genuinely is not unique — see :func:`_submission_key`.  A dict keyed one-to-one
    would have to pick a winner at construction, and picking would make ``POST /claims``
    return somebody else's Beacon ID for the rest of the run.
    """

    def __init__(
        self,
        source: VendorSource,
        partners: Mapping[str, Partner],
        token_budget: int,
    ) -> None:
        self.source = source
        self.partners = dict(partners)
        self.token_budget = token_budget

        self.by_beacon_id: dict[str, Dispense] = {}
        self.by_submission_key: dict[tuple[Any, ...], list[Dispense]] = {}
        self.by_payment_reference: dict[str, list[Dispense]] = {}

        for dispense in source.dispenses:
            self.by_beacon_id[dispense.beacon_id] = dispense
            key = _submission_key(beacon_payloads.submission(dispense))
            self.by_submission_key.setdefault(key, []).append(dispense)
            reference = beacon_payloads.payment_reference(dispense)
            if reference is not None:
                self.by_payment_reference.setdefault(
                    str(reference["payment_reference"]), []
                ).append(dispense)

        self._lock = threading.Lock()
        self._issued: dict[str, _IssuedTokens] = {}
        self._issue_ordinal = 0
        self._submitted: set[str] = set()

    # --- token exchange ---------------------------------------------------

    def issue(self, partner: Partner) -> _IssuedTokens:
        """Mint a token pair for a partner.  Seeded by the issuance ordinal, not a clock.

        The ordinal is what lets a re-exchange after expiry produce a *different* pair while
        the whole run stays reproducible: the nth exchange in a run always yields the nth
        pair of strings, on every machine, at any speed.
        """
        with self._lock:
            self._issue_ordinal += 1
            ordinal = str(self._issue_ordinal)
            tokens = _IssuedTokens(
                partner_id=partner.partner_id,
                access_token=_derive("access-token", partner.partner_id, ordinal),
                private_token=_derive("private-token", partner.partner_id, ordinal),
                requests_remaining=self.token_budget,
            )
            self._issued[tokens.access_token] = tokens
            return tokens

    def spend(self, access_token: str) -> _IssuedTokens | None:
        """The issuance for a token, one request poorer.  ``None`` if it is not ours."""
        with self._lock:
            return self._issued.get(access_token)

    def consume_request(self, tokens: _IssuedTokens) -> bool:
        """Charge one request against a budget.  ``False`` once it is spent."""
        with self._lock:
            if tokens.requests_remaining <= 0:
                return False
            tokens.requests_remaining -= 1
            return True

    # --- submission history ----------------------------------------------

    def record_submission(self, beacon_id: str) -> bool:
        """Remember a submission; report whether it had already been seen."""
        with self._lock:
            repeat = beacon_id in self._submitted
            self._submitted.add(beacon_id)
            return repeat

    @property
    def ambiguous_submission_keys(self) -> tuple[tuple[Any, ...], ...]:
        """Keys that name more than one claim, so a submission on them cannot be resolved.

        Empty on ``demo``; 19 keys on ``full``, all medical.  Exposed rather than merely
        refused at the endpoint, so a readiness report can count the population this mock
        can never acknowledge instead of discovering it one 409 at a time.
        """
        return tuple(
            key for key, claims in self.by_submission_key.items() if len(claims) > 1
        )


# ═══ the HTTP surface ═══════════════════════════════════════════════════════════════


class _BeaconHttpServer(ThreadingHTTPServer):
    """``ThreadingHTTPServer`` with its teardown made complete.

    ``daemon_threads`` is turned back off — ``http.server`` sets it to ``True``, which lets a
    handler outlive the test that made it and take an unclosed socket with it.  With it off
    and ``block_on_close`` left on, ``server_close()`` joins every handler before returning,
    which is what makes "stopped" true rather than likely.

    No ``SO_REUSEADDR``: it exists to reclaim a port stuck in ``TIME_WAIT``, and the whole
    point of binding to port 0 is that this server never asks for a particular port.
    """

    daemon_threads = False
    block_on_close = True
    allow_reuse_address = False

    def __init__(self, address: tuple[str, int], state: _BeaconState) -> None:
        self.state = state
        super().__init__(address, _BeaconHandler)


class _BeaconHandler(BaseHTTPRequestHandler):
    """One request.  Parse, authenticate, check the grant, look up, replay."""

    #: HTTP/1.0, so every connection closes after one response and no handler thread is left
    #: blocked in ``readline`` when ``stop()`` comes to join it.  See the module docstring.
    protocol_version = "HTTP/1.0"

    #: Bounds the remaining hang: a client that connects and then says nothing.
    timeout = _SOCKET_TIMEOUT

    @property
    def state(self) -> _BeaconState:
        return self.server.state  # type: ignore[attr-defined]

    # --- logging ---------------------------------------------------------

    def log_message(self, format: str, *args: Any) -> None:
        """Into this module's channel, never onto ``stderr``, and with no timestamp.

        The base implementation prefixes ``log_date_time_string()`` — a wall clock — and
        writes to ``stderr``, so an ordinary passing test would print an access log that
        reads like a failure.  §4.11 would permit a clock in a log, but there is no reason
        to spend one here.
        """
        logging.getLogger(LOG_CHANNEL).debug("%s %s", self.address_string(), format % args)

    # --- responding ------------------------------------------------------

    def _respond(
        self,
        status: HTTPStatus,
        body: Any,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        """Write one JSON response, with no ``Date`` and no ``Server`` header.

        ``send_response`` would add both, and ``Date`` is a wall clock in the response bytes
        of a server whose claim is that the same request sequence produces the same bytes.
        ``send_response_only`` writes the status line and nothing else, so every header on
        the wire is one this method chose.
        """
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.log_request(int(status), len(payload))
        self.send_response_only(int(status))
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(payload)

    def _fail(self, status: HTTPStatus, error: str, message: str) -> None:
        """The error envelope.  **INVENTED** — Beacon publishes no error shape.

        Two fields and no more: ``error`` is the machine-readable code from
        :data:`ERROR_CODES`, ``message`` is for the human reading a test failure.  Nothing
        from the request is echoed back, so a token can never reach a log or an assertion
        message by way of an error body.
        """
        self._respond(status, {"error": error, "message": message})

    # --- request plumbing -------------------------------------------------

    def _read_body(self) -> tuple[dict[str, Any] | None, bool]:
        """``(body, ok)``.  Always drains the request, even on the failure path.

        A response written while unread bytes are still in flight gets the client a
        connection reset on Windows instead of the 400 it was supposed to see, so the body
        is read before anything can fail.
        """
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return None, False
        if length <= 0:
            return {}, True
        raw = self.rfile.read(length)
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None, False
        if not isinstance(parsed, dict):
            return None, False
        return parsed, True

    def _path_parts(self) -> list[str]:
        path = urlsplit(self.path).path
        return [unquote(part) for part in path.split("/") if part]

    # --- authentication and authorisation ---------------------------------

    def _authenticate(self) -> Partner | None:
        """Both tokens, checked as a pair.  Writes the failure itself and returns ``None``.

        Five distinct refusals rather than one blanket ``401``, because "the client sent only
        the access token" and "the client sent a stale pair" are different bugs and a single
        code would let the first one pass a test suite.  The comparison is
        ``hmac.compare_digest`` for the reason ``sftp_server`` gives: a timing side channel on
        a loopback mock is not a real threat, and comparing the same way everywhere is how the
        habit survives into somewhere it is.
        """
        access = self.headers.get(ACCESS_TOKEN_HEADER)
        private = self.headers.get(PRIVATE_TOKEN_HEADER)
        if not access:
            self._fail(
                HTTPStatus.UNAUTHORIZED,
                "missing_access_token",
                f"{ACCESS_TOKEN_HEADER} is required on every request but {TOKEN_PATH}",
            )
            return None
        if not private:
            self._fail(
                HTTPStatus.UNAUTHORIZED,
                "missing_private_token",
                f"{PRIVATE_TOKEN_HEADER} is required as well; BEACON-011 describes two "
                "tokens and both authenticate the request",
            )
            return None

        tokens = self.state.spend(access)
        if tokens is None:
            self._fail(
                HTTPStatus.UNAUTHORIZED,
                "unknown_access_token",
                "no token exchange on this server issued that access token",
            )
            return None
        if not hmac.compare_digest(private, tokens.private_token):
            self._fail(
                HTTPStatus.UNAUTHORIZED,
                "invalid_private_token",
                "the private token does not belong to that access token's issuance",
            )
            return None
        if not self.state.consume_request(tokens):
            self._fail(
                HTTPStatus.UNAUTHORIZED,
                "token_expired",
                "this token pair's request budget is spent; exchange credentials again. "
                "Expiry is counted in requests, not seconds -- see the module docstring",
            )
            return None
        return self.state.partners.get(tokens.partner_id)

    def _authorise(self, partner: Partner, covered_entity_id: str, *, write: bool) -> bool:
        """The grant check, per 340B ID.  Writes the refusal itself and returns ``False``."""
        if not covered_entity_id:
            self._fail(
                HTTPStatus.BAD_REQUEST,
                "missing_covered_entity_id",
                "BEACON-012 grants permission per 340B ID, so a request with no covered "
                "entity cannot be authorised against anything",
            )
            return False
        if not partner.may_read(covered_entity_id):
            self._fail(
                HTTPStatus.FORBIDDEN,
                "no_permission_for_340b_id",
                f"{partner.partner_id} holds no grant on {covered_entity_id}; BEACON-012 "
                "says permissions may need to be repeated across applicable 340B IDs",
            )
            return False
        if write and not partner.may_write(covered_entity_id):
            self._fail(
                HTTPStatus.FORBIDDEN,
                "read_only_permission",
                f"{partner.partner_id} holds {READ} on {covered_entity_id}; submitting "
                f"requires {READ_WRITE}",
            )
            return False
        return True

    # --- routing ----------------------------------------------------------

    def do_POST(self) -> None:  # noqa: N802 - the name http.server dispatches on
        body, ok = self._read_body()
        parts = self._path_parts()
        try:
            if not ok or body is None:
                self._fail(
                    HTTPStatus.BAD_REQUEST,
                    "malformed_body",
                    "the request body must be a JSON object",
                )
                return
            if parts == ["oauth", "token"]:
                self._exchange_tokens(body)
                return
            if parts == ["claims"]:
                self._submit_claim(body)
                return
            if len(parts) >= 2 and parts[0] == "claims":
                self._fail(
                    HTTPStatus.METHOD_NOT_ALLOWED,
                    "method_not_allowed",
                    f"POST {self.path}; retrieval is GET",
                )
                return
            self._unknown_endpoint()
        except Exception:  # noqa: BLE001 - a mock must answer, not print a traceback
            self._internal_error()

    def do_GET(self) -> None:  # noqa: N802 - the name http.server dispatches on
        parts = self._path_parts()
        try:
            if parts == ["oauth", "token"]:
                self._fail(
                    HTTPStatus.METHOD_NOT_ALLOWED,
                    "method_not_allowed",
                    f"GET {TOKEN_PATH}; the token exchange is POST",
                )
                return
            if len(parts) == 2 and parts[0] == "claims":
                self._claim(parts[1], beacon_payloads.acknowledgement)
                return
            if len(parts) == 3 and parts[0] == "claims" and parts[2] == "validation":
                self._claim(parts[1], beacon_payloads.validation_outcome)
                return
            if len(parts) == 3 and parts[0] == "claims" and parts[2] == "rebate":
                self._claim(parts[1], beacon_payloads.rebate_status)
                return
            if len(parts) == 2 and parts[0] == "payments":
                self._payment(parts[1])
                return
            self._unknown_endpoint()
        except Exception:  # noqa: BLE001 - a mock must answer, not print a traceback
            self._internal_error()

    def _unknown_endpoint(self) -> None:
        self._fail(
            HTTPStatus.NOT_FOUND,
            "unknown_endpoint",
            f"{self.command} {self.path} is not part of this surface; see ENDPOINTS",
        )

    def _internal_error(self) -> None:
        logging.getLogger(LOG_CHANNEL).exception("beacon mock failed handling %s", self.path)
        self._fail(
            HTTPStatus.INTERNAL_SERVER_ERROR,
            "internal_error",
            "the mock failed while handling the request; see the log channel",
        )

    # --- the five operations ----------------------------------------------

    def _exchange_tokens(self, body: Mapping[str, Any]) -> None:
        """``POST /oauth/token`` — partner credentials for an Access and a Private Token.

        ``DOC2-006``: *"Partner onboarding yields an Access Token plus a separate Private
        Token used to authenticate API requests."*  Onboarding is the part that happens out
        of band, so what stands in for it here is a partner id and a derived secret.

        The response carries ``grants`` — this partner's permission per 340B ID.  Invented,
        and included because ``BEACON-012``'s scoping is otherwise invisible until something
        is refused: a connector can read its own grants and decline to attempt a submission
        it is not entitled to make, which is requirement C4's mode B decided from the
        credential rather than from a config file somebody has to keep in step.
        """
        partner_id = body.get("partner_id")
        secret = body.get("client_secret")
        if not isinstance(partner_id, str) or not isinstance(secret, str):
            self._fail(
                HTTPStatus.BAD_REQUEST,
                "missing_credentials",
                "partner_id and client_secret are both required",
            )
            return
        partner = self.state.partners.get(partner_id)
        if partner is None or not hmac.compare_digest(secret, partner.client_secret):
            # One answer for both, so the response cannot be used to enumerate partner ids.
            self._fail(
                HTTPStatus.UNAUTHORIZED,
                "invalid_credentials",
                "unknown partner id or wrong client secret",
            )
            return

        tokens = self.state.issue(partner)
        self._respond(
            HTTPStatus.OK,
            {
                "access_token": tokens.access_token,
                "private_token": tokens.private_token,
                "token_type": "BeaconPartner",
                # Requests, not seconds. There is no clock here -- see the module docstring.
                "expires_in_requests": tokens.requests_remaining,
                "issued_at": ISSUED_AT,
                "partner_id": partner.partner_id,
                "grants": dict(sorted(partner.grants.items())),
            },
        )

    def _submit_claim(self, body: Mapping[str, Any]) -> None:
        """``POST /claims`` — one claim in, its acknowledgement out.

        The body is a :func:`beacon_payloads.submission` payload.  The natural 340B key on it
        finds the dispense; the dispense's ``beacon_id`` comes back.  Nothing is minted and
        nothing is decided — requirement M1, and the reason ``404 unknown_claim`` is the
        right answer for a claim this server has never heard of.

        The permission check runs against the body's own ``340B ID`` — Beacon's published
        name for it, field 1 of both templates — before the lookup, so a partner cannot
        discover whether a claim exists under a 340B ID it has no grant on.  ``BEACON-012``
        scopes permission by exactly this value, which is a pleasing coincidence rather than
        a designed one: the field is required on every submission whether or not anyone
        checks a grant against it.

        **Two claims can share one key, and then this refuses.**  The medical key is
        ``{provider_npi, ndc_11, fill_date}`` and two administrations of the same drug at
        the same site on the same day are indistinguishable on this feed — 19 such pairs
        exist on the ``full`` profile.  ``409 ambiguous_claim_key`` is the only answer that
        is not a guess, and ``tpa.py`` says so first: *"A connector has to park that as
        ambiguous rather than guess through it — guessing is how a reconciliation tool
        quietly starts lying."*

        It is also the clearest statement of why ``beacon_payloads.submission`` emits
        ``Claim Number`` and ``Claim Line Number`` as nulls on the medical template.
        ``BEACON-002`` requires both and describes the claim number as unique; our 340B
        sidecar carries neither, because they live on the 837 feed.  This 409 is what that
        gap costs, on the wire, where requirement E2 can be pointed at it.
        """
        partner = self._authenticate()
        if partner is None:
            return
        covered_entity_id = body.get(beacon_payloads.FIELD_340B_ID) or ""
        if not self._authorise(partner, str(covered_entity_id), write=True):
            return

        matches = self.state.by_submission_key.get(_submission_key(body), [])
        if not matches:
            self._fail(
                HTTPStatus.NOT_FOUND,
                "unknown_claim",
                "no claim matches that natural 340B key. This mock replays claims the "
                "generator already produced; it does not mint a Beacon ID for a claim it "
                "has never seen",
            )
            return
        if len(matches) > 1:
            self._fail(
                HTTPStatus.CONFLICT,
                "ambiguous_claim_key",
                f"{len(matches)} claims share that natural 340B key, so no single Beacon ID "
                "can be acknowledged. The medical key carries no claim number or claim line "
                "number, so two administrations of one drug at one site on one day are "
                "indistinguishable -- park it, do not guess through it",
            )
            return
        dispense = matches[0]
        if dispense.covered_entity_id != covered_entity_id:
            # The body named one 340B ID and the claim belongs to another. Refused as a
            # permission failure rather than answered, because answering would leak a claim
            # across covered entities -- exactly what per-340B-ID scoping exists to stop.
            self._fail(
                HTTPStatus.FORBIDDEN,
                "no_permission_for_340b_id",
                "the submitted '340B ID' does not own that claim",
            )
            return

        repeat = self.state.record_submission(dispense.beacon_id)
        self._respond(
            HTTPStatus.OK,
            beacon_payloads.acknowledgement(dispense),
            headers={SUBMISSION_REPEAT_HEADER: "true" if repeat else "false"},
        )

    def _claim(
        self, beacon_id: str, payload: Callable[[Dispense], dict[str, Any]]
    ) -> None:
        """The three ``GET /claims/...`` reads, which differ only in which payload they call.

        ``acknowledgement`` is submission-status retrieval, ``validation_outcome`` is the
        verdict, ``rebate_status`` is the manufacturer's decision and whether money moved.
        One function per kind, so no endpoint can return a blend of two and no envelope has
        to exist to hold one.
        """
        partner = self._authenticate()
        if partner is None:
            return
        dispense = self.state.by_beacon_id.get(beacon_id)
        if dispense is None:
            self._fail(
                HTTPStatus.NOT_FOUND, "unknown_claim", f"no claim carries Beacon ID {beacon_id}"
            )
            return
        if not self._authorise(partner, dispense.covered_entity_id, write=False):
            return
        self._respond(HTTPStatus.OK, payload(dispense))

    def _payment(self, reference: str) -> None:
        """``GET /payments/{reference}`` — every claim one rebate payment settled.

        A **list**, because one manufacturer ACH credit settles a whole batch of dispenses
        and returning only the first would hide the many-to-one that requirement E3 exists
        to reconcile.  A list rather than an object with a ``claims`` key for the same reason
        every other response here is unwrapped: an envelope would be a field Beacon never
        defined.  Order is the source's own dispense order, so the bytes are stable.

        Filtered by grant, per 340B ID: a batch can span covered entities, and a partner sees
        only the lines it is entitled to.  A reference that exists but yields nothing this
        partner may read is a ``403``, not an empty list — an empty list would read as
        "settled nothing" to anything counting rows.
        """
        partner = self._authenticate()
        if partner is None:
            return
        dispenses = self.state.by_payment_reference.get(reference)
        if not dispenses:
            self._fail(
                HTTPStatus.NOT_FOUND,
                "unknown_payment_reference",
                f"no rebate payment carries reference {reference}",
            )
            return
        visible = [d for d in dispenses if partner.may_read(d.covered_entity_id)]
        if not visible:
            self._fail(
                HTTPStatus.FORBIDDEN,
                "no_permission_for_340b_id",
                f"{partner.partner_id} holds no grant on any covered entity that payment "
                "settled",
            )
            return
        self._respond(
            HTTPStatus.OK, [beacon_payloads.payment_reference(d) for d in visible]
        )


# ═══ lifecycle ═════════════════════════════════════════════════════════════════════


class LoopbackBeaconServer:
    """A Beacon-shaped HTTP endpoint on 127.0.0.1, started and stopped by a test.

    Use it as a context manager, or call :meth:`start` and :meth:`stop`.  The port is
    assigned by the OS — bind to 0, read :attr:`port` back — so a suite running two of these,
    or two suites running at once, cannot collide on a fixed number.

    ::

        with LoopbackBeaconServer(load_source(settings)) as beacon:
            base = beacon.base_url          # http://127.0.0.1:<port>
            partner = beacon.partners[READ_WRITE_PARTNER_ID]

    The state is per instance: token issuances and submission history live on the server
    object and vanish with it, so two tests never share a budget or a submitted set.
    """

    host = "127.0.0.1"

    def __init__(
        self,
        source: VendorSource,
        *,
        partners: Mapping[str, Partner] | None = None,
        token_budget: int = DEFAULT_TOKEN_BUDGET,
    ) -> None:
        self.partners: dict[str, Partner] = dict(
            default_partners(source) if partners is None else partners
        )
        self._state = _BeaconState(source, self.partners, token_budget)
        self._httpd: _BeaconHttpServer | None = None
        self._thread: threading.Thread | None = None
        self._port: int | None = None

    # --- lifecycle -------------------------------------------------------

    @property
    def port(self) -> int:
        if self._port is None:
            raise RuntimeError("the server is not running; call start() first")
        return self._port

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def start(self) -> "LoopbackBeaconServer":
        if self._httpd is not None:
            raise RuntimeError("the server is already running")
        httpd = _BeaconHttpServer((self.host, 0), self._state)
        self._httpd = httpd
        self._port = httpd.socket.getsockname()[1]
        self._thread = threading.Thread(
            target=httpd.serve_forever,
            # A short poll interval so shutdown() is noticed promptly rather than after the
            # default half second, which a suite of many small servers would pay for once
            # per server.
            kwargs={"poll_interval": 0.05},
            name="recon-loopback-beacon",
            daemon=True,
        )
        self._thread.start()
        return self

    def stop(self) -> None:
        """Close everything this server opened, in the order that cannot deadlock.

        ``shutdown()`` first, which asks the serving loop to return and blocks until it has;
        then the thread is joined so "stopped" is true rather than imminent; then
        ``server_close()``, which closes the listener and — because ``block_on_close`` is on
        and ``daemon_threads`` is off — joins every handler thread.  Closing the listener
        first would leave the loop polling a socket that no longer exists.
        """
        httpd, self._httpd = self._httpd, None
        if httpd is not None:
            httpd.shutdown()
        if self._thread is not None:
            self._thread.join(timeout=_JOIN_TIMEOUT)
            self._thread = None
        if httpd is not None:
            httpd.server_close()
        self._port = None

    def __enter__(self) -> "LoopbackBeaconServer":
        return self.start()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.stop()

    # --- what a client needs to know --------------------------------------

    def credentials(self, partner_id: str = READ_WRITE_PARTNER_ID) -> dict[str, str]:
        """The ``POST /oauth/token`` body for one of this server's partners.

        Here so a caller does not have to know how a default secret is derived, and so the
        request shape lives beside the handler that parses it rather than in every test.
        """
        partner = self.partners[partner_id]
        return {"partner_id": partner.partner_id, "client_secret": partner.client_secret}

    def known_beacon_ids(self) -> tuple[str, ...]:
        """Every Beacon ID this server can answer for, in source order.

        A read-through, and the only way a caller learns an id without going through the
        sidecar — useful for a test that wants a claim without caring which one.
        """
        return tuple(self._state.by_beacon_id)

    def payment_references(self) -> tuple[str, ...]:
        """Every payment reference that settled at least one claim, in source order."""
        return tuple(self._state.by_payment_reference)

    def ambiguous_submission_keys(self) -> tuple[tuple[Any, ...], ...]:
        """Natural 340B keys naming more than one claim, which no submission can resolve.

        Empty on ``demo``, 19 keys on ``full``, all medical.  A readiness report can count
        this before a connector ever runs, instead of inferring it from a scatter of 409s.
        """
        return self._state.ambiguous_submission_keys
