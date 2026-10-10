# controlplane/

FastAPI service for identity, orgs and RBAC, and the admin API. It will later also own devices
and the CA, virtual keys, the model catalog, the price book (stored, never computed here),
budgets, usage views, and audit. Owner: Lane D. Commands are in [`CLAUDE.md`](CLAUDE.md).

## What exists (Lane D task 2a)

| Piece | Where |
|---|---|
| Orgs, workspaces, teams, users, memberships (owner, admin, billing admin, member, viewer at org, workspace, or team scope), platform operators, audit | `src/purser_controlplane/models.py`, `alembic/versions/0001_initial.py` |
| Row-level security: the API's role sees only the org its transaction set; operators and the sweeper have their own narrow policies | the migration; `db.py` sets the context per transaction |
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
| `purser_cp_owner` | migrations, `bootstrap-operator`, `dev-seed` | own the schema; not subject to RLS |
| `purser_cp_app` | the API | DML it needs, no deletes, no BYPASSRLS; sees one org per transaction |
| `purser_cp_sweeper` | the sweeper | identifiers only (no names or emails); change or delete pending rows |

Dev creates them in `deploy/compose/controlplane/controlplane-db.sql`; production creates them
with infrastructure code and keeps their passwords in AWS Secrets Manager.

## Not yet (later Lane D tasks)

The Keycloak admin-event reconciler, the per-org SCIM endpoint, user disable and device
revocation (A2), device enrollment and the CA, and the console.
