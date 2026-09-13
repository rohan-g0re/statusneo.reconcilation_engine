"""The HTTP layer: ``service`` holds the read model, ``app`` holds the transport.

Split so the entire read surface is testable without HTTP, and so FastAPI never becomes a place
logic accumulates.  ``service`` imports no web framework at all.

Nothing in this package computes a number.  Every amount, variance, disposition, reason code and
ranking is read back from the verdict log exactly as the deterministic engine wrote it — which is
the same boundary the agent layer will sit behind, for the same reason.
"""

from __future__ import annotations

__all__ = ["app", "service"]
