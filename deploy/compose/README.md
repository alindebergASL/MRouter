# deploy/compose/

Compose files for running Purser's containers.

| File | Purpose |
|---|---|
| `dev.yaml` | The build's development stack. Today: Postgres. Later: mock providers, recorder, relay, control plane, console, Toxiproxy (build plan §2.8). |
| `sidecar.yaml` | (Later, Lane C) The single-host sidecar (architecture §9, D3). |

## The dev stack

```sh
make dev-up      # start and wait until every service is healthy
make dev-psql    # psql shell
make dev-down    # stop (keeps the data volume); make dev-reset also deletes it
```

The same file runs on a Mac (Docker Desktop or Colima), in Claude Code cloud sessions (the
SessionStart hook starts it), and in GitHub Actions (`.github/workflows/ci.yml`).

| Service | Image | Host address | Credentials |
|---|---|---|---|
| `postgres` | `postgres:18.6-trixie` from public ECR, pinned by digest (ADR 0002) | `127.0.0.1:55432` | user `purser_dev`, password `purser-dev-only-not-a-secret`, database `purser_dev` |

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
