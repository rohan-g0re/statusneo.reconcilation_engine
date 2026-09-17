"""Contract-pharmacy sites: the identity below the NPI (requirement E4).

``pharmacy_npi`` alone collapses a health system's contract-pharmacy network into one
identity.  A covered entity can contract several dispensing locations that all bill
under a single NPI-holding entity, and once they do, "which site dispensed this" is a
question the NPI cannot answer.  This module is the reference table that can.

**The module is deliberately invisible to the generators.**  Nothing under
``src/recon/generators/`` imports it, and ``tests/test_sites.py`` asserts that by
walking the AST of every generator module rather than trusting this sentence.  The six
feed files are byte-pinned — hashes in the manifest, recorded agent traces keyed on
them — so a site field that reached a generator would either change a record or move an
RNG draw, and either one breaks the build.  That is also why this is a new module and
not a widened :class:`~recon.reference.entities.Pharmacy`: the generators construct and
iterate ``Pharmacy``, and they do not construct or iterate anything here.

Sites are consumed on the *reading* side only, and today that means exactly one consumer:
the ingest layer resolving an ``episode.site_id`` via :func:`resolve_site`.

An earlier version of this line also claimed "the connector mappings that carry a vendor's
site column". **No such column exists.** No vendor mapping reads or writes a site, nothing
ever sets ``CanonicalRecord.site_id``, and no key builder takes a site argument — so
``episode.site_id`` is populated only for an NPI that already resolved unambiguously, which
is precisely the case requirement E4 was *not* about. The honest statement of where this
stands is in ``ingest/pipeline.py``'s ``_site_for``; a vendor-supplied site is the work that
would make E4 real, and it is not done.

═══ Why the NPI cannot be resolved by guessing ═══════════════════════════════════════

An episode anchored on a core feed carries an NPI and nothing finer.  When that NPI
holds two sites there is no honest answer, so :func:`resolve_site` does not return one:
:attr:`SiteResolution.site_id` is ``None`` unless exactly one site bills under the NPI.
The candidates are still reachable, because showing an operator "this could be either of
these two" is useful and is not the same act as picking one.  Choosing anyway requires
indexing into :attr:`SiteResolution.candidates` on purpose, which is visible in a diff.
This is Decision A23's rule applied one level down: a mapping that cannot be made is
reported, never normalised away.

═══ Field provenance ═════════════════════════════════════════════════════════════════

Written in the form ``src/recon/connectors/vendors/MOCK_FIELDS.md`` uses, and read the
same way: ``SPEC`` cites an evidence id in ``docs/vendor_evidence/index.jsonl``,
``STANDARD`` cites a public standard, ``INVENTED`` is a decision of ours and says so.

**The entity itself is ``SPEC``.**  ``DOC2-010`` (Assignment Doc 2, page 4, verbatim):
"Map 340B ID, site / contract pharmacy, Rx/claim identifiers, NDC, qualification/match
status, invoice and source transaction keys."  A site is named there as its own field to
map, distinct from the 340B ID and from the claim identifiers.  That is what justifies a
table existing at all; it justifies nothing about the table's shape.

| field | tier | evidence | note |
|---|---|---|---|
| `site_id` | INVENTED | — | no vendor publishes a site identifier or its format. ``BEACON-013`` is the nearest precedent and is explicit that even the Beacon ID's *format* is `UNKNOWN`; a site id is further from the wire than that |
| `npi` | INVENTED | — | the NPI the site bills under. Tagged the way `pharmacy_npi` is tagged in `connectors/vendors/MOCK_FIELDS.md` and for the same reason: the *identifier* is NPPES's, but this column, its name and its being a foreign key into our own `Pharmacy` table are ours |
| `name` | INVENTED | — | a human label. Fictional, like every name in `entities.py` (Decision 38) |
| `covered_entity_id` | INVENTED | — | `DOC2-010` names "340B ID" and "site / contract pharmacy" as two separate fields to map and never says one hangs off the other. The direction of this foreign key — a site belongs to exactly one covered entity — is our modelling decision, not a vendor's statement |

**Every row below is ``INVENTED``, and there was no possibility of anything else.**  No
vendor document gives Harborview's contract-pharmacy roster, because Harborview does not
exist: the entity universe is fictional by Decision 38 and the assignment's
confidentiality clause. What the rows have to be *shaped* like is set by E4's acceptance
criterion rather than by a source, and the shape is the only claim being made:

* two sites under NPI ``1234567893`` — the registered main pharmacy — so a network under
  one NPI-holding entity is representable at all.  Without this pair E4's acceptance
  criterion is not merely unmet, it is unmeetable, so ``validate()`` asserts it.
* one site under NPI ``1044723199``, so the unambiguous case is covered too and
  :func:`resolve_site` has something to resolve.

That satellite site belongs to the **decoy** covered entity, and that is load-bearing
rather than decorative.  ``entities.py`` requires NPI ``1044723199`` to be registered with
neither covered entity, which is what makes ``UNREGISTERED_LOCATION`` a state the
generator can produce.  Hanging its site off our own entity would assert exactly the
registration that invariant needs absent.  Hanging it off the decoy says something
truer and sharper: the location is a real contract pharmacy, contracted by somebody
else.  A Harborview 340B dispense there is unregistered because it is **not ours**, not
because the address is unknown to anyone.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType

from recon.reference.errors import UnknownEntityError

__all__ = [
    "Site",
    "SiteResolution",
    "sites",
    "site_by_id",
    "sites_for_npi",
    "sites_for_covered_entity",
    "resolve_site",
]


# --- dataclasses -----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Site:
    """One contract-pharmacy location.

    ``npi`` is not unique across rows — that is the entire point of the table.
    """

    site_id: str
    npi: str
    name: str
    covered_entity_id: str


@dataclass(frozen=True, slots=True)
class SiteResolution:
    """What an NPI alone can honestly say about which site dispensed.

    Three outcomes and no fourth: the NPI holds exactly one site, it holds several, or
    it holds none.  :attr:`site_id` answers only in the first case; the other two are
    ``None``, which is what the nullable ``episode.site_id`` column is for.
    """

    npi: str
    candidates: tuple[Site, ...]

    @property
    def is_unique(self) -> bool:
        """Exactly one site bills under this NPI."""
        return len(self.candidates) == 1

    @property
    def is_ambiguous(self) -> bool:
        """Two or more sites bill under this NPI, and the NPI cannot separate them."""
        return len(self.candidates) > 1

    @property
    def is_unknown(self) -> bool:
        """No site is on file for this NPI at all — a different gap from ambiguity."""
        return not self.candidates

    @property
    def site_id(self) -> str | None:
        """The site id when, and only when, exactly one site bills under this NPI."""
        return self.candidates[0].site_id if self.is_unique else None


# --- the universe ----------------------------------------------------------

_SITES: tuple[Site, ...] = (
    # Two sites, one NPI, one covered entity.  This pair *is* requirement E4.
    Site(
        "SITE_MAIN_CAMPUS",
        "1234567893",
        "HARBORVIEW SPECIALTY PHARMACY - MAIN CAMPUS",
        "DSH310074",
    ),
    Site(
        "SITE_EASTLAKE",
        "1234567893",
        "HARBORVIEW SPECIALTY PHARMACY - EASTLAKE CLINIC",
        "DSH310074",
    ),
    # One site, one NPI: the unambiguous case.  Contracted by the *decoy* entity -- see
    # the module docstring; the satellite must stay unregistered with our own entity or
    # UNREGISTERED_LOCATION stops being generatable.
    Site(
        "SITE_NORTHSIDE",
        "1044723199",
        "HARBORVIEW NORTHSIDE SATELLITE",
        "PED045210A",
    ),
)

_SITE_BY_ID = MappingProxyType({s.site_id: s for s in _SITES})


def _group(key) -> MappingProxyType:
    grouped: dict[str, list[Site]] = {}
    for site in _SITES:
        grouped.setdefault(key(site), []).append(site)
    return MappingProxyType({k: tuple(v) for k, v in grouped.items()})


_SITES_BY_NPI = _group(lambda s: s.npi)
_SITES_BY_COVERED_ENTITY = _group(lambda s: s.covered_entity_id)


# --- accessors -------------------------------------------------------------


def sites() -> tuple[Site, ...]:
    return _SITES


def site_by_id(site_id: str) -> Site:
    try:
        return _SITE_BY_ID[site_id]
    except KeyError:
        raise UnknownEntityError("site", site_id) from None


def sites_for_npi(npi: str) -> tuple[Site, ...]:
    """Every site billing under this NPI, empty when none.

    Empty rather than raising: an NPI with no site on file is a fact about coverage,
    and callers that need it to be an error have :func:`resolve_site` to say so.
    """
    return _SITES_BY_NPI.get(npi, ())


def sites_for_covered_entity(covered_entity_id: str) -> tuple[Site, ...]:
    """A covered entity's contract-pharmacy network — the view E4 exists to make visible."""
    return _SITES_BY_COVERED_ENTITY.get(covered_entity_id, ())


def resolve_site(npi: str) -> SiteResolution:
    """Does this NPI resolve to exactly one site, or is it ambiguous?

    The only accessor the ingest layer should call.  It never returns a chosen site for
    an ambiguous NPI, because there is nothing to choose from — see the module docstring.
    """
    return SiteResolution(npi=npi, candidates=sites_for_npi(npi))
