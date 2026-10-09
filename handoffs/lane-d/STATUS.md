# Lane D: Control plane and console — STATUS

Updated: 2026-10-09 by the Lane D task 2a session
Scope: Auth service, orgs and RBAC, devices and the CA, virtual keys, catalog, budgets, usage views, audit (build plan §5).

## Done
- **Task 1 (in review, PR [alindebergASL/MRouter#4](https://github.com/alindebergASL/MRouter/pull/4),
  branch `claude/youthful-wright-1ri02t`):** auth-service evaluation and
  [ADR 0003](../../docs/adr/0003-auth-service.md) (status Accepted by Andrew on 2026-10-09). Twelve
  candidates scored out of 70 with dated sources. Recommendation: Keycloak, self-hosted on ECS
  Fargate against its own RDS instance, with the control plane serving per-org SCIM over
  Keycloak's admin API. Runner-up: Auth0 B2B Essentials. Guarantee-reviewer run on the diff;
  findings answered in the PR.

- **Task 2a, PR 1 of 3 (in review, branch `claude/sleepy-tesla-brlrn3`):** Keycloak 26.8.0 in the
  dev stack with the `purser-dev` realm: one test organization, alice (password + TOTP) and bob,
  an admin-API client for the control plane without `manage-realm`, a step-up flow (`pwd`/`mfa`),
  admin events kept 7 days. `make dev-check` runs in CI. See `deploy/compose/README.md`.
  PR 2 (control-plane service) and PR 3 (A1 contract and client) follow, stacked on it.

## Next
1. Task 2a, PRs 2 and 3. Then: task 2, the rest of the FastAPI skeleton (orgs, workspaces, users, roles; OpenAPI
   export; generated TypeScript client; A1 drift check; Dockerfile in the Compose stack). Assumes
   Keycloak: validate realm JWTs through discovery and JWKS with a stock library; map `sub` and
   the organization claim to our rows. Includes the Keycloak admin-event reconciler (one-minute
   poll, re-read user state on USER UPDATE/DELETE, alert when behind by 5 minutes) and the per-org
   SCIM 2.0 endpoint scoped and estimated in the ADR (12 engineer-days; re-score trigger at 18).
2. ~~Add Keycloak to `deploy/compose/dev.yaml`~~ (task 2a PR 1).
3. Task 3, device enrollment: sidecar runs Keycloak's device-authorization grant against a public
   client; the control plane binds device to user and issues the mTLS certificate from our CA.
   Revocation (A2) runs through our API, which disables the Keycloak user and the device;
   relay-token issuance reads our database only, never Keycloak.
4. Task 4, console shell on the generated client, signing in through plain OIDC (Auth.js).

## Blocked
- Nothing.

## Guarantee tests passing

| ID | Test | Status |
|---|---|---|
| — | No guarantee tests yet (task 1 is a decision record). | — |
