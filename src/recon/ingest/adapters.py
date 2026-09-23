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

    #: Connector-layer identifiers (requirement 4.6), all defaulting to ``None`` so every
    #: existing adapter is unchanged by their arrival.
    #:
    #: ``covered_entity_id`` is the odd one out: it is **not** a ``normalized_record`` column
    #: and is never stored on the row.  It rides here because ``_create_episode`` needs it at
    #: insert time — ``trg_episode_no_update`` aborts any UPDATE on ``episode``, so a value
    #: that does not arrive with the anchor can never be added later.
    beacon_id: str | None = None
    hcpcs: str | None = None
    site_id: str | None = None
    payment_reference: str | None = None
    covered_entity_id: str | None = None

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

    # Requirement E2.  The reference table is asked first and the wire second, which is the
    # opposite of the usual order and is deliberate: the 837 carries an NDC, and the drug
    # reference is the authority on what that NDC is billed as.  The wire's own SVC01 is the
    # fallback for a line that named a J-code without an NDC.
    hcpcs = _drug_j_code_for_ndc(ndc11) or _first_hcpcs(payload.get("service_lines"))

    published: list[tuple[KeyType, str]] = [keys.medical_clm01(clm01)]
    # The medical 340B bridge, and it is the weakest key in the system: a clinic-infused
    # drug has no prescription, so two administrations of the same drug at the same site on
    # the same day are genuinely indistinguishable (Decision C9).
    if provider_npi and ndc11:
        published.append(keys.natural_340b_medical(provider_npi, ndc11, service_date))
    # E2.  Published whenever the drug has a J-code, not only when the NDC is missing: the
    # publisher cannot know what the records that come looking will carry, and an unpublished
    # key resolves nothing no matter how well-formed the lookup is.  Publishing is cheap — one
    # crosswalk row — and it only ever adds an answer where there was none, because nothing
    # looks this key up while it holds an NDC.
    if provider_npi and hcpcs and _is_known_drug_j_code(hcpcs):
        published.append(keys.hcpcs_medical(hcpcs, provider_npi, service_date))

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
        hcpcs=hcpcs,
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
    # Requirement E2.  ``ndc_11`` was required outright until now, which made a record that
    # identifies its drug by J-code alone unadaptable — it failed on a missing field and was
    # quarantined as unparseable, when in fact it was perfectly well-formed and simply
    # described the drug the way the medical benefit describes it.
    #
    # Still exactly one of the two, not neither: a 340B record naming no drug at all cannot
    # be matched to a dispense by any key, and accepting it would trade a loud quarantine for
    # a silent park.  Every record on the six generated feeds carries ``ndc_11``, so this is
    # additive for all of them.
    ndc11 = payload.get("ndc_11")
    hcpcs = payload.get("hcpcs")
    if not ndc11 and not hcpcs:
        raise AdapterError(
            "340B record identifies no drug: it carries neither 'ndc_11' nor 'hcpcs', so "
            "there is no key on which it could ever reach a dispense"
        )
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
        # Requirement E1.  Carried out of the payload so the crosswalk can *check* it, not so
        # it can key on it: the covered entity is what the record claims about whose 340B
        # claim this is, and `_attach` refuses to attach a claim to an episode that says
        # otherwise.  Already present in ``canonical`` above for the audit trail; this is the
        # same value promoted to a column the resolver can read without parsing JSON.
        covered_entity_id=payload.get("covered_entity_id"),
        hcpcs=hcpcs,
        status_code=payload.get("qualification_status") or payload.get("manufacturer_status"),
        looks_up=_tpa_lookup_keys(payload, ndc11, fill_date),
    )
    return record


#: The vendor datasets that describe a qualification decision, and therefore the ones
#: :func:`_adapt_vendor_export` can adapt.
#:
#: ``verity_invoices`` is not here because it is not a qualification; it has its own adapter
#: below and its own record kind.
_QUALIFICATION_DATASETS = frozenset({"verity_accumulations", "craneware_claims_report"})

#: The vendor datasets that report money rather than a decision.  One, today.
_INVOICE_DATASETS = frozenset({"verity_invoices"})


