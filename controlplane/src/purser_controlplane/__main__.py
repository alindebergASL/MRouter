"""Command line: python -m purser_controlplane <command>."""

import argparse
import logging
import os
import sys
import uuid
from pathlib import Path

from purser_controlplane import logs

log = logging.getLogger("purser.cli")


def _alembic_config(owner_url: str):  # type: ignore[no-untyped-def]
    from alembic.config import Config

    here = Path(__file__).resolve()
    candidates = [
        os.environ.get("PURSER_ALEMBIC_INI"),
        here.parents[2] / "alembic.ini",
        here.parents[1] / "alembic.ini",
    ]
    ini = next(Path(c) for c in candidates if c and Path(c).is_file())
    config = Config(str(ini))
    config.attributes["url"] = owner_url
    config.attributes["configure_logging"] = False
    return config


def _owner_url() -> str:
    from purser_controlplane.settings import get_settings

    url = get_settings().db_owner_url
    if url is None:
        sys.exit("PURSER_DB_OWNER_URL is not set: this command runs as the schema owner role")
    return url.get_secret_value()


def cmd_serve(args: argparse.Namespace) -> None:
    import uvicorn

    uvicorn.run(
        "purser_controlplane.app:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        proxy_headers=False,
        server_header=False,
        date_header=False,
        log_config=None,
    )


def cmd_migrate(args: argparse.Namespace) -> None:
    from alembic import command

    command.upgrade(_alembic_config(_owner_url()), args.revision)


def cmd_bootstrap_operator(args: argparse.Namespace) -> None:
    """Idempotent: make a Keycloak subject a platform operator."""
    from sqlalchemy.dialects.postgresql import insert

    from purser_controlplane import audit
    from purser_controlplane.db import Database
    from purser_controlplane.keycloak import KeycloakAdmin
    from purser_controlplane.models import PlatformOperator
    from purser_controlplane.settings import get_settings

    sub = uuid.UUID(args.sub)
    database = Database(_owner_url(), pool_size=1)
    with database.session() as session:
        inserted = session.execute(
            insert(PlatformOperator)
            .values(keycloak_sub=sub)
            .on_conflict_do_nothing()
            .returning(PlatformOperator.keycloak_sub)
        ).first()
        if inserted is not None:
            audit.record(
                session,
                actor_kind="system",
                actor_sub=None,
                action="operator.bootstrap",
                target_type="operator",
                target_id=sub,
                org_id=None,
            )
        session.commit()
    log.info("operator %s", "added" if inserted else "already present", extra={"sub": str(sub)})

    keycloak = KeycloakAdmin.from_settings(get_settings())
    if keycloak is None:
        log.warning(
            "Keycloak admin API not configured; the operator's second factor was not checked"
        )
        return
    if keycloak.second_factor_configured(sub):
        log.info("operator has a second factor")
    else:
        keycloak.require_action(sub, "CONFIGURE_TOTP")
        log.info("operator has no second factor; TOTP setup is required at next sign-in")


def cmd_dev_seed(args: argparse.Namespace) -> None:
    from purser_controlplane.db import Database
    from purser_controlplane.seed import seed_dev
    from purser_controlplane.settings import get_settings

    if get_settings().env != "dev":
        sys.exit("dev-seed runs only with PURSER_ENV=dev")
    database = Database(_owner_url(), pool_size=1)
    with database.session() as session:
        seed_dev(session)
        session.commit()
    log.info("dev seed applied")


def cmd_sweep(args: argparse.Namespace) -> None:
    from purser_controlplane.db import Database
    from purser_controlplane.keycloak import KeycloakAdmin
    from purser_controlplane.settings import get_settings
    from purser_controlplane.sweeper import run_forever, sweep_once

    settings = get_settings()
    if settings.db_sweeper_url is None:
        sys.exit("PURSER_DB_SWEEPER_URL is not set")
    keycloak = KeycloakAdmin.from_settings(settings)
    if keycloak is None:
        sys.exit("the Keycloak admin API is not configured")
    database = Database(settings.db_sweeper_url.get_secret_value(), pool_size=1)
    if args.once:
        result = sweep_once(database, keycloak, older_than_seconds=settings.sweep_after_seconds)
        log.info(
            "sweep",
            extra={
                "finished": len(result.finished),
                "rolled_back": len(result.rolled_back),
                "skipped": len(result.skipped),
            },
        )
        return
    run_forever(
        database,
        keycloak,
        older_than_seconds=settings.sweep_after_seconds,
        interval=settings.sweep_interval_seconds,
    )


def cmd_export_openapi(args: argparse.Namespace) -> None:
    """Write the admin API's OpenAPI 3.1 document (A1). Needs no settings or services."""
    from purser_controlplane.openapi import render

    document = render()
    if args.output == "-":
        sys.stdout.write(document)
    else:
        Path(args.output).write_text(document, encoding="utf-8")


def main(argv: list[str] | None = None) -> None:
    logs.configure()
    parser = argparse.ArgumentParser(prog="purser_controlplane")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="run the API")
    serve.add_argument("--host", default=os.environ.get("PURSER_HOST", "0.0.0.0"))  # noqa: S104 - in a container
    serve.add_argument("--port", type=int, default=int(os.environ.get("PURSER_PORT", "8080")))
    serve.set_defaults(func=cmd_serve)
    migrate = sub.add_parser("migrate", help="migrate the schema (owner role)")
    migrate.add_argument("revision", nargs="?", default="head")
    migrate.set_defaults(func=cmd_migrate)
    boot = sub.add_parser(
        "bootstrap-operator", help="make a Keycloak subject a platform operator (idempotent)"
    )
    boot.add_argument("--sub", required=True, help="the user's Keycloak subject (UUID)")
    boot.set_defaults(func=cmd_bootstrap_operator)
    sub.add_parser(
        "dev-seed", help="seed the dev realm's org and users (PURSER_ENV=dev only)"
    ).set_defaults(func=cmd_dev_seed)
    export = sub.add_parser("export-openapi", help="write the OpenAPI 3.1 document (A1)")
    export.add_argument("--output", default="-", help="file to write (default: stdout)")
    export.set_defaults(func=cmd_export_openapi)
    sweep = sub.add_parser("sweep", help="finish or roll back pending rows")
    sweep.add_argument("--once", action="store_true")
    sweep.set_defaults(func=cmd_sweep)
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
