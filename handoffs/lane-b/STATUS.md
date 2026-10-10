# Lane B: Money — STATUS

Updated: 2026-10-09 (initial)
Scope: Ledger SQL, attempt state machine, price-book schema and Python oracle, accounting-adapter fixtures (build plan §5).

## Done
- Nothing yet.

## Next
1. Ledger schema and SQL functions: reserve, settle, release, mark-unknown (build plan §7, Lane B task 1).

## Inputs from other lanes
- Lane A, contracts v0 (in review): `contracts/schemas/attempt.schema.json` (the 7.5 state machine and
  transition table as data), `price-book.schema.json` and `price-fixture.schema.json`, and `contracts/fixtures/pricing/`
  with the Opus 5.5 book and the $0.209 fixture. Lane B task 3 builds the oracle and the full fixture set on these;
  schema changes go through a spec-change pull request.

## Blocked
- Nothing.

## Guarantee tests passing

| ID | Test | Status |
|---|---|---|
| — | No guarantee tests yet. | — |
