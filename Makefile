SHELL := /bin/bash
.DEFAULT_GOAL := help
UV := uv

.PHONY: help bootstrap test lint format run image up contract

help:        ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

bootstrap:   ## Resolve and install the environment
	$(UV) sync

test:        ## pytest — OAuth2 flow, opaque-cursor pagination, dialect, dataset
	$(UV) run pytest tests/ -W ignore::DeprecationWarning

lint:        ## ruff + strict mypy
	$(UV) run ruff check .
	$(UV) run ruff format --check .
	$(UV) run mypy src/entra_mock

format:      ## Format the code
	$(UV) run ruff format .
	$(UV) run ruff check --fix .

run:         ## Run the mock locally
	$(UV) run python -m entra_mock

image:       ## Build the container image
	docker build -t entra-mock:dev .

up:          ## Run the mock in a container (docker compose up --build)
	docker compose up --build

contract:    ## Regenerate contracts/msgraph.openapi.yaml from the app
	$(UV) run python -c "import yaml, entra_mock as m; \
open('contracts/msgraph.openapi.yaml','w').write(yaml.safe_dump(m.app.openapi(), sort_keys=False, allow_unicode=True))"
	@echo "✓ contract regenerated — REVIEW the diff: a changed response shape is a contract change for consumers"
