"""Requirement E4 — site / contract-pharmacy identity.

E4 verbatim: "Doc 2 says 'provider/site,' and every TPA page mentions site or contract
pharmacy.  We key on ``pharmacy_npi`` alone, which collapses a health system's
contract-pharmacy network into one identity.  *Acceptance:* two contract pharmacies under
the same NPI-holding entity are distinguishable."

Two things are under test and they pull in opposite directions.  The first is that the
table can tell two sites apart under one NPI — the acceptance criterion.  The second is
that the table is **invisible to the generators**, because the six feed files are
byte-pinned and a reference field a generator can reach either changes a record or moves
an RNG draw.  The last test in this file is the one that keeps the second promise, and it
walks the AST rather than scanning text: a module that merely *documents* the prohibition
in its docstring must not fail for saying so.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from recon import reference
from recon.reference import entities, sites
from recon.reference.errors import UnknownEntityError

# ═══ A. The table exists and holds together ══════════════════════════════════════════


def test_sites_exist():
    assert len(sites.sites()) >= 2, "E4 needs at least two sites to have anything to say"


def test_site_ids_are_unique():
    ids = [s.site_id for s in sites.sites()]
    assert len(set(ids)) == len(ids), f"duplicate site id among {ids}"


def test_every_site_covered_entity_resolves():
    """A site pointing at a covered entity that does not exist is a dangling identity."""
    for site in sites.sites():
        entities.covered_entity_by_id(site.covered_entity_id)


def test_every_site_npi_resolves_to_a_pharmacy():
    for site in sites.sites():
        entities.pharmacy_by_npi(site.npi)


def test_site_by_id_names_the_missing_id():
    with pytest.raises(UnknownEntityError) as excinfo:
        sites.site_by_id("SITE_NOT_A_REAL_SITE")
    assert excinfo.value.identifier == "SITE_NOT_A_REAL_SITE"


def test_reference_validate_accepts_the_site_table():
    """The site invariants are wired into the package-level validator, not only into here."""
    reference.validate()


# ═══ B. E4's acceptance criterion ════════════════════════════════════════════════════


def test_two_contract_pharmacies_under_one_npi_are_distinguishable():
    """**This is E4's acceptance criterion, stated as such.**

    Two contract pharmacies billing under the same NPI-holding entity must be separable.
    Under ``pharmacy_npi`` alone they are one identity; under ``site_id`` they are two.
    """
    shared = [
        npi
        for npi in sorted({s.npi for s in sites.sites()})
        if len(sites.sites_for_npi(npi)) > 1
    ]
    assert shared, "no NPI holds two sites, so the criterion cannot be demonstrated at all"

    npi = shared[0]
    under_one_npi = sites.sites_for_npi(npi)
    assert len({s.site_id for s in under_one_npi}) == len(under_one_npi), (
        f"the {len(under_one_npi)} sites under NPI {npi} do not have distinct site ids"
    )
    assert len({s.name for s in under_one_npi}) == len(under_one_npi), (
        f"the sites under NPI {npi} are not distinguishable to a human reader either"
    )
    assert len({s.covered_entity_id for s in under_one_npi}) == 1, (
        "the pair E4 asks about is two contract pharmacies of ONE covered entity; "
        "a pair under different entities would already be separable without a site id"
    )


def test_the_covered_entity_network_is_visible():
    """What E4 buys: a health system's contract-pharmacy network, not one collapsed row."""
    own = entities.own_covered_entity()
    network = sites.sites_for_covered_entity(own.covered_entity_id)
    assert len(network) >= 2, f"{own.covered_entity_id} shows {len(network)} site(s), not a network"


def test_at_least_one_npi_holds_exactly_one_site():
    """The unambiguous branch needs a subject, or half of ``resolve_site`` is untested."""
    solo = [npi for npi in {s.npi for s in sites.sites()} if len(sites.sites_for_npi(npi)) == 1]
    assert solo, "every NPI is ambiguous, so the one-site case is never exercised"


# ═══ C. Resolution: one site, or an honest refusal ═══════════════════════════════════


def _unambiguous_npi() -> str:
    return sorted(npi for npi in {s.npi for s in sites.sites()} if len(sites.sites_for_npi(npi)) == 1)[0]


def _ambiguous_npi() -> str:
    return sorted(npi for npi in {s.npi for s in sites.sites()} if len(sites.sites_for_npi(npi)) > 1)[0]


def test_resolve_site_returns_the_single_site_for_an_unambiguous_npi():
    npi = _unambiguous_npi()
    resolution = sites.resolve_site(npi)
    assert resolution.is_unique
    assert not resolution.is_ambiguous
    assert not resolution.is_unknown
    assert resolution.site_id == sites.sites_for_npi(npi)[0].site_id


