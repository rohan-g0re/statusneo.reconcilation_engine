"""Verdict codes, and the reachable verdict pairs.

Three vocabularies, all closed:

* **Reimbursement** — 16 pharmacy (``A-xx``) plus 15 medical (``B-xx``) = **31**.
  An episode has exactly one reimbursement track, so a given episode draws from one
  half or the other, never both.
* **Rebate** — **12** ``C-xx`` codes, including ``C-00`` meaning *the 340B track is
  absent*.  ``C-00`` is not "the track finished"; the corresponding
  ``rebate_disposition`` is ``NULL``, not ``CLOSED``.
* **Reachable pairs** — the full cross product, **31 x 12 = 372**, with no gaps.

That the product is exact is a *result*, not an assumption: exhaustive generation over
the 4,224 valid decision-tree configurations reached 372 of a possible 372.  It means
the two tracks are independent at the verdict level, which is why the engine composes
50 rules (31 + 12 + 7 cross-track) instead of enumerating 372 branches, and why the
cross-track flags are annotations rather than a validity table.

``decision_tree/pairs.json`` is the live oracle for all of this.  ``tests/
test_domain_vocabulary.py`` loads it and asserts equality against
:data:`REACHABLE_PAIRS`.  If anyone hand-edits a code here, or the state space moves,
exactly one test breaks.

Retired codes are listed in :data:`RETIRED_CODES` and are asserted absent from every
collection.  Most died when SLA thresholds were dropped as a verdict dimension: a
state whose only distinguishing feature was "past SLA" folded back into its
within-SLA twin.
"""

from __future__ import annotations

from types import MappingProxyType

from recon.domain.enums import ReimbursementTrack

__all__ = [
    "PHARMACY_CODES",
    "MEDICAL_CODES",
    "REIMBURSEMENT_CODES",
    "REBATE_CODES",
    "REACHABLE_PAIRS",
    "RETIRED_CODES",
    "VERDICT_DESCRIPTIONS",
    "REBATE_TRACK_ABSENT",
    "track_for_code",
    "describe",
]

#: ``C-00`` is special: it means the 340B track does not exist on this episode.
REBATE_TRACK_ABSENT = "C-00"

_PHARMACY: tuple[tuple[str, str], ...] = (
    ("A-01", "Rejected at point of sale; drug not dispensed; expected 0"),
    ("A-02", "Adjudication accepted, awaiting payment; no defect"),
    ("A-04", "Fully reconciled: paid in full, cash matched, settlement confirmed"),
    ("A-05", "Remittance claims payment, bank shows no matching deposit"),
    ("A-06", "Paid and cash-matched, settlement confirmation missing"),
    ("A-07", "Underpayment, cash matched the short amount"),
    ("A-08", "Underpayment and no cash: compound failure"),
    ("A-09", "Overpayment; refund liability"),
    ("A-10", "Reversed by pharmacy, money returned; net zero"),
    ("A-11", "Reversal recorded, money not returned"),
    ("A-12", "Recouped after audit, netted and matched"),
    ("A-13", "Recoupment not traceable to any deposit"),
    ("A-14", "Contractual adjustment explains the shortfall; remainder settled"),
    ("A-15", "Adjustment applied, variance still unexplained"),
    ("A-16", "Reversed before payment; expected falls to zero"),
    ("A-17", "Duplicate payment: two payment events for one claim"),
)

_MEDICAL: tuple[tuple[str, str], ...] = (
    ("B-01", "Clearinghouse rejection; 837 never reached the payer"),
    ("B-02", "Submitted and accepted, awaiting 835; no defect"),
    ("B-04", "Paid in full, cash matched"),
    ("B-05", "835 reports payment, no cash arrived"),
    ("B-06", "Partial payment, no appeal filed"),
    ("B-07", "Partial payment, appeal pending"),
    ("B-08", "Partial, appeal won, balance paid"),
    ("B-09", "Partial, appeal lost; balance written off"),
    ("B-10", "Denied, no appeal filed"),
    ("B-11", "Denied, appeal pending"),
    ("B-12", "Denied, appeal won, paid and matched"),
    ("B-13", "Denied, appeal lost; terminal loss"),
    ("B-14", "Appeal won, payment never arrived"),
    ("B-15", "Paid, then payer takeback"),
    ("B-16", "Duplicate 835 for the same claim"),
)

_REBATE: tuple[tuple[str, str], ...] = (
    ("C-00", "No 340B track on this episode; absence is not an exception"),
    ("C-01", "Qualification pending at the TPA; no defect"),
    ("C-02", "Not qualified; expected rebate 0; a correct outcome, not a failure"),
    ("C-03", "Qualified, rebate request not yet submitted"),
    ("C-05", "Request submitted, manufacturer decision pending"),
    ("C-07", "Manufacturer rejected the request"),
    ("C-08", "Approved, paid, cash matched"),
    ("C-09", "Approved and paid per the TPA, no cash in the bank"),
    ("C-10", "Approved, partial rebate paid"),
    ("C-11", "Approved, awaiting payment; no defect"),
    ("C-13", "Rebate clawed back; net rebate zero"),
    ("C-14", "Duplicate rebate payment"),
)

#: Codes that once existed and must never reappear.
#:
#: ``A-03``/``B-03``/``C-04``/``C-06`` were "…, past SLA" twins that folded into
#: their within-SLA counterparts when aging stopped being a verdict input
#: (Decision 19).  ``C-01a``/``C-11a`` were the mirror-image within-SLA states from
#: the same correction pass.  ``B-17`` was financially identical to ``B-04``.
#: ``C-12`` ("unmatched rebate") was never an episode state at all — it is cash with
#: no attributable episode, which is the feed-level exception ``D-4``.
RETIRED_CODES: frozenset[str] = frozenset(
    {"A-03", "B-03", "B-17", "C-04", "C-06", "C-12", "C-01a", "C-11a"}
)

PHARMACY_CODES: tuple[str, ...] = tuple(code for code, _ in _PHARMACY)
MEDICAL_CODES: tuple[str, ...] = tuple(code for code, _ in _MEDICAL)
REIMBURSEMENT_CODES: tuple[str, ...] = PHARMACY_CODES + MEDICAL_CODES
REBATE_CODES: tuple[str, ...] = tuple(code for code, _ in _REBATE)

#: The 372.  Built as an exact product because that is what the oracle measured.
REACHABLE_PAIRS: frozenset[tuple[str, str]] = frozenset(
    (reimbursement, rebate) for reimbursement in REIMBURSEMENT_CODES for rebate in REBATE_CODES
)

VERDICT_DESCRIPTIONS = MappingProxyType(dict(_PHARMACY + _MEDICAL + _REBATE))


def track_for_code(code: str) -> ReimbursementTrack:
    """Which reimbursement track a code belongs to.

    Raises:
        ValueError: if the code is not a live reimbursement code.
    """
    if code in PHARMACY_CODES:
        return ReimbursementTrack.PHARMACY
    if code in MEDICAL_CODES:
        return ReimbursementTrack.MEDICAL
    raise ValueError(f"not a reimbursement verdict code: {code!r}")


def describe(code: str) -> str:
    """One-line meaning of a verdict code.

    Raises:
        ValueError: if the code is unknown or retired.
    """
    try:
        return VERDICT_DESCRIPTIONS[code]
    except KeyError:
        hint = " (retired)" if code in RETIRED_CODES else ""
        raise ValueError(f"unknown verdict code: {code!r}{hint}") from None
