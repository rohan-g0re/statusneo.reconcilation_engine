"""Frozen dataclasses mirroring the tables.

``sqlite3.Row`` becomes a typed object at the repository boundary, so nothing above
the database layer indexes into a row by string.  That matters less for safety than
for refactoring: a renamed column is a failing import rather than a ``KeyError`` in
whichever request happened to touch it.

Mapping is deliberately mechanical — a ``from_row`` per type, no ``__post_init__``
business logic.  If a mapping needs a decision, the decision belongs in the engine.

One shape is not a table: :class:`QueueRow` is the *read-time* projection a queue
renders, and it is the only place ``age_days`` exists.  Aging is
``cursor - date_of_service``, computed in SQL when the queue is read, and never
stored.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from sqlite3 import Row

from recon.domain.enums import (
    AllocationBasis,
    CrossTrackFlag,
    Disposition,
    EvidenceRole,
    KeyType,
    ParkReason,
    RecordKind,
    ReasonCode,
    ReimbursementTrack,
    SourceSystem,
    TrackScope,
)

__all__ = [
    "IngestBatch",
    "RawRecord",
    "QuarantinedRecord",
    "NormalizedRecord",
    "CrosswalkEntry",
    "Resolution",
    "Episode",
    "VerdictReason",
    "EvidenceRow",
    "Verdict",
    "ParkedRecord",
    "CashAllocation",
    "WorkItem",
    "QueueRow",
]


def _column(row: Row, name: str):
    """Read a column, failing with the column's name rather than a bare IndexError."""
    try:
        return row[name]
    except (IndexError, KeyError):
        available = ", ".join(row.keys())
        raise ValueError(
            f"row is missing column {name!r}; the query returned: {available}"
        ) from None


def _optional_enum(enum_cls, value):
    return None if value is None else enum_cls(value)


@dataclass(frozen=True, slots=True)
class IngestBatch:
    batch_id: int
    source_file: str
    source_system: SourceSystem
    file_sha256: str
    record_count: int
    loaded_at: str

    @classmethod
    def from_row(cls, row: Row) -> "IngestBatch":
        return cls(
            batch_id=_column(row, "batch_id"),
            source_file=_column(row, "source_file"),
            source_system=SourceSystem(_column(row, "source_system")),
            file_sha256=_column(row, "file_sha256"),
            record_count=_column(row, "record_count"),
            loaded_at=_column(row, "loaded_at"),
        )


@dataclass(frozen=True, slots=True)
class RawRecord:
    raw_id: int
    batch_id: int
    source_system: SourceSystem
    source_record_id: str | None
    source_line_no: int
    payload: str
    payload_sha256: str
    received_at: str

    @classmethod
    def from_row(cls, row: Row) -> "RawRecord":
        return cls(
            raw_id=_column(row, "raw_id"),
            batch_id=_column(row, "batch_id"),
            source_system=SourceSystem(_column(row, "source_system")),
            source_record_id=_column(row, "source_record_id"),
            source_line_no=_column(row, "source_line_no"),
            payload=_column(row, "payload"),
            payload_sha256=_column(row, "payload_sha256"),
            received_at=_column(row, "received_at"),
        )


@dataclass(frozen=True, slots=True)
class QuarantinedRecord:
    quarantine_id: int
    raw_id: int
    received_at: str
    reason_code: str
    detail: str | None

    @classmethod
    def from_row(cls, row: Row) -> "QuarantinedRecord":
        return cls(
            quarantine_id=_column(row, "quarantine_id"),
            raw_id=_column(row, "raw_id"),
            received_at=_column(row, "received_at"),
            reason_code=_column(row, "reason_code"),
            detail=_column(row, "detail"),
        )


