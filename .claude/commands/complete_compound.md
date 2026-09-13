---
name: complete_compound
description: Refresh docs/solutions against the current code, then compound this session's decisions into the project knowledge graph as a new wave
argument-hint: "[scope hint] [mode:autofix]"
allowed-tools: Skill, Agent, Read, Write, Edit, Grep, Glob, Bash, mcp__memory__search_nodes, mcp__memory__open_nodes, mcp__memory__read_graph
---

# /complete_compound

Run both halves of compounding in one pass: refresh the written learnings against
the code, then capture what this conversation decided into the knowledge graph.

## Purpose

`ce:compound` documents a solved problem. `ce:compound-refresh` keeps those documents
true as the code moves. Neither captures **decisions** — the choices, constraints and
rejected alternatives that live only in conversation and disappear when the session
ends. This command does both jobs in the order that makes them compound: refresh first,
because the refresh produces findings that are themselves graph-worthy, then record.

**Why both.** `docs/solutions/` answers *"has someone solved this before?"*
`docs/knowledge_graph.jsonl` answers *"why is it built this way, and who decided?"*
A session that refreshes without recording loses its own reasoning; a session that
records without refreshing writes new nodes on top of stale ones.

## Usage

```bash
/complete_compound                      # both phases, interactive
/complete_compound mode:autofix         # phase 1 non-interactive
/complete_compound crosswalk            # narrow phase 1 to a scope hint
```

Arguments: `$ARGUMENTS` — a scope hint forwarded to phase 1, and/or `mode:autofix`.

<critical_requirement>
**Phase 2 writes through `docs/build_knowledge_graph.py`, never through the memory MCP
server.** The builder ends with `open(OUT, "w")` — it truncates and regenerates the
entire `.jsonl`. Any `mcp__memory__create_entities`, `add_observations` or
`create_relations` call lands in that file and is destroyed by the next rebuild, with
no error and no trace. The memory server is a **read** interface in this project:
`search_nodes`, `open_nodes`, `read_graph`.
</critical_requirement>

<critical_requirement>
**The graph is a RUNTIME DEPENDENCY, not documentation.** The agent layer's grounding
documents are derived from `docs/knowledge_graph.jsonl` rather than authored as
markdown. So a wave that removes or renames an entity the Coordinator's evaluator
grounds against can break the agent layer, silently, at the next rebuild.

Consequences for phase 2:

- Before `UNOBS`-ing anything, grep `src/recon/agents/` for the entity name and for
  the observation text. An observation that some scorer reads is load-bearing code,
  not prose.
- Prefer adding a correcting observation over removing the original when the entity is
  grounding material — the agent needs to know what changed, not just what is true now.
- After every rebuild, run the agent layer's grounding tests. A green graph build is
  not evidence the grounding still resolves.
</critical_requirement>

<critical_requirement>
**Phase 1 covers `docs/solutions/` AND the living design documents** — currently
`docs/agent_layer_design.md`, `docs/agent_layer_readiness.md` and
`docs/remaining_work.md`. Those go stale faster than the learnings do, because they
describe work in flight rather than work finished. Check each against the code the same
way, and against decisions made since they were written.

`docs/decision_ledger.md`, `START_HERE.md` and `docs/architecture_decisions.md` keep
their standing no-touch rule. If they are stale, **report it and record the staleness
in the graph** — do not edit them. That is the user's call, not the command's.
</critical_requirement>

---

## Phase 1: Refresh the learnings

Invoke the `compound-engineering:ce-compound-refresh` skill, forwarding `$ARGUMENTS`.
Follow it to completion including its report, subject to the scope constraint above.

Two project-specific notes:

- Investigate with **parallel read-only subagents, one per document** (Sonnet), so the
  main thread never absorbs each document's full evidence trail. Subagents gather
  evidence and return text; the orchestrator applies every edit.
- The learnings in this repo were written before implementation existed. Frame every
  investigation that way: *"this claim was written blind — does the code now confirm
  or contradict it?"* That framing is what turns a reference check into a real audit.

**Carry forward into phase 2** anything the refresh turned up that is a decision or a
durable finding: a defect found, a claim disproved, a practice implementation
confirmed. Those belong in the graph, not only in the refreshed prose.

