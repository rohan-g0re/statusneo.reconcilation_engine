# Glossary

The vocabulary used across every document in this repository. If a term here is used loosely anywhere else, this file wins.

Written because the documents had genuinely been conflating *claim* with *episode*, and *verdict* with *status*, in ways that made the design harder to follow than it needed to be.

---

## The nesting

```
DISPENSE              the drug actually handed over or infused — one real-world event
  └── EPISODE         everything financial that follows from it  (the "claim object")
        ├── TRACK 1     reimbursement   (pharmacy XOR medical, always exactly one)
        │     ├── linked records      pointers to immutable source rows
        │     └── verdict history     appended, never edited
        └── TRACK 2     340B rebate     (optional)
              ├── linked records
              └── verdict history
```

---

## Terms

**Dispense** — the physical event: a drug handed to a patient at a counter, or infused in a clinic.

**Claim** — a request for payment sent to an insurer; exactly one per dispense, and the point at which the assignment says this prototype begins.

**Episode** — the whole financial story hanging off one dispense: the claim, the optional rebate, and all the cash. The assignment calls it a *Claim Financial Episode*. This is the object we reconcile, and in code it is the **claim object**.

**Track** — one money road inside an episode. There are exactly two: reimbursement and 340B rebate.

**Reimbursement track** — pharmacy benefit **or** medical benefit, never both and never neither. Which road a drug travels is decided by how it is administered, not by choice.

**340B rebate track** — optional. Present when the dispense qualifies for a 340B rebate, absent otherwise.

**Record** — one row from one feed, immutable, stored exactly as it arrived. Records are never edited and never written back to.

**Feed** — one source system's output. There are four: PBM pharmacy, medical, 340B TPA, and bank.

**`received_at`** — the single field the pipeline adds to every record: when it entered our system. Every other date on a record is native to its format and points backward.

**Cursor** — a point in time used to replay the dataset. The engine processes records where `received_at <= cursor`, so moving the cursor forward or backward gives the answer as it stood at that moment.

**Verdict** — the outcome label on **one track**, such as `A-04` (paid, cash matched, settled) or `C-08` (rebate paid and matched). There are 31 reimbursement verdicts and 12 rebate verdicts.

**Verdict pair** — the two track verdicts taken together. 372 of them are reachable, and all 372 are, which is what proves the tracks are independent.

**Disposition** — the queue bucket for the **whole episode**: `CLOSED`, `PENDING` or `EXCEPTION`. Three values, because "what do I do with this?" has three answers: nothing, wait, work it.

**Reason code** — why the disposition is what it is. A list, not a single value, because an episode can be underpaid *and* missing settlement *and* short on cash at once. Includes `INSUFFICIENT_DATA` for cases the engine can deterministically detect it cannot decide.

**Rollup** — how two track statuses become one episode disposition: worst wins, `EXCEPTION > PENDING > CLOSED`.

**Reopened** — a flag on an episode whose disposition moved backwards because new information arrived, carrying `reopened_from`, `previously_closed_at` and `reopened_on`. Not a fourth disposition.

**Crosswalk** — resolving a key carried by an inbound document to the episode it belongs to. Necessary because no identifier is shared across all four feeds.

**Parked record** — an inbound document whose keys resolved to nothing, held rather than discarded, and re-checked whenever a new document arrives.

**Lineage** — the pointer from a computed number **down** to the raw source row that produced it. This is what makes an agent's citation real rather than decorative.

**Audit trail** — the appended history of what an episode's verdict was **across time**. Different direction from lineage, and both are kept.

**Ground truth** — what the orchestrator knows and the ingestion layer is forbidden to read. It exists so the crosswalk can be scored rather than assumed.

---

## The one-liner

> One dispense creates one episode; the episode has two tracks; each track gets a verdict; the two verdicts roll up into one of three dispositions, which decides which queue the episode sits in.

---

## Where this vocabulary is load-bearing

**Claim vs episode.** A claim is one *request for payment*. An episode is the claim *plus* the rebate *plus* the cash. Statements like "an episode cannot change disposition without a new record" are about the episode, not the claim.

**Verdict vs disposition.** A verdict is per-track and detailed — 372 combinations. A disposition is per-episode and coarse — 3 values. Calling either one "status" loses the distinction that makes the queue work.

**Record vs verdict.** A record is an immutable fact that arrived from outside. A verdict is derived, recomputed at every cursor, and appended. Records are never verdicts and verdicts are never records.
