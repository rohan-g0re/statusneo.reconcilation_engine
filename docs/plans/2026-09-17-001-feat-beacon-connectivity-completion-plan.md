---
status: active
created: 2026-09-17
branch: connectivity_layer
origin: docs/Assignment_Doc_2.pdf (page 3, Beacon) and docs/connectivity_layer_requirements.md
---

# feat: Complete the Beacon connector against its published documentation

## Problem frame

Assignment Doc 2 page 3 makes two demands this build has not met.

**"Use Beacon's published pharmacy / medical data templates."** We did not. The
Beacon support site returns HTTP 403 to automated fetches, so the connector was
written against Doc 2's second-hand summary. 44 of 57 Beacon field names in this
repository are invented. The published templates have now been retrieved
verbatim — the site blocks bot user-agents on the HTML but not on `robots.txt`,
which advertises the sitemap, and a text-extraction proxy returns real page text.

**"Persist Beacon ID against Shields Claim Financial Episode."** We do not.
`adapters._DISPATCH` has no `SourceSystem.BEACON` entry, zero of 443 normalized
records carry a `beacon_id`, and `grep -rni beacon src/recon/engine/` returns
nothing. No Beacon response can change anything.

Underneath both sits a third problem that explains why neither was caught:
`from_canonical` has **zero call sites**. `TpaQualifiedClaim` is never
constructed anywhere in `src/`, `tests/` or `scripts/`. The entire outbound
mapping is dead code, so a submission body that 404s against our own mock server
has sat there unnoticed.

## Scope

**In scope.** The four items above, plus the defects found while establishing
them. Bounded by what Beacon actually publishes.

**Out of scope, and stated so it is a decision rather than an omission:**

- An installable Beacon SDK. None exists — PyPI, npm and GitHub return only name
  collisions (Ethereum beacon chain, GA4GH genomics Beacon, Bluetooth beacons).
  Doc 2 says the same: *"SDK package, API reference, rate limits, versioning
  policy, non-production endpoint and support SLA are not exposed in full public
  documentation and must be obtained through Beacon Support."* Raw code against
  the documented shape was the only option and remains it.
- Response shapes. Beacon publishes **submission** templates only. The
  acknowledgement, validation outcome, rebate status and payment reference have
  no published shape anywhere in the 47-URL sitemap. They stay `INVENTED` and
  stay labelled `INVENTED`.
- Doc 2 step 6 — retry, backoff, rate limiting, operational runbooks. Excluded
  from the start.
- A verdict dimension for Beacon validation `REJECTED` / `REVERSED`. See Risks.

## Settled decisions

Carried forward so no implementation unit re-litigates them.

| # | Decision | Reason |
|---|---|---|
| D1 | Beacon is **Second Sight Solutions**, not Kalderos | Doc 2 p3's "Public evidence reviewed" footer links all point to `support.beaconchannelmanagement.com`. Kalderos sells Truzo and 340B Pay and was acquired by Model N — a different company, and a competitor |
| D2 | New evidence ids `BEACON-014`/`015` at tier `RETRIEVED_VIA_PROXY`; `BEACON-001`/`002` stay `UNAVAILABLE` | Add rather than rewrite — the direct fetch genuinely still 403s, and that fact has not changed. The new tier records that the text arrived through a third party rather than from the vendor's host |
| D3 | Published field names go in **verbatim**, spaces and hyphens intact | `340B ID`, `Rx Number`, `NDC-11`, `HCPCS Code Modifier`. The instruction is the documentation's format, not our transliteration of it |
| D4 | Submission fields get published names; **response fields do not** | Beacon publishes no response shape. Dressing a response in published spellings would imply it published one |
| D5 | `from_canonical` reads a **merged mapping** of the normalized row plus its episode row | Six required Beacon fields live on `episode` and not on `normalized_record`. It already takes a bare `Mapping`, so no signature change — and with no callers, the caller is written correctly the first time |
| D6 | Medical: our one NPI maps to `Service Provider ID`; `Rendering Physician ID` emits null | Beacon wants two and we hold one. We cannot distinguish them and will not pretend to |
| D7 | Pharmacy `Quantity Dispensed` gets a value; medical `Quantity` emits null | `quantity_milli ÷ 1000` is unambiguous under NCPDP. Beacon conditions the medical quantity on a CMS/NCPDP billing-unit rule and publishes no table for it — a wrong number on a rebate submission is worse than a null |
| D8 | **Never emit Beacon's published sentinels** (`999999`, `CASH`, `NONE`) | Each encodes a clinical fact — that the patient was a cash payer — which we do not hold |
| D9 | Envelope stays flat, not nested | The spelling split already separates them for free: Beacon's fields are Title Case, ours stay snake_case. Nesting would invent a structure Beacon never published |
| D10 | Beacon inbound arrives over **files**, not HTTP | The demo must run with no network. `mocks/beacon_server.py` is instantiated in exactly one place in the repo — a test — so wiring the demo to it would give the demo a port, a startup and a failure mode, for a build whose defining property is that the same seed produces the same bytes |

