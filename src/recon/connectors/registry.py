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
from typing import Sequence

from recon import config
from recon.connectors.transport import LocalDirectoryTransport, Transport
from recon.domain.enums import SourceSystem, TransportKind

__all__ = [
    "PayloadFormat",
    "UnknownPayloadFormatError",
    "Source",
    "local_sources",
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
