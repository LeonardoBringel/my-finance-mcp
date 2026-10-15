.PHONY: install run test lint

install:
	uv sync

# SPEC_DEVIATION: no --reload.
# Reason: the reloader keeps running after the app exits on invalid
# configuration, so the target would never fail fast with code 1.
run:
	uv run uvicorn my_finance_mcp.main:create_app --factory

test:
	uv run pytest

lint:
	uv run black --check .
	uv run isort --check-only .
