"""The two roles that actually talk to a model: the Exception Investigator and the
Workflow Coordinator.

The Portfolio Analyst named in `docs/agent_layer_design.md`'s three-role sketch is not
built here -- `docs/agent_layer_readiness.md` and `docs/remaining_work.md` scope this
assignment down to the two roles the analysis screen actually wires up (design S:8.6:
"Explain" and "Decide next steps"), and this package matches that scope rather than the
design doc's original three-role list.

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
