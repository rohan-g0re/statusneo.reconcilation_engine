---
status: complete
created: 2026-09-18
completed: 2026-09-21
branch: connectivity_layer
origin: docs/Assignment_Doc_2.pdf, and the session that built the Beacon inbound leg
supersedes_scope_of: docs/plans/2026-09-17-001-feat-beacon-connectivity-completion-plan.md
---

# feat: Ingest vendor-shaped TPA data, and give the repository a Beacon surface

Two steps, in order. Step 1 is the substantive one and Step 2 depends on nothing in
Step 1, but is smaller and is deliberately second because it is tidying a seam that
Step 1 will stretch.

---

## Where this came from

The build currently ingests `tpa_340b_events.jsonl` — a generic TPA feed whose shape
we invented under Doc 1. Verity's five CSVs and Craneware's five reports exist, are
contract-checked, and are **derived from that same feed** by formatters in
`src/recon/mocks/`. So the relationship is backwards from production: the synthetic
generic feed is the source, and the vendor files are its costumes.

In production there is no generic TPA feed. There is Verity's export, or Craneware's,
or a sixth TPA's. The engine should be running on data shaped the way a vendor ships
it, and the fact that it currently is not is the gap this plan closes.

**One thing this plan does not claim.** Inverting the source moves us from
*generic-assumed* to *vendor-shaped-assumed*. It does not move us to real data. Of the
190 Verity and Craneware field names in this repository, **0 are SPEC, 0 are STANDARD,
190 are INVENTED** — neither vendor publishes a single column, and a web audit
confirmed there is no 403 wall hiding one: every page returns 200 and the fields are
simply not in them. Verity now says the gate out loud — *"Current Verity customers can
access the updated document via our Help Center, or by reaching out to their Account
Manager."*

What inversion genuinely proves is worth stating precisely, because it is a strong
claim and an overstated version of it would not survive a question:

> The reconciliation engine runs on data shaped the way a vendor ships it, and
> onboarding a sixth TPA is a config row plus a mapping module — not an engine change.

---

## Step 1 — Vendor-shaped ingest, verified through the whole chain

### 1.0 The blocker, first

**`verity_invoices` has no adapter and cannot be inverted around.** It is the rebate
money — `invoice_line_amount` against `batch_total_amount` — and it is deliberately
excluded from `adapters._QUALIFICATION_DATASETS` because the batch/line semantics were
never settled: how one invoice row relates to the batch above it is a decision no
evidence we hold answers. Today it reads, contract-checks, and refuses loudly.

Invert the source without it and the rebate payment path disappears entirely. So this
is unit one, not a follow-up.

**What it has to decide.** Every payload repeats `batch_total_amount`. One record per
row means each line claims the whole batch total, which is the `REBATE_BATCH`
fragmentation problem `connectors/vendors/beacon.py` already names. And the fold that
fixes it needs the whole file, which the row-at-a-time ingest path does not have.

**Measured fact that constrains the answer:** for 1 of 20 payment references
(`RBT-20251031-44202`) Beacon's lines sum to 26,500.00 against a declared
`batch_total_amount` of 30,094.00. **Never assume the lines sum to the batch total, and
never sum it.**

Files: `src/recon/ingest/adapters.py`, `src/recon/connectors/vendors/verity.py`.
Tests: `tests/test_vendor_invoices.py` (new).

Test scenarios:
- A paid dispense's invoice row lands with the line amount and not the batch total.
- Two lines under one `invoice_number` produce two records, not one, and not a batch
  carrying the total twice.
- The declared batch total is carried and **not** asserted to equal the sum of lines —
  assert explicitly that a disagreeing file still ingests, because it does in the data.
- Money reaches the episode exactly once. This is the C-14 guard in a new place: the
  340B feed's `REBATE_DISPENSE_LINE` already counts this payment, so whatever
  `verity_invoices` becomes must not let the engine sum it twice.

### 1.1 Make the vendor exports the TPA ingest source

A profile or mode in which `load_feeds` supplies the five non-TPA feeds and the TPA
events come from Verity or from Craneware instead of from `tpa_340b_events.jsonl`.

**Not a deletion.** The generic feed keeps being generated — the vendor formatters read
it, so removing it removes their input. It stops being *ingested*.

**One switch, two vendors, run separately.** The point is not "the engine reads vendor
data"; it is "the engine reads *either* vendor's data and reaches the same answer." A
mode that blends both proves neither.

Files: `src/recon/api/app.py`, `src/recon/connectors/registry.py`, `src/recon/config.py`.

### 1.2 Prove verdict parity through the vendor door

The acceptance test of the whole plan.

Build three databases — generic feed, Verity-sourced, Craneware-sourced — and compare
**episode identity and the verdict table**, not row counts. Use the digest method that
already proved the Beacon leg moved nothing:

