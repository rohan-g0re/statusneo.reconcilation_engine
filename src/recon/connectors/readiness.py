"""What is actually built, per source, derived by looking at what is actually built.

Requirement F3.  *"One generated document per source: transport implemented, auth
implemented, schema registered, mock fidelity (real spec vs invented), golden claims traced,
control totals reconciled, and what remains vendor-blocked.  Our version of Doc 2's Week 3
gate evidence table.  Acceptance: the report generates from code and config, never
hand-maintained."*

**The acceptance clause is the whole design, so it is worth saying what it forbids.**  A
table with ``"verity": {"transport": True}`` in it is not a readiness report; it is a claim
about a readiness report, and it becomes false the first afternoon somebody deletes a
transport.  Every cell below is therefore obtained by *inspecting an object* — a registry
row, a class attribute, an AST, a provenance table, a JSONL index, a SQL table — and there
is no literal anywhere in this module that names a vendor, a source id, a transport class or
a field count.

The one executable exception is :func:`declared_sources`, which calls the registry's three
public constructors by name because that is what they are called; a test asserts that those
names are only ever reached as attributes of ``registry`` and that no other function in this
module names a vendor at all.  Enumerating a module's public API is not a hand-maintained
cell — the moment ``registry.beacon_sources`` grows a fourth Beacon row, every cell in this
report follows it without an edit, which is the property that matters.

That has a consequence worth stating plainly, because it is the acceptance test.  Add a row
to ``registry._VENDOR_ROWS`` and it appears here with the right transport, the right
credential shape, the right contract status and the right blocked list, with **no edit to
this file**.  Delete ``SftpTransport`` and every SFTP source's transport cell flips to
unimplemented.  The report cannot flatter the build, because it has no opinions of its own.

═══ What the report is careful not to say ══════════════════════════════════════════

Every connector in this build talks to a local mock.  Not one of them has ever exchanged a
byte with Verity, Craneware or Beacon, and a document that read as though one had would be
the single most misleading artefact in the repository — it is exactly the "confidently
wrong" failure that ``docs/vendor_evidence/`` exists to prevent, moved one layer up from
field names to the connection itself.

So three levels are kept apart, and they are Doc 2's own, via
``docs/connectivity_layer_requirements.md`` §0:

* **Connector-ready** (:attr:`Stage.CONNECTOR_READY`) — Doc 2 steps 1-5.  The adapter is
  built to the vendor's published contract, exercised against a mock that reproduces it,
  wrapped in the same transport / auth / idempotency / checkpointing machinery a real
  connection would use, and switchable to a live endpoint by changing configuration only.
  **This is what this build targets and the highest level any source reaches here.**
* **Working connection** (:attr:`Stage.WORKING_CONNECTION`) — Doc 2's own bar, and it begins
  with the word *authorized*: ``DOC2-003``.  It needs a credential the vendor issues.  No
  source reaches it, and :attr:`SourceReadiness.blocked` says why in evidence ids.
* **Production-ready** (:attr:`Stage.PRODUCTION_READY`) — Doc 2 step 6: retry/replay,
  observability, DQ queue surfacing, secrets rotation, runbooks, alerting, lineage,
  backfill, cutover.  Explicitly out of scope; §0 lists every row and why.

:attr:`ReadinessReport.live_vendor_connections` exists so a test can assert the report never
claims one, rather than a reader having to take the prose above on trust.

═══ Determinism ═══════════════════════════════════════════════════════════════════

:func:`render_markdown` is byte-identical across runs for the same inputs.  There is no
wall-clock in the bytes: :data:`GENERATED_AT` is pinned to the same
``1970-01-01T00:00:00Z`` that ``ingest_batch.loaded_at`` is pinned to, for the reason
requirement §4.11 gives — a timestamp in a generated artefact is a diff on every run, and a
diff on every run is a document nobody reads.

**Nothing is written to disk on import, and nothing is written to a tracked file at all.**
:func:`render_markdown` and :func:`render_documents` return text.  A generated file that
lived in the tree would be a hand-maintained table wearing a generator's clothes: it would
go stale between runs and be reviewed as though it were current.

═══ What this module may not do ═══════════════════════════════════════════════════

It reads.  It does not fetch, it does not authenticate to anything remote, it writes no
table, it imports nothing from ``recon.ingest``, and it never opens a path under
``truth/``.  The one database it touches it touches read-only, through a connection a caller
hands it, and it tolerates the table being absent — a report that could only be produced
after a successful ingest would be useless on the day you most want it.

═══ Three states that are not one state ═══════════════════════════════════════════

A reader of this report asks three separate questions about a source and they were, for a
while, answered by one cell:

* **Can we read these bytes?**  A transport moves them and a mapping splits them.
* **Does anything turn them into records?**  An adapter accepts the rows, or it does not —
  and "does not" can be a deliberate refusal rather than an unwritten reader.
* **Are we allowed to fetch them yet?**  :attr:`SourceReadiness.awaiting_access`, which is
  Doc 2's Week 1-3 credential gate and nothing else.

:attr:`SourceReadiness.ingest` answers the first two and :attr:`SourceReadiness.awaiting_access`
answers the third, and they are deliberately **not** combined anywhere in this module.  The
same source is routinely fully readable *and* waiting on a credential at the same moment, and
a single cell for both has to pick one of those to say — which is how a source whose reader
works ends up rendered as a source that cannot be read.

The adapter answer is the one fact here that lives on the other side of the connectors/ingest
seam, so it is **handed in** rather than imported: :func:`build_report` takes an ``adapters``
object and :func:`adapter_coverage` finds the answer on it *by shape* — a mapping whose values
are callables, a set of strings — rather than by the names those tables happen to carry today.
The caller that owns both layers does the wiring, which is why this module still imports
nothing from ``recon.ingest`` and why a rename over there does not silently flip a cell here.
``None`` means nobody was asked, which is reported as "not measured" and never as "no".
"""

from __future__ import annotations

import ast
import importlib
import inspect
import json
import os
import pkgutil
import re
import sqlite3
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from types import ModuleType
from typing import Any
from urllib.parse import urlsplit

from recon import config
from recon.connectors import credentials, registry
from recon.connectors import schema_registry as schema_registry_module
from recon.connectors.registry import Source
from recon.connectors.schema_registry import SchemaRegistry

# ``domain.enums.TransportKind`` is deliberately **not** imported.  Every transport comparison
# below goes through ``str(source.transport_kind)`` against a class's ``kind`` attribute, so
# the report matches whatever a registry row declares — including a kind this module has never
# heard of.  Importing the enum would invite a branch on one of its members, and a branch on a
# member is a cell that stops being derived the day a fourth transport lands.

__all__ = [
    "GENERATED_AT",
    "UNCONFIGURED_ENDPOINT",
    "Stage",
    "Reach",
    "Ingest",
    "TransportReadiness",
    "AuthReadiness",
    "SchemaReadiness",
    "MappingReadiness",
    "AdapterCoverage",
    "AdapterReadiness",
    "MockFidelity",
    "GoldenClaimReadiness",
    "ControlTotalReadiness",
    "BlockedItem",
    "SourceReadiness",
    "ReadinessReport",
    "declared_sources",
    "transport_implementations",
    "vendor_mappings",
    "source_mappings",
    "adapter_coverage",
    "mock_field_tallies",
    "evidence_index",
    "build_report",
    "render_markdown",
    "render_source",
    "render_documents",
    "as_dict",
    "NO_LIVE_CONNECTION",
]

#: The timestamp rendered into every generated document.  Pinned, not read from a clock.
#:
#: The same value ``ingest_batch.loaded_at`` is pinned to, and for the same reason
#: (requirement §4.11): a wall-clock in the bytes makes every regeneration a diff, and a
#: document that always differs is a document whose real changes are invisible.  A reader who
#: wants to know *when* should look at the git history of the inputs, which is the honest
#: answer anyway — this report describes the repository, not the moment it was rendered.
GENERATED_AT = "1970-01-01T00:00:00Z"

#: The sentence this whole document exists to be able to say, and the one a reader must not
#: have to reconstruct from a table.
#:
#: A constant rather than two literals because it now has two audiences — the rendered markdown
#: and the JSON the dashboard reads — and a claim about vendor connectivity that could be worded
#: differently in two places is a claim that can go stale in one of them.  It is emitted only
#: when :attr:`ReadinessReport.live_vendor_connections` is empty; the non-empty case names the
#: sources instead, because at that point the interesting fact is *which*.
NO_LIVE_CONNECTION = "No source in this build has ever reached a vendor system."

#: What :func:`declared_sources` puts in :attr:`Source.endpoint` when a deployment has not
#: said where a source lives.  One constant for every source rather than a per-vendor string,
#: because a per-vendor string is a hand-written cell and this module has none.
UNCONFIGURED_ENDPOINT = "<not configured in this deployment>"

#: The package whose modules are searched for :class:`~recon.connectors.transport.Transport`
#: implementations and vendor mappings.  Named once; nothing below hardcodes a module.
_CONNECTORS_PACKAGE = "recon.connectors"
_VENDORS_PACKAGE = f"{_CONNECTORS_PACKAGE}.vendors"
_MOCKS_PACKAGE = "recon.mocks"

#: Where the hand-written provenance tables and the evidence index live, relative to the
#: repository root.  Both are inputs to this report and neither is generated (requirement
#: §4.5) — which is what makes tallying them a measurement rather than a tautology.
_PROVENANCE_GLOB = "src/recon/**/MOCK_FIELDS.md"
_EVIDENCE_INDEX = Path("docs") / "vendor_evidence" / "index.jsonl"
_SCHEMA_SQL = Path("src") / "recon" / "db" / "schema.sql"

#: Test modules searched for end-to-end golden-claim traces (requirement F1).  A glob rather
#: than a filename, so the module that lands is found whatever it is called, and an absent
#: one is simply "not traced yet" instead of an import error.
_GOLDEN_TEST_GLOB = "test_golden*.py"

#: Doc 2's four golden-claim archetypes, read from the module that already counts them rather
#: than re-typed.  Two copies of this tuple could disagree about what "all four" means.
try:  # pragma: no cover - exercised both ways only by deleting the mocks package
    from recon.mocks.coverage import ARCHETYPES as _ARCHETYPES
except ImportError:  # pragma: no cover - the data layer is a separate deliverable (M6)
    _ARCHETYPES: tuple[str, ...] = ()

#: A row of a ``MOCK_FIELDS.md`` provenance table: ``| field | TIER | evidence | note |``.
#:
#: Deliberately a second, independent copy of the pattern in ``tests/test_vendor_evidence.py``
#: rather than an import from the test suite.  Production code importing a test is backwards,
#: and the repository's own argument for duplicating a declaration applies here: two readers
#: of one table can disagree, and a disagreement is a signal.
_FIELD_ROW = re.compile(
    r"^\|\s*`?(?P<field>[A-Za-z0-9_.\[\]-]+)`?\s*\|\s*(?P<tier>[A-Z]+)\s*\|\s*(?P<evidence>[^|]*)\|",
    re.MULTILINE,
)

