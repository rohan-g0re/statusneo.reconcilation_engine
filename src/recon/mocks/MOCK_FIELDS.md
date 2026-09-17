# Field provenance for the vendor mocks

Requirement M4 of `docs/connectivity_layer_requirements.md`, and the artefact requirement V3
is actually about. Every column `verity_export.py` and `craneware_export.py` write, and every
key `beacon_payloads.py` can set, is listed below with one of three tiers: `SPEC` for a field
a cited vendor source names, `STANDARD` for a field that genuinely belongs to a public
standard, and `INVENTED` for our own construction.

**This file is hand-written and never generated.** There is no builder to rerun, for the same
reason `docs/vendor_evidence/index.jsonl` has none: a generated provenance table would derive
its tiers from the code it is supposed to be auditing, which is a table that can only ever
agree with itself. `tests/test_vendor_evidence.py` walks it — an unrecognised tier fails the
build, a `SPEC` row citing an evidence id that is not in the index fails the build, and a
`SPEC` row citing one of the seven 403'd Beacon articles fails the build.

**The honest finding, stated before the tables rather than buried in them: the transport and
the dataset and report names are the vendors'; the fields inside them are overwhelmingly
ours.** 234 of the 247 rows below are `INVENTED`. That is not a gap to be closed by trying
harder. Verity publishes marketing prose and a *reference* to a "Split Transaction data
specification" it does not publish (`VERITY-006`). Craneware's own announcement names all five
reports and the SFTP folder they land in (`CRANEWARE-001`) and no column of any of them.
Beacon's pharmacy template, medical template and validation code glossary are real, public,
and every one of them returns HTTP 403 to automated retrieval (`BEACON-001` … `BEACON-007`).
Writing those field lists from recollection would produce mocks that are *confidently wrong* —
indistinguishable from correct ones in any demo, and wrong in precisely the places a real
integration breaks. So they are written as ours and tagged as ours.

The rule applied for `SPEC`, stated once so the tiering is checkable rather than a matter of
taste: **a field is `SPEC` only when a retrievable source names that field on that payload.**
A source that names the payload kind, or the dataset, or the report, does not thereby name the
fields inside it. That rule is what keeps the `SPEC` count at seven when a looser reading could
have produced thirty.

## Tier summary

Counted from the tables below.

| vendor | rows | SPEC | STANDARD | INVENTED |
|---|---|---|---|---|
| Verity | 110 | 0 | 0 | 110 |
| Craneware | 80 | 0 | 0 | 80 |
| Beacon | 57 | 7 | 6 | 44 |
| **total** | **247** | **7** | **6** | **234** |

Every `SPEC` row cites `DOC2-007` or `BEACON-013`, both `SECOND_HAND` and page-cited. Not one
field in this repository is tagged `SPEC` against a source we retrieved from a vendor
first-hand, because no such source exists for any field.

---

## Verity

`DOC2-009` names the five datasets and the transport. `VERITY-001` confirms Secure Data
Transfer first-hand — automated and encrypted, though not the word SFTP. `VERITY-002` confirms
Split Billing is a real product, which is what the "matches/split transactions" dataset name
refers to, and `VERITY-006` confirms a named Split Transaction data specification exists and
was extended for the rebate model.

`VERITY-006` is also where the evidence stops. That specification is referenced and never
published; no field list for any of the five datasets was retrievable from anywhere. So every
row below is `INVENTED` — not one Verity column has any source at all. The evidence file says
it in as many words: *"every Verity payload field is `INVENTED`."*

The `record_type` / `record_count` pair and the four reversal columns are shared across all
five datasets, and they carry the same meaning everywhere; their notes repeat because the
columns genuinely do.

### `accumulations`

| field | tier | evidence | note |
|---|---|---|---|
| `verity.accumulations.record_type` | INVENTED | — | control column, DETAIL or TRAILER; DOC2-010 asks for source-control totals and publishes no shape for one |
| `verity.accumulations.record_count` | INVENTED | — | populated only on the trailer; a count of detail rows and never a sum, because this package may not add money |
| `verity.accumulations.accumulation_id` | INVENTED | — | minted by the orchestrator under M1 and read back off the sidecar; Verity's own accumulation id format is unknown |
| `verity.accumulations.beacon_id` | INVENTED | — | the orchestrator's minted Beacon ID, carried so a Verity accumulation joins to a Beacon submission |
| `verity.accumulations.covered_entity_id` | INVENTED | — | the 340B ID the dispense was qualified under, copied from the sidecar |
| `verity.accumulations.manufacturer` | INVENTED | — | the labeller the rebate is owed by, copied from the sidecar |
| `verity.accumulations.ndc_11` | INVENTED | — | the 11-digit NDC as the TPA feed spells it; the value is a drug identifier but the column is ours |
| `verity.accumulations.fill_date` | INVENTED | — | YYYYMMDD as the wire spells it, never parsed and never reformatted |
| `verity.accumulations.rx_number` | INVENTED | — | drift included; repairing it here would delete the crosswalk miss defect D-6 exists to produce |
| `verity.accumulations.pharmacy_npi` | INVENTED | — | null on a medically-administered dispense, which is a real state and is kept as one |
| `verity.accumulations.provider_npi` | INVENTED | — | present on every dataset even where it is always null, so a consumer never special-cases per file |
| `verity.accumulations.prescriber_npi` | INVENTED | — | read back from the QUALIFICATION_DECISION event; the Dispense object does not surface it |
| `verity.accumulations.hin` | INVENTED | — | Health Industry Number, read back from the QUALIFICATION_DECISION event |
| `verity.accumulations.wholesaler_invoice_number` | INVENTED | — | read back from the QUALIFICATION_DECISION event; the wholesaler's invoice, not Verity's invoice_number |
| `verity.accumulations.qualification_status` | INVENTED | — | the TPA's own verdict read back; null while the dispense is still in review |
| `verity.accumulations.qualification_received_at` | INVENTED | — | the QUALIFICATION_DECISION event's own received_at; empty when no decision arrived |
| `verity.accumulations.reversal_status` | INVENTED | — | REVERSED when a DISPENSE_REVERSAL event exists; the STATUS_FLAG_ROW representation declared below |
| `verity.accumulations.reversal_received_at` | INVENTED | — | the DISPENSE_REVERSAL event's own received_at, copied |
| `verity.accumulations.reversal_reason` | INVENTED | — | the reversal event's reversal_reason verbatim; RETURN_TO_STOCK is the TPA's vocabulary, not Verity's |
| `verity.accumulations.reversal_quantity` | INVENTED | — | the feed's already-negative quantity_dispensed, copied and never re-negated |

