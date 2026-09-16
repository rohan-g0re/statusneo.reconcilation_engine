"""The vendor mocks are formatters, and this file is what makes that a fact.

Requirement group M of ``docs/connectivity_layer_requirements.md``.  The distinction
between a *formatter* and a *generator* is the load-bearing decision of the whole data
layer, and it is invisible at a glance: both produce vendor-shaped files, both look right
in a demo, and only one of them preserves the guarantee that makes the crosswalk a
measurement.

``recon.generators`` hands each generator a **blind slice** — ``generators/contracts.py``
asserts at runtime that no generator can see an episode id, a verdict or an expected
amount.  That assertion is the only reason crosswalk accuracy is a measured number rather
than a claim.  A new Beacon or Verity *generator* would have to be handed the answer in
order to tell a consistent story, which deletes the guarantee silently.  A *formatter*
re-dresses data that already passed the check, so the guarantee survives.

Nothing about that survives on good intentions, so each rule below is a test:

* **M1** — identifiers are minted by the orchestrator, and the id in a mock response is
  byte-identical to the one minted.
* **M2** — no privileged imports, and no arithmetic on money.  The second is checked
  empirically as well as structurally: every amount in the vendor output must appear
  verbatim in the feed it came from.
* **M3** — the Beacon response side re-dresses, never adjudicates.  The set of outcomes it
  can emit equals the set already present in the data.
* **M5** — coverage is complete, and completeness is measured rather than asserted.
* **M6** — the data layer stands alone, with no connector in existence.

The dataset is generated once per profile as a session fixture.  Generation is the
expensive part and every test below reads from the same one, which mirrors how
``tests/test_generators.py`` is organised.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

from recon.config import CuratedSpine, load_settings
from recon.generators import orchestrator
from recon.mocks import beacon_payloads, coverage, craneware_export, source as source_module
from recon.mocks import verity_export

REPO_ROOT = Path(__file__).resolve().parents[1]
MOCKS_DIR = REPO_ROOT / "src" / "recon" / "mocks"
FIELDS_DOC = MOCKS_DIR / "MOCK_FIELDS.md"

#: Modules that dress vendor data.  ``source.py`` reads and ``coverage.py`` counts; neither
#: emits a vendor field, so neither is required to declare one.
FORMATTER_MODULES = ("verity_export", "craneware_export", "beacon_payloads")

#: What a formatter may never reach for.  ``pricing`` and ``money`` would let it compute an
#: amount; ``decision_tree`` and the orchestrator would let it learn the answer; ``connectors``
#: would collapse requirement M6's "stands alone" into a circular dependency.
FORBIDDEN_IMPORT_PREFIXES: tuple[str, ...] = (
    "recon.reference.pricing",
    "recon.money",
    "recon.generators",
    "recon.connectors",
    "recon.db",
    "decision_tree",
)

#: Names whose presence would mean money was being computed rather than carried.
FORBIDDEN_MONEY_NAMES = frozenset({"Decimal", "to_cents", "from_cents", "apply_bps", "round_half_up"})

_FIELD_ROW = re.compile(
    r"^\|\s*`?(?P<field>[A-Za-z0-9_.\[\]-]+)`?\s*\|\s*(?P<tier>SPEC|STANDARD|INVENTED)\s*\|",
    re.MULTILINE,
)

#: Money as the feeds render it: ``6948.00``, ``-120.50``.
_AMOUNT = re.compile(r"-?\d+\.\d{2}")


@pytest.fixture(scope="module")
def demo(tmp_path_factory) -> tuple:
    settings = load_settings(
        "demo",
        data_dir=tmp_path_factory.mktemp("mocks-demo"),
        curated_spine=CuratedSpine.RECORDED,
    )
    result = orchestrator.generate(settings)
    orchestrator.write_outputs(settings, result)
    return settings, result, source_module.load_source(settings)


def _module_source(name: str) -> str:
    return (MOCKS_DIR / f"{name}.py").read_text(encoding="utf-8")


def _imported_module_names(source: str) -> set[str]:
    """Import names declared in the source text, via ``ast``.

    Read from the text rather than by importing, for the reason
    ``tests/test_generators.py:996`` gives: importing would only prove the module *can* be
    loaded alongside a forbidden one, not that it declares a dependency on it.
    """
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


# ═══ M2 — formatters hold no domain logic ═══


@pytest.mark.parametrize("module_name", [*FORMATTER_MODULES, "source", "coverage"])
def test_no_mock_module_imports_pricing_the_generators_or_the_decision_tree(module_name: str):
    """A mock that can reach the pricing module can disagree with the ledger.

    And one that can reach the orchestrator can see the episode — at which point the
    vendor file it writes shares a key with the feed by construction rather than by
    crosswalk, and the connector built on top of it proves nothing.
    """
    imported = _imported_module_names(_module_source(module_name))
    offenders = sorted(
        name
        for name in imported
        if any(name == prefix or name.startswith(prefix + ".") for prefix in FORBIDDEN_IMPORT_PREFIXES)
    )
    assert not offenders, f"{module_name}.py imports forbidden module(s): {offenders}"


def _executable_names(source: str) -> set[str]:
    """Every identifier and string literal the module actually evaluates.

    Docstrings are excluded deliberately.  ``tests/test_generators.py`` gives ``db/`` and
    ``crosswalk/`` a plain text scan, and that works there because those modules have no
    reason to discuss ground truth — but these modules do.  ``source.py``'s docstring
    exists precisely to say *"what it deliberately does not read: truth/ground_truth.json"*,
    and a check that punishes documenting the prohibition would have exactly one effect:
    the next person deletes the sentence instead of the dependency.

    So the rule is tightened rather than loosened — any *evaluated* mention fails, including
    one inside a non-docstring string literal, which is how a path would have to be spelled.
    """
    tree = ast.parse(source)
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", None)
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstrings.add(id(body[0].value))

    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in docstrings:
                names.add(node.value)
    return names


@pytest.mark.parametrize("module_name", [*FORMATTER_MODULES, "source", "coverage"])
def test_no_mock_module_reads_ground_truth(module_name: str):
    """The prohibition that makes the crosswalk scoreable rather than assumed.

    ``load_feeds`` holds this structurally — it takes a feeds directory and cannot reach
    ``truth/`` even by accident.  This package has the same obligation and a weaker
    structural guard, since it is handed a whole ``Settings``: ``truth_dir()`` is one
    attribute access away at all times.  So the guard is a test.
    """
    offenders = sorted(
        name
        for name in _executable_names(_module_source(module_name))
        if "ground_truth" in name or "truth_dir" in name
    )
    assert not offenders, (
        f"{module_name}.py evaluates {offenders}; the vendor layer re-dresses data that "
        "already passed the blind-slice check, and reading the answer instead would make "
        "every crosswalk number downstream a claim rather than a measurement"
    )


@pytest.mark.parametrize("module_name", FORMATTER_MODULES)
def test_no_formatter_names_a_money_conversion(module_name: str):
    """Amounts are carried as text. Naming a conversion is how that stops being true."""
    tree = ast.parse(_module_source(module_name))
    offenders = sorted(
        {
            node.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Name) and node.id in FORBIDDEN_MONEY_NAMES
        }
    )
    assert not offenders, f"{module_name}.py names money conversion(s): {offenders}"


def test_every_amount_in_the_vendor_output_appears_verbatim_in_the_feed(demo):
    """The empirical half of "no arithmetic on money", and the one that actually binds.

    A structural check can only forbid the names it thought of.  This one takes every
    decimal amount the vendor layer emitted and requires it to appear, character for
    character, somewhere in the TPA feed — so a sum, a rounding, a currency reformat or a
    negation all fail, whatever they were spelled.
    """
    _settings, _result, source = demo
    feed_text = json.dumps(source.feeds["tpa_340b_events.jsonl"])

    rendered: dict[str, str] = {}
    rendered.update({f"verity.{k}": v for k, v in verity_export.render(source).items()})
    rendered.update({f"craneware.{k}": v for k, v in craneware_export.render(source).items()})
    rendered.update({f"beacon.{k}": v for k, v in beacon_payloads.render(source).items()})

    offenders: list[str] = []
    for name, text in rendered.items():
        for amount in sorted(set(_AMOUNT.findall(text))):
            if amount not in feed_text:
                offenders.append(f"{name}: {amount} appears in no TPA feed record")
    assert not offenders, (
        f"vendor output contains amounts the feed never stated: {offenders[:10]}; "
        "a formatter that can produce a new amount is computing one"
    )


# ═══ M1 — the orchestrator mints, the mock echoes ═══


def test_every_beacon_id_a_mock_emits_is_one_the_orchestrator_minted(demo):
    """Requirement M1's acceptance, stated exactly as the requirement states it."""
    settings, _result, source = demo
    minted = {
        json.loads(line)["beacon_id"]
        for line in settings.vendor_identifiers_path().read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    emitted: set[str] = set()
    for text in beacon_payloads.render(source).values():
        for line in text.splitlines():
            if line.strip():
                value = json.loads(line).get("beacon_id")
                if value:
                    emitted.add(value)

    assert emitted, "the Beacon mock emitted no Beacon ID at all"
    assert emitted <= minted, (
        f"Beacon IDs a mock invented rather than echoed: {sorted(emitted - minted)[:5]}"
    )


def test_the_identifier_sidecar_carries_no_episode_id_verdict_or_amount(demo):
    """The sidecar crosses the same boundary the blind slice does, by a different road.

    ``contracts.assert_slice_is_blind`` guards the generator side.  Nothing guarded this
    one until it existed, and a sidecar carrying a verdict would hand every mock the answer
    while every existing test stayed green.
    """
    settings, _result, _source = demo
    rows = [
        json.loads(line)
        for line in settings.vendor_identifiers_path().read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert rows, "the identifier sidecar is empty"
    orchestrator.assert_vendor_rows_are_blind(rows)


def test_the_sidecar_join_keys_resolve_against_the_real_tpa_feed(demo):
    """A sidecar that joins to nothing is a sidecar nobody notices is broken.

    The keys are spelled as ``tpa_340b_events.jsonl`` spells them — ``ndc_11`` with the
    underscore, ``fill_date`` in wire form — and identifier drift is carried rather than
    repaired, so this also asserts the mock never silently fixes defect D-6.
    """
    settings, _result, source = demo
    fields = ("rx_number", "pharmacy_npi", "provider_npi", "ndc_11", "fill_date")
    feed_keys = {
        tuple(record.get(name) for name in fields)
        for record in source.feeds["tpa_340b_events.jsonl"]
        if record.get("event_type") != "REBATE_PAYMENT_BATCH"
    }
    rows = [
        json.loads(line)
        for line in settings.vendor_identifiers_path().read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    missing = [
        row["beacon_id"]
        for row in rows
        if tuple(row.get(name) for name in fields) not in feed_keys
    ]
    assert not missing, f"sidecar rows joining to no TPA record: {missing[:5]}"


# ═══ M3 — the Beacon response side re-dresses, never adjudicates ═══


def test_the_beacon_mock_emits_only_outcomes_the_feed_already_contains(demo):
    """Requirement M3's acceptance.

    Beacon's acknowledgement, validation outcome and rebate status are already present in
    the generated data as ``TPA_MANUFACTURER_DECISION`` records.  The mock replays them
    under Beacon's field names; an outcome it can produce that the data cannot is the mock
    having formed an opinion.
    """
    _settings, _result, source = demo
    emitted = {
        json.loads(line)["outcome"]
        for line in beacon_payloads.render(source)["validation_outcome"].splitlines()
        if line.strip()
    }
    assert emitted <= set(beacon_payloads.VALIDATION_OUTCOMES), (
        f"undeclared outcomes: {sorted(emitted - set(beacon_payloads.VALIDATION_OUTCOMES))}"
    )
    assert emitted == set(beacon_payloads.VALIDATION_OUTCOMES), (
        "the declared outcome vocabulary is wider than the data can produce: "
        f"{sorted(set(beacon_payloads.VALIDATION_OUTCOMES) - emitted)} never occur"
    )


def test_every_beacon_rejection_reason_appears_verbatim_in_the_feed(demo):
    """Requirement C3: the vendor's reason is preserved verbatim, not paraphrased.

    Beacon's real validation vocabulary is unknown — ``BEACON-003``, the code glossary, is
    one of seven 403s.  Mapping our reason onto a Beacon-shaped code would be inventing the
    one thing we are least able to check, so the reason is carried across untouched.
    """
    _settings, _result, source = demo
    feed_text = json.dumps(source.feeds["tpa_340b_events.jsonl"])
    reasons = {
        json.loads(line).get("reason_code")
        for line in beacon_payloads.render(source)["validation_outcome"].splitlines()
        if line.strip()
    }
    offenders = sorted(
        reason for reason in reasons if reason and f'"{reason}"' not in feed_text
    )
    assert not offenders, f"reasons the feed never stated: {offenders}"


# ═══ M4 — every emitted field is declared ═══


def _declared_fields() -> set[str]:
    assert FIELDS_DOC.exists(), (
        f"{FIELDS_DOC} is missing. It is requirement M4's artefact and is hand-written, "
        "never generated; a mapping without one has undeclared fields by default."
    )
    text = FIELDS_DOC.read_text(encoding="utf-8")
    return {match.group("field") for match in _FIELD_ROW.finditer(text)}


def test_every_field_the_formatters_emit_is_declared_with_a_provenance_tier(demo):
    """Requirement M4: *a test fails on an undeclared field.*

    Derived from the code rather than from the document, so adding a column without
    declaring where it came from fails — which is the only version of this rule that
    survives someone being in a hurry.
    """
    _settings, _result, source = demo
    emitted: set[str] = set()

    for dataset, columns in verity_export.COLUMNS.items():
        emitted.update(f"verity.{dataset}.{column}" for column in columns)

    for report, columns in craneware_export.REPORT_COLUMNS.items():
        key = Path(craneware_export.REPORT_FILENAMES[report]).stem
        every = (
            *craneware_export.CONTROL_COLUMNS,
            *columns,
            craneware_export.TRAILER_COLUMN,
        )
        emitted.update(f"craneware.{key}.{column}" for column in every)

    for kind, text in beacon_payloads.render(source).items():
        for line in text.splitlines():
            if line.strip():
                emitted.update(f"beacon.{kind}.{name}" for name in json.loads(line))

    undeclared = sorted(emitted - _declared_fields())
    assert not undeclared, (
        f"fields emitted but not declared in MOCK_FIELDS.md: {undeclared[:15]}"
    )


# ═══ M5 — coverage is measured ═══


def test_no_vendor_record_type_produces_zero_rows(demo):
    """Requirement M5's acceptance.

    A formatter whose population filter excludes everything still writes a file and still
    parses.  The count is the only place that failure is visible.
    """
    _settings, _result, source = demo
    report = coverage.build_report(source)
    assert not report["empty_record_types"], (
        f"vendor record types with zero rows: {report['empty_record_types']}"
    )


def test_all_four_golden_claim_archetypes_appear_in_the_vendor_layer(demo):
    """Paid, rejected, reversed, unmatched — ``DOC2-002`` step 5 names all four.

    Wave 6 has to trace each of them end to end.  A missing archetype here is a wave-6
    failure discovered at the point it is most expensive to fix.
    """
    _settings, _result, source = demo
    report = coverage.build_report(source)
    assert not report["empty_archetypes"], (
        f"archetypes the data cannot exercise: {report['empty_archetypes']}"
    )


# ═══ M6 — the data layer stands alone ═══


@pytest.mark.parametrize("module_name", [*FORMATTER_MODULES, "source", "coverage"])
def test_no_mock_module_imports_the_connector_package(module_name: str):
    """Requirement M6, rendered as something a suite can actually run.

    The requirement says the data layer must be assertable *"with ``src/recon/connectors/``
    absent from the repository entirely"*, which is not executable in-suite.  The import
    ban is the same guarantee: if nothing here reaches the connectors, deleting them cannot
    break this.
    """
    imported = _imported_module_names(_module_source(module_name))
    offenders = sorted(name for name in imported if name.startswith("recon.connectors"))
    assert not offenders, f"{module_name}.py imports {offenders}; the data layer must stand alone"


def test_the_whole_vendor_layer_renders_from_files_with_no_transport(demo):
    """Output is files on disk, openable by a human and readable by a test."""
    settings, _result, _source = demo
    report = coverage.write_all(settings)
    vendor_dir = settings.vendor_dir()
    assert (vendor_dir / coverage.COVERAGE_FILE).exists()
    assert (vendor_dir / "COVERAGE.md").exists()
    written = sorted(path.name for path in vendor_dir.rglob("*") if path.is_file())
    assert len(written) >= 16, f"expected the full vendor layer on disk, found {written}"
    assert report["dispenses"] > 0


def test_rendering_twice_produces_identical_bytes(demo):
    """Determinism (requirement C1), and the thing that makes a mock replayable.

    No wall-clock, no unseeded randomness.  A mock whose output moved between runs would
    make every downstream fixture flaky for a reason nobody would look for here.
    """
    _settings, _result, source = demo
    for renderer in (verity_export.render, craneware_export.render, beacon_payloads.render):
        assert renderer(source) == renderer(source), f"{renderer.__module__} is not deterministic"
