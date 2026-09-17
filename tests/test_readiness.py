"""The readiness report is only worth anything if it cannot be written by hand.

Requirement F3's acceptance is one sentence — *"the report generates from code and config,
never hand-maintained"* — and it is the kind of claim that is trivially satisfied in prose and
almost never satisfied in fact.  A table of literals produces exactly the same document on the
day it is written; the two only diverge later, quietly, at the moment the document matters
most.

So these tests do not check that the report *says* the right things.  They check that it
**cannot say the wrong ones**: change an input and the corresponding cell moves.  Every
derivation test below follows the same shape — take a real input, alter it, rebuild, and
assert the output followed.  A cell that survives having its source removed is a cell that was
typed in, and this file is what finds it.

The other half is honesty.  Every connector in this build talks to a local mock, and a report
that read as though Verity were connected would be the most misleading artefact in the
repository.  ``test_the_report_never_claims_a_live_vendor_connection`` and its neighbours are
what make that a fact about the code rather than a promise in a docstring.
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from recon.connectors import credentials, readiness, registry, sftp
from recon.connectors.readiness import Reach, Stage
from recon.connectors.registry import PayloadFormat, Source
from recon.connectors.schema_registry import FieldSpec, SchemaContract, SchemaRegistry
from recon.domain.enums import SourceSystem, TransportKind

REPO_ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_INDEX = REPO_ROOT / "docs" / "vendor_evidence" / "index.jsonl"
_READINESS_SOURCE = (
    REPO_ROOT / "src" / "recon" / "connectors" / "readiness.py"
).read_text(encoding="utf-8")


@pytest.fixture()
def no_secrets(tmp_path) -> Path:
    """A secrets file that does not exist.

    Passed to every ``build_report`` below so the suite reads neither the developer's
    environment nor ``~/.recon/connector_secrets.json``.  Without it a machine that happens to
    hold a real credential would produce a different report, and "the tests pass on my laptop"
    is the failure mode a readiness report can least afford.
    """
    return tmp_path / "no-such-secrets.json"


@pytest.fixture()
def report(no_secrets):
    return readiness.build_report(env={}, secrets_file=no_secrets)


# ═══ the acceptance criterion: nothing here is hand-maintained ═══


def test_every_registered_source_appears_in_the_report(report):
    """The registry's own tables are the source list, so none can be forgotten."""
    declared = set(registry.VENDOR_SOURCE_IDS) | set(registry.BEACON_SOURCE_IDS)
    missing = sorted(declared - set(report.source_ids))
    assert not missing, (
        f"sources declared in the registry and absent from the readiness report: {missing}"
    )


def test_the_six_generated_feeds_appear_when_a_feeds_directory_is_given(no_secrets, tmp_path):
    from recon import config

    feeds = tmp_path / "feeds"
    feeds.mkdir()
    built = readiness.build_report(
        sources=readiness.declared_sources(feeds_dir=feeds),
        env={},
        secrets_file=no_secrets,
    )
    expected = {Path(name).stem for name in config.FEED_FILENAMES}
    assert expected <= set(built.source_ids)


def test_a_source_added_to_the_registry_appears_without_editing_the_report(
    monkeypatch, no_secrets
):
    """Requirement F3's acceptance, stated the way it actually fires.

    A seventh secure-file vendor is a row in ``registry._VENDOR_ROWS`` and a mapping module —
    requirement A1's whole promise.  If the readiness report needed a matching edit, the
    report would be a hand-maintained table with a generator wrapped around it, and it would
    be wrong from the moment the row landed until somebody remembered.

    Note what is *not* patched: nothing in ``recon.connectors.readiness``.  The new source has
    to arrive with a derived transport, a derived credential shape, a derived contract status
    and a derived blocked list, entirely on the strength of being in the registry.
    """
    row = registry._VendorRow(
        vendor="newvendor",
        source_system=SourceSystem.TPA_PORTAL,
        filenames=("newvendor_export.csv",),
        mapping_version="newvendor-sftp-export-9.9.9",
    )
    monkeypatch.setitem(registry._VENDOR_ROWS, "newvendor_claims", row)
    monkeypatch.setattr(
        registry, "VENDOR_SOURCE_IDS", tuple(registry._VENDOR_ROWS), raising=True
    )

    built = readiness.build_report(env={}, secrets_file=no_secrets)
    assert "newvendor_claims" in built.source_ids

    added = built.by_id("newvendor_claims")
    assert added.vendor == "newvendor"
    # Derived from the registry row's transport kind, not from a table keyed by vendor.
    assert added.transport.kind == str(TransportKind.SFTP)
    assert added.transport.implemented
    # Derived from the transport's AST: SFTP resolves an SSH key, so this row needs one.
    assert added.auth.kind == credentials.CredentialKind.SSH_KEY.value
    # Derived from the schema registry, which has never heard of it.
    assert not added.schema.registered
    # Derived from the provenance tables, which carry no rows for a vendor that does not exist.
    assert not added.fidelity.applies
    # Derived: a vendor with no mapping module and no mock has not reached connector-ready.
    assert added.stage is Stage.DECLARED
    assert "newvendor_claims" in readiness.render_markdown(built)


