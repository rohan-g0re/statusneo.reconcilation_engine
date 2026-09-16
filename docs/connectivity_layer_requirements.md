# Connectivity Layer — Requirements

**Source:** `docs/Assignment_Doc_2.pdf` (StatusNeo, *Connectivity Assessment*, 11 September 2026)
**Branch:** `connectivity_layer`
**Scope:** connector-ready only. Production hardening is deliberately excluded.
**Status:** requirements only — nothing in this document is built yet.

> **Hard requirement, before anything else is built.** Beacon's and Verity's public documentation is indexed into `docs/vendor_evidence/` first, with verbatim excerpts, URLs and retrieval dates. Every subsequent artefact — mappings, mocks, the orchestrator's new identifiers, even prose in this repository — cites that index or is explicitly tagged `INVENTED`. Nothing about a vendor is written from memory or inference. See **Group V**, which gates every other group.

---

## 0. What this document is, and what it refuses to be

Assignment Doc 2 does not ask for new reconciliation logic. It asks for the layer *in front of* it: the thing that goes and gets the data from six 340B platforms and six adjacent systems, authenticates to each, lands it safely, maps it, and proves a claim can be traced end to end.

Our existing build starts one step later. It assumes six feed files already sit in a directory. Everything downstream of that line — canonical model, crosswalk, episode assembly, verdicts, ledger, queues, agent — is built and tested. Everything upstream does not exist.

### The scope line, and where exactly it is drawn

Doc 2 gives us the cut for free. Its six-step connector build method splits cleanly:

| Doc 2 step | In scope? |
|---|---|
| 1. Access & entitlement | **Yes** |
| 2. Interface contract | **Yes** |
| 3. Adapter build — transport, auth, idempotency, checkpointing, schema validation, raw landing, source-normalized model | **Yes** |
| 4. Business mapping — crosswalk 340B IDs, NDC/HCPCS, claim/Rx/fill IDs, provider/site, payer/PBM, manufacturer, transaction and payment references | **Yes** |
| 5. Golden-claim validation — trace paid, rejected, reversed, unmatched end-to-end; reconcile source counts and control totals | **Yes** |
| 6. Production hardening — retry/replay, observability, DQ quarantine, secrets rotation, runbooks, alerting, lineage, backfill, cutover | **No** |

**Steps 1–5 produce a working connection. Step 6 is the entire delta to production-ready. We build 1–5 and stop.**

That is not a shortcut; it is Doc 2's own boundary, stated in its own table:

> **Working connection** — authorized source data reliably reaches a Shields lower environment, parses successfully, maps to the source-normalized contract, and at least one golden claim can be traced end-to-end.

### One honest amendment to that definition

Both of Doc 2's levels begin with the word *authorized*, and no vendor credential is obtainable for a prototype — Beacon, Verity, Craneware, Macro Helix, PharmaForce and Pillr are contracted enterprise products gated behind a covered entity, with no self-service signup. So the target is:

> **Connector-ready** — the adapter is built to the vendor's published contract, exercised against a mock source that reproduces that contract, wrapped in the same transport / auth / idempotency / checkpointing machinery a real connection would use, and switchable to a live endpoint by changing configuration only.

Defensible on Doc 2's own terms: the document says the interface packs "must be obtained through Beacon Support" and that no public specification exists for four of the six platforms. Building against a mock is the correct response to a problem Doc 2 itself says cannot be solved without vendor onboarding.

### What we are explicitly not building

Named so a reviewer does not have to guess whether it was forgotten. Every row is Doc 2 step 6.

| Deferred | Why it is step 6, not step 3 |
|---|---|
| Retry, backoff, replay orchestration | Doc 2 lists "retry/replay" under production hardening. A single failed fetch may fail loudly; it does not need to self-heal. |
| Connector observability, alerting, missed-schedule detection | "Observability" and "alerting" are step 6. A connector-ready build is run by hand, not on a schedule. |
| DQ quarantine queue and surfacing | Quarantine *capture* already exists and step 3 keeps it. The operator-facing queue is step 6. |
| Secrets rotation | Step 1 requires credentials to exist. Rotating them is step 6. |
| Backfill of history | Explicitly step 6. |
| Runbooks, lineage tooling, cutover readiness | Explicitly step 6. |
| Performance work | Named only in the production-ready definition. |
| Exhaustive edge-case reconciliation (partial payments, every reversal shape) | The production-ready definition's words. Step 2 requires reversal semantics to be *declared*; step 6 requires them to be *exhaustively handled*. |

