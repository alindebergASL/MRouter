"""Every request-path query names the org (spec 4), without leaning on row-level security.

The API runs here on the owner role, whose owner_maintenance policy sees every
org. Row-level security therefore filters nothing, and each route must keep
org B out of org A's answers by its own WHERE clause.
"""

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

from purser_controlplane.app import create_app
from purser_controlplane.models import Role
from tests.conftest import LocalIssuer, TestDatabase, settings_for
from tests.integration.helpers import Member, OrgFixture, make_member, make_org


@pytest.fixture(scope="module")
def unguarded_api(local_issuer: LocalIssuer, seeded: TestDatabase) -> TestClient:
    settings = settings_for(local_issuer.issuer, db=seeded, db_url=seeded.owner_url)
    return TestClient(create_app(settings))


@pytest.fixture(scope="module")
def orgs(owner_engine: Any, seeded: TestDatabase) -> tuple[Member, OrgFixture, Member]:
    a, b = make_org(owner_engine, name="A"), make_org(owner_engine, name="B")
    return make_member(owner_engine, a, Role.OWNER), b, make_member(owner_engine, b)


def _headers(local_issuer: LocalIssuer, member: Member) -> dict[str, str]:
    token = local_issuer.mint(member.sub, orgs={"a": member.org.keycloak_id}, acr="pwd")
    return {"Authorization": f"Bearer {token}"}


def test_the_owner_role_really_sees_every_org(seeded: TestDatabase, orgs: Any) -> None:
    from sqlalchemy import create_engine, text

    engine = create_engine(seeded.owner_url)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM controlplane.orgs")).scalar_one() >= 3
    engine.dispose()


@pytest.mark.parametrize("collection", ["workspaces", "users", "memberships"])
def test_lists_hold_only_the_path_org(
    unguarded_api: TestClient,
    local_issuer: LocalIssuer,
    orgs: tuple[Member, OrgFixture, Member],
    collection: str,
) -> None:
    a_owner, _, _ = orgs
    response = unguarded_api.get(
        f"/v1/orgs/{a_owner.org.id}/{collection}",
        params={"limit": 200},
        headers=_headers(local_issuer, a_owner),
    )
    assert response.status_code == 200
    items = response.json()["items"]
    assert items
    assert {item["org_id"] for item in items} == {str(a_owner.org.id)}


def test_teams_list_holds_only_the_path_org(
    unguarded_api: TestClient, local_issuer: LocalIssuer, orgs: tuple[Member, OrgFixture, Member]
) -> None:
    a_owner, _, _ = orgs
    a = a_owner.org
    response = unguarded_api.get(
        f"/v1/orgs/{a.id}/workspaces/{a.workspace_id}/teams",
        headers=_headers(local_issuer, a_owner),
    )
    assert [item["id"] for item in response.json()["items"]] == [str(a.team_id)]


def test_another_orgs_rows_are_not_found_by_id(
    unguarded_api: TestClient, local_issuer: LocalIssuer, orgs: tuple[Member, OrgFixture, Member]
) -> None:
    a_owner, b, b_member = orgs
    base = f"/v1/orgs/{a_owner.org.id}"
    headers = _headers(local_issuer, a_owner)
    for path in (
        f"{base}/workspaces/{b.workspace_id}",
        f"{base}/workspaces/{b.workspace_id}/teams",
        f"{base}/users/{b_member.user_id}",
    ):
        assert unguarded_api.get(path, headers=headers).status_code == 404, path
    created = unguarded_api.post(
        f"{base}/workspaces/{b.workspace_id}/teams", json={"name": "x"}, headers=headers
    )
    assert created.status_code == 404
    for body in (
        {"user_id": str(b_member.user_id), "role": "viewer", "scope_type": "org"},
        {
            "user_id": str(a_owner.user_id),
            "role": "viewer",
            "scope_type": "workspace",
            "workspace_id": str(b.workspace_id),
        },
        {
            "user_id": str(a_owner.user_id),
            "role": "viewer",
            "scope_type": "team",
            "workspace_id": str(a_owner.org.workspace_id),
            "team_id": str(b.team_id),
        },
    ):
        response = unguarded_api.post(f"{base}/memberships", json=body, headers=headers)
        assert response.status_code == 422, body


def test_a_grant_cannot_be_filed_under_another_org(
    owner_engine: Any, orgs: tuple[Member, OrgFixture, Member]
) -> None:
    """Memberships carry their user's org (composite foreign keys), so no grant crosses orgs."""
    from sqlalchemy.exc import IntegrityError
    from sqlalchemy.orm import Session

    from purser_controlplane.models import Membership, ScopeType

    a_owner, b, _ = orgs
    with Session(owner_engine) as session:
        session.add(
            Membership(
                id=uuid.uuid4(),
                org_id=b.id,
                user_id=a_owner.user_id,
                role=Role.VIEWER,
                scope_type=ScopeType.ORG,
            )
        )
        with pytest.raises(IntegrityError, match="fk_memberships"):
            session.commit()
