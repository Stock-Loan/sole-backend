FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency definition files
COPY pyproject.toml README.md ./
COPY app ./app

# Install dependencies to a specific prefix
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir --prefix=/install ".[prod]"

FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

ARG APP_UID=1000
ARG APP_GID=1000

# Create user and install runtime dependencies
RUN addgroup --system --gid ${APP_GID} appuser && adduser --system --uid ${APP_UID} --gid ${APP_GID} appuser \
    && apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /data/uploads && chmod 0777 /data/uploads

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy the rest of the application
COPY . .

# --- CRITICAL FIX ---
# Explicitly copy the migrations folder.
# If env.py is missing from the context, the build might fail here or later,
# but this ensures it is placed correctly if it exists.
COPY migrations /app/migrations

USER appuser

ENV FORWARDED_ALLOW_IPS=127.0.0.1

# Healthcheck
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD python -c "import os, http.client, sys; port=int(os.getenv('PORT','8080')); conn=http.client.HTTPConnection('localhost', port, timeout=3); conn.request('GET','/api/v1/health'); res=conn.getresponse(); sys.exit(0 if res.status==200 else 1)"

# Production start command
CMD ["sh", "-c", "gunicorn app.main:app -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:${PORT} --workers 1 --timeout 300 --graceful-timeout 30 --keep-alive 5 --log-level info --access-logfile - --error-logfile -"]