| Also not building | Why |
|---|---|
| PharmaForce, Macro Helix, Pillr connectors | No public specification for transport *or* payload. Inventing both proves nothing. Doc 2 rates all three Medium or Med-High for exactly this reason. |
| Inmar | Coexistence/control source only — relevant to a real migration, not a prototype with no incumbent. |
| CMS / MTF | MFP dedup matters from 1 Jan 2027. Roadmap, not prototype. |
| ERP / GL write-back | Doc 2 itself says read/report first, write-back only if finance requires it later. |
| Real vendor credentials | Not obtainable. This is the premise of the Week 1–3 access gate. |
| True X12 segment-level parsing | Our EDI-equivalent feeds already exercise the semantics. Fidelity work with no new reconciliation behaviour behind it. |

---

## 1. Scoring the four production patterns

Doc 2, page 9, "Design implication" box, names four patterns the connector fabric must support. Scored against what exists today, at connector-ready scope.

| # | Pattern | Built? | Can build? | What is missing at connector-ready scope |
|---|---|---|---|---|
| 1 | **API / SDK** — Beacon and any TPA with a supported API | **No** | **Yes** | No outbound HTTP client exists anywhere in the codebase. No auth, no token handling, no submission path. The one genuinely new build. |
| 2 | **SFTP / structured files** — Verity, Craneware | **Half** | **Yes** | Raw landing, SHA-256 hashing, file-level idempotency, parsing and quarantine all exist. **Transport does not:** no SFTP client, no key handling, no remote listing, no checkpoint. |
| 3 | **Healthcare EDI / X12 / NCPDP** | **Yes** | Already there | 837, 835, NCPDP-shaped pharmacy events and ACH are generated, parsed, normalized and reconciled end to end. Nothing to do. |
| 4 | **Banking / ERP** | **Half** | **Yes** (bank); ERP out of scope | Bank leg is complete — deposits, trace reassociation, two-hop allocation, amount+date fallback, residuals. ERP write-back is out of scope by decision. |

**Pattern 3 is finished, patterns 2 and 4 need transport bolted onto working machinery, and pattern 1 is the only real build.**

---

## 2. Requirements

Each has an ID, a statement, an acceptance test, and its Doc 2 step. Nineteen requirements, all inside steps 1–5.

### Group V — Vendor documentation index *(Doc 2 step 2 — **HARD PREREQUISITE, BUILT FIRST**)*

**Nothing in Groups A through F may be written until Group V is complete for the vendor it touches.** This is a gate, not a guideline.

The reason is the failure mode this whole build is exposed to. Beacon and Verity are real companies with real published behaviour, and the moment a field name, an auth flow, a status code or a cadence is written from memory or from plausible-sounding inference, the prototype stops being a connector built to a vendor contract and becomes a connector built to our imagination — while looking identical in every demo. Doc 2 makes the same point from the other direction when it warns not to equate "API-driven Beacon" with "public customer API." A mock that is wrong is worse than no mock, because it is confidently wrong.

So the vendor documentation is **indexed into the repository as a first-class source**, before code, and every subsequent artefact cites it.

**V1. Capture every public source into `docs/vendor_evidence/`.**
One file per vendor (`beacon.md`, `verity.md`, and `craneware.md` if built), each entry carrying: a stable evidence id, the source URL, the retrieval date, the **verbatim excerpt**, and a one-line note on what it establishes. Paraphrase goes in the note; the excerpt is never rewritten.
*Acceptance:* every claim made anywhere in this repository about Beacon or Verity behaviour resolves to an evidence id.

**V2. Unreachable sources are recorded as unavailable, never reconstructed from memory.**
Some vendor pages block automated retrieval. When a source cannot be fetched, the entry records the URL, the failure, and the date — and the fact it would have established is treated as **unknown**, exactly as Doc 2 treats "not publicly documented." A model's recollection of a vendor's API is not evidence.
*Acceptance:* no evidence entry exists without either a verbatim excerpt or an explicit unavailability record.

**V3. Three provenance tiers, marked on every field.**
Every field in every mapping, mock and orchestrator change carries one of:
- `SPEC` — taken from a cited vendor source
- `STANDARD` — taken from a public standard (X12, NCPDP, ISO 20022, FHIR), cited to that standard
- `INVENTED` — no public source exists; our own construction, with the reasoning recorded

*Acceptance:* a field with no tier fails the build.

**V4. Citation is enforced by test, not by discipline.**
A machine-readable index (`docs/vendor_evidence/index.jsonl`) lists every evidence id. A test walks the vendor mappings, the mock modules and the orchestrator's new identifiers, and fails on any `SPEC`-tagged field whose evidence id is missing from the index, and on any untagged field.
*Acceptance:* deleting one evidence entry turns the suite red.

