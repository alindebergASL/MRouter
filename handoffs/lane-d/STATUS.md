# Lane D: Control plane and console — STATUS

Updated: 2026-10-10 by the Lane D spec 0.6 alignment session (A5)
Scope: Auth service, the install bootstrap command, orgs and RBAC with row-level security, devices and the CA, virtual keys, catalog, budgets, usage views, audit (build plan §5).

## Done
- **Task 1 (merged, PR [alindebergASL/MRouter#4](https://github.com/alindebergASL/MRouter/pull/4)):** auth-service evaluation and
  [ADR 0003](../../docs/adr/0003-auth-service.md) (status Accepted by Andrew on 2026-10-09). Twelve
  candidates scored out of 70 with dated sources. Recommendation: Keycloak, self-hosted on ECS
  Fargate against its own RDS instance, with the control plane serving per-org SCIM over
  Keycloak's admin API. Runner-up: Auth0 B2B Essentials. Guarantee-reviewer run on the diff;
  findings answered in the PR.

- **Task 2a, PR 1 of 3 (merged, PR [alindebergASL/MRouter#6](https://github.com/alindebergASL/MRouter/pull/6)):** Keycloak 26.8.0 in the
  dev stack with the `purser-dev` realm: one test organization, alice (password + TOTP) and bob,
  an admin-API client for the control plane without `manage-realm`, a step-up flow (`pwd`/`mfa`),
  admin events kept 7 days, users unable to edit their own email. `make dev-check` runs in CI.
  See `deploy/compose/README.md`.

- **Task 2a, PR 2 of 3 (merged, PR [alindebergASL/MRouter#7](https://github.com/alindebergASL/MRouter/pull/7)):** the FastAPI control-plane
  service (`controlplane/README.md`).
  - Schema: orgs, workspaces, teams, users, memberships with five roles at three scopes, platform
    operators, and audit. Alembic migrations, tested up, down and up again.
  - Postgres row-level security on every org-scoped table: the API role has no BYPASSRLS, and each
    transaction sets its own org context (`set_config(..., true)`). Forced by the alignment PR
    below.
  - Keycloak token validation through discovery and JWKS with PyJWT: issuer, audience, expiry,
    algorithm allowlist, rate-limited key refresh. Only `sub`, organization IDs and `acr` are kept.
  - Deny-by-default routes, checked at startup. The request's org comes from the path and is
    checked against our membership rows as well as the token claim. Inaccessible orgs all get the
    same 404.
  - Platform operators: idempotent `bootstrap-operator`, no shipped default; operator calls need
    an MFA `acr` and write an audit row in the same transaction.
  - Keycloak provisioning writes a pending row first (`purser_id` is the idempotency key), then
    marks it active; a sweeper finishes or rolls back rows pending for 5 minutes. No compensating
    deletes on the request path.
  - Image: distroless Python 3.13, non-root, read-only, no shell, with D2 checks, built in CI on
    amd64 and arm64.

- **Task 2a, PR 3 of 3 (merged, PR [alindebergASL/MRouter#10](https://github.com/alindebergASL/MRouter/pull/10)), A1:** PR
  [alindebergASL/MRouter#8](https://github.com/alindebergASL/MRouter/pull/8) merged into PR 2's branch
  after PR 2 had merged, so its content reached main through #10, from the same branch.
  - The OpenAPI 3.1 document is exported to `controlplane/openapi/admin-api.json` (not
    `contracts/`).
  - The TypeScript client is generated into `console/src/client` with pinned `@hey-api/openapi-ts`.
  - The CI `a1-drift` job fails if either one drifts.

  The guarantee-reviewer and security-reviewer ran on each task 2a PR; their findings are fixed or
  answered in the PRs.

- **Spec 0.6 alignment, A5 (PR from `claude/sleepy-tesla-brlrn3-a5`):** migration `0002_force_rls`.
  - `FORCE ROW LEVEL SECURITY` on all six org-data tables. The owner's access (migrations,
    `bootstrap-operator`, `dev-seed`) is an explicit `owner_maintenance` policy, visible in the
    catalog, instead of the implicit owner exemption.
  - The `SECURITY DEFINER` functions (`is_operator`, `current_operator_is_valid`) are owned by
    `purser_cp_definer`, which cannot log in and reads `platform_operators` only; their
    `search_path` is `pg_catalog, pg_temp`.
  - Cross-org work runs on its own roles, never the request path's: operator routes use
    `purser_cp_operator` through a second engine (`PURSER_DB_OPERATOR_URL`), with only what
    creating and listing orgs needs (column-level updates, its own audit rows); the sweeper keeps
    `purser_cp_sweeper`. The request path's `purser_cp_app` has one policy, `org_isolation`, no
    operator function, and no write on `orgs`, so a known operator's sub grants it nothing.
  - Every member-route query names the path's org (spec 4), tested with the API on a role that
    sees every org (`test_org_filters.py`). Row-level security catches a forgotten filter; it is
    not an injection defense, since injected SQL can set `purser.org_id` itself.
  - The reviewed allowlist (`platform_operators`, `alembic_version`; the ledger's tables when Lane
    B adds them) and the A5 part 3 catalog checks in `controlplane/tests/integration/
    rls_catalog.py`, with negative tests for each kind of violation and a check that revision
    0001 fails them for the reasons 0002 fixes.

## Decisions recorded (Andrew, 2026-10-10)
- **Email-existence 409, accepted for now.** `POST /v1/orgs/{org}/users` answers 409 when the email
  already exists anywhere in the Keycloak realm, so an org admin can learn that an address has an
  account in some org. Acceptable while there is one real org; it must be gone before a second
  real org exists. The fix is task 2b. The operator endpoint's 409 stays: operators see every org.

## Next
1. **Task 2b, required before G2 (G2 blocker):** invitations, with spec 0.6's A5 part 4 (one person
   in two orgs).
   - User creation answers the same way whether or not the email exists in the realm: an existing
     identity gets an invitation it must accept; a new one is created.
   - `users.keycloak_sub` becomes unique per org rather than across the realm.
   - Removes the email-existence 409 above.
2. **Budget policy row on org creation (spec 7.1):** waits for Lane B's budget tables; org creation
   writes it once they exist.
3. Task 2, the rest, from spec 0.6's list (the skeleton, OpenAPI export, client, A1 drift check
   and image are done in task 2a; the forced RLS is done in the alignment PR): the Keycloak admin-event reconciler
   (one-minute poll, re-read user state on USER UPDATE/DELETE, alert when behind by 5 minutes;
   user disable through our API, A2) and the per-org SCIM 2.0 endpoint scoped and estimated in
   the ADR (12 engineer-days; re-score trigger at 18).
4. ~~Add Keycloak to `deploy/compose/dev.yaml`~~ (task 2a PR 1).
5. Task 3, install bootstrap command (spec 9.1, D4): one idempotent control-plane task that
   generates the realm, admin-client secrets, signing keys, the device CA, HMAC keys, and the
   first admin, who is also the first platform operator, with MFA required. No default
   credentials outside the dev profile. Needed for the us-east-1 install at G2.
6. Task 4, device enrollment: sidecar runs Keycloak's device-authorization grant against a public
   client; the control plane binds device to user and issues the mTLS certificate from our CA.
   Revocation (A2) runs through our API, which disables the Keycloak user and the device;
   relay-token issuance reads our database only, never Keycloak.
7. Task 5, console shell on the generated client, signing in through plain OIDC (Auth.js). A1's
   second half: a check that the console calls the API only through the generated client.

## Open items for Andrew
- **Spec-change PR**, with protected paths:
  - move `controlplane/openapi/admin-api.json` under `contracts/`;
  - the `test_A5_*` and D4 acceptance modules;
  - add the auth service to architecture §9's component table;
  - add the PostToolUse and Stop hooks proposed in PR 2's description.
- **Production realm.** Written separately from the dev realm, with brute-force protection, TLS
  required, exact redirect URIs and no test clients. The control plane's admin-API secret is
  tier-0, because `manage-users` can reset credentials.
- **D2 remainder** (Lane A): SBOM, cosign signing, and the vulnerability-scan gate for the
  control-plane image.
- **Production database roles** (Lane A infrastructure, before migration 0002 runs there):
  `purser_cp_owner`, `purser_cp_app`, `purser_cp_operator`, `purser_cp_sweeper` (login,
  NOBYPASSRLS), `purser_cp_definer` (NOLOGIN), and `GRANT purser_cp_definer TO purser_cp_owner
  WITH INHERIT TRUE, SET TRUE`, as `deploy/compose/controlplane/controlplane-db.sql` does in dev.
  Without them 0002 fails as a whole, never half-applied.

## Blocked
- Nothing. The A5 and D4 test modules come from a later spec-change PR (spec draft 0.6 defines them).
  Item 2 waits on Lane B's tables but blocks nothing else.

## Guarantee tests passing

`tests/acceptance/` has no Lane D tests yet, so these are component tests and CI checks, not
acceptance tests.

| ID | Test | Status |
|---|---|---|
| A1 | CI `a1-drift`: `make cp-openapi-check`, `make console-client-check`; `controlplane/tests/unit/test_openapi.py` | Passing. The console half awaits task 5. |
| A4 (API side, partial) | `controlplane/tests/integration/test_rbac.py`: role matrix, uniform 404s, scope rules | Passing. Budget isolation comes with the budget endpoints. |
| A5 part 3 (component) | `controlplane/tests/integration/test_rls.py` (as the request path's role, with org A set, unfiltered SELECT, UPDATE and DELETE reach no org B row and an org B insert fails; no org set reads nothing; pooled connections carry no org; the operator and sweeper roles reach only their own policies) and `test_rls_catalog.py` (forced policies, the allowlist, role ownership and BYPASSRLS, definer functions) | Passing. The protected `test_A5_*` acceptance modules come from the spec-change PR; A5 parts 1, 2 and 4 come with their features (part 4 in task 2b). |
| D2 (partial) | CI `controlplane-image` (amd64, arm64): `scripts/check-image-d2.sh` | Passing. SBOM, signing and scan are Lane A's. |
