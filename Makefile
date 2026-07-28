.PHONY: help install dev migrate seed generate db-stop db-reset test lint typecheck check lock clean

UV  := uv
PY  := .venv/bin/python

help:
	@echo "Vietnamese Invoice & Expense Agent"
	@echo ""
	@echo "  make install    - uv venv (3.13) + pip install -e '.[dev]'"
	@echo "  make dev        - Khởi động Postgres + pgvector, chờ healthy, chạy migration"
	@echo "  make migrate    - alembic upgrade head"
	@echo "  make seed       - nạp reference data (phòng ban, danh mục, ngân sách)"
	@echo "  make generate   - sinh synthetic corpus 300 hóa đơn (seed 42)"
	@echo "  make db-stop    - Dừng Postgres (giữ dữ liệu)"
	@echo "  make db-reset   - XOÁ volume, dựng lại từ init_db.sql"
	@echo "  make test       - pytest"
	@echo "  make lint       - ruff"
	@echo "  make typecheck  - mypy"
	@echo "  make check      - lint + typecheck + test (chạy trước khi commit)"
	@echo "  make lock       - Sinh lại requirements.lock từ pyproject.toml"
	@echo "  make clean      - Xoá cache"

install:
	$(UV) venv --python 3.13
	$(UV) pip install -e ".[dev]"

dev:
	docker compose up -d postgres
	@echo "Chờ Postgres healthy..."
	@until [ "$$(docker inspect -f '{{.State.Health.Status}}' invoice-agent-postgres 2>/dev/null)" = "healthy" ]; do \
		sleep 1; \
	done
	@echo "✅ Postgres sẵn sàng ở localhost:5433"
	@docker exec invoice-agent-postgres psql -U invoice -d invoice_agent -tAc \
		"SELECT 'schema: ' || nspname FROM pg_namespace WHERE nspname IN ('app','langgraph') ORDER BY nspname"
	$(MAKE) migrate
	$(MAKE) seed

migrate:
	.venv/bin/alembic upgrade head

seed:
	$(PY) -m scripts.seed_reference

generate:
	$(PY) -m scripts.generate_invoices --count 300 --seed 42 --out corpus


db-stop:
	docker compose stop postgres

db-reset:
	docker compose down -v
	$(MAKE) dev

test:
	$(PY) -m pytest

lint:
	.venv/bin/ruff check src tests migrations

typecheck:
	.venv/bin/mypy

check: lint typecheck test

lock:
	$(UV) pip compile pyproject.toml --extra dev -o requirements.lock

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache
	find . -type d -name __pycache__ -not -path './.venv/*' -exec rm -rf {} +