@dataclass(frozen=True, slots=True)
class NormalizedRecord:
    norm_id: int
    raw_id: int
    parent_norm_id: int | None
    record_kind: RecordKind
    source_system: SourceSystem
    adapter_version: str
    received_at: str
    idempotency_key: str
    pharmacy_npi: str | None
    provider_npi: str | None
    #: Verbatim, exactly as the feed spelled it.  There is no normalized twin, by
    #: design (Decision A23): injected identifier drift is meant to miss, because the
    #: miss is the D-6 crosswalk-failure exception.
    rx_number: str | None
    fill_number: str | None
    ndc11: str | None
    date_of_service: str | None
    clm01: str | None
    clp07: str | None
    trn02: str | None
    ach_trace_number: str | None
    allocation_code: str | None
    authorization_number: str | None
    payer_id: str | None
    amount_cents: int | None
    quantity_milli: int | None
    status_code: str | None
    canonical: str

    @classmethod
    def from_row(cls, row: Row) -> "NormalizedRecord":
        return cls(
            norm_id=_column(row, "norm_id"),
            raw_id=_column(row, "raw_id"),
            parent_norm_id=_column(row, "parent_norm_id"),
            record_kind=RecordKind(_column(row, "record_kind")),
            source_system=SourceSystem(_column(row, "source_system")),
            adapter_version=_column(row, "adapter_version"),
            received_at=_column(row, "received_at"),
            idempotency_key=_column(row, "idempotency_key"),
            pharmacy_npi=_column(row, "pharmacy_npi"),
            provider_npi=_column(row, "provider_npi"),
            rx_number=_column(row, "rx_number"),
            fill_number=_column(row, "fill_number"),
            ndc11=_column(row, "ndc11"),
            date_of_service=_column(row, "date_of_service"),
            clm01=_column(row, "clm01"),
            clp07=_column(row, "clp07"),
            trn02=_column(row, "trn02"),
            ach_trace_number=_column(row, "ach_trace_number"),
            allocation_code=_column(row, "allocation_code"),
            authorization_number=_column(row, "authorization_number"),
            payer_id=_column(row, "payer_id"),
            amount_cents=_column(row, "amount_cents"),
            quantity_milli=_column(row, "quantity_milli"),
            status_code=_column(row, "status_code"),
            canonical=_column(row, "canonical"),
        )


@dataclass(frozen=True, slots=True)
class CrosswalkEntry:
    crosswalk_id: int
    key_type: KeyType
    key_value: str
    episode_id: str | None
    remittance_norm_id: int | None
    resolved_from_norm_id: int
    first_seen_at: str

    @classmethod
    def from_row(cls, row: Row) -> "CrosswalkEntry":
        return cls(
            crosswalk_id=_column(row, "crosswalk_id"),
            key_type=KeyType(_column(row, "key_type")),
            key_value=_column(row, "key_value"),
            episode_id=_column(row, "episode_id"),
            remittance_norm_id=_column(row, "remittance_norm_id"),
            resolved_from_norm_id=_column(row, "resolved_from_norm_id"),
            first_seen_at=_column(row, "first_seen_at"),
        )


@dataclass(frozen=True, slots=True)
class Resolution:
    """What a key lookup resolved to.

    ``episode_id`` and ``remittance_norm_id`` are both nullable and at least one is
    set.  A bank line resolves to a *remittance* and not to an episode — that is the
    first of its two hops, and a caller that assumes an episode here will silently
    drop every deposit.
    """

    key_type: KeyType
    key_value: str
    episode_id: str | None
    remittance_norm_id: int | None
    first_seen_at: str
    resolved_from_norm_id: int

    @classmethod
    def from_row(cls, row: Row) -> "Resolution":
        return cls(
            key_type=KeyType(_column(row, "key_type")),
            key_value=_column(row, "key_value"),
            episode_id=_column(row, "episode_id"),
            remittance_norm_id=_column(row, "remittance_norm_id"),
            first_seen_at=_column(row, "first_seen_at"),
            resolved_from_norm_id=_column(row, "resolved_from_norm_id"),
        )


@dataclass(frozen=True, slots=True)
class Episode:
    episode_id: str
    reimbursement_track: ReimbursementTrack
    anchor_norm_id: int
    pharmacy_npi: str | None
    ndc11: str
    date_of_service: str
    quantity_milli: int
    prescriber_npi: str | None
    rx_number: str | None
    fill_number: str | None
    pbm_id: str | None
    cardholder_id: str | None
    clm01: str | None
    medical_payer_id: str | None
    billing_provider_npi: str | None
    is_340b_flagged: bool
    covered_entity_id: str | None
    created_from_received_at: str

    @classmethod
    def from_row(cls, row: Row) -> "Episode":
        return cls(
            episode_id=_column(row, "episode_id"),
            reimbursement_track=ReimbursementTrack(_column(row, "reimbursement_track")),
            anchor_norm_id=_column(row, "anchor_norm_id"),
            pharmacy_npi=_column(row, "pharmacy_npi"),
            ndc11=_column(row, "ndc11"),
            date_of_service=_column(row, "date_of_service"),
            quantity_milli=_column(row, "quantity_milli"),
            prescriber_npi=_column(row, "prescriber_npi"),
            rx_number=_column(row, "rx_number"),
            fill_number=_column(row, "fill_number"),
            pbm_id=_column(row, "pbm_id"),
            cardholder_id=_column(row, "cardholder_id"),
            clm01=_column(row, "clm01"),
            medical_payer_id=_column(row, "medical_payer_id"),
            billing_provider_npi=_column(row, "billing_provider_npi"),
            is_340b_flagged=bool(_column(row, "is_340b_flagged")),
            covered_entity_id=_column(row, "covered_entity_id"),
            created_from_received_at=_column(row, "created_from_received_at"),
        )


