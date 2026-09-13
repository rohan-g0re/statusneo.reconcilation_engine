"""Hand-authored scenarios for every edge case the assignment names, traced stage by stage.

The assignment's data requirement names eight cases — *fully reconciled, partial payment,
underpayment, reversal/recoupment, unmatched cash or rebate, denial, duplicate event,
late-arriving status* — and each gets at least one scenario here, on each track where it is
representable.  The structural traps get their own: the netted recoupment, the two-hop bank
allocation, settlement hiding in CLP02, identifier drift, the ambiguous medical 340B key, and the
two cross-track compliance cases no single-track system can see.

Every scenario is written **by hand** against ``docs/feed_formats.md``, independently of the
generator.  That independence is the point: the generated profiles prove the pipeline agrees with
the generator, which would still pass if both shared a misreading of the spec.

And every scenario asserts the **intermediate** states, not just the verdict — the raw row, the
normalized tree, the keys published, the park and the unpark, the allocation and its basis, the
derived dimensions, the reason codes, the lineage.  A verdict that is right for the wrong reason
fails here.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from scenario import (  # noqa: E402
    CE,
    MD_DRUG,
    PBM,
    PH_DRUG,
    PH_NPI,
    PROVIDER_NPI,
    SAT_NPI,
    Scenario,
    b1,
    b2,
    bank_row,
    claim_line,
    dispense_line,
    med_277,
    med_835,
    med_837,
    med_claim_line,
    medical_money,
    pbm_835,
    pharmacy_money,
    rebate_batch,
    run_scenario,
    tpa_event,
)

DOS = "2025-09-02"
RX = "7845102"
CLM01 = "ENC-88231-01"
TRACE = "8873020123"


# ═══ 1. Fully reconciled, pharmacy — the happy path, traced completely ══════


def _fully_reconciled_pharmacy() -> Scenario:
    money = pharmacy_money()
    return Scenario(
        name="fully reconciled, pharmacy",
        pbm_claims=[b1(record_id="PBM-EVT-000001", received_at=f"{DOS}T14:22:31Z", rx=RX, dos=DOS)],
        pbm_remittances=[
            pbm_835(
                record_id="PBM-835-000412",
                received_at="2025-09-18T06:00:00Z",
                effective="2025-09-17",
                trace=TRACE,
                lines=[
                    claim_line(
                        rx=RX,
                        dos=DOS,
                        charge=money.charge,
                        paid=money.expected,
                        patient=money.patient,
                        adjustments=[
                            ("CO", "45", money.charge - money.allowed),
                            ("PR", "3", money.patient),
                        ],
                    )
                ],
            )
        ],
        bank_rows=[
            bank_row(posting="2025-09-17", amount=money.expected, trn02=TRACE),
        ],
    )


def test_fully_reconciled_pharmacy_traced_end_to_end(tmp_path):
    """A-04, and every intermediate state on the way to it.

    This is the scenario worth reading first: it walks the whole path once, asserting what each
    stage should contain, so the later scenarios can assert only what makes them different.
    """
    run = run_scenario(_fully_reconciled_pharmacy(), tmp_path)
    money = pharmacy_money()

    # --- stage 1: raw rows, stored verbatim -------------------------------
    raw = run.raw()
    assert len(raw) == 3, "one claim event, one remittance, one bank row"
    claim_raw = next(r for r in raw if r["source_system"] == "PBM_ADJUDICATION")
    assert claim_raw["source_record_id"] == "PBM-EVT-000001"
    assert '"prescription_ref_number":"7845102"' in claim_raw["payload"], (
        "the raw layer must hold the payload verbatim, not a re-serialised version"
    )

    # --- stage 2: normalized tree ----------------------------------------
    assert len(run.normalized("PHARMACY_CLAIM")) == 1
    remittances = run.normalized("REMITTANCE")
    assert len(remittances) == 1
    lines = run.normalized("REMITTANCE_CLAIM_LINE")
    assert len(lines) == 1
    assert lines[0]["parent_norm_id"] == remittances[0]["norm_id"], (
        "a claim line is a child of its remittance; flattening the tree would lose the PLB"
    )
    # The Rx number survives verbatim (Decision A23).
    assert lines[0]["rx_number"] == RX
    assert lines[0]["status_code"] == "1", "CLP02 is where settlement lives"

    # --- stage 3: the episode, anchored on the claim event ---------------
    episode = run.only_episode()
    assert episode["reimbursement_track"] == "PHARMACY"
    assert episode["rx_number"] == RX
    assert episode["clm01"] is None, "the XOR makes the medical columns unrepresentable here"
    assert episode["pbm_id"] == PBM.pbm_id, "the BIN resolved the payer"
    assert episode["quantity_milli"] == 30_000

    # --- stage 4: the crosswalk ------------------------------------------
    assert {"NCPDP_CLAIM", "PBM_AUTH", "TRN02"} <= run.key_types()
    ncpdp = next(row for row in run.crosswalk() if row["key_type"] == "NCPDP_CLAIM")
    assert ncpdp["key_value"] == f"{PH_NPI}|{RX}|00|{DOS}"
    assert ncpdp["episode_id"] == episode["episode_id"]
    trn = next(row for row in run.crosswalk() if row["key_type"] == "TRN02")
    assert trn["remittance_norm_id"] == remittances[0]["norm_id"], (
        "TRN02 resolves to a remittance, never to a claim — that is hop one of two"
    )

    # --- stage 5: nothing parked, nothing quarantined --------------------
    assert run.parked() == []
    assert run.stats.quarantined == 0

    # --- stage 6: cash allocated through the remittance ------------------
    allocations = run.allocations()
    assert len(allocations) == 1
    assert allocations[0]["basis"] == "TRN02"
    assert allocations[0]["episode_id"] == episode["episode_id"]
    assert allocations[0]["allocated_cents"] == money.expected
    assert run.allocated_to_episode(episode["episode_id"]) == money.expected

    # --- stage 7: the derived dimensions ---------------------------------
    assert run.result().dimensions.as_configuration() == {
        "reimb_type": "PHARMACY",
        "ph_adjudication": "ACCEPTED",
        "ph_payment": "FULL",
        "ph_post_event": "NONE",
        "ph_settlement": "CONFIRMED",
        "r_present": "ABSENT",
        "cash_reimb_in": "MATCHED",
    }

    # --- stage 8: the verdict, disposition and money ---------------------
    assert run.result().verdict_pair == ("A-04", "C-00")
    verdict = run.verdict_row(episode["episode_id"])
    assert verdict["episode_disposition"] == "CLOSED"
    assert verdict["rebate_disposition"] is None, "C-00 means absent, which is not CLOSED"
    assert verdict["expected_reimbursement_cents"] == money.expected
    assert verdict["received_reimbursement_cents"] == money.expected
    assert verdict["reimbursement_variance_cents"] == 0
    assert run.reasons(episode["episode_id"]) == []

    # --- stage 9: lineage runs all the way down to the payload ----------
    citations = run.citations(episode["episode_id"])
    assert len(citations) >= 2
    assert all(citation.payload for citation in citations), (
        "every citation must reach the verbatim source record, which is what the assignment grades"
    )
    roles = {citation.role for citation in citations}
    assert "ADJUDICATION" in roles and "REMITTANCE_CLAIM_LINE" in roles


# ═══ 2. Underpayment — an unexplained residual ═════════════════════════════


def test_underpayment_is_detected_from_the_residual(tmp_path):
    """A-07: paid short with a CO-45 that does not explain the gap.

    The *only* thing separating this from a contractual adjustment is arithmetic the wire already
    carries: ``clp03 − clp04 − Σ(adjustments) > 0``.  The generator emits this line deliberately
    unbalanced, which is the single sanctioned exception to the balance invariant.
    """
    money = pharmacy_money()
    short = money.expected - 50_000
    scenario = Scenario(
        name="underpayment",
        pbm_claims=[b1(record_id="PBM-EVT-000001", received_at=f"{DOS}T14:22:31Z", rx=RX, dos=DOS)],
        pbm_remittances=[
            pbm_835(
                record_id="PBM-835-000412",
                received_at="2025-09-18T06:00:00Z",
                effective="2025-09-17",
                trace=TRACE,
                lines=[
                    claim_line(
                        rx=RX,
                        dos=DOS,
                        charge=money.charge,
                        paid=short,
                        patient=money.patient,
                        # Deliberately short of explaining the gap: the 50,000c residual is the
                        # defect, and it is what makes this an underpayment rather than a
                        # contractual write-down.
                        adjustments=[
                            ("CO", "45", money.charge - money.allowed),
                            ("PR", "3", money.patient),
                        ],
                    )
                ],
            )
        ],
        bank_rows=[bank_row(posting="2025-09-17", amount=short, trn02=TRACE)],
    )
    run = run_scenario(scenario, tmp_path)
    episode = run.only_episode()

    dimensions = run.result().dimensions
    assert dimensions.ph_payment == "PARTIAL"
    assert dimensions.ph_post_event == "NONE", (
        "no specific contractual CARC is present, so the shortfall is unexplained"
    )
    assert dimensions.cash_reimb_in == "MATCHED", "the bank paid what the remittance promised"
    assert run.result().verdict_pair == ("A-07", "C-00")

    verdict = run.verdict_row(episode["episode_id"])
    assert verdict["episode_disposition"] == "EXCEPTION"
    assert verdict["reimbursement_variance_cents"] == 50_000
    assert "UNDERPAID" in run.reasons(episode["episode_id"])


def test_contractual_adjustment_is_not_an_underpayment(tmp_path):
    """A-14: the same shortfall, explained — and therefore not an exception.

    Two scenarios differing only in whether a specific contractual CARC is present, landing on
    opposite sides of the CLOSED/EXCEPTION line.  That is the distinction the residual rule exists
    to make.
    """
    money = pharmacy_money()
    reduction = 40_000
    paid = money.expected - reduction
    scenario = Scenario(
        name="contractual adjustment",
        pbm_claims=[b1(record_id="PBM-EVT-000001", received_at=f"{DOS}T14:22:31Z", rx=RX, dos=DOS)],
        pbm_remittances=[
            pbm_835(
                record_id="PBM-835-000412",
                received_at="2025-09-18T06:00:00Z",
                effective="2025-09-17",
                trace=TRACE,
                lines=[
                    claim_line(
                        rx=RX,
                        dos=DOS,
                        charge=money.charge,
                        paid=paid,
                        patient=money.patient,
                        adjustments=[
                            ("CO", "45", money.charge - money.allowed),
                            ("PR", "3", money.patient),
                            # A *specific* contractual reduction, which is what makes the
                            # shortfall explained rather than missing.
                            ("CO", "97", reduction),
                        ],
                    )
                ],
            )
        ],
        bank_rows=[bank_row(posting="2025-09-17", amount=paid, trn02=TRACE)],
    )
    run = run_scenario(scenario, tmp_path)
    assert run.result().dimensions.ph_post_event == "ADJUSTMENT"
    assert run.result().verdict_pair == ("A-14", "C-00")
    assert run.verdict_row(run.only_episode()["episode_id"])["episode_disposition"] == "CLOSED"


# ═══ 3. Remittance with no cash, and settlement missing ════════════════════


def test_remittance_with_no_cash(tmp_path):
    """A-05: the payer's paperwork and the payer's money disagree.

    The absence of a bank row *is* the finding. Nothing in the feeds says "no deposit" — there is
    simply no deposit, which is exactly the position a real operator is in.
    """
    money = pharmacy_money()
    scenario = _fully_reconciled_pharmacy()
    scenario.bank_rows = []
    run = run_scenario(scenario, tmp_path)

    assert run.allocations() == [], "no deposit means nothing to allocate"
    assert run.result().dimensions.cash_reimb_in == "ABSENT"
    assert run.result().verdict_pair == ("A-05", "C-00")
    episode = run.only_episode()
    assert run.verdict_row(episode["episode_id"])["episode_disposition"] == "EXCEPTION"
    assert "NO_CASH" in run.reasons(episode["episode_id"])


def test_settlement_missing_rides_clp02(tmp_path):
    """A-06, and the reason Decision C13 is binding.

    Cash matched, money is not in dispute, only the closing confirmation is absent — and the
    entire signal is ``CLP02 = "19"`` with no later ``"1"``. Get this read wrong and A-06 silently
    never fires, which is the quietest possible failure mode.
    """
    money = pharmacy_money()
    scenario = _fully_reconciled_pharmacy()
    scenario.pbm_remittances[0]["claim_payments"][0]["clp02_claim_status_code"] = "19"
    run = run_scenario(scenario, tmp_path)

    assert run.normalized("REMITTANCE_CLAIM_LINE")[0]["status_code"] == "19"
    assert run.result().dimensions.ph_settlement == "MISSING"
    assert run.result().dimensions.cash_reimb_in == "MATCHED"
    assert run.result().verdict_pair == ("A-06", "C-00")
    assert "SETTLEMENT_MISSING" in run.reasons(run.only_episode()["episode_id"])


def test_a_later_settled_line_closes_the_receivable(tmp_path):
    """The second half of the CLP02 rule, which is easy to omit.

    ``MISSING`` requires a ``19``/``25`` line **and no later ``1``**. Without that second
    condition a claim that was forwarded and then settled would read as unsettled forever.
    """
    money = pharmacy_money()
    scenario = _fully_reconciled_pharmacy()
    scenario.pbm_remittances[0]["claim_payments"][0]["clp02_claim_status_code"] = "19"
    scenario.pbm_remittances[0]["claim_payments"][0]["clp04_payment_amount"] = "0.00"
    scenario.pbm_remittances[0]["claim_payments"][0]["adjustments"] = [
        {"group_code": "CO", "reason_code": "45", "amount": "%0.2f" % (money.charge / 100)}
    ]
    scenario.pbm_remittances.append(
        pbm_835(
            record_id="PBM-835-000555",
            received_at="2025-10-02T06:00:00Z",
            effective="2025-10-01",
            trace="8873020999",
            lines=[
                claim_line(
                    rx=RX,
                    dos=DOS,
                    charge=money.charge,
                    paid=money.expected,
                    patient=money.patient,
                    adjustments=[
                        ("CO", "45", money.charge - money.allowed),
                        ("PR", "3", money.patient),
                    ],
                    clp02="1",
                    clp07="20260610044999",
                )
            ],
        )
    )
    scenario.bank_rows = [
        bank_row(posting="2025-10-01", amount=money.expected, trn02="8873020999")
    ]
    run = run_scenario(scenario, tmp_path)
    assert run.result().dimensions.ph_settlement == "CONFIRMED", (
        "a later CLP02='1' line for the same claim closes the receivable"
    )


# ═══ 4. The netted recoupment — the best trap in the dataset ═══════════════


def test_netted_recoupment_is_invisible_to_the_bank(tmp_path):
    """A-12, and the trap that makes bank amount ≠ sum of claim payments.

    The payer pays a *later* batch short, and the only record of why lives in that batch's PLB.
    ``Σ(CLP04) − PLB = BPR02 = what hits the bank``. A connector reading bank rows alone sees an
    unremarkable deposit and never learns money was taken back.
    """
    money = pharmacy_money()
    clawback = 412_60
    other_rx = "7845119"
    scenario = _fully_reconciled_pharmacy()
    # A second, unrelated claim in a later cycle, out of which our clawback is netted.
    scenario.pbm_claims.append(
        b1(
            record_id="PBM-EVT-000050",
            received_at="2025-09-20T10:00:00Z",
            rx=other_rx,
            dos="2025-09-20",
            auth="AUTH0098412B",
        )
    )
    scenario.pbm_remittances.append(
        pbm_835(
            record_id="PBM-835-000600",
            received_at="2025-10-06T06:00:00Z",
            effective="2025-10-05",
            trace="8873021777",
            lines=[
                claim_line(
                    rx=other_rx,
                    dos="2025-09-20",
                    charge=money.charge,
                    paid=money.expected,
                    patient=money.patient,
                    adjustments=[
                        ("CO", "45", money.charge - money.allowed),
                        ("PR", "3", money.patient),
                    ],
                    clp07="20260610044822",
                    auth="AUTH0098412B",
                )
            ],
            # WO referencing the FIRST claim's authorization: the clawback belongs to that claim,
            # not to the one being paid here.
            plb=[("WO", "AUTH0098231A", clawback)],
        )
    )
    scenario.bank_rows.append(
        bank_row(
            posting="2025-10-05",
            amount=money.expected - clawback,
            trn02="8873021777",
            trace_suffix=2,
        )
    )
    run = run_scenario(scenario, tmp_path)

    # The bank deposit really is short, and nothing on the bank side explains it.
    second_deposit = money.expected - clawback
    assert any(
        row["amount_cents"] == second_deposit
        for row in run.normalized("BANK_TRANSACTION")
    )

    # The PLB resolved to the *first* episode, and its allocation is negative.
    first = next(row for row in run.episodes() if row["rx_number"] == RX)
    negative = [
        row
        for row in run.allocations()
        if row["episode_id"] == first["episode_id"] and row["allocated_cents"] < 0
    ]
    assert negative, "the clawback must be attributed to the claim it recovers against"
    assert negative[0]["allocated_cents"] == -clawback

    result = next(r for r in run.results if r.episode_id == first["episode_id"])
    assert result.dimensions.ph_post_event == "RECOUPMENT"
    assert result.dimensions.cash_reimb_out == "MATCHED"
    assert result.verdict_pair == ("A-12", "C-00")
    assert run.verdict_row(first["episode_id"])["episode_disposition"] == "CLOSED"

    # Every deposit still balances: the allocation did not invent or lose a cent.
    for row in run.conn.execute(
        "SELECT a.bank_norm_id, SUM(a.allocated_cents) AS allocated, n.amount_cents AS deposit"
        "  FROM cash_allocation a JOIN normalized_record n ON n.norm_id = a.bank_norm_id"
        " GROUP BY a.bank_norm_id"
    ):
        assert row["allocated"] == row["deposit"]


def test_untraceable_recoupment_reports_insufficient_data(tmp_path):
    """A-13: the recoupment line exists and cannot be tied to any bank movement.

    Note what distinguishes this from A-12: the PLB still names the claim, so we know *which*
    claim was recouped. What is missing is the deposit it was supposedly netted out of — so we
    cannot confirm the clawback happened, and it may be double-counted. The engine says so rather
    than guessing, which is what ``INSUFFICIENT_DATA`` is for.
    """
    money = pharmacy_money()
    scenario = _fully_reconciled_pharmacy()
    scenario.pbm_remittances.append(
        pbm_835(
            record_id="PBM-835-000601",
            received_at="2025-10-06T06:00:00Z",
            effective="2025-10-05",
            trace="8873021888",
            lines=[],
            plb=[("WO", "AUTH0098231A", 412_60)],
        )
    )
    # No bank row for that second remittance at all.
    run = run_scenario(scenario, tmp_path)
    episode = run.only_episode()
    result = run.result()

    assert result.dimensions.ph_post_event == "RECOUPMENT"
    assert result.dimensions.cash_reimb_out == "ABSENT"
    assert result.verdict_pair == ("A-13", "C-00")
    reasons = run.reasons(episode["episode_id"])
    assert "RECOUPMENT_UNTRACEABLE" in reasons
    assert "INSUFFICIENT_DATA" in reasons, (
        "the assignment requires the system to say when it cannot determine something"
    )


# ═══ 5. Reversal, both outcomes ════════════════════════════════════════════


@pytest.mark.parametrize(
    "money_returned,expected_verdict",
    [(True, "A-10"), (False, "A-11")],
    ids=["money returned", "money not returned"],
)
def test_post_payment_reversal(tmp_path, money_returned: bool, expected_verdict: str):
    """A-10 vs A-11, separated only by whether the money actually went back.

    A pharmacy-initiated reversal and a payer-initiated recoupment have the same numeric shape and
    completely different meanings. A-11 is the one that matters operationally: the books still show
    cash we are not entitled to.
    """
    money = pharmacy_money()
    scenario = _fully_reconciled_pharmacy()
    scenario.pbm_claims.append(
        b2(record_id="PBM-EVT-000318", received_at="2025-09-25T09:11:47Z", rx=RX, dos=DOS)
    )
    if money_returned:
        scenario.pbm_remittances.append(
            pbm_835(
                record_id="PBM-835-000700",
                received_at="2025-10-06T06:00:00Z",
                effective="2025-10-05",
                trace="8873022000",
                lines=[],
                plb=[("WO", "AUTH0098231A", money.expected)],
            )
        )
        scenario.bank_rows.append(
            bank_row(
                posting="2025-10-05",
                amount=-money.expected,
                trn02="8873022000",
                direction="CREDIT",
                trace_suffix=3,
            )
        )
    run = run_scenario(scenario, tmp_path)
    result = run.result()

    assert len(run.normalized("PHARMACY_REVERSAL")) == 1
    assert result.dimensions.ph_post_event == "REVERSAL_POST_PAY", (
        "a reversal after payment is a different dimension from one before it"
    )
    assert result.verdict_pair == (expected_verdict, "C-00")
    if not money_returned:
        assert "REVERSAL_CASH_NOT_RETURNED" in run.reasons(run.only_episode()["episode_id"])


def test_reversal_before_payment(tmp_path):
    """A-16: the claim was cancelled before any money moved, so expected falls to zero."""
    scenario = _fully_reconciled_pharmacy()
    scenario.pbm_remittances = []
    scenario.bank_rows = []
    scenario.pbm_claims.append(
        b2(record_id="PBM-EVT-000318", received_at="2025-09-05T09:11:47Z", rx=RX, dos=DOS)
    )
    run = run_scenario(scenario, tmp_path)
    assert run.result().dimensions.ph_post_event == "REVERSAL_PRE_PAY"
    assert run.result().verdict_pair == ("A-16", "C-00")
    assert run.verdict_row(run.only_episode()["episode_id"])["expected_reimbursement_cents"] == 0


# ═══ 6. Denial, and the 277CA that makes B-01 reachable ════════════════════


def test_pos_rejection_is_terminal(tmp_path):
    """A-01: a real-time rejection, after which nothing downstream can exist."""
    scenario = Scenario(
        name="POS rejection",
        pbm_claims=[
            b1(
                record_id="PBM-EVT-000002",
                received_at=f"{DOS}T14:24:08Z",
                rx=RX,
                dos=DOS,
                accepted=False,
                reject_code="75",
                auth=None,
            )
        ],
    )
    run = run_scenario(scenario, tmp_path)
    episode = run.only_episode()
    assert run.result().dimensions.as_configuration() == {
        "reimb_type": "PHARMACY",
        "ph_adjudication": "REJECTED",
        "r_present": "ABSENT",
    }
    assert run.result().verdict_pair == ("A-01", "C-00")
    verdict = run.verdict_row(episode["episode_id"])
    assert verdict["episode_disposition"] == "CLOSED"
    assert verdict["expected_reimbursement_cents"] == 0, "a rejected claim expects nothing"
    assert "REJECTED_AT_POS" in run.reasons(episode["episode_id"])
    # An episode still exists: the prototype begins once a claim has been filed, and a rejected
    # claim was filed.
    assert episode["reimbursement_track"] == "PHARMACY"


def test_clearinghouse_rejection_needs_the_277ca(tmp_path):
    """B-01, and why the 277CA rides the submissions file (Decision 39).

    Without the acknowledgment, "rejected before reaching the payer" and "accepted, awaiting the
    835" are the same thing on the wire — an 837 with no 835 — and this verdict is unreachable.
    """
    scenario = Scenario(
        name="clearinghouse rejection",
        med_submissions=[
            med_837(record_id="MED-837-000501", received_at="2025-09-10T16:40:00Z", clm01=CLM01, dos=DOS),
            med_277(
                record_id="MED-277-000509",
                received_at="2025-09-11T08:12:00Z",
                clm01=CLM01,
                accepted=False,
            ),
        ],
    )
    run = run_scenario(scenario, tmp_path)
    assert len(run.normalized("MEDICAL_ACKNOWLEDGMENT")) == 1
    assert run.result().dimensions.md_clearinghouse == "REJECTED"
    assert run.result().verdict_pair == ("B-01", "C-00")
    assert "CLEARINGHOUSE_REJECTED" in run.reasons(run.only_episode()["episode_id"])


def test_accepted_acknowledgment_awaits_the_835(tmp_path):
    """B-02: the same two records, one code different, a completely different answer."""
    scenario = Scenario(
        name="awaiting 835",
        med_submissions=[
            med_837(record_id="MED-837-000501", received_at="2025-09-10T16:40:00Z", clm01=CLM01, dos=DOS),
            med_277(record_id="MED-277-000502", received_at="2025-09-11T08:12:00Z", clm01=CLM01),
        ],
    )
    run = run_scenario(scenario, tmp_path)
    assert run.result().verdict_pair == ("B-02", "C-00")
    assert run.verdict_row(run.only_episode()["episode_id"])["episode_disposition"] == "PENDING", (
        "waiting on an external party is not a defect"
    )


def test_medical_denial(tmp_path):
    """B-10: denied with a reason, and nobody has decided appeal-or-write-off yet."""
    money = medical_money()
    scenario = Scenario(
        name="medical denial",
        med_submissions=[
            med_837(record_id="MED-837-000501", received_at="2025-09-10T16:40:00Z", clm01=CLM01, dos=DOS),
            med_277(record_id="MED-277-000502", received_at="2025-09-11T08:12:00Z", clm01=CLM01),
        ],
        med_remittances=[
            med_835(
                record_id="MED-835-000601",
                received_at="2025-09-24T06:00:00Z",
                effective="2025-09-23",
                trace="9012734410",
                lines=[
                    med_claim_line(
                        clm01=CLM01,
                        charge=money.charge,
                        paid=0,
                        patient=0,
                        adjustments=[("CO", "50", money.charge)],
                        clp02="4",
                    )
                ],
            )
        ],
    )
    run = run_scenario(scenario, tmp_path)
    assert run.result().dimensions.md_remittance == "DENIED"
    assert run.result().dimensions.md_appeal == "NOT_FILED"
    assert run.result().verdict_pair == ("B-10", "C-00")
    assert "DENIED" in run.reasons(run.only_episode()["episode_id"])


def test_denial_then_appeal_won_is_not_a_clean_payment(tmp_path):
    """B-12, and the reason the original leg decides ``md_remittance``.

    Both legs together add up to the full expected amount. Judging by the total would report B-04,
    "paid in full first time", and erase the appeal entirely — along with the fact that somebody
    had to fight for the money.
    """
    money = medical_money()
    scenario = Scenario(
        name="denial then appeal won",
        med_submissions=[
            med_837(record_id="MED-837-000501", received_at="2025-09-10T16:40:00Z", clm01=CLM01, dos=DOS),
            med_277(record_id="MED-277-000502", received_at="2025-09-11T08:12:00Z", clm01=CLM01),
            # An appeal resolved by reprocessing: frequency 7, pointing back at the original ICN.
            # X12 has no field that says "this is an appeal".
            med_837(
                record_id="MED-837-000512",
                received_at="2025-10-22T09:05:00Z",
                clm01=CLM01,
                dos=DOS,
                frequency="7",
                original_icn="20260610088410",
                drop_line=True,
            ),
        ],
        med_remittances=[
            med_835(
                record_id="MED-835-000601",
                received_at="2025-09-24T06:00:00Z",
                effective="2025-09-23",
                trace="9012734410",
                lines=[
                    med_claim_line(
                        clm01=CLM01,
                        charge=money.charge,
                        paid=0,
                        patient=0,
                        adjustments=[("CO", "50", money.charge)],
                        clp02="4",
                        clp07="20260610088410",
                    )
                ],
            ),
            med_835(
                record_id="MED-835-000777",
                received_at="2025-11-20T06:00:00Z",
                effective="2025-11-19",
                trace="9012739999",
                lines=[
                    med_claim_line(
                        clm01=CLM01,
                        charge=money.charge,
                        paid=money.expected,
                        patient=money.patient,
                        adjustments=[
                            ("CO", "45", money.charge - money.allowed),
                            ("PR", "2", money.patient),
                        ],
                        # A reprocessed claim gets a brand-new ICN while CLM01 never changes.
                        clp07="20260610099999",
                    )
                ],
            ),
        ],
        bank_rows=[
            bank_row(
                posting="2025-11-19",
                amount=money.expected,
                trn02="9012739999",
                company="BLUE HARBOR HEALTH",
                company_id="1622109834",
                trace_suffix=4,
            )
        ],
    )
    run = run_scenario(scenario, tmp_path)
    dimensions = run.result().dimensions
    assert dimensions.md_remittance == "DENIED", "the payer's first word was a denial"
    assert dimensions.md_appeal == "WON"
    assert run.result().verdict_pair == ("B-12", "C-00")
    assert run.verdict_row(run.only_episode()["episode_id"])["episode_disposition"] == "CLOSED"
    # Two distinct ICNs under one CLM01 — correct payer behaviour a connector must not mistake
    # for two unrelated claims.
    icns = {
        row["clp07"] for row in run.normalized("REMITTANCE_CLAIM_LINE") if row["clp07"]
    }
    assert len(icns) == 2
    assert len(run.episodes()) == 1, "one claim, one episode, however many times it is reprocessed"


# ═══ 7. Duplicate delivery, and duplicate payment ═════════════════════════


def test_duplicate_delivery_is_collapsed_not_summed(tmp_path):
    """D-1: the same payload twice. Summing it would double-count real money."""
    scenario = _fully_reconciled_pharmacy()
    duplicate = dict(scenario.pbm_remittances[0])
    duplicate["received_at"] = "2025-09-19T06:00:00Z"
    scenario.pbm_remittances.append(duplicate)

    run = run_scenario(scenario, tmp_path)
    assert run.stats.duplicates_collapsed >= 1
    assert len(run.normalized("REMITTANCE")) == 1, "the second delivery is not a second remittance"
    assert len(run.normalized("REMITTANCE_CLAIM_LINE")) == 1
    assert run.result().verdict_pair == ("A-04", "C-00"), (
        "a re-delivered remittance must not turn a clean claim into a duplicate payment"
    )


def test_duplicate_payment_is_two_real_events(tmp_path):
    """A-17: two payment events for one claim, with *different* record ids.

    The contrast with the test above is the whole point. Idempotency keys on the source record id
    precisely so that two genuine payment events are not collapsed the way one re-delivered record
    is.
    """
    money = pharmacy_money()
    scenario = _fully_reconciled_pharmacy()
    scenario.pbm_remittances.append(
        pbm_835(
            record_id="PBM-835-000999",
            received_at="2025-09-27T06:00:00Z",
            effective="2025-09-26",
            trace="8873023333",
            lines=[
                claim_line(
                    rx=RX,
                    dos=DOS,
                    charge=money.charge,
                    paid=money.expected,
                    patient=money.patient,
                    adjustments=[
                        ("CO", "45", money.charge - money.allowed),
                        ("PR", "3", money.patient),
                    ],
                    clp07="20260610045555",
                )
            ],
        )
    )
    scenario.bank_rows.append(
        bank_row(posting="2025-09-26", amount=money.expected, trn02="8873023333", trace_suffix=5)
    )
    run = run_scenario(scenario, tmp_path)
    assert len(run.normalized("REMITTANCE_CLAIM_LINE")) == 2
    assert run.result().dimensions.ph_payment == "DUPLICATE"
    assert run.result().verdict_pair == ("A-17", "C-00")
    assert "DUPLICATE_PAYMENT" in run.reasons(run.only_episode()["episode_id"])


# ═══ 8. Late-arriving status, and the out-of-order deposit ════════════════


def test_deposit_arriving_before_its_remittance_is_parked_then_resolved(tmp_path):
    """D-2, and why the backward re-check is not optional.

    The bank deposit beats its own remittance to the door. Without step two of the crosswalk it
    would never get a second chance to match, and the money would be orphaned permanently.
    """
    money = pharmacy_money()
    scenario = _fully_reconciled_pharmacy()
    # The deposit arrives a week before the remittance that explains it.
    scenario.bank_rows = [
        bank_row(
            posting="2025-09-10",
            amount=money.expected,
            trn02=TRACE,
            received_at="2025-09-10T17:50:00Z",
        )
    ]
    run = run_scenario(scenario, tmp_path)

    # It parked on arrival, and a later record cleared it. The park row survives — "was this
    # parked on 12 September?" must stay answerable.
    all_parks = run.parked(unresolved_only=False)
    assert len(all_parks) == 1
    assert all_parks[0]["record_kind"] == "BANK_TRANSACTION"
    assert all_parks[0]["resolved_by_norm_id"] is not None, "the remittance cleared the park"
    assert run.parked() == [], "nothing should remain unresolved"
    assert run.stats.parks_resolved >= 1

    assert run.allocated_to_episode(run.only_episode()["episode_id"]) == money.expected
    assert run.result().verdict_pair == ("A-04", "C-00")


def test_addenda_loss_forces_amount_and_date_matching(tmp_path):
    """~20% of deposits lose ``trn02``, and then amount plus date is all there is.

    The basis is recorded as ``AMOUNT_DATE`` rather than ``TRN02``, so the weaker match is visible
    in the audit trail instead of being passed off as a trace-number hit.
    """
    money = pharmacy_money()
    scenario = _fully_reconciled_pharmacy()
    scenario.bank_rows = [bank_row(posting="2025-09-17", amount=money.expected, trn02=None)]
    run = run_scenario(scenario, tmp_path)

    assert run.normalized("BANK_TRANSACTION")[0]["trn02"] is None
    allocations = run.allocations()
    assert allocations and allocations[0]["basis"] == "AMOUNT_DATE"
    assert run.result().verdict_pair == ("A-04", "C-00")


# ═══ 9. Identifier drift — D-6, which must NOT resolve ═══════════════════


def test_identifier_drift_must_not_resolve(tmp_path):
    """D-6, and Decision A23 in one assertion.

    The remittance spells the Rx number zero-padded. It is *meant* to miss: the miss is the
    crosswalk-failure exception. A connector that normalised it away would silently delete the
    defect it exists to surface — so this test fails if the claim line ever resolves.
    """
    money = pharmacy_money()
    scenario = _fully_reconciled_pharmacy()
    drifted = "0" + RX
    scenario.pbm_remittances[0]["claim_payments"][0][
        "clp01_patient_control_number"
    ] = f"{drifted}FILL00"
    # Strip the authorization number too, so the only available bridge is the drifted key.
    scenario.pbm_remittances[0]["claim_payments"][0]["ref_authorization_number"] = None
    run = run_scenario(scenario, tmp_path)

    line = run.normalized("REMITTANCE_CLAIM_LINE")[0]
    assert line["rx_number"] == drifted, "the drift must survive into storage, verbatim"

    unresolved = run.parked()
    kinds = {row["record_kind"] for row in unresolved}
    assert "REMITTANCE_CLAIM_LINE" in kinds, (
        "the drifted claim line must park; resolving it would delete the D-6 exception"
    )
    assert any(row["park_reason"] == "NO_KEY_MATCH" for row in unresolved)

    # The episode therefore never sees its payment, which is the correct reading of the feeds.
    assert run.result().dimensions.ph_payment == "NONE"
    assert run.result().verdict_pair == ("A-02", "C-00")


# ═══ 10. The 340B chain, gate by gate ════════════════════════════════════


def _with_340b_chain(scenario: Scenario, *, through: str) -> Scenario:
    """Add the 340B records up to a named gate, so each stop on the chain is testable."""
    money = pharmacy_money()
    scenario.pbm_claims[0]["submission_clarification_code"] = "20"
    stages = ["pending", "qualified", "requested", "approved", "paid"]
    assert through in stages

    if through != "pending":
        scenario.tpa_events.append(
            tpa_event(
                record_id="TPA-EVT-000101",
                received_at="2025-09-04T11:02:00Z",
                event_type="QUALIFICATION_DECISION",
                rx=RX,
                dos=DOS,
                qualification="QUALIFIED",
            )
        )
    else:
        # The dispense reached the TPA and no decision has come back: a row with a null status.
        scenario.tpa_events.append(
            tpa_event(
                record_id="TPA-EVT-000101",
                received_at="2025-09-04T11:02:00Z",
                event_type="QUALIFICATION_DECISION",
                rx=RX,
                dos=DOS,
                qualification=None,
            )
        )
    if stages.index(through) >= stages.index("requested"):
        scenario.tpa_events.append(
            tpa_event(
                record_id="TPA-EVT-000155",
                received_at="2025-09-15T10:05:00Z",
                event_type="REBATE_REQUEST",
                rx=RX,
                dos=DOS,
                submission_date="2025-09-14",
            )
        )
    if through == "approved":
        scenario.tpa_events.append(
            tpa_event(
                record_id="TPA-EVT-000198",
                received_at="2025-09-28T08:00:00Z",
                event_type="MANUFACTURER_DECISION",
                rx=RX,
                dos=DOS,
                manufacturer_status="APPROVED",
                source_system="MANUFACTURER_REBATE",
            )
        )
    if through == "paid":
        scenario.tpa_events.append(
            rebate_batch(
                record_id="TPA-EVT-000230",
                received_at="2025-10-02T08:15:00Z",
                effective="2025-10-01",
                allocation_code="RBT-20251001-01",
                dispenses=[dispense_line(rx=RX, dos=DOS, amount=money.rebate)],
            )
        )
        manufacturer = __import__(
            "recon.reference.entities", fromlist=["x"]
        ).manufacturer_for_ndc(PH_DRUG.ndc11)
        scenario.bank_rows.append(
            bank_row(
                posting="2025-10-01",
                amount=money.rebate,
                trn02="RBT-20251001-01",
                company=manufacturer.name,
                company_id=manufacturer.company_id,
                # A rebate is NOT a claim payment under the CORE mandate, so no HCCLAIMPMT.
                entry="CCD",
                trace_suffix=9,
            )
        )
    return scenario


@pytest.mark.parametrize(
    "through,expected",
    [
        ("pending", "C-01"),
        ("qualified", "C-03"),
        ("requested", "C-05"),
        ("approved", "C-11"),
        ("paid", "C-08"),
    ],
)
def test_the_340b_chain_stops_where_the_records_stop(tmp_path, through: str, expected: str):
    """Each gate on the 340B chain, and the verdict it produces.

    Two independent decision-makers sit on this chain: the TPA qualifies, the manufacturer approves
    and pays. Walking the gates one at a time is how you check that the engine reads the chain
    rather than guessing from the endpoint.
    """
    scenario = _with_340b_chain(_fully_reconciled_pharmacy(), through=through)
    run = run_scenario(scenario, tmp_path)
    assert run.result().rebate_verdict == expected
    assert run.result().reimbursement_verdict == "A-04", (
        "the reimbursement track is unaffected by where the rebate chain stopped"
    )


def test_rebate_paid_and_matched_allocates_through_the_allocation_code(tmp_path):
    """C-08, and the rebate side's two-hop resolution.

    ``allocation_code`` rides to the bank in the ``trn02`` column — same column as a claim
    payment's trace number, same addenda-loss odds, different issuer. One splitter serves both.
    """
    money = pharmacy_money()
    scenario = _with_340b_chain(_fully_reconciled_pharmacy(), through="paid")
    run = run_scenario(scenario, tmp_path)
    episode = run.only_episode()

    assert "ALLOCATION_CODE" in run.key_types()
    batch = run.normalized("REBATE_BATCH")
    assert len(batch) == 1
    lines = run.normalized("REBATE_DISPENSE_LINE")
    assert len(lines) == 1 and lines[0]["parent_norm_id"] == batch[0]["norm_id"]

    rebate_allocations = [
        row
        for row in run.allocations()
        if row["remittance_norm_id"] == batch[0]["norm_id"]
    ]
    assert rebate_allocations and rebate_allocations[0]["allocated_cents"] == money.rebate
    assert rebate_allocations[0]["episode_id"] == episode["episode_id"]

    assert run.result().verdict_pair == ("A-04", "C-08")
    verdict = run.verdict_row(episode["episode_id"])
    assert verdict["episode_disposition"] == "CLOSED"
    assert verdict["expected_rebate_cents"] == money.rebate
    assert verdict["received_rebate_cents"] == money.rebate


def test_rebate_paid_per_tpa_but_no_cash(tmp_path):
    """C-09: the TPA's ledger and the bank disagree."""
    scenario = _with_340b_chain(_fully_reconciled_pharmacy(), through="paid")
    scenario.bank_rows = [row for row in scenario.bank_rows if row["trn02"] == TRACE]
    run = run_scenario(scenario, tmp_path)
    assert run.result().verdict_pair == ("A-04", "C-09")
    assert "REBATE_NO_CASH" in run.reasons(run.only_episode()["episode_id"])


