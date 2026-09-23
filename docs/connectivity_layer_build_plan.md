# Connectivity Layer — BUILD PLAN

**Requirements of record:** [`connectivity_layer_requirements.md`](connectivity_layer_requirements.md)
**Source document:** `docs/Assignment_Doc_2.pdf` (StatusNeo, *Connectivity Assessment*, 11 September 2026)
**Branch:** `connectivity_layer` · **Scope:** connector-ready (Doc 2 steps 1–5). Step 6 is out.

This is the execution plan for every section of the requirements document. One wave per
section group, each wave broken into phases. Every phase declares five things, in this
order, because that is the order they are needed in:

1. **Sources** — the documentation the code is written *from*. No phase writes a vendor
   fact from memory.
2. **Deliverables** — the files that will exist afterwards.
3. **Functionality criteria** — what must be true for the phase to count as done.
4. **Test cases** — the specific assertions, named.
5. **Desired behaviour** — what the code does when it is working, stated so a reviewer can
   check it without reading the implementation.

**Status legend:** ✅ complete and committed · 🔄 in progress · ⬜ not started

---

## 0. Standing constraints — these bind every wave

| # | Constraint | Where it comes from | How it is enforced |
|---|---|---|---|
| C1 | No vendor fact written from memory. Every claim is `SPEC` (cited), `STANDARD` (public standard) or `INVENTED` (declared). | Group V | `tests/test_vendor_evidence.py` |
| C2 | The pre-existing 585 tests pass **unedited**. | §5.8, §4.12 | Full-suite run + `git diff --stat` on pre-existing test files |
| C3 | Nothing may read `truth/ground_truth.json`. | §4.9 | AST scan per package; transport refusal tests |
| C4 | No arithmetic on money outside the pricing module. | M2 | AST name ban + empirical amount-provenance test |
| C5 | Determinism: same seed → same bytes. Wall-clock only in checkpoints and logs. | §4.11 | Byte-identity tests; diagram/mocks re-render tests |
| C6 | The six generated feeds stay byte-identical. | Derived from C2 | Regenerate from previous commit, diff every hash |
| C7 | Base install keeps **zero** runtime dependencies. | `pyproject.toml` | New deps go in an optional extra only |

### Conflicts in the requirements document, and their rulings

Recorded here because they were real contradictions, not ambiguities, and a later reader
will otherwise re-discover them.

| # | Conflict | Ruling |
|---|---|---|
| 1 | §4.7 adds four `KeyType` members; §5.8 says tests pass unedited. `test_decisions.py:92-112` pins `KeyType` to exactly eight and pins both SQL `CHECK` blocks to match. | §4.12 scopes "unedited" to the transport seam. Enum-pinning tests are extended in lockstep with §4.7 and that edit is traceable to a requirement. The **transport seam** must force zero test edits — and did. |
| 2 | `paramiko` vs `filterwarnings = ["error"]`. `cryptography` emits `CryptographyDeprecationWarning` on import in many versions, which fails the suite at collection. | One narrow, commented `filterwarnings` ignore scoped to that class. Never broaden to `ignore::DeprecationWarning`. |
| 3 | `paramiko` vs C7. | New optional extra `connectors = ["paramiko"]`. `httpx` is reused from the `agent` extra; the Beacon mock uses stdlib `http.server`. |
| 4 | M6 says assert "with `src/recon/connectors/` absent from the repository", which is not executable in-suite. | Rendered as an AST import ban: nothing under `mocks/` may import `recon.connectors`. Same guarantee, actually runnable. |
| 5 | A mock HTTP server wants a clock; §4.11 forbids one. | Mock tokens carry a fixed issued-at and expire on a request counter, not a wall clock. |
| 6 | Adding tables moves `PRAGMA user_version`. | 3 → 4. `config.SCHEMA_VERSION` bumped in lockstep; `migrate.py` already enforces that they move together. No migration code — the DB is a derived artefact. |

### The evidence reality that shaped every wave

Established by retrieval before planning, not assumed:

- The **Beacon Support Center is real, public, and hosts exactly the articles this build
  wants** — the pharmacy and medical claim templates, the validation code glossary, the
  back-end validations page. **Every one returns HTTP 403** to automated retrieval
  (Intercom-hosted, bot-blocked; tested against four separate URLs including the collection
  index). Recorded as `UNAVAILABLE`, facts marked `UNKNOWN`.
- **Verity's own site is retrievable** and yielded seven first-hand excerpts, two of which
  the assessment document does not contain: the mode A / mode B submission choice in a
  shipping TPA's own words, and the 12 January 2026 pilot pause.
- **Craneware's 2023 announcement confirms Doc 2's report list almost word for word** — the
  strongest first-hand vendor evidence in the directory.
- **Therefore `Assignment_Doc_2.pdf` carries the `SPEC` tier**, page-cited and marked
  second-hand, rather than the vendors carrying it. That is the inverse of what the
  requirements document assumed, and it is why the field tally came out **7 `SPEC` / 6
  `STANDARD` / 234 `INVENTED`**.

---

## Wave 0 — Evidence ✅

**Requirements:** V1–V5 · **Doc 2 step:** 2 · **Gate:** hard — no vendor code before its evidence file.