### `contract_pharmacy_claims_backing`

| field | tier | evidence | note |
|---|---|---|---|
| `verity.contract_pharmacy_claims_backing.record_type` | INVENTED | — | control column, DETAIL or TRAILER |
| `verity.contract_pharmacy_claims_backing.record_count` | INVENTED | — | populated only on the trailer; the count F2 reconciles against what was ingested |
| `verity.contract_pharmacy_claims_backing.beacon_id` | INVENTED | — | the minted Beacon ID; every dispense appears in this dataset, so this is the complete id list |
| `verity.contract_pharmacy_claims_backing.accumulation_id` | INVENTED | — | minted under M1; present even on dispenses that never accumulated |
| `verity.contract_pharmacy_claims_backing.invoice_number` | INVENTED | — | minted under M1; present on every backing row whether or not a rebate was ever invoiced |
| `verity.contract_pharmacy_claims_backing.covered_entity_id` | INVENTED | — | the 340B ID, copied from the sidecar |
| `verity.contract_pharmacy_claims_backing.manufacturer` | INVENTED | — | the labeller the rebate is owed by |
| `verity.contract_pharmacy_claims_backing.ndc_11` | INVENTED | — | the drug identifier as the TPA feed spells it |
| `verity.contract_pharmacy_claims_backing.fill_date` | INVENTED | — | YYYYMMDD, straight off the wire |
| `verity.contract_pharmacy_claims_backing.rx_number` | INVENTED | — | drift included, so a claim that cannot be joined stays unjoinable here too |
| `verity.contract_pharmacy_claims_backing.pharmacy_npi` | INVENTED | — | null on a medical-benefit dispense |
| `verity.contract_pharmacy_claims_backing.provider_npi` | INVENTED | — | carries the identity when there is no prescription to key on |
| `verity.contract_pharmacy_claims_backing.claim_archetype` | INVENTED | — | our own label and not a Verity field; Dispense.archetype read back, so M5's four archetypes are checkable by opening the file |
| `verity.contract_pharmacy_claims_backing.qualification_status` | INVENTED | — | the TPA's verdict, read back |
| `verity.contract_pharmacy_claims_backing.disqualification_reason` | INVENTED | — | the qualification event's own reason string, verbatim under C3 |
| `verity.contract_pharmacy_claims_backing.manufacturer_decision_status` | INVENTED | — | read off the MANUFACTURER_DECISION event only; an approved-and-paid dispense has no such event and shows empty here |
| `verity.contract_pharmacy_claims_backing.rejection_reason` | INVENTED | — | the manufacturer's word verbatim, never reworded |
| `verity.contract_pharmacy_claims_backing.rebate_request_submitted_date` | INVENTED | — | the REBATE_REQUEST event's submission_date; the 45-day window is adjudicated upstream, never here |
| `verity.contract_pharmacy_claims_backing.qualification_received_at` | INVENTED | — | the qualification event's received_at |
| `verity.contract_pharmacy_claims_backing.rebate_request_received_at` | INVENTED | — | the rebate request event's received_at; empty when no request was ever made |
| `verity.contract_pharmacy_claims_backing.manufacturer_decision_received_at` | INVENTED | — | the decision event's received_at; empty when nobody has decided |
| `verity.contract_pharmacy_claims_backing.reversal_status` | INVENTED | — | REVERSED when a DISPENSE_REVERSAL event exists |
| `verity.contract_pharmacy_claims_backing.reversal_received_at` | INVENTED | — | the reversal event's own received_at |
| `verity.contract_pharmacy_claims_backing.reversal_reason` | INVENTED | — | the reversal event's reversal_reason, verbatim |
| `verity.contract_pharmacy_claims_backing.reversal_quantity` | INVENTED | — | already signed negative on the feed; copied, never re-signed |

### `invoices`

| field | tier | evidence | note |
|---|---|---|---|
| `verity.invoices.record_type` | INVENTED | — | control column, DETAIL or TRAILER |
| `verity.invoices.record_count` | INVENTED | — | a count of detail rows; the sum that would be more useful is forbidden, so batch_total_rebate_amount carries a declared figure instead |
| `verity.invoices.invoice_number` | INVENTED | — | minted under M1; the leading column because this dataset is keyed by it |
| `verity.invoices.beacon_id` | INVENTED | — | the minted Beacon ID, so an invoice line joins to a Beacon payment reference |
| `verity.invoices.accumulation_id` | INVENTED | — | minted under M1 |
| `verity.invoices.covered_entity_id` | INVENTED | — | the 340B ID the rebate is owed to |
| `verity.invoices.manufacturer` | INVENTED | — | the labeller the rebate is owed by |
| `verity.invoices.ndc_11` | INVENTED | — | the drug identifier as the TPA feed spells it |
| `verity.invoices.fill_date` | INVENTED | — | YYYYMMDD, straight off the wire |
| `verity.invoices.rx_number` | INVENTED | — | drift included |
| `verity.invoices.pharmacy_npi` | INVENTED | — | null on a medical-benefit dispense |
| `verity.invoices.provider_npi` | INVENTED | — | null on a pharmacy dispense |
| `verity.invoices.rebate_allocation_code` | INVENTED | — | the batch's allocation_code, the same string the manufacturer's ACH credit carries in trn02 |
| `verity.invoices.batch_line_manufacturer_status` | INVENTED | — | read off the REBATE_PAYMENT_BATCH line rather than the decision event, because a paid dispense usually has no decision event |
| `verity.invoices.invoice_line_amount` | INVENTED | — | text copy of the batch line's own rebate_amount, never re-derived |
| `verity.invoices.batch_total_rebate_amount` | INVENTED | — | text copy of the batch's own total_rebate_amount, carried so F2 has a declared figure this module never computed |
| `verity.invoices.payment_effective_date` | INVENTED | — | the batch's own payment_effective_date, copied |
| `verity.invoices.batch_received_at` | INVENTED | — | the batch record's received_at |
| `verity.invoices.reversal_status` | INVENTED | — | REVERSED on a paid dispense that was later reversed; the amount beside it is not negated |
| `verity.invoices.reversal_received_at` | INVENTED | — | the reversal event's own received_at |
| `verity.invoices.reversal_reason` | INVENTED | — | the reversal event's reversal_reason, verbatim |
| `verity.invoices.reversal_quantity` | INVENTED | — | already signed negative on the feed; copied as it stands |

