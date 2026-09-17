"""Beacon's two legs: the claim we send, and everything that comes back.

Requirements C2, C3, C4 and C5.  Beacon is the only **bidirectional** source in the whole
assessment — every other vendor is a pull — and ``DOC2-007`` states the shape of both legs in
one sentence: *"Direction — Outbound eligible pharmacy / medical claims; inbound
acknowledgements, validation outcomes, Beacon IDs, rebate status and reconciliation data."*

═══ Why this module looks nothing like ``verity.py`` or ``craneware.py`` ════════════

Those two are field maps and one declared reversal rule, because
:mod:`recon.connectors.vendors` already holds every line of parsing they need.  Almost none
of that machinery applies here, and forcing it would be worse than writing none of it:

* :class:`~recon.connectors.vendors.SecureFileMapping` checks itself against a registered
  schema contract at construction.  Beacon has no registered contract — it is an API, the
  contract is ``BEACON-001``/``BEACON-002``, and both are HTTP 403.  A mapping declared
  against a contract that does not exist raises at import and says nothing true.
* :func:`~recon.connectors.vendors.read` splits a CSV into detail rows and a trailer.  A
  Beacon response is a JSON object with nested nulls.  There is no trailer, so there is no
  control total, so requirement F2 has no purchase on this leg at all — and pretending
  otherwise would put a reconciliation on a number nobody sends.
* :func:`~recon.connectors.vendors.natural_key` builds the five-component 340B key.  A
  Beacon response carries **only a Beacon ID** past the acknowledgement, which is the entire
  point of requirement C5 and is why that key type had to exist.

What *is* reused is the part that is genuinely shared:
:class:`~recon.connectors.vendors.FlagReversal` and
:class:`~recon.connectors.vendors.ReversalRepresentation`, because requirement B3 applies to
every vendor and the refusal it buys — an import-time error when a representation stops
putting the reversal on the claim's own record — is worth exactly as much here.

═══ The two submission templates are Beacon's.  The rest is still ours ═════════════

``BEACON-001`` (pharmacy) and ``BEACON-002`` (medical) were retrieved on 2026-09-17 through
the ``r.jina.ai`` text-extraction proxy after a direct fetch returned HTTP 403; the verbatim
text is in ``docs/vendor_evidence/raw/``.  So the **outbound field names** in
:func:`_template_body` are Beacon's own, spelled as Beacon spells them, in published order.

Everything else here is still ours.  ``BEACON-003`` (the validation code glossary) and
``BEACON-004`` (back-end validations) remain HTTP 403, and neither template article says one
word about transport — no path, no verb, no envelope, no response shape.  So the endpoint
path, the request envelope, the inbound payload names and the validation vocabulary below
are ours, tagged ``INVENTED`` one by one in ``MOCK_FIELDS.md``.  The asymmetry is
load-bearing: a response dressed in published spellings would claim Beacon published a
response shape it did not.

The consequence that matters most is requirement C3's: **a rejection reason is carried
across byte for byte.**  Not title-cased, not mapped onto a Beacon-looking code, and — the
one that is easy to get wrong — never replaced by a placeholder when it is absent.
``BEACON-003`` is the page that would have told us what Beacon's codes look like, and it is
a 403; a prettier code in its place would be inventing the single thing we can least check,
and then hiding the invention behind a plausible spelling.  A missing reason stays ``None``.

═══ Nothing here decides anything, and nothing here touches money ══════════════════

Every amount is the text the payload carried, copied.  ``batch_total_amount`` is never summed
from the lines beside it: a second copy free to disagree with the first is the whole class of
bug a control total exists to catch.  Converting to cents is the adapter's job downstream,
and doing it here would put two independent roundings on one figure.

Nothing here writes to a table either.  §4.12 confines this package to its own tables, and it
writes to none: :class:`InboundBatch` hands back rows and a caller persists them.  That is
also why :meth:`ShieldsSubmitsDirectly.submission` builds a request and :func:`submit` takes
the sender as an argument — the HTTP client lives in ``connectors/http.py``, and a mapping
module that imported its own transport could not be tested without one.

═══ Requirement C4 is the interesting one, so it is stated here ════════════════════

``DOC2-014``: *"Confirm submission ownership to prevent duplicate Beacon submissions: Shields
direct-to-Beacon vs. PharmaForce-managed submission must be explicitly decided per covered
entity."*  ``VERITY-004`` is the first-hand corroboration that this choice is real and
shipping — a TPA's own page offering the covered entity the alternative of exporting a Beacon
report and submitting it themselves.

The acceptance is that under mode B the outbound path is **unreachable, not skipped**.  An
``if mode is MODE_A:`` guard at the call site is skipping: it is one edit, one inverted
condition or one new call site away from submitting a claim the TPA has already submitted,
and a duplicate 340B submission is not a duplicate row — it is a second rebate claimed on one
dispense.  So the prohibition is structural, and it is locked four times over:

1. :meth:`SubmissionOwnershipPolicy.route` returns one of **two different types**.  The
   mode-B type, :class:`TpaSubmitsOnOurBehalf`, has no method that builds a submission.  The
   attribute does not exist, so there is nothing to call and nothing to flip.
2. :class:`ShieldsSubmitsDirectly` — the only type that can build one — refuses at
   construction to exist for an entity whose declared ownership is not mode A.
3. :class:`SubmissionRequest` carries the ownership it was built under and refuses the same
   way, so there is no value of that type anywhere in the process whose ownership is mode B.
4. The authority is bound to **one covered entity** and refuses a claim belonging to another.
   Without this the other three are per-process rather than per-entity, and a single mode-A
   entity in the config would open the path for every mode-B entity beside it.

There is deliberately **no default ownership**.  An unconfigured covered entity raises.
Defaulting to mode A submits for an entity nobody decided about, which is the duplicate
``DOC2-014`` names; defaulting to mode B silently submits nothing at all, which looks
identical to a working connector until a rebate cycle closes short.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Callable

from recon.connectors.transport import Document
from recon.connectors.vendors import FlagReversal, ReversalRepresentation
from recon.crosswalk import keys
from recon.domain.enums import KeyType, ReasonCode, RecordKind, SourceSystem
from recon.mocks import beacon_payloads

if TYPE_CHECKING:  # pragma: no cover - import cycle guard, types only
    from recon.connectors.registry import Source
    from recon.connectors.transport import ShouldFetch, Transport

__all__ = [
    "VENDOR",
    "TEMPLATE_PHARMACY",
    "TEMPLATE_MEDICAL",
    "SUBMISSION_PATH",
    "REVERSAL_REPRESENTATION",
    "REJECTION_REASON_CODE",
    "PARK_REASONS",
    "BeaconMappingError",
    "SubmissionNotOursError",
    "SubmissionOwnershipUndeclaredError",
    "UnkeyableClaimError",
    "AcknowledgementError",
    "SubmissionOwnership",
    "SubmissionOwnershipPolicy",
    "ShieldsSubmitsDirectly",
    "TpaSubmitsOnOurBehalf",
    "TpaQualifiedClaim",
    "SubmissionRequest",
    "Sender",
    "submit",
    "acknowledged",
    "EpisodeBeaconId",
    "InboundRecord",
    "ParkedPayload",
    "Rejection",
    "InboundBatch",
    "inbound_records",
    "pull",
    "beacon_id_key",
    "payment_reference_key",
]

#: As ``registry.Source.vendor`` spells it.
VENDOR = "beacon"

#: The two templates ``BEACON-013`` names — *"Use Beacon's published pharmacy / medical data
#: templates"*.  The two templates are Beacon's; these two spellings are ours, and they match
#: what ``recon.mocks.beacon_payloads`` writes so a submission round-trips against the mock.
TEMPLATE_PHARMACY = "PHARMACY"
TEMPLATE_MEDICAL = "MEDICAL"

#: The four values a Beacon validation outcome can carry, named individually so this module
#: never indexes into a tuple to find one.  Each is checked against the module that writes
#: the payloads at import (see :func:`_require_outcome`), which catches a *removal* as well
#: as a rename — an imported constant catches only the rename.
OUTCOME_ACCEPTED = "ACCEPTED"
OUTCOME_REJECTED = "REJECTED"
OUTCOME_REVERSED = "REVERSED"
OUTCOME_PENDING = "PENDING"

#: The manufacturer decision literal that means the rebate was refused.  The same string as
#: :data:`OUTCOME_REJECTED` and deliberately a separate constant: one is Beacon's validation
#: verdict and the other is the manufacturer's, they are read off different payloads, and the
#: day they stop agreeing is the day collapsing them would have hidden it.
MANUFACTURER_REJECTED = "REJECTED"


def _require_outcome(value: str) -> str:
    """Refuse an outcome literal the payload writer no longer emits.

    Requirement M3's acceptance is that this connector's outcome vocabulary equals the set
    already present in the feed.  Checked at import, because at map time it would fire once
    per payload after a fetch has already happened — and the failure it guards against does
    not announce itself at map time at all.  It announces itself as every rejection in a
    pull reading as ``PENDING``, which looks like a quiet week rather than a broken mapping.
    """
    if value not in beacon_payloads.VALIDATION_OUTCOMES:
        raise ValueError(
            f"{value!r} is no longer one of the validation outcomes "
            f"{beacon_payloads.VALIDATION_OUTCOMES} that recon.mocks.beacon_payloads emits. "
            "This mapping reads that vocabulary; a literal it no longer contains would match "
            "nothing and report every claim as undecided, with no error anywhere."
        )
    return value


for _outcome in (OUTCOME_ACCEPTED, OUTCOME_REJECTED, OUTCOME_REVERSED, OUTCOME_PENDING):
    _require_outcome(_outcome)
del _outcome

#: **Requirement B3, declared rather than inferred.**
#:
#: Beacon's real reversal representation is ``UNKNOWN``: ``BEACON-003`` (the validation code
#: glossary) and ``BEACON-004`` (back-end validations) are the pages that would have said,
#: and both are 403.  So this is a decision, tagged ``INVENTED``, and what it declares is
#: what this connector actually reads: one ``validation_outcome`` payload per claim, carrying
#: one of four mutually exclusive values, one of which is ``REVERSED``.  That is a status on
#: the claim's own response, which is the ``STATUS_FLAG_ROW`` family.
#:
#: B3's *"exactly one net effect — not zero, not two"* is therefore structural here rather
#: than counted.  There is no correcting row to find and no second payload to double-count:
#: a claim has one validation outcome, and ``REVERSED`` excludes the other three by
#: construction.  :func:`recon.connectors.vendors.reversal_effects` is deliberately **not**
#: reused for the counting, because it keys an effect on the five-component natural key and a
#: Beacon response past the acknowledgement carries only a Beacon ID.
REVERSAL_REPRESENTATION: str = ReversalRepresentation.STATUS_FLAG_ROW

#: The reversal rule, declared through the shared class so that a representation which stopped
#: putting the reversal on the claim's own record would be refused at import of this module —
#: the same guarantee ``verity.py`` and ``craneware.py`` get, for the same reason.
_REVERSAL = FlagReversal(
    column="outcome",
    reversed_value=OUTCOME_REVERSED,
    representation=REVERSAL_REPRESENTATION,
    reason_column="reason_code",
    received_at_column="decided_at",
)

#: The reason code the deterministic engine already derives from a
#: ``TPA_MANUFACTURER_DECISION`` whose manufacturer status is ``REJECTED``.
#:
#: Named here so requirement C3's acceptance — *"a rejected submission produces
#: ``REBATE_REJECTED`` with the vendor's reason preserved verbatim"* — is checkable against
#: this module rather than only against the engine three layers away.  **Naming a destination
#: is not adjudicating one:** nothing in this package writes a reason code onto a verdict, and
#: :class:`Rejection` carries the vendor's own string untouched beside it, plus the name of
#: the payload field it was read from.
REJECTION_REASON_CODE: ReasonCode = ReasonCode.REBATE_REJECTED

#: Why a payload was carried through to :attr:`InboundBatch.parked` instead of becoming a
#: record.  Connector-local strings, deliberately **not** members of
#: ``domain.enums.ParkReason`` — that vocabulary is about a record whose *keys* resolved to
#: nothing, which is a different failure with a different owner, and widening it from here
#: would put connector plumbing into a crosswalk vocabulary backed by a SQL ``CHECK``.
PARK_REASONS: frozenset[str] = frozenset(
    {
        "NOTHING_DECIDED_YET",
        "NO_VENDOR_TIMESTAMP",
        "NO_BEACON_ID",
        "NO_PAYMENT_REFERENCE",
        "UNKNOWN_PAYLOAD_KIND",
        "UNPARSEABLE_LINE",
    }
)

#: Where a submission is posted.  **Invented and declared.**  Item 5 of
#: ``docs/vendor_evidence/beacon.md``'s UNKNOWN list is *"endpoint paths, HTTP verbs, request
#: and response envelopes"*, and ``DOC2-008`` says plainly they are not public.  Beacon's two
#: retrieved template articles say nothing about transport either — neither mentions an API,
#: a path or a verb — so this path is ours and there is no evidence that could make it less
#: so.
#:
#: **One path, corrected.**  This was ``/v1/claims/pharmacy`` and ``/v1/claims/medical``, on
#: the reasoning that two articles mean two endpoints.  Neither path exists on
#: :mod:`recon.mocks.beacon_server`, whose router has always spoken ``POST /claims``, so
#: every submission this connector built fell through to ``404 unknown_endpoint`` — and no
#: test caught it, because the tests that POST submissions POST the *mock's* own output to
#: the mock's own path.  Both path sets were equally invented; the mock's is the one with a
#: working router and a suite behind it, so the connector moved.  The template distinction
#: stays where it was already carried and where Beacon actually puts it: in the body, as the
#: field list and the ``template`` envelope field.
SUBMISSION_PATH = "/claims"

#: The closed set of templates a :class:`SubmissionRequest` may name.  Its own constant now
#: that the path no longer varies by template — the old membership test rode on the path
#: mapping's keys, and collapsing the mapping without this would have deleted the check.
_TEMPLATES: frozenset[str] = frozenset({TEMPLATE_PHARMACY, TEMPLATE_MEDICAL})

#: The natural-key fields on an acknowledgement, as ``recon.mocks.beacon_payloads`` writes
#: them.  **Still snake_case, deliberately, and this is where the asymmetry shows.**  The
#: submission above carries Beacon's published names because Beacon published a submission
#: template; Beacon published no response shape at all, so an acknowledgement's fields are
#: ours and are spelled as ours.  ``date_of_service`` here is the ``fill_date`` echoed back,
#: in the ``CCYYMMDD`` the feed wrote it in — see :func:`_acknowledgement_natural_key`, which
#: parses it to ISO before building a key.
_ACK_RX = "rx_number"
_ACK_PHARMACY_NPI = "pharmacy_npi"
_ACK_PROVIDER_NPI = "provider_npi"
_ACK_NDC = "ndc_11"
_ACK_DATE = "date_of_service"


# ═══ failures ═══════════════════════════════════════════════════════════════════════


class BeaconMappingError(ValueError):
    """A Beacon payload or claim cannot be mapped as asked.

    ``ValueError`` throughout this module, matching
    :class:`~recon.connectors.registry.UnknownPayloadFormatError` and
    :class:`~recon.connectors.vendors.UnsupportedReversalRepresentationError`: every one of
    these is a fact about an argument written in this repository, not about the environment
    the process is running in.  A transport failure is the ``RuntimeError`` case, and it
    belongs to the transport.
    """


class SubmissionNotOursError(BeaconMappingError):
    """Requirement C4, as a class.  This claim's submission belongs to the TPA.

    Raised at the moment a mode-B route, authority or request is asked to exist — never at
    the moment one would have been sent.  By send time the object would already have been
    constructed, and the whole acceptance criterion is that it cannot be.
    """


class SubmissionOwnershipUndeclaredError(BeaconMappingError):
    """No submission ownership has been declared for this covered entity.

    ``DOC2-014`` says the choice *"must be explicitly decided per covered entity"*, so the
    honest response to an entity nobody decided about is to refuse, loudly, naming it.  Both
    available defaults are wrong in a way that is invisible: mode A submits a claim the TPA
    may already have submitted, and mode B submits nothing while looking healthy.
    """


class UnkeyableClaimError(BeaconMappingError):
    """A claim carries neither the pharmacy nor the medical natural key.

    340B has no shared identifier space, so the natural key is the only correlation handle a
    submission has until Beacon answers with an ID.  Without it a re-run cannot tell that it
    is re-sending the same claim, and a duplicate 340B submission is a second rebate claimed
    on one dispense rather than a duplicate row.
    """


class AcknowledgementError(BeaconMappingError):
    """An acknowledgement cannot be trusted to name the claim we sent.

    Either it carries no Beacon ID at all, or its natural key is not the one submitted.  The
    second is the dangerous one and is why it is checked: persisting a Beacon ID against the
    wrong episode joins one entity's rebate to another's dispense, and every report
    afterwards is internally consistent and wrong.
    """


# ═══ requirement C4 — submission ownership as configuration ═════════════════════════


class SubmissionOwnership(StrEnum):
    """Who submits this covered entity's 340B claims to Beacon.  A closed vocabulary.

    ``DOC2-014``'s two modes, named after what they mean rather than after the letters, so a
    config row reads as a decision instead of as a lookup.  ``VERITY-004`` is the first-hand
    corroboration that a shipping TPA offers exactly this choice: *"Alternatively, CEs can
    choose to export a Beacon report to manually submit to Beacon."*

    Deliberately **not** in ``domain.enums``.  That module's own rule is that a vocabulary
    belongs to it when it appears on the wire and is backed by a SQL ``CHECK``; this one is
    neither.  It is connector configuration, and the precedent for connector configuration
    keeping its enum beside the code that reads it is
    :class:`~recon.connectors.registry.PayloadFormat`.

    There is no third member and no ``UNKNOWN``.  An entity nobody has decided about is the
    absence of a row, which :class:`SubmissionOwnershipPolicy` refuses by name — a member
    meaning "undecided" would be a value the outbound path could be handed and would have to
    branch on, which is the branch this design exists to delete.
    """

    #: Mode A.  Shields submits directly to Beacon and consumes its own outcomes.
    SHIELDS_DIRECT = "SHIELDS_DIRECT"
    #: Mode B.  The TPA submits on the entity's behalf; we consume outcomes only.
    TPA_MANAGED = "TPA_MANAGED"


@dataclass(frozen=True, slots=True)
class TpaSubmitsOnOurBehalf:
    """The mode-B route.  It has no method that builds a submission, and that is the point.

    Requirement C4's acceptance is that the outbound path is *unreachable*, not skipped.  A
    boolean on one object and an ``if`` at the call site is skipping — the call still exists,
    it is one inverted condition from firing, and a second call site added next month does
    not inherit the guard.  Here there is no attribute to call: ``route.submission(claim)``
    on a mode-B entity is a name that does not resolve, in a type a checker can see.

    :meth:`__getattr__` exists only to make that failure say why.  It cannot create a path —
    ``__getattr__`` runs exclusively for attributes that do not exist, so the method is still
    genuinely absent — and it re-raises a plain ``AttributeError`` for anything unrelated,
    so an ordinary typo is still reported as an ordinary typo.

    The inbound leg is unaffected and deliberately so: mode B means *the TPA submits and we
    consume the outcomes*, so :func:`pull` is exactly as available here as it is in mode A.
    """

    covered_entity_id: str

    #: The names this route is asked for by someone who meant to submit.  Listed so the
    #: refusal names the requirement instead of reading as a misspelling.
    _OUTBOUND_NAMES = ("submission", "submit", "request", "send", "post")

    def __getattr__(self, name: str) -> Any:
        if name in TpaSubmitsOnOurBehalf._OUTBOUND_NAMES:
            raise SubmissionNotOursError(
                f"covered entity {self.covered_entity_id!r} is configured "
                f"{SubmissionOwnership.TPA_MANAGED} -- the TPA submits its claims to Beacon "
                f"and we consume the outcomes -- so this route has no {name!r} and there is "
                "no submission to construct. Requirement C4: submitting anyway would be the "
                "same dispense claimed for a rebate twice, which is not a duplicate row."
            )
        raise AttributeError(name)


@dataclass(frozen=True, slots=True)
class ShieldsSubmitsDirectly:
    """The mode-A route: the only object in this package that can build a submission.

    It is bound to **one covered entity**, and that binding is the fourth of C4's four locks.
    Without it the other three are per-process rather than per-entity — one mode-A entity
    anywhere in the config would put a live ``submission`` method in reach of every mode-B
    entity beside it, and the claim that gets submitted twice is the one whose TPA was
    already submitting it.

    ``ownership`` is carried rather than assumed so that the refusal is a property of the
    value and not of the factory that made it.  A caller who builds this by hand for a
    mode-B entity gets the same error the route would have given, at the same moment.
    """

    covered_entity_id: str
    #: The registry row: where Beacon lives for this deployment, and which credential name to
    #: resolve at send time.  A row, never a client — requirement A1.
    source: "Source"
    ownership: SubmissionOwnership = SubmissionOwnership.SHIELDS_DIRECT

    def __post_init__(self) -> None:
        """Refuse to exist for an entity whose ownership is not mode A."""
        if self.ownership is not SubmissionOwnership.SHIELDS_DIRECT:
            raise SubmissionNotOursError(
                f"covered entity {self.covered_entity_id!r} is configured {self.ownership}, "
                "so no object that can build a Beacon submission may exist for it. "
                "Requirement C4 asks for the outbound path to be unreachable rather than "
                "skipped, and a constructor that accepted this and left the method inert "
                "would be a skip wearing a type."
            )

    def submission(self, claim: "TpaQualifiedClaim") -> "SubmissionRequest":
        """Map one TPA-qualified claim into Beacon's template and return the call to make.

        Requirement C2's first half.  The request is **built and returned**, not sent, for
        the same reason nothing in this package writes SQL: the thing that knows how to talk
        HTTP is ``connectors/http.py``, and a mapping module that imported its own transport
        could not be asserted on without one.  :func:`submit` is the one-line seam that joins
        them, and it takes the sender as an argument.

        Raises:
            SubmissionNotOursError: ``claim`` belongs to a different covered entity.  See the
                class docstring — this is the per-entity half of requirement C4, and it is
                the half that a process-wide flag does not give you.
            UnkeyableClaimError: the claim carries neither natural key, so a re-run could not
                tell it was re-sending the same dispense.
        """
        if claim.covered_entity_id != self.covered_entity_id:
            raise SubmissionNotOursError(
                f"this route submits for covered entity {self.covered_entity_id!r} and the "
                f"claim belongs to {claim.covered_entity_id!r}. Submission ownership is a "
                "per-entity decision (DOC2-014), so a route may not carry another entity's "
                "claim -- that is how a TPA-managed entity's dispense is submitted a second "
                "time by us."
            )
        template = claim.template
        natural_key = claim.natural_key()
        return SubmissionRequest(
            source_id=self.source.source_id,
            endpoint=self.source.endpoint,
            credential_ref=self.source.credential_ref,
            covered_entity_id=self.covered_entity_id,
            template=template,
            method="POST",
            path=SUBMISSION_PATH,
            body=_template_body(claim, template),
            natural_key=natural_key,
            # ``DOC2-002`` step 3 asks the adapter build for idempotency.  The key is the
            # natural 340B key and never a value we mint: two runs of the same mapping over
            # the same claim have to agree, and a minted id would differ per run and defeat
            # the only duplicate-submission check the outbound leg has.
            idempotency_key=f"{natural_key[0]}{keys.SEPARATOR}{natural_key[1]}",
            ownership=self.ownership,
        )


@dataclass(frozen=True, slots=True)
class SubmissionOwnershipPolicy:
    """Which covered entities we submit for.  Configuration, declared per entity.

    Requirement C4 in its config form.  The mapping is per covered entity because
    ``DOC2-014`` says the decision is, and because the failure it prevents is per entity: one
    entity's TPA submitting while we also submit is that entity's rebate claimed twice, and
    it says nothing about the entity next to it.

    There is no ``default`` parameter, on purpose.  See
    :class:`SubmissionOwnershipUndeclaredError` — both available defaults are wrong in a way
    no report shows.
    """

    by_covered_entity: Mapping[str, SubmissionOwnership]

    def __post_init__(self) -> None:
        """Refuse an ownership value that is not one of the two modes, where it is written.

        Construction is the only place this is worth anything.  A free string here reaches
        :meth:`route`, compares unequal to ``SHIELDS_DIRECT``, and quietly produces a mode-B
        route for an entity somebody meant to submit for — a connector that runs clean and
        sends nothing.
        """
        unknown = sorted(
            entity
            for entity, ownership in self.by_covered_entity.items()
            if ownership not in tuple(SubmissionOwnership)
        )
        if unknown:
            raise SubmissionOwnershipUndeclaredError(
                f"covered entities {unknown} declare a submission ownership that is not one "
                f"of {', '.join(SubmissionOwnership)}. A value this rule does not recognise "
                "compares unequal to mode A and silently becomes mode B, which is a "
                "connector that runs clean and submits nothing."
            )

    def for_entity(self, covered_entity_id: str) -> SubmissionOwnership:
        """The declared mode, or a named refusal.  Never a default."""
        try:
            return SubmissionOwnership(self.by_covered_entity[covered_entity_id])
        except KeyError:
            known = ", ".join(sorted(self.by_covered_entity)) or "<none>"
            raise SubmissionOwnershipUndeclaredError(
                f"no submission ownership is declared for covered entity "
                f"{covered_entity_id!r}; declared entities: {known}. DOC2-014 requires the "
                "Shields-direct versus TPA-managed choice to be made explicitly per covered "
                "entity, and neither default is safe: mode A submits for an entity nobody "
                "decided about, mode B submits nothing and looks healthy doing it."
            ) from None

    def route(
        self, covered_entity_id: str, source: "Source | None" = None
    ) -> "ShieldsSubmitsDirectly | TpaSubmitsOnOurBehalf":
        """The object this entity's claims travel through — of two different types.

        This is the first of requirement C4's four locks.  The return type is a union, and
        the mode-B member has no method that builds a submission, so under mode B there is
        nothing to call: a checker rejects it statically and the process rejects it by name.

        ``source`` is required for mode A and ignored for mode B, which reads oddly and is
        correct: a mode-B entity has no endpoint to post to and no credential to resolve, so
        demanding one would be asking an operator to configure a leg that must not exist.
        """
        ownership = self.for_entity(covered_entity_id)
        if ownership is SubmissionOwnership.TPA_MANAGED:
            return TpaSubmitsOnOurBehalf(covered_entity_id=covered_entity_id)
        if source is None:
            raise SubmissionOwnershipUndeclaredError(
                f"covered entity {covered_entity_id!r} is {ownership} and no registry row was "
                "supplied. Mode A posts to an endpoint under a named credential, and both "
                "live on the source row (requirement A1); routing without one would build a "
                "submission with nowhere to send it."
            )
        return ShieldsSubmitsDirectly(
            covered_entity_id=covered_entity_id, source=source, ownership=ownership
        )


# ═══ requirement C2 — the outbound claim ════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class TpaQualifiedClaim:
    """One TPA-qualified claim, in this fabric's spelling, ready to be dressed for Beacon.

    The canonical model's shape rather than any vendor's, which is what makes this module a
    *mapping* instead of a second copy of the domain.  ``ndc11`` and ``date_of_service`` are
    the ``normalized_record`` column spellings; Beacon's published templates write
    ``NDC-11`` and ``Date of Service``, and the pharmacy template's ``Date of Service`` is
    the fill date the file vendors call ``fill_date``.  Those reconciliations are the whole
    reason ``MOCK_FIELDS.md`` has a table.

    **No identifier is normalised.**  The generators inject an identifier-drift defect on
    purpose, and a connector that repaired a drifted ``rx_number`` on the way out would hand
    Beacon a join the real feed does not have and delete the crosswalk-miss exception the
    defect exists to produce.  So no ``lstrip("0")``, no ``upper()``, no trimming — the same
    prohibition :mod:`recon.crosswalk.keys` states for key components.

    **Dates are the one exception, and it is a format change rather than a repair.**  An
    earlier version of this docstring said "no date is reformatted" and that the date of
    service travels "exactly as the source spelled it".  Both were false the whole time and
    are retracted here: these fields arrive ``YYYY-MM-DD`` from ``normalized_record`` and
    :func:`_template_body` renders them ``CCYYMMDD`` for the wire, through
    :func:`recon.crosswalk.keys.iso_date_to_wire`.  That is lossless and bijective —
    parsing, not normalisation, in the sense ``keys``'s module docstring draws — and it is
    *required*: every feed and the Beacon mock speak ``CCYYMMDD``, so the unconverted ISO
    the old text described joined to nothing.
    """

    covered_entity_id: str
    manufacturer: str | None
    ndc11: str
    #: The dispense or administration date, ``YYYY-MM-DD`` as ``normalized_record`` stores
    #: it.  Rendered ``CCYYMMDD`` on the wire; see the class docstring.
    date_of_service: str
    qualification_status: str | None = None
    #: When the rebate was requested.  ``BEACON-009``'s 45-day window is the best-supported
    #: Beacon fact we hold and is deliberately **not** computed here: a submission that
    #: missed it is already adjudicated upstream and arrives as a decision, so re-deriving
    #: the window would be this connector forming its own opinion about timeliness and then
    #: being free to disagree with the feed.
    submission_date: str | None = None

    # -- pharmacy template ------------------------------------------------
    rx_number: str | None = None
    pharmacy_npi: str | None = None
    prescriber_npi: str | None = None
    #: ``normalized_record.quantity_milli`` / ``episode.quantity_milli`` — the dispensed
    #: quantity in thousandths of a unit, which is NCPDP's own three-implied-decimals form
    #: (field ``442-E7``; ``schema_registry`` records the same convention).  Kept in milli
    #: here and divided only at the moment it is written, so nothing rounds twice.  It feeds
    #: Beacon's pharmacy ``Quantity Dispensed`` and **never** the medical ``Quantity`` —
    #: see :func:`_template_body` for why those are two different decisions.
    quantity_milli: int | None = None

    # -- medical template -------------------------------------------------
    provider_npi: str | None = None
    #: ``BEACON-008``'s medical key names a claim number and a claim line number.  Both live
    #: on the 837 feed and do not reach the 340B sidecar today, so both are normally ``None``
    #: and are written onto the wire as nulls rather than omitted — a gap that is visible is
    #: a gap somebody can close, and a template that looks complete is one nobody will.
    claim_number: str | None = None
    claim_line_number: str | None = None
    #: Requirement E2's J-code.  ``None`` until E2 lands, and carried the moment it does.
    hcpcs: str | None = None
    hcpcs_modifier_code: str | None = None

    #: What a returned Beacon ID is persisted against (``BEACON-013``: *"persist Beacon ID
    #: against Shields Claim Financial Episode"*).  Optional because the outbound mapping
    #: does not need it — it travels on :class:`EpisodeBeaconId` so the caller writing the
    #: row knows which episode it is writing against.
    episode_id: str | None = None

    @property
    def template(self) -> str:
        """``PHARMACY`` or ``MEDICAL`` — a classification of what the claim already is.

        Reads the same null pattern the 340B feed writes: a medically-administered drug has
        no prescription, so ``rx_number`` and ``pharmacy_npi`` are both absent and
        ``provider_npi`` carries the identity instead.  The pharmacy pair is tested first, so
        a claim carrying both stays pharmacy rather than silently changing template between
        two runs that spelled a null differently.
        """
        if self.rx_number is not None or self.pharmacy_npi is not None:
            return TEMPLATE_PHARMACY
        return TEMPLATE_MEDICAL

    def natural_key(self) -> tuple[KeyType, str]:
        """The 340B natural key, in the canonical form the crosswalk indexes on.

        Built through :mod:`recon.crosswalk.keys` rather than spelled here, because that
        module is the one place a ``key_value`` is ever constructed and two builders drift.
        A claim satisfying neither shape raises rather than returning a partial key: a
        composite key with a missing component is not a weaker key, it is a *different* key
        that collides with somebody else's record.
        """
        if self.rx_number and self.pharmacy_npi:
            return keys.natural_340b_pharmacy(
                self.pharmacy_npi, self.rx_number, self.ndc11, self.date_of_service
            )
        if self.provider_npi:
            return keys.natural_340b_medical(
                self.provider_npi, self.ndc11, self.date_of_service
            )
        raise UnkeyableClaimError(
            f"claim for covered entity {self.covered_entity_id!r} carries neither "
            "{pharmacy_npi, rx_number} nor provider_npi, so it has no 340B natural key. "
            "That key is the only correlation handle a submission has before Beacon answers "
            "with an ID, and without it a re-run cannot tell it is sending the same dispense "
            "twice."
        )

    @classmethod
    def from_canonical(cls, record: Mapping[str, Any]) -> "TpaQualifiedClaim":
        """Build a claim from a canonical record, accepting the two spellings that differ.

        ``ndc11``/``ndc_11`` and ``date_of_service``/``fill_date`` are the same two facts
        under two names.  ``normalized_record`` uses the first of each pair; the 340B feed
        and the secure-file vendors use the second.  Accepting both here is not laxness — it
        is the same reconciliation ``verity.py`` and ``craneware.py`` do in their field maps,
        done once rather than at every call site, and the canonical spelling wins so the
        result never depends on which key happened to be present.

        An empty string becomes ``None``, matching
        :func:`recon.connectors.vendors.canonical`: a CSV has no null, so absence arrives as
        an empty cell, and letting the two disagree is how one source reports a field missing
        and another reports it present-and-blank.

        ``quantity_milli`` is the one column read as a number rather than as text, because
        Beacon's ``Quantity Dispensed`` is a count in whole units and this column is in
        thousandths.  It goes through :func:`whole_number` below, which refuses rather than
        defaults: a canonical record that states no quantity must not reach Beacon claiming
        a quantity of nought.
        """

        def cell(*names: str) -> str | None:
            for name in names:
                if name in record:
                    value = record[name]
                    if value is not None and str(value) != "":
                        return str(value)
            return None

        def whole_number(name: str) -> int | None:
            """One integer column, or ``None``.  Never a coerced or defaulted zero.

            ``quantity_milli`` is nullable on ``normalized_record``, and a record that does
            not state a quantity must not arrive at Beacon claiming a quantity of nought —
            that is a number Beacon would validate against a billing unit and pay against.
            A value that is not a whole number is refused for the same reason rather than
            rounded into one.
            """
            value = record.get(name)
            if value is None or value == "":
                return None
            try:
                return int(str(value))
            except (TypeError, ValueError):
                raise BeaconMappingError(
                    f"canonical record carries {name}={value!r}, which is not a whole "
                    "number. Coercing it would put a quantity on a rebate submission that "
                    "nobody wrote down."
                ) from None

        ndc11 = cell("ndc11", "ndc_11")
        date_of_service = cell("date_of_service", "fill_date")
        covered_entity_id = cell("covered_entity_id")
        missing = [
            name
            for name, value in (
                ("covered_entity_id", covered_entity_id),
                ("ndc11", ndc11),
                ("date_of_service", date_of_service),
            )
            if value is None
        ]
        if missing:
            raise BeaconMappingError(
                f"canonical record is missing {missing}, which every Beacon template needs "
                "on both the pharmacy and the medical side. A submission built around the "
                "gap would be accepted and would join to nothing."
            )
        return cls(
            covered_entity_id=str(covered_entity_id),
            manufacturer=cell("manufacturer"),
            ndc11=str(ndc11),
            date_of_service=str(date_of_service),
            qualification_status=cell("qualification_status"),
            submission_date=cell("submission_date"),
            rx_number=cell("rx_number"),
            pharmacy_npi=cell("pharmacy_npi"),
            prescriber_npi=cell("prescriber_npi"),
            quantity_milli=whole_number("quantity_milli"),
            provider_npi=cell("provider_npi"),
            claim_number=cell("claim_number", "clm01"),
            claim_line_number=cell("claim_line_number"),
            hcpcs=cell("hcpcs", "hcpcs_code"),
            hcpcs_modifier_code=cell("hcpcs_modifier_code"),
            episode_id=cell("episode_id"),
        )


def _template_body(claim: TpaQualifiedClaim, template: str) -> dict[str, Any]:
    """One claim in Beacon's published pharmacy or medical template.

    **The field names below are Beacon's, and they are still typed out here rather than
    imported.**  ``BEACON-001`` and ``BEACON-002`` were retrieved on 2026-09-17 through a
    text-extraction proxy, so the 11 pharmacy names and the 13 medical ones are the
    vendor's, spelled exactly as the articles spell them — ``"340B ID"`` with its space,
    ``"NDC-11"`` with its hyphen — in published order.  They match what
    ``recon.mocks.beacon_payloads.submission`` writes, and the two are kept in step by a
    round-trip test rather than by an import, because importing them would mean handing this
    module a mock *object* — and a connector that reads mock objects is a connector that
    cannot be pointed at a real endpoint.  The duplication is the price of that, and the
    round-trip test is what makes it safe; it was *not* safe before, which is how this copy
    drifted from the mock's in the first place with nothing going red.

    **Six envelope keys stay snake_case and stay ours.**  ``payload_kind``, ``direction``,
    ``template``, ``manufacturer``, ``submission_date`` and ``qualification_status`` are
    named by no Beacon page.  The mixed casing on one object is deliberate: it is the
    cheapest way to read which half of a submission is the vendor's contract.

    **Dates go out in wire form.**  ``claim.date_of_service`` and ``claim.submission_date``
    are ``YYYY-MM-DD`` — that is what ``normalized_record`` stores — and every feed, every
    other adapter and the Beacon mock all speak ``CCYYMMDD``.  They are converted through
    :func:`recon.crosswalk.keys.iso_date_to_wire`, the same function the generators use.
    Sending ISO was a real defect: the mock indexes claims on a wire-form date, so a
    connector-built submission matched nothing and came back ``404 unknown_claim``.

    **A published field this fabric cannot populate goes out as ``None``, never omitted, and
    never filled from something nearby.**  ``Date Prescribed`` in particular is not fed from
    ``submission_date`` — Beacon defines it as the day the prescriber wrote the
    prescription, and ours is the day a rebate was requested.  A near-miss is worse than a
    gap: the gap is visible and the near-miss validates.

    **Beacon's published sentinels are never written.**  ``999999`` for ``Rx Bin``, and
    ``CASH``/``NONE`` for ``Rx PCN``, ``Health Plan Name`` and ``Health Plan ID``, each
    assert that the patient was an uninsured or cash payer.  We hold no payer on a 340B
    claim at all, which is a different fact, so all four go out null.

    **The two quantity fields are two decisions, not one.**  Pharmacy ``Quantity
    Dispensed`` is populated: Beacon asks for "the number of units dispensed" and states no
    unit basis, and on the pharmacy side there is nothing ambiguous to resolve.  Medical
    ``Quantity`` is null: Beacon makes its meaning conditional on the HCPCS row — CMS
    billable units or NCPDP standardized billing units — and publishes neither table, so any
    number we sent would be in a unit Beacon did not ask for.

    No ``beacon_id`` on the way out.  Beacon assigns it on receipt (``BEACON-013``), and a
    request that already carried one would make the acknowledgement decorative and delete
    C2's acceptance criterion along with it.
    """
    body: dict[str, Any] = {
        "payload_kind": "submission",
        "direction": "OUTBOUND",
        "template": template,
        "manufacturer": claim.manufacturer,
        "submission_date": _wire_date(claim.submission_date, "submission_date"),
        # The TPA's own eligibility verdict, carried as the feed's word.  ``DOC2-007`` says
        # the outbound direction is *eligible* claims, so the reason the claim is being sent
        # at all travels with it -- and is never re-decided here.
        "qualification_status": claim.qualification_status,
    }
    date_of_service = _wire_date(claim.date_of_service, "date_of_service")
    if template == TEMPLATE_PHARMACY:
        body.update(
            {
                "340b_id": claim.covered_entity_id,
                # The day the prescriber wrote it.  This fabric holds the fill, not the
                # writing, so the field is genuinely absent rather than renamed from
                # something nearby.
                "date_prescribed": None,
                "date_of_service": date_of_service,
                "rx_number": claim.rx_number,
                # No fill number reaches the 340B side: a rebate is about which drug was
                # bought, not which refill it was.  ``keys.natural_340b_pharmacy`` records
                # the same gap from the crosswalk side.
                "fill_number": None,
                "ndc_11": claim.ndc11,
                # **Populated**, unlike its medical namesake.  BEACON-001 asks only for "the
                # number of units dispensed to the patient" and states no unit basis, and on
                # the pharmacy side there is no ambiguity to resolve: a dispensed quantity is
                # a dispensed quantity under NCPDP.  See :func:`_whole_units`.
                "quantity_dispensed": _whole_units(claim.quantity_milli),
                "prescriber_id": claim.prescriber_npi,
                # BEACON-001: "NPI of the pharmacy that filled the prescription."
                "service_provider_id": claim.pharmacy_npi,
                # **Beacon's published sentinels are refused.**  BEACON-001 says to mark
                # "999999" in Rx Bin and "CASH" in Rx PCN for an uninsured or cash payer,
                # and "NONE" in Rx PCN when there is no PCN.  Writing one would assert a
                # clinical fact -- that this patient paid cash -- we have no basis for.  This
                # fabric carries no payer on a 340B claim at all, which is a different state
                # from "the payer is cash", and null is that difference.
                "rx_bin": None,
                "rx_pcn": None,
            }
        )
    else:
        body.update(
            {
                "340b_id": claim.covered_entity_id,
                "claim_number": claim.claim_number,
                "claim_line_number": claim.claim_line_number,
                "date_of_service": date_of_service,
                "hcpcs_code": claim.hcpcs,
                # **Four modifier columns, not one.**  BEACON-002's rendered field list shows
                # a single *HCPCS Code Modifier*; the template file it links to carries four,
                # ``_1`` through ``_4``.  Ours goes in the first and the rest go out null --
                # this fabric parses one modifier off SVC01 and has nowhere to get a second.
                "hcpcs_code_modifier_1": claim.hcpcs_modifier_code,
                "hcpcs_code_modifier_2": None,
                "hcpcs_code_modifier_3": None,
                "hcpcs_code_modifier_4": None,
                # Same sentinel refusal as rx_bin / rx_pcn above: BEACON-002 publishes CASH
                # and NONE for both of these, and we hold no payer to mark either way.
                "health_plan_name": None,
                "health_plan_id": None,
                "ndc_11": claim.ndc11,
                # BEACON-002 asks for two NPIs -- the clinician who rendered the care and
                # the site where it was administered.  This fabric carries one and cannot
                # say which; it is the site's, so it goes in Service Provider ID and the
                # clinician goes out null.  Copying one value into both would manufacture a
                # second fact out of the first.
                "rendering_physician_id": None,
                # **Null even though the pharmacy template's quantity is not**, and the two
                # are genuinely different decisions rather than one applied twice.
                # BEACON-002 conditions this number's meaning on the HCPCS row: with a
                # specific HCPCS code it must be that code's CMS-defined billable units, and
                # without one it must be NCPDP standardized billing units for the NDC-11.
                # Beacon publishes neither table.  ``claim.quantity_milli`` is in this
                # fabric's own units, and ``domain.enums.UnitBasis`` is EACH / ML / MG --
                # not that vocabulary.  Any number written here would be in a unit Beacon
                # did not ask for, and a wrong quantity on a rebate submission is worse than
                # a null: a null is visibly missing, a wrong number is silently paid.
                "quantity": None,
                "unit_of_measure": None,
                # BEACON-002: "the NPI of the healthcare entity where the patient received
                # the medication administration."
                "service_provider_id": claim.provider_npi,
            }
        )
    return body


def _whole_units(quantity_milli: int | None) -> int | None:
    """Thousandths of a unit -> units.  ``None`` stays ``None``.

    ``quantity_milli`` is NCPDP's three-implied-decimals quantity in integer form — the
    convention ``schema_registry`` records for field ``442-E7``, where ``"030000"`` is 30
    units — and Beacon's ``Quantity Dispensed`` wants *"the number of units dispensed to the
    patient"*, so the thousandths have to come off.  The same read
    :func:`recon.api.dossier` already does for the episode dossier, in one place rather than
    two so the two cannot start disagreeing about one figure.

    **This is the only arithmetic in this module and it is deliberately not on money.**
    Requirement M2's prohibition is about amounts: every rebate figure here is the vendor's
    text, copied, because two independent roundings on one amount is how a ledger disagrees
    with itself.  A quantity is not an amount, and this is a unit re-rendering rather than a
    derivation — the same class of operation as ``CCYYMMDD`` to ``YYYY-MM-DD``.

    A quantity that is not a whole number of units is refused rather than truncated.  In
    this fabric it cannot happen — the generator writes ``units * 1000`` — so a remainder
    means the field arrived from somewhere that does not share the convention, and silently
    dropping a fraction of a unit off a rebate submission is the kind of wrong number that
    gets paid.
    """
    if quantity_milli is None:
        return None
    units, remainder = divmod(quantity_milli, 1000)
    if remainder:
        raise BeaconMappingError(
            f"quantity_milli={quantity_milli} is not a whole number of units "
            f"({remainder} thousandths left over). NCPDP 442-E7 carries three implied "
            "decimals and every quantity in this fabric is an exact multiple of 1000, so a "
            "remainder means this value came from a source with a different convention. "
            "Truncating would send Beacon a quantity nobody dispensed."
        )
    return units


def _wire_date(value: str | None, field: str) -> str | None:
    """``"2025-12-16"`` -> ``"20251216"``.  ``None`` stays ``None``.

    The canonical model stores ``YYYY-MM-DD`` so that lexicographic order is chronological;
    every feed and every vendor payload in this repository is ``CCYYMMDD``.  The conversion
    runs through :mod:`recon.crosswalk.keys` rather than a slice here, for the reason that
    module gives about key values: one construction site, or the write side and the read
    side come to disagree about a string and nothing says so.

    Re-rendering one value between two formats is **parsing, not normalisation** — the
    distinction ``keys``'s own module docstring draws.  Nothing is repaired: identifier
    drift is still carried untouched, and a date that is not ``YYYY-MM-DD`` is refused by
    name rather than coerced.
    """
    if value is None:
        return None
    try:
        return keys.iso_date_to_wire(value)
    except keys.KeyError_ as exc:
        raise BeaconMappingError(
            f"{field}={value!r} is not the YYYY-MM-DD this fabric stores, so it cannot be "
            "rendered as the CCYYMMDD Beacon's wire carries. Guessing at the format would "
            "put a date on a submission that joins to nothing, which is a claim silently "
            "lost rather than an error."
        ) from exc


@dataclass(frozen=True, slots=True)
class SubmissionRequest:
    """One Beacon submission, fully built and not yet sent.

    The third of requirement C4's four locks.  :attr:`ownership` is on the value rather than
    only on the factory, so there is no ``SubmissionRequest`` anywhere in the process whose
    ownership is mode B — not one built by hand, not one round-tripped through a serialiser,
    not one assembled in a test helper that meant well.

    It is a description of a call and never a call.  Nothing here opens a socket, which is
    what lets requirement C4's acceptance be asserted with no transport in the repository at
    all: *"a test asserts no submission call can be constructed"* is a statement about this
    type, and this type is pure data.
    """

    source_id: str
    #: Where Beacon lives for this deployment, off the registry row.  A base URL, never a
    #: client, and never a credential — :attr:`credential_ref` is a *name* (requirement A3).
    endpoint: str
    credential_ref: str | None
    covered_entity_id: str
    template: str
    method: str
    path: str
    body: Mapping[str, Any]
    #: ``(KeyType, key_value)`` — the correlation handle until Beacon answers with an ID.
    natural_key: tuple[KeyType, str]
    idempotency_key: str
    ownership: SubmissionOwnership = SubmissionOwnership.SHIELDS_DIRECT

    def __post_init__(self) -> None:
        if self.ownership is not SubmissionOwnership.SHIELDS_DIRECT:
            raise SubmissionNotOursError(
                f"a submission for covered entity {self.covered_entity_id!r} cannot be "
                f"constructed under {self.ownership}: that entity's TPA submits its claims "
                "and we consume the outcomes. Requirement C4 asks for unreachable rather "
                "than skipped, and a request object that existed but was never sent would be "
                "one call site away from being sent."
            )
        if self.template not in _TEMPLATES:
            raise BeaconMappingError(
                f"{self.template!r} is not a Beacon template; known: "
                f"{', '.join(sorted(_TEMPLATES))}. BEACON-001 and BEACON-002 are separate "
                "articles with different field lists -- 11 fields against 13, overlapping "
                "on three -- so the two templates are separate here too."
            )


#: ``(request) -> the decoded acknowledgement``.
#:
#: The seam between this mapping and whatever actually speaks HTTP.  A callable rather than
#: an import so that ``connectors/http.py`` depends on nothing here and nothing here depends
#: on it — which is also what lets the whole outbound leg be exercised against a dictionary.
Sender = Callable[[SubmissionRequest], Mapping[str, Any]]


def submit(request: SubmissionRequest, send: Sender) -> "EpisodeBeaconId":
    """Send one submission and return the Beacon ID to persist.  Requirement C2.

    Three lines long, and the whole of C2's second half.  Everything that could have been
    decided was decided before the request existed, which is what makes the send boring — and
    a boring send is the only kind worth having on a path where a retry means a second rebate
    claimed on one dispense.

    The acknowledgement is checked against the request before its ID is handed back.  See
    :class:`AcknowledgementError`: 340B has no shared identifier space, so the natural key is
    the only thing that can say this receipt belongs to this claim, and an ID persisted
    against the wrong episode is invisible in every report afterwards.

    No retry, no backoff.  Doc 2 files those under step 6 and this build stops at step 5; a
    failed submission raises out of ``send`` and is re-run by hand once somebody has looked
    at why.
    """
    response = send(request)
    return acknowledged(response, request=request)


# ═══ requirement C5 — the Beacon ID as a crosswalk key ══════════════════════════════


def beacon_id_key(beacon_id: str) -> tuple[KeyType, str]:
    """``BEACON_ID`` in its canonical key form.  Requirement C5.

    The canonical construction now lives in :func:`recon.crosswalk.keys.beacon_id`, which is
    where the wave-4 version of this function already said it belonged.

    This wrapper stays because it is the name every caller in this module imports, and it
    keeps validating through :func:`_single_component` first so that a malformed ID still
    raises :class:`BeaconMappingError` rather than the crosswalk's own error type — the
    relocation moves where the string is *built*, and changes nothing a caller can observe.
    """
    return keys.beacon_id(_single_component(beacon_id, "beacon_id"))


def payment_reference_key(reference: str) -> tuple[KeyType, str]:
    """``PAYMENT_REFERENCE`` in its canonical key form.  Requirement E3's key, used by C3.

    The rebate-status source's own reference for a rebate payment, which is a different fact
    from ``TRN02`` (the payer's reassociation reference) and from ``ALLOCATION_CODE`` (the
    batch reference the 340B feed already publishes).  ``DOC2-002`` step 4 names it
    separately for that reason.

    The canonical construction now lives in :func:`recon.crosswalk.keys.payment_reference`;
    see :func:`beacon_id_key` above for why the local validation stays.
    """
    return keys.payment_reference(_single_component(reference, "payment_reference"))


def _single_component(value: object, field: str) -> str:
    """Validate a one-component key value: present, non-empty, no separator."""
    if value is None:
        raise BeaconMappingError(
            f"key component {field!r} is None; a key with no value resolves to everything "
            "or to nothing depending on the reader, and both are wrong."
        )
    text = str(value)
    if text == "":
        raise BeaconMappingError(f"key component {field!r} is empty")
    if keys.SEPARATOR in text:
        raise BeaconMappingError(
            f"key component {field}={text!r} contains the separator {keys.SEPARATOR!r}; two "
            "distinct keys would collide onto one string"
        )
    return text


@dataclass(frozen=True, slots=True)
class EpisodeBeaconId:
    """A Beacon ID and the episode it belongs against.  Rows for a caller to persist.

    ``BEACON-013``'s recommended design in one object: *"persist Beacon ID against Shields
    Claim Financial Episode."*  This package writes nothing itself (§4.12), so what it can
    honestly produce is the row and the key, and :meth:`as_row` is that hand-off written out
    so a caller does not have to read this class to know what to store.

    **This is the only place a ``BEACON_ID`` key is published.**  Every inbound record below
    *looks it up* instead.  One publisher and N consumers is not a style preference: two
    records publishing the same ``(key_type, key_value)`` pair make every lookup on it a
    two-hit, which the crosswalk parks as ``AMBIGUOUS_KEY_MATCH`` — so a second publisher
    would not produce a wrong answer, it would produce no answer, for every rebate at once.
    """

    beacon_id: str
    covered_entity_id: str | None
    template: str | None
    #: The vendor's own timestamp, or ``None``.  There is no clock in this module.
    received_at: str | None
    #: The 340B natural key the acknowledgement echoed back, when it carried one.  This is
    #: what ties the ID to a dispense the first time; afterwards the ID is the join.
    natural_key: tuple[KeyType, str] | None
    #: ``(KeyType.BEACON_ID, beacon_id)`` — requirement C5's key, ready to index.
    publishes: tuple[KeyType, str]
    #: Where this was read from.  ``None`` for an ID that arrived on a live response rather
    #: than in a fetched document — which is the C2 path, and it genuinely has no document.
    document: str | None = None
    line_no: int | None = None
    #: The episode the caller is writing against, when the submission knew it.
    episode_id: str | None = None

    def as_row(self) -> dict[str, Any]:
        """The hand-off, flat.  A caller writes ``normalized_record.beacon_id`` from
        :attr:`beacon_id` and one ``crosswalk_key`` row from :attr:`publishes`.

        Returned rather than written for the reason §4.12 gives: this package may not write
        to any table but its own, and it has none.  The seam is also what keeps the whole
        inbound leg assertable without a database.
        """
        key_type, key_value = self.publishes
        return {
            "beacon_id": self.beacon_id,
            "covered_entity_id": self.covered_entity_id,
            "episode_id": self.episode_id,
            "template": self.template,
            "received_at": self.received_at,
            "key_type": str(key_type),
            "key_value": key_value,
            "natural_key_type": None if self.natural_key is None else str(self.natural_key[0]),
            "natural_key_value": None if self.natural_key is None else self.natural_key[1],
            "document": self.document,
            "line_no": self.line_no,
        }


def acknowledged(
    payload: Mapping[str, Any],
    *,
    request: SubmissionRequest | None = None,
    document: str | None = None,
    line_no: int | None = None,
) -> EpisodeBeaconId:
    """Read a Beacon ID off an acknowledgement, checking it names the claim we sent.

    ``request`` is optional because the same payload arrives two ways: on the live response
    to a submission (requirement C2) and in a pulled document (requirement C3).  When it is
    present the natural keys are compared, and a mismatch raises rather than being recorded —
    a receipt that names a different dispense is not evidence about ours, and persisting its
    ID against our episode would join one entity's rebate to another's claim.

    An acknowledgement with no ``beacon_id`` also raises.  It is the one payload whose entire
    content is the identifier (``DOC2-007`` lists acknowledgements and Beacon IDs as separate
    inbound items), so one without it is not a partial acknowledgement — it is a response the
    submission cannot be confirmed from.
    """
    beacon_id = _text(payload.get("beacon_id"))
    if beacon_id is None:
        raise AcknowledgementError(
            f"acknowledgement carries no beacon_id (payload keys: "
            f"{sorted(payload)}). An acknowledgement's whole content is the identifier "
            "Beacon assigned, so one without it cannot confirm that the claim landed."
        )
    natural_key = _acknowledgement_natural_key(payload)
    if request is not None and natural_key is not None and natural_key != request.natural_key:
        raise AcknowledgementError(
            f"acknowledgement for beacon_id {beacon_id!r} echoes natural key {natural_key} "
            f"and the submission sent {request.natural_key}. 340B has no shared identifier "
            "space, so this key is the only thing that can say the receipt belongs to the "
            "claim; persisting the ID anyway would put a manufacturer's rebate on the wrong "
            "episode, and nothing downstream could tell."
        )
    return EpisodeBeaconId(
        beacon_id=beacon_id,
        covered_entity_id=_text(payload.get("covered_entity_id")),
        template=_text(payload.get("template")),
        received_at=_text(payload.get("received_at")),
        natural_key=natural_key,
        publishes=beacon_id_key(beacon_id),
        document=document,
        line_no=line_no,
    )


def _acknowledgement_natural_key(payload: Mapping[str, Any]) -> tuple[KeyType, str] | None:
    """The 340B key an acknowledgement echoed, or ``None`` when it echoed none.

    ``None`` rather than a raise, which is the opposite of
    :meth:`TpaQualifiedClaim.natural_key` and deliberately so.  On the way out a claim with
    no key cannot be sent idempotently and must be refused.  On the way back the payload
    already carries the Beacon ID, which is the join from here on — so a missing echo costs a
    cross-check and never the identifier itself, and throwing the ID away over it would lose
    the one thing the response was for.

    **The echoed date arrives in wire form and is parsed back to ISO before the key is
    built.**  An acknowledgement carries ``"20251216"``; every ``keys.natural_340b_*`` value
    in this fabric is built from ``"2025-12-16"``, including the one
    :meth:`TpaQualifiedClaim.natural_key` puts on the outbound request.  Comparing the two
    unconverted made :func:`acknowledged`'s cross-check fire on every single submission —
    two spellings of one date reported as a receipt for a different claim.  An unparseable
    date returns ``None`` rather than raising, for the same reason a missing one does: the
    cost is the cross-check, never the identifier.
    """
    rx_number = _text(payload.get(_ACK_RX))
    pharmacy_npi = _text(payload.get(_ACK_PHARMACY_NPI))
    provider_npi = _text(payload.get(_ACK_PROVIDER_NPI))
    ndc11 = _text(payload.get(_ACK_NDC))
    wire_date = _text(payload.get(_ACK_DATE))
    if ndc11 is None or wire_date is None:
        return None
    try:
        date_of_service = keys.wire_date_to_iso(wire_date)
    except keys.KeyError_:
        return None
    if rx_number and pharmacy_npi:
        return keys.natural_340b_pharmacy(pharmacy_npi, rx_number, ndc11, date_of_service)
    if provider_npi:
        return keys.natural_340b_medical(provider_npi, ndc11, date_of_service)
    return None


# ═══ requirement C3 — the inbound leg ═══════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class InboundRecord:
    """One Beacon response, mapped onto an existing canonical record kind.

    **Not** a :class:`~recon.ingest.adapters.CanonicalRecord`, and the difference is the
    scope line this package is drawn along.  ``connectors/`` answers *"how do I get it and am
    I allowed to"*; turning a mapped row into a stored record — cents, lineage, quarantine —
    is ``ingest/adapters.py``'s job.  Two consequences follow, and both are the point:
    :attr:`amount` is the vendor's **text**, never cents, because two independent roundings
    on one figure is how a ledger disagrees with itself; and nothing here is written
    anywhere, because §4.12 confines this package to its own tables and it has none.

    :attr:`looks_up` and :attr:`self_keys` follow ``CanonicalRecord``'s own vocabulary so a
    caller can hand them straight across: what this record resolves *through*, and what it
    publishes *about itself*.
    """

    record_kind: RecordKind
    source_system: SourceSystem
    #: The vendor's own timestamp.  Never a clock reading — §4.11 permits wall-clock in the
    #: checkpoint and in logs, nowhere else, and a manufacturer's decision dated to the
    #: moment we happened to pull it is a decision in the wrong period.
    received_at: str
    idempotency_key: str
    mapping_version: str
    source_id: str
    canonical: Mapping[str, Any]
    looks_up: tuple[tuple[KeyType, str], ...] = ()
    self_keys: tuple[tuple[KeyType, str], ...] = ()
    beacon_id: str | None = None
    payment_reference: str | None = None
    #: Text, copied.  Requirement M2's prohibition read from the connector side.
    amount: str | None = None
    status_code: str | None = None
    document: str | None = None
    line_no: int | None = None

    @property
    def is_rejected(self) -> bool:
        return self.status_code == MANUFACTURER_REJECTED


@dataclass(frozen=True, slots=True)
class ParkedPayload:
    """A payload that arrived and did not become a record, with the reason it did not.

    The same rule :attr:`recon.connectors.vendors.ParsedDocument.unrecognised` exists for,
    one leg over: a reader that silently discards what it does not recognise loses records
    with no error, no quarantine and nothing to reconcile against.  ``PENDING`` outcomes land
    here too, and that is not a failure — a claim nobody has decided on yet has no decision
    to record, and inventing one would be this connector adjudicating.
    """

    reason: str
    payload_kind: str | None
    beacon_id: str | None
    document: str
    line_no: int
    detail: str

    def __post_init__(self) -> None:
        if self.reason not in PARK_REASONS:
            raise BeaconMappingError(
                f"{self.reason!r} is not one of this connector's park reasons "
                f"({', '.join(sorted(PARK_REASONS))}). A free string here is a reason nobody "
                "can count, which is the same as no reason at all."
            )


@dataclass(frozen=True, slots=True)
class Rejection:
    """A rejected claim, the vendor's own word for why, and where that word came from.

    Requirement C3's acceptance.  :attr:`vendor_reason` is the string the payload carried,
    byte for byte — never translated, never title-cased, and never replaced by a placeholder
    when it is absent, because ``BEACON-003`` is a 403 and a plausible-looking substitute
    would be inventing precisely the vocabulary we cannot check.  ``None`` means the vendor
    gave no reason, which is a different and honest fact.

    :attr:`reason_source` names the field the string was read from, so a rejection can be
    walked back to the payload that carried it.  Two fields can hold one: Beacon's own
    validation verdict and the manufacturer's decision are different judgements by different
    parties, kept apart here for the same reason ``verity.py`` refuses to fold
    ``batch_line_manufacturer_status`` into ``manufacturer_decision_status``.
    """

    beacon_id: str | None
    #: :data:`REJECTION_REASON_CODE`.  Named, not decided — see that constant.
    reason_code: ReasonCode
    vendor_reason: str | None
    reason_source: str | None
    document: str | None
    line_no: int | None


@dataclass(frozen=True, slots=True)
class InboundBatch:
    """Everything one pull produced: records to adapt, IDs to persist, payloads parked.

    Three tuples rather than one, because they have three different owners.  :attr:`records`
    go to ``ingest/adapters.py``; :attr:`beacon_ids` go to whoever writes the crosswalk;
    :attr:`parked` goes to a human.  Collapsing them would force each consumer to filter for
    its own half, and a filter written twice is a filter that disagrees with itself.
    """

    source_id: str
    records: tuple[InboundRecord, ...]
    beacon_ids: tuple[EpisodeBeaconId, ...]
    parked: tuple[ParkedPayload, ...]
    #: Documents read, in order, so a caller can say what this batch was built from.
    documents: tuple[str, ...] = ()
    #: Every registry row this batch was pulled from.  More than one is the normal case —
    #: see :func:`pull` for why a Beacon pull spans its four inbound rows at once.
    source_ids: tuple[str, ...] = ()

    @property
    def rejections(self) -> tuple[Rejection, ...]:
        """Every rejected claim in this batch, carrying the vendor's reason verbatim."""
        found: list[Rejection] = []
        for record in self.records:
            if not record.is_rejected:
                continue
            reason, source = _verbatim_rejection_reason(record.canonical)
            found.append(
                Rejection(
                    beacon_id=record.beacon_id,
                    reason_code=REJECTION_REASON_CODE,
                    vendor_reason=reason,
                    reason_source=source,
                    document=record.document,
                    line_no=record.line_no,
                )
            )
        return tuple(found)

    @property
    def episode_beacon_id_rows(self) -> tuple[dict[str, Any], ...]:
        """:attr:`beacon_ids` as flat rows a caller can write.  Requirement C5's hand-off."""
        return tuple(row.as_row() for row in self.beacon_ids)