def _adapt_vendor_export(payload: dict[str, Any]) -> CanonicalRecord:
    """One row of a 340B TPA's own export, under this fabric's spelling.

    The connector half of ``_adapt_tpa``.  Both produce ``TPA_QUALIFICATION`` because both
    describe the same event — a TPA deciding a dispense qualifies — and a second record kind
    meaning that would have made the engine's evidence map depend on which door the fact
    came through.  What differs is only the door: ``_adapt_tpa`` reads the synthetic
    ``tpa_340b_events.jsonl``, this reads what Verity and Craneware actually deliver.

    **The rename happens here and not in the reader**, which is why this function begins by
    looking up a mapping it was not handed.  The raw layer stores the vendor's own columns so
    the registered contract can be checked against the shape the vendor can be held to; by
    the time a payload reaches an adapter that check has passed, and renaming is safe.

    **A reversed row produces a parent and exactly one child.**  Both vendors declare a flag
    representation — the dispense and its reversal ride one row — so a reader looking for a
    second, correcting row would find none and report the reversal as never having happened.
    One ``TPA_REVERSAL`` child, never two, is requirement B3's *"not zero, not two"* arriving
    as a record rather than as a count.

    ═══ Identity: the vendor's row id, and what it costs when there is none ═════════

    See :func:`_vendor_idempotency_key`.  It is a function and not two lines inline because
    the two datasets get genuinely different guarantees out of it and the weaker one has to be
    written down where it is chosen.
    """
    from recon.connectors import vendors as vendor_mappings

    mapping = vendor_mappings.mapping_for(payload["source_id"])
    if mapping.source_id in _INVOICE_DATASETS:
        return _adapt_vendor_invoice(mapping, payload)
    if mapping.source_id not in _QUALIFICATION_DATASETS:
        raise AdapterError(
            f"{mapping.source_id} is mapped and readable but has no adapter: it does not "
            "describe a qualification decision, and adapting it as one would land a record "
            "whose status is null and whose meaning is invented"
        )
    record = vendor_mappings.canonical(mapping, payload)

    ndc11 = record.get("ndc11")
    hcpcs = record.get("hcpcs")
    if not ndc11 and not hcpcs:
        raise AdapterError(
            f"{mapping.source_id} row identifies no drug: it carries neither an NDC nor a "
            "J-code, so there is no key on which it could ever reach a dispense"
        )
    fill_date = keys.wire_date_to_iso(record["fill_date"])

    # Shaped for ``_tpa_lookup_keys``, which reads the feed's spelling because that is the
    # spelling it was written for. Building the four names it needs is cheaper and far less
    # fragile than teaching it a second vocabulary, and it keeps one key-choice rule for both
    # doors — a vendor row and a feed row describing the same dispense must produce the same
    # key, or the crosswalk silently splits one claim into two.
    for_keys = {
        "rx_number": record.get("rx_number"),
        "pharmacy_npi": record.get("pharmacy_npi"),
        "provider_npi": record.get("provider_npi"),
        "hcpcs": hcpcs,
    }
    identity = _vendor_idempotency_key(mapping, payload)
    reversed_row = vendor_mappings.is_reversed(mapping, payload)

    canonical_fields = {
        "event_type": "QUALIFICATION_DECISION",
        "qualification_status": record.get("qualification_status"),
        "disqualification_reason": record.get("disqualification_reason"),
        "manufacturer": record.get("manufacturer"),
        # **Relayed, not asserted, and the prefix is load-bearing.**
        #
        # Both vendors print the manufacturer's answer in their own export — Craneware under
        # ``manufacturer_status``, Verity under ``batch_line_manufacturer_status``. Writing
        # either into the canonical ``manufacturer_status`` key would make a TPA the author of
        # a decision DOC2-004 gives to Beacon, and ``connectors/authority.py`` refuses it:
        # the breach is never the vendor printing a value, it is us letting that value become
        # the canonical fact.
        #
        # This is not theoretical. The first version of this adapter wrote the plain key and
        # nine Craneware rows — every rejected claim in the profile — quarantined as
        # SOURCE_AUTHORITY_BREACH, taking their perfectly valid qualification down with them.
        # Under a relayed name the row keeps both: the qualification it owns, and a record of
        # what the TPA says it heard, which a dossier can show and no verdict can rest on.
        "relayed_manufacturer_status": record.get("manufacturer_decision_status"),
        "relayed_rejection_reason": record.get("rejection_reason"),
        "covered_entity_id": record.get("covered_entity_id"),
        "hin": record.get("hin"),
        "wholesaler_invoice_number": record.get("wholesaler_invoice_number"),
        "reversal_reason": record.get("reversal_reason"),
        # Provenance, carried so a dossier can say which of a vendor's datasets a fact came
        # from. One source system covers every file a vendor sends, so without this a Verity
        # accumulation and a Verity invoice are indistinguishable once they are rows.
        "vendor": mapping.vendor,
        "dataset": mapping.dataset,
        "source_id": mapping.source_id,
    }

    parent = CanonicalRecord(
        record_kind=RecordKind.TPA_QUALIFICATION,
        source_system=SourceSystem(payload["source_system"])
        if payload.get("source_system")
        else _VENDOR_SOURCE_SYSTEMS[mapping.vendor],
        received_at=payload["received_at"],
        idempotency_key=identity,
        canonical=canonical_fields,
        pharmacy_npi=record.get("pharmacy_npi"),
        provider_npi=record.get("provider_npi"),
        rx_number=record.get("rx_number"),
        ndc11=ndc11,
        date_of_service=fill_date,
        covered_entity_id=record.get("covered_entity_id"),
        hcpcs=hcpcs,
        status_code=record.get("qualification_status"),
        looks_up=_tpa_lookup_keys(for_keys, ndc11, fill_date),
    )

    if reversed_row:
        parent.children.append(
            CanonicalRecord(
                record_kind=RecordKind.TPA_REVERSAL,
                source_system=parent.source_system,
                received_at=parent.received_at,
                # Derived from the parent's, so the child inherits whichever guarantee the
                # parent got. A reversal is not a record in its own right — it is the reversal
                # *of this dispense* — so a child that identified itself independently could
                # survive a parent that collapsed, or collapse under a parent that did not.
                idempotency_key=f"{identity}|REVERSAL",
                canonical={
                    "event_type": "DISPENSE_REVERSAL",
                    "reversal_reason": record.get("reversal_reason"),
                    "reversal_quantity": record.get("reversal_quantity"),
                    "representation": mapping.reversal.representation,
                    "vendor": mapping.vendor,
                    "dataset": mapping.dataset,
                    "source_id": mapping.source_id,
                },
                pharmacy_npi=record.get("pharmacy_npi"),
                provider_npi=record.get("provider_npi"),
                rx_number=record.get("rx_number"),
                ndc11=ndc11,
                date_of_service=fill_date,
                covered_entity_id=record.get("covered_entity_id"),
                hcpcs=hcpcs,
                looks_up=parent.looks_up,
            )
        )
    return parent


