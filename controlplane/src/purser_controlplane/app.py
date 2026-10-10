"""The FastAPI application."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute, iter_route_contexts
from sqlalchemy.exc import IntegrityError

from purser_controlplane.api import health, operator, orgs
from purser_controlplane.api.deps import PUBLIC_PATHS, PUBLIC_ROUTES, route_permission
from purser_controlplane.auth.oidc import AuthUnavailableError, OIDCVerifier
from purser_controlplane.db import Database
from purser_controlplane.keycloak import KeycloakAdmin
from purser_controlplane.settings import Settings, get_settings

log = logging.getLogger("purser")

API_TITLE = "Purser admin API"
API_VERSION = "0.1.0"


@dataclass(frozen=True)
class RouteInfo:
    methods: frozenset[str]
    path: str
    permission: str | None


def api_routes(app: FastAPI) -> list[RouteInfo]:
    """Every API route with its full path, walking included routers.

    FastAPI 0.141 keeps included routers nested, so app.routes alone misses
    them; iter_route_contexts is the walk FastAPI's own OpenAPI export uses.
    """
    routes = []
    for context in iter_route_contexts(app.routes):
        if isinstance(context.original_route, APIRoute):
            dependant = getattr(context, "dependant", None) or context.original_route.dependant
            routes.append(
                RouteInfo(
                    frozenset(context.methods or ()),
                    context.path_format or context.original_route.path,
                    route_permission(dependant),
                )
            )
    return routes


def check_deny_by_default(app: FastAPI) -> None:
    """Refuse to run if any route lacks a permission and isn't explicitly public."""
    routes = api_routes(app)
    if not any(r.path not in PUBLIC_PATHS for r in routes):
        # Fail closed if the walk stops seeing routes (e.g. a framework change).
        raise RuntimeError("deny by default: no API routes found to check")
    unguarded = [
        f"{method} {r.path}"
        for r in routes
        for method in sorted(r.methods)
        if r.permission is None and (method, r.path) not in PUBLIC_ROUTES
    ]
    if unguarded:
        raise RuntimeError(f"routes without a permission (deny by default): {unguarded}")


async def _integrity_error(request: Request, exc: Exception) -> JSONResponse:
    # Postgres's DETAIL line can quote values (an email in a unique key), so
    # log the constraint's name only.
    constraint = getattr(getattr(getattr(exc, "orig", None), "diag", None), "constraint_name", None)
    log.info("integrity error", extra={"constraint": constraint})
    return JSONResponse({"detail": "Conflict"}, status_code=409)


def build_app() -> FastAPI:
    """The routes alone, with no settings or connections (for the OpenAPI export)."""
    app = FastAPI(
        title=API_TITLE,
        version=API_VERSION,
        description="Purser's control-plane admin API: every console action is here (A1).",
        # The document is exported offline (controlplane/openapi/); nothing is
        # served unauthenticated beyond health and readiness.
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.add_exception_handler(IntegrityError, _integrity_error)
    app.include_router(health.router)
    app.include_router(operator.router)
    app.include_router(orgs.router)
    check_deny_by_default(app)
    return app


def create_app(
    settings: Settings | None = None,
    *,
    verifier: OIDCVerifier | None = None,
    keycloak: KeycloakAdmin | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    if settings.db_operator_url is None:
        # Operator routes never fall back to the request path's role.
        raise RuntimeError("PURSER_DB_OPERATOR_URL is not set")
    app = build_app()
    app.state.settings = settings
    app.state.db = Database(settings.db_url.get_secret_value())
    app.state.operator_db = Database(settings.db_operator_url.get_secret_value(), pool_size=2)
    app.state.verifier = verifier or OIDCVerifier(settings)
    app.state.keycloak = keycloak if keycloak is not None else KeycloakAdmin.from_settings(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            app.state.verifier.load()
        except AuthUnavailableError:
            log.warning(
                "auth service not reachable at startup; /readyz reports not_ready until it is"
            )
        yield
        app.state.db.dispose()
        app.state.operator_db.dispose()
        if app.state.keycloak is not None:
            app.state.keycloak.close()

    app.router.lifespan_context = lifespan
    return app
