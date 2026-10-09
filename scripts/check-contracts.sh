#!/usr/bin/env bash
# Validate contracts/: every schema, example, and fixture (make check-contracts).
# Builds a private virtualenv in .cache/contracts-venv (gitignored) from the
# hash-pinned contracts/tools/requirements.txt, rebuilt when that file changes,
# then runs contracts/tools/check_contracts.py in isolated mode.
# Set PYTHON to choose the interpreter (Python 3.11 or later).
set -euo pipefail

cd "$(dirname "$0")/.."
python=${PYTHON:-python3}
req=contracts/tools/requirements.txt
venv=.cache/contracts-venv

if ! command -v "$python" >/dev/null 2>&1; then
  echo "check-contracts: $python not found. Install Python 3.11 or later, or set PYTHON." >&2
  exit 1
fi
if ! "$python" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
  echo "check-contracts: $python is $("$python" -V 2>&1); Python 3.11 or later is required" >&2
  echo "  (macOS: brew install python@3.12, then PYTHON=python3.12 make check-contracts)." >&2
  exit 1
fi

if command -v sha256sum >/dev/null 2>&1; then
  want=$(sha256sum "$req" | cut -d' ' -f1)
else
  want=$(shasum -a 256 "$req" | cut -d' ' -f1)
fi
want="$want $("$python" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
stamp="$venv/.requirements-sha256"

if [ ! -x "$venv/bin/python" ] || [ "$(cat "$stamp" 2>/dev/null)" != "$want" ]; then
  echo "check-contracts: building $venv"
  rm -rf "$venv"
  "$python" -m venv "$venv"
  "$venv/bin/python" -m pip install --quiet --disable-pip-version-check \
    --require-hashes --only-binary=:all: --no-cache-dir -r "$req"
  echo "$want" >"$stamp"
fi

exec "$venv/bin/python" -I contracts/tools/check_contracts.py "$@"
