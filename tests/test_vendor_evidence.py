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

#: The four statuses a *source* may carry.  A source is either something we fetched
#: first-hand, something whose genuine text reached us only through a third-party
#: extraction proxy, something a retrievable third party says about the vendor, or
#: something that exists and refused us — and the last is a real answer, not a gap to be
#: filled in.
#:
#: ``RETRIEVED_VIA_PROXY`` is deliberately its own tier and not a shade of ``RETRIEVED``.
#: ``support.beaconchannelmanagement.com`` returns HTTP 403 to a direct automated fetch.
#: It does *not* block ``robots.txt``, which advertises the sitemap, and the text-extraction
#: proxy at ``https://r.jina.ai/`` returns the real page text.  So the words in a
#: ``RETRIEVED_VIA_PROXY`` excerpt are genuinely the vendor's — that is why it outranks
#: ``SECOND_HAND``, which is somebody else's summary in somebody else's words.  But they
#: arrived through a party we do not control, and the proxy *renders* rather than mirrors:
#: it flattened Beacon's HTML table into run-together text and dropped the downloadable
#: template attachment entirely.  Folding that into ``RETRIEVED`` would erase the
#: difference between "we read the vendor's page" and "a third party read it for us," and
#: the only thing this directory is worth is that its tiers are not blurred.  It is strong
#: enough to cite a published field name; it is not strong enough to certify a byte-exact
#: file we never held.
EVIDENCE_STATUSES = frozenset(
    {"RETRIEVED", "RETRIEVED_VIA_PROXY", "SECOND_HAND", "UNAVAILABLE"}
)

#: Statuses for which we hold the vendor's own words, in some form, and may quote them.
#: These are the rows that must carry a verbatim ``excerpt``.
FIRST_HAND_STATUSES = frozenset({"RETRIEVED", "RETRIEVED_VIA_PROXY"})

#: Statuses a ``SPEC``-tagged field may cite.  Written as an allow-list rather than as
#: "anything that is not ``UNAVAILABLE``" on purpose: a fifth status added later then
#: defaults to *not* citable and has to be argued for, instead of silently inheriting the
#: right to back a claim because nobody remembered to exclude it.
SPEC_CITABLE_STATUSES = frozenset({"RETRIEVED", "RETRIEVED_VIA_PROXY", "SECOND_HAND"})

#: The three tiers a *field we wrote* may carry (requirement V3).  Distinct from status:
#: status describes a source, a tier describes a decision we made citing one.
PROVENANCE_TIERS = frozenset({"SPEC", "STANDARD", "INVENTED"})

#: Keys every row must carry regardless of status.
REQUIRED_KEYS = ("id", "vendor", "status", "source_file", "url", "retrieved", "establishes")

#: Keys a ``RETRIEVED_VIA_PROXY`` row must carry on top of ``REQUIRED_KEYS``.  The status is
#: only worth having if it is checkable, and these three are what make it checkable: the
#: route the text came through, the capture it came from, and the words themselves.
PROXY_REQUIRED_KEYS = ("retrieval_url", "raw_file", "excerpt")

#: Directories whose modules must each be accompanied by a field-provenance table.
MAPPING_DIRS = (
    REPO_ROOT / "src" / "recon" / "mocks",
    REPO_ROOT / "src" / "recon" / "connectors" / "vendors",
)

#: A row of a ``MOCK_FIELDS.md`` provenance table: ``| field | TIER | evidence | note |``.
#:
#: The field cell is ``[^|`\n]+?`` — everything except the cell delimiter, the optional
#: backtick fence and a newline — rather than an enumerated character class.  The class this
#: replaced was ``[A-Za-z0-9_.\[\]-]+``, which has no space in it, and that omission was the
#: worst kind of bug this file can have.  Beacon publishes its field names with spaces:
#: ``340B ID``, ``Date of Service``, ``Rx Number``, ``HCPCS Code Modifier``.  A row naming
#: one of those did not fail the pattern, it simply did not *match* it — and ``finditer``
#: says nothing about the rows it walks past.  Every provenance test below would have gone
#: on passing while checking nothing, which is the single failure mode a citation suite
#: cannot survive and the reason
#: ``test_the_evidence_index_is_not_empty`` exists.  So the class is now defined by what a
#: markdown cell cannot contain, and
#: ``test_no_provenance_row_is_silently_skipped_by_the_field_row_pattern`` makes a skipped
#: row fail the build instead of disappearing.
#:
#: The tier cell allows ``_`` so that a malformed tier such as ``NOT_SET`` is caught by
#: ``test_every_declared_field_carries_one_of_the_three_provenance_tiers`` rather than
#: quietly failing to match and vanishing the same way.
#:
#: **Two other copies of this pattern exist and are still narrow**, and they are not this
#: module's to change:
#:
#: * ``tests/test_mocks.py:70`` — fails loudly when a spaced field is emitted-but-undeclared,
#:   so the bug there is visible.
#: * ``src/recon/connectors/readiness.py:221`` — **silent**.  It is production code that
#:   counts provenance rows for the F3 readiness report, so a spaced field name would be
#:   dropped from the count with nothing said.  The duplication is deliberate (that module
#:   explains why it refuses to import from a test), and the copies must be widened where
#:   they live.
_FIELD_ROW = re.compile(
    r"^\|\s*`?(?P<field>[^|`\n]+?)`?\s*\|\s*(?P<tier>[A-Z_]+)\s*\|\s*(?P<evidence>[^|\n]*)\|",
    re.MULTILINE,
)

