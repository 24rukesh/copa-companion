# ⚽ Copa Companion

A GenAI matchday concierge for the FIFA World Cup 2026 — built around one
persona: **the first-time international fan**.

## Chosen vertical

**Fan experience: navigation + multilingual assistance + crowd management**, with
a secondary organizer view (real-time decision support) powered by the same data.

Persona: *Diego, 34, from Argentina.* Spanish speaker, first time in the US,
attending a group-stage match at AT&T Stadium with 80,000 others. He doesn't
know which gate his ticket means, can't read the English signage, and has no
way to know that his assigned gate has a 45-minute security line while another
gate is nearly empty.

## What it does

1. **Ticket-aware navigation** — paste the ticket line ("Section 428, Row 12"),
   the app maps the section to its level and assigned gate.
2. **Live gate queues** — a per-gate wait-time feed (simulated; see
   Assumptions), shown as an always-visible bar and fed into every answer.
3. **Where you are (maps)** — one tap shares your position *with your browser
   only*: the app shows a Leaflet/OpenStreetMap map with your location, the
   route to the stadium (real driving route + ETA via the public OSRM router,
   haversine estimate as fallback), distance, and the nearest gate with its
   current queue. Only the distance/ETA numbers are sent to the assistant —
   raw coordinates never leave the browser.
4. **Multilingual AI concierge** — a chat grounded on a stadium knowledge pack
   (gates, food, transit, bag policy, accessibility, first aid…). Ask in any
   language; Gemini answers in that language, using only the facts and live
   queue data — and reroutes you to a faster gate when yours is congested.
5. **Organizer ops summary** — the same crowd data summarized for staff:
   which gates are above threshold and where to redirect arrivals.

## Approach and logic

```
Browser (static/index.html — vanilla JS, no build step)
   │  /api/ticket   free text ──regex──▶ section ──range map──▶ gate + level
   │  /api/crowd    simulated per-gate waits (deterministic sine over a 3h cycle)
   │  /api/chat     context builder ──▶ Gemini (server-side) ──▶ grounded reply
   │  /api/ops      threshold check ──▶ redirect recommendation
   ▼
FastAPI (app.py) ── data/stadium.json (knowledge pack)
```

Decision logic based on user context:
- If the fan has registered a ticket, answers are personalized to their
  section, level and assigned gate.
- If their assigned gate is ≥10 min slower than the best gate, the assistant
  proactively suggests the faster one.
- Ops view flags gates over 30 min and recommends a redirect target.

The LLM is used where it earns its keep — free-form multilingual Q&A over the
knowledge pack — and *not* for things deterministic code does better (section
lookup, queue math, thresholds). The system prompt confines Gemini to the
provided facts and live data to prevent hallucinated policies or gates.

**No API key? Still works.** Without `GEMINI_API_KEY` the backend answers with
a keyword-routed rule engine over the same knowledge pack (English-leaning,
noted in the UI copy). This keeps the demo and the test suite fully offline.

## Run it

```bash
pip install -r requirements.txt
# optional, for full multilingual GenAI answers:
export GEMINI_API_KEY=...        # Windows: $env:GEMINI_API_KEY="..."
uvicorn app:app --reload
# open http://127.0.0.1:8000
```

Tests:

```bash
pytest -q
```

## Deploy (Docker / Coolify)

A `Dockerfile` ships in the repo root. Coolify: add the repo as a
Dockerfile-build application, set port **8000**, and add `GEMINI_API_KEY` as a
runtime environment variable (never bake it into the image). Plain Docker:

```bash
docker build -t copa-companion .
docker run -p 8000:8000 -e GEMINI_API_KEY=... copa-companion
```

## Security

- Gemini is called **server-side only**; the key never reaches the browser and
  is read from the environment, never committed (`.env` is gitignored).
- All inputs validated with Pydantic (length caps, numeric ranges).
- Provider errors are never leaked to the client; the app degrades to the
  rule-based fallback.
- No user data is stored; the app is stateless.

## Accessibility

- Semantic HTML, labelled inputs, `aria-live` chat log and status regions,
  visible focus outlines, keyboard-operable throughout, high-contrast palette.
- Content answers cover step-free routes, elevators, wheelchair seating and
  the sensory room from the knowledge pack.

## Assumptions

- Crowd data is **simulated** (deterministic sine wave per gate) — a real
  deployment would swap `crowd_snapshot()` for the venue's sensor/turnstile feed.
- Stadium layout, gate names and policies are plausible but illustrative, not
  official AT&T Stadium data. One venue's knowledge pack ships; other stadiums
  are a content swap, not a code change.
- All gates accept all tickets (true at most modern stadiums), which is what
  makes rerouting to a faster gate valid advice.
- Ticket input is pasted text rather than barcode/OCR scanning — out of scope
  for the prototype.
- Maps use OpenStreetMap tiles and the public OSRM demo router (no API key,
  rate-limited; fine for a demo). Production would use a paid routing API.
  Gate GPS coordinates are approximate points on the stadium perimeter.
- Leaflet and the Archivo font load from CDNs; without internet the app still
  works, minus the map and custom font.

Not affiliated with FIFA. Demo project.
