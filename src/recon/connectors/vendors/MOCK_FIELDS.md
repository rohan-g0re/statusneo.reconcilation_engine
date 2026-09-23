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

29 canonical names, 29 `INVENTED`, 0 `SPEC`, 0 `STANDARD`. That count is the **file-vendor**
table only; Beacon has its own table below, for the reason the section gives.

## Beacon, the API vendor

`beacon.py` maps in two directions rather than one, so it needs a table the two columns above
cannot hold: a field is either something we **send** on a submission template (requirement
C2) or something we **read** off an inbound payload (C3). The "Beacon spelling" column is the
name on the wire; the `field` column is this fabric's name for the same fact.

**Names that already appear in the file-vendor table are repeated here, and that is not
duplication.** `ndc11`, `covered_entity_id`, `rebate_amount` and the rest are the same
canonical names — they are listed again because Beacon spells them differently from Verity
and from Craneware, and the whole purpose of this file is to record where a difference in
spelling sits.
Two reconciliations matter more than the others and are the Beacon analogue of the four in
the table above:

| concept | Beacon | this fabric | why they differ |
|---|---|---|---|
| NDC | `ndc_11` | `ndc11` | a third spelling of the same field, after Verity's `ndc_11` and Craneware's `ndc11` |
| the dispense date | `fill_date` on the pharmacy template, `date_of_service` on the medical one | `date_of_service` | one vendor, two templates, two names for one fact; `normalized_record` calls it `date_of_service`, so that wins |

**Almost nothing here can be `SPEC` and the reason is on the record.** `BEACON-001`, `-002`,
`-003` and `-004` are the pharmacy template, the medical template, the validation code
glossary and the back-end validations page. All four are real, public and return HTTP 403; we
did not read them. `tests/test_vendor_evidence.py` fails the build on a `SPEC` row citing any
of the seven, which is the rule doing its job rather than an inconvenience. The `SPEC` rows
below are carried by `DOC2-007`, `DOC2-014` and `BEACON-013` — an assessment document *about*
Beacon, page-cited — and by `VERITY-004`, which is first-hand retrieved text from a TPA that
integrates with Beacon commercially.

### Outbound: what a submission carries (C2)

| field | tier | evidence | Beacon spelling | note |
|---|---|---|---|---|
| `template` | SPEC | BEACON-013 | `template` | "Use Beacon's published pharmacy / medical data templates" — the two templates are Beacon's; the `PHARMACY`/`MEDICAL` spelling is ours |
| `direction` | SPEC | DOC2-007 | `direction` | "Direction — Outbound eligible pharmacy / medical claims" verbatim |
| `covered_entity_id` | INVENTED | — | `covered_entity_id` | the 340B ID; `BEACON-012` says permission is granted per 340B ID, so it is a real field and never null |
| `manufacturer` | INVENTED | — | `manufacturer` | the labeller the rebate will be claimed from |
| `ndc11` | STANDARD | STD-X12-837, STD-NCPDP-TELECOM | `ndc_11` | the drug identifier the 837's loop 2410 LIN02 and the NCPDP telecom claim both carry; the only field on both templates |
| `date_of_service` | INVENTED | — | `fill_date` / `date_of_service` | one fact, two template spellings; carried verbatim, never reformatted, because reformatting it would change the join key |
| `qualification_status` | INVENTED | — | `qualification_status` | rides along because `DOC2-007`'s outbound direction is *eligible* claims; the vocabulary is our TPA's, carried as the feed's word and never re-decided |
| `submission_date` | INVENTED | — | `submission_date` | the rebate request's own date. `BEACON-009`'s 45-day window is adjudicated upstream and arrives as a decision; it is never recomputed here |
| `rx_number` | STANDARD | STD-NCPDP-TELECOM | `rx_number` | the prescription reference number; pharmacy template only, and identifier drift is carried across untouched |
| `pharmacy_npi` | INVENTED | — | `pharmacy_npi` | an NPI, which NCPDP carries behind a service-provider id qualifier; this column name and its presence on the template are ours |
| `prescriber_npi` | INVENTED | — | `prescriber_npi` | the prescriber id qualifier is NCPDP's, this column is ours |
| `provider_npi` | INVENTED | — | `service_provider_npi` | `BEACON-008` names a "service provider ID" and is a search summary, not a source, so this is `INVENTED` rather than `SPEC` |
| `claim_number` | INVENTED | — | `claim_number` | `BEACON-008`'s medical key. Normally null: the claim number lives on the 837 feed and does not reach the 340B sidecar. Null on the wire rather than omitted, so the gap is visible |
| `claim_line_number` | INVENTED | — | `claim_line_number` | `BEACON-008`'s medical key, null for the same reason |
| `hcpcs` | STANDARD | STD-X12-837 | `hcpcs_code` | the SVC01 composite procedure identifier's J-code. Requirement E2; emitted whenever the canonical claim carries one, null until E2 lands |
| `hcpcs_modifier_code` | STANDARD | STD-X12-837 | `hcpcs_modifier_code` | the 837's procedure modifier, which is the vocabulary `BEACON-008` was reaching for |
| `episode_id` | INVENTED | — | not sent | ours entirely, and deliberately never on the wire: it is what a returned Beacon ID is persisted *against*, so it travels on the response row and not in the request |

### Outbound: how the call is addressed (C2, C4)

