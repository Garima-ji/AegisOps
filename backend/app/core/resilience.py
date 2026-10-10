"""Reliability, bounded retries with exponential backoff, and graceful degradation handlers."""

from __future__ import annotations

import functools
import logging
import random
import time
from collections.abc import Callable
from typing import Any, TypeVar

from app.observability.metrics import increment_counter

logger = logging.getLogger("aegisops.core.resilience")

F = TypeVar("F", bound=Callable[..., Any])


def retry_with_backoff(
    max_attempts: int = 3,
    initial_delay: float = 0.1,
    max_delay: float = 2.0,
    backoff_factor: float = 2.0,
    retryable_exceptions: tuple[type[Exception], ...] = (Exception,),
    is_idempotent: bool = True,
) -> Callable[[F], F]:
    """Decorator applying bounded exponential backoff with jitter to idempotent operations.

    Non-idempotent operations must NOT be retried automatically.
    """

    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            if not is_idempotent:
                # Disallow automatic retry on non-idempotent operations
                return func(*args, **kwargs)

            attempt = 0
            current_delay = initial_delay

            while True:
                attempt += 1
                try:
                    return func(*args, **kwargs)
                except retryable_exceptions as exc:
                    if attempt >= max_attempts:
                        logger.error(
                            "Operation '%s' exhausted all %d retry attempts. Last error: %s",
                            func.__name__,
                            max_attempts,
                            exc,
                        )
                        increment_counter("resilience.retries_exhausted_total")
                        raise

                    # Apply exponential backoff with random jitter (+/- 20%)
                    jitter = random.uniform(0.8, 1.2)
                    sleep_time = min(current_delay * jitter, max_delay)
                    logger.warning(
                        "Transient failure in '%s' (attempt %d/%d): %s. Retrying in %.3fs...",
                        func.__name__,
                        attempt,
                        max_attempts,
                        exc,
                        sleep_time,
                    )
                    increment_counter("resilience.retries_attempted_total")
                    time.sleep(sleep_time)
                    current_delay = min(current_delay * backoff_factor, max_delay)

        return wrapper  # type: ignore[return-value]

    return decorator


class GracefulDegradationManager:
    """Provides centralized fallback strategies when subcomponents or dependencies fail."""

    @staticmethod
    def handle_missing_model_artifact(
        model_name: str, fallback_action: str = "use_statistical_baselines"
    ) -> dict[str, Any]:
        """Produce an explicit degraded response when a required ML model artifact is absent."""
        logger.warning(
            "Model artifact '%s' unavailable. Degrading gracefully: %s",
            model_name,
            fallback_action,
        )
        increment_counter("ml.model_load.failures_total")
        return {
            "status": "degraded",
            "model_name": model_name,
            "fallback_used": fallback_action,
            "warning": "Prediction generated using statistical baselines because ML artifact is missing.",
        }

    @staticmethod
    def handle_incomplete_traces(service: str, span_count: int) -> dict[str, Any]:
        """Produce structured response when trace visibility is insufficient for causal inference."""
        logger.warning(
            "Low trace coverage for service '%s' (observed %d spans). Preserving uncertainty.",
            service,
            span_count,
        )
        return {
            "status": "abstained",
            "reason": "LOW_TRACE_COVERAGE",
            "message": f"Trace sample size ({span_count}) is below minimum causal threshold (5 spans).",
        }

    @staticmethod
    def handle_simulation_verification_failure(
        action_type: str, expected_state: Any, observed_state: Any
    ) -> dict[str, Any]:
        """Explicitly record simulation verification mismatch and refuse to claim false success."""
        logger.error(
            "Simulation verification failed for '%s'. Expected %s, observed %s.",
            action_type,
            expected_state,
            observed_state,
        )
        increment_counter("agent.remediation.verification_mismatches_total")
        return {
            "verified": False,
            "status": "SIMULATION_VERIFICATION_FAILED",
            "expected_state": expected_state,
            "observed_state": observed_state,
            "message": "Simulated remediation completed without error, but post-execution verification state did not match expected result.",
        }