### Phase 0.1 — Retrieval ✅
- **Sources:** Beacon Support Center URLs; `verity-solutions.com`; `thecranewaregroup.com`; `Assignment_Doc_2.pdf` (all 10 pages); X12, NCPDP, Nacha.
- **Deliverables:** retrieval outcomes per URL, verbatim or failure.
- **Criteria:** every source either quoted verbatim or recorded with its exact failure and date. No gap filled from recollection.
- **Tests:** n/a (input phase).
- **Behaviour:** an unreachable source produces an `UNAVAILABLE` row, not a plausible paraphrase.

### Phase 0.2 — Index ✅
- **Sources:** phase 0.1 output.
- **Deliverables:** `docs/vendor_evidence/` — `index.jsonl` (47 rows), `README.md`, `assignment_doc2.md`, `beacon.md`, `verity.md`, `craneware.md`, `standards.md`.
- **Criteria:** every row carries id, vendor, status, source file, url, retrieval date, and either an excerpt or a failure. Every Doc 2 claim cites a page. Each vendor file ends with a "what remains genuinely UNKNOWN" section.
- **Tests:** `test_every_index_row_carries_every_required_key`, `test_no_evidence_entry_exists_without_an_excerpt_or_an_unavailability_record`, `test_an_unavailable_entry_records_its_failure_and_claims_nothing`, `test_every_doc2_derived_claim_cites_a_page`.
- **Behaviour:** the index is hand-written and never generated, so no builder can silently destroy an edit.

### Phase 0.3 — Enforcement ✅
- **Sources:** V3, V4; existing test conventions in `test_decisions.py` / `test_generators.py`.
- **Deliverables:** `tests/test_vendor_evidence.py`.
- **Criteria:** citation enforced by test, not discipline. Field-tier tests written *before* the code they police, passing vacuously until wave 1.
- **Tests:** deleting one entry turns the suite red (**verified empirically** by deleting `VERITY-004` and restoring it); a `SPEC` field citing an absent id fails; a `SPEC` field citing an `UNAVAILABLE` source fails; an untagged field fails.
- **Behaviour:** a 403'd URL cannot launder "this page exists" into "this page says what I wrote".

**Result:** 14 tests. The enforcement test caught real drift on its first run — four `STD-*` rows pointed at a file that did not write them up, which is how `standards.md` came to exist.

---

## Wave 1 — Data layer ✅

**Requirements:** M1–M6, plus D2, D3 and C1's payload half · **Built first**, before any transport.

### Phase 1.1 — Identifier minting ✅
- **Sources:** `BEACON-013` (Beacon IDs exist and are persisted), `VERITY-006` (a Split Transaction spec exists, unpublished), §4.4; `orchestrator.py`'s existing `_mint_trace_number` / `_mint_allocation_code`.
- **Deliverables:** `_mint_beacon_id`, `_mint_accumulation_id`, `_mint_invoice_number`; `GenerationResult.vendor_refs`; `config.vendor_dir()` / `vendor_identifiers_path()`; sidecar writing in `write_outputs`.
- **Criteria:** identifiers minted by the orchestrator, never by a mock. **The six feeds stay byte-identical.** The sidecar carries no episode id, verdict or amount.
- **Tests:** `test_every_beacon_id_a_mock_emits_is_one_the_orchestrator_minted`, `test_the_identifier_sidecar_carries_no_episode_id_verdict_or_amount`, `test_the_sidecar_join_keys_resolve_against_the_real_tpa_feed`.
- **Behaviour:** a Beacon ID in a mock response is byte-identical to the minted string.

**Two load-bearing ordering facts discovered here.** Vendor refs are minted **last**, because `SliceRefs` is a dense counter and `_mint_trace_number` derives every `trn02` from the ref it is handed — minting earlier renumbers every trace number and changes all six feeds. And the sidecar spells join keys the way `tpa.py` spells them (`ndc_11`, wire-format `fill_date`); the first version used its own spelling and would have joined to nothing while looking correct in review.

**Why a sidecar and not a seventh feed.** Traced rather than assumed: feed content reaches the dossier, the proposer prompt, and the `ReplayClient` digest. A field would survive *today* (closed adapter dict + allow-listed dossier), but `get_raw_record` returns a feed's `file_sha256` and one fixture calls it — dormant only because that test skips. The safety is one re-recorded fixture away from vanishing.

