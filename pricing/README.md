# pricing/

Python reference implementation of the price book (the test oracle) and fixture tooling. Exact
decimal arithmetic, rounded once per attempt to integer nano-dollars (architecture §6.3–6.4).
The data plane must reproduce every golden fixture exactly.

Owner: Lane B. Guarantees: M2, M5, B6. Nothing here yet.
