DC = docker compose
APP_SERVICE = app

.PHONY: up down logs migrate test audit

up:
	$(DC) up -d

down:
	$(DC) down

logs:
	$(DC) logs -f $(APP_SERVICE)

migrate:
	$(DC) run --rm $(APP_SERVICE) alembic upgrade head

test:
	$(DC) run --rm $(APP_SERVICE) pytest

audit:
	$(DC) run --rm $(APP_SERVICE) pip-audit
