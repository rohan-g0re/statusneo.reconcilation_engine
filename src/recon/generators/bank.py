"""The bank generator: a pure formatter, and deliberately the poorest feed.

A real business bank statement export gives you what a bank gives you and nothing more:
a date, a description, an amount, a type, a running balance, and a bank trace number.  It
is a CSV, not an EDI standard, and it was never designed to carry claims data — it was
designed to reconcile a checking account.  This feed is the weakest link in the dataset
because it is the weakest link in the real workflow it models.

═══ Why this generator is *pure* ═══════════════════════════════════════════════════

It takes ``CashEvent[]`` and writes rows.  That is all.  It never sees an expected figure,
never learns that a deposit is missing, and has no idea what a remittance is.

That is the whole design (Decision 44).  An absent deposit is not a flag this generator
sets — it is a ``CashEvent`` the realizer never created.  So "remittance with no cash"
becomes a property of the data rather than an annotation, and a connector reading only
this feed is in exactly the position a real operator is in: it cannot tell the difference
between *no deposit* and *no such payment*.

═══ Two identifiers that only sometimes make the same trip ═════════════════════════

Every ACH credit carries a 15-digit **ACH trace number** — eight digits of the originating
bank's routing number plus a seven-digit sequence.  It is pure plumbing: it identifies the
transfer to the banking network, carries zero business content, and is **always present**,
because the network cannot move money without one.  This generator mints it, because the
bank mints it.

``trn02`` is a completely different number, assigned by the *payer*, carrying real
business content: the thread back to which remittance a deposit belongs to.  It survives
to the statement only if the CCD+ addenda made the whole trip intact.  When it is absent
here, the realizer decided it was lost in transit — and all that is left for matching is
amount and date.

These are two unrelated numbering systems that happen to ride the same wire transfer.  A
connector that assumes they are interchangeable breaks the day one of them is missing,
which by design happens often.

═══ The running balance ════════════════════════════════════════════════════════════

Computed here, cumulatively over posting order, because it is the bank's own field and
only the bank could know it.  It is also the one field in the dataset that is genuinely
redundant — a reconciliation never needs it — and it is carried anyway because a real
export has it and leaving it out would make the feed look tidier than reality.
"""

from __future__ import annotations

import csv
import io
from typing import Iterable, Sequence

from recon.config import BANK_TRANSACTIONS_FILE
from recon.generators.contracts import CashEvent, Record
from recon.money import format_amount

__all__ = ["gen_bank", "BANK_CSV_COLUMNS", "write_bank_csv", "OPENING_BALANCE_CENTS"]

#: The column order, fixed here as the single authority.  ``feed_formats.md`` §4's worked
#: example is the source; a reordering would silently break a positional CSV reader.
BANK_CSV_COLUMNS: tuple[str, ...] = (
    "posting_date",
    "description",
    "ach_trace_number",
    "trn02",
    "company_name",
    "company_id",
    "company_entry_description",
    "amount",
    "type",
    "running_balance",
    "received_at",
)

#: A plausible opening balance for a specialty pharmacy's operating account.  Arbitrary,
#: and it has to be *something* for the running balance to be cumulative rather than
#: starting at zero on the first deposit of the year.
OPENING_BALANCE_CENTS = 124_000_000


def gen_bank(events: Sequence[CashEvent]) -> list[Record]:
    """Format cash events as bank rows, in posting order.

    Sorted by ``(posting_date, received_at)`` because a statement is chronological and the
    running balance only means anything in that order.  The sort is stable on the incoming
    sequence, so two deposits posting the same day keep the order the realizer produced —
    which keeps the feed byte-identical across runs.
    """
    ordered = sorted(
        enumerate(events), key=lambda pair: (pair[1].posting_date, pair[1].received_at, pair[0])
    )

    balance = OPENING_BALANCE_CENTS
    records: list[Record] = []
    for sequence, (_, event) in enumerate(ordered, start=1):
        balance += event.amount_cents
        payload = {
            "posting_date": event.posting_date,
            "description": "ACH CREDIT" if event.direction == "CREDIT" else "ACH DEBIT",
            "ach_trace_number": _mint_ach_trace_number(event.ach_routing_prefix, sequence),
            # Empty string, not the literal "null": a CSV export writes an empty cell.
            # This is the ~20% addenda loss, and it is the whole reason TRN02 matters.
            "trn02": event.payer_reference or "",
            "company_name": event.company_name,
            "company_id": event.company_id,
            "company_entry_description": event.entry_description,
            "amount": format_amount(event.amount_cents),
            "type": event.direction,
            "running_balance": format_amount(balance),
            "received_at": event.received_at,
        }
        records.append(
            Record(
                feed_file=BANK_TRANSACTIONS_FILE,
                payload=payload,
                # The movement ref is how the orchestrator attributes this row for ground
                # truth.  It is not written to the CSV: a bank statement has no concept of
                # a remittance, and putting one here would hand the connector a free join.
                slice_ref=event.movement_ref or "",
            )
        )
    return records


def _mint_ach_trace_number(routing_prefix: str, sequence: int) -> str:
    """Eight digits of the originating bank's routing number, then a 7-digit sequence.

    Always present.  The network cannot move money without one, so this is the only
    identifier on the feed that never goes missing — and it is also the one that tells you
    nothing about which claim the money was for.
    """
    return f"{routing_prefix}{sequence % 10_000_000:07d}"


def write_bank_csv(records: Iterable[Record]) -> str:
    """Render bank rows as CSV text.

    ``\\n`` line endings explicitly, rather than letting :mod:`csv` default to ``\\r\\n``
    on Windows — otherwise the same seed produces two different file hashes on two
    different platforms and the reproducibility manifest becomes a lie.
    """
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(
        buffer, fieldnames=list(BANK_CSV_COLUMNS), lineterminator="\n", extrasaction="raise"
    )
    writer.writeheader()
    for record in records:
        writer.writerow(record.payload)
    return buffer.getvalue()
