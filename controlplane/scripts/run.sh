#!/bin/sh
# The control plane's toolchain steps, run inside the cp-tools containers by
# `make cp-*` (see the root Makefile). Usage: scripts/run.sh <step> [args]
set -eu
cd /src/controlplane

sync() { uv sync --locked --quiet; }
run() { uv run --no-sync "$@"; }

case "${1:-}" in
  sync) sync ;;
  lock)
    uv lock
    uv export --locked --no-dev --no-emit-project --format requirements-txt \
      --output-file requirements.lock --quiet
    ;;
  lint)
    sync
    run ruff check .
    run ruff format --check .
    run mypy
    ;;
  fmt)
    sync
    shift
    if [ "$#" -gt 0 ]; then
      run ruff format "$@"
      run ruff check --fix --quiet "$@" || true
    else
      run ruff format .
      run ruff check --fix .
    fi
    ;;
  test-unit) sync; shift; run pytest -m "not stack" "$@" ;;
  test) sync; shift; run pytest "$@" ;;
  migrate) sync; run python -m purser_controlplane migrate ;;
  alembic) sync; shift; run alembic "$@" ;;
  python) sync; shift; run python "$@" ;;
  *)
    echo "usage: $0 sync|lock|lint|fmt [files]|test-unit|test|migrate|alembic ...|python ..." >&2
    exit 2
    ;;
esac