def _verbatim_rejection_reason(canonical: Mapping[str, Any]) -> tuple[str | None, str | None]:
    """The rejecting party's own word, and the field it was read from.

    The manufacturer's reason is preferred over Beacon's validation reason when both are
    present, and the choice is recorded rather than hidden: ``rejection_reason`` is the
    manufacturer refusing to pay a rebate, which is the fact ``REBATE_REJECTED`` is about,
    while ``validation_reason_code`` can also be carrying a qualification refusal that never
    reached the manufacturer.  Both stay in ``canonical`` either way, so nothing is lost by
    the preference — only ordered.
    """
    for name in ("rejection_reason", "validation_reason_code"):
        value = _text(canonical.get(name))
        if value is not None:
            return value, name
    return None, None


def pull(
    transport: "Transport",
    sources: "Sequence[Source]",
    *,
    source_id: str | None = None,
    should_fetch: "ShouldFetch | None" = None,
) -> InboundBatch:
    """Fetch Beacon's inbound documents and map them.  Requirement C3's first word, *"pulls"*.

    **Plural, and that is the whole reason this function exists rather than a one-line call
    to :func:`inbound_records`.**  A claim's inbound facts are spread across payload kinds —
    Beacon's validation verdict on one, the manufacturer's decision on another — and each
    payload kind is its own registry row, because a batch is per document and the four arrive
    on genuinely different cadences.  But the *merge* that turns those two payloads into one
    ``TPA_MANUFACTURER_DECISION`` is per **claim**, so it has to see both at once.

    Pull them one row at a time and the merge never happens: each row produces its own
    decision record for the same claim, both keyed on the same Beacon ID, and
    ``ux_norm_idempotency`` is unique on ``(record_kind, idempotency_key)``.  So the second
    one either collides or is dropped, and which of those you get depends on the write path
    rather than on anything anyone decided.  Taking a sequence is what makes the correct call
    the obvious one.

    Typed against the :class:`~recon.connectors.transport.Transport` protocol and nothing
    narrower, which is what lets the same mapping run over a loopback HTTP mock, over a
    directory of generated payload files, and over Beacon itself, with no edit here.  That is
    requirement A2's promise read from the vendor side: a document is a document regardless
    of how it arrived.

    ``should_fetch`` is passed straight through, unexamined.  It is the checkpoint seam
    (requirements A4 and A5), and a mapping module that inspected it would be a mapping
    module that could not be run without a checkpoint.

    Args:
        transport: anything satisfying the transport protocol.
        sources: the registry rows to pull, in the order their documents should be read.
            A bare :class:`~recon.connectors.registry.Source` is refused by name rather than
            wrapped, because a caller passing one has almost certainly written the per-row
            loop this function exists to prevent.
        source_id: what to attribute the batch to.  Defaults to the first row's id.  A merged
            record genuinely spans rows, so precise provenance rides on
            :attr:`InboundRecord.document` and ``line_no``, which is finer-grained than a
            source id anyway.
        should_fetch: the checkpoint predicate, passed through to every row.

    Raises:
        BeaconMappingError: no rows, a bare source, or rows declaring different mapping
            versions — a merged record cannot honestly claim two of those at once.
    """
    if hasattr(sources, "source_id"):
        raise BeaconMappingError(
            "pull() takes a sequence of registry rows, not one row. A Beacon claim's inbound "
            "facts are split across payload kinds and each kind is its own row, so pulling "
            "them one at a time produces two manufacturer decisions for one claim -- both "
            "keyed on the same Beacon ID, which the unique index on (record_kind, "
            "idempotency_key) then resolves by collision or by silence."
        )
    rows = tuple(sources)
    if not rows:
        raise BeaconMappingError(
            "pull() was given no registry rows, so it would return an empty batch that looks "
            "exactly like a vendor with nothing to say."
        )
    versions = {row.mapping_version for row in rows}
    if len(versions) > 1:
        raise BeaconMappingError(
            f"the rows {[row.source_id for row in rows]} declare mapping versions "
            f"{sorted(versions)}. Their payloads are merged into single records, and a record "
            "cannot honestly carry two mapping versions -- requirement 4.8 exists because one "
            "version claiming to describe two mappings is actively misleading."
        )
    documents: list[Document] = []
    for row in rows:
        documents.extend(transport.fetch(row, should_fetch=should_fetch))
    return inbound_records(
        documents,
        source_id=source_id or rows[0].source_id,
        mapping_version=versions.pop(),
        source_ids=tuple(row.source_id for row in rows),
    )


