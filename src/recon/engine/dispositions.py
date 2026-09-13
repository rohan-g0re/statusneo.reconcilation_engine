"""Verdicts to dispositions and reason codes, computed in one pass.

Disposition answers exactly one question — *what do I do with this?* — and it has exactly three
answers: nothing (``CLOSED``), wait (``PENDING``), work it (``EXCEPTION``).  Everything else
lives one layer down in the reason codes, which are a **list**, because an episode can be
underpaid *and* missing settlement *and* short on cash at once, and with two tracks in play it
can be broken on one while merely waiting on the other.

═══ Deciding and explaining are the same rule ══════════════════════════════════════

Not two passes where one decides ``EXCEPTION`` and another works out why.  The rule that
decides is the rule that knows why, because it is the same rule — splitting them would let them
drift, producing a defect with no reason attached or a reason bolted onto a claim the first pass
called clean.

═══ The rollup is worst-wins ═══════════════════════════════════════════════════════

    EXCEPTION > PENDING > CLOSED

An episode sits in the exception queue when one track is broken even if the other is
legitimately still in flight.  The rollup does not average the tracks and does not hide the
healthy one: it surfaces the worse, because that is the one that needs a human.

═══ Why some terminal states are EXCEPTION rather than CLOSED ══════════════════════

A lost appeal and an exhausted denial are *not* open receivables — but they are also not
nothing-to-do.  Somebody has to make a write-off decision, and a system that quietly closed
them would be deciding to write money off on the operator's behalf.  So they are exceptions
carrying a reason that says the remaining action is a decision rather than a chase.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from recon.domain.enums import Disposition, ReasonCode, TrackScope

__all__ = [
    "TrackOutcome",
    "EpisodeOutcome",
    "reimbursement_outcome",
    "rebate_outcome",
    "compose",
    "CROSS_TRACK_REASONS",
]


@dataclass(frozen=True, slots=True)
class TrackOutcome:
    disposition: Disposition
    reasons: tuple[ReasonCode, ...]


@dataclass(frozen=True, slots=True)
class EpisodeOutcome:
    episode_disposition: Disposition
    reimbursement_disposition: Disposition
    #: ``None`` when the rebate verdict is ``C-00``.  The track is *absent*, which is not the
    #: same as finished — so the column is NULL rather than CLOSED.
    rebate_disposition: Disposition | None
    reasons: tuple[tuple[ReasonCode, TrackScope], ...]
    cross_track_flags: tuple[str, ...]


_C = Disposition.CLOSED
_P = Disposition.PENDING
_E = Disposition.EXCEPTION
_R = ReasonCode

#: Reimbursement verdict -> (disposition, reasons).  One row per rule; 31 rows for 31 verdicts.
_REIMBURSEMENT: dict[str, tuple[Disposition, tuple[ReasonCode, ...]]] = {
    # --- pharmacy ---------------------------------------------------------
    # Rejected at the point of sale: expected is zero, the drug was not dispensed, and there is
    # nothing to collect. Closed, with the reason recorded so it is explainable.
    "A-01": (_C, (_R.REJECTED_AT_POS,)),
    "A-02": (_P, (_R.AWAITING_REMITTANCE,)),
    "A-04": (_C, ()),
    "A-05": (_E, (_R.NO_CASH,)),
    "A-06": (_E, (_R.SETTLEMENT_MISSING,)),
    "A-07": (_E, (_R.UNDERPAID,)),
    # Two independent defects on one track, and both need saying: fixing the underpayment does
    # not make the missing cash appear.
    "A-08": (_E, (_R.UNDERPAID, _R.NO_CASH)),
    "A-09": (_E, (_R.OVERPAID,)),
    "A-10": (_C, ()),
    "A-11": (_E, (_R.REVERSAL_CASH_NOT_RETURNED,)),
    "A-12": (_C, ()),
    # The clawback cannot be tied to any bank movement, so we cannot confirm it happened and it
    # may be double-counted. This is one of the three places the engine deterministically knows
    # it cannot decide.
    "A-13": (_E, (_R.RECOUPMENT_UNTRACEABLE, _R.INSUFFICIENT_DATA)),
    "A-14": (_C, ()),
    "A-15": (_E, (_R.ADJUSTMENT_RESIDUAL,)),
    "A-16": (_C, ()),
    "A-17": (_E, (_R.DUPLICATE_PAYMENT,)),
    # --- medical ----------------------------------------------------------
    # The payer never saw the claim, so there is no denial and no appeal path. Resubmission is
    # the action, which makes it work rather than waiting.
    "B-01": (_E, (_R.CLEARINGHOUSE_REJECTED,)),
    "B-02": (_P, (_R.AWAITING_REMITTANCE,)),
    "B-04": (_C, ()),
    "B-05": (_E, (_R.NO_CASH,)),
    # The appeal window may still be open and unused. Somebody has to decide.
    "B-06": (_E, (_R.UNDERPAID,)),
    "B-07": (_P, (_R.APPEAL_PENDING,)),
    "B-08": (_C, ()),
    # Appeal exhausted: the residual is a write-off decision, not a receivable.
    "B-09": (_E, (_R.APPEAL_LOST,)),
    "B-10": (_E, (_R.DENIED,)),
    "B-11": (_P, (_R.APPEAL_PENDING,)),
    "B-12": (_C, ()),
    "B-13": (_E, (_R.APPEAL_LOST, _R.DENIED)),
    # The payer agreed and still did not pay. The highest-value chase available.
    "B-14": (_E, (_R.APPEAL_WON_NO_CASH,)),
    "B-15": (_E, (_R.CASH_MISMATCH,)),
    "B-16": (_E, (_R.DUPLICATE_REMITTANCE,)),
}

#: Rebate verdict -> (disposition, reasons).  ``C-00`` is handled separately: the track does not
#: exist, so it has no disposition at all.
_REBATE: dict[str, tuple[Disposition, tuple[ReasonCode, ...]]] = {
    "C-01": (_P, (_R.REBATE_AWAITING_QUALIFICATION,)),
    # The TPA evaluated and declined. A correct outcome, not a failure — expected rebate is
    # zero and there is nothing to chase.
    "C-02": (_C, (_R.REBATE_NOT_QUALIFIED,)),
    "C-03": (_P, (_R.REBATE_AWAITING_SUBMISSION,)),
    "C-05": (_P, (_R.REBATE_AWAITING_MANUFACTURER,)),
    # The qualification stood and the manufacturer disputed it. Expected falls to zero unless
    # it is corrected and resubmitted, which is a decision rather than a defect.
    "C-07": (_C, (_R.REBATE_REJECTED,)),
    "C-08": (_C, ()),
    "C-09": (_E, (_R.REBATE_NO_CASH,)),
    "C-10": (_E, (_R.REBATE_UNDERPAID,)),
    "C-11": (_P, (_R.REBATE_AWAITING_PAYMENT,)),
    "C-13": (_E, (_R.REBATE_CLAWED_BACK,)),
    "C-14": (_E, (_R.DUPLICATE_REBATE,)),
}

#: Cross-track flag -> the reasons it contributes, and whether it forces an exception.
#:
#: X-4 is the interesting row: it contributes **nothing** and forces nothing.  It exists so the
#: engine can recognise "reimbursement clean, rebate rejected" and deliberately *not* escalate
#: it.  Reporting it would be over-reporting, and an operator who learns that flags are noise
#: stops reading them.
CROSS_TRACK_REASONS: dict[str, tuple[tuple[ReasonCode, ...], bool]] = {
    "X-1": ((_R.DENIED_WITH_REBATE_PAID,), True),
    "X-2": ((_R.REBATE_ON_UNDISPENSED_CLAIM,), True),
    "X-3": ((_R.QUALIFICATION_UNDERMINED_BY_RECOUPMENT,), True),
    "X-4": ((), False),
    # Two tracks showing paperwork-without-money on one episode is almost certainly one cause
    # on our side, not two payers failing independently — so the honest answer is that the
    # engine cannot attribute it, which is exactly what INSUFFICIENT_DATA is for.
    "X-5": ((_R.CORRELATED_CASH_GAP, _R.INSUFFICIENT_DATA), True),
    "X-6": ((_R.TOTAL_LOSS,), True),
    "X-7": ((_R.REBATE_RE_REQUEST_AVAILABLE,), True),
}


def reimbursement_outcome(verdict: str) -> TrackOutcome:
    try:
        disposition, reasons = _REIMBURSEMENT[verdict]
    except KeyError:
        raise ValueError(f"no disposition rule for reimbursement verdict {verdict!r}") from None
    return TrackOutcome(disposition=disposition, reasons=reasons)


def rebate_outcome(verdict: str) -> TrackOutcome | None:
    """``None`` for ``C-00`` — the track is absent, and absence is not a status."""
    if verdict == "C-00":
        return None
    try:
        disposition, reasons = _REBATE[verdict]
    except KeyError:
        raise ValueError(f"no disposition rule for rebate verdict {verdict!r}") from None
    return TrackOutcome(disposition=disposition, reasons=reasons)


def compose(
    *,
    reimbursement_verdict: str,
    rebate_verdict: str,
    cross_track: Sequence[str],
    insufficient_data: bool = False,
) -> EpisodeOutcome:
    """Roll two track verdicts and the cross-track flags into one episode disposition.

    ``insufficient_data`` lets the caller add the code for a case the *records* make
    undecidable — unattributable rebate cash, for instance — as distinct from a case the
    *verdict* makes undecidable, which the tables above already cover.
    """
    reimbursement = reimbursement_outcome(reimbursement_verdict)
    rebate = rebate_outcome(rebate_verdict)

    reasons: list[tuple[ReasonCode, TrackScope]] = [
        (code, TrackScope.REIMBURSEMENT) for code in reimbursement.reasons
    ]
    if rebate is not None:
        reasons.extend((code, TrackScope.REBATE) for code in rebate.reasons)

    escalate = False
    for flag in cross_track:
        extra, forces_exception = CROSS_TRACK_REASONS.get(flag, ((), False))
        reasons.extend((code, TrackScope.CROSS_TRACK) for code in extra)
        escalate = escalate or forces_exception

    if insufficient_data and not any(
        code is _R.INSUFFICIENT_DATA for code, _ in reasons
    ):
        reasons.append((_R.INSUFFICIENT_DATA, TrackScope.CROSS_TRACK))
        escalate = True

    episode = _worst(
        [reimbursement.disposition] + ([rebate.disposition] if rebate else [])
    )
    if escalate:
        episode = _E

    return EpisodeOutcome(
        episode_disposition=episode,
        reimbursement_disposition=reimbursement.disposition,
        rebate_disposition=None if rebate is None else rebate.disposition,
        reasons=tuple(reasons),
        cross_track_flags=tuple(cross_track),
    )


_RANK = {Disposition.CLOSED: 0, Disposition.PENDING: 1, Disposition.EXCEPTION: 2}


def _worst(dispositions: Sequence[Disposition]) -> Disposition:
    """``EXCEPTION > PENDING > CLOSED``.

    Without a rollup you would have to nominate one track to represent the episode, and you
    would be wrong every time the tracks disagree — a rebate clawed back while the
    reimbursement is still legitimately in flight being the obvious case.
    """
    return max(dispositions, key=lambda value: _RANK[value])