## Implementation units

### Landed — commit `69c0e23`

- [x] **The 340B TPA vendor leg.** `SecureFileMapping.received_at_column`,
      `PayloadFormat.VENDOR_CSV`, `_adapt_vendor_export`, `_DISPATCH` entries for
      `TPA_VERITY` / `TPA_CRANEWARE`, `MissingDeliveryStampError`, and
      `coverage.write_all` wired into `build_dataset`.
      Tests: [tests/test_vendor_ingest.py](tests/test_vendor_ingest.py) (15),
      [tests/test_vendor_connector_leg.py](tests/test_vendor_connector_leg.py) (13),
      [tests/test_vendor_epoch_guard.py](tests/test_vendor_epoch_guard.py) (6).
- [x] **The architecture diagram**, appended to the one living canvas: 368 → 439
      elements, zero removed, id-subset verified inside the drawing script.

### In flight

- [ ] **U1 — Evidence layer.** `RETRIEVED_VIA_PROXY` tier; `BEACON-014`/`015`;
      drop the Kalderos attribution from `beacon.md` and `README.md`; widen
      `_FIELD_ROW` so spaced field names stop being *silently skipped*.
      Files: `docs/vendor_evidence/{index.jsonl,beacon.md,README.md}`,
      [tests/test_vendor_evidence.py](tests/test_vendor_evidence.py).

- [ ] **U2 — Submission rename.** Atomic across `beacon_payloads.submission`,
      `beacon_server._submission_key`, `beacon.py::_template_body` and the
      provenance ledger. Includes the date fix (three producing sites plus
      `_acknowledgement_natural_key` consuming) and reconciling the connector's
      `/v1/claims/{template}` paths to the mock's `/claims` router.
      Files: `src/recon/mocks/{beacon_payloads,beacon_server}.py`,
      `src/recon/connectors/vendors/beacon.py`, `src/recon/mocks/MOCK_FIELDS.md`.

- [ ] **U3 — Beacon → fabric → episode.** Seven sequenced phases, each gating the
      next. Vocabulary (three `RecordKind` members, one `EvidenceRole`, schema
      CHECK, authority domains, `_ROLE_BY_KIND`), then `_adapt_beacon`, then
      `Source.received_at_field`, then a no-regression gate, then enabling the
      load.
      Files: `src/recon/domain/enums.py`, `src/recon/db/schema.sql`,
      `src/recon/connectors/authority.py`, `src/recon/engine/run.py`,
      `src/recon/ingest/{adapters,pipeline}.py`,
      `src/recon/connectors/registry.py`, `src/recon/api/app.py`.
      Tests: `tests/test_beacon_ingest.py`, `tests/test_beacon_connector_leg.py`,
      and the missing CHECK-vs-enum lockstep test in
      [tests/test_decisions.py](tests/test_decisions.py).

### Remaining

