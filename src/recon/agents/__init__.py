"""The agent layer: the one place in this system where a language model runs.

Everything below this package is deterministic. Expected amounts, payment
allocation, reconciliation status and every disposition are computed in Python
and are reproducible from a seed. This package explains those results, proposes
what a human should do next, and gates the proposal behind an independent
evaluator. It never computes a number -- the assignment states that constraint
three times, and it is enforced here structurally rather than by prompt: every
figure an agent can surface must arrive through a tool result, and a
deterministic scorer fails any proposal quoting a figure no tool returned.

The layering is deliberate and each level is replaceable in isolation:

    roles/          investigator (explains) and coordinator (proposes)
      |
    harness.py      the loop: propose -> verify -> score -> gate
      |
    rubric.py       weighted scorer list; the LLM judge is one entry among
      scorers.py    deterministic ones, with no special status
      |
    grounding.py    clauses derived at import time from the knowledge graph
      |
    tools.py        seven tools; the only module that touches the database
      envelope.py
      |
    client.py       Protocol + OpenAI-compatible + record/replay
      journal.py    append-only JSONL; model-visible means logged
      config.py

Two facts about this package are worth knowing before changing it.

`grounding.py` reads ``docs/knowledge_graph.jsonl`` at import time, so the
knowledge graph is a runtime dependency and not documentation. Renaming or
removing an entity the evaluator grounds against breaks the agent layer at the
next graph rebuild; the grounding tests exist to make that fail loudly.

`journal.py` holds the invariant that anything a model saw is reconstructable
from the log alone. That single artefact is simultaneously the tool-call trace
the assignment requires, the audit trail, and the replay fixture that lets the
evaluation set run in CI with no API key.
"""

from __future__ import annotations

__all__ = [
    "client",
    "config",
    "envelope",
    "grounding",
    "harness",
    "journal",
    "prompts",
    "roles",
    "rubric",
    "schemas",
    "scorers",
    "tools",
]