def test_resolve_site_refuses_to_choose_when_two_sites_share_an_npi():
    """The refusal is the requirement: ingest must not guess which site dispensed."""
    npi = _ambiguous_npi()
    resolution = sites.resolve_site(npi)
    assert resolution.is_ambiguous
    assert not resolution.is_unique
    assert resolution.site_id is None, (
        f"resolve_site picked {resolution.site_id!r} out of {len(resolution.candidates)} "
        "candidates; an NPI that holds two sites has no single answer"
    )


def test_an_ambiguous_resolution_still_exposes_its_candidates():
    """Refusing to choose is not refusing to say what the choices were.

    Showing an operator "this is one of these two" is a report; picking one is a
    fabrication.  The candidates are reachable so the first is possible without the second.
    """
    resolution = sites.resolve_site(_ambiguous_npi())
    assert len(resolution.candidates) > 1
    assert {s.site_id for s in resolution.candidates} == {
        s.site_id for s in sites.sites_for_npi(resolution.npi)
    }


def test_resolve_site_on_an_unknown_npi_is_unknown_rather_than_ambiguous():
    """"No site on file" and "two sites on file" are different gaps and must not merge."""
    resolution = sites.resolve_site("9999999999")
    assert resolution.is_unknown
    assert not resolution.is_ambiguous
    assert not resolution.is_unique
    assert resolution.site_id is None
    assert resolution.candidates == ()


def test_sites_for_npi_returns_empty_rather_than_raising():
    assert sites.sites_for_npi("9999999999") == ()


# ═══ D. The satellite invariant, restated at site grain ══════════════════════════════


def test_no_site_puts_the_satellite_npi_under_our_own_covered_entity():
    """``entities.py`` needs the satellite registered with neither covered entity, because
    that absence is what makes ``UNREGISTERED_LOCATION`` generatable.  A site row is a
    second place that fact could be contradicted, so it is asserted in both."""
    own_id = entities.own_covered_entity().covered_entity_id
    offenders = [
        s.site_id
        for s in sites.sites_for_npi(entities.satellite_pharmacy().npi)
        if s.covered_entity_id == own_id
    ]
    assert not offenders, (
        f"site(s) {offenders} claim the satellite NPI as a contract pharmacy of {own_id}, "
        "which asserts the registration UNREGISTERED_LOCATION needs absent"
    )


# ═══ E. Generator invisibility ═══════════════════════════════════════════════════════

#: The module a generator must never declare a dependency on.
_FORBIDDEN_IMPORT_PREFIXES: tuple[str, ...] = ("recon.reference.sites",)


def _imported_module_names(source: str) -> set[str]:
    tree = ast.parse(source)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def _generator_modules(repo_root: Path) -> list[Path]:
    paths = sorted((repo_root / "src" / "recon" / "generators").glob("*.py"))
    assert paths, "found no generator modules to check; the isolation test would pass vacuously"
    return paths


def test_no_generator_module_imports_the_sites_module(repo_root):
    """The constraint the whole design is shaped around, checked rather than intended.

    The six feed files are byte-pinned.  A generator that can reach this module can put a
    site on a record or move an RNG draw, and either changes a hash.  Modelled on
    ``tests/test_generators.py``'s import-boundary test, including its reason for using
    ``ast``: parsing the source proves the module *declares* no such dependency, whereas
    importing it and reading ``sys.modules`` would only prove the two can be loaded into
    one interpreter.

    Walked as an AST and never as text, because a text scan would fail a module for
    *documenting* the prohibition in a docstring — which is exactly what this file and
    ``sites.py`` both do.
    """
    offenders: list[str] = []
    for path in _generator_modules(repo_root):
        imported = _imported_module_names(path.read_text(encoding="utf-8"))
        hits = [
            name
            for name in imported
            if any(
                name == prefix or name.startswith(prefix + ".")
                for prefix in _FORBIDDEN_IMPORT_PREFIXES
            )
        ]
        offenders.extend(f"{path.name} imports {name}" for name in hits)
    assert not offenders, (
        f"generator module(s) reach the site table: {offenders}; the six feed hashes are "
        "pinned and reference data a generator can read is reference data that can move them"
    )


def test_no_generator_module_names_a_site_identifier(repo_root):
    """A second, narrower net: an import is not the only way a site could leak into a feed.

    A generator could name ``site_id`` on a record without importing anything.  Checked as
    identifiers and attribute names off the AST rather than as raw text, so a comment or a
    docstring explaining why sites are absent stays legal.
    """
    forbidden = {"site_id", "resolve_site", "sites_for_npi", "SiteResolution"}
    offenders: list[str] = []
    for path in _generator_modules(repo_root):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            name = None
            if isinstance(node, ast.Name):
                name = node.id
            elif isinstance(node, ast.Attribute):
                name = node.attr
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = node.name
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                # A JSON key is written as a string literal, not an identifier.
                name = node.value
            if name in forbidden:
                offenders.append(f"{path.name} names {name!r}")
    assert not offenders, f"site vocabulary reached the generators: {sorted(set(offenders))}"
