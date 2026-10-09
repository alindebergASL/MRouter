# tests/acceptance/ (protected)

One test module per guarantee ID in `docs/architecture.md` (C1–C4, B1–B6, P1, M1–M5, A1–A4, H1, H2,
D1–D3, R1, R2, W1). Tests are named `test_<ID>_<description>`, for example
`test_B1_strict_budget_never_exceeded`.

Each test is written and approved before the code that satisfies it (build plan §2.1).

**Protected path.** Change it only in a spec-change session (`ROUTER_SPEC_CHANGE=1`) through a pull request labeled
`spec-change`.
