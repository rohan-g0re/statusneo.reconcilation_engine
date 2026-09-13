---
title: Generate each source feed as an independent system; never derive them from a shared canonical model
date: 2026-09-12
category: docs/solutions/best-practices
module: synthetic-feed-generation
problem_type: best_practice
component: tooling
severity: high
applies_when:
  - "Building a synthetic data generator to test a multi-source integration, crosswalk, or entity-resolution system"
  - "Designing test fixtures for a connector that is supposed to join records across systems that do not share a key"
  - "About to model a 'canonical' or 'normalized' object before deciding what each source feed contains"
  - "Writing a test harness for a reconciliation, matching, or ETL pipeline and deciding where ground truth lives"
symptoms:
  - "Every generated feed carries the same primary key (claim_id, order_id, event_id) despite modeling independently-operated real-world systems"
  - "The connector's join logic is a single equality check with no fuzzy matching, fallback key, or unmatched-record path"
  - "Test coverage for the matching/crosswalk layer is 100% passing on the first try, with no unmatched or ambiguous cases ever produced"
  - "The generator's ground truth and the feeds it emits are produced by the same function call, so nothing is actually withheld from the system under test"
  - "Cardinality assumptions (one-to-one) went unquestioned because the shared key made every join look one-to-one by construction"
related_components:
  - test-data-generation
  - crosswalk
  - entity-resolution
  - documentation
tags:
  - crosswalk
  - entity-resolution
  - synthetic-data
  - multi-source-integration
  - ground-truth
  - test-fixtures
  - data-generation
  - reconciliation
---

# Generate each source feed as an independent system; never derive them from a shared canonical model

## Context

While building the synthetic data generator for a post-claim pharmacy financial reconciliation prototype (`decision_tree/`, see `docs/feed_formats.md` and `docs/architecture_decisions.md`, Decisions 7, 10 and 11), the natural first instinct was to model the canonical episode — one object per claim, carrying a single `claim_id` — and then have four generator functions each emit a projection of it: a pharmacy view, a medical view, a 340B view, a bank view. That instinct was rejected before any code was written, and the decision record names the rejected alternative explicitly:

> **Rejected.** Designing the generator backwards from the canonical model — i.e., generate the ground-truth episode first, then stamp a shared `claim_id` onto every feed's version of it. That would give every feed a convenient common key and reduce the connector to a no-op join, testing nothing about the actual crosswalk problem.

Research into how the real source systems identify claims (NCPDP pharmacy transactions, X12 837/835 medical claims, a 340B TPA's vendor CSV, and a bank's ACH ledger) confirmed there is no universal identifier to project from in the first place — three separate, non-overlapping identifier universes exist, assigned by different parties, on different rails, and two of the four feeds (pharmacy and medical) share literally no field with each other. A generator built around a canonical model would have had to invent something that does not exist in the domain it claims to simulate.

The system that was actually built has four independent generators and one orchestrator that never lets a generator see another's output or the ground truth it withholds (`docs/architecture_decisions.md`, Decisions 10-11).

## Guidance

### Design source-first, not canonical-first

Before writing a single generator function, answer this for each source system: *if this system had never heard of the other three, what would it natively emit, in its own format, with its own identifiers, on its own timeline?* That answer — not a slice of a shared object — is the generator's contract. The canonical/ground-truth object is allowed to exist, but only inside the orchestrator, and only as the thing the feeds are checked against after the fact, never as the thing they are derived from.

### Structure: orchestrator holds the truth, generators never see it or each other

```
orchestrator            holds the only complete picture; emits ground_truth.json
  gen_feed_a(slice_a)   receives only the slice source A would actually know
  gen_feed_b(slice_b)   receives only the slice source B would actually know
  gen_feed_c(slice_c)
  gen_feed_d(slice_d)
```

Three rules make this hold in practice:

