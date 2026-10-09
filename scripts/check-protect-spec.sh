#!/usr/bin/env bash
# Self-test for .claude/hooks/protect-spec.sh. For every protected path: an
# edit is blocked (exit 2) without ROUTER_SPEC_CHANGE and allowed (exit 0)
# with ROUTER_SPEC_CHANGE=1. Nearby unprotected paths are always allowed.
# Paths are absolute, as Claude Code sends them.
set -uo pipefail

cd "$(dirname "$0")/.." || exit 1
hook=.claude/hooks/protect-spec.sh
root=/workspace/MRouter
failures=0

protected=(
  contracts/x.json
  tests/acceptance/test_B1_example.py
  docs/architecture.md
  .github/workflows/spec-guard.yml
  .github/CODEOWNERS
  .claude/hooks/session-start.sh
  .claude/settings.json
)
allowed=(
  ledger/README.md
  docs/build-plan.md
  docs/adr/0003-example.md
  .github/workflows/ci.yml
  .claude/agents/guarantee-reviewer.md
  .claude/settings.local.json
)

run() { # tool path spec_change expected
  local tool=$1 path=$2 spec=$3 want=$4 got json
  json=$(jq -cn --arg t "$tool" --arg p "$root/$path" \
    '{hook_event_name:"PreToolUse", tool_name:$t, tool_input:{file_path:$p}}')
  if [ "$spec" = 1 ]; then
    ROUTER_SPEC_CHANGE=1 "$hook" <<<"$json" 2>/dev/null; got=$?
  else
    env -u ROUTER_SPEC_CHANGE "$hook" <<<"$json" 2>/dev/null; got=$?
  fi
  local verdict=ok
  if [ "$got" != "$want" ]; then verdict=FAIL; failures=$((failures + 1)); fi
  printf '%-4s %-5s %-40s ROUTER_SPEC_CHANGE=%-5s exit %s (want %s)\n' \
    "$verdict" "$tool" "$path" "$([ "$spec" = 1 ] && echo 1 || echo unset)" "$got" "$want"
}

for p in "${protected[@]}"; do
  run Write "$p" 0 2
  run Edit "$p" 1 0
done
for p in "${allowed[@]}"; do
  run Edit "$p" 0 0
done

if [ "$failures" -gt 0 ]; then
  echo "$failures case(s) failed"
  exit 1
fi
echo "All cases passed."
