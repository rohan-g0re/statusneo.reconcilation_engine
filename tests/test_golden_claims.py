"""Doc 2's four golden claims, each traced from the source file to the ledger.

═══ What this file is for ══════════════════════════════════════════════════════════

Assignment Doc 2 step 5 says *"Trace representative paid, rejected, reversed and
unmatched claims end-to-end."*  Its own working-connection bar is lower — "at least one
golden claim can be traced end-to-end" — and step 5 raises it to four archetypes.  That
is requirement **F1** in ``docs/connectivity_layer_requirements.md``, and it is the
thing that makes this build claimable rather than merely built.

Four tests, one per archetype, each asserting figures at **five** stages:

    source file → raw_record → normalized_record → episode → verdict → ledger

**If you delete this file**, every other test in the suite still passes while the
connector quietly loses money between two stages.  ``test_end_to_end.py`` scores
*verdicts* against what the generator intended, so it catches a wrong conclusion; it
never checks that the 22,393.91 dollars written in ``pbm_claim_events.jsonl`` is the
same 2,239,391 cents that reached ``cash_allocation``.  The scenario suite checks that
carry-through, but only on hand-authored records it wrote itself.  This file is the
only place where one number is followed across all five stages of a claim the
*generator* produced, in the dataset a reviewer is actually shown.

═══ The ledger ═════════════════════════════════════════════════════════════════════

There is no table called ``ledger``.  The reconciliation ledger in this schema is
``cash_allocation``: the record of settled bank money attributed — in two hops, deposit
→ remittance → episode — to the claim it paid.  It is the last stage because it is the
only one that talks about money that actually moved, as opposed to money a document
claimed.  ``schema.sql`` calls it out as "a bank line resolves in two hops".

═══ Which profile, and why ═════════════════════════════════════════════════════════

``full``.  Two reasons, and the second is the decisive one.

1. Coverage, with room to spare.  ``full`` is ``VERDICT_STRATIFIED`` over 1,500
   episodes and reaches all 372 verdict pairs, so each archetype below has dozens of
   candidates and every test can pick by *property* instead of settling for whatever the
   profile happened to contain.  ``demo`` is 60 episodes and, measured rather than
   assumed, holds exactly one candidate for two of these four.  One is not a margin: the
   ``MIXED`` spine samples its remaining slots from a pool wider than the slots, so two
   seeds legitimately disagree about the mix (``config.CuratedSpine``), and an archetype
   with a single candidate is one rebuild away from having none.
2. ``demo`` is frozen.  ``CuratedSpine.RECORDED`` pins the composition the agent-trace
   fixtures were recorded against, and ``ReplayClient`` keys those recordings on a
   digest of the dossier contents.  A test that reaches into the demo spine invites a
   future author to adjust that spine to suit it, and re-recording needs a live model
   and an API key.  ``full`` has no such hostage.

It costs about five seconds to build, once, in a module-scoped fixture.

═══ How a claim is chosen ══════════════════════════════════════════════════════════

Never by a hardcoded episode id.  A hardcoded id drifts to a different claim the next
time the composition moves, and the test then passes while testing something else.
Each test states the *property* that makes a claim its archetype, asserts at least one
claim has it, and takes the lowest id so the choice is reproducible.  The selecting
property is named in each test's docstring.

═══ Where the vocabulary comes from ════════════════════════════════════════════════

Nothing here invents what a code means.  The four archetype names are Doc 2's, spelled
as :meth:`recon.mocks.source.Dispense.archetype` spells them.  Every verdict code is
quoted from :mod:`recon.domain.verdicts`, every reason code from
:class:`recon.domain.enums.ReasonCode`, every park reason from
:class:`recon.domain.enums.ParkReason`, and the rule that produces each verdict code is
named in :mod:`recon.engine.verdicts`.  The citations are in the test docstrings.

═══ Two standing prohibitions ══════════════════════════════════════════════════════

* **Ground truth is never read.**  Not ``truth/``, not ``PipelineRun.ground_truth``.
  Every figure below is read from the six feed files, from the database the connector
  built out of them, or from :mod:`recon.reference.pricing` — the one authority the
  generator and the engine deliberately share.  A golden-claim trace that consulted
  ground truth would be checking the generator's intent, not the connector's arithmetic.
* **Money is carried, not hardcoded.**  Each test reads the amount out of the source
  file's own payload and then asserts every later stage equals it.  Structure, codes and
  counts are asserted with literals; money never is.  That is the same rule
  ``tests/scenario.py`` states, and it is what makes these assertions survive a change to
  a contracted rate while still failing on a lost cent.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from conftest_pipeline import PipelineRun, build_pipeline  # noqa: E402

from recon.domain.enums import ParkReason, ReasonCode, RecordKind  # noqa: E402
from recon.money import to_cents  # noqa: E402
from recon.reference import pricing  # noqa: E402

#: The one crosswalk key a pharmacy 340B dispense joins on, canonical form
#: ``pharmacy_npi|rx_number|ndc11|fill_date`` (``domain.enums.KeyType``).
NATURAL_340B_PHARMACY = "NATURAL_340B_PHARMACY"


@pytest.fixture(scope="module")
def golden(tmp_path_factory) -> PipelineRun:
    """One ``full`` build, shared by all four traces.  See the module docstring."""
    return build_pipeline("full", tmp_path_factory.mktemp("golden-claims"))


# ═══ stage helpers ══════════════════════════════════════════════════════════
#
# Each returns rows, never assertions, so a failure message can name the figures.


def _raw(run: PipelineRun, raw_id: int) -> sqlite3.Row:
    """One raw row plus the file and line it came in on."""
    row = run.conn.execute(
        "SELECT r.raw_id, r.source_record_id, r.source_line_no, r.payload, r.received_at,"
        "       b.source_file, b.source_system"
        "  FROM raw_record r JOIN ingest_batch b ON b.batch_id = r.batch_id"
        " WHERE r.raw_id = ?",
        (raw_id,),
    ).fetchone()
    assert row is not None, f"raw_id {raw_id} does not exist"
    return row


def _payload(raw_row: sqlite3.Row) -> dict:
    """The verbatim JSONL line, parsed.

    ``parse_float=Decimal`` is not a nicety: :func:`recon.money.to_cents` refuses a bare
    float outright, because a binary float round-trip through ``6749.75`` leaves a residue
    indistinguishable from a real cash variance.  Reading the source file any other way
    would put the defect this project exists to detect inside the test that checks for it.
    """
    return json.loads(raw_row["payload"], parse_float=Decimal)


def _assert_raw_is_the_source_file_line(run: PipelineRun, raw_row: sqlite3.Row) -> None:
    """Stage 0 → 1: the raw row is the file's own bytes, not a re-rendering of them.

    This is what lets every later assertion say "the source file says" rather than "the
    database says".  JSONL only — a bank CSV row is re-encoded as JSON on the way in and
    has no verbatim line to compare against, which ``schema.sql`` states on ``raw_record``.
    """
    source_file = raw_row["source_file"]
    assert source_file.endswith(".jsonl"), (
        f"{source_file} is not JSONL; only a JSONL feed has a verbatim line to compare"
    )
    path = run.settings.feed_path(source_file)
    lines = path.read_text(encoding="utf-8").splitlines()
    line_no = raw_row["source_line_no"]
    assert 1 <= line_no <= len(lines), (
        f"{source_file} has {len(lines)} lines; raw_id {raw_row['raw_id']} claims line {line_no}"
    )
    assert lines[line_no - 1] == raw_row["payload"], (
        f"raw_record {raw_row['raw_id']} does not hold {source_file} line {line_no} verbatim; "
        "the raw layer is supposed to be the bytes as received"
    )


def _normalized(run: PipelineRun, norm_id: int) -> sqlite3.Row:
    row = run.conn.execute(
        "SELECT * FROM normalized_record WHERE norm_id = ?", (norm_id,)
    ).fetchone()
    assert row is not None, f"norm_id {norm_id} does not exist"
    return row


def _episode(run: PipelineRun, episode_id: str) -> sqlite3.Row:
    row = run.conn.execute(
        "SELECT * FROM episode WHERE episode_id = ?", (episode_id,)
    ).fetchone()
    assert row is not None, f"episode {episode_id} does not exist"
    return row


def _verdict(run: PipelineRun, episode_id: str) -> sqlite3.Row:
    """The verdict standing at the run's cursor.

    ``ix_verdict_latest`` orders by ``(episode_id, cursor_at DESC, verdict_id DESC)``, and
    this reads the same way: the log is append-only, so "the verdict" always means the last
    one written for that episode at or before the cursor.
    """
    row = run.conn.execute(
        "SELECT * FROM verdict WHERE episode_id = ? AND cursor_at <= ?"
        " ORDER BY cursor_at DESC, verdict_id DESC LIMIT 1",
        (episode_id, run.settings.max_cursor),
    ).fetchone()
    assert row is not None, f"episode {episode_id} has no verdict at the cursor"
    return row


def _reasons(run: PipelineRun, verdict_id: int) -> list[str]:
    return [
        row["reason_code"]
        for row in run.conn.execute(
            "SELECT reason_code FROM verdict_reason WHERE verdict_id = ? ORDER BY ordinal",
            (verdict_id,),
        )
    ]


def _evidence(run: PipelineRun, verdict_id: int) -> list[sqlite3.Row]:
    """What the verdict cites, with each citation's own figures alongside it."""
    return run.conn.execute(
        "SELECT e.ordinal, e.role, e.norm_id, e.raw_id,"
        "       n.record_kind, n.amount_cents, n.status_code"
        "  FROM verdict_evidence e JOIN normalized_record n ON n.norm_id = e.norm_id"
        " WHERE e.verdict_id = ? ORDER BY e.ordinal",
        (verdict_id,),
    ).fetchall()


