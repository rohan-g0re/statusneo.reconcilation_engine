# Evidence: `docs/Assignment_Doc_2.pdf` — StatusNeo, *Connectivity Assessment*, 11 September 2026

**Status of every entry on this page: `SECOND_HAND`.**

Requirement V5. This document is a legitimate source and is indexed like any other, but
it is an assessment *about* the vendors, not published *by* them. Every claim below is
cited by page number and must be read as "StatusNeo states that the vendor states X,"
never as "the vendor states X."

That distinction is load-bearing. Doc 2 itself makes the same point from the other
direction on page 5, warning against equating "API-driven Beacon" with "public customer
API." Where a claim here is contradicted by a first-hand source, the first-hand source
wins and the conflict is recorded.

Retrieved: 2026-09-16 (read from the repository copy — 10 pages).

---

## DOC2-001 — The four production patterns the connector fabric must support

**Page 9**, "Design implication" box. Verbatim:

> Direct-source does not mean API-only
>
> The common connector fabric should explicitly support four production patterns: (1)
> API/SDK for Beacon and any TPA that exposes a supported API, (2) SFTP/structured files
> for Verity/Craneware and vendor exports, (3) healthcare EDI/X12/NCPDP for
> claim/remittance ecosystems, and (4) banking/ERP interfaces for cash and financial
> close. The canonical data and reconciliation layers remain independent of transport.

*Establishes:* the four patterns scored in §1 of the requirements document, and the
principle that the canonical layer stays independent of transport — which is why §4.1 can
assert the engine does not change.

---

## DOC2-002 — The six-step connector build method

**Page 2**, "Common six-step connector build method". Verbatim:

> **1. Access & entitlement:** Partner / customer access, contract/data-right
> confirmation, credentials, sandbox/non-prod and source support owner.
> **2. Interface contract:** Obtain API/SDK or file/EDI specification, sample payloads,
> keys, status/reversal semantics, history and cadence.
> **3. Adapter build:** Implement transport, auth, idempotency, checkpointing, schema
> validation, raw landing and source-normalized model.
> **4. Business mapping:** Crosswalk 340B IDs, NDC/HCPCS, claim/Rx/fill IDs,
> provider/site, payer/PBM, manufacturer, transaction and payment references.
> **5. Golden-claim validation:** Trace representative paid, rejected, reversed and
> unmatched claims end-to-end; reconcile source counts and control totals.
> **6. Production hardening:** Retry/replay, observability, DQ quarantine, secrets
> rotation, runbooks, alerting, lineage, backfill and cutover readiness.

*Establishes:* the scope line. Steps 1–5 are in scope; step 6 is the entire delta to
production-ready and is not built. This is the boundary the requirements document adopts
verbatim rather than inventing its own.

---

## DOC2-003 — "Working connection" versus "production-ready"

**Page 2**. Verbatim:

> **Working connection** — Authorized source data reliably reaches a Shields lower
> environment, parses successfully, maps to the source-normalized contract, and at least
> one golden claim can be traced end-to-end.
>
> **Production-ready** — Adds monitored scheduled operation, replay/idempotency, error
> handling, backfill, performance, security controls, control totals, documented support
> and successful reconciliation of edge cases such as reversals and partial payments.

*Establishes:* both target definitions. Note that both begin with *Authorized*, which is
the word no prototype can satisfy — hence the "connector-ready" amendment in §0 of the
requirements document.

---

## DOC2-004 — The source-of-truth boundary

**Page 2**, "Source-of-truth boundary" table. Verbatim, row by row:

> **TPA** — Authoritative for 340B qualification / source transaction context and the
> TPA's own claim status.
> **Beacon** — Authoritative for rebate submission identifiers, validation outcomes,
> rebate status and Beacon-side reconciliation data.
> **CMS / MTF** — Authoritative for MFP / refund transactions where applicable.
> **Bank** — Authoritative for settled cash.
> **Shields platform** — Authoritative for the consolidated claim-level expected /
> received / outstanding / exception financial status.
> **Inmar** — Coexistence source during transition; not the architectural broker for
> direct TPA or Beacon connections.

*Establishes:* requirement E5. Authority is per-field, and a TPA record must not be able
to set rebate-payment status.

---

## DOC2-005 — The Shields connector fabric components

**Page 2**, "Direct-source connector architecture" diagram. The fabric box is labelled
verbatim:

> SHIELDS CONNECTOR FABRIC
> API / SDK gateway | Secure file & EDI | Auth & secrets | Schema registry | Idempotency
> | Retry / replay | Monitoring

and the flow below it:

> Raw & source-normalized data → Canonical / crosswalk model → Claim Financial Episode →
> Reconciliation ledger → Workflow & reporting

with a cross-cutting bar:

> Cross-cutting controls: tenant isolation | PHI minimization | RBAC / ABAC | encryption
> | audit & lineage | connector observability

*Establishes:* the component list `src/recon/connectors/` implements. Note that
*Retry / replay* and *Monitoring* appear in the fabric but are step-6 items and are
deliberately not built — see the deferred table in §0.