def test_not_qualified_is_a_correct_outcome_not_a_failure(tmp_path):
    """C-02, and the reference data has to actually support the reason given.

    ``UNREGISTERED_LOCATION`` is asserted by the feed *and* true of the episode: the dispense is at
    the satellite pharmacy, which is registered with no covered entity. A dataset that claimed the
    reason while dispensing from the main site would not hang together.
    """
    scenario = _fully_reconciled_pharmacy()
    scenario.pbm_claims[0]["service_provider_id"] = SAT_NPI
    scenario.pbm_claims[0]["submission_clarification_code"] = "20"
    scenario.pbm_remittances[0]["payee_npi"] = SAT_NPI
    scenario.tpa_events.append(
        tpa_event(
            record_id="TPA-EVT-000117",
            received_at="2025-09-06T10:40:00Z",
            event_type="QUALIFICATION_DECISION",
            rx=RX,
            dos=DOS,
            npi=SAT_NPI,
            qualification="NOT_QUALIFIED",
            disqualification_reason="UNREGISTERED_LOCATION",
        )
    )
    run = run_scenario(scenario, tmp_path)
    assert SAT_NPI not in CE.registered_pharmacy_npis, (
        "the reason the feed gives must be true of the reference universe"
    )
    assert run.result().verdict_pair == ("A-04", "C-02")
    verdict = run.verdict_row(run.only_episode()["episode_id"])
    assert verdict["episode_disposition"] == "CLOSED", "a correct decline is not an exception"
    assert verdict["expected_rebate_cents"] == 0
    assert "REBATE_NOT_QUALIFIED" in run.reasons(run.only_episode()["episode_id"])


