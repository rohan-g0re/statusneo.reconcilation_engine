# Vendor evidence index

**This directory is an input to the build, not documentation about it.**

Requirement group V of [`../connectivity_layer_requirements.md`](../connectivity_layer_requirements.md).
Nothing in groups A through F may be written for a vendor until that vendor has an
evidence file here. The reason is stated in the requirements document and is worth
repeating, because it is the failure mode this whole build is exposed to:

> Beacon and Verity are real companies with real published behaviour, and the moment a
> field name, an auth flow, a status code or a cadence is written from memory or from
> plausible-sounding inference, the prototype stops being a connector built to a vendor
> contract and becomes a connector built to our imagination — while looking identical in
> every demo. A mock that is wrong is worse than no mock, because it is confidently wrong.

## Files

| File | What it holds |
|---|---|
| `index.jsonl` | **Machine-readable.** One row per evidence id. This is what the test walks. |
| `assignment_doc2.md` | V5 — claims lifted from `docs/Assignment_Doc_2.pdf`, cited by page, marked second-hand |
| `beacon.md` | V1/V2 — Kalderos / Second Sight Beacon |
| `verity.md` | V1 — Verity Solutions / Verity 340B |
| `craneware.md` | V1 — The Craneware Group (Sentinel / Sentrex / Trisus) |

`index.jsonl` is machine-readable for the same reason `knowledge_graph.jsonl` is: a test
has to walk it. Unlike the knowledge graph, **this one is hand-written and never
generated**, so there is no builder that can silently destroy an edit.

## The three statuses

Every row in `index.jsonl` carries exactly one:

| Status | Meaning |
|---|---|
| `RETRIEVED` | The text was actually fetched. The excerpt is verbatim and was not rewritten. |
| `SECOND_HAND` | A source we can retrieve quotes or characterises the vendor. Usable, but the vendor did not say it to us directly. `docs/Assignment_Doc_2.pdf` is the main one. |
| `UNAVAILABLE` | The source exists and could not be retrieved. Records the URL, the exact failure and the date. **The fact it would have established is treated as UNKNOWN.** |

`UNAVAILABLE` is not a failure of the research. It is the honest result, and it is what
V2 exists to make possible. A recollection of a vendor's API is not evidence.

## The three provenance tiers

Separate from status. Status describes *a source*; a tier describes *a field we wrote*
(requirement V3). Tiers live in the `MOCK_FIELDS.md` beside each mapping module:

| Tier | Meaning |
|---|---|
| `SPEC` | Taken from a cited vendor source. Must name an evidence id present in `index.jsonl`. |
| `STANDARD` | Taken from a public standard (X12, NCPDP, ISO 20022, FHIR), cited to that standard. |
| `INVENTED` | No public source exists. Our own construction, with the reasoning recorded. |

A field with no tier fails the build. A `SPEC` field naming an absent evidence id fails
the build. Both are enforced by [`../../tests/test_vendor_evidence.py`](../../tests/test_vendor_evidence.py),
not by discipline.

## What the evidence actually turned out to be

Worth stating plainly, because it is the opposite of what the requirements document
assumed when it was written.

**The richest usable source is `Assignment_Doc_2.pdf` itself (V5), not the vendors.**
The Beacon Support Center is real, is public, and hosts exactly the articles this build
wants — the pharmacy and medical claim data templates, the validation code glossary, the
back-end validations page. **Every one of them returns HTTP 403 to automated retrieval.**
The site is Intercom-hosted and blocks non-browser clients. That was tested against four
separate URLs, not assumed.

So Beacon's evidence file is mostly `UNAVAILABLE` rows naming real articles, plus
`SECOND_HAND` rows from Doc 2. Verity and Craneware did better: both publish retrievable
prose that confirms the transport and the dataset names, though neither publishes a field
dictionary. **Every Verity and Craneware payload field is therefore `INVENTED`, and is
tagged as such.** That is the honest position and it is defensible — Doc 2 says the same
thing in its own words, that the interface packs must come through vendor onboarding.