---

## DOC2-006 — Beacon: what is proven publicly

**Page 3**, "What is proven publicly". Verbatim:

> - Beacon explicitly states that covered entities and TPAs should use the Beacon SDK for
>   direct data submissions; the older 340B ESP SDK is not valid for rebate-model
>   submissions.
> - Beacon documents a Partner model in which a covered entity can grant a partner Read
>   or Read/Write access to data through the API and SDKs.
> - Partner onboarding yields an Access Token plus a separate Private Token used to
>   authenticate API requests.
> - Beacon publishes pharmacy and medical claim data templates and supports claim-level
>   submission history, validation outcomes and Beacon IDs.

*Establishes:* requirement A3's two-token model, C1's mock surface, C2's template
mapping, C5's Beacon ID. **This is the sole source for the two-token model** — the
first-hand Beacon pages that would confirm it are `UNAVAILABLE` (see `beacon.md`).

---

## DOC2-007 — Beacon: connector design rows

**Page 3**, "Recommended connector design" table. Verbatim:

> **Direction** — Outbound eligible pharmacy / medical claims; inbound acknowledgements,
> validation outcomes, Beacon IDs, rebate status and reconciliation data.
> **Authentication** — Beacon partner Access Token + Private Token; entity Admin grants
> Read/Write permission.
> **Mapping** — Use Beacon's published pharmacy / medical data templates; persist Beacon
> ID against Shields Claim Financial Episode.
> **Processing** — Submit final 340B-qualified claims; treat validation failures as
> source-data exceptions, not financial postings.
> **Payment close** — Reconcile Beacon rebate/payment reference to bank settlement before
> marking cash received / claim closed.

*Establishes:* C2 and C3 in full. Beacon is the only bidirectional connector in the
document — every other source is a pull. "Treat validation failures as source-data
exceptions, not financial postings" is the specific instruction behind C3's acceptance
test.

---

## DOC2-008 — Beacon: what still requires vendor confirmation

**Page 3**. Verbatim:

> - SDK package, API reference, rate limits, versioning policy, non-production endpoint
>   and support SLA are not exposed in full public documentation and must be obtained
>   through Beacon Support.
> - Confirm whether all required rebate/payment/status retrieval operations are available
>   programmatically for the partner role or whether any require export/portal workflows.
> - Each covered entity must be registered and explicitly authorize StatusNeo/Shields
>   partner access; permissions may need to be repeated across applicable 340B IDs.

*Establishes:* why no vendor credential is obtainable, which is the premise of the whole
"connector-ready" scope amendment. Also establishes that permissions are granted **per
340B ID**, which is requirement E1's justification.

---

## DOC2-009 — Verity: what is proven publicly

**Page 4**. Verbatim:

> - Verity publicly documents Secure Data Exports that automatically send encrypted data
>   through Verity SFTP with daily, weekly or monthly delivery options.
> - Published export datasets include accumulations, contract-pharmacy claims backing,
>   invoices, matches/split transactions, claims backing and unmatched claims.
> - Verity describes its platform as integrated with major wholesale vendor order-entry
>   and covered-entity information systems.

*Establishes:* D2's dataset list and the cadence. **Partially corroborated first-hand** —
see `VERITY-001` and `VERITY-002` in `verity.md`, which confirm Secure Data Transfer and
encryption from Verity's own site but do **not** confirm the five dataset names.

---

## DOC2-010 — Verity: connector design rows

**Page 4**. Verbatim:

> **Direction** — Primarily inbound to Shields: qualified claim / dispense context,
> accumulations, matching results, invoices and unmatched transactions.
> **Transport** — Verity SFTP secure exports: use direct file landing, checksum,
> file-level idempotency and source-control totals.
> **Mapping** — Map 340B ID, site / contract pharmacy, Rx/claim identifiers, NDC,
> qualification/match status, invoice and source transaction keys.
> **Cadence** — Recommend daily export for financial operations; retain vendor file name,
> generated timestamp and export type for audit lineage.
> **Beacon relationship** — Use Verity as qualification/source context; Beacon
> submission/status can remain a separate Shields direct connector.

*Establishes:* D1's transport requirements and F2's control totals. Note "checksum,
file-level idempotency and source-control totals" appear under **Transport (step 3)**,
not under hardening — which is why control totals are in scope.

---

## DOC2-011 — Verity: no public API

**Page 4**. Verbatim:

> - No public developer documentation was found for a general customer REST API; do not
>   assume API access in the estimate.
> - Confirm which SFTP export products are licensed for each Shields-associated covered
>   entity and whether fields include all matching keys required for the reconciliation
>   model.
> - Confirm incremental-vs-full-file behavior, historical backfill availability, file
>   versioning and how reversals/requalifications are represented.

*Establishes:* Verity is SFTP-only for our purposes, and that **reversal representation
is explicitly unknown** — which is requirement B3, and why B3 demands the semantics be
*declared* per vendor rather than guessed.

