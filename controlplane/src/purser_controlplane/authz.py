"""Roles, permissions, and their evaluation (architecture §4).

One table maps each role to its permissions. Org-only permissions count only
from an org-scope membership; the others also count from a membership at the
workspace in question, or (for reads) from a team inside it.
"""

import enum
import uuid
from collections.abc import Iterable
from dataclasses import dataclass

from purser_controlplane.models import Role, ScopeType


class Permission(enum.StrEnum):
    ORG_READ = "org.read"
    WORKSPACE_READ = "workspace.read"
    WORKSPACE_CREATE = "workspace.create"
    TEAM_READ = "team.read"
    TEAM_CREATE = "team.create"
    USER_READ = "user.read"
    USER_CREATE = "user.create"
    MEMBERSHIP_READ = "membership.read"
    MEMBERSHIP_CREATE = "membership.create"


P = Permission
_READS = {P.ORG_READ, P.WORKSPACE_READ, P.TEAM_READ}
_DIRECTORY_READS = {P.USER_READ, P.MEMBERSHIP_READ}

ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.OWNER: frozenset(P),
    Role.ADMIN: frozenset(P),
    Role.BILLING_ADMIN: frozenset(_READS | _DIRECTORY_READS),
    Role.MEMBER: frozenset(_READS),
    Role.VIEWER: frozenset(_READS | _DIRECTORY_READS),
}

# Granted only by an org-scope membership.
ORG_ONLY = frozenset(
    {P.WORKSPACE_CREATE, P.USER_READ, P.USER_CREATE, P.MEMBERSHIP_READ, P.MEMBERSHIP_CREATE}
)
# Granted by an org- or workspace-scope membership, never by a team-scope one.
WORKSPACE_OR_ORG = frozenset({P.TEAM_CREATE})
# Only an owner may grant these roles.
OWNER_GRANTED_ROLES = frozenset({Role.OWNER, Role.ADMIN})


@dataclass(frozen=True)
class Grant:
    role: Role
    scope_type: ScopeType
    workspace_id: uuid.UUID | None
    team_id: uuid.UUID | None


def allows(
    grants: Iterable[Grant], permission: Permission, *, workspace_id: uuid.UUID | None = None
) -> bool:
    """Whether any grant gives `permission`, at the org or at `workspace_id`.

    With no workspace_id, a non-org-only permission held at any scope counts:
    list endpoints then filter by visible_workspaces() and visible_teams().
    """
    for grant in grants:
        if permission not in ROLE_PERMISSIONS[grant.role]:
            continue
        if grant.scope_type is ScopeType.ORG:
            return True
        if permission in ORG_ONLY:
            continue
        if permission in WORKSPACE_OR_ORG and grant.scope_type is not ScopeType.WORKSPACE:
            continue
        if permission is P.ORG_READ or workspace_id is None or grant.workspace_id == workspace_id:
            return True
    return False


def has_role_at_org(grants: Iterable[Grant], role: Role) -> bool:
    return any(g.role is role and g.scope_type is ScopeType.ORG for g in grants)


def visible_workspaces(grants: Iterable[Grant]) -> set[uuid.UUID] | None:
    """Workspaces the caller may read; None means all of them."""
    seen: set[uuid.UUID] = set()
    for grant in grants:
        if P.WORKSPACE_READ not in ROLE_PERMISSIONS[grant.role]:
            continue
        if grant.scope_type is ScopeType.ORG:
            return None
        if grant.workspace_id is not None:
            seen.add(grant.workspace_id)
    return seen


def visible_teams(grants: Iterable[Grant], workspace_id: uuid.UUID) -> set[uuid.UUID] | None:
    """Teams in a workspace the caller may read; None means all of them."""
    seen: set[uuid.UUID] = set()
    for grant in grants:
        if P.TEAM_READ not in ROLE_PERMISSIONS[grant.role]:
            continue
        if grant.scope_type is ScopeType.ORG:
            return None
        if grant.workspace_id != workspace_id:
            continue
        if grant.scope_type is ScopeType.WORKSPACE:
            return None
        if grant.team_id is not None:
            seen.add(grant.team_id)
    return seen
