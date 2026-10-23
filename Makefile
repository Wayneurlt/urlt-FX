# Single documented entry point. On Windows use Git Bash (make) or run the commands shown.
PY ?= .venv/Scripts/python
ifeq ($(OS),)
PY = .venv/bin/python
endif

.PHONY: setup migrate admin api worker web dev test test-backend test-frontend test-integration lint typecheck format quality paper docker-up

setup:            ## create venv, install backend + frontend deps
	python -m venv .venv
	$(PY) -m pip install -e ".[dev]"
	cd apps/web && npm ci

migrate:          ## apply database migrations
	$(PY) -m alembic upgrade head

admin:            ## create an administrator (prompts for password)
	$(PY) scripts/create_admin.py admin

api:              ## run the API on :8000
	cd services/api && ../../$(PY) -m uvicorn app.main:app --reload --port 8000

worker:           ## run the worker (trading ticks + jobs)
	cd services/api && ../../$(PY) -m app.workers.runner

web:              ## run the dashboard on :3000
	cd apps/web && npm run dev

paper:            ## start an automated paper session: make paper BOT=news-volatility
	$(PY) scripts/paper_session.py $(BOT)

test-backend:
	$(PY) -m pytest trading/tests services/api/tests

test-integration:
	$(PY) -m pytest tests/integration

test-frontend:
	cd apps/web && npm test

test: test-backend test-integration test-frontend

lint:
	$(PY) -m ruff check .
	cd apps/web && npm run lint

format:
	$(PY) -m ruff format .

typecheck:
	$(PY) -m mypy
	cd apps/web && npm run typecheck

quality: lint typecheck test   ## all gates
	$(PY) -m ruff format --check .
	$(PY) -m alembic check

docker-up:
	docker compose up --build
