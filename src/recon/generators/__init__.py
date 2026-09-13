"""The four source-feed generators, plus the orchestrator that drives them.

The package boundary matters here.  :mod:`recon.generators.orchestrator` is the only module
that may import :mod:`recon.generators.plan` or :mod:`recon.generators.sampling`; the four
generators (:mod:`~recon.generators.pbm`, :mod:`~recon.generators.medical`,
:mod:`~recon.generators.tpa`, :mod:`~recon.generators.bank`) import nothing but
:mod:`~recon.generators.contracts`, the reference package and the money primitives.

That is enforced by a test, not by convention, because it is the property the whole
generator design rests on: a generator that can reach the orchestrator can see the episode,
and a generator that can see the episode can leak a shared key across feeds — which would
quietly reduce the connector under test to a no-op join (Decision 10).

Nothing in this package imports :mod:`recon.db`.  Generators write files; they have no
database at all.
"""

from __future__ import annotations

__all__ = ["contracts"]