---

## DOC2-012 — Craneware: what is proven publicly

**Page 6**. Verbatim:

> - Craneware publicly documents scheduled SFTP delivery of Claims, Ordering Problems,
>   Unreplenished Costs, Audit, Bulk Dispensations and other reports for automatic
>   integration with hospital systems.
> - Craneware documents Trisus rebate functionality integrated with Sentinel and Sentrex
>   data for automated rebate submission and reconciliation.
> - The platform family already processes 340B, claim, compliance and medication-financial
>   data at enterprise health systems.

*Establishes:* D3's report list. **Corroborated first-hand** — see `CRANEWARE-001` in
`craneware.md`, which is Craneware's own 2023 announcement naming the same five reports
and the SFTP folder. This is the one vendor claim in Doc 2 that a primary source confirms
almost word for word.

---

## DOC2-013 — Craneware: transport and the shared framework

**Page 6**. Verbatim:

> **Transport** — Scheduled SFTP is the proven direct path; use file-level controls, file
> schema versioning and daily ingestion.
>
> Recommendation: Use Craneware as the second file-based pattern after Verity; both can
> share the same secure-file connector framework.

*Establishes:* requirement D1 — one connector serving both vendors, with the difference
confined to the field mapping. This is Doc 2's own recommendation, not our invention.

---

## DOC2-014 — PharmaForce: submission ownership, mode A and mode B

**Page 5**. Verbatim:

> **Beacon mode A** — Preferred target: PharmaForce provides qualification; Shields
> submits to Beacon and independently consumes Beacon status.
> **Beacon mode B** — Coexistence: PharmaForce submits to Beacon; Shields consumes TPA +
> Beacon outcomes for independent reconciliation.

and from the same page's confirmation list:

> - Confirm submission ownership to prevent duplicate Beacon submissions: Shields
>   direct-to-Beacon vs. PharmaForce-managed submission must be explicitly decided per
>   covered entity.

*Establishes:* requirement C4 in full, including *why* it matters — duplicate submission.
**Corroborated first-hand for a different vendor:** `VERITY-004` shows Verity offering
exactly this A/B choice in its own words, which means the pattern is real and not
specific to PharmaForce.

---

## DOC2-015 — The regulatory clock

**Page 1**, "Current regulatory context" box. Verbatim:

> HRSA announced a revised 340B rebate-model pilot on 31 July 2026, with selected
> manufacturer plans expected to become effective 1 January 2027. This increases the
> value of front-loading Beacon / TPA readiness and avoiding a late discovery that access
> or data contracts are not available.

*Establishes:* why Beacon is the priority connector. See `VERITY-005` for the first-hand
record of the January 2026 pause that preceded this revision — the two together give the
full sequence: pilot paused 12 January 2026, revised 31 July 2026, effective 1 January 2027.

---

## DOC2-016 — The Week 3 access gate

**Page 10**, "Week 3 gate: required evidence before committing the connector baseline".
Verbatim:

> **Beacon** — Partner tokens / Read-Write permission, SDK/API pack, non-prod access and
> successful test submission/retrieval.
> **Verity / Craneware** — SFTP credentials, scheduled export definition and
> representative files with required claim-level matching keys.
> **PharmaForce / Macro / Pillr** — Private interface/export specification, customer
> entitlement, sample data and named vendor technical owner.
> **All vendors / security** — History/backfill, corrections/reversals, volumes, limits,
> support/versioning plus BAA/data-use permissions, covered-entity authorization and
> minimum-necessary PHI controls.

*Establishes:* the readiness report in F3 is our version of this table, and that every
row on it is vendor-blocked for a prototype. Doc 2's own Go/Amber/Red rule on the same
page reads: *"RED — No supported machine-to-machine path without vendor custom work."*

---

## DOC2-017 — The perimeter table's confidence ratings

**Page 1**, "Assessment perimeter" table. The rows that decide which vendors we build:

| Platform | Public interface evidence | Direct API to Shields? | Confidence |
|---|---|---|---|
| Beacon | API + SDK + file submission; partner read/write tokens | Yes, documented after partner onboarding | High |
| Verity 340B | Scheduled encrypted SFTP data exports | Not publicly documented | High |
| PharmaForce | API-driven Beacon submission; bidirectional Beacon reconciliation; Multi-EDI | Customer-facing API not publicly documented | Med-High |
| Craneware | Scheduled SFTP reports; Trisus rebate integration | External customer API not publicly documented | High for SFTP |
| Macro Helix | Qualified-claim reporting; certified 850/855 EDI interface | Generic customer API not publicly documented | Medium |
| Pillr / RxStrategies | Beacon supporting-entity integration; multi-source connectivity | External customer API not publicly documented | Medium |

*Establishes:* the scope decision in §0 — Beacon, Verity and Craneware are built;
PharmaForce, Macro Helix and Pillr are not, because "no public specification for
transport *or* payload" is exactly what the three Medium rows say.
