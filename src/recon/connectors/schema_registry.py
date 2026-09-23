"""What a record has to look like before anyone is allowed to say what it means.

Requirement B1.  Each source declares a versioned field contract, an inbound record is
checked against the registered version **before** adaptation, and a record that fails is
quarantined with :attr:`~recon.domain.enums.QuarantineReason.SCHEMA_VERSION_MISMATCH` — an
enum member that has sat unused since the deterministic layer was built, and that this module
finally wires up.

**Why the check runs before adaptation rather than inside it.**  Adaptation is where meaning
is assigned.  ``_adapt_tpa`` reads ``payload["fill_date"]`` and calls the result a fill date;
``_adapt_pbm_adjudication`` reads ``total_amount_paid`` and turns it into cents.  Hand those
functions a record whose shape has drifted and the common case is not a crash — it is a
success.  They produce a confidently wrong canonical record, and every verdict downstream
then treats it as fact.  Checking the shape first turns that silent wrong answer into a
quarantine row that names the field and says what was expected.  ``ingest/adapters.py`` keeps
the three failures only it can see — an NCPDP transaction code that is neither ``B1`` nor
``B2``, a TPA ``event_type`` it has no semantics for, a bank row carrying no ACH trace.  Those
are questions about meaning, and meaning is only worth asking about once the shape holds.

**An unregistered source validates vacuously, and that is load-bearing.**  A source with no
registered contract passes through untouched — :meth:`SchemaRegistry.get` returns ``None`` and
:meth:`SchemaRegistry.validate` returns an empty list.  This is not a convenience default, it
is what lets the connector layer be additive.  The six generated feeds have no contract, so
they load byte-for-byte as they always have and the existing suite passes unedited.  A
registry that quarantined anything it had not been told about would fail every one of those
feeds on the day it was first wired in, which is the opposite of the ordering this whole
package was built to allow: onboard one vendor at a time, and leave the rest alone.

**A version is asked for by name, and a name we do not hold is an error.**  A contract is
``(source_id, version)``.  Registering two versions of one source is normal — a vendor
re-cuts its export and the old files still have to replay.  Asking for a version that is not
registered raises :class:`UnknownSchemaVersionError` rather than falling back to the newest
one, because a silent fallback is exactly how a connector validates a v2 file against the v1
contract, finds nothing wrong, and reports success.

**Types here describe the wire, not Python.**  Money is decimal text and never a float, dates
are ``CCYYMMDD`` strings, timestamps are ``%Y-%m-%dT%H:%M:%SZ``, and an NCPDP quantity is a
milli-unit string whose leading zeros are part of it.  On a vendor CSV every cell is a string
anyway, so a "type" here is the lexical shape the text has to have: ``"2026-03-02"`` in a
``date_wire`` column is a type change even though both sides are ``str``, and it is precisely
the kind of change that adapts cleanly into the wrong answer.

**No SQL, and no database.**  ``docs/connectivity_layer_requirements.md`` §4.12 says the
connectors package must not write to any table other than its own, and §4.6 lists a
``schema_contract`` table as a later concern.  The registry is in-memory Python data.  Nothing
in this module opens a connection, executes a statement or reads a row.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Callable

from recon.domain.enums import QuarantineReason

__all__ = [
    "FieldSpec",
    "KINDS",
    "QUARANTINE_REASON",
    "REGISTRY",
    "SchemaContract",
    "SchemaMismatch",
    "SchemaRegistry",
    "UnknownSchemaVersionError",
    "check",
    "format_detail",
]


#: The one reason code a contract violation ever produces, named here so a caller does not
#: have to remember which of the five members applies.
#:
#: ``MISSING_REQUIRED_FIELD`` and ``BAD_TYPE`` also exist and are deliberately **not** used by
#: this module, even for a missing field or a wrong type.  They describe a different observer.
#: An adapter raising ``MISSING_REQUIRED_FIELD`` trusted the record's shape and found a field
#: it needed absent from a record it otherwise believed in.  This module found that the record
#: is not the shape the contract declares at all, which is a statement about the feed rather
#: than about one field — and it is the statement someone working the quarantine queue needs,
#: because the fix is a conversation with the vendor rather than a patch to one record.
QUARANTINE_REASON = QuarantineReason.SCHEMA_VERSION_MISMATCH

#: How many violations a formatted ``detail`` lists before it summarises the rest as a count.
#:
#: Three, because ``_process_raw_record`` stores ``detail`` truncated to 400 characters and a
#: fully spelled-out violation runs to about ninety.  Listing more would push the ``(+N more)``
#: past the cut, and then the reader cannot tell a file where one column moved from a file
#: whose layout changed completely — which is the first thing they need to know, and the thing
#: that decides whether this is a conversation with the vendor or a one-line mapping fix.
#: Above three the individual field names stop being the point anyway; the count is.
_MAX_DETAIL_VIOLATIONS = 3

#: How much of an offending value is quoted back.  Enough to recognise it, short enough that
#: a long free-text cell cannot push the field names out of the truncated detail.
_MAX_VALUE_REPR = 48


class SchemaMismatch(ValueError):
    """A record does not match the contract registered for its source.

    Carries the pieces separately as well as formatted, so a caller that wants to quarantine
    uses ``str(exc)`` for the detail and a caller that wants to report uses
    :attr:`violations`.
    """

    def __init__(self, source_id: str, version: str, violations: Sequence[str]) -> None:
        super().__init__(format_detail(source_id, version, violations))
        self.source_id = source_id
        self.version = version
        self.violations = tuple(violations)


class UnknownSchemaVersionError(LookupError):
    """A registered source was asked to validate against a version it does not have.

    Named and raised rather than resolved to the newest registered version.  A fallback here
    would validate a v2 file against the v1 contract and report that everything is fine, which
    is the failure this whole module exists to make impossible.
    """


# ═══ what a field may be on the wire ════════════════════════════════════════
#
# Every kind below is here because a real field in this repository has that shape.  The
# comment on each names one, so a reader can check the type system against the feeds rather
# than against an idea of what a type system should contain.
#
# Deliberately absent: an ``enum`` kind that pins a field to a closed set of values.  That is
# a question about meaning -- ``transaction_code`` being ``B1`` or ``B2``, ``event_type``
# being one of four -- and ``ingest/adapters.py`` already refuses an unknown one with
# ``UNKNOWN_EVENT_SEMANTICS``.  Answering it here would quarantine the same record under the
# wrong reason code and send whoever reads the queue to the wrong team.


@dataclass(frozen=True, slots=True)
class _Kind:
    description: str
    matches: Callable[[Any], bool]


def _is_string(value: Any) -> bool:
    return isinstance(value, str)


def _is_integer(value: Any) -> bool:
    # ``bool`` is an ``int`` subclass and is never a wire integer, so it is refused for the
    # same reason ``money.to_cents`` refuses it.
    return isinstance(value, int) and not isinstance(value, bool)


def _is_integer_text(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    digits = value[1:] if value[:1] in {"+", "-"} else value
    return bool(digits) and digits.isdigit()


def _is_decimal_text(value: Any) -> bool:
    # ``Decimal`` is accepted alongside ``str`` because ``adapters.parse_jsonl_line`` parses
    # JSON numbers with ``parse_float=Decimal``, so money off a JSONL feed genuinely arrives
    # as a ``Decimal`` while money off a CSV arrives as text.  Both are exact; a ``float`` is
    # not, and is refused here for the same reason ``money.to_cents`` refuses it.
    if isinstance(value, Decimal):
        return value.is_finite()
    if not isinstance(value, str):
        return False
    try:
        return Decimal(value.strip()).is_finite()
    except (InvalidOperation, ValueError):
        return False


def _is_date_wire(value: Any) -> bool:
    # The same test ``crosswalk.keys.wire_date_to_iso`` applies, on purpose: a value this
    # accepts and that function then rejects would be a contract that passes a record its own
    # adapter cannot read.
    return isinstance(value, str) and len(value) == 8 and value.isdigit()


def _is_date_iso(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != 10:
        return False
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return False
    return True


def _is_timestamp(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return False
    return True


def _is_array(value: Any) -> bool:
    return isinstance(value, list)


def _is_object(value: Any) -> bool:
    return isinstance(value, Mapping)


_KINDS: dict[str, _Kind] = {
    # ``rx_number``, ``covered_entity_id`` -- carried verbatim, never normalised.
    "string": _Kind("text", _is_string),
    # ``units`` on an 837 service line -- a JSON number that is genuinely a count.
    "integer": _Kind("a whole number", _is_integer),
    # NCPDP 442-E7 ``quantity_dispensed`` -- ``"030000"`` is three implied decimals and the
    # leading zeros are part of the value, so it stays text and is never parsed to ``int``.
    "integer_text": _Kind('a whole number as text, e.g. "030000" or "-30"', _is_integer_text),
    # ``rebate_amount``, ``total_rebate_amount``, the bank CSV's ``amount``.
    "decimal_text": _Kind('decimal money as text, e.g. "2840.00"', _is_decimal_text),
    # ``fill_date``, ``date_of_service``, ``payment_effective_date`` -- CCYYMMDD on every feed.
    "date_wire": _Kind('a CCYYMMDD wire date, e.g. "20260302"', _is_date_wire),
    # The bank CSV's ``posting_date``, which already arrives in the stored form.
    "date_iso": _Kind('a YYYY-MM-DD date, e.g. "2026-03-02"', _is_date_iso),
    # ``received_at`` -- the only temporal field the pipeline honours.
    "timestamp": _Kind('a UTC timestamp, e.g. "2026-03-02T14:05:00Z"', _is_timestamp),
    # ``reject_codes``, ``adjustments``, ``service_lines``, ``dispenses``.
    "array": _Kind("a list", _is_array),
    # ``bpr``, ``trn``, ``loop_2410``, ``service_line``.
    "object": _Kind("a nested object", _is_object),
}

#: The kind names a contract may use.  Public so a vendor-contract module and a B2 test can
#: both check a spelling without importing the private table.
KINDS: frozenset[str] = frozenset(_KINDS)


# ═══ the contract ═══════════════════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class FieldSpec:
    """One field the contract expects, and the shape its value has to have.

    ``required`` means the *producer* structurally cannot leave it out, established by reading
    the writer rather than by listing what a consumer would like to have.  Marking a field
    required that the vendor legitimately leaves empty turns a quiet day into a quarantine
    storm, and a quarantine storm is how a team learns to ignore the queue.
    """

    name: str
    kind: str
    required: bool = True

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("a field spec needs a name")
        if self.kind not in _KINDS:
            raise ValueError(
                f"unknown field kind {self.kind!r} on field {self.name!r}; "
                f"known kinds: {', '.join(sorted(_KINDS))}"
            )

    @property
    def expectation(self) -> str:
        """``date_wire (a CCYYMMDD wire date, e.g. "20260302")`` — what a violation quotes."""
        return f"{self.kind} ({_KINDS[self.kind].description})"


@dataclass(frozen=True, slots=True)
class SchemaContract:
    """One source's field list at one version.

    The field order is the file's column order.  Nothing in :meth:`validate` depends on it —
    a record is a mapping — but requirement B2's acceptance test compares the registered
    contract against the documented layout, and an order that already matches makes that test
    a list comparison rather than a set comparison with a sorting argument attached.
    """

    source_id: str
    version: str
    fields: tuple[FieldSpec, ...]

    def __post_init__(self) -> None:
        if not self.source_id or not self.version:
            raise ValueError("a contract needs both a source_id and a version")
        seen: set[str] = set()
        for spec in self.fields:
            if spec.name in seen:
                raise ValueError(
                    f"{self.source_id}@{self.version} declares {spec.name!r} twice; "
                    "the second declaration would silently win and the first would never "
                    "be checked"
                )
            seen.add(spec.name)

    @property
    def field_names(self) -> tuple[str, ...]:
        """The declared names in order, for B2's "registered matches documented" test."""
        return tuple(spec.name for spec in self.fields)

    def validate(self, record: Mapping[str, Any]) -> list[str]:
        """Every way this record disagrees with the contract.  Empty means it conforms.

        A field the contract does not mention is **not** a violation.  A vendor adding a
        column is additive and breaks nothing for a reader that addresses fields by name,
        which is what the whole pipeline does — the raw payload is stored verbatim, so the new
        column is preserved either way and can be mapped later.  A field that *vanished* or
        changed shape is the one that breaks meaning, and that is what is reported.
        """
        violations: list[str] = []
        for spec in self.fields:
            value = record.get(spec.name)
            if _is_absent(value):
                if spec.required:
                    violations.append(
                        f"{spec.name}: required field is absent, expected {spec.expectation}"
                    )
                continue
            if not _KINDS[spec.kind].matches(value):
                violations.append(
                    f"{spec.name}: expected {spec.expectation}, got {_describe(value)}"
                )
        return violations


