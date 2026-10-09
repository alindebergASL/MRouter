# Lane A: Spec and proof — STATUS

Updated: 2026-10-09 by the Lane A Phase 0 task 1 session
Scope: contracts/, acceptance tests, the certification lab, the Compose dev stack, CI (build plan §5).

## Done
- **Task 1 (in review, branch `claude/sharp-heisenberg-f6cqav`):** repo skeleton (build plan §3),
  root and component `CLAUDE.md` files, the protect-spec and SessionStart hooks, three subagents,
  CODEOWNERS, the spec-guard check, CI (`dev-stack`, `checks`), the Compose dev stack (Postgres 18.6,
  pinned by digest, `127.0.0.1:55432`), `scripts/cloud-setup.sh`, the container network finding
  (`deploy/compose/README.md`), handoff files, and ADRs 0001 and 0002.
- Verified in a cloud session: cold setup script 18.9 s (warm 0.9 s); stack healthy in about 4 s;
  `select 1` returns 1; SessionStart hook returns in about 10 ms and brings the stack up from a
  stopped daemon, including after a worker restart that left a stale Docker pidfile.

## Next
1. After task 1 merges: open a throwaway PR touching `contracts/` to confirm spec-guard appears,
   fails without `spec-change`, passes with it, and can be a required check. If it can't, switch it
   to `pull_request` (with Andrew's OK).
2. Task 2: `contracts/` v0 (usage event, attempt, error envelopes, price book, decision record,
   trace event), in a spec-change session started with `ROUTER_SPEC_CHANGE=1`.
3. Task 3: recorder. Before the first recording, check which egress path attaches the environment's
   API credentials (see "Not yet verified" in `deploy/compose/README.md`).

## Blocked
- Nothing in this lane. Andrew to do: paste the setup script into the cloud environment; enable
  secret scanning and push protection; protect `main` with required checks `spec-guard`, `dev-stack`,
  and `checks` (no required code-owner review).

## Guarantee tests passing

| ID | Test | Status |
|---|---|---|
| — | No guarantee tests yet (task 1 adds infrastructure only). | — |

Infrastructure checks passing locally: `make check-pins`, `make check-hooks` (20 cases), `make lint`.