def _adapt_vendor_invoice(mapping: Any, payload: dict[str, Any]) -> CanonicalRecord:
    """One line of a TPA's own rebate invoice — the only vendor row that reports money.

    ``verity_invoices`` was mapped and contract-checked for two waves and refused at this
    point with "it does not describe a qualification decision", which was true and left the
    rebate money unreachable through the vendor door.  This is the adapter that resolves it,
    and what shape it takes was decided by the authority table rather than by preference.

    ═══ Why this is not a rebate line ══════════════════════════════════════════════

    The obvious modelling is a ``REBATE_BATCH`` parent with ``REBATE_DISPENSE_LINE`` children,
    matching what ``_adapt_rebate_batch`` builds out of the 340B feed.  **The system refuses
    it, in as many words.**  ``connectors.authority._REBATE_STATUS`` lists both kinds with
    ``authoritative = {BEACON, MANUFACTURER_REBATE}``, and asking it directly returns:

        a TPA_VERITY source may not assert rebate status: a REBATE_DISPENSE_LINE record is
        the claim itself, and Beacon is authoritative per DOC2-004

    That is the same boundary that quarantined nine Craneware rows when this package first
    let a TPA author ``manufacturer_status``, arriving one level up: not a field a TPA may
    not set, but a *kind* a TPA may not be.  And it is the correct boundary.  A TPA's invoice
    is a real fact that Verity owns — this is what we billed, against this batch — about a
    payment decision Verity did not make.  Relaying it is honest; asserting it is not.

    Two mechanical failures sit behind the same wall, either of which would be silent:

    * ``REBATE_BATCH`` is in ``pipeline._RESOLUTION_ROOTS``, where ``_attach`` returns before
      it reads ``looks_up`` — so an invoice line modelled as a batch would publish its
      allocation code and then resolve to no episode at all.  One invoice row per batch would
      also mean N parents publishing one ``ALLOCATION_CODE``, and a second publisher does not
      produce a wrong answer, it produces *no* answer: every deposit carrying that code parks
      as ``AMBIGUOUS_KEY_MATCH``.
    * ``REBATE_DISPENSE_LINE`` is worse, and it is the C-14 guard in a new place.
      ``dimensions._read_rebate_lines`` sums across the bucket and flags ``DUPLICATE`` on
      ``line_count > 1 and len(amounts) == 1``.  ``recon.mocks.verity_export`` formats the
      same generated dispense the 340B feed already reports, so the invoice line's amount *is*
      the rebate line's amount — one payment through two doors, stamping C-14 "duplicate
      rebate payment" on every paid episode in the profile.

    So :class:`~recon.domain.enums.RecordKind.TPA_INVOICE_LINE` is its own kind, mapped in
    ``engine.run._ROLE_BY_KIND`` and deliberately absent from ``dimensions._KIND_BUCKETS``:
    gathered, cited, visible on the trace, moving no verdict.  Exactly the arrangement the
    Beacon inbound leg landed under, for exactly the same reason.

    ═══ The batch total is carried and never summed ════════════════════════════════

    Every row repeats ``batch_total_rebate_amount``, and the plan this work follows warned
    that lines need not sum to it, citing ``RBT-20251031-44202`` — 26,500.00 of lines against
    a declared 30,094.00.  **That measurement came out of a superseded export** and the file
    it came from no longer exists; see ``verity_export.remove_superseded``.  In the live
    export every batch's lines sum to its declared total exactly.

    The handling is unchanged by that, because it never depended on the disagreement being
    real.  Both figures are carried verbatim as text and neither is added to anything: a row
    that claimed the batch total as its own amount would multiply the batch by its line count,
    and an adapter that *checked* the two against each other would be deciding a control total
    one row at a time, without the file.  ``connectors.control_totals`` owns that comparison
    and has the whole document; this has one line of it.

    ``amount_cents`` is therefore ``None``, which is the property the tests pin.

    ═══ A reversed invoice line emits no reversal child ════════════════════════════

    ``_adapt_vendor_export`` gives a reversed row a ``TPA_REVERSAL`` child, and this
    deliberately does not, though the mapping carries the same four reversal columns.
    ``TPA_REVERSAL`` *is* bucketed, and ``dimensions`` reads ``clawed_back =
    bool(evidence.tpa_reversals) and paid > 0`` — so a second reversal arriving through the
    invoice door would move episodes to C-13 "clawed back".  The reversal of a dispense is a
    fact about the dispense, and ``verity_accumulations`` is where this vendor reports it;
    repeating it here is the same event through a second door, which is the hazard this whole
    function is shaped around.  The columns are carried in ``canonical`` so a dossier can show
    that the invoice agreed.
    """
    from recon.connectors import vendors as vendor_mappings

    record = vendor_mappings.canonical(mapping, payload)

    ndc11 = record.get("ndc11")
    if not ndc11:
        raise AdapterError(
            f"{mapping.source_id} row identifies no drug: an invoice line with no NDC has no "
            "key on which it could ever reach the dispense it bills for"
        )
    fill_date = keys.wire_date_to_iso(record["fill_date"])

    # Shaped for ``_tpa_lookup_keys`` in the feed's own spelling, exactly as
    # ``_adapt_vendor_export`` does it, so both vendor doors key one dispense one way.
    for_keys = {
        "rx_number": record.get("rx_number"),
        "pharmacy_npi": record.get("pharmacy_npi"),
        "provider_npi": record.get("provider_npi"),
    }

    return CanonicalRecord(
        record_kind=RecordKind.TPA_INVOICE_LINE,
        source_system=SourceSystem(payload["source_system"])
        if payload.get("source_system")
        else _VENDOR_SOURCE_SYSTEMS[mapping.vendor],
        received_at=payload["received_at"],
        idempotency_key=_vendor_idempotency_key(mapping, payload),
        canonical={
            "invoice_number": record.get("invoice_number"),
            "accumulation_id": record.get("accumulation_id"),
            "manufacturer": record.get("manufacturer"),
            "covered_entity_id": record.get("covered_entity_id"),
            # The batch this line bills against. Carried, not published as a crosswalk key:
            # the manufacturer's own REBATE_BATCH already publishes ALLOCATION_CODE, and a
            # second publisher of one key value parks every deposit that carries it.
            "rebate_allocation_code": record.get("rebate_allocation_code"),
            # Relayed, and the authority table asked for it in those words. ``beacon_id`` is
            # governed by ``_REBATE_SUBMISSION_IDENTIFIER`` with ``authoritative = {BEACON}``,
            # whose own rationale reads: *"a Verity export echoes a beacon_id as a lookup
            # handle, and a caller resolving a key rather than asserting a fact should not
            # pass it here."* Writing the plain key -- or promoting it to the
            # ``normalized_record.beacon_id`` column, which ``pipeline._GOVERNED_COLUMNS``
            # also checks -- quarantines every invoice row. It was doing exactly that until
            # this comment existed.
            "relayed_beacon_id": record.get("beacon_id"),
            # **Relayed, never asserted**, under the same prefix the qualification adapter
            # uses. Verity reads this off the payment batch line; DOC2-004 gives the decision
            # itself to Beacon and the manufacturer.
            "relayed_manufacturer_status": record.get("batch_line_manufacturer_status"),
            # Money, verbatim as the file spells it, in both directions. Text on purpose --
            # see the docstring. Named so that no key here collides with
            # ``rebate_amount_cents``, which ``dimensions._read_rebate_lines`` reads.
            "relayed_invoice_line_amount": record.get("rebate_amount"),
            "relayed_batch_total_amount": record.get("batch_total_rebate_amount"),
            "payment_effective_date": (
                keys.wire_date_to_iso(record["payment_effective_date"])
                if record.get("payment_effective_date")
                else None
            ),
            "batch_received_at": record.get("batch_received_at"),
            # Carried, not emitted as a TPA_REVERSAL child -- see the docstring.
            "reversal_status": record.get("reversal_status"),
            "reversal_received_at": record.get("reversal_received_at"),
            "reversal_reason": record.get("reversal_reason"),
            "reversal_quantity": record.get("reversal_quantity"),
            "vendor": mapping.vendor,
            "dataset": mapping.dataset,
            "source_id": mapping.source_id,
        },
        pharmacy_npi=record.get("pharmacy_npi"),
        provider_npi=record.get("provider_npi"),
        rx_number=record.get("rx_number"),
        ndc11=ndc11,
        date_of_service=fill_date,
        covered_entity_id=record.get("covered_entity_id"),
        # ``beacon_id`` is left unset for the reason given in ``canonical`` above: the column
        # is governed, and Beacon alone may fill it.
        # **Deliberately absent.** Promoting the line amount here is the single change that
        # would turn this record into money the engine counts.
        amount_cents=None,
        # Verity owns no status on this row. The one status it prints is the manufacturer's,
        # and ``status_code`` is explicitly ungoverned by ``authority.py`` -- so writing it
        # here would slip past the guard rather than satisfy it.
        status_code=None,
        looks_up=_tpa_lookup_keys(for_keys, ndc11, fill_date),
    )


