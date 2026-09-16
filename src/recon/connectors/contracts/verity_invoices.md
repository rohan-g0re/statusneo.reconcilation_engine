# Interface contract — `verity_invoices`

| | |
|---|---|
| Source id | `verity_invoices` |
| Vendor | Verity Solutions (Verity 340B) |
| Dataset | `invoices`, the third of the five datasets `DOC2-009` names |
| Transport | `TransportKind.SFTP` |
| Contract version | `verity-export-1.0.0` |
| Registered in | `src/recon/connectors/schema_registry.py`, as `VERITY_INVOICES` |
| Writer (local stand-in) | `src/recon/mocks/verity_export.py` |

Requirement B2 of `docs/connectivity_layer_requirements.md`, which is Doc 2 step 2
(`DOC2-002`) in artefact form. Invoices are the rebate money: one row per paid dispense, drawn
from the lines inside a `REBATE_PAYMENT_BATCH`. It is the Verity dataset that touches the
ledger, which makes it the one where a shape change costs cash rather than context.

**The transport and the dataset name are Verity's. Every field is ours.** `VERITY-001` confirms
first-hand that Verity Secure Data Transfer exists, is automated and is encrypted — it does not
use the word SFTP; that comes from `DOC2-009`. `VERITY-006` confirms first-hand that a named
*Split Transaction data specification* exists and was extended for the rebate model, and that
specification is referenced and never published. No field list for any of the five datasets was
retrievable from anywhere. `docs/vendor_evidence/verity.md` says it in as many words: *"every
Verity payload field is `INVENTED`."* So the twenty-two fields below are an honest invention,
declared one by one in `src/recon/mocks/MOCK_FIELDS.md` under `verity.invoices.*`, all
twenty-two tagged `INVENTED`.

---

## 1. Endpoint and file layout

There is no API. `DOC2-011` is explicit: *"No public developer documentation was found for a
general customer REST API; do not assume API access in the estimate."* A search-index summary
(`VERITY-008`) claims SFTP, API and Snowflake connectivity, it was not corroborated first-hand,
and `docs/vendor_evidence/verity.md` records the tension rather than resolving it quietly. We
follow Doc 2 and build SFTP-only.

| Property | Value |
|---|---|
| Delivery | Verity Secure Data Exports over SFTP (`DOC2-009`; `VERITY-001` confirms automated encrypted transfer first-hand) |
| Endpoint | An SFTP path per covered entity. It belongs in the `Source.endpoint` registry row, never in this file |
| Credential | A `Source.credential_ref` *name*, resolved at fetch time by `connectors/credentials.py`. No credential value appears in any tracked file |
| Filename | `verity_invoices_<stamp>.csv`, e.g. `verity_invoices_20260409T130000Z.csv` |
| Format | CSV, comma-delimited, minimal quoting |
| Encoding | UTF-8, no byte-order mark |
| Line endings | `\n` on every platform |
| Header row | Line 1, the twenty-two column names in the order of the table in §7 |
| Trailer row | Last line, `record_type = TRAILER` |
| Local stand-in | `settings.vendor_dir() / "verity" / verity_invoices_<stamp>.csv`, written by `verity_export.write` |

The filename carries the three things `DOC2-010` asks an audit trail to retain — vendor file
name, export type and generated timestamp — as `verity_<dataset>_<stamp>.csv`, stamped
`YYYYMMDDTHHMMSSZ`. The stamp is the latest `received_at` present in the data and never a clock
reading; `verity_accumulations.md` §1 gives the full argument, and the short version is that a
name derived from the clock makes file-level idempotency untestable because the same export
lands under a new name each run. The real convention is gap 4 of
`docs/vendor_evidence/verity.md` and is unknown. Ours is `INVENTED`.

### The row envelope

