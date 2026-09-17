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

═══ Almost every field name here is invented, and that is the finding ═══════════════

Beacon's pharmacy claim template, medical claim template, validation code glossary and
back-end validation pages are real, public, and every one of them returns ``HTTP 403`` to
automated retrieval (``BEACON-001`` through ``BEACON-007``).  We did not read them.

So we do not know a single Beacon field name, the validation code vocabulary, or the shape
of a Beacon ID.  Writing that field list from recollection would produce a mock that is
**confidently wrong** — indistinguishable from a correct one in any demo, and wrong in
precisely the places a real integration would break.  The names below are therefore ours,
tagged ``INVENTED`` in ``MOCK_FIELDS.md``; the handful that are not ours are tagged to the
public standard (``STD-X12-837``, ``STD-NCPDP-TELECOM``) or the Doc 2 page that carries
them.  A reader who wants to know what Beacon actually said should read the tier column,
and will find it says very little.

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

``BEACON-001`` and ``BEACON-002`` are separate articles, so Beacon separates them too.  A
dispense with an ``rx_number``/``pharmacy_npi`` is pharmacy; one carrying a ``provider_npi``
instead is a medically-administered drug with no prescription to key on.

``BEACON-008`` — a search engine's summary of the medical template, **not** the page —
suggests the medical key combines claim number, claim line number, date of service, NDC,
service provider id and HCPCS modifier code.  It is used here as a design hint and nothing
more: every field taken from it is ``INVENTED``, because the ``SPEC`` tier requires a cited
vendor source and a search summary is not one.  Four of those six are emitted as ``None``,
which is the honest state — ``claim_number`` and ``claim_line_number`` live on the 837 feed
and never reach the 340B sidecar, and HCPCS is requirement E2, not yet built.  They are
written as nulls rather than omitted so the gap is visible on the wire instead of looking
like a template that happens to be complete.
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


# ═══ template selection ═════════════════════════════════════════════════════════════


def _template(dispense: Dispense) -> str:
    """``PHARMACY`` or ``MEDICAL`` — a classification of what the record already is.

    Reads the same null pattern ``tpa.py`` writes: a medically-administered drug has no
    prescription, so ``rx_number`` and ``pharmacy_npi`` are both null and ``provider_npi``
    carries the identity instead.  This looks at the pharmacy pair first, so a record
    carrying both stays pharmacy rather than silently changing template.
    """
    if dispense.rx_number is not None or dispense.pharmacy_npi is not None:
        return "PHARMACY"
    return "MEDICAL"


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
    """Outbound: one 340B-qualified claim, in Beacon's pharmacy or medical template shape.

    Carries no ``beacon_id``.  Beacon assigns that on receipt, and a request that already
    held one would make the acknowledgement decorative.

    ``qualification_status`` rides along because ``DOC2-007`` says the outbound direction
    is *eligible* claims — the TPA's own eligibility verdict is the reason the claim is
    being sent at all — and it is carried as the feed's word, not re-decided here.
    """
    template = _template(dispense)
    qualification = dispense.event("QUALIFICATION_DECISION")
    payload: dict[str, Any] = {
        "payload_kind": "submission",
        "direction": "OUTBOUND",
        "template": template,
        "covered_entity_id": dispense.covered_entity_id,
        "manufacturer": dispense.manufacturer,
        "submission_date": dispense.submitted_at,
        "qualification_status": dispense.qualification_status,
    }

    if template == "PHARMACY":
        payload.update(
            {
                "rx_number": dispense.rx_number,
                "pharmacy_npi": dispense.pharmacy_npi,
                "prescriber_npi": None if qualification is None else qualification.get("prescriber_npi"),
                "ndc_11": dispense.ndc_11,
                "fill_date": dispense.fill_date,
            }
        )
    else:
        payload.update(
            {
                # BEACON-008's medical key, as far as we can populate it.  claim_number and
                # claim_line_number live on the 837 feed and never reach the 340B sidecar.
                #
                # ``hcpcs_code`` stays null for a different reason than it used to, and the
                # old comment here — "HCPCS is requirement E2 and is not built" — is now
                # false: E2 landed in wave 5.  What is missing is narrower.  The J-code
                # reaches an episode by a reference lookup on the NDC, and this sidecar row
                # carries neither the episode nor a resolved drug, so the value is not
                # available *at this point* rather than not existing.  Populating it would
                # mean a mock reaching into reference data to derive a field, which is the
                # one thing a formatter may not do.
                #
                # Null, not absent, so the gap is on the wire rather than hidden by a
                # template that looks complete.
                "claim_number": None,
                "claim_line_number": None,
                "service_provider_npi": dispense.provider_npi,
                "ndc_11": dispense.ndc_11,
                "hcpcs_code": None,
                "hcpcs_modifier_code": None,
                "date_of_service": dispense.fill_date,
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
