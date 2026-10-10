#!/usr/bin/env bash
# Fail unless every image in deploy/compose/*.yaml, and every base image in
# deploy/images/*/Dockerfile, is pinned by digest and is pre-pulled, verbatim,
# by scripts/cloud-setup.sh.
# Usage: scripts/check-image-pins.sh [setup-script]
set -euo pipefail

cd "$(dirname "$0")/.."
setup=${1:-scripts/cloud-setup.sh}
status=0
count=0

while IFS= read -r image; do
  count=$((count + 1))
  if [[ "$image" != *@sha256:* ]]; then
    echo "NOT PINNED: $image (use repo:tag@sha256:<digest>)"
    status=1
  elif ! grep -qF "\"$image\"" "$setup"; then
    echo "NOT PRE-PULLED: $image is missing from IMAGES in $setup"
    status=1
  else
    echo "ok: $image"
  fi
done < <(
  sed -nE 's/^[[:space:]]*image:[[:space:]]*["'\'']?([^"'\''[:space:]]+).*/\1/p' deploy/compose/*.yaml
  # FROM lines, skipping references to earlier stages (FROM <name> after
  # "AS <name>") and scratch. Everything else is a base image.
  shopt -s nullglob
  for dockerfile in deploy/images/*/Dockerfile; do
    stages=$(sed -nE 's/^FROM[[:space:]].*[[:space:]][Aa][Ss][[:space:]]+([^[:space:]]+).*/\1/p' "$dockerfile")
    sed -nE 's/^FROM[[:space:]]+(--platform=[^[:space:]]+[[:space:]]+)?([^[:space:]]+).*/\2/p' "$dockerfile" |
      while IFS= read -r base; do
        if [ "$base" != scratch ] && ! grep -qxF "$base" <<<"$stages"; then
          echo "$base"
        fi
      done
  done
)

if [ "$count" -eq 0 ]; then
  echo "No images found in deploy/compose/*.yaml"
  exit 1
fi
exit "$status"
