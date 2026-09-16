# Interface contract — `craneware_claims_report`

| | |
|---|---|
| Source id | `craneware_claims_report` |
| Vendor | The Craneware Group (Sentinel / Sentrex / Trisus) |
| Dataset | Claims Report, one of the five reports `CRANEWARE-001` names |
| Transport | `TransportKind.SFTP` |
| Contract version | `craneware-sftp-export-1.0.0` |
| Registered in | `src/recon/connectors/schema_registry.py`, as `CRANEWARE_CLAIMS_REPORT` |
| Writer (local stand-in) | `src/recon/mocks/craneware_export.py` |

Requirement B2 of `docs/connectivity_layer_requirements.md`, which is Doc 2 step 2
(`DOC2-002`) in artefact form. This is the Claims Report, and it is the report that carries
both the money and the reversal flag — which makes it the one where a shape change does the
most damage before anyone notices.

**The transport and the report name are Craneware's. Every field is ours.**
`CRANEWARE-001` is Craneware's own 14 June 2023 announcement, retrieved verbatim, and it
names all five reports and the SFTP folder they land in. It names no column of any of them.
`docs/vendor_evidence/craneware.md` lists the column list as gap 1 of seven. So the
twenty-two fields below are an honest invention, declared one by one in
`src/recon/mocks/MOCK_FIELDS.md` under `craneware.claims_report.*`, all twenty-two tagged
`INVENTED`. Nothing in this file should be read as a statement about what Craneware
actually emits.

---

## 1. Endpoint and file layout

There is no API. `CRANEWARE-005` records that no public external customer API specification
exists for Sentinel, Sentrex or Trisus, and an independent search of Craneware's own
properties found none either, so the connector commits to the SFTP baseline. Building
against a hypothetical API would mean inventing both the transport and the payload, and the
whole point of the evidence directory is that we invent one of those and not both.

| Property | Value |
|---|---|
| Delivery | Scheduled drop into the customer's SFTP folder (`CRANEWARE-001`, first-hand) |
| Endpoint | An SFTP directory on the customer's server. The path is per deployment and belongs in the `Source.endpoint` registry row, never in this file |
| Credential | A `Source.credential_ref` *name*, resolved at fetch time by `connectors/credentials.py`. No credential value appears in any tracked file |
| Filename | `claims_report.csv` — stable and undated |
| Format | CSV, comma-delimited, minimal quoting |
| Encoding | UTF-8, no byte-order mark |
| Line endings | `\n` on every platform |
| Header row | Line 1, the twenty-two column names in the order of the table in §7 |
| Trailer row | Last line, `record_type = TRAILER` |
| Local stand-in | `settings.vendor_dir() / "craneware" / "claims_report.csv"`, written by `craneware_export.write` |

**The filename is undated on purpose.** A real scheduled drop would date it. Dating it here
would mean either inventing a cadence Craneware has not published, or stamping the run date
into the bytes — and the second makes the same input produce a different file on two
different days, which is the reproducibility guarantee the rest of this repository is built
on. The transport may date the drop; the payload does not.

**Line endings are pinned rather than defaulted.** `csv` defaults to `\r\n` on Windows, so
the same input would hash differently on two platforms and the manifest would become a
statement about the operating system instead of about the data.

### The row envelope

Three columns are file-level controls rather than claim data, and they wrap the payload: two
in front, one at the back. A reader that slices the controls off takes a contiguous window,
and the count at the back is never mistaken for a payload column while scanning left to
right. `CRANEWARE-004` asks for file-level controls and file schema versioning; Craneware
publishes no format for either, so both shapes are ours.

- `record_type` is `DETAIL` or `TRAILER`, on every row.
- `schema_version` is `craneware-sftp-export-1.0.0`, on every row including the trailer.
  It is a column and not a header comment so that a plain `csv.DictReader` finds it, which
  is what lets a connector pass `version=record["schema_version"]` to the registry and have
  the wire name its own contract.
- `declared_record_count` is empty on every detail row and carries the number of detail rows
  on the trailer. The trailer does not count itself. `craneware_export.declared_record_count`
  reads it back, so requirement F2 never re-implements the parse.

