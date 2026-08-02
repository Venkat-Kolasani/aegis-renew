#!/usr/bin/env python3
"""Apply backend/db/schema.sql when the domains table is missing.

Used by Render/local start so the API works without an interactive DB shell.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import psycopg


def _psycopg_url(database_url: str) -> str:
    """Convert SQLAlchemy-style URLs to a psycopg connection string."""
    return database_url.replace("postgresql+psycopg://", "postgresql://", 1)


def _statements(schema_sql: str) -> list[str]:
    """Split schema SQL into executable statements, skipping line comments."""
    lines: list[str] = []
    for line in schema_sql.splitlines():
        if line.strip().startswith("--"):
            continue
        lines.append(line)
    body = "\n".join(lines)
    return [part.strip() for part in body.split(";") if part.strip()]


def main() -> int:
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        print("apply_schema: DATABASE_URL not set; skipping", file=sys.stderr)
        return 0

    root = Path(__file__).resolve().parents[1]
    schema_path = root / "backend" / "db" / "schema.sql"
    if not schema_path.is_file():
        print(f"apply_schema: missing {schema_path}", file=sys.stderr)
        return 1

    url = _psycopg_url(database_url)
    try:
        with psycopg.connect(url) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT to_regclass('public.domains')")
                if cur.fetchone()[0] is not None:
                    print("apply_schema: tables already present")
                    return 0
            for statement in _statements(schema_path.read_text(encoding="utf-8")):
                with conn.cursor() as cur:
                    cur.execute(statement)
            conn.commit()
    except psycopg.Error as exc:
        print(f"apply_schema: failed: {exc}", file=sys.stderr)
        return 1

    print("apply_schema: applied backend/db/schema.sql")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
