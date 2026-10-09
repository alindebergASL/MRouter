# Lane D: Control plane and console — STATUS

Updated: 2026-10-09 by the spec draft 0.6 session (spec-change)
Scope: Auth service, the install bootstrap command, orgs and RBAC with row-level security, devices and the CA, virtual keys, catalog, budgets, usage views, audit (build plan §5).

## Done
- **Task 1 (merged, PR [alindebergASL/MRouter#4](https://github.com/alindebergASL/MRouter/pull/4)):** auth-service evaluation and
  [ADR 0003](../../docs/adr/0003-auth-service.md) (status Accepted by Andrew on 2026-10-09). Twelve
  candidates scored out of 70 with dated sources. Recommendation: Keycloak, self-hosted on ECS
  Fargate against its own RDS instance, with the control plane serving per-org SCIM over
  Keycloak's admin API. Runner-up: Auth0 B2B Essentials. Guarantee-reviewer run on the diff;
  findings answered in the PR.

## Next
1. Task 2, FastAPI skeleton (orgs, workspaces, users, roles; OpenAPI export; generated
   TypeScript client; A1 drift check; Dockerfile in the Compose stack). Validate tokens against
   the configured OIDC issuer (Keycloak's realm) through discovery and JWKS with a stock library;
   map `sub` and the organization claim to our rows. Every org-scoped table gets a forced
   row-level security policy keyed on the request's org from the first migration (spec 4, A5). Includes the Keycloak admin-event reconciler (one-minute
   poll, re-read user state on USER UPDATE/DELETE, alert when behind by 5 minutes) and the per-org
   SCIM 2.0 endpoint scoped and estimated in the ADR (12 engineer-days; re-score trigger at 18).
2. Add Keycloak to `deploy/compose/dev.yaml` from `quay.io/keycloak/keycloak`, pinned by digest and
   pre-pulled by `scripts/cloud-setup.sh` (so `make check-pins` passes), with its own database in
   the dev Postgres.
3. Task 3, install bootstrap command (spec 9.1, D4): one idempotent control-plane task that
   generates the realm, admin-client secrets, signing keys, the device CA, HMAC keys, and the
   first admin, who is also the first platform operator, with MFA required. No default
   credentials outside the dev profile. Needed for the us-east-1 install at G2.
4. Task 4, device enrollment: sidecar runs Keycloak's device-authorization grant against a public
   client; the control plane binds device to user and issues the mTLS certificate from our CA.
   Revocation (A2) runs through our API, which disables the Keycloak user and the device;
   relay-token issuance reads our database only, never Keycloak.
5. Task 5, console shell on the generated client, signing in through plain OIDC (Auth.js).

## Blocked
- Nothing. The A5 and D4 test modules come from a later spec-change PR (spec draft 0.6 defines them).

## Guarantee tests passing

| ID | Test | Status |
|---|---|---|
| — | No guarantee tests yet (task 1 is a decision record). | — |
