"""
Policy-engine + guardrail tests for Case by Casey.

The point of these tests: the entire refund decision system is verifiable
WITHOUT calling an LLM, because the decisions live in deterministic code, not
in the model. Everything here runs in milliseconds.

Three groups:
  1. Regression — all 15 CRM scenarios resolve to the correct decision + amount.
  2. Guardrails — the executor and the manager refuse out-of-policy actions.
  3. Derived facts — counts and windows are computed from records, not trusted.

Item condition (R3) is CUSTOMER-REPORTED: the engine can't see inside the box,
so callers pass `reported_condition` ("unopened" / "opened_undamaged" / "used").
Scenarios where an earlier rule decides (digital, damage, wrong-item, gift,
subscription, fraud, out-of-window) never reach R3, so condition is irrelevant.

Note: the mock CRM uses dates relative to "now" (the challenge is about the
30-day window), calibrated to hold through the review period.
"""

import pytest

from app.tools import (
    ESCALATIONS,
    REFUNDS_LEDGER,
    _DB,
    _refunds_last_90_days,
    escalate_to_manager,
    get_order,
    process_refund,
    resolve_escalation,
    validate_refund_eligibility,
)


@pytest.fixture(autouse=True)
def _clean_ledgers():
    """Each test starts with empty in-memory ledgers."""
    REFUNDS_LEDGER.clear()
    ESCALATIONS.clear()
    yield
    REFUNDS_LEDGER.clear()
    ESCALATIONS.clear()


# ------------------------------------------------------------- 1. regression

# (customer, order, reported_condition, expected_decision, expected_amount)
SCENARIOS = [
    ("CUST-1001", "ORD-70011", "unopened", "approved_full", 68.0),           # in window
    ("CUST-1002", "ORD-70022", "unopened", "denied", 0.0),                   # outside window
    ("CUST-1003", "ORD-70033", "unopened", "denied", 0.0),                   # final sale
    ("CUST-1004", "ORD-70044", "unknown", "denied", 0.0),                    # digital, downloaded
    ("CUST-1005", "ORD-70055", "unknown", "approved_full", 59.0),            # digital, not downloaded
    ("CUST-1006", "ORD-70066", "unknown", "approved_full", 129.0),           # damaged + photos
    ("CUST-1007", "ORD-70077", "unknown", "approved_full", 118.0),           # wrong item shipped
    ("CUST-1008", "ORD-70088", "unopened", "escalated", 0.0),               # serial returner (R8)
    ("CUST-1009", "ORD-70099", "unopened", "escalated", 749.99),            # high value (R9)
    ("CUST-1010", "ORD-70110", "opened_undamaged", "approved_partial", 178.5),  # 15% restock
    ("CUST-1011", "ORD-70121", "unopened", "approved_store_credit", 75.0),  # gift -> store credit
    ("CUST-1012", "ORD-70132", "unopened", "escalated", 0.0),              # fraud flag (R10)
    ("CUST-1013", "ORD-70143", "unknown", "approved_prorated", 108.0),     # subscription
    ("CUST-1014", "ORD-70155", "unopened", "approved_full", 96.0),         # 2nd order, in window
    ("CUST-1015", "ORD-70166", "unopened", "approved_full", 39.0),         # standard, in window
]


@pytest.mark.parametrize("customer,order,condition,decision,amount", SCENARIOS)
def test_scenario_decides_correctly(customer, order, condition, decision, amount):
    v = validate_refund_eligibility(customer, order, reported_condition=condition)
    assert v["recommended_decision"] == decision, v["summary"]
    assert v["recommended_amount_usd"] == pytest.approx(amount)


def test_unknown_condition_asks_the_customer():
    """A standard good with no reported condition must not auto-decide (R3)."""
    v = validate_refund_eligibility("CUST-1001", "ORD-70011")  # condition unknown
    assert v["recommended_decision"] == "needs_more_info"


