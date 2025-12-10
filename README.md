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
- `DATABASE_URL` (async driver; for docker-compose use `...@db:5432/sole`)
- `REDIS_URL`
- `SESSION_TIMEOUT_MINUTES`, `ACCESS_TOKEN_EXPIRE_MINUTES`
- `ALLOWED_ORIGINS`, `ALLOWED_TENANT_HOSTS`, `LOG_LEVEL`, `ENABLE_HSTS`
- `DEFAULT_ORG_ID` (used when `TENANCY_MODE=single`)
- `RATE_LIMIT_PER_MINUTE`, `LOGIN_ATTEMPT_LIMIT`, `LOGIN_LOCKOUT_MINUTES`, `DEFAULT_PASSWORD_MIN_LENGTH`
- `SECRET_KEY`
- JWT keys: `JWT_PRIVATE_KEY`/`JWT_PUBLIC_KEY` inline PEM or file paths via `JWT_PRIVATE_KEY_PATH`/`JWT_PUBLIC_KEY_PATH` (RS256).
- `PROXIES_COUNT` (default: `1`). Controls how many proxies are trusted for IP resolution.
- Provide real values via environment variables or mounted secrets (`/run/secrets`); no secrets are committed.

### Proxy Configuration (`PROXIES_COUNT`)

The application uses `PROXIES_COUNT` to securely determine the client's real IP address when running behind load balancers or proxies. It trusts the last `N` IP addresses in the `X-Forwarded-For` header.

*   **Render / Heroku / AWS ALB:** Set `PROXIES_COUNT=1` (Default). These platforms terminate SSL and pass the request via one load balancer.
*   **Direct Connection (VPS/Local):** Set `PROXIES_COUNT=0`. Use this if the app is directly exposed to the internet or during local development without a proxy.
*   **Cloudflare -> Nginx -> App:** Set `PROXIES_COUNT=2`. Trust Cloudflare (1) and your Nginx ingress (1).

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