def test_the_readiness_module_names_no_vendor_and_no_source_id():
    """The blunt instrument, and the one that catches a regression fastest.

    Every other test here can be satisfied by a report that derives most cells and hardcodes
    one.  This one cannot: if the word ``verity`` appears in executable code in
    ``readiness.py``, some cell is keyed on a vendor name, and requirement F3's acceptance is
    broken whatever the rest of the suite says.  Prose is exempt — the module has to be able
    to *explain* itself — so docstrings are blanked before the search, and comments never
    reach the AST at all.

    ``declared_sources`` is exempt and is checked separately below: calling
    ``registry.beacon_sources`` is naming the registry's public API, not writing a cell.
    """
    import ast

    tree = ast.parse(_READINESS_SOURCE)
    tree.body = [
        node
        for node in tree.body
        if not (isinstance(node, ast.FunctionDef) and node.name == "declared_sources")
    ]
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                body[0].value.value = ""

    code = ast.unparse(tree).lower()
    forbidden = ["verity", "craneware", "beacon"] + list(
        registry.VENDOR_SOURCE_IDS + registry.BEACON_SOURCE_IDS
    )
    offenders = sorted({name for name in forbidden if name.lower() in code})
    assert not offenders, (
        f"readiness.py names {offenders} in executable code; every cell must be derived by "
        "inspecting an object, never keyed on a vendor or a source id"
    )


def test_declared_sources_reaches_vendor_names_only_through_the_registry():
    """The exemption above, bounded.

    ``declared_sources`` may say ``registry.beacon_sources``, because that is the registry's
    public constructor.  It may not say anything else vendor-shaped — no source id, no
    endpoint, no per-vendor branch — because the moment it does, adding a row to the registry
    stops being enough to make it appear in the report.
    """
    import ast

    tree = ast.parse(_READINESS_SOURCE)
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "declared_sources"
    )
    vendor_words = ("verity", "craneware", "beacon")
    arg_names = {arg.arg for arg in function.args.args + function.args.kwonlyargs}
    if (
        function.body
        and isinstance(function.body[0], ast.Expr)
        and isinstance(function.body[0].value, ast.Constant)
    ):
        function.body[0].value.value = ""  # prose is exempt, as above

    for node in ast.walk(function):
        if isinstance(node, ast.Attribute) and any(
            word in node.attr.lower() for word in vendor_words
        ):
            assert isinstance(node.value, ast.Name) and node.value.id == "registry", (
                f"declared_sources reaches {node.attr!r} other than through the registry"
            )
        if isinstance(node, ast.Name) and any(word in node.id.lower() for word in vendor_words):
            assert node.id in arg_names, (
                f"declared_sources names {node.id!r}, which is neither a parameter nor a "
                "registry attribute"
            )
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            assert not any(word in node.value.lower() for word in vendor_words), (
                f"declared_sources contains the string literal {node.value!r}"
            )


# ═══ derivation: transport ═══


def test_the_transport_cell_is_derived_from_the_classes_that_exist(monkeypatch, no_secrets):
    """Rename the SFTP transport's ``kind`` and every SFTP source loses its transport.

    This is the test the whole module is shaped around.  A hand-written ``"transport": true``
    survives the class being deleted; a discovered one does not.
    """
    before = readiness.build_report(env={}, secrets_file=no_secrets)
    sftp_sources = [
        row.source_id
        for row in before.sources
        if row.transport.kind == str(TransportKind.SFTP)
    ]
    assert sftp_sources, "no SFTP source in the registry; this test has nothing to prove"
    assert all(before.by_id(name).transport.implemented for name in sftp_sources)

    monkeypatch.setattr(sftp.SftpTransport, "kind", "A_KIND_NOTHING_DECLARES")
    after = readiness.build_report(env={}, secrets_file=no_secrets)

    for name in sftp_sources:
        row = after.by_id(name)
        assert not row.transport.implemented, (
            f"{name} still reports a transport after SftpTransport stopped declaring "
            "TransportKind.SFTP; that cell is not derived"
        )
        assert row.stage is Stage.DECLARED
        assert any("no transport implements" in gap for gap in row.gaps)

    assert "A_KIND_NOTHING_DECLARES" not in readiness.transport_implementations().get(
        str(TransportKind.SFTP), ()
    )


def test_every_declared_transport_kind_has_an_implementation(report):
    """A registry row naming a transport nothing implements is a source that never arrives."""
    orphans = sorted(
        {row.transport.kind for row in report.sources if not row.transport.implemented}
    )
    assert not orphans, f"transport kinds declared by a source and implemented by nothing: {orphans}"


# ═══ derivation: schema contract ═══


