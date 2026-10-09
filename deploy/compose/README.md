# deploy/compose/

Compose files for running Purser's containers.

| File | Purpose |
|---|---|
| `dev.yaml` | The build's development stack. Today: Postgres and Keycloak. Later: mock providers, recorder, relay, control plane, console, Toxiproxy (build plan §2.8). |
| `keycloak/` | Keycloak's database init SQL and the dev realms imported at start (`realms/`). |
| `sidecar.yaml` | (Later, Lane C) The single-host sidecar (architecture §9, D3). |

## The dev stack

```sh
make dev-up      # start and wait until every service is healthy
make dev-psql    # psql shell
make dev-check   # smoke-check Keycloak: issuer, token claims, admin events
make dev-token WHO=bob   # print a dev-realm access token (alice or bob)
make dev-down    # stop (keeps the data volume); make dev-reset also deletes it
```

The same file runs on a Mac (Docker Desktop or Colima), in Claude Code cloud sessions (the
SessionStart hook starts it), and in GitHub Actions (`.github/workflows/ci.yml`).

| Service | Image | Host address | Credentials |
|---|---|---|---|
| `postgres` | `postgres:18.6-trixie` from public ECR, pinned by digest (ADR 0002) | `127.0.0.1:55432` | user `purser_dev`, password `purser-dev-only-not-a-secret`, database `purser_dev` |
| `keycloak-db-init` | same Postgres image; one-shot | none | creates role `keycloak_dev` (password `keycloak-db-dev-only-not-a-secret`) and database `keycloak` |
| `keycloak` | `quay.io/keycloak/keycloak:26.8.0`, pinned by digest (ADR 0003) | `127.0.0.1:58080` | bootstrap admin `admin` / `keycloak-admin-dev-only-not-a-secret` (master realm) |

Connection string: `postgresql://purser_dev:purser-dev-only-not-a-secret@127.0.0.1:55432/purser_dev`.
These credentials are for this local stack only and are not secrets.

Notes:

- **Port 55432, loopback only,** so the stack can't clash with a local Postgres or be reached
  from the network.
- **Postgres 18 volume layout.** The image keeps `PGDATA` at `/var/lib/postgresql/18/docker` and
  declares its volume at `/var/lib/postgresql`. The `pgdata` volume is mounted there, not at the
  pre-18 `/var/lib/postgresql/data`.
- **Durability defaults are kept** (no `fsync=off`), because the B1/B2 crash tests depend on
  realistic commit behavior.
- **Adding an image:** pin it by digest (`image: repo:tag@sha256:…`) and add the same string to
  `IMAGES` in `scripts/cloud-setup.sh`. `make check-pins` (run in CI) fails otherwise.

## Keycloak (dev only)

Keycloak runs `start-dev --import-realm`: HTTP, no TLS, and development defaults. **`start-dev` is for
this stack only.** Production runs `start` behind TLS against its own RDS instance, with every
secret read from AWS Secrets Manager at run time (ADR 0003, Consequences).

**One issuer everywhere.** Keycloak listens on port 58080 inside its container as well as on the
host, and `KC_HOSTNAME=http://localhost:58080` fixes the frontend URL, so every token's `iss` is
`http://localhost:58080/realms/<realm>` wherever it was requested. Containers that share
Keycloak's network namespace (`network_mode: service:keycloak`) reach the same URL;
other containers use `http://keycloak:58080`, which `KC_HOSTNAME_BACKCHANNEL_DYNAMIC` lets them
use for discovery, JWKS, and the admin API.

**Realms are imported only when they don't exist.** After editing a file in `keycloak/realms/`,
run `make dev-reset && make dev-up` (this also wipes the dev Postgres), or delete the realm in the
admin console and restart the `keycloak` service.

### Realm `purser-dev`

Every credential below is development-only and not a secret; each ends in `-dev-only-not-a-secret`.

