# Purser: v1 Architecture and Guarantees

Draft 0.6, 2026-10-09. Owner: Andrew Lindeberg. Product name chosen 2026-10-07; trademark and domain checks are still pending. Repository: https://github.com/alindebergASL/MRouter.

Changes in 0.6: v1 is our hosted multi-tenant service, and the deployment models are named (1.1); people sign in through Keycloak per ADR 0003, the control plane validates tokens against a configured OIDC issuer and audience, and a platform operator role administers the install (4); orgs are isolated by org ID in every query and by Postgres row-level security, with guarantee A5 (4); every install generates its own secrets and keys through one idempotent bootstrap command, with guarantee D4 (9.1); the auth service added to the component table (9); a self-hosted org install sends us nothing by default (3.1); the customer-hosted gateway widened to a self-hosted org install, and a dedicated per-org instance and personal mode deferred, each with an entry criterion (12).

Changes in 0.5: containers are the default packaging for every service, with native packaging only where a container costs the user something real (section 9); image guarantees D1 to D3; container footprint added to the bake-off (10.2); hosted orchestration added to open questions.

Changes in 0.4: Switchyard lifts made explicit: route vocabulary (8.1), the routing engine design and its changes, with guarantees R1 and R2 (8.5), a `run` launcher mode (5), and `switchyard-translation` as the candidate for deferred translation routes (12).

Changes in 0.3: price-book rule types and stacking order (6.3); money arithmetic corrected so multiplied rates stay exact (6.4); evidence tiers and coverage (6.5); metering of traffic that bypasses the router (6.6); agent-facing budget interface (7.8); role economics and hook-based model choice in routing (8.2, 8.4). Several of these are lifted from Splunk's Token Meter and extended where the router's position allows; see 6.6.

Changes in 0.2: OpenCode added to the v1 matrix; Cursor added as an experimental row (section 5.1).

This document defines what v1 does, what it promises, and how each promise is tested. A promise without an acceptance test is not a v1 guarantee. Prices in worked examples are illustrative unless attributed to a provider's pricing page with a date (as in 6.3).

## 1. Scope

**Product.** Purser, a general-purpose model router for companies of any size: enterprises, small businesses, startups, and government agencies. No sector-specific compliance regime is assumed unless a customer requires it.

**v1 goals**

| ID | Goal |
|---|---|
| G1 | One local endpoint for Claude Code, Codex, OpenCode, Hermes, OpenClaw, and WorkAgent, configured by a single `connect` command per harness. Cursor connects to the relay's public endpoint instead, because its requests originate from Cursor's cloud (section 5.1). |
| G2 | Provider credentials never reach an end-user device. |
| G3 | Every upstream attempt is metered in tokens and dollars. |
| G4 | Hard (strict) budgets for request types with an established cost ceiling. |
| G5 | Admin console and admin API with MFA and SSO, where the console is a pure client of the API. |
| G6 | Routing runs in shadow mode and is evaluated on cost per accepted task against a fixed-model baseline. |

**Deployment.** v1 is our hosted multi-tenant service, and that service is the revenue model. Customer orgs share one install. They are isolated by org ID in every query, backed by Postgres row-level security, and by Keycloak Organizations in one realm (section 4, ADR 0003).

**Not in v1** (entry criteria in section 12): multi-region, budget leases, expected-cost (bounded) budgets, self-hosted org install, dedicated per-org instance, personal mode, protocol translation between formats, adaptive routing in enforce mode, content-based sensitivity enforcement, prompt and response capture, GPU telemetry routing, MCP and A2A governance.

### 1.1 Deployment models

| Model | Status | Who runs it | Orgs per install | People sign in through | Provider credentials held by |
|---|---|---|---|---|---|
| Hosted multi-tenant | v1 | Us, in AWS us-east-1 | Many, isolated (A5) | Keycloak: one realm, one Keycloak Organization per customer org | Our relay (3.2) |
| Dedicated per-org instance | Deferred (12); premium tier | Us | One | Keycloak, in the instance's own realm | The instance's relay |
| Self-hosted org install | Deferred (12) | The customer | One or more | Keycloak, in the customer's install | The customer's relay |
| Personal mode | Deferred (12); possibly a free tier | One person, on their own machine | One org, one user | A built-in passkey or TOTP login; no Keycloak | The user. G2 and C1 are restated for this model before it is built (12). |

Two rules hold in every model. The data model stays multi-org: an install with one org still has org IDs in every query and row-level security (4). Every install generates its own secrets and keys at first start, with no default credentials outside development (9.1, D4).

## 2. Components

```
 Agent host                         Router cloud (one region)
 ┌──────────────────────────┐       ┌──────────────────────────────────────┐
 │ Harness ──► Sidecar      │──────►│ Relay ──► upstream providers          │
 │ (Claude Code, Codex,     │ mTLS  │   │  holds federated credentials       │
 │  OpenCode, Hermes,       │       │   │                                    │
 │  OpenClaw, WorkAgent)    │       │   │                                    │
 │                │         │       │   ▼                                    │
 │                └─► local │       │ Postgres: reservations, usage, budgets │
 │                  models  │       │   ▲                                    │
 └──────────────────────────┘       │ Control plane: API, console, identity  │
 Cursor app ──► Cursor cloud ──────►│ Relay public listener (virtual key)    │
                             HTTPS  └──────────────────────────────────────┘
```

- **Sidecar.** One binary in `--role sidecar`. Listens on 127.0.0.1 only. Handles harness autoconfiguration, device enrollment, forwarding to the relay, local models (metered, never billed), and shadow-routing logs. Holds no provider credentials.
- **Relay.** The same binary in `--role relay`. Holds federated provider credentials, evaluates destination policy, reserves budget, dispatches upstream, settles usage. It is the only component that talks to paid providers. It accepts traffic from sidecars over mTLS, and on a separate public HTTPS listener from cloud-originated clients (Cursor) authenticated by per-user virtual keys.
- **Control plane.** FastAPI, Postgres, and Next.js; the API and console ship as container images, and Postgres is a managed service (section 9). Owns identity (people sign in through Keycloak, section 4), RBAC, the model catalog, the price book, budgets, the usage ledger, and audit. Its API is described in OpenAPI 3.1, with a generated TypeScript client and a contract drift check in CI, following WorkAgent's pattern.
- **Reservation ledger.** Tables in the control plane's Postgres, in the same region as the relay. The relay writes reservations directly in transactions. No prompt or response content is ever written to Postgres.

The language for the sidecar/relay binary is decided by the bake-off in section 10. The control plane stack is independent of that decision.

## 3. Trust and data boundaries

### 3.1 Who receives content

These are two separate questions, answered per path.

| Path | Does the router service receive content? | Which upstream receives content? | Retention |
|---|---|---|---|
| Local model through the sidecar | No | The local model only | On the device only |
| Managed relay (v1) | Yes, in relay memory for the duration of the request | The chosen provider | Relay: not persisted (C2). Provider: per that provider's policy and the customer's settings with it. For example, OpenAI documents separate abuse-monitoring and application-state retention. |
| Cursor through the managed relay (v1, experimental) | Yes, as above | Cursor's backend first, then the chosen provider | As above, plus Cursor's own policies. The router cannot change what Cursor's backend receives. |
| Self-hosted org install (deferred) | Our service: no. It receives nothing by default; any telemetry to us is opt-in and metadata only. The customer's own relay receives content as the managed relay does. | The chosen provider | Customer-controlled |

A dedicated per-org instance (deferred) handles content as the managed relay does, in an install that serves one org. In personal mode (deferred), the relay runs on the user's own machine and content goes only to the chosen provider.

"We do not persist prompt bodies" is a relay policy. It is not an end-to-end zero-retention claim, which depends on the provider and the features enabled.

### 3.2 Credentials: federation first

| Upstream | v1 method |
|---|---|
| Anthropic API | Workload identity federation. The relay presents a JWT from a per-tenant OIDC issuer the customer registers. Anthropic returns a short-lived token bound to a service account and workspace, with a lifetime of 60 to 86,400 seconds (default 3,600). Tokens with a `jti` claim are single-use by default, so mint a fresh JWT per exchange. |
| OpenAI API | Workload identity federation (documented by OpenAI). Confirm token lifetime and scoping during implementation. |
| OpenRouter | Workload identity federation on Business and Enterprise plans, with access tokens of at most 15 minutes. Encrypted key otherwise. |
| Fallback for all | Encrypted static key: a per-tenant data key, decryption only in relay memory, write-only through the API, rotation supported, optional customer-managed KMS key. |