- [ ] **U4 — The round-trip test `beacon.py` has claimed since it was written.**
      Its docstring says `_template_body` and `beacon_payloads.submission` *"are
      kept in step by a round-trip test rather than by an import."* That test does
      not exist — `_template_body` has zero references in `tests/`. Nothing keeps
      them in step, which is why the wire formats diverged.
      File: [tests/test_api_pattern.py](tests/test_api_pattern.py).

      Test scenarios, each buying a distinct guarantee:
      1. Key sets equal in **both** directions per template — equality, not
         subset, so a field added to either side alone fails.
      2. The 11 and 13 published names pinned as literals in the test file, so a
         rename inside `beacon.py` fails rather than drifting. The test becomes
         the second copy of the contract.
      3. Every published field is present **even when its value is null** —
         otherwise the five unpopulatable fields get quietly dropped and the gap
         stops being visible on the wire.
      4. The date is wire format: build a claim whose `date_of_service` is ISO and
         assert the body's date matches `^\d{8}$`. This is the single assertion
         that would have caught the defect.
      5. `beacon_server._submission_key(beacon._template_body(claim, T))` is
         present in the server's index — proves the two agree by actually joining
         on them, not by comparing key sets.
      6. POST a connector-built body, feed the response to `beacon.acknowledged`,
         assert it returns rather than raising.

      Build the claim via `from_canonical` on a real canonical-shaped dict, not by
      hand — the defect lives in that path and a hand-built claim would hide it.

- [ ] **U5 — Knowledge graph wave.** A **new** entity `"Beacon inbound leg"`, with
      a relation from `"340B feed"` to it. Add no observation to any routed
      entity — see Risks.

## Risks

**The agent eval fixtures are keyed on a request digest.** `ReplayClient` keys
every recorded response on `request_digest(model, messages, tools, tool_choice)`.
Any entity named in `grounding.py`'s ROUTES tables becomes a Clause, the clause
index is rendered into the proposer prompt, and `MAX_CLAUSES` caps it at 72 — so
an added clause can silently push another entity's clauses out of the window. One
observation on a routed entity invalidates all nine traces, and re-recording needs
a live API key. Wave 14 turned 23 passed into 4 failed with no code change.
Mitigation: new entity, new relation, nothing touched.

**Beacon validation `REJECTED` / `REVERSED` has no verdict code.** A claim Beacon
refused at validation never reaches the manufacturer, so `_manufacturer_status`
returns `None` and `classify_rebate` returns C-05, *"request submitted,
manufacturer pending"*, forever. Nine payloads are `REJECTED` and four
`REVERSED`. Closing it needs a new `Dimensions` field, a new branch in
`_derive_rebate` and `classify_rebate`, and either a new row in
`docs/reconciliation_state_space.md` or an argument for folding it into C-07 —
plus a recount of the configuration space that document tallies. Named here
rather than discovered later.

**`rebate_status` moves verdicts; the other three do not.** It maps to
`TPA_MANUFACTURER_DECISION`, which *is* bucketed by `dimensions._KIND_BUCKETS`.
Enabled in a separate step so the delta is attributable.

**Two committed diagram scripts rebuild the elements list rather than extending
it** — `draw_connectivity_architecture.py:316` uses the identical `kept + fresh`
pattern that deleted 122 elements. Both are one careless run from repeating it.

## Verification

1. `uv run --quiet pytest -q` — full suite.
2. `POST /api/regenerate` — dataset rebuilds clean, 60 episodes, 26 files.
3. A Beacon ID reaches an episode:
   `SELECT key_value FROM crosswalk_key WHERE episode_id = ? AND key_type = 'BEACON_ID'`.
4. Every `SPEC`-tagged Beacon field cites a retrieved evidence id.

**Known failure, pre-existing, not to be chased:**
`tests/test_query_plans.py::test_latest_verdict_walks_the_index_not_the_table`.
Verified pre-existing by stashing and re-running. SQLite picks
`sqlite_autoindex_verdict_1` over `ix_verdict_latest`; both are index seeks so
nothing scans, but the assertion names an index rather than the property it
cares about.