### `split_transactions`

| field | tier | evidence | note |
|---|---|---|---|
| `verity.split_transactions.record_type` | INVENTED | — | control column, DETAIL or TRAILER |
| `verity.split_transactions.record_count` | INVENTED | — | populated only on the trailer |
| `verity.split_transactions.beacon_id` | INVENTED | — | the minted Beacon ID |
| `verity.split_transactions.accumulation_id` | INVENTED | — | minted under M1 |
| `verity.split_transactions.covered_entity_id` | INVENTED | — | the 340B ID |
| `verity.split_transactions.manufacturer` | INVENTED | — | the labeller whose outcome this row records |
| `verity.split_transactions.match_key` | INVENTED | — | the natural 340B key pipe-joined with its gaps left in, so a failed match reads without reassembling five columns |
| `verity.split_transactions.rx_number` | INVENTED | — | printed beside match_key so a reviewer can see which component drifted |
| `verity.split_transactions.pharmacy_npi` | INVENTED | — | key component, null on a medical-benefit dispense |
| `verity.split_transactions.provider_npi` | INVENTED | — | key component, null on a pharmacy dispense |
| `verity.split_transactions.ndc_11` | INVENTED | — | key component |
| `verity.split_transactions.fill_date` | INVENTED | — | key component, YYYYMMDD |
| `verity.split_transactions.qualification_status` | INVENTED | — | the TPA's verdict, read back |
| `verity.split_transactions.manufacturer_decision_status` | INVENTED | — | from the MANUFACTURER_DECISION event; a separate column from the batch line's status because the two sources are not interchangeable |
| `verity.split_transactions.rejection_reason` | INVENTED | — | the manufacturer's word verbatim |
| `verity.split_transactions.batch_line_manufacturer_status` | INVENTED | — | from the REBATE_PAYMENT_BATCH line; collapsing it into the column above would invent an agreement the feed does not have |
| `verity.split_transactions.rebate_allocation_code` | INVENTED | — | the batch's allocation_code |
| `verity.split_transactions.matched_rebate_amount` | INVENTED | — | text copy of the batch line's own rebate_amount; the same figure as invoices.invoice_line_amount, not a second derivation of it |
| `verity.split_transactions.manufacturer_decision_received_at` | INVENTED | — | the decision event's received_at |
| `verity.split_transactions.reversal_status` | INVENTED | — | REVERSED when a DISPENSE_REVERSAL event exists |
| `verity.split_transactions.reversal_received_at` | INVENTED | — | the reversal event's own received_at |
| `verity.split_transactions.reversal_reason` | INVENTED | — | the reversal event's reversal_reason, verbatim |
| `verity.split_transactions.reversal_quantity` | INVENTED | — | already signed negative on the feed; copied as it stands |

### `unmatched_claims`

| field | tier | evidence | note |
|---|---|---|---|
| `verity.unmatched_claims.record_type` | INVENTED | — | control column, DETAIL or TRAILER |
| `verity.unmatched_claims.record_count` | INVENTED | — | populated only on the trailer |
| `verity.unmatched_claims.beacon_id` | INVENTED | — | the minted Beacon ID, carried even though nothing has come back against it |
| `verity.unmatched_claims.accumulation_id` | INVENTED | — | minted under M1 |
| `verity.unmatched_claims.invoice_number` | INVENTED | — | minted under M1; populated on a row that has no invoice, which is the point of minting it upstream |
| `verity.unmatched_claims.covered_entity_id` | INVENTED | — | the 340B ID |
| `verity.unmatched_claims.manufacturer` | INVENTED | — | the labeller nothing has been heard from |
| `verity.unmatched_claims.ndc_11` | INVENTED | — | the drug identifier as the TPA feed spells it |
| `verity.unmatched_claims.fill_date` | INVENTED | — | YYYYMMDD, straight off the wire |
| `verity.unmatched_claims.rx_number` | INVENTED | — | drift included, and drift is one of the reasons a claim lands in this dataset |
| `verity.unmatched_claims.pharmacy_npi` | INVENTED | — | null on a medical-benefit dispense |
| `verity.unmatched_claims.provider_npi` | INVENTED | — | null on a pharmacy dispense |
| `verity.unmatched_claims.qualification_status` | INVENTED | — | may be null here: a dispense still in review is unmatched and has no verdict yet |
| `verity.unmatched_claims.disqualification_reason` | INVENTED | — | the qualification event's own reason string, verbatim |
| `verity.unmatched_claims.qualification_received_at` | INVENTED | — | the qualification event's received_at |
| `verity.unmatched_claims.rebate_request_received_at` | INVENTED | — | the rebate request event's received_at; empty when nothing was ever submitted |
| `verity.unmatched_claims.reversal_status` | INVENTED | — | empty on every row by construction: Dispense.archetype gives reversed precedence, so an unmatched claim is never a reversed one |
| `verity.unmatched_claims.reversal_received_at` | INVENTED | — | empty on every row, for the reason above |
| `verity.unmatched_claims.reversal_reason` | INVENTED | — | empty on every row; the column is present anyway so the five datasets share one column discipline |
| `verity.unmatched_claims.reversal_quantity` | INVENTED | — | empty on every row; a column that vanished when unused is a column a consumer has to special-case per file |

