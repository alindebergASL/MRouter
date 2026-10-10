# Purser: the same commands locally and in CI (build plan §3).

# In a Claude Code cloud session, containers need the egress CA bundle
# (deploy/compose/cloud.override.yaml). Elsewhere the file doesn't exist.
EGRESS_CA := $(wildcard /root/.ccr/ca-bundle.crt)
COMPOSE := docker compose -f deploy/compose/dev.yaml $(if $(EGRESS_CA),-f deploy/compose/cloud.override.yaml)
SHELL_SCRIPTS := $(wildcard scripts/*.sh .claude/hooks/*.sh)

.DEFAULT_GOAL := help
.PHONY: help dev-up dev-down dev-reset dev-ps dev-logs dev-psql dev-check dev-token \
        check-pins check-hooks lint test images \
        cp-sync cp-lock cp-lint cp-fmt cp-test-unit cp-test cp-migrate

help: ## List targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-12s %s\n", $$1, $$2}'

dev-up: ## Start the dev stack and wait until every service is healthy
	$(COMPOSE) up -d --wait --wait-timeout 300

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

dev-check: ## Smoke-check the dev stack's Keycloak (issuer, token claims, admin events)
	scripts/check-dev-keycloak.sh

dev-token: ## Print a dev-realm access token: make dev-token WHO=alice|bob
	@scripts/dev-token.sh "$${WHO:-bob}"

# Control plane (controlplane/CLAUDE.md). Every step runs in the pinned Python
# 3.13 tool container. cp-tools shares Keycloak's network namespace and needs
# the stack (make dev-up); cp-tools-solo needs nothing running.
CP_TOOLS := $(COMPOSE) run --rm -T cp-tools /src/controlplane/scripts/run.sh
CP_SOLO := $(COMPOSE) run --rm -T --no-deps cp-tools-solo /src/controlplane/scripts/run.sh
HOST_IDS := $(shell id -u):$(shell id -g)

cp-sync: ## Control plane: install the locked dependencies into the tool container's venv
	$(CP_SOLO) sync

cp-lock: ## Control plane: re-lock (uv.lock) and export requirements.lock with hashes
	$(CP_SOLO) lock
	$(COMPOSE) run --rm -T --no-deps --entrypoint chown cp-tools-solo $(HOST_IDS) uv.lock requirements.lock

cp-lint: ## Control plane: ruff check, ruff format --check, mypy --strict
	$(CP_SOLO) lint

cp-fmt: ## Control plane: format and autofix (FILES=path ... to limit)
	$(CP_SOLO) fmt $(FILES)

cp-test-unit: ## Control plane: unit tests (no stack needed)
	$(CP_SOLO) test-unit

cp-test: ## Control plane: every test, against the dev stack (make dev-up first)
	$(CP_TOOLS) test

cp-migrate: ## Control plane: migrate the dev database to head (as the owner role)
	$(CP_TOOLS) migrate

check-pins: ## Every Compose image is pinned by digest and pre-pulled by cloud-setup.sh
	scripts/check-image-pins.sh

check-hooks: ## Self-test the protected-path hook against every protected path
	scripts/check-protect-spec.sh

lint: ## shellcheck the scripts and hooks; actionlint the workflows
	@command -v shellcheck >/dev/null || { echo "shellcheck not found (apt install shellcheck / brew install shellcheck)"; exit 1; }
	shellcheck $(SHELL_SCRIPTS)
	scripts/actionlint.sh

test: ## Placeholder until components have tests
	@echo "No tests yet."

images: ## Placeholder until components have Dockerfiles
	@echo "No images yet."
