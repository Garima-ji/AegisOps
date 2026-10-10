"""Main FastAPI application entry point and lifespan configuration."""

import time
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.health import router as root_health_router
from app.api.v1.router import api_v1_router
from app.core.config import Settings, get_settings
from app.core.exceptions import AegisOpsError
from app.core.logging import (
    get_correlation_id,
    get_logger,
    set_correlation_id,
    setup_logging,
)
from app.db.session import reset_engine
from app.observability.metrics import increment_counter, record_duration
from app.observability.tracer import tracer
from app.schemas.common import ProblemDetails

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan managing startup configuration and clean shutdown."""
    settings = get_settings()
    setup_logging(settings)
    logger.info(
        "Starting %s v%s (environment: %s, debug: %s)",
        settings.app_name,
        settings.app_version,
        settings.environment,
        settings.debug,
    )
    yield
    logger.info("Shutting down %s, disposing database connection pools...", settings.app_name)
    reset_engine()
    logger.info("Shutdown complete.")


def create_app(settings: Settings | None = None) -> FastAPI:
    """FastAPI application factory."""
    if settings is None:
        settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="AegisOps — AI-Powered IT Operations Intelligence Platform",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    # 1. CORS Middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 2. Correlation ID & Request Logging Middleware
    @app.middleware("http")
    async def request_context_middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        req_id = (
            request.headers.get("X-Request-ID")
            or request.headers.get("X-Correlation-ID")
            or f"req_{uuid.uuid4().hex[:12]}"
        )
        set_correlation_id(req_id)
        start_time = time.perf_counter()

        with tracer.span(
            f"http_{request.method.lower()}",
            tags={"path": request.url.path, "method": request.method},
            trace_id=req_id,
        ) as span:
            try:
                response = await call_next(request)
                duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
                response.headers["X-Request-ID"] = req_id

                # Record metrics
                increment_counter(
                    "http.requests.total",
                    labels={"method": request.method, "status": str(response.status_code)},
                )
                record_duration("http_request", duration_ms)
                span.set_tag("status_code", response.status_code)

                logger.info(
                    "%s %s completed with status %d in %sms",
                    request.method,
                    request.url.path,
                    response.status_code,
                    duration_ms,
                )
                return response
            except Exception as exc:
                duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
                increment_counter("http.requests.errors_total")
                span.finish(
                    status="error", error_category=type(exc).__name__, error_message=str(exc)
                )
                logger.error(
                    "%s %s unhandled exception after %sms: %s",
                    request.method,
                    request.url.path,
                    duration_ms,
                    exc,
                    exc_info=True,
                )
                detail_msg = (
                    str(exc)
                    if settings.debug
                    else "An unexpected internal server error occurred. Please contact the administrator."
                )
                problem = ProblemDetails(
                    type="https://aegisops.dev/errors/internal-server-error",
                    title="Internal Server Error",
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=detail_msg,
                    instance=request.url.path,
                    code="INTERNAL_SERVER_ERROR",
                    request_id=req_id,
                )
                return JSONResponse(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    content=problem.model_dump(mode="json"),
                    headers={"X-Request-ID": req_id},
                )
            finally:
                set_correlation_id(None)

    # 3. RFC 7807 Exception Handlers
    @app.exception_handler(AegisOpsError)
    async def domain_exception_handler(request: Request, exc: AegisOpsError) -> JSONResponse:
        problem = ProblemDetails(
            type=f"https://aegisops.dev/errors/{exc.code.lower().replace('_', '-')}",
            title=exc.code.replace("_", " ").title(),
            status=exc.status_code,
            detail=exc.message,
            instance=request.url.path,
            code=exc.code,
            request_id=get_correlation_id(),
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=problem.model_dump(mode="json"),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        error_details = []
        for error in exc.errors():
            loc = " -> ".join(str(item) for item in error.get("loc", []))
            error_details.append(
                {
                    "field": loc,
                    "message": error.get("msg", ""),
                    "type": error.get("type", ""),
                }
            )

        problem = ProblemDetails(
            type="https://aegisops.dev/errors/validation-error",
            title="Request Validation Error",
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="The incoming request payload failed schema validation rules.",
            instance=request.url.path,
            code="VALIDATION_ERROR",
            request_id=get_correlation_id(),
            errors=error_details,
        )
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=problem.model_dump(mode="json"),
        )

    @app.exception_handler(HTTPException)
    @app.exception_handler(404)
    async def http_exception_handler(request: Request, exc: Any) -> JSONResponse:
        status_code = getattr(exc, "status_code", 404)
        detail = getattr(exc, "detail", "Not Found")
        title = "Not Found" if status_code == 404 else f"HTTP {status_code}"
        problem = ProblemDetails(
            type=f"https://aegisops.dev/errors/http-{status_code}",
            title=title,
            status=status_code,
            detail=str(detail),
            instance=request.url.path,
            code=f"HTTP_{status_code}",
            request_id=get_correlation_id(),
        )
        return JSONResponse(
            status_code=status_code,
            content=problem.model_dump(mode="json"),
        )

    @app.exception_handler(Exception)
    async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Internal unhandled error processing request: %s", exc)
        detail_msg = (
            str(exc)
            if settings.debug
            else "An unexpected internal server error occurred. Please contact the administrator."
        )
        problem = ProblemDetails(
            type="https://aegisops.dev/errors/internal-server-error",
            title="Internal Server Error",
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=detail_msg,
            instance=request.url.path,
            code="INTERNAL_SERVER_ERROR",
            request_id=get_correlation_id(),
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=problem.model_dump(mode="json"),
        )

    # 4. Route Registration
    # Direct liveness and readiness probes at root for standard orchestrator probes
    app.include_router(root_health_router)
    # API v1 versioned endpoints
    app.include_router(api_v1_router, prefix=settings.api_v1_prefix)

    return app


app = create_app()