def inbound_records(
    documents: Iterable[Document],
    *,
    source_id: str,
    mapping_version: str,
    source_ids: tuple[str, ...] = (),
) -> InboundBatch:
    """Map fetched Beacon payloads onto the canonical record kinds.  Requirement C3.

    ``DOC2-007`` names four inbound things and this maps all four, onto two record kinds and
    one crosswalk key:

    ==========================  ====================================================
    ``acknowledgement``         an :class:`EpisodeBeaconId` — the key, not a record
    ``validation_outcome``      merged into ``TPA_MANUFACTURER_DECISION``
    ``rebate_status``           merged into ``TPA_MANUFACTURER_DECISION``
    ``payment_reference``       ``REBATE_BATCH``
    ==========================  ====================================================

    **The merge is the load-bearing decision.**  A validation outcome and a rebate status
    describe one claim from two sides, and mapping each to its own record would put *two*
    manufacturer decisions on a dispense the feed decided once — which the engine reads as a
    duplicate rebate.  So they are joined on the Beacon ID into one record whose idempotency
    key is that ID, and the two verdicts are kept as two fields inside it.  They are never
    collapsed into one: Beacon's validation verdict and the manufacturer's decision are
    different judgements by different parties, and an ``ACCEPTED`` validation on a claim the
    manufacturer later refused is a normal sequence, not a contradiction.

    An acknowledgement is deliberately **not** given a record kind.  A receipt is not a
    business event, and forcing one into ``TPA_MANUFACTURER_DECISION`` would invent a
    decision nobody made — with a timestamp, on a ledger, in a report.  What it produces is
    the ``BEACON_ID`` key everything else looks up, which is requirement C5.

    Order is document order, then line order, then first-seen Beacon ID for the merged
    records, so the same input produces the same batch.

    **All of a claim's documents must be in one call.**  The merge is keyed on the Beacon ID
    and lives in this function's locals, so splitting a pull across two calls produces two
    decision records for one claim.  :func:`pull` takes a sequence of registry rows for
    exactly that reason, and its docstring says what the split costs.
    """
    documents = tuple(documents)
    decisions: dict[str, dict[str, Any]] = {}
    batches: dict[str, dict[str, Any]] = {}
    beacon_ids: list[EpisodeBeaconId] = []
    parked: list[ParkedPayload] = []

    for document in documents:
        for line_no, payload in _payload_lines(document, parked):
            kind = _text(payload.get("payload_kind"))
            if kind == "acknowledgement":
                _collect_acknowledgement(payload, document.name, line_no, beacon_ids, parked)
            elif kind == "validation_outcome":
                _collect_validation_outcome(payload, document.name, line_no, decisions, parked)
            elif kind == "rebate_status":
                _collect_rebate_status(payload, document.name, line_no, decisions, parked)
            elif kind == "payment_reference":
                _collect_payment_reference(payload, document.name, line_no, batches, parked)
            else:
                parked.append(
                    ParkedPayload(
                        reason="UNKNOWN_PAYLOAD_KIND",
                        payload_kind=kind,
                        beacon_id=_text(payload.get("beacon_id")),
                        document=document.name,
                        line_no=line_no,
                        detail=(
                            f"{kind!r} is not an inbound Beacon payload kind; known kinds are "
                            f"{', '.join(beacon_payloads.PAYLOAD_KINDS)}. 'submission' is "
                            "outbound and is parked rather than mapped, because reading our "
                            "own request back as a response would report a claim as decided "
                            "by the fact that we sent it."
                        ),
                    )
                )

    records: list[InboundRecord] = []
    for beacon_id, collected in decisions.items():
        record = _decision_record(beacon_id, collected, source_id, mapping_version, parked)
        if record is not None:
            records.append(record)
    for reference, collected in batches.items():
        record = _batch_record(reference, collected, source_id, mapping_version, parked)
        if record is not None:
            records.append(record)

    return InboundBatch(
        source_id=source_id,
        records=tuple(records),
        beacon_ids=tuple(beacon_ids),
        parked=tuple(parked),
        documents=tuple(document.name for document in documents),
        source_ids=source_ids or (source_id,),
    )


