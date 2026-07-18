"""Live crowd simulation and organizer operations state.

The feed is simulated (deterministic sine per gate) so the demo and tests are
reproducible; a real deployment swaps `crowd_snapshot` for the venue's
sensor/turnstile feed and everything downstream works unchanged.
"""

import json
import math
import time
from functools import lru_cache
from pathlib import Path

BASE_DIR = Path(__file__).parent
DATA = json.loads((BASE_DIR / "data" / "stadium.json").read_text(encoding="utf-8"))

WAIT_ALERT_MINUTES = 30  # ops dashboard flags gates above this
TREND_DELTA_MIN = 2  # minutes of change over 2 min that counts as a trend
OPS_BUCKET_SECONDS = 15  # ops state is memoized per 15s tick (matches UI poll)


def crowd_snapshot(now: float | None = None) -> list[dict]:
    """Per-gate queue waits, drifting over a 3-hour arrival cycle."""
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
    """Gate with the shortest current queue."""
    return min(snapshot, key=lambda g: g["wait_min"])


def section_info(section: int) -> dict | None:
    """Map a seat section number to its level and assigned gate."""
    for s in DATA["sections"]:
        lo, hi = s["range"]
        if lo <= section <= hi:
            return s
    return None


def ops_state(now: float | None = None) -> dict:
    """Memoized per 15s bucket: N dashboard viewers cost one computation."""
    now = time.time() if now is None else now
    return _ops_state_bucket(int(now // OPS_BUCKET_SECONDS))


@lru_cache(maxsize=4)
def _ops_state_bucket(bucket: int) -> dict:
    """Full organizer picture: waits + trend + load ratio + alerts + exit plan.

    Trend needs no storage: the simulated feed is deterministic on timestamp,
    so 'two minutes ago' is just a second call. A real sensor feed would keep
    a two-point history instead.
    """
    now = bucket * OPS_BUCKET_SECONDS
    snapshot = crowd_snapshot(now)
    prev = {g["gate"]: g["wait_min"] for g in crowd_snapshot(now - 120)}
    base = {g["id"]: g["base_wait"] for g in DATA["gates"]}
    for g in snapshot:
        delta = g["wait_min"] - prev[g["gate"]]
        g["trend"] = (
            "rising"
            if delta >= TREND_DELTA_MIN
            else "falling"
            if delta <= -TREND_DELTA_MIN
            else "steady"
        )
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