**This contract describes a DETAIL row and never the TRAILER row.** Hand it a trailer and it
fails, correctly and uselessly — a trailer is not a dispense, so of course it has no NDC. The
split belongs to whoever reads the file. A contract that quietly excused the trailer would
also excuse a detail row whose `record_type` had been corrupted into something unrecognised,
and that is a record going missing without a quarantine.

## 2. Sample payload

Real rows from a real export. Reproduce them with profile `demo`, the published master seed
`20250701` and `CuratedSpine.MIXED`: run `recon.generators.orchestrator.generate` then
`write_outputs`, then `recon.mocks.source.load_source` and
`recon.mocks.craneware_export.render`. That export has 39 detail rows.

```
record_type,schema_version,covered_entity_id,rx_number,pharmacy_npi,provider_npi,ndc11,fill_date,manufacturer,qualification_status,disqualification_reason,manufacturer_status,rejection_reason,dispense_status,reversal_date,reversal_reason,reversal_quantity,rebate_submitted_date,rebate_amount,rebate_payment_date,rebate_allocation_code,declared_record_count
DETAIL,craneware-sftp-export-1.0.0,DSH310074,221055333,1234567893,,00078052515,20251216,SAGEPOINT,QUALIFIED,,,,ACTIVE,,,,20251226,1290.80,20260116,RBT-20260116-13446,
DETAIL,craneware-sftp-export-1.0.0,DSH310074,221055399,1234567893,,00078052515,20260126,SAGEPOINT,QUALIFIED,,,,REVERSED,2026-04-09T13:00:00Z,RETURN_TO_STOCK,-28,20260212,1290.80,20260312,RBT-20260312-12710,
DETAIL,craneware-sftp-export-1.0.0,DSH310074,221055439,1234567893,,00002432280,20250811,ALDEBARAN,QUALIFIED,,REJECTED,NON_CONFORMING_45_DAY,ACTIVE,,,,20251025,,,,
DETAIL,craneware-sftp-export-1.0.0,DSH310074,221055413,1234567893,,00071015523,20251028,VERION,,,,,ACTIVE,,,,,,,,
TRAILER,craneware-sftp-export-1.0.0,,,,,,,,,,,,,,,,,,,,39
```

Line 2 is the **paid** archetype: qualified, submitted, approved and settled, with the
allocation code the manufacturer's ACH credit carries.

Line 3 is the **reversed** archetype, and it is the row requirement B3 is about. Read it
carefully: `dispense_status` is `REVERSED`, `rebate_amount` is still `1290.80`, and there is
no second row. The dispense was paid and then reversed, and the report states both facts on
one line.

Line 4 is the **rejected** archetype: the TPA qualified it and the manufacturer said no. Note
`rebate_amount` is empty — a rejected rebate was never paid, so there is no figure to carry.

Line 5 is the **unmatched** archetype: nothing has come back. `qualification_status` is empty
because the dispense is still in review — a `QUALIFICATION_DECISION` event exists and carries
no verdict — not because the TPA refused it. Absence and refusal are different states and
this file keeps them different.

Line 6 is the trailer: the two leading control columns populated, the nineteen payload columns
empty, and the count of detail rows at the back.

## 3. Join keys

340B has no shared identifier space. The only bridge from a Craneware claim row to anything
else in this system is the natural key, assembled from five columns this file spells as
`rx_number`, `pharmacy_npi`, `provider_npi`, `ndc11` and `fill_date`. Note `ndc11` with no
underscore — Verity spells the same concept `ndc_11`, and a connector that assumed one
spelling across both vendors reads `None` and joins nothing.

| Crosswalk key | Canonical form | Built from |
|---|---|---|
| `KeyType.NATURAL_340B_PHARMACY` | `pharmacy_npi\|rx_number\|ndc11\|fill_date` | a row with a prescription |
| `KeyType.NATURAL_340B_MEDICAL` | `provider_npi\|ndc11\|service_date` | a row with no prescription; `fill_date` is the service date |
| `KeyType.ALLOCATION_CODE` | `rebate_allocation_code`, verbatim | the paid rows only |

Three things a connector has to know about these keys, each of which produces a wrong answer
silently if it is not known:

