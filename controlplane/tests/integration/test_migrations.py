"""Migrations up, down, and up again on a fresh database; models match migrations."""

from alembic import command
from sqlalchemy import create_engine, text

from tests.conftest import alembic_config, create_test_database, drop_test_database, migrate_to


def _objects(url: str) -> tuple[set[str], set[str]]:
    engine = create_engine(url)
    with engine.connect() as conn:
        tables = set(
            conn.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname = 'controlplane'")
            ).scalars()
        )
        functions = set(
            conn.execute(
                text(
                    "SELECT p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace"
                    " WHERE n.nspname = 'controlplane'"
                )
            ).scalars()
        )
    engine.dispose()
    return tables, functions


def test_upgrade_downgrade_upgrade() -> None:
    db = create_test_database(migrate=False)
    try:
        migrate_to(db, "head")
        tables, functions = _objects(db.owner_url)
        assert {
            "orgs",
            "workspaces",
            "teams",
            "users",
            "memberships",
            "platform_operators",
            "audit_events",
        } <= tables
        assert functions == {"current_org_id", "current_operator_is_valid"}

        migrate_to(db, "base")
        tables, functions = _objects(db.owner_url)
        assert tables == {"alembic_version"}
        assert functions == set()

        migrate_to(db, "head")
        # The models and the migrations describe the same schema.
        command.check(alembic_config(db))
    finally:
        drop_test_database(db)