#: The *loose* reading of the same row: a table line whose second cell is a bare upper-case
#: token, which is what a provenance row looks like from across the room.  Deliberately
#: unable to see the field name at all, so that it cannot share a bug with ``_FIELD_ROW``.
#: Anything this finds and ``_FIELD_ROW`` does not is a silently skipped row.
_FIELD_ROW_CANDIDATE = re.compile(
    r"^\|[^|\n]*\|\s*[A-Z][A-Z_]{2,}\s*\|",
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


def test_every_status_is_one_of_the_four_named_statuses(index_rows):
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


def test_a_retrieved_entry_holds_the_words_and_records_no_failure(index_rows):
    """The mirror of the test above, for the statuses that claim success.

    ``UNAVAILABLE`` must hold a failure and no words.  ``RETRIEVED`` and
    ``RETRIEVED_VIA_PROXY`` are the opposite claim, so they must hold words and no failure.
    A row carrying both has not decided what happened, and whichever half a reader believes
    will be the half that suits them.
    """
    offenders: list[str] = []
    for row in index_rows:
        if row.get("status") not in FIRST_HAND_STATUSES:
            continue
        if not row.get("excerpt", "").strip():
            offenders.append(f"{row['id']}: {row['status']} with no excerpt")
        if row.get("failure", "").strip():
            offenders.append(f"{row['id']}: {row['status']} yet still records a failure")
    assert not offenders, offenders


def test_a_proxy_retrieved_entry_names_the_route_and_the_capture_it_came_through(index_rows):
    """``RETRIEVED_VIA_PROXY`` has to cost something to claim, or it is just ``RETRIEVED``.

    The status says: these are the vendor's own words, and they reached us through a third
    party.  Both halves have to be checkable.  So the row names the proxy URL that was
    actually called, and the raw capture the text was taken from — and it must *not* carry
    a ``failure``, because a row that records both a success and a 403 is a row that has
    not decided which one happened.
    """
    offenders: list[str] = []
    for row in index_rows:
        if row.get("status") != "RETRIEVED_VIA_PROXY":
            continue
        for key in PROXY_REQUIRED_KEYS:
            if not str(row.get(key, "")).strip():
                offenders.append(f"{row['id']}: RETRIEVED_VIA_PROXY with no {key}")
        if row.get("failure", "").strip():
            offenders.append(
                f"{row['id']}: RETRIEVED_VIA_PROXY yet still records a failure; "
                "the retrieval either succeeded or it did not"
            )
        raw = str(row.get("raw_file", ""))
        if raw and not (EVIDENCE_DIR / raw).exists():
            offenders.append(f"{row['id']}: raw_file {raw} does not exist")
    assert not offenders, offenders


def test_every_proxy_excerpt_appears_verbatim_in_the_capture_it_claims_to_come_from(index_rows):
    """The check that makes the tier mean something instead of announcing something.

    A proxy-retrieved excerpt is the one place in this directory where a plausible-sounding
    sentence could be typed into ``index.jsonl`` and look exactly like retrieved text — the
    status itself concedes that a third party stood in the middle, so nobody would blink.
    This test closes that door: the excerpt is matched, character for character, against the
    capture file the row names.  A paraphrase fails.  A tidied-up quotation fails.  The
    run-together bold markers and curly quotation marks the proxy emitted have to still be
    there, because they are the evidence that the text was copied and not composed.
    """
    offenders: list[str] = []
    for row in index_rows:
        if row.get("status") != "RETRIEVED_VIA_PROXY":
            continue
        raw = str(row.get("raw_file", ""))
        excerpt = str(row.get("excerpt", ""))
        path = EVIDENCE_DIR / raw
        if not raw or not path.exists() or not excerpt:
            continue  # already named by the test above
        if excerpt not in path.read_text(encoding="utf-8"):
            offenders.append(
                f"{row['id']}: excerpt does not appear verbatim in {raw}; "
                "it has been paraphrased, normalised or written from memory"
            )
    assert not offenders, offenders


def test_the_proxy_tier_is_actually_in_use_and_is_explained_where_a_reader_will_look():
    """A tier nobody holds and nobody explains is a word, not a standard.

    Two things have to be true for ``RETRIEVED_VIA_PROXY`` to be worth having.  Some row
    must carry it — otherwise every test above passes vacuously and the tier is decoration.
    And ``README.md`` must say why it is separate from ``RETRIEVED``, because the next
    person to retrieve something through a proxy decides which tier to use by reading that
    file, and if it does not tell them the two will merge.
    """
    rows = _index_rows()
    holders = [row["id"] for row in rows if row.get("status") == "RETRIEVED_VIA_PROXY"]
    assert holders, (
        "no row carries RETRIEVED_VIA_PROXY; the status was added and then never used, "
        "which makes every proxy test above pass while checking nothing"
    )

    readme = (EVIDENCE_DIR / "README.md").read_text(encoding="utf-8")
    assert "RETRIEVED_VIA_PROXY" in readme, (
        "README.md does not mention RETRIEVED_VIA_PROXY; the tier exists in the tests and "
        "not in the document a human reads to decide which tier applies"
    )
    assert "r.jina.ai" in readme, (
        "README.md names the tier but not the route that produced it; 'via proxy' with no "
        "named proxy is not a provenance record"
    )


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


# ═══ The pattern that reads those tables, policed before it is trusted ═══

#: A provenance table written the way Beacon's published names force it to be written.
#: Held here as a literal rather than read from ``src/`` so that this test cannot be made
#: vacuous by an edit somewhere else in the repository.
_SPACED_FIELD_TABLE = """\
| field | TIER | evidence | note |
|---|---|---|---|
| `340B ID` | SPEC | BEACON-001 | verbatim published name, space and all |
| `Date of Service` | SPEC | BEACON-001, BEACON-002 | published on both templates |
| `Rx Number` | SPEC | BEACON-001 | pharmacy template only |
| `HCPCS Code Modifier` | SPEC | BEACON-002 | medical template only |
| `NDC-11` | SPEC | BEACON-001, BEACON-002 | hyphenated, as published |
| Quantity Dispensed | SPEC | BEACON-001 | unfenced, to prove the backticks are optional |
| `beacon.submission.claim_id` | INVENTED | — | the shape the old pattern could already read |
"""


def test_the_field_row_pattern_reads_published_field_names_that_contain_spaces():
    """Blocker B, stated as the test that would have caught it.

    The pattern this replaced enumerated its field characters and left the space out.  A row
    reading ``| `340B ID` | SPEC | BEACON-001 |`` therefore did not match — and a row that
    does not match is not an error, it is an absence.  ``finditer`` walked past it, the SPEC
    citation was never checked, and every test downstream reported success for a table it had
    only partly read.

    So the assertion is on the *count* and on the *names*, not merely on "something matched".
    Eight rows go in.  Seven are provenance rows and one is the header; the separator row is
    not one and must not be counted as one.  If the pattern silently drops ``340B ID`` the
    count is wrong and this fails, which is the whole point: a skipped row has to be loud.
    """
    matches = list(_FIELD_ROW.finditer(_SPACED_FIELD_TABLE))
    fields = [match.group("field") for match in matches]

    assert fields == [
        "field",
        "340B ID",
        "Date of Service",
        "Rx Number",
        "HCPCS Code Modifier",
        "NDC-11",
        "Quantity Dispensed",
        "beacon.submission.claim_id",
    ], f"pattern parsed {fields!r}"

    assert len(matches) == 8, (
        f"expected 8 parsed rows (1 header + 7 fields), got {len(matches)}; a count that "
        "drifts means a row was skipped rather than failed"
    )

    spaced = {match.group("field") for match in matches if " " in match.group("field")}
    assert spaced == {
        "340B ID",
        "Date of Service",
        "Rx Number",
        "HCPCS Code Modifier",
        "Quantity Dispensed",
    }, f"spaced field names the pattern can see: {sorted(spaced)}"

    by_field = {match.group("field"): match for match in matches}
    assert by_field["340B ID"].group("tier") == "SPEC"
    assert "BEACON-001" in by_field["340B ID"].group("evidence")
    assert by_field["Date of Service"].group("evidence").strip() == "BEACON-001, BEACON-002"
    assert by_field["NDC-11"].group("tier") == "SPEC"

    # The separator row is the one line that looks like a table row and is not a field.
    assert "---" not in fields, "the pattern is reading the table's separator row as a field"


def test_no_provenance_row_is_silently_skipped_by_the_field_row_pattern():
    """The guard that makes Blocker B impossible to reintroduce anywhere in the repository.

    Two readings of the same table, and they must agree.  ``_FIELD_ROW`` is strict: it names
    the field, the tier and the citation.  ``_FIELD_ROW_CANDIDATE`` is loose and *cannot see
    the field name at all* — it only notices that a table line has a bare upper-case token in
    its second cell, which is what a provenance row looks like from across the room.  The two
    patterns share no character class, so they cannot share a bug.

    Any line the loose one finds and the strict one does not is a row that is being walked
    past: present in the file, tagged with a tier, and checked by nothing.  That is a green
    suite proving less than it appears to, which is the failure mode this module was written
    against.  Here it is a named failure instead.
    """
    offenders: list[str] = []
    for path in sorted(REPO_ROOT.glob("src/recon/**/MOCK_FIELDS.md")):
        text = path.read_text(encoding="utf-8")
        parsed = {match.start() for match in _FIELD_ROW.finditer(text)}
        for candidate in _FIELD_ROW_CANDIDATE.finditer(text):
            if candidate.start() in parsed:
                continue
            line = text[candidate.start() : text.find("\n", candidate.start())]
            offenders.append(f"{path.relative_to(REPO_ROOT)}: unparsed provenance row {line!r}")
    assert not offenders, (
        f"provenance rows skipped rather than checked: {offenders}; widen _FIELD_ROW or fix "
        "the row — a row the pattern cannot read is a field with no enforced provenance"
    )


def test_the_provenance_tables_yield_enough_rows_to_be_worth_walking():
    """The mirror of ``test_the_evidence_index_is_not_empty``, for the other artefact.

    Every field test below iterates ``_FIELD_ROW.finditer``.  If that ever returns nothing —
    a renamed file, a reformatted table, a pattern narrowed again — they all pass and nobody
    is told.  A floor per file is the cheapest possible alarm, and it is set well under the
    current counts so that ordinary additions never trip it and a collapse always does.
    """
    counts = {
        str(path.relative_to(REPO_ROOT)): len(_FIELD_ROW.findall(path.read_text(encoding="utf-8")))
        for path in sorted(REPO_ROOT.glob("src/recon/**/MOCK_FIELDS.md"))
    }
    if not counts:
        pytest.skip("no MOCK_FIELDS.md yet; the mapping modules have not landed")

    thin = {name: count for name, count in counts.items() if count < 20}
    assert not thin, (
        f"provenance tables that parsed almost nothing: {thin} (all counts: {counts}); "
        "either the tables were emptied or _FIELD_ROW stopped matching them"
    )
    assert sum(counts.values()) >= 200, (
        f"only {sum(counts.values())} provenance rows parsed across {len(counts)} files "
        f"({counts}); the repository declares several hundred"
    )


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

    ``BEACON-003`` through ``BEACON-007`` name genuine Beacon articles at genuine URLs —
    and we have never read them.  Citing one as ``SPEC`` would launder "this page exists"
    into "this page says what I wrote," which is the precise move requirement V2 was
    written to stop.

    The rule is expressed as ``SPEC_CITABLE_STATUSES`` rather than as "not ``UNAVAILABLE``"
    so that the question a new status has to answer is *may a field cite this?* — asked
    once, deliberately, in the constant.  ``BEACON-001`` and ``BEACON-002`` are the live
    example: they sat here as 403s, the proxy route recovered the pages, and they became
    citable by moving into the allow-list, not by an exception carved into this test.
    """
    citable = {
        row["id"] for row in index_rows if row.get("status") in SPEC_CITABLE_STATUSES
    }
    known = {row["id"] for row in index_rows}
    offenders: list[str] = []
    for path in sorted(REPO_ROOT.glob("src/recon/**/MOCK_FIELDS.md")):
        for match in _FIELD_ROW.finditer(path.read_text(encoding="utf-8")):
            if match.group("tier") != "SPEC":
                continue
            cited = set(_EVIDENCE_ID.findall(match.group("evidence")))
            for identifier in sorted((cited & known) - citable):
                offenders.append(
                    f"{path.relative_to(REPO_ROOT)}: field {match.group('field')!r} "
                    f"is tagged SPEC citing {identifier}, whose source was never retrieved"
                )
    assert not offenders, offenders
