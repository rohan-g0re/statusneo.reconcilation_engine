"""The cash splitter: one deposit, many claims.

A bank deposit carries no claim identifier at all — it cannot, because a bank has no concept
of a claim.  Its ``trn02`` resolves to a *remittance*, and the remittance holds the claim
list.  One bank line can therefore touch dozens of episodes, and the only route to them is
through the remittance it points at (Decision A21).  Never by scanning claims looking for
one whose amount happens to match.

═══ Why allocation is not pro rata ═════════════════════════════════════════════════

The obvious implementation — split the deposit across the claims in proportion to what each
was paid — is wrong, and wrong in a way that destroys the most valuable trap in the dataset.

A deposit is short of the sum of its claim payments because of ``PLB``: the payer netted a
clawback for some *earlier* claim out of today's batch.  The claims in today's batch were
each paid in full.  Pro rata would smear that clawback thinly across every claim in the
batch, turning one traceable recoupment into forty phantom underpayments — and every one of
them would look exactly like a real payment shortfall.

So allocation follows the 835's own identity instead:

    BPR02 = Σ(CLP04) − Σ(PLB, signed)

Each claim line is allocated **its own CLP04**.  Each PLB entry becomes its own negative
allocation, attributed to the episode its reference points at.  The two sets sum to the
deposit exactly, by construction, and the recoupment stays attached to the claim that caused
it.

An unreferenced PLB has no episode to attribute to, so it lands as a ``RESIDUAL`` row with a
null episode.  That is D-7, the allocation residual, and it is money that has to be tracked
as unexplained rather than silently absorbed.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Sequence

from recon.domain.enums import AllocationBasis, RecordKind
from recon.money import round_half_up

__all__ = [
    "AllocationPlan",
    "plan_remittance_allocation",
    "plan_rebate_allocation",
    "largest_remainder_split",
]


@dataclass(frozen=True, slots=True)
class AllocationPlan:
    """What a deposit resolved to, line by line.

    ``rows`` are ready for ``repository.insert_cash_allocations``.  ``residual_cents`` is the
    part that could not be attributed to any episode — it is already included in ``rows`` as
    a ``RESIDUAL`` entry, and surfaced separately so a caller can assert on it.
    """

    rows: tuple[dict[str, object], ...]
    allocated_cents: int
    residual_cents: int

    def balances_to(self, deposit_cents: int) -> bool:
        """Whether the allocation accounts for the deposit exactly.

        Must always be true.  Integer cents and an exact identity mean there is no tolerance
        to apply: an allocation that does not balance is a bug, not a rounding artefact.
        """
        return self.allocated_cents == deposit_cents


def plan_remittance_allocation(
    conn: sqlite3.Connection,
    *,
    bank_norm_id: int,
    remittance_norm_id: int,
    deposit_cents: int,
    basis: AllocationBasis,
    caused_by_received_at: str,
) -> AllocationPlan:
    """Allocate one deposit across the remittance it points at.

    Hop two of two.  The claim lines and PLB entries are the remittance's own children, so
    finding them is a single indexed lookup on ``parent_norm_id`` — not a scan, and not a
    search for claims whose amounts look plausible.
    """
    children = conn.execute(
        "SELECT norm_id, record_kind, amount_cents, canonical"
        "  FROM normalized_record"
        " WHERE parent_norm_id = ?"
        " ORDER BY norm_id",
        (remittance_norm_id,),
    ).fetchall()

    # What the remittance itself says should land: Σ(CLP04) − Σ(PLB, signed).  That is the 835's
    # own identity, so the deposit should equal it exactly.
    claim_children = [
        child
        for child in children
        if RecordKind(child["record_kind"]) is RecordKind.REMITTANCE_CLAIM_LINE
    ]
    plb_signed_total = sum(
        (child["amount_cents"] or 0)
        for child in children
        if RecordKind(child["record_kind"]) is RecordKind.PROVIDER_LEVEL_ADJUSTMENT
    )
    claim_total = sum((child["amount_cents"] or 0) for child in claim_children)
    declared_bpr = claim_total - plb_signed_total

    # ═══ The distinction that makes or breaks this allocator ═══════════════════════
    #
    # A deposit can fall short of Σ(CLP04) for two completely different reasons, and treating
    # them the same way destroys one of them.
    #
    # **Netted.** The payer recovered an earlier claim out of this batch.  The claims in *this*
    # batch were each paid in full; the PLB explains the gap.  Spreading that shortfall across
    # them pro rata would convert one traceable recoupment into forty phantom underpayments,
    # every one indistinguishable from a real payment shortfall.
    #
    # **Unexplained.** The deposit is short of what the remittance itself declares, and nothing
    # in the remittance accounts for it.  Here the shortfall genuinely *is* per-claim: every
    # claim in the batch received less than the payer said it would, and reporting each one at
    # its full CLP04 would hide a real cash gap behind the payer's own paperwork.
    #
    # So: allocate full CLP04 when the deposit matches the declared BPR, and spread the
    # difference when it does not.
    unexplained_shortfall = declared_bpr - deposit_cents
    spread: dict[int, int] = {}
    if unexplained_shortfall != 0 and claim_total > 0:
        shares = largest_remainder_split(
            deposit_cents + plb_signed_total, [child["amount_cents"] or 0 for child in claim_children]
        )
        spread = {
            child["norm_id"]: share for child, share in zip(claim_children, shares)
        }

    rows: list[dict[str, object]] = []
    allocated = 0
    residual = 0

    for child in children:
        kind = RecordKind(child["record_kind"])
        amount = child["amount_cents"] or 0
        if kind is RecordKind.REMITTANCE_CLAIM_LINE and child["norm_id"] in spread:
            amount = spread[child["norm_id"]]

        if kind is RecordKind.REMITTANCE_CLAIM_LINE:
            # The claim was paid what the claim was paid.  Full CLP04, attributed to whichever
            # episode this line resolved to — which may be nothing, if the crosswalk missed.
            episode_id = _episode_for_child(conn, child["norm_id"])
            rows.append(
                {
                    "bank_norm_id": bank_norm_id,
                    "remittance_norm_id": remittance_norm_id,
                    "episode_id": episode_id,
                    "allocated_cents": amount,
                    "basis": str(basis if episode_id else AllocationBasis.RESIDUAL),
                    "caused_by_received_at": caused_by_received_at,
                }
            )
            allocated += amount
            if episode_id is None:
                # Paid money we cannot attribute to a claim: a crosswalk miss with cash
                # attached, which is materially worse than a crosswalk miss without.
                residual += amount

        elif kind is RecordKind.PROVIDER_LEVEL_ADJUSTMENT:
            # Signed on the wire: positive reduces the deposit.  Negating it here is what
            # makes the allocation sum to BPR rather than to Σ(CLP04).
            episode_id = _episode_for_child(conn, child["norm_id"])
            rows.append(
                {
                    "bank_norm_id": bank_norm_id,
                    "remittance_norm_id": remittance_norm_id,
                    "episode_id": episode_id,
                    "allocated_cents": -amount,
                    "basis": str(basis if episode_id else AllocationBasis.RESIDUAL),
                    "caused_by_received_at": caused_by_received_at,
                }
            )
            allocated += -amount
            if episode_id is None:
                residual += -amount

    # Anything left is the deposit disagreeing with its own remittance — which should be
    # impossible given the BPR identity, and is therefore worth recording loudly rather than
    # absorbing.  A partial deposit (cash arrived short) lands here.
    unexplained = deposit_cents - allocated
    if unexplained != 0:
        rows.append(
            {
                "bank_norm_id": bank_norm_id,
                "remittance_norm_id": remittance_norm_id,
                "episode_id": None,
                "allocated_cents": unexplained,
                "basis": str(AllocationBasis.RESIDUAL),
                "caused_by_received_at": caused_by_received_at,
            }
        )
        allocated += unexplained
        residual += unexplained

    return AllocationPlan(
        rows=tuple(rows), allocated_cents=allocated, residual_cents=residual
    )


def plan_rebate_allocation(
    conn: sqlite3.Connection,
    *,
    bank_norm_id: int,
    batch_norm_id: int,
    deposit_cents: int,
    basis: AllocationBasis,
    caused_by_received_at: str,
) -> AllocationPlan:
    """Allocate a rebate deposit across the batch's dispense lines.

    The same splitter, a second consumer.  That is the real payoff of modelling batched
    rebates (Decision A17): the bank feed forces an allocator into existence anyway, and the
    rebate side gets it for free rather than growing a parallel one.

    Unlike a claim remittance, a rebate batch has no PLB — the total genuinely is the sum of
    its lines.  So when a deposit is short, the shortfall is real and gets spread
    proportionally: there is no netting mechanism to explain it away, and a partial rebate
    payment is a unit-or-price dispute affecting every dispense in the batch.
    """
    lines = conn.execute(
        "SELECT norm_id, amount_cents FROM normalized_record"
        " WHERE parent_norm_id = ? AND record_kind = ?"
        " ORDER BY norm_id",
        (batch_norm_id, str(RecordKind.REBATE_DISPENSE_LINE)),
    ).fetchall()

    declared = [(row["norm_id"], row["amount_cents"] or 0) for row in lines]
    declared_total = sum(amount for _, amount in declared)

    if declared_total == deposit_cents or declared_total == 0:
        shares = [amount for _, amount in declared]
    else:
        # Largest-remainder so the parts sum to the whole exactly.  Plain rounding would
        # leave a cent or two stranded, and a stranded cent is indistinguishable from a real
        # variance on a claim worth thousands.
        shares = largest_remainder_split(
            deposit_cents, [amount for _, amount in declared]
        )

    rows: list[dict[str, object]] = []
    allocated = 0
    residual = 0
    for (norm_id, _), share in zip(declared, shares):
        episode_id = _episode_for_child(conn, norm_id)
        rows.append(
            {
                "bank_norm_id": bank_norm_id,
                "remittance_norm_id": batch_norm_id,
                "episode_id": episode_id,
                "allocated_cents": share,
                "basis": str(basis if episode_id else AllocationBasis.RESIDUAL),
                "caused_by_received_at": caused_by_received_at,
            }
        )
        allocated += share
        if episode_id is None:
            # Rebate cash that resolves to no dispense we know about: D-4, the unmatched
            # rebate.  Not a windfall — cash held without provenance is audit exposure.
            residual += share

    unexplained = deposit_cents - allocated
    if unexplained != 0:
        rows.append(
            {
                "bank_norm_id": bank_norm_id,
                "remittance_norm_id": batch_norm_id,
                "episode_id": None,
                "allocated_cents": unexplained,
                "basis": str(AllocationBasis.RESIDUAL),
                "caused_by_received_at": caused_by_received_at,
            }
        )
        allocated += unexplained
        residual += unexplained

    return AllocationPlan(
        rows=tuple(rows), allocated_cents=allocated, residual_cents=residual
    )


def correct_allocation(
    conn: sqlite3.Connection,
    *,
    parent_norm_id: int,
    caused_by_received_at: str,
) -> list[dict[str, object]]:
    """Re-derive a deposit's split now that more of its claim list is resolvable.

    ═══ Why a correction rather than a rewrite ═════════════════════════════════════

    A deposit can be allocated *before* the crosswalk knows which episodes its remittance's
    claims belong to — the deposit arrives, its trace number resolves to the remittance, and the
    claims themselves are still parked waiting for their own claim events.  Everything lands as
    unattributed residual, which is the right answer at that moment: the money is real, and we
    genuinely cannot yet say whose it is.

    When those claims later unpark, the earlier answer is stale.  But it is not *wrong* — it was
    correct on the evidence available, and the allocation table is append-only precisely so that
    what we believed at each point stays answerable.  So this appends a correction: a negating row
    against the residual and a positive row against the episode now known to own it.

    The invariant survives by construction: every correction is a matched pair summing to zero, so
    the total allocated against a bank row still equals the deposit.

    Returns the rows to append — empty when nothing changed, which is the common case.
    """
    allocations = conn.execute(
        "SELECT bank_norm_id, episode_id, basis, SUM(allocated_cents) AS total"
        "  FROM cash_allocation"
        " WHERE remittance_norm_id = ?"
        " GROUP BY bank_norm_id, episode_id, basis",
        (parent_norm_id,),
    ).fetchall()
    if not allocations:
        return []

    bank_rows = sorted({row["bank_norm_id"] for row in allocations})
    kind_row = conn.execute(
        "SELECT record_kind FROM normalized_record WHERE norm_id = ?", (parent_norm_id,)
    ).fetchone()
    if kind_row is None:
        return []
    is_rebate = RecordKind(kind_row["record_kind"]) is RecordKind.REBATE_BATCH

    corrections: list[dict[str, object]] = []
    for bank_norm_id in bank_rows:
        current: dict[tuple[str | None, str], int] = {}
        deposit = 0
        for row in allocations:
            if row["bank_norm_id"] != bank_norm_id:
                continue
            current[(row["episode_id"], row["basis"])] = row["total"]
            deposit += row["total"]

        # The basis the deposit originally resolved on, so a correction does not silently
        # re-label a trace-number match as an amount-and-date one.
        original_basis = next(
            (
                AllocationBasis(row["basis"])
                for row in allocations
                if row["bank_norm_id"] == bank_norm_id
                and row["basis"] != str(AllocationBasis.RESIDUAL)
            ),
            AllocationBasis.TRN02,
        )

        planner = plan_rebate_allocation if is_rebate else plan_remittance_allocation
        kwargs = (
            {"batch_norm_id": parent_norm_id}
            if is_rebate
            else {"remittance_norm_id": parent_norm_id}
        )
        plan = planner(
            conn,
            bank_norm_id=bank_norm_id,
            deposit_cents=deposit,
            basis=original_basis,
            caused_by_received_at=caused_by_received_at,
            **kwargs,
        )

        desired: dict[tuple[str | None, str], int] = {}
        for row in plan.rows:
            key = (row["episode_id"], row["basis"])
            desired[key] = desired.get(key, 0) + int(row["allocated_cents"])

        for key in sorted(set(current) | set(desired), key=lambda k: (k[0] or "", k[1])):
            delta = desired.get(key, 0) - current.get(key, 0)
            if delta == 0:
                continue
            episode_id, basis = key
            corrections.append(
                {
                    "bank_norm_id": bank_norm_id,
                    "remittance_norm_id": parent_norm_id,
                    "episode_id": episode_id,
                    "allocated_cents": delta,
                    "basis": basis,
                    "caused_by_received_at": caused_by_received_at,
                }
            )
    return corrections


def largest_remainder_split(total: int, weights: Sequence[int]) -> list[int]:
    """Split ``total`` in proportion to ``weights``, summing to ``total`` exactly.

    Integer arithmetic throughout.  Each share is first floored, then the leftover cents go
    one each to the largest fractional remainders, tie-broken by position so the result is
    deterministic.

    Exactness matters more than fairness here: this is the difference between an allocation
    that balances and one that leaves a phantom variance on an arbitrary claim.
    """
    weight_total = sum(weights)
    if weight_total == 0:
        # Nothing to apportion against. Give it all to the first line rather than dropping
        # it, so the money stays accounted for.
        return [total] + [0] * (len(weights) - 1) if weights else []

    floors: list[int] = []
    remainders: list[tuple[int, int]] = []
    for index, weight in enumerate(weights):
        exact_numerator = total * weight
        floor_value = exact_numerator // weight_total
        floors.append(floor_value)
        remainders.append((exact_numerator - floor_value * weight_total, index))

    leftover = total - sum(floors)
    # Negative leftover is possible when total is negative; walk the other way.
    step = 1 if leftover >= 0 else -1
    remainders.sort(key=lambda pair: (-pair[0], pair[1]))
    for offset in range(abs(leftover)):
        floors[remainders[offset % len(remainders)][1]] += step
    return floors


def _episode_for_child(conn: sqlite3.Connection, norm_id: int) -> str | None:
    """Which episode a remittance child resolved to, if any.

    Read from the crosswalk rather than recomputed, because the crosswalk is the record of
    what the connector actually managed to resolve — and the whole point is to be able to
    tell a successful match from a failed one.
    """
    row = conn.execute(
        "SELECT episode_id FROM crosswalk_key"
        " WHERE resolved_from_norm_id = ? AND episode_id IS NOT NULL"
        " LIMIT 1",
        (norm_id,),
    ).fetchone()
    return None if row is None else row["episode_id"]