---

## Craneware

`CRANEWARE-001` is the strongest first-hand vendor evidence in the whole directory: Craneware's
own 14 June 2023 announcement, retrieved verbatim, naming all five reports and the SFTP folder
they are delivered to. Doc 2 did not paraphrase it loosely — it tracked the source. The report
names below are Craneware's own wording, including the asymmetry where "Claims Report" and
"Bulk Dispensations Report" carry the word *Report* and the other three do not.

That is where it ends. No column of any of the five reports is published. `CRANEWARE-002`
gestures at two fields of one report — that Unreplenished Costs is keyed by NDC and filterable
by replenishment status — and it is a search-index summary rather than a retrieved page, so
`ndc11` and `replenishment_status` are tagged `INVENTED` like everything else. The `SPEC` tier
requires a cited vendor source and a search summary is not one. Every row below is `INVENTED`.

`record_type`, `schema_version` and `declared_record_count` wrap every report: two in front and
one at the back, so a reader slicing the controls off takes a contiguous window. `CRANEWARE-004`
asks for file-level controls and file schema versioning; Craneware's format for either is
unknown, so both are ours.

### `claims_report`

| field | tier | evidence | note |
|---|---|---|---|
| `craneware.claims_report.record_type` | INVENTED | — | DETAIL or TRAILER, placed where a plain DictReader finds it rather than in a comment line a reader has to know to skip |
| `craneware.claims_report.schema_version` | INVENTED | — | craneware-sftp-export-1.0.0 on every row; the string a schema registry under B1 would key this layout on |
| `craneware.claims_report.covered_entity_id` | INVENTED | — | the 340B ID the dispense was qualified under |
| `craneware.claims_report.rx_number` | INVENTED | — | drift included |
| `craneware.claims_report.pharmacy_npi` | INVENTED | — | null on a medically-administered dispense |
| `craneware.claims_report.provider_npi` | INVENTED | — | carries the identity when there is no prescription |
| `craneware.claims_report.ndc11` | INVENTED | — | spelled without the underscore on this vendor, matching the report's own column style rather than the feed's |
| `craneware.claims_report.fill_date` | INVENTED | — | YYYYMMDD, straight off the wire |
| `craneware.claims_report.manufacturer` | INVENTED | — | the labeller the rebate is owed by |
| `craneware.claims_report.qualification_status` | INVENTED | — | the TPA's verdict, read back |
| `craneware.claims_report.disqualification_reason` | INVENTED | — | the qualification event's own reason string, verbatim |
| `craneware.claims_report.manufacturer_status` | INVENTED | — | from the MANUFACTURER_DECISION event; empty on an approved-and-paid dispense, which carries its status on the batch line |
| `craneware.claims_report.rejection_reason` | INVENTED | — | the manufacturer's word verbatim, never paraphrased |
| `craneware.claims_report.dispense_status` | INVENTED | — | ACTIVE or REVERSED; a rename of is_reversed, which is a DISPENSE_REVERSAL lookup and not a verdict |
| `craneware.claims_report.reversal_date` | INVENTED | — | the reversal event's own received_at |
| `craneware.claims_report.reversal_reason` | INVENTED | — | the reversal event's reversal_reason, verbatim |
| `craneware.claims_report.reversal_quantity` | INVENTED | — | already signed negative on the TPA feed; copied, never re-signed |
| `craneware.claims_report.rebate_submitted_date` | INVENTED | — | the REBATE_REQUEST event's submission_date |
| `craneware.claims_report.rebate_amount` | INVENTED | — | text copy of the payment batch line's own figure; a reversed-and-paid row keeps the amount it was paid |
| `craneware.claims_report.rebate_payment_date` | INVENTED | — | the batch's own payment_effective_date |
| `craneware.claims_report.rebate_allocation_code` | INVENTED | — | the batch's allocation_code, the same string trn02 carries on the ACH credit |
| `craneware.claims_report.declared_record_count` | INVENTED | — | on the trailer only; counts the detail rows above it and does not count itself |

### `ordering_problems`

| field | tier | evidence | note |
|---|---|---|---|
| `craneware.ordering_problems.record_type` | INVENTED | — | DETAIL or TRAILER |
| `craneware.ordering_problems.schema_version` | INVENTED | — | craneware-sftp-export-1.0.0 on every row |
| `craneware.ordering_problems.covered_entity_id` | INVENTED | — | the 340B ID |
| `craneware.ordering_problems.rx_number` | INVENTED | — | drift included |
| `craneware.ordering_problems.pharmacy_npi` | INVENTED | — | null on a medical-benefit dispense |
| `craneware.ordering_problems.provider_npi` | INVENTED | — | null on a pharmacy dispense |
| `craneware.ordering_problems.ndc11` | INVENTED | — | the drug identifier |
| `craneware.ordering_problems.fill_date` | INVENTED | — | YYYYMMDD |
| `craneware.ordering_problems.manufacturer` | INVENTED | — | the labeller involved in the failure, where the failure was a rebate rejection |
| `craneware.ordering_problems.problem_stage` | INVENTED | — | QUALIFICATION or MANUFACTURER_REBATE; the report name is Craneware's and this reading of it is our inference |
| `craneware.ordering_problems.problem_status` | INVENTED | — | the failing status verbatim, NOT_QUALIFIED or REJECTED; selected by reading the two status fields, not by archetype, so a claim that failed both gates is not swallowed |
| `craneware.ordering_problems.problem_code` | INVENTED | — | the reason string verbatim under C3; a reason we reworded is a reason the vendor cannot be held to |
| `craneware.ordering_problems.hin` | INVENTED | — | read back from the QUALIFICATION_DECISION event; here rather than on Claims because it is one of two fields that make the report name mean anything in replenishment terms |
| `craneware.ordering_problems.wholesaler_invoice_number` | INVENTED | — | read back from the QUALIFICATION_DECISION event, for the same reason |
| `craneware.ordering_problems.reported_date` | INVENTED | — | the received_at of whichever event carried the failure: the qualification event for a qualification problem, the decision event for a rebate one |
| `craneware.ordering_problems.declared_record_count` | INVENTED | — | on the trailer only; a dispense that failed both gates contributes two rows to this count |

