"""Who is allowed to say what.  Requirement E5, and Doc 2's page-2 boundary as data.

``normalized_record`` has always carried a ``source_system``, so every row could answer
*"who told us this."*  Nothing anywhere could answer the next question — *"and were they
entitled to?"*  The schema puts one ``CHECK`` on ``record_kind`` and a second, independent
``CHECK`` on ``source_system``; there has never been a constraint over the **pair**, which
means a bank row asserting a 340B qualification, or a TPA row asserting that a manufacturer
paid a rebate, is accepted today and is indistinguishable afterwards from a true one.

``DOC2-004`` is the page-2 *"Source-of-truth boundary"* table, and it is the only per-field
authority statement in ``docs/vendor_evidence/``.  This module is that table, transcribed as
data, plus one function that consults it.  :data:`DOC2_004_AUTHORITY` is written one entry
per authority *domain*, each carrying the verbatim clause of the DOC2-004 row it comes from,
so the table can be diffed against the document row by row — and
``tests/test_authority.py`` performs that diff mechanically against the markdown rather
than trusting that someone did it once.

═══ What the table governs, and what it deliberately does not ═════════════════════════

**Canonical facts, not vendor column spellings.**  A vendor is free to *print* anything in
its export.  Verity's invoices dataset really does carry a ``batch_line_manufacturer_status``
column, and Verity really does echo a ``beacon_id`` (see
``connectors/vendors/verity.py``).  That is not a breach, because ``DOC2-010`` tells us what
to do with it: *"Use Verity as qualification/source context; Beacon submission/status can
remain a separate Shields direct connector."*  The breach is not the vendor printing a
value; it is **us letting that value become the canonical fact**.  So the names in
:attr:`AuthorityDomain.fields` are canonical field names — a ``normalized_record`` column, or
a key of the canonical JSON — never a vendor's column name.

**Fields a record actually asserts.**  ``ingest/adapters.py`` builds one canonical dict for
all four 340B event types, so a ``MANUFACTURER_DECISION`` record carries a
``qualification_status`` key whose value is ``None``.  A key present with no value asserts
nothing, and treating it as an authority claim would reject the entire existing 340B feed.
:func:`asserted_fields` is the filter, and callers are expected to use it.

**``status_code`` is ungoverned on purpose.**  It is the generic column, and its meaning is
whatever the ``record_kind`` says it is — a qualification verdict on one row, an NCPDP
response code on the next.  Authority over *what it means* is carried by the record kind,
which is why :func:`check_authority` takes the kind as well as the fields.  Governing the
column name itself would be governing a container rather than a fact.

**``trn02`` is ungoverned too**, and it is the obvious wrong choice for the bank's row.  The
payer mints TRN02 in the 835 and the bank echoes it back on the deposit; it is a
reassociation handle, not a settlement.  ``ach_trace_number`` is the field only a bank can
originate, and it is the one this table gives the bank.

═══ The scope question: do the pre-connector source systems fall under DOC2-004? ══════

They do, and this was checked against the generated feeds rather than reasoned about.  Doc 2
describes a named-vendor world — Verity, Craneware, Beacon, the bank — while
``TPA_PORTAL`` and ``MANUFACTURER_REBATE`` are the generic source systems of the
pre-connector build, which Doc 2 never mentions.  The obvious worry is that constraining
them would reject data the six existing feeds already produce.

It does not, because the generators already drew Doc 2's boundary; they simply never
enforced it.  ``generators/tpa.py`` flips the source system mid-feed with the reason written
out in full: *"Source system flips to MANUFACTURER_REBATE here: this is no longer the TPA's
own system of record speaking, it is the manufacturer's."*  Run the six feeds through the
adapters and the split is exact:

``TPA_PORTAL``            asserts ``qualification_status`` and ``disqualification_reason``,
                          and never ``manufacturer_status`` or ``rejection_reason``.
``MANUFACTURER_REBATE``   asserts ``manufacturer_status`` and ``rejection_reason``,
                          and never ``qualification_status``.
``BANK``                  asserts no governed field of any other domain.

That is DOC2-004's TPA row and DOC2-004's Beacon row, already separated, in data that
predates the connector layer.  So ``TPA_PORTAL`` is treated as a generic TPA under the TPA
row, and ``MANUFACTURER_REBATE`` as the pre-connector spelling of the rebate authority.  The
second reading is not an invention of this module: ``connectors/registry.py`` and
``connectors/vendors/beacon.py`` both attribute genuine Beacon payloads to
``MANUFACTURER_REBATE`` today, each with a comment saying they do so *"exactly how the 340B
feed attributes its own MANUFACTURER_DECISION records."*

Excluding the legacy systems would have been the convenient answer and it would have been
the weaker one: it would leave the two source systems that carry every rebate record in the
repository outside the only rule written to govern them, and E5's acceptance case —
*a TPA record setting rebate-payment status* — would then be unenforceable on the only TPA
source that actually exists.

**One asymmetry, and it is deliberate.**  ``MANUFACTURER_REBATE`` is authoritative for rebate
*status* but not for the rebate *submission identifier*.  A status is a fact the manufacturer
originates and Beacon relays, so the manufacturer's own system speaking it is the source of
truth arriving by a different road.  A Beacon ID is *minted* by Beacon — ``BEACON-013``:
*"persist Beacon ID against Shields Claim Financial Episode"* — so a non-Beacon source
setting one is not relaying an identifier, it is fabricating one, and there is no road by
which that is true.

═══ What is out of scope ══════════════════════════════════════════════════════════════

The reimbursement leg.  ``DOC2-004`` has no row for a PBM, a clearinghouse or a medical
payer, because Doc 2 is a 340B rebate connectivity assessment and the payer leg is not its
subject.  Those four source systems and the seven reimbursement record kinds are listed
explicitly in :data:`SOURCE_SYSTEMS_WITHOUT_DOC2_AUTHORITY` and
:data:`RECORD_KINDS_WITHOUT_DOC2_AUTHORITY` with the reason written next to each, and a test
asserts the declared sets and the enums cover each other exactly.  A member added to either
enum without a decision about its authority fails the suite, which is the difference between
a source that is *out of scope* and a source that *silently fell through the table*.

Carrying no authority is not a licence: such a source may still set nothing that a domain
below governs.  The table denies what other systems own; it does not enumerate what each
system may have.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

from recon.domain.enums import RecordKind, SourceSystem

__all__ = [
    "AuthorityDomain",
    "AuthorityBreach",
    "SourceAuthorityError",
    "DOC2_004_AUTHORITY",
    "SOURCE_SYSTEMS_WITHOUT_DOC2_AUTHORITY",
    "RECORD_KINDS_WITHOUT_DOC2_AUTHORITY",
    "asserted_fields",
    "authority_for_field",
    "authority_for_record_kind",
    "check_authority",
    "breaches",
]


# ═══ the shape of one row ═══════════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class AuthorityDomain:
    """One thing exactly one kind of system is entitled to say.

    ``doc2_system`` and ``doc2_clause`` are the two halves of a DOC2-004 row, copied
    verbatim and split only so the em dash between them does not have to survive being
    retyped.  They are what makes the table diffable: ``tests/test_authority.py`` parses the
    rows out of ``docs/vendor_evidence/assignment_doc2.md`` and asserts the pairs here are
    exactly the pairs there.

    ``subject`` and ``owner`` exist so the error a breach raises reads as a sentence about
    the business rather than about the schema.  An operator who sees this error is being
    told a vendor sent something it is not entitled to send, which is a data-governance
    fact, not a type error.
    """

    #: Stable name, used in messages and as the identity of the row.
    domain: str
    #: The system column of the DOC2-004 row, verbatim: ``"TPA"``, ``"Beacon"``, ...
    doc2_system: str
    #: The clause after the dash, verbatim from the page-2 table.
    doc2_clause: str
    #: Evidence ids supporting this row.  Every one must resolve in
    #: ``docs/vendor_evidence/index.jsonl``; a test asserts it, and asserts none of them is
    #: an ``UNAVAILABLE`` source.
    evidence: tuple[str, ...]
    #: The business phrase for what this domain is, used in the error message.
    subject: str
    #: Who owns it, in the words a person would use.
    owner: str
    #: Source systems entitled to assert it.  **Empty means no inbound source may** — which
    #: is the Shields row exactly, and is vacuously true for the rows nothing realises yet.
    authoritative: frozenset[SourceSystem]
    #: Record kinds whose very existence asserts this domain.
    record_kinds: frozenset[RecordKind]
    #: Canonical field names carrying it: a ``normalized_record`` column, or a key of the
    #: canonical JSON.  Never a vendor's own column name — see the module docstring.
    fields: frozenset[str]
    #: Why the assignments above are what they are, in one sentence, so a reviewer can
    #: challenge the mapping and not merely the transcription.
    rationale: str


@dataclass(frozen=True, slots=True)
class AuthorityBreach:
    """One specific thing a source said that it was not entitled to say."""

    source_system: SourceSystem
    domain: AuthorityDomain
    #: The offending field, or ``None`` when the record *kind* itself is the claim.
    field: str | None
    record_kind: RecordKind

    def describe(self) -> str:
        if self.domain.authoritative:
            holder = ", ".join(sorted(s.value for s in self.domain.authoritative))
            entitled = (
                f"{self.domain.owner} is authoritative per DOC2-004 "
                f"({self.domain.doc2_system}: \"{self.domain.doc2_clause}\"); "
                f"entitled sources: {holder}"
            )
        else:
            entitled = (
                f"{self.domain.owner} is authoritative per DOC2-004 "
                f"({self.domain.doc2_system}: \"{self.domain.doc2_clause}\"), and it is "
                "computed here rather than delivered, so no inbound source may set it"
            )
        if self.field is None:
            return (
                f"a {self.source_system.value} source may not assert {self.domain.subject}: "
                f"a {self.record_kind.value} record is the claim itself, and "
                f"{entitled}"
            )
        return (
            f"a {self.source_system.value} source may not set {self.domain.subject}: "
            f"the field {self.field!r} on a {self.record_kind.value} record carries it, and "
            f"{entitled}"
        )


class SourceAuthorityError(ValueError):
    """A record tried to set a field its source system does not own (requirement E5).

    Subclasses :class:`ValueError` for the same reason ``ingest.adapters.AdapterError``
    does — a record carrying a value it may not carry *is* a bad value, and a caller
    guarding a whole normalisation step with ``except ValueError`` should not have to know
    this class exists to be safe.

    **That inheritance has one sharp edge, and the caller has to know about it.**
    ``ingest.adapters.adapt`` wraps ``except (TypeError, ValueError)`` around the whole
    dispatch.  Raise this *inside* an adapter and ``adapt`` swallows it and re-raises a
    generic ``AdapterError`` — the record is still refused, so E5's acceptance still holds,
    but the named error and its structured :attr:`breaches` are gone by the time anything
    can act on them.  Check authority on the record ``adapt`` **returns**, not on the
    payload going in.
    """

    def __init__(self, found: tuple[AuthorityBreach, ...]) -> None:
        self.breaches = found
        super().__init__("; ".join(breach.describe() for breach in found))

    @property
    def domain(self) -> AuthorityDomain:
        """The first domain breached — the one the message leads with."""
        return self.breaches[0].domain


# ═══ DOC2-004, transcribed ══════════════════════════════════════════════════
#
# Order is the document's own: TPA, Beacon, CMS / MTF, Bank, Shields platform, Inmar.  The
# Beacon row yields two domains because it names two separably ownable things — an
# identifier Beacon mints and a status Beacon reports — and the asymmetry between them is
# the module docstring's one deliberate exception.  Every other row yields exactly one.


_TPA_QUALIFICATION = AuthorityDomain(
    domain="QUALIFICATION_AND_SOURCE_CONTEXT",
    doc2_system="TPA",
    doc2_clause=(
        "Authoritative for 340B qualification / source transaction context and the "
        "TPA's own claim status."
    ),
    # DOC2-010's Beacon-relationship row is the corroboration that matters here: it says in
    # as many words which half of the picture a TPA owns.  DOC2-009 is the dataset list the
    # qualification actually arrives in.
    evidence=("DOC2-004", "DOC2-010", "DOC2-009"),
    subject="340B qualification",
    owner="the TPA",
    authoritative=frozenset(
        {SourceSystem.TPA_PORTAL, SourceSystem.TPA_VERITY, SourceSystem.TPA_CRANEWARE}
    ),
    # The TPA's own three acts: it qualifies a dispense, it asks the manufacturer for the
    # rebate, and it reverses its own dispense.  TPA_REBATE_REQUEST is the TPA's *request*
    # and not the manufacturer's answer, which is why it sits here and the decision does not.
    record_kinds=frozenset(
        {
            RecordKind.TPA_QUALIFICATION,
            RecordKind.TPA_REBATE_REQUEST,
            RecordKind.TPA_REVERSAL,
        }
    ),
    fields=frozenset({"qualification_status", "disqualification_reason"}),
    rationale=(
        "The qualification verdict and its reason are the only fields in the canonical model "
        "that state whether a dispense was 340B-eligible, and the six generated feeds assert "
        "them under TPA_PORTAL and nowhere else.  TPA_VERITY and TPA_CRANEWARE join the same "
        "row because DOC2-010 puts Verity here explicitly and DOC2-013 makes Craneware the "
        "second instance of the same pattern."
    ),
)

_REBATE_SUBMISSION_IDENTIFIER = AuthorityDomain(
    domain="REBATE_SUBMISSION_IDENTIFIER",
    doc2_system="Beacon",
    doc2_clause=(
        "Authoritative for rebate submission identifiers, validation outcomes, "
        "rebate status and Beacon-side reconciliation data."
    ),
    evidence=("DOC2-004", "DOC2-007", "BEACON-013"),
    subject="a Beacon-assigned rebate submission identifier",
    owner="Beacon",
    # BEACON alone, unlike the status domain below.  DOC2-007's mapping row is
    # "persist Beacon ID against Shields Claim Financial Episode" -- the ID comes back from
    # Beacon on submission.  A source that sets one has not relayed an identifier, it has
    # minted one, and a fabricated join key is worse than a missing one: it resolves.
    authoritative=frozenset({SourceSystem.BEACON}),
    record_kinds=frozenset(),
    fields=frozenset({"beacon_id"}),
    rationale=(
        "``normalized_record.beacon_id`` is the column requirement C5 added for exactly this "
        "identifier, and KeyType.BEACON_ID makes it a join key -- so a wrong value does not "
        "fail to match, it matches the wrong episode.  Note this governs *setting* the "
        "identifier: a Verity export echoes a beacon_id as a lookup handle, and a caller "
        "resolving a key rather than asserting a fact should not pass it here."
    ),
)

_REBATE_STATUS = AuthorityDomain(
    domain="REBATE_STATUS_AND_VALIDATION",
    doc2_system="Beacon",
    doc2_clause=(
        "Authoritative for rebate submission identifiers, validation outcomes, "
        "rebate status and Beacon-side reconciliation data."
    ),
    # DOC2-006 is the fourth bullet of "what is proven publicly": Beacon "supports
    # claim-level submission history, validation outcomes and Beacon IDs".
    evidence=("DOC2-004", "DOC2-007", "DOC2-006"),
    subject="rebate status",
    owner="Beacon",
    # MANUFACTURER_REBATE sits beside BEACON here and not in the identifier domain above.
    # See the module docstring: a status is a fact the manufacturer originates and Beacon
    # relays, so the manufacturer's own system speaking it is the truth arriving by another
    # road.  This is also what keeps the existing 340B feed legal, which is a consequence of
    # the reading rather than the reason for it.
    authoritative=frozenset({SourceSystem.BEACON, SourceSystem.MANUFACTURER_REBATE}),
    record_kinds=frozenset(
        {
            RecordKind.TPA_MANUFACTURER_DECISION,
            RecordKind.REBATE_BATCH,
            RecordKind.REBATE_DISPENSE_LINE,
        }
    ),
    fields=frozenset(
        {
            "manufacturer_status",
            "rejection_reason",
            "validation_outcome",
            "validation_reason_code",
            "rebate_state",
        }
    ),
    rationale=(
        "The first two names are the canonical keys ingest/adapters.py writes for a "
        "MANUFACTURER_DECISION and a rebate dispense line; the last three are what "
        "connectors/vendors/beacon.py writes for a Beacon validation outcome, which "
        "DOC2-007 names as inbound Beacon data.  'rejection_reason' is spelled apart from "
        "the PBM's 'reject_codes' and the dispense's 'reversal_reason' on purpose -- those "
        "are different facts on the reimbursement leg and are not governed here."
    ),
)

_MFP_REFUND = AuthorityDomain(
    domain="MFP_REFUND",
    doc2_system="CMS / MTF",
    doc2_clause="Authoritative for MFP / refund transactions where applicable.",
    evidence=("DOC2-004",),
    subject="an MFP or refund transaction",
    owner="CMS / the Medicare Transaction Facilitator",
    # Declared and unrealised, which is the honest state.  There is no SourceSystem member
    # for CMS or the MTF, no feed, and no record kind -- Doc 2 itself hedges with "where
    # applicable".  The row is carried anyway so that the day an MFP feed is added, the
    # authority decision is a visible edit here rather than an omission nobody notices.
    authoritative=frozenset(),
    record_kinds=frozenset(),
    fields=frozenset(),
    rationale=(
        "Nothing in the canonical model realises this row yet.  Kept declared rather than "
        "dropped so that adding an MFP source is a change to this table, not a silent "
        "arrival outside it."
    ),
)

_SETTLED_CASH = AuthorityDomain(
    domain="SETTLED_CASH",
    doc2_system="Bank",
    doc2_clause="Authoritative for settled cash.",
    # DOC2-007's payment-close row is the corroboration: "Reconcile Beacon rebate/payment
    # reference to bank settlement before marking cash received / claim closed" -- which
    # says plainly that Beacon's reference is not the settlement.  STD-NACHA-CCD is what
    # makes ach_trace_number a thing only a bank originates.
    evidence=("DOC2-004", "DOC2-007", "STD-NACHA-CCD"),
    subject="settled cash",
    owner="the bank",
    authoritative=frozenset({SourceSystem.BANK}),
    record_kinds=frozenset({RecordKind.BANK_TRANSACTION}),
    fields=frozenset({"ach_trace_number", "running_balance_cents"}),
    rationale=(
        "A BANK_TRANSACTION record *is* the assertion that money settled, so the kind "
        "carries most of the weight.  'ach_trace_number' is the field only a bank can "
        "originate -- the network cannot move money without one -- and "
        "'running_balance_cents' is the account's own settled position.  'trn02' is "
        "deliberately absent: the payer mints it in the 835 and the bank echoes it, so "
        "governing it here would forbid every remittance."
    ),
)

_CONSOLIDATED_STATUS = AuthorityDomain(
    domain="CONSOLIDATED_FINANCIAL_STATUS",
    doc2_system="Shields platform",
    doc2_clause=(
        "Authoritative for the consolidated claim-level expected / received / "
        "outstanding / exception financial status."
    ),
    # DOC2-005 is the fabric flow -- raw, canonical, episode, reconciliation ledger -- which
    # places the consolidated picture at the end of our own pipeline rather than at any
    # vendor's edge.
    evidence=("DOC2-004", "DOC2-005"),
    subject="the consolidated claim-level financial status",
    owner="the Shields platform",
    # Empty, and this is the sharpest row in the table.  Shields is not an inbound source
    # system and never will be: the consolidated picture is what the engine computes from
    # everyone else's facts.  A vendor that delivers it is not a source of truth, it is a
    # second opinion overwriting the first.
    authoritative=frozenset(),
    record_kinds=frozenset(),
    fields=frozenset(
        {
            "disposition",
            "reason_codes",
            "cross_track_flags",
            "expected_cents",
            "received_cents",
            "outstanding_cents",
            "exception_status",
        }
    ),
    rationale=(
        "These are the verdict's own fields.  No inbound feed asserts any of them today, "
        "and the point of naming them is that a future connector offering a vendor's "
        "'reconciliation status' has somewhere to be refused."
    ),
)

_INMAR_COEXISTENCE = AuthorityDomain(
    domain="TRANSITIONAL_COEXISTENCE",
    doc2_system="Inmar",
    doc2_clause=(
        "Coexistence source during transition; not the architectural broker for "
        "direct TPA or Beacon connections."
    ),
    evidence=("DOC2-004",),
    subject="a transitional coexistence feed",
    owner="no system",
    # The one DOC2-004 row that confers no authority at all -- it exists to say that a
    # system present in the landscape is not the broker.  Transcribed rather than dropped,
    # because "Inmar is not in the table" and "Inmar is in the table owning nothing" are
    # different statements and only the second is what Doc 2 says.
    authoritative=frozenset(),
    record_kinds=frozenset(),
    fields=frozenset(),
    rationale=(
        "Doc 2 lists Inmar in the boundary table precisely to deny it authority.  No "
        "SourceSystem member exists for it and none should be added without revisiting "
        "this row."
    ),
)


#: DOC2-004 as data, in the document's own row order.
DOC2_004_AUTHORITY: tuple[AuthorityDomain, ...] = (
    _TPA_QUALIFICATION,
    _REBATE_SUBMISSION_IDENTIFIER,
    _REBATE_STATUS,
    _MFP_REFUND,
    _SETTLED_CASH,
    _CONSOLIDATED_STATUS,
    _INMAR_COEXISTENCE,
)


# ═══ the deliberate holes, named ════════════════════════════════════════════
#
# Everything the table above does not reach has to be listed here with a reason.  The pair
# of tests over these two mappings is what turns "we thought about every source system" from
# a claim into a property: add a member to either enum and the suite goes red until someone
# decides whether it owns anything.


#: Source systems carrying no DOC2-004 authority, and why.
#:
#: Carrying no authority is not a licence.  These sources may still set nothing that a
#: domain above governs — the table denies what others own rather than enumerating what each
#: system may have — and in practice none of them tries to.
SOURCE_SYSTEMS_WITHOUT_DOC2_AUTHORITY: dict[SourceSystem, str] = {
    SourceSystem.PBM_ADJUDICATION: (
        "The pharmacy benefit manager's adjudication response.  DOC2-004 has no PBM row: "
        "Doc 2 is a 340B rebate connectivity assessment and the reimbursement leg is not "
        "its subject."
    ),
    SourceSystem.PBM_REMITTANCE: (
        "The PBM's 835.  Same reason as PBM_ADJUDICATION -- and note it mints TRN02, which "
        "is why TRN02 is not the bank's field."
    ),
    SourceSystem.CLEARINGHOUSE_837: (
        "Medical claim submissions and 277CA acknowledgements.  Reimbursement leg; no "
        "DOC2-004 row."
    ),
    SourceSystem.MEDICAL_REMITTANCE: (
        "The medical payer's 835.  Reimbursement leg; no DOC2-004 row."
    ),
}


#: Record kinds asserting nothing DOC2-004 governs, and why.
RECORD_KINDS_WITHOUT_DOC2_AUTHORITY: dict[RecordKind, str] = {
    RecordKind.PHARMACY_CLAIM: "NCPDP adjudication; reimbursement leg, no DOC2-004 row.",
    RecordKind.PHARMACY_REVERSAL: "NCPDP B2 reversal; reimbursement leg, no DOC2-004 row.",
    RecordKind.REMITTANCE: "An 835 batch header; reimbursement leg, no DOC2-004 row.",
    RecordKind.REMITTANCE_CLAIM_LINE: (
        "One claim inside an 835; reimbursement leg, no DOC2-004 row."
    ),
    RecordKind.PROVIDER_LEVEL_ADJUSTMENT: (
        "An 835 PLB line.  Money moved outside any claim loop -- still the payer's leg, and "
        "not the bank's: a PLB is an adjustment on a remittance, not a settlement."
    ),
    RecordKind.MEDICAL_SUBMISSION: "An 837 claim; reimbursement leg, no DOC2-004 row.",
    RecordKind.MEDICAL_ACKNOWLEDGMENT: (
        "A 277CA acknowledgement; reimbursement leg, no DOC2-004 row."
    ),
}


# ═══ lookups ════════════════════════════════════════════════════════════════


def _by_field() -> dict[str, AuthorityDomain]:
    index: dict[str, AuthorityDomain] = {}
    for domain in DOC2_004_AUTHORITY:
        for name in domain.fields:
            # Two domains claiming one field would make enforcement depend on iteration
            # order, which is the quietest way for an authority table to be wrong.
            if name in index:
                raise AssertionError(
                    f"field {name!r} is claimed by both {index[name].domain} and "
                    f"{domain.domain}; a field has exactly one source of truth"
                )
            index[name] = domain
    return index


def _by_record_kind() -> dict[RecordKind, AuthorityDomain]:
    index: dict[RecordKind, AuthorityDomain] = {}
    for domain in DOC2_004_AUTHORITY:
        for kind in domain.record_kinds:
            if kind in index:
                raise AssertionError(
                    f"record kind {kind.value} is claimed by both {index[kind].domain} and "
                    f"{domain.domain}; a record kind has exactly one source of truth"
                )
            index[kind] = domain
    return index


_FIELD_INDEX: dict[str, AuthorityDomain] = _by_field()
_KIND_INDEX: dict[RecordKind, AuthorityDomain] = _by_record_kind()


def authority_for_field(field: str) -> AuthorityDomain | None:
    """The domain governing ``field``, or ``None`` if DOC2-004 does not govern it."""
    return _FIELD_INDEX.get(field)


def authority_for_record_kind(record_kind: RecordKind) -> AuthorityDomain | None:
    """The domain a record of this kind asserts by existing, or ``None``."""
    return _KIND_INDEX.get(record_kind)


def asserted_fields(canonical: Mapping[str, object]) -> frozenset[str]:
    """The keys of ``canonical`` that actually state something.

    A key present with ``None`` or ``""`` asserts nothing, and this distinction is not
    pedantry: ``ingest/adapters.py`` builds one canonical dict covering all four 340B event
    types, so every ``MANUFACTURER_DECISION`` record carries an empty ``qualification_status``
    key and every ``QUALIFICATION_DECISION`` record carries an empty ``manufacturer_status``.
    Passing raw ``.keys()`` to :func:`check_authority` would refuse the entire existing 340B
    feed on fields none of those records ever set.

    ``False`` and ``0`` are kept.  A boolean that is false and an amount that is zero are
    values a system committed to, and dropping them would let a source assert "no" in a
    field it does not own.
    """
    return frozenset(
        name for name, value in canonical.items() if value is not None and value != ""
    )


# ═══ the check ══════════════════════════════════════════════════════════════


def breaches(
    source_system: SourceSystem,
    record_kind: RecordKind,
    fields: Iterable[str] = (),
) -> tuple[AuthorityBreach, ...]:
    """Every DOC2-004 boundary this record crosses, or an empty tuple.

    Separate from :func:`check_authority` so a caller that wants to *report* rather than
    *refuse* — a readiness check over a landed file, say — does not have to catch an
    exception to find out.
    """
    found: list[AuthorityBreach] = []

    kind_domain = _KIND_INDEX.get(record_kind)
    if kind_domain is not None and source_system not in kind_domain.authoritative:
        found.append(
            AuthorityBreach(
                source_system=source_system,
                domain=kind_domain,
                field=None,
                record_kind=record_kind,
            )
        )

    for name in sorted(set(fields)):
        field_domain = _FIELD_INDEX.get(name)
        if field_domain is None:
            continue
        if source_system in field_domain.authoritative:
            continue
        found.append(
            AuthorityBreach(
                source_system=source_system,
                domain=field_domain,
                field=name,
                record_kind=record_kind,
            )
        )
    return tuple(found)


def check_authority(
    source_system: SourceSystem,
    record_kind: RecordKind,
    fields: Iterable[str] = (),
) -> None:
    """Refuse a record whose source is not entitled to what it says.  Requirement E5.

    Two things are checked, because a record can overstep in two ways.  A ``BANK`` source
    delivering a ``TPA_QUALIFICATION`` has overstepped by the *kind* of thing it claims to
    be, before any field is read.  A ``TPA_VERITY`` source delivering a legitimate
    qualification record that also sets ``manufacturer_status`` has overstepped by one
    *field* inside an otherwise proper record, and that is the subtler case — it is
    E5's acceptance criterion, and it is exactly what ``Verity`` invoices would do if their
    ``batch_line_manufacturer_status`` column were mapped onto the canonical fact instead of
    being kept as source context, which is what ``DOC2-010`` says to do with it.

    Args:
        source_system: who is speaking.
        record_kind: what canonical shape they are speaking in.
        fields: the canonical field names this record **asserts a value for**.  Pass
            :func:`asserted_fields` over the canonical dict rather than its raw keys, and
            do not pass identifiers the record merely carries as a join handle — see
            :data:`DOC2_004_AUTHORITY`'s identifier row for why those differ.

    Raises:
        SourceAuthorityError: naming every boundary crossed, the owner of each, and the
            DOC2-004 clause that says so.
    """
    found = breaches(source_system, record_kind, fields)
    if found:
        raise SourceAuthorityError(found)