# ═══ 11. Cross-track compliance — invisible to a single-track system ═══════


def test_x1_reimbursement_refused_while_holding_rebate_money(tmp_path):
    """X-1: the payer refused the claim and we are sitting on rebate money against it.

    Net position is negative and the rebate rests on a claim the payer would not pay. No
    single-track system can see this: the pharmacy side sees a rejection, the 340B side sees a paid
    rebate, and neither sees a problem.
    """
    money = pharmacy_money()
    scenario = Scenario(
        name="X-1",
        pbm_claims=[
            b1(
                record_id="PBM-EVT-000002",
                received_at=f"{DOS}T14:24:08Z",
                rx=RX,
                dos=DOS,
                accepted=False,
                reject_code="75",
                auth=None,
                is_340b=True,
            )
        ],
    )
    scenario = _with_340b_chain(scenario, through="paid")
    run = run_scenario(scenario, tmp_path)
    episode = run.only_episode()

    assert run.result().verdict_pair == ("A-01", "C-08")
    assert "X-1" in run.flags(episode["episode_id"])
    verdict = run.verdict_row(episode["episode_id"])
    assert verdict["episode_disposition"] == "EXCEPTION", (
        "a cross-track compliance flag escalates even though both tracks look fine alone"
    )
    assert "DENIED_WITH_REBATE_PAID" in run.reasons(episode["episode_id"])