| field | tier | evidence | Beacon spelling | note |
|---|---|---|---|---|
| `method` | INVENTED | — | — | item 5 of `docs/vendor_evidence/beacon.md`'s UNKNOWN list is "endpoint paths, HTTP verbs, request and response envelopes"; `DOC2-008` says they are not public |
| `path` | INVENTED | — | — | `/v1/claims/pharmacy` and `/v1/claims/medical`. Two paths because `BEACON-001` and `BEACON-002` are separate articles; the paths themselves are ours |
| `idempotency_key` | INVENTED | — | — | the 340B natural key, because `DOC2-002` step 3 asks the adapter build for idempotency and a minted id would differ per run |
| `submission_ownership` | SPEC | DOC2-014, VERITY-004 | — | "Shields direct-to-Beacon vs. PharmaForce-managed submission must be explicitly decided per covered entity" (`DOC2-014`), corroborated first-hand by `VERITY-004`: "Alternatively, CEs can choose to export a Beacon report to manually submit to Beacon." The two member *names* are ours |

### Inbound: the acknowledgement, and the key it publishes (C2, C5)

| field | tier | evidence | Beacon spelling | note |
|---|---|---|---|---|
| `beacon_id` | SPEC | DOC2-007, BEACON-013 | `beacon_id` | `DOC2-007` lists Beacon IDs inbound; `BEACON-013` says "persist Beacon ID against Shields Claim Financial Episode". The ID's **format** is `UNKNOWN` — ours is minted by the orchestrator and only ever copied |
| `received_at` | INVENTED | — | `received_at` | the vendor's own timestamp. There is no clock in this module, so an absent one stays absent |
| `natural_key` | INVENTED | — | `rx_number` / `pharmacy_npi` / `provider_npi` / `ndc_11` / `date_of_service` | the 340B key echoed back; the only thing that can say a receipt belongs to the claim we sent, because 340B has no shared identifier space |

### Inbound: the merged manufacturer decision (C3)

| field | tier | evidence | Beacon spelling | note |
|---|---|---|---|---|
| `manufacturer_status` | INVENTED | — | `manufacturer_decision` | the manufacturer's own verdict, off `rebate_status` |
| `rejection_reason` | INVENTED | — | `manufacturer_reason_code` | **verbatim, never paraphrased and never defaulted.** `BEACON-003` is a 403, so we do not know Beacon's code vocabulary; an absent reason stays `None` rather than becoming a plausible-looking placeholder |
| `manufacturer_decision_source` | INVENTED | — | `decision_source` | which record the verdict was read from, so a decision walks back to one line of the feed |
| `validation_outcome` | INVENTED | — | `outcome` | Beacon's validation verdict, off `validation_outcome`. Kept as its own field and never folded into `manufacturer_status`: they are different judgements by different parties |
| `validation_reason_code` | INVENTED | — | `reason_code` | verbatim, for the same reason as `rejection_reason`. It is our TPA's vocabulary, not Beacon's, and it is not dressed up as Beacon's |
| `validation_outcome_source` | INVENTED | — | `outcome_source` | the `event_type` the verdict came from |
| `validation_decided_at` | INVENTED | — | `decided_at` | the deciding record's own timestamp |
| `rebate_state` | INVENTED | — | `rebate_state` | `PAID` or `UNPAID`, read off the payment join and never inferred from the decision: `APPROVED` with no payment is the missing-rebate exception |
| `rebate_amount` | INVENTED | — | `rebate_amount` | text, copied. Nothing in this package turns an amount into a number |
| `as_of` | INVENTED | — | `as_of` | when the manufacturer spoke, as the payload says |
| `is_reversed` | INVENTED | — | `outcome` = `REVERSED` | requirement B3's flag for this vendor. Beacon's real reversal representation is `UNKNOWN` — `BEACON-003` and `BEACON-004` are both 403s — so this is a decision, declared in `beacon.REVERSAL_REPRESENTATION` |

### Inbound: the rebate batch and its payment reference (C3, C5)

| field | tier | evidence | Beacon spelling | note |
|---|---|---|---|---|
| `payment_reference` | INVENTED | — | `payment_reference` | the manufacturer's own reference. `DOC2-002` step 4 names a payment reference as its own identifier, distinct from `TRN02` and from `ALLOCATION_CODE`; this spelling and its use as a key are ours |
| `payment_effective_date` | INVENTED | — | `payment_effective_date` | when the money is dated. The same canonical name Verity and Craneware reconcile onto |
| `batch_total_amount` | INVENTED | — | `batch_total_amount` | copied from the payload, never summed from the lines beneath it; a second copy free to disagree with the first is what control totals exist to catch |
| `batch_received_at` | INVENTED | — | `batch_received_at` | the batch's own timestamp; a settlement dated to the pull reconciles against the wrong period |
| `dispense_line_count` | INVENTED | — | — | a count of rows, which is the only arithmetic anywhere in this mapping and is not arithmetic on money |

40 Beacon rows: 4 `SPEC`, 4 `STANDARD`, 32 `INVENTED`. Not one of the four `SPEC` rows is
carried by a Beacon page — three come from an assessment document *about* Beacon and one is
co-signed by a TPA's own site. That is the honest ceiling, and it is why the `INVENTED`
column is as long as it is.

## The reversal representation is not a field, and is declared anyway

Requirement B3. `verity.REVERSAL_REPRESENTATION` and `craneware.REVERSAL_REPRESENTATION` are
both `INVENTED` — `DOC2-011` states Verity's is not publicly confirmed in as many words, and
Craneware's is gap 4 of `docs/vendor_evidence/craneware.md`. Neither is re-typed in a mapping
module: both are read from the module that writes the files, so the declaration and the
writer cannot drift apart. If a vendor hands us a specification tomorrow and it disagrees, the
change is one constant and one rule.
