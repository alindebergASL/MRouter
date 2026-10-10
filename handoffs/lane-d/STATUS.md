# Lane D: Control plane and console — STATUS

Updated: 2026-10-10 by the Lane D task 2a session
Scope: Auth service, orgs and RBAC, devices and the CA, virtual keys, catalog, budgets, usage views, audit (build plan §5).

## Done
- **Task 1 (merged, [alindebergASL/MRouter#4](https://github.com/alindebergASL/MRouter/pull/4)):**
  [ADR 0003](../../docs/adr/0003-auth-service.md), accepted 2026-10-09: Keycloak, self-hosted, with
  the control plane serving per-org SCIM over Keycloak's admin API. Runner-up: Auth0 B2B Essentials.
- **Task 2a: the control-plane skeleton on Keycloak.** Three stacked PRs, each with its own CI.
  - **PR 1, [alindebergASL/MRouter#6](https://github.com/alindebergASL/MRouter/pull/6)** (branch
    `claude/sleepy-tesla-brlrn3`): Keycloak 26.8.0 in the dev stack (`deploy/compose/README.md`).
    The `purser-dev` realm has:
    - one test organization, with alice (password and TOTP) and bob;
    - minimal token claims;
    - a `pwd`/`mfa` step-up flow;
    - admin events kept 7 days;
    - an admin-API client without `manage-realm`;
    - users unable to edit their own email.

    `make dev-check` runs in CI.
  - **PR 2** (branch `claude/sleepy-tesla-brlrn3-controlplane`): the FastAPI service
    (`controlplane/README.md`).
    - Schema: orgs, workspaces, teams, users, memberships with five roles at three scopes, platform
      operators, and audit.
    - Postgres row-level security: the API role has no BYPASSRLS, and each transaction sets its own
      org context.
    - Keycloak token validation through discovery and JWKS.
    - Deny-by-default routes. Inaccessible orgs all get the same 404.
    - Operators need an MFA token, and their calls are audited.
    - Keycloak provisioning writes pending rows first, then a sweeper finishes or rolls them back.
    - Image: distroless, non-root, read-only, with D2 checks, built in CI for amd64 and arm64.
    - 169 tests run against the stack.
  - **PR 3** (branch `claude/sleepy-tesla-brlrn3-a1`), for A1:
    - The OpenAPI 3.1 document is exported to `controlplane/openapi/admin-api.json` (not
      `contracts/`).
    - The TypeScript client is generated into `console/src/client` with pinned `@hey-api/openapi-ts`.
    - The CI `a1-drift` job fails if either one drifts.
  - The guarantee-reviewer and security-reviewer ran on each PR. Their findings are fixed or
    answered in the PRs.

## Next
1. **Keycloak admin-event reconciler** (ADR 0003): one-minute poll; re-read user state on USER
   UPDATE and DELETE; alert when 5 minutes behind. Includes user disable through our API, which
   disables the Keycloak user and revokes devices (A2).
2. **Per-org SCIM 2.0 endpoint**, as scoped in the ADR (12 engineer-days; re-score at 18).
   Invitations and cross-org identities come with it. Today a Keycloak identity belongs to one org,
   and an email that already exists in the realm gets 409.
3. **Task 3, device enrollment:** the device-authorization grant, binding the device to its user,
   and mTLS certificates from our CA.
4. **Task 4, console shell** on `console/src/client` (Auth.js, plain OIDC). A1's second half: a
   check that the console calls the API only through the generated client.

## Open items for Andrew
- **Spec-change PR**, with protected paths:
  - move `controlplane/openapi/admin-api.json` under `contracts/`;
  - add A5 and its acceptance test;
  - add the auth service to architecture §9's component table;
  - add the PostToolUse and Stop hooks proposed in PR 2's description.
- **Production realm.** Write it separately from the dev realm, with:
  - brute-force protection;
  - TLS required;
  - exact redirect URIs;
  - no test clients.

  The control plane's admin-API secret is tier-0, because `manage-users` can reset credentials.
- **D2 remainder** (Lane A): SBOM, cosign signing, and the vulnerability-scan gate for the
  control-plane image.

## Blocked
- Nothing.

## Guarantee tests passing

`tests/acceptance/` has no Lane D tests yet, so these are component tests and CI checks, not
acceptance tests.

| ID | Test | Status |
|---|---|---|
| A1 | CI `a1-drift`: `make cp-openapi-check`, `make console-client-check`; `controlplane/tests/unit/test_openapi.py` | Passing. The console half awaits the console UI. |
| A4 (API side, partial) | `controlplane/tests/integration/test_rbac.py`: role matrix, uniform 404s, scope rules | Passing. Budget isolation comes with the budget endpoints. |
| A5 (pending in the spec) | `controlplane/tests/integration/test_rls.py`: no context means no rows; org A can't read or write org B with hand-written SQL | Passing |
| D2 (partial) | CI `controlplane-image` (amd64, arm64): `scripts/check-image-d2.sh` | Passing locally on amd64. SBOM, signing and scan are Lane A's. |
