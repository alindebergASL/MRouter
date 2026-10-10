# Purser: the same commands locally and in CI (build plan §3).

# In a Claude Code cloud session, containers need the egress CA bundle
# (deploy/compose/cloud.override.yaml). Elsewhere the file doesn't exist.
EGRESS_CA := $(wildcard /root/.ccr/ca-bundle.crt)
COMPOSE := docker compose -f deploy/compose/dev.yaml $(if $(EGRESS_CA),-f deploy/compose/cloud.override.yaml)
SHELL_SCRIPTS := $(wildcard scripts/*.sh .claude/hooks/*.sh controlplane/scripts/*.sh)

.DEFAULT_GOAL := help
.PHONY: help dev-up dev-down dev-reset dev-ps dev-logs dev-psql dev-check dev-token \
        check-pins check-hooks check-contracts lint test images \
        cp-sync cp-lock cp-lint cp-fmt cp-test-unit cp-test cp-migrate \
        cp-image cp-image-check dev-up-app

help: ## List targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-16s %s\n", $$1, $$2}'

dev-up: ## Start the dev stack and wait until every service is healthy
	scripts/retry.sh 5 $(COMPOSE) pull --quiet
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

CP_IMAGE ?= purser-controlplane:dev
# A variable, because the comma would split $(if ...)'s arguments.
CA_BUILD_SECRET := --secret id=egress-ca,src=$(EGRESS_CA)

# With PURSER_BUILD_CANARY set, the canary is also planted as files in the build
# context, inside the package and next to it, as a secret might be; the D2 check
# then proves none of it reached a layer.
CANARY_FILES := controlplane/src/purser_controlplane/.env.canary controlplane/.env.canary

cp-image: ## Control plane: build the image (deploy/images/controlplane/Dockerfile)
	@for base in $$(sed -nE 's/^FROM[[:space:]]+([^[:space:]]+@sha256:[0-9a-f]+).*/\1/p' deploy/images/controlplane/Dockerfile); do \
	  scripts/retry.sh 5 docker pull --quiet "$$base" >/dev/null; done
	@if [ -n "$${PURSER_BUILD_CANARY:-}" ]; then \
	  for f in $(CANARY_FILES); do printf '%s\n' "$$PURSER_BUILD_CANARY" > "$$f"; done; fi
	docker build -f deploy/images/controlplane/Dockerfile -t $(CP_IMAGE) \
	  $(if $(EGRESS_CA),$(CA_BUILD_SECRET)) . ; status=$$?; rm -f $(CANARY_FILES); exit $$status

cp-image-check: ## Control plane: D2 checks on the built image (needs make dev-up)
	scripts/check-image-d2.sh $(CP_IMAGE) "$${PURSER_BUILD_CANARY:-}"

dev-up-app: ## Start the dev stack plus the control plane (API on 127.0.0.1:58180)
	$(COMPOSE) --profile app up -d --build --wait --wait-timeout 300

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
