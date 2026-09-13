"""The one shape every tool call returns, and the fence that marks feed-derived text.

Two problems live here because they are the same problem seen from two sides: a model
must never be able to tell the difference between "this call succeeded with an empty
result" and "this call is broken", and it must never be able to tell the difference
between "text I can act on" and "text an outside party wrote" without a marker doing
that work for it.

**Why constructors, not a literal dict.**  Seven tools each return one of two shapes.
Written as `{"status": "ok", ...}` seven times, `retryable` becomes something every
tool author has to remember to set correctly, and a copy-pasted `False` where a
`db_error` needed `True` is a silent trust bug rather than a loud one.  `ok()` and
`err()` are the only two ways to build a :class:`ToolEnvelope`, so `retryable` is
*derived* from `error_type` in exactly one place (`agent_layer_design.md` closing
rule: model-visible means audited).  ``tools.py`` is checked by an AST-walking test
to contain no `{"status":` literal, which is what makes this true by construction
rather than by convention.

**Why the untrusted-text fence lives beside the envelope, not inside `tools.py`.**
The fence is part of what a model *receives*, exactly like the envelope shape is --
both are contract, not tool-specific logic.  Putting `wrap`/`unwrap` here also means
the harness's citation verifier, the eval fixtures and a future MCP client can
`import recon.agents.envelope` and understand a result without importing
``recon.agents.tools`` at all, which is the one file in the whole agent layer that is
allowed to touch ``sqlite3`` (`spec_tools.md` S0).

The delimiters are U+27E6 / U+27E7 (`⟦` / `⟧`), measured to occur zero times across
every payload in the full profile (`spec_tools.md` S5.3).  They are not typeable by
accident, so their presence in an argument or a summary is itself a signal worth
catching -- which `dispatch()` and `create_mock_work_item` both do.
"""

from __future__ import annotations

import re
from typing import Any, Literal, TypedDict

__all__ = [
    "ErrorType",
    "ToolEnvelope",
    "ok",
    "err",
    "UNTRUSTED_OPEN",
    "UNTRUSTED_CLOSE",
    "wrap",
    "unwrap",
    "contains_wrapper_markers",
]

ErrorType = Literal[
    "not_found",
    "invalid_input",
    "ambiguous",
    "not_permitted",
    "duplicate_call_blocked",
    "db_error",
]


class ToolEnvelope(TypedDict):
    """The one shape every tool returns. Five keys, always all five present."""

    status: Literal["ok", "error"]
    data: Any | None  # the payload on ok; ALWAYS None on error
    error_type: ErrorType | None  # ALWAYS None on ok
    message: str  # "" on plain ok; non-empty and actionable otherwise
    retryable: bool  # False on ok


def ok(data: Any, message: str = "") -> ToolEnvelope:
    """The only way to build a success envelope."""
    return {
        "status": "ok",
        "data": data,
        "error_type": None,
        "message": message,
        "retryable": False,
    }


def err(error_type: ErrorType, message: str) -> ToolEnvelope:
    """The only way to build a failure envelope.

    ``retryable`` is derived from ``error_type``, never accepted as a parameter --
    today that means "true iff db_error" (`spec_tools.md` S1.3), and the single
    place that rule is stated is here, not repeated at every call site.
    """
    return {
        "status": "error",
        "data": None,
        "error_type": error_type,
        "message": message,
        "retryable": error_type == "db_error",
    }


# --- the untrusted-text fence ------------------------------------------------

#: U+27E6 / U+27E7 -- mathematical white square brackets. Zero measured occurrences
#: across the full profile's payloads, so their appearance anywhere else is itself
#: suspicious rather than a false positive waiting to happen.
UNTRUSTED_OPEN = "⟦"
UNTRUSTED_CLOSE = "⟧"

# The closing delimiter is OPEN + "/UNTRUSTED:#<nonce>" + CLOSE -- an opening
# bracket again, not a mirrored closing one -- exactly matching what `wrap()` emits.
_WRAP_RE = re.compile(
    r"^⟦UNTRUSTED:(?P<path>[^#⟧]*)#(?P<nonce>[0-9a-fA-F]+)⟧"
    r"(?P<body>.*)"
    r"⟦/UNTRUSTED:#(?P=nonce)⟧$",
    re.DOTALL,
)


def wrap(field_path: str, content: str, nonce: str) -> str:
    """Fence one piece of feed-derived text.

    ``⟦UNTRUSTED:<field_path>#<nonce>⟧<content>⟦/UNTRUSTED:#<nonce>⟧`` -- no escaping
    and no mutation of ``content``.  The nonce, not escaping, is what makes forgery
    infeasible (an attacker wrote the feed before the run's nonce existed), and
    preserving the bytes exactly is load-bearing: every citation is verified by
    ``str.find()`` against this string (`spec_tools.md` S5.3).
    """
    return (
        f"{UNTRUSTED_OPEN}UNTRUSTED:{field_path}#{nonce}{UNTRUSTED_CLOSE}"
        f"{content}"
        f"{UNTRUSTED_OPEN}/UNTRUSTED:#{nonce}{UNTRUSTED_CLOSE}"
    )


def unwrap(value: str) -> str:
    """Strip one wrapper, or return ``value`` unchanged if it is not wrapped.

    Used by the eval set, the UI and the citation verifier -- all of which need to
    read a result in isolation, without re-deriving which nonce produced it.
    """
    match = _WRAP_RE.match(value)
    return match.group("body") if match else value


def contains_wrapper_markers(value: str) -> bool:
    """True if ``value`` echoes either delimiter, wrapped or not.

    Used at the write boundary (`spec_tools.md` S2.7 step 5): a summary that carries
    ``⟦UNTRUSTED`` forward means the model copied an outside document's framing into
    its own output, which is a quality smell and the visible edge of an injection
    attempt, regardless of whether the fence still parses as a complete pair.
    """
    return "⟦UNTRUSTED" in value or "⟧/UNTRUSTED:" in value
