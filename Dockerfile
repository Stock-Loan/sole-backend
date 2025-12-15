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
    && pip install --no-cache-dir --prefix=/install .[dev]

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

COPY --from=builder /install /usr/local
COPY . .

USER appuser

ENV FORWARDED_ALLOW_IPS=127.0.0.1

HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD python -c "import http.client, sys; conn = http.client.HTTPConnection('localhost', 8000, timeout=3); conn.request('GET', '/api/v1/health'); res = conn.getresponse(); sys.exit(0 if res.status == 200 else 1)"

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips=${FORWARDED_ALLOW_IPS}"]
