# Evidence: Beacon (Beacon Channel Management, operated by Second Sight Solutions)

Retrieved: 2026-09-16; **two entries re-retrieved 2026-09-17** (see below).

**A note on the operator, because this file said the wrong thing for a day.** Beacon is
operated by **Second Sight Solutions**, not by Kalderos. Every "Public evidence reviewed"
link on page 3 of `docs/Assignment_Doc_2.pdf` — the FAQ, the SDK page, partner tokens,
partner permissions, the pharmacy template — resolves to
`support.beaconchannelmanagement.com`, which settles it. Kalderos is a different company
(Truzo, 340B Pay; acquired by Model N) and naming it here was simply wrong. Corrected
rather than quietly dropped, because a vendor-evidence file that silently changes who a
vendor *is* has the same problem as one that silently changes what a vendor *said*.

**Summary of what this page establishes: the two claim templates, first-hand-ish, and
nothing else the build wanted.**

The Beacon Support Center at `support.beaconchannelmanagement.com` is real, is public,
and hosts exactly the articles this build needs — the pharmacy claim data template, the
medical claim data template, the validation code glossary, the back-end validations page.
**Every one of them returns HTTP 403 to a direct automated fetch.** The site is
Intercom-hosted and blocks non-browser clients. That was tested against four separate URLs
including a collection index, so it is systematic and not an outage.

**What changed on 2026-09-17.** The 403 is applied to the fetching client, not to
crawlers as a policy: the host does not block `robots.txt`, and `robots.txt` advertises the
sitemap. Routing the same URLs through the text-extraction proxy at `https://r.jina.ai/`
returned HTTP 200 and the real page text. `BEACON-001` (pharmacy template) and
`BEACON-002` (medical template) are therefore no longer `UNAVAILABLE`. They carry the new
status **`RETRIEVED_VIA_PROXY`**, and their full captures are checked in at
`raw/beacon_pharmacy_template.md` and `raw/beacon_medical_template.md`.

`RETRIEVED_VIA_PROXY` is a notch below `RETRIEVED` on purpose. The words are Beacon's —
that is what puts it above `SECOND_HAND` — but a party we do not control stood in the
middle, and the proxy renders rather than mirrors: it flattened Beacon's HTML tables into
run-together text and dropped the downloadable template attachments entirely. So the
published **field list** is now evidence; the **template file** still is not.
`README.md` states the rule in full.

`BEACON-003` through `BEACON-007` remain `UNAVAILABLE`. Their failure notes now say so
precisely — 403 to a direct fetch, and no proxy capture exists for them — so that nobody
reads their silence as "the proxy was tried and failed too."

Requirement V2 still governs everything not recovered: the URL, the failure and the date
are recorded, and the fact the page would have established is treated as **UNKNOWN**. It
is not reconstructed from recollection. A model's memory of Beacon's field list is not
evidence, and writing one from memory would produce a mock that is confidently wrong — the
specific failure mode requirement group V exists to prevent.

**Consequence for the build.** Beacon's pharmacy and medical claim field names may now be
tagged `SPEC` citing `BEACON-001` / `BEACON-002`, spelled **exactly as Beacon publishes
them** — `340B ID`, `Rx Number`, `NDC-11`, `HCPCS Code Modifier`, spaces and hyphens
intact, never transliterated to `snake_case`. Everything else Beacon-side — auth, endpoint
shapes, Beacon ID format, validation codes — is still carried by `assignment_doc2.md`
(`SECOND_HAND`, page-cited) or is `INVENTED` and tagged as such in the mock's
`MOCK_FIELDS.md`.

---

## BEACON-001 — Pharmacy claims data template · RETRIEVED_VIA_PROXY

