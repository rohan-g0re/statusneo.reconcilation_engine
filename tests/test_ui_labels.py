"""The UI's label table has to agree with the engine's vocabularies.

`web/src/labels.js` carries a plain-English name for every code the dashboard renders. It has
to: the API serves codes, never prose, so the words can only live on the client. That makes it
the one place in the repository where an engine vocabulary is restated by hand, and a restatement
nobody checks is a restatement that drifts.

Two failure modes, both silent in a browser:

  * The engine grows a code and the UI does not. The dashboard renders a raw token in the middle
    of a column of sentences. Nothing errors.
  * The UI carries a label for a code the engine does not have. Dead copy that reads as coverage.
    The first draft of labels.js had five of these and was missing nine real codes, and every
    screen looked fine.

So these tests read the JavaScript as text and compare it against the enums themselves. Parsing
JS with a regex is ugly and it is the right trade here: the alternative is a Node dependency in a
Python suite to check what is, structurally, a flat table of string constants.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from recon.domain.enums import (
    CrossTrackFlag,
    Disposition,
    KeyType,
    ParkReason,
    ReasonCode,
    RecordKind,
)
from recon.domain.verdicts import RETIRED_CODES, VERDICT_DESCRIPTIONS

LABELS_JS = Path(__file__).resolve().parents[1] / "web" / "src" / "labels.js"


@pytest.fixture(scope="module")
def source() -> str:
    return LABELS_JS.read_text(encoding="utf-8")


def table_keys(source: str, declaration: str) -> set[str]:
    """Every key of one `const NAME = { ... }` object literal in labels.js."""
    block = re.search(rf"{declaration}\s*=\s*\{{(.*?)\n\}}", source, re.S)
    assert block, f"no object literal named {declaration} in labels.js"
    keys = re.findall(r"^\s{2}('[^']+'|[A-Za-z_][A-Za-z0-9_]*)\s*:", block.group(1), re.M)
    return {key.strip("'") for key in keys}


def entry_detail(source: str, declaration: str, code: str) -> str | None:
    """The second element of a `CODE: ['label', 'detail']` pair, unescaped."""
    block = re.search(rf"{declaration}\s*=\s*\{{(.*?)\n\}}", source, re.S)
    assert block
    row = re.search(
        rf"^\s{{2}}'?{re.escape(code)}'?\s*:\s*\[\s*'(?:[^'\\]|\\.)*'\s*,\s*'((?:[^'\\]|\\.)*)'",
        block.group(1),
        re.M,
    )
    return row.group(1).replace("\\'", "'") if row else None


# --- the vocabularies the dashboard renders -------------------------------
# `CASH` and `VERDICT` are timeline tags the dossier synthesises rather than RecordKind members,
# so RECORD_KINDS is a superset by design and is checked one-way.
@pytest.mark.parametrize(
    ("declaration", "values", "exact"),
    [
        ("const REASONS", {m.value for m in ReasonCode}, True),
        ("const PARK_REASONS", {m.value for m in ParkReason}, True),
        ("const KEY_TYPES", {m.value for m in KeyType}, True),
        ("const DISPOSITIONS", {m.value for m in Disposition}, True),
        ("const RECORD_KINDS", {m.value for m in RecordKind}, False),
    ],
)
def test_every_engine_code_has_a_label(
    source: str, declaration: str, values: set[str], exact: bool
) -> None:
    labelled = table_keys(source, declaration)
    assert not values - labelled, (
        f"{declaration} is missing labels for {sorted(values - labelled)} — "
        f"the dashboard will render these as raw tokens"
    )
    if exact:
        assert not labelled - values, (
            f"{declaration} labels {sorted(labelled - values)}, which the engine does not "
            f"define — dead copy that reads as coverage"
        )


def test_cross_track_flags_use_the_wire_spelling(source: str) -> None:
    """The enum member is `X_1`; the API serves `X-1`. The UI must key on what it receives."""
    labelled = table_keys(source, "const FLAGS")
    on_the_wire = {m.value.replace("_", "-") for m in CrossTrackFlag}
    assert labelled == on_the_wire
    assert not any("_" in code for code in labelled), (
        "a flag keyed with an underscore will never match a payload"
    )


# --- verdict codes, where a second source of truth already exists ----------
def test_every_live_verdict_code_has_a_label(source: str) -> None:
    labelled = table_keys(source, "const VERDICTS")
    assert not set(VERDICT_DESCRIPTIONS) - labelled
    assert not labelled - set(VERDICT_DESCRIPTIONS)


def test_no_label_resurrects_a_retired_code(source: str) -> None:
    """A-03, C-04 and the rest are asserted absent from the engine. Labelling one would put a
    code on screen that no episode can ever hold."""
    assert not table_keys(source, "const VERDICTS") & set(RETIRED_CODES)


def test_verdict_detail_matches_the_engine_verbatim(source: str) -> None:
    """labels.js claims its `detail` is copied from VERDICT_DESCRIPTIONS. This is that claim.

    The short `label` is the UI's own wording and is deliberately not checked — it is allowed
    to be shorter and plainer than the engine's sentence. The detail is not: two different
    descriptions of one code is how a screen and a design document start disagreeing.
    """
    mismatched = {
        code: (entry_detail(source, "const VERDICTS", code), expected)
        for code, expected in VERDICT_DESCRIPTIONS.items()
        if entry_detail(source, "const VERDICTS", code) != expected
    }
    assert not mismatched, f"detail text drifted from the engine for: {sorted(mismatched)}"