---

## Phase 2: Compound the session's decisions into the graph

### Phase 2.1: Harvest

Scan the conversation for what a future session would get wrong without. Qualifying:

- a choice made, with the alternative rejected and the reason
- a constraint the user stated
- a defect found and what its root cause taught
- a claim that was checked and turned out false
- a practice that implementation confirmed or contradicted

**Exclude anything the repository already tells you on its own** — code structure, git
history, file layout, test names, anything already in `CLAUDE.md` or the design
documents. The graph holds what the code cannot.

Record provenance for each, matching `docs/decision_ledger.md`'s convention: approved
by the user / delegated / agent default. An agent default that enters the graph
unmarked becomes unauthorised architecture — the exact failure the ledger exists to
prevent.

### Phase 2.2: Dedupe against the live graph

Before writing, query what is already there:

- `mcp__memory__search_nodes` on each candidate's subject
- `mcp__memory__open_nodes` on any entity you intend to extend

If the server returns an empty graph, **stop and check the wiring before concluding
the graph is empty** — a relative `MEMORY_FILE_PATH` in `.mcp.json` resolves against
the server's working directory, not the project root, and fails silently. Fall back to
reading `docs/knowledge_graph.jsonl` directly, which always works.

Then classify each candidate:

| Finding | Action |
|---|---|
| Already present, unchanged | Drop it. Say so in the report so the user can challenge the dedupe |
| Present, but this session changed it | Correct the existing entity — see 2.3 |
| Genuinely new | New entity |

Never create a near-duplicate under a slightly different name. Two entities saying
almost the same thing will drift apart and the graph cannot notice.

### Phase 2.3: Add one wave

Read `docs/build_knowledge_graph.py` first and follow its conventions: the
`E(name, kind, *observations)` / `R(src, rel, dst)` helpers, the existing
`entityType` vocabulary (`Decision`, `Mechanism`, `Trap`, `Learning`, `Artifact`,
`CoreObject`, `Plan`, `Status`, …), and the house voice — one fact per string, plain,
specific, no hedging. Reuse an existing `entityType` unless the thing is genuinely a
new kind.

Append a single `def wave_N_<topic>():`, register it in `WAVES`, and **do not edit
earlier waves** — their content is committed history.

To correct an earlier wave, use the correction helpers, never `E()`:

```python
OBS("Build state", "new fact", "another new fact")   # append to an existing entity
UNOBS("Build state", "distinctive substring")        # drop what is no longer true
```

`E()` **appends rather than merges** — calling it twice with the same name emits two
rows for one entity, which reads as a duplicate and drifts. `OBS`/`UNOBS` both raise
rather than no-op, because a correction that silently matched nothing is how a graph
ends up asserting something the project stopped believing.

Relations matter as much as entities. A node with no edges is not usefully in the
graph — wire each new entity to what it constrains, supersedes, realises or depends on.

### Phase 2.4: Rebuild and verify

```bash
python docs/build_knowledge_graph.py
git diff --numstat docs/knowledge_graph.jsonl
```

Check the printed counts moved as expected. Then inspect **what the diff removed**:
removals should be exactly the entities you corrected with `OBS`/`UNOBS` (they are
rewritten in place). A removal you cannot account for means an earlier wave was
disturbed — investigate before committing.

Spot-check two or three new nodes back through `mcp__memory__search_nodes` to confirm
the server reads what the builder wrote.

### Phase 2.5: Commit

One commit for both phases. The body states what was refreshed, what was added to the
graph, and the provenance split of the new decisions. End with:

```
Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
```

---

## Discoverability check

Confirm `CLAUDE.md` still points at both stores — `docs/solutions/` and
`docs/knowledge_graph.jsonl` — and that what it says about them is true. If it claims
the graph is queryable over MCP, verify that it actually is. A pointer that is wrong is
worse than no pointer, because it reads as verified.

## Report

- **Phase 1** — per document: Keep / Update / Consolidate / Replace / Delete, and what
  changed. Plus anything stale found outside `docs/solutions/` you deliberately left.
- **Phase 2** — new entities, corrected entities, new relations, before/after counts,
  and the provenance split. Name what you dropped as already-present, so an
  over-aggressive dedupe is visible and correctable.
