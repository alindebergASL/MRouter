"""Spec 0.6 (A5): forced row-level security, a non-login definer, an operator role.

- Every org-data table's policies are forced, so they bind the owner too. The
  owner (migrations, bootstrap-operator, dev-seed) gets an explicit
  owner_maintenance policy instead of the implicit exemption, so the
  exemption is visible in the catalog and in review.
- The SECURITY DEFINER functions move to a role that can't log in, with
  pg_temp last on their search_path.
- Platform-operator work moves to its own role, never the request path's
  (spec 4). The app role keeps org_isolation only, loses every write on orgs
  (no member route writes one), and can no longer call the operator
  functions, so no operator capability is reachable from the request path.
  The operator role gets exactly what org creation and listing need.

What row-level security does not do: SQL injected into the app role can set
purser.org_id itself and so read the org it names. RLS stops a query that
forgets its org filter; every query also names the org (spec 4).

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-10 06:00:00
"""

import os
from collections.abc import Sequence

from alembic import op

# Roles created outside migrations (dev: deploy/compose/controlplane/
# controlplane-db.sql; production: infrastructure, before this migration runs).
APP_ROLE = os.environ.get("PURSER_DB_APP_ROLE", "purser_cp_app")
OPERATOR_ROLE = os.environ.get("PURSER_DB_OPERATOR_ROLE", "purser_cp_operator")
OWNER_ROLE = os.environ.get("PURSER_DB_OWNER_ROLE", "purser_cp_owner")
DEFINER_ROLE = os.environ.get("PURSER_DB_DEFINER_ROLE", "purser_cp_definer")
S = "controlplane"

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Every table that holds org data (the RLS catalog test fails if one is missing).
ORG_DATA = ("orgs", "workspaces", "teams", "users", "memberships", "audit_events")
DEFINER_FUNCTIONS = ("is_operator(uuid)", "current_operator_is_valid()")
VALID = f"{S}.current_operator_is_valid()"
OPERATOR_SUB = "nullif(current_setting('purser.operator_sub', true), '')"


def _q(role: str) -> str:
    return f'"{role}"'


# -- 0001's operator policies on the app role (dropped here, restored on downgrade)

APP_OPERATOR_POLICIES = {
    "orgs": ("SELECT", "INSERT", "UPDATE"),
    "users": ("SELECT", "INSERT"),
    "memberships": ("SELECT", "INSERT"),
    "audit_events": ("INSERT",),
}


def _app_operator_policies(create: bool) -> None:
    for table, commands in APP_OPERATOR_POLICIES.items():
        for command in commands:
            name = f"operator_{command.lower()}"
            if not create:
                op.execute(f"DROP POLICY {name} ON {S}.{table}")
                continue
            if command == "INSERT":
                clause = f"WITH CHECK ({VALID})"
            elif command == "UPDATE":
                clause = f"USING ({VALID}) WITH CHECK ({VALID})"
            else:
                clause = f"USING ({VALID})"
            op.execute(
                f"CREATE POLICY {name} ON {S}.{table} FOR {command} TO {_q(APP_ROLE)} {clause}"
            )
    if not create:
        op.execute(f"DROP POLICY operator_activate_pending ON {S}.users")
        return
    op.execute(f"""
        CREATE POLICY operator_activate_pending ON {S}.users FOR UPDATE TO {_q(APP_ROLE)}
        USING ({VALID} AND status = 'pending') WITH CHECK ({VALID})
    """)


# -- the operator role: exactly what creating and listing orgs needs ----------------

OPERATOR_POLICIES = {
    # (table, name, command): (USING, WITH CHECK), each ANDed with a valid operator
    ("orgs", "operator_select", "SELECT"): ("true", None),
    ("orgs", "operator_insert", "INSERT"): (
        None,
        "status = 'pending' AND keycloak_org_id IS NULL",
    ),
    ("orgs", "operator_activate_pending", "UPDATE"): ("status = 'pending'", "true"),
    ("users", "operator_select", "SELECT"): ("true", None),
    ("users", "operator_insert", "INSERT"): (None, "status = 'pending' AND keycloak_sub IS NULL"),
    ("users", "operator_activate_pending", "UPDATE"): ("status = 'pending'", "true"),
    ("memberships", "operator_select", "SELECT"): ("true", None),
    # The first owner of a new org, and nothing else.
    ("memberships", "operator_insert", "INSERT"): (None, "role = 'owner' AND scope_type = 'org'"),
    # An operator's audit rows name that operator, never a user or the system.
    ("audit_events", "operator_insert", "INSERT"): (
        None,
        f"actor_kind = 'operator' AND actor_sub::text = {OPERATOR_SUB}",
    ),
}
OPERATOR_GRANTS = (
    f"GRANT SELECT, INSERT, UPDATE (status, keycloak_org_id, updated_at) ON {S}.orgs",
    f"GRANT SELECT, INSERT, UPDATE (status, keycloak_sub, updated_at) ON {S}.users",
    f"GRANT SELECT, INSERT ON {S}.memberships",
    f"GRANT INSERT ON {S}.audit_events",
)


