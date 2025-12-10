DC = docker compose
APP = app
DB = db
REDIS = redis

.PHONY: help up down logs logs-api logs-db restart ps build clean migrate migrate-host revision downgrade seed shell db-shell redis-shell fmt lint type test test-cov test-unit test-integration install setup-env health

.DEFAULT_GOAL := help

help: ## Show this help message
	@echo 'Usage: make [target]'
	@echo ''
	@echo 'Available targets:'
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  %-18s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

up: ## Start all services (docker compose up)
	$(DC) up -d
	@echo "Services started. API: http://localhost:8000"
	@echo "Health: http://localhost:8000/api/v1/health"
	@echo "Docs: http://localhost:8000/docs"

down: ## Stop all services
	$(DC) down

logs: ## Tail logs from all services
	$(DC) logs -f

logs-api: ## Tail logs from API service only
	$(DC) logs -f $(APP)

logs-db: ## Tail logs from database service only
	$(DC) logs -f $(DB)

restart: ## Restart all services
	$(DC) restart

ps: ## Show running containers
	$(DC) ps

build: ## Build/rebuild images
	$(DC) build

clean: ## Stop and remove containers, networks, volumes
	$(DC) down -v
	@echo "Cleaned up containers, networks, and volumes"

migrate: ## Run database migrations (upgrade head)
	$(DC) run --rm $(APP) alembic upgrade head

migrate-host: ## Run database migrations from the host (uses current env DATABASE_URL)
	alembic upgrade head

revision: ## Create new migration (usage: make revision m="description")
	@if [ -z "$(m)" ]; then \
		echo "Error: Migration message required. Usage: make revision m=\"your message\""; \
		exit 1; \
	fi
	$(DC) run --rm $(APP) alembic revision --autogenerate -m "$(m)"

downgrade: ## Downgrade database by 1 revision
	$(DC) run --rm $(APP) alembic downgrade -1

seed: ## Seed database with initial data
	$(DC) run --rm $(APP) python -m app.db.init_db

shell: ## Open Python shell in API container
	$(DC) run --rm $(APP) python

db-shell: ## Open psql shell in database
	$(DC) exec $(DB) psql -U $$POSTGRES_USER -d $$POSTGRES_DB

redis-shell: ## Open redis-cli shell
	$(DC) exec $(REDIS) redis-cli

fmt: ## Format code with black and ruff
	$(DC) run --rm $(APP) black app tests
	$(DC) run --rm $(APP) ruff check --fix app tests

lint: ## Lint code with ruff
	$(DC) run --rm $(APP) ruff check app tests

type: ## Type check with mypy
	$(DC) run --rm $(APP) mypy app

test: ## Run tests with pytest
	$(DC) run --rm $(APP) pytest tests/ -v

test-cov: ## Run tests with coverage report
	$(DC) run --rm $(APP) pytest tests/ -v --cov=app --cov-report=html --cov-report=term

test-unit: ## Run unit tests only
	$(DC) run --rm $(APP) pytest tests/unit/ -v

test-integration: ## Run integration tests only
	$(DC) run --rm $(APP) pytest tests/integration/ -v

install: ## Install dependencies inside container
	$(DC) run --rm $(APP) pip install .[dev]

setup-env: ## Run local setup script to create .env and keys
	./setup.sh

health: ## Check service health
	@curl -s http://localhost:8000/api/v1/health | python -m json.tool || echo "API not responding"