**V5. Doc 2 is evidence about vendors, and is cited as such.**
Several facts we rely on — Beacon's two-token model, Read vs Read/Write permission, the published claim templates, Verity's Secure Data Exports and their cadence, the dataset names — come from `docs/Assignment_Doc_2.pdf` rather than from the vendor directly. That is a legitimate source and is indexed like any other, with page numbers, and marked as second-hand.
*Acceptance:* every Doc 2-derived claim cites a page.

> **Consequence for the build order:** Beacon's evidence file gates wave 2, Verity's and Craneware's gate wave 1, and the orchestrator changes in M1 are gated by whichever vendor's identifiers they mint. A vendor with no evidence file has no code.

---

### Group A — Transport *(Doc 2 step 3)*

**A1. Source registry replaces the hardcoded feed list.**
Today `config.FEED_FILENAMES` is a fixed 6-tuple and `pipeline.FEED_SOURCE_SYSTEMS` is a dict literal keyed by filename. A connector fabric needs sources declared as data: id, vendor, transport kind, endpoint, credential reference, mapping version, enabled flag.
*Acceptance:* adding a seventh source is a config row plus a mapping module — no edit to `pipeline.py`.

**A2. A `Transport` protocol with three implementations.**
- `LocalDirectoryTransport` — today's behaviour, kept so the generated-feeds path never changes
- `SftpTransport` — connect, list remote directory, filter by checkpoint, download
- `HttpApiTransport` — authenticated request, response to payload

*Acceptance:* `load_feeds` becomes transport-agnostic; the existing test suite passes with `LocalDirectoryTransport`, and the same suite passes with `SftpTransport` pointed at a local SFTP server.

**A3. Authentication per transport.**
SSH key for SFTP. Beacon's documented two-token model (Access Token plus Private Token) for HTTP. Credentials resolve from environment or a local secrets file — never from source config, never from the repository.
*Acceptance:* no credential value appears anywhere in tracked files; a wrong credential produces a clear named failure, not a stack trace.

**A4. Fetch checkpointing.**
Remember the last successfully fetched file per source (name, modified time, hash). A re-run must not re-pull what it already has.
*Acceptance:* run twice against the same remote; the second run downloads zero bytes and ingests zero records.

**A5. Fetch-level idempotency.**
Record-level and file-level idempotency already exist. Missing is fetch-level: the same remote file seen on a later listing must not be re-downloaded or re-ingested.
*Acceptance:* replaying a captured fetch sequence twice produces identical database state, verified by row counts and a content hash.

> Retry, backoff and rate limiting are **step 6** and are not built. A failed fetch fails loudly and is re-run by hand.

---

### Group B — Contract and validation *(Doc 2 steps 2 and 3)*

**B1. Schema registry.**
Each source declares a versioned field contract. Inbound records are validated against the registered version before adaptation. A mismatch quarantines with `SCHEMA_VERSION_MISMATCH` — an enum member that **already exists** in `domain/enums.py` and is currently unused.
*Acceptance:* changing one field type in a fixture produces a quarantined record with the right reason code and zero normalized records.

**B2. Interface contract documented per source.**
Doc 2 step 2 in artefact form: endpoints or file layout, sample payload, join keys, status codes, **reversal semantics**, cadence. Written down per vendor, versioned with the mapping.
*Acceptance:* every source has a contract file; a test asserts the registered schema matches the documented one.

**B3. Reversal representation declared per vendor.**
Doc 2 flags this on every vendor page and it is the defect that destroys a build quietly: is a reversal a negative row, a replacement record, or a silent delete? Guess wrong and you double-count or lose a rebate, and both look clean in every report afterwards.
*Acceptance:* each vendor mapping declares its reversal representation explicitly, and a golden reversed claim produces exactly one net effect on the ledger — not zero, not two.

> Exhaustive handling of every partial-payment and reversal permutation is **step 6**. Step 2 requires the semantics to be declared and one golden case to work.

---

### Group M — Mock source data *(prerequisite for Groups C and D)*

No vendor credential is obtainable, so every source in this build is served by a local mock. The question that decides the design is whether those mocks are **new generators** or **formatters over existing generated data**. They must be formatters, and the reason is not convenience.

The existing generator is built backwards from a proof. `decision_tree/` enumerates the state space, `sampling.py` picks leaves stratified over verdicts, and the orchestrator turns each leaf into facts — costing it into integer cents through the same pricing module the engine uses, computing every `received_at` from fixed lag windows, and **minting every cross-feed identifier** because those are the values two files must agree on. Each generator then receives a blind slice: `contracts.py` asserts at runtime that no generator can see an episode id, a verdict, or an expected amount, and that assertion is the only reason crosswalk accuracy is a measured 1,349/1,354 rather than a claim.

