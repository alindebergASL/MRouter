"""Row-level security (A5, defense in depth), with hand-written SQL as the API's role.

These bypass the API entirely: they show what the database itself allows the
app role to see and write, whatever the application code does.
"""

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import Connection, create_engine, text
from sqlalchemy.exc import DBAPIError

from tests.conftest import TestDatabase
from tests.integration.helpers import OrgFixture, make_member, make_org

ORG_SCOPED = ("orgs", "workspaces", "teams", "users", "memberships")


@pytest.fixture(scope="module")
def two_orgs(seeded: TestDatabase, owner_engine: Any) -> tuple[OrgFixture, OrgFixture]:
    a, b = make_org(owner_engine, name="A"), make_org(owner_engine, name="B")
    make_member(owner_engine, a)
    make_member(owner_engine, b)
    return a, b


@pytest.fixture
def app_conn(test_db: TestDatabase) -> Iterator[Connection]:
    engine = create_engine(test_db.app_url)
    with engine.connect() as connection:
        yield connection
        connection.rollback()
    engine.dispose()


def _set(conn: Connection, name: str, value: object) -> None:
    conn.execute(text("SELECT set_config(:n, :v, true)"), {"n": name, "v": str(value)})


def _count(conn: Connection, sql: str, **params: object) -> int:
    return int(conn.execute(text(sql), params).scalar_one())


@pytest.mark.parametrize("table", ORG_SCOPED)
def test_no_org_set_sees_zero_rows(app_conn: Connection, two_orgs: object, table: str) -> None:
    assert _count(app_conn, f"SELECT count(*) FROM controlplane.{table}") == 0


@pytest.mark.parametrize("table", ORG_SCOPED)
def test_org_a_cannot_read_org_b_by_hand(
    app_conn: Connection, two_orgs: tuple[OrgFixture, OrgFixture], table: str
) -> None:
    a, b = two_orgs
    _set(app_conn, "purser.org_id", a.id)
    column = "id" if table == "orgs" else "org_id"
    sql = f"SELECT count(*) FROM controlplane.{table} WHERE {column} = :b"
    assert _count(app_conn, sql, b=b.id) == 0
    everything = f"SELECT count(DISTINCT {column}) FROM controlplane.{table}"
    assert _count(app_conn, everything) == 1


def test_org_a_cannot_write_into_org_b(
    app_conn: Connection, two_orgs: tuple[OrgFixture, OrgFixture]
) -> None:
    a, b = two_orgs
    _set(app_conn, "purser.org_id", a.id)
    with pytest.raises(DBAPIError, match="row-level security"):
        app_conn.execute(
            text("INSERT INTO controlplane.workspaces (id, org_id, name) VALUES (:id, :org, 'x')"),
            {"id": uuid.uuid4(), "org": b.id},
        )


def test_org_a_cannot_move_a_row_to_org_b(
    app_conn: Connection, two_orgs: tuple[OrgFixture, OrgFixture]
) -> None:
    a, b = two_orgs
    _set(app_conn, "purser.org_id", a.id)
    with pytest.raises(DBAPIError, match="row-level security"):
        app_conn.execute(text("UPDATE controlplane.users SET org_id = :b"), {"b": b.id})


def test_a_forged_operator_setting_grants_nothing(app_conn: Connection, two_orgs: object) -> None:
    _set(app_conn, "purser.operator_sub", uuid.uuid4())
    assert _count(app_conn, "SELECT count(*) FROM controlplane.orgs") == 0


def test_the_app_role_cannot_list_operators(app_conn: Connection) -> None:
    from purser_controlplane.seed import ALICE_SUB

    with pytest.raises(DBAPIError, match="permission denied"):
        app_conn.execute(text("SELECT keycloak_sub FROM controlplane.platform_operators"))
    app_conn.rollback()
    # It can only ask about one subject at a time.
    ask = text("SELECT controlplane.is_operator(:s)")
    assert app_conn.execute(ask, {"s": ALICE_SUB}).scalar_one() is True
    assert app_conn.execute(ask, {"s": uuid.uuid4()}).scalar_one() is False


def test_org_context_does_not_outlive_its_transaction_on_a_pooled_connection(
    test_db: TestDatabase, two_orgs: tuple[OrgFixture, OrgFixture]
) -> None:
    from purser_controlplane.db import Database, set_context
    from purser_controlplane.models import User

    a, _ = two_orgs
    database = Database(test_db.app_url, pool_size=1)
    try:
        with database.session() as session:
            set_context(session, org_id=a.id)
            assert session.query(User).count() >= 1
            session.commit()
            # Re-applied after a commit, in the same session.
            assert session.query(User).count() >= 1
        with database.session() as session:  # same pooled connection, no context
            assert session.query(User).count() == 0
            assert session.execute(
                text("SELECT current_setting('purser.org_id', true)")
            ).scalar() in {None, ""}
    finally:
        database.dispose()


def test_a_real_operator_sees_every_org(app_conn: Connection, two_orgs: object) -> None:
    from purser_controlplane.seed import ALICE_SUB

    _set(app_conn, "purser.operator_sub", ALICE_SUB)
    assert _count(app_conn, "SELECT count(*) FROM controlplane.orgs") >= 3


def test_the_app_role_cannot_delete_or_write_operators(
    app_conn: Connection, two_orgs: tuple[OrgFixture, OrgFixture]
) -> None:
    a, _ = two_orgs
    _set(app_conn, "purser.org_id", a.id)
    with pytest.raises(DBAPIError, match="permission denied"):
        app_conn.execute(text("DELETE FROM controlplane.workspaces"))
    app_conn.rollback()
    with pytest.raises(DBAPIError, match="permission denied"):
        app_conn.execute(
            text(
                "INSERT INTO controlplane.platform_operators (keycloak_sub) VALUES (gen_random_uuid())"
            )
        )


def test_app_role_has_no_bypass_and_owns_nothing(app_conn: Connection) -> None:
    row = app_conn.execute(
        text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
    ).one()
    assert row == (False, False)
    owned = _count(
        app_conn,
        "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
        " WHERE n.nspname = 'controlplane' AND pg_get_userbyid(c.relowner) = current_user",
    )
    assert owned == 0


def test_every_org_scoped_table_has_rls(app_conn: Connection) -> None:
    """Any new table with an org_id column must carry RLS, or this fails."""
    rows = app_conn.execute(
        text(
            "SELECT c.relname, c.relrowsecurity,"
            " EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = c.oid AND a.attname = 'org_id')"
            " FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
            " WHERE n.nspname = 'controlplane' AND c.relkind = 'r'"
        )
    ).all()
    unprotected = [name for name, rls, has_org in rows if (has_org or name == "orgs") and not rls]
    assert unprotected == []
    assert {name for name, rls, _ in rows if rls} >= {*ORG_SCOPED, "audit_events"}


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