**A row legitimately has no `rx_number` and no `pharmacy_npi`.** A clinic-administered drug
has no prescription. Those rows carry `provider_npi` instead and key medically. The medical
key is deliberately weaker than the pharmacy one: two administrations of the same drug at the
same site on the same day are genuinely indistinguishable, so a multi-hit is
`AMBIGUOUS_KEY_MATCH` and gets parked. Picking one is a coin flip that puts real money on the
wrong episode and looks identical to a correct match in every report afterwards.

**`fill_date` is `CCYYMMDD` and is never parsed.** Fixed width with no separator means
lexicographic order is chronological order, so it is compared as text. A connector that
reformats it to `2025-12-16` on the way in has changed the key and joins nothing, and the
contract catches that: `fill_date` is kind `date_wire`, and `"2025-12-16"` fails it.

**`rx_number` may have drifted, and repairing it is wrong.** The generator injects identifier
drift into exactly one feed per episode (defect D-6). A claim that cannot be joined is a
claim a real operator also cannot join, and a connector that "corrected" the spelling would
delete the crosswalk-miss exception the dataset exists to produce.

`rebate_allocation_code` resolves to the rebate batch. The same string rides `trn02` on the
manufacturer's ACH credit on the bank feed, where it is keyed as `KeyType.TRN02` — but only
when the credit actually arrived. In the demo export above, `RBT-20260116-13446` has no
matching bank credit. That is a real exception, not a contract violation, and the contract
correctly says nothing about it.

## 4. Status codes

Four vocabularies appear on this report, plus the envelope's own. All four are the TPA feed's
own words, carried verbatim under requirement C3 — a reason we reworded is a reason the
vendor cannot be held to. None of them is Craneware's; Craneware publishes no vocabulary.

| Column | Value | Meaning |
|---|---|---|
| `record_type` | `DETAIL` | A dispense row. This contract describes exactly this row |
| `record_type` | `TRAILER` | The control total. Not validated against this contract |
| `qualification_status` | `QUALIFIED` | The TPA granted 340B eligibility |
| `qualification_status` | `NOT_QUALIFIED` | The TPA declined; `disqualification_reason` says why |
| `qualification_status` | *empty* | No verdict yet — either no `QUALIFICATION_DECISION` event, or one still in review carrying no status. Not a refusal |
| `disqualification_reason` | `NO_QUALIFYING_ENCOUNTER` | No eligible encounter backs the dispense |
| `disqualification_reason` | `PRESCRIBER_NOT_AFFILIATED` | The prescriber is not affiliated with the covered entity |
| `disqualification_reason` | `UNREGISTERED_LOCATION` | The dispensing site is not on the 340B registration |
| `disqualification_reason` | `MEDICAID_DUPLICATE_DISCOUNT` | Claiming 340B would duplicate a Medicaid rebate |
| `manufacturer_status` | `APPROVED` | The manufacturer accepted the rebate request |
| `manufacturer_status` | `REJECTED` | The manufacturer refused; `rejection_reason` says why |
| `manufacturer_status` | *empty* | No standalone `MANUFACTURER_DECISION` record exists. A dispense paid straight out of a batch carries its status on the batch line instead, which is `verity_invoices.batch_line_manufacturer_status` |
| `rejection_reason` | `CONTRACT_PHARMACY_RESTRICTED` | The manufacturer restricts this contract pharmacy |
| `rejection_reason` | `NON_CONFORMING_45_DAY` | The request missed the 45-day submission window (`VERITY-003`, `BEACON-009`) |
| `dispense_status` | `ACTIVE` | No reversal has arrived |
| `dispense_status` | `REVERSED` | A `DISPENSE_REVERSAL` event exists. See §5 |
| `reversal_reason` | `RETURN_TO_STOCK` | The drug went back on the shelf |

The four disqualification reasons and both rejection reasons are the complete vocabularies —
they are `reference/codes.py`'s `disqualification_reasons()` and
`manufacturer_rejection_reasons()`. The demo export exercises two of each; the other two
disqualification reasons are reachable, and a connector that only handled what it saw in one
export would meet them later.

**`replenishment_status` is not on this report.** It belongs to the Unreplenished Costs
report, which is a different file with a different population (qualified and unpaid) and no
registered contract in this repository. Its three values are `REBATE_REJECTED`,
`SUBMITTED_AWAITING_PAYMENT` and `AWAITING_SUBMISSION`. It is named here only so that a
reader who came looking for it stops looking in this file.