def _is_absent(value: Any) -> bool:
    """``None`` and the empty string are both absence, and they have to be.

    A CSV has no null: an unpopulated ``reversal_reason`` is an empty cell.
    ``adapters.parse_bank_row`` already collapses the two for the bank feed, and treating them
    differently here would mean a vendor CSV and the JSONL feed beside it disagreed about
    whether a field was missing.
    """
    return value is None or (isinstance(value, str) and value == "")


def _describe(value: Any) -> str:
    """``int 284000`` — the type first, because the type is what disagreed."""
    text = repr(value)
    if len(text) > _MAX_VALUE_REPR:
        text = text[: _MAX_VALUE_REPR - 3] + "..."
    described = f"{type(value).__name__} {text}"
    if isinstance(value, float):
        # Worth calling out by name.  A float in a money column almost always means the
        # payload was parsed without ``parse_float=Decimal``, which is a bug in the reader
        # rather than a defect in the feed, and the two get fixed by different people.
        described += " (a float here means the payload was parsed without parse_float=Decimal)"
    return described


def format_detail(source_id: str, version: str, violations: Sequence[str]) -> str:
    """The quarantine ``detail`` string: which source, which contract, which fields.

    Written to be read in a queue rather than in a debugger.  Requirement B1 is only worth
    anything if the person who opens the quarantined row can go back to the vendor with the
    field name and the expected shape already in hand.
    """
    shown = list(violations[:_MAX_DETAIL_VIOLATIONS])
    remainder = len(violations) - len(shown)
    body = "; ".join(shown)
    if remainder > 0:
        body += f"; (+{remainder} more)"
    return (
        f"schema mismatch for source {source_id!r} against contract version "
        f"{version!r}: {body}"
    )


