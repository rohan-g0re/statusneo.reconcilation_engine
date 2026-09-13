"""Ingestion: feeds in, canonical records and a resolved crosswalk out.

Three modules, in the order a document passes through them:

``adapters``   raw payload -> canonical record tree, plus the keys it publishes and looks up
``pipeline``   the event-driven loop: load, resolve forward, re-check parked backwards
``allocate``   the cash splitter, which is hop two of the bank's two-hop resolution

**This package never reads ``truth/ground_truth.json``.**  The loader accepts a feeds
directory and nothing else, so it cannot reach the truth directory even by accident.  That is
what makes the crosswalk scoreable rather than assumed correct — and it is enforced by a test
that scans this package for any reference to ground truth, because a comment would not be.

**It computes no verdicts.**  Ingestion builds the record layer, the crosswalk, the parked
pool and the allocations.  Verdicts are derived per ``(episode, cursor)`` by the engine and
appended, never stored here — which is why deleting the verdict log loses nothing that cannot
be rebuilt by replay.
"""

from __future__ import annotations

__all__ = ["adapters", "allocate", "pipeline"]