def _vendor_idempotency_key(mapping: Any, payload: dict[str, Any]) -> str:
    """Identity for one vendor row: the vendor's own id, or a named fallback when it has none.

    ``_insert_tree``'s rule is that idempotency keys on the source record id and never on a
    natural key alone, and its docstring gives the reason: two genuine business events that
    share a natural key are two events, and collapsing them deletes one.  This function is
    where that rule is honoured on the vendor leg, and it used to break it — the key was
    ``f"{source_id}|{natural}"`` on every dataset, including the ones that publish an id.

    **When the vendor stamps a row id, that is the key.**  ``vendors.row_id`` returns it;
    Verity writes ``accumulation_id`` on every accumulation and ``invoice_number`` on every
    invoice line.  This is the strong case and it has every property identity needs: the same
    row re-delivered tomorrow carries the same id, and two different rows carry two different
    ones, however alike their claim facts happen to be.  The failure it buys back is not
    hypothetical — a partial fill and its completion on one Rx, same NDC, same day, are two
    accumulations with one natural key, and under the old rule the second one vanished with no
    quarantine, no park and no control-total shortfall.

    **When the vendor stamps nothing, this invents a key, and the invention is weaker.**
    Craneware's Claims Report carries no usable id on any of its columns, so the fallback is
    the dataset, the natural key, and *where the row sat in which delivery*:
    ``<source_id>|<natural key>|<document>#<line_no>``.  The last component is what stops the
    partial-fill collapse, since two rows in one file cannot share a line number.

    What it costs, stated plainly rather than left to be discovered:

    * **Re-delivery only collapses when the row does not move.**  Craneware sends a full file
      every drop, and the Claims Report is a snapshot of state rather than a journal — so a
      row that was line 12 yesterday and is line 11 today, because something above it was
      dropped, is a *different* key and lands a second time.  The natural key had no such
      problem; this is a real regression and it is the deliberate half of the trade.
    * **It is bounded by file-level idempotency, which is why the trade is worth making.**
      ``load_from_sources`` skips any document whose content hash it has already seen, so an
      unchanged re-delivery never reaches this function at all.  The exposure is only a
      delivery whose bytes genuinely changed — and there the outcome is a duplicate record,
      which is visible, countable and correctable.  The old outcome was a dropped record,
      which is none of those.  A duplicate you can find; a deletion you cannot.
    * **The document name is part of the key**, so a vendor that started dating its filenames
      the way Verity does would break collapsing entirely.  Craneware's mapping declares
      ``filename_prefix="claims_report.csv"`` — the prefix is the whole name — so the name is
      stable today, and that is a fact about a mapping rather than a promise about a vendor.

    The honest fix for all three is a row id from Craneware, and ``CRANEWARE-005`` records
    that no public column list exists to ask for one against.  Until then this is a fallback
    that says it is a fallback, which is the difference between a weak key and a weak key
    nobody knew about.
    """
    from recon.connectors import vendors as vendor_mappings

    vendor_row_id = vendor_mappings.row_id(mapping, payload)
    if vendor_row_id:
        return f"{mapping.source_id}|{vendor_row_id}"

    natural = "|".join(vendor_mappings.natural_key(mapping, payload))
    # Added to the payload by the ``VENDOR_CSV`` branch of ``ingest.pipeline._read_rows``,
    # which is the only place that knows them: an adapter is handed a payload and cannot see
    # the file it came out of. Absent only for a payload adapted directly — a fixture in a
    # test — where the natural key alone is what there is.
    document = payload.get("document")
    line_no = payload.get("line_no")
    if document is None or line_no is None:
        return f"{mapping.source_id}|{natural}"
    return f"{mapping.source_id}|{natural}|{document}#{line_no}"