### `unreplenished_costs`

| field | tier | evidence | note |
|---|---|---|---|
| `craneware.unreplenished_costs.record_type` | INVENTED | — | DETAIL or TRAILER |
| `craneware.unreplenished_costs.schema_version` | INVENTED | — | craneware-sftp-export-1.0.0 on every row |
| `craneware.unreplenished_costs.ndc11` | INVENTED | — | the leading column because CRANEWARE-002 summarises the real report as keyed by NDC; that entry is a search-index summary, so this is INVENTED and not SPEC |
| `craneware.unreplenished_costs.covered_entity_id` | INVENTED | — | the 340B ID carrying the unreplenished cost |
| `craneware.unreplenished_costs.rx_number` | INVENTED | — | drift included |
| `craneware.unreplenished_costs.pharmacy_npi` | INVENTED | — | null on a medical-benefit dispense |
| `craneware.unreplenished_costs.provider_npi` | INVENTED | — | null on a pharmacy dispense |
| `craneware.unreplenished_costs.fill_date` | INVENTED | — | YYYYMMDD |
| `craneware.unreplenished_costs.manufacturer` | INVENTED | — | the labeller the unpaid rebate is owed by |
| `craneware.unreplenished_costs.replenishment_status` | INVENTED | — | REBATE_REJECTED, SUBMITTED_AWAITING_PAYMENT or AWAITING_SUBMISSION; CRANEWARE-002 suggests the filter exists and the three values are ours, each a single-field read |
| `craneware.unreplenished_costs.qualification_status` | INVENTED | — | always QUALIFIED on this report: the population is qualified and unpaid, which is narrower than the unmatched archetype on purpose |
| `craneware.unreplenished_costs.rebate_submitted_date` | INVENTED | — | the REBATE_REQUEST event's submission_date; empty on a row still awaiting submission |
| `craneware.unreplenished_costs.declared_record_count` | INVENTED | — | on the trailer only |

### `audit`

| field | tier | evidence | note |
|---|---|---|---|
| `craneware.audit.record_type` | INVENTED | — | DETAIL or TRAILER |
| `craneware.audit.schema_version` | INVENTED | — | craneware-sftp-export-1.0.0 on every row |
| `craneware.audit.event_sequence` | INVENTED | — | position within one claim's history, restarting at 1 per dispense; source.py already sorted the events by received_at, so this numbers the trail rather than imposing an order on it |
| `craneware.audit.record_id` | INVENTED | — | the TPA event's own record_id, copied, so an audit row walks back to one line of tpa_340b_events.jsonl |
| `craneware.audit.source_system` | INVENTED | — | the TPA event's own source_system |
| `craneware.audit.event_type` | INVENTED | — | the feed's own event type name; this is the one report where a reversal appears as its own row, because a journal is not a snapshot |
| `craneware.audit.event_timestamp` | INVENTED | — | the event's received_at; there is no clock in this module |
| `craneware.audit.covered_entity_id` | INVENTED | — | read off the event record rather than the dispense, so the row is the event as it arrived |
| `craneware.audit.rx_number` | INVENTED | — | read off the event record, drift included |
| `craneware.audit.pharmacy_npi` | INVENTED | — | read off the event record |
| `craneware.audit.provider_npi` | INVENTED | — | read off the event record |
| `craneware.audit.ndc11` | INVENTED | — | read off the event record's ndc_11 and renamed to this vendor's spelling |
| `craneware.audit.fill_date` | INVENTED | — | read off the event record |
| `craneware.audit.event_outcome` | INVENTED | — | the first of qualification_status then manufacturer_status the event actually carries; a fixed key list searched in order is a lookup, not an interpretation |
| `craneware.audit.event_detail` | INVENTED | — | the first of disqualification_reason, rejection_reason, reversal_reason, submission_date the event carries; an event with none of them gets an empty cell rather than an invented one |
| `craneware.audit.declared_record_count` | INVENTED | — | on the trailer only; counts event rows, so it is larger than the Claims Report's count |

### `bulk_dispensations_report`

| field | tier | evidence | note |
|---|---|---|---|
| `craneware.bulk_dispensations_report.record_type` | INVENTED | — | DETAIL or TRAILER |
| `craneware.bulk_dispensations_report.schema_version` | INVENTED | — | craneware-sftp-export-1.0.0 on every row |
| `craneware.bulk_dispensations_report.covered_entity_id` | INVENTED | — | part of the grouping key, not a value carried along |
| `craneware.bulk_dispensations_report.ndc11` | INVENTED | — | part of the grouping key |
| `craneware.bulk_dispensations_report.manufacturer` | INVENTED | — | part of the grouping key, so no row ever has to choose between two manufacturers for the same NDC |
| `craneware.bulk_dispensations_report.dispense_count` | INVENTED | — | an integer count of rows in the group; counting rows is not arithmetic on money, which is why this report has no amount total |
| `craneware.bulk_dispensations_report.paid_count` | INVENTED | — | a tally of Dispense.archetype, written even when zero |
| `craneware.bulk_dispensations_report.rejected_count` | INVENTED | — | a tally of Dispense.archetype, written even when zero |
| `craneware.bulk_dispensations_report.reversed_count` | INVENTED | — | a tally of Dispense.archetype; the same read-back by_archetype filters on, so a coverage test and this report agree by construction |
| `craneware.bulk_dispensations_report.unmatched_count` | INVENTED | — | a tally of Dispense.archetype; a zero that is written down is a fact, a column that disappears is not |
| `craneware.bulk_dispensations_report.earliest_fill_date` | INVENTED | — | min over the wire's own YYYYMMDD strings; fixed width with no separator, so lexicographic order is chronological and no date is parsed |
| `craneware.bulk_dispensations_report.latest_fill_date` | INVENTED | — | max over the same strings; selecting an existing value is not deriving one |
| `craneware.bulk_dispensations_report.declared_record_count` | INVENTED | — | on the trailer only; counts groups, not dispenses |