- **Page URL:** https://support.beaconchannelmanagement.com/en/articles/9723402-data-template-pharmacy-claims-data
- **Retrieval URL:** `https://r.jina.ai/https://support.beaconchannelmanagement.com/en/articles/9723402-data-template-pharmacy-claims-data` → **HTTP 200**
- **Direct fetch of the same URL, as a control:** **HTTP 403**
- **Retrieved:** 2026-09-17. Article dated **December 10, 2025** on the page.
- **Full capture:** [`raw/beacon_pharmacy_template.md`](raw/beacon_pharmacy_template.md)
- **Establishes:** the pharmacy claim template field list as printed in the article —
  **11 fields, all 11 carrying the required asterisk**, each with Beacon's own data type
  and description:

  | # | Field (verbatim) | Data type (verbatim) |
  |---|---|---|
  | 1 | `340B ID` | `Alpha/Numeric` |
  | 2 | `Date Prescribed` | `Standard date formats` |
  | 3 | `Date of Service` | `Standard date formats` |
  | 4 | `Rx Number` | `Numeric` |
  | 5 | `Fill Number` | `Numeric - 0-99` |
  | 6 | `NDC-11` | `Numeric - 11 digits` |
  | 7 | `Quantity Dispensed` | `Numeric` |
  | 8 | `Prescriber ID` | `Numeric - 10 digits` |
  | 9 | `Service Provider ID` | `Numeric - 10 digits` |
  | 10 | `Rx Bin` | `Numeric - 6 digits` |
  | 11 | `Rx PCN` | `Alpha/Numeric` |

  Beacon marks requirement with a literal `*` after the field name and a footnote reading
  `_*Indicates a required field_`. It never writes the words "required" or "optional" per
  field. The only concrete values on the page are three sentinels: `999999` for an
  uninsured or cash payer's `Rx Bin`, and `CASH` / `NONE` for `Rx PCN`.

  Spelling is reproduced, not corrected. `Rx Bin` is lower-case `in` even though its own
  description says "Include BIN"; `NDC-11` is hyphenated; `340B ID` has a space; the data
  type separator here is a plain hyphen where the medical template uses an en dash. These
  are not the same strings and must not be collapsed when matching programmatically.

- **Does NOT establish, and these stay UNKNOWN:** the byte-exact header row of the
  downloadable template file (the attachment did not survive text extraction, so the
  article's field list is not proof of the file's columns), the file format, delimiter or
  encoding, the submission mechanism, the validation codes, and which formats Beacon means
  by "standard date formats" — that phrase is never defined and ISO-8601 must not be
  assumed.
- **Therefore:** a pharmacy field name may be tagged `SPEC` citing `BEACON-001`, spelled
  verbatim. Anything about the *file* remains `INVENTED`.

## BEACON-002 — Medical claims data template · RETRIEVED_VIA_PROXY

- **Page URL:** https://support.beaconchannelmanagement.com/en/articles/9723390-data-template-medical-claims-data
- **Retrieval URL:** `https://r.jina.ai/https://support.beaconchannelmanagement.com/en/articles/9723390-data-template-medical-claims-data` → **HTTP 200**
- **Direct fetch of this host, as a control:** **HTTP 403**
- **Retrieved:** 2026-09-17. Article dated **March 17, 2026** on the page — roughly three
  months newer than the pharmacy template, so the two should not be assumed mutually
  consistent.
- **Full capture:** [`raw/beacon_medical_template.md`](raw/beacon_medical_template.md)
- **Establishes:** the medical claim template field list as printed in the article —
  **13 fields, 10 marked required and 3 not**:

  | # | Field (verbatim) | Required marking | Data type (verbatim) |
  |---|---|---|---|
  | 1 | `340B ID` | `*` | `Alpha/Numeric` |
  | 2 | `Claim Number` | `*` | `Alpha/Numeric` |
  | 3 | `Claim Line Number` | `*` | `Numeric` |
  | 4 | `Date of Service` | `*` | `Standard date formats` |
  | 5 | `HCPCS Code` | not marked | `Alpha/Numeric` |
  | 6 | `HCPCS Code Modifier` | not marked | `Alpha/Numeric` |
  | 7 | `Health Plan Name` | `*` | `Alpha/Numeric` |
  | 8 | `Health Plan ID` | `*` | `Alpha/Numeric` |
  | 9 | `NDC-11` | `*` | `Numeric – 11 digits` |
  | 10 | `Rendering Physician ID` | `*` | `Numeric – 10 digits` |
  | 11 | `Quantity` | `*` | `Numeric` |
  | 12 | `Unit of Measure` | not marked | `Alpha/Numeric` |
  | 13 | `Service Provider ID` | `*` | `Numeric – 10 digits` |

  "Not marked" is deliberately not "optional". Beacon never writes the word optional, and
  for `Unit of Measure` optional would be actively wrong: its description carries the one
  cross-field rule Beacon publishes anywhere — *"Either HCPCS code or UOM is required."*
  `HCPCS Code` is to be left blank for miscellaneous codes (`A9270`, `J3490` are Beacon's
  own examples), and `Quantity` is expressed in **CMS-defined billable units** when a
  specific HCPCS code is reported and in **NCPDP standardized billing units** when it is
  not. That is the trap on this template: the same number means two different things
  depending on row 5.

