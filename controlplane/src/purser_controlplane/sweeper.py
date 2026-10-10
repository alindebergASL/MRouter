"""Finish or roll back rows left pending by a failed Keycloak call (ADR 0003).

Runs as its own database role, which sees only pending orgs and users (and
every org's Keycloak link), through narrow column grants. For each pending row
older than the threshold it looks up the Keycloak object carrying the row's
purser_id: if there is one, it links the row and marks it active; if not, it
deletes the row (its children cascade). Pending orgs go first, so a pending
user's org is settled before the user is.
"""

import logging
import time
import uuid
from dataclasses import dataclass, field

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError

from purser_controlplane import audit
from purser_controlplane.db import Database
from purser_controlplane.keycloak import KeycloakAdmin, KeycloakError
from purser_controlplane.models import Org, OrgStatus, User, UserStatus

log = logging.getLogger("purser.sweeper")


@dataclass
class SweepResult:
    finished: list[uuid.UUID] = field(default_factory=list)
    rolled_back: list[uuid.UUID] = field(default_factory=list)
    skipped: list[uuid.UUID] = field(default_factory=list)


def sweep_once(
    database: Database, keycloak: KeycloakAdmin, *, older_than_seconds: int
) -> SweepResult:
    result = SweepResult()
    cutoff = func.now() - func.make_interval(0, 0, 0, 0, 0, 0, older_than_seconds)
    with database.session() as session:
        pending_orgs = (
            session.execute(
                select(Org.id)
                .where(Org.status == OrgStatus.PENDING, Org.created_at <= cutoff)
                .order_by(Org.created_at)
            )
            .scalars()
            .all()
        )
        for org_id in pending_orgs:
            try:
                keycloak_org_id = keycloak.find_org(org_id)
            except KeycloakError:
                result.skipped.append(org_id)
                continue
            if keycloak_org_id is not None:
                session.execute(
                    update(Org)
                    .where(Org.id == org_id, Org.status == OrgStatus.PENDING)
                    .values(
                        keycloak_org_id=keycloak_org_id,
                        status=OrgStatus.ACTIVE,
                        updated_at=func.now(),
                    )
                )
                action = "org.sweep.finish"
                result.finished.append(org_id)
            else:
                session.execute(
                    delete(Org).where(Org.id == org_id, Org.status == OrgStatus.PENDING)
                )
                action = "org.sweep.rollback"
                result.rolled_back.append(org_id)
            audit.record(
                session,
                actor_kind="system",
                actor_sub=None,
                action=action,
                target_type="org",
                target_id=org_id,
                org_id=org_id,
            )
            session.commit()

        pending_users = session.execute(
            select(User.id, User.org_id)
            .where(User.status == UserStatus.PENDING, User.created_at <= cutoff)
            .order_by(User.created_at)
        ).all()
        for user_id, org_id in pending_users:
            org = session.execute(
                select(Org.status, Org.keycloak_org_id).where(Org.id == org_id)
            ).one_or_none()
            if org is None or org.status != OrgStatus.ACTIVE or org.keycloak_org_id is None:
                result.skipped.append(user_id)
                continue
            try:
                keycloak_user_id = keycloak.find_user(user_id)
                if keycloak_user_id is not None:
                    keycloak.add_member(org.keycloak_org_id, keycloak_user_id)
            except KeycloakError:
                result.skipped.append(user_id)
                continue
            try:
                if keycloak_user_id is not None:
                    session.execute(
                        update(User)
                        .where(User.id == user_id, User.status == UserStatus.PENDING)
                        .values(
                            keycloak_sub=keycloak_user_id,
                            status=UserStatus.ACTIVE,
                            updated_at=func.now(),
                        )
                    )
                    action = "user.sweep.finish"
                    result.finished.append(user_id)
                else:
                    session.execute(
                        delete(User).where(User.id == user_id, User.status == UserStatus.PENDING)
                    )
                    action = "user.sweep.rollback"
                    result.rolled_back.append(user_id)
                audit.record(
                    session,
                    actor_kind="system",
                    actor_sub=None,
                    action=action,
                    target_type="user",
                    target_id=user_id,
                    org_id=org_id,
                )
                session.commit()
            except IntegrityError:
                session.rollback()
                log.warning(
                    "pending user conflicts with an existing link", extra={"user_id": str(user_id)}
                )
                result.skipped.append(user_id)
    return result


def run_forever(
    database: Database, keycloak: KeycloakAdmin, *, older_than_seconds: int, interval: int
) -> None:
    while True:
        try:
            result = sweep_once(database, keycloak, older_than_seconds=older_than_seconds)
            if result.finished or result.rolled_back or result.skipped:
                log.info(
                    "sweep",
                    extra={
                        "finished": len(result.finished),
                        "rolled_back": len(result.rolled_back),
                        "skipped": len(result.skipped),
                    },
                )
        except Exception:
            log.exception("sweep failed")
        time.sleep(interval)
