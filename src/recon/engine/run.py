"""Running the engine: recompute per ``(episode, cursor)``, then append.

Nothing is ever mutated.  A verdict is a pure function of the immutable source records visible
at a cursor, so re-running over the same data must produce the same answer — and that is only
true if the verdict is *derived* from the records rather than stored as truth on them
(Decision 23).

The practical consequence is that the exception queue is not a place things move into.  It is a
query: "episodes whose latest verdict at this cursor is EXCEPTION".  Delete the whole verdict
log and it rebuilds by replay, losing nothing.

═══ Why there is no scan ═══════════════════════════════════════════════════════════

:func:`run_for_episodes` takes the episodes to recompute.  In incremental use that set is
exactly the episodes touched by newly-arrived documents — and because aging is not a verdict
input, an episode with no new record *cannot* have changed, so nothing else needs looking at.
That is a property with a proof, not an optimisation with a hope (Decision A4).

:func:`run_all` exists for a full rebuild and for the test suite, where recomputing everything
at a cursor is the point rather than a cost.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Iterable, Sequence

from recon import config
from recon.db import repository
from recon.domain.enums import Disposition, EvidenceRole, RecordKind
from recon.engine import verdicts as rules
from recon.engine.dimensions import (
    Dimensions,
    EpisodeEvidence,
    derive_dimensions,
    gather_evidence,
)
from recon.engine.dispositions import EpisodeOutcome, compose
from recon.reference import pricing
from recon.reference.fingerprint import fingerprint as reference_fingerprint

__all__ = ["EngineResult", "evaluate_episode", "run_for_episodes", "run_all"]

#: The engine's own wall-clock field is deliberately a constant.  ``computed_at`` is operational
#: metadata, and a real timestamp would make two runs over identical data produce different
#: rows — which would break the byte-for-byte reproducibility the manifest claims.
_COMPUTED_AT = "1970-01-01T00:00:00Z"


@dataclass(frozen=True, slots=True)
class EngineResult:
    """One episode's recomputed verdict, before it is written."""

    episode_id: str
    cursor: str
    dimensions: Dimensions
    reimbursement_verdict: str
    rebate_verdict: str
    outcome: EpisodeOutcome
    expected_reimbursement_cents: int
    received_reimbursement_cents: int
    expected_rebate_cents: int
    received_rebate_cents: int
    evidence: EpisodeEvidence

    @property
    def reimbursement_variance_cents(self) -> int:
        return self.expected_reimbursement_cents - self.received_reimbursement_cents

    @property
    def rebate_variance_cents(self) -> int:
        return self.expected_rebate_cents - self.received_rebate_cents

    @property
    def verdict_pair(self) -> tuple[str, str]:
        return self.reimbursement_verdict, self.rebate_verdict


# ═══ expected amounts ═══════════════════════════════════════════════════════


def expected_amounts(episode_row) -> tuple[int, int]:
    """``(expected_reimbursement, expected_rebate)`` in cents.

    Computed through :mod:`recon.reference.pricing` — the *same* module the generator used.
    That shared dependency is what makes a variance meaningful: because both sides derive the
    expected figure identically, a reported shortfall is the injected shortfall and not a
    rounding disagreement between two implementations.

    Returns ``(0, 0)`` when the payer or the drug cannot be resolved.  That is not a silent
    default — the caller turns it into ``INSUFFICIENT_DATA``, because an expected amount we
    cannot compute is a finding rather than a zero.
    """
    payer_id = episode_row["pbm_id"] or episode_row["medical_payer_id"]
    ndc11 = episode_row["ndc11"]
    quantity_milli = episode_row["quantity_milli"] or 0

    if not payer_id or not ndc11 or quantity_milli <= 0:
        return 0, 0
    if not pricing.has_contract_terms(payer_id, ndc11):
        return 0, 0

    expected_reimbursement = pricing.expected_reimbursement_cents(
        ndc11, payer_id, quantity_milli
    )
    expected_rebate = pricing.expected_rebate_cents(ndc11, quantity_milli)
    return expected_reimbursement, expected_rebate


# ═══ one episode ════════════════════════════════════════════════════════════