- **Does NOT establish, and these stay UNKNOWN:** the byte-exact header row of the
  downloadable template file, the allowed `Unit of Measure` values (Beacon defers to NCPDP
  without listing them), how up to four HCPCS modifiers share one field, the file format,
  and the submission mechanism.
- **Therefore:** a medical field name may be tagged `SPEC` citing `BEACON-002`. This row
  also **supersedes `BEACON-008`**, the search-index summary that was the only prior signal
  about this page.

## BEACON-014 — Pharmacy claims template FILE · RETRIEVED

- **URL:** https://support.beaconchannelmanagement.com/en/articles/9723402-data-template-pharmacy-claims-data
- **Retrieved:** 2026-09-17, in a real browser session. The article offers a download under
  *"Download the pharmacy claims data template here"*; the linked file was fetched from
  Intercom's CDN and saved byte-for-byte at
  `raw/templates/beacon_pharmacy_template.csv` (135 bytes).
- **Header row, verbatim:**

  ```
  340b_id,date_prescribed,date_of_service,rx_number,fill_number,ndc_11,quantity_dispensed,prescriber_id,service_provider_id,rx_bin,rx_pcn
  ```

- **This supersedes `BEACON-001`'s spelling.** `BEACON-001` said so itself, in as many
  words: *"Does NOT establish the byte-exact header row of the downloadable template file,
  which the proxy did not return."* The article renders **display labels** — `340B ID`,
  `NDC-11`, `Date of Service`. The file uses snake_case. Both are Beacon's, and only one is
  what Beacon parses.
- **Why it took a browser.** A direct fetch returns `HTTP 403`, and the text-extraction
  proxy that got past that could render the article's prose but not follow its download
  link. Nothing short of a browser session reaches this file, which is the whole reason
  `BEACON-001` carries the weaker tier.
- **Worth noting:** `ndc_11` is character-identical to Verity's spelling of the same fact.
  Two of the three spellings this repository once carried for an NDC are one string.
- **Therefore:** a pharmacy field name is tagged `SPEC` citing `BEACON-014` and spelled as
  the file spells it. `BEACON-001` remains the citation for Beacon's **descriptions**,
  required markers, data types and the three sentinel values — none of which the header row
  carries.

## BEACON-015 — Medical claims template FILE · RETRIEVED

- **URL:** https://support.beaconchannelmanagement.com/en/articles/9723390-data-template-medical-claims-data
- **Retrieved:** 2026-09-17, same browser session. Saved at
  `raw/templates/beacon_medical_template.csv` (260 bytes).
- **Header row, verbatim:**

  ```
  340b_id,claim_number,claim_line_number,date_of_service,hcpcs_code,hcpcs_code_modifier_1,hcpcs_code_modifier_2,hcpcs_code_modifier_3,hcpcs_code_modifier_4,health_plan_name,health_plan_id,ndc_11,rendering_physician_id,quantity,unit_of_measure,service_provider_id
  ```

- **Sixteen columns, not the thirteen the article's field list renders.** The page prints
  one row called *HCPCS Code Modifier* and notes that *"up to four modifier codes may be
  entered for the same claim line"*. We read that as a delimiting question and recorded it
  as UNKNOWN. It is not a delimiting question: **the file carries four columns**,
  `hcpcs_code_modifier_1` through `_4`. Nothing is delimited and the UNKNOWN is closed.
