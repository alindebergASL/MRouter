# Purser: the same commands locally and in CI (build plan §3).

COMPOSE := docker compose -f deploy/compose/dev.yaml
SHELL_SCRIPTS := $(wildcard scripts/*.sh .claude/hooks/*.sh)

.DEFAULT_GOAL := help
.PHONY: help dev-up dev-down dev-reset dev-ps dev-logs dev-psql \
        check-pins check-hooks check-contracts lint test images

help: ## List targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-16s %s\n", $$1, $$2}'

dev-up: ## Start the dev stack and wait until every service is healthy
	$(COMPOSE) up -d --wait --wait-timeout 180

dev-down: ## Stop the dev stack (keeps data volumes)
	$(COMPOSE) down

dev-reset: ## Stop the dev stack and delete its data volumes
	$(COMPOSE) down -v

dev-ps: ## Show dev stack services and health
	$(COMPOSE) ps

dev-logs: ## Follow dev stack logs
	$(COMPOSE) logs -f

dev-psql: ## Open psql in the dev Postgres
	$(COMPOSE) exec postgres psql -U purser_dev -d purser_dev

check-pins: ## Every Compose image is pinned by digest and pre-pulled by cloud-setup.sh
	scripts/check-image-pins.sh

check-hooks: ## Self-test the protected-path hook against every protected path
	scripts/check-protect-spec.sh

check-contracts: ## Validate contracts/: schemas, examples, and pricing fixtures
	scripts/check-contracts.sh

lint: ## shellcheck the scripts and hooks; actionlint the workflows
	@command -v shellcheck >/dev/null || { echo "shellcheck not found (apt install shellcheck / brew install shellcheck)"; exit 1; }
	shellcheck $(SHELL_SCRIPTS)
	scripts/actionlint.sh

test: ## Placeholder until components have tests
	@echo "No tests yet."

images: ## Placeholder until components have Dockerfiles
	@echo "No images yet."
