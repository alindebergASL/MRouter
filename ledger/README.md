# ledger/

Postgres migrations and the reservation ledger's SQL functions: reserve, settle, release, and
mark-unknown, with the transaction rules from architecture §7.5–7.6. Any relay language calls
these functions, so the ledger doesn't wait for the bake-off.

Owner: Lane B. Guarantees: B1, B2, B5, P1, M1.
Runs against the Compose dev stack's Postgres (`make dev-up`). Nothing here yet.
