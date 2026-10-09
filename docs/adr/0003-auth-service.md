# 0003. Auth service for people identity

- Status: Proposed
- Date: 2026-10-09
- Deciders: Andrew Lindeberg
- Guarantees affected: none changed. Referenced: G5, A1 (every console action available by
  API), A2 (revoked device makes no upstream attempt after 15 minutes; needs user-lifecycle
  events from the auth service), §4 "Devices" (device-authorization enrollment), C1 (the auth
  service is outside the provider-credential path).

## Context

Architecture §4 requires built-in sign-in with MFA (TOTP and passkeys) for organizations
without an identity provider, per-customer-org OIDC and SAML single sign-on plus SCIM
provisioning for those with one, and says to "use an established auth service rather than
building this". Build plan §6 makes the choice part of gate G0, and Lane D's next two tasks
(FastAPI skeleton, device enrollment) depend on it.

### Integration boundary assumed by every candidate

- **The auth service owns** human identities and their credentials: passwords, TOTP secrets,
  passkeys, per-org SSO connections, SCIM endpoints, sessions, and the issuing of OIDC tokens.
- **Our Postgres owns** the organization → workspace → team → user hierarchy, our roles
  (owner, admin, billing admin, member, viewer), devices, virtual keys, budgets, and audit.
  The control plane maps a token's subject and organization claim to our user row; the auth
  service's own roles, if any, are not used.
- **Devices.** The sidecar's enrollment runs an OAuth device-authorization flow (RFC 8628).
  Either the auth service implements it, or the control plane runs it: the control plane
  issues the device code and user code, the user approves in a browser session authenticated
  by the auth service, and the control plane then issues the device's mTLS certificate and
  15-minute relay tokens. In both cases the control plane's CA, not the auth service, signs
  device certificates, so the auth service never touches relay access.
- **Lifecycle.** When a user is disabled, deleted, or deprovisioned by SCIM, the auth service
  must tell the control plane promptly so that the user's devices are revoked and A2's
  15-minute bound holds for the user as well as the device.
- **C1.** The auth service never holds provider credentials, federation tokens, or data keys.
  It authenticates people and nothing else.
