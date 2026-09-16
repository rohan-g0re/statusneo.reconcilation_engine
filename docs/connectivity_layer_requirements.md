# Connectivity Layer — Requirements

**Source:** `docs/Assignment_Doc_2.pdf` (StatusNeo, *Connectivity Assessment*, 11 September 2026)
**Branch:** `connectivity_layer`
**Status:** requirements only — nothing in this document is built yet.

---

## 0. What this document is

Assignment Doc 2 does not ask for new reconciliation logic. It asks for the layer *in front of* the reconciliation logic: the thing that goes and gets the data from six 340B platforms and six adjacent systems, authenticates to each one, lands it safely, and proves a claim can be traced end to end.

Our existing build starts one step later. It assumes six feed files already sit in a directory. Everything downstream of that line — canonical model, crosswalk, episode assembly, verdicts, ledger, queues, agent — is built and tested. Everything upstream of it does not exist.

This document says exactly what to build to close that gap, in what order, and what has to move in the existing code to make room for it.

### The scope boundary, stated honestly

Doc 2 defines two levels of done:

| Level | Doc 2's definition |
|---|---|
| **Working connection** | Authorized source data reliably reaches a Shields lower environment, parses, maps to the source-normalized contract, and at least one golden claim traces end-to-end |
| **Production-ready** | Adds monitored scheduled operation, replay/idempotency, error handling, backfill, performance, security controls, control totals, documented support, and reconciliation of edge cases such as reversals and partial payments |

**Neither is reachable for the six 340B platforms in a prototype**, because both definitions begin with the word *authorized*. Beacon, Verity, Craneware, Macro Helix, PharmaForce and Pillr are all contracted enterprise products gated behind a covered entity. There is no self-service signup. Nobody outside a customer relationship can obtain a token or an SFTP account.

So this document targets a third level, which Doc 2 does not name but which its own Week 1–3 gate implies:

> **Connector-ready** — the adapter is built to the vendor's published contract, exercised against a mock source that reproduces that contract, wrapped in the same transport/auth/idempotency/replay machinery a real connection would use, and switchable to a live endpoint by changing configuration only.

That is a defensible deliverable on Doc 2's own terms: the document itself says the interface packs "must be obtained through Beacon Support" and that no public specification exists for four of the six. Building against a mock is not a shortcut around a solvable problem; it is the correct response to a problem Doc 2 says is not solvable without vendor onboarding.

**What *is* fully achievable at production-ready quality:** every piece of the fabric that does not depend on the vendor — secrets handling, schema registry, checkpointing, retry/replay, idempotency, DQ quarantine, control totals, observability, lineage, backfill. None of that needs a real endpoint.

---

## 1. Scoring the four production patterns

Doc 2, page 9, "Design implication" box, names four production patterns the connector fabric must support. This is the gap analysis.

| # | Pattern | Built? | Can build? | What is actually missing |
|---|---|---|---|---|
| 1 | **API / SDK** — Beacon and any TPA with a supported API | **No** | **Yes** | No outbound HTTP client exists anywhere in the codebase. No auth, no token store, no retry, no pagination, no submission path. This is the one genuinely new build. |
| 2 | **SFTP / structured files** — Verity, Craneware, vendor exports | **Half** | **Yes** | Raw landing, SHA-256 hashing, file-level idempotency, parsing and quarantine all exist. **Transport does not**: no SFTP client, no key handling, no remote listing, no checkpoint of which files were already fetched. |
| 3 | **Healthcare EDI / X12 / NCPDP** — claim and remittance ecosystems | **Yes** | Already there | 837, 835, NCPDP-shaped pharmacy events and ACH are generated, parsed, normalized and reconciled end to end. Gap is fidelity, not capability: our feeds are JSONL-shaped rather than true X12 segment streams. |
| 4 | **Banking / ERP** — cash and financial close | **Half** | **Yes** (bank); ERP deferred | Bank leg is complete — deposits, trace reassociation, two-hop allocation, amount+date fallback, residuals. **No ERP/GL write-back**, which Doc 2 itself marks optional ("read/report first"). |

**Read that table as: pattern 3 is our strongest asset, patterns 2 and 4 need a transport layer bolted onto finished machinery, and pattern 1 is the only real build.**

