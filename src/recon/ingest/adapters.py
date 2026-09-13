"""Raw records to canonical shapes, and the keys each one carries.

Every adapter answers three questions about one inbound document:

1. **What canonical records does it become?**  An 835 is not one row: it is a
   ``REMITTANCE`` parent, N ``REMITTANCE_CLAIM_LINE`` children and M
   ``PROVIDER_LEVEL_ADJUSTMENT`` children.  The parent carries the batch total, the
   children carry the claim-level money.  Flattening that would lose the PLB, and the PLB
   is where the netted recoupment hides.

2. **Which keys does it publish?**  Keys that now resolve *to* something — a claim event
   publishes its NCPDP key pointing at the episode it just created.

3. **Which keys does it look up?**  Keys it carries that should resolve to something
   already known — an 835 claim line looks up the NCPDP key it reconstructed from CLP01.

The split between published and looked-up keys is what makes the crosswalk bidirectional
without any scanning: a document resolves its own keys forward, and the keys it publishes
are what the parked pool is re-checked against backwards (Decision A20).

═══ Two things this module refuses to do ═══════════════════════════════════════════

**It does not normalise identifiers (Decision A23).**  ``rx_number`` is stored and keyed
exactly as the feed spelled it.  Injected drift is meant to miss; the miss is the D-6
crosswalk-failure exception.  There is no ``lstrip("0")`` anywhere in this package and a
test asserts it.

**It does not read ground truth.**  Nothing in the ingestion layer may.  That prohibition
is the only reason the crosswalk is scoreable rather than assumed correct.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Iterable, Sequence

from recon.crosswalk import keys
from recon.domain.enums import KeyType, RecordKind, SourceSystem
from recon.money import to_cents

__all__ = [
    "CanonicalRecord",
    "AdapterError",
    "adapt",
    "parse_jsonl_line",
    "parse_bank_row",
]


class AdapterError(ValueError):
    """A record could not be mapped into the canonical vocabulary.

    Raised rather than coerced.  An event that does not map is quarantined with its lineage
    intact, never forced into the nearest-looking slot — because new *codes* are
    configuration and new *semantics* are a canonical-model change, and the system must
    refuse to guess which one it is looking at.
    """


@dataclass(slots=True)
class CanonicalRecord:
    """One normalized row, plus the keys it publishes and looks up.

    ``children`` are emitted after the parent and carry ``parent_norm_id`` once the parent
    has been written — which is why this is a tree rather than a flat list.
    """

    record_kind: RecordKind
    source_system: SourceSystem
    received_at: str
    idempotency_key: str
    canonical: dict[str, Any]

    #: Keys this record makes resolvable, pointing at whatever *it* resolved to.  A claim
    #: line that resolved to an episode publishes its keys against that episode.
    publishes: tuple[tuple[KeyType, str], ...] = ()
    #: Keys this record carries that should resolve to something already known.
    looks_up: tuple[tuple[KeyType, str], ...] = ()
    #: Keys that resolve to **this record itself**, as a remittance or rebate batch.
    #:
    #: This is the third category, and omitting it is a subtle way to break the whole
    #: crosswalk.  An 835 parent looks up nothing — it is not waiting on anything — but it is
    #: the resolution *target* for every bank deposit carrying its trace number.  Treating
    #: "looks up nothing" as "could not be resolved" would park every remittance and leave
    #: every deposit permanently orphaned.
    self_keys: tuple[tuple[KeyType, str], ...] = ()

    #: Crosswalk-facing columns, stored verbatim.
    pharmacy_npi: str | None = None
    provider_npi: str | None = None
    rx_number: str | None = None
    fill_number: str | None = None
    ndc11: str | None = None
    date_of_service: str | None = None
    clm01: str | None = None
    clp07: str | None = None
    trn02: str | None = None
    ach_trace_number: str | None = None
    allocation_code: str | None = None
    authorization_number: str | None = None
    payer_id: str | None = None
    amount_cents: int | None = None
    quantity_milli: int | None = None
    status_code: str | None = None

    children: list["CanonicalRecord"] = field(default_factory=list)

    #: True when this record is the anchor that brings an episode into existence.  Exactly
    #: two kinds are: an NCPDP claim event and a medical 837 submission.  Everything else
    #: attaches to an episode that already exists, or parks waiting for one.
    creates_episode: bool = False


# ═══ entry point ════════════════════════════════════════════════════════════


def parse_jsonl_line(line: str) -> dict[str, Any]:
    """Parse a feed line, keeping money exact.

    ``parse_float=Decimal`` is not a nicety.  ``json.loads('{"a": 47218.40}')`` yields a
    binary float whose round trip leaves a residue indistinguishable from a real one-cent
    variance — and this is a reconciliation engine, so variance is the product.
    """
    return json.loads(line, parse_float=Decimal)


def adapt(payload: dict[str, Any], source_system: SourceSystem) -> CanonicalRecord:
    """Map one raw payload to its canonical shape.

    Raises:
        AdapterError: if the payload does not map. The caller quarantines it.
    """
    try:
        return _DISPATCH[source_system](payload)
    except AdapterError:
        raise
    except KeyError as exc:
        raise AdapterError(
            f"{source_system} record is missing required field {exc.args[0]!r}"
        ) from None
    except (TypeError, ValueError) as exc:
        raise AdapterError(f"{source_system} record could not be adapted: {exc}") from None


# ═══ PBM adjudication ═══════════════════════════════════════════════════════


def _adapt_pbm_adjudication(payload: dict[str, Any]) -> CanonicalRecord:
    transaction = payload["transaction_code"]
    if transaction not in {"B1", "B2"}:
        raise AdapterError(f"unknown NCPDP transaction code {transaction!r}")

    pharmacy_npi = payload["service_provider_id"]
    rx_number = payload["prescription_ref_number"]
    fill_number = payload["fill_number"]
    service_date = keys.wire_date_to_iso(payload["date_of_service"])
    authorization = payload.get("authorization_number")

    # The NCPDP transaction key.  A B2 reversal carries no identity of its own — it repeats
    # this composite — so both transactions resolve through the same key.
    ncpdp = keys.ncpdp_claim(pharmacy_npi, rx_number, fill_number, service_date)

    is_reversal = transaction == "B2"
    record = CanonicalRecord(
        record_kind=RecordKind.PHARMACY_REVERSAL if is_reversal else RecordKind.PHARMACY_CLAIM,
        source_system=SourceSystem.PBM_ADJUDICATION,
        received_at=payload["received_at"],
        idempotency_key=payload["record_id"],
        canonical={
            "transaction_code": transaction,
            "response_status": payload.get("response_status"),
            "reject_codes": payload.get("reject_codes", []),
            "reversal_reason": payload.get("reversal_reason"),
            "submission_clarification_code": payload.get("submission_clarification_code"),
            "bin": payload.get("bin"),
            "pcn": payload.get("pcn"),
            "cardholder_id": payload.get("cardholder_id"),
            "prescriber_id": payload.get("prescriber_id"),
            "ingredient_cost_paid_cents": _optional_cents(payload.get("ingredient_cost_paid")),
            "dispensing_fee_paid_cents": _optional_cents(payload.get("dispensing_fee_paid")),
            "patient_pay_amount_cents": _optional_cents(payload.get("patient_pay_amount")),
            "total_amount_paid_cents": _optional_cents(payload.get("total_amount_paid")),
        },
        pharmacy_npi=pharmacy_npi,
        # Verbatim.  No zero-stripping, ever (Decision A23).
        rx_number=rx_number,
        fill_number=fill_number,
        ndc11=payload.get("product_service_id"),
        date_of_service=service_date,
        authorization_number=authorization,
        # NCPDP 101-A1, the routing BIN, is what identifies the PBM on a pharmacy claim.  It is
        # the pharmacy-side analogue of the 837's payer loop, and the same reasoning applies:
        # without it, the contracted rate -- and therefore the expected amount -- is unknowable.
        payer_id=_resolve_pbm(payload.get("bin")),
        amount_cents=_optional_cents(payload.get("total_amount_paid")),
        quantity_milli=_optional_int(payload.get("quantity_dispensed")),
        status_code=payload.get("response_status"),
        creates_episode=not is_reversal,
    )

    if is_reversal:
        record.looks_up = (ncpdp,)
    else:
        # A rejected claim still creates an episode: A-01 is a verdict, and an episode with
        # expected = 0 is exactly what it describes.  The prototype begins once a claim has
        # been filed, and a rejected claim was filed.
        published = [ncpdp]
        if authorization:
            published.append(keys.pbm_auth(authorization))
        # The 340B bridge, published only when the pharmacy flagged the dispense.  This is
        # the one 340B-adjacent fact a PBM record carries, and it is a flag the pharmacy put
        # on its own claim rather than an identifier the PBM owns.
        if payload.get("submission_clarification_code") == "20" and payload.get(
            "product_service_id"
        ):
            published.append(
                keys.natural_340b_pharmacy(
                    pharmacy_npi, rx_number, payload["product_service_id"], service_date
                )
            )
        record.publishes = tuple(published)
    return record


# ═══ PBM remittance (835) ═══════════════════════════════════════════════════


def _adapt_pbm_remittance(payload: dict[str, Any]) -> CanonicalRecord:
    bpr = payload["bpr"]
    trn = payload.get("trn") or {}
    trace = trn.get("reassociation_trace_number")
    received_at = payload["received_at"]
    payee_npi = payload.get("payee_npi")

    parent = CanonicalRecord(
        record_kind=RecordKind.REMITTANCE,
        source_system=SourceSystem.PBM_REMITTANCE,
        received_at=received_at,
        idempotency_key=payload["record_id"],
        canonical={
            "payer_name": payload.get("payer_name"),
            "payment_method_code": bpr.get("payment_method_code"),
            "payment_effective_date": keys.wire_date_to_iso(bpr["payment_effective_date"]),
            "claim_line_count": len(payload.get("claim_payments", [])),
        },
        trn02=trace,
        amount_cents=to_cents(bpr["total_actual_provider_payment"]),
        date_of_service=keys.wire_date_to_iso(bpr["payment_effective_date"]),
        # The trace number is the only thread to the bank, and it resolves to this
        # remittance — never to a claim.  That is hop one of two (Decision A21).
        self_keys=(keys.trn02(trace),) if trace else (),
    )

    for line in payload.get("claim_payments", []):
        parent.children.append(_pbm_claim_line(line, received_at, payee_npi, payload["record_id"]))
    for index, entry in enumerate(payload.get("provider_level_adjustments", [])):
        parent.children.append(
            _plb_child(
                entry,
                received_at=received_at,
                parent_record_id=payload["record_id"],
                index=index,
                source_system=SourceSystem.PBM_REMITTANCE,
                reference_field="reference_id",
                reference_key_type=KeyType.PBM_AUTH,
            )
        )
    return parent


def _pbm_claim_line(
    line: dict[str, Any], received_at: str, payee_npi: str | None, parent_record_id: str
) -> CanonicalRecord:
    """One claim's payment, and the crosswalk step that gets it back to a claim.

    ``CLP01`` is ``"7845102FILL00"`` — the Rx number with the fill number glued on after the
    literal word ``FILL``.  Taking it apart yields two of the four components of the NCPDP
    key; the other two come from the remittance's ``payee_npi`` and the service line's own
    ``date_of_service``.  So the whole key is reconstructible, which is why there is no
    weaker "CLP01-only" key type.

    If ``CLP01`` will not parse, the line looks up nothing and parks.  Falling through to a
    partial key would silently attach this payment to whichever claim happened to share the
    fragment.
    """
    clp01 = line["clp01_patient_control_number"]
    service_line = line.get("service_line") or {}
    service_date = (
        keys.wire_date_to_iso(service_line["date_of_service"])
        if service_line.get("date_of_service")
        else None
    )
    clp07 = line.get("clp07_payer_claim_control_number")

    looks_up: list[tuple[KeyType, str]] = []
    rx_number: str | None = None
    fill_number: str | None = None
    try:
        rx_number, fill_number = keys.parse_clp01_pharmacy(clp01)
    except keys.MalformedKeyComponentError:
        rx_number = fill_number = None

    if rx_number and fill_number and payee_npi and service_date:
        looks_up.append(keys.ncpdp_claim(payee_npi, rx_number, fill_number, service_date))
    authorization = line.get("ref_authorization_number")
    if authorization:
        looks_up.append(keys.pbm_auth(authorization))

    return CanonicalRecord(
        record_kind=RecordKind.REMITTANCE_CLAIM_LINE,
        source_system=SourceSystem.PBM_REMITTANCE,
        received_at=received_at,
        idempotency_key=f"{parent_record_id}|{clp01}|{clp07 or ''}",
        canonical={
            "clp01": clp01,
            "clp02_claim_status_code": line.get("clp02_claim_status_code"),
            "charge_cents": to_cents(line["clp03_total_charge"]),
            "payment_cents": to_cents(line["clp04_payment_amount"]),
            "patient_responsibility_cents": to_cents(line["clp05_patient_responsibility"]),
            "adjustments": [
                {
                    "group_code": adjustment["group_code"],
                    "reason_code": adjustment["reason_code"],
                    "amount_cents": to_cents(adjustment["amount"]),
                }
                for adjustment in line.get("adjustments", [])
            ],
            "service_line": {
                "product_id": service_line.get("product_id"),
                "charge_cents": _optional_cents(service_line.get("charge_amount")),
                "paid_cents": _optional_cents(service_line.get("paid_amount")),
            },
        },
        pharmacy_npi=payee_npi,
        rx_number=rx_number,
        fill_number=fill_number,
        ndc11=service_line.get("product_id"),
        date_of_service=service_date,
        clp07=clp07,
        authorization_number=authorization,
        amount_cents=to_cents(line["clp04_payment_amount"]),
        # Settlement lives here and nowhere else (Decision 40).  "1" is final; "19"/"25"
        # mean money moved but the receivable is not closed.
        status_code=line.get("clp02_claim_status_code"),
        looks_up=tuple(looks_up),
        # The payer's ICN becomes resolvable once we have seen it, because a later
        # replacement or void points back at it through REF*F8.
        publishes=(keys.payer_icn(clp07),) if clp07 else (),
    )


def _plb_child(
    entry: dict[str, Any],
    *,
    received_at: str,
    parent_record_id: str,
    index: int,
    source_system: SourceSystem,
    reference_field: str,
    reference_key_type: KeyType,
) -> CanonicalRecord:
    """A provider-level adjustment: money moved for reasons outside any claim loop.

    When the reference is present this is traceable to the claim being adjusted.  When it is
    absent — routine on ``FB`` and general ``CS`` lines — there is nothing to resolve it
    against, so it parks and surfaces as the D-7 unexplained residual.  That absence is also
    the entire difference between A-12 ("recouped, netted and matched") and A-13
    ("recoupment not traceable to any deposit").
    """
    reference = entry.get(reference_field)
    amount = to_cents(entry["amount"])
    return CanonicalRecord(
        record_kind=RecordKind.PROVIDER_LEVEL_ADJUSTMENT,
        source_system=source_system,
        received_at=received_at,
        idempotency_key=f"{parent_record_id}|plb|{index}|{entry['reason_code']}",
        canonical={
            "reason_code": entry["reason_code"],
            # Already signed on the wire: positive reduces the deposit, negative increases
            # it, under BPR02 = Σ(CLP04) − Σ(PLB, signed).
            "amount_cents": amount,
            "reference": reference,
        },
        amount_cents=amount,
        status_code=entry["reason_code"],
        looks_up=((reference_key_type, reference),) if reference else (),
    )


# ═══ medical 837 / 277CA ════════════════════════════════════════════════════


def _adapt_medical_submission(payload: dict[str, Any]) -> CanonicalRecord:
    # The 277CA always carries record_type; an ordinary 837 never does.  Without that
    # distinction a clearinghouse rejection is indistinguishable on the wire from a claim
    # awaiting payment, and verdict B-01 is unreachable.
    if payload.get("record_type") == "277CA":
        return _adapt_medical_acknowledgment(payload)

    clm01 = payload["clm01_patient_control_number"]
    service_date = keys.wire_date_to_iso(payload["date_of_service"])
    loop_2410 = payload.get("loop_2410") or {}
    ndc11 = loop_2410.get("ndc11")
    provider_npi = payload.get("billing_provider_npi")
    frequency = payload.get("clm05_3_frequency_code", "1")
    original_icn = payload.get("ref_f8_original_icn")

    published: list[tuple[KeyType, str]] = [keys.medical_clm01(clm01)]
    # The medical 340B bridge, and it is the weakest key in the system: a clinic-infused
    # drug has no prescription, so two administrations of the same drug at the same site on
    # the same day are genuinely indistinguishable (Decision C9).
    if provider_npi and ndc11:
        published.append(keys.natural_340b_medical(provider_npi, ndc11, service_date))

    looks_up: list[tuple[KeyType, str]] = []
    if frequency != "1":
        # A replacement or void resolves by **CLM01**, not by the ICN it quotes.
        #
        # This ordering is the whole lesson of this feed.  CLM01 is the provider's own key and
        # is stable for the entire life of the claim, including across reprocessing.  The
        # REF*F8 ICN is the payer's key, it changes on every reprocess, and a provider copies
        # back whatever the payer last told them — which can be stale.  Keying on the ICN
        # forks one claim's history into unrelated pieces the first time it is reprocessed, so
        # it is carried as corroboration and never as identity.
        looks_up.append(keys.medical_clm01(clm01))
        if original_icn:
            looks_up.append(keys.payer_icn(original_icn))

    service_lines = payload.get("service_lines") or []
    # Resolve the payer named in loop 2010BB to a reference id.  This is a *lookup*, not
    # identifier normalisation: the payer is named on the wire and the reference table is the
    # authority on what that name means.  Unresolvable leaves it None rather than guessing —
    # the expected amount is then uncomputable, which is a finding, not a default.
    payer_loop = payload.get("loop_2010bb") or {}
    payer_id = _resolve_medical_payer(payer_loop.get("nm103_payer_name"))

    return CanonicalRecord(
        record_kind=RecordKind.MEDICAL_SUBMISSION,
        source_system=SourceSystem.CLEARINGHOUSE_837,
        received_at=payload["received_at"],
        idempotency_key=payload["record_id"],
        canonical={
            "frequency_code": frequency,
            "ref_f8_original_icn": original_icn,
            "rendering_provider_npi": payload.get("rendering_provider_npi"),
            "service_lines": [
                {
                    "svc01_composite": svc.get("svc01_composite"),
                    "charge_cents": _optional_cents(svc.get("svc02_charge_amount")),
                    "units": svc.get("units"),
                }
                for svc in service_lines
            ],
            # The same vial described on two different unit bases.  They are legitimately
            # different numbers and a connector that expects them to reconcile is wrong to.
            "ctp04_quantity": loop_2410.get("ctp04_quantity"),
            "ctp05_uom_qualifier": loop_2410.get("ctp05_uom_qualifier"),
        },
        provider_npi=provider_npi,
        ndc11=ndc11,
        date_of_service=service_date,
        clm01=clm01,
        payer_id=payer_id,
        amount_cents=sum(
            _optional_cents(svc.get("svc02_charge_amount")) or 0 for svc in service_lines
        )
        or None,
        quantity_milli=_optional_int(loop_2410.get("ctp04_quantity"), scale=1_000),
        status_code=frequency,
        publishes=tuple(published),
        looks_up=tuple(looks_up),
        # A frequency-1 original creates the episode.  A 7 or an 8 attaches to the episode
        # the original created — a replacement is a new document about the same claim, not a
        # new claim.
        creates_episode=frequency == "1",
    )


def _adapt_medical_acknowledgment(payload: dict[str, Any]) -> CanonicalRecord:
    clm01 = payload["clm01_patient_control_number"]
    icn = payload.get("payer_claim_control_number")
    composite = payload.get("stc01_composite", "")
    accepted = composite.startswith("A1")
    return CanonicalRecord(
        record_kind=RecordKind.MEDICAL_ACKNOWLEDGMENT,
        source_system=SourceSystem.CLEARINGHOUSE_837,
        received_at=payload["received_at"],
        idempotency_key=payload["record_id"],
        canonical={
            "stc01_composite": composite,
            "stc12_free_form": payload.get("stc12_free_form"),
            "accepted": accepted,
        },
        clm01=clm01,
        clp07=icn,
        status_code=composite,
        looks_up=(keys.medical_clm01(clm01),),
        # The payer ICN exists only on acceptance, which is itself the signal: no ICN means
        # the payer never saw the claim.
        publishes=(keys.payer_icn(icn),) if icn else (),
    )


# ═══ medical 835 ════════════════════════════════════════════════════════════


def _adapt_medical_remittance(payload: dict[str, Any]) -> CanonicalRecord:
    bpr = payload["bpr"]
    trn = payload.get("trn") or {}
    trace = trn.get("trn02_trace_number")
    received_at = payload["received_at"]
    effective = keys.wire_date_to_iso(bpr["bpr16_eft_effective_date"])

    parent = CanonicalRecord(
        record_kind=RecordKind.REMITTANCE,
        source_system=SourceSystem.MEDICAL_REMITTANCE,
        received_at=received_at,
        idempotency_key=payload["record_id"],
        canonical={
            "payment_method_code": bpr.get("bpr04_payment_method"),
            "payment_effective_date": effective,
            "payer_tin": trn.get("trn03_payer_tin"),
            "claim_line_count": len(payload.get("claim_payments", [])),
        },
        trn02=trace,
        amount_cents=to_cents(bpr["bpr02_total_payment"]),
        date_of_service=effective,
        self_keys=(keys.trn02(trace),) if trace else (),
    )

    for line in payload.get("claim_payments", []):
        parent.children.append(_medical_claim_line(line, received_at, payload["record_id"]))
    for index, entry in enumerate(payload.get("provider_level_adjustments", [])):
        parent.children.append(
            _plb_child(
                entry,
                received_at=received_at,
                parent_record_id=payload["record_id"],
                index=index,
                source_system=SourceSystem.MEDICAL_REMITTANCE,
                reference_field="reference_icn",
                # Resolved as the provider's own claim number rather than the payer's ICN.
                # PLB03 nominally carries an ICN, but an ICN is reassigned on every reprocess, so
                # a reference to one is only traceable if you happen to hold the exact generation
                # it names.  The provider claim number is stable for the life of the claim, which
                # is what actually lets a provider-level offset be tied back to the claim that
                # caused it.  Stated as a simplification rather than dressed up as the standard.
                reference_key_type=KeyType.MEDICAL_CLM01,
            )
        )
    return parent


def _medical_claim_line(
    line: dict[str, Any], received_at: str, parent_record_id: str
) -> CanonicalRecord:
    """One medical claim's payment.

    ``CLP01`` echoes the provider's own ``CLM01`` and stays constant for the whole life of
    the claim.  ``CLP07`` is the payer's ICN and gets a **new value every reprocess**, so it
    is recorded and published but never treated as identity — a connector that keys on it
    forks one claim's history into unrelated pieces the first time it is reprocessed.
    """
    clp01 = line["clp01_patient_control_number"]
    clp07 = line.get("clp07_payer_claim_control_number")
    service_lines = line.get("service_lines") or []
    return CanonicalRecord(
        record_kind=RecordKind.REMITTANCE_CLAIM_LINE,
        source_system=SourceSystem.MEDICAL_REMITTANCE,
        received_at=received_at,
        idempotency_key=f"{parent_record_id}|{clp01}|{clp07 or ''}",
        canonical={
            "clp01": clp01,
            "clp02_claim_status_code": line.get("clp02_status_code"),
            "charge_cents": to_cents(line["clp03_total_charge"]),
            "payment_cents": to_cents(line["clp04_payment_amount"]),
            "patient_responsibility_cents": to_cents(line["clp05_patient_responsibility"]),
            "adjustments": [
                {
                    "group_code": adjustment["group_code"],
                    "reason_code": adjustment["reason_code"],
                    "amount_cents": to_cents(adjustment["amount"]),
                    "rarc": adjustment.get("rarc"),
                }
                for adjustment in line.get("adjustments", [])
            ],
            "service_lines": [
                {
                    "svc01_composite": svc.get("svc01_composite"),
                    "charge_cents": _optional_cents(svc.get("svc02_charge")),
                    "paid_cents": _optional_cents(svc.get("svc03_paid")),
                    "units": svc.get("svc05_units"),
                }
                for svc in service_lines
            ],
        },
        clm01=clp01,
        clp07=clp07,
        amount_cents=to_cents(line["clp04_payment_amount"]),
        status_code=line.get("clp02_status_code"),
        looks_up=(keys.medical_clm01(clp01),),
        publishes=(keys.payer_icn(clp07),) if clp07 else (),
    )


# ═══ 340B / TPA ═════════════════════════════════════════════════════════════

_TPA_EVENT_KINDS = {
    "QUALIFICATION_DECISION": RecordKind.TPA_QUALIFICATION,
    "REBATE_REQUEST": RecordKind.TPA_REBATE_REQUEST,
    "MANUFACTURER_DECISION": RecordKind.TPA_MANUFACTURER_DECISION,
    "DISPENSE_REVERSAL": RecordKind.TPA_REVERSAL,
}


def _adapt_tpa(payload: dict[str, Any]) -> CanonicalRecord:
    event_type = payload.get("event_type")
    if event_type == "REBATE_PAYMENT_BATCH":
        return _adapt_rebate_batch(payload)
    kind = _TPA_EVENT_KINDS.get(event_type)
    if kind is None:
        raise AdapterError(f"unknown TPA event_type {event_type!r}")

    fill_date = keys.wire_date_to_iso(payload["fill_date"])
    ndc11 = payload["ndc_11"]
    record = CanonicalRecord(
        record_kind=kind,
        source_system=SourceSystem(payload["source_system"]),
        received_at=payload["received_at"],
        idempotency_key=payload["record_id"],
        canonical={
            "event_type": event_type,
            "qualification_status": payload.get("qualification_status"),
            "disqualification_reason": payload.get("disqualification_reason"),
            "manufacturer": payload.get("manufacturer"),
            "manufacturer_status": payload.get("manufacturer_status"),
            "rejection_reason": payload.get("rejection_reason"),
            "submission_date": (
                keys.wire_date_to_iso(payload["submission_date"])
                if payload.get("submission_date")
                else None
            ),
            "covered_entity_id": payload.get("covered_entity_id"),
            "hin": payload.get("hin"),
            "wholesaler_invoice_number": payload.get("wholesaler_invoice_number"),
            "quantity_dispensed": payload.get("quantity_dispensed"),
            "reversal_reason": payload.get("reversal_reason"),
        },
        pharmacy_npi=payload.get("pharmacy_npi"),
        provider_npi=payload.get("provider_npi"),
        rx_number=payload.get("rx_number"),
        ndc11=ndc11,
        date_of_service=fill_date,
        status_code=payload.get("qualification_status") or payload.get("manufacturer_status"),
        looks_up=_tpa_lookup_keys(payload, ndc11, fill_date),
    )
    return record


def _tpa_lookup_keys(
    payload: dict[str, Any], ndc11: str, fill_date: str
) -> tuple[tuple[KeyType, str], ...]:
    """The natural key, chosen by which shape of dispense this is.

    A pharmacy dispense keys on ``{pharmacy_npi, rx, ndc, fill_date}``.  A medical dispense
    has no prescription at all, so it keys on ``{provider_npi, ndc, service_date}`` — and
    that key cannot distinguish two same-day administrations of the same drug at the same
    site.  The connector parks that ambiguity rather than guessing through it.
    """
    rx_number = payload.get("rx_number")
    pharmacy_npi = payload.get("pharmacy_npi")
    provider_npi = payload.get("provider_npi")
    if rx_number and pharmacy_npi:
        return (keys.natural_340b_pharmacy(pharmacy_npi, rx_number, ndc11, fill_date),)
    if provider_npi:
        return (keys.natural_340b_medical(provider_npi, ndc11, fill_date),)
    # Neither shape is satisfiable: there is no key at all, so this record can never resolve
    # to anything.  It parks with NO_KEYS_PRESENT and becomes a D-4 unmatched rebate.
    return ()


def _adapt_rebate_batch(payload: dict[str, Any]) -> CanonicalRecord:
    allocation = payload["allocation_code"]
    effective = keys.wire_date_to_iso(payload["payment_effective_date"])
    received_at = payload["received_at"]

    parent = CanonicalRecord(
        record_kind=RecordKind.REBATE_BATCH,
        source_system=SourceSystem.MANUFACTURER_REBATE,
        received_at=received_at,
        idempotency_key=payload["record_id"],
        canonical={
            "event_type": "REBATE_PAYMENT_BATCH",
            "manufacturer": payload.get("manufacturer"),
            "payment_effective_date": effective,
            "dispense_line_count": len(payload.get("dispenses", [])),
        },
        allocation_code=allocation,
        amount_cents=to_cents(payload["total_rebate_amount"]),
        date_of_service=effective,
        # The rebate side's exact analogue of TRN02: it resolves to this batch, which holds
        # the dispense list.  Two hops, same as the bank (Decision A21).
        self_keys=(keys.allocation_code(allocation),),
    )

    for index, line in enumerate(payload.get("dispenses", [])):
        fill_date = keys.wire_date_to_iso(line["fill_date"])
        ndc11 = line["ndc_11"]
        parent.children.append(
            CanonicalRecord(
                record_kind=RecordKind.REBATE_DISPENSE_LINE,
                source_system=SourceSystem.MANUFACTURER_REBATE,
                received_at=received_at,
                idempotency_key=f"{payload['record_id']}|{index}",
                canonical={
                    "manufacturer_status": line.get("manufacturer_status"),
                    "covered_entity_id": line.get("covered_entity_id"),
                    "rebate_amount_cents": to_cents(line["rebate_amount"]),
                },
                pharmacy_npi=line.get("pharmacy_npi"),
                provider_npi=line.get("provider_npi"),
                rx_number=line.get("rx_number"),
                ndc11=ndc11,
                date_of_service=fill_date,
                allocation_code=allocation,
                amount_cents=to_cents(line["rebate_amount"]),
                status_code=line.get("manufacturer_status"),
                looks_up=_tpa_lookup_keys(line, ndc11, fill_date),
            )
        )
    return parent


# ═══ bank ═══════════════════════════════════════════════════════════════════


def parse_bank_row(row: dict[str, str]) -> dict[str, Any]:
    """Normalise a CSV row's empty strings to ``None`` before adapting.

    A CSV has no null: a missing ``trn02`` is an empty cell.  Collapsing that to ``None``
    here means the rest of the pipeline never has to know the difference between "absent"
    and "present but empty".
    """
    return {key: (value if value != "" else None) for key, value in row.items()}


def _adapt_bank(payload: dict[str, Any]) -> CanonicalRecord:
    trace = payload.get("ach_trace_number")
    if not trace:
        # The network cannot move money without one, so its absence means the row is not a
        # bank transaction at all.
        raise AdapterError("bank row carries no ach_trace_number")

    trn02 = payload.get("trn02")
    amount = to_cents(payload["amount"])
    entry_description = payload.get("company_entry_description")

    looks_up: list[tuple[KeyType, str]] = []
    if trn02:
        # One column, two issuers.  A claim payment's reference is the 835 trace number; a
        # manufacturer rebate's is the batch allocation code.  Which one it is cannot be
        # decided from the column alone, so both key types are looked up and at most one
        # resolves.  The entry description is a hint, not a guarantee — a rebate deposit
        # deliberately does not carry HCCLAIMPMT (Decision 42).
        looks_up.append(keys.trn02(trn02))
        looks_up.append(keys.allocation_code(trn02))

    return CanonicalRecord(
        record_kind=RecordKind.BANK_TRANSACTION,
        source_system=SourceSystem.BANK,
        received_at=payload["received_at"],
        # A bank CSV row carries no record id, so idempotency rides the ACH trace, which the
        # feed spec guarantees is always present.  Same meaning as a record id: "this
        # delivery arrived twice" is D-1 either way.
        idempotency_key=trace,
        canonical={
            "posting_date": payload["posting_date"],
            "description": payload.get("description"),
            "company_name": payload.get("company_name"),
            "company_id": payload.get("company_id"),
            "company_entry_description": entry_description,
            "direction": payload.get("type"),
            "running_balance_cents": _optional_cents(payload.get("running_balance")),
        },
        trn02=trn02,
        ach_trace_number=trace,
        # Recorded in both columns because the same value means different things depending on
        # the issuer, and the connector must be free to try either reading.
        allocation_code=trn02,
        amount_cents=amount,
        date_of_service=payload["posting_date"],
        status_code=payload.get("type"),
        looks_up=tuple(looks_up),
    )


# ═══ dispatch ═══════════════════════════════════════════════════════════════

_DISPATCH = {
    SourceSystem.PBM_ADJUDICATION: _adapt_pbm_adjudication,
    SourceSystem.PBM_REMITTANCE: _adapt_pbm_remittance,
    SourceSystem.CLEARINGHOUSE_837: _adapt_medical_submission,
    SourceSystem.MEDICAL_REMITTANCE: _adapt_medical_remittance,
    SourceSystem.TPA_PORTAL: _adapt_tpa,
    SourceSystem.MANUFACTURER_REBATE: _adapt_tpa,
    SourceSystem.BANK: _adapt_bank,
}


# ═══ small helpers ══════════════════════════════════════════════════════════


def _resolve_medical_payer(payer_name: str | None) -> str | None:
    """Map a payer name on the wire to a reference payer id.

    Decision 33 in miniature: a new payer adds a reference row, not an engine branch.
    """
    if not payer_name:
        return None
    from recon.reference import entities

    for payer in entities.medical_payers():
        if payer.name == payer_name:
            return payer.payer_id
    return None


def _resolve_pbm(bin_number: str | None) -> str | None:
    if not bin_number:
        return None
    from recon.reference import entities
    from recon.reference.errors import UnknownEntityError

    try:
        return entities.pbm_by_bin(bin_number).pbm_id
    except UnknownEntityError:
        return None


def _optional_cents(value: Any) -> int | None:
    if value is None:
        return None
    return to_cents(value)


def _optional_int(value: Any, *, scale: int = 1) -> int | None:
    """Parse a numeric-ish wire value to ``int``.

    NCPDP 442-E7 carries three implied decimals, so ``"030000"`` is already milli-units and
    needs no scaling; a plain integer quantity does.  ``scale`` says which.
    """
    if value is None:
        return None
    text = str(value)
    if not text.lstrip("-").isdigit():
        return None
    return int(text) * scale