Bedrock, Vertex AI, and Azure OpenAI are not v1 upstreams. When added, they use cross-account role assumption or their native federation.

### 3.3 Guarantees

**C1. No provider credential is ever delivered to a sidecar.**
Acceptance test: after enrollment and 100 requests per supported harness, scan sidecar config, state, logs, process environment, and memory dumps for credential material (including canary keys planted in the relay). Fuzz every control-plane and relay endpoint reachable with a device token and confirm none returns credential material. Zero findings required.

**C2. The relay never persists request or response bodies.**
Acceptance test: send traffic containing unique canary strings through every matrix row (section 5), including failures, interrupted streams, and relay crashes. Scan Postgres, application logs, traces, metrics labels, crash dumps, and temp directories. Zero canary occurrences required.

**C3. Destination policy is evaluated before content leaves the sidecar or relay for any third party.** This covers upstream providers, token-count endpoints, hosted classifiers (Jev, OpenAI Decisions), and shadow models.
Acceptance test: configure a policy that denies destination X and send requests carrying canaries. Packet capture on the sidecar and relay hosts shows zero connections to X. The request is denied with a policy error.

**Sensitivity in v1.** Enforcement is by declared destination policy: an allowlist of providers per org, workspace, and route. It is not based on content inference. A local classifier on a user-controlled laptop cannot guarantee containment, so in v1 it is advisory, logged in shadow mode, and runs only on the device. The relay re-checks destination policy on every attempt.

## 4. Identity and access