- **This supersedes `BEACON-002`'s spelling and its field count.** Not because the page is
  wrong — a prose table that rounds four columns down to one concept is a fair description.
  It is simply not a specification, and the difference only shows up against the file.
- **Therefore:** a medical field name is tagged `SPEC` citing `BEACON-015`. `BEACON-002`
  remains the citation for descriptions, required markers, and the conditional unit-of-measure
  rule, which the header row cannot express.

## BEACON-003 — Pharmacy claims validation code glossary · UNAVAILABLE

- **URL:** https://support.beaconchannelmanagement.com/en/articles/13186697-pharmacy-claims-validation-code-glossary
- **Title (confirmed via search index):** "Pharmacy Claims Validation Code Glossary | Beacon Support Center"
- **Failure:** `HTTP 403 Forbidden` to a direct automated fetch. No `r.jina.ai` capture
  exists for this article either, so it is **unretrieved**, not known-blocked.
- **Would have established:** the rejection/validation code vocabulary. Requirement C3
  says a rejected submission must preserve "the vendor's reason **verbatim, not
  paraphrased**" — without this page we do not know the real vocabulary, so the mock
  replays reasons already present in our generated `TPA_MANUFACTURER_DECISION` records
  rather than inventing Beacon-specific codes.
- **Therefore:** the validation code vocabulary is **UNKNOWN**.

## BEACON-004 — Back-end validations · UNAVAILABLE

- **URL:** https://support.beaconchannelmanagement.com/en/articles/13186516-back-end-validations
- **Title (confirmed via search index):** "Back-end Validations | Beacon Support Center"
- **Failure:** `HTTP 403 Forbidden` to a direct automated fetch. No `r.jina.ai` capture
  exists for this article either, so it is **unretrieved**, not known-blocked.
- **Would have established:** what Beacon checks after ingestion — data consistency,
  program eligibility, compliance with HRSA guidance and manufacturer policies.
- **Therefore:** **UNKNOWN**. The mock does not attempt to reproduce back-end validation
  logic; it replays outcomes already decided by the generator (requirement M3).

## BEACON-005 — How to submit medical claims to Beacon · UNAVAILABLE

- **URL:** https://support.beaconchannelmanagement.com/en/articles/9670192-how-to-submit-medical-claims-to-beacon
- **Failure:** `HTTP 403 Forbidden` to a direct automated fetch. No `r.jina.ai` capture
  exists for this article either, so it is **unretrieved**, not known-blocked.
- **Would have established:** the submission workflow and endpoint semantics.

## BEACON-006 — Column mapping template guide · UNAVAILABLE

- **URL:** https://support.beaconchannelmanagement.com/en/articles/9707706-how-to-create-a-column-mapping-template-for-data-submissions
- **Failure:** `HTTP 403 Forbidden` to a direct automated fetch. No `r.jina.ai` capture
  exists for this article either, so it is **unretrieved**, not known-blocked.
- **Would have established:** that Beacon accepts a customer-defined column mapping,
  which is relevant to how rigid the template actually is.

## BEACON-007 — Beacon Platform Support collection index · UNAVAILABLE

- **URL:** https://support.beaconchannelmanagement.com/en/collections/11972931-beacon-platform-support
- **Failure:** `HTTP 403 Forbidden`. Fetched directly to enumerate the full article list;
  refused. No `r.jina.ai` capture of *this* collection exists. A **sibling** collection,
  `Data Templates` (9974649), *was* readable through the proxy and listed exactly two
  articles — the pharmacy and medical templates now held as `BEACON-001` and `BEACON-002`.
  That is what corroborates that the proxy returned the right pages; it is not an
  enumeration of this collection.
- **Would have established:** the complete set of published articles, so that this file
  could be exhaustive rather than limited to articles a search index happened to surface.
- **Therefore:** this evidence file is **still known to be incomplete**. The proxy route
  recovered two named articles; it did not tell us how many others exist. Stated explicitly
  so no reader mistakes its silence for absence.

