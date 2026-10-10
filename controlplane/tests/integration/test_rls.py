"""Row-level security (A5 part 3), with hand-written SQL as each database role.

These bypass the API entirely: they show what the database itself allows each
role to see and write, whatever the application code does. The request
path's role is purser_cp_app; operator work and the sweeper are the cross-org
roles (spec 4), each with its own narrow policies.
"""

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pytest
from sqlalchemy import Connection, create_engine, text
from sqlalchemy.exc import DBAPIError, ProgrammingError

from tests.conftest import TestDatabase
from tests.integration.helpers import Member, OrgFixture, make_member, make_org
from tests.integration.rls_catalog import RLS_ALLOWLIST, org_data_tables

# Every table that holds org data and is not on the allowlist, and the column
# holding each row's org. test_the_table_list_matches_the_catalog keeps it whole.
ORG_COLUMN = {
    "orgs": "id",
    "workspaces": "org_id",
    "teams": "org_id",
    "users": "org_id",
    "memberships": "org_id",
    "audit_events": "org_id",
}
ORG_TABLES = tuple(ORG_COLUMN)


@dataclass(frozen=True)
class TwoOrgs:
    a: OrgFixture
    b: OrgFixture
    a_member: Member
    b_member: Member


@pytest.fixture(scope="module")
def two_orgs(seeded: TestDatabase, owner_engine: Any) -> TwoOrgs:
    """Two orgs with a row in every org-data table, written by the owner role."""
    a, b = make_org(owner_engine, name="A"), make_org(owner_engine, name="B")
    a_member, b_member = make_member(owner_engine, a), make_member(owner_engine, b)
    with owner_engine.begin() as conn:
        for org in (a, b):
            conn.execute(
                text(
                    "INSERT INTO controlplane.audit_events"
                    " (id, org_id, actor_kind, action, target_type)"
                    " VALUES (:id, :org, 'user', 'test.fixture', 'org')"
                ),
                {"id": uuid.uuid4(), "org": org.id},
            )
    return TwoOrgs(a, b, a_member, b_member)


def _connect(url: str) -> Iterator[Connection]:
    engine = create_engine(url)
    with engine.connect() as connection:
        yield connection
        connection.rollback()
    engine.dispose()


@pytest.fixture
def app_conn(test_db: TestDatabase) -> Iterator[Connection]:
    yield from _connect(test_db.app_url)


@pytest.fixture
def operator_conn(test_db: TestDatabase) -> Iterator[Connection]:
    yield from _connect(test_db.operator_url)


def _set(conn: Connection, name: str, value: object) -> None:
    conn.execute(text("SELECT set_config(:n, :v, true)"), {"n": name, "v": str(value)})


def _count(conn: Connection, sql: str, **params: object) -> int:
    return int(conn.execute(text(sql), params).scalar_one())


def _orgs_touched(conn: Connection, sql: str) -> set[uuid.UUID] | None:
    """The orgs of the rows a statement read or changed; None if it was denied outright.

    Runs in a savepoint, so a denial doesn't end the test's transaction.
    """
    savepoint = conn.begin_nested()
    try:
        return set(conn.execute(text(sql)).scalars())
    except ProgrammingError as exc:
        if "permission denied" not in str(exc):
            raise
        return None
    finally:
        savepoint.rollback()


def test_the_table_list_matches_the_catalog(test_db: TestDatabase) -> None:
    for conn in _connect(test_db.admin_url):
        tables = {f"controlplane.{t}" for t in ORG_TABLES}
        assert tables == set(org_data_tables(conn)) - set(RLS_ALLOWLIST)


# -- A5 part 3, as the request path's role --------------------------------------


