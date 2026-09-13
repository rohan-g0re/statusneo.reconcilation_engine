# Source Feed Formats

> **Vocabulary:** *claim*, *episode*, *track*, *record*, *verdict*, *disposition* and *reason code* are defined once in [`glossary.md`](glossary.md). That file wins wherever this one is loose.

What each generator emits. Every feed is written as if by a source system that has never heard of the other three — its own identifiers, its own format, its own timing.

Status: **All four feeds drafted. Reconciled 2026-09-12 — see `plans/RECONCILIATION.md` for the rulings applied (fictional entity names, medical 340B dispenses, 277CA acknowledgments, rebate request/decision events, PLB `RA` sign, settlement encoding, worked-example corrections).**

### Confidentiality: every entity name is fictional

The assignment mandates synthetic data only — no real client names. Every payer, PBM and manufacturer in the *generated data* (and in the worked examples below) is invented, while keeping the real-world *shape* (≤16-char NACHA truncation, uppercase company names, 6-digit BINs, HRSA ID grammar). The fictional universe:

| Role | Fictional entities |
|---|---|
| PBMs | `MERIDIANRX`, `CASCADERX` |
| Medical payers | `BLUE HARBOR HEALTH`, `GRANITE PEAK HEALTH` |
| Manufacturers (incl. the contract-pharmacy-restricting wave) | `VERION PHARMA`, `ALDEBARAN THERAPEUTICS`, `CORVANE BIOSCIENCES`, `TALVEX LABS`, `SAGEPOINT BIO`, `HALCYON BIOLOGICS` |

Real-world names (Caremark, Anthem, Lilly, the 2020 restricting manufacturers) may still appear in *prose* as domain context; they must never appear in generated records.

### Canonical file names

Six feed files, fixed here as the single naming authority: `pbm_claim_events.jsonl`, `pbm_remittance_835.jsonl`, `medical_837_submissions.jsonl`, `medical_835_remittance.jsonl`, `tpa_340b_events.jsonl`, `bank_transactions.csv` — written to `data/generated/<profile>/feeds/`. Ground truth lives in `data/generated/<profile>/truth/`, which the connector under test never reads.

---

### The one field every generator adds: `received_at`

Every date native to these formats points backward. `date_of_service` is when the drug was dispensed. `payment_effective_date` is when a PBM committed money to move. `fill_date`, `posting_date`, invoice dates — every one of them describes something that already happened. None of them is a promise about the future; a claims or remittance feed never says "we'll decide by Friday," it says "here is what already occurred."

`received_at` is the single field every generator adds that is not native to any of the four source formats. The source system has no opinion about it — it is the timestamp stamped the instant the record lands in *our* pipeline, and it is the field that makes replay possible. Replaying this dataset means replaying the order records showed up in, not the order the underlying events happened in, and `received_at` is the only field that records that order.

This is also why the generator never takes a run date. It writes the entire timeline start to finish, with no concept of "now" anywhere in it. The cursor lives entirely at read time:

```
process(records where received_at <= cursor)
```

Move the cursor forward, later-arriving records fold in and verdicts change. Move it backward, you get the earlier answer. Same code path both directions — there is no separate "replay mode." Aging follows the same discipline: `age_days = cursor - date_of_service`, computed live at query time, never stored on a record, used only to sort exception queues. There is no SLA field anywhere in this dataset; a claim doesn't breach a deadline, it just gets older, and deciding how old is "concerning" is the agent layer's job at read time, not a threshold the generator bakes in.

One documented exception to the no-future-dates rule exists — the NACHA CCD+ Effective Entry Date on the bank feed. It's a real forward date, and it's handled precisely rather than pretended away in Section 4.

---

## 1. PBM / Pharmacy benefit

### Why this feed is two files

In reality the pharmacy's claim adjudication and the PBM's payment are different systems on different rails, arriving days or weeks apart:

| File | What it is | Arrives | Format basis |
|---|---|---|---|
| `pbm_claim_events.jsonl` | Real-time adjudication and reversal transactions | Seconds after the dispense | NCPDP Telecommunication D.0 |
| `pbm_remittance_835.jsonl` | Batched payment advice covering many claims | Days to weeks later | X12 835, per NCPDP's *Pharmacy Reference Guide to the 835* |

Collapsing these into one file would delete the entire reassociation problem — the payment would arrive pre-attached to the claim, and the crosswalk layer would have nothing to do. They stay separate.

---

### File 1 — `pbm_claim_events.jsonl`

One JSON object per line. Every record is a single transaction, not a claim summary.

**Accepted claim (NCPDP B1 billing, approved):**

```json
{
  "record_id": "PBM-EVT-000001",
  "source_system": "PBM_ADJUDICATION",
  "transaction_code": "B1",
  "received_at": "2026-03-02T14:22:31Z",

  "service_provider_id": "1234567893",
  "service_provider_id_qualifier": "01",
  "prescription_ref_number": "7845102",
  "fill_number": "00",
  "date_of_service": "20260302",

  "product_service_id": "00071015523",
  "product_service_id_qualifier": "03",
  "quantity_dispensed": "030000",
  "days_supply": "030",
  "prescriber_id": "1972000897",
  "prescriber_id_qualifier": "01",

  "bin": "604211",
  "pcn": "SPECRX",
  "group_id": "RXGRP0042",
  "cardholder_id": "W884210097",
  "person_code": "01",

  "submission_clarification_code": "20",

  "response_status": "P",
  "authorization_number": "AUTH0098231A",
  "ingredient_cost_paid": 2840.00,
  "dispensing_fee_paid": 1.75,
  "patient_pay_amount": 50.00,
  "total_amount_paid": 2791.75,
  "reject_codes": []
}
```

**Rejected claim (prior authorization required):**

```json
{
  "record_id": "PBM-EVT-000002",
  "source_system": "PBM_ADJUDICATION",
  "transaction_code": "B1",
  "received_at": "2026-03-02T14:24:08Z",

  "service_provider_id": "1234567893",
  "prescription_ref_number": "7845103",
  "fill_number": "00",
  "date_of_service": "20260302",
  "product_service_id": "00002143380",

  "bin": "604211",
  "pcn": "SPECRX",
  "cardholder_id": "W112039884",

  "response_status": "R",
  "authorization_number": null,
  "ingredient_cost_paid": null,
  "dispensing_fee_paid": null,
  "patient_pay_amount": null,
  "total_amount_paid": null,
  "reject_codes": ["75"]
}
```

**Reversal (NCPDP B2) — pharmacy cancels a previously accepted claim:**

