"""Service registry management logic with idempotent upserts."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.telemetry import ServiceModel


def upsert_service(
    session: Session,
    service_name: str,
    environment: str = "production",
    version: str | None = None,
    status: str = "healthy",
    metadata: dict[str, Any] | None = None,
) -> ServiceModel:
    """Idempotently register or refresh a service record upon receiving telemetry."""
    now = datetime.now(UTC)
    stmt = select(ServiceModel).where(
        ServiceModel.name == service_name,
        ServiceModel.environment == environment,
    )
    svc = session.execute(stmt).scalar_one_or_none()

    if svc is not None:
        svc.last_seen = now
        if version is not None:
            svc.version = version
        if status != "unknown":
            svc.status = status
        if metadata:
            existing = svc.metadata_json or {}
            existing.update(metadata)
            svc.metadata_json = existing
        return svc

    svc = ServiceModel(
        name=service_name,
        environment=environment,
        version=version,
        status=status,
        first_seen=now,
        last_seen=now,
        metadata_json=metadata or {},
    )
    session.add(svc)
    session.flush()
    return svc


def list_services(
    session: Session,
    environment: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[ServiceModel], int]:
    """Return paginated services matching criteria."""
    stmt = select(ServiceModel)
    if environment:
        stmt = stmt.where(ServiceModel.environment == environment)

    count_stmt = select(ServiceModel.id)
    if environment:
        count_stmt = count_stmt.where(ServiceModel.environment == environment)
    total = len(session.execute(count_stmt).all())

    stmt = stmt.order_by(ServiceModel.name.asc()).limit(limit).offset(offset)
    results = list(session.execute(stmt).scalars().all())
    return results, total


def get_service_by_name(
    session: Session,
    service_name: str,
    environment: str | None = None,
) -> ServiceModel | None:
    """Find a registered service by name and optional environment."""
    stmt = select(ServiceModel).where(ServiceModel.name == service_name)
    if environment:
        stmt = stmt.where(ServiceModel.environment == environment)
    return session.execute(stmt).scalar_one_or_none()