`record_type` (`DETAIL` or `TRAILER`) and `record_count` lead every row. `record_count` is
empty on detail rows and carries the number of detail rows on the trailer, which is
requirement F2's control total; `verity_export.declared_record_count` reads it back and raises
rather than returning zero on a file with no trailer, because a missing control total returned
as `0` would compare equal to an empty ingest and report agreement. `DOC2-010` asks for
source-control totals; Verity publishes no shape for one, so the shape is ours.

**The trailer declares a count and never a sum, and on this dataset that costs something
visible.** A total is exactly what a reader of an invoice file wants, and the mock package may
not do arithmetic on money — a mock that could add two amounts could disagree with the ledger,
and the reconciliation it exists to exercise would then be measuring the mock. So the declared
figure comes from the wire instead: `batch_total_rebate_amount` carries the payment batch's own
`total_rebate_amount` string, copied verbatim onto every line of that batch. F2 gets a declared
money figure that this module never computed. In the sample below, a two-line batch shows
`35040.80` beside a line of `1290.80`; a one-line batch shows the same value in both columns.

**This contract describes a DETAIL row and never the TRAILER row.** Hand it a trailer and it
fails, correctly and uselessly — a trailer is not an invoice line, so of course it has no NDC
and no amount. A contract that quietly excused the trailer would also excuse a detail row whose
`record_type` had been corrupted into something unrecognised, and that is a record going
missing without a quarantine.

## 2. Sample payload

Real rows from a real export. Reproduce them with profile `demo`, the published master seed
`20250701` and `CuratedSpine.MIXED`: run `recon.generators.orchestrator.generate` then
`write_outputs`, then `recon.mocks.source.load_source` and `recon.mocks.verity_export.render`.
That export has 23 detail rows.

```
record_type,record_count,invoice_number,beacon_id,accumulation_id,covered_entity_id,manufacturer,ndc_11,fill_date,rx_number,pharmacy_npi,provider_npi,rebate_allocation_code,batch_line_manufacturer_status,invoice_line_amount,batch_total_rebate_amount,payment_effective_date,batch_received_at,reversal_status,reversal_received_at,reversal_reason,reversal_quantity
DETAIL,,VINV-5288136,BCN-834790357646,ACC-67240749,DSH310074,SAGEPOINT,00078052515,20251216,221055333,1234567893,,RBT-20260116-13446,APPROVED,1290.80,35040.80,20260116,2026-01-17T08:00:00Z,,,,
DETAIL,,VINV-5934981,BCN-054615289558,ACC-05490270,DSH310074,SAGEPOINT,00078052515,20260126,221055399,1234567893,,RBT-20260312-12710,APPROVED,1290.80,1290.80,20260312,2026-03-13T08:00:00Z,REVERSED,2026-04-09T13:00:00Z,RETURN_TO_STOCK,-28
TRAILER,23,,,,,,,,,,,,,,,,,,,,
```

Line 2 is the **paid** archetype: an approved rebate settled inside a batch that paid two
dispenses, which is why `invoice_line_amount` (`1290.80`) and `batch_total_rebate_amount`
(`35040.80`) differ. Both are strings lifted off the wire; neither was derived here, and the
line figure is not a share of the total but the batch line's own `rebate_amount`.

Line 3 is the **reversed** archetype, and it is the row requirement B3 is about. Read it
carefully: this dispense was **paid and then reversed**. `invoice_line_amount` is still
`1290.80`, `reversal_status` is `REVERSED`, the reversal's arrival time, reason and signed
quantity sit beside it, and there is no second row and no negated figure anywhere. An ingester
that sums `invoice_line_amount` across detail rows without reading `reversal_status` books a
rebate the covered entity no longer holds.

Line 4 is the trailer: the count, and the other twenty columns empty.

Two rows the file does **not** contain, both deliberately:

**A rejected rebate has no invoice line.** The population is dispenses with a line inside a
payment batch, so a manufacturer rejection produces nothing here. It is visible on
`contract_pharmacy_claims_backing` and `split_transactions`.

**A batch line whose natural key matches no dispense does not appear.** The dataset is driven
off dispenses rather than off batch lines. The generator injects identifier drift into exactly
one feed per episode, and a line that has drifted out of the join is a line a real operator also
cannot invoice. Repairing it here would hand the connector a join the real feed does not have.

