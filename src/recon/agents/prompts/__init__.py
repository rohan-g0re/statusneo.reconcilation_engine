"""Prompt text for the agent layer's model-facing roles.

Three modules, one per role that talks to a model (`investigator`, `proposer`,
`evaluator`), plus `_shared` for the two rules `spec_prompts_roles.md` §0 requires to
be byte-identical across all three. Each role module exposes its prompt text as
module-level string constants -- so a diff against the spec is a text diff, not a
runtime trace -- and a `render(**context) -> str` function that fills the per-run
placeholders explicitly, raising rather than silently leaving a `{brace}` unfilled.

This package may import from `recon.agents.schemas` and nothing else in
`recon.agents.*` (`.agents/specs/spec_contracts.md` §5) -- in practice it does not need
to, since every schema-level concern (the closed action vocabulary, the citation kinds)
already has its own home in `schemas.py` and the prompt text below states the same
vocabulary as prose for a model to read, not as a runtime dependency.
"""

from __future__ import annotations

from recon.agents.prompts import evaluator, investigator, proposer
from recon.agents.prompts._shared import NO_ARITHMETIC_RULE, NONCE_RULE, UNTRUSTED_TEXT_RULE

__all__ = [
    "investigator",
    "proposer",
    "evaluator",
    "NO_ARITHMETIC_RULE",
    "UNTRUSTED_TEXT_RULE",
    "NONCE_RULE",
]
