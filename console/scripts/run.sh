#!/bin/sh
# The console's toolchain steps, run inside the console-tools container by
# `make console-*` (see the root Makefile). Usage: scripts/run.sh <step>
set -eu
cd /src/console

install() { npm ci --ignore-scripts --no-audit --no-fund --loglevel=error; }

case "${1:-}" in
  gen) install; npm run --silent gen:client ;;
  typecheck) install; npm run --silent typecheck ;;
  *) echo "usage: $0 gen|typecheck" >&2; exit 2 ;;
esac