def test_x2_rebate_standing_on_a_dispense_that_never_happened(tmp_path):
    """X-2: the drug never reached the patient, so the qualifying event does not exist.

    The rebate is not merely unmatched — it is *invalid*, and it has to be unwound proactively
    rather than discovered by the manufacturer in an audit.
    """
    scenario = _with_340b_chain(_fully_reconciled_pharmacy(), through="paid")
    scenario.pbm_claims.append(
        b2(record_id="PBM-EVT-000318", received_at="2025-09-25T09:11:47Z", rx=RX, dos=DOS)
    )
    run = run_scenario(scenario, tmp_path)
    episode = run.only_episode()
    assert run.result().dimensions.ph_post_event == "REVERSAL_POST_PAY"
    assert "X-2" in run.flags(episode["episode_id"])
    assert "REBATE_ON_UNDISPENSED_CLAIM" in run.reasons(episode["episode_id"])
    assert run.verdict_row(episode["episode_id"])["episode_disposition"] == "EXCEPTION"


def test_x4_is_deliberately_not_an_escalation(tmp_path):
    """X-4: reimbursement clean, rebate rejected. Common, and explicitly NOT a compliance flag.

    This is the row that stops the engine over-reporting. An operator who learns that flags are
    noise stops reading them, so the one flag that means "nothing to see here" earns its place.
    """
    scenario = _with_340b_chain(_fully_reconciled_pharmacy(), through="requested")
    scenario.tpa_events.append(
        tpa_event(
            record_id="TPA-EVT-000198",
            received_at="2025-09-28T08:00:00Z",
            event_type="MANUFACTURER_DECISION",
            rx=RX,
            dos=DOS,
            manufacturer_status="REJECTED",
            rejection_reason="NON_CONFORMING_45_DAY",
            source_system="MANUFACTURER_REBATE",
        )
    )
    run = run_scenario(scenario, tmp_path)
    episode = run.only_episode()
    assert run.result().verdict_pair == ("A-04", "C-07")
    assert "X-4" in run.flags(episode["episode_id"])
    assert run.verdict_row(episode["episode_id"])["episode_disposition"] == "CLOSED", (
        "X-4 must not escalate: the reimbursement is clean and only the rebate failed"
    )