```
WITH_BEACON: verdicts=521 episodes=60 digest=50d1424f35446a7d56d1bfb6
BASELINE:    verdicts=521 episodes=60 digest=50d1424f35446a7d56d1bfb6
```

**Expect differences, and expect them to be explainable.** A vendor export carries less
than the generic feed — Craneware has no per-row arrival time at all, and the two
vendors declare reversals differently. A digest that matches exactly would be suspicious
rather than reassuring: it would suggest the vendor path is not really being used.

The deliverable is a **reconciliation of the differences**, per verdict code, each one
traced to a named field the vendor does or does not carry. An unexplained difference is
the finding.

### 1.3 Verify the whole chain, not just that rows land

This is the part explicitly asked for, and it is where the current vendor leg is thin:
`tests/test_vendor_connector_leg.py` proves records land and resolve, and **nothing
proves what happens after**. Each of these needs a test on vendor-sourced data:

| Stage | What must hold |
|---|---|
| Crosswalk | keys published and resolved; park reasons accounted for, not merely counted |
| Episode | the same episodes exist, with the same identity |
| Bank allocation | a rebate deposit still allocates to the right episode through the vendor door |
| Verdict | dispositions and codes, reconciled per 1.2 |
| Queues | the exception queue contains the same claims for the same reasons |
| Reopening | an episode whose disposition moves backwards still records `reopened_from` |

The last one matters more than it looks: reopening is driven by arrival order, and the
vendor path resolves `received_at` differently — from `qualification_received_at` on
Verity, from the delivery stamp on Craneware. **If arrival order shifts, reopening
shifts.** That is the most likely place for a real defect and the least likely to be
noticed.

### 1.4 Inject deliberate divergence between the vendors

Today Verity and Craneware **cannot disagree** — both derive from one feed, so they
always report the same qualification for the same dispense. The single failure a
reconciliation engine exists to catch is two sources telling different stories, and in
the vendor layer that is currently impossible.

The generator already solves this problem elsewhere: it injects identifier drift
(defect D-6) into exactly one feed per episode so the crosswalk has a real miss to
find. The vendor layer needs the equivalent.

Without this, inversion proves parsing and not reconciliation.

Files: `src/recon/mocks/verity_export.py`, `src/recon/mocks/craneware_export.py`.
Note both are **formatters that decide nothing** — an AST test enforces no money
arithmetic and no decision logic — so the divergence must be *planned by the
orchestrator* and rendered here, exactly as D-6 is.

### 1.5 Demote the generic feed

Only after 1.0–1.4 are green: `tpa_340b_events.jsonl` stops being ingested and becomes
generation-only. Update `config.FEED_FILENAMES`' consumers, the two walkthroughs, and
`DESIGN_NOTE.md`.

---

## Step 2 — A Beacon surface on the repository

### The decision this records

The question was whether the fabric should be able to write to the database, or whether
the shared write capability should be pulled out into a module both the fabric and the
engine call.

**Neither, because the second already exists.** `src/recon/db/repository.py` is the only
module in the system that writes SQL, and `connectors/` is forbidden from writing any —
`test_the_connector_package_writes_no_sql_at_all` enforces it. The Beacon inbound leg
was built with the fabric having zero write capability:

```
Transport -> Document -> _read_rows -> _adapt_beacon -> CanonicalRecord
          -> pipeline._attach -> repository writes
```

78 Beacon IDs reached episodes through that path.

**Giving the fabric write access would break the source-of-truth boundary, not just
duplicate code.** `authority.py` works precisely because vendor data must pass through
adaptation before anything is written — that is where a TPA is refused permission to
assert a manufacturer's decision, and it caught a real defect (nine Craneware rows
quarantined as `SOURCE_AUTHORITY_BREACH`). A fabric that could write would let a
vendor's file author its own facts, and Doc 2's boundary would become a comment rather
than a constraint.

### What is actually missing

`repository.py` has 40 public functions and **one** mention of Beacon. Beacon facts are
reachable only by hand-written SQL at the call site — an episode's Beacon ID is
currently a raw `crosswalk_key` query. That is the real gap: not a missing capability,
a missing **vocabulary**.

Give it a Beacon surface so both consumers use one API:

- `beacon_id_for_episode(conn, episode_id)` — replacing the raw crosswalk query
- `episodes_awaiting_beacon_decision(conn, cursor)` — submitted, no outcome yet
- `beacon_validation_failures(conn, cursor)` — the rejections that today reach no queue
- `beacon_payment_references_for_episode(conn, episode_id)`

Each is a read. **No new writer is needed**, which is itself the finding worth
recording: the write path was already sufficient and the Beacon leg proved it.

### The gap this surfaces

`beacon_validation_failures` has no queue to feed. A claim Beacon refused at validation
never reaches the manufacturer, so `_manufacturer_status` returns `None` and
`classify_rebate` returns **C-05, "request submitted, manufacturer pending", forever** —
9 REJECTED and 4 REVERSED payloads on the demo profile.

