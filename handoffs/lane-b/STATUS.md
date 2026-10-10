# Lane B: Money — STATUS

Updated: 2026-10-10 by the spec draft 0.6 session (spec-change)
Scope: Ledger SQL, attempt state machine, price-book schema and Python oracle, accounting-adapter fixtures (build plan §5).

## Done
- Nothing yet.

## Next
1. Ledger schema and SQL functions: reserve, settle, release, mark-unknown (build plan §7, Lane B task 1).
   Per spec draft 0.6: every function takes the org as an explicit argument from the
   authenticated identity; reserve reads the org's budget policy row first and denies with its
   own error code when there is none, never admitting on zero rows (spec 7.6). B5's test now
   includes that case. The ledger tables have no row-level security in v1 (spec 4).

## Blocked
- Nothing.

## Guarantee tests passing

| ID | Test | Status |
|---|---|---|
| — | No guarantee tests yet. | — |