#: Which source system a vendor's rows are attributed to when the payload does not say.
#:
#: The payload normally does not say: a vendor export publishes no ``source_system`` column,
#: so attribution comes from the registry row that fetched the file and arrives on the raw
#: record rather than inside it.  This is the fallback for a payload adapted directly — a
#: fixture in a test, most often — and it is a dict rather than a default so that a third
#: vendor cannot quietly inherit the second one's identity.
_VENDOR_SOURCE_SYSTEMS = {
    "verity": SourceSystem.TPA_VERITY,
    "craneware": SourceSystem.TPA_CRANEWARE,
}


def _tpa_lookup_keys(
    payload: dict[str, Any], ndc11: str | None, fill_date: str
) -> tuple[tuple[KeyType, str], ...]:
    """The natural key, chosen by which shape of dispense this is.

    A pharmacy dispense keys on ``{pharmacy_npi, rx, ndc, fill_date}``.  A medical dispense
    has no prescription at all, so it keys on ``{provider_npi, ndc, service_date}`` — and
    that key cannot distinguish two same-day administrations of the same drug at the same
    site.  The connector parks that ambiguity rather than guessing through it.

    Requirement E2 adds the last branch: a medical dispense that names its drug by J-code and
    carries no NDC.  It is tried **only when the NDC is absent**, never alongside it.  An NDC
    identifies one manufacturer's presentation of a drug; a J-code spans every manufacturer's,
    so it is the coarser key and would resolve a superset.  Offering both would mean a record
    that already had an exact answer sometimes got a second, vaguer hit and parked as
    ``AMBIGUOUS_KEY_MATCH`` — trading a correct match for no match at all.
    """
    rx_number = payload.get("rx_number")
    pharmacy_npi = payload.get("pharmacy_npi")
    provider_npi = payload.get("provider_npi")
    if rx_number and pharmacy_npi and ndc11:
        return (keys.natural_340b_pharmacy(pharmacy_npi, rx_number, ndc11, fill_date),)
    if provider_npi and ndc11:
        return (keys.natural_340b_medical(provider_npi, ndc11, fill_date),)
    hcpcs = payload.get("hcpcs")
    if provider_npi and hcpcs and _is_known_drug_j_code(hcpcs):
        return (keys.hcpcs_medical(hcpcs, provider_npi, fill_date),)
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
                covered_entity_id=line.get("covered_entity_id"),  # E1, as above
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