def test_the_schema_cell_is_derived_from_the_registry_it_is_handed(no_secrets):
    """Hand in a different registry and the contract column reports that registry's answer.

    Two directions, because a cell can be wrong either way.  An empty registry must strip
    every contract; a registry that knows about a source the shared one does not must add one.
    """
    empty = readiness.build_report(
        schema_registry=SchemaRegistry(), env={}, secrets_file=no_secrets
    )
    assert not any(row.schema.registered for row in empty.sources)
    assert all(
        any("no versioned field contract" in gap for gap in row.gaps)
        for row in empty.sources
        if row.is_vendor
    )

    invented = SchemaRegistry()
    target = registry.BEACON_SOURCE_IDS[0]
    invented.register(
        SchemaContract(
            source_id=target,
            version="a-version-only-this-test-knows",
            fields=(FieldSpec("beacon_id", "string"), FieldSpec("received_at", "timestamp")),
        )
    )
    built = readiness.build_report(
        schema_registry=invented, env={}, secrets_file=no_secrets
    )
    row = built.by_id(target)
    assert row.schema.registered
    assert row.schema.current_version == "a-version-only-this-test-knows"
    assert row.schema.field_count == 2
    # The registry row's mapping_version is Beacon's; the contract's is this test's.  B2 asks
    # them to be one string, so the report must report the disagreement rather than smooth it.
    assert row.schema.version_matches_mapping is False
    assert any("disagrees with the row's mapping version" in gap for gap in row.gaps)


def test_a_registered_contract_reports_the_field_counts_the_contract_actually_has(report):
    from recon.connectors import schema_registry as shared

    for source_id in shared.REGISTRY.source_ids():
        if source_id not in report.source_ids:
            continue
        contract = shared.REGISTRY.get(source_id)
        row = report.by_id(source_id)
        assert row.schema.field_count == len(contract.fields)
        assert row.schema.required_field_count == sum(
            1 for spec in contract.fields if spec.required
        )


# ═══ derivation: auth ═══


def test_the_auth_shape_is_read_off_the_transport_that_serves_the_source(report):
    """SFTP resolves an SSH key and HTTP resolves a token pair — because the code does.

    Derived by reading the transport module's AST for a ``resolve_*`` call, so this is a
    statement about ``sftp.py`` and ``http.py`` rather than about a table somebody wrote that
    says what they ought to do.
    """
    seen: dict[str, set[str]] = {}
    for row in report.sources:
        if row.auth.required:
            seen.setdefault(row.transport.kind, set()).add(str(row.auth.kind))

    assert seen[str(TransportKind.SFTP)] == {credentials.CredentialKind.SSH_KEY.value}
    assert seen[str(TransportKind.HTTP_API)] == {credentials.CredentialKind.TOKEN_PAIR.value}


def test_no_credential_resolves_in_an_empty_environment(report):
    """The Week 1-3 access gate, as the report sees it.

    Not a defect: ``DOC2-016`` is Doc 2's own gate, and no vendor credential is obtainable for
    a prototype.  What matters is that the report *says so* rather than reporting a green
    auth cell it has not earned.
    """
    for row in report.sources:
        if not row.auth.required:
            continue
        assert not row.auth.configured
        assert row.auth.env_vars, f"{row.source_id} reports no variable an operator could set"
        assert any(item.kind == "ACCESS" for item in row.blocked)


def test_a_configured_credential_flips_the_auth_cell(no_secrets):
    """The mirror of the test above: supply the variables and the cell changes.

    Proves the cell reads the environment rather than reporting a constant.  The values are
    obvious nonsense and never leave this process — ``credentials.Secret`` is what stops one
    rendering anywhere, and nothing here reveals it.
    """
    target = next(
        source
        for source in readiness.declared_sources()
        if source.transport_kind is TransportKind.HTTP_API
    )
    env = {
        name: "not-a-real-token"
        for name in credentials.env_var_names(
            str(target.credential_ref), credentials.CredentialKind.TOKEN_PAIR
        )
    }
    built = readiness.build_report(env=env, secrets_file=no_secrets)
    row = built.by_id(target.source_id)
    assert row.auth.configured
    assert not any(
        item.kind == "ACCESS" and "no credential has been issued" in item.summary
        for item in row.blocked
    )
    assert "not-a-real-token" not in readiness.render_markdown(built)


# ═══ derivation: what the ingest layer adapts ═══
#
# The one cell on this report that lives on the far side of the connectors/ingest seam.
# ``readiness`` imports nothing from ``recon.ingest`` — ``test_the_report_module_imports_nothing
# _from_ingest`` asserts that on the import graph — so ``adapter_coverage`` is handed the module
# and finds the two tables on it *by shape*: a mapping whose values are all callable, and a set
# of strings.
#
# The tests below import ``recon.ingest.adapters`` directly and read the same two tables *by
# name*, which is the one thing the module under test may not do.  That asymmetry is the point,
# and it is the same argument ``_tier_rows_for`` makes further down: a probe checked only
# against its own output keeps agreeing with itself long after the thing it probes for has
# moved.


def test_adapter_coverage_finds_the_real_dispatch_table_and_the_real_allow_list():
    """The live answer, found a second way and compared.

    Equality in both directions rather than a subset, because the probe can be wrong twice
    over.  Finding too little is the silent denial the guards below exist for; finding too
    much would mean some unrelated collection of strings in the adapter layer had been absorbed
    into ``datasets``, and a stray name there reads as "this dataset is adapted" — a report
    flattering the build, which is the failure this whole module is shaped to prevent.
    """
    from recon.ingest import adapters

    coverage = readiness.adapter_coverage(adapters)
    assert coverage.source_systems == frozenset(str(key) for key in adapters._DISPATCH), (
        "the shape probe and the dispatch table disagree about which source systems have an "
        "adapter"
    )
    assert coverage.datasets == frozenset(adapters._QUALIFICATION_DATASETS), (
        "the shape probe and the per-dataset allow list disagree; either the probe missed the "
        "table, or it absorbed a second collection of strings that is not an allow list"
    )
    # Not a tautology: two empty sets are equal, and the whole hazard here is emptiness.
    assert coverage.source_systems and coverage.datasets


