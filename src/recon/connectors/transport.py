"""The seam between "where the bytes live" and "what the bytes mean".

Requirement A2.  One protocol, and implementations that differ only in how they obtain a
document.  Everything downstream — ``_read_rows``, ``ingest()``, ``_attach``, the allocator —
is untouched by which one ran, because it operates on ``raw_record`` rows and a row is a row
regardless of how it arrived.  That is the payoff of the original event-driven decision, and
it is the reason this refactor is additive rather than invasive.

**A transport returns documents, not paths.**  That is the whole design.  Requirement §4.9
warns that generalising a feeds directory into a transport threatens the structural
guarantee that ingestion cannot read ``truth/ground_truth.json`` — because a directory
argument *cannot* reach ground truth, while an arbitrary transport could.  So the protocol
hands back :class:`Document` objects with a name and text and no filesystem handle at all,
and the one implementation that does touch a filesystem refuses ground truth explicitly and
is tested for it.

The refusal is a named exception, never a silent skip.  A transport that quietly returned
nothing when pointed somewhere forbidden would produce an empty ingest that looks exactly
like a clean one.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Iterable, Protocol, runtime_checkable

from recon import config

if TYPE_CHECKING:  # pragma: no cover - import cycle guard, types only
    from recon.connectors.registry import Source

__all__ = [
    "Document",
    "ForbiddenPathError",
    "LocalDirectoryTransport",
    "Transport",
    "TransportError",
]

#: Path components a transport may never resolve through.  ``truth`` is the ground-truth
#: directory (``config.TRUTH_SUBDIR``); the rest are the ways a name escapes its root.
_FORBIDDEN_COMPONENTS = frozenset({config.TRUTH_SUBDIR, "..", ""})


class TransportError(RuntimeError):
    """A fetch failed. Raised, never swallowed — see the module docstring."""


class ForbiddenPathError(TransportError):
    """A transport was asked for something outside the data it is allowed to see."""


@dataclass(frozen=True, slots=True)
class Document:
    """One fetched artefact: a logical name and its decoded text.

    No path, no handle, no directory. A consumer holding a :class:`Document` cannot walk
    anywhere from it, which is what makes requirement §4.9's guarantee survive the move from
    "a feeds directory" to "a transport".
    """

    name: str
    text: str


@runtime_checkable
class Transport(Protocol):
    """Obtain the documents a source currently offers.

    Intentionally one method.  Listing, filtering by checkpoint and downloading are the
    implementation's business; the caller only ever needs "what have you got".
    """

    kind: str

    def fetch(self, source: "Source") -> Iterable[Document]:
        """Yield every document this source offers, in a stable order."""
        ...


def _reject_unsafe_name(name: str) -> None:
    """Refuse a document name that is anything other than a bare filename.

    Checked on the *name* before it is joined, not on the result afterwards.  A check that
    ran after joining would have already built the path it was trying to forbid, and on
    Windows a rejected-but-constructed path is still one ``open()`` away from being read.
    """
    candidate = Path(name)
    if candidate.is_absolute() or len(candidate.parts) != 1:
        raise ForbiddenPathError(
            f"{name!r} is not a bare filename; a transport may not traverse directories"
        )
    if name in _FORBIDDEN_COMPONENTS:
        raise ForbiddenPathError(f"{name!r} is a forbidden path component")


class LocalDirectoryTransport:
    """Today's behaviour, named.

    Reads a fixed set of filenames out of one directory.  It is the transport every existing
    test runs through, and it must remain byte-for-byte equivalent to the loop it replaced:
    same files, same order, same skip-when-absent, same text.

    The root is resolved once at construction and every fetch is checked against it, so a
    source cannot widen its own reach after the fact.
    """

    kind = "LOCAL_DIRECTORY"

    __slots__ = ("_root",)

    def __init__(self, root: Path | str) -> None:
        resolved = Path(root).resolve()
        if config.TRUTH_SUBDIR in resolved.parts:
            raise ForbiddenPathError(
                f"{resolved} is inside the ground-truth directory. Ingestion is forbidden to "
                "read truth/, and that prohibition is what makes the crosswalk a measurement "
                "rather than a claim."
            )
        self._root = resolved

    @property
    def root(self) -> Path:
        return self._root

    def fetch(self, source: "Source") -> Iterable[Document]:
        for name in source.filenames:
            _reject_unsafe_name(name)
            path = (self._root / name).resolve()
            if self._root not in path.parents and path != self._root:
                raise ForbiddenPathError(f"{name!r} resolves outside {self._root}")
            if config.TRUTH_SUBDIR in path.parts:
                raise ForbiddenPathError(f"{name!r} resolves into the ground-truth directory")
            if not path.exists():
                # Absent is not an error.  A profile that generated five of six feeds is a
                # smaller dataset, not a broken one, and the loader has always behaved this
                # way — preserving it is part of "the existing suite passes unedited".
                continue
            yield Document(name=name, text=path.read_text(encoding="utf-8"))

    def __repr__(self) -> str:  # pragma: no cover - diagnostic only
        return f"LocalDirectoryTransport({str(self._root)!r})"
