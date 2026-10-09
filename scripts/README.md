# scripts/

Repository tooling. Each script is runnable from any directory.

| Script | Purpose |
|---|---|
| `cloud-setup.sh` | Setup script for the Claude Code cloud environment: starts the Docker daemon, pre-pulls pinned images. Self-contained; its text is pasted into the environment's Setup script field. |
| `check-image-pins.sh` | Fails unless every Compose image is pinned by digest and pre-pulled by `cloud-setup.sh`. |
| `check-protect-spec.sh` | Self-test for `.claude/hooks/protect-spec.sh` over every protected path. |
| `check-contracts.sh` | `make check-contracts`: builds a hash-pinned virtualenv in `.cache/contracts-venv` and runs `contracts/tools/check_contracts.py`. Needs Python 3.11 or later (`PYTHON` overrides). |
| `actionlint.sh` | Runs actionlint on the workflows from a pinned, checksum-verified GitHub release (cached in `.cache/`). |