## 5. Reversal semantics — requirement B3

```
craneware_export.REVERSAL_REPRESENTATION = "STATUS_FLAG_ON_ORIGINAL_ROW"
```

**A reversal is a status flag on the restated row. There is never a second row, and no
amount is ever negated.** The Claims Report emits exactly one row per dispense. When a
dispense was reversed, that one row carries `dispense_status = REVERSED`, plus the reversal's
own `reversal_date`, `reversal_reason` and signed `reversal_quantity`. The `rebate_amount`
beside it is the amount that was actually paid, unchanged.

The argument for that choice is that a scheduled report is a *snapshot of state as of the
run*, not an event journal, and the plausible thing for a snapshot to do is restate the row
rather than append a correction to it. The choice is `INVENTED` — gap 4 of
`docs/vendor_evidence/craneware.md` is that Craneware does not publish how reversals are
represented — and it is written down here rather than left for a connector to infer, because
inferring it wrong is invisible.

**What a connector written for the other representation does wrong.** Suppose it was written
against a vendor that represents a reversal as a negative-quantity row: the original stays on
the wire and a second, correcting row arrives beside it. Point that connector at this file
and it scans for a row with a negative quantity or a negative amount, finds none, concludes
nothing was reversed, and keeps the rebate. Line 3 of the sample above is exactly that trap:
it carries `1290.80` in `rebate_amount` and a `-28` in a column the connector is not reading.
The ledger ends up one rebate long. Nothing errors, no quarantine row is written, and every
report downstream is internally consistent and wrong.

The mirror failure is just as quiet. A connector written against *this* file — one that nets
the amount out whenever it sees `dispense_status = REVERSED` — pointed at a negative-row
vendor will net the reversal once from the flag and once from the negative row, and the
ledger ends up one rebate short.

**The hazard is live in this repository, not theoretical.** `recon.generators.tpa` writes a
reversal to `tpa_340b_events.jsonl` as a separate `DISPENSE_REVERSAL` record carrying a
negative `quantity_dispensed`, with the original dispense left on the wire, following NCPDP's
convention (`STD-NCPDP-TELECOM`). Its own comment is explicit that the quantity is used as
given and never re-negated, because negating it a second time once put the reversal on the
wire as a positive quantity and silently destroyed the one property the feed spec insists on.
So one system in this build uses a negative row and this export uses a flag, over the same
episode. `reversal_quantity` on this report is that already-negative figure copied across
untouched, which is why the contract types it `integer_text` — a kind that accepts a leading
minus — and not `integer`.

**The one in-vendor exception, which is not a contradiction.** The Audit report *is* a
journal, so a reversal appears there as its own event row. A connector must ingest Claims or
Audit as its claim source and never both: Audit is the trail, Claims is the state, and
reading both as claim rows double-counts every reversal.

## 6. Cadence

`CRANEWARE-004` quotes Doc 2 page 6 verbatim:

> **Transport** — Scheduled SFTP is the proven direct path; use file-level controls, file
> schema versioning and daily ingestion.

`DOC2-013`, same page, adds the pairing: *"Use Craneware as the second file-based pattern
after Verity; both can share the same secure-file connector framework."*

Read that precisely. **Daily ingestion is a recommendation to us, not a statement of
Craneware's cadence.** `CRANEWARE-001` confirms delivery is *scheduled*; the schedule's shape
is gap 3 of `docs/vendor_evidence/craneware.md` and is unknown. So the connector polls daily
because Doc 2 recommends it, and the checkpoint is what makes that safe against a vendor who
turns out to drop weekly. A connector that assumed a daily drop *guaranteed* a daily file
would report a delivery failure every quiet day, and a queue that cries wolf daily is a queue
nobody reads.

## 7. Registered fields

Contract version: `craneware-sftp-export-1.0.0`