#: Anything shaped like an evidence id: an upper-case stem, a hyphen, more upper-case.
#:
#: Deliberately **not** the alternation of vendor prefixes that
#: ``tests/test_vendor_evidence.py`` uses.  A vendor name in this module would be a cell keyed
#: on a vendor, which is the one thing requirement F3's acceptance forbids — and it would also
#: silently stop finding a fifth vendor's citations on the day one existed.  The loose shape
#: over-matches on purpose; every match is then intersected with the ids the evidence index
#: actually holds, so a citation reaches the report only by being real.
_EVIDENCE_ID = re.compile(r"\b[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+\b")

#: The three provenance tiers requirement V3 defines, ordered strongest-evidence first.
_TIERS = ("SPEC", "STANDARD", "INVENTED")

#: Evidence statuses.  ``UNAVAILABLE`` is the one that makes a blocked item; the word
#: ``UNKNOWN`` in an ``establishes`` note is the other (``VERITY-006`` is RETRIEVED and still
#: records a specification that exists and is not published).
_UNAVAILABLE = "UNAVAILABLE"
_UNKNOWN = "UNKNOWN"

#: Hosts that mean "this machine".  Used to tell a mock endpoint from a vendor one.
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "[::1]"})


def _repo_root() -> Path:
    """The checkout root, found from this file rather than from the process's cwd.

    ``src/recon/connectors/readiness.py`` -> four parents up.  A cwd-relative root would make
    the report depend on where ``pytest`` was invoked from, which is the kind of dependency
    that produces two different documents from one repository.
    """
    return Path(__file__).resolve().parents[3]


# ═══ vocabularies ═══════════════════════════════════════════════════════════


class Stage(StrEnum):
    """How far one source has got, on Doc 2's own ladder.

    Four members and not five: there is no "in progress".  A rung is either reached or it is
    not, and a report that could say "mostly" is a report that would always say "mostly".
    """

    #: Declared in the registry and nothing more.
    DECLARED = "DECLARED"
    #: Doc 2 steps 1-5, against a mock.  The ceiling for this build.
    CONNECTOR_READY = "CONNECTOR_READY"
    #: Doc 2's own bar — *authorized* source data from the vendor.  Needs a credential.
    WORKING_CONNECTION = "WORKING_CONNECTION"
    #: Doc 2 step 6.  Explicitly out of scope; see the module docstring.
    PRODUCTION_READY = "PRODUCTION_READY"


class Reach(StrEnum):
    """What the configured endpoint actually points at.

    The distinction the whole report turns on.  ``LOCAL_MOCK`` and ``VENDOR`` render very
    differently and must never be confused, which is why this is a closed vocabulary derived
    from the endpoint string rather than a boolean somebody sets.
    """

    #: No endpoint configured for this deployment.
    UNCONFIGURED = "UNCONFIGURED"
    #: A loopback URL or a path on this machine — one of ``src/recon/mocks/``.
    LOCAL_MOCK = "LOCAL_MOCK"
    #: A remote host.  Nothing in this build produces one; a deployment could.
    VENDOR = "VENDOR"


class Ingest(StrEnum):
    """How far a source's bytes get once a transport has fetched them.

    **This is not a rung of :class:`Stage` and must never be rendered as one.**  A stage says
    how much of Doc 2's connector method has been built for a source; this says what happens
    to the bytes themselves, and the two move independently — a source can be connector-ready
    and still have rows nothing adapts, which is a decision rather than a missing rung.

    It is also not :attr:`SourceReadiness.awaiting_access`.  Every member below is a statement
    about a file we can already read; whether anybody has issued us the credential to go and
    fetch one is the separate question, and collapsing the two is the specific confusion this
    vocabulary exists to end.
    """

    #: Nobody was asked.  :func:`build_report` was handed no adapter layer, so the answer is
    #: absent rather than negative — the same three-valued discipline
    #: :attr:`ControlTotalReadiness.clean` follows, and for the same reason: "not measured" and
    #: "no" are different answers and only one of them is a finding.
    UNMEASURED = "UNMEASURED"
    #: No transport implements this source's kind, so there are no bytes to talk about.
    UNREADABLE = "UNREADABLE"
    #: Fetched, split and contract-checked — and no adapter takes the rows.  Reported with the
    #: refusal in :attr:`AdapterReadiness.refusal`, because "we cannot read this" and "we read
    #: this and deliberately stop" are opposite findings that look identical in a boolean.
    READS = "READS"
    #: Fetched, split, contract-checked, and an adapter turns the rows into canonical records.
    READS_AND_ADAPTS = "READS_AND_ADAPTS"


# ═══ the cells ══════════════════════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class TransportReadiness:
    """Whether a class exists that can move this source's bytes.

    ``implementations`` is every class found in ``recon.connectors`` whose ``kind`` matches
    the registry row's :attr:`~recon.connectors.registry.Source.transport_kind`.  Discovered,
    not listed: delete the class and this is empty on the next run.
    """

    kind: str
    implementations: tuple[str, ...]
    bound: str | None

    @property
    def implemented(self) -> bool:
        return bool(self.implementations)


@dataclass(frozen=True, slots=True)
class AuthReadiness:
    """Whether this source can authenticate, and whether it currently could.

    Three facts that are routinely collapsed into one and must not be:

    * :attr:`required` — the registry row names a credential at all.
    * :attr:`implemented` — the transport that serves this source calls a resolver in
      ``connectors.credentials`` for a shape that module actually builds.  Derived by reading
      the transport module's AST, so it is a fact about the code and not about a table.
    * :attr:`configured` — that credential resolves *right now*, in this environment.  It is
      false for every vendor source in this build, and that is Doc 2's Week 1-3 access gate
      rather than a defect.
    """

    required: bool
    kind: str | None
    resolver: str | None
    configured: bool
    env_vars: tuple[str, ...]
    detail: str | None = None

    @property
    def implemented(self) -> bool:
        return self.resolver is not None


@dataclass(frozen=True, slots=True)
class SchemaReadiness:
    """Whether a versioned field contract is registered for this source (requirement B1).

    ``registered`` false is not automatically a gap.  An unregistered source validates
    vacuously and that is load-bearing for the six generated feeds — see
    ``schema_registry``'s own module docstring.  :attr:`SourceReadiness.is_vendor` is what
    tells the two cases apart.
    """

    registered: bool
    current_version: str | None
    versions: tuple[str, ...]
    field_count: int | None
    required_field_count: int | None
    #: The registry row's ``mapping_version``.  Requirement B2 asks the contract to be
    #: versioned *with* the mapping, so a disagreement here is a real finding.
    mapping_version: str | None = None

    @property
    def version_matches_mapping(self) -> bool | None:
        if not self.registered or self.mapping_version is None:
            return None
        return self.current_version == self.mapping_version


@dataclass(frozen=True, slots=True)
class MappingReadiness:
    """Whether a business-mapping module exists for this source (Doc 2 step 4)."""

    module: str | None
    #: True when the module declares this exact source id, false when it only covers the
    #: vendor.  Beacon's mapping is keyed by payload kind rather than by source id, so the
    #: weaker answer is the honest one there.
    per_source: bool = False
    #: The vendor's own name for this dataset, read off the mapping object.  Carried because a
    #: source id is ours and a dataset name is theirs, and a reader comparing this report
    #: against a vendor's file listing needs the name the vendor uses.
    dataset: str | None = None
    #: The column the mapping takes each row's arrival time from, or ``None``.
    #:
    #: The field the whole vendor leg was once blocked on, so the report says it out loud.  A
    #: vendor export carries no column called ``received_at``; each dataset names its own, and
    #: some name none.  ``None`` here is a real answer and not a gap — see :attr:`arrival_time`.
    received_at_column: str | None = None

    @property
    def implemented(self) -> bool:
        return self.module is not None

    @property
    def arrival_time(self) -> str | None:
        """How rows of this dataset get the timestamp the pipeline orders them by.

        ``None`` when no mapping declares this source id, because then there is no dataset to
        answer for.  Otherwise one of two sentences, and the difference between them matters
        to anybody reading a timeline: a per-row stamp says when the row became true, and an
        inherited one says only when the file landed.  Reporting the second as though it were
        the first would hide every day of vendor lag behind a fill date.
        """
        if not self.per_source:
            return None
        if self.received_at_column is None:
            return (
                "inherited from the delivery's own fetch stamp — this dataset publishes no "
                "arrival column, so we know when the file landed and not when the row did"
            )
        return f"read per row from {self.received_at_column}"


@dataclass(frozen=True, slots=True)
class AdapterCoverage:
    """What the ingest layer will turn into canonical records, as two sets of names.

    Handed to :func:`build_report` by a caller that owns both layers, never imported here.
    Both members are *names* and neither is a callable: this report describes what exists, and
    holding a function it could call would make it one step from becoming an ingest run.

    **Neither member is ever empty.**  :func:`adapter_coverage` raises rather than build one
    that is, because an empty set here is read downstream as a denial — "no adapter is
    registered", "this dataset is not accepted" — and a denial produced by a failed lookup is
    indistinguishable, in the rendered document, from one that is true.
    """

    #: Source systems an adapter is registered for, stringified.
    source_systems: frozenset[str]
    #: Source ids the vendor door accepts by name, for the datasets it checks individually.
    datasets: frozenset[str]


@dataclass(frozen=True, slots=True)
class AdapterReadiness:
    """Whether anything turns this source's rows into canonical records, and if not, why not.

    Three-valued exactly like :attr:`ControlTotalReadiness.clean`, and for the same reason:
    ``None`` is "nobody handed this report an adapter layer to ask", which is not "no".  A
    report that answered "no" to an unasked question would mark every source in the build
    unadaptable and read as a catastrophe.

    :attr:`refusal` is the point of the class.  *Not adapted* covers two opposite findings —
    a reader nobody has written, and a reader deliberately withheld because nobody has decided
    what the rows mean — and a boolean cannot tell them apart.
    """

    adapts: bool | None = None
    refusal: str | None = None

    @property
    def measured(self) -> bool:
        return self.adapts is not None


@dataclass(frozen=True, slots=True)
class MockFidelity:
    """Real spec versus invented, tallied from the hand-written provenance tables.

    The counts come from ``src/recon/**/MOCK_FIELDS.md``, which requirement §4.5 requires to
    be hand-written and never generated.  That asymmetry is the point: a generated provenance
    table would derive its tiers from the code it audits and could only ever agree with
    itself, so the tiers are declared by hand and *counted* here.

    :attr:`table` is ``None`` when no provenance table covers this source, which is the
    correct answer for the six generated feeds — they are this repository's own synthetic
    data and were never a mock of anybody.
    """

    table: str | None
    dataset: str | None
    spec: int = 0
    standard: int = 0
    invented: int = 0
    evidence_ids: tuple[str, ...] = ()

    @property
    def rows(self) -> int:
        return self.spec + self.standard + self.invented

    @property
    def applies(self) -> bool:
        return self.table is not None


@dataclass(frozen=True, slots=True)
class GoldenClaimReadiness:
    """Requirement F1, per source: can this source's data exercise the four archetypes, and
    does a test actually follow one through.

    Two independent facts, because they fail independently.  ``archetype_rows`` comes from
    the data layer's own generated coverage artefact — a formatter whose population filter
    went dead still writes a file, and only a count catches it.  ``traced_by`` comes from
    reading the golden-claim test module's AST for functions that name this source.  A source
    with data and no trace is untested; a trace with no data is green and proves nothing.
    """

    #: archetype -> rows in the generated corpus, or empty when no coverage artefact exists.
    archetype_rows: tuple[tuple[str, int], ...] = ()
    #: rows this source's own mock dataset produced, or ``None`` when not measured.
    dataset_rows: int | None = None
    #: test functions that name this source id.
    traced_by: tuple[str, ...] = ()
    test_module: str | None = None

    @property
    def traced(self) -> bool:
        return bool(self.traced_by)

    @property
    def empty_archetypes(self) -> tuple[str, ...]:
        return tuple(name for name, count in self.archetype_rows if count == 0)


