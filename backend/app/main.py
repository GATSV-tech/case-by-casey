"""
Casey support agent — FastAPI server.

Two SSE surfaces:
  POST /api/chat          → streams the events of ONE agent turn (customer UI)
  GET  /api/admin/stream  → global live feed of every event (admin dashboard)

Events are plain JSON dicts; see agent.run_agent_turn for the vocabulary.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import AsyncIterator

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel

from .agent import SESSIONS, run_agent_turn, run_manager_review
from .tools import ESCALATIONS, REFUNDS_LEDGER, _DB

app = FastAPI(title="Casey — AI Refund Support Agent")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----------------------------------------------------- admin broadcast

from collections import deque

_admin_subscribers: set[asyncio.Queue] = set()
_event_log: deque = deque(maxlen=300)  # replay buffer for late-joining admins


async def _broadcast(event: dict) -> None:
    _event_log.append(event)
    for q in list(_admin_subscribers):
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            pass


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"


# ------------------------------------------------------------- routes

class ChatRequest(BaseModel):
    session_id: str
    message: str


@app.post("/api/chat")
async def chat(req: ChatRequest) -> StreamingResponse:
    """Run one agent turn; stream its events as SSE."""
    queue: asyncio.Queue = asyncio.Queue()

    async def emit(event: dict) -> None:
        await queue.put(event)
        await _broadcast(event)

    async def turn() -> None:
        try:
            await run_agent_turn(req.session_id, req.message, emit)
        finally:
            await queue.put({"type": "_close"})

    async def stream() -> AsyncIterator[str]:
        task = asyncio.create_task(turn())
        try:
            while True:
                event = await queue.get()
                if event.get("type") == "_close":
                    break
                yield _sse(event)
        finally:
            task.cancel()

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.post("/api/admin/escalations/{ticket_id}/review")
async def review_escalation(ticket_id: str) -> StreamingResponse:
    """Dispatch Morgan (the manager agent) to rule on a ticket; stream its reasoning."""
    queue: asyncio.Queue = asyncio.Queue()

    async def emit(event: dict) -> None:
        await queue.put(event)
        await _broadcast(event)

    async def review() -> None:
        try:
            await run_manager_review(ticket_id, emit)
        finally:
            await queue.put({"type": "_close"})

    async def stream() -> AsyncIterator[str]:
        task = asyncio.create_task(review())
        try:
            while True:
                event = await queue.get()
                if event.get("type") == "_close":
                    break
                yield _sse(event)
        finally:
            task.cancel()

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.get("/api/admin/stream")
async def admin_stream() -> StreamingResponse:
    """Global SSE feed of all agent events, across every session."""
    queue: asyncio.Queue = asyncio.Queue(maxsize=500)
    _admin_subscribers.add(queue)

    async def stream() -> AsyncIterator[str]:
        try:
            yield _sse({"type": "admin_connected"})
            for past in list(_event_log):  # replay history to late joiners
                yield _sse(past)
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=25)
                    yield _sse(event)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            _admin_subscribers.discard(queue)

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.get("/api/admin/state")
def admin_state() -> dict:
    """Current ledgers + session index for the dashboard side panels."""
    return {
        "refunds": REFUNDS_LEDGER,
        "escalations": ESCALATIONS,
        "sessions": {sid: len(msgs) for sid, msgs in SESSIONS.items()},
    }


@app.get("/api/customers")
def customers() -> dict:
    """Demo helper: the mock CRM directory (for the admin panel)."""
    return {
        "customers": [
            {
                "customer_id": c["customer_id"],
                "name": c["name"],
                "email": c["email"],
                "loyalty_tier": c["loyalty_tier"],
                "flags": c["account_flags"],
                "orders": [o["order_id"] for o in c["orders"]],
            }
            for c in _DB.values()
        ]
    }


class TTSRequest(BaseModel):
    text: str


@app.post("/api/tts")
async def tts(req: TTSRequest) -> Response:
    """Text-to-speech via ElevenLabs. Returns audio/mpeg, or a JSON error with a
    non-200 status so the frontend can fall back to the browser's own voice."""
    key = os.getenv("ELEVENLABS_API_KEY")
    if not key:
        return JSONResponse({"error": "tts_unconfigured"}, status_code=503)
    voice = os.getenv("CASEY_VOICE_ID", "EXAVITQu4vr4xnSDxMaL")
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{voice}",
                headers={"xi-api-key": key, "Content-Type": "application/json"},
                json={"text": req.text, "model_id": "eleven_turbo_v2_5"},
            )
    except httpx.HTTPError as e:
        return JSONResponse({"error": "tts_request_failed", "detail": str(e)},
                            status_code=502)
    if r.status_code != 200:
        return JSONResponse({"error": "tts_failed", "detail": r.text},
                            status_code=r.status_code)
    return Response(content=r.content, media_type="audio/mpeg")


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "service": "casey-support-agent"}
