# ==============================================================================
# AegisOps Multi-Stage Production Dockerfile
# ==============================================================================

# Stage 1: Build & Dependency Wheel Cache
FROM python:3.12-slim AS builder

WORKDIR /build

# Install compilation prerequisites
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Create isolated build virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy package specifications
COPY pyproject.toml README.md ./
COPY backend/ backend/

# Install application and production dependencies
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir .

# Stage 2: Final Secure Minimal Runtime
FROM python:3.12-slim AS runner

WORKDIR /app

# Install minimal runtime dependencies (curl for container healthchecks)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Copy virtual environment from builder stage
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Security: Create non-privileged user and group
RUN groupadd -g 10001 aegisops && \
    useradd -u 10001 -g aegisops -s /bin/bash -m aegisops

# Copy application source code and migrations
COPY --chown=aegisops:aegisops backend /app/backend
COPY --chown=aegisops:aegisops alembic.ini /app/alembic.ini
COPY --chown=aegisops:aegisops pyproject.toml /app/pyproject.toml

# Set secure execution environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH="/app/backend" \
    PORT=8000 \
    HOST=0.0.0.0

USER aegisops

EXPOSE 8000

# Container liveness check
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health/live || exit 1

# Production server entrypoint
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
