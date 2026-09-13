---
title: Have the deterministic layer state facts; let the agent layer do the narrating
date: 2026-09-13
category: docs/solutions/best-practices
module: api-read-layer
problem_type: best_practice
component: api
severity: high
applies_when:
  - "Building a structured payload whose consumer is an LLM rather than only a person"
  - "A deterministic component is composing human-readable English about records it read"
  - "A dispatch table maps record types to hand-written renderers, and the table is growing"
  - "Deciding whether a code value deserves its own template or is a slot in one template"
symptoms:
  - "51 branches across 13 functions, each producing a hand-written headline and detail sentence"
  - "28 constant editorial clauses explaining domain meaning, 9 of them emitted unconditionally on every episode"
  - "The same closing sentence appeared on every cash row because it was appended outside the branch"
  - "Four record kinds silently vanished from every payload because an unmatched dispatch key hit a bare `continue`"
  - "Enumerating the branch space to make the prose exhaustive produced 81 required templates"
related_components:
  - api
  - agent_layer
  - frontend
tags:
  - agent-payload
  - determinism
  - templates
  - projection
  - separation-of-concerns
  - read-layer
---

# Have the deterministic layer state facts; let the agent layer do the narrating

## Context

A read layer assembled one object per reconciliation episode — its records, cash movements and verdict changes, merged into one time-ordered timeline. That object has two consumers: an operator reading a screen, and an LLM agent asked to investigate the episode.

Because a person was one of the consumers, each event was rendered as English. A dispatch table mapped each record type to a function that produced a `headline` and a `detail` sentence. It read well. It was also wrong in a way that only becomes obvious when you ask what happens to data nobody wrote a branch for.

## Guidance

### An event is a tag and its facts

```python
{"at": "2026-03-13T00:00:00Z", "occurred_on": "2026-03-13",
 "tag": "REMITTANCE_CLAIM_LINE",
 "facts": {"clp02_claim_status_code": "1", "charge_cents": 7950000,
           "payment_cents": 4865400,
           "adjustments": [{"group_code": "CO", "reason_code": "45",
                            "amount_cents": 1324349}]},
 "source": {"file": "medical_835_remittance.jsonl", "line": 28}}
```

The tag names what happened. The facts are that record's own fields, carried verbatim. Nothing is composed.

This is not a downgrade in what the payload communicates. `TPA_QUALIFICATION` with `qualification_status = QUALIFIED` carries exactly what *"The TPA qualified the dispense for 340B"* carried — the tag vocabulary is already descriptive, which is the same reason reason codes and verdict codes work without glosses.

### One projection per tag, and drop what is absent

A projection is an ordered list of the fields worth surfacing for that tag. The renderer omits anything absent. That single rule — *drop absent, don't null-pad* — is what lets flat per-tag lists cover a field space that genuinely varies:

- Several record types produced by one adapter shared a wide sparse dict, each populating only its own keys. Listing all keys per type and dropping the empties handles every one without a branch.
- Two record types were each built by *two* adapters with different key names for the same idea (`payer_name` vs `payer_tin`), and different shapes for a real difference (`service_line` singular vs `service_lines` plural). Listing both keys means neither source silently renders nothing.

The key set of the output then *states what the record has*, which is itself information the consumer can use.

### Make a missing projection loud

The version being replaced did this:

```python
builder = _BUILDERS.get(kind)
if builder is None:
    continue            # the record disappears, silently, forever
```

Four record types had no builder. They were absent from every payload for the entire life of that implementation, and nothing anywhere reported it. Replace the silent skip with a completeness check at import:

```python
_UNPROJECTED = sorted(set(RecordKind) - set(_PROJECTIONS))
if _UNPROJECTED:
    raise RuntimeError(f"no projection for {_UNPROJECTED}; their records would vanish")
```

A gap should fail where it is introduced, not evaporate at runtime.

### A code value is a slot, not a template

This is the decision that governs how many templates any templating scheme needs. If the reference data already carries a description for each code — and for published vocabularies like CARC, RARC or claim-status codes it does, written by the standards body rather than by you — then `"{code}: {description}"` is **one** template with two slots, not one template per code.