#: Which ``payload_kind`` becomes which record kind.  ``rebate_status`` is absent on
#: purpose — see :func:`_adapt_beacon`.
_BEACON_KINDS = {
    "acknowledgement": RecordKind.BEACON_ACKNOWLEDGMENT,
    "validation_outcome": RecordKind.BEACON_VALIDATION_OUTCOME,
    "payment_reference": RecordKind.BEACON_PAYMENT_REFERENCE,
}

#: Where each Beacon payload keeps the moment it became true.  Four payloads, four
#: spellings, none of them ``received_at`` except the first — which is why
#: ``registry.Source`` carries ``received_at_field`` rather than the reader assuming.
_BEACON_RECEIVED_AT = {
    "acknowledgement": "received_at",
    "validation_outcome": "decided_at",
    "payment_reference": "batch_received_at",
}


def _adapt_beacon(payload: dict[str, Any]) -> CanonicalRecord:
    """One Beacon inbound payload, as a canonical record.

    ``DOC2-013``'s hand-off is *"persist Beacon ID against Shields Claim Financial
    Episode"*, and this is the half that makes it possible: a Beacon response becomes a row,
    the row resolves to an episode, and the identifier is stored once against the record that
    carried it.

    **The acknowledgement is the bridge and the only publisher.**  It is the one payload
    carrying both the natural 340B key and the Beacon ID, so it looks the claim up by the key
    and publishes ``BEACON_ID`` against whatever it found.  The other two carry only the
    Beacon ID and look it up.  That asymmetry is load-bearing: two records publishing the
    same ``(key_type, key_value)`` pair make every lookup on it a two-hit, which the
    crosswalk parks as ``AMBIGUOUS_KEY_MATCH`` — so a second publisher would not produce a
    wrong answer, it would produce *no* answer, for every rebate at once.

    **What this adapter may and may not say.**  ``connectors/authority.py`` makes Beacon
    authoritative for the submission identifier and for rebate status, and makes it *not*
    authoritative for qualification — that belongs to the TPA sources.  The outbound
    submission payload carries ``qualification_status`` because it is our own request;
    copying it back off an inbound record would be Beacon asserting a decision the TPA made,
    and would quarantine every row as ``SOURCE_AUTHORITY_BREACH``.  It is relayed under
    ``relayed_qualification_status`` instead, the same way the vendor adapter relays the
    manufacturer's answer.

    Raises:
        AdapterError: for an unknown ``payload_kind``, for a submission fed back in, or for a
            payload missing the timestamp its kind keeps its arrival time in.
    """
    kind_name = payload.get("payload_kind")

    if kind_name == "submission":
        raise AdapterError(
            "a Beacon submission is our own outbound request, not a response, and adapting "
            "one would record a claim as decided by the fact that we sent it. Only the four "
            "inbound payload kinds may be ingested."
        )
    if kind_name == "rebate_status":
        # Not an oversight, and not adaptable here. A rebate status is the manufacturer's
        # decision under Beacon's name for it, so its kind is TPA_MANUFACTURER_DECISION --
        # which ``dimensions._KIND_BUCKETS`` *does* bucket, so landing it moves verdicts.
        # That belongs in its own measurable step rather than mixed into this one, where the
        # delta would be unattributable.
        raise AdapterError(
            "a Beacon rebate_status is the manufacturer's decision under another name, and "
            "landing it changes rebate verdicts. It is held out of this wave deliberately so "
            "the verdict delta can be measured on its own."
        )

    record_kind = _BEACON_KINDS.get(kind_name)
    if record_kind is None:
        raise AdapterError(
            f"unknown Beacon payload_kind {kind_name!r}; known: {sorted(_BEACON_KINDS)}"
        )

    beacon_id = payload.get("beacon_id")
    if not beacon_id:
        raise AdapterError(
            f"a Beacon {kind_name} carries no beacon_id. The identifier is the only thing "
            "tying an inbound payload to the submission it answers, so one without it can "
            "reach no episode by any route."
        )

    # The arrival time the READER resolved, under the canonical spelling it wrote back.
    #
    # Deliberately not re-derived from ``_BEACON_RECEIVED_AT[kind_name]`` here. Beacon keeps
    # each payload's stamp under its own name, and the registry row says which — so by the
    # time a payload reaches an adapter that has already been settled, and settling it twice
    # is two chances to disagree. It also matters for the one payload where the vendor's own
    # field is legitimately null: a PENDING validation outcome has no ``decided_at`` because
    # Beacon has not decided, and the reader dates it to the delivery. An adapter re-reading
    # the raw field would see the null and refuse a record that is perfectly readable and
    # perfectly true.
    received_at = payload.get("received_at")
    if not received_at:
        raise AdapterError(
            f"a Beacon {kind_name} reached the adapter with no resolved arrival time. The "
            "reader writes one back under 'received_at' from the field the registry row "
            f"names for this source ({_BEACON_RECEIVED_AT[kind_name]!r}); its absence means "
            "the row did not come through load_from_sources, or the registry row is wrong."
        )

    canonical_fields: dict[str, Any] = {
        "payload_kind": kind_name,
        "direction": payload.get("direction"),
        "template": payload.get("template"),
        "covered_entity_id": payload.get("covered_entity_id"),
        "manufacturer": payload.get("manufacturer"),
        # Beacon's own judgement of the submission, which it owns (DOC2-004).
        "validation_outcome": payload.get("outcome"),
        "validation_reason_code": payload.get("reason_code"),
        "outcome_source": payload.get("outcome_source"),
        # The payment side. ``payment_reference`` is carried and the AMOUNT is not promoted
        # to ``amount_cents`` -- see below.
        "payment_reference": payload.get("payment_reference"),
        "payment_effective_date": payload.get("payment_effective_date"),
        "rebate_amount": payload.get("rebate_amount"),
        "batch_total_amount": payload.get("batch_total_amount"),
        "status": payload.get("status"),
    }

    record = CanonicalRecord(
        record_kind=record_kind,
        source_system=SourceSystem.BEACON,
        received_at=str(received_at),
        idempotency_key=(
            f"{payload['payment_reference']}|{beacon_id}"
            if kind_name == "payment_reference"
            else str(beacon_id)
        ),
        canonical=canonical_fields,
        beacon_id=str(beacon_id),
        covered_entity_id=payload.get("covered_entity_id"),
        payment_reference=payload.get("payment_reference"),
        # **Deliberately no ``amount_cents``.** Beacon's line amount is the same money the
        # 340B feed already reported, because the mock formats the same generated data. If
        # this were promoted to a rebate line the engine would sum both and stamp C-14
        # "duplicate rebate payment" on every paid episode. The figure rides in ``canonical``
        # for the dossier, where nothing adds it up.
        status_code=payload.get("outcome") or payload.get("status"),
    )

    if record_kind is RecordKind.BEACON_ACKNOWLEDGMENT:
        # The bridge. It holds the natural key AND the identifier, so it is the only payload
        # that can tie the two together -- and therefore the only one that may publish.
        ndc11 = payload.get("ndc_11") or payload.get("ndc11")
        date_of_service = payload.get("date_of_service")
        if ndc11 and date_of_service:
            fill_date = keys.wire_date_to_iso(str(date_of_service))
            record.ndc11 = str(ndc11)
            record.date_of_service = fill_date
            record.pharmacy_npi = payload.get("pharmacy_npi")
            record.provider_npi = payload.get("provider_npi")
            record.rx_number = payload.get("rx_number")
            record.looks_up = _tpa_lookup_keys(payload, str(ndc11), fill_date)
        record.publishes = (keys.beacon_id(str(beacon_id)),)
    else:
        record.looks_up = (keys.beacon_id(str(beacon_id)),)

    return record


