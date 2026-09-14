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
    "open_marker",
    "close_marker",
    "fence_regex",
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

#: `envelope.py` is the single fence authority (spec_fixes_round1.md Decision 1):
#: every other module that needs to recognise or build a fence --
#: `prompts/_shared.py`, `scorers.py`, this module's own `wrap`/`unwrap` -- imports
#: `open_marker`/`close_marker`/`fence_regex`/`contains_wrapper_markers` rather than
#: spelling any part of this shape out a second time. Before this fix, three
#: different fence formats were in play across the codebase (`wrap()`'s `⟦...⟧`, a
#: second, angle-bracketed shape the prompts taught, and a regex in `scorers.py`
#: matching that second shape); nothing converted between them, so a masking check
#: built against one format silently never matched what the other produced. (Not
#: spelling the dead shape out here either -- a test asserts it appears nowhere in
#: `src/`.)
_TAG_PREFIX = "UNTRUSTED:"


def open_marker(field_path: str, nonce: str) -> str:
    """The opening half of one fence: ``⟦UNTRUSTED:<field_path>#<nonce>⟧``."""
    return f"{UNTRUSTED_OPEN}{_TAG_PREFIX}{field_path}#{nonce}{UNTRUSTED_CLOSE}"


def close_marker(nonce: str) -> str:
    """The closing half of one fence: ``⟦/UNTRUSTED:#<nonce>⟧``.

    Another OPEN bracket, not a mirrored CLOSE one -- exactly what `wrap()` has
    always emitted, and the shape `contains_wrapper_markers` used to get wrong
    (reviewer finding 17: it tested for a ``⟧``-prefixed closing tag that `wrap()`
    never produces).
    """
    return f"{UNTRUSTED_OPEN}/{_TAG_PREFIX}#{nonce}{UNTRUSTED_CLOSE}"


def fence_regex(nonce: str | None = None) -> re.Pattern[str]:
    """A regex matching one complete fence, open marker through close marker.

    ``nonce=None`` matches ANY run's fence -- what "the model must not itself emit a
    fence" (Decision 2) needs to check. A concrete nonce matches only this run's --
    what masking text the harness itself injected from a tool result needs. Both are
    built from the same two constants and the same tag text `open_marker`/
    `close_marker` use, so this can never drift from what `wrap()` actually emits.
    """
    nonce_group = re.escape(nonce) if nonce is not None else "[0-9a-fA-F]+"
    open_re = re.escape(UNTRUSTED_OPEN)
    close_re = re.escape(UNTRUSTED_CLOSE)
    return re.compile(
        rf"{open_re}{_TAG_PREFIX}(?P<path>[^#{close_re}]*)#(?P<nonce>{nonce_group}){close_re}"
        rf"(?P<body>.*)"
        rf"{open_re}/{_TAG_PREFIX}#(?P=nonce){close_re}",
        re.DOTALL,
    )


#: Matches any run's fence, required to run to the end of the string (`.match()`
#: already anchors at the start) -- what `unwrap()` needs on every call. Built once
#: at import time, from `fence_regex()` itself, rather than recompiled per call --
#: `unwrap()` runs once per feed-derived string leaf.
_ANY_FENCE_RE = re.compile(fence_regex().pattern + r"\Z", re.DOTALL)


def wrap(field_path: str, content: str, nonce: str) -> str:
    """Fence one piece of feed-derived text.

    ``⟦UNTRUSTED:<field_path>#<nonce>⟧<content>⟦/UNTRUSTED:#<nonce>⟧`` -- no escaping
    and no mutation of ``content``.  The nonce, not escaping, is what makes forgery
    infeasible (an attacker wrote the feed before the run's nonce existed), and
    preserving the bytes exactly is load-bearing: every citation is verified by
    ``str.find()`` against this string (`spec_tools.md` S5.3).
    """
    return f"{open_marker(field_path, nonce)}{content}{close_marker(nonce)}"


def unwrap(value: str) -> str:
    """Strip one wrapper, or return ``value`` unchanged if it is not wrapped.

    Used by the eval set, the UI and the citation verifier -- all of which need to
    read a result in isolation, without re-deriving which nonce produced it.
    """
    match = _ANY_FENCE_RE.match(value)
    return match.group("body") if match else value


def contains_wrapper_markers(text: str) -> bool:
    """True if ``text`` contains either delimiter's marker text, wrapped or not.

    Used at the write boundary (`spec_tools.md` S2.7 step 5): a summary that carries
    an opening or closing marker forward means the model copied an outside
    document's framing into its own output (or, per Decision 2, authored a fence of
    its own), which must be caught regardless of whether a complete pair is present.

    Built from `UNTRUSTED_OPEN`/`UNTRUSTED_CLOSE` and the tag text `open_marker`/
    `close_marker` emit, rather than a hand-typed literal -- a hand-typed copy is
    exactly the drift that produced the reviewer-cited typo this replaces (finding
    17: the previous check tested for ``"⟧/UNTRUSTED:"``, a CLOSE bracket, where
    every marker this module emits starts with the OPEN bracket, so a closing marker
    could never match).
    """
    return f"{UNTRUSTED_OPEN}{_TAG_PREFIX}" in text or f"{UNTRUSTED_OPEN}/{_TAG_PREFIX}" in text
