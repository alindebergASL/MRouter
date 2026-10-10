"""Alembic environment: runs as the owner role, inside the controlplane schema."""

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from purser_controlplane.models import SCHEMA, Base

config = context.config
if config.config_file_name is not None and config.attributes.get("configure_logging", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _url() -> str:
    url = config.attributes.get("url") or os.environ.get("PURSER_DB_OWNER_URL")
    if not url:
        raise RuntimeError(
            "PURSER_DB_OWNER_URL is not set; migrations run as the schema owner role"
        )
    return str(url)


def _include_name(name: str | None, type_: str, parent_names: object) -> bool:
    if type_ == "schema":
        return name == SCHEMA
    return True


def run_migrations_online() -> None:
    engine = create_engine(_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table_schema=SCHEMA,
            include_schemas=True,
            include_name=_include_name,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    raise RuntimeError("offline migrations are not supported")
run_migrations_online()
