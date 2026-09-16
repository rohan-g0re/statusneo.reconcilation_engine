"""The three roles that talk to a model: the Exception Investigator, the Workflow
Coordinator and the Portfolio Analyst.

This docstring previously said the Portfolio Analyst was *not* built here, and pointed
at `docs/agent_layer_readiness.md` for the scoping decision that excluded it. Both
statements are now false and one of them never resolved: `analyst.py` sits in this
package. (The readiness document it cited has since been removed.) The
exclusion was real when it was written -- the design's three-role sketch was scoped down
to the two roles the analysis screen wires up (design S:8.6, "Explain" and "Decide next
steps") -- and it went away when the third role shipped onto the dashboard.

`investigator.py` is a single pass with no propose/evaluate/score/gate -- it does not
use `harness.run_until` at all, because there is nothing to gate: one tool-calling loop,
then cited prose, per `spec_prompts_roles.md` S:A.2's own "Shape" line. `coordinator.py`
is the one module that actually builds the two `harness.Propose`/`harness.Evaluate`
closures and hands them to `harness.run_until` -- it is the only place in the agent
layer that constructs both the Proposer and the Evaluator for one run.

Both roles accept an injected `recon.agents.client.LLMClient` rather than constructing
one, so every test in `tests/test_agents_roles.py` runs with no network and no API key.
"""

from __future__ import annotations

from recon.agents.roles import coordinator, investigator

__all__ = ["investigator", "coordinator"]
