---
source_url: https://support.beaconchannelmanagement.com/en/articles/9723390-data-template-medical-claims-data
article_id: 9723390
article_title: "Data Template: Medical Claims Data"
article_subtitle: "Streamline data processes with Beacon's data template"
article_date_on_page: "March 17, 2026"
sitemap_lastmod: "2026-03-17T20:20:35Z"
vendor: Beacon (Beacon Channel Management, operated by Second Sight Solutions)
retrieval_method: "r.jina.ai text-extraction proxy — the direct fetch returns HTTP 403"
retrieved_on: 2026-09-17
evidence_tier: RETRIEVED_VIA_PROXY
evidence_tier_rationale: >
  The text below is genuine Beacon page content, but it did not arrive from a
  clean first-hand fetch. support.beaconchannelmanagement.com is Intercom-hosted
  and returns HTTP 403 to direct automated requests (confirmed this session
  against the sibling pharmacy article). The content was obtained by routing the
  request through the third-party text-extraction proxy r.jina.ai, which fetched
  the page and returned its rendered text. That places one party we do not
  control between us and the vendor, and it flattens the page's HTML table into
  run-together text. Both are reasons to rank this a notch below a first-hand
  fetch. It is strong enough to cite for field names and required markers; it is
  NOT strong enough to certify the byte-exact header row of the downloadable
  template file, which we never obtained (see "What this page did not tell us").
field_count: 13
fields_marked_required: 10
fields_not_marked_required: 3
---

# Beacon — Data Template: Medical Claims Data

Retrieved 2026-09-17 via `https://r.jina.ai/https://support.beaconchannelmanagement.com/en/articles/9723390-data-template-medical-claims-data` (HTTP 200).
Direct, unproxied fetches of this host return **HTTP 403**.

---

## 1. Verbatim excerpt

Everything between the fences is the proxy's returned text, unaltered —
including the Intercom font-licence preamble it picked up, the run-together bold
markers where Beacon's HTML table was flattened, and the curly quotation marks.

```
Title: Data Template: Medical Claims Data

URL Source: https://support.beaconchannelmanagement.com/en/articles/9723390-data-template-medical-claims-data

Markdown Content:
Copyright (c) 2023, Intercom, Inc. (legal@intercom.io) with Reserved Font Name "Inter". This Font Software is licensed under the SIL Open Font License, Version 1.1.Copyright (c) 2023, Intercom, Inc. (legal@intercom.io) with Reserved Font Name "Inter". This Font Software is licensed under the SIL Open Font License, Version 1.1.[Skip to main content](https://support.beaconchannelmanagement.com/en/articles/9723390-data-template-medical-claims-data#main-content)

1.   [All Collections](https://support.beaconchannelmanagement.com/en/)
2.   [Using Beacon Rebate Model](https://support.beaconchannelmanagement.com/en/collections/11972931-using-beacon-rebate-model)
3.   [Data Submissions](https://support.beaconchannelmanagement.com/en/collections/10023718-data-submissions)
4.   [Data Templates](https://support.beaconchannelmanagement.com/en/collections/9974649-data-templates)
5.   Data Template: Medical Claims Data

Streamline data processes with Beacon's data template

March 17, 2026

## Download the medical claims data template here:

## View the field list for medical claims data below

**Field****Data Type****Description**
**340B ID***Alpha/Numeric The unique identification number provided by HRSA to the 340B covered entity.
**Claim Number***Alpha/Numeric The unique claim number identifying the claim.
**Claim Line Number***Numeric Identifies an individual line number on a claim. Line numbers distinguish distinct services that are submitted on the same claim.
**Date of Service***Standard date formats Date on which the medication was administered to the patient.
**HCPCS Code**Alpha/Numeric The five digit HCPCS code corresponding to the medication administered. For drugs billed using a non-specific or miscellaneous HCPCS code (e.g., A9270, J3490), please leave this field blank.
**HCPCS Code Modifier**Alpha/Numeric Modifier to the HCPCS code. Up to four modifier codes may be entered for the same claim line.
**Health Plan Name***Alpha/Numeric Name of the patient's primary health insurance plan. Examples include Medicare Part B, MediCal, Aetna POS, etc. If the patient is uninsured or a cash payer, mark “CASH” in this field. If no health plan information is recorded, mark “NONE” in this field.
**Health Plan ID***Alpha/Numeric The identifier code of the patient's primary health insurance plan. If the patient is uninsured or a cash payer, mark “CASH” in this field. If no health plan information is recorded, mark “NONE” in this field.
**NDC-11***Numeric – 11 digits The NDC-11 of the medication administered to the patient.
**Rendering Physician ID***Numeric – 10 digits The NPI of the healthcare provider who rendered or supervised the care reported on the claim.
**Quantity***Numeric The quantity of medication administered to the patient. If a specific (non-miscellaneous) HCPCS code with CMS-defined billing units is reported, quantity must reflect the CMS-defined billable units for that HCPCS code. If no HCPCS code is reported, quantity must reflect standardized billing units as defined by NCPDP for the NDC-11.
**Unit of Measure**Alpha/Numeric Either HCPCS code or UOM is required. If no specific (non-miscellaneous) HCPCS code is reported, UOM must be reported, and it must be consistent with standardized billing units as defined by NCPDP for the NDC-11.
**Service Provider ID***Numeric – 10 digits The NPI of the healthcare entity where the patient received the medication administration. For example, this could be the NPI of a hospital outpatient surgery center or the NPI of an outpatient infusion center.

_*Indicates a required field_

### **Still have questions?**

If you have questions or need additional help, our team is here for you — please feel free to reach out using any of the contact options below:

* * *

Related Articles

*   [Rebate Model Frequently Asked Questions](https://support.beaconchannelmanagement.com/en/articles/9589827-rebate-model-frequently-asked-questions)
*   [Data Template: Pharmacy Claims Data](https://support.beaconchannelmanagement.com/en/articles/9723402-data-template-pharmacy-claims-data)
*   [Back-end Validations](https://support.beaconchannelmanagement.com/en/articles/13186516-back-end-validations)
*   [Medical Claims Validation Code Glossary](https://support.beaconchannelmanagement.com/en/articles/13186562-medical-claims-validation-code-glossary)
*   [Pharmacy Claims Validation Code Glossary](https://support.beaconchannelmanagement.com/en/articles/13186697-pharmacy-claims-validation-code-glossary)
```

