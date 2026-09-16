# Interface contract — `verity_accumulations`

| | |
|---|---|
| Source id | `verity_accumulations` |
| Vendor | Verity Solutions (Verity 340B) |
| Dataset | `accumulations`, the first of the five datasets `DOC2-009` names |
| Transport | `TransportKind.SFTP` |
| Contract version | `verity-export-1.0.0` |
| Registered in | `src/recon/connectors/schema_registry.py`, as `VERITY_ACCUMULATIONS` |
| Writer (local stand-in) | `src/recon/mocks/verity_export.py` |

Requirement B2 of `docs/connectivity_layer_requirements.md`, which is Doc 2 step 2
(`DOC2-002`) in artefact form. Accumulations are the dispenses the TPA qualified, which makes
this the dataset that carries the 340B join keys. An accumulation exists *because* 340B
eligibility was granted, so the whole population is `qualification_status == "QUALIFIED"`.

**The transport and the dataset name are Verity's. Every field is ours.** `VERITY-001`
confirms first-hand that Verity Secure Data Transfer exists, is automated and is encrypted —
it does not use the word SFTP; that comes from `DOC2-009`. `VERITY-006` confirms first-hand
that a named *Split Transaction data specification* exists and was extended for the rebate
model, and that specification is referenced and never published. No field list for any of the
five datasets was retrievable from anywhere. `docs/vendor_evidence/verity.md` says it in as
many words: *"every Verity payload field is `INVENTED`."* So the twenty fields below are an
honest invention, declared one by one in `src/recon/mocks/MOCK_FIELDS.md` under
`verity.accumulations.*`, all twenty tagged `INVENTED`.

---

## 1. Endpoint and file layout

There is no API. `DOC2-011` is explicit: *"No public developer documentation was found for a
general customer REST API; do not assume API access in the estimate."* A search-index summary
(`VERITY-008`) claims SFTP, API and Snowflake connectivity, it was not corroborated
first-hand, and `docs/vendor_evidence/verity.md` records the tension rather than resolving it
quietly. We follow Doc 2 and build SFTP-only, because building against an unspecified API
would mean inventing both transport and payload.

| Property | Value |
|---|---|
| Delivery | Verity Secure Data Exports over SFTP (`DOC2-009`; `VERITY-001` confirms automated encrypted transfer first-hand) |
| Endpoint | An SFTP path per covered entity. It belongs in the `Source.endpoint` registry row, never in this file |
| Credential | A `Source.credential_ref` *name*, resolved at fetch time by `connectors/credentials.py`. No credential value appears in any tracked file |
| Filename | `verity_accumulations_<stamp>.csv`, e.g. `verity_accumulations_20260409T130000Z.csv` |
| Format | CSV, comma-delimited, minimal quoting |
| Encoding | UTF-8, no byte-order mark |
| Line endings | `\n` on every platform |
| Header row | Line 1, the twenty column names in the order of the table in §7 |
| Trailer row | Last line, `record_type = TRAILER` |
| Local stand-in | `settings.vendor_dir() / "verity" / verity_accumulations_<stamp>.csv`, written by `verity_export.write` |

### The name carries three things, and the stamp is not a clock reading

`DOC2-010` asks that the vendor file name, the generated timestamp and the export type be
retained for audit lineage, so the name carries all three: `verity_<dataset>_<stamp>.csv`, with
the stamp formatted `YYYYMMDDTHHMMSSZ`.

**The stamp is derived from the data, never from the clock.** It is the latest `received_at`
present in the source — a real export is named for the moment the vendor cut it, and the
moment this data stops arriving is the closest thing to that moment this repository knows.
A `datetime.now()` here would make the same seed produce a different filename on every run,
which breaks the reproducibility manifest and, worse, makes file-level idempotency untestable:
the same export would land under a new name each time, so a connector could never recognise
that it had already seen it. `DOC2-010` asks for file-level idempotency by name.

The real naming convention is gap 4 of `docs/vendor_evidence/verity.md` and is unknown. Ours
is `INVENTED`.

### The row envelope

Two columns are file-level controls rather than claim data, and both lead the row.
`DOC2-010` asks for *source-control totals*; Verity publishes no shape for one, so the shape
is ours.

- `record_type` is `DETAIL` or `TRAILER`, on every row.
- `record_count` is empty on every detail row and carries the number of detail rows on the
  trailer. `verity_export.declared_record_count` reads it back, and requirement F2 compares it
  against what was ingested. It raises rather than returning zero on a file with no trailer,
  because a missing control total returned as `0` would compare equal to an empty ingest and
  report agreement.