@pytest.mark.parametrize("table", ORG_TABLES)
def test_with_org_a_set_unfiltered_statements_reach_no_org_b_row(
    app_conn: Connection, two_orgs: TwoOrgs, table: str
) -> None:
    a = two_orgs.a
    column = ORG_COLUMN[table]
    _set(app_conn, "purser.org_id", a.id)
    t = f"controlplane.{table}"
    statements = {
        "SELECT": f"SELECT {column} FROM {t}",
        "UPDATE": f"UPDATE {t} SET {column} = {column} RETURNING {column}",
        "DELETE": f"DELETE FROM {t} RETURNING {column}",
    }
    for command, sql in statements.items():
        touched = _orgs_touched(app_conn, sql)
        # Denied outright (the role has no such grant) reaches nothing either.
        assert touched is None or touched <= {a.id}, (command, touched)
    if table != "audit_events":  # the API only ever inserts audit rows
        # The policy filters to org A; it doesn't just deny.
        assert _orgs_touched(app_conn, statements["SELECT"]) == {a.id}


@pytest.mark.parametrize("table", ORG_TABLES)
def test_the_org_key_alone_confines_every_command(
    test_db: TestDatabase, two_orgs: TwoOrgs, table: str
) -> None:
    """The policy, not a missing grant: with every privilege granted, org A still reaches only A.

    The app role lacks most UPDATE and every DELETE grant, so the test above
    passes on "permission denied" for those. Here the grants are added inside a
    transaction that is rolled back, and the same statements run as the role.
    """
    a = two_orgs.a
    column = ORG_COLUMN[table]
    for conn in _connect(test_db.admin_url):
        conn.execute(text(f"GRANT ALL ON controlplane.{table} TO purser_cp_app"))
        conn.execute(text("SET LOCAL ROLE purser_cp_app"))
        _set(conn, "purser.org_id", a.id)
        t = f"controlplane.{table}"
        for sql in (
            f"SELECT {column} FROM {t}",
            f"UPDATE {t} SET {column} = {column} RETURNING {column}",
        ):
            assert _orgs_touched(conn, sql) == {a.id}, sql


def test_the_org_key_alone_confines_deletes(test_db: TestDatabase, two_orgs: TwoOrgs) -> None:
    """As above, for DELETE: leaf tables first, so org A's own foreign keys don't object."""
    a = two_orgs.a
    leaf_first = ("audit_events", "memberships", "teams", "users", "workspaces", "orgs")
    assert set(leaf_first) == set(ORG_TABLES)
    for conn in _connect(test_db.admin_url):
        for table in leaf_first:
            conn.execute(text(f"GRANT ALL ON controlplane.{table} TO purser_cp_app"))
        conn.execute(text("SET LOCAL ROLE purser_cp_app"))
        _set(conn, "purser.org_id", a.id)
        for table in leaf_first:
            column = ORG_COLUMN[table]
            deleted = set(
                conn.execute(text(f"DELETE FROM controlplane.{table} RETURNING {column}")).scalars()
            )
            assert deleted == {a.id}, table


def _insert_tagged_with(org: OrgFixture, member: Member) -> dict[str, tuple[str, dict[str, Any]]]:
    new = uuid.uuid4
    return {
        # An orgs row is its own org: any id but the current org's is another org.
        "orgs": (
            "INSERT INTO controlplane.orgs (id, name, status) VALUES (:id, 'x', 'pending')",
            {"id": new()},
        ),
        "workspaces": (
            "INSERT INTO controlplane.workspaces (id, org_id, name) VALUES (:id, :org, 'x')",
            {"id": new(), "org": org.id},
        ),
        "teams": (
            "INSERT INTO controlplane.teams (id, org_id, workspace_id, name)"
            " VALUES (:id, :org, :ws, 'x')",
            {"id": new(), "org": org.id, "ws": org.workspace_id},
        ),
        "users": (
            "INSERT INTO controlplane.users (id, org_id, email, display_name, status)"
            " VALUES (:id, :org, 'x@test.example', 'x', 'pending')",
            {"id": new(), "org": org.id},
        ),
        "memberships": (
            "INSERT INTO controlplane.memberships (id, org_id, user_id, role, scope_type)"
            " VALUES (:id, :org, :user, 'viewer', 'org')",
            {"id": new(), "org": org.id, "user": member.user_id},
        ),
        "audit_events": (
            "INSERT INTO controlplane.audit_events (id, org_id, actor_kind, action, target_type)"
            " VALUES (:id, :org, 'user', 'x', 'org')",
            {"id": new(), "org": org.id},
        ),
    }


