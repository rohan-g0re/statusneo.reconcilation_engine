"""The reproducibility manifest: shape, writer and comparison helper.

The assignment asks for "a script that generates the data so the results can be
reproduced".  ``manifest.json`` is what turns that from a claim into an assertion: it
records the seed, the window, the reference-data fingerprint and a SHA-256 per feed
file, so regenerating into a fresh directory and diffing the manifests is a one-line
test.

Group 1 owns the shape and the comparison helper so that the reproducibility test is
shared rather than reimplemented per group; Group 2 owns the act of writing it during
a generation run.

Note what is *not* claimed: the SQLite database is not byte-reproducible — it carries
load wall-clocks and is a derived artefact anyway.  The **feeds** are reproducible,
and this file is the contract for that.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path

__all__ = ["Manifest", "file_sha256", "build_manifest", "write_manifest", "read_manifest", "diff_manifests"]

_CHUNK = 1 << 20


@dataclass(frozen=True, slots=True)
class Manifest:
    """Everything needed to decide whether two generation runs agree."""

    profile: str
    master_seed: int
    episode_count: int
    window_start: str
    window_end: str
    reference_fingerprint: str
    files: dict[str, str]  # filename -> sha256 hex

    def to_json(self) -> str:
        """Canonical JSON: sorted keys, fixed separators, trailing newline."""
        return json.dumps(asdict(self), sort_keys=True, indent=2) + "\n"

    @classmethod
    def from_json(cls, text: str) -> "Manifest":
        return cls(**json.loads(text))


def file_sha256(path: Path | str) -> str:
    """SHA-256 of a file's bytes, streamed."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def build_manifest(
    *,
    profile: str,
    master_seed: int,
    episode_count: int,
    window_start: str,
    window_end: str,
    reference_fingerprint: str,
    feeds_dir: Path | str,
    filenames: tuple[str, ...],
) -> Manifest:
    """Hash every named feed file in ``feeds_dir``.

    Raises:
        FileNotFoundError: if a named feed is missing, naming the path.  A manifest
            that silently omits a feed would compare equal to one that has it.
    """
    feeds = Path(feeds_dir)
    hashes: dict[str, str] = {}
    for name in filenames:
        path = feeds / name
        if not path.is_file():
            raise FileNotFoundError(f"feed file missing, cannot manifest it: {path}")
        hashes[name] = file_sha256(path)
    return Manifest(
        profile=profile,
        master_seed=master_seed,
        episode_count=episode_count,
        window_start=window_start,
        window_end=window_end,
        reference_fingerprint=reference_fingerprint,
        files=hashes,
    )


def write_manifest(manifest: Manifest, path: Path | str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(manifest.to_json(), encoding="utf-8")


def read_manifest(path: Path | str) -> Manifest:
    return Manifest.from_json(Path(path).read_text(encoding="utf-8"))


def diff_manifests(left: Manifest, right: Manifest) -> list[str]:
    """Human-readable differences; empty means the two runs produced the same data."""
    problems: list[str] = []
    for attribute in (
        "profile",
        "master_seed",
        "episode_count",
        "window_start",
        "window_end",
        "reference_fingerprint",
    ):
        lhs, rhs = getattr(left, attribute), getattr(right, attribute)
        if lhs != rhs:
            problems.append(f"{attribute}: {lhs!r} != {rhs!r}")
    for name in sorted(set(left.files) | set(right.files)):
        lhs, rhs = left.files.get(name), right.files.get(name)
        if lhs != rhs:
            problems.append(f"files[{name}]: {lhs} != {rhs}")
    return problems
