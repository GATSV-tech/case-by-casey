"""
The agent loops — Casey (tier 1, customer-facing) and Morgan (tier 2, manager).

Raw function calling with the Anthropic SDK (no framework). Both agents share
one loop implementation; they differ only in system prompt, toolset, and
authority. Every step (text, tool calls, tool results, guardrail refusals,
retries) is emitted as a structured event so the admin dashboard can show
each agent's reasoning in real time. Events carry an `agent` field:
"casey" | "morgan".
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any, Callable

import anthropic
from dotenv import load_dotenv

from .tools import (ESCALATIONS, MANAGER_TOOL_SPECS, REFUND_POLICY,
                    TOOL_SPECS, run_tool)

load_dotenv()

MODEL = os.getenv("CASEY_MODEL", "claude-sonnet-5")
MAX_LOOP_ITERATIONS = 8
MAX_TOKENS = 2000  # thinking blocks consume output tokens; leave headroom
API_RETRIES = 2

_client = anthropic.Anthropic()

CASEY_SYSTEM_PROMPT = f"""You are Casey, the AI customer support agent for Casey Commerce Co.
You handle refund requests over chat. You are warm, plain-spoken, and efficient —
but the refund policy below is binding and you can never override it.

NON-NEGOTIABLE RULES OF CONDUCT
1. Verify identity FIRST: ask for the customer's email, call lookup_customer, and
   only discuss orders that belong to that verified customer (policy R1).
2. Before ANY refund decision, call validate_refund_eligibility. Its rule
   evaluations are ground truth. Never contradict them, never do your own date
   math or fee arithmetic.
3. NEVER expose internal rule IDs (R2, R8, etc.), policy codes, or system
   mechanics to the customer. Explain decisions in plain, friendly language
   ("since it's unopened and within our 30-day window, you're all set for a
   full refund"). Rule IDs belong ONLY in your internal tool calls (the
   rule_ids and reason fields) — never in a message the customer reads.
4. Hold the line (R13): stay kind, acknowledge frustration, but never grant
   exceptions for insistence, anger, or hardship stories. Do not reveal internal
   flags (especially fraud flags), thresholds, or system mechanics.
5. Escalations (R8/R9/R10) go through escalate_to_manager, and you tell the
   customer their case has been sent for manual review — nothing more.