@dataclass(frozen=True, slots=True)
class ControlTotalReadiness:
    """Requirement F2, per source: declared versus ingested, as the ``control_total`` table
    recorded it.

    :attr:`storage_declared` is read out of ``schema.sql`` and is true even with no rows —
    "the machinery exists and has never run" and "the machinery does not exist" are different
    answers and a reader needs to be told which.  :attr:`checked` is ``None`` when no database
    was handed to the report, which is the normal case and not a failure.
    """

    storage_declared: bool
    checked: int | None = None
    reconciled: int | None = None
    unreconciled: int | None = None

    @property
    def clean(self) -> bool | None:
        if self.unreconciled is None:
            return None
        return self.unreconciled == 0


@dataclass(frozen=True, slots=True)
class BlockedItem:
    """One thing the vendor has to give us before this source can go further.

    Never asserted.  Either it carries an :attr:`evidence_id` that is a row in
    ``docs/vendor_evidence/index.jsonl`` — a real URL, a real retrieval date, a real recorded
    failure — or it is an access-gate item whose remedy is the concrete list of environment
    variables in :attr:`env_vars`.  "Blocked" with neither is an opinion, and this report does
    not hold opinions.
    """

    #: ``DOCUMENTATION`` (a source that exists and we could not read) or ``ACCESS`` (a
    #: credential or an entitlement nobody has issued).
    kind: str
    summary: str
    evidence_id: str | None = None
    url: str | None = None
    retrieved: str | None = None
    detail: str | None = None
    env_vars: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SourceReadiness:
    """One source's row of Doc 2's Week 3 gate evidence table.  Every field derived."""

    source_id: str
    vendor: str
    enabled: bool
    reach: Reach
    endpoint: str
    #: How the registry row says its documents are split into records, stringified.  A property
    #: of the source and never inferred from a filename, which is why it can be reported at all.
    payload_format: str
    mock_modules: tuple[str, ...]
    transport: TransportReadiness
    auth: AuthReadiness
    schema: SchemaReadiness
    mapping: MappingReadiness
    adapter: AdapterReadiness
    fidelity: MockFidelity
    golden: GoldenClaimReadiness
    control_totals: ControlTotalReadiness
    blocked: tuple[BlockedItem, ...]

    @property
    def is_vendor(self) -> bool:
        """Whether this source represents somebody else's system.

        Three structural signals, any one of which settles it, and not a list of vendor names
        — so a seventh vendor is classified correctly on the day its registry row lands rather
        than on the day somebody remembers to add it here.

        **The credential is the signal that matters and it is first for a reason.**  A source
        that has to authenticate is by definition reaching something we do not own; a source
        reading a local directory is not.  Classifying on the mapping module alone would call
        a brand-new registry row "not a vendor" purely because its mapping had not been
        written yet — which is the exact moment the row most needs to be reported as an
        incomplete vendor rather than as a complete feed.
        """
        return self.auth.required or self.mapping.implemented or self.fidelity.applies

    @property
    def talks_to_a_vendor(self) -> bool:
        """The question the whole document exists to answer honestly."""
        return self.reach is Reach.VENDOR and self.auth.configured

    @property
    def ingest(self) -> Ingest:
        """How far this source's bytes get once they have been fetched.

        Reads the transport cell first, because "no transport implements this kind" makes
        every question after it moot — there are no bytes to split, map or adapt.  After that
        the answer is the adapter's, three-valued, with ``None`` staying ``None``.
        """
        if not self.transport.implemented:
            return Ingest.UNREADABLE
        if self.adapter.adapts is None:
            return Ingest.UNMEASURED
        return Ingest.READS_AND_ADAPTS if self.adapter.adapts else Ingest.READS

    @property
    def awaiting_access(self) -> bool:
        """Whether this row is switched off pending something somebody else has to issue.

        **Deliberately independent of :attr:`ingest`, and that independence is the finding.**
        A vendor row here is switched off because Doc 2's Week 1-3 access gate has not cleared
        — no credential, no scheduled export, no authorised covered entity — and for no other
        reason.  It is not switched off because the file cannot be read; :attr:`ingest` says
        whether it can, separately, and for the vendor datasets it says yes.

        Kept as its own property rather than folded into a status string so that no caller can
        render the two as alternatives.  They are simultaneously true of the same source.
        :attr:`blocked` carries the evidence for the ``ACCESS`` items behind it.
        """
        return not self.enabled

    @property
    def gaps(self) -> tuple[str, ...]:
        """Everything short of complete that is *our* work rather than the vendor's.

        Kept separate from :attr:`blocked` and from :attr:`stage`, because the three answer
        different questions and collapsing them loses the one that matters.  A blocked item
        needs a vendor to act.  A gap needs us to act.  A stage says which of Doc 2's rungs
        has been reached, and a rung is not un-reached by an outstanding gap — otherwise one
        missing trace would demote an otherwise-complete connector and the ladder would stop
        meaning anything.
        """
        found: list[str] = []
        if not self.transport.implemented:
            found.append(f"no transport implements {self.transport.kind}")
        if self.auth.required and not self.auth.implemented:
            found.append("no transport resolves a credential for this source")
        if self.is_vendor and not self.schema.registered:
            found.append("no versioned field contract is registered (requirement B1)")
        if self.schema.version_matches_mapping is False:
            found.append(
                "the registered contract version disagrees with the row's mapping version "
                "(requirement B2)"
            )
        if self.is_vendor and not self.mapping.implemented:
            found.append("no business-mapping module (Doc 2 step 4)")
        if not self.golden.traced:
            found.append("no end-to-end golden-claim trace names this source (requirement F1)")
        if self.control_totals.clean is False:
            found.append(
                f"{self.control_totals.unreconciled} control total(s) did not reconcile "
                "(requirement F2)"
            )
        if self.fidelity.applies and self.fidelity.spec == 0 and self.fidelity.rows:
            found.append(
                "every field of this mock is INVENTED or STANDARD; no vendor source names one"
            )
        return tuple(found)

    @property
    def stage(self) -> Stage:
        """The highest rung this source has actually reached.

        The machinery test is transport, auth and mapping — *how the bytes move, how we
        authenticate, and what the bytes mean*, which is Doc 2 steps 3 and 4.  A missing
        schema contract is deliberately **not** demoting: ``schema_registry``'s own docstring
        establishes that an unregistered source validates vacuously by design, so treating an
        absent contract as "not built" would misreport the six generated feeds and would also
        misreport Beacon, whose connector is built end to end and whose payloads are JSON with
        no registered contract.  It is reported instead as a gap, where a reader can see it.

        :attr:`Stage.PRODUCTION_READY` is unreachable by construction: Doc 2 step 6 is out of
        scope, so nothing here can compute it and nothing here should pretend to.
        """
        machinery = self.transport.implemented and (
            self.auth.implemented or not self.auth.required
        )
        if self.is_vendor:
            machinery = machinery and self.mapping.implemented
        if not machinery:
            return Stage.DECLARED
        if self.talks_to_a_vendor:
            return Stage.WORKING_CONNECTION
        return Stage.CONNECTOR_READY


@dataclass(frozen=True, slots=True)
class ReadinessReport:
    """Every source, plus the repository-level facts a reader needs to interpret them."""

    sources: tuple[SourceReadiness, ...]
    evidence_entries: int
    unavailable_evidence: tuple[str, ...]
    #: Doc 2 step 5's four archetypes, each with the F1 test functions that trace it.
    #:
    #: Repository-level rather than per source, because an archetype is a property of a claim
    #: and a claim crosses four feeds on its way to a verdict.  Attributing "paid" to one
    #: source would be attributing the trace to whichever feed happened to be asserted first.
    archetype_traces: tuple[tuple[str, tuple[str, ...]], ...] = ()
    golden_test_module: str | None = None
    generated_at: str = GENERATED_AT
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def untraced_archetypes(self) -> tuple[str, ...]:
        return tuple(name for name, functions in self.archetype_traces if not functions)

    def by_id(self, source_id: str) -> SourceReadiness:
        for row in self.sources:
            if row.source_id == source_id:
                return row
        known = ", ".join(row.source_id for row in self.sources)
        raise KeyError(f"no source {source_id!r} in this report; it covers: {known}")

    @property
    def source_ids(self) -> tuple[str, ...]:
        return tuple(row.source_id for row in self.sources)

    @property
    def live_vendor_connections(self) -> tuple[str, ...]:
        """Sources that reach a real vendor with a resolved credential.

        Expected to be empty, and a test asserts it.  A report that grew a non-empty tuple
        here without anybody noticing would be the failure this property exists to catch.
        """
        return tuple(row.source_id for row in self.sources if row.talks_to_a_vendor)

    @property
    def blocked_evidence_ids(self) -> tuple[str, ...]:
        seen: list[str] = []
        for row in self.sources:
            for item in row.blocked:
                if item.evidence_id is not None and item.evidence_id not in seen:
                    seen.append(item.evidence_id)
        return tuple(seen)


# ═══ discovery: what is implemented ═════════════════════════════════════════


def _submodules(package_name: str) -> Iterator[ModuleType]:
    """Every importable module directly inside a package, in a stable order.

    Subpackages are skipped: a subpackage's own modules are walked separately when they are
    wanted, so that importing ``connectors`` to find transports does not drag in every vendor
    mapping as a side effect.
    """
    try:
        package = importlib.import_module(package_name)
    except ImportError:  # pragma: no cover - a deleted package is a valid answer
        return
    for info in sorted(pkgutil.iter_modules(package.__path__), key=lambda item: item.name):
        if info.ispkg:
            continue
        try:
            yield importlib.import_module(f"{package_name}.{info.name}")
        except ImportError:  # pragma: no cover - reported as "not implemented", not raised
            continue


def transport_implementations() -> dict[str, tuple[str, ...]]:
    """Transport kind -> the dotted paths of the classes that implement it.

    Discovered by walking ``recon.connectors`` for classes that satisfy the
    :class:`~recon.connectors.transport.Transport` protocol *structurally* — a string
    ``kind`` and a callable ``fetch``.  Checked with ``hasattr`` rather than ``isinstance``
    against the protocol, because these are classes and not instances, and a data protocol's
    ``isinstance`` on a class object answers a subtly different question than the one being
    asked.

    The protocol class itself is excluded automatically: it annotates ``kind`` without
    assigning it, so there is nothing to read.
    """
    found: dict[str, list[str]] = {}
    for module in _submodules(_CONNECTORS_PACKAGE):
        for name, obj in sorted(vars(module).items()):
            if not inspect.isclass(obj) or obj.__module__ != module.__name__:
                continue
            kind = getattr(obj, "kind", None)
            if not isinstance(kind, str) or not callable(getattr(obj, "fetch", None)):
                continue
            found.setdefault(kind, []).append(f"{module.__name__}.{name}")
    return {kind: tuple(sorted(paths)) for kind, paths in sorted(found.items())}