```json
{
  "record_id": "PBM-EVT-000318",
  "source_system": "PBM_ADJUDICATION",
  "transaction_code": "B2",
  "received_at": "2026-03-05T09:11:47Z",

  "service_provider_id": "1234567893",
  "prescription_ref_number": "7845102",
  "fill_number": "00",
  "date_of_service": "20260302",
  "product_service_id": "00071015523",

  "bin": "604211",
  "pcn": "SPECRX",

  "response_status": "P",
  "authorization_number": "AUTH0098231A",
  "reversal_reason": "RETURN_TO_STOCK"
}
```

Note the reversal carries **no new identity of its own**. It matches the original by repeating `{service_provider_id, prescription_ref_number, fill_number, date_of_service}` — the NCPDP transaction key. That is exactly how the real B2 works, and it means the connector has to match on the composite key rather than look up a foreign key.

**Field reference:**

| Field | NCPDP # | Format | Assigned by |
|---|---|---|---|
| `prescription_ref_number` | 402-D2 | numeric, ≤12 digits | **Pharmacy** |
| `fill_number` | 403-D3 | 2 digits, `00` = original | Pharmacy |
| `date_of_service` | 401-D1 | CCYYMMDD | — |
| `service_provider_id` | 201-B1 | 10-digit NPI | NPPES |
| `product_service_id` | 407-D7 | 11-digit NDC, no punctuation | FDA |
| `quantity_dispensed` | 442-E7 | numeric, 3 implied decimals (`030000` = 30.000) | — |
| `bin` | 101-A1 | exactly 6 digits | ANSI |
| `pcn` | 104-A4 | alphanumeric ≤10, no standard structure | PBM |
| `cardholder_id` | 302-C2 | alphanumeric ≤20 | **Plan/PBM** |
| `submission_clarification_code` | 420-DK | `20` flags a 340B-related dispense | Pharmacy |
| `authorization_number` | 503-F3 | opaque, PBM-specific format | **PBM** |
| `reject_codes` | 511-FB | list of 2-char codes | PBM |

**Reject codes in use:** `70` not covered / plan exclusion · `75` prior auth required · `76` plan limitations exceeded · `79` refill too soon · `88` DUR reject · `40` pharmacy not contracted on DOS · `65` patient not covered · `21` invalid NDC · `25` M/I prescriber ID

---

### File 2 — `pbm_remittance_835.jsonl`

One JSON object per line, where **one line is one whole remittance file** covering many claims. This is what makes the deposit a lump.

```json
{
  "record_id": "PBM-835-000412",
  "source_system": "PBM_REMITTANCE",
  "received_at": "2026-03-18T06:00:00Z",

  "bpr": {
    "transaction_handling_code": "I",
    "total_actual_provider_payment": 47218.40,
    "credit_debit_flag": "C",
    "payment_method_code": "ACH",
    "payment_effective_date": "20260317"
  },

  "trn": {
    "trace_type_code": "1",
    "reassociation_trace_number": "8873020123",
    "originating_company_id": "1043251982"
  },

  "payer_name": "MERIDIANRX",
  "payee_npi": "1234567893",

  "claim_payments": [
    {
      "clp01_patient_control_number": "7845102FILL00",
      "clp02_claim_status_code": "1",
      "clp03_total_charge": 2891.75,
      "clp04_payment_amount": 2791.75,
      "clp05_patient_responsibility": 50.00,
      "clp06_claim_filing_indicator": "CI",
      "clp07_payer_claim_control_number": "20260610044821",
      "ref_authorization_number": "AUTH0098231A",
      "service_line": {
        "product_id_qualifier": "N4",
        "product_id": "00071015523",
        "charge_amount": 2891.75,
        "paid_amount": 2791.75,
        "quantity": "30",
        "date_of_service": "20260302"
      },
      "adjustments": [
        { "group_code": "CO", "reason_code": "45", "amount": 50.00 },
        { "group_code": "PR", "reason_code": "3", "amount": 50.00 }
      ]
    },
    {
      "clp01_patient_control_number": "7845119FILL01",
      "clp02_claim_status_code": "1",
      "clp03_total_charge": 1420.00,
      "clp04_payment_amount": 1275.00,
      "clp05_patient_responsibility": 45.00,
      "clp07_payer_claim_control_number": "20260610044822",
      "ref_authorization_number": "AUTH0098412B",
      "service_line": {
        "product_id": "00074433902",
        "charge_amount": 1420.00,
        "paid_amount": 1275.00,
        "quantity": "28",
        "date_of_service": "20260304"
      },
      "adjustments": [
        { "group_code": "PR", "reason_code": "3", "amount": 45.00 },
        { "group_code": "CO", "reason_code": "45", "amount": 100.00 }
      ]
    }
  ],

  "provider_level_adjustments": [
    { "reason_code": "WO", "reference_id": "20260588210033", "amount": 412.60 }
  ]
}
```

**The three things to notice, because each one creates work downstream:**

**`clp01_patient_control_number` is `"7845102FILL00"`.** Not a claim ID — the pharmacy's own Rx number with the fill number appended after the literal string `FILL`. This is NCPDP's documented convention for pharmacy 835s. The connector has to parse that string apart to get back to `{rx=7845102, fill=00}`. That is a real crosswalk, not a join.

**`trn.reassociation_trace_number` is the only link to the bank.** This same value should appear on the ACH deposit. When it does, matching is trivial. When the bank drops the addenda — which is common — you are left matching on amount and date alone.

**`provider_level_adjustments` does not balance to any claim.** The `WO` (overpayment recovery) of $412.60 is a clawback for some *earlier* claim, netted out of today's payment. So:

```
sum(clp04_payment_amount) across all claims   =  47,631.00
minus provider_level_adjustments (WO)         =     412.60
-------------------------------------------------------------
bpr.total_actual_provider_payment             =  47,218.40   <- what hits the bank
```

The bank will show $47,218.40 and nothing else. The $412.60 is invisible outside this segment. This is the single most realistic reconciliation trap in the whole dataset, and it is why bank amount will not equal the sum of claim payments.

### How settlement is encoded (binding — the engine must mirror this read)

Neither NCPDP nor the X12 835 has a "settlement" transaction, so `ph_settlement` has no native record of its own. It is encoded on the paying claim line's **CLP02**:

- `ph_settlement = CONFIRMED` → `clp02_claim_status_code: "1"` (processed as primary — final).
- `ph_settlement = MISSING` → `clp02_claim_status_code: "19"` (processed as primary, forwarded to additional payer — money moved, receivable not closed), with `"25"` (predetermination) as a rarer second variant, **and no later CLP02 `"1"` line exists for that CLP01**.

