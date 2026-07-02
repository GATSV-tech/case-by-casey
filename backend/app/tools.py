"""
Tools for Casey, the AI refund support agent.

Design principle: the LLM never does date math, threshold checks, or fee
arithmetic. A deterministic policy engine (`validate_refund_eligibility`)
computes hard facts and rule evaluations; the agent reasons over them and
handles the conversation. `process_refund` re-runs validation as a guardrail,
so even a confused (or manipulated) agent cannot execute an out-of-policy
refund — the tool itself refuses.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

with open(DATA_DIR / "customers.json") as f:
    _DB = {c["customer_id"]: c for c in json.load(f)["customers"]}

REFUND_POLICY = (DATA_DIR / "refund_policy.md").read_text()

# In-memory ledgers (reset on restart — fine for a vertical slice).
REFUNDS_LEDGER: list[dict] = []
ESCALATIONS: list[dict] = []

STANDARD_WINDOW_DAYS = 30
DIGITAL_WINDOW_DAYS = 14
RESTOCKING_FEE = 0.15
ABUSE_THRESHOLD = 3          # more than this many refunds in 90d -> escalate
HIGH_VALUE_LIMIT = 500.0     # refunds above this -> escalate
SUBSCRIPTION_ADMIN_MONTHS = 1


def _today() -> date:
    return date.today()


def _days_since(iso: str) -> int:
    return (_today() - datetime.strptime(iso, "%Y-%m-%d").date()).days


def _refunds_last_90_days(customer: dict) -> tuple[int, list[dict]]:
    """Derive the trailing-90-day refund count from actual refund records.

    Same philosophy as the R2 window check: never trust a stored aggregate —
    count real records with real date math.
    """
    recent = [r for r in customer.get("refund_history", [])
              if _days_since(r["refund_date"]) <= 90]
    return len(recent), recent


# ---------------------------------------------------------------- lookups

def lookup_customer(email: str) -> dict:
    """Find a customer by email. Returns profile + order summaries."""
    email = email.strip().lower()
    for c in _DB.values():
        if c["email"].lower() == email:
            count_90d, recent = _refunds_last_90_days(c)
            return {
                "found": True,
                "customer_id": c["customer_id"],
                "name": c["name"],
                "loyalty_tier": c["loyalty_tier"],
                "refunds_last_90_days": count_90d,
                "recent_refund_history": [
                    {"item": r["item"], "amount_usd": r["amount_usd"],
                     "refund_date": r["refund_date"], "reason": r["reason"]}
                    for r in recent
                ],
                "account_flags": c["account_flags"],
                "orders": [
                    {
                        "order_id": o["order_id"],
                        "item": o["item"],
                        "price_usd": o["price_usd"],
                        "delivered_date": o.get("delivered_date"),
                        "delivery_status": o["delivery_status"],
                    }
                    for o in c["orders"]
                ],
            }
    return {"found": False, "error": f"No customer found for {email}."}


def get_order(customer_id: str, order_id: str) -> dict:
    """Full detail for one order, only if it belongs to this customer (R1)."""
    c = _DB.get(customer_id)
    if not c:
        return {"found": False, "error": f"Unknown customer {customer_id}."}
    for o in c["orders"]:
        if o["order_id"] == order_id:
            return {"found": True, "order": o}
    return {
        "found": False,
        "error": f"Order {order_id} does not belong to {customer_id}. "
                 "Per policy R1, do not discuss another customer's order.",
    }


# ------------------------------------------------------- policy engine

def validate_refund_eligibility(customer_id: str, order_id: str,
                                skip_escalation_gates: bool = False,
                                reported_condition: str = "unknown") -> dict:
    """Deterministic evaluation of every policy rule for one order.

    Returns hard facts, per-rule evaluations, and a recommended decision.
    The agent must cite these rule IDs; it may not contradict them.

    `skip_escalation_gates` is for MANAGER review only: it skips the R8/R9
    escalation gates to reveal the underlying item-level eligibility a manager
    is deciding on. R10 (fraud) is never skipped for anyone.
    """
    c = _DB.get(customer_id)
    if not c:
        return {"error": f"Unknown customer {customer_id}."}
    order = next((o for o in c["orders"] if o["order_id"] == order_id), None)
    if not order:
        return {"error": f"Order {order_id} not found for {customer_id} (R1)."}

    attrs = order.get("attributes", {})
    price = order["price_usd"]
    delivered = order.get("delivered_date")
    days = _days_since(delivered) if delivered else None
    rules: list[dict] = []

    def rule(rid: str, result: str, detail: str) -> None:
        rules.append({"rule": rid, "result": result, "detail": detail})

    # --- R10 fraud (hard stop)
    if "fraud_review" in c["account_flags"]:
        rule("R10", "fail", "Account is under fraud review.")
        return _verdict(order, rules, "escalated", 0.0, "none",
                        "Manual review required before any refund action. "
                        "Do NOT disclose the fraud flag to the customer (R10).")

    # --- R8 abuse threshold (count derived from real refund records, not a stored field)
    count_90d, recent = _refunds_last_90_days(c)
    if not skip_escalation_gates and (count_90d > ABUSE_THRESHOLD or "serial_returner_watch" in c["account_flags"]):
        receipts = "; ".join(f"{r['item']} on {r['refund_date']}" for r in recent) or "none"
        rule("R8", "fail",
             f"{count_90d} refunds in trailing 90 days (threshold {ABUSE_THRESHOLD}) "
             f"[counted: {receipts}]"
             + (" + serial-returner watch flag." if "serial_returner_watch" in c["account_flags"] else "."))
        return _verdict(order, rules, "escalated", 0.0, "none",
                        "Refund requires human manager approval (R8).")

    # --- R12 subscription
    if attrs.get("subscription"):
        term = attrs["term_months"]
        used = attrs["months_used"]
        monthly = price / term
        refundable_months = max(0, term - used - SUBSCRIPTION_ADMIN_MONTHS)
        amount = round(monthly * refundable_months, 2)
        rule("R12", "pass",
             f"{used}/{term} months used; {refundable_months} months refundable "
             f"after {SUBSCRIPTION_ADMIN_MONTHS}-month admin charge.")
        return _verdict(order, rules, "approved_prorated", amount,
                        "original_payment_method",
                        f"Pro-rated refund of ${amount:.2f} per R12.")

    # --- R5 digital
    if attrs.get("digital"):
        if attrs.get("downloaded"):
            rule("R5", "fail", "Digital license already downloaded/activated.")
            return _verdict(order, rules, "denied", 0.0, "none",
                            "Downloaded digital goods are not refundable (R5).")
        pdays = _days_since(order["purchase_date"])
        if pdays <= DIGITAL_WINDOW_DAYS:
            rule("R5", "pass", f"Not downloaded; {pdays}d since purchase (limit {DIGITAL_WINDOW_DAYS}).")
            return _verdict(order, rules, "approved_full", price,
                            "original_payment_method",
                            f"Full refund of ${price:.2f} per R5.")
        rule("R5", "fail", f"{pdays}d since purchase exceeds {DIGITAL_WINDOW_DAYS}d digital window.")
        return _verdict(order, rules, "denied", 0.0, "none",
                        "Outside the 14-day digital refund window (R5).")

    # --- R7 wrong item shipped (overrides window + final sale)
    if attrs.get("wrong_item_reported"):
        rule("R7", "pass", f"Fulfillment error: received '{attrs.get('received_item', 'wrong item')}'.")
        return _verdict(order, rules, "approved_full", price,
                        "original_payment_method",
                        f"Full refund incl. shipping of ${price:.2f} per R7 "
                        "(or free exchange, customer's choice).")

    # --- R6 damaged on arrival (overrides final sale)
    if attrs.get("damage_reported"):
        if days is not None and days <= STANDARD_WINDOW_DAYS:
            if attrs.get("photos_provided"):
                rule("R6", "pass", f"Damage reported at {days}d with photos provided.")
                return _verdict(order, rules, "approved_full", price,
                                "original_payment_method",
                                f"Full refund incl. shipping of ${price:.2f} per R6.")
            rule("R6", "needs_info", "Damage reported but no photos on file.")
            return _verdict(order, rules, "needs_more_info", 0.0, "none",
                            "Request damage photos before deciding (R6).")
        rule("R6", "fail", f"Damage reported outside {STANDARD_WINDOW_DAYS}d window ({days}d).")
        return _verdict(order, rules, "denied", 0.0, "none",
                        "Damage claim outside the 30-day window (R2/R6).")

    # --- R4 final sale
    if attrs.get("final_sale"):
        rule("R4", "fail", "Item marked FINAL SALE; no R6/R7 exception applies.")
        return _verdict(order, rules, "denied", 0.0, "none",
                        "Final-sale items are not refundable (R4).")

    # --- R2 window
    if days is None:
        rule("R2", "needs_info", "No delivered date on record.")
        return _verdict(order, rules, "needs_more_info", 0.0, "none",
                        "Delivery not confirmed; investigate with carrier (R13).")
    if days > STANDARD_WINDOW_DAYS:
        rule("R2", "fail", f"{days}d since delivery exceeds {STANDARD_WINDOW_DAYS}d window.")
        return _verdict(order, rules, "denied", 0.0, "none",
                        f"Outside the 30-day refund window by {days - STANDARD_WINDOW_DAYS} days (R2).")
    rule("R2", "pass", f"{days}d since delivery is within {STANDARD_WINDOW_DAYS}d window.")

    # --- R11 gift
    if attrs.get("gift"):
        rule("R11", "pass", "Gift purchase: refund as store credit to recipient.")
        return _verdict(order, rules, "approved_store_credit", price,
                        "store_credit_to_recipient",
                        f"Store credit of ${price:.2f} to the gift recipient per R11.")

    # --- R3 condition (customer-reported — the system cannot see inside the box)
    if reported_condition == "unopened":
        rule("R3", "pass", "Customer reports unopened/unused: full refund eligible.")
        verdict = _verdict(order, rules, "approved_full", price,
                           "original_payment_method",
                           f"Full refund of ${price:.2f} (R2, R3).")
    elif reported_condition == "opened_undamaged":
        amount = round(price * (1 - RESTOCKING_FEE), 2)
        rule("R3", "partial", f"Customer reports opened but undamaged: "
                              f"{int(RESTOCKING_FEE*100)}% restocking fee applies.")
        verdict = _verdict(order, rules, "approved_partial", amount,
                           "original_payment_method",
                           f"Refund of ${amount:.2f} after 15% restocking fee (R3).")
    elif reported_condition in ("used", "damaged_by_customer"):
        rule("R3", "fail", "Customer reports item used/damaged: not refundable.")
        return _verdict(order, rules, "denied", 0.0, "none",
                        "Used or customer-damaged items are not refundable (R3).")
    else:
        rule("R3", "needs_info",
             "Item condition is customer-reported. Ask the customer whether the "
             "item is unopened, opened-but-undamaged, or used.")
        return _verdict(order, rules, "needs_more_info", 0.0, "none",
                        "Ask the customer for the item's condition (R3).")

    # --- R9 high value gate (applies to computed amount)
    if not skip_escalation_gates and verdict["recommended_amount_usd"] > HIGH_VALUE_LIMIT:
        rules.append({"rule": "R9", "result": "fail",
                      "detail": f"Amount ${verdict['recommended_amount_usd']:.2f} exceeds "
                                f"${HIGH_VALUE_LIMIT:.0f} agent limit."})
        return _verdict(order, rules, "escalated",
                        verdict["recommended_amount_usd"], "none",
                        "Eligible, but amount requires human manager execution (R9).")
    return verdict


def _verdict(order: dict, rules: list[dict], decision: str,
             amount: float, destination: str, summary: str) -> dict:
    return {
        "order_id": order["order_id"],
        "item": order["item"],
        "price_usd": order["price_usd"],
        "days_since_delivery": _days_since(order["delivered_date"]) if order.get("delivered_date") else None,
        "rule_evaluations": rules,
        "recommended_decision": decision,
        "recommended_amount_usd": round(amount, 2),
        "refund_destination": destination,
        "summary": summary,
    }


# ------------------------------------------------------------ actions

def process_refund(customer_id: str, order_id: str, decision: str,
                   amount_usd: float, rule_ids: list[str], reason: str,
                   reported_condition: str = "unknown") -> dict:
    """Execute a refund. GUARDRAIL: re-validates; refuses out-of-policy calls."""
    verdict = validate_refund_eligibility(customer_id, order_id,
                                          reported_condition=reported_condition)
    if "error" in verdict:
        return {"executed": False, "error": verdict["error"]}

    allowed = {"approved_full", "approved_partial", "approved_store_credit", "approved_prorated"}
    if verdict["recommended_decision"] not in allowed:
        return {
            "executed": False,
            "guardrail": "POLICY_VIOLATION_BLOCKED",
            "error": (f"Refused: policy engine recommends "
                      f"'{verdict['recommended_decision']}' for {order_id}, not an approval. "
                      f"{verdict['summary']}"),
        }
    if decision != verdict["recommended_decision"]:
        return {
            "executed": False,
            "guardrail": "DECISION_MISMATCH_BLOCKED",
            "error": (f"Refused: agent requested '{decision}' but policy engine "
                      f"requires '{verdict['recommended_decision']}'."),
        }
    if abs(amount_usd - verdict["recommended_amount_usd"]) > 0.01:
        return {
            "executed": False,
            "guardrail": "AMOUNT_MISMATCH_BLOCKED",
            "error": (f"Refused: requested ${amount_usd:.2f} but policy computes "
                      f"${verdict['recommended_amount_usd']:.2f}. Retry with the correct amount."),
        }

    record = {
        "refund_id": f"RFD-{len(REFUNDS_LEDGER) + 1:05d}",
        "customer_id": customer_id,
        "order_id": order_id,
        "decision": decision,
        "amount_usd": verdict["recommended_amount_usd"],
        "destination": verdict["refund_destination"],
        "rule_ids": rule_ids,
        "reason": reason,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
    }
    REFUNDS_LEDGER.append(record)
    return {"executed": True, **record}


def escalate_to_manager(customer_id: str, order_id: str, reason: str,
                        reported_condition: str = "unknown") -> dict:
    """Open a manager escalation ticket (picked up by the Manager agent)."""
    ticket = {
        "ticket_id": f"ESC-{len(ESCALATIONS) + 1:05d}",
        "customer_id": customer_id,
        "order_id": order_id,
        "reason": reason,
        "reported_condition": reported_condition,
        "status": "open",
        "resolution": None,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
    }
    ESCALATIONS.append(ticket)
    return {"created": True, **ticket}


# ------------------------------------------------- manager (tier 2) tools

def _get_ticket(ticket_id: str) -> dict | None:
    return next((t for t in ESCALATIONS if t["ticket_id"] == ticket_id), None)


def get_escalation_context(ticket_id: str) -> dict:
    """Everything a manager needs to rule on one escalation ticket."""
    t = _get_ticket(ticket_id)
    if not t:
        return {"error": f"Unknown ticket {ticket_id}."}
    c = _DB[t["customer_id"]]
    count_90d, recent = _refunds_last_90_days(c)
    return {
        "ticket": t,
        "customer": {
            "customer_id": c["customer_id"],
            "name": c["name"],
            "loyalty_tier": c["loyalty_tier"],
            "account_flags": c["account_flags"],
            "refunds_last_90_days": count_90d,
            "refund_history": recent,
        },
        "gated_verdict": validate_refund_eligibility(
            t["customer_id"], t["order_id"],
            reported_condition=t.get("reported_condition", "unknown")),
        "underlying_eligibility": validate_refund_eligibility(
            t["customer_id"], t["order_id"], skip_escalation_gates=True,
            reported_condition=t.get("reported_condition", "unknown")),
    }


def resolve_escalation(ticket_id: str, resolution: str,
                       amount_usd: float, rule_ids: list[str], reason: str) -> dict:
    """Manager ruling on a ticket. GUARDRAILS: fraud accounts can never be
    approved; approval amounts must match the policy engine's underlying
    computation — tier 2 has more authority, not more arithmetic freedom."""
    t = _get_ticket(ticket_id)
    if not t:
        return {"resolved": False, "error": f"Unknown ticket {ticket_id}."}
    if t["status"] != "open":
        return {"resolved": False, "error": f"{ticket_id} is already {t['status']}."}
    if resolution not in ("approved_manager", "denied_manager"):
        return {"resolved": False,
                "error": "resolution must be approved_manager or denied_manager."}

    c = _DB[t["customer_id"]]

    if resolution == "approved_manager":
        if "fraud_review" in c["account_flags"]:
            return {"resolved": False, "guardrail": "MANAGER_FRAUD_BLOCKED",
                    "error": "Accounts under fraud review cannot be refunded by "
                             "anyone, including managers (R10). Deny or leave open."}
        underlying = validate_refund_eligibility(
            t["customer_id"], t["order_id"], skip_escalation_gates=True,
            reported_condition=t.get("reported_condition", "unknown"))
        if underlying["recommended_decision"] not in (
                "approved_full", "approved_partial",
                "approved_store_credit", "approved_prorated"):
            return {"resolved": False, "guardrail": "MANAGER_POLICY_BLOCKED",
                    "error": f"Underlying eligibility is "
                             f"'{underlying['recommended_decision']}' — a manager may "
                             f"override escalation gates, not item-level policy. "
                             f"{underlying['summary']}"}
        if abs(amount_usd - underlying["recommended_amount_usd"]) > 0.01:
            return {"resolved": False, "guardrail": "MANAGER_AMOUNT_MISMATCH",
                    "error": f"Requested ${amount_usd:.2f} but policy computes "
                             f"${underlying['recommended_amount_usd']:.2f}."}
        record = {
            "refund_id": f"RFD-{len(REFUNDS_LEDGER) + 1:05d}",
            "customer_id": t["customer_id"],
            "order_id": t["order_id"],
            "decision": "approved_manager",
            "amount_usd": underlying["recommended_amount_usd"],
            "destination": underlying["refund_destination"],
            "rule_ids": rule_ids,
            "reason": reason,
            "approved_by": "manager_agent",
            "timestamp": datetime.now().isoformat(timespec="seconds"),
        }
        REFUNDS_LEDGER.append(record)
        t.update(status="resolved", resolution="approved_manager")
        return {"resolved": True, "ticket_id": ticket_id, **record}

    t.update(status="resolved", resolution="denied_manager")
    return {"resolved": True, "ticket_id": ticket_id,
            "decision": "denied_manager", "reason": reason,
            "rule_ids": rule_ids}


# ----------------------------------------------------- tool registry

TOOL_SPECS = [
    {
        "name": "lookup_customer",
        "description": "Find a customer record by email address. Always verify identity "
                       "with this before discussing any order (policy R1).",
        "input_schema": {
            "type": "object",
            "properties": {"email": {"type": "string", "description": "Customer email address"}},
            "required": ["email"],
        },
    },
    {
        "name": "get_order",
        "description": "Fetch full details of one order, verifying it belongs to the customer.",
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_id": {"type": "string"},
                "order_id": {"type": "string"},
            },
            "required": ["customer_id", "order_id"],
        },
    },
    {
        "name": "validate_refund_eligibility",
        "description": "Run the deterministic policy engine on an order. Returns hard facts, "
                       "per-rule evaluations (R1–R13), a recommended decision, and the exact "
                       "refund amount. You MUST call this before any refund decision and you "
                       "may not contradict its rule evaluations. The item's physical condition "
                       "is customer-reported: pass what the CUSTOMER told you, never assume.",
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_id": {"type": "string"},
                "order_id": {"type": "string"},
                "reported_condition": {
                    "type": "string",
                    "enum": ["unknown", "unopened", "opened_undamaged",
                             "used", "damaged_by_customer"],
                    "description": "The condition the customer stated. Use 'unknown' "
                                   "if not yet asked — the engine will tell you to ask.",
                },
            },
            "required": ["customer_id", "order_id", "reported_condition"],
        },
    },
    {
        "name": "process_refund",
        "description": "Execute an approved refund. Re-validates policy internally and will "
                       "REFUSE any request that does not exactly match the policy engine's "
                       "recommended decision and amount.",
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_id": {"type": "string"},
                "order_id": {"type": "string"},
                "decision": {"type": "string",
                             "enum": ["approved_full", "approved_partial",
                                      "approved_store_credit", "approved_prorated"]},
                "amount_usd": {"type": "number"},
                "rule_ids": {"type": "array", "items": {"type": "string"}},
                "reason": {"type": "string"},
                "reported_condition": {
                    "type": "string",
                    "enum": ["unknown", "unopened", "opened_undamaged",
                             "used", "damaged_by_customer"],
                    "description": "Same condition the customer reported during validation.",
                },
            },
            "required": ["customer_id", "order_id", "decision", "amount_usd",
                         "rule_ids", "reason", "reported_condition"],
        },
    },
    {
        "name": "escalate_to_manager",
        "description": "Open a ticket for manager review (required by R8, R9, R10, or "
                       "any situation the agent cannot resolve within policy).",
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_id": {"type": "string"},
                "order_id": {"type": "string"},
                "reason": {"type": "string"},
                "reported_condition": {
                    "type": "string",
                    "enum": ["unknown", "unopened", "opened_undamaged",
                             "used", "damaged_by_customer"],
                    "description": "Condition the customer reported, so the manager "
                                   "sees the same facts.",
                },
            },
            "required": ["customer_id", "order_id", "reason"],
        },
    },
]

MANAGER_TOOL_SPECS = [
    {
        "name": "get_escalation_context",
        "description": "Fetch the full context for one escalation ticket: the ticket, the "
                       "customer (with derived refund history), the gated policy verdict, "
                       "and the underlying item-level eligibility with escalation gates "
                       "lifted. Always call this first.",
        "input_schema": {
            "type": "object",
            "properties": {"ticket_id": {"type": "string"}},
            "required": ["ticket_id"],
        },
    },
    {
        "name": "resolve_escalation",
        "description": "Rule on an escalation ticket. Approvals execute the refund. "
                       "GUARDRAILS: fraud-review accounts can never be approved; the "
                       "amount must exactly match the policy engine's underlying "
                       "computation; item-level policy cannot be overridden.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticket_id": {"type": "string"},
                "resolution": {"type": "string",
                               "enum": ["approved_manager", "denied_manager"]},
                "amount_usd": {"type": "number"},
                "rule_ids": {"type": "array", "items": {"type": "string"}},
                "reason": {"type": "string"},
            },
            "required": ["ticket_id", "resolution", "amount_usd", "rule_ids", "reason"],
        },
    },
]

_IMPLS = {
    "lookup_customer": lookup_customer,
    "get_order": get_order,
    "validate_refund_eligibility": validate_refund_eligibility,
    "process_refund": process_refund,
    "escalate_to_manager": escalate_to_manager,
    # manager-only
    "get_escalation_context": get_escalation_context,
    "resolve_escalation": resolve_escalation,
}


def run_tool(name: str, tool_input: dict[str, Any]) -> dict:
    """Dispatch a tool call. Never raises — errors return as structured results."""
    fn = _IMPLS.get(name)
    if fn is None:
        return {"error": f"Unknown tool: {name}"}
    try:
        return fn(**tool_input)
    except TypeError as e:
        return {"error": f"Bad arguments for {name}: {e}"}
    except Exception as e:  # surface, don't crash the loop
        return {"error": f"{name} failed: {e}"}
