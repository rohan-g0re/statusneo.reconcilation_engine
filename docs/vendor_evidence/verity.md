# Evidence: Verity Solutions / Verity 340B

Retrieved: 2026-09-16.

**Summary: the transport is confirmed first-hand, the field dictionary does not exist.**

Unlike the Beacon support site, `verity-solutions.com` is retrievable, and it yielded
verbatim text on Secure Data Transfer, encryption, split billing, and — unexpectedly — a
direct first-hand confirmation of the Beacon submission-ownership choice that Doc 2 only
attributes to PharmaForce. It also gave us the January 2026 pilot pause, which Doc 2 does
not mention.

What it did **not** give is any field-level detail. No dataset schema, no column list, no
file layout, no sample file. That is not for lack of searching; Verity publishes marketing
and product prose, not integration documentation.

**Consequence for the build: every Verity payload field is `INVENTED`.** The transport is
real, the cadence is real, the dataset names come from `DOC2-009`, and the fields inside
each dataset are our honest construction, declared field by field in the mock's
`MOCK_FIELDS.md`. Requirement D2 anticipated exactly this and says so: *"transport is real
and payload is an honest invention."*

---

## VERITY-001 — Secure Data Transfer exists and is encrypted · RETRIEVED

- **URL:** https://verity-solutions.com/solutions/
- **Verbatim excerpt:**

  > For all healthcare systems and pharmacy chains, that apply custom analysis to their
  > data, Verity Secure Data Transfer automatically sends relevant, encrypted

  (The sentence is truncated in the page's own rendering at the point shown. Quoted as
  retrieved; not completed from inference.)

- **Establishes:** Verity operates an automated, encrypted outbound data transfer product.
  This is the first-hand basis for requirement D1's transport. Note it confirms
  *automated* and *encrypted*; it does **not** say SFTP — that word comes from `DOC2-009`.

## VERITY-002 — Split Billing product · RETRIEVED

- **URL:** https://verity-solutions.com/solutions/
- **Verbatim excerpt:**

  > Verity 340B® Split Billing manages qualified pharmaceutical usage and replenishment in
  > 340B eligible healthcare organizations, optimizing returns on qualified

- **Establishes:** the split-billing product named in `DOC2-009`'s "matches/split
  transactions" dataset is a real Verity product. Supports the dataset name; does not
  describe its export format.

## VERITY-003 — Automatic Beacon submission within 45 days · RETRIEVED

- **URL:** https://verity-solutions.com/the-340b-rebate-model-pilot-is-approved/
- **Verbatim excerpt:**

  > V340B can automatically submit the claim to Beacon on behalf of the CE for rebate
  > claims submitted within 45 days

- **Establishes:** two things, and both matter.
  1. The 45-day submission window, first-hand from a vendor that integrates with Beacon
     commercially. This is the independent corroboration recorded at `BEACON-009`.
  2. That a TPA submitting to Beacon **on behalf of the covered entity** is real,
     shipping behaviour — not a hypothetical. See `VERITY-004`.

## VERITY-004 — The submission-ownership choice, in Verity's own words · RETRIEVED

- **URL:** https://verity-solutions.com/the-340b-rebate-model-pilot-is-approved/
- **Verbatim excerpt:**

  > Alternatively, CEs can choose to export a Beacon report to manually submit to Beacon.

- **Establishes:** **requirement C4, first-hand.** Doc 2 defines mode A / mode B only on
  its PharmaForce page (`DOC2-014`), which left open whether the pattern was specific to
  that vendor. It is not. Verity offers the covered entity the same binary choice: the TPA
  submits on the entity's behalf, or the entity exports and submits itself.

  This is the strongest single piece of evidence on any of these pages, because it
  converts C4 from "a distinction one assessment document drew" into "a choice a shipping
  TPA actually presents to its customers." C4's acceptance test — that under mode B the
  outbound path is *unreachable*, not merely skipped — is guarding against a real
  double-submission, not a theoretical one.

## VERITY-005 — Beacon integration is under active development · RETRIEVED

- **URL:** https://verity-solutions.com/the-340b-rebate-model-pilot-is-approved/
- **Verbatim excerpt:**

  > Verity has been actively collaborating with Beacon to develop a fully integrated
  > submission solution

- **Establishes:** the Verity→Beacon path is a live integration, which supports treating
  Verity as qualification/source context and Beacon as a separate direct connector —
  exactly the arrangement `DOC2-010`'s "Beacon relationship" row recommends.

