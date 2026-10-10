"""The A5 part 3 catalog checks: what the database must look like, read from the catalog.

CI fails (spec 4, A5 part 3) if:
- a table that is not on the reviewed allowlist lacks forced row-level security
  with a policy for reads and writes keyed on the request's org;
- an org-data table other than the ledger's is on the allowlist;
- a request-path or cross-org role owns anything, inherits from another role,
  or has BYPASSRLS (or is a superuser);
- a SECURITY DEFINER function lacks a fixed search_path or is owned by a role
  that can log in.

And, since the request path must see one org only: every permissive policy
that applies to a request-path role is keyed on the request's org.

The negative tests below plant each kind of violation in a transaction that
is rolled back, and show the check reports it.
"""

from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import Connection, create_engine, text

from tests.conftest import TestDatabase, create_test_database, drop_test_database, migrate_to
from tests.integration.rls_catalog import RLS_ALLOWLIST, org_data_tables, rls_violations


def _admin(db: TestDatabase) -> Iterator[Connection]:
    """The dev superuser, in a transaction that is always rolled back."""
    engine = create_engine(db.admin_url)
    with engine.connect() as connection:
        yield connection
        connection.rollback()
    engine.dispose()


@pytest.fixture
def admin_conn(test_db: TestDatabase) -> Iterator[Connection]:
    yield from _admin(test_db)


def test_the_migrated_schema_complies(admin_conn: Connection) -> None:
    assert rls_violations(admin_conn) == []


def test_the_org_data_tables_are_the_expected_ones(admin_conn: Connection) -> None:
    expected = {"orgs", "workspaces", "teams", "users", "memberships", "audit_events"}
    assert org_data_tables(admin_conn) == expected


def test_the_check_reports_what_revision_0001_lacked() -> None:
    """Before spec 0.6's alignment (0002), the check fails for the reasons 0002 fixes."""
    db = create_test_database(migrate=False)
    try:
        migrate_to(db, "0001")
        for conn in _admin(db):
            violations = rls_violations(conn)
    finally:
        drop_test_database(db)
    assert "orgs: row-level security is not enabled and forced" in violations
    assert "orgs.operator_select: lets the request path's role past its org" in violations
    assert (
        "function controlplane.is_operator: SECURITY DEFINER owned by a role that can log in"
        in violations
    )


def test_every_allowlist_entry_has_a_reason() -> None:
    assert all(reason.strip() for reason in RLS_ALLOWLIST.values())


# -- negative tests: each planted violation is reported ---------------------------


def _flags(conn: Connection, ddl: str, expected: str, **kwargs: Any) -> None:
    for statement in ddl.split(";"):
        if statement.strip():
            conn.execute(text(statement))
    violations = rls_violations(conn, **kwargs)
    assert any(expected in v for v in violations), violations


def test_flags_a_table_whose_rls_is_not_forced(admin_conn: Connection) -> None:
    _flags(
        admin_conn,
        "ALTER TABLE controlplane.workspaces NO FORCE ROW LEVEL SECURITY",
        "workspaces: row-level security is not enabled and forced",
    )


def test_flags_a_new_org_data_table_without_rls(admin_conn: Connection) -> None:
    _flags(
        admin_conn,
        "CREATE TABLE controlplane.widgets (id uuid PRIMARY KEY, org_id uuid NOT NULL)",
        "widgets: row-level security is not enabled and forced",
    )


def test_flags_a_table_with_rls_but_no_org_keyed_policy(admin_conn: Connection) -> None:
    _flags(
        admin_conn,
        "CREATE TABLE controlplane.widgets (id uuid PRIMARY KEY, org_id uuid NOT NULL);"
        " ALTER TABLE controlplane.widgets ENABLE ROW LEVEL SECURITY;"
        " ALTER TABLE controlplane.widgets FORCE ROW LEVEL SECURITY",
        "widgets: no policy keys the request path's reads and writes on its org",
    )


def test_flags_org_data_on_the_allowlist_found_through_a_foreign_key(
    admin_conn: Connection,
) -> None:
    # No org_id column: it is org data because it points at a workspace.
    _flags(
        admin_conn,
        "CREATE TABLE controlplane.notes"
        " (id uuid PRIMARY KEY, workspace_id uuid REFERENCES controlplane.workspaces (id))",
        "notes: holds org data but is on the allowlist",
        allowlist={**RLS_ALLOWLIST, "notes": "planted"},
    )


def test_flags_a_policy_that_lets_the_request_path_past_its_org(admin_conn: Connection) -> None:
    _flags(
        admin_conn,
        "CREATE POLICY leak ON controlplane.workspaces FOR SELECT TO purser_cp_app USING (true)",
        "workspaces.leak: lets the request path's role past its org",
    )


def test_flags_a_public_policy(admin_conn: Connection) -> None:
    _flags(
        admin_conn,
        "CREATE POLICY leak ON controlplane.teams FOR SELECT USING (true)",
        "teams.leak: lets the request path's role past its org",
    )


def test_flags_a_role_with_bypassrls(admin_conn: Connection) -> None:
    _flags(
        admin_conn,
        "ALTER ROLE purser_cp_app BYPASSRLS",
        "role purser_cp_app: superuser or BYPASSRLS",
    )


def test_flags_a_role_that_owns_a_table(admin_conn: Connection) -> None:
    _flags(
        admin_conn,
        "ALTER TABLE controlplane.teams OWNER TO purser_cp_operator",
        "role purser_cp_operator: owns",
    )


def test_flags_a_role_that_inherits_from_the_owner(admin_conn: Connection) -> None:
    _flags(
        admin_conn,
        "GRANT purser_cp_owner TO purser_cp_sweeper",
        "role purser_cp_sweeper: is a member of another role",
    )


def test_flags_a_definer_function_owned_by_a_login_role(admin_conn: Connection) -> None:
    _flags(
        admin_conn,
        "CREATE FUNCTION controlplane.planted() RETURNS int LANGUAGE sql"
        " SECURITY DEFINER SET search_path = pg_catalog AS 'SELECT 1'",
        "function controlplane.planted: SECURITY DEFINER owned by a role that can log in",
    )


def test_flags_a_definer_function_without_a_fixed_search_path(admin_conn: Connection) -> None:
    _flags(
        admin_conn,
        "CREATE FUNCTION controlplane.planted() RETURNS int LANGUAGE sql"
        " SECURITY DEFINER AS 'SELECT 1';"
        " ALTER FUNCTION controlplane.planted() OWNER TO purser_cp_definer",
        "function controlplane.planted: SECURITY DEFINER without a fixed search_path",
    )
