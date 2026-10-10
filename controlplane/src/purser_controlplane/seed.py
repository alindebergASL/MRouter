"""The development seed: the dev realm's organization and users as our rows.

Matches deploy/compose/keycloak/realms/purser-dev.json. Refuses to run unless
PURSER_ENV=dev, and only ever runs as the owner role.
"""

import uuid

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from purser_controlplane.models import (
    Membership,
    Org,
    OrgStatus,
    PlatformOperator,
    Role,
    ScopeType,
    Team,
    User,
    UserStatus,
    Workspace,
)

ACME_ORG_ID = uuid.UUID("0a3e0000-0000-4000-8000-0000000000a1")
ACME_KEYCLOAK_ORG_ID = uuid.UUID("ac3e0000-0000-4000-8000-000000000001")
ACME_WORKSPACE_ID = uuid.UUID("0a3e0000-0000-4000-8000-0000000000b1")
ACME_TEAM_ID = uuid.UUID("0a3e0000-0000-4000-8000-0000000000c1")
ALICE_SUB = uuid.UUID("a11ce000-0000-4000-8000-000000000001")
BOB_SUB = uuid.UUID("b0b00000-0000-4000-8000-000000000002")
ALICE_ID = uuid.UUID("0a3e0000-0000-4000-8000-0000000000d1")
BOB_ID = uuid.UUID("0a3e0000-0000-4000-8000-0000000000d2")


def _put(session: Session, model: type, **values: object) -> None:
    session.execute(insert(model).values(**values).on_conflict_do_nothing())


def seed_dev(session: Session, *, alice_is_operator: bool = True) -> None:
    _put(
        session,
        Org,
        id=ACME_ORG_ID,
        name="Acme Dev",
        status=OrgStatus.ACTIVE,
        keycloak_org_id=ACME_KEYCLOAK_ORG_ID,
    )
    _put(session, Workspace, id=ACME_WORKSPACE_ID, org_id=ACME_ORG_ID, name="default")
    _put(
        session,
        Team,
        id=ACME_TEAM_ID,
        org_id=ACME_ORG_ID,
        workspace_id=ACME_WORKSPACE_ID,
        name="core",
    )
    for user_id, sub, email, name in (
        (ALICE_ID, ALICE_SUB, "alice@acme-dev.example", "Alice Dev"),
        (BOB_ID, BOB_SUB, "bob@acme-dev.example", "Bob Dev"),
    ):
        _put(
            session,
            User,
            id=user_id,
            org_id=ACME_ORG_ID,
            keycloak_sub=sub,
            email=email,
            display_name=name,
            status=UserStatus.ACTIVE,
        )
    for user_id, role in ((ALICE_ID, Role.OWNER), (BOB_ID, Role.MEMBER)):
        _put(
            session,
            Membership,
            id=uuid.uuid5(user_id, role.value),
            org_id=ACME_ORG_ID,
            user_id=user_id,
            role=role,
            scope_type=ScopeType.ORG,
        )
    if alice_is_operator:
        _put(session, PlatformOperator, keycloak_sub=ALICE_SUB)
