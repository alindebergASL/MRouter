#!/usr/bin/env bash
# Run actionlint on .github/workflows, downloading a pinned release on first use.
# The binary comes from GitHub releases, verified by SHA-256, and is cached in
# .cache/ (gitignored). This avoids Docker Hub, which rate-limits anonymous pulls.
set -euo pipefail

VERSION=1.7.12
cd "$(dirname "$0")/.."

os=$(uname -s | tr '[:upper:]' '[:lower:]')
arch=$(uname -m)
case "$arch" in
  x86_64 | amd64) arch=amd64 ;;
  aarch64 | arm64) arch=arm64 ;;
  *) echo "actionlint.sh: unsupported architecture $arch" >&2; exit 1 ;;
esac

case "${os}_${arch}" in
  linux_amd64)  sha=8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8 ;;
  linux_arm64)  sha=325e971b6ba9bfa504672e29be93c24981eeb1c07576d730e9f7c8805afff0c6 ;;
  darwin_amd64) sha=5b44c3bc2255115c9b69e30efc0fecdf498fdb63c5d58e17084fd5f16324c644 ;;
  darwin_arm64) sha=aba9ced2dee8d27fecca3dc7feb1a7f9a52caefa1eb46f3271ea66b6e0e6953f ;;
  *) echo "actionlint.sh: unsupported platform ${os}_${arch}" >&2; exit 1 ;;
esac

dir=".cache/actionlint-${VERSION}-${os}_${arch}"
bin="$dir/actionlint"
if [ ! -x "$bin" ]; then
  mkdir -p "$dir"
  tarball="$dir/actionlint.tar.gz"
  curl -fsSL -o "$tarball" \
    "https://github.com/rhysd/actionlint/releases/download/v${VERSION}/actionlint_${VERSION}_${os}_${arch}.tar.gz"
  if command -v sha256sum >/dev/null 2>&1; then
    actual=$(sha256sum "$tarball" | cut -d' ' -f1)
  else
    actual=$(shasum -a 256 "$tarball" | cut -d' ' -f1)
  fi
  if [ "$actual" != "$sha" ]; then
    echo "actionlint.sh: checksum mismatch for $tarball (got $actual, want $sha)" >&2
    rm -f "$tarball"
    exit 1
  fi
  tar -xzf "$tarball" -C "$dir" actionlint
  rm -f "$tarball"
fi

exec "$bin" "$@"
