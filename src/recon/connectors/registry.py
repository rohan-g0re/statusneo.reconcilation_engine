"""Sources declared as data, so onboarding one is a config row rather than an edit.

Requirement A1.  Today ``config.FEED_FILENAMES`` is a fixed six-tuple and
``pipeline.FEED_SOURCE_SYSTEMS`` is a dict literal keyed by filename.  Both assume a local
directory containing exactly six known files, and both have to be edited to add a seventh.
A connector fabric cannot work that way — Doc 2's whole premise is six 340B platforms plus
six adjacent systems, onboarded over weeks, each with its own transport and its own cadence.

So a source becomes a row: id, vendor, transport kind, endpoint, credential reference,
mapping version, enabled flag (``DOC2-002`` step 1 and step 2 between them ask for exactly
these).  The acceptance test for A1 is that adding a seventh source is a config row plus a
mapping module, with **no edit to ``pipeline.py``** — and the way to keep that true is for
``pipeline.py`` to never name a vendor again.

**``mapping_version`` is per source, and that is a correction rather than an addition.**
``config.ADAPTER_VERSION`` is a single global written onto every ``normalized_record``.  Once
several vendors have independently versioned mappings, one global version is not merely
incomplete, it is actively misleading: it claims every record was mapped by the same logic
at a moment when that has stopped being true.  The constant stays as the *fabric* version;
the per-source value says which vendor mapping actually ran.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from typing import Mapping, Sequence

from recon import config
from recon.connectors.transport import LocalDirectoryTransport, Transport
from recon.domain.enums import SourceSystem, TransportKind

__all__ = [
    "PayloadFormat",
    "UnknownPayloadFormatError",
    "Source",
    "local_sources",
    "VENDOR_SOURCE_IDS",
    "vendor_sources",
    "BEACON_SOURCE_IDS",
    "BEACON_SUBMISSION_SOURCE_ID",
    "beacon_sources",
    "enabled",
    "by_id",
]


class PayloadFormat(StrEnum):
    """How a document is split into records.  A closed vocabulary, checked at construction.

    It used to be a free string, and a free string fails here in the worst available way.
    The loader branched on one known value and treated **everything else** as line-delimited
    JSON, so ``"csv"``, ``"CSV"`` or ``"ndjson"`` never raised — they misread the whole
    document and landed whatever survived, with no signal at all.  A typo in a registry row
    is a plausible mistake; a typo that silently changes how every record in a file is
    parsed is not a failure mode anything downstream can detect.

    Deliberately **not** in ``domain.enums``, where the other closed vocabularies live.
    That module's own rule is that a vocabulary belongs to it when it appears on the wire
    and is backed by a SQL ``CHECK``.  A payload format is neither: no column stores it and
    no source ever sends it.  It is connector configuration, and the precedent for connector
    configuration keeping its own enum next to the code that reads it is
    :class:`~recon.connectors.credentials.CredentialKind`.

    ``BANK_CSV`` names a *shape* — a header row, one record per line, an empty cell meaning
    absent — and not the source that first had that shape.  Any delimited source declares
    it, and each is attributed from its own registry row.
    """

    JSONL = "jsonl"
    BANK_CSV = "bank_csv"


class UnknownPayloadFormatError(ValueError):
    """A source row declares a payload format nothing knows how to split.

    A ``ValueError``, which is the opposite of the choice
    :class:`~recon.connectors.credentials.CredentialError` makes and for the same reason
    read in reverse.  A missing credential is a fact about the environment the process is
    running in, so it is a ``RuntimeError``.  A bad ``payload_format`` is a fact about an
    argument a caller passed — a registry row written wrong, in this repository, by us —
    which is what ``ValueError`` is for.
    """


@dataclass(frozen=True, slots=True)
class Source:
    """One registered source and everything needed to fetch and attribute it.

    ``credential_ref`` is a *name*, never a secret.  It is the key
    ``connectors.credentials`` resolves from the environment or a local secrets file, and
    keeping it a name is what lets this registry be committed to the repository at all
    (requirement A3: no credential value appears anywhere in tracked files).
    """

    source_id: str
    vendor: str
    transport_kind: TransportKind
    #: The source system attributed to a record that does not declare its own.  The 340B feed
    #: carries two systems and declares them per record, which is why this is a default and
    #: not an override.
    source_system: SourceSystem
    #: Documents this source offers, in the order they must be read.
    filenames: tuple[str, ...]
    #: Where the transport points: a directory, an SFTP path, or a base URL.
    endpoint: str
    #: How to split a document into records.  A property of the source, never inferred from
    #: the filename — inferring it is what made the loader need editing for every new vendor.
    payload_format: PayloadFormat = PayloadFormat.JSONL
    #: The *name* of a credential, resolved at fetch time. Never a secret.
    credential_ref: str | None = None
    mapping_version: str = config.ADAPTER_VERSION
    enabled: bool = True
    transport: Transport | None = field(default=None, compare=False, repr=False)

    def __post_init__(self) -> None:
        """Refuse an unknown payload format at the moment the row is written.

        Construction is the only place this check is worth anything.  Deferred to parse
        time it would fire once per document, after a transport has already fetched, and
        the old code did not even do that — it fell through to the line-delimited reader
        and misread the file.  A registry row is configuration, and configuration that is
        wrong should fail where it is declared.
        """
        try:
            payload_format = PayloadFormat(self.payload_format)
        except ValueError:
            raise UnknownPayloadFormatError(
                f"source {self.source_id!r} declares payload_format "
                f"{self.payload_format!r}, which is not a format this fabric can split. "
                f"Permitted values: {', '.join(PayloadFormat)}. Onboarding a source whose "
                "documents are shaped differently is a new member here plus a branch in "
                "the loader, never a new string in a source row."
            ) from None
        # Stored as the member rather than as whatever string it arrived as, so the
        # annotation is true and an ``is`` comparison downstream means what it reads as.
        # ``object.__setattr__`` because the row is frozen — which is also why this is the
        # only place in the module that does it.
        object.__setattr__(self, "payload_format", payload_format)

    def with_transport(self, transport: Transport) -> "Source":
        return replace(self, transport=transport)

    def require_transport(self) -> Transport:
        if self.transport is None:
            raise ValueError(
                f"source {self.source_id!r} has no transport bound. A registry row describes "
                "a source; binding it to a transport is what makes it fetchable."
            )
        return self.transport


def local_sources(feeds_dir: Path | str) -> tuple[Source, ...]:
    """The six generated feeds, as registry rows over one local directory.

    This is what keeps ``load_feeds`` byte-identical.  The order is ``FEED_FILENAMES``'
    order, the attribution is exactly the old ``FEED_SOURCE_SYSTEMS`` mapping, and each file
    is its own source — which matters because ``insert_ingest_batch`` records one batch per
    file and every existing assertion about batch counts depends on that.

    One source per file rather than one source holding six filenames, for the same reason:
    a batch is per file, and a source that fetched six documents would want to be one batch.
    """
    transport = LocalDirectoryTransport(feeds_dir)
    rows: list[Source] = []
    for filename in config.FEED_FILENAMES:
        rows.append(
            Source(
                source_id=Path(filename).stem,
                vendor="generated",
                transport_kind=TransportKind.LOCAL_DIRECTORY,
                source_system=_GENERATED_SOURCE_SYSTEMS[filename],
                filenames=(filename,),
                payload_format=(
                    PayloadFormat.BANK_CSV
                    if filename == config.BANK_TRANSACTIONS_FILE
                    else PayloadFormat.JSONL
                ),
                endpoint=str(feeds_dir),
                transport=transport,
            )
        )
    return tuple(rows)


#: The attribution table, moved here from ``pipeline.py`` and otherwise unchanged.
#:
#: The 340B feed carries two source systems — a manufacturer's payment batch and a TPA's
#: qualification decision genuinely come from different systems that happen to be exported
#: together — and distinguishes them per record via its own ``source_system`` field.  This
#: table is the fallback for a record that declares nothing.
_GENERATED_SOURCE_SYSTEMS: dict[str, SourceSystem] = {
    config.PBM_CLAIM_EVENTS_FILE: SourceSystem.PBM_ADJUDICATION,
    config.PBM_REMITTANCE_835_FILE: SourceSystem.PBM_REMITTANCE,
    config.MEDICAL_837_SUBMISSIONS_FILE: SourceSystem.CLEARINGHOUSE_837,
    config.MEDICAL_835_REMITTANCE_FILE: SourceSystem.MEDICAL_REMITTANCE,
    config.TPA_340B_EVENTS_FILE: SourceSystem.TPA_PORTAL,
    config.BANK_TRANSACTIONS_FILE: SourceSystem.BANK,
}


# ═══ the vendor rows ════════════════════════════════════════════════════════
#
# Requirement D1's acceptance is *"Verity and Craneware differ by a config row and a mapping
# module, nothing else."*  This is the config row half.  The mapping module half is
# ``connectors/vendors/verity.py`` and ``connectors/vendors/craneware.py``, and the two halves
# genuinely do not know about each other: nothing below imports a vendor module, and nothing
# in a vendor module imports this one.  Onboarding a third secure-file vendor is one entry in
# the table below plus one mapping module, with no edit to ``pipeline.py`` — which is the
# property requirement A1 is measured on.


@dataclass(frozen=True, slots=True)
class _VendorRow:
    """The parts of a vendor's registry row that are a property of the vendor, not of us.

    Split out from :class:`Source` because an endpoint and a credential name are per
    deployment — an SFTP path per covered entity, a secret named per environment — while a
    vendor name, a payload shape and a mapping version are the same everywhere the connector
    runs.  Keeping them apart is what lets :func:`vendor_sources` take two arguments instead
    of nine.
    """

    vendor: str
    source_system: SourceSystem
    #: Blank when the landed name is not knowable in advance.  Verity stamps its exports from
    #: the data (``DOC2-010`` asks the name to retain vendor, export type and generated
    #: timestamp), so a listing transport matches the mapping's ``filename_prefix`` instead.
    filenames: tuple[str, ...]
    mapping_version: str


def _default_credential_ref(vendor: str) -> str:
    """``<vendor>_<transport>`` — the credential *name* a row resolves at fetch time.

    Derived from a convention rather than written out per row, for two reasons.  The first is
    that three hand-written strings are three chances to typo one, and a typo here resolves no
    credential and fails at fetch rather than at declaration.  The second is the rule this
    whole registry is committed under: ``credential_ref`` is a lookup key and never a secret
    (requirement A3), and the surest way to keep a field from becoming somewhere to paste a
    token is for it to have no hand-written value at all.

    ``connectors.credentials`` upper-cases and prefixes this to reach the environment, so
    ``verity`` becomes ``RECON_CONNECTOR_VERITY_SFTP_*``.
    """
    return f"{vendor}_{TransportKind.SFTP.value.lower()}"


#: The three registered secure-file datasets, keyed by the source id their schema contract is
#: registered under.  ``schema_registry.REGISTRY.source_ids()`` is the authority on that list,
#: and a test in ``tests/test_connectors.py`` already asserts every registered contract has an
#: interface-contract document, so the three spellings here are checkable against two
#: independent artefacts rather than against memory.
#:
#: **``mapping_version`` is the contract version, deliberately.**  Requirement B2 says the
#: interface contract is *"versioned with the mapping"*, so they are one string rather than
#: two that can disagree.  It is written here as a literal rather than imported from
#: ``schema_registry``, following that module's own argument: a declaration computed from the
#: thing it is supposed to be auditing can only ever agree with itself.
#:
#: Verity's two datasets share an endpoint and a credential — one export product, one SFTP
#: path — and are still two rows, because a batch is per file and the contracts are per
#: dataset.
_VENDOR_ROWS: dict[str, _VendorRow] = {
    "verity_accumulations": _VendorRow(
        vendor="verity",
        source_system=SourceSystem.TPA_PORTAL,
        filenames=(),
        mapping_version="verity-export-1.0.0",
    ),
    "verity_invoices": _VendorRow(
        vendor="verity",
        source_system=SourceSystem.TPA_PORTAL,
        filenames=(),
        mapping_version="verity-export-1.0.0",
    ),
    "craneware_claims_report": _VendorRow(
        vendor="craneware",
        source_system=SourceSystem.TPA_PORTAL,
        # Stable and undated, unlike Verity's.  Dating it would mean stamping the run date
        # into the bytes, and then the same input produces a different file on two different
        # days.
        filenames=("claims_report.csv",),
        mapping_version="craneware-sftp-export-1.0.0",
    ),
}

#: Every secure-file source this fabric knows how to declare, in onboarding order — Verity
#: first, Craneware second, which is ``DOC2-013``'s own order.
VENDOR_SOURCE_IDS: tuple[str, ...] = tuple(_VENDOR_ROWS)


def vendor_sources(
    endpoints: Mapping[str, str],
    *,
    credential_refs: Mapping[str, str] | None = None,
    filenames: Mapping[str, Sequence[str]] | None = None,
    transport: Transport | None = None,
    enabled: bool = False,
) -> tuple[Source, ...]:
    """Registry rows for the secure-file vendors, one per dataset named in ``endpoints``.

    ``endpoints`` is keyed by source id and does double duty: it says where each source lives
    *and* which sources this deployment has onboarded at all.  A source id absent from it gets
    no row, because "we never onboarded this vendor" is a different fact from "we onboarded it
    and turned it off" — see :func:`enabled`, and requirement F3's readiness report, which has
    to tell the two apart.  A source id that is not one of :data:`VENDOR_SOURCE_IDS` raises,
    since a typo in a config key is otherwise a source that silently never arrives.

    **The rows default to disabled, and that is the honest default rather than a cautious
    one.**  ``DOC2-016`` is Doc 2's own Week 3 access gate: *"Verity / Craneware — SFTP
    credentials, scheduled export definition and representative files with required
    claim-level matching keys."*  Until that gate clears there is no credential to resolve and
    no file to fetch, so a row that declared itself ready would be asserting something nobody
    has yet been given.  Pass ``enabled=True`` when the gate has cleared for this deployment.

    There is a second, sharper reason.  ``payload_format`` below is honest about the *shape* —
    a header row, one record per line, an empty cell meaning absent — but the delimited reader
    in ``ingest/pipeline.py`` reads a ``received_at`` off every row, and a vendor export does
    not carry one: its timestamps are ``qualification_received_at``, ``batch_received_at``,
    ``reversal_received_at``, and choosing among them is a mapping decision rather than a
    parsing one.  So these rows are read today by ``connectors.vendors``, which maps them, and
    not by ``load_from_sources``.  Turning one on before that seam exists would fail loudly on
    the first row, which is survivable; leaving the reason undocumented would not be.

    Args:
        endpoints: source id to the SFTP path that source is delivered to.  An SFTP path per
            covered entity; it belongs here in the registry row and never in a contract
            document or a mapping module.
        credential_refs: source id to the *name* of the credential to resolve at fetch time,
            overriding the default.  Never a secret — that is requirement A3, and it is what
            lets this registry be committed to the repository at all.
        filenames: source id to the documents to fetch, overriding the default.  Verity's
            default is empty because its export name carries a data-derived stamp, so the
            landed name is not knowable in advance and a listing transport matches
            ``SecureFileMapping.filename_prefix``; pass the landed names here when they are
            already known, which is what reading a local stand-in directory does.
        transport: bound to every row, or ``None`` to leave them unbound.  A registry row
            describes a source; binding it to a transport is what makes it fetchable.
        enabled: whether the access gate has cleared.  Shadows the module-level
            :func:`enabled` inside this function only, which is not called here.

    Raises:
        KeyError: an ``endpoints`` key that is not a registered secure-file source.
    """
    unknown = sorted(set(endpoints) - set(_VENDOR_ROWS))
    if unknown:
        known = ", ".join(VENDOR_SOURCE_IDS)
        raise KeyError(
            f"{unknown} are not registered secure-file sources; known sources: {known}. "
            "A mistyped config key is a source that silently never arrives, which is why "
            "this raises rather than skipping it."
        )

    credential_overrides = dict(credential_refs or {})
    filename_overrides = dict(filenames or {})

    rows: list[Source] = []
    for source_id, declared in _VENDOR_ROWS.items():
        if source_id not in endpoints:
            continue
        rows.append(
            Source(
                source_id=source_id,
                vendor=declared.vendor,
                transport_kind=TransportKind.SFTP,
                source_system=declared.source_system,
                filenames=tuple(filename_overrides.get(source_id, declared.filenames)),
                endpoint=endpoints[source_id],
                # ``BANK_CSV`` names a shape and not the source that first had it — a header
                # row, one record per line, an empty cell meaning absent — and that is
                # exactly what both vendors write.  Declaring a new member for "the same
                # shape, a different vendor" is how a format enum becomes a vendor list.
                payload_format=PayloadFormat.BANK_CSV,
                credential_ref=credential_overrides.get(
                    source_id, _default_credential_ref(declared.vendor)
                ),
                mapping_version=declared.mapping_version,
                enabled=enabled,
                transport=transport,
            )
        )
    return tuple(rows)


# ═══ the Beacon rows ════════════════════════════════════════════════════════
#
# Requirements C2 and C3.  Beacon is the only bidirectional source in the assessment
# (``DOC2-007``: *"Direction — Outbound eligible pharmacy / medical claims; inbound
# acknowledgements, validation outcomes, Beacon IDs, rebate status and reconciliation
# data."*), so it is the only vendor whose rows are not all pull rows.
#
# A separate table and a separate function rather than more entries in ``_VENDOR_ROWS``,
# because that table is the secure-file half of requirement D1 and every row in it is
# SFTP-delivered, delimited text with a trailer.  Folding an HTTP/JSON source into it would
# mean ``vendor_sources`` growing a branch on transport kind — and D1's acceptance is that
# the *file* vendors differ by a config row and nothing else, which stops being checkable the
# moment the function serving them also serves an API.
#
# Nothing here imports ``connectors/vendors/beacon.py`` and that module imports nothing here:
# the mapping reads ``source.source_id`` off the row it is handed and never names one.  So a
# source id exists in exactly one place in the repository and there is nothing to keep in
# step.


#: One row per inbound payload kind, plus the outbound leg, keyed by source id.
#:
#: **One source per payload kind rather than one Beacon source holding four documents**, for
#: the reason :func:`local_sources` gives: ``insert_ingest_batch`` records one batch per
#: document, so a source fetching four would want to be one batch and the four would lose
#: their separate control of cadence and checkpoint.  The four are genuinely independent — an
#: acknowledgement arrives on submission, a payment reference arrives when a rebate settles
#: weeks later.
#:
#: ``source_system`` is ``MANUFACTURER_REBATE`` on every row.  Requirement §4.7 names a
#: ``SourceSystem.BEACON`` member and it does not exist in ``domain.enums`` yet — only the
#: four ``KeyType`` members landed — so until it does, a Beacon record is attributed to the
#: system whose decision it carries.  That is exactly how the 340B feed attributes its own
#: ``MANUFACTURER_DECISION`` and ``REBATE_PAYMENT_BATCH`` records, so the attribution is
#: consistent rather than merely available.
_BEACON_ROWS: dict[str, _VendorRow] = {
    "beacon_submissions": _VendorRow(
        vendor="beacon",
        source_system=SourceSystem.MANUFACTURER_REBATE,
        # Empty, and not an oversight.  There is nothing to *fetch* from the submission
        # endpoint: this row exists to say where Beacon lives and which credential name to
        # resolve, which is what requirement C2's outbound adapter needs and all it needs.
        # A transport pointed at it returns no documents, which is correct.
        filenames=(),
        mapping_version="beacon-api-1.0.0",
    ),
    "beacon_acknowledgements": _VendorRow(
        vendor="beacon",
        source_system=SourceSystem.MANUFACTURER_REBATE,
        filenames=("acknowledgement.jsonl",),
        mapping_version="beacon-api-1.0.0",
    ),
    "beacon_validation_outcomes": _VendorRow(
        vendor="beacon",
        source_system=SourceSystem.MANUFACTURER_REBATE,
        filenames=("validation_outcome.jsonl",),
        mapping_version="beacon-api-1.0.0",
    ),
    "beacon_rebate_status": _VendorRow(
        vendor="beacon",
        source_system=SourceSystem.MANUFACTURER_REBATE,
        filenames=("rebate_status.jsonl",),
        mapping_version="beacon-api-1.0.0",
    ),
    "beacon_payment_references": _VendorRow(
        vendor="beacon",
        source_system=SourceSystem.MANUFACTURER_REBATE,
        filenames=("payment_reference.jsonl",),
        mapping_version="beacon-api-1.0.0",
    ),
}

#: Every Beacon source this fabric can declare, outbound first — ``DOC2-007``'s own order.
BEACON_SOURCE_IDS: tuple[str, ...] = tuple(_BEACON_ROWS)

#: The one outbound row.  Named because the outbound adapter has to be handed exactly this
#: row and a caller should not have to know the spelling.
BEACON_SUBMISSION_SOURCE_ID = "beacon_submissions"


def beacon_sources(
    endpoints: Mapping[str, str],
    *,
    credential_refs: Mapping[str, str] | None = None,
    filenames: Mapping[str, Sequence[str]] | None = None,
    transport: Transport | None = None,
    enabled: bool = False,
) -> tuple[Source, ...]:
    """Registry rows for Beacon, one per source id named in ``endpoints``.

    The same shape as :func:`vendor_sources` and for the same reasons: ``endpoints`` says
    both where each source lives *and* which ones this deployment has onboarded at all, a key
    that is not a registered Beacon source raises rather than silently never arriving, and
    the rows default to **disabled**.

    That default is sharper here than it is for the file vendors.  ``DOC2-008`` states
    plainly that Beacon's *"SDK package, API reference, rate limits, versioning policy,
    non-production endpoint and support SLA are not exposed in full public documentation and
    must be obtained through Beacon Support"*, and ``BEACON-012`` adds that each covered
    entity must separately authorise partner access, *"repeated across applicable 340B IDs"*.
    A row that declared itself ready would be asserting two things nobody has been given: a
    credential, and an entity's permission.

    ``payload_format`` is ``JSONL`` on every row, including the outbound one.  Beacon is an
    API (``BEACON-010``: an SDK is the intended submission path), so its payloads are objects
    with nested nulls and no natural column order; flattening them to a delimited shape would
    invent a structure the transport never has.

    Args:
        endpoints: source id to the base URL that source is served from.  Per deployment and
            per covered entity, which is why it belongs on the row and never in a mapping
            module.
        credential_refs: source id to the *name* of the credential to resolve at request
            time, overriding the default ``beacon_http_api``.  Never a secret — requirement
            A3, and it is what lets this registry be committed at all.
        filenames: source id to the documents to fetch, overriding the default.  The defaults
            are the payload-kind names ``recon.mocks.beacon_payloads`` writes, which is what
            a local stand-in directory offers; an HTTP transport maps them to its own paths.
        transport: bound to every row, or ``None`` to leave them unbound.
        enabled: whether the vendor-access gate has cleared for this deployment.  Shadows the
            module-level :func:`enabled` inside this function only, which is not called here.

    Raises:
        KeyError: an ``endpoints`` key that is not a registered Beacon source.
    """
    unknown = sorted(set(endpoints) - set(_BEACON_ROWS))
    if unknown:
        known = ", ".join(BEACON_SOURCE_IDS)
        raise KeyError(
            f"{unknown} are not registered Beacon sources; known sources: {known}. "
            "A mistyped config key is a source that silently never arrives, which is why "
            "this raises rather than skipping it."
        )

    credential_overrides = dict(credential_refs or {})
    filename_overrides = dict(filenames or {})

    rows: list[Source] = []
    for source_id, declared in _BEACON_ROWS.items():
        if source_id not in endpoints:
            continue
        rows.append(
            Source(
                source_id=source_id,
                vendor=declared.vendor,
                transport_kind=TransportKind.HTTP_API,
                source_system=declared.source_system,
                filenames=tuple(filename_overrides.get(source_id, declared.filenames)),
                endpoint=endpoints[source_id],
                payload_format=PayloadFormat.JSONL,
                credential_ref=credential_overrides.get(
                    source_id, _default_api_credential_ref(declared.vendor)
                ),
                mapping_version=declared.mapping_version,
                enabled=enabled,
                transport=transport,
            )
        )
    return tuple(rows)


def _default_api_credential_ref(vendor: str) -> str:
    """``<vendor>_http_api`` — the credential *name* an API row resolves at request time.

    The same convention :func:`_default_credential_ref` uses one transport over, and derived
    rather than written per row for the same two reasons: five hand-written strings are five
    chances to typo one, and a field with no hand-written value is a field that never becomes
    somewhere to paste a token.

    What it resolves to is a **token pair**, not an SSH key — ``BEACON-011``: *"Partner
    onboarding yields an Access Token plus a separate Private Token used to authenticate API
    requests."*  That is single-sourced and recorded as such; the pages that would confirm it
    are ``BEACON-001`` through ``BEACON-007`` and every one is a 403.
    """
    return f"{vendor}_{TransportKind.HTTP_API.value.lower()}"


def enabled(sources: Sequence[Source]) -> tuple[Source, ...]:
    """Only the sources currently switched on.

    A disabled source stays in the registry rather than being deleted, because "we onboarded
    this vendor and turned it off" and "we never onboarded this vendor" are different facts
    and the readiness report in requirement F3 has to tell them apart.
    """
    return tuple(source for source in sources if source.enabled)


def by_id(sources: Sequence[Source], source_id: str) -> Source:
    for source in sources:
        if source.source_id == source_id:
            return source
    known = ", ".join(sorted(item.source_id for item in sources))
    raise KeyError(f"no source {source_id!r} is registered; known sources: {known}")