| # | field | kind | required |
|---|---|---|---|
| 1 | `record_type` | string | yes |
| 2 | `schema_version` | string | yes |
| 3 | `covered_entity_id` | string | no |
| 4 | `rx_number` | string | no |
| 5 | `pharmacy_npi` | string | no |
| 6 | `provider_npi` | string | no |
| 7 | `ndc11` | string | yes |
| 8 | `fill_date` | date_wire | yes |
| 9 | `manufacturer` | string | no |
| 10 | `qualification_status` | string | no |
| 11 | `disqualification_reason` | string | no |
| 12 | `manufacturer_status` | string | no |
| 13 | `rejection_reason` | string | no |
| 14 | `dispense_status` | string | yes |
| 15 | `reversal_date` | timestamp | no |
| 16 | `reversal_reason` | string | no |
| 17 | `reversal_quantity` | integer_text | no |
| 18 | `rebate_submitted_date` | date_wire | no |
| 19 | `rebate_amount` | decimal_text | no |
| 20 | `rebate_payment_date` | date_wire | no |
| 21 | `rebate_allocation_code` | string | no |
| 22 | `declared_record_count` | integer_text | no |

The `#` column is the file's column order, and it is the header order byte for byte. Nothing
in `SchemaContract.validate` depends on order — a record is a mapping — but a positional
reader depends on it entirely, and pinning it here is what lets B2's acceptance test be a
list comparison instead of a set comparison with a sorting argument attached.

### Why only five fields are required

`required` means the *producer* structurally cannot leave the field out, established by
reading the writer rather than by listing what a consumer would like to have. Marking a field
required that the vendor legitimately leaves empty turns a quiet day into a quarantine storm,
and a quarantine storm is how a team learns to ignore the queue.

- `record_type` and `schema_version` are written onto every row by `_render_csv`, including
  the trailer.
- `dispense_status` is always one of two literals — it is a rename of a lookup, not a verdict.
- `ndc11` and `fill_date` come from fields the TPA adapter reads without `.get`, so the feed
  underneath guarantees them.

Everything else is genuinely optional, and the sample above shows why: a medical dispense has
no `rx_number` at all, an unmatched claim has no `qualification_status` yet, and a rejected
claim has no rebate figures. `declared_record_count` is optional because it lives on the
trailer, and the trailer is F2's control total rather than this contract's business.

### Notes on three kinds that are easy to get wrong

`reversal_date` is typed `timestamp`, not `date_wire`, despite the name. It is written from
the reversal event's `received_at`, so it is a full `%Y-%m-%dT%H:%M:%SZ` value — see
`2026-04-09T13:00:00Z` in the sample. Contracting it as a date would accept a truncated value
and lose the time the reversal actually arrived.

`rebate_amount` is `decimal_text`: money is exact decimal text on the wire and never a float.
The contract refuses a float by name and says so in the violation, because a float in a money
column almost always means the payload was parsed without `parse_float=Decimal`, which is a
bug in the reader rather than a defect in the feed, and the two get fixed by different people.

`reversal_quantity` is `integer_text` rather than `integer` because the value arrives as text
already carrying its sign, and the sign is the whole content of the field. See §5.

## 8. Versioning

The version string is `craneware-sftp-export-1.0.0`, it is `INVENTED`, and it is defined once
in `craneware_export.SCHEMA_VERSION` and repeated in `schema_registry.CRANEWARE_CLAIMS_REPORT`.

**That duplication is deliberate.** A contract computed from its own producer can never catch
the producer drifting — it agrees with whatever the writer does, including the day the writer
starts doing the wrong thing. Two independent declarations can disagree, and B2's acceptance
test is what makes the disagreement visible. Importing `COLUMNS` from `recon.mocks` would turn
that test into a tautology, and would also point the production connector package at a package
of test doubles.

Bump the version whenever a column is added, removed or reordered, and register the new
contract beside the old one rather than editing it. `SchemaRegistry.register` refuses to
redefine an existing `(source_id, version)` pair with different content, because the second
definition would silently replace the first and every record already validated against the
first would have been checked against a contract that no longer exists. An archived file has
to keep replaying against the contract it was written under, which is what
`register(..., current=False)` is for.

Because `schema_version` rides on every row, a connector passes
`version=record["schema_version"]` and the wire names its own contract. A version we have
never registered raises `UnknownSchemaVersionError`, which `schema_registry.check` converts
into a quarantine detail rather than a crash — that case is the vendor having re-cut the
export without telling anyone, and it is the purest `SCHEMA_VERSION_MISMATCH` there is. There
is no fallback to the newest registered version, because a silent fallback is exactly how a
connector validates a 2.0.0 file against the 1.0.0 contract, finds nothing wrong, and reports
success.