def _ledger(run: PipelineRun, episode_id: str, *, track: str | None = None) -> list[sqlite3.Row]:
    """``cash_allocation`` rows for one episode, optionally one track only.

    The track split is the engine's own, not a new one:
    :func:`recon.engine.dimensions.gather_evidence` calls an allocation a rebate allocation
    when the thing its deposit resolved to is a ``REBATE_BATCH``, and a reimbursement
    allocation otherwise.  Splitting them any other way here would let a test agree with
    itself while disagreeing with the engine.
    """
    sql = (
        "SELECT a.*, p.record_kind AS parent_kind, bank.amount_cents AS deposit_cents"
        "  FROM cash_allocation a"
        "  LEFT JOIN normalized_record p ON p.norm_id = a.remittance_norm_id"
        "  JOIN normalized_record bank ON bank.norm_id = a.bank_norm_id"
        " WHERE a.episode_id = ? AND a.caused_by_received_at <= ?"
    )
    params: list[object] = [episode_id, run.settings.max_cursor]
    if track == "REIMBURSEMENT":
        sql += " AND COALESCE(p.record_kind, '') <> ?"
        params.append(str(RecordKind.REBATE_BATCH))
    elif track == "REBATE":
        sql += " AND p.record_kind = ?"
        params.append(str(RecordKind.REBATE_BATCH))
    return run.conn.execute(sql + " ORDER BY a.allocation_id", params).fetchall()


def _one(rows: list[sqlite3.Row], what: str) -> sqlite3.Row:
    assert len(rows) == 1, f"expected exactly one {what}, got {len(rows)}"
    return rows[0]


