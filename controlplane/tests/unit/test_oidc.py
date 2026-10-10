"""Token validation, one check at a time, against a local issuer (no stack needed)."""

import time
import uuid

import jwt
import pytest
from cryptography.hazmat.primitives import serialization

from purser_controlplane.auth.oidc import AuthUnavailableError, OIDCVerifier, TokenError
from purser_controlplane.settings import Settings
from tests.conftest import LocalIssuer, settings_for

ORG = uuid.UUID("11111111-1111-4111-8111-111111111111")


@pytest.fixture
def verifier(local_issuer: LocalIssuer) -> OIDCVerifier:
    v = OIDCVerifier(settings_for(local_issuer.issuer, oidc_leeway_seconds=0))
    v.load()
    return v


def reason(verifier: OIDCVerifier, token: str) -> str:
    with pytest.raises(TokenError) as excinfo:
        verifier.verify(token)
    return excinfo.value.reason


def test_valid_token_yields_identifiers_only(
    verifier: OIDCVerifier, local_issuer: LocalIssuer
) -> None:
    sub = uuid.uuid4()
    principal = verifier.verify(local_issuer.mint(sub, orgs={"acme": ORG}, acr="mfa"))
    assert principal.sub == sub
    assert principal.org_ids == {ORG}
    assert principal.acr == "mfa"


def test_expired(verifier: OIDCVerifier, local_issuer: LocalIssuer) -> None:
    now = int(time.time())
    assert (
        reason(verifier, local_issuer.mint(uuid.uuid4(), iat=now - 600, exp=now - 60)) == "expired"
    )


def test_not_yet_valid(verifier: OIDCVerifier, local_issuer: LocalIssuer) -> None:
    token = local_issuer.mint(uuid.uuid4(), nbf=int(time.time()) + 600)
    assert reason(verifier, token) == "not_yet_valid"


def test_wrong_issuer_signed_by_a_trusted_key(
    verifier: OIDCVerifier, local_issuer: LocalIssuer
) -> None:
    token = local_issuer.mint(uuid.uuid4(), iss="https://elsewhere.example/realms/purser-dev")
    assert reason(verifier, token) == "wrong_issuer"


def test_wrong_audience(verifier: OIDCVerifier, local_issuer: LocalIssuer) -> None:
    assert reason(verifier, local_issuer.mint(uuid.uuid4(), aud="account")) == "wrong_audience"


def test_alg_none(verifier: OIDCVerifier, local_issuer: LocalIssuer) -> None:
    claims = jwt.decode(local_issuer.mint(uuid.uuid4()), options={"verify_signature": False})
    token = jwt.encode(claims, None, algorithm="none", headers={"kid": "k1"})
    assert reason(verifier, token) == "algorithm_not_allowed"


def test_hs256_with_the_public_key_as_secret(
    verifier: OIDCVerifier, local_issuer: LocalIssuer
) -> None:
    """Algorithm confusion: an HMAC token keyed with the published RSA public key."""
    claims = jwt.decode(local_issuer.mint(uuid.uuid4()), options={"verify_signature": False})
    public_pem = (
        local_issuer.keys["k1"]
        .public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    )
    header = {"alg": "HS256", "typ": "JWT", "kid": "k1"}
    signing_input = b".".join(
        jwt.utils.base64url_encode(jwt.api_jws.json.dumps(part, separators=(",", ":")).encode())
        for part in (header, claims)
    )
    import hashlib
    import hmac

    signature = jwt.utils.base64url_encode(
        hmac.new(public_pem, signing_input, hashlib.sha256).digest()
    )
    token = (signing_input + b"." + signature).decode()
    assert reason(verifier, token) == "algorithm_not_allowed"


def test_unknown_key_id(verifier: OIDCVerifier, local_issuer: LocalIssuer) -> None:
    local_issuer.add_key("unpublished", publish=False)
    assert reason(verifier, local_issuer.mint(uuid.uuid4(), kid="unpublished")) == "unknown_kid"


def test_unknown_key_ids_refresh_the_jwks_at_most_once_per_interval(
    local_issuer: LocalIssuer,
) -> None:
    local_issuer.add_key("rogue", publish=False)
    v = OIDCVerifier(settings_for(local_issuer.issuer, oidc_jwks_min_refresh_seconds=60))
    v.load()
    before = local_issuer.jwks_requests()
    for _ in range(5):
        with pytest.raises(TokenError):
            v.verify(local_issuer.mint(uuid.uuid4(), kid="rogue"))
    assert local_issuer.jwks_requests() - before == 1


def test_rotated_key_is_picked_up(local_issuer: LocalIssuer) -> None:
    v = OIDCVerifier(settings_for(local_issuer.issuer, oidc_jwks_min_refresh_seconds=0))
    v.load()
    local_issuer.add_key("rotated")
    sub = uuid.uuid4()
    assert v.verify(local_issuer.mint(sub, kid="rotated")).sub == sub


