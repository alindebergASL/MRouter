"""Shared fixtures.

Unit tests need nothing running. Tests under tests/integration/ are marked
`stack` and need the dev stack (make dev-up): they create a throwaway database
per session, migrate it as the owner role, and connect the API as the
non-owner app role, so row-level security applies exactly as in production.

Two token sources:
- LocalIssuer: a discovery document and JWKS served from this process, with a
  key the tests hold, so they can mint any subject, org, or acr.
- The dev stack's Keycloak, for end-to-end token validation and MFA.
"""

import base64
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import struct
import time
import urllib.parse
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from pytest_httpserver import HTTPServer
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from purser_controlplane.settings import Settings

REPO = Path(__file__).resolve().parents[2]
DB_INIT_SQL = REPO / "deploy/compose/controlplane/controlplane-db.sql"
AUDIENCE = "purser-admin-api"


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if "integration" in item.path.parts:
            item.add_marker(pytest.mark.stack)


# -- local issuer -------------------------------------------------------------


def _jwk(public_key: rsa.RSAPublicKey, kid: str) -> dict[str, Any]:
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(public_key))
    return {**jwk, "kid": kid, "use": "sig", "alg": "RS256"}


@dataclass
class LocalIssuer:
    server: HTTPServer
    issuer: str
    keys: dict[str, rsa.RSAPrivateKey] = field(default_factory=dict)
    published: list[str] = field(default_factory=list)

    def add_key(self, kid: str, *, publish: bool = True) -> rsa.RSAPrivateKey:
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.keys[kid] = key
        if publish:
            self.published.append(kid)
        return key

    def jwks(self) -> dict[str, Any]:
        return {"keys": [_jwk(self.keys[k].public_key(), k) for k in self.published]}

    def jwks_requests(self) -> int:
        return sum(1 for request, _ in self.server.log if request.path.endswith("/certs"))

    def mint(
        self,
        subject: uuid.UUID | str,
        *,
        orgs: dict[str, uuid.UUID] | None = None,
        acr: str | None = "pwd",
        kid: str = "k1",
        lifetime: int = 300,
        **overrides: Any,
    ) -> str:
        now = int(time.time())
        claims: dict[str, Any] = {
            "iss": self.issuer,
            "aud": AUDIENCE,
            "sub": str(subject),
            "iat": now,
            "exp": now + lifetime,
            "typ": "Bearer",
            "organization": {alias: {"id": str(org_id)} for alias, org_id in (orgs or {}).items()},
        }
        if acr is not None:
            claims["acr"] = acr
        claims.update(overrides)
        claims = {k: v for k, v in claims.items() if v is not None}
        return jwt.encode(claims, self.keys[kid], algorithm="RS256", headers={"kid": kid})


@pytest.fixture(scope="session")
def local_issuer() -> Iterator[LocalIssuer]:
    server = HTTPServer(host="127.0.0.1", port=0)
    server.start()
    issuer = LocalIssuer(server=server, issuer=server.url_for("/realms/local"))
    issuer.add_key("k1")
    server.expect_request("/realms/local/.well-known/openid-configuration").respond_with_handler(
        lambda _: _json_response(
            {
                "issuer": issuer.issuer,
                "jwks_uri": server.url_for("/realms/local/protocol/openid-connect/certs"),
            }
        )
    )
    server.expect_request("/realms/local/protocol/openid-connect/certs").respond_with_handler(
        lambda _: _json_response(issuer.jwks())
    )
    yield issuer
    server.clear()
    if server.is_running():
        server.stop()


def _json_response(body: dict[str, Any]) -> Any:
    from werkzeug import Response

    return Response(json.dumps(body), content_type="application/json")


NO_DATABASE = "postgresql+psycopg://nobody@127.0.0.1:1/none"


def settings_for(issuer: str, db: "TestDatabase | None" = None, **overrides: Any) -> Settings:
    """Settings for an app on `db`'s app and operator roles (or on no database)."""
    values: dict[str, Any] = {
        "env": "test",
        "db_url": db.app_url if db else NO_DATABASE,
        "db_operator_url": db.operator_url if db else NO_DATABASE,
        "oidc_issuer": issuer,
        "oidc_jwks_min_refresh_seconds": 30,
        "keycloak_url": None,
        "keycloak_realm": None,
        "keycloak_client_secret": None,
    }
    values.update(overrides)
    return Settings(**values)


# -- throwaway database ---------------------------------------------------------


@dataclass(frozen=True)
class TestDatabase:
    __test__ = False  # not a pytest test class

    name: str
    owner_url: str
    app_url: str
    sweeper_url: str
    operator_url: str
    admin_url: str


def _with_database(url: str, name: str) -> str:
    return make_url(url).set(database=name).render_as_string(hide_password=False)


def create_test_database(migrate: bool = True) -> TestDatabase:
    admin_url = os.environ.get("PURSER_TEST_DB_ADMIN_URL")
    if not admin_url:
        pytest.skip("PURSER_TEST_DB_ADMIN_URL is not set (run through make cp-test)")
    name = f"purser_cp_test_{secrets.token_hex(4)}"
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    admin.dispose()
    init = create_engine(_with_database(admin_url, name), isolation_level="AUTOCOMMIT")
    with init.connect() as connection:
        connection.exec_driver_sql(DB_INIT_SQL.read_text())
        connection.execute(
            text(
                f'GRANT CONNECT ON DATABASE "{name}"'
                " TO purser_cp_owner, purser_cp_app, purser_cp_sweeper, purser_cp_operator"
            )
        )
    init.dispose()
    db = TestDatabase(
        name=name,
        owner_url=_with_database(os.environ["PURSER_DB_OWNER_URL"], name),
        app_url=_with_database(os.environ["PURSER_DB_URL"], name),
        sweeper_url=_with_database(os.environ["PURSER_DB_SWEEPER_URL"], name),
        operator_url=_with_database(os.environ["PURSER_DB_OPERATOR_URL"], name),
        admin_url=_with_database(admin_url, name),
    )
    if migrate:
        migrate_to(db, "head")
    return db