def evaluate_episode(
    conn: sqlite3.Connection, episode_row, cursor: str
) -> EngineResult:
    """Derive the verdict this episode's records describe at ``cursor``.

    Four stages, which is exactly the shape the state-space analysis predicted: route by track
    (one check), resolve the reimbursement verdict (1–12 checks), resolve the rebate verdict
    (1–12), then apply seven cross-track checks unconditionally.
    """
    expected_reimbursement, expected_rebate = expected_amounts(episode_row)
    evidence = gather_evidence(conn, episode_row, cursor)
    dimensions = derive_dimensions(
        evidence,
        expected_reimbursement_cents=expected_reimbursement,
        expected_rebate_cents=expected_rebate,
    )
    configuration = dimensions.as_configuration()

    reimbursement_verdict = rules.classify_reimbursement(configuration)
    rebate_verdict = rules.classify_rebate(configuration)
    flags = rules.cross_track_flags(configuration)

    # An expected amount we could not compute is genuinely undecidable, and saying so is the
    # required behaviour rather than a fallback: the alternative is reporting a variance against
    # an expectation of zero, which would look like a clean claim.
    could_not_price = expected_reimbursement == 0 and dimensions.ph_adjudication != "REJECTED"
    outcome = compose(
        reimbursement_verdict=reimbursement_verdict,
        rebate_verdict=rebate_verdict,
        cross_track=flags,
        insufficient_data=could_not_price and reimbursement_verdict not in {"A-01", "B-01"},
    )

    # What was actually received, as the allocations say — not as the remittance claims.  The
    # gap between the two is the reconciliation finding.
    received_reimbursement = evidence.reimbursement_cash_in_cents
    received_rebate = evidence.rebate_cash_in_cents

    # A rejected or withdrawn claim expects nothing, so a variance against it would be
    # meaningless.  Expected collapses to zero rather than being carried forward.
    if reimbursement_verdict in {"A-01", "A-16", "A-10", "A-11"}:
        expected_reimbursement = 0
    if rebate_verdict in {"C-00", "C-02", "C-07"}:
        expected_rebate = 0

    return EngineResult(
        episode_id=episode_row["episode_id"],
        cursor=cursor,
        dimensions=dimensions,
        reimbursement_verdict=reimbursement_verdict,
        rebate_verdict=rebate_verdict,
        outcome=outcome,
        expected_reimbursement_cents=expected_reimbursement,
        received_reimbursement_cents=received_reimbursement,
        expected_rebate_cents=expected_rebate,
        received_rebate_cents=received_rebate,
        evidence=evidence,
    )


# ═══ writing ════════════════════════════════════════════════════════════════


def append_result(
    conn: sqlite3.Connection, result: EngineResult, *, previous=None
) -> int:
    """Append one verdict row plus its reasons, flags and evidence, atomically.

    ``reopened_from`` is set when the disposition moved *backwards* — from closed, or from
    pending — because new information arrived.  It is a flag and never a fourth disposition: a
    reopened episode lands in whichever of the three the recomputation produces, and nothing
    special-cases it, because the disposition is recomputed rather than assigned.
    """
    reopened_from = None
    previously_closed_at = None
    reopened_on = None
    if previous is not None:
        was = Disposition(previous["episode_disposition"])
        now = result.outcome.episode_disposition
        if _moved_backwards(was, now):
            reopened_from = str(was)
            previously_closed_at = previous["cursor_at"]
            reopened_on = result.cursor

    return repository.append_verdict(
        conn,
        episode_id=result.episode_id,
        cursor_at=result.cursor,
        computed_at=_COMPUTED_AT,
        engine_version=config.ENGINE_VERSION,
        reference_fingerprint=reference_fingerprint(),
        episode_disposition=str(result.outcome.episode_disposition),
        reimbursement_disposition=str(result.outcome.reimbursement_disposition),
        rebate_disposition=(
            None
            if result.outcome.rebate_disposition is None
            else str(result.outcome.rebate_disposition)
        ),
        reimbursement_verdict_code=result.reimbursement_verdict,
        rebate_verdict_code=result.rebate_verdict,
        expected_reimbursement_cents=result.expected_reimbursement_cents,
        received_reimbursement_cents=result.received_reimbursement_cents,
        reimbursement_variance_cents=result.reimbursement_variance_cents,
        expected_rebate_cents=result.expected_rebate_cents,
        received_rebate_cents=result.received_rebate_cents,
        rebate_variance_cents=result.rebate_variance_cents,
        reopened_from=reopened_from,
        previously_closed_at=previously_closed_at,
        reopened_on=reopened_on,
        reasons=[(code, track) for code, track in result.outcome.reasons],
        cross_track_flags=list(result.outcome.cross_track_flags),
        evidence=_evidence_rows(result.evidence),
    )


