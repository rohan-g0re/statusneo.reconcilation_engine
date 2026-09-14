---
module: agent_layer
tags: [eval-set, scorers, g3, hardening]
problem_type: known-limitation
---

# The eval set's deliberate failure case: a clean proposal, zeroed by one figure

## What this is

`tests/fixtures/agent_traces/eval-g3-veto-failure.jsonl` is a hand-scripted, offline
fixture — no live model, no API key, generated once by
`scripts/record_g3_veto_fixture.py` and replayed forever by
`tests/test_agents_evals.py` via `ReplayClient`. It is the eval set's one *deliberate*
failure case, requested explicitly rather than left to chance: a scenario constructed
to fail, so the failure mode is understood and documented rather than discovered by
accident on a bad day against a live model.

## The scenario

A proposal for `E-000006` (a denied claim with an already-paid 340B rebate, cross-track
flag `X-1`) that:

- makes **zero tool calls** — it reasons entirely from a grounding clause quoted
  verbatim (satisfies G5, G10),
- names an action (`WRITE_OFF`) valid for the episode's `EXCEPTION` disposition
  (satisfies G1),
- states the replay cursor's date, as G15 requires,
- is scripted to be graded `SUPPORTED` on every one of the eight judge criteria that
  apply to it (G4, G6, G7, G9, G11, G12, G14, and — separately tracked —
  G16), by an evaluator that has been told, in the fixture, to approve everything,
- and states one dollar figure — *"the residual exposure after the denial is
  $412.60"* — that no tool result backs, because none was called.

## What actually happens (measured, not asserted)

Running it through the real `run_coordinator` produces:

```
findings   G1 SUPPORTED, G3 CONTRADICTED, G5 SUPPORTED, G8 SUPPORTED, G15 SUPPORTED,
           G10 SUPPORTED, G4 SUPPORTED, G6 SUPPORTED, G7 SUPPORTED, G9 SUPPORTED,
           G11 SUPPORTED, G12 SUPPORTED, G14 SUPPORTED, G16 SUPPORTED
score      final=0.0 best=0.0
outcome    capped (with budgets.max_iterations pinned to 1 for this fixture)
```

Thirteen of fourteen applicable criteria pass. `rubric.score`'s veto gate does not
average that in: *"a criterion is a veto... if it is CONTRADICTED, gate = 0.0,"* full
stop, regardless of how many other criteria are perfect. One unsourced number zeroes
a proposal that is, in every other respect, exactly what the harness asks for. This is
the assignment's central constraint (*"every number is born in Python"*) holding under
direct pressure, and the eval set asserts on it precisely because it is the property
most worth protecting against regression.

## The surprising part, worth its own line

`no_unsourced_number`'s actual reasoning for this run flags **two** things, not one:

```
'2026-07-01' at offset 6 is not in any tool result this run; '$412.60' at offset 281
is not in any tool result this run
```

The cursor date — the exact string G15 requires the proposal to state — is *itself*
flagged as unsourced. This is not a bug in G3; it is a real interaction between two
criteria that only shows up when a proposal makes no tool calls at all:

- G15 requires the reasoning to contain the literal cursor date.
- G3 only exempts a date that appears in some tool result's `_ISO_DATE_PREFIX_RE`-
  matched field this run (`_build_sourced_sets` walks `tool_results`, nothing else).
- In every real run measured so far, the proposer calls `get_episode` or
  `calculate_reconciliation` early, and both echo the dossier's `cursor` field
  verbatim in their response — which is what sources the date in practice.
- A proposal that skips tool calls entirely — reasoning purely from grounding
  clauses, as this scripted one deliberately does — has no such echo, so satisfying
  G15 and G3 simultaneously becomes structurally impossible for it.

## The production-hardening note

Three ways a real deployment should treat this, in order of how much they change:

1. **Cheapest, no code change:** this is not actually a gap — a proposer that never
   calls a tool is already violating the design's own expectation that a
   recommendation "explains what the code decided," and the veto correctly refuses
   to reward it. Leave it as observed behavior and let it keep failing loudly.
2. **If lazy-proposal runs turn out to be common in production** (a cheaper/faster
   model, or a prompt regression that lets the proposer skip research): add a
   structural check *before* the evaluator ever runs — e.g. "the proposer's tool-call
   count for this run is zero" is itself a `NOT_ADDRESSED`-forcing condition, giving a
   sharper, cheaper-to-diagnose signal than "G3 failed" (which does not by itself say
   *why* nothing was sourced).
3. **If G15/G3 tension shows up on proposals that DO call tools** (not measured here,
   but plausible if a future tool omits `cursor` from its response): source the
   cursor unconditionally into `_build_sourced_sets`'s `dates` set from
   `ScoringInput.dossier["cursor"]` directly, independent of which tools were called.
   That is a one-line, low-risk change to `scorers._build_sourced_sets` — not made
   here, because it is not needed by anything actually observed; recorded as the
   concrete next step if it ever is.

## Where this is exercised

`tests/test_agents_evals.py::test_g3_veto_zeroes_an_otherwise_clean_proposal` replays
the fixture and asserts: the outcome is `capped` (never `complete`), `final_score ==
0.0`, `G3_figures_are_sourced` is the only `CONTRADICTED` veto, and every other
applicable criterion is `SUPPORTED` — i.e., that the failure is real and specific, not
a fixture that merely looks broken everywhere.
