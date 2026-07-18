"""GenAI assistant core: prompts, Gemini calls, and the offline fallback.

Gemini is confined to a stadium knowledge pack plus the live queue data, so
it advises instead of hallucinating. Without an API key the same knowledge
pack answers through a keyword-routed rule engine, keeping the demo and the
test suite fully offline.
"""

import json
import logging
import os

from crowd import DATA, best_gate, crowd_snapshot, section_info

logger = logging.getLogger("copa")

REROUTE_ADVANTAGE_MIN = 10  # suggest another gate when it saves at least this

# static part of the assistant context, built once — not per request
FACTS_JSON = json.dumps(
    {k: DATA[k] for k in ("faq", "concessions", "transit", "sections")},
    ensure_ascii=False,
)

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

OPS_PROMPT = """You are the operations-room assistant at {venue} for {event}.
Below is the live gate state: queue wait in minutes, trend over the last two
minutes, and load (1.0 = normal inflow for that gate). Write a briefing for
stadium staff: 2 sentences of situation, then up to 3 prioritized actions as
short imperative bullets. Plain language, no fluff, only facts from the data.

GATE STATE:
{state}
"""


def build_context(
    section: int | None, distance_km: float | None = None, eta_min: int | None = None
) -> tuple[str, list[dict]]:
    """Assemble the grounded system prompt plus the snapshot it was built from."""
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
            ticket = f"Section {section}, {info['level']}, assigned entry Gate {info['gate']}"
    queues = ", ".join(f"Gate {g['gate']}: {g['wait_min']} min" for g in snapshot)
    prompt = SYSTEM_PROMPT.format(
        venue=DATA["venue"],
        event=DATA["event"],
        facts=FACTS_JSON,
        queues=queues,
        ticket=ticket,
        position=position,
    )
    return prompt, snapshot


def ask_gemini(system: str, message: str) -> str:
    """Server-side Gemini call; the key never reaches the browser."""
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

    gate_words = ("gate", "puerta", "entry", "entrance", "entrada", "where do i go", "donde")
    if any(w in text for w in gate_words):
        lines = []
        if section is not None and (info := section_info(section)):
            assigned = next(g for g in snapshot if g["gate"] == info["gate"])
            lines.append(
                f"Your seat (section {section}) is on the {info['level']}, assigned Gate {info['gate']} "
                f"({assigned['location']}) — current wait {assigned['wait_min']} min."
            )
            if (
                fast["gate"] != info["gate"]
                and assigned["wait_min"] - fast["wait_min"] >= REROUTE_ADVANTAGE_MIN
            ):
                lines.append(
                    f"Faster option: Gate {fast['gate']} ({fast['location']}), "
                    f"only {fast['wait_min']} min. All gates accept all tickets."
                )
            if position:
                lines.append(position.strip())
        else:
            lines.append(
                f"Shortest queue right now: Gate {fast['gate']} ({fast['location']}), "
                f"{fast['wait_min']} min. Tell me your section number for a personal route."
            )
        return " ".join(lines)

    food_words = ("food", "eat", "comida", "halal", "vegetarian", "vegan", "hungry")
    if any(w in text for w in food_words):
        stands = DATA["concessions"]
        wanted = [s for s in stands if any(t in text for t in s["tags"])] or stands
        return "Food nearby: " + "; ".join(f"{s['name']} — {s['location']}" for s in wanted)

    exit_words = (
        "exit", "leave", "train", "bus", "shuttle", "salida", "transporte",
        "uber", "taxi", "get back", "go home", "after the match", "regresar",
    )
    if any(w in text for w in exit_words):
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


def fallback_briefing(state: dict) -> str:
    """Rule-based ops briefing when Gemini is unavailable."""
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
