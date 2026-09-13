"""The realizer: the only place in the codebase where cash faults live.

A payer-side generator *declares* money is leaving (:class:`MoneyMovement`).  The realizer
decides what actually landed (:class:`CashEvent`).  The bank generator then formats
whatever it is handed, knowing nothing.

Concentrating the faults here is what makes the bank feed honest.  If the bank generator
decided that a deposit was missing, it would have to be told what was expected — and a
generator that knows the expected figure can leak it.  Instead, an absent deposit is
simply an event that was never created, which is indistinguishable from "no such payment"
exactly as it is for a real operator.

═══ Why cash outcome is a property of the *batch* ══════════════════════════════════

The decision tree declares cash per episode (``cash_reimb_in = ABSENT``), but a deposit is
per batch — one ACH covering dozens of claims.  A single deposit amount cannot be
simultaneously present for one claim and absent for another.

So batch composition is driven by cash outcome: the orchestrator groups episodes by
``(payer, cycle, cash outcome)``, and every claim in a batch shares the batch's fate.  That
is also what really happens — a payer's settlement either arrives or it does not, and when
it arrives short, every claim in it is short.  The grouping lives in the orchestrator
because batch composition is orchestrator machinery (Decision 44); the realizer is handed
one outcome per movement and applies it.

═══ The faults, and what each one produces ════════════════════════════════════════

``MATCHED``   the deposit lands for the declared amount.
``PARTIAL``   the deposit lands short, with no bank-side explanation.
``ABSENT``    no bank row at all — verdicts A-05, B-05, C-09.
addenda loss  ~20% of credits lose ``trn02``, forcing amount-plus-date matching.
D-3 orphan    a credit attributable to nothing, parked until something explains it.
true reversal a genuine ACH debit, within five banking days of its credit — rare and
              clean, and not to be confused with the far more common netting, where the
              payer simply pays less later and the bank never sees a reversal at all.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal, Sequence

from recon.generators.contracts import CashEvent, MoneyMovement
from recon.generators.timeline import stamp
from recon.money import apply_bps
from recon.reference import calendar, entities
from recon.rng import rng_for, stable_choice

__all__ = [
    "CashOutcome",
    "DeclaredMovement",
    "realize",
    "ADDENDA_LOSS_BPS",
    "ORPHAN_DEPOSIT_BPS",
    "PARTIAL_CASH_BPS",
]

#: The one sourced injection rate in the whole generator: roughly 20% of bank credits lose
#: their CCD+ addenda, and therefore their ``trn02``.  Applies to rebate deposits too
#: (Decision 42), because the addenda either survive the trip or they do not and the
#: issuer makes no difference to that.
ADDENDA_LOSS_BPS = 2_000
#: D-3: cash with nothing to attribute it to.  Parked, never recognised as revenue.
ORPHAN_DEPOSIT_BPS = 200
#: How short a ``PARTIAL`` deposit lands.
PARTIAL_CASH_BPS = 6_000

CashOutcome = Literal["MATCHED", "PARTIAL", "ABSENT"]


@dataclass(frozen=True, slots=True)
class DeclaredMovement:
    """A movement a generator declared, plus the fate the orchestrator assigned it."""

    movement: MoneyMovement
    outcome: CashOutcome
    #: Set for a genuine ACH reversal: the credit is followed by a matching debit within
    #: five banking days.  Distinct from netting, where nothing appears on the bank side.
    reversal_outcome: CashOutcome | None = None


def realize(
    declared: Sequence[DeclaredMovement],
    *,
    master_seed: int,
    profile_name: str,
    curated: bool = False,
) -> list[CashEvent]:
    """Turn declared movements into the bank rows that actually exist.

    ``curated`` switches the ``demo`` profile's behaviour: defects are hand-placed exactly
    once each rather than sampled at a rate, so a 60-episode walkthrough reliably contains
    one of everything instead of whatever 20% of 60 happened to be.
    """
    events: list[CashEvent] = []
    addenda_dropped = 0

    for index, item in enumerate(declared):
        movement = item.movement
        rng = rng_for(master_seed, profile_name, "cash", index)

        if item.outcome == "ABSENT":
            # No row at all.  This is the entire content of "remittance with no cash":
            # the payer's paperwork and the payer's money disagree, and the only evidence
            # is an absence.
            continue

        amount = movement.amount_cents
        if item.outcome == "PARTIAL":
            amount = amount - apply_bps(amount, PARTIAL_CASH_BPS)

        # In curated mode drop the addenda on exactly every fifth credit, so the demo
        # profile contains the defect deterministically rather than by luck.
        if curated:
            drop_addenda = index % 5 == 4
        else:
            drop_addenda = rng.randint(0, 9_999) < ADDENDA_LOSS_BPS

        # Never stack addenda loss on a short deposit.
        #
        # They are independent defects and each is worth observing on its own.  Together they
        # produce a deposit with no business reference *and* an amount matching nothing, which is
        # indistinguishable from an orphan (D-3) — so the compound masks both defects it is made
        # of: the lost addenda can no longer be shown to force amount-plus-date matching, and the
        # short payment can no longer be shown to surface as a cash variance.  Orphans are
        # injected deliberately and separately, so the compound adds no coverage and removes two
        # kinds of it.
        if item.outcome == "PARTIAL":
            drop_addenda = False

        if drop_addenda:
            addenda_dropped += 1

        posting = _posting_date(movement.effective_date, rng)
        events.append(
            CashEvent(
                posting_date=posting.isoformat(),
                amount_cents=amount,
                company_name=movement.company_name,
                company_id=movement.company_id,
                ach_routing_prefix=movement.ach_routing_prefix,
                entry_description=movement.entry_description,
                direction=movement.direction,
                received_at=stamp(posting, hour=17, minute=rng.randint(45, 59)),
                payer_reference=None if drop_addenda else movement.payer_reference,
                movement_ref=movement.slice_ref,
            )
        )

        # A true ACH reversal: a debit for the same amount, inside five banking days of the
        # credit, carrying the same payer reference.  Rare and clean.
        if item.reversal_outcome == "MATCHED":
            reversal_posting = calendar.add_banking_days(posting, rng.randint(1, 5))
            events.append(
                CashEvent(
                    posting_date=reversal_posting.isoformat(),
                    amount_cents=-amount,
                    company_name=movement.company_name,
                    company_id=movement.company_id,
                    ach_routing_prefix=movement.ach_routing_prefix,
                    entry_description=movement.entry_description,
                    direction="DEBIT",
                    received_at=stamp(reversal_posting, hour=17, minute=rng.randint(40, 55)),
                    payer_reference=None if drop_addenda else movement.payer_reference,
                    movement_ref=movement.slice_ref,
                )
            )

    events.extend(
        _orphan_deposits(
            events, master_seed=master_seed, profile_name=profile_name, curated=curated
        )
    )
    return events


def _posting_date(effective_date: str, rng: random.Random) -> date:
    """When the money actually appeared.

    On or after the effective date, never before, and always a banking day.  The effective
    date is the one genuinely forward-looking value in the dataset — a settlement
    instruction already transmitted to the ACH network — and by the time it becomes a
    posting date it has resolved into an ordinary backward-looking fact.
    """
    effective = date.fromisoformat(effective_date)
    return calendar.add_banking_days(effective, rng.randint(0, 1))


def _orphan_deposits(
    existing: Sequence[CashEvent],
    *,
    master_seed: int,
    profile_name: str,
    curated: bool,
) -> list[CashEvent]:
    """Synthesise D-3: credits with no attributable claim, remittance or rebate batch.

    Not a windfall.  Cash held without provenance is audit exposure, and it may well be a
    crosswalk failure rather than genuinely unattributable money — which is exactly why the
    connector parks it rather than writing it off.

    Given no payer reference and an amount that matches no movement, there is nothing for a
    connector to resolve it against.  That is the point.
    """
    if not existing:
        return []
    rng = rng_for(master_seed, profile_name, "orphan")
    count = 2 if curated else max(1, len(existing) * ORPHAN_DEPOSIT_BPS // 10_000)

    payers = list(entities.pbms()) + list(entities.medical_payers())
    orphans: list[CashEvent] = []
    for index in range(count):
        template = existing[rng.randrange(len(existing))]
        payer = stable_choice(rng, payers)
        posting = date.fromisoformat(template.posting_date) + timedelta(days=rng.randint(1, 20))
        posting = calendar.add_banking_days(posting, 0)
        orphans.append(
            CashEvent(
                posting_date=posting.isoformat(),
                # An amount deliberately unlike any declared movement, so amount+date
                # rescue cannot accidentally resolve it either.
                amount_cents=rng.randrange(17_000, 930_000),
                company_name=payer.name[:16],
                company_id=payer.company_id,
                ach_routing_prefix=payer.ach_routing_prefix,
                entry_description="HCCLAIMPMT",
                direction="CREDIT",
                received_at=stamp(posting, hour=18, minute=rng.randint(0, 30)),
                payer_reference=None,
                movement_ref=None,
            )
        )
    return orphans