A new Beacon or Verity generator would have to be handed the answer to produce a consistent story, which deletes that guarantee. A formatter re-dresses a slice that has already been through the blind-slice check, so the guarantee survives intact.

**M1. Beacon and Verity identifiers are minted by the orchestrator, not by the mock.**
Add `beacon_id` to the identifiers the orchestrator mints, in the same place and for the same reason as `trn02` and `allocation_code`: it is a value that two independent systems must agree on, so exactly one component may decide it. Verity-side references (accumulation id, invoice number) get the same treatment.
*Acceptance:* `contracts.py` still passes — no slice carries an episode id, a verdict or an expected amount — and a Beacon ID appearing in a mock response is the same string the orchestrator minted.

**M2. Mocks are formatters over generated data, and hold no domain logic.**
`verity_export.py`, `craneware_export.py` and `beacon_server.py` read what `src/recon/generators/` already produced and re-dress it into vendor shape. None of them may decide whether a claim qualified, what it was worth, or when it arrived.
*Acceptance:* a test asserts the mock modules import no pricing, no decision-tree and no ground-truth module, and contain no arithmetic on money.

**M3. The Beacon response side is a re-dressing, not a new decision.**
Beacon's acknowledgement, validation outcome and rebate status are already present in the generated data as `TPA_MANUFACTURER_DECISION` records. The mock server replays them under Beacon's field names over HTTP; it does not adjudicate.
*Acceptance:* every response the mock returns traces back to a generated record, and the set of outcomes it can produce equals the set already in the feed.

**M4. Invented fields are declared, never inferred silently.**
Verity and Craneware publish dataset and report *names* but no field dictionary, so some payload fields are honest inventions. Each is listed in a `MOCK_FIELDS.md` beside its mapping module, marked real-spec or invented.
*Acceptance:* every field in a vendor mapping appears in that file with a provenance marker; a test fails on an undeclared field.

---

### Group C — Beacon connector *(Doc 2 steps 2–4)*

Beacon is the only bidirectional connector in the entire document — every other source is a pull — and the only one of the six with enough public documentation to build faithfully. Doc 2's Beacon page names the partner model, the two-token auth, Read vs Read/Write permission, claim-level submission history, validation outcomes, Beacon IDs, and **published pharmacy and medical claim data templates**, which is an actual field list.

**C1. Beacon mock server.**
A local HTTP service implementing the documented surface: token exchange, claim submission, submission-status retrieval, validation outcomes, rebate status. Deterministic and seeded.
*Acceptance:* the mock can produce every Beacon-driven outcome our engine already models — accepted, rejected, reversed, paid, unpaid.

**C2. Outbound submission adapter.**
Takes a TPA-qualified claim from our canonical model, maps it into Beacon's pharmacy or medical claim template, submits, and persists the returned Beacon ID against the episode.
*Acceptance:* a golden claim submits, receives a Beacon ID, and that ID appears on the episode timeline.

**C3. Inbound status adapter.**
Pulls acknowledgements, validation outcomes, rebate status and payment references; maps them onto the existing `TPA_MANUFACTURER_DECISION` and `REBATE_BATCH` record kinds.
*Acceptance:* a rejected submission produces `REBATE_REJECTED` with the vendor's reason preserved verbatim, not paraphrased.

**C4. Submission ownership as configuration.**
Doc 2's PharmaForce page defines two modes: **mode A**, Shields submits directly; **mode B**, the TPA submits and Shields consumes outcomes. A per-covered-entity decision, and getting it wrong means the same claim is submitted twice.
*Acceptance:* with mode B configured the outbound path is not merely skipped but **unreachable** — a test asserts no submission call can be constructed.

**C5. Beacon ID as a first-class crosswalk key.**
New `KeyType.BEACON_ID`. It joins our episode to the manufacturer's rebate decision, and that decision to the payment reference on the bank leg.
*Acceptance:* a rebate payment carrying only a Beacon ID resolves to the correct episode.

---

### Group D — Secure-file connector *(Doc 2 steps 2–4)*

Doc 2 recommends treating Verity and Craneware as one pattern — its own words: "both can share the same secure-file connector framework." Verity is the committed baseline; Craneware is the second file-based pattern after it.

**D1. One scheduled-SFTP connector serving both vendors.**
Same transport, same checkpointing. Vendor difference is confined to the field mapping.
*Acceptance:* Verity and Craneware differ by a config row and a mapping module, nothing else.

**D2. Verity export mock.**
Verity publicly documents *which datasets* it exports — accumulations, contract-pharmacy claims backing, invoices, matches and split transactions, unmatched claims — and that delivery is encrypted SFTP on a daily/weekly/monthly cadence. It does **not** publish a field dictionary, so transport is real and payload is an honest invention.
*Acceptance:* every invented field is listed in a `MOCK_FIELDS.md` beside the mapping module, so a reviewer sees exactly what was assumed.