Closing it needs a new `Dimensions` field, a branch in `_derive_rebate` and in
`classify_rebate`, and either a new row in `docs/reconciliation_state_space.md` or an
argument for folding it into C-07 — plus a recount of the configuration space that
document tallies. **Out of scope here; named so it is a decision rather than a
discovery.**

---

## Risks

**Arrival order is the sharpest one.** Craneware publishes no per-row timestamp, so its
rows inherit the delivery stamp. Verity's carry their own. Inverting the source changes
when records arrive relative to each other, and the engine evaluates at a cursor —
so verdicts can move for reasons that have nothing to do with content. Measure the
arrival-order delta explicitly before blaming any verdict difference on mapping.

**The agent eval fixtures break on any dossier change.** `ReplayClient` keys on a digest
of the whole request. Re-recording works offline via
`scripts/record_scripted_eval_fixtures.py`, but that script **opens** a database while
`tests/test_agents_evals.py` **builds** one — so rebuild the recorded-spine database
first or the re-recording silently diverges and fails as `ReplayMiss`.

**The knowledge graph is a runtime dependency.** Any entity named in `grounding.py`'s
ROUTES tables becomes a Clause, and `MAX_CLAUSES` caps the index at 72. Record this work
on a **new** entity with a relation to it; add no observation to a routed one.

---

## Verification

1. Full suite. Known pre-existing failure:
   `tests/test_query_plans.py::test_latest_verdict_walks_the_index_not_the_table`.
2. Three-way digest comparison — generic, Verity-sourced, Craneware-sourced — with every
   difference explained per verdict code.
3. Bank allocation, reopening and queue contents asserted on vendor-sourced data.
4. `uv run --quiet python -c "... build_dataset ..."` clean on both profiles.

---

## Outcome — what this plan got wrong

Both steps shipped. Five of the plan's own premises did not survive being measured, and
they are recorded here because the corrections are worth more than the plan was.

**1. The measurement that motivated §1.0 came from a file that should not have existed.**
`RBT-20251031-44202`'s lines summing to 26,500.00 against a declared 30,094.00 was read out
of a *superseded* Verity export. Verity names files from the data, nothing deleted old ones,
and three generations were on disk — 37 of 37 allocation codes in the two stale ones appear
in no feed. In the live export every batch's lines sum to its declared total exactly. The
sweep that fixes it is commit `b9cfd99`; "never assume the lines sum to the batch total"
remains sound advice about real vendors and was not evidence about this one.

**2. §1.0's blocker was not batch/line semantics.** `authority.py` refuses a TPA source the
rebate kinds outright — *"a TPA_VERITY source may not assert rebate status"*. The modelling
the plan proposed was never available. `TPA_INVOICE_LINE` is what a TPA may say.

**3. §1.5 is not available as written.** `tpa_340b_events.jsonl` cannot be demoted to
generation-only: 78 of its rows are the TPA's and 35 are the *manufacturer's*, and DOC2-004
puts the second set outside a TPA's authority entirely. Switching source swaps 78 rows, not a
file. What shipped instead is `exclude_source_systems` plus a `tpa_source` mode, and
`/api/regenerate` now takes it so the path is reachable from the dashboard rather than only
from pytest.

**4. §1.2 predicted explainable differences and under-predicted the cause.** The dominant one
was that **no TPA export carries the rebate request**, so `_derive_rebate` short-circuited 30
of 60 episodes to C-03. Closed by reading the submission from Beacon's acknowledgement, which
DOC2-004 makes authoritative and which measurement showed is a strict superset — zero
orphans. Its delta was taken alone: one episode on the generic build, C-03 to C-05. The vendor
deltas fell from 35 and 30 episodes changed to **4 and 1**.

**5. Step 2's gap is real but not the one described.** There is no hand-written Beacon SQL at
any call site to replace, and refused submissions do not sit at C-05 forever. Of the eight
episodes behind a refused submission, four are covered by a manufacturer decision arriving
independently; the other four survive on a reversal or on never having qualified. Nothing
reads the refusal, so the property now pinned is the one that matters: a refused submission
must never sit in `PENDING`.

**What the plan got right and is worth keeping:** the instruction to verify through the whole
chain rather than at ingest, and the warning that arrival order was the sharpest risk. It was.
Craneware publishes no per-row timestamp, lands a whole report at one instant, and reopens 64
closed verdicts against the generic feed's 34 — on a different set of episodes.

### Still open, named rather than discovered

- `beacon_rebate_status` remains unenabled; it maps to `TPA_MANUFACTURER_DECISION`, which is
  bucketed, so it moves verdicts and needs its own measured step.
- A Beacon validation refusal has no verdict code. Closing it needs a `Dimensions` field, a
  branch in `_derive_rebate`, and either a new state-space row or an argument for folding it
  into C-07.
- Whether the demo should default to a vendor source now that the vendor deltas are 4 and 1.