---

## Beacon

Beacon is the only vendor with any `SPEC` rows, and all seven are carried by `DOC2-007` and
`BEACON-013` — an assessment document about Beacon, page-cited, not a Beacon page. The Beacon
pages that would have given a field list are `BEACON-001` through `BEACON-007`, and every one
of them is a 403. `tests/test_vendor_evidence.py` has a test whose entire job is to fail the
build if any of those seven ids ever appears in a `SPEC` row here, because citing a page we
could not read would launder *this page exists* into *this page says what I wrote*.

What `DOC2-007` genuinely gives is direction, and it gives it field-shaped: *"Direction —
Outbound eligible pharmacy / medical claims; inbound acknowledgements, validation outcomes,
Beacon IDs, rebate status and reconciliation data."* That single sentence is the source of five
of the seven `SPEC` rows. `BEACON-013`'s *"Use Beacon's published pharmacy / medical data
templates; persist Beacon ID against Shields Claim Financial Episode"* carries the other two.

Six rows are `STANDARD`. `standards.md` is explicit about two of them: the HCPCS J-code and its
modifier "are not our invention, and tagging them `INVENTED` would be inaccurate in the
opposite direction." The NDC is the 837's loop-2410 drug identifier and the NCPDP telecom
claim's, and the prescription reference number is NCPDP's. Everything else on these payloads is
ours, including the envelope field `payload_kind`, the outcome vocabulary, and the shape of the
Beacon ID itself.

`BEACON-008` — a search engine's summary of the medical template — is used as a design hint and
nothing more. Every field taken from it is `INVENTED`, and four of the six are emitted as
`None`, which is the honest state rather than a template that looks complete.

### `submission`

Outbound. Carries no `beacon_id`: Beacon assigns that on receipt, and a request that already
knew its own id would make the acknowledgement decorative and delete C2's acceptance test.

| field | tier | evidence | note |
|---|---|---|---|
| `beacon.submission.payload_kind` | INVENTED | — | our envelope field; the five kind names follow DOC2-007's enumeration but Beacon publishes no envelope at all |
| `beacon.submission.direction` | SPEC | DOC2-007 | "Direction — Outbound eligible pharmacy / medical claims" verbatim |
| `beacon.submission.template` | SPEC | BEACON-013 | "Use Beacon's published pharmacy / medical data templates" — the two templates are Beacon's; the PHARMACY and MEDICAL spelling is ours |
| `beacon.submission.covered_entity_id` | INVENTED | — | the 340B ID; BEACON-012 says permission is granted per 340B ID, which is why it is a real field here and never null |
| `beacon.submission.manufacturer` | INVENTED | — | the labeller the rebate will be claimed from |
| `beacon.submission.submission_date` | INVENTED | — | the REBATE_REQUEST event's submission_date; the 45-day window is adjudicated upstream and arrives here as a decision, never recomputed |
| `beacon.submission.qualification_status` | INVENTED | — | rides along because DOC2-007's outbound direction is eligible claims; the vocabulary is our TPA's, carried as the feed's word |
| `beacon.submission.rx_number` | STANDARD | STD-NCPDP-TELECOM | the prescription reference number; pharmacy template only, and drift is carried across untouched |
| `beacon.submission.pharmacy_npi` | INVENTED | — | an NPI, which NCPDP carries behind a service-provider id qualifier; the column name and its presence on this template are ours |
| `beacon.submission.prescriber_npi` | INVENTED | — | read back from the QUALIFICATION_DECISION event; the prescriber id qualifier is NCPDP's, this column is ours |
| `beacon.submission.ndc_11` | STANDARD | STD-X12-837, STD-NCPDP-TELECOM | the drug identifier the 837's loop 2410 LIN02 and the NCPDP telecom claim both carry; the only field on both templates |
| `beacon.submission.fill_date` | INVENTED | — | date of service on the pharmacy template, spelled as the TPA feed spells it |
| `beacon.submission.claim_number` | INVENTED | — | BEACON-008's medical key, emitted as null: the claim number lives on the 837 feed and never reaches the 340B sidecar. Null rather than absent, so the gap is on the wire |
| `beacon.submission.claim_line_number` | INVENTED | — | BEACON-008's medical key, emitted as null for the same reason |
| `beacon.submission.service_provider_npi` | INVENTED | — | BEACON-008 names a service provider id and is a search summary, not a source; populated from provider_npi |
| `beacon.submission.hcpcs_code` | STANDARD | STD-X12-837 | the SVC01 composite procedure identifier's J-code; emitted as null because this sidecar row carries no resolved drug and a formatter may not reach into reference data to derive one. E2 itself landed in wave 5 -- an earlier version of this cell said it had not |
| `beacon.submission.hcpcs_modifier_code` | STANDARD | STD-X12-837 | the 837's procedure modifier, which is the vocabulary BEACON-008 was reaching for; null for the same reason |
| `beacon.submission.date_of_service` | INVENTED | — | the medical template's fill date under BEACON-008's spelling; a search summary is not a cited source |

### `acknowledgement`

Inbound. The one payload whose whole content is the identifier.