# ═══ the registry ═══════════════════════════════════════════════════════════


class SchemaRegistry:
    """Contracts held by ``(source_id, version)``, plus which version is current per source.

    In-memory and mutable by construction: a vendor module registers its contract at import,
    and a test builds its own instance rather than mutating the shared one.
    """

    __slots__ = ("_contracts", "_current")

    def __init__(self) -> None:
        self._contracts: dict[tuple[str, str], SchemaContract] = {}
        self._current: dict[str, str] = {}

    def register(self, contract: SchemaContract, *, current: bool = True) -> None:
        """Add a contract.  The newest registration becomes the source's current version.

        ``current=False`` registers an older version without promoting it, which is what
        replaying an archived file needs: the contract has to be reachable by name without
        becoming the one an unversioned record is checked against.

        Re-registering the same contract is a no-op, so a module imported twice is harmless.
        Re-registering a *different* contract under the same ``(source_id, version)`` raises,
        because the second one would silently replace the first and every record already
        validated against the first would have been validated against a contract that no
        longer exists.
        """
        key = (contract.source_id, contract.version)
        existing = self._contracts.get(key)
        if existing is not None and existing != contract:
            raise ValueError(
                f"a different contract is already registered for {contract.source_id!r} at "
                f"version {contract.version!r}; bump the version rather than redefining one"
            )
        self._contracts[key] = contract
        if current or contract.source_id not in self._current:
            self._current[contract.source_id] = contract.version

    def source_ids(self) -> tuple[str, ...]:
        """Every source that has a registered contract, sorted.

        Public because requirement B2's acceptance — *a test asserts the registered schema
        matches the documented one* — has to enumerate what is registered before it can check
        anything, and a test that reached into ``_contracts`` to do that would be pinned to a
        private structure it does not own.  A test forced to break encapsulation is a test
        that gets deleted the first time the internals move.
        """
        return tuple(sorted({source_id for (source_id, _version) in self._contracts}))

    def versions(self, source_id: str) -> tuple[str, ...]:
        """Every registered version of one source, sorted.  Empty for an unknown source."""
        return tuple(
            sorted(version for (known, version) in self._contracts if known == source_id)
        )

    def current_version(self, source_id: str) -> str | None:
        """The version an unversioned record is checked against, or ``None`` if unregistered."""
        return self._current.get(source_id)

    def get(self, source_id: str, version: str | None = None) -> SchemaContract | None:
        """The contract to check against, or ``None`` when the source has none.

        ``None`` for an unregistered source is the pass-through case and it is absolute — a
        source we have not onboarded is not validated, whether or not a version was named.
        There is no contract to be wrong about, so there is nothing to refuse.  This is what
        keeps the six generated feeds loading exactly as they do today.

        Raises:
            UnknownSchemaVersionError: when the source *is* registered and the named version
                is not.  Never a fallback to the current one.
        """
        if source_id not in self._current:
            return None
        resolved = self._current[source_id] if version is None else version
        contract = self._contracts.get((source_id, resolved))
        if contract is None:
            known = ", ".join(repr(item) for item in self.versions(source_id))
            raise UnknownSchemaVersionError(
                f"schema mismatch for source {source_id!r}: version {resolved!r} is not "
                f"registered; registered versions are {known}"
            )
        return contract

    def validate(
        self, source_id: str, record: Mapping[str, Any], *, version: str | None = None
    ) -> list[str]:
        """Every way this record disagrees with its contract.  Empty means it conforms.

        Empty is also what an unregistered source returns.  The two are indistinguishable on
        purpose: "checked and clean" and "nothing to check" are the same instruction to the
        caller, which is to carry on and adapt.
        """
        contract = self.get(source_id, version)
        if contract is None:
            return []
        return contract.validate(record)

    def require(
        self, source_id: str, record: Mapping[str, Any], *, version: str | None = None
    ) -> None:
        """:meth:`validate`, raising :class:`SchemaMismatch` instead of returning a list.

        For a connector that wants the record to stop travelling at the point of failure.  The
        ingest pipeline does not use this — it quarantines rather than raises — which is why
        :func:`check` exists beside it.
        """
        contract = self.get(source_id, version)
        if contract is None:
            return
        violations = contract.validate(record)
        if violations:
            raise SchemaMismatch(source_id, contract.version, violations)

    def __repr__(self) -> str:  # pragma: no cover - diagnostic only
        return f"SchemaRegistry({len(self._contracts)} contracts, {len(self._current)} sources)"


