"""Request dependencies: authentication, the RLS-scoped session, and authorization.

Deny by default: every route must depend on require(...) or require_operator(),
or be listed in PUBLIC_ROUTES; app.create_app refuses to start otherwise.

Org access fails with one 404, whatever the reason (no such org, org pending,
org not in the token's organization claim, no active user row for the
caller), so org existence never leaks across orgs. A member who lacks a
permission inside their own org gets 403. Operator routes also answer 404 to
anyone who isn't an operator holding an MFA token.
"""

import logging
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from purser_controlplane.auth.oidc import AuthUnavailableError, OIDCVerifier, Principal, TokenError
from purser_controlplane.authz import Grant, Permission, allows
from purser_controlplane.db import Database, set_context
from purser_controlplane.keycloak import KeycloakAdmin
from purser_controlplane.models import (
    Membership,
    Org,
    OrgStatus,
    Role,
    ScopeType,
    User,
    UserStatus,
)
from purser_controlplane.settings import Settings

log = logging.getLogger("purser.authz")

# Public routes, by method and path: health and readiness only.
PUBLIC_ROUTES = frozenset({("GET", "/healthz"), ("GET", "/readyz")})
PUBLIC_PATHS = frozenset(path for _, path in PUBLIC_ROUTES)
PERMISSION_MARK = "__purser_permission__"

_bearer = HTTPBearer(
    auto_error=False, description="A Keycloak access token for the purser-admin-api audience."
)


class RolledBackError(Exception):
    """A pending row was rolled back by the sweeper before the request activated it."""


def _not_found() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_principal(
    request: Request, credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]
) -> Principal:
    if credentials is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    verifier: OIDCVerifier = request.app.state.verifier
    try:
        return verifier.verify(credentials.credentials)
    except TokenError as exc:
        log.info("token rejected", extra={"reason": exc.reason})
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Invalid token",
            headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
        ) from None
    except AuthUnavailableError:
        log.warning("token validation unavailable")
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Authentication unavailable"
        ) from None


def get_session(request: Request) -> Iterator[Session]:
    database: Database = request.app.state.db
    with database.session() as session:
        yield session


def get_keycloak(request: Request) -> KeycloakAdmin:
    keycloak: KeycloakAdmin | None = request.app.state.keycloak
    if keycloak is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Identity provisioning is not configured"
        )
    return keycloak


PrincipalDep = Annotated[Principal, Depends(get_principal)]
SessionDep = Annotated[Session, Depends(get_session)]
KeycloakDep = Annotated[KeycloakAdmin, Depends(get_keycloak)]


@dataclass(frozen=True)
class OrgContext:
    org_id: uuid.UUID
    keycloak_org_id: uuid.UUID
    user_id: uuid.UUID
    principal: Principal
    grants: tuple[Grant, ...]
    session: Session

    def can(self, permission: Permission, workspace_id: uuid.UUID | None = None) -> bool:
        return allows(self.grants, permission, workspace_id=workspace_id)


def get_org_context(org_id: uuid.UUID, principal: PrincipalDep, session: SessionDep) -> OrgContext:
    # Every row this transaction touches is filtered to org_id by RLS.
    set_context(session, org_id=org_id)
    # One query in every case, so the 404s can't be told apart by timing.
    row = session.execute(
        select(Org.keycloak_org_id, Org.status, User.id, User.status)
        .select_from(Org)
        .outerjoin(User, and_(User.org_id == Org.id, User.keycloak_sub == principal.sub))
        .where(Org.id == org_id)
    ).one_or_none()
    if row is None:
        raise _not_found()
    keycloak_org_id, org_status, user_id, user_status = row
    if (
        org_status != OrgStatus.ACTIVE
        or keycloak_org_id not in principal.org_ids
        or user_id is None
        or user_status != UserStatus.ACTIVE
    ):
        raise _not_found()
    grants = tuple(
        Grant(Role(r), ScopeType(s), w, t)
        for r, s, w, t in session.execute(
            select(
                Membership.role, Membership.scope_type, Membership.workspace_id, Membership.team_id
            ).where(Membership.user_id == user_id)
        )
    )
    if not grants:
        raise _not_found()
    return OrgContext(org_id, keycloak_org_id, user_id, principal, grants, session)


def require(permission: Permission) -> Callable[..., OrgContext]:
    """Dependency: the caller holds `permission` in the path's org (and workspace, if any)."""

    def dependency(
        request: Request, ctx: Annotated[OrgContext, Depends(get_org_context)]
    ) -> OrgContext:
        raw = request.path_params.get("workspace_id")
        try:
            workspace_id = uuid.UUID(raw) if raw else None
        except ValueError:
            # Not a workspace ID at all, so not a workspace in this org.
            raise _not_found() from None
        if not ctx.can(permission, workspace_id):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Forbidden")
        return ctx

    setattr(dependency, PERMISSION_MARK, permission.value)
    return dependency


@dataclass(frozen=True)
class OperatorContext:
    principal: Principal
    session: Session


def require_operator() -> Callable[..., OperatorContext]:
    """Dependency: the caller is a platform operator whose token shows MFA."""

    def dependency(
        principal: PrincipalDep,
        session: SessionDep,
        settings: Annotated[Settings, Depends(get_settings)],
    ) -> OperatorContext:
        # A SECURITY DEFINER function: the API role can test this subject but
        # can't read the operator list.
        is_operator = bool(session.scalar(select(func.controlplane.is_operator(principal.sub))))
        if not is_operator or principal.acr != settings.oidc_mfa_acr:
            log.info(
                "operator access refused",
                extra={"reason": "no_mfa" if is_operator else "not_operator"},
            )
            raise _not_found()
        set_context(session, operator_sub=principal.sub)
        return OperatorContext(principal, session)

    setattr(dependency, PERMISSION_MARK, "operator")
    return dependency


def route_permission(dependant: Any) -> str | None:
    """The permission a route declares through its dependency tree, if any."""
    for dep in dependant.dependencies:
        mark = getattr(dep.call, PERMISSION_MARK, None)
        if mark is not None:
            return str(mark)
        found = route_permission(dep)
        if found is not None:
            return found
    return None
