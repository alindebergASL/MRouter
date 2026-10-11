# controlplane

Owner: Lane D. Guarantees: A1, A4 (API side), A5 (org isolation, with forced row-level security).
FastAPI on Keycloak (ADR 0003). Python 3.13, the runtime image's version.
Read the root `CLAUDE.md` first. Guarantee IDs refer to `docs/architecture.md`.

## Commands
Every command runs from the repository root in the pinned Python 3.13 tool container, so a Mac,
a cloud session, and CI use the same interpreter. Integration tests need `make dev-up` first.

- Build: `make cp-image` (the image), `make cp-sync` (install the locked dependencies).
- Test: `make cp-test` (unit and integration, against the dev stack); `make cp-test-unit` (no stack).
- Lint: `make cp-lint` (ruff check, ruff format --check, mypy --strict).
- Format: `make cp-fmt` (all files) or `make cp-fmt FILES="src/x.py tests/y.py"`.
- Image checks: `make cp-image-check` (D2, needs the stack).
- Run it: `make dev-up-app` (API on 127.0.0.1:58180), `make dev-token WHO=bob` for a token.
- Lock: `make cp-lock` after editing `pyproject.toml` (move the `exclude-newer` cooldown date
  deliberately).
- Migrations: `make cp-migrate`. New migration: `controlplane/scripts/run.sh alembic revision
  --autogenerate` inside cp-tools; then add forced row-level security and an `org_isolation`
  policy for any table holding org data by hand, or put a table without org data on the reviewed
  allowlist with its reason (`tests/integration/rls_catalog.py`; `test_rls_catalog.py` fails
  otherwise).

## Rules for this component
- Every route declares a permission (`require(...)` or `require_operator()`) or is in
  `PUBLIC_PATHS`; the app refuses to start otherwise. Inaccessible orgs are one identical 404.
- Member routes connect as `purser_cp_app`, the request path's role: no BYPASSRLS, and only
  org-keyed policies. Operator routes connect as `purser_cp_operator`. Never give the API the
  owner or sweeper URL, and never add a policy for `purser_cp_app` that isn't keyed on the
  request's org (spec 4: cross-org work runs on its own roles).
- Store nothing from a token but identifiers (`sub`, organization IDs). Log reason codes and
  IDs, never tokens, bodies, emails, or Keycloak responses.
- Keycloak writes: pending row first, `purser_id` as the idempotency key, then active; the
  sweeper settles leftovers. No compensating deletes on the request path.
