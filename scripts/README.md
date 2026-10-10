# scripts/

Repository tooling. Each script is runnable from any directory.

| Script | Purpose |
|---|---|
| `cloud-setup.sh` | Setup script for the Claude Code cloud environment: starts the Docker daemon, pre-pulls pinned images. Self-contained; its text is pasted into the environment's Setup script field. |
| `check-image-pins.sh` | Fails unless every Compose image and every Dockerfile base image is pinned by digest and pre-pulled by `cloud-setup.sh`. |
| `check-image-d2.sh` | D2 checks on a built image: non-root, no shell or package manager, healthy with a read-only root filesystem, no canary or egress CA in its layers (`make cp-image-check`). |
| `check-protect-spec.sh` | Self-test for `.claude/hooks/protect-spec.sh` over every protected path. |
| `check-dev-keycloak.sh` | Smoke-checks the dev stack's Keycloak: issuer, token claims, admin-event retention, admin-API client scope (`make dev-check`). |
| `dev-token.sh` | Prints a dev-realm access token for alice or bob (`make dev-token WHO=…`). |
| `retry.sh` | Retries a command with backoff; `make dev-up` and `make cp-image` pull images through it, since public registries rate-limit anonymous pulls. |
| `actionlint.sh` | Runs actionlint on the workflows from a pinned, checksum-verified GitHub release (cached in `.cache/`). |