---

## 2. How to read the excerpt

The page renders an HTML table with three columns, headed `Field`, `Data Type`,
`Description`. The proxy flattened each row onto one line, so a row reads:

    **<Field name>**<required asterisk, if any><Data Type> <Description>

The required marker is a literal `*` immediately after the bolded field name.
The page's own footnote defines it:

> `_*Indicates a required field_`

Unlike the pharmacy template, this page has rows **with** and **without** the
asterisk, so the marker is load-bearing here. Three fields lack it:
`HCPCS Code`, `HCPCS Code Modifier`, and `Unit of Measure` — visible in the raw
text as `**HCPCS Code**Alpha/Numeric` (closing `**` runs straight into the data
type) versus `**340B ID***Alpha/Numeric` (an extra `*` before the data type).

Important wording caution: **Beacon never writes the word "optional" on this
page.** It marks fields required, or leaves them unmarked. Calling an unmarked
field "optional" would be our word, not Beacon's — and for `Unit of Measure` it
would be wrong, since its description states a conditional requirement. The
table below therefore reports "not marked required" rather than "optional".

---

## 3. Field table

Field order is exactly as published, top to bottom. Field names are spelled
exactly as Beacon spells them — capitalisation, spaces, and hyphens preserved,
not normalised.

| # | Field | Required (Beacon's marking) | Data Type (verbatim) | Description (verbatim) |
|---|-------|------------------------------|----------------------|------------------------|
| 1 | `340B ID` | `*` — required | `Alpha/Numeric` | The unique identification number provided by HRSA to the 340B covered entity. |
| 2 | `Claim Number` | `*` — required | `Alpha/Numeric` | The unique claim number identifying the claim. |
| 3 | `Claim Line Number` | `*` — required | `Numeric` | Identifies an individual line number on a claim. Line numbers distinguish distinct services that are submitted on the same claim. |
| 4 | `Date of Service` | `*` — required | `Standard date formats` | Date on which the medication was administered to the patient. |
| 5 | `HCPCS Code` | **not marked required** (conditional — see row 12) | `Alpha/Numeric` | The five digit HCPCS code corresponding to the medication administered. For drugs billed using a non-specific or miscellaneous HCPCS code (e.g., A9270, J3490), please leave this field blank. |
| 6 | `HCPCS Code Modifier` | **not marked required** | `Alpha/Numeric` | Modifier to the HCPCS code. Up to four modifier codes may be entered for the same claim line. |
| 7 | `Health Plan Name` | `*` — required | `Alpha/Numeric` | Name of the patient's primary health insurance plan. Examples include Medicare Part B, MediCal, Aetna POS, etc. If the patient is uninsured or a cash payer, mark “CASH” in this field. If no health plan information is recorded, mark “NONE” in this field. |
| 8 | `Health Plan ID` | `*` — required | `Alpha/Numeric` | The identifier code of the patient's primary health insurance plan. If the patient is uninsured or a cash payer, mark “CASH” in this field. If no health plan information is recorded, mark “NONE” in this field. |
| 9 | `NDC-11` | `*` — required | `Numeric – 11 digits` | The NDC-11 of the medication administered to the patient. |
| 10 | `Rendering Physician ID` | `*` — required | `Numeric – 10 digits` | The NPI of the healthcare provider who rendered or supervised the care reported on the claim. |
| 11 | `Quantity` | `*` — required | `Numeric` | The quantity of medication administered to the patient. If a specific (non-miscellaneous) HCPCS code with CMS-defined billing units is reported, quantity must reflect the CMS-defined billable units for that HCPCS code. If no HCPCS code is reported, quantity must reflect standardized billing units as defined by NCPDP for the NDC-11. |
| 12 | `Unit of Measure` | **not marked required**, but description states: "Either HCPCS code or UOM is required." | `Alpha/Numeric` | Either HCPCS code or UOM is required. If no specific (non-miscellaneous) HCPCS code is reported, UOM must be reported, and it must be consistent with standardized billing units as defined by NCPDP for the NDC-11. |
| 13 | `Service Provider ID` | `*` — required | `Numeric – 10 digits` | The NPI of the healthcare entity where the patient received the medication administration. For example, this could be the NPI of a hospital outpatient surgery center or the NPI of an outpatient infusion center. |