def test_adapter_coverage_refuses_an_object_with_no_dispatch_table():
    """Nothing shaped like a dispatch table means the question cannot be answered at all."""
    stand_in = SimpleNamespace(some_allow_list=frozenset({"a_dataset_name"}))
    with pytest.raises(ValueError) as excinfo:
        readiness.adapter_coverage(stand_in)
    assert "dispatch table" in str(excinfo.value)


def test_adapter_coverage_refuses_an_object_with_no_per_dataset_allow_list():
    """The other half of the same guard, and the half that was missing.

    An absent dispatch table denies every source at once, which is loud enough that a reader
    disbelieves the page.  An absent allow list denies only the sources whose mapping declares
    them individually — which is to say only the vendor datasets that actually have a reader —
    and it denies them in the same words the report uses for a dataset that genuinely has none.
    That is the answer a reader has no way to catch, so it must not be an answer at all.

    The third case is the control: hand in both shapes and the same function returns both,
    which is what makes the two failures above about the missing table rather than about the
    stand-in.
    """
    only_dispatch = SimpleNamespace(some_dispatch={"A_SOURCE_SYSTEM": lambda payload: payload})
    with pytest.raises(ValueError) as excinfo:
        readiness.adapter_coverage(only_dispatch)
    message = str(excinfo.value)
    assert "allow list" in message
    assert "deny a connector leg that works" in message, (
        "the guard raises without saying what the quiet answer would have cost, which is the "
        "only thing that tells the next reader why it is not simply returning an empty set"
    )

    both = SimpleNamespace(
        some_dispatch={"A_SOURCE_SYSTEM": lambda payload: payload},
        some_allow_list=frozenset({"a_dataset_name"}),
    )
    coverage = readiness.adapter_coverage(both)
    assert coverage.source_systems == frozenset({"A_SOURCE_SYSTEM"})
    assert coverage.datasets == frozenset({"a_dataset_name"})


def test_a_reshaped_allow_list_cannot_silently_unadapt_a_working_source(
    monkeypatch, no_secrets
):
    """The regression the guard exists for, reproduced exactly as it would arrive.

    ``_QUALIFICATION_DATASETS`` is a ``frozenset`` and the probe matches sets.  Make it a
    tuple.  Nothing changes in the layer that owns it — ``in`` works on both, and every test
    over there still passes — and over here the probe stops matching, ``datasets`` comes back
    empty, and the per-dataset rule answers "this adapter does not accept this dataset" for
    every vendor dataset that adapts.  No exception, no failing test, no signal of any kind:
    the one page in this repository whose job is to be honest about what is built would quietly
    deny a connector leg that works.

    So this asserts two things.  First that the wrong answer is real and reachable, by handing
    the rule an empty coverage directly — otherwise the guard would be standing in front of
    nothing.  Second that the public door now refuses to produce a report at all, which is the
    only outcome that beats a plausible lie.
    """
    from recon.ingest import adapters

    # The detector, armed.  A test that only asserts "this raises" passes just as happily
    # against a build where nothing adapted in the first place.  The population actually at
    # risk is narrower than "everything that adapts": a source is only checked against the
    # allow list when its mapping declares it by name, so a generated feed is answered by the
    # dispatch table alone and an empty allow list never reaches it.
    before = readiness.build_report(env={}, secrets_file=no_secrets, adapters=adapters)
    at_risk = [
        source
        for source in readiness.declared_sources()
        if before.by_id(source.source_id).adapter.adapts
        and before.by_id(source.source_id).mapping.per_source
    ]
    assert at_risk, (
        "no source both adapts and is checked by name, so nothing is at risk from an empty "
        "allow list and this test has nothing to prove"
    )

    # What the guard stands in front of, stated rather than described: the same source, the
    # same rule, with the allow list empty.
    target = at_risk[0]
    denied = readiness._adapter_for(
        target,
        readiness.AdapterCoverage(
            source_systems=frozenset({str(target.source_system)}), datasets=frozenset()
        ),
        True,
    )
    assert denied.adapts is False
    assert "does not accept this dataset" in str(denied.refusal), (
        f"an empty allow list reports {target.source_id} as a dataset nobody has decided the "
        "meaning of, which is a finding rather than a lookup failure"
    )

    # And the door that a caller actually uses, after the behaviour-preserving refactor.
    monkeypatch.setattr(
        adapters, "_QUALIFICATION_DATASETS", tuple(adapters._QUALIFICATION_DATASETS)
    )
    with pytest.raises(ValueError) as excinfo:
        readiness.build_report(env={}, secrets_file=no_secrets, adapters=adapters)
    assert "allow list" in str(excinfo.value)


