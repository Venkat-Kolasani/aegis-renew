# Aegis

Aegis is an agentic infrastructure-renewal prototype for the Prava Agentic
Commerce Hackathon. It detects domain, TLS, and DNS risk, ranks urgency, and
executes a configured renewal only when a user-approved Prava mandate covers it.

## Backend development

Create a Python 3.11+ virtual environment, install the backend dependencies,
then run the API from the repository root:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r backend/requirements.txt
uvicorn backend.main:app --reload
```

Run the complete backend suite from the repository root:

```bash
python -m pytest backend/tests -q
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

The dashboard currently renders six-plus contract-shaped domain fixtures with
green/yellow/red expiry urgency and a distinct DNS takeover signal. `npm test`
covers empty, loading, error, healthy, near-expiry, urgent, and DNS-risk states;
live API data is added in the follow-up dashboard slice.

## Configuration and database

Copy `.env.example` to `.env` and fill local values only. Export
`DATABASE_URL` into the backend process environment; the SQLAlchemy production
engine requires PostgreSQL through the psycopg driver. `PRAVA_SECRET_KEY` is
server-only; never use a public browser environment-variable prefix for it.

Create a local Postgres database and apply the approved shared schema:

```bash
createdb aegis
export DATABASE_URL="postgresql+psycopg://postgres:postgres@localhost:5432/aegis"
psql "postgresql://postgres:postgres@localhost:5432/aegis" -f backend/db/schema.sql
```

Application persistence uses SQLAlchemy 2.x ORM models and sessions. Tests use
a fresh temporary SQLite database per test, so they never touch the configured
Postgres database.

The schema keeps detection results, recommendations, mandate metadata, and
sanitized payment outcomes. Models contain no card information, payment
credentials, network tokens, dynamic CVVs, or secret fields.

Security note: the shared Phase 0 SQL currently names a required column
`provider_mandate_id`, which conflicts with the project rule forbidding raw
Prava mandate identifiers. Until schema ownership is handed over, the ORM maps
that column to `provider_mandate_id_digest` and rejects non-digest values. The
shared SQL has not been changed; it should later be renamed explicitly so the
database-level name also communicates the security boundary. This digest is
for correlation only and cannot be used to execute a payment; a later payment
phase must use an explicitly approved provider-side lookup or secret-management
design without adding a raw identifier to these models.

## Continuous integration

Every push and pull request runs backend `pytest`, frontend render tests, lint,
and a frontend production build through GitHub Actions. Run the same commands
locally before pushing a build slice.

## Build disclosure

The Aegis idea and public integration research existed before the event. All
application code in this repository is being written during the hackathon build
window. Payment claims require real Prava sandbox evidence and a completed
merchant checkout; no payment outcome is mocked.
