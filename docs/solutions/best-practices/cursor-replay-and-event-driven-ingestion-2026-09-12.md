---
title: Model time with one added timestamp, a read-time cursor, and event-driven key resolution — never a scan window
date: 2026-09-12
category: docs/solutions/best-practices
module: reconciliation-time-replay
problem_type: best_practice
component: service_object
severity: high
applies_when:
  - "Designing how a system ingests facts that describe something that already happened, from more than one external source, arriving out of order"
  - "Deciding whether to add a bitemporal pair of fields (event_date, received_at) to a record schema"
  - "Building 'what did we believe as of date X' / audit-replay functionality"
  - "A reconciliation, billing, or workflow engine's disposition can change without any new inbound record, because it is keyed off elapsed time (an SLA, an age threshold)"
  - "An ingestion pipeline sweeps entities on a schedule and re-checks each one for new activity, rather than reacting to the document that arrived"
  - "A document from one external system does not carry the identifier of the entity you actually need to update"
symptoms:
  - "A proposed event_date field duplicates a date the source format already carries natively (date of service, fill date, posting date, effective date)"
  - "A 'replay' or 'rebuild' feature is planned as a second code path distinct from normal processing"
  - "An exception queue is a table that rows get moved into and out of, with bespoke undo logic for every kind of correction"
  - "A nightly job scans all open entities under a window (30/45/60 days) to catch anything that crossed a threshold since yesterday"
  - "A late-arriving fact for a 200-day-old entity is missed because it fell outside the scan window"
  - "An inbound document whose lookup key matches nothing is dropped or logged as an error instead of parked"
  - "An out-of-order arrival (the explanation shows up after the thing it explains) never self-corrects even once the explaining document lands"
related_components:
  - ingestion_pipeline
  - reconciliation_engine
  - audit_trail
  - workflow_queue
tags:
  - bitemporal
  - cursor
  - event-driven
  - replay
  - append-only
  - idempotent-ingestion
  - sla-policy
  - reconciliation
---

# Model time with one added timestamp, a read-time cursor, and event-driven key resolution — never a scan window

## Context

A post-claim financial reconciliation engine ingests four independent, out-of-order external feeds (pharmacy claim events, medical 837/835, a manufacturer-rebate TPA export, and a bank CSV) and has to answer, correctly, both "what is the status right now" and "what did we believe on any past date." The naive design reaches for two things that feel obviously correct and are both wrong: a bitemporal pair of date fields per record, and a scheduled scan that re-checks everything under a time window to catch anything that aged past a threshold.

Neither survives contact with the real record formats. Every one of the four source standards already carries its own backward-looking domain dates as ordinary content — date of service, payment effective date, posting date, fill date — so a generic `event_date` field duplicates information the format defines rather than adding a dimension. And a scan window can only be as trustworthy as the width chosen for it, which means correctness for a genuinely stale record is never provable, only probable.

The design that replaced both: exactly one added field (`received_at`), a cursor that lives at read time instead of inside the record, recompute-and-append instead of mutation, and inbound documents as triggers that resolve their own lookup keys instead of a sweep. The four decisions interlock — each one is unsound without the others, and the SLA-removal decision is the one that makes the whole thing provably correct rather than probably fine. See `docs/architecture_decisions.md` Sections D (16–23) and E (24–28) for the full decision record this pattern was extracted from.

## Guidance

### 1. Add one timestamp, not two

Resist modelling bitemporality as `(event_date, received_at)`. Check what the source format already carries first — real record standards are opinionated about dates because the domain requires it (a fill date, a date of service, a settlement date). Adding a second generic date field on top double-counts that information under a new name.

Add exactly one field, and only this one: `received_at`, the moment the record entered *your* pipeline. It is the one thing no source system has an opinion about, because it describes an event in your infrastructure's timeline, not theirs.

```python
# Native to the format — do not duplicate these:
#   date_of_service, fill_date, payment_effective_date, posting_date

def ingest(raw_record: dict) -> dict:
    record = parse_native_format(raw_record)   # untouched domain dates stay as-is
    record["received_at"] = utcnow()            # the one field the pipeline adds
    return record
```