## 3. Join keys

The natural key is the only bridge from an invoice line back to the dispense it pays for, and
this file spells its components `rx_number`, `pharmacy_npi`, `provider_npi`, `ndc_11` and
`fill_date`. Note `ndc_11` with an underscore — Craneware spells the same concept `ndc11`, and
a connector that assumed one spelling across both vendors reads `None` and joins nothing.

This dataset also carries the one identifier in the whole Verity set that reaches the bank.

| Crosswalk key | Canonical form | Built from |
|---|---|---|
| `KeyType.NATURAL_340B_PHARMACY` | `pharmacy_npi\|rx_number\|ndc11\|fill_date` | a row with a prescription |
| `KeyType.NATURAL_340B_MEDICAL` | `provider_npi\|ndc11\|service_date` | a row with no prescription; `fill_date` is the service date |
| `KeyType.ALLOCATION_CODE` | `rebate_allocation_code`, verbatim | every row — it is required here |

**`rebate_allocation_code` is the money join, and it is required on this dataset for that
reason.** It resolves to the `REBATE_BATCH`, which holds the dispense list, and the same
splitter then fans the batch total back out across the lines. It is the rebate side's exact
analogue of `TRN02` on the pharmacy leg: two hops rather than one.

The same string rides `trn02` on the manufacturer's ACH credit on the bank feed, where it is
keyed as `KeyType.TRN02`. **It is not always there, and its absence is the point.** In the demo
export above, `RBT-20260116-13446` has no matching bank credit — an approved, invoiced rebate
with no money behind it. That is the missing-rebate exception the dataset exists to produce, and
this contract correctly says nothing about it: an invoice line that does not settle is a
reconciliation finding, not a schema violation.

Three things a connector has to know, each of which produces a wrong answer silently:

**A row legitimately has no `rx_number` and no `pharmacy_npi`.** A clinic-administered drug has
no prescription; those rows carry `provider_npi` and key medically. The medical key is
deliberately weaker — two administrations of the same drug at the same site on the same day are
genuinely indistinguishable — so a multi-hit is `AMBIGUOUS_KEY_MATCH` and gets parked. Picking
one is a coin flip that puts real money on the wrong episode.

**`fill_date` and `payment_effective_date` are `CCYYMMDD` and are never parsed.** Fixed width
with no separator means lexicographic order is chronological order. The contract enforces it:
both are kind `date_wire`, and `"2026-01-16"` fails. A connector that normalises the fill date
on the way in has changed the key and joins nothing.

**`rx_number` may have drifted, and repairing it is wrong.** See the note in §2 about lines that
do not appear. A claim that cannot be joined is a claim a real operator also cannot join.

`invoice_number`, `beacon_id` and `accumulation_id` are minted by
`recon.generators.orchestrator` under requirement M1 and read back off the identifier sidecar —
minted in exactly one place because a value two independent systems must agree on may be decided
by exactly one component. None of the three is a `KeyType` today. `KeyType` has eight members
and `BEACON_ID` is not one of them; that is requirement C5 and it is not built. So `beacon_id`
is carried for traceability and currently resolves nothing, which is worth saying plainly
because a connector author reading a column named `beacon_id` will assume it joins.

## 4. Status codes

One business vocabulary appears on this dataset, plus the reversal flag and the envelope's own.
All are the TPA feed's own words, carried verbatim under requirement C3. None is Verity's;
Verity publishes no vocabulary.

| Column | Value | Meaning |
|---|---|---|
| `record_type` | `DETAIL` | An invoice line. This contract describes exactly this row |
| `record_type` | `TRAILER` | The control total. Not validated against this contract |
| `batch_line_manufacturer_status` | `APPROVED` | The manufacturer accepted this line and the batch paid it |
| `batch_line_manufacturer_status` | *empty* | The batch line carried no status. The line is still a paid line; the amount is what makes it one |
| `reversal_status` | `REVERSED` | A `DISPENSE_REVERSAL` event exists for this dispense. See §5 |
| `reversal_status` | *empty* | No reversal. The other three reversal columns are empty too |
| `reversal_reason` | `RETURN_TO_STOCK` | The drug went back on the shelf |

