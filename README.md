# SOLE Backend

FastAPI backend scaffold for the SOLE platform, aligned with the provided directory map and tenancy/security requirements.

## Prerequisites
- Python 3.11+
- Docker + Docker Compose

## Quickstart
- `./setup.sh` — one-time local bootstrap (creates/fills `.env`, generates strong `SECRET_KEY`, and creates RSA keys for JWT).
- `make up` — build and start app + Postgres + Redis
- `make logs` — follow application logs
- `make migrate` — run Alembic migrations (placeholder until models exist)
- `make test` — execute pytest suite inside the container
- `make down` — stop and remove containers/volumes

The API listens on http://localhost:8000 with a health check at `/api/v1/health`.

## Configuration
Environment defaults live in `.env.example` (the setup script copies to `.env` and fills any missing values):
- `TENANCY_MODE` (`single`|`multi`)
- `DATABASE_URL` (async driver)
- `REDIS_URL`
- `SESSION_TIMEOUT_MINUTES`, `ACCESS_TOKEN_EXPIRE_MINUTES`
- `ALLOWED_ORIGINS`, `LOG_LEVEL`, `ENABLE_HSTS`
- `DEFAULT_ORG_ID` (used when `TENANCY_MODE=single`)
- `SECRET_KEY`
- JWT keys: `JWT_PRIVATE_KEY`/`JWT_PUBLIC_KEY` inline PEM or file paths via `JWT_PRIVATE_KEY_PATH`/`JWT_PUBLIC_KEY_PATH` (RS256).
- Provide real values via environment variables or mounted secrets (`/run/secrets`); no secrets are committed.

## Project Layout
- `app/` — application code (api, core, db, models, services, etc.)
- `migrations/` — Alembic migrations (async-ready scaffold)
- `tests/` — pytest suite
- `compose.yaml`, `Dockerfile`, `Makefile`, `pyproject.toml` — infra/tooling (Postgres 18-alpine, Redis 7). Supply environment via `.env` or your secrets manager.

## Security & Observability Baseline
- Containers built with a multi-stage pipeline and run as non-root user; uvicorn configured with proxy headers.
- Security headers middleware (HSTS optional via `ENABLE_HSTS`), CORS configured from `ALLOWED_ORIGINS`.
- Structured JSON logging with request/tenant IDs, with separate handlers for transactional vs. audit logs.
- JWTs signed with RS256 using provided private/public keys.
