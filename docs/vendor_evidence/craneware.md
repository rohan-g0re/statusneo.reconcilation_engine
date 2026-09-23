# Evidence: The Craneware Group (Sentinel / Sentrex / Trisus)

Retrieved: 2026-09-16.

**Summary: this is the one vendor claim in Doc 2 that a primary source confirms almost
word for word.**

`DOC2-012` asserts that "Craneware publicly documents scheduled SFTP delivery of Claims,
Ordering Problems, Unreplenished Costs, Audit, Bulk Dispensations and other reports for
automatic integration with hospital systems." That is a strong, specific, checkable claim,
and checking it was the point of this file rather than accepting it.

It checks out. Craneware's own 2023 product announcement names the same five reports, in
the same order, delivered to the same place — an SFTP folder — for the same reason,
automatic integration with native systems. Doc 2 did not paraphrase loosely; it tracked
the source.

As with Verity, the transport and the report names are confirmed and **no field-level
detail is published**. Every Craneware payload field is `INVENTED`.

---

## CRANEWARE-001 — Scheduled SFTP report delivery, with the five report names · RETRIEVED

- **URL:** https://www.thecranewaregroup.com/news-events/news-insights/2023/sentinel-sentrex-340b-applications-get-customer-friendly-upgrades/
- **Title:** "Sentinel, Sentrex 340B applications get customer-friendly upgrades | The Craneware Group"
- **Published:** 14 June 2023
- **Verbatim excerpt:**

  > Users now have the option of having Claims Report, Ordering Problems, Unreplenished
  > Costs, Audit, and Bulk Dispensations Report and other commonly requested reports
  > delivered straight to their Secure File Transfer Protocol (SFTP) folder

- **Establishes:** requirement D3 in full, first-hand:
  - **SFTP** is the delivery mechanism, spelled out in the source rather than abbreviated.
  - The five report names, verbatim: **Claims Report, Ordering Problems, Unreplenished
    Costs, Audit, Bulk Dispensations Report**.
  - "and other commonly requested reports" — the list is **not exhaustive**, which is
    itself worth recording. Our coverage report (M5) asserts these five are non-empty; it
    does not claim they are all Craneware emits.
  - The products are Sentinel and Sentrex.

- **Note on two small differences from `DOC2-012`:** the source says "Claims Report" and
  "Bulk Dispensations Report" where Doc 2 writes "Claims" and "Bulk Dispensations." The
  mock uses Craneware's own wording. A trivial difference, recorded because the point of
  this file is that wording is checked rather than smoothed.

## CRANEWARE-002 — Unreplenished cost report semantics, Trisus · SECOND_HAND

- **Claim:** the Trisus Platform includes an "Unreplenished cost report" that summarises
  total cost to replenish drugs by NDC and allows filtering by various replenishment
  statuses.
- **Source:** a search-index summary referencing Craneware's Trisus material. The primary
  page was not retrieved verbatim.
- **Establishes, tentatively:** that the Unreplenished Costs report is keyed by **NDC**
  and carries a **replenishment status**. Both are plausible and both match the domain, but
  this is a summary, not a source.
- **Use:** the two fields it suggests (`ndc11`, a replenishment status) are used in the
  mock and tagged `INVENTED` rather than `SPEC`, because the tier requires a cited vendor
  source and a search summary is not one. Recorded so the reasoning behind those two
  invented fields is visible rather than arbitrary.

## CRANEWARE-003 — Trisus rebate integration with Sentinel and Sentrex · SECOND_HAND

- **Source:** `DOC2-012`, page 6.
- **Claim:** Craneware documents Trisus rebate functionality integrated with Sentinel and
  Sentrex data for automated rebate submission and reconciliation.
- **Use:** confirms Craneware occupies the same structural position as Verity — a TPA that
  can itself submit to Beacon. Relevant to requirement C4's per-covered-entity submission
  ownership, which therefore applies to Craneware too, not only to Verity and PharmaForce.
- **Not independently retrieved.** The Trisus rebate documentation was not obtained.

## CRANEWARE-004 — Scheduled SFTP is the recommended transport, shared with Verity · SECOND_HAND

- **Source:** `DOC2-013`, page 6. Verbatim from that document:

  > **Transport** — Scheduled SFTP is the proven direct path; use file-level controls, file
  > schema versioning and daily ingestion.
  >
  > Recommendation: Use Craneware as the second file-based pattern after Verity; both can
  > share the same secure-file connector framework.

- **Establishes:** requirement D1 — one connector serving both vendors, difference confined
  to the field mapping. This is Doc 2's own recommendation, and `CRANEWARE-001` confirms
  the SFTP half of it first-hand.

## CRANEWARE-005 — No public external customer API · SECOND_HAND

- **Source:** `DOC2-012`, page 6. Verbatim:

  > No public external customer API specification was found for Sentinel/Sentrex/Trisus
  > 340B data; commit to the SFTP baseline unless a private API is supplied.

- **Independent check:** searching Craneware's own properties for an external API
  specification also found none. The absence is corroborated, insofar as an absence can be.
- **Use:** Craneware is built SFTP-only, matching Verity.

---

## What remains genuinely UNKNOWN about Craneware

Answering the two questions directly:

**Can the five report names be sourced to Craneware itself?** **Yes.** `CRANEWARE-001` is
Craneware's own announcement, retrieved verbatim, naming all five and the SFTP folder.
This is the strongest first-hand vendor evidence in this entire directory.

**Is there any field-level detail anywhere?** **No.** Every Craneware payload field is an
honest invention.

The specific gaps:

1. **The column list of all five reports.** Only `CRANEWARE-002` gestures at two fields for
   one report, and it is a search summary.
2. **File format.** CSV, fixed-width, Excel — unknown.
3. **File naming convention and scheduling granularity.** "Scheduled" is confirmed; the
   schedule's shape is not. `DOC2-013` recommends daily ingestion; that is a
   recommendation to us, not a statement of Craneware's cadence.
4. **How reversals are represented.** Same gap as Verity, same consequence: requirement B3
   makes us *declare* it, and the declaration is tagged `INVENTED`.
5. **Whether a trailer or control total accompanies a report.** `DOC2-013` says to use
   "file-level controls" and "file schema versioning," which implies support exists.
   Format unknown.
6. **Which of Sentinel, Sentrex and Trisus a given health system runs**, and therefore
   which reports are even available. `DOC2-012`'s own confirmation list raises this: *"the
   available data products differ between Sentinel, Sentrex and Trisus."* Our source
   registry models Craneware as one source; a real deployment would need one per product.
   Recorded as a known simplification.
7. **Whether the five named reports are the full set.** `CRANEWARE-001` says "and other
   commonly requested reports," so explicitly not.

Same honest position as Verity, and the readiness report in F3 will state it the same way:
**the transport and the report names are Craneware's; the fields inside them are ours.**