def _pick(rows: list[sqlite3.Row], archetype: str, prop: str) -> sqlite3.Row:
    """The lowest-id candidate, or a failure that says what was being looked for."""
    assert rows, (
        f"the full profile contains no {archetype} golden claim: no record satisfies {prop}. "
        "Doc 2 step 5 requires all four archetypes, so this is a dataset defect, not a "
        "test that needs relaxing."
    )
    return rows[0]


def _rx_differs_only_in_rendering(feed_rx: str, episode_rx: str) -> bool:
    """Is ``feed_rx`` the same prescription number as ``episode_rx``, spelled differently?

    Defect D-6 renders one feed's ``rx_number`` either zero-padded or one character short
    (``generators/plan.py`` ``rendered_rx``), and ``normalized_record.rx_number`` keeps
    whatever the feed said, verbatim, because that miss *is* the exception (Decision A23).
    This is the test's own re-reading of the two spellings — never the connector's, which is
    forbidden from doing it, and never ground truth's, which is not allowed to be opened.
    """
    if feed_rx == episode_rx:
        return False
    if feed_rx.lstrip("0") == episode_rx.lstrip("0"):
        return True
    return episode_rx.startswith(feed_rx) and len(episode_rx) == len(feed_rx) + 1


# ═══ 1. PAID ════════════════════════════════════════════════════════════════


def test_golden_paid_claim_traces_from_the_feed_line_to_the_bank(golden: PipelineRun) -> None:
    """**paid** — the money arrived and the claim reconciles, and one figure proves it.

    *Archetype:* Doc 2 step 5's "paid".  *Selected by:* the lowest-numbered episode whose
    reimbursement verdict is ``A-04`` and whose episode disposition is ``CLOSED``.  ``A-04``
    is "Fully reconciled: paid in full, cash matched, settlement confirmed"
    (``domain/verdicts.py``); ``CLOSED`` on the episode means neither track has anything
    outstanding, so the claim is paid in the whole sense and not only on one leg.

    *What is asserted:* the ``total_amount_paid`` written in ``pbm_claim_events.jsonl`` is
    followed, as the same integer cent count, into the normalized claim, into the payer's
    own 835 claim line, into both money columns of the verdict, and into the bank
    allocation.  Five stages, one number.  Delete this and a connector could drop or alter
    that number between any two of them while every other test in the suite stays green.
    """
    conn = golden.conn
    picked = _pick(
        conn.execute(
            "SELECT episode_id FROM verdict"
            " WHERE reimbursement_verdict_code = 'A-04' AND episode_disposition = 'CLOSED'"
            "   AND cursor_at = ? ORDER BY episode_id",
            (golden.settings.max_cursor,),
        ).fetchall(),
        "paid",
        "reimbursement verdict A-04 with the episode CLOSED",
    )
    episode_id = picked["episode_id"]

    # ── stage 3: the episode, first, because it names the record the trace starts from ──
    episode = _episode(golden, episode_id)
    assert episode["reimbursement_track"] == "PHARMACY"

    # ── stage 1: raw, verbatim from the feed file ──
    anchor = _normalized(golden, episode["anchor_norm_id"])
    raw = _raw(golden, anchor["raw_id"])
    _assert_raw_is_the_source_file_line(golden, raw)
    payload = _payload(raw)
    assert raw["source_file"] == "pbm_claim_events.jsonl"
    assert payload["transaction_code"] == "B1"
    assert payload["response_status"] == "P", "a paid claim was accepted at adjudication"
    assert payload["reject_codes"] == []
    #: THE figure. Everything below is asserted against this and nothing else.
    paid_cents = to_cents(payload["total_amount_paid"])
    assert paid_cents > 0

    # ── stage 2: normalized ──
    assert anchor["record_kind"] == str(RecordKind.PHARMACY_CLAIM)
    assert anchor["amount_cents"] == paid_cents, (
        "the adapter changed the amount between the file and the canonical record"
    )
    assert anchor["status_code"] == "P"
    assert anchor["idempotency_key"] == raw["source_record_id"], (
        "idempotency is the source record id, never a natural key (schema.sql)"
    )
    assert anchor["rx_number"] == payload["prescription_ref_number"], (
        "rx_number is stored verbatim, exactly as the feed spelled it (Decision A23)"
    )

    # ── stage 3 continued: the episode carries the claim's identity, unchanged ──
    assert episode["pharmacy_npi"] == payload["service_provider_id"]
    assert episode["rx_number"] == payload["prescription_ref_number"]
    assert episode["fill_number"] == payload["fill_number"]
    assert episode["ndc11"] == payload["product_service_id"]
    assert episode["quantity_milli"] == int(payload["quantity_dispensed"])
    assert _one(
        conn.execute(
            "SELECT episode_id FROM episode"
            " WHERE pharmacy_npi = ? AND rx_number = ? AND fill_number = ? AND date_of_service = ?",
            (
                episode["pharmacy_npi"],
                episode["rx_number"],
                episode["fill_number"],
                episode["date_of_service"],
            ),
        ).fetchall(),
        "episode for this NCPDP key",
    )["episode_id"] == episode_id

    # ── stage 4: verdict ──
    verdict = _verdict(golden, episode_id)
    assert verdict["reimbursement_verdict_code"] == "A-04"
    assert verdict["reimbursement_disposition"] == "CLOSED"
    assert verdict["episode_disposition"] == "CLOSED"
    assert verdict["expected_reimbursement_cents"] == paid_cents, (
        "A-04 is 'paid in full', so what the contract expects and what the PBM adjudicated "
        "are the same number; the generator and the engine derive it through the same "
        "recon.reference.pricing, which is what makes a variance meaningful"
    )
    assert verdict["received_reimbursement_cents"] == paid_cents
    assert verdict["reimbursement_variance_cents"] == 0
    assert _reasons(golden, verdict["verdict_id"]) == [], (
        "a fully reconciled claim has nothing to explain"
    )
    # The same figure, independently, through the authority both sides share.
    assert pricing.expected_reimbursement_cents(
        episode["ndc11"], episode["pbm_id"], episode["quantity_milli"]
    ) == paid_cents

    # The payer's own remittance says the same number.
    citations = _evidence(golden, verdict["verdict_id"])
    claim_lines = [
        row for row in citations
        if row["record_kind"] == str(RecordKind.REMITTANCE_CLAIM_LINE)
    ]
    assert _one(claim_lines, "remittance claim line cited")["amount_cents"] == paid_cents, (
        "the 835's CLP04 disagrees with what the claim was adjudicated at, which A-04 says "
        "it cannot"
    )
    assert [row["record_kind"] for row in citations] == [
        str(RecordKind.PHARMACY_CLAIM),
        str(RecordKind.REMITTANCE_CLAIM_LINE),
    ], "a paid-and-matched pharmacy claim is decided from exactly the adjudication and the 835"

    # ── stage 5: the ledger ──
    allocation = _one(_ledger(golden, episode_id), "cash allocation")
    assert allocation["allocated_cents"] == paid_cents, (
        "the cash attributed to this claim is not the cash the 835 said it was paid"
    )
    assert allocation["basis"] == "TRN02", (
        "the strongest basis there is: the deposit carried the remittance's trace number"
    )
    # The two-hop resolution is real work, not a 1:1 join: this claim's cents are a slice of
    # a larger deposit, and that deposit's slices balance to the cent.
    assert allocation["deposit_cents"] != paid_cents
    banked = conn.execute(
        "SELECT SUM(allocated_cents) AS allocated, COUNT(*) AS rows_ FROM cash_allocation"
        " WHERE bank_norm_id = ?",
        (allocation["bank_norm_id"],),
    ).fetchone()
    assert banked["rows_"] > 1
    assert banked["allocated"] == allocation["deposit_cents"], (
        "the deposit this claim was paid out of does not balance; allocation invented or "
        "lost money on the way to the episode"
    )


