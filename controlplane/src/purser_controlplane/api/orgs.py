"""Org-scoped endpoints: the org, its workspaces, teams, users, and memberships.

Every route depends on require(<permission>), which resolves the caller in the
path's org (404 if they can't access it) and checks the permission (403).
The session is scoped to that org by row-level security.
"""

import logging
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from purser_controlplane import audit
from purser_controlplane.api.deps import KeycloakDep, OrgContext, require
from purser_controlplane.api.pagination import page
from purser_controlplane.authz import (
    OWNER_GRANTED_ROLES,
    Permission,
    has_role_at_org,
    visible_teams,
    visible_workspaces,
)
from purser_controlplane.keycloak import KeycloakError
from purser_controlplane.models import (
    Membership,
    Org,
    Role,
    ScopeType,
    Team,
    User,
    UserStatus,
    Workspace,
)
from purser_controlplane.schemas import (
    MembershipCreate,
    MembershipOut,
    OrgOut,
    Page,
    TeamCreate,
    TeamOut,
    UserCreate,
    UserOut,
    WorkspaceCreate,
    WorkspaceOut,
)

log = logging.getLogger("purser.api")
router = APIRouter(prefix="/v1/orgs/{org_id}", tags=["orgs"])
P = Permission
Limit = Annotated[int, Query(ge=1, le=200)]


def _ctx(permission: Permission) -> Any:
    return Depends(require(permission))


def _audit(ctx: OrgContext, action: str, target_type: str, target_id: uuid.UUID) -> None:
    audit.record(
        ctx.session,
        actor_kind="user",
        actor_sub=ctx.principal.sub,
        action=action,
        target_type=target_type,
        target_id=target_id,
        org_id=ctx.org_id,
    )


def _conflict() -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, "Conflict")


# -- org ---------------------------------------------------------------------


@router.get("", operation_id="getOrg", response_model=OrgOut)
def get_org(org_id: uuid.UUID, ctx: Annotated[OrgContext, _ctx(P.ORG_READ)]) -> OrgOut:
    org = ctx.session.get(Org, org_id)
    assert org is not None  # get_org_context found it
    return OrgOut.model_validate(org)


# -- workspaces ----------------------------------------------------------------


@router.post(
    "/workspaces", operation_id="createWorkspace", status_code=201, response_model=WorkspaceOut
)
def create_workspace(
    org_id: uuid.UUID, body: WorkspaceCreate, ctx: Annotated[OrgContext, _ctx(P.WORKSPACE_CREATE)]
) -> WorkspaceOut:
    workspace = Workspace(id=uuid.uuid4(), org_id=org_id, name=body.name)
    ctx.session.add(workspace)
    try:
        ctx.session.flush()
    except IntegrityError:
        raise _conflict() from None
    _audit(ctx, "workspace.create", "workspace", workspace.id)
    ctx.session.commit()
    return WorkspaceOut.model_validate(workspace)


@router.get("/workspaces", operation_id="listWorkspaces", response_model=Page[WorkspaceOut])
def list_workspaces(
    org_id: uuid.UUID,
    ctx: Annotated[OrgContext, _ctx(P.WORKSPACE_READ)],
    after: uuid.UUID | None = None,
    limit: Limit = 50,
) -> Page[WorkspaceOut]:
    query = select(Workspace)
    visible = visible_workspaces(ctx.grants)
    if visible is not None:
        query = query.where(Workspace.id.in_(visible))
    return page(ctx.session, query, Workspace.id, after, limit, WorkspaceOut)


@router.get("/workspaces/{workspace_id}", operation_id="getWorkspace", response_model=WorkspaceOut)
def get_workspace(
    org_id: uuid.UUID, workspace_id: uuid.UUID, ctx: Annotated[OrgContext, _ctx(P.WORKSPACE_READ)]
) -> WorkspaceOut:
    workspace = ctx.session.get(Workspace, workspace_id)
    if workspace is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")
    return WorkspaceOut.model_validate(workspace)


# -- teams ---------------------------------------------------------------------


