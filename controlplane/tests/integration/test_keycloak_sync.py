"""Org and user creation through Keycloak, and the sweeper (ADR 0003).

Real Keycloak admin API; tokens from the local issuer, so a test can act as
an operator with MFA or as an org owner without a browser login. Everything
created in Keycloak is deleted afterwards.
"""

import contextlib
import os
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from purser_controlplane.app import create_app
from purser_controlplane.db import Database
from purser_controlplane.keycloak import KeycloakAdmin, KeycloakError
from purser_controlplane.models import Role
from purser_controlplane.seed import ALICE_SUB
from purser_controlplane.sweeper import sweep_once
from tests.conftest import LocalIssuer, TestDatabase, settings_for
from tests.integration.helpers import Member, OrgFixture


@pytest.fixture(scope="module")
def keycloak() -> Iterator[KeycloakAdmin]:
    client = KeycloakAdmin(
        os.environ["PURSER_KEYCLOAK_URL"],
        os.environ["PURSER_KEYCLOAK_REALM"],
        os.environ["PURSER_KEYCLOAK_CLIENT_ID"],
        os.environ["PURSER_KEYCLOAK_CLIENT_SECRET"],
        10,
    )
    yield client
    client.close()


@pytest.fixture(scope="module")
def created(keycloak: KeycloakAdmin) -> Iterator[list[tuple[str, uuid.UUID]]]:
    """Keycloak objects to delete at the end: ("organizations" | "users", id)."""
    objects: list[tuple[str, uuid.UUID]] = []
    yield objects
    for kind, object_id in reversed(objects):
        with contextlib.suppress(KeycloakError):
            keycloak._request("DELETE", f"/{kind}/{object_id}")


def _app(local_issuer: LocalIssuer, db: TestDatabase, keycloak: KeycloakAdmin) -> TestClient:
    return TestClient(
        create_app(settings_for(local_issuer.issuer, db_url=db.app_url), keycloak=keycloak)
    )


def _operator(local_issuer: LocalIssuer) -> dict[str, str]:
    return {"Authorization": f"Bearer {local_issuer.mint(ALICE_SUB, acr='mfa')}"}


def _row(owner_engine: Any, table: str, row_id: str) -> dict[str, Any] | None:
    with owner_engine.connect() as conn:
        row = (
            conn.execute(text(f"SELECT * FROM controlplane.{table} WHERE id = :id"), {"id": row_id})
            .mappings()
            .first()
        )
    return dict(row) if row else None


def _email() -> str:
    return f"owner-{uuid.uuid4().hex[:10]}@sync-test.example"