This is native, per-claim, and needs no invented record. The future reconciliation engine must read verdict **A-06** ("paid and matched, settlement missing") exactly this way: cash matched, covering line's CLP02 is `19`/`25`, no later `1` line for the same CLP01. Rejected alternatives: dropping the TRN (collides with the bank's missing-TRN defect, and is file-level), `bpr04 = NON` (file-level, would de-settle the whole batch), a zero-dollar closing 835 line (invented, confuses duplicate-835 detection).

### Every claim line balances — with one deliberate exception

The invariant on every 835 claim line, both channels: `clp03 = clp04 + Σ(adjustments)` and `clp05 = Σ(PR adjustments)`. The one deliberate exception is the pharmacy **underpayment** defect (see the defect table below): `clp04` short of the adjudicated promise with a `CO-45` that does not explain the whole gap, leaving `clp03 − clp04 − Σ(adjustments) > 0`. Medical 835s always balance; the medical dispute is about the *reason* for a reduction, never the arithmetic. *(Correction log: worked example 1 above originally omitted its `CO-45` of 50.00 and did not balance; fixed 2026-09-12.)*

---

### What the generator decides vs. what it emits

The orchestrator holds the truth — which episode this is, which verdict it should land on, what the timeline looks like. The PBM generator receives only the slice a PBM would know and emits the records above.

It never writes: episode ID, verdict name, 340B status, expected rebate, or anything about the bank. Those exist only in `ground_truth.json`, which the connector is never allowed to read.

**Defects the generator injects deliberately:**

| Defect | How it appears |
|---|---|
| Remittance with no cash | 835 line exists, no matching bank deposit |
| Underpayment | `clp04_payment_amount` < expected, with a `CO-45` adjustment that does not explain the whole gap |
| Late arrival | 835 `received_at` after the bank deposit date |
| Duplicate delivery | Same `record_id` emitted twice in the file |
| Identifier drift | `prescription_ref_number` zero-padded in one feed, bare in another (`07845102` vs `7845102`) |
| Recoupment | `WO` provider-level adjustment referencing an earlier claim |
| Missing settlement | Payment present, no closing settlement event |

---

## 2. 340B / TPA

### Why this feed looks nothing like the other two

Two 340B mechanisms exist in the real world, and they reconcile against completely different money:

- **Replenishment** — the pharmacy buys inventory at wholesale price, dispenses, then "flips" a bottle to the 340B account once the dispense is confirmed to qualify, earning a discount on its *next* wholesale purchase. No cash moves after the fact; it reconciles against wholesaler invoices, not a bank deposit. This is the dominant mechanism in the real 340B market today.
- **Rebate** — the pharmacy bills and dispenses at full price, and the *manufacturer* later cuts a cash rebate once a TPA has confirmed the dispense qualifies. Real money moves after the fact, on its own timeline, and needs reconciling like any other cash event.

The assignment's own language — "manufacturer payment," "unmatched rebate" — describes the rebate model, so that is what this feed generates. Replenishment is real and common; it's out of scope here, carried as a known simplification rather than pretended away.

The other thing that makes this feed different from the first: **there is no 835, no trace number, and no standard underneath it at all.** Real 340B TPAs ship vendor-specific CSV exports or portal downloads, and nothing about the shape of the data is standardized vendor to vendor. That isn't a modeling shortcut — it's the actual state of this part of the industry, and it's exactly why "unmatched rebate" is one of the assignment's own named exceptions: a feed with no shared identifier space is structurally prone to losing the thread.

**Envelope ruling (Decision 13, reaffirmed):** this feed is emitted as `tpa_340b_events.jsonl` — JSONL, one record per line — because the rebate payment batch is irreducibly nested (one total, N dispense lines). The feed's structural *poverty* is about identifiers and linkage, not serialization: vendor-invented event names, no standards body, no trace number, a natural key as the only bridge. All of that is preserved in JSON.

### Two decision-makers, not one

Every dispense that reaches this feed passes through two independent gates:

1. **The TPA decides qualification** — does the dispense meet the program's patient-definition rules?
2. **The manufacturer decides approval and pays** — even a qualified dispense can still be rejected at the rebate stage, on completely different grounds.

Model both. A dispense can be `QUALIFIED` at the TPA and still `REJECTED` at the manufacturer, and that gap is where most of the real dispute volume in this program lives.

### The join keys, because there is nothing better

