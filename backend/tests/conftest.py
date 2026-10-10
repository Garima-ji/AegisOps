"""Pytest fixtures and test environment setup."""

import os
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

# Force test environment variables before importing app modules
os.environ["ENVIRONMENT"] = "test"
os.environ["DEBUG"] = "true"
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["SECRET_KEY"] = "test-secret-key-that-is-at-least-32-chars-long"

from app.core.config import Settings, clear_settings_cache, get_settings
from app.db.base import Base
from app.db.session import get_db, get_db_session, reset_engine
from app.ingestion.fault_injection import fault_engine
from app.main import create_app
from app.observability.metrics import reset_observability_metrics


@pytest.fixture(autouse=True)
def clean_environment() -> Generator[None, None, None]:
    """Ensure clean settings cache, engine state, metrics, and fault engine for each test."""
    clear_settings_cache()
    reset_engine()
    reset_observability_metrics()
    fault_engine.reset()
    yield
    clear_settings_cache()
    reset_engine()
    reset_observability_metrics()
    fault_engine.reset()


@pytest.fixture
def test_settings() -> Settings:
    """Return test-specific settings."""
    return get_settings()


@pytest.fixture
def test_db_engine():
    """Create in-memory SQLite engine for tests."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    yield engine
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.fixture
def test_db_session(test_db_engine) -> Generator[Session, None, None]:
    """Provide a transactional database session for tests."""
    testing_session_local = sessionmaker(autocommit=False, autoflush=False, bind=test_db_engine)
    session = testing_session_local()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(test_db_session: Session) -> Generator[TestClient, None, None]:
    """Provide a TestClient with overridden database session."""
    app = create_app()

    def override_get_db_session() -> Generator[Session, None, None]:
        yield test_db_session

    app.dependency_overrides[get_db_session] = override_get_db_session
    app.dependency_overrides[get_db] = override_get_db_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
