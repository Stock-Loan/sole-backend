# SOLE Backend

FastAPI backend scaffold for the SOLE platform, aligned with the provided directory map and tenancy/security requirements.

## Prerequisites
- Python 3.11+
- Docker + Docker Compose

## Quickstart
- `make up` — build and start app + Postgres + Redis
- `make logs` — follow application logs
- `make migrate` — run Alembic migrations (placeholder until models exist)
- `make test` — execute pytest suite inside the container
- `make down` — stop and remove containers/volumes

The API listens on http://localhost:8000 with a health check at `/api/v1/health`.

## Configuration
Environment defaults live in `.env.example`:
- `TENANCY_MODE` (`single`|`multi`)
- `DATABASE_URL` (async driver)
- `REDIS_URL`
- `SESSION_TIMEOUT_MINUTES`, `ACCESS_TOKEN_EXPIRE_MINUTES`
- `SECRET_KEY`

## Project Layout
- `app/` — application code (api, core, db, models, services, etc.)
- `migrations/` — Alembic migrations (async-ready scaffold)
- `tests/` — pytest suite
- `compose.yaml`, `Dockerfile`, `Makefile`, `pyproject.toml` — infra/tooling (Postgres 18-alpine, Redis 7)
