"""The third transport, and the only one that talks to a vendor's API.

Requirement A2's third implementation.  ``DOC2-001`` scores *API / SDK* as the one production
pattern with nothing behind it — *"No outbound HTTP client exists anywhere in the codebase.
No auth, no token handling, no submission path"* — and ``DOC2-007`` puts Beacon on it:
outbound claims, inbound acknowledgements, validation outcomes, Beacon IDs and rebate status.
This module is the **pull** half of that.  Submitting a claim is requirement C2 and belongs to
``connectors/vendors/beacon.py``; a :class:`~recon.connectors.transport.Transport` has one
method and it returns documents, so a write is not something it can express.

**``source.filenames`` means something different here, and that is the one thing to read
twice.**  For :class:`~recon.connectors.sftp.SftpTransport` an entry is a *remote filename* in
a directory.  For an API there is no directory and no filename: an entry here is a **resource
path under the source's base URL** — ``claims/status``, ``rebates?window=45d`` — one request
each, one :class:`~recon.connectors.transport.Document` each, named by the path that produced
it.  The registry row is the same shape either way, which is what requirement A1 is for; only
the reading of the field changes with the transport that reads it.

**Two tokens on every request (``BEACON-011``), and the header names are INVENTED.**
``DOC2-007`` page 3 says *"Beacon partner Access Token + Private Token"*, and that is
second-hand and **single-sourced** — every first-hand Beacon page that would confirm it
returned HTTP 403 and is recorded UNAVAILABLE in ``docs/vendor_evidence/beacon.md``.  That
file also lists *how* the two tokens are presented — header names, scheme, ordering — as
genuinely UNKNOWN, and ``DOC2-008`` says the API reference *"must be obtained through Beacon
Support"*.  So the header names below are ours.  They are **constructor arguments with
declared defaults, not literals in the request**, and that is deliberate: §0 defines
connector-ready as *"switchable to a live endpoint by changing configuration only"*, and a
header name baked into this module would make the day Beacon Support answers a code edit
instead of a config change.  Base URL, port and credential ref come from the registry row and
the environment for the same reason.  Nothing about the destination is written down here.

**Nothing in this module chains an exception, and the reason is specific rather than
cautious.**  ``httpx.RequestError`` carries ``.request``, and ``request.headers`` holds both
tokens.  ``httpx.Headers.__repr__`` obscures exactly two names — ``authorization`` and
``proxy-authorization`` — so an invented header renders **in full** from any reporter that
walks an exception chain and reprs what it finds.  That is ``credentials._load_entry``'s
``__context__`` lesson one layer up: ``raise ... from None`` would not help either, because it
clears ``__cause__`` and leaves ``__context__`` pointing at the same object.  So every failure
is carried out of its ``except`` block as a **string** and raised after the handler has exited,
where there is no exception in flight and no chain to walk.  ``SftpTransport`` chains its
connection errors because paramiko's text is the whole diagnostic and holds nothing secret;
that argument does not survive the move to HTTP, so neither does the behaviour.

**No response body appears in any message, for the same reason read from the other end.**  A
401 page or a 500 stack trace routinely echoes the request back, headers included.  A status
failure here reports the code, the URL and the body's *length*, never its content.

**Redirects are not followed.**  A 3xx is a server proposing a destination that this
transport's reach check never approved, and httpx forwards headers across a same-host
redirect — so following one would be both a §4.9 hole and a way to hand two tokens to a URL
nobody configured.  A redirect is reported as the status failure it is.

**NO retry.  NO backoff.  NO rate limiting.**  Stated here so the absence reads as a decision
and not as an oversight: Doc 2 files retry/replay and rate limits under **step 6**, production
hardening, and this build stops at step 5.  ``DOC2-008`` additionally lists Beacon's real rate
limits among the things *not* publicly documented, so a limiter built now would be enforcing a
number we invented.  A failed fetch fails loudly, by name, and is re-run by hand.

**§4.9 is re-earned, not inherited.**  A directory argument could not reach ``truth/`` by
construction; a URL can be spelled to reach anything the process can open.  So a base URL, a
source endpoint and a resource path are each refused if any component is the ground-truth
directory, if any traverses upwards, or if the scheme is anything but ``http``/``https`` — a
``file:`` URL is a filesystem reach wearing a transport's clothes.  Percent-encoding is
decoded before the check, because a server decodes it too.

**No clock is read.**  §4.11 confines wall-clock to the checkpoint and to logs.
``Document.modified_at`` is the server's own ``Last-Modified`` header re-rendered, or ``None``
when it sent none — which for a JSON API is the normal case and is exactly why
``connector_checkpoint.remote_mtime`` is nullable.  The timeout is a socket deadline, not a
reading of the current time.

**One call opens the client and closes it.**  :meth:`HttpApiTransport.fetch` returns a tuple
rather than a generator, the same choice ``SftpTransport`` makes and for the same reason: a
consumer that broke out of a generator's loop would leave a connection pool alive until the
collector noticed, and under this repository's ``filterwarnings = ["error"]`` that surfaces as
a ``ResourceWarning`` raised inside whatever unrelated test happened to trigger it.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import TYPE_CHECKING, Iterable
from urllib.parse import SplitResult, unquote, urlsplit, urlunsplit

from recon import config
from recon.connectors import credentials
from recon.connectors.transport import Document, ForbiddenPathError, ShouldFetch, TransportError

try:
    import httpx
except ImportError as exc:  # pragma: no cover - exercised only without the extra
    raise ImportError(
        "recon.connectors.http needs httpx, which is the 'agent' optional extra: "
        "pip install -e '.[agent]'. It is reused rather than added as a second client, which "
        "is what pyproject.toml's own comment on the 'connectors' extra records."
    ) from exc

if TYPE_CHECKING:  # pragma: no cover - import cycle guard, types only
    from recon.connectors.registry import Source

__all__ = [
    "DEFAULT_ACCESS_TOKEN_HEADER",
    "DEFAULT_PRIVATE_TOKEN_HEADER",
    "HttpApiAuthenticationError",
    "HttpApiConnectionError",
    "HttpApiDecodeError",
    "HttpApiError",
    "HttpApiStatusError",
    "HttpApiTransport",
]

#: How this build presents Beacon's Access Token.  **INVENTED.**
#:
#: ``BEACON-011`` establishes that two tokens exist; ``docs/vendor_evidence/beacon.md`` lists
#: the exchange itself — header names, scheme, ordering — as UNKNOWN, and ``DOC2-008`` says the
#: API reference is not public.  So this is a default, overridable at construction, and never a
#: literal inside a request: switching to whatever Beacon Support eventually says must be a
#: configuration change, which is §0's definition of connector-ready.
DEFAULT_ACCESS_TOKEN_HEADER = "X-Beacon-Access-Token"

#: How this build presents Beacon's Private Token.  **INVENTED**, see above.
DEFAULT_PRIVATE_TOKEN_HEADER = "X-Beacon-Private-Token"

#: The only two schemes a transport may speak.  ``file:``, ``ftp:`` and the rest are refused as
#: §4.9 violations rather than as unsupported features, because that is what they are.
_PERMITTED_SCHEMES = frozenset({"http", "https"})

#: Default ports, so a base URL that omits one still compares equal to a source endpoint that
#: states it.  Origin equality decided by string is origin equality decided wrong.
_DEFAULT_PORTS = {"http": 80, "https": 443}


# --- failures --------------------------------------------------------------


class HttpApiError(TransportError):
    """A fetch over HTTP failed.  A :class:`~recon.connectors.transport.TransportError`.

    Subclassed by remedy, not by layer — the same split ``sftp.py`` makes.  Each of the four
    below answers a different question from the operator reading it, and collapsing them would
    mean answering none of them.
    """


class HttpApiConnectionError(HttpApiError):
    """No answer came back: DNS, refused, timed out, TLS, or a malformed URL.

    Remedy: the network, the base URL or the port — never the credential.  Kept apart from
    :class:`HttpApiAuthenticationError` because "we cannot reach them" and "they will not have
    us" are different outages with different people to call.
    """


class HttpApiAuthenticationError(HttpApiError):
    """The credential is missing, unresolvable, or the vendor refused it (401/403).

    A3's acceptance criterion stated as a class: a wrong credential produces this, naming the
    ref and the variables to set, and never an httpx traceback.

    A 403 deserves a second reading before the token is blamed.  ``BEACON-012`` says each
    covered entity must explicitly authorize partner access and that *"permissions may need to
    be repeated across applicable 340B IDs"* — so a valid token with no grant behind it looks
    exactly like a bad token from out here, and the message says so.
    """


class HttpApiStatusError(HttpApiError):
    """The request was answered, and the answer was not a 2xx.

    404 means the resource path in the registry row is wrong or the vendor moved it; 5xx means
    theirs is unwell; 3xx means they proposed a redirect this transport declined to follow.
    The response body is deliberately never quoted — see the module docstring.
    """


class HttpApiDecodeError(HttpApiError):
    """A body arrived and is not UTF-8 text.

    A payload fact, not a transport fact.  Every format this fabric splits is text
    (``registry.PayloadFormat``), so a vendor shipping something else has changed the contract,
    and that is a conversation rather than a re-request.
    """


# --- urls ------------------------------------------------------------------


def _reject_forbidden_components(path: str, *, origin: str) -> None:
    """Refuse the two things a URL path may never contain.

    Percent-decoded first, and backslashes folded, because the server on the other end decodes
    and normalises too — a check that reads the raw spelling is checking a string the recipient
    will never see.  Compared case-insensitively: a refusal that is slightly too broad costs a
    resource nobody wanted to name, while one that is slightly too narrow costs the guarantee.
    """
    decoded = unquote(str(path)).replace("\\", "/")
    for component in decoded.split("/"):
        folded = component.strip().casefold()
        if folded == config.TRUTH_SUBDIR.casefold():
            raise ForbiddenPathError(
                f"{origin} {path!r} points into the ground-truth directory. Ingestion is "
                "forbidden to read it, and that prohibition is what makes the crosswalk a "
                "measurement rather than a claim. A transport that dials out over HTTP does "
                "not get an exemption."
            )
        if folded == "..":
            raise ForbiddenPathError(
                f"{origin} {path!r} traverses upwards; a transport's reach is fixed at "
                "construction and a source may not widen it"
            )


def _split_base(
    value: str,
    *,
    origin: str,
    port: int | None = None,
    default_port: int | None = None,
) -> SplitResult:
    """Parse a base URL, refuse what §4.9 and A3 forbid, and pin scheme, host and port.

    Returned with the netloc rebuilt from the hostname and the resolved port alone, which
    drops any ``user:password@`` the caller supplied.  That is not tidying: credentials in a
    URL live in a config row, and A3's acceptance is that no credential value appears in a
    tracked file at all.  It is refused loudly first; rebuilding is what stops a later edit
    from reintroducing it quietly.

    ``port`` overrides whatever the URL says and disagreeing with it is an error.
    ``default_port`` only fills a gap, and exists so a *source endpoint* that names no port
    inherits its transport's rather than the scheme's.  Without it a transport configured as
    ``http://host`` plus ``port=8080`` would refuse a source endpoint spelled ``http://host/v1``
    for being on port 80 — a true statement about two normalised URLs and a useless one about
    what the operator configured.
    """
    text = str(value).strip()
    if not text:
        raise ValueError(f"{origin} must be a non-empty http(s) URL")
    parts = urlsplit(text)
    if parts.scheme.lower() not in _PERMITTED_SCHEMES:
        raise ForbiddenPathError(
            f"{origin} {text!r} is not an http or https URL. A transport is not a filesystem "
            "and not a general URL opener; a 'file:' base would be a route into exactly the "
            "data requirement §4.9 says a transport may never resolve."
        )
    scheme = parts.scheme.lower()
    if parts.username or parts.password:
        raise ValueError(
            f"{origin} embeds a username or password in the URL. Credentials resolve from the "
            "environment or the connector secrets file by name (requirement A3); a value in a "
            "URL is a value in a config row. The value is described here and never echoed."
        )
    host = parts.hostname
    if not host:
        raise ValueError(f"{origin} {text!r} has no host")
    _reject_forbidden_components(parts.path, origin=origin)

    declared = None
    try:
        declared = parts.port
    except ValueError:
        raise ValueError(f"{origin} {text!r} has a port that is not a number") from None
    if port is not None:
        if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
            raise ValueError(f"port must be an integer between 1 and 65535, got {port!r}")
        if declared is not None and declared != port:
            raise ValueError(
                f"{origin} {text!r} declares port {declared} but port={port} was passed. One "
                "of the two is stale, and guessing which would point this connector at a host "
                "nobody configured."
            )
        resolved = port
    elif declared is not None:
        resolved = declared
    else:
        resolved = default_port if default_port is not None else _DEFAULT_PORTS[scheme]

    return SplitResult(
        scheme=scheme,
        netloc=f"{host.lower()}:{resolved}",
        path=parts.path.rstrip("/"),
        query="",
        fragment="",
    )


def _reject_unsafe_resource(resource: str, *, origin: str) -> SplitResult:
    """Refuse a ``filenames`` entry that is anything but a relative path under the base.

    The HTTP analogue of ``transport.py``'s bare-filename rule, loosened exactly once and
    tightened everywhere else.  Loosened: an internal ``/`` is allowed, because an API resource
    genuinely is a path (``claims/status``) while an SFTP name genuinely is not.  Tightened: a
    resource carrying its own scheme or host would silently retarget the request at a server
    this transport's reach check never saw, and a leading ``/`` would discard the base path the
    source was confined to — so both are refused rather than normalised.

    Checked **before** the join, not on the result, for the reason ``transport.py`` gives: the
    join is the moment the forbidden destination exists.
    """
    if not isinstance(resource, str) or not resource.strip():
        raise ForbiddenPathError(
            f"{origin} lists an empty resource. A source that requests nothing fetches nothing, "
            "and downstream that is indistinguishable from a vendor with nothing to give."
        )
    parts = urlsplit(resource.strip())
    if parts.scheme or parts.netloc:
        raise ForbiddenPathError(
            f"{origin} resource {resource!r} is a URL of its own rather than a path under the "
            "source's base URL. A source cannot widen its own transport's reach; register a "
            "second transport if it genuinely needs a different host."
        )
    if parts.path.startswith("/") or "\\" in resource:
        raise ForbiddenPathError(
            f"{origin} resource {resource!r} is an absolute path; it would discard the base "
            "path this source is confined to"
        )
    _reject_forbidden_components(parts.path, origin=f"{origin} resource")
    return parts


def _modified_at(response: "httpx.Response") -> str | None:
    """The server's ``Last-Modified`` in this repository's timestamp format, or ``None``.

    **The only source of this value is a header the server actually sent.**  No clock is read:
    §4.11 permits wall-clock in the checkpoint and in logs and nowhere else, and a
    ``datetime.now()`` here would put a real clock inside a value that is about to be persisted
    and compared on the next run — a checkpoint that always looks newer than itself.

    ``None`` is the expected answer, not a degraded one.  A JSON API has no concept of a
    modification time, which is precisely why ``connector_checkpoint.remote_mtime`` is nullable
    and why the checkpoint falls back to a content hash.  An unparseable header is also ``None``
    rather than an error: a vendor's malformed date is not a reason to fail a fetch whose bytes
    already arrived intact.
    """
    raw = response.headers.get("last-modified")
    if not raw:
        return None
    try:
        stamp = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if stamp is None:  # pragma: no cover - older email parsers returned None instead
        return None
    if stamp.tzinfo is None:
        # RFC 9110 says an HTTP-date is always GMT; "-0000" parses naive.  Treating it as UTC
        # is reading the spec, not guessing a zone.
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc).strftime(config.TIMESTAMP_FORMAT)


def _decode(raw: bytes, resource: str, source_id: str) -> str:
    """UTF-8 text with newlines folded — never ``response.text``, and never a guessed charset.

    ``httpx`` will happily decode a body using the charset in ``Content-Type`` or, failing
    that, a detected one.  A guessed encoding is a silent corruption: the bytes land, the
    records parse, and a handful of fields are subtly wrong in a way no assertion names.  Every
    payload format this fabric splits is UTF-8 text, so the honest move is to say so and fail
    by name when it is not.

    The folding matches ``SftpTransport._decode`` and ``LocalDirectoryTransport``'s text-mode
    read, so a :class:`~recon.connectors.transport.Document`'s text does not depend on which
    transport fetched it — which is what makes a checkpoint's content hash comparable across a
    change of transport.

    The failure is carried out of the handler as a string and raised after it, because
    ``UnicodeDecodeError.object`` is the **entire response body** and ``raise ... from None``
    would leave ``__context__`` pointing straight at it.
    """
    failure: str | None = None
    text = ""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        # Position and reason only.  `exc.object` -- the body -- is deliberately never read.
        failure = (
            f"source {source_id!r}: the response to {resource!r} is not UTF-8 text (byte "
            f"{exc.start} of {len(raw)}: {exc.reason}). Every payload format this fabric "
            "splits is text, so this is a changed vendor contract rather than a transfer fault."
        )
    if failure is not None:
        raise HttpApiDecodeError(failure)
    return text.replace("\r\n", "\n").replace("\r", "\n")


# --- the transport ---------------------------------------------------------


class HttpApiTransport:
    """Fetch documents from one HTTP API, confined to one base URL.

    Construction fixes the reach: a scheme, a host, a port and a base path that the sources
    bound to this transport may not point outside of.  ``SftpTransport`` fixes a remote root for
    the same reason, and ``LocalDirectoryTransport`` resolves a directory for the same reason
    before that.

    ``base_url`` is the **first positional parameter** and is validated before anything else, so
    a §4.9 refusal cannot be shadowed by an unrelated argument error — the ordering
    ``SftpTransport`` adopted deliberately and for the same reason.

    Nothing about the destination is written down in this module.  Base URL, port, credential
    ref and even the two token header names arrive as configuration, which is what makes §0's
    *"switchable to a live endpoint by changing configuration only"* literally true rather than
    aspirationally true.
    """

    kind = "HTTP_API"

    __slots__ = (
        "_access_token_header",
        "_base",
        "_client",
        "_env",
        "_private_token_header",
        "_secrets_file",
        "_timeout",
    )

    def __init__(
        self,
        base_url: str,
        *,
        port: int | None = None,
        timeout: float = 15.0,
        access_token_header: str = DEFAULT_ACCESS_TOKEN_HEADER,
        private_token_header: str = DEFAULT_PRIVATE_TOKEN_HEADER,
        env: Mapping[str, str] | None = None,
        secrets_file: Path | str | None = None,
        client: "httpx.Client | None" = None,
    ) -> None:
        """Bind a reach, not a session.  Nothing is requested until :meth:`fetch` is called.

        Args:
            base_url: the API root this transport may read — ``https://host[:port][/path]``.
                A loopback URL and a vendor URL differ only in this string, which is the whole
                point of it being an argument.
            port: the port, when the base URL does not state one or when a deployment supplies
                it separately.  Stating it in both places and disagreeing is an error rather
                than a precedence rule.
            timeout: seconds allowed for connect, write, read and pool acquisition.  A bound on
                a hang, not a reading of the clock.
            access_token_header: how the Access Token is presented.  **INVENTED** — see
                :data:`DEFAULT_ACCESS_TOKEN_HEADER`.
            private_token_header: how the Private Token is presented.  **INVENTED**.
            env: environment to resolve the credential from.  Defaults to ``os.environ``.
            secrets_file: overrides the connector secrets file location.
            client: an ``httpx.Client`` to borrow instead of opening one per fetch.  Supplied,
                it is **not** closed here — the opener closes.  It exists so a test can drive
                this against ``httpx.MockTransport`` with no socket at all, and so a deployment
                can hand over a client carrying its own TLS or proxy configuration without this
                module growing an option for each.
        """
        self._base = _split_base(base_url, origin="transport base_url", port=port)
        for name, value in (
            ("access_token_header", access_token_header),
            ("private_token_header", private_token_header),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty header name, got {value!r}")
        if access_token_header.strip().casefold() == private_token_header.strip().casefold():
            raise ValueError(
                "the two token headers must differ. BEACON-011 is that two separate tokens "
                "authenticate a request; presenting both under one name sends one of them."
            )
        self._access_token_header = access_token_header.strip()
        self._private_token_header = private_token_header.strip()
        self._timeout = float(timeout)
        self._env = env
        self._secrets_file = secrets_file
        self._client = client

    # --- what it is ------------------------------------------------------

    @property
    def base_url(self) -> str:
        return urlunsplit(self._base)

    @property
    def host(self) -> str:
        return self._base.netloc.rsplit(":", 1)[0]

    @property
    def port(self) -> int:
        return int(self._base.netloc.rsplit(":", 1)[1])

    @property
    def token_headers(self) -> tuple[str, str]:
        """The two header names, access first.  INVENTED; see the module docstring."""
        return (self._access_token_header, self._private_token_header)

    def __repr__(self) -> str:  # pragma: no cover - diagnostic only
        return f"HttpApiTransport({self.base_url!r})"

    # --- the one method --------------------------------------------------

    def fetch(
        self,
        source: "Source",
        *,
        should_fetch: ShouldFetch | None = None,
    ) -> Iterable[Document]:
        """Every resource this source declares, requested once each, in declared order.

        Args:
            source: the registry row.  ``endpoint`` is the base URL for this source and must
                sit at or under the transport's own; ``filenames`` are **resource paths under
                it**, not filenames — see the module docstring, because the same field means
                remote filenames to ``SftpTransport``; ``credential_ref`` names the token pair.
            should_fetch: optional predicate ``(name, modified_at) -> bool``.  Consulted before
                the request is made, with ``modified_at=None``, and that ``None`` is honest
                rather than lazy: SFTP gets a modification time from a listing round trip that
                costs one request for the whole directory, while HTTP has no listing — the only
                way to learn a resource's ``Last-Modified`` is to ask for the resource.  So A4's
                *"the second run downloads zero bytes"* holds here only as far as the caller's
                name-based decision reaches, and ``checkpoint.content_changed`` rules on the
                rest after the bytes arrive.  That weaker guarantee is the true one for a
                remote that will not say when it last wrote, and ``checkpoint.py``'s own
                docstring names this case.

        Returns:
            A tuple, not a generator.  See the module docstring: the client is opened and closed
            inside this call so an abandoned iterator cannot leak a connection pool.

        Raises:
            ValueError: the source declares no resources, or its endpoint is unusable.
            ForbiddenPathError: the endpoint escapes the transport's base URL, or a resource
                path is absolute, is a URL of its own, traverses upwards, or names the
                ground-truth directory.
            HttpApiAuthenticationError, HttpApiConnectionError, HttpApiStatusError,
            HttpApiDecodeError: named by remedy, never swallowed, never a raw httpx traceback.
        """
        base = self._endpoint_of(source)
        resources = self._resources_of(source)
        credential = self._credential_for(source)

        client, owned = self._open()
        try:
            documents: list[Document] = []
            for resource, parts in resources:
                if should_fetch is not None and not should_fetch(resource, None):
                    continue
                response = self._request(client, base, parts, resource, source, credential)
                documents.append(
                    Document(
                        name=resource,
                        text=_decode(response.content, resource, source.source_id),
                        modified_at=_modified_at(response),
                    )
                )
            return tuple(documents)
        finally:
            if owned:
                client.close()

    # --- steps -----------------------------------------------------------

    def _open(self) -> tuple["httpx.Client", bool]:
        """The client to use, and whether closing it is our job.

        ``follow_redirects`` is off.  httpx forwards headers across a same-host redirect, so
        following one would hand both tokens to a URL the reach check never approved — and a
        redirect off-host is the §4.9 hole in its purest form.  A 3xx is reported as the status
        failure it is.
        """
        if self._client is not None:
            return self._client, False
        return httpx.Client(timeout=self._timeout, follow_redirects=False), True

    def _endpoint_of(self, source: "Source") -> SplitResult:
        """The source's base URL, checked against the one fixed at construction.

        A blank endpoint means "the transport's own base", which is the common case for an API:
        the path lives in ``filenames``, one entry per resource, and there is nothing left for
        the row to add.  A stated endpoint must share the transport's scheme, host and port and
        sit at or under its path, at a component boundary — ``/v1`` must not admit ``/v1x``.

        An endpoint that names no port inherits the transport's, rather than the scheme's.  See
        :func:`_split_base`: the alternative refuses the ordinary loopback configuration for
        being a true statement about two normalised URLs.
        """
        declared = str(source.endpoint or "").strip()
        if not declared:
            return self._base
        endpoint = _split_base(
            declared,
            origin=f"source {source.source_id!r} endpoint",
            default_port=self.port,
        )
        same_origin = (
            endpoint.scheme == self._base.scheme and endpoint.netloc == self._base.netloc
        )
        under = endpoint.path == self._base.path or endpoint.path.startswith(
            self._base.path + "/"
        )
        if not same_origin or not under:
            raise ForbiddenPathError(
                f"source {source.source_id!r} points at {declared!r}, which is outside "
                f"{self.base_url}. A source cannot widen its own transport's reach; register a "
                "second transport if it genuinely needs a different host or root."
            )
        return endpoint

    def _resources_of(self, source: "Source") -> tuple[tuple[str, SplitResult], ...]:
        """``source.filenames`` read as resource paths, checked, deduplicated, order kept.

        An empty ``filenames`` is refused rather than treated as "nothing to do".  For
        ``SftpTransport`` an empty tuple is meaningful — the listing supplies the names — but
        there is no listing here, so a source with no resources fetches nothing and produces an
        empty ingest that looks exactly like a clean one.  That is the failure this package's
        own docstring says a transport must never produce silently.

        A ``ValueError`` rather than a :class:`HttpApiError`, following the split
        ``registry.UnknownPayloadFormatError`` already makes: a missing credential is a fact
        about the environment the process runs in and is a ``RuntimeError``; an empty resource
        list is a fact about a registry row we wrote, which is what ``ValueError`` is for.
        """
        if not source.filenames:
            raise ValueError(
                f"source {source.source_id!r} declares no resources. For an HTTP source, "
                "'filenames' is the list of resource paths to request under its base URL -- "
                "there is no listing to discover them from, so an empty list is a source that "
                "silently never arrives."
            )
        chosen: list[tuple[str, SplitResult]] = []
        seen: set[str] = set()
        for entry in source.filenames:
            parts = _reject_unsafe_resource(entry, origin=f"source {source.source_id!r}")
            resource = str(entry).strip()
            if resource in seen:
                # Requested once.  `insert_ingest_batch` records one batch per document, so a
                # resource fetched twice would be two batches for one delivery.
                continue
            seen.add(resource)
            chosen.append((resource, parts))
        return tuple(chosen)

    def _credential_for(self, source: "Source") -> credentials.TokenPairCredential:
        """Resolve the token pair this source names.  The ref is a name; this is the value.

        ``kind`` is pinned by calling ``resolve_token_pair`` rather than inferred, which is what
        ``credentials.resolve``'s own docstring asks a transport to do: a transport knows what
        shape it needs, and saying so is what turns "nothing configured" into a message naming
        the right variables instead of a shrug.
        """
        if not source.credential_ref:
            raise HttpApiAuthenticationError(
                f"source {source.source_id!r} has no credential_ref. A Beacon-style HTTP source "
                "authenticates with an Access Token plus a separate Private Token (BEACON-011), "
                "and the registry row names which pair -- the name is the thing that is safe to "
                "commit."
            )
        return credentials.resolve_token_pair(
            source.credential_ref, env=self._env, secrets_file=self._secrets_file
        )

    def _url(self, base: SplitResult, parts: SplitResult) -> str:
        """Join a checked base to a checked resource.  String concatenation, not ``urljoin``.

        ``urljoin`` implements RFC 3986 reference resolution, which is a feature set this does
        not want: under it a resource beginning ``//`` adopts a new host and one beginning ``/``
        replaces the base path. Both are refused above, and using a joiner that would have
        honoured them means the refusal is the only thing standing between a config typo and a
        request to somewhere else.
        """
        path = f"{base.path}/{parts.path.lstrip('/')}" if parts.path else base.path or "/"
        return urlunsplit((base.scheme, base.netloc, path or "/", parts.query, ""))

    def _request(
        self,
        client: "httpx.Client",
        base: SplitResult,
        parts: SplitResult,
        resource: str,
        source: "Source",
        credential: credentials.TokenPairCredential,
    ) -> "httpx.Response":
        """One authenticated GET, or a failure named by remedy.

        **Both tokens are revealed here and nowhere else in this module, inside the argument
        list of the call that sends them.**  Never into a named local, never into a dict that
        outlives the call, never into a log record — the complete list of places this process
        can emit a credential is the list of ``.reveal()`` call sites, which is the property
        that makes ``Secret`` worth having.

        Every failure is carried out of its handler as a string and raised once the handler has
        exited, with **no cause attached anywhere**.  ``httpx.RequestError.request.headers``
        holds both tokens and ``httpx.Headers.__repr__`` obscures only ``authorization`` and
        ``proxy-authorization``, so a chained httpx exception is a live reference to the two
        tokens for anything that walks and reprs an exception chain — Sentry, a
        ``repr(vars(exc))`` debug line, pytest's own chained-traceback rendering.  ``from None``
        is not a fix: it clears ``__cause__`` and leaves ``__context__`` pointing at the same
        object, which is the trap ``credentials._load_entry`` already had to be rewritten out
        of.

        The status branch quotes no part of the body.  A 401 page or a 500 stack trace routinely
        echoes the request back, headers included, so the length is reported and the content is
        not.
        """
        url = self._url(base, parts)
        unreachable: str | None = None
        response: "httpx.Response | None" = None
        try:
            response = client.get(
                url,
                headers={
                    self._access_token_header: credential.access_token.reveal(),
                    self._private_token_header: credential.private_token.reveal(),
                },
            )
        except (httpx.HTTPError, OSError) as exc:
            unreachable = (
                f"source {source.source_id!r}: no response from {url} "
                f"({type(exc).__name__}: {exc}). No retry is attempted -- retry, backoff and "
                "rate limiting are Doc 2 step 6 and are not built; re-run this fetch by hand."
            )
        if unreachable is not None:
            raise HttpApiConnectionError(unreachable)
        assert response is not None  # noqa: S101 -- narrowing, not a check

        status = response.status_code
        if status in (401, 403):
            variables = ", ".join(
                credentials.env_var_names(
                    str(source.credential_ref), credentials.CredentialKind.TOKEN_PAIR
                )
            )
            grant = (
                " A 403 is as likely to be a missing grant as a bad token: BEACON-012 says each "
                "covered entity must explicitly authorize partner access, and that permission "
                "may have to be repeated across applicable 340B IDs."
                if status == 403
                else ""
            )
            raise HttpApiAuthenticationError(
                f"source {source.source_id!r}: {url} returned HTTP {status} for the token pair "
                f"resolved from credential {source.credential_ref!r}. The values come from "
                f"{variables} or from the connector secrets file, and neither is echoed here "
                f"-- requirement A3.{grant} The response body is not quoted, because a refusal "
                "page routinely repeats the request headers back."
            )
        if not 200 <= status < 300:
            hint = (
                " A 3xx is a redirect this transport declines to follow, because httpx would "
                "carry both token headers to the proposed destination."
                if 300 <= status < 400
                else ""
            )
            raise HttpApiStatusError(
                f"source {source.source_id!r}: {url} returned HTTP {status} "
                f"({len(response.content)} bytes, not quoted).{hint} No retry is attempted -- "
                "that is Doc 2 step 6."
            )
        return response
