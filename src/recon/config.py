"""Configuration: every value that would otherwise be a literal somewhere else.

Three things about this module are load-bearing rather than stylistic.

**No mutable module-level singleton.**  ``load_settings()`` returns a fresh frozen
instance.  A mutable global is the classic way a test leaks state into a generator
run and silently changes the dataset that the published manifest hashes describe.

**``repo_root`` resolves from ``__file__``, never from the current directory.**  The
generator, the loader, pytest and uvicorn are each launched from a different
directory during a demo.  A cwd-relative path works right up until someone runs the
API from ``web/``.

**No wall-clock.**  Nothing in this module reads the system clock, and a test walks
the AST to assert it.  The generator has no concept of "now" (Decision 18); the
cursor lives at read time and is passed in.

There is no SLA, deadline, due-by or aging bucket here either, and a test asserts
that too (Decision 19).  Aging is ``cursor - date_of_service``, computed in SQL at
read time.

Environment overrides are **paths only** (``RECON_DATA_DIR``, ``RECON_DB_PATH``).
The seed and the window are what "reproducible" means; letting the environment move
them would make every published hash meaningless.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from enum import StrEnum
from pathlib import Path

__all__ = [
    "Profile",
    "ProfileSelection",
    "ProfileSpec",
    "ToleranceKind",
    "Settings",
    "load_settings",
    "cursor_from_date",
    "cursor_to_date",
    "TIMESTAMP_FORMAT",
    "DATE_FORMAT",
    "SCHEMA_VERSION",
    "ADAPTER_VERSION",
    "ENGINE_VERSION",
    "FEED_FILENAMES",
    "PBM_CLAIM_EVENTS_FILE",
    "PBM_REMITTANCE_835_FILE",
    "MEDICAL_837_SUBMISSIONS_FILE",
    "MEDICAL_835_REMITTANCE_FILE",
    "TPA_340B_EVENTS_FILE",
    "BANK_TRANSACTIONS_FILE",
    "GROUND_TRUTH_FILE",
    "MANIFEST_FILE",
    "VENDOR_IDENTIFIERS_FILE",
]

# --- formats ---------------------------------------------------------------

#: Fixed width, UTC, no offset.  Lexicographic order *is* chronological order, which
#: is what lets the cursor filter be a plain string comparison in SQL.
TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
DATE_FORMAT = "%Y-%m-%d"

#: Bumped whenever ``db/schema.sql`` changes shape.  A database stamped with an older
#: value is rebuilt, not migrated (the database is a derived artefact).
SCHEMA_VERSION = 5
ADAPTER_VERSION = "1.0.0"
ENGINE_VERSION = "1.0.0"

# --- canonical file names (docs/feed_formats.md is the naming authority) ---

PBM_CLAIM_EVENTS_FILE = "pbm_claim_events.jsonl"
PBM_REMITTANCE_835_FILE = "pbm_remittance_835.jsonl"
MEDICAL_837_SUBMISSIONS_FILE = "medical_837_submissions.jsonl"
MEDICAL_835_REMITTANCE_FILE = "medical_835_remittance.jsonl"
TPA_340B_EVENTS_FILE = "tpa_340b_events.jsonl"
BANK_TRANSACTIONS_FILE = "bank_transactions.csv"

#: The six feeds, in the order ``feed_formats.md`` introduces them.
FEED_FILENAMES: tuple[str, ...] = (
    PBM_CLAIM_EVENTS_FILE,
    PBM_REMITTANCE_835_FILE,
    MEDICAL_837_SUBMISSIONS_FILE,
    MEDICAL_835_REMITTANCE_FILE,
    TPA_340B_EVENTS_FILE,
    BANK_TRANSACTIONS_FILE,
)

GROUND_TRUTH_FILE = "ground_truth.json"
MANIFEST_FILE = "manifest.json"

#: Cross-system identifiers the orchestrator mints for the vendor-format layer.
#:
#: Deliberately **not** a seventh feed.  ``FEED_FILENAMES`` is what ``load_feeds`` iterates
#: and what every existing hash, adapter and dossier projection is pinned to, so a vendor
#: identifier added there would travel into ``raw_record`` and — through
#: ``get_raw_record``'s ``file_sha256`` — into a recorded agent prompt, where it would
#: invalidate every replay fixture.  The sidecar sits beside the feeds instead: written by
#: the orchestrator, read by the vendor mocks, invisible to ingestion.
VENDOR_IDENTIFIERS_FILE = "identifiers.jsonl"

FEEDS_SUBDIR = "feeds"
TRUTH_SUBDIR = "truth"
VENDOR_SUBDIR = "vendor"


# --- profiles --------------------------------------------------------------


class Profile(StrEnum):
    DEMO = "demo"
    FULL = "full"


class ProfileSelection(StrEnum):
    """How a profile picks the episodes it generates.

    ``CURATED`` hand-places each defect exactly once so a 20-minute walkthrough can
    reach every interesting state.  ``VERDICT_STRATIFIED`` samples over verdict
    *pairs*, never over configurations — configuration density varies 252-fold across
    pairs, so uniform sampling over configurations would floor the rarest verdicts.
    """

    CURATED = "CURATED"
    VERDICT_STRATIFIED = "VERDICT_STRATIFIED"


class CuratedSpine(StrEnum):
    """Which spine a ``CURATED`` profile draws, when one seed is not enough.

    The ``demo`` profile serves two readers with incompatible needs, and pretending
    otherwise is what makes one of them break silently.

    ``MIXED`` is the default and what a human rebuilding the demo wants: one guaranteed
    episode per named edge case, drawn by seed from a family of pairs that all demonstrate
    it, and the remaining slots sampled from a pool deliberately wider than the slots.  Two
    seeds then disagree about the queue mix, which a fixed sixty-pair list could not do —
    it was exactly as long as the profile it filled, so there was nothing to choose.

    ``RECORDED`` pins the hand-placed spine the agent-trace fixtures under
    ``tests/fixtures/agent_traces/`` were recorded against.  ``ReplayClient`` matches the
    digest of every request the recorded run made, and those requests carry dossier
    contents — so *any* change to the demo's composition invalidates every recording, and
    re-recording needs a live model and an API key.  The eval set asks for this explicitly
    rather than inheriting it, so that "the evals replay a frozen dataset" is a property
    someone can read in the code instead of a coincidence that held until it did not.
    """

    MIXED = "MIXED"
    RECORDED = "RECORDED"


@dataclass(frozen=True, slots=True)
class ProfileSpec:
    name: Profile
    episode_count: int
    selection: ProfileSelection
    subdir: str


_PROFILE_SPECS: dict[Profile, ProfileSpec] = {
    Profile.DEMO: ProfileSpec(Profile.DEMO, 60, ProfileSelection.CURATED, "demo"),
    Profile.FULL: ProfileSpec(Profile.FULL, 1500, ProfileSelection.VERDICT_STRATIFIED, "full"),
}


class ToleranceKind(StrEnum):
    CASH_MATCH = "CASH_MATCH"
    UNDERPAYMENT = "UNDERPAYMENT"
    REBATE = "REBATE"


# --- settings --------------------------------------------------------------


def _default_repo_root() -> Path:
    # <repo>/src/recon/config.py -> parents[0]=recon, [1]=src, [2]=<repo>
    return Path(__file__).resolve().parents[2]


@dataclass(frozen=True, slots=True)
class Settings:
    """An immutable snapshot of everything configurable.

    Construct through :func:`load_settings`; mutating a field raises
    ``FrozenInstanceError``, which is the point.
    """

    profile: Profile
    master_seed: int
    window_start: date
    window_end: date
    repo_root: Path
    data_dir: Path
    db_path: Path
    cash_match_tolerance_cents: int
    underpayment_tolerance_cents: int
    rebate_tolerance_cents: int
    curated_spine: CuratedSpine = CuratedSpine.MIXED
    schema_version: int = SCHEMA_VERSION
    adapter_version: str = ADAPTER_VERSION
    engine_version: str = ENGINE_VERSION
    _profile_specs: dict[Profile, ProfileSpec] = field(
        default_factory=lambda: dict(_PROFILE_SPECS), repr=False, compare=False
    )

    # -- profile ------------------------------------------------------------

    @property
    def spec(self) -> ProfileSpec:
        return self._profile_specs[self.profile]

    @property
    def episode_count(self) -> int:
        return self.spec.episode_count

    @property
    def selection(self) -> ProfileSelection:
        return self.spec.selection

    # -- paths --------------------------------------------------------------

    @property
    def generated_root(self) -> Path:
        return self.data_dir / "generated"

    def generated_dir(self, profile: Profile | None = None) -> Path:
        """``data/generated/<profile>/`` — the root of one profile's artefacts."""
        spec = self._profile_specs[Profile(profile) if profile else self.profile]
        return self.generated_root / spec.subdir

    def feeds_dir(self, profile: Profile | None = None) -> Path:
        """``data/generated/<profile>/feeds/`` — the only directory the loader reads."""
        return self.generated_dir(profile) / FEEDS_SUBDIR

    def truth_dir(self, profile: Profile | None = None) -> Path:
        """``data/generated/<profile>/truth/`` — ingestion and the engine never read this."""
        return self.generated_dir(profile) / TRUTH_SUBDIR

    def vendor_dir(self, profile: Profile | None = None) -> Path:
        """``data/generated/<profile>/vendor/`` — the vendor-format layer's own directory.

        Holds the minted-identifier sidecar and, once the mocks run, the Beacon, Verity and
        Craneware files.  A sibling of ``feeds/`` rather than a member of it, so the six
        feeds stay byte-identical and ``load_feeds`` cannot reach any of this.
        """
        return self.generated_dir(profile) / VENDOR_SUBDIR

    def vendor_identifiers_path(self, profile: Profile | None = None) -> Path:
        return self.vendor_dir(profile) / VENDOR_IDENTIFIERS_FILE

    def feed_path(self, filename: str, profile: Profile | None = None) -> Path:
        if filename not in FEED_FILENAMES:
            raise ValueError(
                f"unknown feed file {filename!r}; expected one of {', '.join(FEED_FILENAMES)}"
            )
        return self.feeds_dir(profile) / filename

    def manifest_path(self, profile: Profile | None = None) -> Path:
        return self.generated_dir(profile) / MANIFEST_FILE

    def ground_truth_path(self, profile: Profile | None = None) -> Path:
        return self.truth_dir(profile) / GROUND_TRUTH_FILE

    # -- cursor -------------------------------------------------------------

    def cursor_from_date(self, day: date) -> str:
        return cursor_from_date(day)

    def cursor_to_date(self, cursor: str) -> date:
        return cursor_to_date(cursor)

    @property
    def max_cursor(self) -> str:
        """The cursor at which every record in the dataset is visible."""
        return cursor_from_date(self.window_end)

    @property
    def min_cursor(self) -> str:
        """The cursor at which nothing is visible yet — a valid, empty state."""
        return cursor_from_date(self.window_start - timedelta(days=1))

    # -- tolerances ---------------------------------------------------------

    def tolerance_for(self, kind: ToleranceKind | str) -> int:
        """Cent tolerance for a comparison of the given kind.

        The signature carries no tenant and no pathway today, and that is the
        deliberate part: when a second payer arrives, this grows arguments and the
        callers do not grow ``if`` statements (Decision 33).  A rule that reads a
        tolerance through an accessor scales by rows; a rule with the number inline
        forks per payer.
        """
        try:
            kind = ToleranceKind(kind)
        except ValueError:
            valid = ", ".join(k.value for k in ToleranceKind)
            raise ValueError(f"unknown tolerance kind {kind!r}; expected one of {valid}") from None
        return {
            ToleranceKind.CASH_MATCH: self.cash_match_tolerance_cents,
            ToleranceKind.UNDERPAYMENT: self.underpayment_tolerance_cents,
            ToleranceKind.REBATE: self.rebate_tolerance_cents,
        }[kind]

    def for_profile(self, profile: Profile | str) -> "Settings":
        """A copy of these settings pointed at a different profile."""
        profile = Profile(profile)
        return replace(self, profile=profile, db_path=_default_db_path(self.data_dir, profile))


