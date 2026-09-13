"""Seeded randomness, derived rather than shared.

Four generators run in any order and produce byte-identical output across runs,
machines, processes and ``PYTHONHASHSEED`` values.  Two choices buy that.

**Seeds are derived from a path, with ``blake2b``.**  Python's built-in ``hash()``
for strings is salted per process by ``PYTHONHASHSEED``; a seed built on it would
change between two runs on the same machine, which is precisely the failure the
assignment asks us to avoid.  ``blake2b`` is standard library, stable across Python
versions and platforms, and fast enough that per-episode derivation is free.

**Every stream gets its own ``Random``.**  With one shared mutable generator, a
stream's output depends on how many draws every *other* stream made first — so
generator order matters, and adding one episode reshuffles the whole dataset.  With
derived per-path seeds, a stream depends only on its own path.  Order independence
and insertion independence both fall out rather than being maintained by discipline.

The canonical string is fixed and must not be "tidied"::

    canonical = f"{master_seed}|{'/'.join(str(p) for p in path)}"
    seed      = int.from_bytes(blake2b(canonical.encode("utf-8"), digest_size=8).digest(), "big")

A test pins it with hardcoded expected seeds.  Changing the separator would
invalidate every previously published manifest hash.

Path convention, so four generators agree without coordinating::

    rng_for(settings, profile_name, generator_name)               # generator-level draws
    rng_for(settings, profile_name, generator_name, episode_id)   # per-episode draws

The profile name is part of the path, which makes ``demo`` and ``full`` independent
datasets rather than one being a prefix of the other.
"""

from __future__ import annotations

import hashlib
import random
from typing import Sequence, TypeVar

__all__ = [
    "derive_seed",
    "rng_for",
    "stable_choice",
    "stable_sample",
    "stable_shuffle",
    "canonical_seed_string",
]

T = TypeVar("T")

_DIGEST_SIZE = 8
_UNORDERED = (set, frozenset, dict)


def canonical_seed_string(master_seed: int, *path: str | int) -> str:
    """The exact string that gets hashed.  Exposed so a test can pin the format."""
    if isinstance(master_seed, bool) or not isinstance(master_seed, int):
        raise TypeError(f"master_seed must be an int, got {type(master_seed).__name__}")
    parts: list[str] = []
    for element in path:
        if isinstance(element, bool) or not isinstance(element, (str, int)):
            raise TypeError(
                "seed path elements must be str or int, got "
                f"{type(element).__name__}. Stringifying an arbitrary object would "
                "put its memory address in the seed and destroy reproducibility."
            )
        text = str(element)
        if "/" in text:
            raise ValueError(
                f"seed path element {element!r} contains '/', which is the path "
                "separator; two different paths would collide onto one seed."
            )
        parts.append(text)
    return f"{master_seed}|{'/'.join(parts)}"


def derive_seed(master_seed: int, *path: str | int) -> int:
    """A pure, order-independent, insertion-independent 64-bit seed for one stream."""
    canonical = canonical_seed_string(master_seed, *path)
    digest = hashlib.blake2b(canonical.encode("utf-8"), digest_size=_DIGEST_SIZE).digest()
    return int.from_bytes(digest, "big")


def rng_for(settings, *path: str | int) -> random.Random:
    """A *fresh* ``random.Random`` for one stream.

    Two calls with the same path return two independent generators that produce the
    same sequence.  Draining one has no effect on the other, so a caller never has to
    reason about what else has already drawn from "the" generator.

    ``settings`` is anything carrying a ``master_seed`` attribute (a
    :class:`recon.config.Settings`), or a bare ``int`` seed.
    """
    master_seed = settings if isinstance(settings, int) else getattr(settings, "master_seed")
    return random.Random(derive_seed(master_seed, *path))


def _require_ordered(seq: Sequence[T], func_name: str) -> Sequence[T]:
    if isinstance(seq, _UNORDERED):
        raise TypeError(
            f"{func_name}() requires an ordered sequence; got {type(seq).__name__}. "
            "Iterating a set or dict yields an order that depends on insertion "
            "history and string hashing, so the draw would not be reproducible. "
            "Pass sorted(...) or an explicit tuple."
        )
    if not isinstance(seq, Sequence):
        raise TypeError(
            f"{func_name}() requires an ordered sequence, got {type(seq).__name__}"
        )
    return seq


def stable_choice(rng: random.Random, seq: Sequence[T]) -> T:
    """``rng.choice`` that refuses unordered collections."""
    return rng.choice(_require_ordered(seq, "stable_choice"))


def stable_sample(rng: random.Random, seq: Sequence[T], k: int) -> list[T]:
    """``rng.sample`` that refuses unordered collections."""
    return rng.sample(_require_ordered(seq, "stable_sample"), k)


def stable_shuffle(rng: random.Random, seq: Sequence[T]) -> list[T]:
    """Return a shuffled *copy*; the input is left alone."""
    items = list(_require_ordered(seq, "stable_shuffle"))
    rng.shuffle(items)
    return items
