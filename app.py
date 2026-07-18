"""Copa Companion — GenAI matchday concierge for FIFA World Cup 2026.

FastAPI routes and HTTP concerns only; domain logic lives in `crowd.py`
(simulation + ops state) and `assistant.py` (prompts, Gemini, fallback).
"""

import json
import logging
import os
import re
import time
from collections import deque
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from assistant import (
    OPS_PROMPT,
    ask_gemini,
    build_context,
    fallback_answer,
    fallback_briefing,
)
from crowd import DATA, crowd_snapshot, ops_state, section_info
from schemas import ChatRequest, TicketRequest

logger = logging.getLogger("copa")

BASE_DIR = Path(__file__).parent

RATE_LIMIT = 20  # chat requests allowed per client...
RATE_WINDOW = 60  # ...within this many seconds

CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' https://unpkg.com; "
    "style-src 'self' 'unsafe-inline' https://unpkg.com https://fonts.googleapis.com; "
    "font-src https://fonts.gstatic.com; "
    "img-src 'self' data: https://tile.openstreetmap.org "
    "https://*.tile.openstreetmap.org https://unpkg.com; "
    "connect-src 'self' https://router.project-osrm.org"
)

app = FastAPI(title="Copa Companion", docs_url=None, redoc_url=None)
app.add_middleware(GZipMiddleware, minimum_size=500)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Content-Security-Policy"] = CSP
    if request.url.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"  # live data, never cache
    return resp


# ponytail: in-memory per-process rate limit — enough for one container; use
# a shared store (redis) if this ever scales horizontally
_hits: dict[str, deque] = {}


def within_rate_limit(client_ip: str, now: float | None = None) -> bool:
    """Sliding-window limiter: RATE_LIMIT requests per RATE_WINDOW seconds."""
    now = time.time() if now is None else now
    q = _hits.setdefault(client_ip, deque())
    while q and now - q[0] > RATE_WINDOW:
        q.popleft()
    if len(q) >= RATE_LIMIT:
        return False
    q.append(now)
    return True


@app.post("/api/chat")
def chat(req: ChatRequest, request: Request) -> dict:
    client_ip = request.client.host if request.client else "unknown"
    if not within_rate_limit(client_ip):
        raise HTTPException(429, "Too many requests — slow down a little.")
    system, snapshot = build_context(req.section, req.distance_km, req.eta_min)
    if os.environ.get("GEMINI_API_KEY"):
        try:
            reply = ask_gemini(system, req.message)
        except Exception:
            # never leak provider errors to the client; degrade gracefully
            logger.exception("Gemini chat call failed, using fallback")
            reply = fallback_answer(req.message, req.section, snapshot, req.distance_km, req.eta_min)
    else:
        reply = fallback_answer(req.message, req.section, snapshot, req.distance_km, req.eta_min)
    return {"reply": reply}


@app.get("/api/crowd")
def crowd() -> dict:
    return {"venue": DATA["coords"], "gates": crowd_snapshot()}


@app.get("/api/ops")
def ops() -> dict:
    """Organizer view: same crowd data, enriched for decision support."""
    return ops_state()


@app.get("/api/ops/briefing")
def ops_briefing() -> dict:
    state = ops_state()
    if os.environ.get("GEMINI_API_KEY"):
        prompt = OPS_PROMPT.format(
            venue=DATA["venue"], event=DATA["event"], state=json.dumps(state["gates"])
        )
        try:
            return {"briefing": ask_gemini(prompt, "Write the briefing now.")}
        except Exception:
            logger.exception("Gemini briefing call failed, using fallback")
    return {"briefing": fallback_briefing(state)}


def parse_section(ticket_text: str) -> int | None:
    """Pull a section number out of free ticket text like 'Section 428, Row 12'."""
    m = re.search(r"(?:section|secci[oó]n|sec)\s*[.:#]?\s*(\d{1,3})", ticket_text, re.I)
    return int(m.group(1)) if m else None


@app.post("/api/ticket")
def ticket(req: TicketRequest) -> dict:
    section = parse_section(req.text)
    if section is None or (info := section_info(section)) is None:
        raise HTTPException(422, "Could not find a valid section number in the ticket text.")
    return {"section": section, "gate": info["gate"], "level": info["level"]}


# ponytail: no auth on the staff dashboard — demo scope; real deploy puts it behind staff login
@app.get("/organizer")
def organizer_page() -> FileResponse:
    return FileResponse(BASE_DIR / "static" / "organizer.html")


app.mount("/", StaticFiles(directory=BASE_DIR / "static", html=True), name="static")