@router.post(
    "/workspaces/{workspace_id}/teams",
    operation_id="createTeam",
    status_code=201,
    response_model=TeamOut,
)
def create_team(
    org_id: uuid.UUID,
    workspace_id: uuid.UUID,
    body: TeamCreate,
    ctx: Annotated[OrgContext, _ctx(P.TEAM_CREATE)],
) -> TeamOut:
    if ctx.session.get(Workspace, workspace_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")
    team = Team(id=uuid.uuid4(), org_id=org_id, workspace_id=workspace_id, name=body.name)
    ctx.session.add(team)
    try:
        ctx.session.flush()
    except IntegrityError:
        raise _conflict() from None
    _audit(ctx, "team.create", "team", team.id)
    ctx.session.commit()
    return TeamOut.model_validate(team)


@router.get(
    "/workspaces/{workspace_id}/teams", operation_id="listTeams", response_model=Page[TeamOut]
)
def list_teams(
    org_id: uuid.UUID,
    workspace_id: uuid.UUID,
    ctx: Annotated[OrgContext, _ctx(P.TEAM_READ)],
    after: uuid.UUID | None = None,
    limit: Limit = 50,
) -> Page[TeamOut]:
    if ctx.session.get(Workspace, workspace_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")
    query = select(Team).where(Team.workspace_id == workspace_id)
    visible = visible_teams(ctx.grants, workspace_id)
    if visible is not None:
        query = query.where(Team.id.in_(visible))
    return page(ctx.session, query, Team.id, after, limit, TeamOut)


# -- users ---------------------------------------------------------------------


@router.post(
    "/users",
    operation_id="createUser",
    status_code=201,
    response_model=UserOut,
    responses={
        202: {"model": UserOut, "description": "Accepted; identity provisioning is pending"}
    },
)
def create_user(
    org_id: uuid.UUID,
    body: UserCreate,
    ctx: Annotated[OrgContext, _ctx(P.USER_CREATE)],
    keycloak: KeycloakDep,
    response: Response,
) -> UserOut:
    """Create a user in the org. Same pending-first ordering as org creation."""
    session = ctx.session
    email = body.email.lower()
    user = User(
        id=uuid.uuid4(),
        org_id=org_id,
        email=email,
        display_name=body.display_name,
        status=UserStatus.PENDING,
    )
    try:
        if keycloak.email_taken_by_other(email, user.id):
            raise _conflict()
    except KeycloakError:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Identity provider unavailable"
        ) from None
    session.add(user)
    try:
        session.flush()
    except IntegrityError:
        raise _conflict() from None
    _audit(ctx, "user.create", "user", user.id)
    session.commit()

    try:
        keycloak_user_id = keycloak.create_user(user.id, email, body.display_name)
        keycloak.add_member(ctx.keycloak_org_id, keycloak_user_id)
        session.execute(
            update(User)
            .where(User.id == user.id, User.status == UserStatus.PENDING)
            .values(keycloak_sub=keycloak_user_id, status=UserStatus.ACTIVE, updated_at=func.now())
        )
        session.commit()
        user.status = UserStatus.ACTIVE
    except Exception:
        session.rollback()
        log.warning("user provisioning left pending", extra={"user_id": str(user.id)})
        response.status_code = status.HTTP_202_ACCEPTED
    return UserOut.model_validate(user)


@router.get("/users", operation_id="listUsers", response_model=Page[UserOut])
def list_users(
    org_id: uuid.UUID,
    ctx: Annotated[OrgContext, _ctx(P.USER_READ)],
    after: uuid.UUID | None = None,
    limit: Limit = 50,
) -> Page[UserOut]:
    return page(ctx.session, select(User), User.id, after, limit, UserOut)


@router.get("/users/{user_id}", operation_id="getUser", response_model=UserOut)
def get_user(
    org_id: uuid.UUID, user_id: uuid.UUID, ctx: Annotated[OrgContext, _ctx(P.USER_READ)]
) -> UserOut:
    user = ctx.session.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")
    return UserOut.model_validate(user)


# -- memberships (role assignments) ------------------------------------------


@router.post(
    "/memberships", operation_id="createMembership", status_code=201, response_model=MembershipOut
)
def create_membership(
    org_id: uuid.UUID, body: MembershipCreate, ctx: Annotated[OrgContext, _ctx(P.MEMBERSHIP_CREATE)]
) -> MembershipOut:
    """Assign a role to a user at org, workspace, or team scope.

    Only an owner may grant owner or admin.
    """
    session = ctx.session
    if body.role in OWNER_GRANTED_ROLES and not has_role_at_org(ctx.grants, Role.OWNER):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only an owner may grant owner or admin")
    if session.get(User, body.user_id) is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown user")
    if body.workspace_id is not None and session.get(Workspace, body.workspace_id) is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown workspace")
    if body.team_id is not None:
        team = session.get(Team, body.team_id)
        if team is None or team.workspace_id != body.workspace_id:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown team")
    membership = Membership(
        id=uuid.uuid4(),
        org_id=org_id,
        user_id=body.user_id,
        role=body.role,
        scope_type=body.scope_type,
        workspace_id=body.workspace_id,
        team_id=body.team_id,
    )
    session.add(membership)
    try:
        session.flush()
    except IntegrityError:
        raise _conflict() from None
    _audit(ctx, "membership.create", "membership", membership.id)
    session.commit()
    return MembershipOut.model_validate(membership)


@router.get("/memberships", operation_id="listMemberships", response_model=Page[MembershipOut])
def list_memberships(
    org_id: uuid.UUID,
    ctx: Annotated[OrgContext, _ctx(P.MEMBERSHIP_READ)],
    user_id: uuid.UUID | None = None,
    scope_type: ScopeType | None = None,
    after: uuid.UUID | None = None,
    limit: Limit = 50,
) -> Page[MembershipOut]:
    query = select(Membership)
    if user_id is not None:
        query = query.where(Membership.user_id == user_id)
    if scope_type is not None:
        query = query.where(Membership.scope_type == scope_type)
    return page(ctx.session, query, Membership.id, after, limit, MembershipOut)