def _module_source(module: ModuleType) -> str:
    path = inspect.getsourcefile(module)
    if path is None:  # pragma: no cover - only for builtins, which are never transports
        return ""
    return Path(path).read_text(encoding="utf-8")


def _resolver_calls(module: ModuleType) -> tuple[str, ...]:
    """Names like ``resolve_ssh_key`` that this module calls on ``connectors.credentials``.

    Read out of the AST rather than out of a table mapping transport kind to credential
    shape, and that difference is the requirement.  A table would say what we believe the
    SFTP transport authenticates with; this says what it *does*.  Stop calling
    ``resolve_ssh_key`` in ``sftp.py`` and the auth cell changes without anybody editing
    this file.
    """
    source = _module_source(module)
    if not source:  # pragma: no cover - see _module_source
        return ()
    names: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        if isinstance(name, str) and name.startswith("resolve_") and name not in names:
            names.append(name)
    return tuple(sorted(names))


def _credential_kind_for(resolver: str) -> str | None:
    """``resolve_ssh_key`` -> ``SSH_KEY``, by matching the suffix against the enum.

    Derived from :class:`~recon.connectors.credentials.CredentialKind`'s own members, so a
    third shape added there is recognised here the moment a transport calls its resolver.
    """
    for kind in credentials.CredentialKind:
        if resolver == f"resolve_{kind.value.lower()}":
            return kind.value
    return None


def _self_describing_tables(module: ModuleType) -> Iterator[Mapping[str, Any]]:
    """Every module-level mapping whose values each carry a ``source_id`` equal to their key.

    The shape :func:`~recon.connectors.vendors.mappings_by_source_id` builds.  Requiring the
    values to agree with the keys rather than trusting any dict of strings is what keeps
    ``__builtins__`` and every other incidental mapping out of the answer — and it is why a
    vendor module can grow a third dataset without anything here being told.

    Factored out because two callers now need it and they must not be able to disagree about
    what counts as a mapping table: :func:`vendor_mappings` wants the keys, and
    :func:`source_mappings` wants the objects behind them.
    """
    for name, value in sorted(vars(module).items()):
        if name.startswith("_") or not isinstance(value, Mapping) or not value:
            continue
        if all(
            isinstance(key, str) and getattr(entry, "source_id", None) == key
            for key, entry in value.items()
        ):
            yield value


def source_mappings() -> dict[str, Any]:
    """Source id -> the mapping object declared for it, for the sources that have one.

    The same walk :func:`vendor_mappings` does, keeping the objects instead of counting their
    keys, because two cells now want to read a *declaration* off the mapping rather than merely
    know one exists: the vendor's own name for the dataset, and which of its columns carries a
    row's arrival time.

    Deliberately typed loosely and read with ``getattr``.  This module has no business knowing
    the mapping class, and a report that imported it would stop working the day a vendor needed
    a different one — which is precisely the day the report is worth reading.
    """
    found: dict[str, Any] = {}
    for module in _submodules(_VENDORS_PACKAGE):
        vendor = getattr(module, "VENDOR", None)
        if not isinstance(vendor, str) or not vendor:
            continue
        for table in _self_describing_tables(module):
            for key, entry in table.items():
                found.setdefault(key, entry)
    return dict(sorted(found.items()))


def adapter_coverage(adapters: object) -> AdapterCoverage:
    """What the ingest layer accepts, found on the object a caller hands in.

    **Found by shape, never by name.**  The dispatch table is "a non-empty mapping whose every
    value is callable"; a per-dataset allow list is "a non-empty set of strings".  That is the
    same discovery discipline the rest of this module uses, and the reason for it here is
    concrete: those tables are private to the layer that owns them, they are free to be renamed
    tomorrow, and a report keyed on today's spelling would answer a renamed table with silence.
    A shape survives a rename; a name does not.

    Nothing found is ever called.  Both members of :class:`AdapterCoverage` are sets of names,
    so this stays a description of what exists rather than becoming one step from running an
    ingest — which is the line the module docstring draws and this function sits closest to.

    ═══ Why a probe, when a probe is brittle ═══

    A shape survives a rename but it does not survive a reshape, and that is a real hazard
    rather than a theoretical one.  Change the allow list from ``frozenset({...})`` to a tuple
    in the layer that owns it and nothing over there notices: every ``in`` test still works and
    every test over there still passes.  Over here the probe stops matching and the allow list
    comes back empty.  Both alternatives to the probe were weighed and both are worse.

    *Reading the table by its name* trades one silent failure for another.  The name is private
    to the other layer and is no more stable than the shape is; a rename produces exactly the
    same empty answer, and it puts a decision that belongs to the ingest layer — what its own
    tables are called — into a cell of this report.

    *Widening the probe to any collection of strings* — tuple, list, set — would survive the
    reshape, and would pay for it by absorbing every other module-level list of strings the
    adapter layer happens to hold.  A stray name in :attr:`AdapterCoverage.datasets` reads as
    "this dataset is adapted", which is the direction of error this whole document exists to
    prevent: a report that flatters the build.  A narrow probe can only ever find too little.

    So the probe stays narrow and what is fixed is the **silence**.  Both guards below raise, so
    there are two outcomes and not three: either both tables are found, or no report is produced
    at all.  There is no path on which this quietly returns a smaller answer than the truth.

    The durable fix is on the far side of the seam and is not this module's to make.  An adapter
    layer that published its own coverage — a documented attribute or function on its public
    surface — would let this read a declaration instead of probing for one.  Until one exists,
    the pair of guards is what stands in for it.

    Raises:
        ValueError: nothing on the object looks like a dispatch table, or nothing on it looks
            like a per-dataset allow list.  Loud on purpose in both cases: an absent table and
            an empty one are indistinguishable from out here and have opposite answers, and
            every quiet answer available is a wrong one that reads as a finding.
    """
    systems: set[str] = set()
    datasets: set[str] = set()
    for name, value in sorted(vars(adapters).items()):
        if name.startswith("__"):
            continue
        if isinstance(value, Mapping) and value and all(callable(item) for item in value.values()):
            systems.update(str(key) for key in value)
        elif (
            isinstance(value, (set, frozenset))
            and value
            and all(isinstance(item, str) for item in value)
        ):
            datasets.update(value)
    if not systems:
        raise ValueError(
            f"{getattr(adapters, '__name__', adapters)!r} carries nothing shaped like an "
            "adapter dispatch table — no non-empty mapping whose every value is callable. "
            "Answering 'nothing adapts' here would mark every source unadaptable and read as "
            "a catastrophe, so this raises rather than guessing."
        )
    if not datasets:
        # The same guard as the one above, for the same reason, and it is here because its
        # absence was the more dangerous of the two.  A missing dispatch table denies every
        # source at once, which is loud enough that a reader disbelieves the whole page.  A
        # missing allow list denies only the sources whose mapping names them individually —
        # which is to say only the vendor datasets that actually have a reader — and it denies
        # them in the same words the report uses for a dataset that genuinely has none.  That
        # is a lie a reader has no way to catch, on the one page whose entire purpose is to be
        # trustworthy about what is built.
        raise ValueError(
            f"{getattr(adapters, '__name__', adapters)!r} carries nothing shaped like a "
            "per-dataset allow list — no non-empty set of strings. An empty answer is not a "
            "safe default here: every source whose mapping declares it by name would come "
            "back not adapted, so this report would deny a connector leg that works, in the "
            "same words it uses for one that has no reader at all. From out here a table that "
            "does not exist and a table this probe failed to match look identical and mean "
            "opposite things, so this raises rather than picking one."
        )
    return AdapterCoverage(source_systems=frozenset(systems), datasets=frozenset(datasets))


def vendor_mappings() -> dict[str, tuple[str, tuple[str, ...]]]:
    """Vendor -> (mapping module path, the source ids it declares).

    Every module in ``recon.connectors.vendors`` that names a ``VENDOR`` is a mapping module;
    the source ids come from its self-describing tables (:func:`_self_describing_tables`).

    Beacon's module has no such table — its payloads are keyed by kind rather than by source
    — so it contributes a vendor with an empty id tuple, which is what
    :attr:`MappingReadiness.per_source` reports as the weaker, truer answer.
    """
    found: dict[str, tuple[str, tuple[str, ...]]] = {}
    for module in _submodules(_VENDORS_PACKAGE):
        vendor = getattr(module, "VENDOR", None)
        if not isinstance(vendor, str) or not vendor:
            continue
        ids: list[str] = []
        for table in _self_describing_tables(module):
            ids.extend(key for key in table if key not in ids)
        found[vendor] = (module.__name__, tuple(sorted(ids)))
    return found


def _mock_modules() -> dict[str, tuple[str, ...]]:
    """Vendor -> the ``recon.mocks`` modules that stand in for it.

    Matched on the module name's leading segment, because that is the convention the package
    already follows and a convention can be checked; a list would have to be maintained.  The
    modules are found without being imported — presence is the whole question.
    """
    found: dict[str, list[str]] = {}
    try:
        package = importlib.import_module(_MOCKS_PACKAGE)
    except ImportError:  # pragma: no cover - the data layer is a separate deliverable
        return {}
    for info in sorted(pkgutil.iter_modules(package.__path__), key=lambda item: item.name):
        vendor = info.name.split("_", 1)[0]
        found.setdefault(vendor, []).append(f"{_MOCKS_PACKAGE}.{info.name}")
    return {vendor: tuple(names) for vendor, names in found.items()}


# ═══ discovery: provenance and evidence ═════════════════════════════════════


def _singular(name: str) -> str:
    """``acknowledgements`` -> ``acknowledgement``.

    One rule, applied to *both* sides of every comparison, which is what makes it safe.  The
    registry spells a source id in the plural (``beacon_payment_references``) and the
    provenance table spells the dataset in the singular (``beacon.payment_reference``); a
    lookup table between them would be exactly the hand-maintained cell F3 forbids, and it
    would be wrong the first time a sixth payload kind landed.  Normalising both sides means
    ``rebate_status`` matches ``rebate_status`` too, because the rule is applied to each.
    """
    return name[:-1] if name.endswith("s") else name


@dataclass(frozen=True, slots=True)
class _Tally:
    table: str
    #: The dataset exactly as the provenance table spells it — ``rebate_status``, not the
    #: singularised ``rebate_statu`` this tally is *keyed* by.  The key exists to join a
    #: registry row to a table; the name exists to be read, and a report that printed the
    #: lookup key would be printing a word that appears in no file.
    dataset: str
    counts: dict[str, int]
    evidence_ids: tuple[str, ...]