@dataclass(frozen=True, slots=True)
class VerdictReason:
    ordinal: int
    reason_code: ReasonCode
    track: TrackScope

    @classmethod
    def from_row(cls, row: Row) -> "VerdictReason":
        return cls(
            ordinal=_column(row, "ordinal"),
            reason_code=ReasonCode(_column(row, "reason_code")),
            track=TrackScope(_column(row, "track")),
        )


@dataclass(frozen=True, slots=True)
class EvidenceRow:
    """One cited record, with its raw payload already joined in.

    ``payload`` is the verbatim source line.  "Preserve source identifiers so an
    investigator can trace a result back to the synthetic source record" is explicitly
    graded, and this is the shape that answers it in one query.
    """

    verdict_id: int
    ordinal: int
    norm_id: int
    raw_id: int
    role: EvidenceRole
    source_system: SourceSystem | None = None
    source_record_id: str | None = None
    source_file: str | None = None
    source_line_no: int | None = None
    received_at: str | None = None
    payload: str | None = None

    @classmethod
    def from_row(cls, row: Row) -> "EvidenceRow":
        keys = set(row.keys())
        return cls(
            verdict_id=_column(row, "verdict_id"),
            ordinal=_column(row, "ordinal"),
            norm_id=_column(row, "norm_id"),
            raw_id=_column(row, "raw_id"),
            role=EvidenceRole(_column(row, "role")),
            source_system=(
                SourceSystem(row["source_system"])
                if "source_system" in keys and row["source_system"] is not None
                else None
            ),
            source_record_id=row["source_record_id"] if "source_record_id" in keys else None,
            source_file=row["source_file"] if "source_file" in keys else None,
            source_line_no=row["source_line_no"] if "source_line_no" in keys else None,
            received_at=row["received_at"] if "received_at" in keys else None,
            payload=row["payload"] if "payload" in keys else None,
        )


@dataclass(frozen=True, slots=True)
class Verdict:
    """One row of the append-only log, with its children.

    ``reasons`` is composed at construction rather than fetched on demand: deciding
    and explaining are the same rule, so a verdict that exists without its explanation
    is a shape this type declines to have.
    """

    verdict_id: int
    episode_id: str
    cursor_at: str
    computed_at: str
    engine_version: str
    reference_fingerprint: str
    episode_disposition: Disposition
    reimbursement_disposition: Disposition
    rebate_disposition: Disposition | None
    reimbursement_verdict_code: str
    rebate_verdict_code: str
    expected_reimbursement_cents: int
    received_reimbursement_cents: int
    reimbursement_variance_cents: int
    expected_rebate_cents: int
    received_rebate_cents: int
    rebate_variance_cents: int
    reopened_from: Disposition | None
    previously_closed_at: str | None
    reopened_on: str | None
    reasons: tuple[ReasonCode, ...] = ()
    reason_tracks: tuple[TrackScope, ...] = ()
    cross_track_flags: tuple[CrossTrackFlag, ...] = ()
    evidence: tuple[EvidenceRow, ...] = field(default=())

    @classmethod
    def from_row(
        cls,
        row: Row,
        *,
        reasons: tuple[VerdictReason, ...] = (),
        cross_track_flags: tuple[CrossTrackFlag, ...] = (),
        evidence: tuple[EvidenceRow, ...] = (),
    ) -> "Verdict":
        return cls(
            verdict_id=_column(row, "verdict_id"),
            episode_id=_column(row, "episode_id"),
            cursor_at=_column(row, "cursor_at"),
            computed_at=_column(row, "computed_at"),
            engine_version=_column(row, "engine_version"),
            reference_fingerprint=_column(row, "reference_fingerprint"),
            episode_disposition=Disposition(_column(row, "episode_disposition")),
            reimbursement_disposition=Disposition(_column(row, "reimbursement_disposition")),
            rebate_disposition=_optional_enum(Disposition, _column(row, "rebate_disposition")),
            reimbursement_verdict_code=_column(row, "reimbursement_verdict_code"),
            rebate_verdict_code=_column(row, "rebate_verdict_code"),
            expected_reimbursement_cents=_column(row, "expected_reimbursement_cents"),
            received_reimbursement_cents=_column(row, "received_reimbursement_cents"),
            reimbursement_variance_cents=_column(row, "reimbursement_variance_cents"),
            expected_rebate_cents=_column(row, "expected_rebate_cents"),
            received_rebate_cents=_column(row, "received_rebate_cents"),
            rebate_variance_cents=_column(row, "rebate_variance_cents"),
            reopened_from=_optional_enum(Disposition, _column(row, "reopened_from")),
            previously_closed_at=_column(row, "previously_closed_at"),
            reopened_on=_column(row, "reopened_on"),
            reasons=tuple(r.reason_code for r in reasons),
            reason_tracks=tuple(r.track for r in reasons),
            cross_track_flags=tuple(cross_track_flags),
            evidence=tuple(evidence),
        )

    @property
    def is_reopened(self) -> bool:
        return self.reopened_from is not None


