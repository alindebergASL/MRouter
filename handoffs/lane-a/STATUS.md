# Lane A: Spec and proof — STATUS

Updated: 2026-10-10 by the Lane A Phase 0 task 2 session (contracts v0)
Scope: contracts/, acceptance tests, the certification lab, the Compose dev stack, CI (build plan §5).

## Done
- **Task 1 (merged):** repo skeleton, `CLAUDE.md` files, hooks, subagents, CODEOWNERS, spec-guard, CI,
  the Compose dev stack (Postgres 18.6), `scripts/cloud-setup.sh`, handoff files, ADRs 0001 and 0002.
- **Task 2 (in review, branch `claude/youthful-shannon-0piszb`, label `spec-change`):** `contracts/` v0.
  - Schemas, all 0.1.0, draft 2020-12: `common`, `usage-event`, `attempt`, `error-envelope`,
    `price-book`, `price-fixture`, `decision-record`, `trace-event`. The README table lists the
    guarantees that reference each one.
  - Valid and invalid examples for every schema. Each invalid example names the one rule it breaks.
  - `fixtures/pricing/`: the audited Opus 5.5 price book and golden fixtures, including the §6.3
    example at $0.209 = 209,000,000 nd (M5).
  - `contracts/tools/check_contracts.py` with hash-pinned dependencies, run by `make check-contracts`
    and by CI's `checks` job. It checks:
    - the metaschema and keyword vocabulary;
    - the README table;
    - every example against its expectation;
    - cross-field rules;
    - no floats, NaN, or duplicate keys;
    - the no-free-text lint for C2 and C4, with a self-test;
    - denial bodies against OpenCode's retry triggers;
    - fixture arithmetic.
  - ADR 0004 (contract conventions), proposed.

## Next
1. Resolve the open questions listed in the task 2 PR. They decide:
   - two attempt transitions;
   - the evidence tier of ceiling charges;
   - four error-mapping details;
   - whether harness session IDs are hashed.

   Each answer is a small spec-change follow-up.
2. After task 1's spec-guard is a required check: confirm on the task 2 PR that spec-guard fails
   without `spec-change` and passes with it.
3. Task 3: the recorder. Before the first recording, check which egress path attaches the
   environment's API credentials (see "Not yet verified" in `deploy/compose/README.md`).
4. Acceptance tests that use the contracts:
   - M5 against `fixtures/pricing/`, once Lane B's oracle exists;
   - C4's schema check, which is the no-free-text lint, wrapped as `test_C4_*`;
   - B3 and B4 denial-shape tests against `error-envelope`.

## Blocked
- Nothing in this lane. Andrew to do: add the `spec-change` label to the task 2 PR; decide the open
  questions; enable secret scanning and push protection; protect `main` with required checks
  `spec-guard`, `dev-stack`, and `checks`.

## Guarantee tests passing

| ID | Test | Status |
|---|---|---|
| — | No guarantee tests yet. `contracts/` v0 gives M1, M2, M4, M5, P1, B1–B6, C2–C4, R1, R2, and H1 the shapes their tests will use. | — |

Infrastructure checks passing locally: `make check-contracts`, `make check-pins`, `make check-hooks`
(20 cases), and `make lint`.

## Notes for the next session
- The Python on CI's runner (ubuntu-24.04) is 3.12, and `contracts/tools/requirements.txt` pins
  `typing-extensions` for it. macOS's `/usr/bin/python3` (3.9) is too old: run
  `PYTHON=python3.12 make check-contracts`.
- Subagents inherit the session's permission mode. In a session that entered plan mode, subagents
  cannot write files even after the plan is approved in chat, so use them only for read-only review.
