#!/usr/bin/env bash
# PreToolUse guard (build plan §2.2, Appendix B): blocks Edit and Write on
# protected paths unless the session was started with ROUTER_SPEC_CHANGE=1.
# A guardrail, not a wall: CI's spec-guard check is the enforcement.
# Keep the path list in step with .github/workflows/spec-guard.yml.
path=$(jq -r '.tool_input.file_path // empty')
case "$path" in
  */contracts/*|*/tests/acceptance/*|*/docs/architecture.md|\
  */.github/workflows/spec-guard.yml|*/.github/CODEOWNERS|\
  */.claude/hooks/*|*/.claude/settings.json)
    if [ "${ROUTER_SPEC_CHANGE:-0}" != "1" ]; then
      echo "Protected path: $path. Contracts and acceptance tests change only in a spec-change session." >&2
      exit 2
    fi
    ;;
esac
exit 0
