"""Database engine, connection pooling, and session lifecycle management."""

import time
from collections.abc import Generator
from typing import Any

from sqlalchemy import Engine, create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings, get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def create_db_engine(settings: Settings | None = None) -> Engine:
    """Create and configure a SQLAlchemy Engine instance."""
    if settings is None:
        settings = get_settings()

    db_url = settings.database_url

    # Configure SQLite (primarily for unit testing and local in-memory execution)
    if db_url.startswith("sqlite"):
        connect_args: dict[str, Any] = {"check_same_thread": False}
        if ":memory:" in db_url:
            return create_engine(
                db_url,
                connect_args=connect_args,
                poolclass=StaticPool,
                echo=settings.db_echo,
            )
        return create_engine(
            db_url,
            connect_args=connect_args,
            echo=settings.db_echo,
        )

    # Configure PostgreSQL with connection pooling
    return create_engine(
        db_url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout,
        pool_recycle=settings.db_pool_recycle,
        pool_pre_ping=True,
        echo=settings.db_echo,
    )


def get_engine() -> Engine:
    """Return the global SQLAlchemy Engine singleton."""
    global _engine
    if _engine is None:
        _engine = create_db_engine()
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    """Return the global sessionmaker singleton."""
    global _session_factory
    if _session_factory is None:
        engine = get_engine()
        _session_factory = sessionmaker(
            autocommit=False,
            autoflush=False,
            bind=engine,
            expire_on_commit=False,
        )
    return _session_factory


def reset_engine() -> None:
    """Dispose and clear existing engine and session factory (used in test fixtures)."""
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
        _engine = None
    _session_factory = None


def check_database_connection(timeout_seconds: float = 3.0) -> tuple[bool, str, dict[str, Any]]:
    """Execute a ping query (SELECT 1) against the database and measure latency.

    Returns:
        tuple[bool, str, dict[str, Any]]: (is_healthy, message, diagnostic_details)
    """
    start_time = time.perf_counter()
    dialect_name = "unknown"
    try:
        engine = get_engine()
        dialect_name = engine.dialect.name
        with engine.connect() as conn:
            # Set statement timeout for PostgreSQL if applicable
            if "postgresql" in engine.dialect.name:
                conn.execute(text(f"SET statement_timeout = {int(timeout_seconds * 1000)}"))
            result = conn.execute(select(1)).scalar()
            latency_ms = round((time.perf_counter() - start_time) * 1000, 2)
            if result == 1:
                return (
                    True,
                    "Database connection healthy",
                    {
                        "dialect": engine.dialect.name,
                        "latency_ms": latency_ms,
                    },
                )
            return (
                False,
                f"Unexpected ping result: {result}",
                {"dialect": engine.dialect.name, "latency_ms": latency_ms},
            )
    except Exception as exc:
        latency_ms = round((time.perf_counter() - start_time) * 1000, 2)
        logger.warning(
            "Database connectivity check failed: %s (latency: %sms)",
            exc,
            latency_ms,
        )
        return (
            False,
            f"Database connection error: {exc!s}",
            {
                "dialect": dialect_name,
                "latency_ms": latency_ms,
                "error_type": type(exc).__name__,
            },
        )


def get_db_session() -> Generator[Session, None, None]:
    """FastAPI dependency that provides a transactional database session."""
    session_factory = get_session_factory()
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


get_db = get_db_session