def check(
    source_id: str,
    record: Mapping[str, Any],
    *,
    version: str | None = None,
    registry: SchemaRegistry | None = None,
) -> str | None:
    """``None`` when the record may be adapted; a quarantine ``detail`` when it may not.

    The pipeline-facing entry point, and one call rather than three so that wiring it into
    ``_process_raw_record`` adds a branch instead of a block.

    It swallows :class:`UnknownSchemaVersionError` and returns it as a detail string, which is
    the one place that error is not a programming mistake.  When a record declares its own
    contract version on the wire — Craneware writes ``schema_version`` into every row — a
    version we have never registered is the vendor having re-cut the export without telling
    anyone.  That is a record to quarantine, not a process to crash, and it is the purest
    ``SCHEMA_VERSION_MISMATCH`` there is.
    """
    active = REGISTRY if registry is None else registry
    try:
        contract = active.get(source_id, version)
    except UnknownSchemaVersionError as exc:
        return str(exc)
    if contract is None:
        return None
    violations = contract.validate(record)
    if not violations:
        return None
    return format_detail(source_id, contract.version, violations)


# ═══ registered contracts ═══════════════════════════════════════════════════
#
# Declared here as literal data rather than derived from the module that writes the files.
#
# That duplication is the point.  A contract computed from its own producer can never catch
# the producer drifting -- it agrees with whatever the writer does, including the day the
# writer starts doing the wrong thing.  Two independent declarations can disagree, and
# requirement B2's acceptance test ("a test asserts the registered schema matches the
# documented one") is what makes the disagreement visible.  Importing ``COLUMNS`` from
# ``recon.mocks`` would turn that test into a tautology, and would also point the production
# connector package at a package of test doubles.
#
# Both version strings are INVENTED, in the sense ``docs/vendor_evidence/`` uses the word:
# neither vendor publishes a field dictionary.  They differ in one way that matters to this
# module.  Craneware writes its version into every row, so the wire names the contract and the
# caller passes ``version=record["schema_version"]``.  Verity writes only a record count, so
# the registry has to be told, and ``version=None`` resolves to the current registration.
#
# ═══ Every contract below describes a DETAIL row, never a TRAILER row ═══
#
# Both vendors end a file with a trailer that carries a count and leaves every other column
# empty.  Feed one of those rows to the contract for its file and it fails, correctly and
# uselessly -- it is not a dispense, so of course it has no NDC.
#
# The split belongs to whoever reads the file, not here, and the repository already draws it
# that way: ``craneware_export.declared_record_count`` and ``verity_export.declared_record_count``
# each scan for the trailer on their own and the detail rows are what is left.  Comparing that
# declared count against what was ingested is requirement F2's control total, a different
# check with a different reason code.  So a reader hands this module the detail rows, and a
# contract that quietly excused the trailer instead would also excuse a detail row whose
# ``record_type`` had been corrupted into something unrecognised -- which is a record going
# missing without a quarantine, the one outcome this package exists to prevent.