Before accepting a bitemporal pair, check every native date field in the format for whether it is backward-looking. In the four formats studied here, every native date described something that had already happened — no "will pay by X," no "response due by X." The one exception found (a payment-network batch header's forward-dated settlement instruction) was a decision already transmitted to the network, not a promise about an uncertain outcome, and by the time it reaches your side of the pipeline it has usually already resolved into a backward-looking posting date anyway. Model a genuine exception like that precisely, on its own terms — do not force it backward to preserve a blanket rule, and do not let one exception talk you into a second generic field for everything else.

### 2. Put the cursor at read time; give the generator/producer no concept of "now"

Do not stamp records with "current status as of processing time" at write time. Keep every record immutable and native, and compute status as a query parameterized by a cursor:

```python
def status_as_of(entity_id: str, cursor: datetime) -> Verdict:
    records = fetch_records(entity_id, where="received_at <= :cursor", cursor=cursor)
    return derive_verdict(records)   # same function, any cursor value
```

Moving the cursor forward folds in newly-arrived records and can change the verdict. Moving it backward reproduces exactly what the system believed at that earlier moment. There is no separate "replay mode" — it is the same code path, called with a different argument. This has two consequences worth calling out explicitly to stakeholders:

- **The audit answer is free.** "What did we believe on 31 March?" is `status_as_of(entity_id, march_31)`, not a feature you build.
- **Disorder is between records, not inside one.** A single record is never "out of order" by itself — what makes replay meaningful is that different records carry different `received_at` values, and a later cursor can admit a record whose `received_at` predates records already admitted at an earlier cursor. That is the entire mechanism; nothing else is needed to represent out-of-order arrival.

### 3. Recompute and append; never mutate a stored status

Two designs compete here. (a) Find the record, flip a status column, move it between queues. (b) Discard nothing, and re-derive the verdict from every record received so far, appending the result as a new row.

Choose (b). With mutation, every kind of correction needs its own undo logic, and the cursor can never move backward because the "before" state was overwritten. With recompute:

```python
def on_new_record(entity_id: str):
    verdict = status_as_of(entity_id, cursor=utcnow())
    verdict_log.append({          # append-only; never UPDATE an existing row
        "entity_id": entity_id,
        "cursor": utcnow(),
        "status": verdict.status,
        "reasons": verdict.reasons,
    })
```

State this plainly when documenting the design, because it changes how three other pieces are built:

- **Status is never a stored field.** It is a function of `(entity, cursor)`, recomputed, not looked up.
- **The work/exception queue is a query, not a place things move into.** "At this cursor, which entities come out as EXCEPTION?" — there is no `move_to_exception_queue()` call anywhere.
- **The verdict log is a derived artifact, not the source of truth.** Delete it and it rebuilds by replaying the immutable source records through the same function. That discipline is what prevents the stored status and the rules from drifting apart — there is only one place the rules live.
- **Source records are never written back to.** They are immutable facts; only the derived log is appended to.

### 4. Trigger on the inbound document, not on a scan window

The failure mode of a scheduled sweep: to guarantee catching a record that went stale 200 days ago, the scan window has to be at least 200 days wide, and correctness depends on a bound you can never fully verify in advance. The fix inverts the control flow — the document is the trigger, and it carries its own lookup keys:

```python
def on_document_arrival(document: dict):
    keys = extract_lookup_keys(document)         # e.g. {pharmacy_npi, rx_number, fill_date}
    entities = resolve_entities(keys)             # fetch ONLY what these keys resolve to
    for entity_id in entities:
        on_new_record(entity_id)                  # recompute + append, from step 3
```

Entity age becomes irrelevant to cost: resolving a 200-day-old entity is exactly as cheap as resolving one from this morning, because resolution goes key → entity, never entity → "has anything new arrived for you?"

**Refinement A — multi-hop resolution.** Not every document resolves directly to the entity you ultimately care about. A bank deposit typically carries no claim-level identifier at all — only a trace number that resolves to a *remittance*, and the remittance in turn holds the list of claims it pays. One inbound bank line can therefore touch dozens of downstream entities, found entirely through the intermediate hop, never by scanning:

```python
def on_document_arrival(document: dict):
    if document["type"] == "bank_deposit":
        remittance = resolve_remittance(document["trace_number"])
        if remittance is None:
            park(document)                         # see Refinement B
            return
        for entity_id in remittance.entity_ids:     # one deposit -> many entities
            on_new_record(entity_id)
```

**Refinement B — parked records and the backward re-check.** When a document's keys resolve to nothing *yet*, park it rather than dropping or erroring on it. Every inbound document then does two things, not one: resolve forward against existing entities, and check the parked pool backward.

```python
def on_document_arrival(document: dict):
    keys = extract_lookup_keys(document)
    entities = resolve_entities(keys)
    if not entities:
        park(document)
    else:
        for entity_id in entities:
            on_new_record(entity_id)

    # Backward check: does this NEW document unlock anything sitting in the park?
    for parked in parked_pool.matching(document):
        resolved = resolve_entities(extract_lookup_keys(parked))
        if resolved:
            parked_pool.remove(parked)
            for entity_id in resolved:
                on_new_record(entity_id)
```

Worked example: a bank deposit arrives Tuesday and parks because no remittance with its trace number exists yet. The remittance arrives Friday. It resolves its own claims forward *and*, via the backward check, clears the parked Tuesday deposit — both driven by the same arrival, in the same pass. Skip the backward half and an out-of-order arrival is unmatched forever, even after the record that would explain it finally lands — the forward-only version of this pattern quietly reintroduces the "hope the window is wide enough" problem it was built to eliminate.

**Refinement C — the keyless document, and why matching on attributes is not a retreat.** Refinements A and B both assume the document carries *some* key, even a key that resolves to nothing yet. Real feeds break that assumption: a bank deposit's trace number lives in the CCD+ addenda record, and addenda are routinely stripped somewhere between the originating bank and the receiving one. The deposit still arrives, still carries real money, and identifies nothing at all.

The temptation is to treat this as an exception and queue it for a human. That is wrong at volume, and it is wrong in principle: a deposit with no key is not unmatched, it is unmatched *by trace number*. It still has two attributes that are as much a part of the payment as its trace is — an amount and a settlement date — and a remittance that expects exactly that amount within a couple of days of that date is a match on evidence, not a guess.

```python
def resolve_or_park(document):
    if document.trace_number:
        remittance = resolve_remittance(document.trace_number)
        if remittance:
            return remittance, AllocationBasis.TRACE

    # No trace, or a trace that resolves to nothing: fall back to amount + date.
    # A unique hit is a resolution. Two candidates is NOT a coin flip — it parks.
    candidates = remittances_expecting(
        amount=document.amount_cents,
        within_days=AMOUNT_DATE_WINDOW_DAYS,
        of=document.settlement_date,
    )
    if len(candidates) == 1:
        return candidates[0], AllocationBasis.AMOUNT_DATE
    return None, None                                  # park; Refinement B takes it from here
```

Three things make this safe rather than sloppy:

- **Record which basis resolved it.** A link found by amount-and-date is a weaker claim than one found by trace, and downstream needs to know that. Store the basis on the link; never let the two look identical once written.
- **Ambiguity parks, it does not guess.** If two remittances expect the same amount in the same window, picking either is a coin flip that will be wrong half the time and will look authoritative in both. Park it.
- **The window is not a scan window.** This is the one place a day count appears, and the distinction matters: `within_days` narrows a *lookup keyed on amount*, it does not sweep entities by age. Cost still scales with arrivals, not with the size or age of the book.

Expect a residual false-positive rate and do not treat it as a defect. Two genuinely identical payments inside the window are indistinguishable on the evidence available — matching them confidently is what amount-and-date matching *is*, and the honest response is the recorded basis, not a wider window.

**Implementation reference:** `src/recon/ingest/pipeline.py` — `AMOUNT_DATE_WINDOW_DAYS` (line 93), `_recheck_parked_by_amount_and_date` (lines 612-683) and `_resolve_bank_by_amount_and_date` (lines 497-533); `AllocationBasis.AMOUNT_DATE` records the provenance; the partial index `ix_norm_keyless_amount` (`src/recon/db/schema.sql:171-174`) keeps the fallback lookup off a table scan by indexing only the keyless rows.

### 5. Drop time-based thresholds from the verdict logic — the decision that makes the rest sound

This is the keystone, and it is easy to under-rate because it looks like a simplification rather than a correctness argument.

With any SLA-style threshold in the verdict rule (`if age_days > 30: status = EXCEPTION`), an entity with *no new record at all* can still flip state overnight — day 29 is fine, day 30 is not. That means a nightly sweep over every open entity is mandatory "just in case," and the event-driven design from step 4 becomes quietly incorrect: it is only complete if the sweep is also running to catch pure aging.

Remove every threshold from the disposition rule and the sweep is no longer needed for correctness — it can only be needed for performance, and here it turns out not to be needed at all:

```python
# Before: aging changes disposition -> requires a sweep to be correct
def verdict(entity, cursor):
    age = cursor - entity.date_of_service
    if age > 30 and not entity.paid:
        return EXCEPTION
    ...

# After: aging is a sort key over the queue, never a verdict input
def verdict(entity, cursor):
    if not entity.paid and entity.has_defect:
        return EXCEPTION           # driven by a defect a record revealed, not by elapsed time
    ...

def exception_queue(cursor):
    rows = [e for e in all_entities if verdict(e, cursor) == EXCEPTION]
    return sorted(rows, key=lambda e: cursor - e.date_of_service, reverse=True)  # oldest first
```

State the resulting guarantee explicitly, because it is a proof, not a hope: **if disposition can only change in response to a new inbound record, an entity with no new record since the last run cannot have a different disposition now.** "Only recompute entities touched by new events" is then a property you can state and defend, not an optimization you are trusting to be safe.

Real deadlines do not disappear — they move to being a *read-time sort key* over the queues (oldest EXCEPTION first) rather than a *write-time input* to what counts as an exception. If a genuine timing rule needs reintroducing (a payment ceiling, a prompt-payment statute, a remittance-pairing window), it belongs in engine configuration, looked up per tenant/source/pathway (`sla_for(tenant, source, pathway)`), never as a literal inside the rule (`age_days > 30`). A literal forks the rule the moment a second counterparty needs a different number; a config lookup adds a row.

## Why This Matters

**Two fields double-count what the format already tells you.** Checking whether a format's native dates are backward- or forward-looking is cheap and decisive — it tells you in one pass whether a bitemporal pair is modelling something real or modelling the same fact twice under two names. Skipping that check is how systems end up with an `event_date` column that is, in practice, always equal to some other column already on the record.

**A scan window is a number you cannot defend.** "30 days" invites "why not 45?" in review, and the honest answer is usually "it seemed wide enough." A window-based design can never upgrade that answer to a proof; an event-driven, threshold-free design can, because the claim ("nothing changes without a new record") is checkable by inspection of the rule set rather than by choosing a number and hoping.

**Mutation forecloses the audit story before it starts.** Once a status field has been overwritten, "what did we believe before" requires either a separate history table kept manually in sync (which can drift from the live field) or is simply unanswerable. Recompute-and-append gets the audit trail as a side effect of the storage discipline, not as a feature built on top of it.

**The backward re-check is the part that is easy to omit and expensive to omit.** A forward-only resolver looks complete in testing, because test data is usually generated in causal order. Real feeds are not — the explanation for a fact routinely arrives after the fact itself. Without the backward half, that ordering isn't an edge case the system handles slowly; it's a case the system never handles, silently, forever.

**The SLA-removal decision is what upgrades "probably correct" to "provably correct."** Every other decision in this pattern (one timestamp, read-time cursor, recompute-and-append, event-driven resolution) is compatible with keeping a hidden aging threshold somewhere in the verdict logic — and if one survives, the whole design quietly reverts to needing a sweep, because that threshold is the one thing that can change an entity's state with zero new input. Auditing a design for this pattern means checking specifically for any `if age >` or `if elapsed_time >` inside a disposition rule; its presence anywhere is the tell that the event-driven claim doesn't actually hold.

## When to Apply

Reach for this pattern — one added timestamp, a read-time cursor, recompute-and-append, event-driven key resolution with parking and backward re-check, and threshold-free verdicts — when most of these hold:

- A fact about the past can arrive after you have already reported on the period it describes (a late payment, a corrected claim, a reversed transaction). This is the single test: *can something dated yesterday show up next week?* If yes, you need the cursor.
- More than one external source feeds the same reconciliation or workflow decision, and those sources are not guaranteed to arrive in causal order relative to each other.
- An auditor, regulator, or internal reviewer will plausibly ask "what did the system believe as of date X," and manufacturing that answer after the fact is not acceptable.
- The current design's verdict logic branches on elapsed time (an SLA, an age threshold), and the entity population is large enough that a full sweep to catch pure aging is itself a scaling concern.
- At least one inbound feed resolves only indirectly to the entity you care about (a payment resolves to a remittance, which resolves to claims) — multi-hop resolution and parking earn their keep here specifically.

**Overkill when:** there is exactly one synchronous source of truth, records arrive in guaranteed causal order, nothing ever needs to be reopened or corrected after being reported on, and no one will ever ask what the system believed at a past point in time. A single-writer CRUD system with no late-arriving corrections gets nothing from an append-only verdict log except storage growth and query complexity — mutate the field and move on. Introduce the pattern when a second source, a replay/audit requirement, or a first late-arriving correction actually shows up, not preemptively.

## Examples

**Subscription billing with retries and chargebacks.** A failed charge, a delayed webhook retry, and a chargeback filed 60 days later are three documents that can arrive in any order relative to each other. `received_at` on each; subscription status is `status_as_of(subscription_id, cursor)`; a chargeback is the trigger that resolves back to the original charge (multi-hop, since the chargeback carries the processor's dispute ID, not your internal charge ID) and recomputes.

**Inventory with delayed receiving.** A purchase order, an ASN (advance ship notice), and a warehouse receipt for the same shipment routinely arrive out of order — the receipt sometimes beats the ASN to the door. Park the receipt if no matching ASN/PO exists yet; when the ASN lands, it resolves forward and clears the parked receipt backward.

**Compliance workflows with late evidence.** A compliance case closed as "resolved" can have new evidence submitted afterward. Recompute-and-append means the case's status is re-derived including the new evidence rather than requiring someone to manually reopen a mutated status field; `reopened_from` becomes a flag on the new verdict row, not a fourth state machine bucket bolted on afterward.

**Any event-sourced system with external feeds.** The generalized shape is: immutable inbound facts, one arrival timestamp, a cursor-parameterized derivation function, and a trigger-based resolver with a parking pool. The reconciliation-domain specifics (claims, remittances, bank deposits) are the worked example; the mechanism transfers as-is to any domain where "a fact about the past can still be arriving."

## Related

- `docs/architecture_decisions.md` — Section D (Decisions 16–23: `received_at`, the no-future-dates exception, the cursor-at-read-time model, two-hop bank resolution, parking and the backward re-check, recompute-never-mutate) and Section E (Decisions 24–28: the three-disposition status model this cursor feeds into)
- `docs/reconciliation_state_space.md` — the state space this pattern's threshold-free verdict rules generate against
- `docs/solutions/best-practices/exhaustive-generation-beats-hand-enumeration-2026-09-12.md` — the companion practice for validating the resulting rule set once thresholds are removed and every remaining branch is a defect-driven, not time-driven, condition
