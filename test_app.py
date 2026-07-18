"""Smallest checks that fail if the core logic breaks. Run: pytest -q"""

from fastapi.testclient import TestClient

import app as m

client = TestClient(m.app)


def test_crowd_snapshot_deterministic_and_sane():
    a = m.crowd_snapshot(now=1_000_000)
    b = m.crowd_snapshot(now=1_000_000)
    assert a == b
    assert {g["gate"] for g in a} == {"A", "E", "F", "K", "M"}
    assert all(g["wait_min"] >= 2 for g in a)


def test_section_maps_to_gate():
    assert m.section_info(428)["gate"] == "K"
    assert m.section_info(105)["gate"] == "A"
    assert m.section_info(999) is None


def test_parse_section():
    assert m.parse_section("Section 428, Row 12, Seat 8") == 428
    assert m.parse_section("SECCIÓN 105 FILA 3") == 105
    assert m.parse_section("no numbers here") is None


def test_ticket_endpoint():
    ok = client.post("/api/ticket", json={"text": "Section 428, Row 12"})
    assert ok.status_code == 200
    assert ok.json()["gate"] == "K"
    bad = client.post("/api/ticket", json={"text": "hello"})
    assert bad.status_code == 422


def test_chat_fallback_routes_to_faster_gate(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    res = client.post("/api/chat", json={"message": "Where is my gate?", "section": 428})
    assert res.status_code == 200
    reply = res.json()["reply"]
    assert "Gate K" in reply  # assigned gate always named


def test_chat_rejects_oversized_input():
    res = client.post("/api/chat", json={"message": "x" * 501})
    assert res.status_code == 422


def test_crowd_includes_coords():
    res = client.get("/api/crowd")
    data = res.json()
    assert {"lat", "lng"} <= set(data["venue"])
    assert all({"lat", "lng"} <= set(g) for g in data["gates"])


def test_chat_uses_travel_context(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    res = client.post(
        "/api/chat",
        json={"message": "where is my gate", "section": 428, "distance_km": 3.2, "eta_min": 12},
    )
    assert "3.2 km" in res.json()["reply"]


def test_chat_fallback_transport_quick_chip(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    res = client.post("/api/chat", json={"message": "How do I get back to Dallas after the match?"})
    assert "After the match" in res.json()["reply"]


def test_ops_state():
    res = client.get("/api/ops")
    assert res.status_code == 200
    data = res.json()
    assert len(data["gates"]) == 5
    for g in data["gates"]:
        assert g["trend"] in ("rising", "falling", "steady")
        assert g["load"] > 0
    assert len(data["exit_plan"]) == 5
    assert data["exit_plan"][0]["hold_min"] == 0  # first group releases immediately


def test_ops_briefing_fallback(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    res = client.get("/api/ops/briefing")
    assert res.status_code == 200
    text = res.json()["briefing"]
    assert "Gate" in text and ("ACTIONS" in text or "normal range" in text)


def test_organizer_page():
    res = client.get("/organizer")
    assert res.status_code == 200
    assert "Organizer Dashboard" in res.text


def test_chat_gemini_path_mocked(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(m, "ask_gemini", lambda system, msg: f"MOCKED:{msg}")
    res = client.post("/api/chat", json={"message": "hola"})
    assert res.json()["reply"] == "MOCKED:hola"


def test_chat_rate_limited(monkeypatch):
    monkeypatch.setattr(m, "RATE_LIMIT", 3)
    m._hits.clear()
    codes = [client.post("/api/chat", json={"message": "hi"}).status_code for _ in range(5)]
    m._hits.clear()
    assert codes[:3] == [200, 200, 200] and codes[3] == 429


def test_serves_ui():
    res = client.get("/")
    assert res.status_code == 200
    assert "Copa Companion" in res.text