---

## 2. Requirements

Every requirement is written so it can be implemented and tested. Each has an ID, a statement, an acceptance test, and a note on where it lives.

### Group A — Transport

Transport is the missing front half of Doc 2's step 3 (*adapter build*).

**A1. Source registry replaces the hardcoded feed list.**
Today `config.FEED_FILENAMES` is a fixed 6-tuple and `pipeline.FEED_SOURCE_SYSTEMS` is a dict literal keyed by filename. A connector fabric needs sources declared as data: id, vendor, transport kind, endpoint, credential reference, schedule, adapter version, enabled flag.
*Acceptance:* adding a seventh source is a config row plus an adapter module — no edit to `pipeline.py`.

**A2. A `Transport` protocol with three implementations.**
One interface, three concrete classes:
- `LocalDirectoryTransport` — what we have today, kept so the existing generated-feeds path never changes
- `SftpTransport` — connect, list remote directory, filter by pattern and checkpoint, download to a staging area, verify size/checksum
- `HttpApiTransport` — authenticated request, pagination, rate-limit backoff, response-to-payload

*Acceptance:* `load_feeds` becomes transport-agnostic; the same test suite passes with `LocalDirectoryTransport` and with `SftpTransport` pointed at a local SFTP server.

**A3. SFTP fetch with checkpointing.**
Remember the last successfully fetched file per source (name plus modified-time plus hash). A restart must resume, not re-pull the whole directory. A file already ingested must be skipped before download, not after.
*Acceptance:* run twice against the same remote; second run downloads zero bytes and ingests zero records.

**A4. HTTP client with auth, retry and rate limiting.**
Two-token auth model as Beacon documents it (Access Token plus Private Token). Exponential backoff on 429 and 5xx, bounded attempts, jitter. Every request and response journalled with secrets redacted.
*Acceptance:* a mock server returning 429 then 200 produces one ingested record and one logged retry; a mock server returning 500 five times produces a recorded failure, not a crash and not a silent skip.

**A5. Staging area with atomic promotion.**
Downloads land in a staging path and are promoted into the ingest path only after checksum verification. A half-transferred file must never reach `load_feeds`.
*Acceptance:* truncating a file mid-transfer produces a rejected fetch and no `ingest_batch` row.

---

### Group B — Fabric cross-cutting controls

These are the seven items named inside the yellow strip of Doc 2's page-2 diagram. None of them depends on a live vendor, so all are buildable to production quality.

**B1. Secrets and credential handling.**
Credentials resolve from environment or a secrets file, never from source config, never from the repository. A rotation path exists (credential reference, not credential value, is what a source row stores). Secrets are redacted in every log line and every journal entry.
*Acceptance:* a test greps the run journal and the connector logs for any configured secret value and fails on a hit.

**B2. Schema registry.**
Each source declares a versioned field contract. Inbound records are validated against the registered version before adaptation. A version mismatch quarantines with `SCHEMA_VERSION_MISMATCH` — an enum member that **already exists** in `domain/enums.py` and is currently unused.
*Acceptance:* changing one field type in a fixture produces a quarantined record with the right reason code, and zero normalized records.

**B3. Connector-level idempotency.**
Record-level and file-level idempotency already exist. What is missing is *fetch-level*: the same remote file re-listed on a later poll must not be re-downloaded, and an API page re-delivered after a retry must not be re-ingested.
*Acceptance:* replaying a captured fetch sequence twice produces identical database state, verified by row counts and a content hash.

**B4. Retry and replay.**
Two different things, both required. *Retry* is transport-level and automatic. *Replay* is operator-level: re-run a named source for a named date range from the raw layer without re-fetching, because `raw_record` is immutable and already holds the bytes.
*Acceptance:* deleting every `normalized_record` and replaying from `raw_record` reproduces the same episodes, verdicts and ledger figures.

**B5. Connector observability.**
Per source, per run: did it run, did it arrive, how many records, how many bytes, how long, how many quarantined, how many parked, and what the control totals were. Doc 2's specific worry is a feed that arrives *empty or short* and looks healthy.
*Acceptance:* a source that delivers 0 records when its baseline is ~500 raises a `FEED_VOLUME_ANOMALY`; a source that misses its schedule raises `FEED_NOT_RECEIVED`.

