"""Request and response models for the admin API (OpenAPI 3.1, A1)."""

import uuid
from datetime import datetime
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints, model_validator

from purser_controlplane.models import OrgStatus, Role, ScopeType, UserStatus

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page[T](BaseModel):
    """A page of results, ordered by id. Pass next_after as `after` for the next page."""

    items: list[T]
    next_after: uuid.UUID | None = None


class OrgCreate(_In):
    name: Name
    owner_email: EmailStr = Field(
        description="The first owner. A Keycloak user is created for this address."
    )
    owner_display_name: Name


class OrgOut(_Out):
    id: uuid.UUID
    name: str
    status: OrgStatus
    created_at: datetime


class UserOut(_Out):
    id: uuid.UUID
    org_id: uuid.UUID
    email: str
    display_name: str
    status: UserStatus
    created_at: datetime


class OrgCreated(BaseModel):
    org: OrgOut
    owner: UserOut


class WorkspaceCreate(_In):
    name: Name


class WorkspaceOut(_Out):
    id: uuid.UUID
    org_id: uuid.UUID
    name: str
    created_at: datetime


class TeamCreate(_In):
    name: Name


class TeamOut(_Out):
    id: uuid.UUID
    org_id: uuid.UUID
    workspace_id: uuid.UUID
    name: str
    created_at: datetime


class UserCreate(_In):
    email: EmailStr
    display_name: Name


class MembershipCreate(_In):
    user_id: uuid.UUID
    role: Role
    scope_type: ScopeType
    workspace_id: uuid.UUID | None = None
    team_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _scope_columns(self) -> Self:
        if self.scope_type is ScopeType.ORG and (self.workspace_id or self.team_id):
            raise ValueError("org scope takes neither workspace_id nor team_id")
        if self.scope_type is ScopeType.WORKSPACE and (not self.workspace_id or self.team_id):
            raise ValueError("workspace scope takes workspace_id only")
        if self.scope_type is ScopeType.TEAM and not (self.workspace_id and self.team_id):
            raise ValueError("team scope takes workspace_id and team_id")
        if self.role is Role.OWNER and self.scope_type is not ScopeType.ORG:
            raise ValueError("owner is granted at org scope only")
        return self


class MembershipOut(_Out):
    id: uuid.UUID
    org_id: uuid.UUID
    user_id: uuid.UUID
    role: Role
    scope_type: ScopeType
    workspace_id: uuid.UUID | None
    team_id: uuid.UUID | None
    created_at: datetime


class Status(BaseModel):
    status: str