# ═══ 2. REJECTED ════════════════════════════════════════════════════════════


def test_golden_rejected_claim_expects_nothing_and_receives_nothing(
    golden: PipelineRun,
) -> None:
    """**rejected** — the claim was refused, so the expected amount collapses to zero.

    *Archetype:* Doc 2 step 5's "rejected".  *Selected by:* the lowest-numbered episode
    whose reimbursement verdict is ``A-01`` — "Rejected at point of sale; drug not
    dispensed; expected 0" (``domain/verdicts.py``), reached when the adjudication
    dimension is ``REJECTED`` (``engine/verdicts.py`` ``classify_pharmacy``) and carrying
    :attr:`~recon.domain.enums.ReasonCode.REJECTED_AT_POS`.

    *What is asserted, and why it is not four zeroes:* the drug is priced.  The contract
    for this NDC, payer and quantity yields a real, non-zero expected reimbursement, and
    the engine deliberately throws it away because the drug was never dispensed
    (``engine/run.py``: expected collapses to 0 for ``A-01``).  The figure that matters is
    therefore the one the test computes and the verdict refuses to use.  An engine that
    stopped collapsing it would report a phantom shortfall of exactly that many cents on
    every rejected claim in the book, and this test is where that shows up.
    """
    conn = golden.conn
    picked = _pick(
        conn.execute(
            "SELECT episode_id FROM verdict"
            " WHERE reimbursement_verdict_code = 'A-01' AND cursor_at = ? ORDER BY episode_id",
            (golden.settings.max_cursor,),
        ).fetchall(),
        "rejected",
        "reimbursement verdict A-01, rejected at point of sale",
    )
    episode_id = picked["episode_id"]
    episode = _episode(golden, episode_id)

    # ── stage 1: raw ──
    anchor = _normalized(golden, episode["anchor_norm_id"])
    raw = _raw(golden, anchor["raw_id"])
    _assert_raw_is_the_source_file_line(golden, raw)
    payload = _payload(raw)
    assert raw["source_file"] == "pbm_claim_events.jsonl"
    assert payload["transaction_code"] == "B1"
    assert payload["response_status"] == "R", "a rejected claim is response_status R"
    assert payload["reject_codes"], "a rejection carries at least one NCPDP reject code"
    assert payload["total_amount_paid"] is None, "a rejection pays nothing, and says so"
    assert payload["authorization_number"] is None

    # ── stage 2: normalized — no money, and the absence is NULL rather than 0 ──
    assert anchor["record_kind"] == str(RecordKind.PHARMACY_CLAIM)
    assert anchor["amount_cents"] is None, (
        "a rejected claim has no paid amount; storing 0 would make 'paid nothing' and "
        "'not adjudicated' the same row"
    )
    assert anchor["status_code"] == "R"
    assert anchor["idempotency_key"] == raw["source_record_id"]

    # ── stage 3: episode — a refused claim is still a claim, and still gets one ──
    assert episode["reimbursement_track"] == "PHARMACY"
    assert episode["rx_number"] == payload["prescription_ref_number"]
    assert episode["ndc11"] == payload["product_service_id"]
    assert episode["quantity_milli"] == int(payload["quantity_dispensed"])
    assert episode["anchor_norm_id"] == anchor["norm_id"]

    # ── stage 4: verdict — the priced amount exists, and is deliberately not used ──
    contracted_cents = pricing.expected_reimbursement_cents(
        episode["ndc11"], episode["pbm_id"], episode["quantity_milli"]
    )
    assert contracted_cents > 0, (
        "this dispense is priced, so 'expected 0' below is a decision the engine made and "
        "not an accident of an unpriceable drug"
    )
    verdict = _verdict(golden, episode_id)
    assert verdict["reimbursement_verdict_code"] == "A-01"
    assert verdict["reimbursement_disposition"] == "CLOSED", (
        "a rejection is a correct outcome with nothing to work, not a defect"
    )
    assert verdict["expected_reimbursement_cents"] == 0, (
        f"the contract prices this dispense at {contracted_cents} cents; A-01 means the drug "
        "was never dispensed, so carrying that expectation forward would report a shortfall "
        "on a claim that was correctly refused"
    )
    assert verdict["received_reimbursement_cents"] == 0
    assert verdict["reimbursement_variance_cents"] == 0
    assert str(ReasonCode.REJECTED_AT_POS) in _reasons(golden, verdict["verdict_id"]), (
        "an A-01 that does not say REJECTED_AT_POS is a verdict nobody can explain"
    )
    citations = _evidence(golden, verdict["verdict_id"])
    adjudication = _one(
        [row for row in citations if row["norm_id"] == anchor["norm_id"]],
        "citation of the rejected adjudication",
    )
    assert adjudication["role"] == "ADJUDICATION"
    assert adjudication["raw_id"] == raw["raw_id"], (
        "lineage runs downward: the verdict must point back at the feed line itself"
    )

    # ── stage 5: the ledger — nothing moved, and nothing is attributed ──
    reimbursement_rows = _ledger(golden, episode_id, track="REIMBURSEMENT")
    assert reimbursement_rows == [], (
        "a claim refused at the counter was never paid, so no bank cent may be attributed "
        "to its reimbursement track"
    )
    assert sum(row["allocated_cents"] for row in reimbursement_rows) == 0
    # And no remittance ever named it either — the absence is upstream of the bank.
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM verdict_evidence e"
        "  JOIN normalized_record n ON n.norm_id = e.norm_id"
        " WHERE e.verdict_id = ? AND n.record_kind = ?",
        (verdict["verdict_id"], str(RecordKind.REMITTANCE_CLAIM_LINE)),
    ).fetchone()["n"] == 0