def test_operator_creates_an_org_and_its_owner(
    local_issuer: LocalIssuer,
    seeded: TestDatabase,
    owner_engine: Any,
    keycloak: KeycloakAdmin,
    created: list[Any],
) -> None:
    api = _app(local_issuer, seeded, keycloak)
    email = _email()
    response = api.post(
        "/v1/orgs",
        json={"name": "Sync Co", "owner_email": email, "owner_display_name": "Owner"},
        headers=_operator(local_issuer),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    org_id, owner_id = body["org"]["id"], body["owner"]["id"]
    assert body["org"]["status"] == "active" and body["owner"]["status"] == "active"

    org_row, owner_row = _row(owner_engine, "orgs", org_id), _row(owner_engine, "users", owner_id)
    assert org_row and owner_row
    keycloak_org_id, keycloak_user_id = org_row["keycloak_org_id"], owner_row["keycloak_sub"]
    created += [("users", keycloak_user_id), ("organizations", keycloak_org_id)]
    assert keycloak.find_org(uuid.UUID(org_id)) == keycloak_org_id
    assert keycloak.find_user(uuid.UUID(owner_id)) == keycloak_user_id
    members = keycloak._request("GET", f"/organizations/{keycloak_org_id}/members").json()
    assert [m["id"] for m in members] == [str(keycloak_user_id)]
    # The organization's name and alias are our ID; the display name stays with us.
    kc_org = keycloak._request("GET", f"/organizations/{keycloak_org_id}").json()
    assert kc_org["alias"] == org_id and kc_org["name"] == org_id

    # The new owner can use the org, keyed by their Keycloak identity.
    owner = Member(
        keycloak_user_id,
        uuid.UUID(owner_id),
        OrgFixture(uuid.UUID(org_id), keycloak_org_id, uuid.uuid4(), uuid.uuid4()),
    )
    token = local_issuer.mint(owner.sub, orgs={org_id: keycloak_org_id})
    assert (
        api.get(f"/v1/orgs/{org_id}", headers={"Authorization": f"Bearer {token}"}).status_code
        == 200
    )

    with owner_engine.connect() as conn:
        audit = conn.execute(
            text(
                "SELECT actor_kind, actor_sub FROM controlplane.audit_events WHERE action = 'org.create' AND target_id = :id"
            ),
            {"id": org_id},
        ).all()
    assert audit == [("operator", ALICE_SUB)]

    # The same email again: one Keycloak identity belongs to one org.
    again = api.post(
        "/v1/orgs",
        json={"name": "Dup", "owner_email": email, "owner_display_name": "X"},
        headers=_operator(local_issuer),
    )
    assert again.status_code == 409


class _FailingMembership(KeycloakAdmin):
    """Keycloak that creates objects but fails to add a member (step 3 fails)."""

    def add_member(self, keycloak_org_id: uuid.UUID, keycloak_user_id: uuid.UUID) -> None:
        raise KeycloakError("injected failure")


def test_a_failed_keycloak_step_leaves_pending_rows_the_sweeper_finishes(
    local_issuer: LocalIssuer,
    seeded: TestDatabase,
    owner_engine: Any,
    keycloak: KeycloakAdmin,
    created: list[Any],
) -> None:
    failing = _FailingMembership(
        os.environ["PURSER_KEYCLOAK_URL"],
        os.environ["PURSER_KEYCLOAK_REALM"],
        os.environ["PURSER_KEYCLOAK_CLIENT_ID"],
        os.environ["PURSER_KEYCLOAK_CLIENT_SECRET"],
        10,
    )
    api = _app(local_issuer, seeded, failing)
    response = api.post(
        "/v1/orgs",
        json={"name": "Half Co", "owner_email": _email(), "owner_display_name": "H"},
        headers=_operator(local_issuer),
    )
    assert response.status_code == 202
    org_id, owner_id = response.json()["org"]["id"], response.json()["owner"]["id"]
    assert _row(owner_engine, "orgs", org_id)["status"] == "pending"  # type: ignore[index]
    assert _row(owner_engine, "users", owner_id)["status"] == "pending"  # type: ignore[index]
    created += [
        ("users", keycloak.find_user(uuid.UUID(owner_id))),
        ("organizations", keycloak.find_org(uuid.UUID(org_id))),
    ]

    result = sweep_once(Database(seeded.sweeper_url, pool_size=1), keycloak, older_than_seconds=0)
    assert uuid.UUID(org_id) in result.finished and uuid.UUID(owner_id) in result.finished
    org_row, owner_row = _row(owner_engine, "orgs", org_id), _row(owner_engine, "users", owner_id)
    assert org_row and org_row["status"] == "active"
    assert owner_row and owner_row["status"] == "active"
    members = keycloak._request(
        "GET", f"/organizations/{org_row['keycloak_org_id']}/members"
    ).json()
    assert [m["id"] for m in members] == [str(owner_row["keycloak_sub"])]


class _FailingUserCreation(KeycloakAdmin):
    """Keycloak that creates the organization but fails to create the owner (step 2)."""

    def create_user(self, purser_id: uuid.UUID, email: str, display_name: str) -> uuid.UUID:
        raise KeycloakError("injected failure")


def test_an_org_without_its_owner_is_rolled_back_whole(
    local_issuer: LocalIssuer, seeded: TestDatabase, owner_engine: Any, keycloak: KeycloakAdmin
) -> None:
    failing = _FailingUserCreation(
        os.environ["PURSER_KEYCLOAK_URL"],
        os.environ["PURSER_KEYCLOAK_REALM"],
        os.environ["PURSER_KEYCLOAK_CLIENT_ID"],
        os.environ["PURSER_KEYCLOAK_CLIENT_SECRET"],
        10,
    )
    api = _app(local_issuer, seeded, failing)
    response = api.post(
        "/v1/orgs",
        json={"name": "No Owner Co", "owner_email": _email(), "owner_display_name": "N"},
        headers=_operator(local_issuer),
    )
    assert response.status_code == 202
    org_id = uuid.UUID(response.json()["org"]["id"])
    assert keycloak.find_org(org_id) is not None  # step 1 happened

    result = sweep_once(Database(seeded.sweeper_url, pool_size=1), keycloak, older_than_seconds=0)
    assert org_id in result.rolled_back
    # Never an active org without an owner: the rows and Keycloak's organization are gone.
    assert _row(owner_engine, "orgs", str(org_id)) is None
    assert keycloak.find_org(org_id) is None


def test_the_sweeper_rolls_back_rows_keycloak_never_saw(
    seeded: TestDatabase, owner_engine: Any, keycloak: KeycloakAdmin
) -> None:
    org_id = uuid.uuid4()
    with owner_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO controlplane.orgs (id, name, status) VALUES (:id, 'Ghost', 'pending')"
            ),
            {"id": org_id},
        )
        conn.execute(
            text(
                "INSERT INTO controlplane.users (id, org_id, email, display_name, status)"
                " VALUES (gen_random_uuid(), :org, 'ghost@sync-test.example', 'G', 'pending')"
            ),
            {"org": org_id},
        )
    database = Database(seeded.sweeper_url, pool_size=1)
    # Younger than the threshold: left alone.
    assert org_id not in sweep_once(database, keycloak, older_than_seconds=3600).rolled_back
    result = sweep_once(database, keycloak, older_than_seconds=0)
    assert org_id in result.rolled_back
    assert _row(owner_engine, "orgs", str(org_id)) is None
    with owner_engine.connect() as conn:
        assert (
            conn.execute(
                text("SELECT count(*) FROM controlplane.users WHERE org_id = :o"), {"o": org_id}
            ).scalar_one()
            == 0
        )
        actions = conn.execute(
            text("SELECT actor_kind, action FROM controlplane.audit_events WHERE target_id = :id"),
            {"id": org_id},
        ).all()
    assert actions == [("system", "org.sweep.rollback")]


