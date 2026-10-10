"""The A5 part 3 catalog checks: what the database must look like, read from the catalog.

CI fails (spec 4, A5 part 3) if:
- a table that is not on the reviewed allowlist lacks forced row-level security
  with a policy for reads and writes keyed exactly on the request's org;
- an org-data table other than the ledger's is on the allowlist;
- a request-path or cross-org role owns anything, inherits from another role,
  or has BYPASSRLS (or is a superuser);
- a SECURITY DEFINER function lacks a fixed search_path (pg_temp last) or is
  owned by a role that can log in (or that has other powers), or PUBLIC may
  execute it.

And, beyond the spec's list, because each would undo it:
- a permissive policy reaching the request path's role is anything but the
  org key; one reaching the operator role isn't gated on a registered
  operator; one reaching the sweeper isn't in the reviewed set;
- the org key function itself changes;
- a view (unless security_invoker), materialized view, or foreign table
  exists in any non-system schema.

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
    tables = ("orgs", "workspaces", "teams", "users", "memberships", "audit_events")
    assert set(org_data_tables(admin_conn)) == {f"controlplane.{t}" for t in tables}


def test_the_check_reports_what_revision_0001_lacked() -> None:
    """Before spec 0.6's alignment (0002), the check fails for the reasons 0002 fixes."""
    db = create_test_database(migrate=False)
    try:
        migrate_to(db, "0001")
        for conn in _admin(db):
            violations = rls_violations(conn)
    finally:
        drop_test_database(db)
    for expected in (
        "controlplane.orgs: row-level security is not enabled and forced",
        "controlplane.orgs.operator_select: lets the request path's role past its org",
        "function controlplane.is_operator: SECURITY DEFINER owned by a role that can log in",
        "function controlplane.is_operator: SECURITY DEFINER without a fixed search_path",
    ):
        assert expected in violations


def test_every_allowlist_entry_has_a_reason() -> None:
    assert all(reason.strip() for reason in RLS_ALLOWLIST.values())


# -- negative tests: each planted violation is reported ---------------------------


def _flags(conn: Connection, ddl: str, expected: str, **kwargs: Any) -> None:
    for statement in ddl.split(";"):
        if statement.strip():
            conn.execute(text(statement))
    violations = rls_violations(conn, **kwargs)
    assert any(expected in v for v in violations), violations


ORG_KEY = "controlplane.current_org_id()"
VALID = "controlplane.current_operator_is_valid()"
DEFINER = (
    "CREATE FUNCTION controlplane.planted() RETURNS int LANGUAGE sql SECURITY DEFINER {} AS 'SELECT 1';"
    " REVOKE ALL ON FUNCTION controlplane.planted() FROM PUBLIC;"
    " ALTER FUNCTION controlplane.planted() OWNER TO purser_cp_definer"
)
SAFE_PATH = "SET search_path = pg_catalog, pg_temp"

