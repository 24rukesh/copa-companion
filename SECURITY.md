# Security model

## Secrets
- The Gemini API key lives only in a server-side environment variable
  (`GEMINI_API_KEY`). It is never sent to the browser, never logged, never
  committed (`.env` is gitignored; the Docker image never bakes it in).

## Input validation
- Every request body is validated with Pydantic at the trust boundary
  ([schemas.py](schemas.py)): length caps on free text, numeric ranges on
  section/distance/ETA.
- Ticket parsing is a bounded regex — no eval, no format-string tricks.

## Abuse resistance
- `/api/chat` is rate-limited per client IP (sliding window, 20 req/min) so
  the paid LLM endpoint can't be farmed. In-memory by design for a single
  container; swap for a shared store when scaling horizontally.

## Browser hardening
- `Content-Security-Policy` allowlisting only the four origins the app uses
  (Google Fonts, unpkg/Leaflet, OpenStreetMap tiles, OSRM router).
- `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`,
  `Referrer-Policy: no-referrer`; API responses are `Cache-Control: no-store`.
- FastAPI docs endpoints are disabled in production (`docs_url=None`).

## Privacy
- The app is stateless: no accounts, no database, no stored user data.
- Raw GPS coordinates never reach the server — the browser computes
  distance/ETA and shares only those two numbers with the assistant.

## LLM safety
- Gemini is grounded on a fixed knowledge pack and live queue data, and
  instructed to refuse questions outside it. Provider errors are never
  leaked to clients; the app degrades to a rule-based fallback.

## Container
- Multi-stage build: test tooling never enters the runtime image.
- Runs as a non-root user with a container healthcheck.

## Reporting
Found something? Open a GitHub issue on the repository.
