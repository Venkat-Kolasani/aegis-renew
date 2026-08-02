#!/usr/bin/env bash
# Render/local API entrypoint. Rewrites postgres:// URLs for SQLAlchemy+psycopg.
set -euo pipefail

if [[ -n "${DATABASE_URL:-}" ]]; then
  # A pasted local .env on Render points at localhost and cannot reach aegis-db.
  if [[ -n "${RENDER:-}" ]] && [[ "${DATABASE_URL}" == *"localhost"* || "${DATABASE_URL}" == *"127.0.0.1"* ]]; then
    echo "start_api: DATABASE_URL points at localhost on Render." >&2
    echo "start_api: In the service Environment, delete DATABASE_URL, then" >&2
    echo "start_api: Add from Database → aegis-db → DATABASE_URL, and redeploy." >&2
    exit 1
  fi
  DATABASE_URL="${DATABASE_URL/postgres:\/\//postgresql+psycopg:\/\/}"
  DATABASE_URL="${DATABASE_URL/postgresql:\/\//postgresql+psycopg:\/\/}"
  export DATABASE_URL
  python3 scripts/apply_schema.py
fi

exec python3 -m uvicorn backend.main:app --host 0.0.0.0 --port "${PORT:-8000}"