PLANTED = {
    "not forced": (
        "ALTER TABLE controlplane.workspaces NO FORCE ROW LEVEL SECURITY",
        "controlplane.workspaces: row-level security is not enabled and forced",
    ),
    "new org-data table without RLS": (
        "CREATE TABLE controlplane.widgets (id uuid PRIMARY KEY, org_id uuid NOT NULL)",
        "controlplane.widgets: row-level security is not enabled and forced",
    ),
    "table in another schema": (
        "CREATE SCHEMA planted; CREATE TABLE planted.spend (id uuid, organization_id uuid)",
        "planted.spend: row-level security is not enabled and forced",
    ),
    "forced but no org-keyed policy": (
        "CREATE TABLE controlplane.widgets (id uuid PRIMARY KEY, org_id uuid NOT NULL);"
        " ALTER TABLE controlplane.widgets ENABLE ROW LEVEL SECURITY;"
        " ALTER TABLE controlplane.widgets FORCE ROW LEVEL SECURITY",
        "controlplane.widgets: no policy keys the request path's reads and writes on its org",
    ),
    "a key that only mentions the org": (
        "CREATE TABLE controlplane.widgets (id uuid PRIMARY KEY, org_id uuid NOT NULL);"
        " ALTER TABLE controlplane.widgets ENABLE ROW LEVEL SECURITY;"
        " ALTER TABLE controlplane.widgets FORCE ROW LEVEL SECURITY;"
        f" CREATE POLICY org_isolation ON controlplane.widgets TO purser_cp_app"
        f" USING ({ORG_KEY} IS NOT NULL) WITH CHECK ({ORG_KEY} IS NOT NULL)",
        "controlplane.widgets: no policy keys the request path's reads and writes on its org",
    ),
    "the org key ORed with operator reach": (
        f"CREATE POLICY leak ON controlplane.teams TO purser_cp_app"
        f" USING (org_id = {ORG_KEY} OR {VALID})",
        "controlplane.teams.leak: lets the request path's role past its org",
    ),
    "the org key ORed with true": (
        f"CREATE POLICY leak ON controlplane.teams TO purser_cp_app USING (org_id = {ORG_KEY} OR true)",
        "controlplane.teams.leak: lets the request path's role past its org",
    ),
    "a public policy": (
        "CREATE POLICY leak ON controlplane.teams FOR SELECT USING (true)",
        "controlplane.teams.leak: lets the request path's role past its org",
    ),
    "the org key function replaced": (
        "CREATE OR REPLACE FUNCTION controlplane.current_org_id() RETURNS uuid"
        " LANGUAGE sql STABLE AS 'SELECT NULL::uuid'",
        "function controlplane.current_org_id(): missing or changed",
    ),
    "an operator policy not gated": (
        "CREATE POLICY leak ON controlplane.memberships FOR SELECT TO purser_cp_operator USING (true)",
        "controlplane.memberships.leak: an operator-role policy not gated on an operator",
    ),
    "an operator policy ORing past the gate": (
        f"CREATE POLICY leak ON controlplane.orgs FOR SELECT TO purser_cp_operator"
        f" USING ({VALID} OR true)",
        "controlplane.orgs.leak: an operator-role policy not gated on an operator",
    ),
    "an unreviewed sweeper policy": (
        "CREATE POLICY sweeper_more ON controlplane.memberships FOR SELECT TO purser_cp_sweeper"
        " USING (true)",
        "controlplane.memberships.sweeper_more: a sweeper policy not in the reviewed set",
    ),
    "a view running as its owner": (
        "CREATE VIEW controlplane.everyone AS SELECT id FROM controlplane.users",
        "controlplane.everyone: a view or foreign table that bypasses row-level security",
    ),
    "a materialized view": (
        "CREATE MATERIALIZED VIEW controlplane.everyone AS SELECT id FROM controlplane.users",
        "controlplane.everyone: a view or foreign table that bypasses row-level security",
    ),
    "BYPASSRLS on the request path": (
        "ALTER ROLE purser_cp_app BYPASSRLS",
        "role purser_cp_app: superuser or BYPASSRLS",
    ),
    "a cross-org role owning a table": (
        "ALTER TABLE controlplane.teams OWNER TO purser_cp_operator",
        "role purser_cp_operator: owns",
    ),
    "a cross-org role inheriting the owner": (
        "GRANT purser_cp_owner TO purser_cp_sweeper",
        "role purser_cp_sweeper: is a member of another role",
    ),
    "the definer inheriting the owner": (
        "GRANT purser_cp_sweeper TO purser_cp_definer",
        "role purser_cp_definer: is a member of another role",
    ),
    "a definer function owned by a login role": (
        f"CREATE FUNCTION controlplane.planted() RETURNS int LANGUAGE sql SECURITY DEFINER"
        f" {SAFE_PATH} AS 'SELECT 1'; REVOKE ALL ON FUNCTION controlplane.planted() FROM PUBLIC",
        "function controlplane.planted: SECURITY DEFINER owned by a role that can log in",
    ),
    "a definer function without a search_path": (
        DEFINER.format(""),
        "function controlplane.planted: SECURITY DEFINER without a fixed search_path",
    ),
    "a definer function searching pg_temp first": (
        DEFINER.format("SET search_path = pg_temp, pg_catalog"),
        "function controlplane.planted: SECURITY DEFINER without a fixed search_path",
    ),
    "a definer function PUBLIC may execute": (
        DEFINER.format(SAFE_PATH) + "; GRANT EXECUTE ON FUNCTION controlplane.planted() TO PUBLIC",
        "function controlplane.planted: SECURITY DEFINER executable by PUBLIC",
    ),
}


@pytest.mark.parametrize("case", PLANTED)
def test_flags_a_planted_violation(admin_conn: Connection, case: str) -> None:
    ddl, expected = PLANTED[case]
    _flags(admin_conn, ddl, expected)


def test_flags_org_data_on_the_allowlist_found_through_a_foreign_key(
    admin_conn: Connection,
) -> None:
    # No org ID column: it is org data because it points at a workspace.
    _flags(
        admin_conn,
        "CREATE TABLE controlplane.notes"
        " (id uuid PRIMARY KEY, workspace_id uuid REFERENCES controlplane.workspaces (id))",
        "controlplane.notes: holds org data but is on the allowlist",
        allowlist={**RLS_ALLOWLIST, "controlplane.notes": "planted"},
    )


def test_flags_org_data_on_the_allowlist_found_by_its_column_name(
    admin_conn: Connection,
) -> None:
    _flags(
        admin_conn,
        "CREATE TABLE controlplane.spend (id uuid PRIMARY KEY, organization_id uuid NOT NULL)",
        "controlplane.spend: holds org data but is on the allowlist",
        allowlist={**RLS_ALLOWLIST, "controlplane.spend": "planted"},
    )
