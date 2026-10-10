"""Finish or roll back rows left pending by a failed Keycloak call (ADR 0003).

Runs as its own database role, which reads identifiers only (no names or
emails, by column grants) and can change or delete only pending rows. For
each pending row older than the threshold it looks up the Keycloak objects
carrying the row's purser_id:

- A pending org and its pending first owner settle together, so an org is
  never active without an owner: if Keycloak has both, both become active
  (and the owner joins the organization); otherwise both rows are deleted
  (children cascade), along with whichever of the two Keycloak objects exist.
- A pending user in an active org becomes active if Keycloak has the
  identity (and joins the organization), and is deleted otherwise.

An ambiguous lookup (two objects with one purser_id) skips the row.
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
            owner_ids = (
                session.execute(
                    select(User.id).where(User.org_id == org_id, User.status == UserStatus.PENDING)
                )
                .scalars()
                .all()
            )
            try:
                keycloak_org_id = keycloak.find_org(org_id)
                keycloak_users = {user_id: keycloak.find_user(user_id) for user_id in owner_ids}
                complete = (
                    keycloak_org_id is not None
                    and bool(keycloak_users)
                    and None not in keycloak_users.values()
                )
                if complete:
                    assert keycloak_org_id is not None
                    for keycloak_user_id in keycloak_users.values():
                        assert keycloak_user_id is not None
                        keycloak.add_member(keycloak_org_id, keycloak_user_id)
                else:
                    # Roll back Keycloak's half first: if this fails, the rows
                    # stay pending and the next sweep tries again.
                    for keycloak_user_id in keycloak_users.values():
                        if keycloak_user_id is not None:
                            keycloak.delete_user(keycloak_user_id)
                    if keycloak_org_id is not None:
                        keycloak.delete_org(keycloak_org_id)
            except KeycloakError:
                result.skipped.append(org_id)
                continue
            if complete:
                for user_id, keycloak_user_id in keycloak_users.items():
                    session.execute(
                        update(User)
                        .where(User.id == user_id, User.status == UserStatus.PENDING)
                        .values(
                            keycloak_sub=keycloak_user_id,
                            status=UserStatus.ACTIVE,
                            updated_at=func.now(),
                        )
                    )
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
                result.finished.extend([org_id, *owner_ids])
            else:
                session.execute(
                    delete(Org).where(Org.id == org_id, Org.status == OrgStatus.PENDING)
                )
                action = "org.sweep.rollback"
                result.rolled_back.extend([org_id, *owner_ids])
            audit.record(
                session,
                actor_kind="system",
                actor_sub=None,
                action=action,
                target_type="org",
                target_id=org_id,
                org_id=org_id,
            )
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                log.warning(
                    "pending org conflicts with an existing link", extra={"org_id": str(org_id)}
                )
                result.skipped.append(org_id)

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