**D3. Craneware export mock.**
Same treatment, using the documented report names: Claims, Ordering Problems, Unreplenished Costs, Audit, Bulk Dispensations.

---

### Group E — Business mapping *(Doc 2 step 4)*

Step 4 is the part already built to a depth Doc 2 only sketches in one line — canonical model, eight crosswalk key types, the natural key, `CLM01` for 837↔835, `TRN02` for 835↔bank, parking instead of guessing. These are the gaps against Doc 2's own crosswalk list.

**E1. 340B ID as a real field and key.**
`_create_episode` currently writes `covered_entity_id=None` — the column exists and is never populated. Doc 2 lists 340B IDs first, and Beacon permissions are granted *per 340B ID*.
*Acceptance:* episodes carry a covered entity; a TPA record for the wrong covered entity does not resolve.

**E2. HCPCS alongside NDC.**
Doc 2's list reads "NDC/HCPCS." Medical-benefit drugs are billed by HCPCS J-code; we model NDC only.
*Acceptance:* a medical record carrying only a J-code resolves to the right episode.

**E3. Manufacturer and payment references as crosswalk keys.**
Named explicitly in step 4. Today the manufacturer is a reference entity, not a key, and the payment reference exists only as `TRN02` / `allocation_code`.
*Acceptance:* a manufacturer rebate payment resolves by its own payment reference.

**E4. Site / contract-pharmacy identity.**
Doc 2 says "provider/site," and every TPA page mentions site or contract pharmacy. We key on `pharmacy_npi` alone, which collapses a health system's contract-pharmacy network into one identity.
*Acceptance:* two contract pharmacies under the same NPI-holding entity are distinguishable.

**E5. Source-of-truth boundary enforced in data.**
Doc 2's page-2 table assigns authority per field: TPA owns qualification, Beacon owns rebate status, bank owns settled cash, Shields owns only the consolidated picture. Records carry a source system today, but nothing asserts which source may set which field.
*Acceptance:* a TPA record attempting to set rebate-payment status is rejected, not silently accepted.

---

### Group F — Golden-claim validation *(Doc 2 step 5)*

This group *is* the definition of done. Doc 2's working-connection bar is "at least one golden claim can be traced end-to-end"; step 5 raises it to four archetypes plus control totals.

**F1. Four golden claims traced end to end.**
Doc 2 names them: **paid, rejected, reversed, unmatched**. For each, a test following one claim from the source file through raw, normalized, episode, verdict and ledger, asserting figures at every hop.
*Acceptance:* four tests, one per archetype, each asserting at five stages.

**F2. Source counts and control totals reconciled.**
Doc 2 step 5's second clause. Compare the vendor's declared count and sum against what was ingested — silent truncation is the failure mode that parses cleanly.
*Acceptance:* a feed whose trailer declares 500 records when 450 arrived fails the batch and records the discrepancy, rather than ingesting 450 successfully.

**F3. Connector readiness report.**
One generated document per source: transport implemented, auth implemented, schema registered, mock fidelity (real spec vs invented), golden claims traced, control totals reconciled, and what remains vendor-blocked. Our version of Doc 2's Week 3 gate evidence table.
*Acceptance:* the report generates from code and config, never hand-maintained.

---

## 3. Build order

Mirrors Doc 2's own wave structure, which sequences by proven-path confidence. **Documentation first, then data, then the system** — so every wave has something concrete to test against before the next one starts.

| Wave | Contents | Rationale |
|---|---|---|
| **0 — Evidence** | V1–V5 | **Hard gate.** Index Beacon's, Verity's and Craneware's public documentation into `docs/vendor_evidence/` with verbatim excerpts, URLs and retrieval dates, plus the citation-enforcing test. No vendor code exists before its evidence file does. |
| **1 — Framework** | A1, A2, A3, A5, B1, B2 | Doc 2's Weeks 2–4 row: the common gateway, credentials, schema registry and idempotency **before** any vendor adapter. Testable on the existing local-directory path alone. |
| **2 — Mock data** | M1, M2, M4 | One orchestrator change to mint `beacon_id` and the Verity references, then the formatters — every field tagged `SPEC`, `STANDARD` or `INVENTED` against wave 0. Must precede waves 3 and 4: a connector with nothing to connect to cannot be tested. |
| **3 — File pattern** | A4, D1, D2, D3, B3 | Verity and Craneware are the highest-confidence sources in the whole assessment. SFTP is boring and proven. |
| **4 — API pattern** | M3, C1, C2, C3, C4, C5 | Beacon. The only outbound path, and the only vendor with published field templates. |
| **5 — Mapping** | E1–E5 | Cheap once real sources exist to exercise them; expensive to retrofit after. |
| **6 — Proof** | F1, F2, F3 | Golden claims, control totals, readiness report. This wave is what makes the build claimable. |

