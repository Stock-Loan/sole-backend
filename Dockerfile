FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libpq-dev python3-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency definition files
COPY pyproject.toml README.md ./
COPY app ./app

# Install dependencies to a specific prefix
RUN pip install --default-timeout=100 --no-cache-dir --upgrade pip \
    && pip install --default-timeout=100 --no-cache-dir --prefix=/install ".[prod]"

FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

ARG APP_UID=1000
ARG APP_GID=1000
ENV GUNICORN_WORKERS=2 \
    CONFIG_FILE=/app/config.prod.yaml

# Create user and install runtime dependencies
RUN addgroup --system --gid ${APP_GID} appuser && adduser --system --uid ${APP_UID} --gid ${APP_GID} appuser \
    && apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl libpq5 \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /data/uploads \
    && chown appuser:appuser /data/uploads \
    && chmod 0770 /data/uploads

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy only required runtime artifacts
COPY app /app/app
COPY migrations /app/migrations
COPY alembic.ini /app/alembic.ini
COPY config*.yaml /app/

USER appuser

ENV FORWARDED_ALLOW_IPS=127.0.0.1

# Healthcheck
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD python -c "import os, http.client, sys; port=int(os.getenv('PORT','8080')); conn=http.client.HTTPConnection('localhost', port, timeout=3); conn.request('GET','/api/v1/health/live'); res=conn.getresponse(); sys.exit(0 if res.status==200 else 1)"

# Production start command
CMD ["sh", "-c", "gunicorn app.main:app -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:${PORT} --workers ${GUNICORN_WORKERS} --timeout 300 --graceful-timeout 30 --keep-alive 5 --log-level info --access-logfile - --error-logfile -"]
