# Field provenance for the secure-file vendor mappings

Requirement M4 and V3 of `docs/connectivity_layer_requirements.md`, applied to the *reading*
side. `src/recon/mocks/MOCK_FIELDS.md` declares where every column the mocks **write** came
from; this file declares where every name those columns are **read into** came from.

`tests/test_vendor_evidence.py` walks both — `MAPPING_DIRS` names this directory explicitly —
so an untagged field fails the build, a `SPEC` row citing an evidence id that is not in
`docs/vendor_evidence/index.jsonl` fails the build, and a `SPEC` row citing one of the seven
403'd Beacon articles fails the build.

## The honest finding, stated before the table

**Every canonical name below is `INVENTED`, and there was never a possibility of anything
else.** A canonical name is a decision *we* make about what to call a thing once it is inside
this system. No vendor publishes it, because no vendor knows this system exists. Tagging one
`SPEC` would be laundering "Verity writes a column called `ndc_11`" into "Verity says the
canonical name is `ndc11`", which is precisely the move the tier system exists to catch.

The vendor-side spellings in the last two columns are **not** claims either. They are the
columns `src/recon/mocks/verity_export.py` and `src/recon/mocks/craneware_export.py` write,
all of them tagged `INVENTED` in that file, for the reason it gives: `VERITY-006` confirms a
*Split Transaction data specification* exists and never publishes it, and `CRANEWARE-001`
names all five reports and the SFTP folder they land in and no column of any of them.

## What the canonical names are actually for

One thing, and it is worth being blunt about it because the table looks like busywork
otherwise: **Verity writes `ndc_11` and Craneware writes `ndc11` for the same concept.** A
connector that assumed one spelling across both vendors reads `None` and joins nothing, and
340B has no shared identifier space, so that join is the only bridge there is. Four rows below
are that kind of reconciliation and the rest are pass-throughs. The four:

| concept | Verity | Craneware | why they differ |
|---|---|---|---|
| NDC | `ndc_11` | `ndc11` | nothing but spelling, and it silently breaks every join |
| the reversal flag | `reversal_status` (empty when not reversed) | `dispense_status` (`ACTIVE` when not reversed) | an export of state against a report run; "no reversal" has two spellings and "reversed" has one, which is why the rule keys on the positive literal |
| when the reversal arrived | `reversal_received_at` | `reversal_date` | Craneware's is a full timestamp despite its name; the canonical name stops it reading as a date |
| when the money is dated | `payment_effective_date` | `rebate_payment_date` | the TPA feed underneath calls it `payment_effective_date`, so that is the canonical spelling |

Two columns are deliberately **not** reconciled, and that is a decision as much as any
mapping is: Verity's `batch_line_manufacturer_status` is read off a payment batch line and
Craneware's `manufacturer_status` is read off a standalone decision event. A dispense paid
straight out of a batch usually has no decision event at all, so collapsing them would either
report every paid rebate as undecided or invent an agreement the feed does not have.

## Not in the table: the file envelope

`record_type`, `record_count`, `declared_record_count` and `schema_version` are file-level
controls rather than claim data, and no mapping reads them into a canonical name. They are
handled by the envelope in `vendors/__init__.py` — mapping a control total onto a dispense is
how a record count quietly becomes a covered entity id. Their shapes are `INVENTED` and are
declared in `src/recon/mocks/MOCK_FIELDS.md`, where they are written.

## Canonical field names

| field | tier | evidence | Verity spelling | Craneware spelling |
|---|---|---|---|---|
| `accumulation_id` | INVENTED | — | `accumulation_id` | not on this report |
| `batch_line_manufacturer_status` | INVENTED | — | `batch_line_manufacturer_status` | deliberately not mapped here |
| `batch_received_at` | INVENTED | — | `batch_received_at` | not on this report |
| `batch_total_rebate_amount` | INVENTED | — | `batch_total_rebate_amount` | not on this report |
| `beacon_id` | INVENTED | — | `beacon_id` | not on this report |
| `covered_entity_id` | INVENTED | — | `covered_entity_id` | `covered_entity_id` |
| `disqualification_reason` | INVENTED | — | not on the mapped datasets | `disqualification_reason` |
| `fill_date` | INVENTED | — | `fill_date` | `fill_date` |
| `hin` | INVENTED | — | `hin` | on Ordering Problems, not mapped |
| `invoice_number` | INVENTED | — | `invoice_number` | not on this report |
| `manufacturer` | INVENTED | — | `manufacturer` | `manufacturer` |
| `manufacturer_decision_status` | INVENTED | — | deliberately not mapped here | `manufacturer_status` |
| `ndc11` | INVENTED | — | `ndc_11` | `ndc11` |
| `payment_effective_date` | INVENTED | — | `payment_effective_date` | `rebate_payment_date` |
| `pharmacy_npi` | INVENTED | — | `pharmacy_npi` | `pharmacy_npi` |
| `prescriber_npi` | INVENTED | — | `prescriber_npi` | not on this report |
| `provider_npi` | INVENTED | — | `provider_npi` | `provider_npi` |
| `qualification_received_at` | INVENTED | — | `qualification_received_at` | not on this report |
| `qualification_status` | INVENTED | — | `qualification_status` | `qualification_status` |
| `rebate_allocation_code` | INVENTED | — | `rebate_allocation_code` | `rebate_allocation_code` |
| `rebate_amount` | INVENTED | — | `invoice_line_amount` | `rebate_amount` |
| `rebate_submitted_date` | INVENTED | — | not on the mapped datasets | `rebate_submitted_date` |
| `rejection_reason` | INVENTED | — | not on the mapped datasets | `rejection_reason` |
| `reversal_quantity` | INVENTED | — | `reversal_quantity` | `reversal_quantity` |
| `reversal_reason` | INVENTED | — | `reversal_reason` | `reversal_reason` |
| `reversal_received_at` | INVENTED | — | `reversal_received_at` | `reversal_date` |
| `reversal_status` | INVENTED | — | `reversal_status` | `dispense_status` |
| `rx_number` | INVENTED | — | `rx_number` | `rx_number` |
| `wholesaler_invoice_number` | INVENTED | — | `wholesaler_invoice_number` | on Ordering Problems, not mapped |

29 canonical names, 29 `INVENTED`, 0 `SPEC`, 0 `STANDARD`.

## The reversal representation is not a field, and is declared anyway

Requirement B3. `verity.REVERSAL_REPRESENTATION` and `craneware.REVERSAL_REPRESENTATION` are
both `INVENTED` — `DOC2-011` states Verity's is not publicly confirmed in as many words, and
Craneware's is gap 4 of `docs/vendor_evidence/craneware.md`. Neither is re-typed in a mapping
module: both are read from the module that writes the files, so the declaration and the
writer cannot drift apart. If a vendor hands us a specification tomorrow and it disagrees, the
change is one constant and one rule.
