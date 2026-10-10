from fastapi import APIRouter

from app.api.v1.experiments import router as experiments_router
from app.api.v1.health import router as health_router
from app.api.v1.investigations import router as investigations_router
from app.api.v1.ml import router as ml_router
from app.api.v1.rca import router as rca_router
from app.api.v1.services import router as services_router
from app.api.v1.system import router as system_router
from app.api.v1.telemetry import router as telemetry_router
from app.api.v1.topology import router as topology_router

api_v1_router = APIRouter()

# Mount health probes and system information
api_v1_router.include_router(health_router)
api_v1_router.include_router(system_router)

# Mount Phase 2 operational telemetry, service registry, and experiments
api_v1_router.include_router(telemetry_router)
api_v1_router.include_router(services_router)
api_v1_router.include_router(experiments_router)

# Mount Phase 3 ML detection, model registry, and incident prediction
api_v1_router.include_router(ml_router)

# Mount Phase 4 Service Topology and Incident Root-Cause Analysis
api_v1_router.include_router(topology_router)
api_v1_router.include_router(rca_router)

# Mount Phase 5 Agentic Incident Investigation and Safe Remediation
api_v1_router.include_router(investigations_router)