| field | tier | evidence | note |
|---|---|---|---|
| `beacon.acknowledgement.payload_kind` | INVENTED | — | our envelope field |
| `beacon.acknowledgement.direction` | SPEC | DOC2-007 | "inbound acknowledgements" verbatim, from the same Direction sentence |
| `beacon.acknowledgement.beacon_id` | SPEC | DOC2-007, BEACON-013 | DOC2-007 lists Beacon IDs inbound and BEACON-013 says persist Beacon ID against the Shields Claim Financial Episode. The ID's FORMAT is unknown; ours is minted by the orchestrator under M1 and copied here, never generated by the mock |
| `beacon.acknowledgement.status` | INVENTED | — | the constant RECEIVED; an acknowledgement means received and not accepted, and every verdict lives on validation_outcome where it can be traced to the record that decided it |
| `beacon.acknowledgement.template` | INVENTED | — | echoed so a reader can tell the two templates apart without rejoining; Beacon publishes no acknowledgement shape, so the echo is our design |
| `beacon.acknowledgement.covered_entity_id` | INVENTED | — | the 340B ID, echoed back |
| `beacon.acknowledgement.received_at` | INVENTED | — | the earliest received_at on the dispense's events, taken as the first element of an already sorted list; there is no clock in this module |
| `beacon.acknowledgement.rx_number` | STANDARD | STD-NCPDP-TELECOM | the prescription reference number, echoed as one component of the natural key |
| `beacon.acknowledgement.pharmacy_npi` | INVENTED | — | key component; an NPI, but the column and its presence here are ours |
| `beacon.acknowledgement.provider_npi` | INVENTED | — | key component, carrying the identity when there is no prescription |
| `beacon.acknowledgement.ndc_11` | STANDARD | STD-X12-837, STD-NCPDP-TELECOM | key component; the drug identifier both standards carry |
| `beacon.acknowledgement.date_of_service` | INVENTED | — | the key's fill_date under Beacon's spelling; the natural key is the only correlation handle 340B has, so it is echoed with its gaps and its drift intact |

### `validation_outcome`

| field | tier | evidence | note |
|---|---|---|---|
| `beacon.validation_outcome.payload_kind` | INVENTED | — | our envelope field; DOC2-007's phrase is "validation outcomes" and this is the name we gave the payload |
| `beacon.validation_outcome.direction` | SPEC | DOC2-007 | "inbound ... validation outcomes" verbatim |
| `beacon.validation_outcome.beacon_id` | INVENTED | — | repeating the id on this payload is our design; DOC2-007 names Beacon IDs inbound but not which responses carry them |
| `beacon.validation_outcome.template` | INVENTED | — | echoed; Beacon publishes no response envelope |
| `beacon.validation_outcome.outcome` | INVENTED | — | one of VALIDATION_OUTCOMES; BEACON-003, the pharmacy claims validation code glossary, is a 403, so the vocabulary is ours and the precedence mirrors Dispense.archetype exactly |
| `beacon.validation_outcome.reason_code` | INVENTED | — | the TPA's own string carried verbatim under C3 — NON_CONFORMING_45_DAY, PRESCRIBER_NOT_AFFILIATED, RETURN_TO_STOCK — and deliberately not dressed up as a Beacon code we cannot see |
| `beacon.validation_outcome.outcome_source` | INVENTED | — | the event_type the verdict was read from, so every response walks back to one line of tpa_340b_events.jsonl; this is what makes M3's acceptance checkable rather than asserted |
| `beacon.validation_outcome.decided_at` | INVENTED | — | the deciding record's own received_at; a batch-sourced decision takes the REBATE_PAYMENT_BATCH record's instead |

### `rebate_status`

| field | tier | evidence | note |
|---|---|---|---|
| `beacon.rebate_status.payload_kind` | INVENTED | — | our envelope field |
| `beacon.rebate_status.direction` | SPEC | DOC2-007 | "inbound ... rebate status" verbatim |
| `beacon.rebate_status.beacon_id` | INVENTED | — | repeating the id here is our design |
| `beacon.rebate_status.manufacturer` | INVENTED | — | the labeller whose decision this payload reports |
| `beacon.rebate_status.manufacturer_decision` | INVENTED | — | read from the MANUFACTURER_DECISION event, or from the payment batch line when the rebate was paid and no decision event exists; reading only the event would report every paid rebate as undecided |
| `beacon.rebate_status.manufacturer_reason_code` | INVENTED | — | the manufacturer's word verbatim; null when the decision came off a batch line, which carries no reason |
| `beacon.rebate_status.decision_source` | INVENTED | — | MANUFACTURER_DECISION or REBATE_PAYMENT_BATCH; names which of the two places the verdict was actually read from |
| `beacon.rebate_status.rebate_state` | INVENTED | — | PAID or UNPAID, read off the payment join and never inferred from the decision: APPROVED with no payment line is the missing-rebate exception the dataset was built to produce |
| `beacon.rebate_status.rebate_amount` | INVENTED | — | text copy of the batch line's own figure, never derived; a mock that could compute an amount could disagree with the ledger |
| `beacon.rebate_status.as_of` | INVENTED | — | the received_at of whichever record carried the decision, so the timestamp says when the manufacturer spoke and not when the mock was run |

### `payment_reference`

Emitted only when a payment batch covers the dispense, so this file has fewer rows than there
are dispenses. A reference row that referenced nothing would read as "paid, details pending" to
anything counting rows.

| field | tier | evidence | note |
|---|---|---|---|
| `beacon.payment_reference.payload_kind` | INVENTED | — | our envelope field |
| `beacon.payment_reference.direction` | SPEC | DOC2-007 | "inbound ... reconciliation data" verbatim; this payload is the reconciliation half of that clause |
| `beacon.payment_reference.beacon_id` | INVENTED | — | repeating the id here is our design |
| `beacon.payment_reference.manufacturer` | INVENTED | — | the labeller whose ACH credit settles this rebate |
| `beacon.payment_reference.payment_reference` | INVENTED | — | the batch's allocation_code, the same string the manufacturer's ACH credit carries in trn02. The ACH mechanics are STD-NACHA-CCD; standards.md is explicit that the rebate payment reference itself is ours |
| `beacon.payment_reference.payment_effective_date` | INVENTED | — | the batch's own payment_effective_date; the ACH effective entry date it corresponds to is the standard's, this column is not |
| `beacon.payment_reference.rebate_amount` | INVENTED | — | text copy of the batch line's own figure |
| `beacon.payment_reference.batch_total_amount` | INVENTED | — | copied from the batch record, not summed from its lines; a second copy free to disagree with the first is the whole class of bug control totals exist to catch |
| `beacon.payment_reference.batch_received_at` | INVENTED | — | the batch record's own received_at |

---

## Declared representations

Four module constants are decisions rather than fields, so they carry no row above and are
recorded here instead. All four are `INVENTED`. None of them is inferrable from any source we
hold.