@pytest.mark.parametrize("table", ORG_TABLES)
def test_with_org_a_set_an_insert_tagged_with_org_b_fails(
    app_conn: Connection, two_orgs: TwoOrgs, table: str
) -> None:
    _set(app_conn, "purser.org_id", two_orgs.a.id)
    sql, params = _insert_tagged_with(two_orgs.b, two_orgs.b_member)[table]
    # orgs: refused before the policy, since the request path may not create orgs at all.
    with pytest.raises(DBAPIError, match=r"row-level security|permission denied for table orgs"):
        app_conn.execute(text(sql), params)


@pytest.mark.parametrize("table", ORG_TABLES)
def test_no_org_set_reads_no_rows(app_conn: Connection, two_orgs: TwoOrgs, table: str) -> None:
    touched = _orgs_touched(app_conn, f"SELECT {ORG_COLUMN[table]} FROM controlplane.{table}")
    assert touched is None or touched == set()


def test_org_a_cannot_move_a_row_to_org_b(app_conn: Connection, two_orgs: TwoOrgs) -> None:
    _set(app_conn, "purser.org_id", two_orgs.a.id)
    with pytest.raises(DBAPIError, match="row-level security"):
        app_conn.execute(text("UPDATE controlplane.users SET org_id = :b"), {"b": two_orgs.b.id})


def test_the_request_path_cannot_create_or_change_orgs(
    app_conn: Connection, two_orgs: TwoOrgs
) -> None:
    """Only operators create orgs (spec 4): the app role can't, even for an org it sets itself."""
    forged = uuid.uuid4()
    _set(app_conn, "purser.org_id", forged)
    with pytest.raises(DBAPIError, match="permission denied"):
        app_conn.execute(
            text("INSERT INTO controlplane.orgs (id, name, status) VALUES (:o, 'x', 'pending')"),
            {"o": forged},
        )
    app_conn.rollback()
    _set(app_conn, "purser.org_id", two_orgs.a.id)
    with pytest.raises(DBAPIError, match="permission denied"):
        app_conn.execute(text("UPDATE controlplane.orgs SET name = 'renamed'"))


def test_the_request_path_has_no_operator_reach(app_conn: Connection, two_orgs: TwoOrgs) -> None:
    """Even a real operator's subject grants the app role nothing (spec 4)."""
    from purser_controlplane.seed import ALICE_SUB

    _set(app_conn, "purser.operator_sub", ALICE_SUB)
    for table in ORG_TABLES:
        touched = _orgs_touched(app_conn, f"SELECT {ORG_COLUMN[table]} FROM controlplane.{table}")
        assert touched is None or touched == set(), table
    with pytest.raises(DBAPIError, match="permission denied"):
        app_conn.execute(text("SELECT controlplane.is_operator(:s)"), {"s": ALICE_SUB})


def _pooled_context_does_not_leak(url: str, **context: uuid.UUID) -> None:
    from purser_controlplane.db import Database, set_context
    from purser_controlplane.models import Org

    backend = text("SELECT pg_backend_pid()")
    database = Database(url, pool_size=1)
    try:
        with database.session() as session:
            set_context(session, **context)
            assert session.query(Org).count() >= 1
            session.commit()
            # Re-applied after a commit, in the same session.
            assert session.query(Org).count() >= 1
            first = session.execute(backend).scalar()
        with database.session() as session:  # same pooled connection, no context
            assert session.execute(backend).scalar() == first
            assert session.query(Org).count() == 0
            for key in ("purser.org_id", "purser.operator_sub"):
                setting = text("SELECT current_setting(:k, true)")
                assert session.execute(setting, {"k": key}).scalar() in {None, ""}
    finally:
        database.dispose()


