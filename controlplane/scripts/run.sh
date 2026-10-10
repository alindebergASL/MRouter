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
  openapi) sync; run python -m purser_controlplane export-openapi --output openapi/admin-api.json ;;
  openapi-check)
    sync
    fresh=$(mktemp)
    run python -m purser_controlplane export-openapi --output "$fresh"
    if ! diff -u openapi/admin-api.json "$fresh"; then
      echo "controlplane/openapi/admin-api.json differs from FastAPI's output: run make cp-openapi" >&2
      exit 1
    fi
    echo "ok: controlplane/openapi/admin-api.json matches FastAPI's output"
    ;;
  alembic) sync; shift; run alembic "$@" ;;
  python) sync; shift; run python "$@" ;;
  *)
    echo "usage: $0 sync|lock|lint|fmt [files]|test-unit|test|migrate|openapi|openapi-check|alembic ...|python ..." >&2
    exit 2
    ;;
esac
