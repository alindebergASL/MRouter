# Purser

A model router that sits between AI coding agents and model providers, enforcing
budgets and metering every request. Spec: docs/architecture.md. Guarantee IDs
(C1, B1, H1, ...) refer to it.

## Invariants (never break these)
- No provider credential ever reaches a sidecar or an end-user device.
- No prompt or response content is persisted, logged, or put in metrics labels.
- Every upstream attempt, including retries, has a committed reservation first.
- Money is exact decimal, rounded once per attempt to integer nano-dollars;
  ceilings round up.
- Unpriced means unbounded. Unavailable is not zero.
- Pass-through traffic is forwarded byte for byte.
- Images carry no credentials, run as non-root on a read-only root filesystem,
  and contain the same binary as the native release.
- Every install generates its own secrets and keys; no default credentials
  outside dev.
- Every request-path query on org data filters by org ID, and row-level security
  backs it up.

## Workflow
1. Plan mode first; reference the guarantee IDs the change affects.
2. Acceptance tests live in tests/acceptance/ and are named test_<ID>_*.
   They are protected: never edit them or contracts/ in a normal session.
3. Implement until the tests pass; run the guarantee-reviewer subagent.
4. Update handoffs/<lane>/STATUS.md and open a small pull request.

## Commands
(Each component's CLAUDE.md lists its build, test, and lint commands.)

Root: `make help` lists them. `make dev-up` / `make dev-down` start and stop the
Compose dev stack (deploy/compose/dev.yaml); `make check-pins`, `make check-hooks`,
and `make lint` are the repository checks CI runs.

## Protected paths
contracts/, tests/acceptance/, docs/architecture.md, .github/workflows/spec-guard.yml,
.github/CODEOWNERS, .claude/hooks/, .claude/settings.json. They change only in a
session started with ROUTER_SPEC_CHANGE=1, through a pull request labeled spec-change.