def test_x5_correlated_cash_gap_reports_insufficient_data(tmp_path):
    """X-5: no cash on both tracks at once.

    Almost certainly one cause on our side rather than two payers independently failing — so the
    honest answer is that the engine cannot attribute it.
    """
    scenario = _with_340b_chain(_fully_reconciled_pharmacy(), through="paid")
    scenario.bank_rows = []
    run = run_scenario(scenario, tmp_path)
    episode = run.only_episode()
    assert run.result().verdict_pair == ("A-05", "C-09")
    assert "X-5" in run.flags(episode["episode_id"])
    reasons = run.reasons(episode["episode_id"])
    assert "CORRELATED_CASH_GAP" in reasons
    assert "INSUFFICIENT_DATA" in reasons


# ═══ 12. The medical 340B key, and the ambiguity it cannot resolve ════════


def test_ambiguous_medical_340b_key_is_parked_not_guessed(tmp_path):
    """Two administrations of the same drug at the same site on the same day.

    ``{provider_npi, ndc11, service_date}`` is the whole key and it does not separate them. Picking
    one would attribute real money to a coin flip and look identical to a correct match in every
    report afterwards, so the connector must park the ambiguity.
    """
    money = medical_money()
    scenario = Scenario(
        name="ambiguous medical 340B key",
        med_submissions=[
            med_837(record_id="MED-837-000501", received_at="2025-09-10T16:40:00Z", clm01="ENC-A", dos=DOS),
            med_277(record_id="MED-277-000502", received_at="2025-09-11T08:12:00Z", clm01="ENC-A"),
            # Same provider, same drug, same day — a different encounter the key cannot tell apart.
            med_837(record_id="MED-837-000502", received_at="2025-09-10T17:10:00Z", clm01="ENC-B", dos=DOS),
            med_277(
                record_id="MED-277-000503",
                received_at="2025-09-11T08:13:00Z",
                clm01="ENC-B",
                icn="20260610077999",
            ),
        ],
        tpa_events=[
            tpa_event(
                record_id="TPA-EVT-000142",
                received_at="2025-09-12T09:30:00Z",
                event_type="QUALIFICATION_DECISION",
                rx=None,
                dos=DOS,
                provider_npi=PROVIDER_NPI,
                qualification="QUALIFIED",
            )
        ],
    )
    run = run_scenario(scenario, tmp_path)

    assert len(run.episodes()) == 2
    unresolved = run.parked()
    assert any(
        row["record_kind"] == "TPA_QUALIFICATION" and row["park_reason"] == "AMBIGUOUS_KEY_MATCH"
        for row in unresolved
    ), f"expected an ambiguous park, got {[dict(r) for r in unresolved]}"
    # Neither episode gets the rebate track, because neither can be shown to own it.
    for result in run.results:
        assert result.rebate_verdict == "C-00"