# ═══ 3. REVERSED ════════════════════════════════════════════════════════════


def test_golden_reversed_claim_nets_to_exactly_zero_on_the_ledger(
    golden: PipelineRun,
) -> None:
    """**reversed** — money went out and came back, exactly once.

    *Archetype:* Doc 2 step 5's "reversed".  *Selected by:* the lowest-numbered episode
    whose reimbursement verdict is ``A-10`` — "Reversed by pharmacy, money returned; net
    zero" (``domain/verdicts.py``) — and whose reimbursement-leg allocations contain both a
    credit and a debit that sum to zero.  ``A-10`` is reached when the post-payment event is
    ``REVERSAL_POST_PAY`` and the returned cash matched (``engine/verdicts.py``
    ``classify_pharmacy``); its sibling ``A-11`` is the same reversal with the money *not*
    returned, and ``A-16`` the reversal that arrived before any payment.  The selection asks
    for a complete round trip so that both legs are visible in the ledger.

    *What is asserted:* requirement **B3**'s acceptance — *"a golden reversed claim produces
    exactly one net effect on the ledger — not zero, not two."*  Here that is two allocation
    rows whose magnitudes each equal a specific source record: the credit equals the 835's
    ``CLP04`` for this claim, the debit equals the ``PLB`` write-off that took it back, and
    together they sum to zero.  Netting it twice, or not at all, changes one of those three
    figures and fails here.

    *And the trap this guards:* a B2 reversal carries **no amount at all** — it repeats the
    transaction key and nothing else.  A connector that expected a negative row would find
    nothing; one that invented an amount would double-count.  That is exactly the hazard
    ``docs/connectivity_layer_requirements.md`` B3 says destroys a build quietly, and the
    assertions below pin the representation this repo actually ships.
    """
    conn = golden.conn
    picked = _pick(
        conn.execute(
            "SELECT v.episode_id FROM verdict v"
            " WHERE v.reimbursement_verdict_code = 'A-10' AND v.cursor_at = ?"
            "   AND (SELECT SUM(a.allocated_cents) FROM cash_allocation a"
            "         LEFT JOIN normalized_record p ON p.norm_id = a.remittance_norm_id"
            "        WHERE a.episode_id = v.episode_id"
            "          AND COALESCE(p.record_kind, '') <> 'REBATE_BATCH') = 0"
            "   AND (SELECT COUNT(*) FROM cash_allocation a"
            "         LEFT JOIN normalized_record p ON p.norm_id = a.remittance_norm_id"
            "        WHERE a.episode_id = v.episode_id AND a.allocated_cents < 0"
            "          AND COALESCE(p.record_kind, '') <> 'REBATE_BATCH') > 0"
            " ORDER BY v.episode_id",
            (golden.settings.max_cursor,),
        ).fetchall(),
        "reversed",
        "reimbursement verdict A-10 with both a credit and a debit on the reimbursement leg",
    )
    episode_id = picked["episode_id"]
    episode = _episode(golden, episode_id)

    # ── stage 1: raw — two lines of the same feed, the claim and its reversal ──
    anchor = _normalized(golden, episode["anchor_norm_id"])
    claim_raw = _raw(golden, anchor["raw_id"])
    _assert_raw_is_the_source_file_line(golden, claim_raw)
    claim_payload = _payload(claim_raw)
    assert claim_payload["transaction_code"] == "B1"
    adjudicated_cents = to_cents(claim_payload["total_amount_paid"])
    assert adjudicated_cents > 0

    verdict = _verdict(golden, episode_id)
    citations = _evidence(golden, verdict["verdict_id"])
    reversal_citation = _one(
        [
            row for row in citations
            if row["record_kind"] == str(RecordKind.PHARMACY_REVERSAL)
        ],
        "cited pharmacy reversal",
    )
    reversal = _normalized(golden, reversal_citation["norm_id"])
    reversal_raw = _raw(golden, reversal["raw_id"])
    _assert_raw_is_the_source_file_line(golden, reversal_raw)
    reversal_payload = _payload(reversal_raw)
    assert reversal_raw["source_file"] == claim_raw["source_file"]
    assert reversal_payload["transaction_code"] == "B2"
    assert reversal_payload["record_id"] != claim_payload["record_id"], (
        "the reversal is its own delivery with its own record id; sharing one would make it "
        "a duplicate (D-1) instead of an event"
    )
    #: The B3 hazard, asserted rather than described. A B2 is a status transaction.
    assert "total_amount_paid" not in reversal_payload, (
        "a B2 reversal carries no amount on the wire. A connector written for a negative-row "
        "convention would find nothing here, which is requirement B3's whole point"
    )
    # It repeats the transaction key and invents no identity of its own.
    for claim_field, reversal_field in (
        ("service_provider_id", "service_provider_id"),
        ("prescription_ref_number", "prescription_ref_number"),
        ("fill_number", "fill_number"),
        ("date_of_service", "date_of_service"),
    ):
        assert reversal_payload[reversal_field] == claim_payload[claim_field], (
            f"the B2's {reversal_field} does not repeat the B1's; the reversal would then "
            "attach to the wrong claim or to none"
        )

    # ── stage 2: normalized ──
    assert anchor["amount_cents"] == adjudicated_cents
    assert reversal["record_kind"] == str(RecordKind.PHARMACY_REVERSAL)
    assert reversal["amount_cents"] is None, (
        "the reversal has no money of its own; the cash effect is the PLB write-off below"
    )
    assert reversal["rx_number"] == anchor["rx_number"]
    assert reversal["date_of_service"] == anchor["date_of_service"]

    # ── stage 3: episode — one claim, one episode, and the reversal did not mint a second ──
    assert _one(
        conn.execute(
            "SELECT episode_id FROM episode"
            " WHERE pharmacy_npi = ? AND rx_number = ? AND fill_number = ? AND date_of_service = ?",
            (
                episode["pharmacy_npi"],
                episode["rx_number"],
                episode["fill_number"],
                episode["date_of_service"],
            ),
        ).fetchall(),
        "episode for the reversed claim's NCPDP key",
    )["episode_id"] == episode_id
    assert episode["anchor_norm_id"] == anchor["norm_id"], (
        "the episode is anchored on the B1, never on the B2"
    )
    # The reversal resolved to this episode by repeating the key, rather than publishing one.
    assert _one(
        conn.execute(
            "SELECT episode_id, key_type FROM crosswalk_key WHERE resolved_from_norm_id = ?",
            (reversal["norm_id"],),
        ).fetchall(),
        "crosswalk row for the reversal",
    )["episode_id"] == episode_id

    # ── stage 4: verdict ──
    assert verdict["reimbursement_verdict_code"] == "A-10"
    assert verdict["expected_reimbursement_cents"] == 0, (
        "a withdrawn claim expects nothing; a variance against it would be meaningless "
        "(engine/run.py collapses expected for A-10)"
    )
    claim_line = _one(
        [
            row for row in citations
            if row["record_kind"] == str(RecordKind.REMITTANCE_CLAIM_LINE)
        ],
        "cited remittance claim line",
    )
    write_off = _one(
        [
            row for row in citations
            if row["record_kind"] == str(RecordKind.PROVIDER_LEVEL_ADJUSTMENT)
        ],
        "cited provider-level adjustment",
    )
    paid_cents = claim_line["amount_cents"]
    assert paid_cents > 0
    assert write_off["amount_cents"] == paid_cents, (
        "the PLB write-off does not equal what was paid, so the reversal took back a "
        "different amount from the one it reversed"
    )
    assert write_off["status_code"] == "WO", "a recovery rides a PLB WO (reference/codes.py)"
    assert verdict["received_reimbursement_cents"] == paid_cents, (
        "the verdict reports cash IN, gross; the netting is the ledger's job, and the two "
        "must not be conflated"
    )

    # ── stage 5: the ledger — one net effect, not zero and not two ──
    rows = _ledger(golden, episode_id, track="REIMBURSEMENT")
    assert len(rows) == 2, (
        f"expected exactly two reimbursement allocations — the payment and its recovery — "
        f"got {[(r['allocated_cents'], r['basis']) for r in rows]}"
    )
    credit = _one([row for row in rows if row["allocated_cents"] > 0], "credit allocation")
    debit = _one([row for row in rows if row["allocated_cents"] < 0], "debit allocation")
    assert credit["allocated_cents"] == paid_cents, (
        "the cash credited to this claim is not what the 835 said it paid"
    )
    assert debit["allocated_cents"] == -paid_cents, (
        "the clawback on the ledger is not the PLB write-off's amount"
    )
    assert credit["allocated_cents"] + debit["allocated_cents"] == 0, (
        "B3: a reversed claim nets to exactly one effect. A non-zero net here means the "
        "reversal was counted twice, or the money never actually went back"
    )
    assert debit["remittance_norm_id"] != credit["remittance_norm_id"], (
        "the recovery is netted out of a LATER remittance, which is why a connector reading "
        "bank rows alone never learns the money was taken back"
    )