**B6. Control totals.**
Compare the vendor's declared count and declared sum against what was ingested. Doc 2 puts this in step 5 because silent truncation is the failure mode that parses cleanly.
*Acceptance:* a feed whose trailer declares 500 records when 450 arrived fails the batch and records the discrepancy rather than ingesting 450 successfully.

**B7. DQ quarantine surfacing.**
Quarantine exists in the database but is invisible in the UI and API. It needs a queue, a reason breakdown and a replay-after-fix path.
*Acceptance:* a quarantined record is visible through the API, and re-ingesting after correcting the source clears it.

**B8. Backfill.**
Load history from a start date with the same code path as the daily pull. Doc 2 lists it under production hardening, and the existing `received_at` cursor design already supports arbitrary historical loads without a special mode.
*Acceptance:* backfilling 90 days then running the daily fetch produces no duplicates and no re-parked records.

---

### Group C — Beacon connector (pattern 1, the only outbound path)

Beacon is the only bidirectional connector in the entire document. Every other source is a pull. This is also the only one of the six 340B platforms with enough public documentation to build faithfully — Doc 2's Beacon page names the partner model, the two-token auth, Read vs Read/Write permission, claim-level submission history, validation outcomes, Beacon IDs, and **published pharmacy and medical claim data templates**, which is an actual field list.

**C1. Beacon mock server.**
A local HTTP service implementing the documented surface: token exchange, claim submission, submission-status retrieval, validation-outcome retrieval, rebate-status retrieval. Deterministic, seeded, and capable of producing every outcome our engine already models — accepted, rejected, reversed, paid, unpaid.
*Acceptance:* the mock covers every `RebateCode` outcome in `domain/verdicts.py` that a Beacon response could drive.

**C2. Beacon outbound submission adapter.**
Takes a TPA-qualified claim from our canonical model, maps it into Beacon's pharmacy or medical claim template, submits, and persists the returned Beacon ID against the episode.
*Acceptance:* a golden claim submits, receives a Beacon ID, and that ID appears on the episode timeline.

**C3. Beacon inbound status adapter.**
Pulls acknowledgements, validation outcomes, rebate status and payment references; maps them onto the existing `TPA_MANUFACTURER_DECISION` and `REBATE_BATCH` record kinds.
*Acceptance:* a rejected submission produces `REBATE_REJECTED` with the vendor's reason preserved verbatim, not paraphrased.

**C4. Submission ownership as configuration.**
Doc 2's PharmaForce page defines two modes: **mode A**, Shields submits directly to Beacon; **mode B**, the TPA submits and Shields consumes outcomes. This is a per-covered-entity decision and getting it wrong produces duplicate submissions — real money, submitted twice.
*Acceptance:* with mode B configured, the outbound path is not merely skipped but **unreachable**; a test asserts no submission call can be constructed.

**C5. Beacon ID as a first-class crosswalk key.**
New `KeyType.BEACON_ID`. It is the join between our episode and the manufacturer's rebate decision, and later between that decision and the payment reference on the bank leg.
*Acceptance:* a rebate payment carrying only a Beacon ID resolves to the correct episode.

---

### Group D — Secure-file connector (pattern 2: Verity and Craneware)

Doc 2 recommends treating these two as one pattern — its own words: "both can share the same secure-file connector framework." Verity is the committed baseline; Craneware is the second file-based pattern after it.

**D1. One scheduled-SFTP connector serving both vendors.**
Same transport, same checkpointing, same control totals. Vendor difference is confined to the field mapping.
*Acceptance:* Verity and Craneware differ by a config row and a mapping module, nothing else.

**D2. Verity export mock.**
Verity publicly documents *which datasets* it exports — accumulations, contract-pharmacy claims backing, invoices, matches and split transactions, unmatched claims — and that delivery is daily/weekly/monthly encrypted SFTP. It does **not** publish a field dictionary. So the transport is real and the payload is an honest invention, marked as such.
*Acceptance:* every invented field is listed in a `MOCK_FIELDS.md` next to the mapping module, so a reviewer can see exactly what was assumed.