def _default_db_path(data_dir: Path, profile: Profile) -> Path:
    return data_dir / f"recon_{_PROFILE_SPECS[profile].subdir}.sqlite"


def load_settings(
    profile: Profile | str = Profile.DEMO,
    *,
    master_seed: int | None = None,
    data_dir: Path | str | None = None,
    db_path: Path | str | None = None,
    repo_root: Path | str | None = None,
    curated_spine: CuratedSpine | str = CuratedSpine.MIXED,
) -> Settings:
    """Build a frozen :class:`Settings`.

    Args:
        profile: ``demo`` or ``full``.
        master_seed: overridable for tests.  The default is the published seed and
            changing it changes every generated identifier.
        curated_spine: which spine the ``demo`` profile draws; see :class:`CuratedSpine`.
            Pass ``RECORDED`` to reproduce the dataset the agent-trace fixtures were
            recorded against.  Ignored by ``full``, which stratifies instead.
        data_dir: overrides ``RECON_DATA_DIR`` and the default ``<repo>/data``.
        db_path: overrides ``RECON_DB_PATH`` and the per-profile default.
        repo_root: only useful in tests that relocate the tree.

    Raises:
        ValueError: on an unknown profile name, naming the valid ones.
    """
    try:
        resolved_profile = Profile(profile)
    except ValueError:
        valid = ", ".join(p.value for p in Profile)
        raise ValueError(f"unknown profile {profile!r}; expected one of {valid}") from None

    root = Path(repo_root).resolve() if repo_root is not None else _default_repo_root()

    if data_dir is not None:
        resolved_data = Path(data_dir)
    elif os.environ.get("RECON_DATA_DIR"):
        resolved_data = Path(os.environ["RECON_DATA_DIR"])
    else:
        resolved_data = root / "data"
    resolved_data = resolved_data.expanduser()

    if db_path is not None:
        resolved_db = Path(db_path)
    elif os.environ.get("RECON_DB_PATH"):
        resolved_db = Path(os.environ["RECON_DB_PATH"])
    else:
        resolved_db = _default_db_path(resolved_data, resolved_profile)

    return Settings(
        profile=resolved_profile,
        curated_spine=CuratedSpine(curated_spine),
        master_seed=MASTER_SEED if master_seed is None else master_seed,
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        repo_root=root,
        data_dir=resolved_data,
        db_path=resolved_db.expanduser(),
        cash_match_tolerance_cents=CASH_MATCH_TOLERANCE_CENTS,
        underpayment_tolerance_cents=UNDERPAYMENT_TOLERANCE_CENTS,
        rebate_tolerance_cents=REBATE_TOLERANCE_CENTS,
    )


