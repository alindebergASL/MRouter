# controlplane/

FastAPI service for identity, orgs and RBAC, and the admin API. It will later also own devices
and the CA, virtual keys, the model catalog, the price book (stored, never computed here),
budgets, usage views, and audit. Owner: Lane D. Commands are in [`CLAUDE.md`](CLAUDE.md).

## What exists (Lane D task 2a)

| Piece | Where |
|---|---|
| Orgs, workspaces, teams, users, memberships (owner, admin, billing admin, member, viewer at org, workspace, or team scope), platform operators, audit | `src/purser_controlplane/models.py`, `alembic/versions/0001_initial.py` |
| Row-level security (A5), forced on every org-data table: the request path's role sees only the org its transaction set; operator work and the sweeper run on their own roles with their own narrow policies | `alembic/versions/0001_initial.py`, `0002_force_rls.py`; `db.py` sets the context per transaction; `tests/integration/rls_catalog.py` holds the reviewed allowlist and the catalog check |
| Keycloak access-token validation (discovery, JWKS, issuer, audience, expiry, algorithm allowlist) | `auth/oidc.py` |
| Roles and permissions; deny by default | `authz.py`, `api/deps.py`, `app.py` |
| Admin API under `/v1` | `api/operator.py` (operators: create and list orgs), `api/orgs.py` (everything inside an org) |
| Keycloak provisioning and the pending-row sweeper | `keycloak.py`, `sweeper.py` |
| CLI: `serve`, `migrate`, `bootstrap-operator`, `dev-seed`, `sweep` | `__main__.py` |
| Image | `deploy/images/controlplane/Dockerfile` |

## Identity and access in one page

- **Tokens.** Keycloak issues them. The API checks the signature against the realm's JWKS, the
  issuer (from configuration; the discovery document must name the same one), the audience
  `purser-admin-api`, the expiry, and that the algorithm is an allowed asymmetric one. It keeps
  only `sub`, the organization IDs from the `organization` claim, and `acr`.
- **Orgs.** A request to `/v1/orgs/{org}/…` succeeds only if the org is active, its Keycloak
  organization ID is in the token, and the caller has an active user row there with a role. Any
  of those missing is the same 404. A role that doesn't allow the action is 403.
- **Roles.** One table in `authz.py`:

  | Permission | owner | admin | billing admin | member | viewer |
  |---|---|---|---|---|---|
  | read org, workspaces, teams | ✓ | ✓ | ✓ | ✓ | ✓ |
  | create workspaces and teams | ✓ | ✓ | | | |
  | read users and memberships | ✓ | ✓ | ✓ | | ✓ |
  | create users | ✓ | ✓ | | | |
  | assign roles | ✓ | ✓ (not owner or admin) | | | |

  A workspace-scope grant applies inside that workspace (a workspace admin can create teams
  there); a team-scope grant reads that team. Creating workspaces, users, and memberships needs
  an org-scope grant.
- **Operators** create orgs and their first owner. They are listed in `platform_operators`
  (added only by `bootstrap-operator`, which runs as the owner role), need a token with
  `acr=mfa` (TOTP or a passkey through Keycloak's step-up flow), and every operator call writes
  an audit row in the same transaction. Anyone else gets 404 from operator routes.
- **Keycloak writes.** Creating an org or user writes our row as pending, creates the Keycloak
  organization or user carrying our ID as `purser_id`, then marks the row active. If Keycloak
  fails, the API answers 202 and the sweeper finishes the row (the Keycloak object exists) or
  deletes it (it doesn't) after 5 minutes. A Keycloak identity belongs to one org for now; an
  email already in Keycloak is a 409 until invitations exist.

## Database roles

| Role | Used by | Can |
|---|---|---|
| `purser_cp_owner` | migrations, `bootstrap-operator`, `dev-seed` | own the schema; RLS is forced on it too, and its exemption is the explicit `owner_maintenance` policy |
| `purser_cp_app` | the API's member routes (the request path) | DML it needs in its org: reads its org's row but never writes `orgs`, no deletes, no BYPASSRLS; its only policy is `org_isolation`, so it sees the one org its transaction set |
| `purser_cp_operator` | the API's operator routes | list orgs; create a pending org, its pending first owner, and the owner grant; activate them; audit as the operator. Its policies apply only while the transaction names a registered operator (the API sets it after checking the operator's MFA token) |
| `purser_cp_sweeper` | the sweeper | identifiers only (no names or emails); change or delete pending rows |
| `purser_cp_definer` | nobody (cannot log in) | owns the `SECURITY DEFINER` functions (`is_operator`, `current_operator_is_valid`); reads `platform_operators` only |

Cross-org work never runs on the request path's role (spec 4): operator views on
`purser_cp_operator`, pending-row reconciliation on `purser_cp_sweeper`, migrations on
`purser_cp_owner`. The API process holds both the app and operator credentials; the separation
is between database roles. SQL reaching the app role can't create orgs or use operator powers.

What row-level security is for: a query that forgets its org filter, or names the wrong org,
still returns and changes nothing outside the transaction's org. It is not a defense against
SQL injection: injected SQL on the app role can set `purser.org_id` to any org ID it knows.
That is why every query also names the path's org itself (spec 4; `api/orgs.py`, checked by
`tests/integration/test_org_filters.py` with row-level security out of the way).

Dev creates them in `deploy/compose/controlplane/controlplane-db.sql`; production creates them
with infrastructure code and keeps their passwords in AWS Secrets Manager.

## Not yet (later Lane D tasks)

The Keycloak admin-event reconciler, the per-org SCIM endpoint, user disable and device
revocation (A2), device enrollment and the CA, and the console.
