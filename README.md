# Aegis

Aegis is an agentic infrastructure-renewal prototype for the Prava Agentic
Commerce Hackathon. It detects domain, TLS, and DNS risk, ranks urgency, and
executes a configured renewal only when a user-approved Prava mandate covers it.

## Backend development

Install dependencies from `backend/requirements.txt`, then run the API:

```bash
uvicorn backend.main:app --reload
```

Run the current backend checks:

```bash
pytest -q
```

The Phase 0 API exposes `GET /health`; business routes are intentionally typed
501 placeholders until their owner begins the corresponding build prompt.

## Build disclosure

The Aegis idea and public integration research existed before the event. All
application code in this repository is being written during the hackathon build
window. Payment claims require real Prava sandbox evidence and a completed
merchant checkout; no payment outcome is mocked.