def mock_field_tallies(repo_root: Path | None = None) -> dict[tuple[str, str], _Tally]:
    """``(vendor, dataset)`` -> the tier counts declared for it in a ``MOCK_FIELDS.md``.

    Only rows whose field name is dotted and vendor-qualified — ``verity.accumulations.ndc_11``
    — are attributed to a dataset.  The mapping side's table lists bare canonical names
    (``ndc11``), because a canonical name belongs to this fabric and to no vendor's dataset;
    those rows are tallied under the empty dataset ``""`` for their directory, so they are
    counted somewhere rather than silently dropped.
    """
    root = _repo_root() if repo_root is None else repo_root
    tallies: dict[tuple[str, str], tuple[str, str, dict[str, int], list[str]]] = {}
    for path in sorted(root.glob(_PROVENANCE_GLOB)):
        table = path.relative_to(root).as_posix()
        for match in _FIELD_ROW.finditer(path.read_text(encoding="utf-8")):
            tier = match.group("tier")
            if tier not in _TIERS:  # the table's own header row, and nothing else
                continue
            parts = match.group("field").split(".")
            if len(parts) >= 3:
                key, spelled = (parts[0], _singular(parts[1])), parts[1]
            else:
                key, spelled = (path.parent.name, ""), ""
            entry = tallies.setdefault(
                key, (table, spelled, {name: 0 for name in _TIERS}, [])
            )
            entry[2][tier] += 1
            for identifier in _EVIDENCE_ID.findall(match.group("evidence")):
                if identifier not in entry[3]:
                    entry[3].append(identifier)
    return {
        key: _Tally(
            table=table,
            dataset=spelled,
            counts=dict(counts),
            evidence_ids=tuple(sorted(ids)),
        )
        for key, (table, spelled, counts, ids) in sorted(tallies.items())
    }


def evidence_index(repo_root: Path | None = None) -> tuple[dict, ...]:
    """``docs/vendor_evidence/index.jsonl``, read directly.

    The live artefact and never a copy, for the reason ``tests/test_vendor_evidence.py``
    gives about the same file: a frozen copy is a claim about evidence rather than evidence.
    An absent index yields an empty tuple, and every blocked list then comes back empty —
    which is visibly wrong rather than quietly reassuring.
    """
    root = _repo_root() if repo_root is None else repo_root
    path = root / _EVIDENCE_INDEX
    if not path.exists():  # pragma: no cover - the index is a hard prerequisite (V1)
        return ()
    return tuple(
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )


def _coverage_report(vendor_dir: Path | None) -> dict:
    """The data layer's generated ``coverage.json``, or an empty mapping.

    Read rather than recomputed.  Recomputing it would mean loading the generated corpus and
    rendering every vendor file, which turns a readiness report into a build step — and the
    artefact on disk is what requirement M5's test already asserts against, so reading it
    keeps one number rather than making a second one free to disagree.
    """
    if vendor_dir is None:
        return {}
    try:
        from recon.mocks.coverage import COVERAGE_FILE
    except ImportError:  # pragma: no cover - the data layer is a separate deliverable
        return {}
    path = vendor_dir / COVERAGE_FILE
    if not path.exists():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):  # pragma: no cover - reported as "not measured"
        return {}
    return loaded if isinstance(loaded, dict) else {}


@dataclass(frozen=True, slots=True)
class _GoldenTraces:
    """What the F1 trace module contains, read off its AST."""

    module: str | None
    #: string literal -> the ``test_*`` functions that mention it.
    by_literal: dict[str, tuple[str, ...]]
    functions: tuple[str, ...]

    def naming(self, *candidates: str) -> tuple[str, ...]:
        found: list[str] = []
        for candidate in candidates:
            for name in self.by_literal.get(candidate, ()):
                if name not in found:
                    found.append(name)
        return tuple(sorted(found))


def _golden_traces(repo_root: Path) -> _GoldenTraces:
    """The golden-claim test module, and which test functions mention which strings.

    Walks the module's AST for ``test_*`` functions and records, per function, the string
    literals it mentions.  Attribution then happens against facts already on the registry row
    — the source id and the source's filenames — because that is how an F1 trace genuinely
    cites its origin: ``assert raw["source_file"] == "pbm_claim_events.jsonl"`` names the
    source by the document it arrived in, and a rule that only looked for the source id would
    report a fully traced feed as untraced.

    No module means no traces, reported as such rather than guessed at.
    """
    tests_dir = repo_root / "tests"
    if not tests_dir.exists():  # pragma: no cover - only in a stripped checkout
        return _GoldenTraces(None, {}, ())
    candidates = sorted(tests_dir.glob(_GOLDEN_TEST_GLOB))
    if not candidates:
        return _GoldenTraces(None, {}, ())
    module_path = candidates[0]
    try:
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):  # pragma: no cover - a broken test is "not traced"
        return _GoldenTraces(module_path.name, {}, ())

    by_literal: dict[str, list[str]] = {}
    functions: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not node.name.startswith("test_"):
            continue
        functions.append(node.name)
        literals = {
            child.value
            for child in ast.walk(node)
            if isinstance(child, ast.Constant) and isinstance(child.value, str)
        }
        for literal in literals:
            by_literal.setdefault(literal, []).append(node.name)
    return _GoldenTraces(
        module=module_path.name,
        by_literal={key: tuple(sorted(set(value))) for key, value in by_literal.items()},
        functions=tuple(sorted(functions)),
    )


def _control_total_rows(conn: sqlite3.Connection | None) -> dict[str, tuple[int, int]]:
    """source_id -> (rows, unreconciled rows), or an empty mapping.

    Queried defensively on purpose.  The table is another wave's deliverable and may not
    exist, the database may never have been built, and a readiness report that raised in
    either case would be unusable exactly when it is most wanted — before the first
    successful run.  Nothing here imports the module that writes these rows; the table is the
    interface, which is what makes this a reconciliation rather than a restatement.
    """
    if conn is None:
        return {}
    try:
        cursor = conn.execute(
            "SELECT source_id, COUNT(*), "
            "SUM(CASE WHEN reconciled = 0 THEN 1 ELSE 0 END) "
            "FROM control_total GROUP BY source_id"
        )
        rows = cursor.fetchall()
    except sqlite3.Error:
        return {}
    return {str(row[0]): (int(row[1]), int(row[2] or 0)) for row in rows}


def _control_total_storage(repo_root: Path) -> bool:
    """Whether ``schema.sql`` declares the ``control_total`` table.

    Read out of the schema text so that "the machinery exists and has never run" is
    distinguishable from "the machinery does not exist".  Those two look identical in a row
    count and mean opposite things to whoever reads this report.
    """
    path = repo_root / _SCHEMA_SQL
    if not path.exists():  # pragma: no cover - the schema is not optional
        return False
    return "CREATE TABLE control_total" in path.read_text(encoding="utf-8")


def _reach(endpoint: str) -> Reach:
    """What an endpoint string points at, decided from the string itself.

    A URL is classified by its host, a path by whether it exists on this machine.  Neither
    test names a vendor, which is the property that matters: a deployment that pointed a row
    at a real SFTP host would be classified :attr:`Reach.VENDOR` by this function without
    anybody telling it the host was Verity's.
    """
    if not endpoint or endpoint == UNCONFIGURED_ENDPOINT:
        return Reach.UNCONFIGURED
    split = urlsplit(endpoint)
    if split.scheme in {"http", "https", "sftp", "ftp", "ftps"}:
        host = (split.hostname or "").lower()
        return Reach.LOCAL_MOCK if host in _LOOPBACK_HOSTS else Reach.VENDOR
    try:
        return Reach.LOCAL_MOCK if Path(endpoint).exists() else Reach.UNCONFIGURED
    except OSError:  # pragma: no cover - an endpoint too strange to be a path
        return Reach.UNCONFIGURED


# ═══ the sources the report covers ══════════════════════════════════════════


def declared_sources(
    *,
    feeds_dir: Path | str | None = None,
    vendor_endpoints: Mapping[str, str] | None = None,
    beacon_endpoints: Mapping[str, str] | None = None,
    enabled: bool = False,
) -> tuple[Source, ...]:
    """Every source this fabric declares, whether or not a deployment has onboarded it.

    The lists come from the registry's own tables —
    :data:`~recon.connectors.registry.VENDOR_SOURCE_IDS`,
    :data:`~recon.connectors.registry.BEACON_SOURCE_IDS` and ``config.FEED_FILENAMES``
    through :func:`~recon.connectors.registry.local_sources` — so a row added there appears
    here, and in the report, with no edit anywhere in this module.  That property *is*
    requirement F3's acceptance test.

    The endpoint defaults to :data:`UNCONFIGURED_ENDPOINT` rather than to a plausible-looking
    host, because "we have not been told where this lives" and "we are pointed at the vendor"
    must not render the same way.  ``enabled`` defaults to ``False`` for the vendor rows for
    the reason :func:`~recon.connectors.registry.vendor_sources` gives: the Week 1-3 access
    gate has not cleared, and a row that declared itself ready would be asserting something
    nobody has been given.
    """
    rows: list[Source] = []
    if feeds_dir is not None:
        rows.extend(registry.local_sources(feeds_dir))
    rows.extend(
        registry.vendor_sources(
            dict(vendor_endpoints)
            if vendor_endpoints is not None
            else {source_id: UNCONFIGURED_ENDPOINT for source_id in registry.VENDOR_SOURCE_IDS},
            enabled=enabled,
        )
    )
    rows.extend(
        registry.beacon_sources(
            dict(beacon_endpoints)
            if beacon_endpoints is not None
            else {source_id: UNCONFIGURED_ENDPOINT for source_id in registry.BEACON_SOURCE_IDS},
            enabled=enabled,
        )
    )
    return tuple(rows)


# ═══ building one row ═══════════════════════════════════════════════════════


def _auth_for(
    source: Source,
    transports: Mapping[str, tuple[str, ...]],
    env: Mapping[str, str],
    secrets_file: Path | str | None,
) -> AuthReadiness:
    kind_value = str(source.transport_kind)
    if source.credential_ref is None:
        return AuthReadiness(
            required=False,
            kind=None,
            resolver=None,
            configured=False,
            env_vars=(),
            detail="the transport reads a local directory and authenticates to nothing",
        )

    resolver: str | None = None
    credential_kind: str | None = None
    for dotted in transports.get(kind_value, ()):
        module_name = dotted.rsplit(".", 1)[0]
        try:
            module = importlib.import_module(module_name)
        except ImportError:  # pragma: no cover - already imported by discovery
            continue
        for name in _resolver_calls(module):
            matched = _credential_kind_for(name)
            if matched is not None:
                resolver, credential_kind = name, matched
                break
        if resolver is not None:
            break

    env_vars = credentials.env_var_names(
        source.credential_ref,
        credentials.CredentialKind(credential_kind) if credential_kind else None,
    )
    configured = False
    detail: str | None = None
    try:
        credentials.resolve(
            source.credential_ref,
            kind=credential_kind,
            env=env,
            secrets_file=secrets_file,
        )
        configured = True
    except credentials.MissingCredentialError:
        detail = "no credential is configured in this environment"
    except credentials.CredentialError as exc:
        # Named and carried, never re-raised.  ``credentials`` guarantees its messages
        # describe a value's shape and never its content, which is what makes it safe to put
        # one in a generated document at all.
        detail = str(exc)

    return AuthReadiness(
        required=True,
        kind=credential_kind,
        resolver=f"{credentials.__name__}.{resolver}" if resolver else None,
        configured=configured,
        env_vars=env_vars,
        detail=detail,
    )


def _schema_for(source: Source, registry_: SchemaRegistry) -> SchemaReadiness:
    if source.source_id not in registry_.source_ids():
        return SchemaReadiness(
            registered=False,
            current_version=None,
            versions=(),
            field_count=None,
            required_field_count=None,
            mapping_version=source.mapping_version,
        )
    contract = registry_.get(source.source_id)
    fields = contract.fields if contract is not None else ()
    return SchemaReadiness(
        registered=True,
        current_version=registry_.current_version(source.source_id),
        versions=registry_.versions(source.source_id),
        field_count=len(fields),
        required_field_count=sum(1 for spec in fields if spec.required),
        mapping_version=source.mapping_version,
    )


