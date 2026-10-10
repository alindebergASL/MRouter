"""Audit rows: who did what to which object, and when. Identifiers only.

Written in the same transaction as the change, so a change can't commit
without its audit row. No RETURNING: the API role can insert audit rows but
not read them.
"""

import uuid
from typing import Literal

from sqlalchemy import insert
from sqlalchemy.orm import Session

from purser_controlplane.models import AuditEvent


def record(
    session: Session,
    *,
    actor_kind: Literal["operator", "user", "system"],
    actor_sub: uuid.UUID | None,
    action: str,
    target_type: str,
    target_id: uuid.UUID | None,
    org_id: uuid.UUID | None,
) -> None:
    session.execute(
        insert(AuditEvent).values(
            id=uuid.uuid4(),
            org_id=org_id,
            actor_sub=actor_sub,
            actor_kind=actor_kind,
            action=action,
            target_type=target_type,
            target_id=target_id,
        )
    )