**D3. Craneware export mock.**
Same treatment, using the documented report names: Claims, Ordering Problems, Unreplenished Costs, Audit, Bulk Dispensations.

**D4. Reversal and requalification semantics, declared per vendor.**
Doc 2 flags this on every vendor page and it is the defect that destroys a build quietly: is a reversal a negative row, a replacement record, or a silent delete? Guess wrong and you double-count or lose a rebate, and both look perfectly clean in every report afterwards.
*Acceptance:* each vendor mapping declares its reversal representation explicitly; a test asserts a reversal produces exactly one net effect on the ledger, not zero and not two.

---

### Group E — Business mapping gaps (Doc 2 step 4)

Step 4 is the part we have already built to a depth Doc 2 only sketches in one line — the canonical model, eight crosswalk key types, the natural key, `CLM01` for 837↔835, `TRN02` for 835↔bank, and parking instead of guessing. These are the named gaps against Doc 2's own crosswalk list.

**E1. 340B ID as a real field and key.**
`_create_episode` currently writes `covered_entity_id=None` — the column exists and is never populated. Doc 2 lists 340B IDs first in its crosswalk list, and Beacon permissions are granted *per 340B ID*.
*Acceptance:* episodes carry a covered entity; a TPA record for the wrong covered entity does not resolve.

**E2. HCPCS alongside NDC.**
Doc 2's crosswalk list reads "NDC/HCPCS." Medical-benefit drugs are billed by HCPCS J-code; we only model NDC.
*Acceptance:* a medical record carrying only a J-code resolves to the right episode.

**E3. Manufacturer and payment references as crosswalk keys.**
Named explicitly in Doc 2 step 4. Today the manufacturer is a reference entity, not a key, and the payment reference exists only as `TRN02`/`allocation_code`.
*Acceptance:* a manufacturer rebate payment resolves by its own payment reference.

**E4. Site / contract-pharmacy identity.**
Doc 2 says "provider/site" and every TPA page mentions site or contract pharmacy. We key on `pharmacy_npi` only, which collapses a health system's contract-pharmacy network into one identity.
*Acceptance:* two contract pharmacies under the same NPI-holding entity are distinguishable.

**E5. Source-of-truth boundary made explicit in the data.**
Doc 2's page-2 table assigns authority per field: TPA owns qualification, Beacon owns rebate status, bank owns settled cash, Shields owns only the consolidated picture. Right now our records carry a source system but nothing asserts which source is *allowed* to set which field.
*Acceptance:* a TPA record attempting to set rebate-payment status is rejected or flagged, not silently accepted.

---

### Group F — Golden-claim validation (Doc 2 step 5)

**F1. Four golden-claim archetypes traced end to end.**
Doc 2 names them: **paid, rejected, reversed, unmatched**. For each, a test that follows one claim from the source file through raw, normalized, episode, verdict and ledger, asserting the figures at every hop.
*Acceptance:* four tests, one per archetype, each asserting at five stages.

**F2. Source count and control-total reconciliation per batch.**
See B6. Reported as a first-class artefact, not an assertion buried in a test.

**F3. A connector readiness report.**
One generated document per source stating: transport implemented, auth implemented, schema registered, mock fidelity (real spec vs invented), golden claims traced, control totals reconciled, and what remains vendor-blocked. This is our version of Doc 2's Week 3 gate evidence table.
*Acceptance:* the report generates from code and config, never hand-maintained.

---

### Group G — Explicitly out of scope

Stated so a reviewer does not have to guess whether they were forgotten.

| Not building | Why |
|---|---|
| **PharmaForce, Macro Helix, Pillr connectors** | No public specification for either transport *or* payload. Inventing both proves nothing. Doc 2 rates all three Medium or Med-High precisely because the interface is unknown. |
| **Inmar** | Coexistence/control source only. Relevant during a real migration, not in a prototype with no incumbent to migrate from. |
| **CMS / MTF** | MFP dedup matters from 1 Jan 2027 and belongs in the roadmap, not the prototype. |
| **ERP / GL write-back** | Doc 2 itself says read/report first, write-back only if finance requires it after reconciliation rules stabilise. |
| **Real vendor credentials** | Not obtainable. This is the whole premise of the Week 1–3 access gate. |
| **True X12 segment-level parsing** | Our EDI-equivalent feeds already exercise the semantics. Segment-level parsing is fidelity work with no new reconciliation behaviour behind it. |

