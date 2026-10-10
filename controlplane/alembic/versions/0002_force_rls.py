"""Spec 0.6 (A5): forced row-level security, a non-login definer, an operator role.

- Every org-data table's policies are forced, so they bind the owner too. The
  owner (migrations, bootstrap-operator, dev-seed) gets an explicit
  owner_maintenance policy instead of the implicit exemption, so the
  exemption is visible in the catalog and in review.
- The SECURITY DEFINER functions move to a role that can't log in.
- Platform-operator work moves to its own role, never the request path's
  (spec 4): the app role keeps org_isolation only, so no setting its
  transaction can make reaches another org's rows.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-10 06:00:00
"""

import os
from collections.abc import Sequence

from alembic import op

# Roles created outside migrations (dev: deploy/compose/controlplane/
# controlplane-db.sql; production: infrastructure).
APP_ROLE = os.environ.get("PURSER_DB_APP_ROLE", "purser_cp_app")
OPERATOR_ROLE = os.environ.get("PURSER_DB_OPERATOR_ROLE", "purser_cp_operator")
OWNER_ROLE = os.environ.get("PURSER_DB_OWNER_ROLE", "purser_cp_owner")
DEFINER_ROLE = os.environ.get("PURSER_DB_DEFINER_ROLE", "purser_cp_definer")
S = "controlplane"

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Every table that holds org data, as in 0001 (the RLS catalog test fails if a
# new one is missing).
ORG_DATA = ("orgs", "workspaces", "teams", "users", "memberships", "audit_events")
DEFINER_FUNCTIONS = ("is_operator(uuid)", "current_operator_is_valid()")

# The operator policies (0001 gave them to the app role), and the table
# privileges they need.
OPERATOR_POLICIES = {
    "orgs": ("SELECT", "INSERT", "UPDATE"),
    "users": ("SELECT", "INSERT"),
    "memberships": ("SELECT", "INSERT"),
    "audit_events": ("INSERT",),
}
OPERATOR_GRANTS = {
    "orgs": "SELECT, INSERT, UPDATE",
    "users": "SELECT, INSERT, UPDATE",
    "memberships": "SELECT, INSERT",
    "audit_events": "INSERT",
}
VALID_OPERATOR = f"{S}.current_operator_is_valid()"


def _q(role: str) -> str:
    return f'"{role}"'


def _create_operator_policies(role: str) -> None:
    for table, commands in OPERATOR_POLICIES.items():
        for command in commands:
            if command == "INSERT":
                clause = f"WITH CHECK ({VALID_OPERATOR})"
            elif command == "UPDATE":
                clause = f"USING ({VALID_OPERATOR}) WITH CHECK ({VALID_OPERATOR})"
            else:
                clause = f"USING ({VALID_OPERATOR})"
            op.execute(
                f"CREATE POLICY operator_{command.lower()} ON {S}.{table}"
                f" FOR {command} TO {_q(role)} {clause}"
            )
    # Activating the first owner of an org an operator just created.
    op.execute(f"""
        CREATE POLICY operator_activate_pending ON {S}.users FOR UPDATE TO {_q(role)}
        USING ({VALID_OPERATOR} AND status = 'pending') WITH CHECK ({VALID_OPERATOR})
    """)


def _drop_operator_policies() -> None:
    for table, commands in OPERATOR_POLICIES.items():
        for command in commands:
            op.execute(f"DROP POLICY operator_{command.lower()} ON {S}.{table}")
    op.execute(f"DROP POLICY operator_activate_pending ON {S}.users")


def _move_operator_access(source: str, target: str) -> None:
    """Move the operator policies, their table grants, and the definer functions."""
    _drop_operator_policies()
    for table, privileges in OPERATOR_GRANTS.items():
        if source == APP_ROLE:
            # The app role keeps the privileges its own org-scoped routes use.
            continue
        op.execute(f"REVOKE {privileges} ON {S}.{table} FROM {_q(source)}")
    for table, privileges in OPERATOR_GRANTS.items():
        op.execute(f"GRANT {privileges} ON {S}.{table} TO {_q(target)}")
    for function in DEFINER_FUNCTIONS:
        op.execute(f"REVOKE EXECUTE ON FUNCTION {S}.{function} FROM {_q(source)}")
        op.execute(f"GRANT EXECUTE ON FUNCTION {S}.{function} TO {_q(target)}")
    _create_operator_policies(target)


def upgrade() -> None:
    # 1. Forced policies, with the owner's exemption stated as a policy.
    for table in ORG_DATA:
        op.execute(f"ALTER TABLE {S}.{table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY owner_maintenance ON {S}.{table} TO {_q(OWNER_ROLE)}"
            " USING (true) WITH CHECK (true)"
        )

    # 2. Operator access leaves the request path's role.
    _move_operator_access(APP_ROLE, OPERATOR_ROLE)

    # 3. The definer functions belong to a role that can't log in. It reads
    # the operator list and nothing else. A new owner needs CREATE on the
    # schema at the moment of transfer only.
    op.execute(f"GRANT SELECT ON {S}.platform_operators TO {_q(DEFINER_ROLE)}")
    op.execute(f"GRANT CREATE ON SCHEMA {S} TO {_q(DEFINER_ROLE)}")
    for function in DEFINER_FUNCTIONS:
        op.execute(f"ALTER FUNCTION {S}.{function} OWNER TO {_q(DEFINER_ROLE)}")
    op.execute(f"REVOKE CREATE ON SCHEMA {S} FROM {_q(DEFINER_ROLE)}")


def downgrade() -> None:
    for function in DEFINER_FUNCTIONS:
        op.execute(f"ALTER FUNCTION {S}.{function} OWNER TO {_q(OWNER_ROLE)}")
    op.execute(f"REVOKE SELECT ON {S}.platform_operators FROM {_q(DEFINER_ROLE)}")

    _move_operator_access(OPERATOR_ROLE, APP_ROLE)

    for table in ORG_DATA:
        op.execute(f"DROP POLICY owner_maintenance ON {S}.{table}")
        op.execute(f"ALTER TABLE {S}.{table} NO FORCE ROW LEVEL SECURITY")
