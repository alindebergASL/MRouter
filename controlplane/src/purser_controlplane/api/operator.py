"""Operator endpoints: creating and listing orgs. MFA required; every call audited."""

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select, update

from purser_controlplane import audit
from purser_controlplane.api.deps import (
    KeycloakDep,
    OperatorContext,
    RolledBackError,
    require_operator,
)
from purser_controlplane.api.pagination import page
from purser_controlplane.db import affected
from purser_controlplane.keycloak import KeycloakError
from purser_controlplane.models import Membership, Org, OrgStatus, Role, ScopeType, User, UserStatus
from purser_controlplane.schemas import OrgCreate, OrgCreated, OrgOut, Page, UserOut

log = logging.getLogger("purser.api")
router = APIRouter(prefix="/v1", tags=["operator"])
Operator = Annotated[OperatorContext, Depends(require_operator())]


@router.post(
    "/orgs",
    operation_id="createOrg",
    status_code=status.HTTP_201_CREATED,
    response_model=OrgCreated,
    responses={
        202: {"model": OrgCreated, "description": "Accepted; identity provisioning is pending"}
    },
)
def create_org(
    body: OrgCreate, op: Operator, keycloak: KeycloakDep, response: Response
) -> OrgCreated:
    """Create an org and its first owner (operators only, with an MFA token).

    Our rows are written first as pending, with the audit row in the same
    transaction. Keycloak's organization and user carry our IDs as purser_id;
    the rows become active once both exist. If Keycloak fails, the response is
    202 and the sweeper finishes or rolls back the rows.
    """
    session, actor = op.session, op.principal.sub
    email = body.owner_email.lower()
    org_id, owner_id = uuid.uuid4(), uuid.uuid4()
    try:
        if keycloak.email_taken_by_other(email, owner_id):
            # Cross-org identities arrive with invitations; until then one
            # Keycloak identity belongs to one org.
            raise HTTPException(status.HTTP_409_CONFLICT, "A user with this email already exists")
    except KeycloakError:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Identity provider unavailable"
        ) from None

    org = Org(id=org_id, name=body.name, status=OrgStatus.PENDING)
    owner = User(
        id=owner_id,
        org_id=org_id,
        email=email,
        display_name=body.owner_display_name,
        status=UserStatus.PENDING,
    )
    session.add(org)
    session.flush()
    session.add(owner)
    session.flush()
    session.add(
        Membership(
            id=uuid.uuid4(),
            org_id=org_id,
            user_id=owner_id,
            role=Role.OWNER,
            scope_type=ScopeType.ORG,
        )
    )
    audit.record(
        session,
        actor_kind="operator",
        actor_sub=actor,
        action="org.create",
        target_type="org",
        target_id=org_id,
        org_id=org_id,
    )
    session.commit()

    try:
        keycloak_org_id = keycloak.create_org(org_id)
        keycloak_user_id = keycloak.create_user(owner_id, email, body.owner_display_name)
        keycloak.add_member(keycloak_org_id, keycloak_user_id)
        activated = affected(
            session,
            update(Org)
            .where(Org.id == org_id, Org.status == OrgStatus.PENDING)
            .values(
                keycloak_org_id=keycloak_org_id, status=OrgStatus.ACTIVE, updated_at=func.now()
            ),
        )
        activated += affected(
            session,
            update(User)
            .where(User.id == owner_id, User.status == UserStatus.PENDING)
            .values(keycloak_sub=keycloak_user_id, status=UserStatus.ACTIVE, updated_at=func.now()),
        )
        if activated != 2:
            raise RolledBackError
        session.commit()
        org.status, owner.status = OrgStatus.ACTIVE, UserStatus.ACTIVE
    except RolledBackError:
        session.rollback()
        log.warning("org was rolled back before activation", extra={"org_id": str(org_id)})
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Provisioning was rolled back; retry"
        ) from None
    except Exception:
        session.rollback()
        log.warning("org provisioning left pending", extra={"org_id": str(org_id)})
        response.status_code = status.HTTP_202_ACCEPTED
    return OrgCreated(org=OrgOut.model_validate(org), owner=UserOut.model_validate(owner))


@router.get("/orgs", operation_id="listOrgs", response_model=Page[OrgOut])
def list_orgs(
    op: Operator,
    after: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> Page[OrgOut]:
    """List every org (operators only, with an MFA token)."""
    session = op.session
    audit.record(
        session,
        actor_kind="operator",
        actor_sub=op.principal.sub,
        action="org.list",
        target_type="org",
        target_id=None,
        org_id=None,
    )
    result = page(session, select(Org), Org.id, after, limit, OrgOut)
    session.commit()
    return result