---

## 3. Build order

Mirrors Doc 2's own wave structure, which is not an accident — it sequences by proven-path confidence.

| Wave | Contents | Rationale |
|---|---|---|
| **0 — Framework** | A1, A2, A5, B1, B2, B3, B4 | Doc 2's Weeks 2–4 row: build the common gateway, secrets, schema registry, idempotency and replay **before** any vendor adapter. |
| **1 — File pattern** | A3, D1, D2, D3, D4, B6 | Verity and Craneware are the highest-confidence sources in the entire assessment. SFTP is boring and proven. |
| **2 — API pattern** | A4, C1, C2, C3, C4, C5 | Beacon. The only outbound path, and the only vendor with published field templates. |
| **3 — Mapping gaps** | E1–E5 | Cheap once the sources exist to exercise them; expensive to retrofit after. |
| **4 — Hardening** | B5, B7, B8, F1, F2, F3 | Observability, quarantine surfacing, backfill, golden claims and the readiness report. |

Wave 1 before wave 2 is deliberate and is Doc 2's own recommendation: prove the fabric on the easy transport first, so that when Beacon's harder auth model is being debugged, the framework underneath it is already known-good.

---

## 4. Structural changes to the existing codebase

**Headline: nothing is rewritten. The connector layer is almost entirely additive.**

That is not luck. The existing design already made the two decisions that matter here — ingest is event-driven rather than a scan, and an inbound document carries its own lookup keys — so a record that arrives over SFTP or HTTP is indistinguishable, downstream, from one read off disk. What follows is mostly *new modules beside existing ones*, plus four genuine extension points and one honest refactor.

### 4.1 What does not change at all

These stay byte-for-byte as they are, and the existing tests are the proof:

- `src/recon/engine/` — verdicts, dispositions, dimensions, run. The engine is a pure function of `(episode, cursor)`. It does not know where records came from and must not learn.
- `src/recon/api/` and `web/` — the API projects, it does not calculate. New sources produce more episodes, not different ones.
- `src/recon/agents/` — nine tools over the same dossier. **One caution:** the agent layer's grounding rules derive from `docs/knowledge_graph.jsonl`, so any new entity added there must be checked against `src/recon/agents/` before a rebuild.
- `src/recon/crosswalk/keys.py` — key *construction* is unchanged. New key types are added to the enum; the construction rules for existing keys are untouched.
- `src/recon/money.py`, `src/recon/rng.py`, `src/recon/reference/` — unaffected.
- `decision_tree/` — the state space is about what can happen to a claim, not about how the data arrived.

### 4.2 The one genuine refactor: `load_feeds` grows a transport seam

This is the only place where existing code must change shape rather than simply gain a neighbour.

Today (`src/recon/ingest/pipeline.py:135`):

```python
def load_feeds(conn, feeds_dir: Path) -> IngestStats:
    for feed_file in config.FEED_FILENAMES:
        path = feeds_dir / feed_file
        ...
```

Two hardcodings sit in that loop: `config.FEED_FILENAMES` (a fixed 6-tuple) and `FEED_SOURCE_SYSTEMS` (a dict literal keyed by filename, at `pipeline.py:77`). Both assume a local directory containing exactly six known files.

The change is to make the *source* the parameter instead of the directory:

```python
def load_feeds(conn, source: Source, transport: Transport) -> IngestStats:
    for document in transport.fetch(source):
        ...
```

with `LocalDirectoryTransport` preserving today's behaviour exactly, so the generated-feeds path and every existing test keep working. Keep the current signature as a thin wrapper for one release rather than editing every call site at once:

```python
def load_feeds(conn, feeds_dir: Path) -> IngestStats:   # existing callers untouched
    return load_from_sources(conn, local_sources(feeds_dir))
```

**Everything below `load_feeds` — `ingest()`, `_process_raw_record`, `_attach`, `_park`, `_recheck_parked`, the allocator — is untouched.** They operate on `raw_record` rows, and a row is a row regardless of how it arrived. That is the payoff of the original event-driven decision.

### 4.3 New package: `src/recon/connectors/`

