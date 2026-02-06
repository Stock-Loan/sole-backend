# SOLE Backend

FastAPI backend scaffold for the SOLE platform.

## Prerequisites

- **Python 3.11+**
- **Docker + Docker Compose**
- **Google Cloud CLI** (`gcloud`) - _Required for deployment_

---

## Tenancy & Identity

- **Org-scoped identity:** Users are scoped to an org (`org_id + email` is unique). The same email can exist in multiple orgs as separate accounts.
- **Tenant context:** In multi-tenant mode, requests must include `X-Org-Id`. Tokens include an `org` claim and are rejected if they do not match the resolved tenant (superusers may bypass in multi-tenant mode).
- **Isolation:** Profile data and memberships are org-scoped; cross-org access is rejected by API checks and composite foreign keys.

---

## 🚀 Local Development (Daily Workflow)

We use Docker Compose to run the API, Postgres, and Redis locally with hot-reloading enabled.

### 1. Setup Environment

Run the interactive setup to create your local `.env` file:

```bash
make setup-env
# Choose 'dev' when prompted
```

### Seed Data (Local/Dev)

The seed script (`make seed`) creates org-scoped admin accounts and demo users.

- `SEED_ADMIN_EMAIL` + `SEED_ADMIN_PASSWORD` create **one admin user per org** (org-scoped identity).
- `EXTRA_SEED_ORG_IDS` (comma-separated) creates additional orgs and **dedicated** admin users in each org.
- In non-production environments, demo users are created per org:
  - `hr-<org_id>@example.com` (HR + EMPLOYEE roles)
  - `employee-<org_id>@example.com` (EMPLOYEE role)
  - Password = `SEED_ADMIN_PASSWORD`

To disable demo users in production, set `ENVIRONMENT=production`.

### 2. Start the App

Start the API, Database, and Redis. The API will auto-reload when you save files.

```bash
make up

```

- **API:** http://localhost:8000
- **Docs:** http://localhost:8000/docs
- **Health:** http://localhost:8000/api/v1/health

### 3. Initialize Database

Since your local DB is fresh, run migrations and seed the initial admin user.

```bash
# Apply migrations
make migrate

# Seed initial data (admin user)
make seed

```

### Common Commands

| Command                     | Description                                          |
| --------------------------- | ---------------------------------------------------- |
| `make up`                   | Start all services (detached mode)                   |
| `make down`                 | Stop all services                                    |
| `make logs`                 | Tail logs for all services                           |
| `make revision m="message"` | Create a new Alembic migration file                  |
| `make migrate`              | Run `alembic upgrade head` locally                   |
| `make test`                 | Run the full test suite                              |
| `make clean`                | Nuke everything (containers, volumes) to start fresh |

---

## ☁️ Production Deployment (Google Cloud Run)

Production uses **Cloud Run** for the API and **Cloud Run Jobs** for migrations/seeding.

- **Service:** `sole-api` (The API server)
- **Jobs:** `sole-db-migrate` (Runs Alembic), `sole-db-seed` (Runs initial data)

### One-Command Release

To build, deploy, update jobs, and run migrations automatically:

```bash
make prod-release

```

### Manual Steps

If you need more control, you can run steps individually:

1. **Deploy Code** (Builds and pushes image to Cloud Run Service):

```bash
make deploy

```

2. **Update Jobs** (Updates the Migration/Seed jobs to use the new image):

```bash
make prod-update-jobs

```

3. **Run Migrations** (Executes schema changes on Cloud SQL/Neon):

```bash
make prod-migrate

```

### Production Logs

To see errors from Cloud Run without leaving your terminal:

```bash
make prod-logs

```

---

## ✅ Release Checklist (Org-Scoped)

- Set `ENVIRONMENT=production` in production config to avoid demo user seeding.
- Confirm CI/CD runs the **current baseline migrations** (legacy migrations are archived and should not be used).
- If using `EXTRA_SEED_ORG_IDS`, verify each org has a **dedicated** admin user seeded.
- Verify logs/metrics include `org_id`/`tenant_id` for request tracing and isolation checks.

---

## 🔧 Configuration & Secrets

### Local (`.env`)

The `make setup-env` script creates this. It controls your local Docker environment.

- **Database:** `postgresql+psycopg://user:password@db:5432/sole-db`
- **Redis:** `redis://redis:6379/0`
- **Secrets:** Can be weak (e.g., `secret`) for local dev.

### Production (Google Secret Manager + Config File)

We do **not** use `.env` files in production. Non-secret configuration lives in
`config.prod.yaml` and is passed to Cloud Run via `--env-vars-file`. Secrets are injected via Google Secret Manager.

Ensure these secrets exist in your Google Cloud Project:
- `DATABASE_URL` (POOLED connection string for runtime, e.g. Neon pooler host)
- `DATABASE_URL_DIRECT` (DIRECT connection string for migrations/admin tasks only)
- `JWT_PRIVATE_KEY`
- `JWT_PUBLIC_KEY`
- `REDIS_URL`
- `SECRET_KEY`
- `SEED_ADMIN_EMAIL`
- `SEED_ADMIN_PASSWORD`

---

## 📂 Project Layout

- `app/` - Main application code
- `api/` - Routes and controllers
- `core/` - Settings and security config
- `db/` - Database session and base models
- `models/` - SQLAlchemy models

- `migrations/` - Alembic migration scripts
- `tests/` - Pytest suite
- `compose.yaml` - **Local Dev** configuration (Mounts code, hot-reload)
- `Dockerfile` - **Production** configuration (Optimized, Gunicorn)
- `cloudbuild.yaml` - Google Cloud Build config
- `Makefile` - Shortcuts for all commands

---

## ⛑ Troubleshooting

### "ImportError: Can't find Python file migrations/env.py" (Cloud Run)

**Cause:** The `migrations` folder wasn't copied into the container or was ignored.
**Fix:**

1. Check `.gcloudignore` and remove `!migrations/**` if present.
2. Ensure `Dockerfile` has `COPY migrations /app/migrations`.
3. Run `make prod-release` to rebuild.

### "ValidationError: Field required [SECRET_KEY]" (Cloud Run Jobs)

**Cause:** Cloud Run Jobs do **not** inherit environment variables from the Service.
**Fix:** You must set env vars explicitly on the job.

```bash
gcloud run jobs update sole-db-migrate --set-env-vars SECRET_KEY="...",REDIS_URL="..."

```

If you are using pooled runtime connections, also set the **direct** URL for jobs:

```bash
gcloud run jobs update sole-db-migrate --set-secrets DATABASE_URL_DIRECT=DATABASE_URL_DIRECT:latest
```

### "Code changes aren't showing up locally"

**Cause:** You might be running the production image instead of the dev volume mount.
**Fix:**

1. Run `make down`
2. Run `make up` (This forces `docker compose` to use the overrides in `compose.yaml`)

### "Build failed: invalid argument" (Cloud Build)

**Cause:** Using a custom Service Account without a configured logs bucket.
**Fix:** Ensure `cloudbuild.yaml` has `options: { logging: CLOUD_LOGGING_ONLY }` at the end.