Two leading columns rather than a positional trailer, because a positional trailer that reused
the first data column works until someone reads the file with a `DictReader` — which is how a
control total quietly becomes a covered entity id.

**The trailer declares a count and no sum.** A sum would mean adding amounts, and the mock
package may not do arithmetic on money: a mock that could add two amounts could disagree with
the ledger, and the reconciliation it exists to exercise would then be measuring the mock.
Accumulations carry no money column at all, so the question does not arise here; it does on
`verity_invoices`, which carries the batch's own declared total instead.

**This contract describes a DETAIL row and never the TRAILER row.** Hand it a trailer and it
fails, correctly and uselessly — a trailer is not a dispense, so of course it has no NDC. A
contract that quietly excused the trailer would also excuse a detail row whose `record_type`
had been corrupted into something unrecognised, and that is a record going missing without a
quarantine.

## 2. Sample payload

Real rows from a real export. Reproduce them with profile `demo`, the published master seed
`20250701` and `CuratedSpine.MIXED`: run `recon.generators.orchestrator.generate` then
`write_outputs`, then `recon.mocks.source.load_source` and
`recon.mocks.verity_export.render`. That export has 34 detail rows.

```
record_type,record_count,accumulation_id,beacon_id,covered_entity_id,manufacturer,ndc_11,fill_date,rx_number,pharmacy_npi,provider_npi,prescriber_npi,hin,wholesaler_invoice_number,qualification_status,qualification_received_at,reversal_status,reversal_received_at,reversal_reason,reversal_quantity
DETAIL,,ACC-67240749,BCN-834790357646,DSH310074,SAGEPOINT,00078052515,20251216,221055333,1234567893,,1720044662,HN3021998,WI-88000037,QUALIFIED,2025-12-22T15:00:00Z,,,,
DETAIL,,ACC-05490270,BCN-054615289558,DSH310074,SAGEPOINT,00078052515,20260126,221055399,1234567893,,1805591736,HN3021998,WI-88000407,QUALIFIED,2026-02-02T12:00:00Z,REVERSED,2026-04-09T13:00:00Z,RETURN_TO_STOCK,-28
DETAIL,,ACC-48406598,BCN-466996134633,DSH310074,ALDEBARAN,00002754901,20250913,,,1493387025,1720044662,HN3021998,WI-88001184,QUALIFIED,2025-09-21T13:00:00Z,,,,
TRAILER,34,,,,,,,,,,,,,,,,,,
```

Line 2 is the **paid** archetype as it appears here. Accumulations record that eligibility was
granted; nothing on this row says whether the rebate was ever paid, and that is correct — the
payment lives on `verity_invoices`.

Line 3 is the **reversed** archetype, and it is the row requirement B3 is about.
`reversal_status` is `REVERSED`, the reversal's own arrival time, reason and signed quantity
sit beside it, and there is no second row.

Line 4 is the **unmatched** archetype and a **medically administered** dispense, and it is the
row a connector most often gets wrong. The TPA qualified it and nothing has come back since.
`rx_number` and `pharmacy_npi` are both empty, because a clinic-infused drug has no
prescription and no dispensing pharmacy; the identity is carried by `provider_npi` instead. A
connector that requires an Rx number drops this row or, worse, joins it on a key with two empty
components and matches the wrong episode.

Line 5 is the trailer: the count, and the other eighteen columns empty.

Note what this dataset deliberately keeps: a dispense the TPA qualified and the manufacturer
later rejected still accumulated, and still appears here. Dropping it would hide the state
where most 340B dispute volume actually lives.

## 3. Join keys

340B has no shared identifier space. The only bridge from a Verity accumulation to anything
else in this system is the natural key, assembled from five columns this file spells as
`rx_number`, `pharmacy_npi`, `provider_npi`, `ndc_11` and `fill_date`. Note `ndc_11` with an
underscore — Craneware spells the same concept `ndc11`, and a connector that assumed one
spelling across both vendors reads `None` and joins nothing. That difference is the reason
contracts are written per dataset rather than per fabric.

| Crosswalk key | Canonical form | Built from |
|---|---|---|
| `KeyType.NATURAL_340B_PHARMACY` | `pharmacy_npi\|rx_number\|ndc11\|fill_date` | a row with a prescription, such as line 2 above |
| `KeyType.NATURAL_340B_MEDICAL` | `provider_npi\|ndc11\|service_date` | a row with no prescription, such as line 4 above; `fill_date` is the service date |