The fabric is a new sibling of `ingest/`, not a rewrite of it. Proposed layout:

```
src/recon/connectors/
  __init__.py
  registry.py        # A1  source declarations: id, vendor, transport, schedule, credentials ref
  transport.py       # A2  Transport protocol + LocalDirectoryTransport
  sftp.py            # A3  SftpTransport, remote listing, checkpointing
  http.py            # A4  HttpApiTransport, auth, retry, backoff, pagination
  secrets.py         # B1  credential resolution + redaction
  schema_registry.py # B2  versioned field contracts, validation before adaptation
  checkpoint.py      # A3/B3  per-source fetch cursor
  observability.py   # B5  run stats, volume anomaly, missed-schedule detection
  control_totals.py  # B6  declared vs ingested reconciliation
  vendors/
    beacon.py        # C2/C3  submission + status mapping
    verity.py        # D2     export mapping
    craneware.py     # D3     export mapping
```

Why a new package rather than extending `ingest/`: `ingest/` answers "what does this record mean and what does it join to." `connectors/` answers "how do I get the record and am I allowed to." Those fail differently, are tested differently, and only one of them touches the network. Keeping the seam visible is also what makes the AST and grep tests in the existing suite still meaningful.

### 4.4 New package: `src/recon/mocks/`

Mock sources belong beside the generators, not inside the connectors they serve — a connector that ships with its own fake is a connector nobody can prove is real.

```
src/recon/mocks/
  beacon_server.py   # C1  local HTTP service implementing the documented Beacon surface
  sftp_server.py     # D1  local SFTP endpoint serving generated exports
  verity_export.py   # D2  writes Verity-shaped files from existing generated episodes
  craneware_export.py# D3  ditto
```

These consume what `src/recon/generators/` already produces. No new synthetic data engine — the existing orchestrator already mints every cross-feed identifier and withholds ground truth, so the mocks are *formatters*, not generators. That preserves the existing guarantee that generators never see an episode id or an expected amount.

### 4.5 Schema additions (`src/recon/db/schema.sql`)

All additive. No existing column changes type, no existing table loses a constraint.

| Table | Change | For |
|---|---|---|
| `connector_source` | **new** — registered sources and their config | A1 |
| `connector_fetch` | **new** — one row per fetch attempt: source, started, files, bytes, outcome | A3, B5 |
| `connector_checkpoint` | **new** — per-source cursor (last file, mtime, hash) | A3, B3 |
| `schema_contract` | **new** — versioned field contracts per source | B2 |
| `control_total` | **new** — declared vs ingested count and sum per batch | B6 |
| `ingest_batch` | add `source_id`, `fetch_id` | links a batch to how it arrived |
| `episode` | populate existing `covered_entity_id`; add `hcpcs`, `site_id` | E1, E2, E4 |
| `normalized_record` | add `beacon_id`, `hcpcs`, `site_id`, `payment_reference` | C5, E2, E3, E4 |

Immutability triggers: `connector_fetch` and `control_total` should carry the same `RAISE(ABORT)` treatment as `raw_record`, for the same reason — an audit row you can edit is not an audit row.

### 4.6 Enum extensions (`src/recon/domain/enums.py`)

Additive only. Existing members keep their string values so no stored row changes meaning.

- `SourceSystem` — add `BEACON`, `TPA_VERITY`, `TPA_CRANEWARE`. Keep `TPA_PORTAL` for the existing generic feed; do not repurpose it.
- `KeyType` — add `BEACON_ID` (C5), `COVERED_ENTITY_340B` (E1), `HCPCS` (E2), `PAYMENT_REFERENCE` (E3).
- `QuarantineReason` — `SCHEMA_VERSION_MISMATCH` already exists and is currently unused; B2 finally wires it up. Add `CONTROL_TOTAL_MISMATCH`.
- New `TransportKind` — `LOCAL_DIRECTORY`, `SFTP`, `HTTP_API`.
- New `FetchOutcome` — `SUCCESS`, `NOTHING_NEW`, `AUTH_FAILED`, `TRANSPORT_FAILED`, `CHECKSUM_FAILED`, `NOT_RECEIVED`.

### 4.7 `adapter_version` becomes per-source