# --- published defaults ----------------------------------------------------

#: Mnemonic: the window start.  Every generated identifier descends from this.
MASTER_SEED = 20_250_701

WINDOW_START = date(2025, 7, 1)
WINDOW_END = date(2026, 7, 1)

#: Exact-match comparisons.  Zero, because money is integer cents and a real cash
#: match either is or is not exact.  The constant exists so the *rule* reads
#: ``abs(delta) <= tolerance_for(CASH_MATCH)`` rather than ``delta == 0``.
CASH_MATCH_TOLERANCE_CENTS = 0
UNDERPAYMENT_TOLERANCE_CENTS = 0
REBATE_TOLERANCE_CENTS = 0


# --- cursor ----------------------------------------------------------------


def cursor_from_date(day: date) -> str:
    """A UI date ``D`` means "everything that had arrived by the end of ``D``".

    Defined exactly once, here, because the alternative is that the engine reads a
    date as start-of-day while the front-end slider means end-of-day, every claim is
    off by one day's worth of arrivals, and nobody notices until the walkthrough.
    """
    if not isinstance(day, date):
        raise TypeError(f"cursor_from_date() expects a date, got {type(day).__name__}")
    return f"{day.isoformat()}T23:59:59Z"


def cursor_to_date(cursor: str) -> date:
    """Inverse of :func:`cursor_from_date`."""
    if not isinstance(cursor, str) or len(cursor) != 20 or not cursor.endswith("Z"):
        raise ValueError(f"not a cursor timestamp (YYYY-MM-DDTHH:MM:SSZ): {cursor!r}")
    return date.fromisoformat(cursor[:10])
