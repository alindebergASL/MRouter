#!/bin/sh
# Entrypoint of the cp-tools containers (deploy/compose/dev.yaml): installs the
# pinned uv from requirements/uv.txt (hash-checked) into the cache volume once
# per version, then runs the given command with uv on PATH.
set -eu

req=/src/controlplane/requirements/uv.txt
stamp=$(sha256sum "$req" | cut -c1-16)
dest=/cache/uv-$stamp
if [ ! -x "$dest/bin/uv" ]; then
  python -m pip install --quiet --root-user-action=ignore --no-deps --require-hashes --target "$dest/lib" -r "$req"
  mkdir -p "$dest/bin"
  ln -sf "$dest/lib/bin/uv" "$dest/bin/uv"
fi
PATH="$dest/bin:/venv/bin:$PATH"
export PATH
exec "$@"