# ═══ 13. One deposit, many claims — the splitter ══════════════════════════


def test_one_deposit_splits_across_many_claims(tmp_path):
    """Hop two: the remittance holds the claim list, and the deposit is split across it.

    A bank line carries no claim identifier at all, so the only route to the claims is through the
    remittance it points at — never by scanning claims looking for one whose amount matches.
    """
    money = pharmacy_money()
    rx_numbers = [f"784510{n}" for n in range(1, 6)]
    scenario = Scenario(
        name="lump deposit",
        pbm_claims=[
            b1(
                record_id=f"PBM-EVT-{index:06d}",
                received_at=f"{DOS}T1{index}:00:00Z",
                rx=rx,
                dos=DOS,
                auth=f"AUTH000{index:04d}A",
            )
            for index, rx in enumerate(rx_numbers, start=1)
        ],
        pbm_remittances=[
            pbm_835(
                record_id="PBM-835-000412",
                received_at="2025-09-18T06:00:00Z",
                effective="2025-09-17",
                trace=TRACE,
                lines=[
                    claim_line(
                        rx=rx,
                        dos=DOS,
                        charge=money.charge,
                        paid=money.expected,
                        patient=money.patient,
                        adjustments=[
                            ("CO", "45", money.charge - money.allowed),
                            ("PR", "3", money.patient),
                        ],
                        clp07=f"2026061004{index:04d}",
                        auth=f"AUTH000{index:04d}A",
                    )
                    for index, rx in enumerate(rx_numbers, start=1)
                ],
            )
        ],
        bank_rows=[
            bank_row(posting="2025-09-17", amount=money.expected * len(rx_numbers), trn02=TRACE)
        ],
    )
    run = run_scenario(scenario, tmp_path)

    assert len(run.episodes()) == 5
    allocations = run.allocations()
    assert len({row["bank_norm_id"] for row in allocations}) == 1, "one deposit"
    assert len({row["episode_id"] for row in allocations}) == 5, "five episodes"
    assert sum(row["allocated_cents"] for row in allocations) == money.expected * 5
    for result in run.results:
        assert result.verdict_pair == ("A-04", "C-00")


