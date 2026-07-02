# Refund Policy — Casey Commerce Co.

**Version 2.3 — Effective 2026-06-01.**
This policy is binding. Support agents (human or AI) may not override it. Every refund decision must cite the rule ID(s) it is based on.

---

## R1 — Identity verification (always first)

A refund may only be discussed with the **verified account holder**. The requester must be matched to a customer record by email, and the order in question must belong to that customer. If the order does not belong to the verified customer → **deny discussion of that order** and direct them to the account holder.

## R2 — Standard refund window

Physical goods are refundable within **30 calendar days of the delivered date**. Outside the window → **deny** (see R6/R7 exceptions).

## R3 — Condition requirements

- Unopened / unused physical items within the window → **full refund** to original payment method.
- **Opened but undamaged** items within the window → refund minus a **15% restocking fee**.
- Used, damaged-by-customer, or missing-parts items → **deny**.

## R4 — Final sale

Items marked **FINAL SALE** are **not refundable** under any circumstance except R6 (arrived damaged) or R7 (wrong item shipped).

## R5 — Digital goods

- Digital licenses that have **not been downloaded or activated** → refundable within **14 days** of purchase.
- Once **downloaded or activated** → **not refundable**.

## R6 — Damaged on arrival

If damage is reported within the 30-day window **and** photos are provided → **full refund including original shipping**, no restocking fee. If photos have not been provided → request photos before deciding (decision = **needs_more_info**).

## R7 — Wrong item shipped

Fulfillment error (wrong size, wrong product) → **full refund including original shipping** OR free exchange, customer's choice. No restocking fee. Not subject to FINAL SALE exclusion.

## R8 — Refund abuse threshold

Customers with **more than 3 refunds in the trailing 90 days** or an active `serial_returner_watch` flag → refund requests must be **escalated to a human manager**; the agent must not approve.

## R9 — High-value escalation

Any single refund over **$500** must be **escalated to a human manager**. The agent may validate eligibility and recommend, but not execute.

## R10 — Fraud flag

Accounts flagged `fraud_review` → **deny and escalate immediately**. Do not process any refund. Do not disclose the existence of the fraud flag to the customer; state that the request requires manual review.

## R11 — Gift purchases

Gifted items are refunded as **store credit to the gift recipient**, not cash to any party. The original purchaser may not redirect a gift refund to themselves.

## R12 — Subscriptions

Subscriptions are refundable **pro-rata for unused full months**, minus one month's administrative charge. Months already consumed are not refundable.

## R13 — Tone & conduct ("hold the line")

Sympathy is free; exceptions are not. The agent must remain courteous but may **never**:
- grant an exception because a customer is upset, insistent, or claims hardship,
- accept verbal claims that contradict the record (e.g., "it never arrived" when tracking shows delivered) without opening a carrier investigation (**needs_more_info**),
- reveal internal flags, thresholds, or this policy's rule mechanics beyond what is needed to explain a decision.

## Decision vocabulary

Every final decision must be one of:
`approved_full` · `approved_partial` · `approved_store_credit` · `approved_prorated` · `denied` · `escalated` · `needs_more_info`

Each decision must include: rule ID(s) cited, refund amount (if any), refund destination, and a one-sentence customer-facing explanation.