def _operator_policies(create: bool) -> None:
    for (table, name, command), (using, check) in OPERATOR_POLICIES.items():
        if not create:
            op.execute(f"DROP POLICY {name} ON {S}.{table}")
            continue
        clauses = []
        if using is not None:
            clauses.append(
                f"USING ({VALID} AND {using})" if using != "true" else f"USING ({VALID})"
            )
        if check is not None:
            clauses.append(
                f"WITH CHECK ({VALID} AND {check})" if check != "true" else f"WITH CHECK ({VALID})"
            )
        op.execute(
            f"CREATE POLICY {name} ON {S}.{table} FOR {command} TO {_q(OPERATOR_ROLE)}"
            f" {' '.join(clauses)}"
        )


def upgrade() -> None:
    # 1. Forced policies, with the owner's exemption stated as a policy.
    for table in ORG_DATA:
        op.execute(f"ALTER TABLE {S}.{table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY owner_maintenance ON {S}.{table} TO {_q(OWNER_ROLE)}"
            " USING (true) WITH CHECK (true)"
        )

    # 2. Operator access leaves the request path's role, which also stops
    # writing orgs at all (member routes only read their own org).
    _app_operator_policies(create=False)
    op.execute(f"REVOKE INSERT, UPDATE ON {S}.orgs FROM {_q(APP_ROLE)}")
    for grant in OPERATOR_GRANTS:
        op.execute(f"{grant} TO {_q(OPERATOR_ROLE)}")
    _operator_policies(create=True)

    # 3. The definer functions: callable by the operator role only, owned by a
    # role that can't log in and reads the operator list only, pg_temp last on
    # the search path. A new owner needs CREATE on the schema at the moment of
    # transfer only.
    for function in DEFINER_FUNCTIONS:
        op.execute(f"REVOKE EXECUTE ON FUNCTION {S}.{function} FROM {_q(APP_ROLE)}")
        op.execute(f"GRANT EXECUTE ON FUNCTION {S}.{function} TO {_q(OPERATOR_ROLE)}")
        op.execute(f"ALTER FUNCTION {S}.{function} SET search_path = pg_catalog, pg_temp")
    op.execute(f"GRANT SELECT ON {S}.platform_operators TO {_q(DEFINER_ROLE)}")
    op.execute(f"GRANT CREATE ON SCHEMA {S} TO {_q(DEFINER_ROLE)}")
    for function in DEFINER_FUNCTIONS:
        op.execute(f"ALTER FUNCTION {S}.{function} OWNER TO {_q(DEFINER_ROLE)}")
    op.execute(f"REVOKE CREATE ON SCHEMA {S} FROM {_q(DEFINER_ROLE)}")


def downgrade() -> None:
    # Back to 0001 exactly.
    for function in DEFINER_FUNCTIONS:
        op.execute(f"ALTER FUNCTION {S}.{function} OWNER TO {_q(OWNER_ROLE)}")
        op.execute(f"ALTER FUNCTION {S}.{function} SET search_path = pg_catalog")
        op.execute(f"REVOKE EXECUTE ON FUNCTION {S}.{function} FROM {_q(OPERATOR_ROLE)}")
        op.execute(f"GRANT EXECUTE ON FUNCTION {S}.{function} TO {_q(APP_ROLE)}")
    op.execute(f"REVOKE SELECT ON {S}.platform_operators FROM {_q(DEFINER_ROLE)}")

    _operator_policies(create=False)
    for table in ("orgs", "users", "memberships", "audit_events"):
        op.execute(f"REVOKE ALL ON {S}.{table} FROM {_q(OPERATOR_ROLE)}")
    op.execute(f"GRANT INSERT, UPDATE ON {S}.orgs TO {_q(APP_ROLE)}")
    _app_operator_policies(create=True)

    for table in ORG_DATA:
        op.execute(f"DROP POLICY owner_maintenance ON {S}.{table}")
        op.execute(f"ALTER TABLE {S}.{table} NO FORCE ROW LEVEL SECURITY")
