#!/usr/bin/env bash
# Run a command, retrying with backoff: public registries rate-limit anonymous
# image pulls ("toomanyrequests"), and CI runners share IP addresses.
# Usage: scripts/retry.sh <attempts> <command> [args...]
set -uo pipefail

attempts=${1:?usage: $0 <attempts> <command> [args...]}
shift
delay=${RETRY_DELAY:-10}
for attempt in $(seq 1 "$attempts"); do
  "$@"
  status=$?
  [ "$status" -eq 0 ] && exit 0
  if [ "$attempt" -lt "$attempts" ]; then
    echo "retry.sh: attempt $attempt of $attempts failed (exit $status); retrying in ${delay}s" >&2
    sleep "$delay"
    delay=$((delay * 2))
  fi
done
echo "retry.sh: giving up after $attempts attempts" >&2
exit "$status"