def test_orphan_deposit_is_parked_never_recognised(tmp_path):
    """D-3: cash with nothing to attribute it to.

    Not a windfall. Cash held without provenance is audit exposure, and it may well be a crosswalk
    failure rather than genuinely unattributable money — which is why it parks rather than being
    written off.
    """
    from recon.db import repository

    scenario = _fully_reconciled_pharmacy()
    scenario.bank_rows.append(
        bank_row(posting="2025-09-20", amount=777_77, trn02=None, trace_suffix=8)
    )
    run = run_scenario(scenario, tmp_path)

    orphans = repository.orphan_deposits(run.conn, run.settings.max_cursor)
    assert len(orphans) == 1
    assert orphans[0].amount_cents == 777_77
    # And the real claim is unaffected.
    assert run.result().verdict_pair == ("A-04", "C-00")


# ═══ 14. Cursor replay — the same code path in both directions ════════════


def test_the_cursor_reproduces_earlier_answers(tmp_path):
    """"What did we believe on 10 September?" is a cursor value, not a feature.

    Walking one episode forward through its own timeline: accepted and awaiting payment, then paid
    but no cash yet, then fully reconciled. Every step uses the identical code path — there is no
    separate replay mode.
    """
    money = pharmacy_money()
    scenario = _fully_reconciled_pharmacy()
    run = run_scenario(scenario, tmp_path)

    # Day one: the claim has been filed and nothing else exists. Awaiting payment, no defect.
    assert run.verdict_at("2025-09-03T23:59:59Z") == ("A-02", "C-00")

    # The deposit posts on the 17th, a day *before* the remittance that explains it. On its own a
    # deposit tells you nothing at all — it carries no claim identifier, so at this cursor the
    # episode still has no visible payment and the money is parked, unattributable.
    assert run.verdict_at("2025-09-17T23:59:59Z") == ("A-02", "C-00"), (
        "cash with no remittance to explain it cannot settle a claim"
    )

    # The 18th brings the remittance, which resolves the parked deposit and closes the episode.
    assert run.verdict_at("2025-09-18T23:59:59Z") == ("A-04", "C-00")
    assert run.verdict_at(run.settings.max_cursor) == ("A-04", "C-00")

    # And going backwards gives the earlier answers again: the cursor is not a ratchet, and
    # replay runs the same code path in both directions.
    assert run.verdict_at("2025-09-17T23:59:59Z") == ("A-02", "C-00")
    assert run.verdict_at("2025-09-03T23:59:59Z") == ("A-02", "C-00")