### Phase 1.2 — Vendor formatters ✅
- **Sources:** `DOC2-009` (Verity's five datasets), `CRANEWARE-001` (Craneware's five reports, verbatim), `DOC2-007` (Beacon's five payload kinds), `MOCK_FIELDS.md` tiers.
- **Deliverables:** `src/recon/mocks/` — `source.py`, `verity_export.py`, `craneware_export.py`, `beacon_payloads.py`, `coverage.py`, `MOCK_FIELDS.md`.
- **Criteria:** formatters only. No pricing/decision-tree/ground-truth import, no money arithmetic, no adjudication. Every emitted field declared with a tier.
- **Tests:** `tests/test_mocks.py` — import bans, `test_every_amount_in_the_vendor_output_appears_verbatim_in_the_feed`, `test_the_beacon_mock_emits_only_outcomes_the_feed_already_contains`, `test_every_field_the_formatters_emit_is_declared_with_a_provenance_tier`.
- **Behaviour:** files land on disk, openable by a human and assertable by a test, with no transport in existence.

### Phase 1.3 — Coverage ✅
- **Sources:** M5; `DOC2-002` step 5 (the four archetypes).
- **Deliverables:** `coverage.py`, `coverage.json`, `COVERAGE.md`.
- **Criteria:** every vendor record type counted; zero empty types; all four archetypes present.
- **Tests:** `test_no_vendor_record_type_produces_zero_rows`, `test_all_four_golden_claim_archetypes_appear_in_the_vendor_layer`.
- **Behaviour:** a dead population filter is visible as a count, which is the only place that failure shows.

**Result:** 29 tests; 15 record types, none empty, on both profiles. Two tests verified to bite by injecting the violation.

---

## Wave 2 — Framework ✅

**Requirements:** A1, A2, A3, A5, B1, B2 · **Gate:** the existing suite passes with **zero** edits.

### Phase 2.1 — Transport seam ✅
- **Sources:** §4.2, §4.9; `pipeline.py:135` as it stood.
- **Deliverables:** `connectors/transport.py` (`Document`, `Transport`, `LocalDirectoryTransport`), `connectors/registry.py` (`Source`, `local_sources`), `pipeline.load_from_sources` with `load_feeds` as a wrapper.
- **Criteria:** `load_feeds` keeps its signature; nothing below it changes; no transport can reach `truth/`.
- **Tests:** three refusal-route tests, `test_a_document_carries_no_filesystem_handle`, `test_no_transport_implementation_can_resolve_a_path_under_truth` (parametrised over whatever the module ships, so later transports are covered on arrival).
- **Behaviour:** `LocalDirectoryTransport` reproduces the old loop exactly — same files, same order, same skip-when-absent.

**Defect found and fixed here.** `_read_rows` derived both payload format and fallback attribution from the filename, so any source outside the original six raised `KeyError` on the first record not declaring its own `source_system` — making A1's "no edit to `pipeline.py`" untrue for exactly the case A1 exists to cover. Both are now properties of the `Source` row.

### Phase 2.2 — Credentials ✅
- **Sources:** A3, §4.10; `BEACON-011` (two-token, single-sourced); existing agent-key redaction discipline.
- **Deliverables:** `connectors/credentials.py`.
- **Criteria:** env then secrets file; paths and credentials in separate namespaces; one greppable escape hatch.
- **Tests:** redaction across five render paths; `test_a_missing_credential_is_a_named_failure_naming_what_to_do`; `test_no_credential_value_appears_in_any_tracked_file`.
- **Behaviour:** a wrong credential produces a named failure naming the variable to set, not a stack trace.

### Phase 2.3 — Schema registry and contracts ✅
- **Sources:** B1, B2; `QuarantineReason.SCHEMA_VERSION_MISMATCH` (shipped unused).
- **Deliverables:** `connectors/schema_registry.py`; `connectors/contracts/` (README + one document per registered contract).
- **Criteria:** validation before adaptation; unregistered sources validate vacuously; documented field table matches the registry field-for-field.
- **Tests:** `test_changing_one_field_type_quarantines_with_schema_version_mismatch`, `test_the_documented_field_table_matches_the_registered_contract_exactly`.
- **Behaviour:** a mismatch quarantines with a detail naming the offending field, so the queue is actionable.

**Result:** 28 tests. **656 passing, zero edits to pre-existing tests.**

---

## Wave 3 — File pattern ⬜

**Requirements:** A4, D1, B3 · **Doc 2 step:** 3 · **Rationale:** prove the fabric on the easy transport first, so Beacon's harder auth is debugged against known-good framework.

### Phase 3.1 — SFTP transport
- **Sources:** `DOC2-010` (Verity transport row: file landing, checksum, file-level idempotency, source-control totals), `CRANEWARE-004`, `DOC2-013`; paramiko documentation.
- **Deliverables:** `connectors/sftp.py` (`SftpTransport`), `connectors` optional extra, narrow `filterwarnings` entry.
- **Criteria:** connect, list remote directory, filter by checkpoint, download. Must refuse a ground-truth path like every other transport. SSH key resolved through `credentials.resolve` — the **first** `.reveal()` call site.
- **Tests:** loopback SFTP server only, no network; `ForbiddenPathError` on a truth path; `test_revealing_a_secret_is_a_single_greppable_call` updated to expect `sftp.py`.
- **Behaviour:** a failed fetch fails loudly and is re-run by hand. No retry, no backoff — step 6.

### Phase 3.2 — Checkpointing (A4, A5)
- **Sources:** A4, A5, §4.6, §4.11.
- **Deliverables:** `connectors/checkpoint.py`; `connector_checkpoint` table; `PRAGMA user_version` → 5.
- **Criteria:** remember last fetched file per source (name, mtime, hash). Wall-clock permitted **here and in logs only**; nothing downstream may read a checkpoint timestamp, because `received_at` remains the only temporal field the pipeline honours.
- **Tests:** run twice against the same remote → second run downloads **zero bytes** and ingests **zero records**; replaying a captured fetch sequence twice produces identical DB state by row count and content hash.
- **Behaviour:** a re-run is safe to just do.

### Phase 3.3 — Vendor mappings and reversal semantics (D1, B3)
- **Sources:** `DOC2-013` ("both can share the same secure-file connector framework"); `verity_export.REVERSAL_REPRESENTATION`, `craneware_export.REVERSAL_REPRESENTATION`; the contract documents.
- **Deliverables:** `connectors/vendors/verity.py`, `connectors/vendors/craneware.py`; registry rows.
- **Criteria:** Verity and Craneware differ by a config row and a mapping module, nothing else. Each mapping declares its reversal representation explicitly.
- **Tests:** a golden reversed claim produces **exactly one** net ledger effect — not zero, not two; the two vendors differ only in their mapping module.
- **Behaviour:** the B3 hazard is live in-repo — `tpa.py` uses a negative-quantity row upstream while both exports use a status flag — so a connector written for the wrong one is demonstrably wrong rather than theoretically wrong.

---

## Wave 4 — API pattern ⬜

**Requirements:** C1 (server half), C2, C3, C4, C5 · **Beacon: the only bidirectional connector in the document.**

### Phase 4.1 — Beacon mock server (C1)
- **Sources:** `DOC2-007` (direction, auth, mapping, processing, payment close), `BEACON-009` (45-day window), `beacon_payloads.py` from wave 1.
- **Deliverables:** `mocks/beacon_server.py` — stdlib `http.server`, deterministic, seeded.
- **Criteria:** token exchange, claim submission, submission-status retrieval, validation outcomes, rebate status. Replays wave-1 payloads; **never adjudicates**. Tokens expire on a request counter, not a clock (ruling 5).
- **Tests:** the mock produces every Beacon-driven outcome the engine already models; loopback only.
- **Behaviour:** the response set equals the set already in the feed.

### Phase 4.2 — HTTP transport and two-token auth (A2, A3)
- **Sources:** `BEACON-011`, `DOC2-008`; `httpx` (already a dependency).
- **Deliverables:** `connectors/http.py` (`HttpApiTransport`).
- **Criteria:** Access Token + Private Token presented per request; endpoint switchable to live by configuration only.
- **Tests:** wrong credential → named failure; no key in any journal or repr.
- **Behaviour:** the adapter is built to the documented contract; only the endpoint changes.

### Phase 4.3 — Outbound and inbound adapters (C2, C3)
- **Sources:** `DOC2-007`; `beacon_payloads.submission` / `validation_outcome` / `rebate_status`.
- **Deliverables:** `connectors/vendors/beacon.py`.
- **Criteria:** outbound maps canonical → Beacon template, submits, persists the Beacon ID against the episode. Inbound maps onto existing `TPA_MANUFACTURER_DECISION` / `REBATE_BATCH` kinds.
- **Tests:** a golden claim submits, receives a Beacon ID, and it appears on the episode timeline; a rejected submission produces `REBATE_REJECTED` with the vendor reason **verbatim, not paraphrased**.
- **Behaviour:** validation failures are source-data exceptions, not financial postings (`DOC2-007`).

### Phase 4.4 — Submission ownership and the crosswalk key (C4, C5)
- **Sources:** `DOC2-014` (mode A / mode B), **`VERITY-004`** (the same choice, first-hand, in a shipping TPA's words), §4.7.
- **Deliverables:** per-covered-entity mode configuration; `KeyType.BEACON_ID`; `normalized_record.beacon_id`; enum-pinning tests extended per ruling 1.
- **Criteria:** with mode B configured the outbound path is **unreachable**, not merely skipped.
- **Tests:** a test asserts no submission call can be constructed under mode B; a rebate payment carrying only a Beacon ID resolves to the correct episode.
- **Behaviour:** getting this wrong means the same claim is submitted twice — a real double-submission, not a hypothetical, because `VERITY-004` shows a TPA offering exactly this choice.

---

## Wave 5 — Business mapping ⬜

**Requirements:** E1–E5 · **Doc 2 step:** 4. Shared-file edits (schema, enums) done in the foreground first; then five parallel agents on disjoint mappings.

| Req | Sources | Deliverable | Criteria | Test | Behaviour |
|---|---|---|---|---|---|
| **E1** 340B ID | `BEACON-012`, `DOC2-008` (permission is per 340B ID) | populate `episode.covered_entity_id`; `KeyType.COVERED_ENTITY_340B` | the column stops being written `None` | a TPA record for the wrong covered entity does not resolve | episodes carry a covered entity |
| **E2** HCPCS | `DOC2-002` step 4 ("NDC/HCPCS"), `STD-X12-837`, `BEACON-008` | `episode.hcpcs`, `normalized_record.hcpcs`, `KeyType.HCPCS` | medical-benefit drugs billed by J-code are modelled | a medical record carrying only a J-code resolves | NDC and HCPCS both key |
| **E3** payment refs | `DOC2-002` step 4, `STD-NACHA-CCD` | `normalized_record.payment_reference`, `KeyType.PAYMENT_REFERENCE` | manufacturer becomes a key, not just a reference entity | a manufacturer rebate payment resolves by its own payment reference | the rebate leg joins to cash |
| **E4** site identity | `DOC2-010` ("site / contract pharmacy") | `episode.site_id`, `normalized_record.site_id` | `pharmacy_npi` alone stops collapsing a network into one identity | two contract pharmacies under one NPI-holding entity are distinguishable | a health system's network is visible |
| **E5** source-of-truth | `DOC2-004` (the page-2 authority table) | per-field authority enforcement | authority enforced **in data**, not by convention | a TPA record attempting to set rebate-payment status is **rejected**, not silently accepted | each system may only set what it owns |

**Regression criterion for the whole wave:** crosswalk accuracy does not fall below the existing 99% floor. **Held** — the floor is an existing test (`MIN_RESOLVABLE_RATE_FULL`), and it is green.

### The E1 caveat — stated rather than buried

**E1's first clause is met on the pharmacy track and not on the medical track**, and the
reason is a decision, not an omission.

`episode` is immutable (`trg_episode_no_update`), so all three identity columns are derived
at INSERT or never. On the full profile: 927 pharmacy episodes carry `DSH310074`, 17 carry
NULL because their NPI is the satellite the covered entity never registered — that NULL is
the unregistered-location finding, not a gap — and **556 medical episodes carry NULL**,
because a 837 names no pharmacy and nothing else on a medical anchor is registration-backed.

The first implementation did fill them, by matching the rendering prescriber against
`affiliated_prescriber_npis`. It reached 22 of 23 medical episodes and was then **wrong on
five**: it contradicted the TPA's own covered entity, which `DOC2-004` makes authoritative
for 340B qualification and source transaction context. Affiliation says who a prescriber
practises with; it does not say whose 340B claim a dispense is.

That made the inferred value worse than no value. It lands in a column everything downstream
reads as fact, and here it drove the contradiction guard — so the guess parked five records
the TPA had labelled correctly. The guard treats absence as "no opinion" and never fires on
it, so a NULL is inert where a wrong entity is actively harmful. Registration-only it is,
and a medical episode's entity now arrives from the TPA record that states it outright.

**What would close it properly:** a billing-provider NPI on `CoveredEntity`, which is
registration-backed and is what a medical 340B claim is actually billed under. That is
reference-data work with feed-byte-identity implications, so it is named here rather than
done quietly.

### E3's recorded basis is deliberately imprecise

A bank deposit resolving via `PAYMENT_REFERENCE` now shares the rebate-batch branch with
`ALLOCATION_CODE`. **An earlier version of this section claimed the omission produced wrong
money. It does not, and the correction is worth recording because it changes who should
care.** `_allocate_bank_row` chooses its splitter from the resolved target's `record_kind`,
not from the basis, so such a deposit is fanned out across the batch's dispense lines either
way. What the grouping protects is the audit trail: the basis is the field that states how a
deposit was tied to what it paid, and a manufacturer's rebate settlement filed as a payer's
claim-payment reassociation is a reconciliation nobody can re-derive later.

The basis is still recorded as `ALLOCATION_CODE` rather than getting a member of its own.
`AllocationBasis` is guarded at import by `agents/tools.py`'s match-strength table, and every
recorded agent-eval trace is keyed on a digest of the prompt that table feeds — so a new
member invalidates the fixtures to record a distinction no ledger consumer reads. Both
references are exact rather than inferred, so the *strength* the audit trail reports is
honest even though the reference name is not. Carried to `DESIGN_NOTE.md` §9.

---

## Wave 6 — Proof ⬜

**Requirements:** F1, F2, F3 · **This wave is what makes the build claimable.**

### Phase 6.1 — Four golden claims (F1)
- **Sources:** `DOC2-002` step 5; `DOC2-003` (the working-connection bar).
- **Deliverables:** four tests, one per archetype.
- **Criteria:** paid, rejected, reversed, unmatched, each traced from source file → raw → normalized → episode → verdict → ledger.
- **Tests:** four tests, each asserting figures at **five** stages.
- **Behaviour:** Doc 2's bar is "at least one golden claim"; step 5 raises it to four plus control totals.

### Phase 6.2 — Control totals (F2)
- **Sources:** `DOC2-010` ("source-control totals"), `DOC2-013` ("file-level controls"); the trailer rows wave 1 already emits.
- **Deliverables:** `connectors/control_totals.py`; `control_total` table with `RAISE(ABORT)` immutability; `QuarantineReason.CONTROL_TOTAL_MISMATCH`.
- **Criteria:** declared vs ingested count and sum reconciled per batch.
- **Tests:** a feed whose trailer declares 500 when 450 arrived **fails the batch** rather than ingesting 450 successfully; editing a control total raises.
- **Behaviour:** silent truncation is the failure mode that parses cleanly, so it is the one that gets a named condition.

### Phase 6.3 — Readiness report (F3)
- **Sources:** `DOC2-016` (the Week 3 gate evidence table); `MOCK_FIELDS.md` tiers; the registry.
- **Deliverables:** a generated report per source.
- **Criteria:** generated from code and config, **never hand-maintained**. States transport/auth/schema implemented, mock fidelity (real spec vs invented), golden claims traced, control totals reconciled, and what remains vendor-blocked.
- **Tests:** the report regenerates identically; every source appears; the `SPEC`/`INVENTED` split matches `MOCK_FIELDS.md`.
- **Behaviour:** our version of Doc 2's Week 3 gate — and it will say, honestly, that every row is vendor-blocked for a prototype.

---

## Wave 7 — UI ⬜

**Not from the requirements document** — from the session goal. Runs last because the connectivity work gives it something real to show.

### Phase 7.1 — Design system
- **Sources:** existing `web/src/styles.css` (already light-only, borders-not-shadows, tabular numerals); linear.app as visual reference.
- **Deliverables:** spacing scale and type scale as CSS custom properties replacing per-rule pixel literals; Inter via `@fontsource-variable/inter` (local npm package, **no CDN**); retuned accent.
- **Criteria:** **light mode stays** — `color-scheme: light` and the `<meta>` preserved, no `prefers-color-scheme: dark` block introduced. Information density preserved; this is a dashboard, not a marketing page.
- **Tests:** browser — no console errors; existing `test_api.py` still passes.
- **Behaviour:** a retune, not a rewrite. The stylesheet is already in Linear's neighbourhood.

### Phase 7.2 — Connectivity page
- **Sources:** wave 6's readiness report; the source registry; checkpoints; control totals.
- **Deliverables:** a new page plus its API route.
- **Criteria:** every source listed with real/mocked status, transport, schema version, last checkpoint, control-total state.
- **Tests:** browser — page renders, every source visible with correct status; API route returns the same data the report generates.
- **Behaviour:** this is where the connectivity layer becomes visible, and what makes browser testing meaningful for waves 0–6.

---

## Build architecture

**The shared-file rule — the thing that decides whether parallel fanout works at all.**
Four files are touched by many waves: `schema.sql`, `domain/enums.py`, `config.py`,
`ingest/pipeline.py`. Concurrent agent edits would clobber each other. So **every
shared-file edit is made in the foreground, first**, and agents are then fanned out onto
*disjoint new files only*. After each fanout the seams are re-read and the suite run before
the wave closes.

**Fanout is sized at runtime** by real modularity and task depth — not a fixed number. Each
agent is required to **read the actual code and evidence before writing**; none may write a
vendor fact from memory. Observed sizing so far: wave 0 → 0 build agents (evidence is
accuracy-critical and was written in the foreground) + 1 convention-reader; wave 1 → 1
serial (orchestrator) then 3 parallel formatters + 1 provenance assembler; wave 2 → 1
foreground seam + 3 parallel (credentials, schema registry, contracts).

---

## Testing architecture

Two **Fable reviewer** subagents, each directing **Opus sub-sub-agents** spawned dynamically
by load. The Fable agents **plan first**, work only from the documentation, review, and
decide next steps. They never write code. The Opus workers fetch code, run scripts, and
report results back.

- **Fable A — contract reviewer.** Works from `connectivity_layer_requirements.md` and
  `docs/vendor_evidence/`. Asks: does the code do what the requirement says, and does every
  vendor claim resolve to an evidence id or an `INVENTED` tag?
- **Fable B — regression reviewer.** Asks: did anything that used to hold stop holding?
  Owns the existing suite, the structural guarantees, determinism, and blind-slice.

**Two aspects, both required per wave:**

1. **API-level** — the pytest suite plus live probes against
   `uvicorn recon.api.app:create_app --factory --port 8000`.
2. **Browser** — Playwright MCP against `http://localhost:5173`, verifying the outcome is
   actually visible, not merely returned.

**One constraint the goal did not anticipate.** Background subagents lose MCP access, so a
Playwright sub-sub-agent cannot reach the browser. The Fable reviewer therefore **authors**
the browser script and **grades** the returned snapshots, while the Playwright run is a
foreground worker — one at a time, never parallel, shared browser. Same division of labour,
MCP constraint respected.

---

## Diagram protocol

One living canvas: `docs/images/project-overview.excalidraw`. **Append only — never redraw.**
All 271 original elements keep their ids, coordinates and `versionNonce`.

Each wave appends one band below the existing content via
`scripts/append_diagram_band.py --wave N`, starting at y = 5100 and stepping 210px, matching
the existing band style. Bands are prefixed `cN_` and the script deletes its own prefix
before appending, so a band can be corrected and re-applied without ever duplicating. Seeds
derive from `blake2b` over the element id and the timestamp is fixed, so re-running is
byte-identical.

---

## Commit protocol

Commit at the end of **every phase** and **every wave**, once its tests are green.
**Commit only — no push, no PR, no branch changes.** Each message states the wave, the
requirement ids closed, what was discovered, and the test delta.

---

## Wave gate — all must pass before the next wave starts

1. `uv run --quiet pytest -q` green. Wave 2 additionally required **zero edits** to
   pre-existing tests — met.
2. `uvicorn` + `POST /api/regenerate?profile=demo` rebuilds clean.
3. Fable A: every new vendor claim resolves to an evidence id or is tagged `INVENTED`.
4. Fable B: determinism holds (same seed, same feed hashes); blind-slice and `truth/`
   isolation still hold.
5. Browser pass from wave 7, and on any wave that changes an API response the dashboard reads.

**Final acceptance** is §5 of the requirements document, items 0 through 8.

---

## Progress

| Wave | Requirements | Status | Tests | Commit |
|---|---|---|---|---|
| 0 Evidence | V1–V5 | ✅ *(see V1 caveat)* | +14 (599) | `b2e0d11` |
| 1 Data layer | M1–M6, D2, D3, C1a | ✅ | +29 (628) | `dcafce5` |
| 2 Framework | A1, **A2 partial**, A3, A5†, B1, B2† | ✅ | +28 (656) | `9bd79aa` |
| 3 File pattern | A4, D1, B3, **A2 (SFTP)** | ✅ | +14 (705) | `302b3bd` |
| 4 API pattern | C1b, C2–C5, **A2 (HTTP)** | ✅ | +15 (720) | `4e56eda` |
| 5 Mapping | E1–E5 | ✅ *(see E1 caveat)* | +85 (805) | `ab9f7d8` |
| 6 Proof | F1–F3 | ✅ | +76 (881) | `e02be93` |
| 7.1 UI restyle | goal item 5 | ✅ | — | `5eafe36` |
| 7.2 Connectivity page + browser | goal item 5 | ✅ | +6 (887) | `4533988` |

Baseline was **585**.

---

## Verdict after the two-Fable adversarial review

Waves 3-6 were reviewed by two independent Fable reviewers, each directing its own fan-out of
Opus workers, each planning before dispatching. **A wave being committed and green is recorded
above; it is not the same as its requirements being met, and the two disagree.**

| Req | Status column above | Reviewed verdict | Why |
|---|---|---|---|
| A2 (SFTP) | closed | **PARTIAL** | 7 tests of 890, 114 bytes, 2 synthetic JSONL documents over the socket. No vendor export and no six-feed path ever crosses it. |
| A4 | closed | **PARTIAL** | The zero-bytes acceptance genuinely passes. `content_changed` is dead code and `content_sha256` is write-only, so the documented hash mechanism is not the one running. |
| B3 / D1 | closed | **PARTIAL** | The ledger half is proven on the PBM path by F1's reversed claim. On the vendor path it is detection only, and `ReversalEffect` reaches no table. |
| C1 | closed | **PASS** | A real threaded `http.server` on a loopback socket, clock-free, replaying rather than adjudicating. 15 of 17 error codes have no negative test. |
| C2 | closed | **FAIL** | Mapping is built; submit and persist are not. No `Sender` implementation exists and no Beacon ID reaches any table. |
| C3 | closed | **PARTIAL** | The verbatim rule is written correctly and never persisted; the only test compares two mock-side reads of the same dict. |
| C4 | closed | **PARTIAL** | The unreachability mechanism is genuinely strong — four structural locks, no `if`-skip. "As configuration" was never built: ownership exists only as a constructor argument. |
| C5 | closed | **FAIL** | `keys.beacon_id` has zero callers in `ingest/`. **0 `BEACON_ID` rows** in any database this build produces. |
| E1 | partial | **PARTIAL, worse than recorded** | `COVERED_ENTITY_340B` is published and never looked up. The acceptance holds by an equality check, not by construction as `keys.py` claimed. |
| E2 | closed | **PASS** | Enum, publish, lookup and column all wired; the acceptance test carries a J-code and that key's own components, nothing else. |
| E3 | closed | **FAIL** | **0 `PAYMENT_REFERENCE` rows.** `normalized_record.payment_reference` is never assigned by any adapter. Manufacturer-as-a-key — this table's own stated criterion — was never built and no decision records dropping it. |
| E4 | closed | **FAIL** | No record carries a `site_id`. The acceptance passes against a hardcoded Python tuple, never against an episode or a key. |
| E5 | closed | **PASS** | The check sits on the only ingest path, before `_insert_tree`, with a named quarantine reason and lineage intact. |
| F1 | closed | **PASS** | Four archetypes, five stages, figures re-read from the feed file on disk, mutation-checked. |
| F2 | closed | **PARTIAL** | The batch genuinely does not land. But the only two trailered sources never reach the check, so every check that executes is vacuous. |
| F3 | closed | **PASS** | Every cell derived; a worker rebuilt the report independently and diffed it byte-for-byte against the served payload. |

**One root cause explains C2, C5, E3, F2 and half of D1: nothing in `src/` imports the vendor
connector modules.** Verity, Craneware and Beacon are written, documented and tested in
isolation, and no path carries a vendor record into the database.

### What the review proved was NOT broken

A full build at HEAD against one at `dbb129e` (pre-connector), both profiles: episode identity
— id, track, NDC, date — **identical**; `raw_record`, `normalized_record`, `episode`,
`verdict`, `cash_allocation`, `parked_record` all identical. The only downstream change is
`crosswalk_key` 11947 -> 13613 on `full`, and that delta of 1666 is exactly
`COVERED_ENTITY_340B` 855 + `HCPCS` 811. Zero orphan raw rows, zero quarantines, zero
`COVERED_ENTITY_MISMATCH` parks.

### Defects the review found and that are fixed (`346f9bf`)

1. **A demonstrated ground-truth bypass.** `SftpTransport` refused `/exports/truth` and
   accepted `/exports/TRUTH`; a worker fetched `ground_truth.json` over real SSH through it.
   Three transports each carried their own copy of the check and disagreed. Now one helper.
2. **The test that should have caught it** enumerated one class while its docstring promised
   all three.
3. **Assertions that could not fail**, including the scoped-key check added in wave 5, which
   selected an episode carrying no scoped key so its loop body never ran.
4. **Three docstrings asserting things the code does not do** — corrected in place.

### Conflict ruling 2 was wrong about its own build

The table above says one narrow ignore for `CryptographyDeprecationWarning`. The build
actually carries **two**, neither for cryptography: a starlette TestClient httpx deprecation
and an anyio `BlockingPortal` alias. Both are message-pinned and `"error"` survives as entry
one, so the discipline holds — but the ruling describes a decision that did not ship.

**Two pre-existing tests have been edited, and only two.** Both for the same underlying
reason — a set pinned at exactly the eight Decision-A24 key types, which no §4.7 addition
can satisfy — and both edited as *additions* rather than relaxations. Ruling 1 anticipated
this; it did not anticipate that the pin appeared in two places.

| Test | Wave | What changed | Why it is not a relaxation |
|---|---|---|---|
| `tests/test_decisions.py` | 4 | A24 pin split into `A24_KEY_TYPES` (eight, exact) plus `CONNECTOR_KEY_TYPES` (four, exact) | a ninth A24 member and a fifth connector member each still fail |
| `tests/test_api.py` | 5 | the `/trace` key-type set gained the two episode-bound connector types | the eight stay a set of their own, and two **new** assertions were added: a scoped key must wrap a natural key present on that same episode, and one episode may be scoped to at most one covered entity |

Every other pre-existing test remains untouched. `tests/test_connectors.py` was also edited
in wave 5, but it is connector-layer work from wave 2 rather than part of the 585.

**One connector-layer test was corrected, not relaxed.**
`test_pipeline_names_no_vendor_and_no_feed_filename` walked every string constant in
`pipeline.py` and rejected any containing a vendor name. That caught two false positives the
moment wave 5 touched the file: a docstring *explaining* the rule, and `"beacon_id"`, which
is a `normalized_record` column rather than a vendor branch. Both exclusions are now
explicit — docstrings by AST node identity, column names by joining against the repository's
own column tuples — and a genuine `if source == "beacon":` is still caught. **This is the
third time in this build that a text-or-constant scan has punished a module for documenting
the prohibition it obeys.** The cheapest way to make such a test pass is to delete the
explanation, which is exactly backwards, so the exclusion is now structural rather than a
hope that no prose mentions a vendor.

**Environment note.** The suite runs on Python 3.12 / SQLite 3.50, because the 3.11
interpreter available on this machine is ARM64 and has no `cryptography` wheel — so paramiko
cannot load on it and wave 3's SFTP work could not be exercised at all. On SQLite 3.50's
query planner, `test_query_plans.py::test_latest_verdict_walks_the_index_not_the_table`
selects `sqlite_autoindex_verdict_1` over `ix_verdict_latest` and fails. **This is not a
regression:** it fails identically at `dbb129e`, the commit before this branch began,
verified in a worktree on the same interpreter. The query is still an index seek; the planner
simply prefers a different index. Reported as "705 passed, 1 pre-existing environmental
failure" rather than as green, because rounding it to green is how a real failure later gets
mistaken for this one.

### Corrections after adversarial review

Two Fable reviewers audited waves 0–2, each directing Opus workers. Fable B's verdict was
**no regression** — all eight standing guarantees verified empirically, including
cross-commit byte comparison of every feed hash on three profiles, 52 adversarial `truth/`
path vectors, and an old-database rejection test. Fable A found real defects, and the
status table above is corrected accordingly rather than left flattering.

**A2 is one implementation of three, not complete.** `SftpTransport` and `HttpApiTransport`
do not exist; `TransportKind` declares `SFTP` and `HTTP_API` with nothing behind them. A2's
acceptance — *"the same suite passes with `SftpTransport` pointed at a local SFTP server"* —
cannot be run yet. The protocol and `LocalDirectoryTransport` are done; A2 completes in
waves 3 and 4.

**† A5 meets its acceptance sentence only.** Measured with a counting transport: the second
run ingests zero records and the database fingerprint is identical, but `fetch()` still runs
and the bytes are still pulled. The requirement says "must not be re-downloaded **or**
re-ingested". The download half needs A4's checkpoint, in wave 3.

**† B2 covers 3 registered contracts, not every source.** The six live feeds and 12 of 15
vendor record types have neither a schema nor a document. The shipped test checks only
*registered* sources, so it cannot see the gap.

**V1's universal claim does not hold.** The citation test verifies that *cited* ids resolve;
it never verifies that a *claim* is cited, and it does not police code comments or
docstrings. Concrete uncited vendor claims exist in `mocks/__init__.py`, `credentials.py`
and the requirements document itself. The machine-enforced core is real; the universal
statement in §5 item 0 is not, and no amount of the current test shape can make it true.

**SPEC is over-claimed against the file's own rule.** `MOCK_FIELDS.md` states that a field is
`SPEC` only when a retrievable source names *that field on that payload*. Six of the seven
`SPEC` rows are envelope fields (`direction`, `template`) that Beacon never published. Under
the stated rule the honest count is nearer 0–1 than 7. The values are verbatim from the
cited excerpts and the thinness is disclosed, so this is inconsistent tiering rather than
fabrication — but it is inconsistent, and `payload_kind` with identical provenance is tagged
`INVENTED`.

**Fixed immediately after the review:** three credential leak paths (key material echoed
from `SSH_KEY_PATH`, `__getstate__` bypassing the pickle refusal, a `JSONDecodeError`
retaining the whole secrets file via `__context__`); the `bank_csv` branch ignoring the
source row's attribution; `payload_format` accepting any string; a bare `ValueError` on an
unknown `source_system`; the diagram band re-render not being idempotent; a false "mirrors
archetype exactly" docstring; and `ingest/__init__.py` promising a ground-truth scan that
did not exist — the scan now exists and covers `ingest/`, `engine/` and `api/`.