- **People.** Keycloak, self-hosted (ADR 0003): one realm per install, with Keycloak Organizations as the customer-org primitive. It provides built-in sign-in with MFA (TOTP and passkeys) for organizations without an identity provider, and OIDC and SAML single sign-on for those with one. The control plane serves SCIM provisioning per org and drives Keycloak's admin API. Keycloak is off the traffic path: relay-token issuance reads our database only.
- **Token validation.** The control plane validates tokens against a configured OIDC issuer, through the issuer's discovery document and JWKS, with a stock library, and requires the configured audience: in one realm every client shares the issuer, so a token issued to another client (the device-flow client, a customer's machine-to-machine client) is rejected. Nothing in the control plane is specific to Keycloak: the issuer is configuration, so another issuer can take its place (personal mode's built-in login, 12). A token from any other issuer or for any other audience is rejected (A5).
- **Devices.** Enrollment uses a device-authorization flow approved by the user. Each sidecar gets a keypair and an mTLS client certificate from the control plane's CA. Relay access uses short-lived tokens (15 minutes) bound to device and user. Revoking a device or user blocks new tokens immediately; outstanding tokens expire within 15 minutes.
- **Harness to sidecar.** Loopback only. `connect` writes a per-harness local token, so requests are attributed to a harness and other local processes need that token to use the sidecar.
- **Cloud-originated clients (Cursor).** A per-user virtual key, scoped to that client type and to the public listener, issued from the console. It is not a provider credential, so C1 still holds, and anything it spends passes through the same reservations. It carries no device binding, so it is revocable and rotatable on its own and subject to its own budget.
- **RBAC.** Scopes are org → workspace → team → user. Roles are owner, admin, billing admin, member, and viewer. Above the orgs, the platform operator role administers the install itself and requires MFA; the first platform operator is the first admin the bootstrap creates (9.1). No org role grants platform-operator access or can assign it. Model catalog states: proposed → approved → assigned (to groups) → deprecated → retired.
- **Org isolation.** The data model is multi-org in every deployment model (1.1). Every request-path query on org data filters by org ID.
  - **The request's org.** A request acts for one org. For a person, it is the org the request names, and our database must record the person as a member of it; the token's organization claim alone never selects or grants an org. For a device, virtual key, SCIM token, or machine-to-machine client, it is the org the credential was issued in.
  - **Row-level security, as defense in depth.** Every control-plane table that holds org data has a Postgres row-level security policy keyed on the request's org, for reads and writes (`USING` and `WITH CHECK`). The control plane sets the org per transaction with `SET LOCAL purser.org_id`, never per connection, so a pooled connection carries no org into the next transaction. A query that forgets its org filter still returns nothing from another org, a write cannot touch or create another org's row, and a transaction with no org set sees no org rows. "Holds org data" means every table with an org ID column or a foreign key into one; anything else is on a reviewed allowlist. Policies are forced (`FORCE ROW LEVEL SECURITY`), and the request path's database roles neither own the tables nor have `BYPASSRLS`.
  - **Finding the org before it is set.** Authentication looks up a credential (a device certificate fingerprint, a virtual-key or SCIM-token hash, a user's subject) before any org is set. It does so only through narrow `SECURITY DEFINER` functions that take the credential and return the principal and its org, and nothing else. Cross-org work (platform-operator views, migrations, the reconciler) runs under separate database roles, never the request path's.
  - **One person, several orgs.** In one realm a person is one Keycloak user. Deprovisioning from an org (SCIM `active: false`, delete, or an admin action through our API) removes that org's membership and revokes the devices enrolled under it; the realm user is disabled only when no membership remains.
  - **SSO domains.** An org's single sign-on can claim only email domains verified for that org, and a domain verified by one org cannot be claimed by another.

**A1. Every console action is available through the admin API.**
Acceptance test: the CI drift check fails if the console calls any endpoint missing from the OpenAPI contract, or if the generated client differs from the contract.

**A2. A revoked device makes no successful upstream attempt more than 15 minutes after revocation.**
Acceptance test: revoke a device mid-session and confirm the relay rejects every request after token expiry. Record the measured gap.

**A5. No org can read or change another org's data.**
Acceptance test, on one install with two orgs, A and B, each with users, devices, virtual keys, SCIM tokens, machine-to-machine clients, budgets, and usage, and one person who is a member of both:
1. **API and relay.** Call every control-plane API endpoint, every relay endpoint, the SCIM endpoint, and every budget-interface tool (7.8) with each of org A's credentials (user token, device token and mTLS certificate, virtual key, SCIM token, machine-to-machine client) and org B's identifiers (org, workspace, team, user, device, key, budget, attempt, and session IDs). Every call is refused, no response contains org B data, and no org B row changes. The person in both orgs, acting in org A, gets the same result. No org A role reaches a platform-operator endpoint or can grant that role.
2. **Row-level security.** As each request-path database role, with org A set on the transaction, run an unfiltered `SELECT`, `UPDATE`, and `DELETE` against every table that holds org data, and an `INSERT` of a row tagged with org B: zero org B rows are read or changed, and the insert fails. With no org set, zero org rows are read. A pooled connection reused after an org A transaction carries no org. CI fails if any table that holds org data lacks a forced policy for reads and writes, if a table outside that set is missing from the allowlist, or if a request-path role owns a table or has `BYPASSRLS`.
3. **Shared realm.** Deprovisioning the shared person from org A, by SCIM and through the API, leaves their org B membership, sign-in, and devices working. Org A's single sign-on setup cannot claim a domain verified by org B, nor link an identity provider that signs in org B's users.
4. **Issuer and audience.** Present to every endpoint that accepts it a well-formed user token from another issuer (including another install's realm), a token from the configured issuer for another audience, a relay token signed by another install's keys, and a device certificate from another install's CA. Every one is rejected.

## 5. Harness and protocol compatibility

v1 is native pass-through only: the client's format is forwarded to an upstream that speaks the same format. Translation between formats is deferred until each route is certified.

| Harness | Client protocol | v1 upstreams | Configuration written by `connect` |
|---|---|---|---|
| Claude Code | Anthropic Messages | Anthropic API | Managed settings: `ANTHROPIC_BASE_URL` set to the sidecar, `apiKeyHelper` returning the local token, `CLAUDE_CODE_GATEWAY_HINT_HEADERS=1`. Optionally `allowedProviders: ["customEndpoint"]` (Claude Code v2.1.285 or later). |
| Codex | OpenAI Responses over HTTP | OpenAI | A `[model_providers.<name>]` block in `~/.codex/config.toml` with `wire_api = "responses"` and `supports_websockets = false`. |
| OpenCode | OpenAI Chat Completions or OpenAI Responses | OpenAI, OpenRouter | A provider block in `opencode.json` (project) or `~/.config/opencode/opencode.jsonc` (global). `npm` is `@ai-sdk/openai-compatible` for Chat Completions or `@ai-sdk/openai` for Responses; `options.baseURL` points at the sidecar; `options.apiKey` uses `{env:VAR}`; each model's `limit.context` and `limit.output` are written from the model catalog. |
| Hermes | OpenAI Chat Completions | OpenAI, OpenRouter | `~/.hermes/config.yaml` provider entry plus auxiliary-task endpoints. Secrets go in `.env`. |
| OpenClaw | OpenAI Chat Completions (`api: "openai-completions"`) | OpenAI, OpenRouter | A `models.providers` entry and an allowlist entry under `agents.defaults.models` in `~/.openclaw/openclaw.json`. |
| WorkAgent | OpenAI Responses | OpenAI | WorkAgent accepts an approved router origin (section 11). |
| Any | OpenAI Chat or Anthropic Messages | Local models (e.g. Ollama) | Sidecar routes directly; metered, no billing. |

There are two ways to attach a harness. `connect` writes persistent configuration, suited to MDM rollout. `run <harness>` launches the harness with the sidecar endpoint injected for that one process and changes no files, the way Switchyard's launchers work; it suits trials.

OpenCode through an Anthropic-format custom provider is not a v1 row. OpenCode's provider docs confirm the Chat Completions and Responses packages for custom providers; Anthropic Messages needs verification first.

Responses API traffic is supported statelessly: encrypted reasoning items and conversation items are forwarded unchanged between requests. Stateful use (`store: true`, `previous_response_id`) passes through but is not certified in v1.

Claude Code with non-Claude models is not a v1 route. Anthropic does not support routing Claude Code to non-Claude models through any gateway. If added later, it will be an explicit, labeled opt-in.

**H1. Each matrix row passes the certification suite before it ships.** The suite replays recorded real harness sessions through the router and the same sessions directly against the provider:

1. Streaming: event order identical, keepalive `ping` events preserved, no response buffering, final events present (`message_delta` and `message_stop` for Anthropic).
2. Interrupted streams: client abort and upstream reset mid-stream. The correct error reaches the client, and the attempt is recorded as `unknown` with its reservation held (section 7).
3. Tool use: multi-turn and parallel tool calls.
4. Continuation and compaction: Claude Code compaction requests and the first request after compaction.
5. Headers: `anthropic-version` and `anthropic-beta` forwarded byte for byte. Response headers `retry-after`, `x-should-retry`, and rate-limit headers forwarded. Error bodies unmodified.
6. Prompt caching: from the second turn onward, cache-read tokens through the router equal those of the direct run. This proves the prefix was forwarded byte for byte.

Pass criterion: 100% of scenarios per row. Any failure blocks that row.

### 5.1 Cursor (experimental row)

Cursor does not fit the sidecar model:

- **Requests come from Cursor's cloud, not the laptop.** Cursor sends requests through its own backend, so a localhost base URL points at Cursor's server, not the user's machine. The router endpoint must be publicly reachable over HTTPS: the relay's public listener (section 2), with a per-user virtual key (section 4).
- **Configuration** is Cursor's "Override OpenAI Base URL" setting plus the virtual key. Users report the override can be switched off without notice, so the console flags users whose Cursor traffic stops.
- **Protocol:** agent mode sends Responses API request shapes. With an OpenAI upstream, both formats are pass-through in v1.
- **Partial coverage.** Cursor gates custom keys by mode and model. Features that run on Cursor's own models never reach the router, so they are neither metered nor budgeted. The console must label Cursor usage as partial.
- **CLI not covered.** The Cursor CLI (`agent`, `cursor-agent`) cannot target a custom gateway.
- **Unsupported integration.** Cursor does not officially support AI gateways; existing integrations are best effort, built from observed traffic.

**H2. The Cursor row stays certified.** Acceptance test: the H1 suite, using recorded Cursor Ask, Plan, and Agent sessions, re-run on every Cursor release. A failure demotes the row to unsupported until it passes again.

**A3. A revoked virtual key is rejected on the next request.** Acceptance test: revoke a key mid-session and confirm the relay rejects the next request with an authentication error.

## 6. Metering

### 6.1 Usage records

One record per upstream attempt (not per client request), including retries, failures, and unknown outcomes. Fields:

- **Identity:** attempt ID; org, workspace, team, user, device, harness.
- **Correlation:** session and prompt IDs when the harness provides them. Claude Code sends a session ID on every request, and with hint headers enabled a prompt ID shared by all requests serving one user prompt.
- **Routing:** upstream, model, route, data path, price-book version.
- **Tokens:** by class: uncached input, cache read, cache write (by TTL where reported), output, reasoning.
- **Money:** computed cost; provider-billed cost (filled in by reconciliation).
- **Outcome:** state, timing (time to first byte, total duration).
- **Evidence:** a tier for each value (6.5).

### 6.2 Accounting adapters

Providers report usage with opposite conventions, so each provider gets its own adapter with recorded-response fixtures.

| Provider | Total input tokens | Notes |
|---|---|---|
| Anthropic | `input_tokens + cache_creation_input_tokens + cache_read_input_tokens` | `input_tokens` excludes cache reads and writes. Cache writes are split into 5-minute and 1-hour counts in the `cache_creation` object; the adapter checks that the two sum to `cache_creation_input_tokens`. It also reads `speed`, `inference_geo`, and `server_tool_use`, because each changes the price (6.3). |
| OpenAI | `input_tokens` | `input_tokens_details.cached_tokens` is a subset of input. `output_tokens` already includes `reasoning_tokens`; never add them again. WorkAgent's transport code already enforces both rules. The request's `service_tier` and the input size (long-context threshold) change the price (6.3). |
| OpenRouter | Per its usage object | Defined by fixtures before the row ships. |

### 6.3 Price-book rules

The price book is a versioned, effective-dated dataset in the control plane, published to the relay. Each entry is a set of rules:

| Rule type | What it covers | Current examples (provider docs as of 2026-10-07) |
|---|---|---|
| Base rates | Per-million rates for each token class: input, cache read, cache write (5-minute, 1-hour), output | Claude Opus 5.5: $4 input, $20 output; cache reads 0.05× input for Opus 5.5, 0.1× for most models; cache writes 1.25× (5-minute) and 2× (1-hour) |
| Speed or service tier | Multiplier on all token classes for a request-level setting | Anthropic fast mode: 2× on Opus 5.5, Opus 5, and Opus 4.8; Opus 4.6 fast requests are billed at standard rates. OpenAI priority/fast 2×, flex 0.5× |
| Geography | Multiplier for data-residency routing | Anthropic `inference_geo: "us"`: 1.1× on every token class for Claude 4.6 and later. OpenAI data residency: 10% uplift for eligible models released on or after March 5, 2026 |
| Long-context tier | Different rates when input exceeds a threshold, applied to the whole request | OpenAI GPT-5.6-family and later models (per Token Meter's encoding): over 272,000 input tokens, 2× input and 1.5× output. Claude Haiku 5.5: over 100,000 tokens, 5×. Claude 4.6 and later (except Haiku 5.5): no tier |
| Per-use fees | Fixed charges per tool use | Anthropic web search: $10 per 1,000 searches ($0.01 each), not billed on error. Web fetch: tokens only |
| Promotions | Time-boxed rates with start and end dates | Promotional OpenAI pricing announced through at least November 21, 2026 |
| Negotiated rates | Org-specific discounts or commitments | Per customer contract |

**Stacking.** Anthropic documents that caching and data-residency multipliers stack on top of fast mode, so modifiers multiply. Worked example, Claude Opus 5.5 with fast mode and US-only inference (4 × 2 × 1.1):

| Token class | Effective rate |
|---|---|
| Input | $8.80/M |
| Output | $44.00/M |
| Cache write, 5-minute | $11.00/M |
| Cache write, 1-hour | $17.60/M |
| Cache read (0.05×) | $0.44/M |

An attempt with 3,000 input, 90,000 cache-read, 5,000 cache-write (5-minute), and 2,000 output tokens costs $0.0264 + $0.0396 + $0.055 + $0.088 = **$0.209**.

**Unpriced, not guessed.** Any dimension the price book doesn't cover makes the attempt unpriced, and under a strict budget, unbounded (B3). That includes an unknown `service_tier`, `speed`, or `inference_geo` value, an unrecognized tool fee, or a model without a rule. Token Meter applies the same rule after the fact. The relay does better because it sees these settings in the request before dispatch, so an unpriced request is rejected before it costs anything.

**Contradictory usage.** If a response's usage is internally inconsistent (for example, 5-minute plus 1-hour cache writes don't sum to the total), the attempt settles at the higher-cost reading (all ambiguous writes priced as 1-hour) and is flagged. Charges may exceed true cost, never fall below it.

### 6.4 Money representation

Money is stored as integer nano-dollars (1 nd = $0.000000001). Rates are stored as exact decimals, not as integer nano-dollars per token.

Integer per-token rates break under multipliers: $0.025/M × 1.1 = $0.0275/M, which is 27.5 nd per token. So:

- **Each attempt is computed in exact decimal arithmetic:** token counts × effective rates, summed across classes, plus fees.
- **Rounding happens once per attempt.** Ceilings round up to the next nano-dollar, so a ceiling is never below the true maximum (B1). Settled costs round half-up, an error of at most 0.5 nd per attempt.
- **Example:** 2,000 output tokens at $25/M = $0.05 = 50,000,000 nd, with no rounding needed.
- **Range:** a signed 64-bit integer holds about $9.22 billion in nano-dollars.

### 6.5 Evidence and coverage

Every usage value carries an evidence tier, lifted from Token Meter's model and extended with a reconciled tier:

| Tier | Meaning | Source |
|---|---|---|
| Reconciled | Matched to the provider's billing data | Monthly reconciliation (M3) |
| Measured | Reported by the provider in the response the relay handled | Relay |
| Inferred | Derived exactly from measured values (for example, uncached input = OpenAI input − cached) | Adapter |
| Estimated | Approximated (for example, output tokens from character counts) | Trace readers (6.6) |
| Unavailable | The source cannot provide it | Any |

Rules:

- **Unavailable is not zero.** A measured zero and an unavailable value are different and are displayed differently.
- **Every aggregate shows its coverage:** the share of its cost backed by measured or reconciled evidence.
- **Comparisons need full coverage.** A period-over-period change is shown only when every attempt in both periods has cost evidence (lifted from Token Meter's role-economics design).
- **Budgets enforce only on relay evidence** (reservations and measured settlements). Estimated usage drives alerts and reports, never denials.

### 6.6 Traffic outside the router

Some agent spend never passes through the relay:

- Claude Code or Codex sessions authenticated with a subscription rather than a key.
- Cursor features that run on Cursor's own models (5.1).
- Any harness a user points somewhere else.

Splunk's Token Meter (MIT license) shows this traffic can be measured from the transcripts agents write to disk. It reads Claude Code session JSONL under `~/.claude/projects`, Codex rollout files under `~/.codex/sessions`, Cursor transcripts enriched read-only from `state.vscdb`, and has readers for OpenCode, Hermes, Kiro, and Pi.

The sidecar includes an optional trace-reader module built on the same approach, improved in five ways:

1. **Content-free by schema.** Readers emit only an allowlisted event schema: token counts by class, model, timestamps, session ID, and a keyed hash of the provider message or response ID. No free-text field exists in the schema.
2. **Deduplicated against the relay.** The relay computes the same keyed hash (HMAC with a per-org key) for every response it handles. A trace event whose hash matches a relay record is marked covered and not counted again. Only unmatched events become outside-router usage.
3. **Org-wide.** Token Meter totals one machine. The router aggregates across devices, showing each org what share of agent spend bypasses its budgets and policies.
4. **Labeled honestly.** Events carry their evidence tier (6.5). Cursor output tokens, for example, are estimated from character counts, and Cursor's cache usage and hidden reasoning are unavailable locally.
5. **Certified per harness version.** Trace formats change with harness releases. An unrecognized format yields unavailable, never zero. Token Meter's parsers serve as a differential test oracle: the same transcripts run through both, and any disagreement is investigated.

Trace reading is off by default and enabled per org, with notice shown to the user, because it reads local files.

### 6.7 Guarantees

**M1. Every upstream attempt produces exactly one usage record.**
Acceptance test: chaos runs against a mock provider that logs every attempt it receives, with relay crashes, Postgres failover, and client disconnects injected. The mock's attempt count must equal the number of usage records, matched one-to-one by attempt ID.

**M2. Computed cost equals the reference calculation exactly.**
Acceptance test: golden fixtures per provider and token class, compared with zero tolerance in integer nano-dollars.

**M3. Computed and provider-billed cost are reconciled monthly.**
Acceptance test: the reconciliation job produces a per-provider difference report for each billing period. The alert threshold is set after the first month of measurement.

**M4. Usage that passes through the relay is never counted twice.**
Acceptance test: run sessions through the relay with trace reading enabled on the same device. The org total must equal the relay-measured total exactly, and every trace event for those sessions must be marked covered.

**M5. Every modifier in 6.3 prices exactly.**
Acceptance test: golden fixtures for each rule type alone and stacked (including the Opus 5.5 fast plus US-only example above, which must equal $0.209), plus a fixture for each unpriced dimension that must yield unpriced.

**C4. Trace-reader events contain no content.**
Acceptance test: plant canary strings in local transcripts for every supported harness, capture all sidecar egress, and confirm zero occurrences. CI fails if any field in the event schema can carry free text.

## 7. Budgets and hard enforcement

### 7.1 Model

- **Scopes:** org, workspace, team, user, device, and optionally session.
- **Windows:** calendar day or month in UTC for v1.
- **Limit:** in nano-dollars.
- **Modes:** `strict` (the default) or `meter-only` (alerts, no blocking).

A request is admitted only after reservations commit against every applicable budget.

### 7.2 Bounded request classes

Strict budgets admit only requests whose maximum charge is established. A request is bounded when all of these hold:

1. **Priced model.** The model, its speed or service tier, its geography, and every token class the request can incur are covered by price-book rules (6.3).
2. **Output capped.** `max_tokens` (Anthropic) or `max_output_tokens` (OpenAI) is present. A route may opt in to injecting a configured cap. Otherwise a request without one is unbounded.
3. **Priced features.** Every billable feature is priced and capped: requested cache TTLs, and server tools with per-use fees (which must have a maximum-use limit).
4. **Input upper bound.** An upper bound on input tokens is available (7.3).
5. **Retries counted.** Relay-initiated retries are capped (at most one in v1). Each retry is a separate attempt with its own reservation.

Unbounded requests under a strict budget are denied with an explicit error.

### 7.3 Input-token upper bound

A count endpoint gives an estimate, not a bound: Anthropic describes its token count as an estimate. Calling one also sends content to a provider (see C3). So v1 uses, in order of preference:

1. **Exact local count,** where the provider publishes its tokenizer for the model and the content is text only.
2. **Certified ratio bound:** tokens ≤ k × UTF-8 bytes + per-message overhead. This is used only after k is established for that tokenizer by a test corpus with a safety margin, and recorded in the price book.
3. **The model's context window,** which the provider enforces by rejecting longer requests.

Images, files, and tool definitions are counted per the provider's documented rules, or the request is unbounded.

**Incremental counting** (previous reported total input plus the new suffix) is an optimization enabled per provider only after certification. It requires the same model, the same tokenizer, and a verified unchanged prefix hash. Reported totals must be reconstructed by the adapter (6.2) first.

### 7.4 Reservation ceiling

```
ceiling = I_max × p_in × m_in  +  O_max × p_out  +  Σ max per-use fees
```

`m_in` is the highest input multiplier the request's features allow. For Anthropic that is 1.0 with no cache markers, 1.25 if any 5-minute cache write is requested, and 2.0 if any 1-hour cache write is requested. Cache reads are cheaper than base input, so writes set the bound.

`p_in` and `p_out` are effective rates after the request-level modifiers in 6.3 (speed or service tier, geography, negotiated rates). Two further rules:

- **Long-context tiers.** If `I_max` can exceed a model's long-context threshold, the whole request is priced at the long-context tier. For a GPT-5.6-family model with a context window above 272,000 tokens, that means `2 × p_in` and `1.5 × p_out` throughout, unless a tighter input bound (7.3) proves the request stays under the threshold.
- **Rounding.** The ceiling is rounded up to the next nano-dollar (6.4).

Worked example at $5/M input and $25/M output, with O_max = 32,000:

| I_max | Caching requested | Input part | Output part | Ceiling |
|---|---|---|---|---|
| 100,000 | none | $0.50 | $0.80 | $1.30 |
| 100,000 | 5-minute writes | $0.625 | $0.80 | $1.425 |
| 100,000 | 1-hour writes | $1.00 | $0.80 | $1.80 |
| 200,000 (context-window bound) | none | $1.00 | $0.80 | $1.80 |
| 200,000 (context-window bound) | 5-minute writes | $1.25 | $0.80 | $2.05 |
| 200,000 (context-window bound) | 1-hour writes | $2.00 | $0.80 | $2.80 |

**The cost of strictness is held headroom.** Admission requires `spent + reserved + ceiling ≤ limit`. With 10 outstanding attempts at a $2.05 ceiling, $20.50 is held at once, and the next request is denied whenever less than its own ceiling remains unreserved. Tighter input bounds (7.3) are the main lever to reduce this.

### 7.5 Attempt state machine

| State | Entered when | Effect on budgets |
|---|---|---|
| `reserved` | The reservation transaction commits | Ceiling added to `reserved` on every applicable window |
| `dispatched` | The first byte is sent upstream | None |
| `released` | The upstream connection failed before any request byte was sent | Ceiling released |
| `settled` | Usage is obtained from the response or by reconciliation | Ceiling released; actual cost added to `spent` |
| `unknown` | The outcome is uncertain after dispatch (stream cut, relay crash, timeout) | Ceiling stays reserved |

`unknown` resolves to `settled` when exact usage is found: in partial stream events, or in provider usage data matched by request ID. If no exact usage is found within the reconciliation window, the attempt is charged its full ceiling. The rule is that charges may exceed true cost, but never fall below it.

### 7.6 Transactions

**Reserve** (one transaction):
1. Lock all applicable budget-window rows in ascending ID order (avoids deadlocks).
2. Check `limit − spent − reserved ≥ ceiling` on each row.
3. Increment `reserved` on each row.
4. Insert the attempt row with its ceiling, window IDs, and price-book version.

**Settle** (idempotent on attempt ID): transition the state exactly once, release the ceiling, add the cost to the windows recorded on the attempt. A reservation belongs to the window in which it was made, even if settlement happens after rollover.

**If cost exceeds ceiling,** which should be impossible for bounded classes: charge the actual cost, record an overrun event, and page. This is an invariant breach, handled as an incident.

### 7.7 Guarantees

**B1. Under a strict budget, the sum of true provider charges for attempts admitted in a window does not exceed the limit.** This assumes the price book is correct for those attempts. It follows because true cost ≤ ceiling for each bounded attempt, and admission keeps the sum of ceilings and settled costs ≤ limit.
Acceptance test: property-based tests against a mock provider that bills random usage within each request's bounds, under random concurrency, retries, relay crashes, client disconnects, and Postgres failover. Assert the invariant on every window after every step.

**B2. No upstream attempt, including relay retries, is sent without its own committed reservation.**
Acceptance test: the mock provider's attempt log is joined to reservation rows by attempt ID. Any attempt without a reservation committed before its first byte is a failure.

**B3. Unbounded requests are never admitted under a strict budget.**
Acceptance test: requests with no output cap, an unpriced model or tier, an uncapped server tool, or an unbounded input are each denied with a specific error code.

**B4. Denials are protocol-correct.**
- Return HTTP 429 with the Anthropic or OpenAI error envelope matching the client's protocol, and `retry-after` set to the integer seconds until the window resets.
- Claude Code stops retrying and shows the error immediately when that value exceeds 60, except in sessions running Claude Code's retry watchdog.

Acceptance test: real Claude Code and Codex clients against an exhausted budget, with the watchdog both on and off. Record the observed client behavior.

**B5. If Postgres is unavailable, strict budgets fail closed.**
Acceptance test: block Postgres and confirm every strict-budget request is denied, with no upstream attempt made.

**P1. Every attempt is priced with the price-book version effective when its reservation committed.** A missing entry makes the request unbounded.
Acceptance test: change a price mid-run and confirm in-flight attempts keep their original version while new reservations use the new one.

**Performance target, not yet a guarantee:** reservation p99 ≤ 20 ms at 200 attempts per second against one org's hottest budget row, on the reference Postgres instance. Measure it before treating it as a commitment. A miss is the entry criterion for leases (section 12).

### 7.8 Agent-facing budget interface

Token Meter ships a read-only MCP server so an agent can query its own usage. The sidecar ships the same idea, tied to enforcement so the answers match what the relay will actually do. Read-only tools:

| Tool | Returns |
|---|---|
| `budget_status` | Remaining unreserved budget for each scope that applies to the caller, and when each window resets |
| `estimate_ceiling` | The reservation ceiling for a planned request (model, tier, output cap, cache settings), computed by the same function the relay uses (7.4) |
| `explain_denial` | For an attempt ID: which budget denied it, the ceiling, and the remaining amount at the time |
| `session_cost` | Cost so far for the current session or prompt, with its evidence tier (6.5) |

This lets an agent check before starting a long task, or pick a cheaper model, instead of being denied midway.

**A4. The budget interface exposes only the caller's own scopes and cannot change anything.**
Acceptance test: call every tool with identities from two users in different teams and confirm neither sees the other's budgets or attempts. Confirm no tool has a write path.

**B6. `estimate_ceiling` equals the relay's ceiling for the same request.**
Acceptance test: for a fixture set covering every price-book rule type, compare the tool's answer with the ceiling the relay reserves. Zero tolerance in nano-dollars.

## 8. Routing: measurement first

### 8.1 Route types

The route vocabulary is lifted from NVIDIA NeMo Switchyard, which documents passthrough, stage-router, LLM-classifier, escalation, and random-split routes. In v1 only the first two change traffic.

| Route type | How it decides | v1 status |
|---|---|---|
| Passthrough | A named model goes to its upstream | Live |
| Tier alias | A policy maps an alias (for example `fast`, `deep`) to a model | Live |
| Stage | Reads signals already in the conversation (tool activity, errors, token counts) with no extra model call | Shadow |
| Classifier | A decision model picks a tier, with affinity for the rest of the episode | Shadow |
| Escalation | Starts on the cheaper tier; promotes after repeated bad turns | Shadow |
| Random split | Fixed traffic split for baselines and A/B tests | Experiments only (8.4), assigned per episode |

No automatic routing changes traffic in v1.

### 8.2 Shadow router

For every attempt, the router computes and logs the decision it *would* make, without acting on it. Inputs:

- Harness hints, such as Claude Code's request class (main, subagent, compaction, auxiliary).
- Stage signals: tool activity, errors, and token counts already in the conversation.
- Optionally, a decision model.

A hosted decision model (TypeSafe Jev, or OpenAI's Decisions API, which is in limited preview with unpublished pricing and accuracy) is called only if destination policy allows sending content to it (C3).

**A second actuator: harness hooks.** Besides changing the model at the proxy, the router can recommend a model inside the harness before a task's first request, through hooks such as a prompt-submit hook. Token Meter's repository holds archived research on exactly this for Codex Desktop; it was never shipped. Choosing at task start keeps the prompt cache and protocol intact, which is the cheapest switch point (8.3). In v1, hook recommendations are shadow-only and logged with the same decision ID as proxy-side decisions, so the 8.4 experiment can compare both actuators.

### 8.3 The unit of value is an accepted task

A cheaper call can make a completed task more expensive. A cheap model's failed attempts, retries, and the loss of cache reuse all count against it.

Reference point: in LangChain's run through Switchyard, routing cut cost 74% versus Opus 4.8 alone, with accuracy 80.0% against 86.0%. The routed arm beat the cheap model alone by 2.3 points, inside the run-to-run noise of about 2.7 points.

Cache re-warm cost belongs in every comparison. With a 100,000-token context and 2,000 output tokens (frontier $5/M input and $25/M output; cheap $0.30/M input and $1.20/M output, priced cold):

- **0.1× cache-read rate:** each cheap turn saves $0.0676. Returning to the frontier model after its cache expired costs $0.575 extra. Break-even is 9 consecutive cheap turns.
- **0.05× cache-read rate:** the saving is $0.0426 per turn and the penalty $0.600. Break-even is 15 turns.

### 8.4 Experiment protocol for promoting routing to enforce mode

1. **Define "accepted task" per harness.** For coding harnesses, tests pass on a fixed task suite. For WorkAgent, an accepted artifact revision. Use human acceptance where no automatic signal exists.
2. **Arms:** fixed-model baseline versus routed, on the same task set.
3. **Repeat runs** enough times to measure run-to-run variance before comparing arms.
4. **Metrics:**
   - Cost per accepted task, including classifier cost, retries, and cache re-warm cost.
   - Acceptance rate.
   - Time to accepted task.
   - Retries per task.
5. **Pre-registered decision rule.** Promote only if cost per accepted task falls, and acceptance rate does not fall by more than the measured noise.

Each task carries an episode ID: a harness session or prompt ID, or WorkAgent's assignment and run IDs. A feedback API records acceptance against it. This is the episode-plus-feedback pattern from TensorZero; that repository was archived on June 12, 2026, so the idea is borrowed, not the code.

**Role economics.** Token Meter reports spend, average and P95 cost per run, and run counts for each named subagent role, and warns that lower spend is not proof of better outcomes. The router reports the same per role, from measured evidence and across the org, and adds the missing half: cost per accepted task for each role. A role's change between periods is shown only with full evidence coverage in both periods (6.5).

### 8.5 Routing engine: what is lifted from Switchyard, and what changes

**Lifted.**

1. **Decision separated from transport.** Switchyard's `libsy` picks a target and hands the call back to its host; it has no HTTP stack of its own. The router uses the same split: the decision is a pure function of request features and the policy version. The relay owns transport, policy enforcement, reservations, and settlement. This is why candidate A in the bake-off (10.1) can embed `libsy` without inheriting Switchyard's server.
2. **The route vocabulary** in 8.1.
3. **Episode affinity.** Switchyard's classifier route keeps session affinity. The router keeps every decision sticky for the episode, the same unit the 8.4 experiment measures.
4. **Zero-cost signals first.** The stage route makes no extra model call, which is why it is the first route type to evaluate.
5. **Configuration validation without traffic,** like Switchyard's `--dry-run`.
6. **Routing overhead as a metric.** Switchyard exports routing overhead alongside requests, errors, latency, and tokens. The router does the same (target below).

**Changed, because of what Switchyard's own users measured.** In LangChain's evaluation, the escalation judge was 21.2% of routed spend and added roughly 700ms per turn. Classmethod found that a minor version change of the classifier model flipped the same 50 conversations from 39 cheap-tier routings to one, with no error and high confidence. So:

1. **Every decision is recorded and replayable.** Each decision record holds the policy version, the input features, the decision-model ID and version, that model's raw output, and the chosen target. Replaying a recorded session with the recorded model outputs reproduces the same decisions, so any policy change can be tested offline against recorded traffic before it touches anyone.
2. **Decision models are pinned.** The policy names an exact model version. Changing it is a policy change: it gets replayed against recorded traffic first, and the shift in tier mix is shown before it ships.
3. **Drift alarms.** When the tier mix on live traffic moves by more than a configured amount between consecutive days or policy versions, the console raises an alert. This would have caught the 39-to-1 flip.
4. **Classifier cost counts.** Decision-model calls are metered attempts like any other. Their cost is part of cost per accepted task (8.4), never treated as overhead.
5. **Escalation is cache-aware.** Escalating mid-episode re-warms the frontier model's cache (8.3). Escalation fires only at a turn boundary, and its decision includes the estimated re-warm cost.
6. **A/B assignment is per episode, not per request.** Per-request splits mix tiers inside one task, which breaks cache reuse and makes outcome attribution impossible.

**Guarantees**

**R1. Routing decisions are reproducible.**
Acceptance test: replay recorded sessions twice with the same policy version and recorded decision-model outputs. 100% of decisions must be identical.

**R2. In v1, shadow routing never changes what is sent upstream.**
Acceptance test: run the certification sessions (H1) with shadow routing on and off. The upstream request bytes must be identical.

**Performance target, not yet a guarantee:** routing decision overhead p99 ≤ 1 ms for passthrough, alias, and stage routes, excluding any decision-model call. Measure it before committing.

## 9. Packaging and deployment

**Rule.** Containers are the default for everything that runs as a service, in our cloud and in a customer's. Native packaging is used only where a container would cost the user something real, and a managed service is used where it does the job better than a container we operate. Both packagings carry the same binary (D1).

| Component | Where it runs | Packaging | Why |
|---|---|---|---|
| Relay | Our cloud (AWS us-east-1) | Container image, two listeners: mTLS for sidecars, public HTTPS with virtual-key authentication for cloud-originated clients (Cursor) | Long-running service; the same image later serves the dedicated per-org instance and the self-hosted org install (12) |
| Control plane API and console | Our cloud | Two container images | Same reason; they move on-premises unchanged, with no dependence on a cloud's static-hosting services |
| Auth service (Keycloak) | Our cloud | Third-party container image (`quay.io/keycloak/keycloak`), pinned by digest, run as non-root with a read-only root filesystem where its documentation allows, on its own hostname and load balancer, against its own Amazon RDS for PostgreSQL instance separate from the ledger (ADR 0003) | Sign-in and enrollment only. It is off the traffic path, so an outage blocks new sign-ins and enrollments, not metered traffic or token renewal. |
| Sidecar in Kubernetes pods | Customer clusters | Container image as a native sidecar: an `initContainers` entry with `restartPolicy: Always`, stable since Kubernetes v1.33. It starts before the agent container and stops after it. | Pods are containers already |
| Sidecar on servers and VMs with a container runtime | Customer hosts | Container image through Compose, listening on loopback only, with no published port. When the harness runs on the host, the sidecar uses host networking (Linux). When the harness runs in a container, it shares the sidecar's network namespace, as in a pod (D3). | Same image as pods; upgrades are an image pull |
| Sidecar on servers and VMs without a container runtime | Customer hosts | Native binary as a systemd unit | A runtime installed only for us is a cost to the customer |
| Sidecar on laptops | End-user devices | Native binary as a launchd, systemd, or Windows service, distributed by MDM or package managers. Static binaries for macOS (arm64, x86_64), Linux (x86_64, arm64), and Windows (x86_64). | See below |
| `purser` CLI (`connect`, `run`) | Wherever the harness runs | Native binary | `run` launches the harness as a child process, and `connect` edits the harness's own configuration files |
| Postgres (ledger and control plane) | Our cloud | Amazon RDS for PostgreSQL | Backups, point-in-time restore, and Multi-AZ failover are what the ledger needs, and running them ourselves adds nothing. Postgres runs as a container only in development, tests, and CI, pinned to the production major version. |
| Secrets and keys | Our cloud | Generated by the install bootstrap (9.1) into AWS KMS and Secrets Manager, read at run time through the service's IAM role | Never in an image (D2); never shared between installs or environments (D4) |
| Development, tests, and CI | Laptops, Claude Code cloud sessions, GitHub Actions | One Compose stack: Postgres, mock providers, relay, control plane, console, and a fault-injection proxy | Same stack in all three places. Crash and partition tests (B1, B2, C2) need processes to kill and networks to cut, which containers make repeatable. |

**Why not containers on laptops.** Developers who already run containers may use the image on a laptop, but Purser never requires one there:
- On macOS and Windows a Linux container runs inside a virtual machine. The native sidecar avoids adding one to every user's laptop, and the VM's memory would count against the footprint budget in 10.2.
- Docker Desktop is free only for companies with fewer than 250 employees and less than $10 million in annual revenue, and government entities must buy a subscription regardless of size.
- The sidecar's device private key should live in the operating system's key store (the Keychain on macOS, TPM-backed storage on Windows), which a container can't reach.

**Image standards**
- **One build per target.** Each image is assembled from the released binary, not rebuilt from source (D1).
- **Multi-architecture:** `linux/amd64` and `linux/arm64`.
- **Minimal runtime:** distroless base images with no shell or package manager, pinned by digest. The data-plane binary is static, so its image holds the binary and CA certificates and little else.
- **Locked down:** non-root user, read-only root filesystem, and writable paths only as declared `tmpfs` mounts. That turns C2's scan into a closed list of places to check.
- **Supply chain:** an SBOM for every image, a vulnerability-scan gate in CI, and a keyless Sigstore (cosign) signature bound to this repository's release workflow.
- **Registry:** CI pushes to Amazon ECR in us-east-1 through GitHub's OIDC federation to AWS, so no AWS key is stored in GitHub; our services pull with their IAM role. A public registry for customers' pods is chosen before the first pilot (G3 in the build plan).
- **Releases:** images and installers are signed, and the control plane publishes the minimum supported sidecar version.

**Hosted orchestration (proposed, open question 9).** Amazon ECS on Fargate for v1: there is no cluster to operate and no control-plane fee. EKS charges $0.10 per cluster-hour on standard Kubernetes version support ($73.00 for a 730-hour month) and $0.60 per cluster-hour on extended support ($438.00) when version upgrades lag. The Helm chart exists either way for customers' pods, and CI tests it on kind (Kubernetes in Docker). Revisit when the self-hosted org install starts.

**Guarantees**

**D1. The sidecar/relay binary in the container image is the same build as the native release.**
Acceptance test: for each release, the SHA-256 of the binary inside the `linux/amd64` and `linux/arm64` images equals the published native Linux artifact for that architecture. H1 passes against the container image on Linux and against the native binary on macOS and Windows.

**D2. Every published image is minimal, non-root, read-only, and signed.**
Acceptance test: for each image and architecture, CI confirms that it runs as a non-zero UID; that its smoke tests (the H1 subset, for the data-plane image) pass with a read-only root filesystem and only the declared `tmpfs` mounts writable; that it contains no shell and no package manager; that an SBOM is attached; that its cosign signature verifies against this repository's release-workflow identity; and that the scan reports no critical vulnerability with an available fix. A canary key planted in the build environment never appears in any image layer.

**D3. A containerized sidecar accepts connections only from its own host or pod.**
Acceptance test: deploy through Compose and through the Helm chart (on kind). From the same host or pod, the harness connects with its local token. From another host, another pod, and another container on the same host's network, connections to the sidecar port are refused.

### 9.1 Install bootstrap

Every install and every environment is unique: development, staging, and production, and later each dedicated instance and self-hosted install. Each generates its own keys; a development install differs only in that it may also use the fixed credentials on the known-default list (below). One command, `bootstrap`, run as a one-off control-plane task at first start, generates:

- the Keycloak realm and its signing keys; the control plane's admin-API client and the console's OIDC client, with their secrets; the console's session secret;
- the control plane's own signing keys, including those for relay tokens (4);
- the device CA (4);
- the install-level HMAC keys;
- the TLS private keys for the relay's listeners, where the install holds them rather than a managed certificate service;
- the database credentials for Keycloak and the control plane, where the install creates them;
- the first admin, who is also the first platform operator (4).

Per-org material is generated when the org is created, by the same code and under the same rules: the org's HMAC key for trace deduplication (6.6), its data key for encrypted provider keys (3.2), and the signing keys of its OIDC issuer for workload identity federation (3.2).

Rules:

- **Idempotent.** It creates what is missing and changes nothing that exists. Re-running it on a bootstrapped install changes nothing: it never rotates a key and never creates a second admin. Each item is written to the secret store before it is installed where it is used, so a run interrupted at any step resumes without replacing anything already stored. Rotation is a separate, explicit operation.
- **Generated, never shipped.** Material comes from a cryptographically secure random source inside the install and is written only to the install's secret store (AWS Secrets Manager and KMS when we host it). None of it appears in an image, the repository, a Compose file, a Helm chart's values, or a log.
- **No default credentials outside development.** The development Compose stack may use fixed, published credentials, listed in the repository as known defaults. Every other profile refuses to start with any credential on that list, and an unset profile is not development. Keycloak's temporary bootstrap admin is generated, used only by the bootstrap, and deleted when it finishes.
- **The first admin** receives a one-time credential through the secret store, never a log. It expires at first sign-in, where the admin sets their own credential and enrolls MFA, which the platform operator role requires from then on.

**D4. Every install has its own secrets and keys, and no usable default credential.**
Acceptance test: in CI, bootstrap two fresh installs from the same images and configuration on the Compose stack in a non-development profile, each with its own secret store, and create one org in each. Before gate G2 in the build plan, run it once more against AWS Secrets Manager.
1. **Unique.** Inventory every secret and key from the secret stores, the services' running configuration, Keycloak's realm, and the databases, not from the bootstrap's own output. No secret, private key, or HMAC key is byte-equal between the two installs, and no public-key or certificate fingerprint matches.
2. **No default.** Against each install, try every credential found in the repository, the images, the Compose files, and the Helm chart, plus each component's upstream defaults (for example Keycloak's `admin`/`admin` and Postgres's `postgres` user), on every endpoint that authenticates: the console and admin API, Keycloak's admin console and admin API, Postgres, both relay listeners, and device enrollment. Every attempt is rejected. Keycloak's temporary bootstrap admin no longer exists.
3. **First admin.** The first admin cannot finish signing in without enrolling MFA, and the one-time credential is rejected after first use. A platform operator without MFA is refused.
4. **Not leaked.** No generated secret, private key, or the one-time credential appears in any log or image layer.
5. **Idempotent.** A second bootstrap run changes no stored secret or key and creates no second admin. A run killed at each step in turn, then re-run, ends with a working install and replaces nothing already stored.
6. **Profiles.** Starting any service with the profile unset, or outside development with a credential from the known-default list, fails.

## 10. Foundation decision: bake-off

The sidecar/relay foundation is chosen by measurement, not assertion.

### 10.1 Candidates

- **A. Rust binary embedding Switchyard's crates:** `libsy`, `protocol`, `switchyard-translation`.
  - `libsy` picks a target and hands the model call back to the host; it has no HTTP stack.
  - The README still labels the project pre-alpha and "not for production use". The server installs with `cargo install --locked switchyard-server`; the library path uses Git dependencies. Pin by commit and evaluate each component separately.
- **B. Go binary embedding Bifrost's core** (`go get github.com/maximhq/bifrost/core`, Apache 2.0), with its middleware for reservation hooks.
- **C. An existing gateway in the relay role** (for example agentgateway or the Bifrost server) behind our reservation service.
  - This only qualifies if it exposes a pre-dispatch hook that runs before every upstream attempt, including its own retries and fallbacks.
  - agentgateway's built-in budgets charge after the response returns, so its native budgets can't satisfy B1.

### 10.2 Criteria, measured the same way for each candidate

| Criterion | Measurement | Gate |
|---|---|---|
| Compatibility | Certification suite (H1) pass rate per matrix row | 100% on the v1 rows |
| Enforcement coverage | Share of upstream attempts, including internal retries and fallbacks, preceded by a committed reservation (B2) | 100% |
| Laptop footprint | Binary size, idle resident memory, resident memory at 10 concurrent streams, idle CPU over 10 minutes, cold start time, on two reference laptops (one macOS, one Windows) | Thresholds fixed before measuring |
| Container footprint | Image size per architecture, time from container start to ready, resident memory at 10 concurrent streams in a pod; D2 checks pass | Thresholds fixed before measuring; D2 must pass |
| Added latency | Time to first byte p50 and p99, versus a direct connection | Thresholds fixed before measuring |
| Maintenance | Upstream release cadence, breaking changes in the last 90 days, local patches needed | Reviewed |
| Effort | Engineer-days to pass the first two rows | Reviewed |

Timebox: two weeks. If the results don't separate the candidates, choose the one with 100% enforcement coverage and the highest compatibility, and break ties on footprint.

## 11. WorkAgent as design partner

**Current state.** WorkAgent is pinned to the OpenAI Responses API during alpha (`gpt-6.1-sol`, official origin, base URL checked against that origin). It has its own grant ledger with dollar, call, and token reservations enforced in both the app and Postgres. Stateful Responses may be dropped.

**Integration.**
1. WorkAgent's provenance check accepts an approved router origin.
2. The relay returns routing-receipt headers: upstream, model, attempt ID, price-book version, and shadow decision ID.
3. WorkAgent's ledger concepts map onto router budgets: conservative reservations, unknown outcomes keeping their reservations, computed and billed cost reported separately. The two products share concepts but release independently.

**W1. WorkAgent's bounded live test produces the same usage through the router as direct.**
Acceptance test: replay the same synthetic turns directly and through the router. Token counts per class must be identical, and the router's computed cost must equal WorkAgent's own calculation from the same price inputs.

## 12. Deferred items and their entry criteria

| Item | Entry criterion | Required design before starting |
|---|---|---|
| Budget leases and multi-region | The reservation performance target (7.7) is missed in measurement | Fencing tokens. A lease is never reallocated until its holder's attempts are reconciled or the maximum billable duration (provider timeout plus maximum stream length) has passed. |
| Expected-cost (bounded) budgets | Strict-mode held headroom is a measured top complaint | The overrun bound is defined over globally outstanding billable attempts, including retries and unknown outcomes. Unknown attempts retain their reservation and their unreserved risk allowance. |
| Self-hosted org install | The first design partner or paying customer who requires it | The full stack (Keycloak, Postgres, control plane, relay) as a Compose file and a Helm chart, from the same images we run. The same bootstrap (9.1), so D4 holds for each install. Postgres is the customer's managed instance or a container they operate. Our service receives nothing by default; any telemetry to us is opt-in and metadata only (3.1). |
| Dedicated per-org instance (premium tier) | The first customer who will pay for it | The hosted images and bootstrap, one org per install, with its own realm, databases, and keys (D4). The data model stays multi-org (1.1). |
| Personal mode (possibly a free tier) | After the release candidate (gate G4 in the build plan) | One person, shipped as a container, with no Keycloak. On a laptop that is an exception to section 9's rule that Purser never requires a container there; the design either justifies it or adds native packaging. A built-in passkey or TOTP login serves as the configured OIDC issuer (4). The user holds their own provider keys, so G2 and C1 are restated per deployment model, each with its acceptance test, before work starts. The data model stays multi-org (1.1). |
| Protocol translation routes | A named harness/provider pair with demand | Each pair passes H1 on recorded traffic. The candidate implementation is Switchyard's `switchyard-translation` crate, which translates requests, responses, and streams between OpenAI Chat, OpenAI Responses, and Anthropic Messages. Claude Code with non-Claude models is an explicit, labeled-unsupported opt-in. |
| Adaptive routing in enforce mode | The 8.4 experiment shows lower cost per accepted task | Pre-registered rule met. |
| Content-based sensitivity enforcement | A classifier evaluated with measured recall on labeled data | Advisory at first; destination policy remains authoritative. |
| Status companion (macOS menu bar, Windows and Linux tray) | Trace reading and the budget interface are in use | Shows connection state, remaining budget, and evidence coverage; reads only the sidecar's local status endpoint. Token Meter's companions are the reference. |
| Provider quota reads for subscription plans | Customers want subscription limits next to router budgets | Local-only and read-only, using credentials already on the device; quota numbers may leave the device, credentials never do. |
| Hook-based model choice in enforce mode | The 8.4 experiment shows the hook actuator lowers cost per accepted task | Per-harness hook certification, and the user can always override. |
| Prompt and response capture | Customer demand for work-per-dollar analysis | Writes to the customer's own storage. |
| GPU and on-prem telemetry routing | A customer running on-prem inference pools | Fast loop in the gateway (inference-server queue and KV-cache metrics); slow loop in the control plane. GPU metrics come from NVIDIA's DCGM exporter, which already runs as a container on GPU nodes. |
| More upstreams (Bedrock, Vertex, Azure OpenAI) | Customer demand | Native-format pass-through first; federation for credentials. |

## 13. Open questions

1. Product name. Decided 2026-10-07: Purser.
2. Source of truth for the price book: manual entry, provider APIs, or both.
3. Budget windows in UTC or the org's time zone.
4. Which two reference laptops to use for the bake-off.
5. Relay retry policy beyond the v1 cap of one.
6. Whether hosted decision models (Jev, OpenAI Decisions) are allowed in the shadow router by default, or opt-in per org.
7. Whether partial metering is acceptable to Cursor customers, given that features on Cursor's own models bypass the router, and whether to offer it at all before Cursor supports gateways officially.
8. Whether to port Token Meter's trace parsers (MIT, Python) into the sidecar's language, or reimplement them and keep Token Meter purely as a test oracle.
9. Hosted orchestration: Amazon ECS on Fargate (proposed in section 9) or Amazon EKS. Needed before the first cloud deployment in Phase 2.

## Sources

- Claude prompt caching: https://platform.claude.com/docs/en/build-with-claude/prompt-caching
- Claude token counting: https://platform.claude.com/docs/en/build-with-claude/token-counting
- Anthropic workload identity federation: https://platform.claude.com/docs/en/manage-claude/workload-identity-federation
- OpenAI workload identity federation: https://developers.openai.com/api/docs/guides/workload-identity-federation
- OpenRouter workload identity federation: https://openrouter.ai/docs/guides/overview/auth/workload-identity-federation
- Claude Code gateway compatibility guide: https://code.claude.com/docs/en/llm-gateway-protocol
- Claude Code: other LLM gateways: https://code.claude.com/docs/en/llm-gateway
- Codex through a gateway (Coder docs): https://coder.com/beta-docs/ai-coder/ai-gateway/clients/codex/
- Hermes Agent configuration: https://hermes-agent.nousresearch.com/docs/user-guide/configuration
- OpenClaw custom provider setup: https://haimaker.ai/blog/openclaw-custom-provider-setup/
- OpenCode providers: https://opencode.ai/docs/providers
- Why localhost doesn't work as the OpenAI base URL in Cursor: https://dev.to/orchidfiles/why-localhost-doesnt-work-as-openai-base-url-in-cursor-and-how-to-fix-it-589e
- LiteLLM Cursor integration: https://docs.litellm.ai/docs/tutorials/cursor_integration
- Splunk Token Meter: https://github.com/splunk/token-meter
- Token Meter Claude cost-correctness design: https://github.com/splunk/token-meter/blob/main/specs/claude-cost-correctness/design.md
- Token Meter subagent role economics design: https://github.com/splunk/token-meter/blob/main/specs/2026-09-24-subagent-role-economics-design.md
- Anthropic pricing: https://platform.claude.com/docs/en/about-claude/pricing
- OpenAI API pricing and multipliers (secondary, checked 2026-09-08): https://www.eesel.ai/blog/openai-api-pricing
- NVIDIA NeMo Switchyard: https://github.com/NVIDIA-NeMo/Switchyard
- Switchyard benchmark analysis (LangChain figures): https://nerdleveltech.com/en/nemo-switchyard-agent-model-routing
- Bifrost: https://github.com/maximhq/bifrost
- agentgateway budget limits: https://agentgateway.dev/docs/standalone/latest/llm/cost-controls/budget-limits/
- TensorZero (archived): https://github.com/tensorzero/tensorzero
- OpenAI Decisions API coverage: https://ai-tldr.dev/releases/openai-decisions-api/
- Docker pricing FAQ: https://www.docker.com/pricing/faq/
- Kubernetes sidecar containers: https://kubernetes.io/docs/concepts/workloads/pods/sidecar-containers/
- Amazon EKS pricing: https://aws.amazon.com/eks/pricing/
- Distroless container images: https://github.com/GoogleContainerTools/distroless
- Sigstore cosign: https://docs.sigstore.dev/
- kind (Kubernetes in Docker): https://kind.sigs.k8s.io/
- WorkAgent repository: https://github.com/alindebergASL/Workagent
