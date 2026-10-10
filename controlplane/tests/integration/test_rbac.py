"""Authorization through the API: the role matrix, 404s across orgs, operators, audit.

Tokens come from the local issuer, so a test can mint any subject, org, or acr;
the database is real and the API connects as the non-owner app role.
"""

import uuid
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from purser_controlplane.app import create_app
from purser_controlplane.models import Role, ScopeType
from purser_controlplane.seed import ALICE_SUB
from tests.conftest import LocalIssuer, TestDatabase, settings_for
from tests.integration.helpers import Member, OrgFixture, make_member, make_org

NOT_FOUND = {"detail": "Not Found"}


@pytest.fixture(scope="module")
def api(local_issuer: LocalIssuer, seeded: TestDatabase) -> TestClient:
    return TestClient(create_app(settings_for(local_issuer.issuer, db=seeded)))


@pytest.fixture(scope="module")
def org_a(owner_engine: Any, seeded: TestDatabase) -> OrgFixture:
    return make_org(owner_engine, name="A")


@pytest.fixture(scope="module")
def org_b(owner_engine: Any, seeded: TestDatabase) -> OrgFixture:
    return make_org(owner_engine, name="B")


@pytest.fixture
def as_member(local_issuer: LocalIssuer) -> Callable[..., dict[str, str]]:
    def headers(
        member: Member, *, orgs: list[OrgFixture] | None = None, acr: str = "pwd"
    ) -> dict[str, str]:
        claim = {
            f"o{i}": o.keycloak_id for i, o in enumerate(orgs if orgs is not None else [member.org])
        }
        return {"Authorization": f"Bearer {local_issuer.mint(member.sub, orgs=claim, acr=acr)}"}

    return headers


def _endpoints(
    org: OrgFixture, target: Member
) -> list[tuple[str, str, dict[str, Any] | None, Any]]:
    """(method, path, body, permission) for every org-scoped route."""
    base = f"/v1/orgs/{org.id}"
    ws = f"{base}/workspaces/{org.workspace_id}"
    return [
        ("GET", base, None, "org.read"),
        ("GET", f"{base}/workspaces", None, "workspace.read"),
        ("GET", ws, None, "workspace.read"),
        ("POST", f"{base}/workspaces", {"name": f"w-{uuid.uuid4().hex[:8]}"}, "workspace.create"),
        ("GET", f"{ws}/teams", None, "team.read"),
        ("POST", f"{ws}/teams", {"name": f"t-{uuid.uuid4().hex[:8]}"}, "team.create"),
        ("GET", f"{base}/users", None, "user.read"),
        ("GET", f"{base}/users/{target.user_id}", None, "user.read"),
        (
            "POST",
            f"{base}/users",
            {"email": f"{uuid.uuid4().hex[:8]}@t.example", "display_name": "T"},
            "user.create",
        ),
        ("GET", f"{base}/memberships", None, "membership.read"),
        (
            "POST",
            f"{base}/memberships",
            {
                "user_id": str(target.user_id),
                "role": "viewer",
                "scope_type": "workspace",
                "workspace_id": str(org.workspace_id),
            },
            "membership.create",
        ),
    ]


ALLOWED = {
    Role.OWNER: {"*"},
    Role.ADMIN: {"*"},
    Role.BILLING_ADMIN: {"org.read", "workspace.read", "team.read", "user.read", "membership.read"},
    Role.MEMBER: {"org.read", "workspace.read", "team.read"},
    Role.VIEWER: {"org.read", "workspace.read", "team.read", "user.read", "membership.read"},
}


@pytest.mark.parametrize("role", list(Role))
def test_role_matrix_at_org_scope(
    api: TestClient, owner_engine: Any, as_member: Any, role: Role
) -> None:
    org = make_org(owner_engine)
    caller = make_member(owner_engine, org, role)
    target = make_member(owner_engine, org, None)
    for method, path, body, permission in _endpoints(org, target):
        response = api.request(method, path, json=body, headers=as_member(caller))
        allowed = "*" in ALLOWED[role] or permission in ALLOWED[role]
        if allowed:
            # user.create reaches Keycloak, which this app has none of: 503 means authorized.
            assert response.status_code in {200, 201, 503}, (
                role,
                method,
                path,
                response.status_code,
            )
        else:
            assert response.status_code == 403, (role, method, path, response.status_code)