_DISPATCH = {
    SourceSystem.PBM_ADJUDICATION: _adapt_pbm_adjudication,
    SourceSystem.PBM_REMITTANCE: _adapt_pbm_remittance,
    SourceSystem.CLEARINGHOUSE_837: _adapt_medical_submission,
    SourceSystem.MEDICAL_REMITTANCE: _adapt_medical_remittance,
    SourceSystem.TPA_PORTAL: _adapt_tpa,
    SourceSystem.MANUFACTURER_REBATE: _adapt_tpa,
    SourceSystem.BANK: _adapt_bank,
    # The connector door. Both vendors route to one adapter because the per-vendor difference
    # is already data — ``SecureFileMapping`` — and a branch here would be that difference
    # expressed a second time, in a place that could disagree with the first.
    #
    # ``BEACON`` gets its own adapter rather than a share of the vendor one, which is what
    # the comment that used to sit here predicted it would need: its inbound records are
    # decisions, receipts and payments, not qualifications, so they map to different kinds
    # and answer to a different authority domain.
    SourceSystem.TPA_VERITY: _adapt_vendor_export,
    SourceSystem.TPA_CRANEWARE: _adapt_vendor_export,
    SourceSystem.BEACON: _adapt_beacon,
}


# ═══ HCPCS, requirement E2 ══════════════════════════════════════════════════
#
# ``DOC2-002``'s step-4 crosswalk list reads "NDC/HCPCS", and ``BEACON-008`` is a
# second-hand indication that Beacon's medical matching key is partial without it.  We
# modelled NDC only, which loses every record that identifies its drug by J-code and carries
# no NDC at all — and on the medical benefit that is not an edge case, it is how the 835
# service line is written.