def test_an_episode_with_no_new_record_cannot_change(tmp_path):
    """The property that makes event-driven processing provably complete (Decision A4).

    Aging is never a verdict input, so two cursors with no new arrival between them must produce
    identical dimensions. If this failed, the set of episodes needing recomputation would be
    unknowable and incremental processing would be a guess.
    """
    run = run_scenario(_fully_reconciled_pharmacy(), tmp_path)
    quiet_one = run.dimensions_at("2025-09-19T23:59:59Z")
    quiet_two = run.dimensions_at("2025-09-30T23:59:59Z")
    assert quiet_one == quiet_two, (
        "eleven days passed, no record arrived, and the answer moved anyway"
    )


def test_reopening_is_recorded_when_a_closed_episode_comes_undone(tmp_path):
    """``reopened_from`` is a flag, never a fourth disposition.

    A settled claim that is later clawed back lands in whichever of the three dispositions the
    recomputation produces — and carries the provenance of where it fell from, because
    reopened-from-closed outranks never-paid when the queue is prioritised.
    """
    from recon.db import repository
    from recon.engine import run as engine_run

    money = pharmacy_money()
    scenario = _fully_reconciled_pharmacy()
    scenario.pbm_remittances.append(
        pbm_835(
            record_id="PBM-835-000601",
            received_at="2025-10-06T06:00:00Z",
            effective="2025-10-05",
            trace="8873021888",
            lines=[],
            plb=[("WO", "AUTH0098231A", 412_60)],
        )
    )
    run = run_scenario(scenario, tmp_path, cursor="2025-09-30T23:59:59Z")
    episode = run.only_episode()
    first = run.verdict_row(episode["episode_id"])
    assert first["episode_disposition"] == "CLOSED"

    # Advance the cursor past the clawback and recompute.
    engine_run.run_for_episodes(run.conn, [episode["episode_id"]], run.settings.max_cursor)
    latest = run.verdict_row(episode["episode_id"])
    assert latest["episode_disposition"] == "EXCEPTION"
    assert latest["reopened_from"] == "CLOSED"
    assert latest["previously_closed_at"] == "2025-09-30T23:59:59Z"

    # The audit trail keeps both, in order. Deleting the earlier belief would make "what did we
    # think in September?" unanswerable.
    history = repository.verdict_history(run.conn, episode["episode_id"])
    assert [v.episode_disposition.value for v in history] == ["CLOSED", "EXCEPTION"]


def test_an_episode_does_not_exist_before_its_anchor_arrives(tmp_path):
    """Replaying to a cursor before the claim was filed must evaluate nothing.

    This one was a real bug, and it was invisible until the dashboard evaluated at monthly cursors.
    ``run_all`` evaluated *every* episode at every cursor, and an episode whose claim event had not
    arrived yet has no evidence at all — which reads identically to "rejected at the point of sale".
    So claims that had not happened were reported as A-01, CLOSED, expected zero: confidently wrong
    about a claim that did not exist, and wrong in the most reassuring direction.

    An episode comes into existence when its anchor record arrives. Before that there is nothing to
    have an opinion about.
    """
    from recon.engine import run as engine_run

    run = run_scenario(_fully_reconciled_pharmacy(), tmp_path)
    episode = run.only_episode()

    # The claim was filed on 2 September; a month earlier nothing existed.
    before = engine_run.run_for_episodes(
        run.conn, [episode["episode_id"]], "2025-08-01T23:59:59Z"
    )
    assert before == [], "an episode was evaluated before its anchor record arrived"

    # And on the day itself it does exist, awaiting payment.
    after = engine_run.run_for_episodes(
        run.conn, [episode["episode_id"]], "2025-09-02T23:59:59Z"
    )
    assert len(after) == 1
    assert after[0].verdict_pair == ("A-02", "C-00")