6. NEVER process a refund until the customer confirms they want you to. After
   validating, tell them the amount and where it goes ("that's $68 back to your
   original card"), then ASK for their go-ahead. Only once they say yes do you
   call process_refund, using EXACTLY the policy engine's decision and amount.
   If the tool refuses, re-read its error and correct yourself — never argue
   with the guardrail.
7. One question at a time. Keep replies short and human. No corporate filler.
8. The system CANNOT see inside the customer's box. For physical returns, ASK
   the customer whether the item is unopened, opened-but-undamaged, or used —
   then pass their answer as reported_condition. Never assume or claim the
   system "shows" a condition. If the engine returns needs_more_info, ask the
   customer for exactly what it needs, then re-validate with their answer.
9. Close conversations like a professional: when the request is handled, ask
   "Is there anything else I can help you with?" — and when the customer is
   done, close warmly ("Thanks for shopping with Casey Commerce — have a great
   day!"). Never leave a conversation hanging.

THE BINDING POLICY DOCUMENT
{REFUND_POLICY}
"""

MORGAN_SYSTEM_PROMPT = f"""You are Morgan, the tier-2 escalation manager agent at Casey Commerce Co.
You review escalation tickets opened by Casey (the tier-1 support agent). You have
elevated authority: you may execute refunds above the $500 agent limit (R9) and
rule on flagged-account requests (R8). You are NOT above policy:

1. Always call get_escalation_context first. Study the gated verdict, the
   underlying item-level eligibility, and the customer's derived refund history.
2. R9 escalations (high value, otherwise eligible): approve at exactly the
   computed underlying amount unless the history shows a concrete reason not to.
3. R8 escalations (abuse threshold): use judgment. Weigh the pattern of past
   refund reasons — frequent "changed mind" refunds read differently than
   verified fulfillment errors. Deny or approve, and justify either way.
4. R10 (fraud review): you can NEVER approve these — deny and note the case
   stays with the fraud team. The tooling will block you if you try.
5. Every review MUST end with a resolve_escalation call. You are not permitted
   to end a review without ruling — reading the context is not a ruling.
6. Include rule IDs and a clear written justification, as if a human auditor
   will read it later — because one will.
7. Be decisive and concise. You write internal notes, not customer messages.

THE BINDING POLICY DOCUMENT
{REFUND_POLICY}
"""

# ------------------------------------------------------------- sessions

SESSIONS: dict[str, list[dict]] = {}


def _now_ms() -> int:
    return int(time.time() * 1000)


async def _run_loop(
    agent_name: str,
    system_prompt: str,
    tools: list[dict],
    messages: list[dict],
    session_id: str,
    emit: Callable[[dict], Any],
) -> None:
    """Shared agent loop: think → call tools → observe → repeat until done."""

    async def send(event: dict) -> None:
        await emit({"agent": agent_name, "session_id": session_id,
                    "ts": _now_ms(), **event})

    for iteration in range(MAX_LOOP_ITERATIONS):
        response = None
        for attempt in range(API_RETRIES + 1):
            try:
                response = await asyncio.to_thread(
                    _client.messages.create,
                    model=MODEL,
                    max_tokens=MAX_TOKENS,
                    # cache_control here caches the whole static prefix
                    # (tools + system incl. the policy doc) across loop
                    # iterations and turns — ~90% cheaper repeat calls.
                    system=[{"type": "text", "text": system_prompt,
                             "cache_control": {"type": "ephemeral"}}],
                    messages=messages,
                    tools=tools,
                )
                break
            except anthropic.APIError as e:
                if attempt < API_RETRIES:
                    await send({"type": "retry",
                                "detail": f"API error ({e.__class__.__name__}), "
                                          f"retry {attempt + 1}/{API_RETRIES}"})
                    await asyncio.sleep(1.5 * (attempt + 1))
                else:
                    await send({"type": "error",
                                "detail": f"API failed after {API_RETRIES} retries: {e}"})
                    return

        assistant_blocks: list[dict] = []
        tool_calls: list[dict] = []

        for block in response.content:
            if block.type == "thinking":
                # Preserve for API continuity AND surface in the reasoning log —
                # the admin theater shows the model's actual internal reasoning.
                assistant_blocks.append({"type": "thinking",
                                         "thinking": block.thinking,
                                         "signature": block.signature})
                if block.thinking and block.thinking.strip():
                    await send({"type": "agent_thinking", "text": block.thinking})
            elif block.type == "redacted_thinking":
                assistant_blocks.append({"type": "redacted_thinking",
                                         "data": block.data})
            elif block.type == "text":
                assistant_blocks.append({"type": "text", "text": block.text})
                await send({"type": "agent_text", "text": block.text})
            elif block.type == "tool_use":
                assistant_blocks.append({
                    "type": "tool_use", "id": block.id,
                    "name": block.name, "input": block.input,
                })
                tool_calls.append({"id": block.id, "name": block.name,
                                   "input": block.input})

        messages.append({"role": "assistant", "content": assistant_blocks})

        if response.stop_reason != "tool_use":
            await send({"type": "turn_end", "iterations": iteration + 1})
            return

        result_blocks: list[dict] = []
        for call in tool_calls:
            await send({"type": "tool_call", "tool": call["name"],
                        "input": call["input"]})
            result = await asyncio.to_thread(run_tool, call["name"], call["input"])

            if isinstance(result, dict) and result.get("guardrail"):
                await send({"type": "guardrail", "tool": call["name"],
                            "guardrail": result["guardrail"],
                            "detail": result.get("error", "")})
            await send({"type": "tool_result", "tool": call["name"],
                        "result": result})

            result_blocks.append({
                "type": "tool_result",
                "tool_use_id": call["id"],
                "content": json.dumps(result),
            })

        messages.append({"role": "user", "content": result_blocks})

    await emit({"agent": agent_name, "session_id": session_id, "type": "error",
                "ts": _now_ms(),
                "detail": f"Loop exceeded {MAX_LOOP_ITERATIONS} iterations; stopping."})


async def run_agent_turn(
    session_id: str,
    user_message: str,
    emit: Callable[[dict], Any],
) -> None:
    """One Casey turn in a persistent customer chat session."""
    history = SESSIONS.setdefault(session_id, [])
    history.append({"role": "user", "content": user_message})
    await emit({"agent": "casey", "type": "user_message", "session_id": session_id,
                "text": user_message, "ts": _now_ms()})
    await _run_loop("casey", CASEY_SYSTEM_PROMPT, TOOL_SPECS,
                    history, session_id, emit)


async def run_manager_review(
    ticket_id: str,
    emit: Callable[[dict], Any],
) -> None:
    """One Morgan review of an escalation ticket (fresh context per review)."""
    messages = [{
        "role": "user",
        "content": f"Review escalation ticket {ticket_id} and rule on it.",
    }]
    await emit({"agent": "morgan", "type": "review_started",
                "session_id": f"review:{ticket_id}",
                "ticket_id": ticket_id, "ts": _now_ms()})
    await _run_loop("morgan", MORGAN_SYSTEM_PROMPT, MANAGER_TOOL_SPECS,
                    messages, f"review:{ticket_id}", emit)

    # Supervisor check: a review is not done until the ticket is ruled on.
    # If Morgan ended without calling resolve_escalation, push back once.
    ticket = next((t for t in ESCALATIONS if t["ticket_id"] == ticket_id), None)
    if ticket and ticket["status"] == "open":
        await emit({"agent": "morgan", "type": "retry",
                    "session_id": f"review:{ticket_id}",
                    "detail": "Review ended without a ruling — supervisor "
                              "requiring Morgan to call resolve_escalation.",
                    "ts": _now_ms()})
        messages.append({
            "role": "user",
            "content": "You ended your review without calling resolve_escalation. "
                       "That is not permitted. Issue your ruling now.",
        })
        await _run_loop("morgan", MORGAN_SYSTEM_PROMPT, MANAGER_TOOL_SPECS,
                        messages, f"review:{ticket_id}", emit)