Wave 3 before wave 4 is Doc 2's own recommendation: prove the fabric on the easy transport first, so that when Beacon's harder auth model is being debugged, the framework underneath is already known-good.

### The rule between waves

**Each wave ends green before the next begins.** Every wave delivers something runnable and something asserted — wave 0 a test that fails when an evidence entry is deleted, wave 1 the existing suite passing through the new transport seam, wave 2 a mock file that parses, wave 3 a real SFTP fetch, wave 4 a submitted claim with a Beacon ID, wave 5 a resolving crosswalk key, wave 6 four traced claims. A wave that cannot be demonstrated on its own has been cut at the wrong boundary.

This matters more than usual here because the build is against mocks. The only thing standing between a mock and a fiction is that each layer was checked when it was written, against evidence that was captured before it.

---

## 4. Structural changes to the existing codebase

**Headline: nothing is rewritten. The connector layer is almost entirely additive.**

Not luck. The existing design already made the two decisions that matter — ingest is event-driven rather than a scan, and an inbound document carries its own lookup keys — so a record arriving over SFTP or HTTP is indistinguishable, downstream, from one read off disk. What follows is mostly *new modules beside existing ones*, plus one honest refactor.

### 4.1 What does not change at all

These stay byte-for-byte, and the existing tests are the proof:

- `src/recon/engine/` — verdicts, dispositions, dimensions, run. The engine is a pure function of `(episode, cursor)`. It does not know where records came from and must not learn.
- `src/recon/api/` and `web/` — the API projects, it does not calculate. New sources produce more episodes, not different ones.
- `src/recon/agents/` — nine tools over the same dossier. **One caution:** the agent layer's grounding rules derive from `docs/knowledge_graph.jsonl`, so any new entity added there must be grepped against `src/recon/agents/` before a rebuild.
- `src/recon/crosswalk/keys.py` — key *construction* is unchanged. New key types are added to the enum; existing construction rules are untouched.
- `src/recon/money.py`, `src/recon/rng.py`, `src/recon/reference/` — unaffected.
- `decision_tree/` — the state space concerns what can happen to a claim, not how the data arrived.

### 4.2 The one genuine refactor: `load_feeds` grows a transport seam

The only place where existing code changes shape rather than gaining a neighbour.

Today (`src/recon/ingest/pipeline.py:135`):

```python
def load_feeds(conn, feeds_dir: Path) -> IngestStats:
    for feed_file in config.FEED_FILENAMES:
        path = feeds_dir / feed_file
        ...
```

Two hardcodings sit in that loop: `config.FEED_FILENAMES` (a fixed 6-tuple) and `FEED_SOURCE_SYSTEMS` (a dict literal keyed by filename, `pipeline.py:77`). Both assume a local directory containing exactly six known files.

Make the *source* the parameter instead of the directory:

```python
def load_from_sources(conn, sources: Sequence[Source]) -> IngestStats:
    for source in sources:
        for document in source.transport.fetch(source):
            ...
```

with `LocalDirectoryTransport` preserving today's behaviour exactly. Keep the current signature as a thin wrapper rather than editing every call site:

```python
def load_feeds(conn, feeds_dir: Path) -> IngestStats:   # existing callers untouched
    return load_from_sources(conn, local_sources(feeds_dir))
```

**Everything below `load_feeds` — `ingest()`, `_process_raw_record`, `_attach`, `_park`, `_recheck_parked`, the allocator — is untouched.** They operate on `raw_record` rows, and a row is a row regardless of how it arrived. That is the payoff of the original event-driven decision.

### 4.3 New package: `src/recon/connectors/`

The fabric is a new sibling of `ingest/`, not a rewrite of it.

```
src/recon/connectors/
  __init__.py
  registry.py        # A1  source declarations
  transport.py       # A2  Transport protocol + LocalDirectoryTransport
  sftp.py            # A2/A4  SftpTransport, remote listing, checkpointing
  http.py            # A2  HttpApiTransport, two-token auth
  credentials.py     # A3  credential resolution from env / secrets file
  schema_registry.py # B1  versioned field contracts, validation before adaptation
  checkpoint.py      # A4/A5  per-source fetch cursor
  control_totals.py  # F2  declared vs ingested reconciliation
  contracts/         # B2  one interface-contract document per source
  vendors/
    beacon.py        # C2/C3
    verity.py        # D2
    craneware.py     # D3
```

