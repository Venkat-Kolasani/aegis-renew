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

## Frontend development

```bash
cd frontend
npm run dev
npm run lint
npm run build
npm test
```

The dashboard currently renders contract-shaped domain fixtures. `npm test`
server-renders the domain list through its empty, loading, and populated states;
live API data is added in the follow-up dashboard slice.

## Configuration and database

Copy `.env.example` to `.env` and fill local values only. `PRAVA_SECRET_KEY`
is server-only; never use a public browser environment-variable prefix for it.

Create a local Postgres database and apply the initial schema:

```bash
psql "$DATABASE_URL" -f backend/db/schema.sql
```

The schema keeps detection results, recommendations, mandate metadata, and
sanitized payment outcomes. It never stores payment tokens or dynamic CVVs.

## Continuous integration

Every push and pull request runs backend `pytest`, frontend lint, and a frontend
production build through GitHub Actions. Run the same commands locally before
pushing a build slice.

## Build disclosure

The Aegis idea and public integration research existed before the event. All
application code in this repository is being written during the hackathon build
window. Payment claims require real Prava sandbox evidence and a completed
merchant checkout; no payment outcome is mocked.