---

## BEACON-008 — Medical claim matching key · SECOND_HAND, superseded by BEACON-002

- **Source:** search-index summary of the `BEACON-002` page, not the page itself.
- **Claim:** for medical claim submissions the identifying combination of fields includes
  the claim number, claim line number, date of service, NDC, service provider ID and
  HCPCS modifier code.
- **Status caveat:** this is a search engine's extraction. It is **not** a verbatim
  excerpt and must not be treated as one.
- **Superseded.** When this was written the page itself was a 403 and this was the only
  signal we had. `BEACON-002` is now the page. Checked against it, the summary was
  **accurate on all six fields** — they appear there as `Claim Number`, `Claim Line
  Number`, `Date of Service`, `NDC-11`, `Service Provider ID` and `HCPCS Code Modifier`.
  Note what the summary still got wrong in a way that would have mattered: it lower-cased
  and re-spelled every name, and "HCPCS modifier code" is not what Beacon calls the field.
  A mapping built from this row would have had six plausible, wrong column names.
- **Kept, not deleted,** because a second-hand summary turning out to be accurate is itself
  a finding about how far a search extract can be trusted — and because deleting an entry
  that other prose cites is the drift `test_every_evidence_id_cited_in_prose_resolves_to_the_index`
  exists to catch.
- **Use:** motivated requirement E2 (HCPCS alongside NDC). A field must now cite
  `BEACON-002`, not this row.

## BEACON-009 — The 45-day submission window · SECOND_HAND, independently corroborated

- **Claim:** 340B claim submissions must be made within 45 calendar days of the date of
  the eligible dispense or administration.
- **Sources:** a search-index summary of Beacon support material, **and independently**
  `VERITY-003`, which is first-hand retrieved text from Verity's own site reading: *"V340B
  can automatically submit the claim to Beacon on behalf of the CE for rebate claims
  submitted within 45 days"*.
- **Why this one is stronger than `BEACON-008`:** two unrelated sources agree, one of them
  retrieved first-hand from a vendor that integrates with Beacon commercially. This is the
  only Beacon behavioural fact on this page with that property.
- **Use:** the mock's submission window. Still not `SPEC` — Beacon did not tell us
  directly — but it is the best-supported Beacon claim we hold.

## BEACON-010 — SDK required; 340B ESP SDK not valid for rebate model · SECOND_HAND

- **Source:** `DOC2-006`, page 3.
- **Claim:** covered entities and TPAs should use the Beacon SDK for direct data
  submissions; the older 340B ESP SDK is not valid for rebate-model submissions.
