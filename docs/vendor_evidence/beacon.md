# Evidence: Beacon (Kalderos / Second Sight Solutions)

Retrieved: 2026-09-16.

**Summary of what this page establishes: almost nothing first-hand, and that is the
finding.**

The Beacon Support Center at `support.beaconchannelmanagement.com` is real, is public,
and hosts exactly the articles this build needs — the pharmacy claim data template, the
medical claim data template, the validation code glossary, the back-end validations page.
**Every one of them returns HTTP 403 to automated retrieval.** The site is Intercom-hosted
and blocks non-browser clients. This was tested against four separate URLs including the
collection index, so it is systematic and not an outage.

Requirement V2 governs exactly this case: the URL, the failure and the date are recorded,
and the fact the page would have established is treated as **UNKNOWN**. It is not
reconstructed from recollection. A model's memory of Beacon's field list is not evidence,
and writing one from memory would produce a mock that is confidently wrong — the specific
failure mode requirement group V exists to prevent.

**Consequence for the build.** Beacon's `SPEC` tier is carried almost entirely by
`assignment_doc2.md` (`SECOND_HAND`, page-cited), with the 45-day window independently
corroborated by Verity's own site. Every Beacon payload field beyond what Doc 2 names is
`INVENTED` and tagged as such in the mock's `MOCK_FIELDS.md`.

---

## BEACON-001 — Pharmacy claims data template · UNAVAILABLE

- **URL:** https://support.beaconchannelmanagement.com/en/articles/9723402-data-template-pharmacy-claims-data
- **Title (confirmed via search index):** "Data Template: Pharmacy Claims Data | Beacon Support Center"
- **Failure:** `HTTP 403 Forbidden`. Response body not retrieved.
- **Would have established:** the complete field list of Beacon's pharmacy claim
  submission template, with required/optional markers and formats. This is the single
  most valuable missing artefact for requirement C2.
- **Therefore:** the pharmacy template field list is **UNKNOWN**. Fields in
  `src/recon/mocks/` beyond those named in `DOC2-007` are tagged `INVENTED`.

## BEACON-002 — Medical claims data template · UNAVAILABLE

- **URL:** https://support.beaconchannelmanagement.com/en/articles/9723390-data-template-medical-claims-data
- **Title (confirmed via search index):** "Data Template: Medical Claims Data | Beacon Support Center"
- **Failure:** `HTTP 403 Forbidden`.
- **Would have established:** the medical-benefit claim submission field list.
- **Therefore:** **UNKNOWN**. See `BEACON-008` for a second-hand partial.

## BEACON-003 — Pharmacy claims validation code glossary · UNAVAILABLE

- **URL:** https://support.beaconchannelmanagement.com/en/articles/13186697-pharmacy-claims-validation-code-glossary
- **Title (confirmed via search index):** "Pharmacy Claims Validation Code Glossary | Beacon Support Center"
- **Failure:** `HTTP 403 Forbidden`.
- **Would have established:** the rejection/validation code vocabulary. Requirement C3
  says a rejected submission must preserve "the vendor's reason **verbatim, not
  paraphrased**" — without this page we do not know the real vocabulary, so the mock
  replays reasons already present in our generated `TPA_MANUFACTURER_DECISION` records
  rather than inventing Beacon-specific codes.
- **Therefore:** the validation code vocabulary is **UNKNOWN**.

## BEACON-004 — Back-end validations · UNAVAILABLE

- **URL:** https://support.beaconchannelmanagement.com/en/articles/13186516-back-end-validations
- **Title (confirmed via search index):** "Back-end Validations | Beacon Support Center"
- **Failure:** `HTTP 403 Forbidden`.
- **Would have established:** what Beacon checks after ingestion — data consistency,
  program eligibility, compliance with HRSA guidance and manufacturer policies.
- **Therefore:** **UNKNOWN**. The mock does not attempt to reproduce back-end validation
  logic; it replays outcomes already decided by the generator (requirement M3).

## BEACON-005 — How to submit medical claims to Beacon · UNAVAILABLE

- **URL:** https://support.beaconchannelmanagement.com/en/articles/9670192-how-to-submit-medical-claims-to-beacon
- **Failure:** `HTTP 403 Forbidden`.
- **Would have established:** the submission workflow and endpoint semantics.

## BEACON-006 — Column mapping template guide · UNAVAILABLE

- **URL:** https://support.beaconchannelmanagement.com/en/articles/9707706-how-to-create-a-column-mapping-template-for-data-submissions
- **Failure:** `HTTP 403 Forbidden`.
- **Would have established:** that Beacon accepts a customer-defined column mapping,
  which is relevant to how rigid the template actually is.

## BEACON-007 — Beacon Platform Support collection index · UNAVAILABLE

- **URL:** https://support.beaconchannelmanagement.com/en/collections/11972931-beacon-platform-support
- **Failure:** `HTTP 403 Forbidden`. Fetched directly to enumerate the full article list;
  refused.
- **Would have established:** the complete set of published articles, so that this file
  could be exhaustive rather than limited to articles a search index happened to surface.
- **Therefore:** this evidence file is **known to be incomplete**, and cannot be made
  complete without browser-based access. Stated explicitly so no reader mistakes its
  silence for absence.

---

## BEACON-008 — Medical claim matching key · SECOND_HAND

- **Source:** search-index summary of `BEACON-002`, not the page itself.
- **Claim:** for medical claim submissions the identifying combination of fields includes
  the claim number, claim line number, date of service, NDC, service provider ID and
  HCPCS modifier code.
- **Status caveat:** this is a search engine's extraction of a page we could not
  retrieve. It is **not** a verbatim excerpt and must not be treated as one. Recorded
  because it is the only signal we have about the medical template's key structure, and
  because it is independently plausible — it matches the crosswalk list in `DOC2-002`
  step 4, which names "NDC/HCPCS" and "claim/Rx/fill IDs."
- **Use:** motivates requirement E2 (HCPCS alongside NDC). Any field taken from this entry
  is tagged `INVENTED`, not `SPEC`, because the tier requires a cited vendor source and
  this is not one.

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
- **Status caveat:** **single-sourced.** The first-hand Beacon pages that would confirm
  this are `BEACON-001` through `BEACON-007`, all `UNAVAILABLE`. No independent
  corroboration was found.
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

1. **The pharmacy claim template field list.** Not one field name is known first-hand.
2. **The medical claim template field list.** Only the partial key combination in
   `BEACON-008`, and that is a search summary, not a source.
3. **The validation / rejection code vocabulary.** No code, no meaning, no format.
4. **Beacon ID format.** Length, character set, prefix — all unknown. Ours is invented.
5. **Endpoint paths, HTTP verbs, request and response envelopes.** `DOC2-008` says these
   are not public. Ours are invented and declared.
6. **Rate limits, versioning policy, non-production endpoint.** Explicitly named by
   `DOC2-008` as vendor-onboarding items.
7. **Whether every rebate/payment/status retrieval operation is even available
   programmatically**, or whether some require portal workflows. `DOC2-008` raises this as
   an open question and we cannot close it.
8. **The exact token exchange.** That two tokens exist is `BEACON-011`; how they are
   presented on a request — header names, scheme, ordering — is unknown.

Items 1, 3, 4 and 5 are the ones that make the Beacon mock a mock. Everything the
connector does *around* them — two-token auth, idempotency, checkpointing, persisting the
returned id as a crosswalk key, reconciling the payment reference to the bank leg — is
real machinery built to `DOC2-007`'s described behaviour, and switching to a live endpoint
is a configuration change. That is the honest claim, and it is the one the readiness
report in F3 will make per source.