def _fidelity_for(
    source: Source,
    tallies: Mapping[tuple[str, str], _Tally],
    known_evidence: frozenset[str],
) -> MockFidelity:
    dataset = _singular(source.source_id.removeprefix(f"{source.vendor}_"))
    tally = tallies.get((source.vendor, dataset))
    if tally is None:
        return MockFidelity(table=None, dataset=None)
    counts = tally.counts
    return MockFidelity(
        table=tally.table,
        dataset=f"{source.vendor}.{tally.dataset}",
        spec=counts.get("SPEC", 0),
        standard=counts.get("STANDARD", 0),
        invented=counts.get("INVENTED", 0),
        # Only ids the index actually holds.  The pattern that found them over-matches by
        # design (see :data:`_EVIDENCE_ID`), and an id this report cites that nothing backs
        # would be precisely the "invention wearing a citation" the evidence rules exist for.
        evidence_ids=tuple(
            identifier for identifier in tally.evidence_ids if identifier in known_evidence
        ),
    )


def _adapter_for(
    source: Source,
    coverage: AdapterCoverage | None,
    per_source: bool,
) -> AdapterReadiness:
    """Whether the ingest layer would turn this source's rows into records, and if not, why.

    One rule, applied to every source, written once.  It reads in two steps because the two
    refusals mean different things:

    * A source system nothing dispatches on has **no adapter at all**.  Its rows are fetched,
      split and contract-checked, and then stop — which is a true and useful thing to report,
      and a thing no cell on this report said until there was somewhere to say it.
    * A source whose *dataset* a mapping declares individually is checked by name a second
      time, because the door that serves it checks by name: one adapter can serve several of a
      vendor's datasets and decline one of them on the grounds that its rows describe something
      else.  That is a decision about meaning, not a reader nobody wrote, and the refusal says
      so rather than leaving a reader to assume the weaker explanation.

    ``per_source`` rather than the source id alone, so the second check only applies where a
    per-dataset declaration exists to be checked against.  A source with no mapping of its own
    is answered entirely by the first step, which is the honest answer for a generated feed.
    """
    if coverage is None:
        return AdapterReadiness()
    system = str(source.source_system)
    if system not in coverage.source_systems:
        return AdapterReadiness(
            adapts=False,
            refusal=(
                f"no adapter is registered for {system}: these rows are fetched, split and "
                "contract-checked, and they do not become canonical records"
            ),
        )
    if per_source and source.source_id not in coverage.datasets:
        return AdapterReadiness(
            adapts=False,
            refusal=(
                f"the adapter for {system} serves this vendor and does not accept this "
                "dataset. The file is readable and the rows are contract-checked; what is "
                "missing is a decision about what they mean, not a reader"
            ),
        )
    return AdapterReadiness(adapts=True)


def _golden_for(
    source: Source,
    coverage: Mapping,
    traces: _GoldenTraces,
) -> GoldenClaimReadiness:
    archetypes = coverage.get("archetypes")
    rows: tuple[tuple[str, int], ...] = ()
    if isinstance(archetypes, Mapping):
        order = _ARCHETYPES or tuple(sorted(archetypes))
        rows = tuple((name, int(archetypes.get(name, 0))) for name in order)

    dataset_rows: int | None = None
    vendors = coverage.get("vendors")
    if isinstance(vendors, Mapping):
        detail = vendors.get(source.vendor)
        if isinstance(detail, Mapping):
            record_types = detail.get("record_types")
            if isinstance(record_types, Mapping):
                wanted = _singular(source.source_id.removeprefix(f"{source.vendor}_"))
                for name, count in record_types.items():
                    if _singular(str(name)) == wanted:
                        dataset_rows = int(count)
                        break

    return GoldenClaimReadiness(
        archetype_rows=rows,
        dataset_rows=dataset_rows,
        traced_by=traces.naming(source.source_id, *source.filenames),
        test_module=traces.module,
    )


def _blocked_for(source: Source, evidence: Sequence[dict], auth: AuthReadiness) -> tuple[BlockedItem, ...]:
    items: list[BlockedItem] = []
    for row in evidence:
        if str(row.get("vendor", "")) != source.vendor:
            continue
        status = str(row.get("status", ""))
        establishes = str(row.get("establishes", ""))
        if status == _UNAVAILABLE:
            items.append(
                BlockedItem(
                    kind="DOCUMENTATION",
                    summary=establishes,
                    evidence_id=str(row.get("id")),
                    url=str(row.get("url")) or None,
                    retrieved=str(row.get("retrieved")) or None,
                    detail=str(row.get("failure", "")) or None,
                )
            )
        elif _UNKNOWN in establishes:
            # Retrieved, and still records a gap — ``VERITY-006`` names a data specification
            # that exists and is not published.  A source we *did* read can still be a source
            # that establishes nothing, and treating it as satisfied would overstate fidelity
            # exactly where this report must not.
            items.append(
                BlockedItem(
                    kind="DOCUMENTATION",
                    summary=establishes,
                    evidence_id=str(row.get("id")),
                    url=str(row.get("url")) or None,
                    retrieved=str(row.get("retrieved")) or None,
                )
            )

    if auth.required and not auth.configured:
        items.append(
            BlockedItem(
                kind="ACCESS",
                summary=(
                    "no credential has been issued for this source, so Doc 2's "
                    "working-connection bar — which begins with the word authorized — "
                    "cannot be reached"
                ),
                detail=auth.detail,
                env_vars=auth.env_vars,
            )
        )
    if not source.enabled:
        items.append(
            BlockedItem(
                kind="ACCESS",
                summary=(
                    "the registry row is switched off; it stays in the registry because "
                    "onboarded-and-disabled is a different fact from never-onboarded"
                ),
            )
        )
    return tuple(items)


def build_report(
    *,
    sources: Sequence[Source] | None = None,
    schema_registry: SchemaRegistry | None = None,
    repo_root: Path | str | None = None,
    env: Mapping[str, str] | None = None,
    secrets_file: Path | str | None = None,
    control_totals: sqlite3.Connection | None = None,
    vendor_dir: Path | str | None = None,
    settings: config.Settings | None = None,
    adapters: object | None = None,
) -> ReadinessReport:
    """Derive the readiness of every source from the objects that define them.

    Args:
        sources: the registry rows to report on.  ``None`` means every source the fabric
            declares, via :func:`declared_sources` — which is what makes "a source added to
            the registry appears in the report" true without an edit here.
        schema_registry: the contract registry to ask.  ``None`` means the process-wide one.
            An argument rather than a global read so a test can prove the schema cell is
            derived by handing in a registry with a different answer.
        repo_root: the checkout to read provenance tables, evidence and schema from.
        env: the environment credentials resolve against.  ``None`` means ``os.environ``.  An
            explicit ``{}`` is an empty environment, not the default one.
        secrets_file: overrides the credential secrets file location.
        control_totals: an open, read-only-in-practice connection to a database carrying the
            ``control_total`` table.  ``None`` means "not checked", which is reported as such
            and never as "clean".
        vendor_dir: where the data layer wrote ``coverage.json``.  Defaults to the settings'
            vendor directory.
        settings: used only to locate ``feeds_dir`` and ``vendor_dir``.  ``None`` loads the
            default profile, and a failure to do so is not fatal — the six generated feeds
            are then simply absent from the report rather than the report being absent.
        adapters: the ingest layer's adapter module, or anything carrying the same shapes, from
            which :func:`adapter_coverage` reads what will be turned into canonical records.
            **Handed in rather than imported**, which is what keeps the connectors/ingest seam
            intact — the caller that owns both layers does the wiring.  ``None`` means nobody
            was asked, and every source's :attr:`SourceReadiness.ingest` is then
            :attr:`Ingest.UNMEASURED`, which is reported as "not measured" and never as "no".
    """
    root = _repo_root() if repo_root is None else Path(repo_root)
    environ = os.environ if env is None else env
    active_registry = schema_registry_module.REGISTRY if schema_registry is None else schema_registry

    resolved_settings = settings
    if resolved_settings is None:
        try:
            resolved_settings = config.load_settings()
        except Exception:  # pragma: no cover - settings are path arithmetic and do not fail
            resolved_settings = None

    if sources is None:
        feeds_dir = resolved_settings.feeds_dir() if resolved_settings is not None else None
        rows = declared_sources(feeds_dir=feeds_dir)
    else:
        rows = tuple(sources)

    if vendor_dir is not None:
        coverage_dir: Path | None = Path(vendor_dir)
    elif resolved_settings is not None:
        coverage_dir = resolved_settings.vendor_dir()
    else:  # pragma: no cover - see above
        coverage_dir = None

    transports = transport_implementations()
    mappings = vendor_mappings()
    declared_mappings = source_mappings()
    coverage_of_adapters = None if adapters is None else adapter_coverage(adapters)
    mocks = _mock_modules()
    tallies = mock_field_tallies(root)
    evidence = evidence_index(root)
    known_evidence = frozenset(str(row.get("id")) for row in evidence)
    coverage = _coverage_report(coverage_dir)
    traces = _golden_traces(root)
    totals = _control_total_rows(control_totals)
    storage = _control_total_storage(root)

    built: list[SourceReadiness] = []
    for source in rows:
        kind_value = str(source.transport_kind)
        bound = source.transport
        transport = TransportReadiness(
            kind=kind_value,
            implementations=transports.get(kind_value, ()),
            bound=f"{type(bound).__module__}.{type(bound).__name__}" if bound else None,
        )
        auth = _auth_for(source, transports, environ, secrets_file)
        mapping_module, mapping_ids = mappings.get(source.vendor, (None, ()))
        per_source = source.source_id in mapping_ids
        declared = declared_mappings.get(source.source_id)
        counts = totals.get(source.source_id)
        built.append(
            SourceReadiness(
                source_id=source.source_id,
                vendor=source.vendor,
                enabled=source.enabled,
                reach=_reach(source.endpoint),
                endpoint=source.endpoint,
                payload_format=str(source.payload_format),
                mock_modules=mocks.get(source.vendor, ()),
                transport=transport,
                auth=auth,
                schema=_schema_for(source, active_registry),
                mapping=MappingReadiness(
                    module=mapping_module,
                    per_source=per_source,
                    dataset=getattr(declared, "dataset", None),
                    received_at_column=getattr(declared, "received_at_column", None),
                ),
                adapter=_adapter_for(source, coverage_of_adapters, per_source),
                fidelity=_fidelity_for(source, tallies, known_evidence),
                golden=_golden_for(source, coverage, traces),
                control_totals=ControlTotalReadiness(
                    storage_declared=storage,
                    checked=counts[0] if counts else None,
                    reconciled=counts[0] - counts[1] if counts else None,
                    unreconciled=counts[1] if counts else None,
                ),
                blocked=_blocked_for(source, evidence, auth),
            )
        )

    return ReadinessReport(
        sources=tuple(built),
        evidence_entries=len(evidence),
        unavailable_evidence=tuple(
            str(row.get("id"))
            for row in evidence
            if str(row.get("status", "")) == _UNAVAILABLE
        ),
        # An archetype is traced when a test function names it or carries its name.  Both,
        # because a trace cites its archetype either as a literal it asserts on or in the
        # name of the test itself, and only counting one of the two would report a suite
        # that follows the other convention as empty.
        archetype_traces=tuple(
            (
                name,
                tuple(
                    sorted(
                        set(traces.naming(name))
                        | {
                            function
                            for function in traces.functions
                            if name in function
                        }
                    )
                ),
            )
            for name in _ARCHETYPES
        ),
        golden_test_module=traces.module,
        notes=(
            "Connector-ready is Doc 2 steps 1-5 against a mock. Working connection needs "
            "authorized vendor data and is not reached by any source here. Production-ready "
            "is Doc 2 step 6 and is out of scope by decision.",
            "Every connector in this build talks to a local mock or a local directory. No "
            "row below has exchanged a byte with a vendor system.",
            # The third note, and the one that exists because its absence was actively
            # misleading.  Readable and awaiting-access are simultaneously true of the same
            # vendor row, and a reader who takes the switched-off flag to mean the file cannot
            # be read draws exactly the wrong conclusion about what is left to do.
            "Whether a source can be read and whether we are allowed to fetch it are two "
            "questions, and a vendor row here answers yes to the first and no to the second. "
            "A row that reads and does not adapt is a third answer again: the file is "
            "readable and contract-checked, and what is missing is a decision about what its "
            "rows mean.",
        ),
    )