1. **No generator reads another generator's output.** Each one is called with only the narrow slice of episode state that source system would plausibly have. `gen_feed_pbm` never receives the 340B qualification status; `gen_feed_bank` never receives a claim ID, because a bank has no concept of a claim.
2. **The orchestrator writes a ground-truth file the connector is forbidden to read.** This is what turns "does the crosswalk work" into a scored question instead of an assumed one. Without a withheld ground truth, you can observe that the connector ran and produced *some* output — you cannot measure whether the output is correct.
3. **Each generator invents its own identifiers, in its own native format.** Not a foreign key into the canonical object — a value shaped the way that real system actually produces values (a composite transaction key, an opaque authorization number, a vendor-specific batch code, a bank trace number).

### Before / after: the shared-key shortcut versus earned natural keys

**Before (canonical-first, the rejected approach):**

```python
# One object, one key, projected into four "feeds."
episode = {
    "claim_id": "CLM-000042",
    "verdict": "UNDERPAID",
    "amount_expected": 2891.75,
    "amount_paid": 2791.75,
}

def gen_pbm_feed(ep):
    return {"claim_id": ep["claim_id"], "paid": ep["amount_paid"]}

def gen_medical_feed(ep):
    return {"claim_id": ep["claim_id"], "paid": ep["amount_paid"]}

def gen_bank_feed(ep):
    return {"claim_id": ep["claim_id"], "amount": ep["amount_paid"]}

# The "crosswalk" is now: WHERE a.claim_id = b.claim_id. Nothing to solve.
```

Every feed conveniently carries `claim_id`. The join is a no-op equi-join the connector cannot fail. There is no unmatched case, no ambiguous case, no cardinality surprise — because the data was reverse-engineered from the join, not the other way around.

**After (source-first, what was actually built):**

```python
# Orchestrator holds ground truth. Each generator gets only its own slice
# and invents identifiers the way the real system would.

ground_truth = {"episode_id": "EP-000042", "verdict": "UNDERPAID", ...}  # connector never sees this

def gen_pbm_claim_event(slice_):
    return {
        "service_provider_id": slice_.pharmacy_npi,      # NPI, assigned by NPPES
        "prescription_ref_number": slice_.rx_number,      # assigned by the pharmacy
        "fill_number": slice_.fill_number,
        "date_of_service": slice_.dos,
        "authorization_number": slice_.pbm_auth_number,   # PBM-specific, opaque
    }

def gen_pbm_remittance(slice_):
    return {
        "clp01_patient_control_number": f"{slice_.rx_number}FILL{slice_.fill_number}",
        "trn": {"reassociation_trace_number": slice_.payer_trace_number},  # payer-assigned
    }

def gen_medical_837(slice_):
    return {"clm01_patient_control_number": slice_.provider_claim_number}  # provider's own key

def gen_bank_line(slice_):
    return {
        "ach_trace_number": slice_.ach_trace,   # banking-network plumbing, zero business content
        "trn02": slice_.payer_trace_number if slice_.addenda_survived else None,
    }

# The crosswalk is now real work: parse "7845102FILL00" back into {rx, fill};
# match TRN02 when present, fall back to amount+date when the addenda is dropped;
# resolve a bank line through the remittance it points at, never directly to a claim.
```

No field in `gen_pbm_claim_event`'s output appears anywhere in `gen_medical_837`'s output. The join the connector has to perform is exactly the join a real operator has to perform — and it can fail exactly the ways real ones fail (`docs/architecture_decisions.md`, Decision 8: TPA lag, ICN reassignment on reprocessing, dropped ACH addenda).

### Design principle to apply this generally

For any feed, ask two questions before writing the generator: *who assigns this identifier in reality*, and *does any other feed ever see it*. If the honest answer to the second question is "no," that absence is correct and should be preserved, not patched over with a convenience key.

### Three ways this went wrong in the build — all of them silent

This document was written before the generators existed. Building them produced three concrete failures, and the instructive thing about all three is that **none of them raised an error**. Each one produced a dataset that looked entirely plausible and was quietly worthless for the thing it was built to test.

