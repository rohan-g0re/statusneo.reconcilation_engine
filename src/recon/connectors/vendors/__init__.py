"""One secure-file connector, and the vendor difference confined to a field map.

Requirement D1, whose acceptance is a sentence about *diffs*: *"Verity and Craneware differ
by a config row and a mapping module, nothing else."*  ``DOC2-013`` is where it comes from —
*"Use Craneware as the second file-based pattern after Verity; both can share the same
secure-file connector framework."*

So every line of parsing lives here and neither vendor module contains any.  That is the
enforceable reading of D1: if :mod:`recon.connectors.vendors.verity` and
:mod:`recon.connectors.vendors.craneware` both knew how to find a trailer, then "the vendor
difference is confined to the field mapping" would be false the moment one of them fixed a
parsing bug the other kept.  Two readers drift; one reader cannot.  What the vendor modules
hold is data: a column map, a natural-key spelling, and the reversal rule their vendor
declares.

**What this module is not.**  It does not adapt a vendor row into the canonical record —
that is Group E, and ``ingest/adapters.py`` owns it.  It does not reconcile the declared
count against what was ingested — that is requirement F2, and :attr:`ParsedDocument.declared_record_count`
is deliberately handed over unexamined so F2 can own both the comparison and the reason code.
It does not touch a database: ``docs/connectivity_layer_requirements.md`` §4.12 forbids this
package from writing to any table but its own, and today it writes to none.

═══ Why the trailer is split off before anything else ══════════════════════════════

Both vendors end a file with a control row: ``record_type = TRAILER``, a declared count, and
every other column empty (``DOC2-010`` asks for *source-control totals*; ``CRANEWARE-004``
asks for *file-level controls*).  The registered schema contracts describe a DETAIL row and
say so in as many words — hand one a trailer and it fails, correctly and uselessly, because a
trailer is not a dispense and so of course it has no NDC.

The split therefore belongs to the reader, and the reader has to do it *first*.  A connector
that validated every row it found would quarantine one perfectly healthy control row per
file, per drop, forever — and a quarantine queue with a permanent known-good entry in it is a
queue people learn to skim.

The mirror mistake is worse and is why :attr:`ParsedDocument.unrecognised` exists.  A reader
that split on "``record_type`` is ``TRAILER``, otherwise detail" is fine; a reader that split
on "``record_type`` is ``DETAIL``, otherwise ignore" silently discards a detail row whose
``record_type`` was corrupted in transfer.  That is a record going missing with no error, no
quarantine and a control total that will not tally — which is F2 finding a discrepancy nobody
can explain.  So rows that are neither are kept, counted and handed back.

═══ Requirement B3: the representation is declared, never inferred ═════════════════

Doc 2 flags reversal representation on every vendor page, and it is the defect that destroys
a build quietly: guess wrong and you double-count or lose a rebate, and both look clean in
every report afterwards.

Three things make that a fact about this code rather than a promise in prose:

1. Each vendor module reads its representation **from the module that writes the files**,
   rather than re-typing the string.  Two copies of a string can disagree; one cannot.
2. :class:`ReversalRepresentation` is a closed vocabulary, and :class:`FlagReversal` refuses
   at import any representation that does not put the reversal on the dispense's own row.  If
   a writer ever changes its mind and starts emitting a correcting row, the connector written
   for a flag stops with a named error instead of quietly counting nothing.
3. :func:`reversal_effects` emits exactly one effect per reversed row and nothing else, and
   :func:`net_effects_by_claim` is what a caller uses to prove B3's *"not zero, not two"*.

This module deliberately does **not** name a reversal representation as a default.  There is
no sensible default; the whole requirement is that somebody had to decide and write it down.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from recon.connectors import schema_registry
from recon.connectors.transport import Document

__all__ = [
    "ReversalRepresentation",
    "UnsupportedReversalRepresentationError",
    "FlagReversal",
    "ReversalEffect",
    "SecureFileMapping",
    "ParsedDocument",
    "read",
    "canonical",
    "natural_key",
    "is_reversed",
    "reversal_effects",
    "net_effects_by_claim",
    "schema_violation",
    "mappings_by_source_id",
]


class ReversalRepresentation(StrEnum):
    """Every way a vendor can tell you a dispense was reversed.  A closed vocabulary.

    The first three of B3's own list — *"is a reversal a negative row, a replacement record,
    or a silent delete?"* — plus the two this repository actually produces.  Members exist
    here that no mapping uses, and that is the point: a representation nobody built for is
    still a representation a vendor can send, and it needs a name before it can be refused.

    Deliberately **not** in ``domain.enums``, and for that module's own stated rule: a
    vocabulary belongs there when it appears on the wire and is backed by a SQL ``CHECK``.
    This one is neither.  It is connector configuration, and the precedent for connector
    configuration keeping its enum next to the code that reads it is
    :class:`~recon.connectors.registry.PayloadFormat` and
    :class:`~recon.connectors.credentials.CredentialKind`.

    **The member values duplicate two strings that live in ``recon.mocks``, and the
    duplication is load-bearing in the opposite direction from usual.**  The vendor modules
    never re-type their own choice — they read it from the writer, so the two cannot drift.
    What they read it *into* is this vocabulary, which is what lets a drift be caught: a
    writer that changes its constant to something this enum does not hold raises at import,
    and a writer that changes it to ``NEGATIVE_QUANTITY_ROW`` is refused by name.  A free
    string would have absorbed either change without a sound.
    """

    #: Verity: a flag plus the reversal's own detail on the one row for the dispense.
    STATUS_FLAG_ROW = "STATUS_FLAG_ROW"
    #: Craneware: the same idea on a snapshot report, where the row is a restatement.
    STATUS_FLAG_ON_ORIGINAL_ROW = "STATUS_FLAG_ON_ORIGINAL_ROW"
    #: NCPDP's convention, and what ``recon.generators.tpa`` writes to the TPA feed: the
    #: original stays on the wire and a correcting row arrives beside it carrying a negative
    #: ``quantity_dispensed``.  No mapping in this package reads it; it is named so that a
    #: mapping which met it would have to say so.
    NEGATIVE_QUANTITY_ROW = "NEGATIVE_QUANTITY_ROW"
    #: The original is superseded by a whole new record for the same claim.
    REPLACEMENT_RECORD = "REPLACEMENT_RECORD"
    #: The row simply stops appearing in the next full file.  The worst of the five, because
    #: under full-file delivery it is indistinguishable from a truncated transfer.
    SILENT_DELETE = "SILENT_DELETE"


#: The representations where the reversal rides the dispense's own row, so reading that one
#: row is the whole story.  The other three need a second row, a prior file, or both, and a
#: reader built for a flag finds none of them — which is B3's *"not zero"* failure exactly.
_ON_THE_DISPENSE_ROW = frozenset(
    {
        ReversalRepresentation.STATUS_FLAG_ROW,
        ReversalRepresentation.STATUS_FLAG_ON_ORIGINAL_ROW,
    }
)


class UnsupportedReversalRepresentationError(ValueError):
    """A vendor declares a reversal representation this rule cannot honestly read.

    A ``ValueError`` rather than a ``RuntimeError``, matching
    :class:`~recon.connectors.registry.UnknownPayloadFormatError`: it is a fact about an
    argument written in this repository, not about the environment the process is running in.

    Raised at import of the vendor module, which is the only moment it is worth anything.
    Deferred to parse time it would fire after a transport had already fetched, and the
    failure it guards against does not announce itself at parse time at all — it announces
    itself as a reversal count of zero, months later, as an unexplained receivable.
    """


@dataclass(frozen=True, slots=True)
class FlagReversal:
    """A reversal is a flag, plus the reversal's own detail, on the dispense's own row.

    Both vendors in this package declare a representation of this family, for reasons their
    own modules give.  What the family buys the reader is that the whole reversal is on one
    row, so "how many reversals are in this file" is "how many rows carry the flag" — and
    that count is idempotent under the full-file re-delivery both vendors use.

    ``reversed_value`` is the literal that means reversed, and it is read rather than
    inferred from absence.  That matters more than it looks: Craneware writes ``ACTIVE`` in
    its flag column where Verity leaves the cell empty, so "not reversed" has two spellings
    across two files and "reversed" has one.  A rule keyed on the positive literal works for
    both; a rule keyed on emptiness reports every Craneware row as reversed.
    """

    #: The column carrying the flag.  ``reversal_status`` on Verity, ``dispense_status`` on
    #: Craneware — the same fact under two names, which is the whole reason a field map
    #: exists.
    column: str
    #: The literal that means reversed.  ``REVERSED`` on both, checked rather than assumed.
    reversed_value: str
    #: What the vendor calls this representation, read from the module that writes the files.
    representation: str
    #: The three columns carrying the reversal's own detail, under the vendor's spelling.
    #: Each may be ``None`` for a dataset that does not carry it.
    received_at_column: str | None = None
    reason_column: str | None = None
    quantity_column: str | None = None

    def __post_init__(self) -> None:
        """Refuse a representation this rule would misread, at the moment it is declared."""
        try:
            declared = ReversalRepresentation(self.representation)
        except ValueError:
            raise UnsupportedReversalRepresentationError(
                f"{self.representation!r} is not a reversal representation this fabric has a "
                f"name for. Known: {', '.join(ReversalRepresentation)}. A vendor that has "
                "changed how it represents a reversal is the one change that must never be "
                "absorbed silently — see requirement B3."
            ) from None
        if declared not in _ON_THE_DISPENSE_ROW:
            raise UnsupportedReversalRepresentationError(
                f"{declared} does not put the reversal on the dispense's own row, so reading "
                f"the flag column {self.column!r} would find nothing and report that nothing "
                "was reversed. That is B3's 'not zero' failure, and it is invisible in every "
                "report afterwards. A mapping for this representation needs its own rule, "
                "not this one."
            )
        object.__setattr__(self, "representation", declared)

    def flagged(self, row: Mapping[str, str]) -> bool:
        """Does this row carry the reversal flag?  One cell, compared as text."""
        return row.get(self.column) == self.reversed_value


@dataclass(frozen=True, slots=True)
class ReversalEffect:
    """One reversal, and the single net effect it is worth on the ledger.

    B3's acceptance is that *"a golden reversed claim produces exactly one net effect on the
    ledger — not zero, not two."*  :attr:`net_effect` is that one, written down rather than
    implied, so a caller counting effects and a caller summing them get the same answer.

    **No money on this object, on purpose.**  The reversal columns carry a signed *quantity*
    and never a reversed amount; the amount beside it on the row is the amount that was
    actually paid, unchanged.  Netting money is the reconciliation engine's job, triggered by
    this flag, and one net effect is only reachable if exactly one side does the netting.
    :attr:`reversal_quantity` is therefore carried as the text the file holds — already signed
    by ``recon.generators.tpa`` on the way onto the wire, and never re-signed here, because
    negating it a second time once put a reversal on the wire as a positive quantity and
    destroyed the one property the feed spec insists on.
    """

    source_id: str
    #: The document this was read from, for lineage. A name, never a path.
    document: str
    #: 1-based position of the row in the file, header counted, so a reader can find it.
    line_no: int
    #: ``(rx_number, pharmacy_npi, provider_npi, ndc11, fill_date)`` as text, gaps included.
    claim_key: tuple[str, ...]
    #: The representation this effect was detected under. Carried so a downstream reader can
    #: tell a flag-derived effect from a correcting-row-derived one without guessing.
    representation: str
    reversal_received_at: str | None
    reversal_reason: str | None
    #: Text. Already signed. Never parsed, never negated.
    reversal_quantity: str | None
    #: Always 1. A reversal on a one-row-per-dispense representation is one event.
    net_effect: int = 1


@dataclass(frozen=True, slots=True)
class SecureFileMapping:
    """Everything that differs between two vendors reading the same way, as data.

    One instance per *dataset*, never per vendor.  Verity's accumulations and invoices have
    genuinely different required sets and genuinely different columns, and the registered
    schema contracts are already per dataset for that reason.  A per-vendor mapping would
    have to carry the union and then branch inside itself, which is the branch this whole
    package exists to delete.

    Construction checks the mapping against the contract registered for the same source id,
    so a column added to the contract and forgotten here fails at import rather than reading
    as ``None`` on every row.
    """

    source_id: str
    vendor: str
    #: The vendor's own name for this file — ``accumulations``, ``Claims Report``.
    dataset: str
    #: Vendor column name to the spelling this fabric uses.  The reason this exists at all:
    #: Verity writes ``ndc_11`` and Craneware writes ``ndc11`` for the same concept, and a
    #: connector that assumed one spelling across both reads ``None`` and joins nothing.
    fields: Mapping[str, str]
    #: Canonical names of the five natural-key components, in the order
    #: ``recon.mocks.source.Dispense.natural_key`` uses them.  340B has no shared identifier
    #: space, so this is the only bridge there is.
    natural_key_fields: tuple[str, ...]
    reversal: FlagReversal
    #: The leading name of the landed file. Verity stamps its exports from the data
    #: (``DOC2-010`` asks the name to retain vendor, export type and generated timestamp), so
    #: the full name is not knowable in advance and a listing transport matches this instead.
    filename_prefix: str
    record_type_column: str = "record_type"
    detail_record_type: str = "DETAIL"
    trailer_record_type: str = "TRAILER"
    #: Where the trailer's declared count lives. ``record_count`` on Verity,
    #: ``declared_record_count`` on Craneware.
    declared_count_column: str = "record_count"
    #: The column carrying the contract version, when the vendor writes one onto every row.
    #: Craneware does, so the wire names its own contract; Verity does not, so this is
    #: ``None`` and the registry's current registration is what a record is checked against.
    schema_version_column: str | None = None

    @property
    def control_columns(self) -> frozenset[str]:
        """Columns that describe the file rather than the claim.

        Excluded from :attr:`fields` because they are the envelope, and mapping them into the
        canonical record would put a control total on a dispense.
        """
        names = {self.record_type_column, self.declared_count_column}
        if self.schema_version_column is not None:
            names.add(self.schema_version_column)
        return frozenset(names)

    def __post_init__(self) -> None:
        """Refuse a mapping that does not cover the contract it claims to read.

        The check runs against :data:`recon.connectors.schema_registry.REGISTRY`, which is
        the *independently declared* shape of the file — not against the module that writes
        it.  A mapping checked against its own writer agrees with whatever the writer does,
        including the day the writer starts doing the wrong thing.

        A field the contract declares and the mapping omits is the quiet failure: nothing
        raises, the column is simply never read, and every record downstream is missing it
        in a way no report shows.  A field the mapping claims and the contract does not
        declare is the reverse and is just as worth refusing — it is a column that will read
        ``None`` on every row of every file.
        """
        contract = schema_registry.REGISTRY.get(self.source_id)
        if contract is None:
            raise ValueError(
                f"no schema contract is registered for {self.source_id!r}, so there is "
                "nothing to check this mapping against. Register the contract in "
                "schema_registry.py first; a mapping with no contract behind it is a "
                "mapping nothing can prove is complete."
            )
        declared = set(contract.field_names)
        mapped = set(self.fields)
        missed = sorted(declared - mapped - self.control_columns)
        if missed:
            raise ValueError(
                f"{self.source_id}: the contract declares {missed} and this mapping does not "
                "read them. An unmapped column does not raise — it is simply absent from "
                "every record, which is the kind of gap nobody finds until a reconciliation "
                "is short and nobody knows why."
            )
        unknown = sorted(mapped - declared)
        if unknown:
            raise ValueError(
                f"{self.source_id}: this mapping reads {unknown}, which the registered "
                "contract does not declare. Those columns would be absent from every row of "
                "every file, and the mapping would read None and say nothing."
            )
        for name in self.natural_key_fields:
            if name not in self.fields.values():
                raise ValueError(
                    f"{self.source_id}: natural-key component {name!r} is not produced by "
                    "this field map, so the only bridge 340B has would be built from a "
                    "column that is never read."
                )
        if self.reversal.column not in self.fields:
            raise ValueError(
                f"{self.source_id}: the reversal flag rides column "
                f"{self.reversal.column!r}, which this field map does not read."
            )


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    """One fetched file, split into the three kinds of row it can hold.

    Values are the text the file carried, verbatim.  Nothing here parses a date, normalises a
    key or touches an amount — money is exact decimal text on the wire, and a connector that
    reformatted a ``CCYYMMDD`` fill date on the way in would have changed the join key and
    joined nothing.
    """

    source_id: str
    #: The document's logical name, from :class:`~recon.connectors.transport.Document`.
    document: str
    #: DETAIL rows, in file order, paired with their 1-based line number in the file.
    rows: tuple[tuple[int, Mapping[str, str]], ...]
    #: TRAILER rows. Normally exactly one. Two means two files were concatenated in
    #: transfer, which is a real secure-file failure and is kept visible rather than
    #: collapsed to "the last one wins".
    trailers: tuple[tuple[int, Mapping[str, str]], ...]
    #: Rows whose ``record_type`` is neither. Never silently dropped — see the module
    #: docstring. Normally empty; anything here is a record that would otherwise vanish.
    unrecognised: tuple[tuple[int, Mapping[str, str]], ...]
    #: Where this vendor's trailer keeps its declared count. Carried on the parsed document
    #: so the count can be read back without the mapping in hand — F2 gets a number and a
    #: document name, which is all the reconciliation needs.
    declared_count_column: str = "record_count"

    @property
    def parsed_record_count(self) -> int:
        """How many detail rows were actually read."""
        return len(self.rows)

    @property
    def declared_record_count(self) -> int | None:
        """What the vendor's trailer says was in the file, or ``None`` when it said nothing.

        Requirement F2 compares this against what was ingested and owns both the comparison
        and the reason code; this property hands the number over and stops. Deliberately not
        compared here, because a control total checked in two places is a control total that
        can disagree with itself.

        **``None`` is not zero and must not be collapsed into it.** A missing control total
        returned as ``0`` compares equal to an empty ingest and reports agreement, which is a
        truncated delivery passing its own check — the exact failure the trailer exists to
        catch. ``verity_export.declared_record_count`` raises for the same reason; a
        connector reports instead of raising, because a vendor sending a malformed file is a
        thing to record and re-request, not a reason for the process to stop.

        The **last** trailer wins when there is more than one, since a concatenation puts the
        later file's total last. :attr:`trailers` is public so a caller can see that happened.
        """
        if not self.trailers:
            return None
        _line_no, trailer = self.trailers[-1]
        declared = trailer.get(self.declared_count_column, "")
        # ``int`` of a row count, which is the only arithmetic anywhere in this package.
        # Counting rows is not arithmetic on money, and there is none of the latter here:
        # every amount is text, copied.
        return int(declared) if declared else None


def read(mapping: SecureFileMapping, document: Document) -> ParsedDocument:
    """Split one fetched document into detail rows, trailers and anything unrecognised.

    A :class:`~recon.connectors.transport.Document` and not a path, which is what keeps the
    guarantee in ``connectors/__init__.py`` intact: a consumer holding a document cannot walk
    anywhere from it.

    ``csv.DictReader`` over the text, addressing every column by name. A positional reader
    would work until a vendor added a column, and both file layouts put a control column
    first — which is how a control total quietly becomes a covered entity id.
    """
    reader = csv.DictReader(io.StringIO(document.text, newline=""))
    rows: list[tuple[int, Mapping[str, str]]] = []
    trailers: list[tuple[int, Mapping[str, str]]] = []
    unrecognised: list[tuple[int, Mapping[str, str]]] = []

    # Line 1 is the header, so the first data row is line 2 and a reported line number is
    # one a reader can count to in the file itself.
    for line_no, row in enumerate(reader, start=2):
        kind = row.get(mapping.record_type_column)
        if kind == mapping.detail_record_type:
            rows.append((line_no, row))
        elif kind == mapping.trailer_record_type:
            trailers.append((line_no, row))
        else:
            unrecognised.append((line_no, row))

    return ParsedDocument(
        source_id=mapping.source_id,
        document=document.name,
        rows=tuple(rows),
        trailers=tuple(trailers),
        unrecognised=tuple(unrecognised),
        declared_count_column=mapping.declared_count_column,
    )


def canonical(mapping: SecureFileMapping, row: Mapping[str, str]) -> dict[str, str | None]:
    """One vendor row under this fabric's spelling, values copied verbatim.

    The whole of the vendor difference, applied. Everything else in this package — the
    reader, the reversal rule, the control total — is already identical for both vendors,
    which is requirement D1's acceptance stated as code rather than as an intention.

    An empty cell becomes ``None``. A CSV has no null, so absence arrives as an empty string,
    and ``schema_registry`` already treats the two as the same thing; keeping that agreement
    here is what stops a vendor CSV and the JSONL feed beside it disagreeing about whether a
    field is missing.
    """
    return {
        canonical_name: (row.get(vendor_name) or None)
        for vendor_name, canonical_name in mapping.fields.items()
    }


def natural_key(mapping: SecureFileMapping, row: Mapping[str, str]) -> tuple[str, ...]:
    """The five natural-key components as text, in order, with their gaps kept.

    340B has no shared identifier space, so this is the only bridge from a vendor row to
    anything else. It is returned with empty components intact and never compacted: a
    clinic-administered drug genuinely has no prescription and no dispensing pharmacy, and a
    key that hid those two gaps would look like a key that matched.

    Building a ``KeyType`` out of these is the crosswalk's job and not this module's. What
    this is for here is identity — telling one reversed claim from another when counting
    net effects.
    """
    record = canonical(mapping, row)
    return tuple((record.get(name) or "") for name in mapping.natural_key_fields)


def is_reversed(mapping: SecureFileMapping, row: Mapping[str, str]) -> bool:
    """Was this dispense reversed, under the representation this vendor declares?

    One cell compared against one literal. It reads that short because the representation
    was decided and written down; a connector that had to infer it would be scanning the
    file for a second row and finding nothing. See :class:`FlagReversal`.
    """
    return mapping.reversal.flagged(row)


def reversal_effects(
    mapping: SecureFileMapping, parsed: ParsedDocument
) -> tuple[ReversalEffect, ...]:
    """One effect per reversed row, and nothing else.  Requirement B3.

    *Exactly* one, which is the acceptance: not zero, not two. Under a flag representation
    the file holds one row per dispense and the reversal rides that row, so one flagged row
    is one reversal and there is no second row to find. Nothing here scans for a correcting
    row, and nothing here nets an amount — doing either would be the second effect.

    Trailers and unrecognised rows are not examined, because :func:`read` has already put
    them somewhere else. A reader that scanned every row would find the flag column empty on
    the trailer and move on, which is right by accident; being right by accident is how a
    reader survives until the day a vendor starts putting a summary status on its trailer.
    """
    rule = mapping.reversal
    effects: list[ReversalEffect] = []
    for line_no, row in parsed.rows:
        if not rule.flagged(row):
            continue
        effects.append(
            ReversalEffect(
                source_id=mapping.source_id,
                document=parsed.document,
                line_no=line_no,
                claim_key=natural_key(mapping, row),
                representation=str(rule.representation),
                reversal_received_at=_cell(row, rule.received_at_column),
                reversal_reason=_cell(row, rule.reason_column),
                reversal_quantity=_cell(row, rule.quantity_column),
            )
        )
    return tuple(effects)


def net_effects_by_claim(effects: Iterable[ReversalEffect]) -> dict[tuple[str, ...], int]:
    """Net effect per claim key — B3's *"not zero, not two"*, as something countable.

    Every value should be ``1``. A ``0`` cannot occur by construction, which is the point: an
    effect exists only because a row carried the flag. A value above ``1`` means two rows in
    the same delivery carried the flag under one natural key, and that is worth surfacing
    rather than summing away.

    It is **not** automatically a double count. The medical key is deliberately weaker than
    the pharmacy one — two administrations of the same drug at the same site on the same day
    are genuinely indistinguishable — so a multi-hit there is the ambiguity the crosswalk
    parks as ``AMBIGUOUS_KEY_MATCH`` rather than a defect in this reader. Picking one would
    be a coin flip that puts real money on the wrong episode and looks identical to a correct
    match in every report afterwards. So this reports the collision and decides nothing.
    """
    totals: dict[tuple[str, ...], int] = {}
    for effect in effects:
        totals[effect.claim_key] = totals.get(effect.claim_key, 0) + effect.net_effect
    return totals


def schema_violation(mapping: SecureFileMapping, row: Mapping[str, str]) -> str | None:
    """``None`` when this row may be mapped; a quarantine detail when it may not.

    Requirement B1's *"validated against the registered version before adaptation"*, and the
    ordering is the whole value. Adaptation is where meaning is assigned, and handing a
    drifted row to an adapter does not usually crash — it succeeds, and produces a
    confidently wrong record that every verdict downstream then treats as fact.

    The row is checked in the **vendor's** spelling, before :func:`canonical` renames
    anything, because that is the shape the contract describes and the shape the vendor can
    be held to.

    When the vendor writes its contract version onto every row, that version is what the row
    is checked against, so the wire names its own contract. A version nobody registered comes
    back as a quarantine detail rather than an exception — that case is the vendor having
    re-cut the export without telling anyone, which is a record to park and a conversation to
    have, not a process to stop.
    """
    version = (
        None
        if mapping.schema_version_column is None
        else (row.get(mapping.schema_version_column) or None)
    )
    return schema_registry.check(mapping.source_id, row, version=version)


def _cell(row: Mapping[str, str], column: str | None) -> str | None:
    """One cell as text, or ``None`` for a column this dataset does not carry.

    An empty cell and an absent column both come back ``None``. Telling them apart would
    mean deciding which absence means what, and that decision belongs to whoever knows the
    dataset — not to a cell reader.
    """
    if column is None:
        return None
    return row.get(column) or None


def mappings_by_source_id(
    mappings: Sequence[SecureFileMapping],
) -> dict[str, SecureFileMapping]:
    """Index a vendor module's mappings by source id, so a caller looks one up by name.

    Both vendor modules end with this call, which is the last thing they have in common and
    the reason neither needs a lookup function of its own.
    """
    return {mapping.source_id: mapping for mapping in mappings}
