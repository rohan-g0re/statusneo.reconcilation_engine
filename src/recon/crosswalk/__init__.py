"""The crosswalk: resolving an inbound document's keys to the episode it belongs to.

Necessary because no identifier is shared across all four feeds.  Three bridges, each
with a documented failure mode (``docs/architecture_decisions.md`` §B):

* **PBM <-> 340B** — the natural key only.  Breaks on TPA lag and on retroactive
  eligibility flips.
* **837 <-> 835** — the ``CLM01``/``CLP01`` round trip.  Breaks when the payer
  reassigns an ICN on reprocessing, which is why ``PAYER_ICN`` is a key we *record*
  and never a key we treat as stable identity.
* **835 <-> bank** — ``TRN02``.  Breaks constantly, because most bank exports drop the
  CCD+ addenda that carries it.

``keys`` is the single authority on the canonical string form of every key.  Nothing
else in the codebase may build a ``key_value``: two spellings of the same key are two
keys, and the bug is silent.
"""

from __future__ import annotations

from recon.crosswalk import keys

__all__ = ["keys"]
