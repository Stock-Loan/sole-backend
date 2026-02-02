FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY app ./app

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir --prefix=/install ".[dev]"

FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

ARG APP_UID=1000
ARG APP_GID=1000

RUN addgroup --system --gid ${APP_GID} appuser && adduser --system --uid ${APP_UID} --gid ${APP_GID} appuser \
    && apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /data/uploads && chmod 0777 /data/uploads

COPY --from=builder /install /usr/local
COPY . .

USER appuser

ENV FORWARDED_ALLOW_IPS=127.0.0.1

# Optional: this is mostly ignored by Cloud Run, but if you keep it, don't hardcode 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD python -c "import os, http.client, sys; port=int(os.getenv('PORT','8080')); conn=http.client.HTTPConnection('localhost', port, timeout=3); conn.request('GET','/api/v1/health'); res=conn.getresponse(); sys.exit(0 if res.status==200 else 1)"

# Production server (recommended)
CMD ["sh", "-c", "gunicorn -k uvicorn.workers.UvicornWorker app.main:app --bind 0.0.0.0:${PORT} --workers ${WEB_CONCURRENCY:-2} --timeout 120 --access-logfile - --error-logfile -"]