Three things a connector has to know, each of which produces a wrong answer silently if it is
not known:

**A row legitimately has no `rx_number` and no `pharmacy_npi`.** See line 4. The medical key
is deliberately weaker than the pharmacy one: two administrations of the same drug at the same
site on the same day are genuinely indistinguishable, so a multi-hit is `AMBIGUOUS_KEY_MATCH`
and gets parked. Picking one is a coin flip that puts real money on the wrong episode and
looks identical to a correct match in every report afterwards.

**`fill_date` is `CCYYMMDD` and is never parsed.** Fixed width with no separator means
lexicographic order is chronological order, so it is compared as text. The contract enforces
this: `fill_date` is kind `date_wire`, and `"2025-12-16"` fails it. A connector that
normalises the date on the way in has changed the key and joins nothing.

**`rx_number` may have drifted, and repairing it is wrong.** The generator injects identifier
drift into exactly one feed per episode (defect D-6). A claim that cannot be joined is a claim
a real operator also cannot join, and a connector that "corrected" the spelling would delete
the crosswalk-miss exception the dataset exists to produce.

### Three identifiers that are not crosswalk keys

`accumulation_id`, `beacon_id` and (on `verity_invoices`) `invoice_number` are minted by
`recon.generators.orchestrator` under requirement M1 and read back off the identifier sidecar.
They are minted in exactly one place for the same reason `trn02` and `allocation_code` are: a
value two independent systems must agree on may be decided by exactly one component. A mock
that minted its own Beacon ID would be inventing a fact the rest of the system then has to
accept on faith.

None of the three is a `KeyType` today. `KeyType` has eight members and `BEACON_ID` is not one
of them — that is requirement C5, and it is not built. So `beacon_id` on this row is carried
for traceability and currently resolves nothing. Saying so plainly matters: a connector author
reading a column named `beacon_id` will assume it joins, and it does not yet.

`covered_entity_id` is not a key either. It is the 340B ID the dispense was qualified under,
and Beacon grants permission per 340B ID (`BEACON-012`), which is why requirement E1 makes it
a real field on the episode rather than the `None` it is written as today.

## 4. Status codes

Two vocabularies appear on this dataset, plus the envelope's own. Both are the TPA feed's own
words, carried verbatim under requirement C3. Neither is Verity's; Verity publishes no
vocabulary.

| Column | Value | Meaning |
|---|---|---|
| `record_type` | `DETAIL` | An accumulation row. This contract describes exactly this row |
| `record_type` | `TRAILER` | The control total. Not validated against this contract |
| `qualification_status` | `QUALIFIED` | The TPA granted 340B eligibility |
| `reversal_status` | `REVERSED` | A `DISPENSE_REVERSAL` event exists for this dispense. See §5 |
| `reversal_status` | *empty* | No reversal. The other three reversal columns are empty too |
| `reversal_reason` | `RETURN_TO_STOCK` | The drug went back on the shelf |

**`qualification_status` can only be `QUALIFIED` here, and that is a property of the dataset
rather than of the vocabulary.** The population is selected on the status being `QUALIFIED`, so
a row carrying `NOT_QUALIFIED` or an empty status is a row that cannot have been in this file.
That is why the contract marks `qualification_status` and `qualification_received_at` required
on this dataset when the same fields are optional elsewhere — and it is the argument for
contracts being per dataset rather than per vendor. The wider vocabulary, which shows up on
`contract_pharmacy_claims_backing` and `unmatched_claims`, is `QUALIFIED`, `NOT_QUALIFIED` and
empty-while-in-review, with four disqualification reasons behind it:
`NO_QUALIFYING_ENCOUNTER`, `PRESCRIBER_NOT_AFFILIATED`, `UNREGISTERED_LOCATION`,
`MEDICAID_DUPLICATE_DISCOUNT`.

**There is no manufacturer status on this dataset and there is no replenishment status
anywhere in Verity's five.** Manufacturer outcomes live on `contract_pharmacy_claims_backing`
and `split_transactions` as `manufacturer_decision_status`, and on `verity_invoices` as
`batch_line_manufacturer_status`; the values are `APPROVED` and `REJECTED`, with
`CONTRACT_PHARMACY_RESTRICTED` and `NON_CONFORMING_45_DAY` as the rejection reasons.
Replenishment status is a Craneware concept, on the Unreplenished Costs report, and it has no
Verity counterpart. Both are named here only so that a reader who came looking for them stops
looking in this file.

## 5. Reversal semantics — requirement B3

```
verity_export.REVERSAL_REPRESENTATION = "STATUS_FLAG_ROW"
```