**`batch_line_manufacturer_status` is read off the payment batch line, not off a decision
event, and that is not interchangeable with the column it resembles.** A dispense that was paid
usually has no standalone `MANUFACTURER_DECISION` record at all — the batch line is where its
status lives. A connector that read manufacturer outcomes only from decision events would report
every paid rebate as undecided. The sibling column on `contract_pharmacy_claims_backing` and
`split_transactions` is `manufacturer_decision_status` and comes from the event; the two are
kept as separate columns on `split_transactions` precisely so that collapsing them does not
invent an agreement the feed does not have.

**There is no qualification status on this dataset.** A line exists because a batch paid it; the
TPA's eligibility verdict lives on `verity_accumulations` and
`contract_pharmacy_claims_backing`, where the vocabulary is `QUALIFIED`, `NOT_QUALIFIED` and
empty-while-in-review, with four disqualification reasons behind it.

**A rejected rebate does not appear here at all**, so `REJECTED` is not a value this column can
carry in practice — the population is `is_paid`. The manufacturer rejection vocabulary, visible
on the other datasets, is `REJECTED` with `CONTRACT_PHARMACY_RESTRICTED` or
`NON_CONFORMING_45_DAY` as the reason. **Replenishment status is a Craneware concept**, on the
Unreplenished Costs report, and has no Verity counterpart. Both are named here only so that a
reader who came looking for them stops looking in this file.

## 5. Reversal semantics — requirement B3

```
verity_export.REVERSAL_REPRESENTATION = "STATUS_FLAG_ROW"
```

**A reversal is a flag plus its own detail on the one row for the dispense. There is never a
second row, and no amount is ever negated.** On this dataset that is the sharpest version of the
statement, because this is where the money is: line 3 of the sample is a reversed dispense whose
`invoice_line_amount` still reads `1290.80`. The file is telling you that this much was paid and
that the dispense was later reversed. It is not telling you the net is zero, and it is not going
to.

`DOC2-011` states in as many words that Verity's reversal and requalification representation is
not publicly confirmed, and it is gap 6 of `docs/vendor_evidence/verity.md`. So this is declared
rather than discovered, and tagged `INVENTED` rather than cited.

The deciding argument for a flag over a negative row is that **these exports are full files, not
deltas.** `DOC2-009`'s cadence is daily, weekly or monthly, and whether Verity sends increments
is unknown, so the mock emits full files. Under full-file re-delivery a second, negative row is
re-delivered too, and an ingester that accumulates rows across two deliveries counts the reversal
twice — B3's *"not zero, not two"* failing in the direction nobody notices. A flag on the one row
is idempotent under re-delivery, which is what `DOC2-010`'s file-level idempotency already
assumes of this transport. The second argument is that a negative row needs a negative amount to
be worth having, and negating an amount is arithmetic on money, which the mock package may not
do. A flag keeps exactly one money figure per dispense, copied as text.

**What a connector written for the other representation does wrong.** Suppose it was written
against a vendor that represents a reversal as a negative row — the original line stays and a
correcting line with a negative amount arrives beside it. Point that connector at this file and
it does the natural thing: it sums `invoice_line_amount` over the detail rows, finds no negative
figure, and books the full amount. Line 3's `1290.80` goes onto the ledger as a rebate the
covered entity no longer holds. The trailer count agrees, the control total agrees, nothing is
quarantined, and the variance shows up months later as an unexplained receivable.

The mirror failure is just as quiet. A connector written against *this* file — one that nets the
line out whenever `reversal_status` is `REVERSED` — pointed at a negative-row vendor nets the
reversal once from the flag and once from the negative line, and the ledger ends up one rebate
short.