#: Craneware's Claims Report — the report that carries the money and the reversal flag, which
#: makes it the one where a shape change does the most damage before anyone notices.
#:
#: The required set is small and each member is provable from the writer rather than wished
#: for.  ``record_type`` and ``schema_version`` are written on every row by
#: ``craneware_export._render_csv``; ``dispense_status`` is always one of two literals;
#: ``ndc11`` and ``fill_date`` come from fields the TPA adapter reads without ``.get``, so the
#: feed underneath guarantees them.  Everything else is genuinely optional — a medical
#: dispense has no ``rx_number`` at all, and an unmatched claim has no rebate figures.
#:
#: ``declared_record_count`` is optional because the trailer row is a different shape from a
#: detail row: the trailer carries the count and nothing else, and comparing it against what
#: was ingested is requirement F2's control total rather than this contract's business.
CRANEWARE_CLAIMS_REPORT = SchemaContract(
    source_id="craneware_claims_report",
    version="craneware-sftp-export-1.0.0",
    fields=(
        FieldSpec("record_type", "string"),
        FieldSpec("schema_version", "string"),
        FieldSpec("covered_entity_id", "string", required=False),
        FieldSpec("rx_number", "string", required=False),
        FieldSpec("pharmacy_npi", "string", required=False),
        FieldSpec("provider_npi", "string", required=False),
        FieldSpec("ndc11", "string"),
        FieldSpec("fill_date", "date_wire"),
        FieldSpec("manufacturer", "string", required=False),
        FieldSpec("qualification_status", "string", required=False),
        FieldSpec("disqualification_reason", "string", required=False),
        FieldSpec("manufacturer_status", "string", required=False),
        FieldSpec("rejection_reason", "string", required=False),
        FieldSpec("dispense_status", "string"),
        # Named ``reversal_date`` but written from the reversal event's ``received_at``, so it
        # is a full timestamp.  Contracting it as a date would accept a truncated value and
        # lose the time the reversal actually arrived.
        FieldSpec("reversal_date", "timestamp", required=False),
        FieldSpec("reversal_reason", "string", required=False),
        # Already signed negative on the TPA feed and copied across without being re-signed,
        # which is why the text form has to accept a leading minus.
        FieldSpec("reversal_quantity", "integer_text", required=False),
        FieldSpec("rebate_submitted_date", "date_wire", required=False),
        FieldSpec("rebate_amount", "decimal_text", required=False),
        FieldSpec("rebate_payment_date", "date_wire", required=False),
        FieldSpec("rebate_allocation_code", "string", required=False),
        FieldSpec("declared_record_count", "integer_text", required=False),
    ),
)

