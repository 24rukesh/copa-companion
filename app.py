"""Copa Companion — GenAI matchday concierge for FIFA World Cup 2026.

FastAPI backend. Serves the fan-facing chat UI, a simulated live crowd feed,
and an organizer ops summary. Gemini API calls happen server-side only, so the
API key never reaches the browser. Without a key the app degrades to a
rule-based assistant so the demo and tests work offline.
"""

import json
import math
import os
import re
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

BASE_DIR = Path(__file__).parent
DATA = json.loads((BASE_DIR / "data" / "stadium.json").read_text(encoding="utf-8"))

WAIT_ALERT_MINUTES = 30  # ops dashboard flags gates above this

app = FastAPI(title="Copa Companion", docs_url=None, redoc_url=None)


# ---------------------------------------------------------------- crowd feed

def crowd_snapshot(now: float | None = None) -> list[dict]:
    """Simulated per-gate queue waits, drifting over a 3-hour arrival cycle.

    Deterministic for a given timestamp so tests are reproducible.
    Real deployment would replace this with the venue's sensor feed.
    """
    now = time.time() if now is None else now
    minute = (now / 60) % 180
    snapshot = []
    for gate in DATA["gates"]:
        wave = math.sin(minute / 180 * 2 * math.pi + gate["surge_phase"])
        wait = max(2, round(gate["base_wait"] + gate["surge"] * wave))
        snapshot.append(
            {
                "gate": gate["id"],
                "location": gate["location"],
                "lat": gate["lat"],
                "lng": gate["lng"],
                "wait_min": wait,
            }
        )
    return snapshot


def best_gate(snapshot: list[dict]) -> dict:
    return min(snapshot, key=lambda g: g["wait_min"])


def section_info(section: int) -> dict | None:
    for s in DATA["sections"]:
        lo, hi = s["range"]
        if lo <= section <= hi:
            return s
    return None


# ------------------------------------------------------------ assistant core

SYSTEM_PROMPT = """You are Copa Companion, a matchday assistant at {venue} for {event}.
Answer ONLY from the stadium facts and live queue data below. If the answer is
not in the facts, say you don't know and point the fan to a Fan Help kiosk.
Always reply in the same language the fan wrote in. Be brief and concrete:
give the action first, then one line of context. Never invent gates, times,
prices or policies.

STADIUM FACTS:
{facts}

LIVE GATE QUEUES (minutes):
{queues}

FAN'S TICKET: {ticket}
FAN'S CURRENT POSITION: {position}
"""


def build_context(
    section: int | None, distance_km: float | None = None, eta_min: int | None = None
) -> tuple[str, list[dict]]:
    snapshot = crowd_snapshot()
    position = "unknown"
    if distance_km is not None:
        position = f"about {distance_km} km from the stadium"
        if eta_min is not None:
            position += f", roughly {eta_min} min of travel away"
    ticket = "unknown"
    if section is not None:
        info = section_info(section)
        if info:
            ticket = (
                f"Section {section}, {info['level']}, assigned entry Gate {info['gate']}"
            )
    facts = json.dumps(
        {k: DATA[k] for k in ("faq", "concessions", "transit", "sections")},
        ensure_ascii=False,
    )
    queues = ", ".join(f"Gate {g['gate']}: {g['wait_min']} min" for g in snapshot)
    prompt = SYSTEM_PROMPT.format(
        venue=DATA["venue"],
        event=DATA["event"],
        facts=facts,
        queues=queues,
        ticket=ticket,
        position=position,
    )
    return prompt, snapshot


def ask_gemini(system: str, message: str) -> str:
    from google import genai  # lazy: app must run without the package installed

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    resp = client.models.generate_content(
        model=os.environ.get("GEMINI_MODEL", "gemini-2.5-flash"),
        contents=f"{system}\n\nFAN SAYS: {message}",
    )
    return resp.text or "Sorry, I could not generate an answer. Please try again."


def fallback_answer(
    message: str,
    section: int | None,
    snapshot: list[dict],
    distance_km: float | None = None,
    eta_min: int | None = None,
) -> str:
    """Keyword-routed answers when no Gemini key is configured (offline demo)."""
    text = message.lower()
    fast = best_gate(snapshot)
    position = ""
    if distance_km is not None and eta_min is not None:
        position = f" You are ~{distance_km} km away, about {eta_min} min of travel."

    if any(w in text for w in ("gate", "puerta", "entry", "entrance", "entrada", "where do i go", "donde")):
        lines = []
        if section is not None and (info := section_info(section)):
            assigned = next(g for g in snapshot if g["gate"] == info["gate"])
            lines.append(
                f"Your seat (section {section}) is on the {info['level']}, assigned Gate {info['gate']} "
                f"({assigned['location']}) — current wait {assigned['wait_min']} min."
            )
            if fast["gate"] != info["gate"] and assigned["wait_min"] - fast["wait_min"] >= 10:
                lines.append(
                    f"Faster option: Gate {fast['gate']} ({fast['location']}), only {fast['wait_min']} min. "
                    f"All gates accept all tickets."
                )
            if position:
                lines.append(position.strip())
        else:
            lines.append(
                f"Shortest queue right now: Gate {fast['gate']} ({fast['location']}), {fast['wait_min']} min. "
                f"Tell me your section number for a personal route."
            )
        return " ".join(lines)

    if any(w in text for w in ("food", "eat", "comida", "halal", "vegetarian", "vegan", "hungry")):
        stands = DATA["concessions"]
        wanted = [s for s in stands if any(t in text for t in s["tags"])] or stands
        return "Food nearby: " + "; ".join(f"{s['name']} — {s['location']}" for s in wanted)

    if any(w in text for w in ("exit", "leave", "train", "bus", "shuttle", "salida", "transporte", "uber", "taxi")):
        return (
            "After the match: "
            + " | ".join(f"{t['mode']}: {t['detail']}" for t in DATA["transit"])
            + " Tip: waiting 20-30 min inside beats standing in the crush."
        )

    for item in DATA["faq"]:
        if any(w in text for w in item["q"].split()):
            return item["a"]

    return (
        "I can help with gates and queues, food, exits and transport, bag policy, "
        "accessibility, first aid and more. What do you need? "
        "(Full multilingual answers need the GEMINI_API_KEY configured.)"
    )


