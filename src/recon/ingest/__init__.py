"""Ingestion: feeds in, canonical records and a resolved crosswalk out.

Three modules, in the order a document passes through them:

``adapters``   raw payload -> canonical record tree, plus the keys it publishes and looks up
``pipeline``   the event-driven loop: load, resolve forward, re-check parked backwards
``allocate``   the cash splitter, which is hop two of the bank's two-hop resolution

**This package never reads ``truth/ground_truth.json``.**  That is what makes the crosswalk
scoreable rather than assumed correct, and it is enforced by
``tests/test_connectors.py::test_no_module_on_the_ingest_path_evaluates_ground_truth``,
which scans this package, ``engine/`` and ``api/`` — because a comment would not be.

The guarantee used to be structural as well: the loader accepted a feeds directory and could
not reach the truth directory even by accident.  Since requirement A2 it accepts a
:class:`~recon.connectors.transport.Transport`, which is a more general thing than a path, so
the structural half now lives in the transport's own refusals and the scan is what covers
this package.

**It computes no verdicts.**  Ingestion builds the record layer, the crosswalk, the parked
pool and the allocations.  Verdicts are derived per ``(episode, cursor)`` by the engine and
appended, never stored here — which is why deleting the verdict log loses nothing that cannot
be rebuilt by replay.
"""

from __future__ import annotations

__all__ = ["adapters", "allocate", "pipeline"]
