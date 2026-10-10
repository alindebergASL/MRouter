"""Keycloak admin-API client for the control plane's service account (ADR 0003).

Our database is the source of truth; Keycloak holds the identity. Objects the
control plane creates carry our row's ID in a `purser_id` attribute, which is
the idempotency key: before creating, look it up; the sweeper uses the same
lookup to finish or roll back pending rows. Organizations use our org ID as
both name and alias, so no customer text reaches token claims or Keycloak's
unique-name constraints; the display name lives in our database.

Response bodies can contain personal data, so only status codes are logged.
"""

import logging
import threading
import time
import uuid
from typing import Any

import httpx

from purser_controlplane.settings import Settings

log = logging.getLogger("purser.keycloak")

PURSER_ID = "purser_id"
SECOND_FACTOR_TYPES = frozenset({"otp", "webauthn", "webauthn-passwordless"})
NEW_USER_ACTIONS = ("UPDATE_PASSWORD", "CONFIGURE_TOTP")


class KeycloakError(Exception):
    """A Keycloak admin call failed. The message carries no response body."""


class KeycloakConflictError(KeycloakError):
    pass


class KeycloakAdmin:
    def __init__(
        self, base_url: str, realm: str, client_id: str, client_secret: str, timeout: float
    ) -> None:
        self._token_url = f"{base_url.rstrip('/')}/realms/{realm}/protocol/openid-connect/token"
        self._client_id = client_id
        self._client_secret = client_secret
        self._http = httpx.Client(
            base_url=f"{base_url.rstrip('/')}/admin/realms/{realm}",
            timeout=timeout,
            trust_env=False,
        )
        self._token: str | None = None
        self._token_expires = 0.0
        self._lock = threading.Lock()

    @classmethod
    def from_settings(cls, settings: Settings) -> "KeycloakAdmin | None":
        if not (
            settings.keycloak_url and settings.keycloak_realm and settings.keycloak_client_secret
        ):
            return None
        return cls(
            settings.keycloak_url,
            settings.keycloak_realm,
            settings.keycloak_client_id,
            settings.keycloak_client_secret.get_secret_value(),
            settings.keycloak_timeout_seconds,
        )

    def close(self) -> None:
        self._http.close()

    # -- transport -----------------------------------------------------------

    def _access_token(self) -> str:
        with self._lock:
            if self._token is None or time.monotonic() > self._token_expires:
                try:
                    response = httpx.post(
                        self._token_url,
                        data={"grant_type": "client_credentials"},
                        auth=(self._client_id, self._client_secret),
                        timeout=self._http.timeout,
                        trust_env=False,
                    )
                except httpx.HTTPError as exc:
                    raise KeycloakError("token request failed") from exc
                if response.status_code != 200:
                    raise KeycloakError(f"token request returned {response.status_code}")
                body = response.json()
                self._token = str(body["access_token"])
                self._token_expires = time.monotonic() + max(
                    0, int(body.get("expires_in", 60)) - 15
                )
            return self._token

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            response = self._http.request(
                method, path, headers={"Authorization": f"Bearer {self._access_token()}"}, **kwargs
            )
        except httpx.HTTPError as exc:
            raise KeycloakError(f"{method} {path.split('?')[0]} failed") from exc
        if response.status_code == 409:
            raise KeycloakConflictError(f"{method} returned 409")
        if response.status_code >= 400:
            log.warning(
                "keycloak admin call failed",
                extra={"method": method, "status": response.status_code},
            )
            raise KeycloakError(f"{method} returned {response.status_code}")
        return response

    @staticmethod
    def _created_id(response: httpx.Response) -> uuid.UUID:
        location = str(response.headers.get("Location", ""))
        # Keycloak IDs are UUIDs; anything else raises ValueError.
        return uuid.UUID(location.rstrip("/").rsplit("/", 1)[-1])

    @staticmethod
    def _single(matches: list[dict[str, Any]]) -> uuid.UUID | None:
        """None if absent; an error if ambiguous, so callers skip rather than act."""
        if len(matches) > 1:
            raise KeycloakError("more than one object carries this purser_id")
        return uuid.UUID(matches[0]["id"]) if matches else None

    @staticmethod
    def _has_purser_id(obj: dict[str, Any], purser_id: uuid.UUID) -> bool:
        return (obj.get("attributes") or {}).get(PURSER_ID) == [str(purser_id)]

    # -- organizations -------------------------------------------------------

    def find_org(self, purser_id: uuid.UUID) -> uuid.UUID | None:
        response = self._request(
            "GET",
            "/organizations",
            params={"q": f"{PURSER_ID}:{purser_id}", "briefRepresentation": "false", "max": "2"},
        )
        return self._single([o for o in response.json() if self._has_purser_id(o, purser_id)])

    def create_org(self, purser_id: uuid.UUID) -> uuid.UUID:
        """Create the organization for our org row, or return the one already made."""
        existing = self.find_org(purser_id)
        if existing is not None:
            return existing
        response = self._request(
            "POST",
            "/organizations",
            json={
                "name": str(purser_id),
                "alias": str(purser_id),
                "enabled": True,
                "attributes": {PURSER_ID: [str(purser_id)]},
            },
        )
        return self._created_id(response)

    def add_member(self, keycloak_org_id: uuid.UUID, keycloak_user_id: uuid.UUID) -> None:
        try:
            self._request(
                "POST", f"/organizations/{keycloak_org_id}/members", json=str(keycloak_user_id)
            )
        except KeycloakConflictError:
            return  # already a member

    def delete_org(self, keycloak_org_id: uuid.UUID) -> None:
        """Used only by the sweeper to roll back an org whose rows it deletes."""
        self._request("DELETE", f"/organizations/{keycloak_org_id}")

    # -- users ---------------------------------------------------------------

    def find_user(self, purser_id: uuid.UUID) -> uuid.UUID | None:
        response = self._request(
            "GET",
            "/users",
            params={"q": f"{PURSER_ID}:{purser_id}", "briefRepresentation": "false", "max": "2"},
        )
        return self._single([u for u in response.json() if self._has_purser_id(u, purser_id)])

    def email_taken_by_other(self, email: str, purser_id: uuid.UUID) -> bool:
        """Whether a Keycloak user with this email exists that we didn't create for purser_id."""
        response = self._request(
            "GET",
            "/users",
            params={"email": email, "exact": "true", "briefRepresentation": "false"},
        )
        return any(not self._has_purser_id(u, purser_id) for u in response.json())

    def create_user(self, purser_id: uuid.UUID, email: str, display_name: str) -> uuid.UUID:
        """Create the identity for our user row, or return the one already made.

        No password: the user sets one, and a second factor, at first sign-in.
        """
        existing = self.find_user(purser_id)
        if existing is not None:
            return existing
        response = self._request(
            "POST",
            "/users",
            json={
                "username": email,
                "email": email,
                "firstName": display_name,
                "lastName": "-",
                "enabled": True,
                "emailVerified": False,
                "requiredActions": list(NEW_USER_ACTIONS),
                "attributes": {PURSER_ID: [str(purser_id)]},
            },
        )
        return self._created_id(response)

    def delete_user(self, keycloak_user_id: uuid.UUID) -> None:
        """Used only by the sweeper to roll back a user whose row it deletes."""
        self._request("DELETE", f"/users/{keycloak_user_id}")

    def second_factor_configured(self, keycloak_user_id: uuid.UUID) -> bool:
        response = self._request("GET", f"/users/{keycloak_user_id}/credentials")
        return any(c.get("type") in SECOND_FACTOR_TYPES for c in response.json())

    def require_action(self, keycloak_user_id: uuid.UUID, action: str) -> None:
        user = self._request("GET", f"/users/{keycloak_user_id}").json()
        actions = list(user.get("requiredActions") or [])
        if action not in actions:
            self._request(
                "PUT",
                f"/users/{keycloak_user_id}",
                json={**user, "requiredActions": [*actions, action]},
            )
