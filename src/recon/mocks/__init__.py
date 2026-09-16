"""Local stand-ins for vendor systems nobody can give us credentials to.

Requirement group M of ``docs/connectivity_layer_requirements.md``.  Beacon, Verity and
Craneware are contracted enterprise products gated behind a covered entity, with no
self-service signup, so every source the connector layer talks to is served from here.

**These are formatters, not generators, and the distinction is load-bearing.**

``recon.generators`` is built backwards from a proof.  ``decision_tree/`` enumerates the
state space, ``sampling.py`` picks leaves stratified over verdicts, and the orchestrator
turns each leaf into facts — costing it into integer cents through the same pricing module
the engine uses and minting every cross-feed identifier.  Each generator then receives a
*blind slice*: ``generators/contracts.py`` asserts at runtime that no generator can see an
episode id, a verdict or an expected amount, and that assertion is the only reason
crosswalk accuracy is a measured number rather than a claim.

A new Beacon or Verity *generator* would have to be handed the answer in order to produce
a consistent story, which deletes that guarantee.  A *formatter* re-dresses data that has
already passed the blind-slice check, so the guarantee survives intact.

Concretely, every module in this package obeys three rules, each enforced by a test in
``tests/test_mocks.py`` rather than by good intentions:

1. **No domain logic.**  Nothing here decides whether a claim qualified, what it was worth
   or when it arrived.  Those were decided upstream and are read back.
2. **No money arithmetic.**  Amounts are copied verbatim from the feed as text.  A mock
   that could add two amounts could disagree with the ledger, and the reconciliation it
   was built to exercise would be measuring the mock.
3. **No privileged imports.**  Nothing here imports ``recon.reference.pricing``,
   ``decision_tree``, or anything that reads ``ground_truth``.

The one thing the orchestrator gained for this package is identifier minting — ``beacon_id``,
``accumulation_id`` and ``invoice_number``, written to a sidecar beside the feeds.  They
live there for the same reason ``trn02`` and ``allocation_code`` do: a value two independent
systems must agree on may be decided by exactly one component.  A mock that minted its own
Beacon ID would be inventing a fact the rest of the system then has to accept on faith.
"""

from __future__ import annotations

__all__ = ["source"]