def test_org_context_does_not_outlive_its_transaction_on_a_pooled_connection(
    test_db: TestDatabase, two_orgs: TwoOrgs
) -> None:
    _pooled_context_does_not_leak(test_db.app_url, org_id=two_orgs.a.id)


def test_operator_context_does_not_outlive_its_transaction_on_a_pooled_connection(
    test_db: TestDatabase, two_orgs: TwoOrgs
) -> None:
    from purser_controlplane.seed import ALICE_SUB

    _pooled_context_does_not_leak(test_db.operator_url, operator_sub=ALICE_SUB)


def test_the_app_role_cannot_delete_or_write_operators(
    app_conn: Connection, two_orgs: TwoOrgs
) -> None:
    _set(app_conn, "purser.org_id", two_orgs.a.id)
    with pytest.raises(DBAPIError, match="permission denied"):
        app_conn.execute(text("DELETE FROM controlplane.workspaces"))
    app_conn.rollback()
    with pytest.raises(DBAPIError, match="permission denied"):
        app_conn.execute(
            text(
                "INSERT INTO controlplane.platform_operators (keycloak_sub) VALUES (gen_random_uuid())"
            )
        )


# -- the operator role (cross-org) ----------------------------------------------


def test_a_forged_operator_setting_grants_nothing(
    operator_conn: Connection, two_orgs: TwoOrgs
) -> None:
    _set(operator_conn, "purser.operator_sub", uuid.uuid4())
    assert _count(operator_conn, "SELECT count(*) FROM controlplane.orgs") == 0


def test_an_org_setting_grants_the_operator_role_nothing(
    operator_conn: Connection, two_orgs: TwoOrgs
) -> None:
    _set(operator_conn, "purser.org_id", two_orgs.a.id)
    assert _count(operator_conn, "SELECT count(*) FROM controlplane.orgs") == 0


def test_a_real_operator_sees_every_org(operator_conn: Connection, two_orgs: TwoOrgs) -> None:
    from purser_controlplane.seed import ALICE_SUB

    _set(operator_conn, "purser.operator_sub", ALICE_SUB)
    assert _count(operator_conn, "SELECT count(*) FROM controlplane.orgs") >= 3


def test_the_operator_role_reaches_only_what_operator_routes_need(
    operator_conn: Connection, two_orgs: TwoOrgs
) -> None:
    from purser_controlplane.seed import ALICE_SUB

    _set(operator_conn, "purser.operator_sub", ALICE_SUB)
    for table in ("workspaces", "teams"):
        assert _orgs_touched(operator_conn, f"SELECT org_id FROM controlplane.{table}") is None
    for table in ORG_TABLES:
        sql = f"DELETE FROM controlplane.{table} RETURNING {ORG_COLUMN[table]}"
        assert _orgs_touched(operator_conn, sql) is None, table