def _payload_lines(
    document: Document, parked: list[ParkedPayload]
) -> Iterable[tuple[int, Mapping[str, Any]]]:
    """One JSON object per line, with the line number a reader can count to.

    A line that will not parse is parked rather than raised, matching
    :attr:`recon.connectors.vendors.ParsedDocument.declared_record_count`'s reasoning: a
    vendor sending something malformed is a record to park and a conversation to have, not a
    reason for the process to stop and lose the ninety-nine lines beside it.

    The offending text is **not** carried into the park detail.  A payload can hold a covered
    entity's claim data, and a park row is read in a terminal and pasted into a ticket.
    """
    lines: list[tuple[int, Mapping[str, Any]]] = []
    for line_no, line in enumerate(document.text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except ValueError as exc:
            parked.append(
                ParkedPayload(
                    reason="UNPARSEABLE_LINE",
                    payload_kind=None,
                    beacon_id=None,
                    document=document.name,
                    line_no=line_no,
                    detail=f"not JSON ({type(exc).__name__}); the line itself is not quoted "
                    "here because a payload carries claim data and a park row gets pasted "
                    "into tickets",
                )
            )
            continue
        if not isinstance(payload, dict):
            parked.append(
                ParkedPayload(
                    reason="UNPARSEABLE_LINE",
                    payload_kind=None,
                    beacon_id=None,
                    document=document.name,
                    line_no=line_no,
                    detail=f"JSON {type(payload).__name__}, expected an object",
                )
            )
            continue
        lines.append((line_no, payload))
    return lines


def _collect_acknowledgement(
    payload: Mapping[str, Any],
    document: str,
    line_no: int,
    beacon_ids: list[EpisodeBeaconId],
    parked: list[ParkedPayload],
) -> None:
    """One acknowledgement into a Beacon ID row, or parked if it names no claim."""
    try:
        beacon_ids.append(acknowledged(payload, document=document, line_no=line_no))
    except AcknowledgementError as exc:
        parked.append(
            ParkedPayload(
                reason="NO_BEACON_ID",
                payload_kind="acknowledgement",
                beacon_id=None,
                document=document,
                line_no=line_no,
                detail=str(exc),
            )
        )


def _collect_validation_outcome(
    payload: Mapping[str, Any],
    document: str,
    line_no: int,
    decisions: dict[str, dict[str, Any]],
    parked: list[ParkedPayload],
) -> None:
    """Beacon's validation verdict, filed under the Beacon ID it names.

    A ``PENDING`` outcome is parked rather than recorded.  It means nothing has been decided,
    and a record saying so would be a decision dated to the moment of the pull — which is
    both a clock reading this module is not entitled to (§4.11) and a verdict nobody gave.
    """
    beacon_id = _text(payload.get("beacon_id"))
    if beacon_id is None:
        parked.append(
            ParkedPayload(
                reason="NO_BEACON_ID",
                payload_kind="validation_outcome",
                beacon_id=None,
                document=document,
                line_no=line_no,
                detail="a validation outcome carries no natural key, so without a Beacon ID "
                "there is nothing to file it under -- requirement C5 is exactly this",
            )
        )
        return
    outcome = _text(payload.get("outcome"))
    if outcome == OUTCOME_PENDING or outcome is None:
        parked.append(
            ParkedPayload(
                reason="NOTHING_DECIDED_YET",
                payload_kind="validation_outcome",
                beacon_id=beacon_id,
                document=document,
                line_no=line_no,
                detail=f"outcome is {outcome!r}; a claim nobody has decided on has no "
                "decision to record, and writing one would date a verdict to the pull",
            )
        )
        return
    entry = decisions.setdefault(beacon_id, {})
    entry["template"] = entry.get("template") or _text(payload.get("template"))
    entry["validation_outcome"] = outcome
    # VERBATIM.  Requirement C3.  Not translated, not defaulted -- see `Rejection`.
    entry["validation_reason_code"] = _text(payload.get("reason_code"))
    entry["validation_outcome_source"] = _text(payload.get("outcome_source"))
    entry["validation_decided_at"] = _text(payload.get("decided_at"))
    entry["is_reversed"] = _REVERSAL.flagged({"outcome": outcome})
    entry.setdefault("document", document)
    entry.setdefault("line_no", line_no)


def _collect_rebate_status(
    payload: Mapping[str, Any],
    document: str,
    line_no: int,
    decisions: dict[str, dict[str, Any]],
    parked: list[ParkedPayload],
) -> None:
    """The manufacturer's decision and whether the money moved, filed under the Beacon ID.

    ``manufacturer_decision`` and ``rebate_state`` stay two fields.  An ``APPROVED`` rebate
    with no payment is a normal mid-flight state *and* the exact shape of a rebate that has
    gone missing, so one combined field would have to choose which it meant — and choosing
    is adjudicating.
    """
    beacon_id = _text(payload.get("beacon_id"))
    if beacon_id is None:
        parked.append(
            ParkedPayload(
                reason="NO_BEACON_ID",
                payload_kind="rebate_status",
                beacon_id=None,
                document=document,
                line_no=line_no,
                detail="a rebate status carries no natural key; the Beacon ID is the join",
            )
        )
        return
    decision = _text(payload.get("manufacturer_decision"))
    if decision is None:
        parked.append(
            ParkedPayload(
                reason="NOTHING_DECIDED_YET",
                payload_kind="rebate_status",
                beacon_id=beacon_id,
                document=document,
                line_no=line_no,
                detail="manufacturer_decision is absent, which is the manufacturer not having "
                "spoken rather than a decision of None",
            )
        )
        return
    entry = decisions.setdefault(beacon_id, {})
    entry["manufacturer"] = _text(payload.get("manufacturer"))
    entry["manufacturer_status"] = decision
    # VERBATIM.  Requirement C3.
    entry["rejection_reason"] = _text(payload.get("manufacturer_reason_code"))
    entry["manufacturer_decision_source"] = _text(payload.get("decision_source"))
    entry["rebate_state"] = _text(payload.get("rebate_state"))
    # Text, copied.  Nothing in this package turns an amount into a number.
    entry["rebate_amount"] = _text(payload.get("rebate_amount"))
    entry["as_of"] = _text(payload.get("as_of"))
    entry.setdefault("document", document)
    entry.setdefault("line_no", line_no)


def _collect_payment_reference(
    payload: Mapping[str, Any],
    document: str,
    line_no: int,
    batches: dict[str, dict[str, Any]],
    parked: list[ParkedPayload],
) -> None:
    """One rebate payment reference, folded into the batch it belongs to.

    Several dispenses share one payment, so several payloads carry the same
    ``payment_reference``.  They are folded into **one** ``REBATE_BATCH`` keyed on that
    reference, with one line per Beacon ID — which is the parent/child shape
    ``domain.enums.RecordKind`` already describes and the shape ``_adapt_rebate_batch``
    already produces from the 340B feed.  One payload per batch line would have created one
    batch per dispense, each carrying the whole batch total, and a ledger summing them would
    report the payment as many times as it had lines.
    """
    reference = _text(payload.get("payment_reference"))
    if reference is None:
        parked.append(
            ParkedPayload(
                reason="NO_PAYMENT_REFERENCE",
                payload_kind="payment_reference",
                beacon_id=_text(payload.get("beacon_id")),
                document=document,
                line_no=line_no,
                detail="a payment reference payload with no reference on it references "
                "nothing; recorded as a batch it would read as 'paid, details pending' to "
                "anything counting rows",
            )
        )
        return
    entry = batches.setdefault(
        reference,
        {
            "manufacturer": _text(payload.get("manufacturer")),
            "payment_effective_date": _text(payload.get("payment_effective_date")),
            # Copied from the payload, never summed from the lines below it.  A second copy
            # free to disagree with the first is the whole class of bug control totals exist
            # to catch.
            "batch_total_amount": _text(payload.get("batch_total_amount")),
            "batch_received_at": _text(payload.get("batch_received_at")),
            "lines": [],
            "document": document,
            "line_no": line_no,
        },
    )
    entry["lines"].append(
        {
            "beacon_id": _text(payload.get("beacon_id")),
            # Text, copied.
            "rebate_amount": _text(payload.get("rebate_amount")),
        }
    )


def _decision_record(
    beacon_id: str,
    collected: Mapping[str, Any],
    source_id: str,
    mapping_version: str,
    parked: list[ParkedPayload],
) -> InboundRecord | None:
    """One merged ``TPA_MANUFACTURER_DECISION``, or a park when it has no vendor timestamp.

    ``received_at`` is ``NOT NULL`` downstream and there is no clock in this module, so a
    payload that carried neither ``decided_at`` nor ``as_of`` cannot become a record here.
    Parked rather than stamped: a manufacturer's decision dated to the moment we polled lands
    in the wrong period, and a period is what a rebate cycle closes on.

    ``status_code`` prefers the manufacturer's verdict over Beacon's validation outcome,
    because ``REBATE_REJECTED`` is about the manufacturer refusing to pay.  Both verdicts stay
    in ``canonical`` regardless, so the preference orders them and loses neither.
    """
    received_at = collected.get("validation_decided_at") or collected.get("as_of")
    if received_at is None:
        parked.append(
            ParkedPayload(
                reason="NO_VENDOR_TIMESTAMP",
                payload_kind="validation_outcome",
                beacon_id=beacon_id,
                document=str(collected.get("document", "")),
                line_no=int(collected.get("line_no", 0)),
                detail="neither decided_at nor as_of was present. There is no clock in this "
                "package (requirement 4.11), and dating a manufacturer's decision to the "
                "moment of the pull puts it in the wrong period.",
            )
        )
        return None
    canonical = {
        "event_type": "MANUFACTURER_DECISION",
        "beacon_id": beacon_id,
        "template": collected.get("template"),
        "manufacturer": collected.get("manufacturer"),
        # -- the manufacturer's side --
        "manufacturer_status": collected.get("manufacturer_status"),
        "rejection_reason": collected.get("rejection_reason"),
        "manufacturer_decision_source": collected.get("manufacturer_decision_source"),
        "rebate_state": collected.get("rebate_state"),
        "rebate_amount": collected.get("rebate_amount"),
        "as_of": collected.get("as_of"),
        # -- Beacon's side.  Kept separate, never folded into the two above.  A dispense the
        # TPA never qualified is REJECTED here with a qualification reason and has no
        # manufacturer decision at all; collapsing them would invent one.
        "validation_outcome": collected.get("validation_outcome"),
        "validation_reason_code": collected.get("validation_reason_code"),
        "validation_outcome_source": collected.get("validation_outcome_source"),
        "validation_decided_at": collected.get("validation_decided_at"),
        "is_reversed": bool(collected.get("is_reversed", False)),
    }
    status_code = collected.get("manufacturer_status") or collected.get("validation_outcome")
    return InboundRecord(
        record_kind=RecordKind.TPA_MANUFACTURER_DECISION,
        # The member landed in wave 4, and requirement E5 is what made using it necessary
        # rather than merely possible.  This record carries a ``beacon_id``, and DOC2-004
        # gives Beacon sole authority over rebate submission identifiers -- so attributing it
        # to MANUFACTURER_REBATE now states that a non-Beacon source minted a Beacon ID.
        # ``connectors/authority.py`` refuses exactly that, and refused this record before
        # the line changed.
        source_system=SourceSystem.BEACON,
        received_at=str(received_at),
        # The Beacon ID, so re-pulling the same claim twice is one record and not two.  This
        # is the idempotency ``DOC2-002`` step 3 asks for, and it is available here precisely
        # because requirement C5 made the ID a first-class key.
        idempotency_key=beacon_id,
        mapping_version=mapping_version,
        source_id=source_id,
        canonical=canonical,
        # Looks the ID up; never publishes it.  ``EpisodeBeaconId`` is the one publisher --
        # see that class for why a second one would park every rebate as ambiguous.
        looks_up=(beacon_id_key(beacon_id),),
        beacon_id=beacon_id,
        amount=collected.get("rebate_amount"),
        status_code=status_code,
        document=collected.get("document"),
        line_no=collected.get("line_no"),
    )


def _batch_record(
    reference: str,
    collected: Mapping[str, Any],
    source_id: str,
    mapping_version: str,
    parked: list[ParkedPayload],
) -> InboundRecord | None:
    """One ``REBATE_BATCH`` per payment reference, with its Beacon IDs as lookups.

    This is requirement C5's acceptance made structural: *"a rebate payment carrying only a
    Beacon ID resolves to the correct episode."*  The batch carries no natural key at all —
    the payload does not have one — so every join it can make runs through
    :data:`KeyType.BEACON_ID`, which the acknowledgement published.

    ``ALLOCATION_CODE`` is deliberately **not** published from here, even though the value is
    the same string.  The 340B feed's own ``REBATE_PAYMENT_BATCH`` already publishes it, and
    two records publishing one ``(key_type, key_value)`` pair turn every bank deposit that
    looks it up into a two-hit the crosswalk parks as ``AMBIGUOUS_KEY_MATCH`` — so a second
    publisher would not produce a wrong answer, it would produce none, for every rebate
    deposit at once.  ``PAYMENT_REFERENCE`` is published instead: ``DOC2-002`` step 4 names
    it as its own identifier, and it is the manufacturer's reference rather than the payer's.
    """
    received_at = collected.get("batch_received_at")
    if received_at is None:
        parked.append(
            ParkedPayload(
                reason="NO_VENDOR_TIMESTAMP",
                payload_kind="payment_reference",
                beacon_id=None,
                document=str(collected.get("document", "")),
                line_no=int(collected.get("line_no", 0)),
                detail="batch_received_at is absent, and a settlement dated to the pull "
                "reconciles against the wrong period",
            )
        )
        return None
    lines: Sequence[Mapping[str, Any]] = collected.get("lines", ())
    looks_up = tuple(
        beacon_id_key(str(line["beacon_id"])) for line in lines if line.get("beacon_id")
    )
    canonical = {
        "event_type": "REBATE_PAYMENT_BATCH",
        "manufacturer": collected.get("manufacturer"),
        "payment_reference": reference,
        "payment_effective_date": collected.get("payment_effective_date"),
        # Text, copied from the payload.  Never summed from ``lines``.
        "batch_total_amount": collected.get("batch_total_amount"),
        "lines": [dict(line) for line in lines],
        # Counting rows is not arithmetic on money, and it is the only counting here.
        "dispense_line_count": len(lines),
    }
    return InboundRecord(
        record_kind=RecordKind.REBATE_BATCH,
        # Beacon, for the same reason as the decision record above: this batch is identified
        # by Beacon's own payment reference, which Beacon originates.
        source_system=SourceSystem.BEACON,
        received_at=str(received_at),
        idempotency_key=reference,
        mapping_version=mapping_version,
        source_id=source_id,
        canonical=canonical,
        looks_up=looks_up,
        self_keys=(payment_reference_key(reference),),
        payment_reference=reference,
        amount=collected.get("batch_total_amount"),
        document=collected.get("document"),
        line_no=collected.get("line_no"),
    )


def _text(value: object) -> str | None:
    """One payload value as text, with ``None`` and the empty string collapsed.

    The same rule :func:`recon.connectors.vendors.canonical` applies to a CSV cell, applied
    to a JSON field, so a Beacon payload and a Verity export cannot disagree about whether a
    field is missing.  ``json`` gives a real ``null`` where a CSV gives ``""``; letting the
    two mean different things here is how one leg reports a gap and the other reports a
    present-and-blank value for the same absent fact.
    """
    if value is None:
        return None
    text = str(value)
    return text or None
