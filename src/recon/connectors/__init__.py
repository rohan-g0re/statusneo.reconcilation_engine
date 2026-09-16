"""How we get the data, and whether we are allowed to.

Requirement groups A and B of ``docs/connectivity_layer_requirements.md``, which are Doc 2
steps 2 and 3: the interface contract, and the adapter build.

**Why this is a new package rather than an extension of ``ingest/``.**  ``ingest/`` answers
*"what does this record mean and what does it join to."*  ``connectors/`` answers *"how do I
get it and am I allowed to."*  Those two fail differently — a crosswalk bug produces a wrong
number, a transport bug produces no number at all — they are tested differently, and only
one of them touches a network.  Keeping the seam visible is also what keeps the existing AST
and grep tests meaningful: a rule about ``ingest/`` stops meaning anything once ``ingest/``
also dials out.

**What is deliberately not here.**  No ``retry.py``, no ``observability.py``.  Doc 2 lists
retry/replay, observability, alerting, DQ quarantine, secrets rotation, runbooks, lineage,
backfill and cutover under step 6, production hardening, and this build stops at step 5.  A
failed fetch fails loudly and is re-run by hand.  That is not an omission; it is the scope
line, and it is Doc 2's own.

**The guarantee this package must not lose.**  ``load_feeds`` used to take a feeds directory
and could not reach ``truth/`` even by accident.  That structural fact is why crosswalk
accuracy is a measurement rather than a claim, and generalising a directory into a
:class:`~recon.connectors.transport.Transport` threatens it, because a transport is a more
general thing than a path.  So the protocol exposes no arbitrary filesystem access, every
implementation refuses a ground-truth path explicitly, and
``tests/test_connectors.py`` asserts it for each one.  Restating the guarantee elsewhere is
fine.  Losing it quietly during a refactor is not.
"""

from __future__ import annotations

__all__ = ["credentials", "registry", "schema_registry", "transport"]
