"""Token validation end to end against the dev stack's Keycloak (ADR 0003).

The app trusts the purser-dev realm through its discovery document and JWKS,
exactly as in production. Leeway is 0 here so the expiry test doesn't wait
30 seconds; production keeps the default.
"""

import os
import time
import uuid
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from purser_controlplane.app import create_app
from purser_controlplane.seed import ACME_ORG_ID
from tests.conftest import (
    ALICE_TOTP_SECRET,
    KEYCLOAK,
    TestDatabase,
    keycloak_browser_token,
    keycloak_password_token,
    settings_for,
)

ISSUER = os.environ.get("PURSER_OIDC_ISSUER", f"{KEYCLOAK}/realms/purser-dev")
ACME = f"/v1/orgs/{ACME_ORG_ID}"
BOB = ("bob@acme-dev.example", "bob-dev-only-not-a-secret")
ALICE = ("alice@acme-dev.example", "alice-dev-only-not-a-secret")


@pytest.fixture(scope="module")
def api(seeded: TestDatabase) -> TestClient:
    return TestClient(create_app(settings_for(ISSUER, db=seeded, oidc_leeway_seconds=0)))


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def unverified(token: str) -> dict[str, Any]:
    return dict(jwt.decode(token, options={"verify_signature": False}))


def test_valid_keycloak_token(api: TestClient) -> None:
    response = api.get(ACME, headers=bearer(keycloak_password_token(*BOB)))
    assert response.status_code == 200
    assert response.json()["id"] == str(ACME_ORG_ID)


def test_expired(api: TestClient) -> None:
    token = keycloak_password_token(*BOB, client_id="purser-dev-shortlived")
    assert api.get(ACME, headers=bearer(token)).status_code == 200
    time.sleep(max(0.0, unverified(token)["exp"] - time.time()) + 1.5)
    assert api.get(ACME, headers=bearer(token)).status_code == 401


def test_wrong_issuer(api: TestClient) -> None:
    token = keycloak_password_token(
        "carol@other-dev.example",
        "carol-dev-only-not-a-secret",
        realm_url=f"{KEYCLOAK}/realms/purser-dev-other",
    )
    assert unverified(token)["iss"] != ISSUER
    assert api.get(ACME, headers=bearer(token)).status_code == 401


def test_wrong_audience(api: TestClient) -> None:
    token = keycloak_password_token(*BOB, client_id="purser-dev-noaud")
    assert "purser-admin-api" not in str(unverified(token).get("aud"))
    assert api.get(ACME, headers=bearer(token)).status_code == 401


def test_alg_none(api: TestClient) -> None:
    claims = unverified(keycloak_password_token(*BOB))
    kid = jwt.get_unverified_header(keycloak_password_token(*BOB))["kid"]
    token = jwt.encode(claims, None, algorithm="none", headers={"kid": kid})
    assert api.get(ACME, headers=bearer(token)).status_code == 401


def test_unknown_key(api: TestClient) -> None:
    claims = unverified(keycloak_password_token(*BOB))
    rogue = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = jwt.encode(claims, rogue, algorithm="RS256", headers={"kid": f"rogue-{uuid.uuid4()}"})
    assert api.get(ACME, headers=bearer(token)).status_code == 401


def test_real_signature_with_a_swapped_kid(api: TestClient) -> None:
    """A real Keycloak signature over modified claims fails verification."""
    header, _, signature = keycloak_password_token(*BOB).split(".")
    claims = unverified(keycloak_password_token(*BOB)) | {"sub": str(uuid.uuid4())}
    payload = jwt.utils.base64url_encode(jwt.api_jws.json.dumps(claims).encode()).decode()
    assert api.get(ACME, headers=bearer(f"{header}.{payload}.{signature}")).status_code == 401


@pytest.fixture(scope="module")
def alice_mfa() -> str:
    # One TOTP login per module: Keycloak accepts each code only once.
    return keycloak_browser_token(*ALICE, acr="mfa", totp_secret=ALICE_TOTP_SECRET)


def test_operator_endpoints_with_real_mfa(api: TestClient, alice_mfa: str) -> None:
    assert unverified(alice_mfa)["acr"] == "mfa"
    assert api.get("/v1/orgs", headers=bearer(alice_mfa)).status_code == 200


def test_operator_endpoints_refuse_a_password_only_login(api: TestClient) -> None:
    token = keycloak_browser_token(*ALICE, acr="pwd")
    assert unverified(token)["acr"] == "pwd"
    response = api.get("/v1/orgs", headers=bearer(token))
    assert (response.status_code, response.json()) == (404, {"detail": "Not Found"})
    # The same token still works for alice's own org.
    assert api.get(ACME, headers=bearer(token)).status_code == 200


def test_readiness_against_keycloak(api: TestClient) -> None:
    assert api.get("/readyz").json() == {"status": "ready"}