def test_the_connectivity_endpoint_tells_no_adapter_apart_from_no_decision(tmp_path):
    """Registry row to probe to cell to JSON, over HTTP, on the three states that matter.

    *Not adapted* covers two opposite findings and the payload has to keep them apart.  One
    vendor dataset here is mapped, contract-checked, perfectly readable and deliberately has no
    adapter — the rows are rebate money rather than a qualification decision, and adapting them
    as one would land a record whose meaning was invented.  The other two have a reader.  A
    boolean renders those identically; ``refusal`` is what does not.

    The names are written out rather than derived, and that is deliberate here.  Deriving the
    expected set from ``_QUALIFICATION_DATASETS`` would assert the allow list against itself and
    keep passing if a dataset silently dropped out of it.  ``readiness.py`` may not name a
    vendor; a test may, and this is where one has to.  The last assertion ties the pinned names
    back to the registry, so a seventh vendor row fails here rather than going unchecked.
    """
    pytest.importorskip(
        "fastapi", reason="the API layer is an optional extra: pip install -e '.[api]'"
    )
    from fastapi.testclient import TestClient

    from recon.api.app import create_app
    from recon.config import load_settings

    adapted = {"verity_accumulations", "craneware_claims_report"}
    refused = {"verity_invoices"}
    assert adapted | refused == set(registry.VENDOR_SOURCE_IDS), (
        "the secure-file registry rows have changed; this test pins which of them have an "
        "adapter and the pinned list no longer covers them"
    )

    response = TestClient(create_app(load_settings("demo", data_dir=tmp_path))).get(
        "/api/connectivity"
    )
    assert response.status_code == 200
    rows = {row["source_id"]: row for row in response.json()["sources"]}

    for source_id in sorted(adapted):
        row = rows[source_id]
        assert row["adapter"]["adapts"] is True, (
            f"{source_id} has an adapter and the endpoint says it does not; the report is "
            "denying a connector leg that works"
        )
        assert row["ingest"] == "READS_AND_ADAPTS"
        assert row["adapter"]["refusal"] is None

    for source_id in sorted(refused):
        row = rows[source_id]
        assert row["adapter"]["adapts"] is False
        assert row["ingest"] == "READS"
        # Readable, mapped, and refused on purpose — three facts a single cell would collapse.
        assert row["transport"]["implemented"]
        assert row["mapping"]["per_source"] is True
        refusal = row["adapter"]["refusal"]
        assert "not a reader" in refusal, (
            f"{source_id} is refused without saying why; 'nobody wrote a reader' and 'nobody "
            "has decided what these rows mean' are opposite findings"
        )
        assert "no adapter is registered" not in refusal, (
            f"{source_id} is reported as having no adapter at all, which is the other refusal "
            "and is not true of it"
        )


# ═══ derivation: mock fidelity ═══


def _tier_rows_for(dataset_prefix: str) -> dict[str, int]:
    """Tally one dataset out of ``MOCK_FIELDS.md`` by a deliberately different method.

    ``readiness.mock_field_tallies`` parses with a regex and groups by a dotted prefix.  This
    counts lines that start with the literal prefix and splits on pipes.  Two readers that
    agree are evidence; one reader compared against itself is not, which is the same argument
    ``schema_registry`` makes for declaring contracts rather than deriving them.
    """
    text = (REPO_ROOT / "src" / "recon" / "mocks" / "MOCK_FIELDS.md").read_text(
        encoding="utf-8"
    )
    counts: dict[str, int] = {}
    for line in text.splitlines():
        if not line.startswith(f"| `{dataset_prefix}."):
            continue
        tier = line.split("|")[2].strip()
        counts[tier] = counts.get(tier, 0) + 1
    return counts


@pytest.mark.parametrize("source_id", registry.VENDOR_SOURCE_IDS + registry.BEACON_SOURCE_IDS)
def test_mock_fidelity_matches_the_provenance_table_for_that_dataset(report, source_id):
    """Every vendor source's tally comes from the hand-written table, field by field.

    Also the guard on the singular/plural rule.  The registry spells a source id
    ``..._acknowledgements`` and the provenance table spells the dataset
    ``beacon.acknowledgement``; a rule that stopped matching would report zero rows, which
    reads as "no fields declared" rather than as "the lookup broke".  A zero here fails.
    """
    row = report.by_id(source_id)
    assert row.fidelity.applies, (
        f"{source_id} resolves to no provenance table; the dataset-name rule in "
        "readiness._singular no longer reaches src/recon/mocks/MOCK_FIELDS.md"
    )
    assert row.fidelity.rows > 0

    expected = _tier_rows_for(row.fidelity.dataset)
    assert row.fidelity.spec == expected.get("SPEC", 0)
    assert row.fidelity.standard == expected.get("STANDARD", 0)
    assert row.fidelity.invented == expected.get("INVENTED", 0)


def test_fidelity_is_reported_honestly_for_the_file_vendors(report):
    """Not one Verity or Craneware field has a vendor source, and the report must say so.

    ``VERITY-006`` names a Split Transaction data specification that is never published and
    ``CRANEWARE-001`` names five reports and no column of any of them.  A fidelity cell that
    implied otherwise would be the exact "confidently wrong" artefact ``docs/vendor_evidence/``
    exists to prevent, moved up a layer.
    """
    file_vendor_rows = [
        report.by_id(source_id) for source_id in registry.VENDOR_SOURCE_IDS
    ]
    assert file_vendor_rows
    for row in file_vendor_rows:
        assert row.fidelity.spec == 0
        assert row.fidelity.invented == row.fidelity.rows
        assert any("every field of this mock is INVENTED" in gap for gap in row.gaps)