The PBM's `authorization_number` (Section 1) never reaches the TPA — the two systems don't talk to each other. The only way to tie a 340B record back to a PBM claim is the natural key **{pharmacy NPI, Rx#, NDC, fill date}**. What flags a claim as 340B-related in the first place, at the point of adjudication, is NCPDP field 420-DK (`submission_clarification_code`) carrying the value `20` on the PBM claim event (Section 1) — a flag on someone else's record, not an identifier of its own.

**Medical episodes have a 340B track too — with an even poorer key.** A medically-administered drug is bought and infused at the covered entity's clinic. There is no prescription and therefore **no Rx number**: nothing pharmacy-shaped exists to key on. A TPA record for a medical-benefit dispense carries the NDC, the service date, the administering site's provider NPI, the prescriber/ordering NPI, and the covered entity ID — and nothing else. On these records:

- `rx_number` is `null` and `pharmacy_npi` is `null`;
- `provider_npi` carries the billing provider NPI that also appears on the 837 (Section 3);
- `fill_date` carries the administration date — the same value as the 837's `date_of_service`. (Yes, the vendor column is still called `fill_date`; the export schema was built for pharmacy and reused for medical. That is realistic, not sloppy modelling.)

The medical 340B join is therefore the natural key **{provider NPI, NDC, service date}** — strictly weaker than the pharmacy key, because two administrations of the same drug at the same site on the same day are indistinguishable. That ambiguity is a real crosswalk hazard the connector must park as ambiguous rather than guess through.

### Worked examples

**TPA qualification decision — qualified:**

```json
{
  "record_id": "TPA-EVT-000101",
  "source_system": "TPA_PORTAL",
  "event_type": "QUALIFICATION_DECISION",
  "received_at": "2026-03-04T11:02:00Z",

  "rx_number": "7845102",
  "ndc_11": "00071015523",
  "fill_date": "20260302",
  "pharmacy_npi": "1234567893",
  "prescriber_npi": "1972000897",
  "covered_entity_id": "DSH310074",
  "hin": "HN3021998",
  "wholesaler_invoice_number": "WI-88213340",

  "qualification_status": "QUALIFIED",
  "disqualification_reason": null
}
```

**TPA qualification decision — not qualified:**

```json
{
  "record_id": "TPA-EVT-000117",
  "source_system": "TPA_PORTAL",
  "event_type": "QUALIFICATION_DECISION",
  "received_at": "2026-03-06T10:40:00Z",

  "rx_number": "7845210",
  "ndc_11": "00074433902",
  "fill_date": "20260304",
  "pharmacy_npi": "1234567893",
  "prescriber_npi": "1855120044",
  "covered_entity_id": "PED045210A",
  "hin": "HN4471029",
  "wholesaler_invoice_number": "WI-88213401",

  "qualification_status": "NOT_QUALIFIED",
  "disqualification_reason": "PRESCRIBER_NOT_AFFILIATED"
}
```

**TPA qualification decision — medical-benefit (clinic-administered) dispense.** No Rx number exists; the natural key is `{provider_npi, ndc_11, fill_date}`:

```json
{
  "record_id": "TPA-EVT-000142",
  "source_system": "TPA_PORTAL",
  "event_type": "QUALIFICATION_DECISION",
  "received_at": "2026-03-12T09:30:00Z",

  "rx_number": null,
  "pharmacy_npi": null,
  "provider_npi": "1497821345",
  "ndc_11": "50242007923",
  "fill_date": "20260308",
  "prescriber_npi": "1972000897",
  "covered_entity_id": "DSH310074",
  "hin": "HN3021998",
  "wholesaler_invoice_number": "WI-88213422",

  "qualification_status": "QUALIFIED",
  "disqualification_reason": null
}
```

**Rebate request — separates C-03 (qualified, not yet submitted) from C-05 (submitted, manufacturer pending), and carries the submission date the 45-day rule needs:**

```json
{
  "record_id": "TPA-EVT-000155",
  "source_system": "TPA_PORTAL",
  "event_type": "REBATE_REQUEST",
  "received_at": "2026-03-15T10:05:00Z",

  "rx_number": "7845102",
  "ndc_11": "00071015523",
  "fill_date": "20260302",
  "pharmacy_npi": "1234567893",
  "covered_entity_id": "DSH310074",

  "submission_date": "20260314",
  "manufacturer": "VERION"
}
```

**Manufacturer decision — a rejection cannot ride inside a payment batch, so it gets its own record:**

```json
{
  "record_id": "TPA-EVT-000198",
  "source_system": "MANUFACTURER_REBATE",
  "event_type": "MANUFACTURER_DECISION",
  "received_at": "2026-03-28T08:00:00Z",

  "rx_number": "7845310",
  "ndc_11": "00002143380",
  "fill_date": "20260210",
  "pharmacy_npi": "1234567893",
  "covered_entity_id": "DSH310074",

  "manufacturer": "VERION",
  "manufacturer_status": "REJECTED",
  "rejection_reason": "NON_CONFORMING_45_DAY"
}
```

An `APPROVED` decision may be emitted as its own `MANUFACTURER_DECISION` record (C-11, approved but not yet paid) or be implied by the dispense's later appearance in a `REBATE_PAYMENT_BATCH` — both are real vendor behaviours.

**Manufacturer rebate batch — one payment, many dispenses:**

```json
{
  "record_id": "TPA-EVT-000230",
  "source_system": "MANUFACTURER_REBATE",
  "event_type": "REBATE_PAYMENT_BATCH",
  "received_at": "2026-04-02T08:15:00Z",

  "manufacturer": "VERION",
  "allocation_code": "RBT-20260402-01",
  "total_rebate_amount": 18420.00,
  "payment_effective_date": "20260401",

  "dispenses": [
    {
      "rx_number": "7845102",
      "ndc_11": "00071015523",
      "fill_date": "20260302",
      "pharmacy_npi": "1234567893",
      "covered_entity_id": "DSH310074",
      "manufacturer_status": "APPROVED",
      "rebate_amount": 612.40
    },
    {
      "rx_number": "7846044",
      "ndc_11": "00071015523",
      "fill_date": "20260303",
      "pharmacy_npi": "1234567893",
      "covered_entity_id": "DSH310074",
      "manufacturer_status": "APPROVED",
      "rebate_amount": 598.10
    }
  ]
}
```

`allocation_code` is what makes this splittable. The bank deposit for this rebate (Section 4) carries the same code **in its `trn02` column, when the CCD+ addenda survive the trip**: `trn02` is semantically "the payer-assigned business reference from the addenda" — for a claim payment the payer is the PBM/health plan and the reference is the 835 reassociation trace number; for a rebate the payer is the manufacturer and the reference is the `allocation_code`. Same column, same semantics, different issuer. Rebate deposits are subject to the same ~20% addenda-loss rate as claim payments, and a rebate deposit that loses its `allocation_code` *and* cannot be resolved on amount+date is exactly D-4, "orphan / unmatched rebate" — the exception emerges from the mechanism rather than being hand-placed. The bank CSV gains no rebate-specific column.

Batch `dispenses[]` lines for medical-benefit episodes follow the same shape as their qualification records: `rx_number: null`, `pharmacy_npi: null`, `provider_npi` populated.

**Reversal — negative quantity, not a delete:**

```json
{
  "record_id": "TPA-EVT-000340",
  "source_system": "TPA_PORTAL",
  "event_type": "DISPENSE_REVERSAL",
  "received_at": "2026-03-09T09:40:00Z",

  "rx_number": "7845102",
  "ndc_11": "00071015523",
  "fill_date": "20260302",
  "pharmacy_npi": "1234567893",
  "covered_entity_id": "DSH310074",
  "quantity_dispensed": -30,
  "reversal_reason": "RETURN_TO_STOCK"
}
```

**Field reference:**

| Field | Format | Assigned by |
|---|---|---|
| `rx_number` | numeric, ≤12 digits; **`null` on medical-benefit dispenses** (no prescription exists) | Pharmacy |
| `ndc_11` | 11-digit NDC, no punctuation | FDA |
| `fill_date` | CCYYMMDD; on medical dispenses carries the administration/service date | — |
| `pharmacy_npi` | 10-digit NPI; **`null` on medical-benefit dispenses** | NPPES |
| `provider_npi` | 10-digit NPI, the 837 billing provider; **present only on medical-benefit dispenses** | NPPES |
| `prescriber_npi` | 10-digit NPI | NPPES |
| `submission_date` | CCYYMMDD, on `REBATE_REQUEST` only; >45 days after `fill_date` drives `NON_CONFORMING_45_DAY` | TPA |
| `covered_entity_id` | HRSA format, e.g. `DSH310074`; prefix identifies entity type (`DSH`/`CAH`/`CAN`/`PED`/`RRC`/`SCH` = hospitals, `CH`/`FQHC`/`HM`/`RW*` = grantees); optional trailing letter = child site (`DSH310074A`) | **HRSA** |
| `hin` | 9-char alphanumeric | **HIBCC** |
| `wholesaler_invoice_number` | vendor-specific | Wholesaler |
| `qualification_status` | `QUALIFIED` / `NOT_QUALIFIED` | **TPA** |
| `manufacturer_status` | `APPROVED` / `REJECTED` | **Manufacturer** |
| `allocation_code` | vendor-specific, shared with the bank feed | TPA / manufacturer |

**Defects the generator injects:**

| Defect | How it appears |
|---|---|
| Patient-definition failure | `qualification_status = NOT_QUALIFIED`; reason one of `NO_QUALIFYING_ENCOUNTER`, `PRESCRIBER_NOT_AFFILIATED`, `UNREGISTERED_LOCATION` |
| Medicaid duplicate-discount exclusion | `qualification_status = NOT_QUALIFIED`; reason `MEDICAID_DUPLICATE_DISCOUNT` |
| Contract-pharmacy restriction | `manufacturer_status = REJECTED`; reason `CONTRACT_PHARMACY_RESTRICTED`; manufacturer one of the fictional restricting wave — `VERION`, `ALDEBARAN`, `CORVANE`, `TALVEX`, `SAGEPOINT`, `HALCYON` (modelled on the real 2020 manufacturer restrictions; real names appear in prose only) |
| Non-conforming claim | `manufacturer_status = REJECTED`; reason `NON_CONFORMING_45_DAY`; submitted more than 45 days after `fill_date` |
| Reversal after rebate already paid | A `DISPENSE_REVERSAL` (negative quantity) arrives after a `REBATE_PAYMENT_BATCH` already paid that dispense — cross-track cleanup, not a simple reject |
| Batched rebate needing allocation | One `REBATE_PAYMENT_BATCH` covers many `dispenses[]`; the connector must fan the total back out to individual claims via `allocation_code` |
| Orphan rebate | A rebate line whose `{pharmacy_npi, rx_number, ndc_11, fill_date}` doesn't resolve against any known PBM claim |

---

## 3. Medical benefit / 837 + 835

### Why this feed is two files

Same shape as PBM, different rails: the provider files an institutional/professional claim, the payer adjudicates and pays separately, days to weeks apart.

| File | What it is | Format basis |
|---|---|---|
| `medical_837_submissions.jsonl` | Claim submissions, replacements and voids, **plus 277CA clearinghouse acknowledgments** | X12 837 + X12 277CA |
| `medical_835_remittance.jsonl` | Payer remittance advice | X12 835 |

The 277CA rides in the submissions file because it is the same rail: the clearinghouse acknowledges the 837 it just carried. Without it, a clearinghouse rejection (verdict B-01 — "837 rejected before reaching the payer") would be indistinguishable on the wire from "accepted, awaiting 835" (B-02): both would be an 837 with no 835. The 277CA is the real, standard acknowledgment transaction — this is realism, not invention, and it keeps `received_at` the only non-native field.

### The identifier that actually matters: CLM01 vs CLP01 vs CLP07

**CLM01** (Patient Control Number) is assigned by the **provider** on the 837 — alphanumeric, typically 20 characters or fewer, and it is the provider's own internal claim key, not anything the payer invents.

**CLP01**, on the 835, simply echoes that same value back. **CLP07** is a different thing entirely: the payer's own Internal Control Number (ICN/DCN), assigned payer-side. This distinction is the crux of the whole feed. CLP01 is the provider's key and stays constant across the life of a claim. CLP07 is the payer's key, and a reprocessed claim typically gets a **brand-new CLP07** every time even though CLP01 never changes. A connector that treats CLP07 as a stable claim identifier will silently fork a claim's history into unrelated pieces the first time it's reprocessed.

### Frequency codes, and what a replacement actually means

CLM05-3 carries the frequency code: `1` original, `7` replacement, `8` void. A `7` or `8` must carry `REF*F8*<original payer claim control number>` pointing back at the CLP07 being replaced or voided.

**A `7` is a full replacement, not a patch.** Whatever the new 837 leaves out is dropped from the payer's record — there is no merge. A resubmission that omits a service line the original claim had doesn't leave that line untouched; it deletes it.

### Worked examples — 837

**Original submission:**

```json
{
  "record_id": "MED-837-000501",
  "source_system": "CLEARINGHOUSE_837",
  "received_at": "2026-03-10T16:40:00Z",

  "clm01_patient_control_number": "ENC-88231-01",
  "clm05_3_frequency_code": "1",
  "ref_f8_original_icn": null,

  "billing_provider_npi": "1497821345",
  "rendering_provider_npi": "1972000897",
  "date_of_service": "20260308",

  "service_lines": [
    {
      "line_number": 1,
      "svc01_composite": "HC:J9035:JW",
      "svc02_charge_amount": 8420.00,
      "units": 4
    }
  ],
  "loop_2410": {
    "lin02_qualifier": "N4",
    "ndc11": "50242007923",
    "ctp04_quantity": "400",
    "ctp05_uom_qualifier": "ML"
  }
}
```

Note the drug appears twice, on two different unit bases: SVC05 (`4`, J-code billing units) and CTP04 (`400`, mL of drug product). These are legitimately different numbers describing the same vial. A connector that expects them to reconcile numerically is wrong to expect that.

**Replacement (frequency 7) — corrects units, points back at the original:**

```json
{
  "record_id": "MED-837-000512",
  "source_system": "CLEARINGHOUSE_837",
  "received_at": "2026-03-22T09:05:00Z",

  "clm01_patient_control_number": "ENC-88231-01",
  "clm05_3_frequency_code": "7",
  "ref_f8_original_icn": "20260610077213",

  "billing_provider_npi": "1497821345",
  "rendering_provider_npi": "1972000897",
  "date_of_service": "20260308",

  "service_lines": [
    {
      "line_number": 1,
      "svc01_composite": "HC:J9035:JW",
      "svc02_charge_amount": 8420.00,
      "units": 5
    }
  ],
  "loop_2410": {
    "lin02_qualifier": "N4",
    "ndc11": "50242007923",
    "ctp04_quantity": "500",
    "ctp05_uom_qualifier": "ML"
  }
}
```

**Void (frequency 8):**

```json
{
  "record_id": "MED-837-000560",
  "source_system": "CLEARINGHOUSE_837",
  "received_at": "2026-04-01T13:20:00Z",

  "clm01_patient_control_number": "ENC-90142-01",
  "clm05_3_frequency_code": "8",
  "ref_f8_original_icn": "20260610081190"
}
```

### Worked examples — 277CA clearinghouse acknowledgment

A second `record_type` in the same file. `stc01_composite` carries the claim-status category and code; the payer claim control number is populated only on acceptance.

**Accepted:**

```json
{
  "record_id": "MED-277-000502",
  "source_system": "CLEARINGHOUSE_837",
  "record_type": "277CA",
  "received_at": "2026-03-11T08:12:00Z",

  "clm01_patient_control_number": "ENC-88231-01",
  "stc01_composite": "A1:19",
  "stc12_free_form": "ACCEPTED FOR PROCESSING",
  "payer_claim_control_number": "20260610077213"
}
```

**Rejected (never reached the payer — verdict B-01 territory):**

```json
{
  "record_id": "MED-277-000509",
  "source_system": "CLEARINGHOUSE_837",
  "record_type": "277CA",
  "received_at": "2026-03-11T08:12:00Z",

  "clm01_patient_control_number": "ENC-89544-01",
  "stc01_composite": "A3:21",
  "stc12_free_form": "MISSING OR INVALID SUBSCRIBER ID",
  "payer_claim_control_number": null
}
```

Rejection codes in use: `A3:21`, `A3:33`, `A3:187`. Ordinary 837 records carry no `record_type` field; the 277CA always does.

### Worked example — 835

```json
{
  "record_id": "MED-835-000601",
  "source_system": "MEDICAL_REMITTANCE",
  "received_at": "2026-03-24T06:00:00Z",

  "bpr": {
    "bpr02_total_payment": 5242.30,
    "bpr04_payment_method": "ACH",
    "bpr16_eft_effective_date": "20260323"
  },
  "trn": {
    "trn01": "1",
    "trn02_trace_number": "9012734410",
    "trn03_payer_tin": "1954002211"
  },

  "claim_payments": [
    {
      "clp01_patient_control_number": "ENC-88231-01",
      "clp02_status_code": "1",
      "clp03_total_charge": 8420.00,
      "clp04_payment_amount": 6100.00,
      "clp05_patient_responsibility": 300.00,
      "clp07_payer_claim_control_number": "20260610088410",
      "service_lines": [
        {
          "svc01_composite": "HC:J9035:JW",
          "svc02_charge": 8420.00,
          "svc03_paid": 6100.00,
          "svc05_units": 4
        }
      ],
      "adjustments": [
        { "group_code": "CO", "reason_code": "45", "amount": 1820.00 },
        { "group_code": "CO", "reason_code": "197", "amount": 200.00, "rarc": "N522" },
        { "group_code": "PR", "reason_code": "2", "amount": 300.00 }
      ]
    }
  ],

  "provider_level_adjustments": [
    { "reason_code": "WO", "reference_icn": "20260610055120", "amount": 900.00 },
    { "reason_code": "L6", "reference_icn": null, "amount": -42.30 }
  ]
}
```

The CO-197 adjustment (prior auth absent) sits alongside a paid claim — very common on specialty J-codes, where the drug gets administered before the auth paperwork fully clears. It's paired with RARC N522 for machine-readable detail.

`service_lines` is an **array**: an infusion claim is one J-code drug line (which owns the claim-level `loop_2410`) plus zero to two CPT administration lines (`96413`, `96415`) that legitimately carry no NDC. The single-line example above is the degenerate one-element case. The pharmacy 835 (Section 1) keeps `service_line` singular — pharmacy claims genuinely are one line.

The PLB math, same sign convention as Section 1:

```
sum(clp04_payment_amount)                     =   6,100.00
minus provider_level_adjustments (WO)         =     900.00
minus provider_level_adjustments (L6, credit) =    (42.30)
-------------------------------------------------------------
bpr02_total_payment                           =   5,242.30   <- what hits the bank
```

`L6` is interest the payer owes for a late payment, carried as a negative amount specifically because it increases what's paid — the opposite direction from `WO`.

### Field reference

| Field | Segment | Format | Assigned by |
|---|---|---|---|
| `clm01_patient_control_number` | CLM01 | alphanumeric, ≤20 chars | **Provider** |
| `clm05_3_frequency_code` | CLM05-3 | `1` original / `7` replacement / `8` void | Provider |
| `ref_f8_original_icn` | REF*F8 | payer ICN being replaced/voided | Provider (copies payer's value back) |
| `clp01_patient_control_number` | CLP01 | echoes CLM01 | Provider (echoed) |
| `clp02_status_code` | CLP02 | `1` primary / `2` secondary / `3` tertiary / `4` denied / `19`,`20`,`21` forwarded / `22` reversal of prior payment / `25` predetermination | Payer |
| `clp07_payer_claim_control_number` | CLP07 | opaque payer ICN/DCN, **new value every reprocess** | **Payer** |
| `bpr02_total_payment` | BPR02 | decimal | Payer |
| `bpr04_payment_method` | BPR04 | `ACH` / `CHK` / `NON` | Payer |
| `bpr16_eft_effective_date` | BPR16 | CCYYMMDD, ACH only | Payer |
| `trn02_trace_number` | TRN02 | reassociation trace number | Payer |
| `trn03_payer_tin` | TRN03 | `1` + 9-digit TIN | Payer |
| `svc01_composite` | SVC01 | `qualifier:HCPCS:modifiers`, e.g. `HC:J9035:JW` | Provider |
| `ndc11` (loop 2410) | LIN02=`N4` | 11-digit NDC | FDA |
| `ctp04_quantity` / `ctp05_uom_qualifier` | CTP04/05 | quantity + unit (`F2`/`GR`/`ME`/`ML`/`UN`) | Provider |

**Group codes:** `CO` contractual obligation (not billable to patient) · `PR` patient responsibility · `OA` other · `PI` payer-initiated · `CR` correction/reversal

**CARC codes in use:** `CO-45` exceeds fee schedule (on nearly every paid claim) · `CO-97` bundled into another service · `CO-16` missing information (pair with an N-code) · `CO-18` duplicate · `CO-50` not medically necessary · `CO-151` frequency/quantity exceeds limit · `CO-197` prior authorization absent — very common on specialty J-codes · `PR-1` deductible · `PR-2` coinsurance · `PR-3` copay · `PR-204` not covered

**RARC codes in use:** `N130`, `N362` (units exceed maximum), `N522`, `M15`, `N54`, `N56`

**PLB codes:** `WO` overpayment recovery · `FB` forward balance, unrecovered remainder carried to next cycle · `L6` interest owed (negative, increases payment) · `CS` adjustment · `72` authorized return · `RA` retroactive adjustment (**when it represents an appeal credit it is carried negative, same direction as `L6`** — under the identity `BPR02 = Σ(CLP04) − Σ(PLB, signed)` a positive PLB reduces payment, so a credit must be negative; an earlier draft said "positive" and was self-contradictory)

**PLB sign convention, in one block:**

```
WO  > 0   overpayment recovery       -> reduces the deposit
FB  > 0   forward balance, no ref    -> reduces the deposit, unexplained residual
CS  > 0   adjustment                 -> reduces the deposit
72  > 0   authorized return          -> reduces the deposit
L6  < 0   interest owed to provider  -> increases the deposit
RA  < 0   appeal credit              -> increases the deposit
```

A negative CLP04 (`clp02 = "22"`, reversal of prior payment) reduces the batch total through the `Σ(CLP04)` term, not through PLB — the two mechanisms are never both applied to the same reversal.

### PLB sits outside every claim loop

The PLB segment lives at the **end of the 835 file**, outside any CLP loop. PLB03 is a composite: a 2-character reason code plus an optional reference, usually the *original* claim's ICN being adjusted. That reference is what lets a connector trace a provider-level offset back to the claim that caused it — when it's populated. `FB` lines and general `CS` adjustments often carry no reference at all, and that residual has to be tracked as unexplained rather than silently absorbed.

### There is no native appeal marker

X12 has no field anywhere that says "this is an appeal." An appeal resolved by reprocessing looks exactly like one of two things:

- A replacement claim: frequency `7`, a brand-new CLP07, `REF*F8` pointing at the original — indistinguishable on the wire from an ordinary correction.
- A `PLB` credit with reason code `RA`, referencing the original ICN, with no new claim submission at all.

This dataset uses **both conventions**, deliberately, because both are real and a connector has to recognize each pattern rather than look for a flag that doesn't exist.

### Defects the generator injects

| Defect | How it appears |
|---|---|
| J-code / NDC unit mismatch | `svc05_units` and `ctp04_quantity` on the same line describe the same vial on different bases and don't numerically match — legitimate, not a defect to "fix" |
| Missing prior auth | `CO-197` + `N522` on an otherwise paid specialty J-code line |
| Silent replacement | Frequency `7` submitted; a service line present on the original is absent on the replacement and is dropped, not preserved |
| CLP07 discontinuity | Same `CLM01`/`CLP01` across two remittances, different `CLP07` each time — correct behavior a connector must not mistake for two unrelated claims |
| Untraceable provider offset | `PLB` `FB` or `CS` line with no `reference_icn` |
| Appeal via reprocessing | New 837 (freq `7`) with no explicit "appeal" flag anywhere |
| Appeal via PLB credit | `RA` reason code, **negative** amount (a credit — increases the payment, same direction as `L6`), no new claim submitted |
| Duplicate 835 | Same `record_id` (or same `clp07`) emitted twice |

---

## 4. Bank / cash

### Why this is deliberately the poorest feed

A real business bank statement export gives you what a bank gives you, nothing more: `date`, `description`, `amount`, `type`, a `running_balance`, and a bank reference/trace number. It is a CSV, not an EDI standard, and it was never designed to carry claims data — it was designed to reconcile a checking account. Standard bank CSV exports essentially never surface the ACH addenda record that would otherwise carry the payer's own trace number; recovering that needs a treasury-grade banking product most operators don't have. This feed is the weakest link in the dataset by design, because it's the weakest link in the real workflow it's modeling.

### Two identifiers, only sometimes the same trip

Every ACH credit carries a 15-digit **ACH trace number** — 8 digits of the originating bank's routing number followed by a 7-digit sequence number. It is pure banking plumbing: it identifies the transfer to the banking network, carries zero business content, and is **always present**, because the network can't move money without it.

`TRN02` — the reassociation trace number from Section 1's 835 (`trn.reassociation_trace_number`) — is a completely different number, assigned by the *payer*, carrying real business content: it's the thread back to which remittance a deposit belongs to. It only survives to the bank statement if the ACH addenda record made the whole trip intact, which is common but not guaranteed. Semantically the column is "the payer-assigned business reference from the CCD+ addenda": for a claim payment that is the 835 trace number; **for a manufacturer rebate it is the batch's `allocation_code`** (Section 2). Same column, same survival odds, different issuer.

These are two unrelated numbering systems that happen to ride the same wire transfer. A connector that assumes they're interchangeable breaks the day one of them is missing — which, by design, happens often (see Defects, below).

### The three fields that classify a deposit before you parse anything else

Every ACH credit also carries, off the same NACHA CCD+ batch header:

| Field | Format | Notes |
|---|---|---|
| `company_name` | ≤16 chars | Often truncated, e.g. `BLUE HARBOR HEAL` (16 chars of `BLUE HARBOR HEALTH`) |
| `company_id` | 10 chars | Usually `1` + 9-digit EIN, e.g. `1911234567` |
| `company_entry_description` | ≤10 chars | For healthcare claim payments, the mandated literal `HCCLAIMPMT` |

`company_entry_description = HCCLAIMPMT` is a CORE/CAQH mandate for claim EFT payments specifically — it lets a connector classify a bank line as a claim payment before parsing a single dollar amount. A manufacturer rebate deposit is **not** a claim payment under that mandate, so it doesn't carry `HCCLAIMPMT` — it shows up with an ordinary entry description, and `company_name` is the only channel signal you get for it.

### The one forward-looking date in the whole dataset

Everything above is backward-looking — a posting date is when a deposit already landed. The NACHA CCD+ batch header these three fields come from is also where the one documented exception lives: it carries an **Effective Entry Date**, the date the originating bank instructs the funds to settle, typically set 1-2 banking days after the file is built. This value is not new — it's the same one already modeled as `bpr.payment_effective_date` on the PBM 835 (Section 1) and `bpr16_eft_effective_date` on the medical 835 (Section 3).

This is not the kind of forward date the rest of this document forbids. It isn't a projection about an uncertain outcome — "we'll decide by Wednesday" — it's a settlement instruction about a decision already made and already transmitted to the ACH network — "this committed money lands on Wednesday." Commitment, not forecast. That's why it earns a documented exception instead of a redesign.

And by the time it reaches our side of the pipeline, the exception has already resolved. Our bank feed only ever shows `posting_date` — the day the deposit actually appeared, on or after the effective date, never before. So even the one legitimately forward-looking field in this entire dataset is backward-looking again by the time it becomes a row we ingest. `received_at` still does its job.

### Worked example

```csv
posting_date,description,ach_trace_number,trn02,company_name,company_id,company_entry_description,amount,type,running_balance,received_at
2026-03-17,ACH CREDIT,071000301234567,8873020123,MERIDIANRX,1911234567,HCCLAIMPMT,47218.40,CREDIT,1284302.11,2026-03-17T18:05:00Z
2026-03-24,ACH CREDIT,071000305567234,,BLUE HARBOR HEAL,1622109834,HCCLAIMPMT,5242.30,CREDIT,1289544.41,2026-03-24T17:58:00Z
2026-04-02,ACH CREDIT,073000199981122,,VERION PHARMA,1837765021,CCD,18420.00,CREDIT,1307964.41,2026-04-02T18:10:00Z
2026-03-06,ACH DEBIT,071000309912345,7734410098,MERIDIANRX,1911234567,HCCLAIMPMT,-3150.00,DEBIT,1236883.71,2026-03-06T17:50:00Z
```

Read across: line 1 is the pharmacy deposit from Section 1's 835, `trn02` present, trivially matched. Line 2 is a medical deposit for the exact payment amount worked out in Section 3, `trn02` missing — matched on amount and date alone. Line 3 is the 340B rebate batch from Section 2 — for a rebate deposit, `trn02` carries the batch's `allocation_code` when the CCD+ addenda survive (Section 2's join-key ruling); this particular row is one of the ~20% where the addenda dropped, so it is distinguishable only by `company_name`, since `company_entry_description` isn't `HCCLAIMPMT` for a rebate. Lose the addenda *and* fail amount+date and you have D-4, the orphan rebate. Line 4 is a true ACH reversal — a debit, inside 5 banking days of an unrelated earlier credit carrying `trn02 = 7734410098`, for the exact same amount as that credit. Note it is deliberately unrelated to the $412.60 `WO` recoupment from Section 1 — that recoupment is netted, not reversed, and never appears as its own bank line at all (see below).

### Deposits are never combined

The pharmacy deposit, the medical deposit and the rebate deposit are three separate bank lines, always — one sender, one company, one wire, one row. `company_name` tells you which channel a line belongs to before you've matched a single claim.

**Realistic amounts:** individual specialty claims run from hundreds to tens of thousands of dollars each; a payer's batch settlement to a specialty pharmacy plausibly lands anywhere from tens of thousands to several million dollars, depending on how many claims it bundles.

### Negative amounts mean two different things

A **true ACH reversal** is a debit, and it shows up within 5 banking days of the original credit — line 4 above. That's rare and clean.

Far more common: the payer doesn't reverse anything. It **nets** a recoupment straight into a *later* batch, and the bank only ever sees the smaller net amount. The explanation for why that batch is short lives entirely in that batch's 835 `PLB` segment (Sections 1 and 3) — nothing on the bank side says a clawback happened. A connector that only reads bank lines will see an unremarkable deposit and never learn money was taken back.

### Defects the generator injects

| Defect | How it appears |
|---|---|
| Missing TRN | ~20% of credit deposits have `trn02` blank, forcing amount + date matching instead of a direct lookup |
| Netted recoupment | Deposit amount is short with no bank-side explanation; the `WO`/`CS` PLB line on the corresponding 835 is the only record of it |
| Orphan deposit | A credit with no attributable claim, remittance or rebate batch — parked until something resolves it (Section 5) |
| Channel misclassification | A rebate deposit missing `HCCLAIMPMT` gets skipped by a naive "claim payments only" filter |
| True reversal vs. netting confusion | A genuine debit-based reversal exists alongside far more common non-reversed netting, and the two must not be conflated |

---

## 5. The ingestion model

### Event-driven, not scan-driven

None of the four feeds gets scanned for candidates. An inbound document *is* the trigger, and it already carries its own lookup keys:

```
inbound document arrives
   -> parse, extract its keys
   -> fetch ONLY the claim objects those keys resolve to
   -> run deterministic logic on just those
   -> append a new status row per affected claim
```

The latest appended row for a claim is its current status. Nothing is ever updated in place.

### Key resolution map

| Inbound | Keys it carries | Resolves to |
|---|---|---|
| PBM 835 | `clp01_patient_control_number` = `"7845102FILL00"`, parsed to `{rx, fill}`, plus `payee_npi` | Pharmacy claim |
| Medical 835 | `clp01_patient_control_number` = provider claim number | 837 submission |
| 340B / TPA response (pharmacy dispense) | `rx_number` + `ndc_11` + `fill_date` + `pharmacy_npi` | Dispense |
| 340B / TPA response (medical dispense) | `provider_npi` + `ndc_11` + `fill_date` (= service date); `rx_number` is null | 837 submission |
| Bank line | `ach_trace_number` / `trn02` (which for a rebate deposit carries the `allocation_code`) | **A remittance or rebate batch, NOT a claim** |

### The bank line is two hops

A bank deposit carries no claim identifier at all — it can't, a bank has no concept of a claim. `trn02` resolves to a *remittance*; the remittance holds the claim list. One bank line can touch 47 claims, and the only way to find them is through the remittance it points at — never by scanning claims looking for one that might match the amount.

### Parked records, and why step 2 is not optional

When a document's keys resolve to nothing yet, it gets **parked**, not discarded. Every inbound document does two things, not one:

1. Resolve its own keys forward, and touch whatever claims they point at.
2. Check the parked pool backward: does anything sitting there now resolve *against* me?

Skip step 2 and two failure modes become permanent instead of temporary: an out-of-order arrival (a bank deposit that beat its own remittance to the door) never gets a second chance to match, and an unmatched deposit stays unmatched forever even after the record that would explain it finally lands.

**Worked example.** A bank line with `trn02 = 8873020123` arrives Tuesday. Nothing resolves it yet — no remittance carrying that trace number exists in the system. It parks. The remittance arrives Friday, carrying that same `reassociation_trace_number`. It does its normal forward work (resolving its own claims), and it also clears the parked bank line from Tuesday, because the parked pool gets checked against every new arrival, not just future ones.

### Lineage vs. audit trail — two different things, both required

**Lineage runs downward.** Every computed number — a variance, a status, an aging bucket — points back to the exact raw source row that produced it. This is what the assignment actually grades: *"Preserve source identifiers so an investigator can trace a result back to the synthetic source record."*

**Audit trail runs across time.** It's the append-only log of every verdict a claim has held, in order. It falls out for free once the system is append-only rather than update-in-place, and it's what gives `reopened_from` any meaning — a claim can't be "reopened" from a state that was overwritten.

Both are needed and they answer different questions: lineage answers "where did this number come from," audit trail answers "what did we believe, and when did we stop believing it."
