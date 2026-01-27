# SOLE Backend

FastAPI backend scaffold for the SOLE platform, aligned with the provided directory map and tenancy/security requirements.

## Prerequisites

- Python 3.11+
- Docker + Docker Compose

## Quickstart

1. `make setup-env` — interactive bootstrap (choose dev or prod). Creates `.env` for development or `.env.prod` for production.
2. `make up` — build and start app + Postgres + Redis.
3. `make migrate` — run Alembic migrations.
4. `make logs` — follow application logs.
5. `make test` — run the test suite.
6. `make down` — stop and remove containers/volumes.
7. `make clean` — remove all containers, volumes, and images.

The API listens on http://localhost:8000 with a health check at `/api/v1/health`.

## Getting Started (Step-by-Step)

1. Run `make setup-env` and choose **dev** when prompted.
2. Verify `.env` was created in the project root and review values (especially `ALLOWED_ORIGINS` and `DATABASE_URL`).
3. Start services with `make up`.
4. Apply migrations with `make migrate`.
5. Open docs at http://localhost:8000/docs.

## Development Workflow

- Use `make logs` to monitor API output.
- Use `make test` for the full test suite, or `make test-unit` / `make test-integration` for smaller runs.
- Use `make fmt`, `make lint`, and `make type` before pushing changes.

## Production Notes

- Run `make setup-env` and choose **prod** to create `.env.prod`.
- Production setup requires all values and writes inline RSA keys to `.env.prod` (no secrets are written to disk).
- Prefer injecting secrets via environment variables or your secrets manager in production.

## Configuration

Environment defaults will be created automatically if you **run `make setup-env`** and choose **dev**. The dev setup writes `.env` from prompts, and the prod setup writes `.env.prod` with required values:

- `TENANCY_MODE` (`single`|`multi`)
- `DATABASE_URL` (async driver; for docker-compose use `...@db:5432/sole`)
- `REDIS_URL`
- `SESSION_TIMEOUT_MINUTES`, `ACCESS_TOKEN_EXPIRE_MINUTES`
- `ALLOWED_ORIGINS`, `LOG_LEVEL`, `ENABLE_HSTS`
- `DEFAULT_ORG_ID`, `DEFAULT_ORG_NAME`, `DEFAULT_ORG_SLUG`
- `RATE_LIMIT_PER_MINUTE`, `LOGIN_ATTEMPT_LIMIT`, `LOGIN_LOCKOUT_MINUTES`, `DEFAULT_PASSWORD_MIN_LENGTH`
- `SECRET_KEY`
- JWT keys: `JWT_PRIVATE_KEY`/`JWT_PUBLIC_KEY` inline PEM or file paths via `JWT_PRIVATE_KEY_PATH`/`JWT_PUBLIC_KEY_PATH` (RS256). For dev, paths are relative to the project root (e.g., `./secrets/...`).
- `PROXIES_COUNT` (default: `1`). Controls how many proxies are trusted for IP resolution.
- Provide real values via environment variables or mounted secrets (`/run/secrets`); no secrets are committed.

## Common Make Targets

- `make up` — start services
- `make down` — stop services
- `make logs` — tail logs
- `make migrate` — run migrations
- `make revision m="message"` — create a new migration
- `make test` — run test suite
- `make fmt` / `make lint` / `make type` — code quality checks

## Troubleshooting

- If containers fail to start, run `make logs` and check for missing env values.
- If migrations fail, ensure `DATABASE_URL` points to the running DB (`...@db:5432/sole` for Docker).
- If CORS errors occur, ensure `ALLOWED_ORIGINS` is valid JSON, e.g. `["http://localhost:5173"]`.

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

## MFA Notes

- MFA secrets are encrypted at rest using a Fernet key derived from `SECRET_KEY`.
- Changing `SECRET_KEY` invalidates existing MFA secrets and will break verification until users re-enroll.
- For consistent MFA behavior across environments, keep `SECRET_KEY` stable between restarts.
- Org-level MFA settings live in `org_settings` and are configured via the org settings endpoints.
- `require_two_factor` enforces MFA for users in the org.
- `mfa_required_actions` controls which actions require a fresh MFA check (e.g., login, org settings changes).
- `remember_device_days` controls how long a remembered device can bypass MFA prompts.
- MFA enrollment and verification use TOTP (e.g., Google Authenticator, Authy).
- Users can remember devices for a configurable period to reduce MFA prompts.
- Users can disable MFA from their profile, which removes the MFA secret.
- Users must have MFA enabled to perform actions requiring MFA.
- Users with admin roles must enroll MFA on login.