#: Verity's accumulations export — the dispenses the TPA qualified, and therefore the dataset
#: that carries the 340B join keys.
#:
#: ``qualification_status`` and ``qualification_received_at`` are required *for this dataset
#: specifically*: the population is selected on the status being ``QUALIFIED``, so a row with
#: neither is a row that cannot have been in this file. That is a real contract and not a
#: generic one, which is the argument for contracts being per dataset rather than per vendor.
VERITY_ACCUMULATIONS = SchemaContract(
    source_id="verity_accumulations",
    version="verity-export-1.0.0",
    fields=(
        FieldSpec("record_type", "string"),
        # Empty on every detail row and the count on the trailer, same as Craneware's.
        FieldSpec("record_count", "integer_text", required=False),
        FieldSpec("accumulation_id", "string", required=False),
        FieldSpec("beacon_id", "string", required=False),
        FieldSpec("covered_entity_id", "string", required=False),
        FieldSpec("manufacturer", "string", required=False),
        FieldSpec("ndc_11", "string"),
        FieldSpec("fill_date", "date_wire"),
        FieldSpec("rx_number", "string", required=False),
        FieldSpec("pharmacy_npi", "string", required=False),
        FieldSpec("provider_npi", "string", required=False),
        FieldSpec("prescriber_npi", "string", required=False),
        FieldSpec("hin", "string", required=False),
        FieldSpec("wholesaler_invoice_number", "string", required=False),
        FieldSpec("qualification_status", "string"),
        FieldSpec("qualification_received_at", "timestamp"),
        FieldSpec("reversal_status", "string", required=False),
        FieldSpec("reversal_received_at", "timestamp", required=False),
        FieldSpec("reversal_reason", "string", required=False),
        FieldSpec("reversal_quantity", "integer_text", required=False),
    ),
)