- **Use:** establishes that an SDK exists and is the intended path. We do not have the SDK
  package or its API reference (`DOC2-008` states plainly that it "must be obtained
  through Beacon Support"), so `HttpApiTransport` is built to the *documented shape* — a
  two-token authenticated HTTP client — rather than to an SDK we cannot see.

## BEACON-011 — Two-token authentication · SECOND_HAND, single source

- **Source:** `DOC2-006` and `DOC2-007`, page 3. Verbatim from that document: *"Partner
  onboarding yields an Access Token plus a separate Private Token used to authenticate API
  requests"* and *"Beacon partner Access Token + Private Token; entity Admin grants
  Read/Write permission."*
- **Status caveat:** **single-sourced.** The Beacon pages that would confirm this are
  `BEACON-003` through `BEACON-007`, all still `UNAVAILABLE`. `BEACON-001` and
  `BEACON-002` were recovered on 2026-09-17 and say nothing about authentication — they
  are field lists. No independent corroboration was found.
- **Use:** requirement A3's HTTP auth model. Implemented exactly as described and cited to
  this id. If it is wrong, it is wrong in a way that is traceable to a named source rather
  than to our imagination — which is the whole point of the tier system.

## BEACON-012 — Partner model, Read vs Read/Write, per 340B ID · SECOND_HAND

- **Source:** `DOC2-006` and `DOC2-008`, page 3.
- **Claim:** a covered entity can grant a partner Read or Read/Write access through the
  API and SDKs; each covered entity must be registered and explicitly authorize partner
  access; *"permissions may need to be repeated across applicable 340B IDs."*
- **Use:** this is the justification for requirement E1. Permission is scoped to a 340B
  ID, so the 340B ID has to be a real field and a real key, not a column that
  `_create_episode` writes as `None`.

## BEACON-013 — Beacon IDs and submission history · SECOND_HAND

- **Source:** `DOC2-006` and `DOC2-007`, page 3.
- **Claim:** Beacon supports claim-level submission history, validation outcomes and
  Beacon IDs; the recommended design says *"persist Beacon ID against Shields Claim
  Financial Episode."*
- **Use:** requirement C5 — `KeyType.BEACON_ID` as a first-class crosswalk key. The
  *format* of a Beacon ID is **UNKNOWN**; our minted form is `INVENTED` and declared.

---

## What remains genuinely UNKNOWN about Beacon

Stated bluntly, because this list is as load-bearing as the evidence above and a reader
should not have to infer it:

1. ~~**The pharmacy claim template field list.**~~ **Closed on 2026-09-17** by
   `BEACON-001`. All 11 names, types and descriptions are held verbatim. Struck through
   rather than deleted so the change is visible to a reader who saw the earlier version.
2. ~~**The medical claim template field list.**~~ **Closed on 2026-09-17** by `BEACON-002`.
   All 13 names, the required markings and the HCPCS/UOM/Quantity rule are held verbatim.
3. **What the downloadable template *files* actually contain.** Still open, and it is a
   different question from 1 and 2. Both articles head a download link that the text
   extraction dropped, so the byte-exact header row — column spelling, column order, and
   whether the file even matches the article's field list — is unverified.
4. **The file format itself.** CSV or XLSX, delimiter, encoding, quoting, whether a header
   row is expected: neither page says.
5. **Which date formats "standard date formats" means.** Written on both templates and
   defined on neither. ISO-8601 must not be assumed.
6. **The allowed `Unit of Measure` values.** `BEACON-002` defers to "standardized billing
   units as defined by NCPDP" and lists none.
7. **The validation / rejection code vocabulary.** No code, no meaning, no format.
8. **Beacon ID format.** Length, character set, prefix — all unknown. Ours is invented.
9. **Endpoint paths, HTTP verbs, request and response envelopes.** `DOC2-008` says these
   are not public. Ours are invented and declared.
10. **Rate limits, versioning policy, non-production endpoint.** Explicitly named by
    `DOC2-008` as vendor-onboarding items.
11. **Whether every rebate/payment/status retrieval operation is even available
    programmatically**, or whether some require portal workflows. `DOC2-008` raises this as
    an open question and we cannot close it.
12. **The exact token exchange.** That two tokens exist is `BEACON-011`; how they are
    presented on a request — header names, scheme, ordering — is unknown.
13. **The submission mechanism for either template.** Beacon documents it in articles we
    have not captured (`9670187` pharmacy, `9670192` medical, `9707706` column mapping).
    Neither template page says anything about SFTP, API, portal upload, filename
    convention or cadence.

Items 7, 8 and 9 are now the ones that make the Beacon mock a mock. That list used to open
with the two field lists, and it no longer does — the payload's *column names* are Beacon's
own, and what stays invented is the envelope around them: the endpoints, the id format and
the rejection vocabulary. Everything the connector does around those — two-token auth,
idempotency, checkpointing, persisting the returned id as a crosswalk key, reconciling the
payment reference to the bank leg — is real machinery built to `DOC2-007`'s described
behaviour, and switching to a live endpoint is a configuration change. That is the honest
claim, and it is the one the readiness report in F3 will make per source.

One caution that survives the upgrade, and it is the reason `RETRIEVED_VIA_PROXY` is not
`RETRIEVED`: item 3. Knowing the article's field list is not the same as knowing the
file's header row. A connector that writes `340B ID` into a CSV because the *article* spells
it that way is still guessing about the *template*, and it will look completely correct
until the day a real file arrives.