**The hazard is live in this repository, not theoretical.** `recon.generators.tpa` writes a
reversal to `tpa_340b_events.jsonl` as a separate `DISPENSE_REVERSAL` record carrying a negative
`quantity_dispensed`, with the original dispense left on the wire, following NCPDP's convention
(`STD-NCPDP-TELECOM`). Its own comment is explicit that the quantity is used as given and never
re-negated, because negating it a second time once put the reversal on the wire as a positive
quantity and silently destroyed the one property the feed spec insists on. So one system in this
build uses a negative row and this export uses a flag, over the same episode.
`reversal_quantity` here is that already-negative figure copied across untouched — `-28` in the
sample — which is why the contract types it `integer_text`, a kind that accepts a leading minus,
and not `integer`.

Note the one thing the reversal columns do *not* carry: a reversed amount. The quantity is
signed; the money is not. Anyone computing the net effect on the ledger does it from the
reconciliation engine, using the flag as the trigger, and not by reading a figure off this file.
B3's acceptance is that a golden reversed claim produces exactly one net effect on the ledger —
not zero, not two — and one net effect is only reachable if exactly one side does the netting.

## 6. Cadence

`DOC2-009` establishes the cadence: Verity Secure Data Exports over SFTP, **daily, weekly or
monthly**, with the five dataset names *"accumulations, contract-pharmacy claims backing,
invoices, matches/split transactions, claims backing and unmatched claims."*

`DOC2-010` adds what the transport has to do at that cadence: *"use direct file landing,
checksum, file-level idempotency and source-control totals."*

On this dataset, cadence and money interact in a way the other four avoid:

**A monthly invoice drop means a truncated file costs a month of rebates.** A truncated CSV
parses perfectly and reconciles wrongly — 500 declared against 450 arrived has to fail the
batch, not ingest 450 successfully — which is exactly what the `record_count` trailer and
requirement F2 exist for.

**The cadence is per covered entity and is not knowable from the file.** Nothing in the payload
says which of daily, weekly or monthly this drop is. It is configuration, it lives in the
`Source` registry row, and a connector that inferred it from arrival gaps would reclassify a
weekly customer as broken the first time a public holiday moved a drop.

**Full files, not increments.** Whether Verity sends increments is gap 5 of
`docs/vendor_evidence/verity.md` and is unknown; the mock emits full files, and the checkpoint in
requirement A4 is what makes that safe. This is also the premise the reversal representation in
§5 rests on, so the two decisions have to move together — and on an invoice file, re-ingesting a
full drop without file-level idempotency double-books every rebate in it.

## 7. Registered fields

Contract version: `verity-export-1.0.0`

| # | field | kind | required |
|---|---|---|---|
| 1 | `record_type` | string | yes |
| 2 | `record_count` | integer_text | no |
| 3 | `invoice_number` | string | no |
| 4 | `beacon_id` | string | no |
| 5 | `accumulation_id` | string | no |
| 6 | `covered_entity_id` | string | no |
| 7 | `manufacturer` | string | no |
| 8 | `ndc_11` | string | yes |
| 9 | `fill_date` | date_wire | yes |
| 10 | `rx_number` | string | no |
| 11 | `pharmacy_npi` | string | no |
| 12 | `provider_npi` | string | no |
| 13 | `rebate_allocation_code` | string | yes |
| 14 | `batch_line_manufacturer_status` | string | no |
| 15 | `invoice_line_amount` | decimal_text | yes |
| 16 | `batch_total_rebate_amount` | decimal_text | yes |
| 17 | `payment_effective_date` | date_wire | yes |
| 18 | `batch_received_at` | timestamp | yes |
| 19 | `reversal_status` | string | no |
| 20 | `reversal_received_at` | timestamp | no |
| 21 | `reversal_reason` | string | no |
| 22 | `reversal_quantity` | integer_text | no |

