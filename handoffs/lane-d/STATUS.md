# Lane D: Control plane and console — STATUS

Updated: 2026-10-09 by the Lane D Phase 0 task 1 session
Scope: Auth service, orgs and RBAC, devices and the CA, virtual keys, catalog, budgets, usage views, audit (build plan §5).

## Done
- **Task 1 (in review, PR [alindebergASL/MRouter#4](https://github.com/alindebergASL/MRouter/pull/4),
  branch `claude/youthful-wright-1ri02t`):** auth-service evaluation and
  [ADR 0003](../../docs/adr/0003-auth-service.md) (status Proposed, awaiting Andrew). Twelve
  candidates scored out of 70 with dated sources. Recommendation: Keycloak, self-hosted on ECS
  Fargate against its own RDS instance, with the control plane serving per-org SCIM over
  Keycloak's admin API. Runner-up: Auth0 B2B Essentials. Guarantee-reviewer run on the diff;
  findings answered in the PR.

## Next
1. After ADR 0003 is accepted: task 2, FastAPI skeleton (orgs, workspaces, users, roles; OpenAPI
   export; generated TypeScript client; A1 drift check; Dockerfile in the Compose stack). Assumes
   Keycloak: validate realm JWTs through discovery and JWKS with a stock library; map `sub` and
   the organization claim to our rows; the control plane owns the per-org SCIM endpoint.
2. Add Keycloak to `deploy/compose/dev.yaml` from `quay.io/keycloak/keycloak`, pinned by digest and
   pre-pulled by `scripts/cloud-setup.sh` (so `make check-pins` passes), with its own database in
   the dev Postgres.
3. Task 3, device enrollment: sidecar runs Keycloak's device-authorization grant against a public
   client; the control plane binds device to user and issues the mTLS certificate from our CA.
   Revocation (A2) runs through our API, which disables the Keycloak user and the device.
4. Task 4, console shell on the generated client, signing in through plain OIDC (Auth.js).

## Blocked
- ADR 0003 awaits Andrew's acceptance (G0 exit criterion). Nothing else.

## Guarantee tests passing

| ID | Test | Status |
|---|---|---|
| — | No guarantee tests yet (task 1 is a decision record). | — |
