#!/usr/bin/env bash
# SessionStart hook: in Claude Code cloud sessions, start the Docker daemon (the
# environment cache keeps pulled images but not a running daemon) and then the
# Compose dev stack, in the background. Locally it does nothing; run
# `make dev-up` yourself. It never fails the session: every path exits 0.
set -u

[ "${CLAUDE_CODE_REMOTE:-}" = "true" ] || exit 0

project=${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}

if ! command -v docker >/dev/null 2>&1; then
  echo "WARNING: docker is not installed; the Purser dev stack was not started."
  exit 0
fi

mkdir -p "$project/.claude/logs" 2>/dev/null
log="$project/.claude/logs/dev-up.log"

# Detach completely (new session, no inherited stdio) so Claude Code doesn't
# wait for the background job. The inner script is single-quoted on purpose:
# it expands its own variables when it runs.
# shellcheck disable=SC2016
setsid nohup bash -c '
  project=$1
  echo "[$(date -u +%FT%TZ)] starting Docker daemon if needed"
  bash "$project/scripts/cloud-setup.sh" --daemon-only
  if ! docker info >/dev/null 2>&1; then
    echo "WARNING: Docker daemon unavailable; the dev stack was not started."
    exit 0
  fi
  echo "[$(date -u +%FT%TZ)] make dev-up"
  if make -C "$project" dev-up; then
    echo "[$(date -u +%FT%TZ)] dev stack healthy"
  else
    echo "WARNING: make dev-up failed (exit $?)."
  fi
' bash "$project" </dev/null >"$log" 2>&1 &

echo "Purser dev stack starting in the background (Postgres on 127.0.0.1:55432); log: .claude/logs/dev-up.log"
exit 0