**A reversal is a flag plus its own detail on the one row for the dispense. There is never a
second row, and no amount is ever negated.** Four columns carry it — `reversal_status`,
`reversal_received_at`, `reversal_reason` and the signed `reversal_quantity` — and they are
present on all five Verity datasets, including the two where they can only ever be empty. A
fixed column set with some of it null is what a real vendor export looks like; a column that
appears only on the datasets that happen to use it is a column a consumer has to special-case
per file.

`DOC2-011` states in as many words that Verity's reversal and requalification representation
is not publicly confirmed, and it is gap 6 of `docs/vendor_evidence/verity.md`. So this is
declared rather than discovered, and it is tagged `INVENTED` rather than cited.

Three candidates were considered: a negative-quantity row, a replacement row, and a status flag
on the restated row. The deciding argument for the flag is that **these exports are full files,
not deltas.** `DOC2-009`'s cadence is daily, weekly or monthly, and whether Verity sends
increments is listed as unknown, so the mock emits full files. Under full-file re-delivery a
second, negative row is re-delivered too — and an ingester that accumulates rows across two
deliveries counts the reversal twice. That is B3's *"not zero, not two"* failing in the
direction nobody notices. A flag on the one row for the dispense is idempotent under
re-delivery, which is what `DOC2-010`'s file-level idempotency already assumes of this
transport. The second argument is that a negative row needs a negative amount to be worth
having, and negating an amount is arithmetic on money, which the mock package may not do.

**What a connector written for the other representation does wrong.** Suppose it was written
against a vendor that represents a reversal as a negative-quantity row, with the original left
on the wire and a correcting row beside it. Point that connector at this file and it scans for
a second row bearing a negative quantity, finds none, concludes nothing was reversed, and
counts the accumulation as live. Line 3 of the sample above is exactly that trap: it is a
reversed dispense, it looks like every other qualified accumulation to a row-counting reader,
and the `-28` sits in a column that reader never opens. The entity keeps an accumulation it is
not entitled to. Nothing errors and no quarantine row is written.

The mirror failure is just as quiet. A connector written against *this* file and pointed at a
negative-row vendor nets the reversal once from the flag and once from the negative row, and
ends up one reversal long in the other direction.

**The hazard is live in this repository, not theoretical.** `recon.generators.tpa` writes a
reversal to `tpa_340b_events.jsonl` as a separate `DISPENSE_REVERSAL` record carrying a
negative `quantity_dispensed`, with the original dispense left on the wire, following NCPDP's
convention (`STD-NCPDP-TELECOM`). Its own comment is explicit that the quantity is used as
given and never re-negated, because negating it a second time once put the reversal on the
wire as a positive quantity and silently destroyed the one property the feed spec insists on.
So one system in this build uses a negative row and this export uses a flag, over the same
episode. `reversal_quantity` here is that already-negative figure copied across untouched —
`-28` in the sample — which is why the contract types it `integer_text`, a kind that accepts a
leading minus, and not `integer`.

If a vendor hands us a specification tomorrow and it disagrees, the change is one constant and
one mapping. That it is a small change rather than an archaeology project is the entire return
on writing the assumption down.

## 6. Cadence

`DOC2-009` establishes the cadence: Verity Secure Data Exports over SFTP, **daily, weekly or
monthly**, with the five dataset names *"accumulations, contract-pharmacy claims backing,
invoices, matches/split transactions, claims backing and unmatched claims."*

`DOC2-010` adds what the transport has to do at that cadence: *"use direct file landing,
checksum, file-level idempotency and source-control totals."* All four of those are load-bearing
at a monthly cadence in a way they are not at a daily one — a month-long window means a
truncated file costs a month of accumulations, which is what the trailer count in §1 exists to
catch.

Two consequences a connector must not get wrong:

**The cadence is per covered entity and is not knowable from the file.** Nothing in the payload
says which of daily, weekly or monthly this drop is. It is configuration, it lives in the
`Source` registry row, and a connector that inferred it from arrival gaps would reclassify a
weekly customer as broken the first time a public holiday moved a drop.

**Full files, not increments.** Whether Verity sends increments is gap 5 of
`docs/vendor_evidence/verity.md` and is unknown; the mock emits full files, and the checkpoint
in requirement A4 is what makes that safe. This is also the premise the reversal representation
in §5 rests on, so the two decisions have to move together.

## 7. Registered fields

Contract version: `verity-export-1.0.0`

