.PHONY: up down restart logs build test lint format shell-api shell-mongo health

# ── 환경 기동/종료 ────────────────────────────────────────────────────────
up:
	@bash scripts/setup-local.sh

down:
	docker compose down

down-v:
	docker compose down -v

restart:
	docker compose restart api

# ── 개발 ─────────────────────────────────────────────────────────────────
build:
	docker compose build api

logs:
	docker compose logs -f api

logs-all:
	docker compose logs -f

health:
	@bash scripts/health-check.sh

# ── 테스트 ────────────────────────────────────────────────────────────────
test:
	docker compose exec api pytest tests/ -v --tb=short

test-cov:
	docker compose exec api pytest tests/ --cov=app --cov-report=term-missing

test-local:
	cd backend && python -m pytest tests/ -v --tb=short

# ── 코드 품질 ─────────────────────────────────────────────────────────────
lint:
	cd backend && python -m black --check app/ tests/
	cd backend && python -m isort --check-only app/ tests/
	cd backend && python -m ruff check app/ tests/

format:
	cd backend && python -m black app/ tests/
	cd backend && python -m isort app/ tests/

# ── 셸 접속 ──────────────────────────────────────────────────────────────
shell-api:
	docker compose exec api bash

shell-mongo:
	@bash scripts/mongo-shell.sh

# ── MongoDB 스키마 재초기화 ───────────────────────────────────────────────
mongo-init:
	docker compose up mongo-init
