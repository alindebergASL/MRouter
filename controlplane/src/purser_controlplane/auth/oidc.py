"""Access-token validation against the auth service's discovery document and JWKS.

Stock PyJWT does the cryptography and the registered-claim checks. This
module adds what a library can't know:

- The issuer comes from configuration, and the discovery document must name
  that same issuer, so a misconfigured or spoofed document can't change it.
- Only asymmetric algorithms from an allowlist are accepted, and the key's
  type and declared algorithm must match the token's header.
- An unknown key ID refreshes the JWKS at most once per interval, so tokens
  with random kids can't make us hammer the auth service.
- Only identifiers leave this module: the subject, organization IDs, and the
  authentication level (acr). Tokens are never logged or stored.
"""

import logging
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx
import jwt
from jwt import PyJWK, PyJWKClient
from jwt.exceptions import PyJWKClientConnectionError, PyJWKClientError

from purser_controlplane.settings import Settings

log = logging.getLogger("purser.auth")

_KEY_TYPES = {"RS": "RSA", "PS": "RSA", "ES": "EC"}
MAX_TOKEN_BYTES = 8192


class TokenError(Exception):
    """The token is not acceptable. `reason` is a fixed code, safe to log."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class AuthUnavailableError(Exception):
    """Discovery or the JWKS can't be fetched right now."""


@dataclass(frozen=True)
class Principal:
    sub: uuid.UUID
    org_ids: frozenset[uuid.UUID]
    acr: str | None


class _RateLimitedJWKClient(PyJWKClient):
    def __init__(self, uri: str, *, min_refresh_seconds: float, timeout: float) -> None:
        super().__init__(uri, cache_keys=False, cache_jwk_set=True, lifespan=300, timeout=timeout)
        self._min_refresh = min_refresh_seconds
        self._last_forced_refresh = float("-inf")
        self._lock = threading.Lock()
        self._http_timeout = timeout

    def fetch_data(self) -> Any:
        # PyJWT's own fetch uses urllib, which follows proxy environment
        # variables; fetch the keys the way discovery is fetched, with none.
        try:
            with httpx.Client(timeout=self._http_timeout, trust_env=False) as client:
                response = client.get(self.uri)
                response.raise_for_status()
                jwk_set = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise PyJWKClientConnectionError("jwks fetch failed") from exc
        if not isinstance(jwk_set, dict):
            raise PyJWKClientError("jwks is not a JSON object")
        if self.jwk_set_cache is not None:
            self.jwk_set_cache.put(jwk_set)
        return jwk_set

    def get_signing_key(self, kid: str) -> PyJWK:
        key = self.match_kid(self.get_signing_keys(), kid)
        if key is None:
            with self._lock:
                now = time.monotonic()
                if now - self._last_forced_refresh >= self._min_refresh:
                    self._last_forced_refresh = now
                    key = self.match_kid(self.get_signing_keys(refresh=True), kid)
        if key is None:
            raise TokenError("unknown_kid")
        return key


