DC = docker compose
APP = app
DB = db
REDIS = redis

# --- PRODUCTION CONFIG ---
PROJECT_ID ?= sole-486122
REGION ?= us-central1
SERVICE_NAME ?= sole-api
MIGRATE_JOB ?= sole-db-migrate
SEED_JOB ?= sole-db-seed

.PHONY: help up down logs logs-api logs-db restart ps build clean migrate migrate-host revision downgrade seed shell db-shell redis-shell fmt lint type test test-cov test-unit test-integration install setup-env health deploy prod-update-jobs prod-migrate prod-seed prod-logs prod-release audit check

.DEFAULT_GOAL := help

help: ## Show this help message
	@echo 'Usage: make [target]'
	@echo ''
	@echo 'Available targets:'
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  %-20s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

# ==============================================================================
# LOCAL DEVELOPMENT (Docker Compose)
# ==============================================================================

build: ## Build local Docker images
	$(DC) build

up: ## Start local dev server (Hot Reload) without rebuilding
	$(DC) up -d
	@echo "Local API started: http://localhost:8000"
	@echo "Docs: http://localhost:8000/docs"

down: ## Stop local services
	$(DC) down

logs: ## Tail logs from all local services
	$(DC) logs -f

logs-api: ## Tail logs from local API only
	$(DC) logs -f $(APP)

logs-db: ## Tail logs from local DB only
	$(DC) logs -f $(DB)

restart: ## Restart local services
	$(DC) restart

clean: ## Stop and remove local containers, volumes, networks
	$(DC) down -v
	@echo "Cleaned up local environment."

setup-env: ## Set up local environment files (runs setup.sh)
	@./setup.sh

migrate: ## Run migrations on LOCAL database
	$(DC) run --rm $(APP) alembic upgrade head

revision: ## Create new migration file (usage: make revision m="description")
	@if [ -z "$(m)" ]; then \
		echo "Error: Migration message required. Usage: make revision m=\"your message\""; \
		exit 1; \
	fi
	$(DC) run --rm $(APP) alembic revision --autogenerate -m "$(m)"

seed: ## Seed LOCAL database
	$(DC) run --rm $(APP) python -m app.db.init_db

shell: ## Open Python shell in local container
	$(DC) run --rm $(APP) python

db-shell: ## Open SQL shell in local database
	$(DC) exec $(DB) psql -U $$POSTGRES_USER -d $$POSTGRES_DB

# ==============================================================================
# PRODUCTION (Google Cloud Run)
# ==============================================================================

deploy: ## Build and Deploy API to Cloud Run
	@echo "🚀 Deploying to Google Cloud..."
	gcloud builds submit --config cloudbuild.yaml .

prod-update-jobs: ## Update Cloud Run Jobs with the latest API image
	@echo "🔄 Updating migration and seed jobs with latest image..."
	@IMG=$$(gcloud run services describe $(SERVICE_NAME) --region $(REGION) --format='value(spec.template.spec.containers[0].image)'); \
	echo "Using image: $$IMG"; \
	gcloud run jobs update $(MIGRATE_JOB) --image $$IMG --region $(REGION) --quiet; \
	gcloud run jobs update $(SEED_JOB) --image $$IMG --region $(REGION) --quiet

prod-migrate: ## Execute Migration Job on Cloud
	@echo "🐘 Running migrations on Cloud SQL..."
	gcloud run jobs execute $(MIGRATE_JOB) --region $(REGION) --wait

prod-seed: ## Execute Seed Job on Cloud
	@echo "🌱 Seeding Cloud SQL..."
	gcloud run jobs execute $(SEED_JOB) --region $(REGION) --wait

prod-logs: ## View recent errors from Cloud Run
	gcloud logging read 'resource.type="cloud_run_revision" AND severity>=ERROR' --limit 20 --format="value(textPayload,jsonPayload.message)"

prod-release: deploy prod-update-jobs prod-migrate ## FULL RELEASE: Deploy -> Update Jobs -> Migrate
	@echo "✅ Production release complete!"

# ==============================================================================
# CODE QUALITY & TESTING
# ==============================================================================

fmt: ## Format code (black, ruff)
	$(DC) run --rm $(APP) black app tests
	$(DC) run --rm $(APP) ruff check --fix app tests

lint: ## Lint code (ruff)
	$(DC) run --rm $(APP) ruff check app tests

lint-commits: ## Check that services use flush(), not commit()
	@violations=$$(grep -rn --include="*.py" "\.commit()" app/services/ | grep -v "# commit-ok"); \
	if [ -n "$$violations" ]; then \
		echo "ERROR: Unauthorized db.commit() in services:"; \
		echo "$$violations"; \
		echo "Services should use db.flush(). Add '# commit-ok: <reason>' if intentional."; \
		exit 1; \
	fi
	@echo "No unauthorized db.commit() in services"

test: ## Run all tests
	$(DC) run --rm $(APP) pytest tests/ -v

audit: ## Run pip-audit for known vulnerabilities
	$(DC) run --rm $(APP) pip-audit

check: lint lint-commits audit test ## Run all quality checks
	@echo "All checks passed."
