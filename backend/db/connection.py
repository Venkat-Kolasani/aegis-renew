"""SQLAlchemy engine and session configuration for Aegis."""

from __future__ import annotations

import logging
import os
from collections.abc import Generator
from contextlib import contextmanager
from threading import Lock

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

logger = logging.getLogger(__name__)


class DatabaseConfigurationError(RuntimeError):
    """Raised when required database configuration is absent or invalid."""


_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None
_initialization_lock = Lock()


def get_database_url() -> str:
    """Return the configured SQLAlchemy database URL from the environment."""
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise DatabaseConfigurationError("DATABASE_URL is required")

    try:
        make_url(database_url)
    except (ArgumentError, TypeError, ValueError) as exc:
        raise DatabaseConfigurationError("DATABASE_URL is not a valid database URL") from exc
    return database_url


def _enable_sqlite_foreign_keys(engine: Engine) -> None:
    """Enable SQLite foreign-key behavior used by the production schema."""

    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection: object, _: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()


def create_database_engine(database_url: str | None = None) -> Engine:
    """Create a SQLAlchemy 2.x engine for the supplied or configured URL."""
    resolved_url = database_url or get_database_url()
    try:
        engine = create_engine(resolved_url, pool_pre_ping=True)
    except (ImportError, ModuleNotFoundError, SQLAlchemyError) as exc:
        logger.error(
            "Failed to configure the database engine (%s)", type(exc).__name__
        )
        raise DatabaseConfigurationError("Could not configure the database engine") from exc

    if engine.dialect.name == "sqlite":
        _enable_sqlite_foreign_keys(engine)
    return engine


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Create the single session-factory pattern used by Aegis persistence."""
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_engine() -> Engine:
    """Return the process-wide production engine, creating it lazily."""
    global _engine, _session_factory
    if _engine is None:
        with _initialization_lock:
            if _engine is None:
                _engine = create_database_engine()
                _session_factory = create_session_factory(_engine)
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    """Return the process-wide SQLAlchemy session factory."""
    get_engine()
    if _session_factory is None:  # pragma: no cover - defensive invariant
        raise DatabaseConfigurationError("Database session factory was not initialized")
    return _session_factory


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    """Provide a transactional session that commits or rolls back atomically."""
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except SQLAlchemyError:
        session.rollback()
        logger.exception("Database transaction failed")
        raise
    finally:
        session.close()