| # | field | kind | required |
|---|---|---|---|
| 1 | `record_type` | string | yes |
| 2 | `record_count` | integer_text | no |
| 3 | `accumulation_id` | string | no |
| 4 | `beacon_id` | string | no |
| 5 | `covered_entity_id` | string | no |
| 6 | `manufacturer` | string | no |
| 7 | `ndc_11` | string | yes |
| 8 | `fill_date` | date_wire | yes |
| 9 | `rx_number` | string | no |
| 10 | `pharmacy_npi` | string | no |
| 11 | `provider_npi` | string | no |
| 12 | `prescriber_npi` | string | no |
| 13 | `hin` | string | no |
| 14 | `wholesaler_invoice_number` | string | no |
| 15 | `qualification_status` | string | yes |
| 16 | `qualification_received_at` | timestamp | yes |
| 17 | `reversal_status` | string | no |
| 18 | `reversal_received_at` | timestamp | no |
| 19 | `reversal_reason` | string | no |
| 20 | `reversal_quantity` | integer_text | no |

The `#` column is the file's column order, and it is the header order byte for byte. Nothing
in `SchemaContract.validate` depends on order — a record is a mapping — but a positional reader
depends on it entirely, and pinning it here is what lets B2's acceptance test be a list
comparison instead of a set comparison with a sorting argument attached.

### Why these four are required and the rest are not

`required` means the *producer* structurally cannot leave the field out, established by reading
the writer rather than by listing what a consumer would like to have. Marking a field required
that the vendor legitimately leaves empty turns a quiet day into a quarantine storm, and a
quarantine storm is how a team learns to ignore the queue.

- `record_type` is written onto every row by `_render_dataset`.
- `ndc_11` and `fill_date` come from fields the TPA adapter reads without `.get`, so the feed
  underneath guarantees them.
- `qualification_status` and `qualification_received_at` are required **for this dataset
  specifically**, because the population is selected on the status being `QUALIFIED`. A row
  with neither is a row that cannot have been in this file.

`rx_number` and `pharmacy_npi` are optional and must stay optional: line 4 of the sample is a
medical dispense that has neither, and requiring them would quarantine every medically
administered 340B dispense in the file. `record_count` is optional because it lives on the
trailer, and the trailer is F2's control total rather than this contract's business.

### Notes on the kinds

`qualification_received_at` and `reversal_received_at` are `timestamp`, meaning
`%Y-%m-%dT%H:%M:%SZ` exactly — `2025-12-22T15:00:00Z` in the sample. `received_at` is the only
temporal field the pipeline honours, and truncating it to a date would lose the ordering the
pipeline reads.

`fill_date` is `date_wire`, meaning eight digits. `"2026-01-26"` in that column is a type
change even though both sides are `str`, and it is precisely the kind of change that adapts
cleanly into the wrong answer — which is why the shape is checked before adaptation rather
than inside it.

`record_count` and `reversal_quantity` are `integer_text` rather than `integer` because on a
vendor CSV every cell is a string, and in `reversal_quantity`'s case the leading minus is the
whole content of the field.

## 8. Versioning

The version string is `verity-export-1.0.0`, and it is `INVENTED` — Verity publishes no field
dictionary, so there is no vendor version to track.

**Verity does not write its version into the file, and Craneware does.** That difference is
load-bearing. Craneware's rows carry `schema_version`, so a connector passes
`version=record["schema_version"]` and the wire names its own contract. Verity writes only a
record count, so the registry has to be *told*, and `version=None` resolves to the current
registration. The consequence is that a Verity re-cut is not self-announcing: if Verity
changes the layout and we do not bump the registration, records are validated against a
contract the file no longer follows. What catches that is the contract itself — a vanished or
reshaped field is a violation — and not the version string.

`verity_accumulations` and `verity_invoices` share the version string because they are cut by
the same export product at the same revision, and they are still two separate contracts because
they are two separate files with genuinely different required sets. Registering them
separately is what lets `verity_invoices` require five fields this dataset leaves optional.

Bump the version whenever a column is added, removed or reordered, and register the new
contract beside the old one rather than editing it. `SchemaRegistry.register` refuses to
redefine an existing `(source_id, version)` pair with different content, because the second
definition would silently replace the first and every record already validated against the
first would have been checked against a contract that no longer exists. An archived file has to
keep replaying against the contract it was written under, which is what
`register(..., current=False)` is for. Asking for a version we do not hold raises
`UnknownSchemaVersionError` rather than falling back to the newest, because a silent fallback
is exactly how a connector validates a 2.0.0 file against the 1.0.0 contract, finds nothing
wrong, and reports success.
