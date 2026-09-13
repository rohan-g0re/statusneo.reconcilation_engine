"""Shared fixtures.

The repo is laid out ``src/recon/...`` and the test suite imports ``recon`` as an
installed package would.  ``sys.path`` is extended here rather than relying on an
editable install, so ``pytest`` works in a bare checkout with no build step — which is
what "clone it and run the demo" has to mean.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

DECISION_TREE = REPO_ROOT / "decision_tree"


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(scope="session")
def pairs_oracle() -> list[dict]:
    """``decision_tree/pairs.json`` — the live oracle (Decision C17 / 48).

    Not a fixture file copied into ``tests/``: the real artefact the exhaustive
    generator wrote.  If the state space moves and this suite does not, the tests that
    read this fail, which is the entire point of making it an oracle rather than a
    document.
    """
    import json

    path = DECISION_TREE / "pairs.json"
    assert path.exists(), (
        f"{path} is missing. It is a live test oracle, not an optional artefact; "
        "rebuild it with `python decision_tree/classify.py`."
    )
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture()
def conn() -> sqlite3.Connection:
    """An in-memory database with the schema applied."""
    from recon.db import connection, migrate

    handle = connection.connect(":memory:")
    migrate.ensure_schema(handle)
    try:
        yield handle
    finally:
        handle.close()


@pytest.fixture()
def settings(tmp_path: Path):
    """Settings pointed at a temp directory, so no test touches ``data/``."""
    from recon.config import load_settings

    return load_settings("demo", data_dir=tmp_path)
