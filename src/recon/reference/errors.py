"""Domain errors raised by the reference layer.

These cross a package boundary into the generators and the engine, so they are named
domain errors rather than a bare ``KeyError`` on a dictionary nobody outside this
package can see.
"""

from __future__ import annotations

__all__ = ["ReferenceError", "UnknownNdcError", "UnknownEntityError", "NoContractTermsError"]


class ReferenceError(LookupError):
    """Base class for every reference-data lookup failure."""


class UnknownNdcError(ReferenceError):
    def __init__(self, ndc11: str) -> None:
        super().__init__(f"no drug in the reference table with NDC {ndc11!r}")
        self.ndc11 = ndc11


class UnknownEntityError(ReferenceError):
    def __init__(self, kind: str, identifier: str) -> None:
        super().__init__(f"no {kind} in the reference table with id {identifier!r}")
        self.kind = kind
        self.identifier = identifier


class NoContractTermsError(ReferenceError):
    def __init__(self, payer_id: str, ndc11: str) -> None:
        super().__init__(f"no contract terms for payer {payer_id!r} and NDC {ndc11!r}")
        self.payer_id = payer_id
        self.ndc11 = ndc11
