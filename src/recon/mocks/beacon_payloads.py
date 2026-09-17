"""Beacon's five payload kinds, re-dressed from records the generators already wrote.

Beacon is the only **bidirectional** source in the whole assessment.  ``DOC2-007`` states
its behaviour verbatim: *"Direction — Outbound eligible pharmacy / medical claims; inbound
acknowledgements, validation outcomes, Beacon IDs, rebate status and reconciliation
data."*  That one sentence is this module's table of contents — one outbound kind and four
inbound ones, which is exactly the list requirement M5 names.

This is the payload layer only.  It writes files.  The HTTP surface that serves them is a
later wave, and keeping the two apart is deliberate: the data has to be inspectable on
disk, with no transport in existence, or the first thing anyone can assert on is a server
rather than a payload.

═══ The submission templates are Beacon's.  Every response here is still ours. ══════

Beacon's two data-template articles were read on 2026-09-17 and **their linked template
files downloaded** — ``Beacon - Pharmacy Claims Template.csv`` (11 columns) and ``Beacon -
Medical Claims Template.csv`` (16).  Both are saved byte-for-byte at
``docs/vendor_evidence/raw/templates/``.

So :func:`submission` carries **the column names of Beacon's own file**, in file order.

**Read the file, not the page — this cost us a rewrite.**  A direct fetch of either article
returns ``HTTP 403``, and a text-extraction proxy got past that far enough to render the
field-list *table*, which prints ``340B ID``, ``NDC-11``, ``Date of Service``.  Those
names were implemented here in good faith and every one of them is wrong: the header row
of the file Beacon actually parses is ``340b_id,date_prescribed,date_of_service,...``,
snake_case throughout.  A display label is written for a human reading a web page; a header
row is the contract.  They are allowed to differ, and here they do.

The page understated the medical template as well — it renders one row called *HCPCS Code
Modifier* where the file carries four columns, ``hcpcs_code_modifier_1`` through ``_4``.
That is not the page being wrong so much as a prose table rounding four columns down to one
concept, which is a fair description and an unusable spec.

One thing the correction gives back: ``ndc_11`` is character-identical to the spelling
Verity uses, so two of the three spellings this repository once carried for an NDC turn out
to be the same string.

**The four inbound payloads keep their invented snake_case names, and that asymmetry is
the finding.**  Beacon publishes submission *templates*.  It publishes no response shape
at all — ``BEACON-003`` (the validation code glossary) and ``BEACON-004`` (back-end
validations) are still ``HTTP 403``, and neither template article says anything about what
comes back.  So ``acknowledgement``, ``validation_outcome``, ``rebate_status`` and
``payment_reference`` stay ours and stay tagged ``INVENTED`` in ``MOCK_FIELDS.md``.
Dressing a response in published spellings would imply Beacon published a response shape
it did not, which is the same confident wrongness this module was written to avoid, only
better disguised.

**A published field we cannot populate is emitted as ``None``, never omitted.**  Beacon
marks ``Date Prescribed``, ``Fill Number``, ``Quantity Dispensed``, ``Rx Bin`` and
``Rx PCN`` required on every pharmacy row, and the 340B sidecar carries none of them.  A
null in a published field is an honest *we do not have this*; dropping the field hides the
gap behind a template that looks complete.  In particular ``submission_date`` is **not**
mapped onto ``Date Prescribed``: Beacon defines that as *"Date the prescriber wrote the
prescription"* and ours is the date a rebate was requested, which is a different fact
wearing a plausible name.

The envelope — ``payload_kind``, ``direction``, ``template``, ``manufacturer``,
``submission_date``, ``qualification_status`` — stays snake_case on the submission too.
Those six are ours, Beacon names none of them, and leaving them in our own spelling is
what lets a reader tell at a glance which keys on that payload are Beacon's.

═══ Nothing here adjudicates ═══════════════════════════════════════════════════════

Requirement M3.  Beacon's acknowledgement, validation outcome and rebate status are
already in the generated data — as ``QUALIFICATION_DECISION`` and ``MANUFACTURER_DECISION``
events, ``DISPENSE_REVERSAL`` events and ``REBATE_PAYMENT_BATCH`` lines.  This module
replays them under Beacon's field names.  It decides nothing.

Two consequences are load-bearing:

1. **A rejection reason is carried across as text, byte for byte.**  Requirement C3 says
   the vendor's reason is preserved "verbatim, not paraphrased," and there is a second
   reason here beyond fidelity: ``BEACON-003``, the page that would have given Beacon's
   real code vocabulary, is one of the 403s.  Mapping ``NON_CONFORMING_45_DAY`` onto a
   prettier Beacon-looking code would be inventing a vocabulary and then hiding the
   invention behind a plausible spelling.  The feed's own word travels instead.
2. **Every outcome names the record it was read from.**  ``outcome_source`` on a
   validation outcome is the ``event_type`` the decision came from, so any response can be
   walked back to a line in ``tpa_340b_events.jsonl``.  That is what makes M3's acceptance
   — "every response traces back to a generated record" — checkable rather than asserted.

The 45-day submission window (``BEACON-009``, corroborated first-hand by ``VERITY-003``)
is the best-supported Beacon fact we hold, and this module deliberately does **not**
compute it.  It is already adjudicated upstream: a submission that missed it arrives here
as a ``MANUFACTURER_DECISION`` carrying ``NON_CONFORMING_45_DAY``, and that string is
replayed.  Re-deriving the window from ``fill_date`` and ``submission_date`` would be this
module forming its own opinion about timeliness, which is the one thing M3 forbids — and
it could then disagree with the feed.

═══ The Beacon ID is received, never minted ════════════════════════════════════════

Requirement M1's acceptance is literal: a Beacon ID in a mock response is the same string
the orchestrator minted.  So ``dispense.beacon_id`` is copied, and it appears **only on
inbound payloads**.  The outbound submission carries no Beacon ID at all, because Beacon
is the one who assigns it — a submission that already knew its own Beacon ID would quietly
delete the acknowledgement's only reason to exist, and with it the C2 acceptance test that
a golden claim "submits, receives a Beacon ID, and that ID appears on the episode
timeline."

What correlates a submission to its acknowledgement instead is the natural 340B key, which
is genuinely all a 340B connector has: ``tpa_340b_events.jsonl`` has no shared identifier
space, which is the reason "unmatched rebate" is one of the assignment's named exceptions.
Identifier drift is carried across untouched for the same reason — a mock that repaired a
drifted ``rx_number`` would hand the connector a join the real feed does not have.

═══ Pharmacy and medical are two different templates ═══════════════════════════════

``BEACON-001`` and ``BEACON-002`` are separate articles with different field lists — 11
fields against 13, overlapping on only three (``340B ID``, ``Date of Service``, ``NDC-11``)
— so Beacon separates them and so does this.  A dispense with an ``rx_number``/
``pharmacy_npi`` is pharmacy; one carrying a ``provider_npi`` instead is a
medically-administered drug with no prescription to key on.

``BEACON-008`` — a search engine's summary of the medical template — is now **superseded**
by ``BEACON-002``, the page itself.  Its guess was close and not right: the real medical
template does name a claim number, a claim line number, a date of service, an NDC, a
service provider id and a HCPCS modifier, but it also requires a health plan name, a health
plan id, a quantity, a unit of measure and a *second* NPI the summary never mentioned.
Nine of the thirteen medical fields are emitted as ``None``, which is the honest state.

**The medical template asks for two NPIs and we hold one.**  ``Rendering Physician ID`` is
*"the NPI of the healthcare provider who rendered or supervised the care"*; ``Service
Provider ID`` is *"the NPI of the healthcare entity where the patient received the
medication administration"* — a clinician and a facility.  The 340B sidecar carries one
``provider_npi`` and cannot say which it is.  It is mapped to ``Service Provider ID``,
which is what the sidecar's own field means, and ``Rendering Physician ID`` goes out null.
Copying the one value into both would manufacture a second fact out of the first.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from recon.config import Settings
from recon.mocks.source import Dispense, VendorSource

__all__ = [
    "PAYLOAD_KINDS",
    "VALIDATION_OUTCOMES",
    "BEACON_SUBDIR",
    "TEMPLATE_PHARMACY",
    "TEMPLATE_MEDICAL",
    "PHARMACY_TEMPLATE_FIELDS",
    "MEDICAL_TEMPLATE_FIELDS",
    "FIELD_340B_ID",
    "FIELD_DATE_OF_SERVICE",
    "FIELD_NDC_11",
    "FIELD_RX_NUMBER",
    "FIELD_SERVICE_PROVIDER_ID",
    "submission",
    "acknowledgement",
    "validation_outcome",
    "rebate_status",
    "payment_reference",
    "render",
    "write",
]

#: The five payload kinds requirement M5 names for Beacon, in the order ``DOC2-007``
#: introduces them: one outbound, then the four inbound ones.
PAYLOAD_KINDS: tuple[str, ...] = (
    "submission",
    "acknowledgement",
    "validation_outcome",
    "rebate_status",
    "payment_reference",
)

#: Every value ``validation_outcome`` can put in its ``outcome`` field.
#:
#: Four values, and each one is a state the feed already holds rather than a judgement made
#: here — ``REVERSED`` is a ``DISPENSE_REVERSAL`` event existing, ``REJECTED`` is a
#: ``NOT_QUALIFIED`` qualification or a ``REJECTED`` manufacturer decision, ``ACCEPTED`` is
#: an ``APPROVED`` manufacturer status (on its own event, or on the payment-batch line that
#: replaces it when the rebate was paid), and ``PENDING`` is the absence of all of them.
#: Requirement M3's acceptance is that this set equals the set already in the feed, so a
#: fifth value may only be added here when the generator can actually produce it.
VALIDATION_OUTCOMES: tuple[str, ...] = ("ACCEPTED", "REJECTED", "REVERSED", "PENDING")

#: ``data/generated/<profile>/vendor/beacon/``.  A directory per vendor, so a reviewer can
#: see at a glance which files are Beacon's claim about the world and which are Verity's.
BEACON_SUBDIR = "beacon"

#: JSONL rather than CSV, unlike the file-delivered vendors.  Beacon is an API
#: (``BEACON-010``: an SDK is the intended submission path), so its payloads are objects
#: with nested nulls and no natural column order, and flattening them to CSV would invent a
#: shape the transport never has.
_LINE_ENDING = "\n"


# ═══ Beacon's two published templates, verbatim ═════════════════════════════════════
#
# Field names below are Beacon's, copied character for character from ``BEACON-001`` and
# ``BEACON-002`` (``docs/vendor_evidence/raw/beacon_pharmacy_template.md`` and
# ``beacon_medical_template.md``).  Capitalisation, the spaces in ``340B ID`` and ``Rx
# Bin``, and the hyphen in ``NDC-11`` are all reproduced rather than normalised.  Order is
# the order the articles print, top to bottom, because that order is the one piece of
# structure a reader of the file can use and re-sorting it would scramble it.

#: The two template names.  **Ours** — Beacon publishes two articles, not two enum members.
TEMPLATE_PHARMACY = "PHARMACY"
TEMPLATE_MEDICAL = "MEDICAL"

#: The five published names another module has to spell.  Named individually so that
#: :func:`recon.mocks.beacon_server._submission_key`, which is the exact inverse of
#: :func:`submission`, reads the same string this module writes rather than a copy of it —
#: a copy is how a rename here turns every ``POST /claims`` into ``404 unknown_claim``
#: without a single test going red.
#:
#: **These are the column names of Beacon's own template file, not the labels on its help
#: page.**  An earlier version of this block spelled them ``"340B ID"``, ``"NDC-11"``,
#: ``"Date of Service"`` — taken from the field-list table the article renders, which was
#: all a text-extraction proxy could reach past the site's HTTP 403.  The article offers a
#: download, and the download disagrees: the file's header row is snake_case throughout.
#: A display label is written for a human reading a web page; a header row is what Beacon
#: parses.  Saved first-hand at ``docs/vendor_evidence/raw/templates/``.
FIELD_340B_ID = "340b_id"
FIELD_DATE_OF_SERVICE = "date_of_service"
FIELD_NDC_11 = "ndc_11"
FIELD_RX_NUMBER = "rx_number"
FIELD_SERVICE_PROVIDER_ID = "service_provider_id"

#: ``Beacon - Pharmacy Claims Template.csv``, all 11 columns in file order.  Every one of
#: them carries Beacon's required marker on the article's field list.
#:
#: ``ndc_11`` is worth noticing: it is character-identical to the spelling Verity uses for
#: the same fact, so two of the three spellings this repository once carried for an NDC turn
#: out to be one.
PHARMACY_TEMPLATE_FIELDS: tuple[str, ...] = (
    FIELD_340B_ID,
    "date_prescribed",
    FIELD_DATE_OF_SERVICE,
    FIELD_RX_NUMBER,
    "fill_number",
    FIELD_NDC_11,
    "quantity_dispensed",
    "prescriber_id",
    FIELD_SERVICE_PROVIDER_ID,
    "rx_bin",
    "rx_pcn",
)

#: ``Beacon - Medical Claims Template.csv``, all 16 columns in file order.
#:
#: **Sixteen, not the thirteen the article's field list shows.**  The page prints one row
#: called *HCPCS Code Modifier*; the file carries four columns, ``hcpcs_code_modifier_1``
#: through ``_4``.  That settles a question recorded here as unanswerable — *"Beacon never
#: says how four modifiers fit in one field"* — and the answer is that they do not fit in
#: one field, because there are four fields.  It is also the sharpest argument for reading
#: the file rather than the page: a prose table can round four columns down to one concept
#: and still be a fair description, and a parser cannot.
MEDICAL_TEMPLATE_FIELDS: tuple[str, ...] = (
    FIELD_340B_ID,
    "claim_number",
    "claim_line_number",
    FIELD_DATE_OF_SERVICE,
    "hcpcs_code",
    "hcpcs_code_modifier_1",
    "hcpcs_code_modifier_2",
    "hcpcs_code_modifier_3",
    "hcpcs_code_modifier_4",
    "health_plan_name",
    "health_plan_id",
    FIELD_NDC_11,
    "rendering_physician_id",
    "quantity",
    "unit_of_measure",
    FIELD_SERVICE_PROVIDER_ID,
)


# ═══ template selection ═════════════════════════════════════════════════════════════


def _template(dispense: Dispense) -> str:
    """``PHARMACY`` or ``MEDICAL`` — a classification of what the record already is.

    Reads the same null pattern ``tpa.py`` writes: a medically-administered drug has no
    prescription, so ``rx_number`` and ``pharmacy_npi`` are both null and ``provider_npi``
    carries the identity instead.  This looks at the pharmacy pair first, so a record
    carrying both stays pharmacy rather than silently changing template.
    """
    if dispense.rx_number is not None or dispense.pharmacy_npi is not None:
        return TEMPLATE_PHARMACY
    return TEMPLATE_MEDICAL


# ═══ reading the decisions back ═════════════════════════════════════════════════════


def _manufacturer_decision(dispense: Dispense) -> tuple[str | None, str | None, str | None]:
    """The manufacturer's verdict, its reason, and the record both were read from.

    Two places can hold it, and missing the second is a real bug rather than a subtlety.
    ``MANUFACTURER_DECISION`` is only emitted when the outcome is ``REJECTED``, or when it
    is ``APPROVED`` and no payment ever followed; an approved-and-paid dispense carries its
    ``manufacturer_status`` on the ``REBATE_PAYMENT_BATCH`` line instead.  Reading only the
    event would report every paid rebate as undecided — the feed plainly says ``APPROVED``
    on the line, and the mock would be contradicting it.
    """
    record = dispense.event("MANUFACTURER_DECISION")
    if record is not None:
        return record.get("manufacturer_status"), record.get("rejection_reason"), "MANUFACTURER_DECISION"
    if dispense.payment_line is not None:
        return dispense.payment_line.get("manufacturer_status"), None, "REBATE_PAYMENT_BATCH"
    return None, None, None


def _outcome(dispense: Dispense) -> tuple[str, str | None, str | None, str | None]:
    """``(outcome, reason_code, outcome_source, decided_at)`` — all four read, none decided.

    The precedence follows :meth:`recon.mocks.source.Dispense.archetype` — reversal first,
    then either gate's rejection, then approval — but the two **deliberately disagree on one
    population**, and that is worth stating precisely because an earlier version of this
    docstring claimed they matched "exactly" and they never did.

    ``archetype`` reads the ``MANUFACTURER_DECISION`` event only.  This function also reads
    the payment line, via :func:`_manufacturer_decision`, because ``orchestrator.py`` emits a
    decision record **only** when the outcome is ``REJECTED``, or ``APPROVED`` with no
    payment.  So every paid dispense has no decision event at all: reading the event alone
    reports ``PENDING`` for a rebate the manufacturer demonstrably paid, which would
    contradict the feed rather than replay it.

    The measured consequence is that an approved-but-unpaid dispense is ``unmatched`` to the
    coverage report and ``ACCEPTED`` here — 1 record of 30 on ``demo``, 98 of 1395 on
    ``full``.  Both sides are field reads and neither adjudicates, so requirement M3 holds;
    the two answers are to two different questions.  ``archetype`` asks *did the money
    arrive*, which is what a coverage count needs.  This asks *what did the manufacturer
    say*, which is what a Beacon validation outcome means.
    """
    reversal = dispense.event("DISPENSE_REVERSAL")
    if reversal is not None:
        return (
            "REVERSED",
            reversal.get("reversal_reason"),
            "DISPENSE_REVERSAL",
            reversal.get("received_at"),
        )

    qualification = dispense.event("QUALIFICATION_DECISION")
    if dispense.qualification_status == "NOT_QUALIFIED":
        return (
            "REJECTED",
            dispense.disqualification_reason,
            "QUALIFICATION_DECISION",
            None if qualification is None else qualification.get("received_at"),
        )

    status, reason, origin = _manufacturer_decision(dispense)
    if status == "REJECTED":
        return "REJECTED", reason, origin, _decided_at(dispense, origin)
    if status == "APPROVED":
        return "ACCEPTED", reason, origin, _decided_at(dispense, origin)

    # Nothing has spoken yet.  A qualification row with a null status is a dispense sitting
    # in review, and it is a different state from one that was never submitted — both land
    # here, and neither is an outcome this module is entitled to resolve.
    return "PENDING", None, None, None


def _decided_at(dispense: Dispense, origin: str | None) -> str | None:
    """When the record that carried the decision arrived, as that record itself says.

    Requirement C1 is deterministic and seeded, so there is no clock in this module at all.
    Every timestamp on every payload is a ``received_at`` lifted off a feed record, which
    also makes the timestamps honest: they say when the TPA or the manufacturer spoke, not
    when the mock was run.
    """
    if origin == "REBATE_PAYMENT_BATCH":
        return None if dispense.payment_batch is None else dispense.payment_batch.get("received_at")
    if origin is None:
        return None
    record = dispense.event(origin)
    return None if record is None else record.get("received_at")


def _first_received_at(dispense: Dispense) -> str | None:
    """The earliest thing the TPA said about this dispense.

    ``load_source`` sorts a dispense's events by ``received_at``, so the first element is
    the earliest without this function needing to compare anything.
    """
    for record in dispense.events:
        value = record.get("received_at")
        if value:
            return value
    return None


def _natural_key_fields(dispense: Dispense) -> dict[str, Any]:
    """The 340B natural key, spelled Beacon-side, drift included.

    This is the correlation handle between an outbound submission and every inbound
    response, because 340B has no shared identifier space to use instead.  The values are
    copied exactly as the TPA feed spells them: defect D-6 drifts an identifier in one feed
    per episode, and repairing it here would delete the crosswalk-miss exception that drift
    exists to produce.
    """
    return {
        "rx_number": dispense.rx_number,
        "pharmacy_npi": dispense.pharmacy_npi,
        "provider_npi": dispense.provider_npi,
        "ndc_11": dispense.ndc_11,
        "date_of_service": dispense.fill_date,
    }


# ═══ the five payload kinds ═════════════════════════════════════════════════════════


def submission(dispense: Dispense) -> dict[str, Any]:
    """Outbound: one 340B-qualified claim, in Beacon's published pharmacy or medical template.

    The template block below is :data:`PHARMACY_TEMPLATE_FIELDS` or
    :data:`MEDICAL_TEMPLATE_FIELDS` — every published field, in published order, under
    Beacon's own spelling.  A field the 340B sidecar cannot populate is ``None`` rather than
    absent, so the gap travels on the wire where somebody can close it.

    The six envelope keys in front of it are snake_case and are **ours**: Beacon names none
    of them.  The mixed casing on one object is deliberate and is the cheapest way to see
    which half of a submission is a vendor's contract and which half is our plumbing.

    Carries no ``beacon_id``.  Beacon assigns that on receipt, and a request that already
    held one would make the acknowledgement decorative.

    ``qualification_status`` rides along because ``DOC2-007`` says the outbound direction
    is *eligible* claims — the TPA's own eligibility verdict is the reason the claim is
    being sent at all — and it is carried as the feed's word, not re-decided here.

    ``340B ID`` is the only key on this payload that changed *owner* rather than spelling.
    It used to be our envelope field ``covered_entity_id``; ``BEACON-001`` and
    ``BEACON-002`` both open with ``340B ID``, so it is Beacon's field now and it moved into
    the template block where Beacon prints it.
    """
    template = _template(dispense)
    qualification = dispense.event("QUALIFICATION_DECISION")
    payload: dict[str, Any] = {
        "payload_kind": "submission",
        "direction": "OUTBOUND",
        "template": template,
        "manufacturer": dispense.manufacturer,
        # The rebate request's own date.  NOT Beacon's ``Date Prescribed``: that is the day
        # the prescriber wrote the prescription, and we do not hold it.  Keeping this under
        # our own name is what stops the two being confused by the next reader.
        "submission_date": dispense.submitted_at,
        "qualification_status": dispense.qualification_status,
    }

    if template == TEMPLATE_PHARMACY:
        payload.update(
            {
                FIELD_340B_ID: dispense.covered_entity_id,
                # Beacon: "Date the prescriber wrote the prescription."  The 340B feed
                # carries the fill, never the writing, so this is genuinely absent.
                "date_prescribed": None,
                # Beacon: "Date on which the pharmacy filled the prescription" -- which is
                # exactly what ``fill_date`` is.  Wire form (CCYYMMDD), as the feed spells
                # it; Beacon says only "Standard date formats" and never enumerates them.
                FIELD_DATE_OF_SERVICE: dispense.fill_date,
                FIELD_RX_NUMBER: dispense.rx_number,
                # The TPA export has no fill number -- a rebate is about which drug was
                # bought, not which refill it was.  ``keys.natural_340b_pharmacy`` records
                # the same gap from the crosswalk side.
                "fill_number": None,
                FIELD_NDC_11: dispense.ndc_11,
                # Not reachable from here, and the near-miss is worth naming because it is
                # sitting right there: ``quantity_dispensed`` exists on the 340B feed on
                # ``DISPENSE_REVERSAL`` events **only**, where it is the *negative* quantity
                # being returned to stock.  It is absent from the qualification and rebate
                # request events that describe the dispense itself.  So a dispensed quantity
                # would have to come from a reversal that most dispenses never had, with its
                # sign flipped -- which is deriving a fact, not reading one.  The connector's
                # copy of this template does populate the field, from
                # ``normalized_record.quantity_milli``; this formatter reads the sidecar and
                # the sidecar does not carry it.
                "quantity_dispensed": None,
                # Beacon: "NPI of the physician that wrote the prescription."
                "prescriber_id": None if qualification is None else qualification.get("prescriber_npi"),
                # Beacon: "NPI of the pharmacy that filled the prescription."
                FIELD_SERVICE_PROVIDER_ID: dispense.pharmacy_npi,
                # **Beacon's published sentinels are refused.**  BEACON-001 says to mark
                # "999999" in Rx Bin and "CASH" in Rx PCN when the patient is uninsured or a
                # cash payer, and "NONE" in Rx PCN when there is no PCN.  Writing any of them
                # would assert a clinical fact -- that this patient paid cash -- that we have
                # no basis for.  The 340B sidecar carries no payer at all, which is a
                # different state from "the payer is cash".  Null is that difference, and a
                # plausible-looking placeholder is the exact failure the provenance tiers in
                # MOCK_FIELDS.md exist to prevent.
                "rx_bin": None,
                "rx_pcn": None,
            }
        )
    else:
        payload.update(
            {
                FIELD_340B_ID: dispense.covered_entity_id,
                # Both live on the 837 feed and never reach the 340B sidecar.
                "claim_number": None,
                "claim_line_number": None,
                # Beacon: "Date on which the medication was administered to the patient."
                FIELD_DATE_OF_SERVICE: dispense.fill_date,
                # The J-code reaches an episode by a reference lookup on the NDC, and this
                # sidecar row carries neither the episode nor a resolved drug -- so the
                # value is unavailable *at this point* rather than non-existent.  E2 landed
                # in wave 5; populating it here would still mean a formatter reaching into
                # reference data to derive a field, which is the one thing it may not do.
                "hcpcs_code": None,
                # **Four modifier columns, not one.**  The article's field list prints a
                # single row called *HCPCS Code Modifier*; the template file it links to
                # carries ``hcpcs_code_modifier_1`` through ``_4``.  An earlier note here
                # recorded "Beacon never says how four modifiers fit in one field" as an
                # open question -- they do not fit in one, and the page could not say so
                # because the page is a description and the file is the contract.
                "hcpcs_code_modifier_1": None,
                "hcpcs_code_modifier_2": None,
                "hcpcs_code_modifier_3": None,
                "hcpcs_code_modifier_4": None,
                # Beacon publishes the same CASH/NONE sentinels on both of these, and they
                # are refused for the same reason as Rx Bin and Rx PCN above: we hold no
                # payer at all, which is not the same fact as "the payer is cash".
                "health_plan_name": None,
                "health_plan_id": None,
                FIELD_NDC_11: dispense.ndc_11,
                # The clinician.  We hold one NPI and it is the site's -- see the module
                # docstring.  Copying ``provider_npi`` into both would invent a second fact.
                "rendering_physician_id": None,
                # **Null even though the pharmacy template's quantity is not**, and the two
                # are genuinely different decisions.  BEACON-002 conditions this number's
                # meaning on row 5: with a specific HCPCS code it must be that code's
                # CMS-defined billable units, and without one it must be NCPDP standardized
                # billing units for the NDC-11.  Beacon publishes neither table.  Our own
                # ``UnitBasis`` vocabulary is EACH / ML / MG, which is not that vocabulary at
                # all, so any number put here would be in a unit Beacon did not ask for --
                # and a wrong quantity on a rebate submission is worse than a null, because
                # a null is visibly missing and a wrong number is silently paid.
                "quantity": None,
                "unit_of_measure": None,
                # Beacon: "the NPI of the healthcare entity where the patient received the
                # medication administration" -- the site, which is what ``provider_npi`` is.
                FIELD_SERVICE_PROVIDER_ID: dispense.provider_npi,
            }
        )
    return payload


def acknowledgement(dispense: Dispense) -> dict[str, Any]:
    """Inbound: receipt, carrying the Beacon ID.

    The one payload whose whole content is the identifier.  ``DOC2-007`` lists
    acknowledgements and Beacon IDs as separate inbound items, and ``BEACON-013``'s
    recommended design — *"persist Beacon ID against Shields Claim Financial Episode"* — is
    what the connector does with this response.

    ``status`` is a constant, and deliberately so: an acknowledgement means *received*, not
    *accepted*.  Every validation verdict is in ``validation_outcome``, where it can be
    traced to the record that decided it.  Collapsing the two would make receipt look like
    approval, which is the mistake a rebate operator pays for weeks later.
    """
    return {
        "payload_kind": "acknowledgement",
        "direction": "INBOUND",
        "beacon_id": dispense.beacon_id,
        "status": "RECEIVED",
        "template": _template(dispense),
        "covered_entity_id": dispense.covered_entity_id,
        "received_at": _first_received_at(dispense),
        **_natural_key_fields(dispense),
    }


def validation_outcome(dispense: Dispense) -> dict[str, Any]:
    """Inbound: accepted or rejected, with the reason the feed already gave.

    ``reason_code`` is the feed's own string — ``PRESCRIBER_NOT_AFFILIATED``,
    ``NON_CONFORMING_45_DAY``, ``RETURN_TO_STOCK`` — carried verbatim under requirement C3.
    It is **not** a Beacon validation code, and it is not dressed up as one, because
    ``BEACON-003`` is a 403 and we do not know what Beacon's codes look like.  A connector
    reading this field is reading our TPA's vocabulary, which is the truthful thing for it
    to be reading until somebody hands us Beacon's.

    ``outcome_source`` names the ``event_type`` the verdict came from, so every response
    here walks back to one line of ``tpa_340b_events.jsonl``.
    """
    outcome, reason_code, outcome_source, decided_at = _outcome(dispense)
    return {
        "payload_kind": "validation_outcome",
        "direction": "INBOUND",
        "beacon_id": dispense.beacon_id,
        "template": _template(dispense),
        "outcome": outcome,
        "reason_code": reason_code,
        "outcome_source": outcome_source,
        "decided_at": decided_at,
    }


def rebate_status(dispense: Dispense) -> dict[str, Any]:
    """Inbound: the manufacturer's decision and whether the rebate has settled.

    Two independent facts, kept in two fields on purpose.  ``manufacturer_decision`` is the
    manufacturer's verdict; ``rebate_state`` is whether money has actually moved.  They
    come apart in real 340B administration — an ``APPROVED`` rebate with no payment batch
    is a normal mid-flight state, and it is also exactly the shape of a rebate that has
    gone missing.  One combined field would have to choose which of those it meant, and
    choosing would be adjudicating.

    ``rebate_amount`` is the payment batch's own text, copied.  Requirement M2 forbids
    arithmetic on money here for a concrete reason: a mock that could compute an amount
    could disagree with the ledger, and the reconciliation this data exists to exercise
    would then be measuring the mock.
    """
    status, reason, origin = _manufacturer_decision(dispense)
    return {
        "payload_kind": "rebate_status",
        "direction": "INBOUND",
        "beacon_id": dispense.beacon_id,
        "manufacturer": dispense.manufacturer,
        "manufacturer_decision": status,
        "manufacturer_reason_code": reason,
        "decision_source": origin,
        # Present or absent, read off the join — never inferred from the decision.  An
        # APPROVED dispense with no payment line is the missing-rebate exception, and
        # reporting it as PAID would erase the exception the dataset was built to produce.
        "rebate_state": "PAID" if dispense.is_paid else "UNPAID",
        "rebate_amount": dispense.rebate_amount,
        "as_of": _decided_at(dispense, origin),
    }


def payment_reference(dispense: Dispense) -> dict[str, Any] | None:
    """Inbound: the reference that ties this rebate to the settlement on the bank feed.

    ``None`` when no payment batch covers the dispense, rather than a payload with empty
    fields.  A reference row that exists but references nothing is worse than no row: it
    reads as "paid, details pending" to anything counting rows.

    ``payment_reference`` is the batch's ``allocation_code``, which is the same value the
    manufacturer's ACH credit carries in ``trn02`` — so this payload is the whole join from
    a Beacon rebate to a line on ``bank_transactions.csv``.  It is also the join that
    breaks: the CCD+ addenda carrying ``trn02`` is dropped on roughly a fifth of deposits,
    and when it is gone all that is left to match on is amount and date.  That is
    requirement E3's reason to exist, and it is a property of the data rather than an
    annotation this module adds.
    """
    if dispense.payment_line is None or dispense.payment_batch is None:
        return None
    batch = dispense.payment_batch
    return {
        "payload_kind": "payment_reference",
        "direction": "INBOUND",
        "beacon_id": dispense.beacon_id,
        "manufacturer": dispense.manufacturer,
        "payment_reference": dispense.allocation_code,
        "payment_effective_date": dispense.payment_effective_date,
        "rebate_amount": dispense.rebate_amount,
        # Copied from the batch record, not summed from its lines.  The batch already
        # formatted this figure once; a second copy free to disagree with the first is the
        # whole class of bug control totals exist to catch.
        "batch_total_amount": batch.get("total_rebate_amount"),
        "batch_received_at": batch.get("received_at"),
    }


# ═══ rendering and writing ══════════════════════════════════════════════════════════


def render(source: VendorSource) -> dict[str, str]:
    """Every payload kind as JSONL text, keyed by kind.  Nothing is opened here.

    The same division :mod:`recon.mocks.source` sets up: formatters return text and never
    touch the filesystem, so every one of them can be asserted on without a temporary
    directory in sight.

    Dispense order is the sidecar's order, which is the orchestrator's episode order, so
    the same seed produces byte-identical files.  ``payment_reference`` is the one kind
    with fewer rows than there are dispenses — an unpaid dispense has no reference, and
    padding the file to a uniform row count would report settlements that never happened.
    """
    lines: dict[str, list[str]] = {kind: [] for kind in PAYLOAD_KINDS}
    for dispense in source.dispenses:
        lines["submission"].append(_dumps(submission(dispense)))
        lines["acknowledgement"].append(_dumps(acknowledgement(dispense)))
        lines["validation_outcome"].append(_dumps(validation_outcome(dispense)))
        lines["rebate_status"].append(_dumps(rebate_status(dispense)))
        reference = payment_reference(dispense)
        if reference is not None:
            lines["payment_reference"].append(_dumps(reference))
    return {
        kind: "".join(line + _LINE_ENDING for line in rows) for kind, rows in lines.items()
    }


def _dumps(payload: dict[str, Any]) -> str:
    """One JSON object, one line.

    Key order is the dict's insertion order rather than sorted, because the insertion order
    is the template's own field order and re-sorting it would scramble the one bit of
    structure a reader can use.  It is just as deterministic either way.
    """
    return json.dumps(payload, ensure_ascii=False)


def write(settings: Settings, source: VendorSource) -> dict[str, Path]:
    """Write the five files under ``data/generated/<profile>/vendor/beacon/``.

    ``vendor/`` and not ``feeds/``: the six feeds are hashed, pinned and read by ingestion,
    and a seventh file appearing among them would travel into places that have nothing to
    do with Beacon.  A sibling directory keeps the feeds byte-identical.

    ``\\n`` line endings are forced rather than left to the platform, for the same reason
    ``bank.py`` forces them — otherwise one seed produces two different file hashes on two
    different machines and the reproducibility claim quietly stops being true.
    """
    directory = settings.vendor_dir() / BEACON_SUBDIR
    directory.mkdir(parents=True, exist_ok=True)

    written: dict[str, Path] = {}
    for kind, text in render(source).items():
        path = directory / f"{kind}.jsonl"
        with path.open("w", encoding="utf-8", newline=_LINE_ENDING) as handle:
            handle.write(text)
        written[kind] = path
    return written
