#!/usr/bin/env bash
# Render/local API entrypoint. Rewrites postgres:// URLs for SQLAlchemy+psycopg.
set -euo pipefail

if [[ -n "${DATABASE_URL:-}" ]]; then
  DATABASE_URL="${DATABASE_URL/postgres:\/\//postgresql+psycopg:\/\/}"
  DATABASE_URL="${DATABASE_URL/postgresql:\/\//postgresql+psycopg:\/\/}"
  export DATABASE_URL
fi

exec uvicorn backend.main:app --host 0.0.0.0 --port "${PORT:-8000}"