#: X12 ``SVC01-1`` qualifier for a HCPCS/CPT procedure code (``STD-X12-837``).  The composite
#: is ``<qualifier>:<code>:<modifier>…``, and the qualifier is what decides *what the code
#: is*.  Reading position two without checking position one would happily lift a revenue code
#: or a NUBC code out of some other line and call it a drug.
_SVC01_HCPCS_QUALIFIER = "HC"

_SVC01_SEPARATOR = ":"


def _hcpcs_from_svc01(composite: str | None) -> str | None:
    """Pull the HCPCS code out of an X12 ``SVC01`` composite, or ``None``.

    ``"HC:J9035:JW"`` -> ``"J9035"``.

    Returns ``None`` rather than raising for anything that is not HC-qualified, because this
    is an enrichment: a service line that carries no HCPCS is not malformed, and quarantining
    it would delete records the system reconciles correctly today.

    The code is returned **verbatim**, like every other identifier (Decision A23).  No
    upper-casing, no stripping of the modifier's effect on the code — the modifier is a
    separate component and is simply not read.
    """
    if not composite:
        return None
    parts = str(composite).split(_SVC01_SEPARATOR)
    if len(parts) < 2 or parts[0] != _SVC01_HCPCS_QUALIFIER:
        return None
    return parts[1] or None


def _first_hcpcs(service_lines: object) -> str | None:
    """The first HC-qualified code across a claim's service lines, or ``None``.

    Takes ``object`` and checks the shape rather than trusting it: this runs on both the 837
    and the 835, whose service lines are spelled differently everywhere except ``SVC01``.
    """
    if not isinstance(service_lines, list):
        return None
    for line in service_lines:
        if not isinstance(line, dict):
            continue
        code = _hcpcs_from_svc01(line.get("svc01_composite"))
        if code:
            return code
    return None


def _drug_j_code_for_ndc(ndc11: str | None) -> str | None:
    """The J-code the drug reference gives for an NDC, or ``None``.

    This is a *lookup*, in the same sense :func:`_resolve_medical_payer` is: the reference
    table is the authority on what a drug is billed as, and `reference/__init__.py` already
    validates that every medical-benefit drug carries a J-code and no pharmacy-benefit drug
    does.  So a pharmacy-benefit NDC correctly yields ``None`` here rather than a guess.

    An NDC the reference has never heard of also yields ``None``.  A vendor feed is entitled
    to mention a drug we do not stock, and that is not a reason to fail the record.
    """
    if not ndc11:
        return None
    from recon.reference import drugs
    from recon.reference.errors import UnknownNdcError

    try:
        return drugs.by_ndc(ndc11).hcpcs_j_code
    except UnknownNdcError:
        return None


def _is_known_drug_j_code(hcpcs: str | None) -> bool:
    """True when the reference knows this code as some drug's J-code.

    The gate on publishing an ``HCPCS`` crosswalk key, and the reason the key is safe.  A CPT
    administration code — ``96413``, "chemotherapy administration, IV infusion, up to 1 hour"
    — parses out of ``SVC01`` exactly as cleanly as ``J9035`` does, but it describes the
    *infusion*, not the drug.  Keying on it would collapse every drug one provider infused on
    one day onto a single key value, and every one of those records would then resolve
    ambiguously and park.  The column may hold such a code honestly; the key may not.
    """
    if not hcpcs:
        return False
    from recon.reference import drugs
    from recon.reference.errors import UnknownNdcError

    try:
        drugs.by_j_code(hcpcs)
    except UnknownNdcError:
        return False
    return True


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