The `#` column is the file's column order, and it is the header order byte for byte. Nothing in
`SchemaContract.validate` depends on order — a record is a mapping — but a positional reader
depends on it entirely, and pinning it here is what lets B2's acceptance test be a list
comparison instead of a set comparison with a sorting argument attached.

### Why eight fields are required here and only four on `verity_accumulations`

Five of them are required on this dataset and optional on its sibling, for one reason: the
population is `is_paid`, so a payment batch and a payment line both exist by construction, and
the TPA adapter reads `allocation_code`, `rebate_amount`, `total_rebate_amount` and
`payment_effective_date` without `.get`. **A row missing any of them did not come from a payment
batch, and treating it as an invoice line would put money on the ledger that no batch ever
paid.** That is the argument for contracts being per dataset rather than per vendor, stated as a
consequence rather than as a principle.

The other three are the same as everywhere: `record_type` is written onto every row by
`_render_dataset`, and `ndc_11` and `fill_date` come from fields the TPA adapter reads without
`.get`.

`rx_number` and `pharmacy_npi` stay optional and must: a medically administered dispense has
neither, and requiring them would quarantine every medical 340B rebate in the file.
`batch_line_manufacturer_status` stays optional because the amount, not the status, is what makes
a line a paid line. `record_count` is optional because it lives on the trailer, and the trailer is
F2's control total rather than this contract's business.

### Notes on the kinds

`invoice_line_amount` and `batch_total_rebate_amount` are `decimal_text`: money is exact decimal
text on the wire and never a float. The kind accepts a `Decimal` as well as a string, because
money off a JSONL feed genuinely arrives as a `Decimal` — `parse_jsonl_line` parses with
`parse_float=Decimal` — while money off a CSV arrives as text. Both are exact. A `float` is not,
and the violation message calls it out by name, because a float in a money column almost always
means the payload was parsed without `parse_float=Decimal`, which is a bug in the reader rather
than a defect in the feed, and the two get fixed by different people.

`batch_received_at` and `reversal_received_at` are `timestamp`, meaning `%Y-%m-%dT%H:%M:%SZ`
exactly — `2026-01-17T08:00:00Z` in the sample. `received_at` is the only temporal field the
pipeline honours.

`payment_effective_date` is `date_wire` and `batch_received_at` is `timestamp`, and they are
genuinely different things: the effective date is when the money is dated, the received-at is when
the batch record arrived. Reconciling against the wrong one puts a payment in the wrong period.

`reversal_quantity` is `integer_text` rather than `integer` because the value arrives as text
already carrying its sign, and the sign is the whole content of the field.

## 8. Versioning

The version string is `verity-export-1.0.0`, and it is `INVENTED` — Verity publishes no field
dictionary, so there is no vendor version to track.

**Verity does not write its version into the file, and Craneware does.** Craneware's rows carry
`schema_version`, so a connector passes `version=record["schema_version"]` and the wire names its
own contract. Verity writes only a record count, so the registry has to be *told*, and
`version=None` resolves to the current registration. The consequence is that a Verity re-cut is
not self-announcing: if Verity changes the layout and we do not bump the registration, records are
validated against a contract the file no longer follows. What catches that is the contract itself
— a vanished or reshaped field is a violation — and not the version string.

This dataset shares its version string with `verity_accumulations` because both are cut by the
same export product at the same revision. They are still two separate contracts, because they are
two separate files with genuinely different required sets, and registering them separately is what
lets this one require five fields the other leaves optional.

Bump the version whenever a column is added, removed or reordered, and register the new contract
beside the old one rather than editing it. `SchemaRegistry.register` refuses to redefine an
existing `(source_id, version)` pair with different content, because the second definition would
silently replace the first and every record already validated against the first would have been
checked against a contract that no longer exists. An archived file has to keep replaying against
the contract it was written under, which is what `register(..., current=False)` is for. Asking for
a version we do not hold raises `UnknownSchemaVersionError` rather than falling back to the
newest, because a silent fallback is exactly how a connector validates a 2.0.0 file against the
1.0.0 contract, finds nothing wrong, and reports success.