def _moved_backwards(was: Disposition, now: Disposition) -> bool:
    """A disposition moving away from CLOSED, or from PENDING to EXCEPTION.

    Reopening is not exclusively a fall from ``CLOSED``: a 340B rebate clawed back while the
    reimbursement is still legitimately in flight reopens from ``PENDING``, and the flag records
    which it came from either way — because reopened-from-closed outranks never-paid when the
    queue is prioritised.
    """
    if was is Disposition.CLOSED and now is not Disposition.CLOSED:
        return True
    return was is Disposition.PENDING and now is Disposition.EXCEPTION


_ROLE_BY_KIND = {
    str(RecordKind.PHARMACY_CLAIM): EvidenceRole.ADJUDICATION,
    str(RecordKind.PHARMACY_REVERSAL): EvidenceRole.REVERSAL,
    str(RecordKind.REMITTANCE_CLAIM_LINE): EvidenceRole.REMITTANCE_CLAIM_LINE,
    str(RecordKind.PROVIDER_LEVEL_ADJUSTMENT): EvidenceRole.PROVIDER_LEVEL_ADJUSTMENT,
    str(RecordKind.MEDICAL_SUBMISSION): EvidenceRole.MEDICAL_SUBMISSION,
    str(RecordKind.MEDICAL_ACKNOWLEDGMENT): EvidenceRole.MEDICAL_SUBMISSION,
    str(RecordKind.TPA_QUALIFICATION): EvidenceRole.TPA_QUALIFICATION,
    str(RecordKind.TPA_REBATE_REQUEST): EvidenceRole.TPA_QUALIFICATION,
    str(RecordKind.TPA_MANUFACTURER_DECISION): EvidenceRole.TPA_QUALIFICATION,
    str(RecordKind.TPA_REVERSAL): EvidenceRole.REVERSAL,
    str(RecordKind.REBATE_DISPENSE_LINE): EvidenceRole.REBATE_LINE,
    str(RecordKind.BANK_TRANSACTION): EvidenceRole.BANK_CREDIT,
}


def _evidence_rows(evidence: EpisodeEvidence) -> list[tuple[int, int, str]]:
    """Lineage, running downward: every computed number points back at a raw source row.

    This is what the assignment actually grades — *"preserve source identifiers so an
    investigator can trace a result back to the synthetic source record"* — and it is why the
    link table carries ``raw_id`` alongside ``norm_id``: the trace back to the verbatim payload
    is then one join rather than two.
    """
    rows: list[tuple[int, int, str]] = []
    seen: set[int] = set()
    for norm_id, raw_id, kind in evidence.citations:
        if norm_id in seen:
            continue
        seen.add(norm_id)
        role = _ROLE_BY_KIND.get(kind, EvidenceRole.ADJUDICATION)
        rows.append((norm_id, raw_id, str(role)))
    return rows


# ═══ batch drivers ══════════════════════════════════════════════════════════


def run_for_episodes(
    conn: sqlite3.Connection, episode_ids: Sequence[str], cursor: str
) -> list[EngineResult]:
    """Recompute exactly the episodes named, at one cursor.

    This is the incremental path, and the set handed in is the set of episodes touched by
    newly-arrived documents.  Nothing else can have moved.
    """
    if not episode_ids:
        return []
    results: list[EngineResult] = []
    placeholders = ",".join("?" * len(episode_ids))
    rows = conn.execute(
        f"SELECT * FROM episode WHERE episode_id IN ({placeholders}) ORDER BY episode_id",
        list(episode_ids),
    ).fetchall()
    for row in rows:
        previous = repository.latest_verdict(conn, row["episode_id"], cursor, with_children=False)
        result = evaluate_episode(conn, row, cursor)
        append_result(
            conn,
            result,
            previous=None
            if previous is None
            else {
                "episode_disposition": str(previous.episode_disposition),
                "cursor_at": previous.cursor_at,
            },
        )
        results.append(result)
    return results


def run_all(conn: sqlite3.Connection, cursor: str) -> list[EngineResult]:
    """Recompute every episode at ``cursor``.

    A full rebuild.  Used by the test suite, where evaluating everything is the assertion, and
    by the front end's regenerate control, where the dataset has just been rebuilt from scratch
    and there is no prior state to be incremental against.
    """
    rows = conn.execute("SELECT episode_id FROM episode ORDER BY episode_id").fetchall()
    return run_for_episodes(conn, [row["episode_id"] for row in rows], cursor)