@pytest.mark.parametrize("claim", ["sub", "exp", "iat", "aud"])
def test_missing_required_claim(
    verifier: OIDCVerifier, local_issuer: LocalIssuer, claim: str
) -> None:
    token = local_issuer.mint(uuid.uuid4(), **{claim: None})
    assert reason(verifier, token) in {"missing_claim", "wrong_audience"}


def test_a_token_without_typ_is_not_an_access_token(
    verifier: OIDCVerifier, local_issuer: LocalIssuer
) -> None:
    assert reason(verifier, local_issuer.mint(uuid.uuid4(), typ=None)) == "not_an_access_token"


def test_id_token_is_not_an_access_token(verifier: OIDCVerifier, local_issuer: LocalIssuer) -> None:
    assert reason(verifier, local_issuer.mint(uuid.uuid4(), typ="ID")) == "not_an_access_token"


def test_tampered_signature(verifier: OIDCVerifier, local_issuer: LocalIssuer) -> None:
    header, _payload, signature = local_issuer.mint(uuid.uuid4()).split(".")
    forged_payload = jwt.utils.base64url_encode(b'{"sub":"x"}').decode()
    assert reason(verifier, f"{header}.{forged_payload}.{signature}") in {
        "bad_signature",
        "invalid",
    }


def test_garbage_and_oversized_tokens(verifier: OIDCVerifier) -> None:
    assert reason(verifier, "not-a-jwt") == "malformed"
    assert reason(verifier, "a" * 9000) == "malformed"


def test_organization_claim_keys_on_ids_not_aliases(
    verifier: OIDCVerifier, local_issuer: LocalIssuer
) -> None:
    token = local_issuer.mint(
        uuid.uuid4(), organization={"acme": {"id": str(ORG)}, "junk": "x", "bad": {"id": "nope"}}
    )
    assert verifier.verify(token).org_ids == {ORG}
    token = local_issuer.mint(uuid.uuid4(), organization=["acme"])
    assert verifier.verify(token).org_ids == frozenset()


def test_discovery_must_name_the_configured_issuer(local_issuer: LocalIssuer) -> None:
    # Fetch the real document, but expect a different issuer than it names.
    v = OIDCVerifier(
        settings_for(
            "https://purser.example/realms/purser",
            oidc_discovery_url=local_issuer.issuer + "/.well-known/openid-configuration",
        )
    )
    with pytest.raises(AuthUnavailableError):
        v.load()


@pytest.mark.parametrize("algorithms", [["HS256"], ["none"], ["RS256", "HS512"], []])
def test_settings_refuse_symmetric_or_none_algorithms(algorithms: list[str]) -> None:
    with pytest.raises(ValueError, match="oidc_algorithms"):
        Settings(
            db_url="postgresql://x", oidc_issuer="https://i.example", oidc_algorithms=algorithms
        )


def test_jwks_must_come_from_the_discovery_origin(local_issuer: LocalIssuer) -> None:
    from pytest_httpserver import HTTPServer

    from tests.conftest import _json_response

    rogue = HTTPServer(host="127.0.0.1", port=0)
    rogue.start()
    try:
        rogue.expect_request("/discovery").respond_with_handler(
            lambda _: _json_response(
                {
                    "issuer": local_issuer.issuer,
                    "jwks_uri": local_issuer.server.url_for(
                        "/realms/local/protocol/openid-connect/certs"
                    ),
                }
            )
        )
        v = OIDCVerifier(
            settings_for(local_issuer.issuer, oidc_discovery_url=rogue.url_for("/discovery"))
        )
        with pytest.raises(AuthUnavailableError, match="origin"):
            v.load()
    finally:
        rogue.stop()


def test_failed_discovery_is_not_retried_on_every_request() -> None:
    v = OIDCVerifier(
        settings_for("http://127.0.0.1:9/realms/nowhere", oidc_http_timeout_seconds=0.5)
    )
    with pytest.raises(AuthUnavailableError, match="discovery unavailable"):
        v.load()
    started = time.monotonic()
    for _ in range(20):
        with pytest.raises(AuthUnavailableError, match="recently failed"):
            v.verify("a.b.c")
    assert time.monotonic() - started < 0.5


@pytest.mark.parametrize("field", ["oidc_issuer", "oidc_discovery_url", "keycloak_url"])
def test_production_requires_https(field: str) -> None:
    https = "https://id.example/realms/purser"
    values = {
        "env": "prod",
        "db_url": "postgresql://x",
        "oidc_issuer": https,
        "oidc_discovery_url": https,
        "keycloak_url": https,
        field: "http://id.example/realms/purser",
    }
    with pytest.raises(ValueError, match=f"{field} must be https"):
        Settings(**values)
    Settings(**{**values, field: "https://id.example/realms/purser"})