Why a new package rather than extending `ingest/`: `ingest/` answers "what does this record mean and what does it join to." `connectors/` answers "how do I get it and am I allowed to." Those fail differently, are tested differently, and only one of them touches the network. Keeping the seam visible is also what keeps the existing AST and grep tests meaningful.

No `observability.py`, no `retry.py` — both are step 6.

### 4.4 New package: `src/recon/mocks/`

Mock sources belong beside the generators, not inside the connectors they serve — a connector shipping its own fake is a connector nobody can prove is real.

```
src/recon/mocks/
  beacon_server.py    # C1  local HTTP service implementing the documented Beacon surface
  sftp_server.py      # D1  local SFTP endpoint serving generated exports
  verity_export.py    # D2  writes Verity-shaped files from existing generated episodes
  craneware_export.py # D3  ditto
```

These consume what `src/recon/generators/` already produces. **No new synthetic data engine, and no new generators** — see Group M for why that distinction is load-bearing rather than stylistic.

The one change inside the existing generator package is in `orchestrator.py`: it gains `beacon_id` and the Verity-side accumulation and invoice references to the identifiers it mints. That is the correct home for them — the orchestrator already mints `trn02`, `allocation_code` and every natural key, precisely because a cross-feed identifier is a value two independent systems must agree on, so exactly one component may decide it. A mock that minted its own Beacon ID would be inventing a fact the rest of the system then has to accept on faith.

Everything else in `generators/` is untouched, including `contracts.py`, whose runtime blind-slice assertion is what makes the crosswalk a measurement instead of a claim. If a mock ever needs data that assertion forbids, the mock is wrong, not the assertion.

### 4.5 New directory: `docs/vendor_evidence/`

Built first, before any code. Not documentation about the build — an input to it.

```
docs/vendor_evidence/
  index.jsonl        # V4  machine-readable: evidence id, vendor, url, retrieved, status
  beacon.md          # V1  verbatim excerpts, one entry per evidence id
  verity.md          # V1
  craneware.md       # V1  (only if wave 3 includes Craneware)
  assignment_doc2.md # V5  second-hand claims lifted from Doc 2, cited by page
```

`index.jsonl` is machine-readable for the same reason `knowledge_graph.jsonl` is: a test has to walk it. The markdown files are for a human reading the excerpts; the JSONL is what makes V4 enforceable. Unlike the knowledge graph, this one is **hand-written and never generated**, so there is no builder that can silently destroy an edit.

Each vendor mapping module then carries a provenance table beside it — the `MOCK_FIELDS.md` of M4 — listing every field as `SPEC` with an evidence id, `STANDARD` with a standard reference, or `INVENTED` with the reasoning. The test in V4 joins the two.

### 4.6 Schema additions (`src/recon/db/schema.sql`)

All additive. No existing column changes type; no existing table loses a constraint.

| Table | Change | For |
|---|---|---|
| `connector_source` | **new** — registered sources and their config | A1 |
| `connector_checkpoint` | **new** — per-source cursor (last file, mtime, hash) | A4, A5 |
| `schema_contract` | **new** — versioned field contracts per source | B1 |
| `control_total` | **new** — declared vs ingested count and sum per batch | F2 |
| `ingest_batch` | add `source_id` | links a batch to the source that produced it |
| `episode` | populate existing `covered_entity_id`; add `hcpcs`, `site_id` | E1, E2, E4 |
| `normalized_record` | add `beacon_id`, `hcpcs`, `site_id`, `payment_reference` | C5, E2, E3, E4 |

No `connector_fetch` table — per-fetch history is observability, which is step 6. The checkpoint is the only fetch state connector-ready needs.

`control_total` should carry the same `RAISE(ABORT)` immutability treatment as `raw_record`, for the same reason: a control total you can edit is not a control total.

### 4.7 Enum extensions (`src/recon/domain/enums.py`)

Additive only. Existing members keep their string values, so no stored row changes meaning.

- `SourceSystem` — add `BEACON`, `TPA_VERITY`, `TPA_CRANEWARE`. Keep `TPA_PORTAL` for the existing generic feed; do not repurpose it.
- `KeyType` — add `BEACON_ID` (C5), `COVERED_ENTITY_340B` (E1), `HCPCS` (E2), `PAYMENT_REFERENCE` (E3).
- `QuarantineReason` — `SCHEMA_VERSION_MISMATCH` already exists and is unused; B1 finally wires it up. Add `CONTROL_TOTAL_MISMATCH`.
- New `TransportKind` — `LOCAL_DIRECTORY`, `SFTP`, `HTTP_API`.

No `FetchOutcome` enum — that exists to populate a fetch-history table we are not building.

### 4.8 `adapter_version` becomes per-source

