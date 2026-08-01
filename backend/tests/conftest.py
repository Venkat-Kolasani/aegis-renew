"""Shared pytest fixtures for isolated database tests."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from backend.db.connection import create_database_engine, create_session_factory
from backend.db.models import Base


@pytest.fixture
def db_engine(tmp_path: Path) -> Iterator[Engine]:
    """Create a fresh SQLite database with the production ORM metadata."""
    database_path = tmp_path / "aegis-test.sqlite3"
    engine = create_database_engine(f"sqlite+pysqlite:///{database_path}")
    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture
def db_session(db_engine: Engine) -> Iterator[Session]:
    """Yield an isolated SQLAlchemy session and roll it back after each test."""
    session = create_session_factory(db_engine)()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
