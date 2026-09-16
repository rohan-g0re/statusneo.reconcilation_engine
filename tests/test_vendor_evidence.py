"""Citation is enforced here, not by discipline.

Requirement group V of ``docs/connectivity_layer_requirements.md``.  Beacon, Verity and
Craneware are real companies with real published behaviour, and the moment a field name,
an auth flow, a status code or a cadence is written from memory the prototype stops being
a connector built to a vendor contract and becomes one built to our imagination — while
looking identical in every demo.  A mock that is wrong is worse than no mock, because it
is confidently wrong.

So every vendor field carries a provenance tier, and this file is what makes that a fact
about the repository rather than a promise in prose.  Deleting one evidence entry turns
the suite red; tagging a field ``SPEC`` against an evidence id that does not exist turns
the suite red; leaving a field untagged turns the suite red.

The field-tier tests below are written *before* the code they police.  While
``src/recon/mocks/`` and ``src/recon/connectors/`` do not yet exist they pass vacuously,
and that is the correct starting state — they are armed, waiting for wave 1.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_DIR = REPO_ROOT / "docs" / "vendor_evidence"
INDEX_PATH = EVIDENCE_DIR / "index.jsonl"

#: The three statuses a *source* may carry.  A source is either something we actually
#: fetched, something a retrievable third party says about the vendor, or something that
#: exists and refused us — and the third is a real answer, not a gap to be filled in.
EVIDENCE_STATUSES = frozenset({"RETRIEVED", "SECOND_HAND", "UNAVAILABLE"})

#: The three tiers a *field we wrote* may carry (requirement V3).  Distinct from status:
#: status describes a source, a tier describes a decision we made citing one.
PROVENANCE_TIERS = frozenset({"SPEC", "STANDARD", "INVENTED"})

#: Keys every row must carry regardless of status.
REQUIRED_KEYS = ("id", "vendor", "status", "source_file", "url", "retrieved", "establishes")

#: Directories whose modules must each be accompanied by a field-provenance table.
MAPPING_DIRS = (
    REPO_ROOT / "src" / "recon" / "mocks",
    REPO_ROOT / "src" / "recon" / "connectors" / "vendors",
)

#: A row of a ``MOCK_FIELDS.md`` provenance table: ``| field | TIER | evidence | note |``.
_FIELD_ROW = re.compile(
    r"^\|\s*`?(?P<field>[A-Za-z0-9_.\[\]-]+)`?\s*\|\s*(?P<tier>[A-Z]+)\s*\|\s*(?P<evidence>[^|]*)\|",
    re.MULTILINE,
)

#: An evidence id anywhere in prose: ``BEACON-001``, ``DOC2-014``, ``STD-X12-835``.
_EVIDENCE_ID = re.compile(r"\b(?:DOC2|BEACON|VERITY|CRANEWARE|STD)-[A-Z0-9-]+\b")


def _index_rows() -> list[dict]:
    """``docs/vendor_evidence/index.jsonl`` — the live artefact, read directly.

    Mirrors the ``pairs_oracle`` fixture at ``tests/conftest.py:30-46`` and the
    ``graph_rows`` fixture at ``tests/test_agents_grounding.py:38-54``: this reads the real
    file, with a hint if it is ever missing, rather than a frozen copy checked into
    ``tests/``.  Unlike the knowledge graph there is no builder to rerun — this index is
    hand-written, so the hint says to write the entry rather than to regenerate it.
    """
    assert INDEX_PATH.exists(), (
        f"{INDEX_PATH} is missing. It is the machine-readable half of requirement V4 and "
        "is hand-written, never generated; restore it or add the missing evidence entry."
    )
    return [
        json.loads(line)
        for line in INDEX_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


@pytest.fixture(scope="module")
def index_rows() -> list[dict]:
    return _index_rows()


@pytest.fixture(scope="module")
def evidence_ids(index_rows) -> frozenset[str]:
    return frozenset(row["id"] for row in index_rows)


def _mapping_modules() -> list[Path]:
    """Every vendor mapping or mock module that must declare its field provenance."""
    modules: list[Path] = []
    for directory in MAPPING_DIRS:
        if not directory.exists():
            continue
        modules.extend(
            path
            for path in sorted(directory.rglob("*.py"))
            if path.name != "__init__.py"
        )
    return modules


# ═══ The index itself ═══


def test_the_evidence_index_is_not_empty():
    """An empty index would make every citation test below pass vacuously.

    That is the one failure mode a citation test cannot survive: a green suite that
    proves nothing because there was nothing to check.  Requirement V4 says deleting one
    evidence entry turns the suite red, and deleting *all* of them must too.
    """
    rows = _index_rows()
    assert len(rows) >= 20, (
        f"only {len(rows)} evidence entries; the index covers Doc 2, three vendors and "
        "the public standards, so a number this small means entries were lost"
    )


def test_every_index_row_carries_every_required_key(index_rows):
    offenders: list[str] = []
    for row in index_rows:
        missing = [key for key in REQUIRED_KEYS if key not in row]
        if missing:
            offenders.append(f"{row.get('id', '<no id>')}: missing {missing}")
    assert not offenders, f"incomplete evidence rows: {offenders}"


def test_every_evidence_id_is_unique(index_rows):
    """Two rows sharing an id means one silently shadows the other in every lookup."""
    seen: dict[str, int] = {}
    for row in index_rows:
        seen[row["id"]] = seen.get(row["id"], 0) + 1
    duplicates = sorted(key for key, count in seen.items() if count > 1)
    assert not duplicates, f"duplicate evidence ids: {duplicates}"


def test_every_status_is_one_of_the_three_named_statuses(index_rows):
    offenders = [
        f"{row['id']}: {row.get('status')!r}"
        for row in index_rows
        if row.get("status") not in EVIDENCE_STATUSES
    ]
    assert not offenders, (
        f"evidence rows with an unrecognised status: {offenders}; "
        f"permitted values are {sorted(EVIDENCE_STATUSES)}"
    )


def test_no_evidence_entry_exists_without_an_excerpt_or_an_unavailability_record(index_rows):
    """Requirement V2, stated as a test.

    An entry with neither is the shape a remembered fact takes when it is written down as
    though it were a retrieved one.  Either we hold the words, or we hold the failure —
    there is no third state in which the entry is still evidence.
    """
    offenders: list[str] = []
    for row in index_rows:
        has_excerpt = bool(row.get("excerpt", "").strip())
        has_failure = bool(row.get("failure", "").strip())
        if not has_excerpt and not has_failure:
            offenders.append(row["id"])
    assert not offenders, (
        f"evidence rows with neither an excerpt nor a recorded failure: {offenders}; "
        "a fact with no source is not evidence, it is recollection"
    )


def test_an_unavailable_entry_records_its_failure_and_claims_nothing(index_rows):
    """An UNAVAILABLE source must not quietly still assert something.

    The whole value of recording a 403 is that the fact the page would have established
    is treated as UNKNOWN.  An entry that records the failure *and* an excerpt has had its
    gap filled from somewhere, and that somewhere is the thing requirement V2 forbids.
    """
    offenders: list[str] = []
    for row in index_rows:
        if row.get("status") != "UNAVAILABLE":
            continue
        if not row.get("failure", "").strip():
            offenders.append(f"{row['id']}: UNAVAILABLE with no recorded failure")
        if row.get("excerpt", "").strip():
            offenders.append(f"{row['id']}: UNAVAILABLE yet carries an excerpt")
        if "UNKNOWN" not in row.get("establishes", ""):
            offenders.append(f"{row['id']}: UNAVAILABLE but does not mark the fact UNKNOWN")
    assert not offenders, offenders


def test_every_doc2_derived_claim_cites_a_page(index_rows):
    """Requirement V5.

    Doc 2 is a legitimate source and is indexed like any other — but it is an assessment
    *about* the vendors, not published *by* them, so a claim taken from it has to be
    findable on a page.  A Doc 2 citation with no page number cannot be checked, and an
    uncheckable citation is decoration.
    """
    offenders = [
        row["id"]
        for row in index_rows
        if row.get("url", "").endswith("Assignment_Doc_2.pdf") and not isinstance(row.get("page"), int)
    ]
    assert not offenders, f"Doc 2 citations with no page number: {offenders}"


def test_every_row_points_at_an_evidence_file_that_exists(index_rows):
    offenders = [
        f"{row['id']} -> {row.get('source_file')}"
        for row in index_rows
        if not (EVIDENCE_DIR / str(row.get("source_file", ""))).exists()
    ]
    assert not offenders, f"evidence rows naming a missing file: {offenders}"


def test_every_evidence_id_is_written_up_in_its_markdown_file(index_rows):
    """The index and the prose must not drift apart.

    ``index.jsonl`` is what the tests walk; the markdown is what a human reads.  An id in
    one and not the other means a reviewer checking the excerpt finds nothing, which is
    exactly the moment the citation stops being worth anything.
    """
    cache: dict[str, str] = {}
    offenders: list[str] = []
    for row in index_rows:
        name = str(row["source_file"])
        if name not in cache:
            cache[name] = (EVIDENCE_DIR / name).read_text(encoding="utf-8")
        if row["id"] not in cache[name]:
            offenders.append(f"{row['id']} is indexed but absent from {name}")
    assert not offenders, offenders


def test_every_evidence_id_cited_in_prose_resolves_to_the_index(index_rows, evidence_ids):
    """The mirror of the test above: prose may not cite an id the index does not hold.

    This is the half that catches a deleted entry.  Remove a row from ``index.jsonl`` and
    every markdown reference to it becomes a dangling citation, and this test names it —
    which is requirement V4's acceptance criterion, stated the way it actually fires.
    """
    offenders: list[str] = []
    for path in sorted(EVIDENCE_DIR.glob("*.md")):
        for cited in sorted(set(_EVIDENCE_ID.findall(path.read_text(encoding="utf-8")))):
            if cited not in evidence_ids:
                offenders.append(f"{path.name} cites {cited}, which is not in index.jsonl")
    assert not offenders, offenders


# ═══ The fields those sources are cited by (armed for wave 1) ═══


def test_every_vendor_mapping_module_has_a_field_provenance_table():
    """Requirement M4: a mapping with no ``MOCK_FIELDS.md`` beside it is untagged by default.

    Passes vacuously until ``src/recon/mocks/`` exists.  That is deliberate — the test is
    written in wave 0, before the code it polices, so that the first mapping module to
    land arrives into a repository that already refuses an undeclared one.
    """
    offenders = [
        str(module.relative_to(REPO_ROOT))
        for module in _mapping_modules()
        if not (module.parent / "MOCK_FIELDS.md").exists()
    ]
    assert not offenders, (
        f"vendor mapping modules with no MOCK_FIELDS.md beside them: {offenders}; "
        "every field in a vendor mapping must carry a provenance tier"
    )


def test_every_declared_field_carries_one_of_the_three_provenance_tiers():
    """Requirement V3: a field with no tier fails the build."""
    offenders: list[str] = []
    for path in sorted(REPO_ROOT.glob("src/recon/**/MOCK_FIELDS.md")):
        for match in _FIELD_ROW.finditer(path.read_text(encoding="utf-8")):
            tier = match.group("tier")
            if tier in {"TIER", "PROVENANCE"}:  # the table's own header row
                continue
            if tier not in PROVENANCE_TIERS:
                offenders.append(
                    f"{path.relative_to(REPO_ROOT)}: field {match.group('field')!r} "
                    f"has tier {tier!r}"
                )
    assert not offenders, (
        f"fields with an unrecognised provenance tier: {offenders}; "
        f"permitted values are {sorted(PROVENANCE_TIERS)}"
    )


def test_every_spec_tagged_field_names_an_evidence_id_that_exists(evidence_ids):
    """Requirement V4's sharp edge.

    ``SPEC`` is the only tier that asserts a vendor actually said something.  If the id it
    names is absent from the index then nobody said it, and the field is an invention
    wearing a citation — the single most expensive kind of mistake this directory exists
    to prevent.
    """
    offenders: list[str] = []
    for path in sorted(REPO_ROOT.glob("src/recon/**/MOCK_FIELDS.md")):
        for match in _FIELD_ROW.finditer(path.read_text(encoding="utf-8")):
            if match.group("tier") != "SPEC":
                continue
            cited = set(_EVIDENCE_ID.findall(match.group("evidence")))
            if not cited:
                offenders.append(
                    f"{path.relative_to(REPO_ROOT)}: field {match.group('field')!r} "
                    "is tagged SPEC but cites no evidence id"
                )
                continue
            for identifier in sorted(cited - evidence_ids):
                offenders.append(
                    f"{path.relative_to(REPO_ROOT)}: field {match.group('field')!r} "
                    f"cites {identifier}, which is not in index.jsonl"
                )
    assert not offenders, offenders


def test_no_spec_tagged_field_cites_an_unavailable_source(index_rows):
    """A 403 cannot support a ``SPEC`` claim, however real the URL is.

    ``BEACON-001`` names a genuine Beacon article at a genuine URL — and we never read it.
    Citing it as ``SPEC`` would launder "this page exists" into "this page says what I
    wrote," which is the precise move requirement V2 was written to stop.
    """
    unavailable = {row["id"] for row in index_rows if row.get("status") == "UNAVAILABLE"}
    offenders: list[str] = []
    for path in sorted(REPO_ROOT.glob("src/recon/**/MOCK_FIELDS.md")):
        for match in _FIELD_ROW.finditer(path.read_text(encoding="utf-8")):
            if match.group("tier") != "SPEC":
                continue
            for identifier in sorted(set(_EVIDENCE_ID.findall(match.group("evidence"))) & unavailable):
                offenders.append(
                    f"{path.relative_to(REPO_ROOT)}: field {match.group('field')!r} "
                    f"is tagged SPEC citing {identifier}, whose source was never retrieved"
                )
    assert not offenders, offenders
