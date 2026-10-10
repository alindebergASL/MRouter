"""Building orgs, users, and grants directly as the owner role (bypassing the API)."""

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from purser_controlplane.models import Membership, Org, Role, ScopeType, Team, User, Workspace


@dataclass(frozen=True)
class OrgFixture:
    id: uuid.UUID
    keycloak_id: uuid.UUID
    workspace_id: uuid.UUID
    team_id: uuid.UUID


@dataclass(frozen=True)
class Member:
    sub: uuid.UUID
    user_id: uuid.UUID
    org: OrgFixture


def make_org(owner_engine: Any, *, status: str = "active", name: str = "Test org") -> OrgFixture:
    org = OrgFixture(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
    with Session(owner_engine) as session:
        session.add(Org(id=org.id, name=name, status=status, keycloak_org_id=org.keycloak_id))
        session.flush()
        session.add(Workspace(id=org.workspace_id, org_id=org.id, name="ws"))
        session.flush()
        session.add(Team(id=org.team_id, org_id=org.id, workspace_id=org.workspace_id, name="team"))
        session.commit()
    return org


def make_member(
    owner_engine: Any,
    org: OrgFixture,
    role: Role | None = Role.MEMBER,
    *,
    scope: ScopeType = ScopeType.ORG,
    status: str = "active",
    workspace_id: uuid.UUID | None = None,
) -> Member:
    member = Member(uuid.uuid4(), uuid.uuid4(), org)
    with Session(owner_engine) as session:
        session.add(
            User(
                id=member.user_id,
                org_id=org.id,
                keycloak_sub=member.sub,
                email=f"{member.user_id.hex[:12]}@test.example",
                display_name="Test user",
                status=status,
            )
        )
        session.flush()
        if role is not None:
            ws = workspace_id or org.workspace_id
            session.add(
                Membership(
                    id=uuid.uuid4(),
                    org_id=org.id,
                    user_id=member.user_id,
                    role=role,
                    scope_type=scope,
                    workspace_id=None if scope is ScopeType.ORG else ws,
                    team_id=org.team_id if scope is ScopeType.TEAM else None,
                )
            )
        session.commit()
    return member