def drop_test_database(db: TestDatabase) -> None:
    admin = create_engine(os.environ["PURSER_TEST_DB_ADMIN_URL"], isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'DROP DATABASE IF EXISTS "{db.name}" WITH (FORCE)'))
    admin.dispose()


def alembic_config(db: TestDatabase) -> Any:
    from alembic.config import Config

    config = Config(str(REPO / "controlplane/alembic.ini"))
    config.attributes["url"] = db.owner_url
    config.attributes["configure_logging"] = False
    return config


def migrate_to(db: TestDatabase, revision: str) -> None:
    from alembic import command

    if revision == "base":
        command.downgrade(alembic_config(db), "base")
    else:
        command.upgrade(alembic_config(db), revision)


@pytest.fixture(scope="session")
def test_db() -> Iterator[TestDatabase]:
    db = create_test_database()
    yield db
    drop_test_database(db)


@pytest.fixture(scope="session")
def owner_engine(test_db: TestDatabase) -> Iterator[Any]:
    engine = create_engine(test_db.owner_url)
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def seeded(test_db: TestDatabase, owner_engine: Any) -> TestDatabase:
    from sqlalchemy.orm import Session

    from purser_controlplane.seed import seed_dev

    with Session(owner_engine) as session:
        seed_dev(session)
        session.commit()
    return test_db


# -- the dev stack's Keycloak ---------------------------------------------------

KEYCLOAK = os.environ.get("PURSER_KEYCLOAK_URL", "http://localhost:58080")
REALM_URL = f"{KEYCLOAK}/realms/purser-dev"
ALICE_TOTP_SECRET = b"alice-totp-dev-only-not-a-secret"


def keycloak_password_token(
    username: str, password: str, client_id: str = "purser-dev-test", realm_url: str = REALM_URL
) -> str:
    response = httpx.post(
        f"{realm_url}/protocol/openid-connect/token",
        data={
            "grant_type": "password",
            "client_id": client_id,
            "username": username,
            "password": password,
        },
        trust_env=False,
    )
    response.raise_for_status()
    return str(response.json()["access_token"])


def _totp(secret: bytes, at: float) -> str:
    digest = hmac.new(secret, struct.pack(">Q", int(at) // 30), hashlib.sha1).digest()
    offset = digest[-1] & 15
    code = (struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF) % 1_000_000
    return f"{code:06d}"


def keycloak_browser_token(
    username: str, password: str, *, acr: str, totp_secret: bytes | None = None
) -> str:
    """Log in through the browser flow (authorization code with PKCE) and return the access token.

    Keycloak sets Secure cookies even over http://localhost; browsers send them
    to localhost, Python's cookie jar doesn't, so cookies are carried by hand.
    """
    verifier = secrets.token_urlsafe(48)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
    redirect_uri = "http://localhost/dev-test-callback"
    cookies: dict[str, str] = {}

    def send(client: httpx.Client, method: str, url: str, **kwargs: Any) -> httpx.Response:
        headers = {"Cookie": "; ".join(f"{k}={v}" for k, v in cookies.items())} if cookies else {}
        response = client.request(method, url, headers=headers, **kwargs)
        for header in response.headers.get_list("set-cookie"):
            name, _, rest = header.partition("=")
            cookies[name.strip()] = rest.split(";", 1)[0]
        return response

    def form_action(body: str) -> str:
        match = re.search(r'action="([^"]+)"', body)
        assert match, "no form in Keycloak's page"
        return html.unescape(match.group(1))

    with httpx.Client(follow_redirects=False, trust_env=False, timeout=10) as client:
        query = urllib.parse.urlencode(
            {
                "client_id": "purser-dev-test",
                "response_type": "code",
                "scope": "openid",
                "redirect_uri": redirect_uri,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": secrets.token_urlsafe(8),
                "acr_values": acr,
            }
        )
        page = send(client, "GET", f"{REALM_URL}/protocol/openid-connect/auth?{query}")
        response = send(
            client,
            "POST",
            form_action(page.text),
            data={"username": username, "password": password},
        )
        for attempt in range(2):
            if 'name="otp"' not in response.text:
                break
            assert totp_secret is not None, "Keycloak asked for a second factor"
            if attempt:
                # Keycloak accepts each code once; wait for the next window.
                time.sleep(30 - time.time() % 30 + 1)
            response = send(
                client,
                "POST",
                form_action(response.text),
                data={"otp": _totp(totp_secret, time.time())},
            )
        assert response.status_code == 302, f"login did not complete (HTTP {response.status_code})"
        code = urllib.parse.parse_qs(urllib.parse.urlparse(response.headers["location"]).query)[
            "code"
        ][0]
        token = client.post(
            f"{REALM_URL}/protocol/openid-connect/token",
            data={
                "grant_type": "authorization_code",
                "client_id": "purser-dev-test",
                "code": code,
                "redirect_uri": redirect_uri,
                "code_verifier": verifier,
            },
        )
        token.raise_for_status()
        return str(token.json()["access_token"])