class OIDCVerifier:
    def __init__(self, settings: Settings) -> None:
        self._issuer = settings.oidc_issuer
        self._audience = settings.oidc_audience
        self._algorithms = list(settings.oidc_algorithms)
        self._leeway = settings.oidc_leeway_seconds
        self._discovery_url = settings.discovery_url
        self._min_refresh = settings.oidc_jwks_min_refresh_seconds
        self._timeout = settings.oidc_http_timeout_seconds
        self._require_https = settings.env == "prod"
        self._jwks: _RateLimitedJWKClient | None = None
        self._lock = threading.Lock()
        # While the auth service is unreachable, retry discovery at most this
        # often, so unauthenticated requests can't pile up blocking fetches.
        self._load_retry_seconds = 5.0
        self._last_failed_load = float("-inf")

    @property
    def ready(self) -> bool:
        return self._jwks is not None

    def load(self) -> None:
        """Fetch discovery, check its issuer, and prime the JWKS cache."""
        if self._jwks is not None:
            return
        if time.monotonic() - self._last_failed_load < self._load_retry_seconds:
            raise AuthUnavailableError("discovery recently failed")
        with self._lock:
            if self._jwks is not None:
                return
            try:
                self._load_locked()
            except AuthUnavailableError:
                self._last_failed_load = time.monotonic()
                raise

    def _load_locked(self) -> None:
        try:
            with httpx.Client(timeout=self._timeout, trust_env=False) as client:
                response = client.get(self._discovery_url)
                response.raise_for_status()
                document = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise AuthUnavailableError("discovery unavailable") from exc
        if document.get("issuer") != self._issuer:
            # Never adopt an issuer from the network.
            raise AuthUnavailableError("discovery issuer does not match the configured issuer")
        jwks_uri = document.get("jwks_uri")
        if not isinstance(jwks_uri, str) or _origin(jwks_uri) != _origin(self._discovery_url):
            # Keys come from where discovery came from, never elsewhere.
            raise AuthUnavailableError("jwks_uri is not on the discovery document's origin")
        if self._require_https and not jwks_uri.startswith("https://"):
            raise AuthUnavailableError("jwks_uri is not https")
        jwks = _RateLimitedJWKClient(
            jwks_uri, min_refresh_seconds=self._min_refresh, timeout=self._timeout
        )
        try:
            jwks.get_signing_keys()
        except PyJWKClientError as exc:
            raise AuthUnavailableError("jwks unavailable") from exc
        self._jwks = jwks

    def verify(self, token: str) -> Principal:
        if not token or len(token) > MAX_TOKEN_BYTES:
            raise TokenError("malformed")
        if self._jwks is None:
            self.load()
        assert self._jwks is not None

        try:
            header = jwt.get_unverified_header(token)
        except jwt.InvalidTokenError as exc:
            raise TokenError("malformed") from exc
        alg = header.get("alg")
        if alg not in self._algorithms:
            raise TokenError("algorithm_not_allowed")
        kid = header.get("kid")
        if not isinstance(kid, str) or not kid:
            raise TokenError("missing_kid")

        try:
            key = self._jwks.get_signing_key(kid)
        except PyJWKClientConnectionError as exc:
            raise AuthUnavailableError("jwks unavailable") from exc
        except PyJWKClientError as exc:
            raise TokenError("unknown_kid") from exc
        if key.key_type != _KEY_TYPES[alg[:2]]:
            raise TokenError("key_type_mismatch")
        declared = key._jwk_data.get("alg")
        if declared is not None and declared != alg:
            raise TokenError("key_algorithm_mismatch")

        try:
            claims: dict[str, Any] = jwt.decode(
                token,
                key=key.key,
                algorithms=[alg],
                audience=self._audience,
                issuer=self._issuer,
                leeway=self._leeway,
                options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise TokenError("expired") from exc
        except jwt.ImmatureSignatureError as exc:
            raise TokenError("not_yet_valid") from exc
        except jwt.InvalidAudienceError as exc:
            raise TokenError("wrong_audience") from exc
        except jwt.InvalidIssuerError as exc:
            raise TokenError("wrong_issuer") from exc
        except jwt.MissingRequiredClaimError as exc:
            raise TokenError("missing_claim") from exc
        except jwt.InvalidSignatureError as exc:
            raise TokenError("bad_signature") from exc
        except jwt.InvalidTokenError as exc:
            raise TokenError("invalid") from exc

        # Keycloak marks access tokens typ=Bearer; ID and refresh tokens differ.
        if claims.get("typ") != "Bearer":
            raise TokenError("not_an_access_token")
        try:
            sub = uuid.UUID(str(claims["sub"]))
        except ValueError as exc:
            raise TokenError("bad_subject") from exc
        acr = claims.get("acr")
        return Principal(
            sub=sub,
            org_ids=_organization_ids(claims.get("organization")),
            acr=acr if isinstance(acr, str) else None,
        )


def _origin(url: str) -> tuple[str, str, int | None]:
    parts = urlsplit(url)
    return parts.scheme, parts.hostname or "", parts.port


def _organization_ids(claim: object) -> frozenset[uuid.UUID]:
    """IDs from Keycloak's organization claim: {"<alias>": {"id": "<uuid>"}}.

    Aliases can change, so only IDs count. Anything else is ignored.
    """
    if not isinstance(claim, dict):
        return frozenset()
    ids = set()
    for value in claim.values():
        if isinstance(value, dict) and isinstance(value.get("id"), str):
            try:
                ids.add(uuid.UUID(value["id"]))
            except ValueError:
                continue
    return frozenset(ids)
