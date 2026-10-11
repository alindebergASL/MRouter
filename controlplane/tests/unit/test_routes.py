"""Deny by default, checked over the route inventory (no stack needed)."""

import re
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from purser_controlplane.api.deps import PUBLIC_PATHS
from purser_controlplane.app import api_routes, build_app, check_deny_by_default, create_app
from tests.conftest import LocalIssuer, settings_for

ROUTES = api_routes(build_app())
PROTECTED = [
    (method, r.path) for r in ROUTES if r.path not in PUBLIC_PATHS for method in sorted(r.methods)
]


def test_inventory_sees_every_route() -> None:
    # Guards the walk itself: an empty inventory would make every check vacuous.
    assert len(PROTECTED) == 13
    assert {"/v1/orgs", "/v1/orgs/{org_id}/memberships"} <= {r.path for r in ROUTES}


def test_every_route_declares_a_permission_or_is_public() -> None:
    for route in ROUTES:
        assert route.path in PUBLIC_PATHS or route.permission, route.path


def test_public_routes_are_only_health_and_readiness() -> None:
    assert {r.path for r in ROUTES if r.permission is None} == {"/healthz", "/readyz"}


def test_public_means_get_on_health_only() -> None:
    app = build_app()

    @app.post("/healthz")
    def sneaky() -> dict[str, str]:
        return {}

    with pytest.raises(RuntimeError, match="POST /healthz"):
        check_deny_by_default(app)


def test_an_app_with_no_routes_does_not_pass() -> None:
    with pytest.raises(RuntimeError, match="no API routes"):
        check_deny_by_default(FastAPI())


def test_an_unguarded_route_stops_the_app() -> None:
    app = build_app()

    @app.get("/v1/oops")
    def oops() -> dict[str, str]:
        return {}

    with pytest.raises(RuntimeError, match="deny by default"):
        check_deny_by_default(app)


def test_api_documents_are_not_served(local_issuer: LocalIssuer) -> None:
    client = TestClient(create_app(settings_for(local_issuer.issuer)))
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404


def test_the_api_refuses_to_start_without_its_operator_role(local_issuer: LocalIssuer) -> None:
    # Operator routes never fall back to the request path's role (spec 4).
    with pytest.raises(RuntimeError, match="PURSER_DB_OPERATOR_URL"):
        create_app(settings_for(local_issuer.issuer, db_operator_url=None))


def _fill(path: str) -> str:
    return re.sub(r"\{[^}]+\}", lambda _: str(uuid.uuid4()), path)


@pytest.fixture(scope="module")
def client(local_issuer: LocalIssuer) -> TestClient:
    # The database URL points nowhere: 401 must come before any query.
    return TestClient(create_app(settings_for(local_issuer.issuer)), raise_server_exceptions=False)


@pytest.mark.parametrize(("method", "path"), PROTECTED)
def test_no_token_401_everywhere(client: TestClient, method: str, path: str) -> None:
    response = client.request(method, _fill(path), json={})
    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith("Bearer")


@pytest.mark.parametrize(("method", "path"), PROTECTED)
def test_bad_token_401_everywhere(client: TestClient, method: str, path: str) -> None:
    response = client.request(
        method, _fill(path), json={}, headers={"Authorization": "Bearer not-a-jwt"}
    )
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == 'Bearer error="invalid_token"'


def test_health_is_public(client: TestClient) -> None:
    assert client.get("/healthz").json() == {"status": "ok"}


def test_unknown_routes_are_404_not_405(client: TestClient) -> None:
    assert isinstance(build_app(), FastAPI)
    assert client.get("/v1/nothing-here").status_code == 404