| Object | Details |
|---|---|
| Organization `acme-dev` | "Acme Dev", ID `ac3e0000-0000-4000-8000-000000000001`, domain `acme-dev.example`; members alice and bob |
| User `alice@acme-dev.example` | ID `a11ce000-0000-4000-8000-000000000001`; password `alice-dev-only-not-a-secret`; TOTP whose secret bytes are the ASCII string `alice-totp-dev-only-not-a-secret` (base32 `MFWGSY3FFV2G65DQFVSGK5RNN5XGY6JNNZXXILLBFVZWKY3SMV2A`) |
| User `bob@acme-dev.example` | ID `b0b00000-0000-4000-8000-000000000002`; password `bob-dev-only-not-a-secret`; no second factor |
| Client `purser-controlplane` | Confidential, service account only, secret `controlplane-dev-only-not-a-secret`. The control plane's admin-API client (see roles below). |
| Client `purser-console` | Public, authorization code with PKCE (S256), redirect `http://localhost:3000/*`. For the console (Lane D task 4). |
| Client `purser-dev-test` | Public; direct grant and code flow (redirect `http://localhost/dev-test-callback`). Tests and `make dev-token` only. |
| Client `purser-dev-noaud` | As `purser-dev-test` but without the admin-API audience: tokens the control plane must reject. |
| Client `purser-dev-shortlived` | As `purser-dev-test` with 2-second access tokens: for expiry tests. |

**Tokens carry identifiers and levels only.** The realm defines its own minimal client scopes in
place of Keycloak's defaults. An access token for the admin API has `iss`, `sub`, `aud`
(`purser-admin-api`), `azp`, `acr`, `auth_time`, `scope`, and `organization` as
`{"<alias>": {"id": "<organization id>"}}`. The control plane keys on the ID, because aliases can
change. Name and email go in the ID token only, for the console.

**Step-up authentication.** The browser flow `browser-step-up` maps levels of authentication to
`acr` values (`acr.loa.map`): level 1 `pwd` is a password; level 2 `mfa` adds TOTP or a passkey
(WebAuthn). A client asks for `acr_values=mfa` to force the second factor. Operator endpoints in the
control plane require `acr=mfa`. Direct grants always yield `acr=pwd`.

**Admin events** are saved with 7-day retention (`adminEventsExpiration` 604800 s) and without
representations, so a reconciler outage can catch up (ADR 0003). User attribute `purser_id`
(admin-only, declared in the user profile) holds the control plane's own ID, for idempotent
creation; organizations carry the same attribute.

**The admin-API client's roles** (on `realm-management`, granted to the service account and
mapped into its token scope, with full scope off): `view-users`, `query-users`, `manage-users`,
`view-events`, and `manage-realm`. Measured on Keycloak 26.8.0: the Organization endpoints return
403 without `manage-realm`, even for reads, so it is required while the control plane creates
organizations. It is broad (it covers realm settings and authentication flows); revisit when
Keycloak offers a narrower organization permission. The client cannot list clients or reach the
master realm (`make dev-check` asserts the first).

### Realm `purser-dev-other`

A second issuer with its own keys, for tests that must reject a foreign realm's token: user
`carol@other-dev.example` (password `carol-dev-only-not-a-secret`) and public client
`purser-dev-test` with the admin-API audience.

## Network access from containers in a Claude Code cloud session

The recorder (Lane A task 3) runs as a container and must reach `api.openai.com` and
`openrouter.ai`, so this was measured on 2026-10-09 in a cloud session (Docker 29.8.2, Compose
v5.6.0, default bridge and the Compose project network).

### How the session reaches the network

- **Processes on the VM** use the agent proxy at `$HTTPS_PROXY`, an `http://127.0.0.1:<port>`
  address. **The port is not stable:** it changed from 38287 to 36101 when the session's worker
  restarted. Never hard-code it.