def test_identical_404_for_every_inaccessible_org(
    api: TestClient, owner_engine: Any, as_member: Any, org_a: OrgFixture, org_b: OrgFixture
) -> None:
    alice_in_a = make_member(owner_engine, org_a, Role.OWNER)
    pending = make_org(owner_engine, status="pending")
    disabled = make_member(owner_engine, org_a, Role.OWNER, status="disabled")
    stranger = Member(uuid.uuid4(), uuid.uuid4(), org_a)
    cases = {
        "another org": (alice_in_a, f"/v1/orgs/{org_b.id}", None),
        "no such org": (alice_in_a, f"/v1/orgs/{uuid.uuid4()}", None),
        "org not in the claim": (alice_in_a, f"/v1/orgs/{org_a.id}", [org_b]),
        "unknown subject": (stranger, f"/v1/orgs/{org_a.id}", None),
        "disabled user": (disabled, f"/v1/orgs/{org_a.id}", None),
        "pending org": (alice_in_a, f"/v1/orgs/{pending.id}", [pending]),
    }
    for name, (who, path, orgs) in cases.items():
        response = api.get(path, headers=as_member(who, orgs=orgs))
        assert (response.status_code, response.json()) == (404, NOT_FOUND), name
        # The same 404 for writes, before any body validation.
        response = api.post(
            path + "/workspaces", json={"name": "x"}, headers=as_member(who, orgs=orgs)
        )
        assert (response.status_code, response.json()) == (404, NOT_FOUND), name
    assert api.get(f"/v1/orgs/{org_a.id}", headers=as_member(alice_in_a)).status_code == 200


def test_a_member_of_two_claims_still_sees_only_rows_for_the_path_org(
    api: TestClient, owner_engine: Any, as_member: Any, org_a: OrgFixture, org_b: OrgFixture
) -> None:
    viewer = make_member(owner_engine, org_a, Role.VIEWER)
    response = api.get(f"/v1/orgs/{org_a.id}/users", headers=as_member(viewer, orgs=[org_a, org_b]))
    assert response.status_code == 200
    assert {u["org_id"] for u in response.json()["items"]} == {str(org_a.id)}


def test_workspace_scoped_admin(api: TestClient, owner_engine: Any, as_member: Any) -> None:
    org = make_org(owner_engine)
    admin = make_member(owner_engine, org, Role.ADMIN, scope=ScopeType.WORKSPACE)
    base = f"/v1/orgs/{org.id}"
    h = as_member(admin)
    assert (
        api.post(
            f"{base}/workspaces/{org.workspace_id}/teams", json={"name": "a"}, headers=h
        ).status_code
        == 201
    )
    other = api.post(
        f"{base}/workspaces",
        json={"name": "other"},
        headers=as_member(make_member(owner_engine, org, Role.OWNER)),
    )
    assert other.status_code == 201
    other_ws = other.json()["id"]
    assert (
        api.post(f"{base}/workspaces/{other_ws}/teams", json={"name": "b"}, headers=h).status_code
        == 403
    )
    assert api.post(f"{base}/workspaces", json={"name": "c"}, headers=h).status_code == 403
    listed = api.get(f"{base}/workspaces", headers=h).json()["items"]
    assert [w["id"] for w in listed] == [str(org.workspace_id)]


def test_team_member_sees_only_their_team(
    api: TestClient, owner_engine: Any, as_member: Any
) -> None:
    org = make_org(owner_engine)
    owner = make_member(owner_engine, org, Role.OWNER)
    api.post(
        f"/v1/orgs/{org.id}/workspaces/{org.workspace_id}/teams",
        json={"name": "other"},
        headers=as_member(owner),
    )
    member = make_member(owner_engine, org, Role.MEMBER, scope=ScopeType.TEAM)
    teams = api.get(
        f"/v1/orgs/{org.id}/workspaces/{org.workspace_id}/teams", headers=as_member(member)
    ).json()
    assert [t["id"] for t in teams["items"]] == [str(org.team_id)]


