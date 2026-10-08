#!/usr/bin/env bash
# Fetch read-only reference checkouts at pinned commits into .reference/ (gitignored).
# See docs/prior-art.md for how to use them. Changing a pin is a deliberate pull request.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
dest="${root}/.reference"
mkdir -p "${dest}"

fetch() {
  local name="$1" url="$2" sha="$3"
  local dir="${dest}/${name}"
  if [ -d "${dir}/.git" ] && [ "$(git -C "${dir}" rev-parse HEAD 2>/dev/null)" = "${sha}" ]; then
    echo "${name}: already at ${sha}"
    return 0
  fi
  rm -rf "${dir}"
  git init -q "${dir}"
  git -C "${dir}" remote add origin "${url}"
  git -C "${dir}" fetch -q --depth 1 origin "${sha}"
  git -C "${dir}" -c advice.detachedHead=false checkout -q FETCH_HEAD
  echo "${name}: ${sha}"
}

fetch switchyard  https://github.com/NVIDIA-NeMo/Switchyard.git 1a0d1191a377e3b97e285219e9a6f15efa2cb675
fetch token-meter https://github.com/splunk/token-meter.git     f34c78be66e257d168c51b955f9b90c3cacc71b7
