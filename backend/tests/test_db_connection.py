"""Configuration tests for the SQLAlchemy database connection layer."""

from pathlib import Path

import pytest
from sqlalchemy import Engine

from backend.db.connection import (
    DatabaseConfigurationError,
    create_database_engine,
    get_database_url,
)


def test_engine_reads_database_url_from_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Engine creation uses DATABASE_URL when no explicit URL is supplied."""
    database_url = f"sqlite+pysqlite:///{tmp_path / 'configured.sqlite3'}"
    monkeypatch.setenv("DATABASE_URL", database_url)

    engine: Engine = create_database_engine()
    try:
        assert get_database_url() == database_url
        assert engine.dialect.name == "sqlite"
    finally:
        engine.dispose()


def test_missing_database_url_raises_specific_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing production configuration raises a dedicated exception."""
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(DatabaseConfigurationError, match="DATABASE_URL is required"):
        get_database_url()