- **Every outbound TLS connection is intercepted,** whether or not it goes through the agent
  proxy. Hosts in `NO_PROXY` (for example `pypi.org`) and traffic from containers on a bridge
  network go out through a transparent egress gateway that re-signs TLS with
  `O=Anthropic; CN=Egress Gateway SDS Issuing CA`. Traffic through the agent proxy is re-signed by
  `CN=CCR agent-proxy interception CA`, except for some hosts it passes through untouched
  (`api.openai.com` showed its real Google Trust Services certificate through the proxy).
- `/root/.ccr/ca-bundle.crt` contains both Anthropic CAs **and** the public roots, so it can
  replace a container's trust store outright.

### Measurements

A `python:3.13-slim` container (public ECR, pinned by digest) fetched `https://pypi.org/simple/pip/`
and `https://api.openai.com/` with `urllib`. Any HTTP status counts as reachable; OpenAI answers its
bare root with 421.

| Case | Network | Proxy settings | CA bundle | pypi.org | api.openai.com |
|---|---|---|---|---|---|
| a | default bridge | none | none | ✗ certificate verify failed | ✗ certificate verify failed |
| b | default bridge | `host.docker.internal:<port>` or `172.17.0.1:<port>` | mounted | ✗ connection refused | ✗ connection refused |
| c | host | `HTTPS_PROXY`, `NO_PROXY` | none | ✗ certificate verify failed | ✓ 421 (proxy passes it through) |
| d | host | `HTTPS_PROXY`, `NO_PROXY` | mounted | ✓ 200 | ✓ 421 |
| e | host | none | none | ✗ certificate verify failed | ✗ certificate verify failed |
| **f** | **default bridge** | **none** | **mounted** | **✓ 200** | **✓ 421** |
| **g** | **Compose network `purser-dev_default`** | **none** | **mounted** | **✓ 200** | **✓ 421** |

Case b fails because the agent proxy listens only on the VM's loopback, which a bridge-networked
container can't reach.

### What a container needs

**Only the CA bundle.** On the default bridge or a Compose network, mount
`/root/.ccr/ca-bundle.crt` read-only and point the runtime's trust variables at it. **Don't pass
the proxy settings**: from a bridge network they can't work (case b), and they aren't needed (cases
f and g). Host networking plus `HTTPS_PROXY` plus the CA also works (case d), but it breaks
Compose service-name DNS and port isolation, so don't use it for stack services.

For the recorder, a cloud-only override file (applied with `-f dev.yaml -f cloud.override.yaml`)
would look like this:

```yaml
services:
  recorder:
    volumes:
      - /root/.ccr/ca-bundle.crt:/etc/purser/egress-ca.crt:ro
    environment:
      SSL_CERT_FILE: /etc/purser/egress-ca.crt        # Python ssl, OpenSSL, Go
      REQUESTS_CA_BUNDLE: /etc/purser/egress-ca.crt   # requests / httpx
      NODE_EXTRA_CA_CERTS: /etc/purser/egress-ca.crt  # Node (adds to, not replaces, the store)
```

Distroless images have no update-ca-certificates, so use the environment variables (or the
runtime's own CA option) rather than rebuilding the trust store. Never bake the CA into an image
(D2), and never disable TLS verification.

### Not yet verified

- **Provider API credentials.** Build plan §11 stores the OpenAI and OpenRouter keys as API
  credentials on the cloud environment, which the egress layer attaches. This session had none
  configured, so whether they're attached on the container's direct path (cases f and g), the
  agent-proxy path (case d), or both is unknown. Check it before the first recording. Note that
  through the agent proxy `api.openai.com` was *not* intercepted, which suggests credential
  attachment there depends on the environment's configuration.
- **Egress policy on the direct path.** This environment allowed every host tried
  (`example.com` and `www.wikipedia.org` included) on both paths, so a denial couldn't be shown. A
  stricter environment should be checked to confirm the direct path enforces the same allowlist.

### Elsewhere

None of this applies on a Mac or in GitHub Actions: containers there reach the internet directly
with the image's own public trust store. Keep the CA mount in a cloud-only override, never in
`dev.yaml`.