# ═══ rendering ══════════════════════════════════════════════════════════════


def _cell(value: object) -> str:
    """One markdown table cell: never a pipe, never a newline, never empty.

    An empty cell renders as a collapsed column and reads as "no answer", which is the one
    thing this document must never accidentally say.
    """
    text = "" if value is None else str(value)
    text = text.replace("|", "\\|").replace("\n", " ").strip()
    return text or "—"


def _yes_no(value: bool | None, *, yes: str = "yes", no: str = "no") -> str:
    if value is None:
        return "not measured"
    return yes if value else no


def _fidelity_cell(fidelity: MockFidelity) -> str:
    if not fidelity.applies:
        return "no vendor mock — this repository's own generated data"
    return (
        f"{fidelity.rows} fields: {fidelity.spec} SPEC, "
        f"{fidelity.standard} STANDARD, {fidelity.invented} INVENTED"
    )


def _reach_cell(row: SourceReadiness) -> str:
    """What this source is pointed at, in words a reader cannot misread as a vendor.

    ``local mock`` and ``local generated files`` are distinguished by whether a module in
    ``recon.mocks`` stands in for this vendor — derived, not asserted — because "a mock of
    somebody else's system" and "our own synthetic feed" are different things and only one of
    them is a claim about a vendor.
    """
    if row.reach is Reach.LOCAL_MOCK:
        return "local mock" if row.mock_modules else "local generated files"
    if row.reach is Reach.VENDOR:
        return "vendor endpoint" + ("" if row.auth.configured else " (no credential)")
    return "not configured"


def _ingest_cell(row: SourceReadiness) -> str:
    """How far the bytes get, in words that cannot be read as an access answer.

    None of the four phrases mentions a credential, a gate or being switched on, and that is
    the constraint rather than an accident: this cell and :func:`_access_cell` sit beside each
    other, and a reader who can tell them apart at a glance is the entire point of the pair.
    """
    if row.ingest is Ingest.READS_AND_ADAPTS:
        return "reads and adapts"
    if row.ingest is Ingest.READS:
        return "reads, no adapter"
    if row.ingest is Ingest.UNREADABLE:
        return "no transport moves its bytes"
    return "not measured"


def _access_cell(row: SourceReadiness) -> str:
    """Whether anybody has let us fetch this yet — and nothing whatever about readability."""
    return "awaiting access" if row.awaiting_access else "switched on"


def render_markdown(report: ReadinessReport) -> str:
    """The whole report as one document.  Byte-identical for identical inputs.

    Every string below is either a column heading, a connective, or a value read off
    ``report``.  No cell is typed out per source, which is the property the acceptance
    criterion is about and the property :func:`render_markdown` would lose the moment
    somebody added a special case for one vendor.
    """
    lines: list[str] = [
        "# Connector readiness",
        "",
        f"Requirement F3. Generated from code and config at {report.generated_at}; "
        "no cell in this document is hand-maintained.",
        "",
    ]
    for note in report.notes:
        lines.extend([f"> {note}", ""])

    live = report.live_vendor_connections
    lines.extend(
        [
            "**Live vendor connections: "
            + (", ".join(live) if live else "none")
            + ".** "
            + (
                "A source above reaches a vendor endpoint with a resolved credential."
                if live
                else NO_LIVE_CONNECTION
            ),
            "",
            "## Gate evidence table",
            "",
            # ``ingest`` and ``access`` are two columns and never one.  They answer different
            # questions about the same row and are true at the same time; one column would have
            # to pick which of the two to say, and the one it dropped is the one a reader would
            # then assume.
            "| source | vendor | stage | reaches | ingest | access | transport | auth | "
            "schema | mapping | mock fidelity | golden claims | control totals | gaps | "
            "blocked |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
        ]
    )

    for row in report.sources:
        lines.append(
            "| "
            + " | ".join(
                _cell(value)
                for value in (
                    row.source_id,
                    row.vendor,
                    row.stage.value,
                    _reach_cell(row),
                    _ingest_cell(row),
                    _access_cell(row),
                    _yes_no(row.transport.implemented) + f" ({row.transport.kind})",
                    (
                        "not required"
                        if not row.auth.required
                        else _yes_no(row.auth.implemented)
                        + f" ({row.auth.kind or 'shape unknown'}), "
                        + ("configured" if row.auth.configured else "not configured")
                    ),
                    (
                        f"yes ({row.schema.current_version})"
                        if row.schema.registered
                        else "not registered"
                    ),
                    (
                        f"{row.mapping.module.rsplit('.', 1)[-1]}"
                        + (" (this source)" if row.mapping.per_source else " (vendor)")
                        if row.mapping.implemented
                        else "none"
                    ),
                    _fidelity_cell(row.fidelity),
                    (
                        f"{len(row.golden.traced_by)} trace(s)"
                        if row.golden.traced
                        else "not traced"
                    )
                    + (
                        f", {row.golden.dataset_rows} rows"
                        if row.golden.dataset_rows is not None
                        else ""
                    ),
                    (
                        "not checked"
                        if row.control_totals.checked is None
                        else f"{row.control_totals.checked} checked, "
                        f"{row.control_totals.unreconciled} unreconciled"
                    )
                    + ("" if row.control_totals.storage_declared else " (no storage)"),
                    str(len(row.gaps)) if row.gaps else "none",
                    str(len(row.blocked)) if row.blocked else "none",
                )
            )
            + " |"
        )

    lines.extend(
        [
            "",
            f"Evidence index: {report.evidence_entries} entries, "
            f"{len(report.unavailable_evidence)} of them sources that exist and could not be "
            "retrieved.",
            "",
        ]
    )

    if report.archetype_traces:
        lines.extend(
            [
                "## Golden claims (Doc 2 step 5)",
                "",
                "Traced end to end by "
                + (f"`{report.golden_test_module}`" if report.golden_test_module else "nothing")
                + ". An archetype is a property of a claim rather than of a feed, so it is "
                "counted here and not per source.",
                "",
                "| archetype | traced by |",
                "|---|---|",
            ]
        )
        for name, functions in report.archetype_traces:
            lines.append(
                "| "
                + _cell(name)
                + " | "
                + _cell(
                    ", ".join(f"`{function}`" for function in functions)
                    if functions
                    else "**not traced**"
                )
                + " |"
            )
        lines.append("")

    lines.extend(["## Per source", ""])
    for row in report.sources:
        lines.append(render_source(row))
    return "\n".join(lines).rstrip("\n") + "\n"


def render_source(row: SourceReadiness) -> str:
    """One source's document — F3's *"one generated document per source"*."""
    lines = [
        f"### `{row.source_id}`",
        "",
        f"- **Vendor**: {_cell(row.vendor)}",
        f"- **Stage**: {row.stage.value} "
        f"({'switched on' if row.enabled else 'switched off'})",
        f"- **Reaches**: {_reach_cell(row)} — `{_cell(row.endpoint)}`",
        f"- **Transport** ({row.transport.kind}): "
        + (
            ", ".join(f"`{name}`" for name in row.transport.implementations)
            if row.transport.implementations
            else "**no implementation registered**"
        )
        + (f"; bound to `{row.transport.bound}`" if row.transport.bound else "; not bound"),
    ]

    if row.auth.required:
        lines.append(
            f"- **Auth**: {row.auth.kind or 'shape undetermined'} via "
            + (f"`{row.auth.resolver}`" if row.auth.resolver else "**no resolver**")
            + f"; {'configured' if row.auth.configured else 'not configured here'}"
            + (f" — {_cell(row.auth.detail)}" if row.auth.detail else "")
        )
        if row.auth.env_vars:
            lines.append(
                "  - set: " + ", ".join(f"`{name}`" for name in row.auth.env_vars)
            )
    else:
        lines.append(f"- **Auth**: not required — {_cell(row.auth.detail)}")

    if row.schema.registered:
        match = row.schema.version_matches_mapping
        lines.append(
            f"- **Schema**: registered at `{row.schema.current_version}` "
            f"({row.schema.field_count} fields, {row.schema.required_field_count} required); "
            f"versions {', '.join(f'`{v}`' for v in row.schema.versions)}; "
            + (
                "matches the row's mapping version"
                if match
                else f"**disagrees with the row's mapping version `{row.schema.mapping_version}`**"
            )
        )
    else:
        lines.append(
            "- **Schema**: no contract registered; this source validates vacuously"
            + (
                " — which is a gap for a vendor source"
                if row.is_vendor
                else " — correct for a generated feed, which has no vendor to disagree with"
            )
        )

    lines.append(
        "- **Mapping**: "
        + (
            f"`{row.mapping.module}`"
            + (" declares this source id" if row.mapping.per_source else " covers this vendor")
            if row.mapping.implemented
            else "none"
        )
        + (f" — the vendor calls this dataset `{row.mapping.dataset}`" if row.mapping.dataset else "")
    )
    if row.mapping.arrival_time:
        lines.append(f"  - arrival time: {_cell(row.mapping.arrival_time)}")

    # The two lines this document grew because one line was answering both questions and could
    # only ever answer one of them.  They are adjacent on purpose: a reader who sees "reads and
    # adapts" directly above "awaiting access" cannot come away thinking the file is unreadable.
    lines.append(
        f"- **Ingest**: {_ingest_cell(row)} — documents are split as `{row.payload_format}`"
        + (f". {_cell(row.adapter.refusal)}" if row.adapter.refusal else "")
    )
    lines.append(
        "- **Access**: "
        + (
            "the registry row is switched off; what is outstanding is a credential, not a "
            "reader"
            if row.awaiting_access
            else "the row is switched on"
        )
        + " — a separate question from the ingest line above, and true at the same time"
    )
    lines.append(
        "- **Mock**: "
        + (
            ", ".join(f"`{name}`" for name in row.mock_modules)
            if row.mock_modules
            else "no mock module"
        )
        + f" — {_fidelity_cell(row.fidelity)}"
        + (f" (`{row.fidelity.table}`)" if row.fidelity.table else "")
    )
    if row.fidelity.evidence_ids:
        lines.append(
            "  - cited: " + ", ".join(f"`{name}`" for name in row.fidelity.evidence_ids)
        )

    golden = row.golden
    lines.append(
        "- **Golden claims**: "
        + (
            f"traced by {', '.join(f'`{name}`' for name in golden.traced_by)}"
            + (f" in `{golden.test_module}`" if golden.test_module else "")
            if golden.traced
            else "no test names this source"
        )
        + (f"; this dataset produced {golden.dataset_rows} rows" if golden.dataset_rows is not None else "")
    )
    if golden.archetype_rows:
        lines.append(
            "  - corpus archetypes: "
            + ", ".join(f"{name} {count}" for name, count in golden.archetype_rows)
            + (
                f" — **empty: {', '.join(golden.empty_archetypes)}**"
                if golden.empty_archetypes
                else ""
            )
        )

    totals = row.control_totals
    lines.append(
        "- **Control totals**: "
        + ("storage declared in `schema.sql`" if totals.storage_declared else "**no storage**")
        + (
            "; not checked in this report"
            if totals.checked is None
            else f"; {totals.checked} checked, {totals.reconciled} reconciled, "
            f"{totals.unreconciled} unreconciled"
        )
    )

    if row.gaps:
        lines.append("- **Gaps (ours to close)**:")
        lines.extend(f"  - {_cell(gap)}" for gap in row.gaps)
    else:
        lines.append("- **Gaps (ours to close)**: none")

    if row.blocked:
        lines.extend(["- **Vendor-blocked**:", ""])
        lines.append("  | kind | evidence | retrieved | what is blocked |")
        lines.append("  |---|---|---|---|")
        for item in row.blocked:
            reference = f"`{item.evidence_id}`" if item.evidence_id else "access gate"
            if item.url:
                reference += f" — {item.url}"
            detail = item.summary
            if item.detail:
                detail += f" ({item.detail})"
            if item.env_vars:
                detail += "; set " + ", ".join(item.env_vars)
            lines.append(
                "  | "
                + " | ".join(
                    _cell(value) for value in (item.kind, reference, item.retrieved, detail)
                )
                + " |"
            )
        lines.append("")
    else:
        lines.extend(["- **Vendor-blocked**: nothing", ""])

    return "\n".join(lines)


