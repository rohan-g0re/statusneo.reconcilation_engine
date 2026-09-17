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
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Iterable, Protocol, runtime_checkable

from recon import config

if TYPE_CHECKING:  # pragma: no cover - import cycle guard, types only
    from recon.connectors.registry import Source

__all__ = [
    "Document",
    "ForbiddenPathError",
    "LocalDirectoryTransport",
    "ShouldFetch",
    "Transport",
    "TransportError",
]

#: ``(document_name, remote_mtime) -> should it be transferred``.
#:
#: The mtime is text in this repository's one timestamp format, not an epoch float, because
#: it goes straight into a checkpoint row next to every other timestamp — and because fixed
#: width UTC compares lexicographically in the same order it compares chronologically, which
#: is the property the whole cursor design rests on.  ``None`` when the remote reports none,
#: which is normal and is why the checkpoint also stores a content hash.
ShouldFetch = Callable[[str, str | None], bool]


def _utc_stamp(epoch_seconds: float) -> str:
    """A filesystem mtime in the repository's timestamp format.

    Truncated to whole seconds deliberately.  Sub-second precision would make the checkpoint
    comparison depend on a filesystem's granularity, so the same file copied onto a different
    volume would look modified — a re-download that nothing could explain.
    """
    return datetime.fromtimestamp(epoch_seconds, tz=timezone.utc).strftime(
        config.TIMESTAMP_FORMAT
    )

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
    #: When the source last modified it, in this repository's timestamp format, or ``None``
    #: when the source does not report one.
    #:
    #: Metadata, not a handle — which is the distinction this class is built around. A
    #: consumer still cannot walk anywhere from a ``Document``; it simply now knows enough to
    #: write a checkpoint. Without this the checkpoint stored ``None`` for every document,
    #: the next run had nothing to compare against, and requirement A4's "the second run
    #: downloads zero bytes" quietly degraded to "ingests zero records" — which was already
    #: true before any checkpoint existed.
    modified_at: str | None = None


@runtime_checkable
class Transport(Protocol):
    """Obtain the documents a source currently offers.

    Intentionally one method.  Listing, filtering by checkpoint and downloading are the
    implementation's business; the caller only ever needs "what have you got".
    """

    kind: str

    def fetch(
        self,
        source: "Source",
        *,
        should_fetch: "ShouldFetch | None" = None,
    ) -> Iterable[Document]:
        """Yield the documents this source offers, in a stable order.

        ``should_fetch`` is the checkpoint seam (requirements A4, A5).  It is consulted
        **after** listing and **before** the bytes are pulled, which is what makes "the
        second run downloads zero bytes" literally true rather than merely "ingests zero
        records" — the latter was already guaranteed by file-level idempotency and is a
        weaker promise than the requirement makes.

        It is an argument rather than a dependency so that no transport imports
        ``connectors.checkpoint``: a transport that knew about checkpoint storage would be
        a transport that could not be tested without a database.
        """
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

    def fetch(
        self,
        source: "Source",
        *,
        should_fetch: "ShouldFetch | None" = None,
    ) -> Iterable[Document]:
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
            # Asked before the read, not after, so a skipped document costs no I/O.  The
            # mtime is rendered the way every other timestamp in this repository is, because
            # it is about to be stored in a checkpoint row alongside them.
            modified_at = _utc_stamp(path.stat().st_mtime)
            if should_fetch is not None and not should_fetch(name, modified_at):
                continue
            yield Document(
                name=name,
                text=path.read_text(encoding="utf-8"),
                modified_at=modified_at,
            )

    def __repr__(self) -> str:  # pragma: no cover - diagnostic only
        return f"LocalDirectoryTransport({str(self._root)!r})"