#: Verity's invoices export — the rebate money, one row per paid dispense.
#:
#: Five fields are required here that are optional on accumulations, and for one reason: the
#: population is ``is_paid``, so a payment batch and a payment line both exist, and the TPA
#: adapter reads ``allocation_code``, ``rebate_amount``, ``total_rebate_amount`` and
#: ``payment_effective_date`` without ``.get``. A row missing any of them did not come from a
#: payment batch, and treating it as an invoice line would put money on the ledger that no
#: batch ever paid.
VERITY_INVOICES = SchemaContract(
    source_id="verity_invoices",
    version="verity-export-1.0.0",
    fields=(
        FieldSpec("record_type", "string"),
        FieldSpec("record_count", "integer_text", required=False),
        FieldSpec("invoice_number", "string", required=False),
        FieldSpec("beacon_id", "string", required=False),
        FieldSpec("accumulation_id", "string", required=False),
        FieldSpec("covered_entity_id", "string", required=False),
        FieldSpec("manufacturer", "string", required=False),
        FieldSpec("ndc_11", "string"),
        FieldSpec("fill_date", "date_wire"),
        FieldSpec("rx_number", "string", required=False),
        FieldSpec("pharmacy_npi", "string", required=False),
        FieldSpec("provider_npi", "string", required=False),
        FieldSpec("rebate_allocation_code", "string"),
        FieldSpec("batch_line_manufacturer_status", "string", required=False),
        FieldSpec("invoice_line_amount", "decimal_text"),
        FieldSpec("batch_total_rebate_amount", "decimal_text"),
        FieldSpec("payment_effective_date", "date_wire"),
        FieldSpec("batch_received_at", "timestamp"),
        FieldSpec("reversal_status", "string", required=False),
        FieldSpec("reversal_received_at", "timestamp", required=False),
        FieldSpec("reversal_reason", "string", required=False),
        FieldSpec("reversal_quantity", "integer_text", required=False),
    ),
)


#: The process-wide registry.
#:
#: **The six generated feeds are deliberately absent**, and so are the other seven vendor
#: layouts.  An unregistered source validates vacuously, so ``pbm_claim_events``,
#: ``pbm_remittance_835``, ``medical_837_submissions``, ``medical_835_remittance``,
#: ``tpa_340b_events`` and ``bank_transactions`` load exactly as they did before this module
#: existed.  That is the acceptance bar for this wave — the existing suite passes unedited —
#: and the way to hold it is to add nothing that those feeds have to satisfy.
#:
#: It is also the honest position.  A contract on the generated feeds would be a contract with
#: our own generator, agreed by us on both sides; a contract on a vendor export is a statement
#: about a file someone else produces and can change without asking. The second is the one B1
#: is about. The generated feeds get contracts when they get a vendor, not before.
REGISTRY = SchemaRegistry()
REGISTRY.register(CRANEWARE_CLAIMS_REPORT)
REGISTRY.register(VERITY_ACCUMULATIONS)
REGISTRY.register(VERITY_INVOICES)