`config.ADAPTER_VERSION = "1.0.0"` is a single global written onto every `normalized_record`. Once several vendors have independently versioned mappings, one global version is actively misleading — it claims all records were mapped by the same logic when they were not.

Keep the constant as the *fabric* version, and add a per-source `mapping_version` on the registry row, stored alongside it on each normalized record. Low risk, high diagnostic value when one vendor changes a field.

### 4.9 The "no ground truth" guarantee has to be restated

`load_feeds` currently takes a feeds directory and *cannot* reach `truth/` even by accident. That structural guarantee is load-bearing — it is why crosswalk accuracy is a measurement rather than a claim — and the refactor in 4.2 threatens it, because a `Transport` is a more general thing than a directory path.

Preserve it explicitly: the `Transport` protocol must not expose arbitrary filesystem access, and a test must assert that no transport implementation can resolve a path under `truth/`. Restating the guarantee elsewhere is fine; losing it silently during a refactor is not.

### 4.10 Configuration and credentials stay in separate namespaces

`config.py` allows path-only environment overrides (`RECON_DATA_DIR`, `RECON_DB_PATH`) — a deliberate restriction that keeps runs reproducible. Credentials break that rule by necessity, so keep the categories apart: paths stay in `config.py`, credentials go through `connectors/credentials.py`, and neither reads the other's namespace. The existing "same seed, same bytes" property then continues to hold for everything except the network, which was never reproducible anyway.

### 4.11 Determinism

Every random stream is seeded from a canonical path string hashed with `blake2b`, and `ingest_batch.loaded_at` is hardcoded to `1970-01-01T00:00:00Z` so no wall-clock leaks into the data. Real transport introduces a real clock.

Contain it: wall-clock is permitted in `connector_checkpoint` and in logs, nowhere else. Nothing downstream may read a checkpoint timestamp, because `received_at` remains the only temporal field the pipeline honours, and the cursor is what makes replay equal reality.

### 4.12 Testing implications

- The existing 585 tests must keep passing **unedited**. If any needs changing, the seam in 4.2 was cut in the wrong place.
- New transport tests run against local servers — a loopback SFTP daemon and a loopback HTTP mock — so the suite's no-network, no-API-key guarantee holds.
- The AST test forbidding SQL mutation outside the single write tool now also covers `connectors/`; the new package must not write to any table other than its own four.
- Add a test asserting no transport can reach `truth/` (4.9).
- Add the citation test (V4): walk every vendor mapping, mock module and orchestrator-minted identifier; fail on an untagged field, and on any `SPEC` tag whose evidence id is absent from `docs/vendor_evidence/index.jsonl`. This test is written in wave 0, before the code it will police — it should be red until the first evidence entry exists, and that is the correct starting state.

### 4.13 Documentation to update

- `DESIGN_NOTE.md` §3 — add the connector boundary as a fifth architectural boundary held by a test.
- `DESIGN_NOTE.md` §9 — item 4 ("connector onboarding as config") moves from future work to built.
- `docs/knowledge_graph.jsonl` — a new wave via `docs/build_knowledge_graph.py`. Never write to the `.jsonl` directly; the builder ends with `open(OUT, "w")` and destroys direct writes silently.
- `docs/decision_ledger.md` — the connector-ready scope decision (§0) is a **user decision**, not an agent default, and must be recorded as one.

---

## 5. Acceptance — what "done" looks like

Connector-ready is reached when all of the following hold:

0. **Every Beacon and Verity claim in this repository — in code, comments, mocks, mappings and documents — resolves to a cited evidence entry, or is tagged `INVENTED`.** Deleting one evidence entry turns the suite red.
1. A source is added by writing a config row and a mapping module. No edit to `pipeline.py`.
2. Verity and Craneware data arrives over real SFTP from a local server, checkpointed, with control totals reconciled.
3. A claim submits to a local Beacon mock over authenticated HTTP, receives a Beacon ID, and that ID is a working crosswalk key.
4. Four golden claims — paid, rejected, reversed, unmatched — each trace end to end with figures asserted at five stages.
5. A short feed and a schema-mismatched feed each fail with the right named condition instead of passing silently.
6. The connector readiness report generates from code and states, per source, exactly what is real and what is mocked.
7. Every mock is a formatter: `contracts.py` still passes unedited, and no mock module contains money arithmetic or a qualification decision.
8. The existing 585 tests still pass, unedited.

Items 1, 4, 5, 7 and 8 are the ones worth defending in a walkthrough, and none of them depends on a vendor credential.

**Explicitly not part of acceptance**, because it is step 6: scheduled unattended operation, alerting, retry-on-failure, backfill, quarantine queue, cutover parity against Inmar.