# ═══ 4. UNMATCHED ═══════════════════════════════════════════════════════════


def test_golden_unmatched_claim_resolves_to_no_episode_and_its_cash_sits_unattributed(
    golden: PipelineRun,
) -> None:
    """**unmatched** — real money arrived for a real claim, and reconciled to nothing.

    *Archetype:* Doc 2 step 5's "unmatched".  *Selected by:* the lowest-``norm_id``
    ``REBATE_DISPENSE_LINE`` that is parked with
    :attr:`~recon.domain.enums.ParkReason.NO_KEY_MATCH`, is still unresolved at the cursor,
    and whose payment batch was settled by a deposit equal to the batch's declared total.
    That last clause is what makes the ledger figure readable: when the deposit equals the
    declared total, each line's share is its own amount and no apportionment stands between
    the line and the cents attributed to it.

    ``NO_KEY_MATCH`` means *keys were present and resolved to nothing* — defect **D-6**, the
    crosswalk miss, the one produced by the identifier drift the generator injects into
    exactly one feed per episode.  The cash that follows it is defect **D-4**: rebate money
    with no attributable episode, which is why ``C-12`` was retired from the verdict
    vocabulary — it was never an episode state (``domain/verdicts.py``).

    *What is asserted:* the rebate amount printed in ``tpa_340b_events.jsonl`` becomes a
    normalized line carrying exactly those cents, resolves to **zero** episodes, is cited by
    **zero** verdicts, and lands in the ledger as an unattributed ``RESIDUAL`` row — while
    the claim it was paid for is sitting in the episode table the whole time, differing only
    in how one feed spelled the prescription number.  The claim is not missing; the mapping
    failed, and this test is the measurement of what that costs.

    Deleting it would let a connector "fix" the drift — normalising the two spellings
    together — and the exception this dataset exists to surface would quietly disappear
    while the score went up.  That is precisely what Decision A23 forbids.
    """
    conn = golden.conn
    cursor = golden.settings.max_cursor
    picked = _pick(
        conn.execute(
            "SELECT line.norm_id, line.amount_cents, line.parent_norm_id, line.raw_id,"
            "       p.parked_id"
            "  FROM parked_record p"
            "  JOIN normalized_record line ON line.norm_id = p.norm_id"
            "  JOIN normalized_record batch ON batch.norm_id = line.parent_norm_id"
            "  LEFT JOIN parked_record_resolution r"
            "         ON r.parked_id = p.parked_id AND r.resolved_by_received_at <= ?"
            " WHERE p.record_kind = ? AND p.park_reason = ? AND p.received_at <= ?"
            "   AND r.parked_id IS NULL"
            "   AND EXISTS (SELECT 1 FROM cash_allocation a"
            "                 JOIN normalized_record bank ON bank.norm_id = a.bank_norm_id"
            "                WHERE a.remittance_norm_id = batch.norm_id"
            "                  AND bank.amount_cents = batch.amount_cents)"
            " ORDER BY line.norm_id",
            (
                cursor,
                str(RecordKind.REBATE_DISPENSE_LINE),
                str(ParkReason.NO_KEY_MATCH),
                cursor,
            ),
        ).fetchall(),
        "unmatched",
        "a rebate dispense line parked NO_KEY_MATCH and still unresolved, whose batch was "
        "paid in full",
    )
    line = _normalized(golden, picked["norm_id"])
    batch = _normalized(golden, line["parent_norm_id"])

    # ── stage 3 (early): the key the record carried, and the miss itself ──
    parked_keys = conn.execute(
        "SELECT key_type, key_value FROM parked_record_key WHERE parked_id = ?"
        " ORDER BY key_type, key_value",
        (picked["parked_id"],),
    ).fetchall()
    parked_key = _one(
        [row for row in parked_keys if row["key_type"] == NATURAL_340B_PHARMACY],
        "natural 340B pharmacy key on the parked line",
    )
    npi, feed_rx, ndc11, fill_date = parked_key["key_value"].split("|")

    # ── stage 1: raw — the batch line, verbatim from the TPA feed ──
    raw = _raw(golden, line["raw_id"])
    _assert_raw_is_the_source_file_line(golden, raw)
    payload = _payload(raw)
    assert raw["source_file"] == "tpa_340b_events.jsonl"
    assert payload["event_type"] == "REBATE_PAYMENT_BATCH"
    dispense = _one(
        [
            entry for entry in payload["dispenses"]
            if entry.get("rx_number") == feed_rx
            and entry.get("pharmacy_npi") == npi
            and entry.get("ndc_11") == ndc11
        ],
        "dispense entry in the batch payload matching the parked key",
    )
    #: THE figure: what the manufacturer said it was paying for this dispense.
    rebate_cents = to_cents(dispense["rebate_amount"])
    assert rebate_cents > 0
    assert dispense["manufacturer_status"] == "APPROVED", (
        "this rebate was approved and paid; the miss is a mapping failure, not a refusal"
    )

    # ── stage 2: normalized — the batch splits into lines without losing a cent ──
    assert line["record_kind"] == str(RecordKind.REBATE_DISPENSE_LINE)
    assert line["amount_cents"] == rebate_cents
    assert batch["record_kind"] == str(RecordKind.REBATE_BATCH)
    assert batch["amount_cents"] == to_cents(payload["total_rebate_amount"])
    children = conn.execute(
        "SELECT norm_id, amount_cents FROM normalized_record WHERE parent_norm_id = ?"
        " ORDER BY norm_id",
        (batch["norm_id"],),
    ).fetchall()
    assert sum(row["amount_cents"] for row in children) == batch["amount_cents"], (
        "the batch total and its dispense lines disagree; the split lost or invented money"
    )
    assert line["rx_number"] == feed_rx, (
        "the drifted spelling is kept verbatim. A normalized twin of this column would "
        "resolve the record, delete the exception, and leave the engine covering up the "
        "defect it exists to surface (Decision A23)"
    )

    # ── stage 3: episode — it resolved to none, and here is the one it should have hit ──
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM crosswalk_key WHERE resolved_from_norm_id = ?",
        (line["norm_id"],),
    ).fetchone()["n"] == 0, "a parked record resolved to nothing, so it published nothing"
    parked = _one(
        conn.execute(
            "SELECT * FROM parked_record WHERE norm_id = ?", (line["norm_id"],)
        ).fetchall(),
        "parked_record row",
    )
    assert parked["park_reason"] == str(ParkReason.NO_KEY_MATCH), (
        "keys were present and matched nothing; the connector parked rather than guessed"
    )
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM parked_record_resolution"
        " WHERE parked_id = ? AND resolved_by_received_at <= ?",
        (parked["parked_id"], cursor),
    ).fetchone()["n"] == 0, "still unresolved at the cursor, which is what 'unmatched' means"

    same_dispense = [
        row
        for row in conn.execute(
            "SELECT * FROM episode"
            " WHERE pharmacy_npi = ? AND ndc11 = ? AND date_of_service = ?",
            (npi, ndc11, fill_date),
        )
        if row["rx_number"] and _rx_differs_only_in_rendering(feed_rx, row["rx_number"])
    ]
    orphaned_from = _one(same_dispense, "episode this rebate was paid for")
    assert orphaned_from["is_340b_flagged"] == 1, (
        "the claim is a 340B claim; it is the join that failed, not the entitlement"
    )
    assert orphaned_from["covered_entity_id"] == dispense["covered_entity_id"], (
        "same covered entity on both sides — the only thing that differs is how one feed "
        "spelled the prescription number"
    )
    episode_key = _one(
        conn.execute(
            "SELECT key_value FROM crosswalk_key"
            " WHERE episode_id = ? AND key_type = ?",
            (orphaned_from["episode_id"], NATURAL_340B_PHARMACY),
        ).fetchall(),
        "natural 340B key published by that episode",
    )["key_value"]
    assert episode_key != parked_key["key_value"]
    assert episode_key.split("|")[0::2] == parked_key["key_value"].split("|")[0::2], (
        "the two keys must differ in the rx component and nowhere else, or this is a "
        "different dispense and the trace is wrong"
    )

    # ── stage 4: verdict — no verdict was computed from this money, anywhere ──
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM verdict_evidence WHERE norm_id = ?", (line["norm_id"],)
    ).fetchone()["n"] == 0, (
        "an unmatched line is cited by no verdict; if it were, it reached an episode after all"
    )
    verdict = _verdict(golden, orphaned_from["episode_id"])
    assert verdict["rebate_verdict_code"] == "C-00", (
        "the whole 340B track drifted with the rx number, so the engine sees no 340B track "
        "on a claim that has one. C-00 means 'the track does not exist', not 'it finished'"
    )
    assert verdict["rebate_disposition"] is None, (
        "C-00 and a NULL rebate disposition are the same fact, stated twice"
    )
    assert verdict["expected_rebate_cents"] == 0
    assert verdict["received_rebate_cents"] == 0, (
        f"{rebate_cents} cents of rebate for this exact dispense settled in the bank and "
        "none of it reached the episode"
    )
    assert _ledger(golden, orphaned_from["episode_id"], track="REBATE") == []

    # ── stage 5: the ledger — the cash arrived, balanced, and attributed to nothing ──
    deposit = conn.execute(
        "SELECT a.bank_norm_id, bank.amount_cents AS deposit_cents,"
        "       MIN(a.caused_by_received_at) AS at"
        "  FROM cash_allocation a JOIN normalized_record bank ON bank.norm_id = a.bank_norm_id"
        " WHERE a.remittance_norm_id = ? AND bank.amount_cents = ?"
        " GROUP BY a.bank_norm_id ORDER BY at LIMIT 1",
        (batch["norm_id"], batch["amount_cents"]),
    ).fetchone()
    assert deposit is not None
    allocations = conn.execute(
        "SELECT * FROM cash_allocation WHERE bank_norm_id = ? AND remittance_norm_id = ?"
        " ORDER BY allocation_id",
        (deposit["bank_norm_id"], batch["norm_id"]),
    ).fetchall()
    assert sum(row["allocated_cents"] for row in allocations) == deposit["deposit_cents"], (
        "the deposit does not balance; allocation lost or invented money"
    )
    unattributed = [row for row in allocations if row["episode_id"] is None]
    assert unattributed, "the unmatched money has to be somewhere, and nowhere is not an option"
    assert {row["basis"] for row in unattributed} == {"RESIDUAL"}, (
        "cash attributed no more precisely than the account it landed in is D-7, and the "
        "basis is how the audit trail says so"
    )
    unresolved_line_cents = conn.execute(
        "SELECT COALESCE(SUM(child.amount_cents), 0) AS cents FROM normalized_record child"
        "  JOIN parked_record pr ON pr.norm_id = child.norm_id"
        "  LEFT JOIN parked_record_resolution res"
        "         ON res.parked_id = pr.parked_id AND res.resolved_by_received_at <= ?"
        " WHERE child.parent_norm_id = ? AND res.parked_id IS NULL",
        (cursor, batch["norm_id"]),
    ).fetchone()["cents"]
    assert sum(row["allocated_cents"] for row in unattributed) == unresolved_line_cents, (
        "the unattributed cents must be exactly the lines that resolved to nothing — no "
        "more, which would mean attributed money was dropped, and no less, which would mean "
        "unmatched money was attributed to some episode anyway"
    )
    assert rebate_cents in [row["allocated_cents"] for row in unattributed], (
        f"this line's {rebate_cents} cents are not sitting in the residual bucket, so they "
        "went somewhere the crosswalk said they could not go"
    )