def test_only_an_owner_grants_owner_or_admin(
    api: TestClient, owner_engine: Any, as_member: Any
) -> None:
    org = make_org(owner_engine)
    admin, owner = (
        make_member(owner_engine, org, Role.ADMIN),
        make_member(owner_engine, org, Role.OWNER),
    )
    target = make_member(owner_engine, org, None)
    for role in ("owner", "admin"):
        body = {"user_id": str(target.user_id), "role": role, "scope_type": "org"}
        assert (
            api.post(
                f"/v1/orgs/{org.id}/memberships", json=body, headers=as_member(admin)
            ).status_code
            == 403
        )
        assert (
            api.post(
                f"/v1/orgs/{org.id}/memberships", json=body, headers=as_member(owner)
            ).status_code
            == 201
        )
    body = {"user_id": str(target.user_id), "role": "member", "scope_type": "org"}
    assert (
        api.post(f"/v1/orgs/{org.id}/memberships", json=body, headers=as_member(admin)).status_code
        == 201
    )
    # The same grant twice conflicts.
    assert (
        api.post(f"/v1/orgs/{org.id}/memberships", json=body, headers=as_member(admin)).status_code
        == 409
    )


def test_memberships_cannot_point_into_another_org(
    api: TestClient, owner_engine: Any, as_member: Any, org_a: OrgFixture, org_b: OrgFixture
) -> None:
    owner = make_member(owner_engine, org_a, Role.OWNER)
    target = make_member(owner_engine, org_a, None)
    foreign_user = make_member(owner_engine, org_b, None)
    base = f"/v1/orgs/{org_a.id}/memberships"
    body = {"user_id": str(foreign_user.user_id), "role": "viewer", "scope_type": "org"}
    assert api.post(base, json=body, headers=as_member(owner)).status_code == 422
    body = {
        "user_id": str(target.user_id),
        "role": "viewer",
        "scope_type": "workspace",
        "workspace_id": str(org_b.workspace_id),
    }
    assert api.post(base, json=body, headers=as_member(owner)).status_code == 422


def _audit_count(owner_engine: Any, action: str, actor: uuid.UUID) -> int:
    with owner_engine.connect() as conn:
        return int(
            conn.execute(
                text(
                    "SELECT count(*) FROM controlplane.audit_events WHERE action = :a AND actor_sub = :s"
                ),
                {"a": action, "s": actor},
            ).scalar_one()
        )


def test_operator_endpoints_need_an_operator_with_mfa(
    api: TestClient, local_issuer: LocalIssuer, owner_engine: Any, org_a: OrgFixture
) -> None:
    def get(sub: uuid.UUID, acr: str) -> Any:
        return api.get(
            "/v1/orgs", headers={"Authorization": f"Bearer {local_issuer.mint(sub, acr=acr)}"}
        )

    not_operator = make_member(owner_engine, org_a, Role.OWNER)
    assert (get(not_operator.sub, "mfa").status_code, get(not_operator.sub, "mfa").json()) == (
        404,
        NOT_FOUND,
    )
    assert (get(ALICE_SUB, "pwd").status_code, get(ALICE_SUB, "pwd").json()) == (404, NOT_FOUND)
    before = _audit_count(owner_engine, "org.list", ALICE_SUB)
    response = get(ALICE_SUB, "mfa")
    assert response.status_code == 200
    assert str(org_a.id) in {o["id"] for o in response.json()["items"]}
    assert _audit_count(owner_engine, "org.list", ALICE_SUB) == before + 1


def test_member_writes_are_audited(
    api: TestClient, owner_engine: Any, as_member: Any, org_a: OrgFixture
) -> None:
    owner = make_member(owner_engine, org_a, Role.OWNER)
    response = api.post(
        f"/v1/orgs/{org_a.id}/workspaces",
        json={"name": f"a-{uuid.uuid4().hex[:6]}"},
        headers=as_member(owner),
    )
    assert response.status_code == 201
    assert _audit_count(owner_engine, "workspace.create", owner.sub) == 1


def test_malformed_ids_are_404_not_500(api: TestClient, owner_engine: Any, as_member: Any) -> None:
    org = make_org(owner_engine)
    owner = make_member(owner_engine, org, Role.OWNER)
    for path in (
        f"/v1/orgs/{org.id}/workspaces/not-a-uuid",
        f"/v1/orgs/{org.id}/workspaces/not-a-uuid/teams",
    ):
        response = api.get(path, headers=as_member(owner))
        assert (response.status_code, response.json()) == (404, NOT_FOUND), path
    # The org itself must be a UUID: FastAPI's validation answers before auth runs.
    assert api.get("/v1/orgs/not-a-uuid", headers=as_member(owner)).status_code in {404, 422}