**1. The blindness guard existed and was never called.** Every per-episode slice reference was spelled `f"{episode_id}:pbm-claim"` — so the handle passed to each generator embedded the episode identity that generator is specifically forbidden to know. `assert_slice_is_blind` had been written, was correct, and was imported without ever being invoked. The generators did not *use* the leaked id, so nothing failed; the isolation was simply unenforced, and one careless later edit would have been enough to cash it in. The fix was opaque handles (`SliceRefs`) plus `_guard`/`_guard_batches` wrappers at every slice-production site, so the assertion runs on the path rather than sitting beside it.

> A guard you wrote but never call is worse than no guard, because it reads like coverage. If a rule matters, invoke it on the production path and write a test that proves the invocation fails when the rule is broken (`tests/test_generators.py`).

**2. A weak record-id digest collided, and the collision ate real data.** Record ids were derived from a character-sum digest of the slice reference. Digests collided, and because the connector's idempotency key *is* the source record id, a collision did not surface as a duplicate — it made the connector discard the second record as a redelivery of the first. Sixteen of fifty-three remittances vanished, taking their claim lines and trace numbers with them, and the run still completed cleanly. Fixed by having the orchestrator mint dense sequence integers (`sequence` on the batch slices) instead of deriving ids from content.

> When a generated identifier doubles as a deduplication key downstream, a collision is not a warning — it is silent deletion. Mint from a counter you control; do not hash your way to uniqueness.

**3. Identifiers ignored the master seed, so every seed produced the same claims.** `_mint_clm01` and `_mint_rx_number` were pure functions of the sequence number, which meant the "different seed, different dataset" guarantee was false: two unrelated seeds minted byte-identical claim identities. Every other part of the run varied, so the defect was invisible in aggregate. Fixed with a seed-derived per-run offset (`_run_offset` in `src/recon/generators/sampling.py`), and regression-tested by generating under an unrelated seed and asserting the identifiers differ.

> If a generator promises seed-varied output, assert it across two seeds. "It looks different every time" is not the same claim, and the parts that did not vary are exactly the parts nobody checks.

The common thread: independence, idempotency, and seed-variation are all properties that hold or fail *invisibly*. Each one needs an assertion on the live path, not a comment saying it is true.

## Why This Matters

**The crosswalk is usually the hardest and most valuable part of the system.** If the generator hands every feed the same key, the component you most need to prove works is the one component that gets zero exercise. You ship a connector that has only ever seen a no-op join.

**You cannot score what you did not withhold.** A ground-truth file the connector is allowed to read is not ground truth, it is an answer key left on the desk. The only way to know whether matching logic actually works — versus merely ran without crashing — is to check its output against a fact set it structurally could not have seen.

**Independent generation forces you to research how real source systems actually identify things, and that research surfaces facts a canonical-first design would never expose.** In this project, two consequential findings only appeared because each generator had to be built from the real format outward:

- One bank deposit legitimately covers dozens of claims through a single remittance, which means the connector needs many-to-one allocation logic (`docs/feed_formats.md`, Section 5) — invisible if every claim had already been assigned its own bank-line key.
- A recoupment can be netted into a *later* payment, so the bank line shows only the net amount, and the only place the explanation lives is a remittance segment (`PLB`) that the bank layer is structurally blind to (`docs/feed_formats.md`, Section 4). A canonical-first model would have had nowhere natural for this asymmetry to live, because it assumes every feed can see the same facts.

**Concrete evidence from the domain this was built for.** Real US healthcare payments have no universal claim identifier — three separate identifier universes, assigned by different parties:

| Domain | Identifiers | Assigned by |
|---|---|---|
| Pharmacy (NCPDP) | Composite transaction key {pharmacy NPI, Rx number, fill number, date of service}; separately, a PBM authorization number | Pharmacy (key); PBM (auth number) |
| Medical (X12) | CLP01 — provider's own claim number, echoed back; CLP07 — payer's internal control number, **reassigned on every reprocess** even though CLP01 stays constant | Provider (CLP01); Payer (CLP07) |
| Bank | 15-digit ACH trace number (pure plumbing, zero business content); TRN02 reassociation number (payer-assigned, present only if addenda survived) | Banking network (trace); Payer (TRN02) |

The pharmacy and medical feeds share **no field at all** — different standards bodies, different rails, different vocabularies. The join from remittance to bank deposit requires an industry operating rule (CAQH CORE Rule 370) to exist at all, and it still fails routinely because most basic bank exports drop the addenda that carries TRN02. And the 340B TPA export has no standard format, no trace number, and is a vendor CSV — deliberately the structurally poorest feed in the dataset, because that is the real state of that part of the industry, not a shortcut.

None of these facts — the reassignment of CLP07, the netted recoupment, the many-to-one bank allocation, the addenda drop rate — would surface from a canonical-first design, because a canonical-first design assumes away the very asymmetries that make them true.

## When to Apply

Reach for source-first, independently-generated feeds with a withheld ground truth when most of these hold:

- Records describing the same real-world event will arrive from two or more independently-operated systems that were not designed to talk to each other.
- The component under test is specifically a join, match, crosswalk, or entity-resolution layer, and its correctness — not just its execution — needs to be measurable.
- The systems being modeled genuinely use different identifier schemes in reality (different standards bodies, different assigning authorities, different formats).
- You need the test data to exercise unmatched records, ambiguous matches, and cardinality edge cases (one-to-many, many-to-one) rather than only the happy path.

**Overkill when:** you genuinely control all the source systems and can mandate a shared correlation ID across them (a real, enforceable contract, not a convenience); the integration is a single feed rather than several independently-sourced ones; or the prototype's point is something else entirely and the join is not what is being demonstrated. In those cases a canonical-first design is simpler and loses nothing, because there is no real-world asymmetry being erased.

## Examples

The same trap and the same fix generalize past healthcare claims to any domain where independently-operated systems describe the same event:

**E-commerce.** Orders come from the storefront (order ID assigned by the storefront platform), payments from the PSP (its own transaction ID, opaque to the merchant), shipments from the carrier (tracking number, carrier-assigned), refunds from support tooling (ticket ID). Four systems, four ID schemes, no shared key — the merchant's own order ID is not guaranteed to appear in the PSP's or carrier's record at all.

**Advertising.** Impressions, clicks, and conversions are frequently logged by different platforms (ad server, analytics SDK, attribution vendor) on different click-ID or device-ID schemes, reconciled against billing records that use yet another invoice-line identifier.

**Logistics.** Bookings (freight forwarder's booking number), customs filings (customs entry number), tracking events (carrier's container/bill-of-lading number), and invoices (vendor's own invoice number) rarely share a field; a shipment is stitched together the same way a claim episode is here.

**Banking.** A core ledger entry, a card-network authorization ID, an ATM terminal transaction log, and a printed statement line each use a different identifier for what is, from the customer's point of view, one transaction.

In every case, the generator for each feed should be built by asking what that specific real system emits on its own — never by slicing a shared object — and the harness scoring the connector should hold back a ground-truth mapping the connector never gets to read.

## Related

- `docs/architecture_decisions.md` — Decisions 7, 8, 9 (no universal identifier; three bridges, each with a documented failure mode; the 340B feed deliberately the poorest) and Decisions 10-11 (four independent generators plus an orchestrator; ground truth withheld from the connector)
- `docs/feed_formats.md` — Sections 1-4 (worked examples of the four native formats and their non-overlapping identifiers) and Section 5 (two-hop bank resolution and parked-record backward re-check, both consequences of feeds sharing no key)
- `docs/solutions/best-practices/exhaustive-generation-beats-hand-enumeration-2026-09-12.md` — companion best practice from the same project, on generating the state space this data populates
