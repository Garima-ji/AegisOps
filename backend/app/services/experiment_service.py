"""Domain service for controlled fault injection experiments and ground-truth label management."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.exceptions import ResourceNotFoundError, ValidationError
from app.db.models.experiment import ExperimentModel, FaultLabelModel
from app.observability.metrics import increment_counter
from app.schemas.experiment import ExperimentCreateRequest

ALLOWLISTED_SERVICES = {
    "order-service",
    "payment-service",
    "inventory-service",
    "notification-service",
}


class ExperimentService:
    """Manages the lifecycle of controlled fault-injection scenarios."""

    @staticmethod
    def create_experiment(session: Session, req: ExperimentCreateRequest) -> ExperimentModel:
        """Create and launch a controlled, bounded fault injection scenario."""
        # Safety constraint: Check allowlisted target service
        if req.target_service not in ALLOWLISTED_SERVICES:
            raise ValidationError(
                f"Target service '{req.target_service}' is not on the safety allowlist for fault injection."
            )

        # Enforce no two active experiments on the exact same target service concurrently
        active_existing = session.execute(
            select(ExperimentModel).where(
                ExperimentModel.target_service == req.target_service,
                ExperimentModel.status == "ACTIVE",
            )
        ).scalar_one_or_none()

        if active_existing:
            raise ValidationError(
                f"Target service '{req.target_service}' already has an active experiment ('{active_existing.id}'). Stop it first."
            )

        now = datetime.now(UTC)
        exp_id = f"exp_{now.strftime('%Y%m%d%H%M%S')}_{req.fault_type}_{uuid.uuid4().hex[:6]}"
        end_time = now + timedelta(seconds=req.duration_seconds)

        experiment = ExperimentModel(
            id=exp_id,
            scenario_name=req.scenario_name,
            description=req.description,
            target_service=req.target_service,
            fault_type=req.fault_type,
            parameters=req.parameters,
            start_time=now,
            end_time=end_time,
            status="ACTIVE",
            initiating_service=req.initiating_service,
            symptom_services=req.symptom_services,
            cleanup_result="Active scenario with scheduled automatic restoration.",
            created_at=now,
        )
        session.add(experiment)
        session.flush()

        # Seed initial ground-truth labels for initiating and symptom services
        labels_to_add: list[FaultLabelModel] = [
            FaultLabelModel(
                experiment_id=exp_id,
                timestamp=now,
                service_name=req.initiating_service,
                is_fault=True,
                fault_type=req.fault_type,
                severity="CRITICAL",
                metadata_json={
                    "role": "initiator",
                    "parameters": req.parameters,
                },
            )
        ]

        for symptom_svc in req.symptom_services:
            labels_to_add.append(
                FaultLabelModel(
                    experiment_id=exp_id,
                    timestamp=now,
                    service_name=symptom_svc,
                    is_fault=True,
                    fault_type=req.fault_type,
                    severity="HIGH",
                    metadata_json={
                        "role": "symptom",
                        "upstream": req.initiating_service,
                    },
                )
            )

        session.add_all(labels_to_add)
        session.flush()

        increment_counter("experiments.total", 1)
        increment_counter("experiments.active", 1)

        return experiment

    @staticmethod
    def get_experiment(session: Session, experiment_id: str) -> ExperimentModel | None:
        """Retrieve experiment by identifier."""
        return session.execute(
            select(ExperimentModel).where(ExperimentModel.id == experiment_id)
        ).scalar_one_or_none()

    @staticmethod
    def list_experiments(
        session: Session, limit: int = 50, offset: int = 0
    ) -> tuple[list[ExperimentModel], int]:
        """List historical and active experiments."""
        bounded_limit = min(max(1, limit), 200)
        total = session.execute(select(func.count(ExperimentModel.id))).scalar_one() or 0
        stmt = (
            select(ExperimentModel)
            .order_by(ExperimentModel.created_at.desc())
            .limit(bounded_limit)
            .offset(offset)
        )
        return list(session.execute(stmt).scalars().all()), total

    @staticmethod
    def get_experiment_labels(
        session: Session, experiment_id: str, limit: int = 500, offset: int = 0
    ) -> tuple[list[FaultLabelModel], int]:
        """Fetch ground-truth labels associated with an experiment."""
        exp = ExperimentService.get_experiment(session, experiment_id)
        if not exp:
            raise ResourceNotFoundError(f"Experiment '{experiment_id}' does not exist.")

        bounded_limit = min(max(1, limit), 1000)
        count_stmt = select(func.count(FaultLabelModel.id)).where(
            FaultLabelModel.experiment_id == experiment_id
        )
        total = session.execute(count_stmt).scalar_one() or 0

        stmt = (
            select(FaultLabelModel)
            .where(FaultLabelModel.experiment_id == experiment_id)
            .order_by(FaultLabelModel.timestamp.asc())
            .limit(bounded_limit)
            .offset(offset)
        )
        return list(session.execute(stmt).scalars().all()), total

    @staticmethod
    def stop_experiment(session: Session, experiment_id: str) -> ExperimentModel:
        """Manually stop an active experiment and perform state cleanup."""
        exp = ExperimentService.get_experiment(session, experiment_id)
        if not exp:
            raise ResourceNotFoundError(f"Experiment '{experiment_id}' does not exist.")

        if exp.status == "ACTIVE":
            now = datetime.now(UTC)
            exp.status = "STOPPED"
            exp.end_time = now
            exp.cleanup_result = "Cleaned up safely by user request."
            increment_counter("experiments.active", -1)
            session.flush()

        return exp