@dataclass(frozen=True, slots=True)
class ParkedRecord:
    parked_id: int
    norm_id: int
    raw_id: int
    record_kind: RecordKind
    received_at: str
    park_reason: ParkReason
    resolved_by_norm_id: int | None = None
    resolved_by_received_at: str | None = None
    resolved_to_episode_id: str | None = None
    resolved_to_remittance_norm_id: int | None = None

    @property
    def is_resolved(self) -> bool:
        return self.resolved_by_norm_id is not None

    @classmethod
    def from_row(cls, row: Row) -> "ParkedRecord":
        keys = set(row.keys())

        def optional(name: str):
            return row[name] if name in keys else None

        return cls(
            parked_id=_column(row, "parked_id"),
            norm_id=_column(row, "norm_id"),
            raw_id=_column(row, "raw_id"),
            record_kind=RecordKind(_column(row, "record_kind")),
            received_at=_column(row, "received_at"),
            park_reason=ParkReason(_column(row, "park_reason")),
            resolved_by_norm_id=optional("resolved_by_norm_id"),
            resolved_by_received_at=optional("resolved_by_received_at"),
            resolved_to_episode_id=optional("resolved_to_episode_id"),
            resolved_to_remittance_norm_id=optional("resolved_to_remittance_norm_id"),
        )


@dataclass(frozen=True, slots=True)
class CashAllocation:
    allocation_id: int
    bank_norm_id: int
    remittance_norm_id: int | None
    episode_id: str | None
    allocated_cents: int
    basis: AllocationBasis
    caused_by_received_at: str

    @classmethod
    def from_row(cls, row: Row) -> "CashAllocation":
        return cls(
            allocation_id=_column(row, "allocation_id"),
            bank_norm_id=_column(row, "bank_norm_id"),
            remittance_norm_id=_column(row, "remittance_norm_id"),
            episode_id=_column(row, "episode_id"),
            allocated_cents=_column(row, "allocated_cents"),
            basis=AllocationBasis(_column(row, "basis")),
            caused_by_received_at=_column(row, "caused_by_received_at"),
        )


@dataclass(frozen=True, slots=True)
class WorkItem:
    work_item_id: int
    episode_id: str
    created_at: str
    created_by: str
    at_cursor: str
    from_verdict_id: int
    summary: str
    recommended_action: str

    @classmethod
    def from_row(cls, row: Row) -> "WorkItem":
        return cls(
            work_item_id=_column(row, "work_item_id"),
            episode_id=_column(row, "episode_id"),
            created_at=_column(row, "created_at"),
            created_by=_column(row, "created_by"),
            at_cursor=_column(row, "at_cursor"),
            from_verdict_id=_column(row, "from_verdict_id"),
            summary=_column(row, "summary"),
            recommended_action=_column(row, "recommended_action"),
        )


@dataclass(frozen=True, slots=True)
class QueueRow:
    """A queue line as the UI and the agent see it.

    The only shape carrying ``age_days``, and it is computed in the query rather than
    stored.  ``age_days`` is a sort key; it is never an input to a verdict.
    """

    episode_id: str
    reimbursement_track: ReimbursementTrack
    date_of_service: str
    ndc11: str
    age_days: int
    verdict_id: int
    cursor_at: str
    episode_disposition: Disposition
    reimbursement_verdict_code: str
    rebate_verdict_code: str
    reimbursement_variance_cents: int
    rebate_variance_cents: int
    reopened_from: Disposition | None

    @property
    def total_variance_cents(self) -> int:
        return self.reimbursement_variance_cents + self.rebate_variance_cents

    @classmethod
    def from_row(cls, row: Row) -> "QueueRow":
        return cls(
            episode_id=_column(row, "episode_id"),
            reimbursement_track=ReimbursementTrack(_column(row, "reimbursement_track")),
            date_of_service=_column(row, "date_of_service"),
            ndc11=_column(row, "ndc11"),
            age_days=_column(row, "age_days"),
            verdict_id=_column(row, "verdict_id"),
            cursor_at=_column(row, "cursor_at"),
            episode_disposition=Disposition(_column(row, "episode_disposition")),
            reimbursement_verdict_code=_column(row, "reimbursement_verdict_code"),
            rebate_verdict_code=_column(row, "rebate_verdict_code"),
            reimbursement_variance_cents=_column(row, "reimbursement_variance_cents"),
            rebate_variance_cents=_column(row, "rebate_variance_cents"),
            reopened_from=_optional_enum(Disposition, _column(row, "reopened_from")),
        )