def test_every_decision_cites_at_least_one_rule():
    for customer, order, condition, *_ in SCENARIOS:
        v = validate_refund_eligibility(customer, order, reported_condition=condition)
        assert v["rule_evaluations"], f"{order} produced no rule citations"


# ------------------------------------------------------------- 2. guardrails

def test_executor_refuses_refund_on_escalated_case():
    """Casey cannot pay out a case the engine says must escalate (fraud account)."""
    r = process_refund("CUST-1012", "ORD-70132", "approved_full",
                       1899.0, ["R2"], "customer was insistent",
                       reported_condition="unopened")
    assert r["executed"] is False
    assert r["guardrail"] == "POLICY_VIOLATION_BLOCKED"


def test_executor_refuses_wrong_amount():
    """Requesting the pre-restocking-fee amount is blocked."""
    r = process_refund("CUST-1010", "ORD-70110", "approved_partial",
                       210.0, ["R3"], "skip the restocking fee",
                       reported_condition="opened_undamaged")
    assert r["executed"] is False
    assert r["guardrail"] == "AMOUNT_MISMATCH_BLOCKED"


def test_executor_allows_legitimate_refund():
    r = process_refund("CUST-1001", "ORD-70011", "approved_full",
                       68.0, ["R2", "R3"], "unopened within window",
                       reported_condition="unopened")
    assert r["executed"] is True
    assert r["amount_usd"] == pytest.approx(68.0)
    assert r["refund_id"].startswith("RFD-")


def test_manager_cannot_approve_fraud_account():
    """Not even tier-2 can refund a fraud-flagged account (R10)."""
    t = escalate_to_manager("CUST-1012", "ORD-70132", "R10 fraud review")
    r = resolve_escalation(t["ticket_id"], "approved_manager",
                           1899.0, ["R10"], "override attempt")
    assert r["resolved"] is False
    assert r["guardrail"] == "MANAGER_FRAUD_BLOCKED"


def test_manager_cannot_invent_amount():
    """Tier-2 has more authority, not more arithmetic freedom."""
    t = escalate_to_manager("CUST-1009", "ORD-70099", "R9 high value",
                            reported_condition="unopened")
    r = resolve_escalation(t["ticket_id"], "approved_manager",
                           900.0, ["R9"], "rounding up")
    assert r["resolved"] is False
    assert r["guardrail"] == "MANAGER_AMOUNT_MISMATCH"


def test_manager_approves_eligible_high_value():
    """Whitney's $749 projector is eligible; only the amount gate stopped Casey."""
    t = escalate_to_manager("CUST-1009", "ORD-70099", "R9 high value",
                            reported_condition="unopened")
    r = resolve_escalation(t["ticket_id"], "approved_manager",
                           749.99, ["R2", "R3", "R9"], "eligible; high-value review done")
    assert r["resolved"] is True
    assert r["amount_usd"] == pytest.approx(749.99)
    assert r["approved_by"] == "manager_agent"


def test_resolved_ticket_cannot_be_resolved_twice():
    t = escalate_to_manager("CUST-1008", "ORD-70088", "R8")
    first = resolve_escalation(t["ticket_id"], "denied_manager", 0.0, ["R8"], "abuse pattern")
    assert first["resolved"] is True
    again = resolve_escalation(t["ticket_id"], "denied_manager", 0.0, ["R8"], "dup")
    assert again["resolved"] is False


# ---------------------------------------------------------- 3. derived facts

def test_refund_count_is_derived_and_excludes_old_records():
    """Gary has 5 refunds on record; only the 4 inside 90 days count (R8)."""
    gary = _DB["CUST-1008"]
    count, recent = _refunds_last_90_days(gary)
    assert count == 4
    assert len(recent) == 4
    assert all("2026-02" not in r["refund_date"] for r in recent)  # Feb one excluded


def test_r1_blocks_cross_customer_order_access():
    """An order that isn't the verified customer's is refused (R1)."""
    r = get_order("CUST-1001", "ORD-70088")  # Gary's earbuds, Maria's id
    assert r["found"] is False
    assert "R1" in r["error"]