`config.ADAPTER_VERSION = "1.0.0"` is currently a single global, written onto every `normalized_record`. Once six vendors have independently versioned mappings, one global version is actively misleading — it says all records were mapped by the same logic when they were not.

Change: keep the constant as the *fabric* version, and add a per-source `mapping_version` on the source registry row, stored alongside it on each normalized record. Low risk, high diagnostic value when a vendor changes a field and only some records are affected.

### 4.8 Where the existing "no ground truth" guarantee has to be restated

`load_feeds` currently takes a feeds directory and *cannot* reach `truth/` even by accident. That structural guarantee is load-bearing — it is why crosswalk accuracy is a measurement rather than a claim — and the refactor in 4.2 threatens it, because a `Transport` is a more general thing than a directory path.

Preserve it explicitly: the `Transport` protocol must not expose arbitrary filesystem access, and a test should assert that no transport implementation can resolve a path under `truth/`. Restating the guarantee in a different place is fine; losing it silently during a refactor is not.

### 4.9 Configuration and environment

`config.py` currently allows path-only environment overrides (`RECON_DATA_DIR`, `RECON_DB_PATH`) — a deliberate restriction that keeps runs reproducible. Connector credentials break that rule by necessity, so keep the two categories separate: paths stay in `config.py`, credentials go through `connectors/secrets.py`, and neither reads the other's namespace. That way the existing "same seed, same bytes" property continues to hold for everything except the network, which was never reproducible anyway.

### 4.10 Determinism, and the one place it genuinely breaks

Every random stream in this build is seeded from a canonical path string hashed with `blake2b`, and `loaded_at` is hardcoded to `1970-01-01T00:00:00Z` specifically so no wall-clock leaks into the data. Real transport breaks that: a fetch happens at a real time, and a retry happens some milliseconds later.

Contain it rather than fight it. Wall-clock is permitted in `connector_fetch` and in logs, and nowhere else — the same discipline already applied to `ingest_batch.loaded_at`. Nothing downstream may read a fetch timestamp, because `received_at` remains the only temporal field the pipeline honours, and the cursor is what makes replay equal to reality.

### 4.11 Testing implications

- Existing 585 tests must keep passing untouched. If any of them needs editing, the seam in 4.2 was cut in the wrong place.
- New transport tests run against local servers (a loopback SFTP daemon, a loopback HTTP mock) — no network, no API key, consistent with the current suite's guarantee.
- The AST test that forbids SQL mutation outside the single write tool now also has to cover `connectors/`; the new package must not write to any table other than its own five.
- Add a test asserting no transport can reach `truth/` (4.8).

### 4.12 Documentation to update

- `DESIGN_NOTE.md` §3 — add the connector boundary as a fifth architectural boundary held by a test.
- `DESIGN_NOTE.md` §9 — item 4 ("connector onboarding as config") moves from future work to built.
- `docs/knowledge_graph.jsonl` — a new wave via `docs/build_knowledge_graph.py`. Never write to the `.jsonl` directly; the builder ends with `open(OUT, "w")` and destroys direct writes silently.
- `docs/decision_ledger.md` — the connector-ready scope decision (§0) is a user decision, not an agent default, and should be recorded as one.

---

## 5. Acceptance — what "done" looks like

The prototype is complete when all of the following hold:

1. A source is added by writing a config row and a mapping module. No edit to `pipeline.py`.
2. Verity and Craneware data arrives over real SFTP from a local server, checkpointed, with control totals reconciled.
3. A claim submits to a local Beacon mock over authenticated HTTP, receives a Beacon ID, and that ID is a working crosswalk key.
4. Four golden claims — paid, rejected, reversed, unmatched — each trace end-to-end with figures asserted at five stages.
5. Deleting every `normalized_record` and replaying from `raw_record` reproduces identical verdicts and ledger figures.
6. A short feed, a schema-mismatched feed, and a missed schedule each raise the right named condition instead of passing silently.
7. The connector readiness report generates from code and states, per source, exactly what is real and what is mocked.
8. The existing 585 tests still pass, unedited.

Items 1, 4, 5, 6 and 8 are the ones worth defending in a walkthrough. They are also the ones that do not depend on a single vendor credential.