Get this wrong and the count explodes for no gain. Enumerating the branch space of the prose version, treating structural variation as templates and code values as slots, gave **81** templates and roughly **148** reference descriptions. Treating each code value as its own template would have multiplied that severalfold, and every one of those templates would have said the same thing with a different noun.

## Why This Matters

**Prose in a deterministic layer is a capability claim the code cannot keep.** English existed only for branches somebody had written out. A record type nobody anticipated produced nothing, or fell through to a generic fallback. So the component *looked* able to describe any record while in fact describing a fixed list — and a reader, including a reviewer evaluating the system, would credit it with a faculty it did not have. The demo was more convincing than the software.

**It also does the agent's job, one layer too early and worse.** The payload exists to be handed to a model when an episode needs explaining. Pre-narrating fixes the wording to whatever a person happened to type, in one register, with no access to the question being asked. The model can already say what `qualification_status = QUALIFIED` means, and it can say it differently for an auditor than for a pharmacy technician. Narrating first throws that away and adds nothing.

**The maintenance shape is upside-down.** Prose grows with the *cross-product* of structural variation; facts grow with the *number of record types*. Here that was 51 hand-written branches versus 16 projections for the same coverage — and the 51 were still incomplete, while the 16 are complete by a check that fails on import.

**Editorial commentary is the worst of it, because it is invariant.** Of 28 constant clauses, 9 were emitted unconditionally — the same sentences on every single episode regardless of its data. One explained the architecture's two-hop bank resolution on *every cash row*. That is a lecture stapled to a data row: it costs payload, it teaches the reader nothing after the first time, and being invariant it cannot possibly be describing the record it is attached to.

**Keep the units machine-native.** Money stayed as integer cents in the payload, formatted only at the display edge. The agent wants the exact figure; a rounded string is a lossy rendering decided by the wrong component.

**The distinction that survives.** Standards-published text is reference data and belongs in a lookup table — CARC 45's *"Charge exceeds fee schedule"* is written by X12 and is citable. A sentence explaining *why that matters here* is authored commentary. The first is a fact about the code; the second is a model's job.

## When to Apply

Reach for tag-and-facts when most of these hold:

- An LLM is a primary consumer of the payload, not an afterthought bolted onto a human-facing view.
- The set of record or event types is closed and enumerable, so a completeness check is possible.
- Record shapes vary enough that a uniform schema would be mostly nulls.
- The vocabulary is already descriptive — typed enums, standard code sets — so the tag carries meaning on its own.

**Not this when:** the output's only consumer is a human reading prose and there is no agent; the domain vocabulary is opaque without a gloss and no reference table exists to hold one; or the payload is genuinely a document (a letter, a notice) rather than a record of events, where the prose *is* the artifact.

## Examples

The same trap recurs wherever a deterministic component renders for a model:

- **Audit and activity logs.** `{"action": "permission_revoked", "actor": ..., "scope": ...}` beats `"Alice revoked Bob's write access to the billing repo"` — the structured form supports filtering, aggregation and diffing, and any consumer that wants the sentence can produce it.
- **Error and diagnostic payloads.** A machine-readable code plus the offending values, rather than a formatted message, lets the caller decide the register. Message-only errors force downstream code to parse English back into data.
- **Tool results in an agent loop.** A tool that returns narrated results is deciding what mattered before the model has seen it. Return the observations; let the loop summarise.

The test in every case: *if a record type arrived that nobody anticipated, would this produce something honest, or would it produce nothing at all?*

## Related

- [`exhaustive-generation-beats-hand-enumeration-2026-09-12.md`](exhaustive-generation-beats-hand-enumeration-2026-09-12.md) — the same family of failure. There it was a docstring asserting a test that did not exist; here it was prose asserting a descriptive capability that did not exist. Both are unfalsifiable claims in a place no test can reach.
- [`generate-source-feeds-as-independent-systems-2026-09-12.md`](generate-source-feeds-as-independent-systems-2026-09-12.md) — the silent-failure theme: a guard imported and never called, a collision that deleted data, and a dispatch miss that dropped four record types all share the property of producing plausible output while being wrong.
- `docs/decision_ledger.md` — decision A25, *the episode is the object*, is what makes a single per-episode payload the right unit to hand an agent in the first place.