def test_org_admin_creates_a_user(
    local_issuer: LocalIssuer,
    seeded: TestDatabase,
    owner_engine: Any,
    keycloak: KeycloakAdmin,
    created: list[Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    import logging

    from purser_controlplane import logs
    from purser_controlplane.seed import ACME_KEYCLOAK_ORG_ID, ACME_ORG_ID
    from tests.integration.helpers import make_member

    # The service's own logging setup (which quiets httpx), with pytest's
    # capture handler put back on the root logger it replaces.
    logs.configure(logging.DEBUG)
    logging.getLogger().addHandler(caplog.handler)
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING

    acme = OrgFixture(ACME_ORG_ID, ACME_KEYCLOAK_ORG_ID, uuid.uuid4(), uuid.uuid4())
    admin = make_member(owner_engine, acme, Role.ADMIN)
    api = _app(local_issuer, seeded, keycloak)
    headers = {
        "Authorization": f"Bearer {local_issuer.mint(admin.sub, orgs={'acme-dev': ACME_KEYCLOAK_ORG_ID})}"
    }
    email = _email()
    response = api.post(
        f"/v1/orgs/{ACME_ORG_ID}/users",
        json={"email": email, "display_name": "New"},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    user = _row(owner_engine, "users", response.json()["id"])
    assert user and user["status"] == "active"
    created.append(("users", user["keycloak_sub"]))
    # The address went to Keycloak, but never into a log line.
    assert caplog.records, "capture is working (the request logged something)"
    assert email.split("@")[0] not in caplog.text
    members = {
        m["id"]
        for m in keycloak._request("GET", f"/organizations/{ACME_KEYCLOAK_ORG_ID}/members").json()
    }
    assert str(user["keycloak_sub"]) in members
    # An existing identity's email is refused (bob's identity belongs to acme already).
    taken = api.post(
        f"/v1/orgs/{ACME_ORG_ID}/users",
        json={"email": "bob@acme-dev.example", "display_name": "B"},
        headers=headers,
    )
    assert taken.status_code == 409
