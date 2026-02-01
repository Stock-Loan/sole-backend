# SOLE Backend

FastAPI backend scaffold for the SOLE platform, aligned with the provided directory map and tenancy/security requirements.

## Prerequisites

- Python 3.11+
- Docker + Docker Compose

## Quickstart

1. `make setup-env` — interactive bootstrap (choose dev or prod). Creates `.env` for development or `.env.prod` for production.
2. `make build` — build Docker images.
3. `make up` — build and start app + Postgres + Redis.
4. `make migrate` — run Alembic migrations.
5. `make seed` — (optional) seed initial data.
6. `make logs` — follow application logs.
7. `make test` — run the test suite.
8. `make down` — stop and remove containers/volumes.
9. `make clean` — remove all containers, volumes, and images.

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

## Deployment

Below are two supported deployment paths: containerized (Docker) and non-container (systemd or process manager).

### Deploy as Container (Docker)

Recommended for most environments.

1. **Prepare env**
   - Run `make setup-env` and choose **prod**, or provide environment variables via your platform (Kubernetes, ECS, etc.).
   - Ensure `DATABASE_URL` points to your production database and `REDIS_URL` to your Redis instance.
   - Set `TENANCY_MODE`, `DEFAULT_ORG_ID`, `DEFAULT_ORG_NAME`, `DEFAULT_ORG_SLUG`, and JWT keys.

2. **Build and run**
   - Build: `make build`
   - Start: `make up`

3. **Migrate**
   - `make migrate`

4. **(Optional) Seed**
   - `make seed`

5. **Verify**
   - Health check: `GET /api/v1/health/live`
   - Docs: `/docs`

Notes:
- If you use `compose.yaml` in production, mount your secrets (or inject via env) and configure persistent volumes for Postgres/Redis and uploads (`LOCAL_UPLOAD_DIR`).
- If you deploy to a container platform, you can run the same image from `Dockerfile` and set `UVICORN_WORKERS` as needed.

### Deploy as a Normal App (no Docker)

Use this when you want to run the API directly on a VM or server.

1. **System requirements**
   - Python 3.11+
   - Postgres (compatible with the schema in `migrations/`)
   - Redis

2. **Set up the app**
   ```bash
   python -m venv venv
   . venv/bin/activate
   pip install -U pip
   pip install -e .
   ```

3. **Configure environment**
   - Create `.env` or export environment variables. Minimum required:
     - `DATABASE_URL`, `REDIS_URL`
     - `SECRET_KEY`
     - `JWT_PRIVATE_KEY`/`JWT_PUBLIC_KEY` (or `JWT_PRIVATE_KEY_PATH`/`JWT_PUBLIC_KEY_PATH`)
     - `DEFAULT_ORG_ID`, `DEFAULT_ORG_NAME`, `DEFAULT_ORG_SLUG`
     - `TENANCY_MODE`
   - Optional: `ALLOWED_ORIGINS`, `LOG_LEVEL`, `ENABLE_HSTS`, `PROXIES_COUNT`, `LOCAL_UPLOAD_DIR`

4. **Run migrations**
   ```bash
   alembic upgrade head
   ```

5. **Start the server**
   ```bash
   uvicorn app.main:app --host 0.0.0.0 --port 8000
   ```
   For production, run via systemd, supervisord, or a process manager and configure multiple workers (e.g., `--workers 2`).

6. **Verify**
   - `GET /api/v1/health/live`

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