def test_every_evidence_id_the_fidelity_cell_cites_is_in_the_index(report):
    index = {json.loads(line)["id"] for line in EVIDENCE_INDEX.read_text(encoding="utf-8").splitlines() if line.strip()}
    for row in report.sources:
        dangling = sorted(set(row.fidelity.evidence_ids) - index)
        assert not dangling, f"{row.source_id} cites {dangling}, absent from index.jsonl"


# ═══ derivation: vendor-blocked ═══


def test_every_blocked_documentation_item_resolves_to_a_real_evidence_entry(report):
    """A blocked item is a fact with a URL and a date, never an assertion.

    The whole value of recording a 403 is that it is checkable: someone can open the URL and
    see the same refusal.  An item with an id the index does not hold is a claim wearing a
    citation, which is the single most expensive mistake this repository's evidence rules
    exist to prevent.
    """
    rows = {
        json.loads(line)["id"]: json.loads(line)
        for line in EVIDENCE_INDEX.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    documentation = [
        item
        for row in report.sources
        for item in row.blocked
        if item.kind == "DOCUMENTATION"
    ]
    assert documentation, "no documentation-blocked item at all; the evidence index is not being read"

    for item in documentation:
        assert item.evidence_id in rows, (
            f"blocked item cites {item.evidence_id}, which is not in index.jsonl"
        )
        entry = rows[item.evidence_id]
        assert item.url == entry["url"]
        assert item.retrieved == entry["retrieved"]
        assert item.summary == entry["establishes"]


def test_every_unavailable_beacon_article_is_surfaced_against_its_sources(report):
    """The seven 403s are the headline finding of wave 0 and must not be quietly dropped."""
    index = [
        json.loads(line)
        for line in EVIDENCE_INDEX.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    unavailable_by_vendor: dict[str, set[str]] = {}
    for entry in index:
        if entry["status"] == "UNAVAILABLE":
            unavailable_by_vendor.setdefault(entry["vendor"], set()).add(entry["id"])
    assert unavailable_by_vendor, "no UNAVAILABLE evidence entry; wave 0's finding has been lost"

    for row in report.sources:
        expected = unavailable_by_vendor.get(row.vendor, set())
        surfaced = {item.evidence_id for item in row.blocked if item.kind == "DOCUMENTATION"}
        assert expected <= surfaced, (
            f"{row.source_id} does not surface {sorted(expected - surfaced)}"
        )


def test_an_access_gate_item_names_the_variables_that_would_clear_it(report):
    for row in report.sources:
        for item in row.blocked:
            if item.kind != "ACCESS" or not item.env_vars:
                continue
            assert all(name.startswith(credentials.ENV_PREFIX) for name in item.env_vars)


# ═══ honesty: what the report must never say ═══


def test_the_report_never_claims_a_live_vendor_connection(report):
    """The one sentence this document must not contain.

    Every connector in this build talks to a local mock.  A readiness report is read as a
    statement of fact by someone deciding whether to trust the pipeline, and "Verity is
    connected" is the most expensive thing it could get wrong.
    """
    assert report.live_vendor_connections == ()
    for row in report.sources:
        assert not row.talks_to_a_vendor
        assert row.reach is not Reach.VENDOR
        assert row.stage is not Stage.WORKING_CONNECTION
        assert row.stage is not Stage.PRODUCTION_READY


def test_the_rendered_document_distinguishes_the_three_levels(report):
    """Connector-ready, working connection and production-ready must not read as synonyms."""
    markdown = readiness.render_markdown(report)
    assert Stage.CONNECTOR_READY.value in markdown
    for phrase in ("Working connection", "Production-ready", "authorized"):
        assert phrase in markdown, f"the rendered report does not distinguish {phrase!r}"
    assert "No source in this build has ever reached a vendor system." in markdown


def test_a_vendor_endpoint_with_a_credential_would_be_reported_as_a_live_connection(no_secrets):
    """The detector is armed, not merely absent.

    A test that only asserts "no live connection" passes just as happily against a report that
    can never report one.  This points a row at a real host, resolves a credential for it, and
    asserts the report says so — which is what makes the assertion in the test above mean
    something.
    """
    source_id = registry.BEACON_SOURCE_IDS[0]
    sources = readiness.declared_sources(
        beacon_endpoints={source_id: "https://api.example-vendor.test/v1"}
    )
    target = registry.by_id(sources, source_id)
    env = {
        name: "not-a-real-token"
        for name in credentials.env_var_names(
            str(target.credential_ref), credentials.CredentialKind.TOKEN_PAIR
        )
    }
    built = readiness.build_report(sources=sources, env=env, secrets_file=no_secrets)
    row = built.by_id(source_id)
    assert row.reach is Reach.VENDOR
    assert row.talks_to_a_vendor
    assert built.live_vendor_connections == (source_id,)
    assert "A source above reaches a vendor endpoint" in readiness.render_markdown(built)


def test_a_loopback_endpoint_is_a_mock_and_not_a_vendor(no_secrets):
    source_id = registry.BEACON_SOURCE_IDS[0]
    sources = readiness.declared_sources(
        beacon_endpoints={source_id: "http://127.0.0.1:8099/v1"}
    )
    built = readiness.build_report(sources=sources, env={}, secrets_file=no_secrets)
    row = built.by_id(source_id)
    assert row.reach is Reach.LOCAL_MOCK
    assert not row.talks_to_a_vendor
    assert "local mock" in readiness.render_source(row)


# ═══ control totals ═══


def test_control_totals_are_read_from_the_table_and_tolerate_it_being_empty(
    conn, no_secrets
):
    """Requirement F2's column, read through the table rather than through a module.

    Queried and not imported, deliberately: the module that writes these rows is a separate
    deliverable, and a report that imported it would be restating its author's intent instead
    of reading what actually landed.  A database with the table and no rows is reported as
    "not measured" and never as "clean".
    """
    empty = readiness.build_report(control_totals=conn, env={}, secrets_file=no_secrets)
    target = registry.VENDOR_SOURCE_IDS[0]
    assert empty.by_id(target).control_totals.storage_declared
    assert empty.by_id(target).control_totals.checked is None
    assert empty.by_id(target).control_totals.clean is None

    conn.executemany(
        "INSERT INTO control_total (source_id, source_file, file_sha256, declared_count, "
        "observed_count, reconciled, checked_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (target, "a.csv", "0" * 64, 500, 500, 1, "1970-01-01T00:00:00Z"),
            (target, "b.csv", "1" * 64, 500, 450, 0, "1970-01-01T00:00:00Z"),
        ],
    )
    conn.commit()

    filled = readiness.build_report(control_totals=conn, env={}, secrets_file=no_secrets)
    totals = filled.by_id(target).control_totals
    assert (totals.checked, totals.reconciled, totals.unreconciled) == (2, 1, 1)
    assert totals.clean is False
    assert any("did not reconcile" in gap for gap in filled.by_id(target).gaps)


def test_a_database_without_the_table_is_not_an_error(no_secrets):
    handle = sqlite3.connect(":memory:")
    try:
        built = readiness.build_report(
            control_totals=handle, env={}, secrets_file=no_secrets
        )
    finally:
        handle.close()
    assert built.by_id(registry.VENDOR_SOURCE_IDS[0]).control_totals.checked is None


# ═══ golden claims ═══


def test_the_golden_claim_column_reflects_the_test_module_that_exists(report):
    """Derived from the F1 trace module's AST, and correct whether or not it has landed.

    Asserted as a property rather than as a count on purpose: this wave builds the trace suite
    in parallel, so a test pinned to "not traced" would go red the hour it arrives and a test
    pinned to "traced" would be red until then.  What must hold either way is that every name
    reported is a real test function in a real module.
    """
    import ast

    for row in report.sources:
        if not row.golden.traced:
            continue
        module = REPO_ROOT / "tests" / str(row.golden.test_module)
        assert module.exists()
        functions = {
            node.name
            for node in ast.walk(ast.parse(module.read_text(encoding="utf-8")))
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        assert set(row.golden.traced_by) <= functions


def test_a_golden_trace_naming_a_source_is_attributed_to_it(tmp_path, monkeypatch):
    """The attribution rule, exercised against a module written here.

    The real trace module belongs to another deliverable, so this builds one and checks that a
    test function naming a source id is credited to that source — and that one naming nothing
    is credited to nobody.
    """
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    target = registry.VENDOR_SOURCE_IDS[0]
    (tests_dir / "test_golden_claims.py").write_text(
        "def test_paid_claim_traces_end_to_end():\n"
        f"    source_id = {target!r}\n"
        "    assert source_id\n"
        "\n"
        "def test_unrelated():\n"
        "    assert True\n",
        encoding="utf-8",
    )

    traces = readiness._golden_traces(tmp_path)
    assert traces.module == "test_golden_claims.py"
    assert traces.naming(target) == ("test_paid_claim_traces_end_to_end",)
    assert traces.naming("a_source_nobody_traces") == ()
    assert set(traces.functions) == {"test_paid_claim_traces_end_to_end", "test_unrelated"}


def test_a_golden_trace_is_attributed_by_filename_as_well_as_by_source_id(
    tmp_path, no_secrets
):
    """An F1 trace cites its origin as ``raw["source_file"] == "...jsonl"``.

    That is the honest citation — a claim arrives in a document, and the document is what the
    assertion can see.  Attributing only on the source id would report a fully traced feed as
    untraced, which is the kind of false negative that gets a column ignored.
    """
    from recon import config

    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    filename = config.FEED_FILENAMES[0]
    (tests_dir / "test_golden_claims.py").write_text(
        "def test_golden_paid_claim_traces_end_to_end():\n"
        f"    assert raw['source_file'] == {filename!r}\n",
        encoding="utf-8",
    )
    traces = readiness._golden_traces(tmp_path)
    assert traces.naming(filename) == ("test_golden_paid_claim_traces_end_to_end",)

    feeds = tmp_path / "feeds"
    feeds.mkdir()
    built = readiness.build_report(
        sources=readiness.declared_sources(feeds_dir=feeds),
        repo_root=tmp_path,
        env={},
        secrets_file=no_secrets,
    )
    row = built.by_id(Path(filename).stem)
    assert row.golden.traced
    assert row.golden.traced_by == ("test_golden_paid_claim_traces_end_to_end",)


def test_the_four_archetypes_are_counted_at_the_report_level(report):
    """Doc 2 step 5 names four, and the report must account for all four by name.

    Counted at the report level because an archetype crosses feeds: a paid claim is an
    adjudication, a remittance and a bank deposit.  Attributing it to one source would credit
    whichever feed the test happened to assert on first.
    """
    from recon.mocks.coverage import ARCHETYPES

    assert tuple(name for name, _ in report.archetype_traces) == ARCHETYPES
    markdown = readiness.render_markdown(report)
    for name in ARCHETYPES:
        assert name in markdown


# ═══ the rendered document ═══


def test_the_rendered_markdown_is_byte_identical_across_runs(no_secrets):
    """Requirement §4.11.  A document that differs on every run is a document nobody reads."""
    first = readiness.render_markdown(
        readiness.build_report(env={}, secrets_file=no_secrets)
    )
    second = readiness.render_markdown(
        readiness.build_report(env={}, secrets_file=no_secrets)
    )
    assert first == second
    assert first.encode("utf-8") == second.encode("utf-8")


def test_no_wall_clock_leaks_into_the_rendered_bytes(report):
    """The pinned epoch is in the document and no other timestamp of that shape is.

    Evidence retrieval dates are ``YYYY-MM-DD`` and are facts about when a page was fetched,
    so they belong.  A full ``...T..:..:..Z`` stamp other than the pinned one would be a clock
    reading, and a clock reading is a diff on every regeneration.
    """
    markdown = readiness.render_markdown(report)
    assert readiness.GENERATED_AT in markdown
    stamps = set(re.findall(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", markdown))
    assert stamps == {readiness.GENERATED_AT}, f"unpinned timestamps in the report: {stamps}"


def test_one_document_per_source_plus_an_index(report):
    """F3 asks for one generated document per source, so that is the shape returned."""
    documents = readiness.render_documents(report)
    for source_id in report.source_ids:
        assert f"{source_id}.md" in documents
        assert source_id in documents[f"{source_id}.md"]
    assert "index.md" in documents
    assert readiness.render_documents(report) == documents


def test_the_summary_table_has_one_row_per_source_and_no_empty_cell(report):
    markdown = readiness.render_markdown(report)
    body = markdown.split("|---|", 1)[1].split("\n\n", 1)[0]
    rows = [line for line in body.splitlines() if line.startswith("| ")]
    assert len(rows) == len(report.sources)
    for line in rows:
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        assert all(cells), f"empty cell in the gate table: {line}"


def test_nothing_is_written_to_disk_by_importing_or_building(report, tmp_path):
    """The report returns text.  A generated file in the tree is a stale file in the tree."""
    before = sorted(path.name for path in tmp_path.iterdir())
    readiness.render_markdown(report)
    readiness.render_documents(report)
    assert sorted(path.name for path in tmp_path.iterdir()) == before


# ═══ the structural guarantees this package must not lose ═══


def test_the_report_module_imports_nothing_from_ingest():
    """The connectors/ingest seam, restated for this module.

    Checked on the import graph rather than on the file's text, because the file's text
    *explains* the seam and a substring search cannot tell an explanation from a violation.
    """
    import ast

    tree = ast.parse(_READINESS_SOURCE)
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    offenders = sorted({name for name in imported if name.startswith("recon.ingest")})
    assert not offenders, f"readiness.py imports from ingest: {offenders}"


def test_the_report_module_can_reach_no_path_under_truth():
    """Requirement §4.9.

    The report reads provenance tables, an evidence index, a schema file and a coverage
    artefact, and every one of those paths is built from a module-level constant.  None may
    name the ground-truth directory, and none may be assembled from a caller's string — which
    is checked by looking at the constants themselves rather than at prose about them.
    """
    from recon import config

    path_constants = [
        str(value)
        for name, value in vars(readiness).items()
        if isinstance(value, (str, Path)) and not name.startswith("__")
    ]
    offenders = [text for text in path_constants if config.TRUTH_SUBDIR in Path(text).parts]
    assert not offenders, f"a readiness path constant reaches ground truth: {offenders}"


def test_an_explicitly_supplied_source_is_reported_without_being_in_the_registry(no_secrets):
    """The report describes the rows it is handed, whatever they are.

    A deployment's registry is not necessarily this repository's, and a report that could only
    describe sources it already knew about would be a report about us rather than about the
    deployment.
    """
    row = Source(
        source_id="an_unknown_source",
        vendor="nobody",
        transport_kind=TransportKind.LOCAL_DIRECTORY,
        source_system=SourceSystem.BANK,
        filenames=("x.csv",),
        endpoint="",
        payload_format=PayloadFormat.BANK_CSV,
    )
    built = readiness.build_report(sources=[row], env={}, secrets_file=no_secrets)
    assert built.source_ids == ("an_unknown_source",)
    reported = built.by_id("an_unknown_source")
    assert reported.reach is Reach.UNCONFIGURED
    assert not reported.auth.required
    assert not reported.fidelity.applies
    assert "an_unknown_source" in readiness.render_markdown(built)


def test_by_id_names_what_it_holds_when_asked_for_something_it_does_not(report):
    with pytest.raises(KeyError) as excinfo:
        report.by_id("not_a_source")
    assert "not_a_source" in str(excinfo.value)
    assert report.source_ids[0] in str(excinfo.value)