## VERITY-006 — Rebate-model fields added to the Split Transaction specification · RETRIEVED

- **URL:** https://verity-solutions.com/the-340b-rebate-model-pilot-is-approved/
- **Verbatim excerpts:**

  > All of these data fields have been added to our Split Transaction data specifications.

  > The inclusion of all new required data fields for submitting medical claims under the
  > 340B Rebate Model Pilot Program.

- **Establishes:** Verity maintains a **"Split Transaction data specification"** — a named
  artefact with a field list, extended for the rebate model. It is referenced but **not
  published**; no version of it was retrievable.
- **Therefore:** the existence of a field specification is known; its **contents are
  UNKNOWN**. This is the precise boundary requirement D2 describes. The dataset name
  "matches/split transactions" in `DOC2-009` now has a first-hand counterpart in Verity's
  own vocabulary, which is why the mock names that dataset `split_transactions`.

## VERITY-007 — The pilot was paused in January 2026 · RETRIEVED

- **URL:** https://verity-solutions.com/the-340b-rebate-model-pilot-is-approved/
- **Verbatim excerpt:**

  > As of January 12, 2026, the 340B Rebate Model Pilot is paused.

- **Establishes:** a fact `docs/Assignment_Doc_2.pdf` does not record. Combined with
  `DOC2-015` the full sequence is: **paused 12 January 2026 → revised pilot announced 31
  July 2026 → selected manufacturer plans effective 1 January 2027.**
- **Why it is worth recording:** it is a case where a first-hand source is *more current*
  than the assessment document, and it explains why Doc 2 calls the July announcement a
  *revised* pilot. It does not change the build, but it is exactly the kind of fact that,
  left unrecorded, later gets reconstructed wrongly from memory.

---

## VERITY-008 — SFTP, API and Snowflake connectivity · SECOND_HAND

- **Claim:** Verity's data connectivity supports SFTP, API, or Snowflake integrations.
- **Source:** a search-index summary, not a Verity page. Attempts to locate a primary
  Verity page stating this did not succeed.
- **Status caveat:** **not corroborated first-hand**, and it is in partial tension with
  `DOC2-011`, which says plainly: *"No public developer documentation was found for a
  general customer REST API; do not assume API access in the estimate."*
- **Resolution adopted:** follow Doc 2. Verity is built as **SFTP-only**. If an API exists
  it is not publicly specified, so building against it would be inventing both transport
  and payload — the exact thing §0 of the requirements document refuses to do for
  PharmaForce, Macro Helix and Pillr. Recorded here so the tension is visible rather than
  quietly resolved.

---

## What remains genuinely UNKNOWN about Verity

Answering the question the build depends on, directly: **is there any published
field-level detail? No. Every payload field we write is an honest invention.**

1. **The field list of every export dataset.** Accumulations, claims backing, invoices,
   split transactions, unmatched claims — names only, from `DOC2-009`. No columns.
2. **The "Split Transaction data specification" contents** (`VERITY-006`). Known to exist,
   not published.
3. **File format.** CSV, fixed-width, JSON, XML — unknown. Ours is `INVENTED`.
4. **File naming convention and the generated-timestamp format.** `DOC2-010` says to
   *retain* vendor file name and generated timestamp for audit lineage, which implies both
   exist, but neither shape is published.
5. **Incremental versus full-file behaviour.** `DOC2-011` raises it as an open question.
   Our mock emits full files; the checkpoint in A4 is what makes that safe.
6. **How reversals and requalifications are represented.** `DOC2-011` names this
   explicitly as unconfirmed. This is requirement B3, and it is the reason B3 demands the
   representation be *declared* per vendor rather than assumed — for Verity we declare it
   and tag the declaration `INVENTED`, because nobody published the answer.
7. **Whether a control total or trailer record accompanies an export.** `DOC2-010` says to
   use "source-control totals," implying they are available; the format is unknown.
   Requirement F2's trailer is `INVENTED`.
8. **Accumulation id and invoice number formats.** Minted by our orchestrator per
   requirement M1, tagged `INVENTED`.

The honest position, stated the way the readiness report in F3 will state it: **Verity's
transport is real and the connector is built to it; Verity's payload is ours.** A reviewer
can see exactly which is which by reading the `MOCK_FIELDS.md` beside the mapping, and
that is the whole purpose of requiring one.