# ------------------------------------------------------------------- routes

class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=500)
    section: int | None = Field(default=None, ge=1, le=999)
    # distance/ETA computed client-side from the fan's position; raw
    # coordinates deliberately never reach the server (privacy)
    distance_km: float | None = Field(default=None, ge=0, le=1000)
    eta_min: int | None = Field(default=None, ge=0, le=6000)


@app.post("/api/chat")
def chat(req: ChatRequest) -> dict:
    system, snapshot = build_context(req.section, req.distance_km, req.eta_min)
    if os.environ.get("GEMINI_API_KEY"):
        try:
            reply = ask_gemini(system, req.message)
        except Exception:
            # never leak provider errors to the client; degrade gracefully
            reply = fallback_answer(req.message, req.section, snapshot, req.distance_km, req.eta_min)
    else:
        reply = fallback_answer(req.message, req.section, snapshot, req.distance_km, req.eta_min)
    return {"reply": reply}


@app.get("/api/crowd")
def crowd() -> dict:
    return {"venue": DATA["coords"], "gates": crowd_snapshot()}


def ops_state(now: float | None = None) -> dict:
    """Full organizer picture: waits + trend + load ratio + alerts + exit plan.

    Trend needs no storage: the simulated feed is deterministic on timestamp,
    so 'two minutes ago' is just a second call. A real sensor feed would keep
    a two-point history instead.
    """
    now = time.time() if now is None else now
    snapshot = crowd_snapshot(now)
    prev = {g["gate"]: g["wait_min"] for g in crowd_snapshot(now - 120)}
    base = {g["id"]: g["base_wait"] for g in DATA["gates"]}
    for g in snapshot:
        delta = g["wait_min"] - prev[g["gate"]]
        g["trend"] = "rising" if delta >= 2 else "falling" if delta <= -2 else "steady"
        g["load"] = round(g["wait_min"] / base[g["gate"]], 1)  # 1.0 = normal inflow
    alerts = [g["gate"] for g in snapshot if g["wait_min"] >= WAIT_ALERT_MINUTES]
    exit_plan = [
        {
            "level": s["level"],
            "hold_min": i * 8,
            "route": DATA["transit"][i % len(DATA["transit"])]["mode"],
        }
        for i, s in enumerate(DATA["sections"])
    ]
    return {"gates": snapshot, "alerts": alerts, "exit_plan": exit_plan}


@app.get("/api/ops")
def ops() -> dict:
    """Organizer view: same crowd data, enriched for decision support."""
    return ops_state()


OPS_PROMPT = """You are the operations-room assistant at {venue} for {event}.
Below is the live gate state: queue wait in minutes, trend over the last two
minutes, and load (1.0 = normal inflow for that gate). Write a briefing for
stadium staff: 2 sentences of situation, then up to 3 prioritized actions as
short imperative bullets. Plain language, no fluff, only facts from the data.

GATE STATE:
{state}
"""


def fallback_briefing(state: dict) -> str:
    gates = state["gates"]
    worst = max(gates, key=lambda g: g["wait_min"])
    fast = best_gate(gates)
    rising = [g["gate"] for g in gates if g["trend"] == "rising"]
    lines = [
        f"Worst queue: Gate {worst['gate']} at {worst['wait_min']} min "
        f"({worst['load']}x normal, {worst['trend']}). "
        f"Shortest: Gate {fast['gate']} at {fast['wait_min']} min."
    ]
    if state["alerts"]:
        lines.append(
            f"ACTIONS: Redirect arrivals from Gate {worst['gate']} to Gate {fast['gate']} "
            f"via signage and app push. Staff the overflow lane at Gate {worst['gate']}."
        )
    else:
        lines.append("All gates within normal range. No intervention needed.")
    if rising:
        lines.append(f"Watch: rising queues at Gate {', Gate '.join(rising)}.")
    return " ".join(lines)


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
            pass  # fall through to rule-based
    return {"briefing": fallback_briefing(state)}


def parse_section(ticket_text: str) -> int | None:
    """Pull a section number out of free ticket text like 'Section 428, Row 12'."""
    m = re.search(r"(?:section|secci[oó]n|sec)\s*[.:#]?\s*(\d{1,3})", ticket_text, re.I)
    return int(m.group(1)) if m else None


class TicketRequest(BaseModel):
    text: str = Field(min_length=1, max_length=300)


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