def render_documents(report: ReadinessReport) -> dict[str, str]:
    """``<source_id>.md`` -> that source's document, plus ``index.md`` for the whole table.

    F3 asks for *one generated document per source*, so this is the shape that satisfies it
    literally.  Returned rather than written: see the module docstring on why nothing here
    lands in a tracked file.
    """
    documents = {
        f"{row.source_id}.md": (
            "# Connector readiness — "
            f"`{row.source_id}`\n\nGenerated from code and config at "
            f"{report.generated_at}.\n\n" + render_source(row)
        )
        for row in report.sources
    }
    documents["index.md"] = render_markdown(report)
    return documents


# ═══ serialisation ══════════════════════════════════════════════════════════
#
# Every field below is written out by name.  ``dataclasses.asdict`` was the obvious thing to
# reach for and is wrong here twice over.
#
# **It drops the answers.**  ``stage``, ``gaps``, ``is_vendor``, ``talks_to_a_vendor``,
# ``clean``, ``traced``, ``implemented`` and ``applies`` are *properties*, not fields, and a
# property is invisible to ``asdict``.  A payload built that way would carry the raw cells and
# silently lose every conclusion drawn from them — including the one that says whether this
# source has reached a vendor — and it would do it without raising anything.
#
# **It is a deny-list where this needs an allow-list.**  Requirement A3 says no credential value
# appears in a tracked file, and the same rule has to hold for a value on the wire.  Nothing on
# :class:`AuthReadiness` holds a secret today — ``env_vars`` are variable *names* and ``detail``
# is a message ``credentials`` guarantees describes a value's shape and never its content — but
# ``asdict`` would publish whatever a future field happened to hold.  Naming the seven fields
# means a new one is invisible here until somebody decides it should be visible, which is the
# direction that failure should point.


def _transport_dict(transport: TransportReadiness) -> dict[str, Any]:
    return {
        "kind": transport.kind,
        "implementations": list(transport.implementations),
        "bound": transport.bound,
        "implemented": transport.implemented,
    }


def _auth_dict(auth: AuthReadiness) -> dict[str, Any]:
    """The three facts that must not collapse into one, plus the variables that would clear it.

    ``configured`` is about *this* environment and is the only one of the three that can change
    without a commit.  ``env_vars`` carries names so an operator reading this knows what to set;
    see the section note above for why no value can ride along with them.
    """
    return {
        "required": auth.required,
        "kind": auth.kind,
        "resolver": auth.resolver,
        "implemented": auth.implemented,
        "configured": auth.configured,
        "env_vars": list(auth.env_vars),
        "detail": auth.detail,
    }


def _schema_dict(schema: SchemaReadiness) -> dict[str, Any]:
    return {
        "registered": schema.registered,
        "current_version": schema.current_version,
        "versions": list(schema.versions),
        "field_count": schema.field_count,
        "required_field_count": schema.required_field_count,
        "mapping_version": schema.mapping_version,
        "version_matches_mapping": schema.version_matches_mapping,
    }


def _mapping_dict(mapping: MappingReadiness) -> dict[str, Any]:
    return {
        "module": mapping.module,
        "per_source": mapping.per_source,
        "implemented": mapping.implemented,
        "dataset": mapping.dataset,
        "received_at_column": mapping.received_at_column,
        # The sentence, not just the column name.  A client that turned a null column into
        # words itself would have to decide what a missing arrival time means, and the honest
        # answer — we know when the file landed and not when the row did — is exactly the kind
        # of thing a UI would round up to "no timestamp".
        "arrival_time": mapping.arrival_time,
    }


def _adapter_dict(adapter: AdapterReadiness) -> dict[str, Any]:
    """Three-valued, with the refusal carried beside it.

    ``adapts: null`` is "nobody was asked", which is not ``false``.  ``refusal`` is what makes
    ``false`` readable: it distinguishes a reader nobody has written from a reader deliberately
    withheld, and those two are opposite findings that a boolean renders identically.
    """
    return {
        "adapts": adapter.adapts,
        "measured": adapter.measured,
        "refusal": adapter.refusal,
    }


def _fidelity_dict(fidelity: MockFidelity) -> dict[str, Any]:
    return {
        "table": fidelity.table,
        "dataset": fidelity.dataset,
        "spec": fidelity.spec,
        "standard": fidelity.standard,
        "invented": fidelity.invented,
        "rows": fidelity.rows,
        "applies": fidelity.applies,
        "evidence_ids": list(fidelity.evidence_ids),
        # The same sentence the markdown prints, so a reader who sees both cannot be told two
        # different things about how much of a mock was invented.
        "summary": _fidelity_cell(fidelity),
    }


def _golden_dict(golden: GoldenClaimReadiness) -> dict[str, Any]:
    return {
        "archetype_rows": [
            {"archetype": name, "rows": count} for name, count in golden.archetype_rows
        ],
        "dataset_rows": golden.dataset_rows,
        "traced_by": list(golden.traced_by),
        "traced": golden.traced,
        "test_module": golden.test_module,
        "empty_archetypes": list(golden.empty_archetypes),
    }


def _control_totals_dict(totals: ControlTotalReadiness) -> dict[str, Any]:
    return {
        "storage_declared": totals.storage_declared,
        "checked": totals.checked,
        "reconciled": totals.reconciled,
        "unreconciled": totals.unreconciled,
        # Three-valued on purpose: ``null`` is "not checked", which is not "clean".
        "clean": totals.clean,
    }


def _blocked_dict(item: BlockedItem) -> dict[str, Any]:
    return {
        "kind": item.kind,
        "summary": item.summary,
        "evidence_id": item.evidence_id,
        "url": item.url,
        "retrieved": item.retrieved,
        "detail": item.detail,
        "env_vars": list(item.env_vars),
    }


def _source_dict(row: SourceReadiness) -> dict[str, Any]:
    return {
        "source_id": row.source_id,
        "vendor": row.vendor,
        "enabled": row.enabled,
        "stage": row.stage.value,
        "reach": row.reach.value,
        # The rendered phrase rather than a second derivation in the client.  A UI that turned
        # ``LOCAL_MOCK`` into words itself would be free to choose flattering ones.
        "reaches": _reach_cell(row),
        "endpoint": row.endpoint,
        "payload_format": row.payload_format,
        "is_vendor": row.is_vendor,
        "talks_to_a_vendor": row.talks_to_a_vendor,
        # ═══ two keys, never one ═══
        #
        # ``ingest`` says how far the bytes get; ``awaiting_access`` says whether anybody has
        # let us go and fetch them.  They are routinely both interesting about the same source
        # at the same moment — a vendor dataset here reads and adapts *and* waits on a
        # credential — so a single status string would have to drop one of them, and whichever
        # it dropped is the one a client would then invent for itself.
        #
        # ``ingest_summary`` is the rendered phrase, for the same reason ``reaches`` is: a
        # client turning ``READS`` into words is a client free to choose flattering ones.
        "ingest": row.ingest.value,
        "ingest_summary": _ingest_cell(row),
        "awaiting_access": row.awaiting_access,
        "access_summary": _access_cell(row),
        "mock_modules": list(row.mock_modules),
        "transport": _transport_dict(row.transport),
        "auth": _auth_dict(row.auth),
        "schema": _schema_dict(row.schema),
        "mapping": _mapping_dict(row.mapping),
        "adapter": _adapter_dict(row.adapter),
        "fidelity": _fidelity_dict(row.fidelity),
        "golden": _golden_dict(row.golden),
        "control_totals": _control_totals_dict(row.control_totals),
        # Ours to close, theirs to unblock.  Kept apart here for the reason
        # :attr:`SourceReadiness.gaps` gives: merging them loses the question of who acts.
        "gaps": list(row.gaps),
        "blocked": [_blocked_dict(item) for item in row.blocked],
    }


def as_dict(report: ReadinessReport) -> dict[str, Any]:
    """The whole report as JSON-safe primitives, conclusions included.

    Written so the HTTP route can stay three lines long, for the same reason every other route
    in ``recon.api.app`` is three lines long: a payload assembled inside a route is a payload no
    test can build without starting a web server.

    ``live_vendor_connections`` and ``live_vendor_connection_statement`` are first in the
    mapping and first on the screen, which is the point.  The statement is computed from the
    report rather than typed into the client, so a client cannot render "all systems connected"
    over a report that says the opposite.
    """
    live = report.live_vendor_connections
    return {
        "generated_at": report.generated_at,
        "live_vendor_connections": list(live),
        "live_vendor_connection_statement": (
            "These sources reach a vendor endpoint with a resolved credential: "
            + ", ".join(live)
            if live
            else NO_LIVE_CONNECTION
        ),
        "notes": list(report.notes),
        "sources": [_source_dict(row) for row in report.sources],
        "evidence_entries": report.evidence_entries,
        "unavailable_evidence": list(report.unavailable_evidence),
        "blocked_evidence_ids": list(report.blocked_evidence_ids),
        "golden_test_module": report.golden_test_module,
        "archetype_traces": [
            {"archetype": name, "traced_by": list(functions)}
            for name, functions in report.archetype_traces
        ],
        "untraced_archetypes": list(report.untraced_archetypes),
    }
