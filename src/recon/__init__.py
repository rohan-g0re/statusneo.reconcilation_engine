"""Specialty-pharmacy reconciliation prototype.

This module exports ``__version__`` and nothing else, deliberately.

A package ``__init__`` that imported submodules would drag ``recon.db`` into the
generator's import graph and ``recon.api`` into the engine's, quietly breaking the
layering rule (Decision 36) while every other test still passed.  Keep it empty.
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
