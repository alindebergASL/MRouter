"""The role table and scope rules, as pure functions."""

import uuid

import pytest

from purser_controlplane.authz import Grant, Permission, allows, visible_teams, visible_workspaces
from purser_controlplane.models import Role, ScopeType

P = Permission
W1, W2 = uuid.uuid4(), uuid.uuid4()
T1 = uuid.uuid4()


def org(role: Role) -> Grant:
    return Grant(role, ScopeType.ORG, None, None)


def ws(role: Role, workspace: uuid.UUID = W1) -> Grant:
    return Grant(role, ScopeType.WORKSPACE, workspace, None)


def team(role: Role) -> Grant:
    return Grant(role, ScopeType.TEAM, W1, T1)


# The matrix from the plan, at org scope.
EXPECTED = {
    Role.OWNER: set(P),
    Role.ADMIN: set(P),
    Role.BILLING_ADMIN: {P.ORG_READ, P.WORKSPACE_READ, P.TEAM_READ, P.USER_READ, P.MEMBERSHIP_READ},
    Role.MEMBER: {P.ORG_READ, P.WORKSPACE_READ, P.TEAM_READ},
    Role.VIEWER: {P.ORG_READ, P.WORKSPACE_READ, P.TEAM_READ, P.USER_READ, P.MEMBERSHIP_READ},
}


@pytest.mark.parametrize("role", list(Role))
@pytest.mark.parametrize("permission", list(P))
def test_org_scope_matrix(role: Role, permission: Permission) -> None:
    assert allows([org(role)], permission, workspace_id=W1) == (permission in EXPECTED[role])


def test_no_grants_no_access() -> None:
    assert not any(allows([], p) for p in P)


@pytest.mark.parametrize(
    "permission",
    [P.WORKSPACE_CREATE, P.USER_READ, P.USER_CREATE, P.MEMBERSHIP_READ, P.MEMBERSHIP_CREATE],
)
def test_org_only_permissions_never_come_from_a_workspace_or_team(permission: Permission) -> None:
    assert not allows([ws(Role.ADMIN), team(Role.ADMIN)], permission, workspace_id=W1)


def test_workspace_admin_creates_teams_in_their_workspace_only() -> None:
    grants = [ws(Role.ADMIN)]
    assert allows(grants, P.TEAM_CREATE, workspace_id=W1)
    assert not allows(grants, P.TEAM_CREATE, workspace_id=W2)


def test_team_admin_cannot_create_teams() -> None:
    assert not allows([team(Role.ADMIN)], P.TEAM_CREATE, workspace_id=W1)


def test_workspace_member_reads_only_their_workspace() -> None:
    grants = [ws(Role.MEMBER)]
    assert allows(grants, P.WORKSPACE_READ, workspace_id=W1)
    assert not allows(grants, P.WORKSPACE_READ, workspace_id=W2)
    assert allows(grants, P.ORG_READ)
    assert visible_workspaces(grants) == {W1}


def test_visibility() -> None:
    assert visible_workspaces([org(Role.VIEWER)]) is None
    assert visible_teams([ws(Role.MEMBER)], W1) is None
    assert visible_teams([team(Role.MEMBER)], W1) == {T1}
    assert visible_teams([team(Role.MEMBER)], W2) == set()