def test_the_operator_role_writes_only_what_org_creation_needs(
    operator_conn: Connection, two_orgs: TwoOrgs
) -> None:
    from purser_controlplane.seed import ALICE_SUB

    _set(operator_conn, "purser.operator_sub", ALICE_SUB)
    refused = {
        # An audit row naming someone else, or the system.
        "forged audit actor": (
            "INSERT INTO controlplane.audit_events (id, actor_kind, actor_sub, action, target_type)"
            " VALUES (gen_random_uuid(), 'user', gen_random_uuid(), 'x', 'org')",
            "row-level security",
        ),
        # An org born active, or with a Keycloak link it didn't provision.
        "active org": (
            "INSERT INTO controlplane.orgs (id, name, status) VALUES (gen_random_uuid(), 'x', 'active')",
            "row-level security",
        ),
        # Any grant but an org's first owner.
        "admin grant": (
            "INSERT INTO controlplane.memberships (id, org_id, user_id, role, scope_type)"
            f" VALUES (gen_random_uuid(), '{two_orgs.a.id}', '{two_orgs.a_member.user_id}',"
            " 'admin', 'org')",
            "row-level security",
        ),
        # Moving a user to another org, or renaming an org.
        "user org change": (
            "UPDATE controlplane.users SET org_id = gen_random_uuid()",
            "permission denied",
        ),
        "org rename": ("UPDATE controlplane.orgs SET name = 'renamed'", "permission denied"),
    }
    for sql, error in refused.values():
        savepoint = operator_conn.begin_nested()
        with pytest.raises(DBAPIError, match=error):
            operator_conn.execute(text(sql))
        savepoint.rollback()
    # Active rows can't be changed at all, even in the columns it may write.
    savepoint = operator_conn.begin_nested()
    changed = operator_conn.execute(
        text("UPDATE controlplane.orgs SET status = 'pending' WHERE id = :a"), {"a": two_orgs.a.id}
    ).rowcount
    savepoint.rollback()
    assert changed == 0


def test_the_operator_role_cannot_list_operators(operator_conn: Connection) -> None:
    from purser_controlplane.seed import ALICE_SUB

    with pytest.raises(DBAPIError, match="permission denied"):
        operator_conn.execute(text("SELECT keycloak_sub FROM controlplane.platform_operators"))
    operator_conn.rollback()
    # It can only ask about one subject at a time.
    ask = text("SELECT controlplane.is_operator(:s)")
    assert operator_conn.execute(ask, {"s": ALICE_SUB}).scalar_one() is True
    assert operator_conn.execute(ask, {"s": uuid.uuid4()}).scalar_one() is False


# -- the sweeper (cross-org) and the owner ----------------------------------------


def test_sweeper_changes_only_pending_rows_and_reads_no_emails(
    test_db: TestDatabase, owner_engine: Any
) -> None:
    org = make_org(owner_engine)
    pending = make_member(owner_engine, org, None, status="pending")
    active = make_member(owner_engine, org, None)
    engine = create_engine(test_db.sweeper_url)
    with engine.connect() as conn:
        with pytest.raises(DBAPIError, match="permission denied"):
            conn.execute(text("SELECT email FROM controlplane.users"))
        conn.rollback()
        deleted = conn.execute(
            text("DELETE FROM controlplane.users WHERE id IN (:p, :a)"),
            {"p": pending.user_id, "a": active.user_id},
        ).rowcount
        assert deleted == 1  # the active row is invisible to DELETE
        updated = conn.execute(
            text("UPDATE controlplane.orgs SET status = 'pending' WHERE id = :o"), {"o": org.id}
        ).rowcount
        assert updated == 0  # active orgs can't be changed
        conn.rollback()
    engine.dispose()


def test_the_sweeper_sees_nothing_it_has_no_policy_for(test_db: TestDatabase) -> None:
    for conn in _connect(test_db.sweeper_url):
        for table in ("workspaces", "teams", "memberships"):
            touched = _orgs_touched(conn, f"SELECT org_id FROM controlplane.{table}")
            assert touched is None or touched == set(), table


def test_forced_rls_binds_the_owner_through_its_stated_policy(test_db: TestDatabase) -> None:
    """FORCE binds the owner too; its access is the explicit owner_maintenance policy."""
    for conn in _connect(test_db.admin_url):
        row = conn.execute(
            text(
                "SELECT bool_and(c.relforcerowsecurity), count(p.polname)"
                " FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
                " LEFT JOIN pg_policy p ON p.polrelid = c.oid AND p.polname = 'owner_maintenance'"
                " WHERE n.nspname = 'controlplane' AND c.relname = ANY(:t)"
            ),
            {"t": list(ORG_TABLES)},
        ).one()
        assert row == (True, len(ORG_TABLES))
