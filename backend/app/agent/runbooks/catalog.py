"""Versioned local runbook catalog and deterministic keyword/token retriever."""

from __future__ import annotations

import re

from pydantic import BaseModel


class RunbookDocument(BaseModel):
    """Structured operational runbook entry."""

    id: str
    title: str
    target_service: str
    symptoms: list[str]
    diagnostic_steps: list[str]
    remediation_actions: list[str]
    content: str
    version: str = "1.0.0"


# Built-in versioned local runbook knowledge base
LOCAL_RUNBOOKS: list[RunbookDocument] = [
    RunbookDocument(
        id="rb-pay-latency-001",
        title="Payment Gateway Latency & Downstream Slowdown",
        target_service="payment-service",
        symptoms=[
            "payment-service http.server.request.duration_ms p95 > 800ms",
            "order-service http.server.request.duration_ms p95 > 850ms",
            "Elevated span duration across payment-service spans",
        ],
        diagnostic_steps=[
            "Inspect third-party payment gateway latency metrics and circuit breaker trip state.",
            "Verify thread pool and connection pool saturation on payment-service instances.",
            "Check upstream order-service retry policies to ensure retry amplification is avoided.",
        ],
        remediation_actions=[
            "mitigate_latency_injection",
            "restore_service",
            "stop_fault_scenario",
        ],
        content=(
            "# Runbook: Payment Gateway Slowdown\n\n"
            "## Summary\n"
            "Cascading latency in payment-service propagating to order-service.\n\n"
            "## Recommended Remediation\n"
            "1. Deactivate ongoing fault injection or simulator delay.\n"
            "2. Reset latency throttle parameter to nominal (0ms).\n"
            "3. Enable circuit breaker on order-service if latency persists.\n"
        ),
    ),
    RunbookDocument(
        id="rb-db-exhaust-002",
        title="Database Connection Pool Exhaustion & Transaction Rollback",
        target_service="payment-service",
        symptoms=[
            "payment-service http.server.errors.count surge",
            "order-service DownstreamPaymentError structured log spike",
            "Database pool timeout and connection acquisition failure logs",
        ],
        diagnostic_steps=[
            "Check active vs idle PostgreSQL connections in pg_stat_activity.",
            "Verify presence of long-running uncommitted transactions or lock contention.",
            "Verify max_pool_size configuration against worker thread concurrency.",
        ],
        remediation_actions=[
            "stop_fault_scenario",
            "restore_service",
        ],
        content=(
            "# Runbook: Database Connection Pool Exhaustion\n\n"
            "## Summary\n"
            "Connection pool starvation causing 500 errors and transaction rollback.\n\n"
            "## Recommended Remediation\n"
            "1. Stop fault scenario injecting simulated error surges.\n"
            "2. Recycle connection pools and clear idle stale connections.\n"
            "3. Restore nominal database pool settings.\n"
        ),
    ),
    RunbookDocument(
        id="rb-net-blackhole-003",
        title="Downstream Network Blackhole & Client Timeout",
        target_service="payment-service",
        symptoms=[
            "order-service ClientTimeoutException logged at CRITICAL severity",
            "order-service request duration plateaus at timeout threshold (2000ms)",
            "payment-service spans incomplete or cancelled with ERROR status",
        ],
        diagnostic_steps=[
            "Verify network reachability and socket connectivity between order-service and payment-service.",
            "Inspect TCP SYN retransmissions and dropped packets on virtual interface.",
            "Check client timeout deadline settings on outbound HTTP clients.",
        ],
        remediation_actions=[
            "restore_service",
            "stop_fault_scenario",
        ],
        content=(
            "# Runbook: Downstream Network Blackhole\n\n"
            "## Summary\n"
            "Client calls to payment-service timeout after 2000ms, triggering cascading failures.\n\n"
            "## Recommended Remediation\n"
            "1. Deactivate simulated network timeout or fault scenario.\n"
            "2. Verify order-service fallback circuit breaker status.\n"
            "3. Restore service routing to healthy target replica.\n"
        ),
    ),
    RunbookDocument(
        id="rb-inv-deadlock-004",
        title="Inventory Lock Contention and Stock Reservation Timeout",
        target_service="inventory-service",
        symptoms=[
            "inventory-service database lock timeout",
            "High concurrency lock wait on inventory reservation table",
        ],
        diagnostic_steps=[
            "Inspect exclusive row locks on stock inventory tables.",
            "Review transaction isolation levels on reservation workflows.",
        ],
        remediation_actions=[
            "restore_service",
            "stop_fault_scenario",
        ],
        content=(
            "# Runbook: Inventory Lock Contention\n\n"
            "## Summary\n"
            "Row lock contention causing reservation timeouts in inventory-service.\n\n"
            "## Recommended Remediation\n"
            "1. Reset inventory-service simulation state.\n"
            "2. Release blocked advisory locks.\n"
        ),
    ),
    RunbookDocument(
        id="rb-notif-backlog-005",
        title="Notification Queue Backlog and Worker Starvation",
        target_service="notification-service",
        symptoms=[
            "notification-service queue backlog count exceeding threshold",
            "Delayed customer dispatch notifications",
        ],
        diagnostic_steps=[
            "Check queue consumer lag and dead-letter queue metrics.",
            "Verify downstream third-party email/SMS provider health.",
        ],
        remediation_actions=[
            "restore_service",
        ],
        content=(
            "# Runbook: Notification Queue Backlog\n\n"
            "## Summary\n"
            "Backlog in notification delivery workers.\n\n"
            "## Recommended Remediation\n"
            "1. Restart worker processing tasks.\n"
            "2. Flush or purge dead-letter queues if unrecoverable.\n"
        ),
    ),
]


class RunbookRetriever:
    """Deterministic token/keyword retriever for local versioned runbooks."""

    def __init__(self, runbooks: list[RunbookDocument] | None = None) -> None:
        self.runbooks = runbooks or list(LOCAL_RUNBOOKS)

    def retrieve(
        self,
        query: str,
        target_service: str | None = None,
        limit: int = 3,
    ) -> list[tuple[RunbookDocument, float]]:
        """Retrieve most relevant runbooks based on query terms and service matching."""
        query_tokens = set(re.findall(r"\w+", query.lower()))
        results: list[tuple[RunbookDocument, float]] = []

        for rb in self.runbooks:
            score = 0.0

            # Service match is a heavy positive boost
            if target_service and rb.target_service == target_service:
                score += 5.0

            # Document text token matching
            doc_text = (
                f"{rb.title} {rb.target_service} {' '.join(rb.symptoms)} {rb.content}".lower()
            )
            doc_tokens = set(re.findall(r"\w+", doc_text))

            overlap = query_tokens.intersection(doc_tokens)
            score += float(len(overlap))

            if score > 0.0:
                results.append((rb, score))

        # Sort descending by score
        results.sort(key=lambda x: x[1], reverse=True)
        return results[:limit]