**`verity_export.REVERSAL_REPRESENTATION` = `STATUS_FLAG_ROW`.** A reversal is a flag plus its
own detail on the one row for the dispense: `reversal_status`, `reversal_received_at`,
`reversal_reason` and the signed `reversal_quantity`. There is never a second row and no amount
is ever negated. The deciding argument is that these exports are full files rather than deltas
— `DOC2-009`'s cadence is daily, weekly or monthly, and whether Verity sends increments is
listed as unknown in `docs/vendor_evidence/verity.md`. Under full-file re-delivery a second,
negative row is re-delivered too, and an ingester that accumulates rows across two deliveries
counts the reversal twice. A flag on the existing row is idempotent under re-delivery, which is
what `DOC2-010`'s file-level idempotency already assumes of this transport.

**`craneware_export.REVERSAL_REPRESENTATION` = `STATUS_FLAG_ON_ORIGINAL_ROW`.** The Claims
Report restates the dispense once with `dispense_status` set to `REVERSED`. The argument is
different from Verity's: a scheduled report is a snapshot of state as of the run, and the
plausible thing for a snapshot to do is restate the row rather than append a correction to it.
The Audit report is the one exception and is not a contradiction — Audit is a journal, so the
reversal appears there as its own event row. A connector must pick one of the two as its claim
source; ingesting Claims *and* Audit as claim rows double-counts every reversal.

**`craneware_export.SCHEMA_VERSION` = `craneware-sftp-export-1.0.0`.** `CRANEWARE-004` asks for
file schema versioning and Craneware publishes no format for one, so the string is ours. It is
written into every row rather than a header comment so a plain `DictReader` finds it, and it is
the key a schema registry under requirement B1 would track this layout by. Bump it whenever a
column is added, removed or reordered — a positional reader breaks silently otherwise.

**`beacon_payloads.VALIDATION_OUTCOMES` = `ACCEPTED`, `REJECTED`, `REVERSED`, `PENDING`.**
`BEACON-003`, the page that would have given Beacon's real validation code vocabulary, is a
403. These four are not a guess at that vocabulary; they are the four states the feed already
holds, read back — `REVERSED` is a `DISPENSE_REVERSAL` event existing, `REJECTED` is a
`NOT_QUALIFIED` qualification or a `REJECTED` manufacturer decision, `ACCEPTED` is an
`APPROVED` manufacturer status, and `PENDING` is the absence of all of them. Requirement M3's
acceptance is that this set equals the set already in the feed, so a fifth value may only be
added when the generator can actually produce it.

### Why B3 exists, and why the hazard is live in this repository

Requirement B3 makes each vendor *declare* its reversal representation because vendors
genuinely differ, and guessing wrong is the defect that destroys a build quietly: you
double-count or you lose a rebate, and both look clean in every report afterwards. A connector
written against a negative-row vendor and pointed at a status-flag file finds no reversal row,
concludes nothing was reversed, and keeps the rebate. A connector written the other way nets
the reversal twice.

This is not a theoretical hazard in this repository — the disagreement is already here.
`recon.generators.tpa` writes a reversal to the TPA feed as a **negative-quantity row**: a
separate `DISPENSE_REVERSAL` record carrying a negative `quantity_dispensed`, with the original
dispense record left on the wire, following NCPDP's `B1`/`B2` convention
(`STD-NCPDP-TELECOM`). Both vendor exports above deliberately do not copy that convention. So
one system in this build represents a reversal as a negative row and two represent it as a
flag, which is exactly the situation B3 was written for, and these constants are how a reader
tells which one they are looking at.

The same disagreement is what makes the `reversal_quantity` notes above insist the figure is
copied and never re-signed. The value arrives from the TPA feed already negative; `tpa.py` has
a comment recording that negating it a second time put the reversal on the wire as a positive
quantity and silently destroyed the one property the feed spec insists on.

---

## What this file does not claim

Stated in the voice of the evidence files' own UNKNOWN sections, because a provenance table
that is read as a schema is worse than none:

1. **That any of these column names is a vendor's.** 234 of 247 are ours. Not one Verity
   column and not one Craneware column has any source, and the thirteen Beacon rows that are
   not `INVENTED` are carried by a public standard or by an assessment document *about* Beacon,
   never by Beacon.

2. **That the datasets and reports contain these fields.** The dataset names are `DOC2-009`'s
   and the report names are `CRANEWARE-001`'s, retrieved verbatim. What is inside them is
   unpublished. A `SPEC` row on a field does not make the payload it sits in a vendor payload.

3. **That these are all the fields.** `CRANEWARE-001` says "and other commonly requested
   reports," so Craneware's five are explicitly not exhaustive, and the same caution applies
   inside a report. This table is exhaustive over *what this repository writes*, which is a
   different claim and the only one it makes.

4. **That the file formats are the vendors'.** CSV with a header row, `\n` line endings, an
   undated Craneware filename and a data-derived Verity timestamp are all choices, made for
   reviewability and reproducibility. Verity's and Craneware's real formats are unknown.

5. **That the control totals, trailers and schema version resemble the vendors'.**
   `DOC2-010` says source-control totals and `CRANEWARE-004` says file-level controls and
   schema versioning, which implies support exists on both sides. Neither shape is published.
   Ours are `INVENTED` and are placed where a `DictReader` finds them.

6. **That the Beacon ID looks like this.** `BEACON-013` establishes that Beacon IDs exist and
   that the recommended design persists one against a Shields Claim Financial Episode. Length,
   character set and prefix are unknown. Ours is minted by the orchestrator under M1, which is
   a statement about who decides it, not about its shape.

7. **That the reason codes are Beacon's.** They are our TPA's, carried verbatim under
   requirement C3 precisely so that nobody mistakes them for Beacon's. `BEACON-003` is a 403.

8. **That the reversal representations are right.** They are declared, not discovered.
   `DOC2-011` names Verity's as unconfirmed in as many words, and Craneware's is the same gap.
   If a vendor hands us a specification tomorrow and it disagrees, the change is one constant
   and one mapping — and the reason that is a small change rather than an archaeology project
   is that the assumption was written down here instead of being spread through the code.
