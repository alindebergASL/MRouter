#!/usr/bin/env bash
# Fail unless every image in deploy/compose/*.yaml is pinned by digest and is
# pre-pulled, verbatim, by scripts/cloud-setup.sh.
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
done < <(sed -nE 's/^[[:space:]]*image:[[:space:]]*["'\'']?([^"'\''[:space:]]+).*/\1/p' deploy/compose/*.yaml)

if [ "$count" -eq 0 ]; then
  echo "No images found in deploy/compose/*.yaml"
  exit 1
fi
exit "$status"
