#!/usr/bin/env bash
# Purser: setup script for the Claude Code cloud environment.
#
# Paste this file's full text into the environment's "Setup script" field.
# It runs as root once per environment-cache build: it starts the Docker
# daemon and pre-pulls the dev stack's pinned images, so the environment cache
# keeps them on disk. The repository's SessionStart hook reuses it with
# --daemon-only to restart the daemon in later sessions.
#
# Self-contained: it reads nothing from the repository. Keep IMAGES in step
# with deploy/compose/*.yaml (CI's `make check-pins` fails if they differ).
# Never fails: a non-zero exit would stop the session from starting, so every
# step only warns.

set -u

IMAGES=(
  "public.ecr.aws/docker/library/postgres:18.6-trixie@sha256:74935e72241653ca55e0414067e6d8763aceb8a810eb51b452253ec3dcfc4336"
)

DAEMON_WAIT_SECONDS=60
PULL_TIMEOUT_SECONDS=240
DOCKERD_LOG=/var/log/purser-dockerd.log

start=$(date +%s)
log() { echo "[purser-setup +$(( $(date +%s) - start ))s] $*"; }
warn() { log "WARNING: $*" >&2; }

start_daemon() {
  if ! command -v docker >/dev/null 2>&1; then
    warn "docker CLI not found; skipping"
    return 1
  fi
  if docker info >/dev/null 2>&1; then
    log "Docker daemon already running"
    return 0
  fi
  if ! command -v dockerd >/dev/null 2>&1; then
    warn "dockerd not found; cannot start the Docker daemon"
    return 1
  fi
  if pgrep -x dockerd >/dev/null 2>&1; then
    log "dockerd is running but not answering yet; waiting for it"
  else
    # A pidfile left by an earlier run makes dockerd refuse to start if its PID
    # now belongs to another process. No dockerd is running, so it's stale.
    if [ -e /var/run/docker.pid ]; then
      log "Removing stale /var/run/docker.pid"
      rm -f /var/run/docker.pid
    fi
    log "Starting dockerd (log: $DOCKERD_LOG)"
    setsid nohup dockerd >"$DOCKERD_LOG" 2>&1 </dev/null &
  fi
  for _ in $(seq 1 "$DAEMON_WAIT_SECONDS"); do
    if docker info >/dev/null 2>&1; then
      log "Docker daemon is up"
      return 0
    fi
    sleep 1
  done
  warn "Docker daemon did not start within ${DAEMON_WAIT_SECONDS}s; see $DOCKERD_LOG"
  return 1
}

pull_images() {
  local image
  for image in "${IMAGES[@]}"; do
    log "Pulling $image"
    if timeout "$PULL_TIMEOUT_SECONDS" docker pull --quiet "$image" >/dev/null; then
      log "Pulled $image"
    else
      warn "could not pull $image"
    fi
  done
}

if start_daemon && [ "${1:-}" != "--daemon-only" ]; then
  pull_images
fi
log "Done"
exit 0
