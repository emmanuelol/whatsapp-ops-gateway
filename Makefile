.PHONY: help build test check-blacklist audit clean lint format typecheck lock

help:
	@echo "Available commands:"
	@echo "  make build           - Build local Docker container image"
	@echo "  make test            - Run complete test suite in isolated container"
	@echo "  make check-blacklist - Verify zero secret or internal IP leaks in codebase"
	@echo "  make audit           - Run image layer and Trivy vulnerability audit"
	@echo "  make clean           - Tear down containers, networks, and orphaned volumes"
	@echo "  make lint            - Run ruff linter"
	@echo "  make format          - Run ruff code formatter"
	@echo "  make typecheck       - Run strict mypy static type analysis"
	@echo "  make lock            - Regenerate deterministic uv.lock"

build:
	docker build -t whatsapp-ops-gateway:latest .

test:
	docker compose run --rm test

check-blacklist:
	bash ./scripts/check_blacklist.sh .

audit: check-blacklist
	bash ./scripts/audit_image.sh

clean:
	docker compose down --rmi all --volumes --remove-orphans

lint:
	uv run ruff check src/ tests/

format:
	uv run ruff format src/ tests/

typecheck:
	uv run mypy src/

lock:
	uv lock
