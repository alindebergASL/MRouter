"""Tables in the control plane's Postgres schema.

Org → workspace → team → user (architecture §4). Child tables carry org_id and
reference their parent through composite foreign keys that include it, so a
row can never point at another org's workspace, team, or user. Row-level
security policies (in the migration) filter every org-scoped table by the
caller's org.

Nothing here is copied from a token except identifiers: users.keycloak_sub,
orgs.keycloak_org_id, platform_operators.keycloak_sub, audit_events.actor_sub.
users.email and users.display_name are typed in by an admin.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

SCHEMA = "controlplane"


class Role(enum.StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    BILLING_ADMIN = "billing_admin"
    MEMBER = "member"
    VIEWER = "viewer"


class ScopeType(enum.StrEnum):
    ORG = "org"
    WORKSPACE = "workspace"
    TEAM = "team"


class OrgStatus(enum.StrEnum):
    PENDING = "pending"
    ACTIVE = "active"


class UserStatus(enum.StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    DISABLED = "disabled"


class Base(DeclarativeBase):
    metadata = MetaData(
        schema=SCHEMA,
        naming_convention={
            "pk": "pk_%(table_name)s",
            "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
            "uq": "uq_%(table_name)s_%(column_0_N_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "ix": "ix_%(table_name)s_%(column_0_N_name)s",
        },
    )


def _now() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


def _in(column: str, values: type[enum.StrEnum]) -> str:
    return f"{column} IN ({', '.join(repr(v.value) for v in values)})"


class Org(Base):
    __tablename__ = "orgs"
    __table_args__ = (
        CheckConstraint(_in("status", OrgStatus), name="status"),
        CheckConstraint("status <> 'active' OR keycloak_org_id IS NOT NULL", name="active_linked"),
        CheckConstraint("char_length(name) BETWEEN 1 AND 200", name="name_length"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    keycloak_org_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), unique=True)
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()


class Workspace(Base):
    __tablename__ = "workspaces"
    __table_args__ = (
        ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        UniqueConstraint("org_id", "name"),
        UniqueConstraint("org_id", "id"),
        CheckConstraint("char_length(name) BETWEEN 1 AND 200", name="name_length"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = _now()


class Team(Base):
    __tablename__ = "teams"
    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "workspace_id"], ["workspaces.org_id", "workspaces.id"], ondelete="CASCADE"
        ),
        UniqueConstraint("workspace_id", "name"),
        UniqueConstraint("org_id", "workspace_id", "id"),
        CheckConstraint("char_length(name) BETWEEN 1 AND 200", name="name_length"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = _now()


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        UniqueConstraint("org_id", "id"),
        UniqueConstraint("org_id", "email"),
        # One Keycloak identity belongs to one org in v1 (cross-org membership
        # arrives with invitations), so the subject is unique across orgs.
        UniqueConstraint("keycloak_sub"),
        CheckConstraint(_in("status", UserStatus), name="status"),
        CheckConstraint(
            "status = 'pending' OR keycloak_sub IS NOT NULL", name="linked_unless_pending"
        ),
        CheckConstraint(
            "email = lower(email) AND char_length(email) BETWEEN 3 AND 320", name="email_form"
        ),
        CheckConstraint("char_length(display_name) BETWEEN 1 AND 200", name="display_name_length"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    keycloak_sub: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    email: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()


class Membership(Base):
    """A role for a user at org, workspace, or team scope.

    Team-scoped rows also carry their team's workspace_id, so "every membership
    within workspace W" is a single column test.
    """

    __tablename__ = "memberships"
    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "user_id"], ["users.org_id", "users.id"], ondelete="CASCADE"
        ),
        ForeignKeyConstraint(
            ["org_id", "workspace_id"], ["workspaces.org_id", "workspaces.id"], ondelete="CASCADE"
        ),
        ForeignKeyConstraint(
            ["org_id", "workspace_id", "team_id"],
            ["teams.org_id", "teams.workspace_id", "teams.id"],
            ondelete="CASCADE",
        ),
        CheckConstraint(_in("role", Role), name="role"),
        CheckConstraint(_in("scope_type", ScopeType), name="scope_type"),
        CheckConstraint(
            "(scope_type = 'org' AND workspace_id IS NULL AND team_id IS NULL)"
            " OR (scope_type = 'workspace' AND workspace_id IS NOT NULL AND team_id IS NULL)"
            " OR (scope_type = 'team' AND workspace_id IS NOT NULL AND team_id IS NOT NULL)",
            name="scope_columns",
        ),
        CheckConstraint("role <> 'owner' OR scope_type = 'org'", name="owner_at_org_scope"),
        Index(
            "uq_memberships_grant",
            "user_id",
            "role",
            "scope_type",
            "workspace_id",
            "team_id",
            unique=True,
            postgresql_nulls_not_distinct=True,
        ),
        Index("ix_memberships_org_id_user_id", "org_id", "user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    scope_type: Mapped[str] = mapped_column(String(16), nullable=False)
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    team_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = _now()


class PlatformOperator(Base):
    """Keycloak subjects allowed to call operator endpoints (with an MFA token).

    Written only by the bootstrap CLI as the owner role; the API can read it.
    """

    __tablename__ = "platform_operators"

    keycloak_sub: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    created_at: Mapped[datetime] = _now()


class AuditEvent(Base):
    """Who did what to which object, and when. Identifiers only; no bodies."""

    __tablename__ = "audit_events"
    __table_args__ = (
        CheckConstraint("actor_kind IN ('operator', 'user', 'system')", name="actor_kind"),
        Index("ix_audit_events_org_id_at", "org_id", "at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    at: Mapped[datetime] = _now()
    # The org the action concerns; null for operator actions with no single org.
    org_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    actor_sub: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    actor_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