**Count: 13 fields — 10 marked required, 3 not marked required.**

Transcription notes, so nothing above looks like a silent edit:

- The data-type separator on this page is an **en dash** (`Numeric – 11 digits`,
  U+2013). The pharmacy page uses a plain **hyphen** (`Numeric - 11 digits`) for
  the same idea. Both are reproduced as published; they are not the same string
  and should not be collapsed when matching programmatically.
- `Unit of Measure` is spelled out in the `Field` column, but its own description
  refers to it by the abbreviation `UOM`. Beacon's inconsistency, preserved.
- `HCPCS Code` and `HCPCS Code Modifier` are two separate fields, not one
  repeating field.
- Curly quotation marks around `“CASH”` and `“NONE”` are Beacon's; straight
  quotes would be our normalisation.

---

## 4. The conditional rule between rows 5, 11 and 12

This is the one piece of real cross-field logic Beacon publishes on this page, so
it is worth stating plainly — every clause below is quoted from the excerpt:

- `HCPCS Code` — "For drugs billed using a non-specific or miscellaneous HCPCS
  code (e.g., A9270, J3490), **please leave this field blank**."
- `Unit of Measure` — "**Either HCPCS code or UOM is required.** If no specific
  (non-miscellaneous) HCPCS code is reported, UOM must be reported, and it must
  be consistent with standardized billing units as defined by NCPDP for the
  NDC-11."
- `Quantity` — "If a specific (non-miscellaneous) HCPCS code with CMS-defined
  billing units is reported, quantity must reflect the **CMS-defined billable
  units** for that HCPCS code. If no HCPCS code is reported, quantity must
  reflect **standardized billing units as defined by NCPDP** for the NDC-11."

So `Quantity` is always required, but the *unit it is expressed in* depends on
whether a specific HCPCS code was reported. That is the trap on this template:
the same number means two different things depending on row 5.

---

## 5. Stated formats, lengths, allowed values

Everything the page states, and nothing more:

| Field | What Beacon states about format/length/values |
|-------|-----------------------------------------------|
| `340B ID` | Alpha/Numeric. No length, no pattern, no example given. |
| `Claim Number` | Alpha/Numeric. Stated to be unique. No length given. |
| `Claim Line Number` | Numeric. No range or length given. |
| `Date of Service` | "Standard date formats". The page does not enumerate which formats. |
| `HCPCS Code` | Alpha/Numeric, "five digit". Named examples of miscellaneous codes to omit: `A9270`, `J3490`. |
| `HCPCS Code Modifier` | Alpha/Numeric. "Up to four modifier codes may be entered for the same claim line." No length; no statement of how the four are delimited. |
| `Health Plan Name` | Alpha/Numeric. Named examples: `Medicare Part B`, `MediCal`, `Aetna POS`. Sentinels: `CASH`, `NONE`. |
| `Health Plan ID` | Alpha/Numeric. Sentinels: `CASH`, `NONE`. |
| `NDC-11` | Numeric, exactly 11 digits. |
| `Rendering Physician ID` | Numeric, exactly 10 digits (an NPI). |
| `Quantity` | Numeric. Unit basis is CMS billable units or NCPDP standardized billing units — see section 4. No precision or decimal rule given. |
| `Unit of Measure` | Alpha/Numeric. Must be "consistent with standardized billing units as defined by NCPDP for the NDC-11". **The allowed UOM values are not listed on this page.** |
| `Service Provider ID` | Numeric, exactly 10 digits (an NPI). |

Concrete literal values Beacon publishes on this page: the sentinels **`CASH`**
and **`NONE`**, the miscellaneous-HCPCS examples **`A9270`** and **`J3490`**, and
the health-plan-name examples **`Medicare Part B`**, **`MediCal`**, **`Aetna POS`**.
Note the plan names are given as *examples*, not as an allowed-value list.

---

## 6. What this page did not tell us

Recorded as gaps rather than filled in. Nothing below was inferred or completed.

1. **The downloadable template file was not obtained.** The page has a heading
   `## Download the medical claims data template here:` followed by nothing in
   the extracted text. The actual attachment — almost certainly an Intercom-hosted
   file — did not survive text extraction. So the byte-exact header row of the
   real template (its column spelling, order, and whether it even matches this
   field list) is **unverified**. The field names above are the names as printed
   in the article's field-list table, which is not the same artefact as the file.
2. **The allowed `Unit of Measure` values are not enumerated.** Beacon defers to
   "standardized billing units as defined by NCPDP" without listing them. We
   cannot state the accepted UOM codes from this page.
3. **How four HCPCS modifiers fit in one field is not stated.** Beacon says up to
   four "may be entered for the same claim line" but does not say whether that
   means four sub-columns, a delimited list, or four repeated rows.
4. **No file format, delimiter, encoding, or header-row prose.** Nothing about
   CSV vs XLSX, comma vs pipe, UTF-8, or quoting is on this page.
5. **No submission mechanism.** Nothing about SFTP, API, portal upload, filename
   convention, or cadence. Beacon documents that elsewhere — in
   `How to Submit Medical Claims to Beacon` (article 9670192) and
   `How to Create a Column Mapping Template for Data Submissions` (article
   9707706). Neither was retrieved for this file.
6. **No example row**, only the scattered example values in section 5.
7. **No validation codes or error behaviour.** Those live in
   `Medical Claims Validation Code Glossary` (article 13186562), not retrieved here.
8. **"Standard date formats" is never defined.** Do not assume ISO-8601.
9. **The "Still have questions?" contact block rendered empty.**
10. **No cardinality or uniqueness rules** — e.g. whether `Claim Number` +
    `Claim Line Number` is the intended natural key — are stated on this page,
    though `Claim Number` is described as "unique".

---

## 7. Provenance

| Step | URL | Status |
|------|-----|--------|
| Proxy fetch (used) | `https://r.jina.ai/https://support.beaconchannelmanagement.com/en/articles/9723390-data-template-medical-claims-data` | **HTTP 200**, 4691 bytes |
| Direct fetch of sibling article (control) | `https://support.beaconchannelmanagement.com/en/articles/9723402-data-template-pharmacy-claims-data` | **HTTP 403** |
| Collection confirming the article | `https://r.jina.ai/https://support.beaconchannelmanagement.com/en/collections/9974649-data-templates` | **HTTP 200** |

Cross-check: the `Data Templates` collection (9974649) lists exactly 2 articles —
this one and the pharmacy one — and its listing text matches this article's title
and subtitle, which corroborates that the proxy returned the right page.

Note on freshness: this article's `lastmod` is `2026-03-17`, roughly three months
more recent than the pharmacy template's `2025-12-10`. The two templates were
last revised at different times and should not be assumed mutually consistent.

Verification performed after writing: every field name in section 3 was checked
back against the verbatim excerpt in section 1. All 13 appear there. No field in
the table is absent from the excerpt.
