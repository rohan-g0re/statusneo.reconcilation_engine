"""The deterministic reconciliation engine.

Four modules, in the order a verdict is produced:

``dimensions``    records at a cursor -> the abstract dimensions a verdict needs (the bridge)
``verdicts``      43 track rules + 7 cross-track rules, ported from the validated oracle
``dispositions``  verdict pair + flags -> one of three dispositions and a list of reasons
``run``           recompute per (episode, cursor) and append

**Every number in this package is born here, in Python.**  No amount, no allocation, no status
and no priority is ever produced by a language model — the agent layer explains what this code
decided and recommends what a human should do.  That boundary is the single most important
constraint in the project, and the engine's job is to make the agent's job possible without
letting it compute.
"""

from __future__ import annotations

__all__ = ["dimensions", "dispositions", "run", "verdicts"]
