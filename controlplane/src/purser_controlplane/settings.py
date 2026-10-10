"""Configuration from PURSER_* environment variables.

Secrets (database passwords inside the URLs, the Keycloak client secret) come
from the environment at run time: in production, injected from AWS Secrets
Manager through the task's IAM role; never baked into the image (D2).
"""

from functools import lru_cache
from typing import Literal, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Asymmetric algorithms only. "none" and every HMAC algorithm are rejected
# outright, so a token can never be verified with a public key as an HMAC secret.
ALLOWED_ALGORITHMS = frozenset(
    {"RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384"}
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PURSER_", extra="ignore")

    env: Literal["dev", "test", "prod"] = "prod"

    # Database URLs, one per role (row-level security, A5). The API uses
    # db_url (the request path, one org per transaction) and db_operator_url
    # (platform-operator routes, the only cross-org role it holds); the
    # sweeper db_sweeper_url; migrations and the bootstrap CLI db_owner_url.
    db_url: SecretStr
    db_operator_url: SecretStr | None = None
    db_sweeper_url: SecretStr | None = None
    db_owner_url: SecretStr | None = None
    db_schema: str = "controlplane"

    # OIDC: the expected issuer is configuration, never taken from a token.
    oidc_issuer: str
    oidc_audience: str = "purser-admin-api"
    # Where to fetch discovery from, when the backchannel differs from the
    # issuer's public URL. The document's "issuer" must still equal oidc_issuer.
    oidc_discovery_url: str | None = None
    oidc_algorithms: list[str] = Field(default_factory=lambda: ["RS256"])
    oidc_leeway_seconds: int = Field(default=30, ge=0, le=120)
    oidc_jwks_min_refresh_seconds: float = Field(default=30.0, ge=0)
    oidc_http_timeout_seconds: float = Field(default=5.0, gt=0)
    # The acr value that proves a second factor (Keycloak's acr.loa.map).
    oidc_mfa_acr: str = "mfa"

    # Keycloak admin API (the control plane's service-account client).
    keycloak_url: str | None = None
    keycloak_realm: str | None = None
    keycloak_client_id: str = "purser-controlplane"
    keycloak_client_secret: SecretStr | None = None
    keycloak_timeout_seconds: float = Field(default=10.0, gt=0)

    # Pending-row sweeper.
    # At least a minute, so the sweeper never races a request still provisioning.
    sweep_after_seconds: int = Field(default=300, ge=60)
    sweep_interval_seconds: int = Field(default=60, ge=1)

    @field_validator("oidc_algorithms")
    @classmethod
    def _asymmetric_only(cls, v: list[str]) -> list[str]:
        bad = [a for a in v if a not in ALLOWED_ALGORITHMS]
        if bad or not v:
            raise ValueError(
                f"oidc_algorithms must be a non-empty subset of {sorted(ALLOWED_ALGORITHMS)}"
            )
        return v

    @model_validator(mode="after")
    def _https_in_prod(self) -> Self:
        # Anyone who can intercept the key fetch can forge any token, and the
        # Keycloak client secret travels on keycloak_url: TLS outside dev.
        if self.env == "prod":
            for name in ("oidc_issuer", "oidc_discovery_url", "keycloak_url"):
                value = getattr(self, name)
                if value is not None and not value.startswith("https://"):
                    raise ValueError(f"{name} must be https:// when PURSER_ENV=prod")
        return self

    @property
    def discovery_url(self) -> str:
        if self.oidc_discovery_url:
            return self.oidc_discovery_url
        return self.oidc_issuer.rstrip("/") + "/.well-known/openid-configuration"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
