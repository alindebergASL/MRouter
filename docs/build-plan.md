# Purser: v1 Build Plan

Draft 0.3, 2026-10-09. Owner: Andrew Lindeberg. Spec: `docs/architecture.md` in the repository (draft 0.6). Guarantee IDs (C1, B1, H1, D1, …) refer to that document.

Changes in 0.3: the deployment decision recorded (10): v1 is our hosted multi-tenant service, and the other models are deferred with entry criteria in spec section 12. The install bootstrap command (spec 9.1, D4) and Postgres row-level security for org isolation (spec 4, A5) are added to Lane D (5, 7) and to gate G2 (6). Lane B's ledger functions take the org as an explicit argument and fail closed when the org has no budget policy (spec 7.6, B5). The v1 schedule doesn't change. The auth service decision is recorded as made (ADR 0003), and Appendix A matches the root `CLAUDE.md` invariants.

Changes in 0.2: containers in the build (2.8), the `deploy/` layout (3), container work assigned to lanes and gates (5 to 7), and two new open decisions (10).

## 1. Assumptions

- **Team.** Andrew is owner, reviewer, and merger. Claude Code does most implementation, with at most four concurrent sessions at peak; review is the bottleneck, not typing.
- **Repository.** One GitHub monorepo: https://github.com/alindebergASL/MRouter (empty as of 2026-10-07). It is currently public; while it stays public, recordings, customer data, and price agreements must never be committed, and secret scanning must be on from the first commit. The product is named Purser; the repository keeps the name MRouter. Use `purser` for the CLI and binary (`purser connect claude-code`) and `purser-*` for crates, modules, and packages. Run a USPTO search and domain check before any public launch.
- **The spec is the contract.** Every guarantee ID becomes an acceptance test before the code that satisfies it.
- **Containers are first-class.** Every service ships as a container image, and the build itself runs on one Compose stack. Native packaging is used only where spec section 9 says a container costs the user something real (laptops, the `purser` CLI, hosts without a container runtime), and managed services replace containers we would otherwise operate (Postgres, secrets).
- **Durations are planning targets,** not commitments. Re-baseline at gates G0 and G1 using measured throughput (merged PRs per week and guarantee tests passing).

## 2. How Claude Code is used

### 2.1 Tests first, from the spec

For each guarantee, a session first writes the acceptance test, named for its ID (for example `test_B1_strict_budget_never_exceeded`). Andrew approves the test. Only then does a session implement against it. Claude Code is strongest when "done" is a test that passes; the guarantee IDs make that explicit, and they keep any session from redefining success.

### 2.2 Protected paths

`contracts/` and `tests/acceptance/` are the spec in code. Workstream sessions may read them but not change them. Changes go through a spec-change session and a pull request labeled `spec-change`. Three layers enforce this:

1. **A `PreToolUse` hook** blocks `Edit` and `Write` on protected paths unless the session was started with `ROUTER_SPEC_CHANGE=1` (Appendix B). This is a guardrail, not a wall; a shell command could still write the file.
2. **CODEOWNERS** puts Andrew on every protected path.
3. **A CI check** fails any pull request that touches a protected path without the `spec-change` label. This is the actual enforcement.

### 2.3 One lane, one worktree, one status file

- Each lane runs in its own worktree: `claude --worktree lane-b-money`. Claude Code creates it under `.claude/worktrees/<name>/` on a branch named `worktree-<name>`. Add `.claude/worktrees/` to `.gitignore`, and list local env files in `.worktreeinclude` so each new worktree gets them.
- Each lane keeps `handoffs/<lane>/STATUS.md`: what's done, what's next, what's blocked, and which guarantee tests pass. This is the same handoff pattern WorkAgent uses, and it's what lets a new session pick up where the last one stopped.
- Use **agent view** (`claude agents`, research preview) to dispatch and watch background lane sessions from one screen, and **cross-session messaging** when one lane needs to tell another something (for example "contracts v0.2 merged").
- **Agent teams** are experimental, disabled by default, and don't isolate teammates in worktrees. Don't use them here; separate worktrees per lane are safer.
- For long work that should continue while your machine is off, **Projects** at claude.ai/code runs threads in the cloud (public beta on Pro and Max).

### 2.4 Configuration checked into the repo

- **Root `CLAUDE.md`:** the product in one paragraph, the invariants, the guarantee-ID convention, protected paths, and the workflow. Draft in Appendix A. Keep it short; per-component detail goes in each component's own `CLAUDE.md`.
- **Subagents in `.claude/agents/`:**
  - `guarantee-reviewer`: reviews a diff against the guarantees it claims, in its own context, without having written the code. Draft in Appendix C.
  - `pricing-auditor`: checks every price-book entry against a dated provider source and flags any that lack one.
  - `security-reviewer`: checks diffs touching credentials, logging, or egress against C1 to C4.
- **Hooks in `.claude/settings.json`:** the protected-path guard; a `PostToolUse` formatter per component; a `Stop` hook that runs the component's fast tests and blocks stopping while they fail (guard it against loops).
- **Review on pull requests:** Claude Code's Code Review for multi-agent PR review, plus the security-guidance plugin on lanes touching credentials or egress. Andrew merges.

### 2.5 Every slice follows the same loop

1. Plan mode: the session proposes a plan referencing guarantee IDs; Andrew approves.
2. Acceptance tests (if not already merged).
3. Implement until they pass.
4. `guarantee-reviewer` subagent pass.
5. Update STATUS.md; open a small pull request.
6. Code Review, then Andrew merges.

### 2.6 Dogfooding

From gate G2, route the build team's own Claude Code sessions through the router. The build becomes the first budgeted customer and a source of real traffic. Keep a one-command fallback profile that points Claude Code straight at the provider, so a router outage never blocks the build.

### 2.7 Recordings are sensitive

Certification recordings contain real prompts and code. Store them in a private bucket, never in git. Commit only manifests (scenario, harness version, hashes). Capture them only from sessions recorded deliberately for this purpose. The recorder strips `x-api-key`, `Authorization`, and cookie headers before writing anything, and a test with a canary key proves it.

### 2.8 Containers in the build

- **One Compose stack, three places.** `deploy/compose/dev.yaml` runs Postgres, mock providers, the relay, the control plane, the console, and a fault-injection proxy (Toxiproxy). The same file runs:
  - on Andrew's Mac, under Docker Desktop (free under its small-business terms) or Colima;
  - in Claude Code cloud sessions, whose VMs include `docker` and `docker compose`, and whose default Trusted network level allows Docker Hub, GHCR, gcr.io, and public ECR;
  - in GitHub Actions.
- **Cloud-session start-up.** The environment's setup script pre-pulls the pinned images; the environment cache keeps pulled images on disk but not running containers. A `SessionStart` hook in the repository starts the stack when `CLAUDE_CODE_REMOTE` is `true`. Locally, `make dev-up` starts it.
- **Real Postgres in tests.** Ledger tests run against the stack's Postgres container, pinned by digest to the same major version as production (Amazon RDS). The cloud VM's preinstalled PostgreSQL 16 is not used, so the version is decided in one place.
- **Faults are container operations.** Crash injection for B1, B2, and C2 kills the relay container mid-attempt; partitions go through Toxiproxy. Both are scripted and repeatable.
- **Images from day one.** Each component gets its Dockerfile in the same pull request as its first code, and CI builds both architectures and runs the D2 checks from then on.
- **Base images.** Pull official images from the public ECR mirror (`public.ecr.aws/docker/library/…`) and distroless images from gcr.io rather than Docker Hub, which rate-limits anonymous pulls. Pin every base image by digest.
- **The `purser` CLI and the laptop sidecar stay native.** They are tested on macOS and Windows runners as well as in containers (D1).

## 3. Repository layout

```
MRouter/
├── CLAUDE.md
├── Makefile            dev-up, dev-down, test, images: the same commands locally and in CI
├── contracts/          OpenAPI admin API; JSON Schemas: usage event, attempt, decision record,
│                       price book, trace event, error envelopes; golden fixtures      [protected]
├── tests/acceptance/   One test module per guarantee ID                               [protected]
├── ledger/             Postgres migrations and reserve/settle/release/mark-unknown functions
├── pricing/            Python reference implementation (test oracle) and fixture tooling
├── dataplane/          Sidecar and relay: one binary, two roles (language from the bake-off)
├── controlplane/       FastAPI service
├── console/            Next.js app on the generated TypeScript client
├── certification/      Recorder, replayer, mock providers, scenario catalog
├── deploy/
│   ├── images/         Dockerfile per image (relay/sidecar, control plane, console, mock providers)
│   ├── compose/        dev.yaml (the build's stack); sidecar.yaml (single-host sidecar)
│   ├── helm/           Chart for the pod sidecar; relay and control plane later
│   ├── aws/            Infrastructure as code for us-east-1 (from Phase 2)
│   └── native/         systemd, launchd, and Windows service units; installers
├── docs/adr/           One decision record per significant choice
├── handoffs/<lane>/STATUS.md
└── .claude/            agents/, skills/, settings.json (hooks)
```

## 4. Decoupling the language decision

The bake-off (spec section 10) decides the data-plane language after about two weeks. Everything else must not wait for it:

- **The ledger lives in Postgres.** Reserve, settle, release, and mark-unknown are SQL functions with the transaction rules from spec 7.6. Any relay language calls them, and the B1/B2/B5 property tests run against them from Python before the relay exists.
- **Pricing is defined by fixtures.** A price-book JSON Schema, golden fixtures (usage in, exact nano-dollars out), and a Python reference implementation. The data-plane implementation must reproduce every fixture exactly (M2, M5, B6). The control plane stores and displays prices but never computes them.
- **Accounting adapters are defined by fixtures.** Recorded provider responses in, normalized usage out.
- **The control plane and console depend only on `contracts/`.**

## 5. Lanes

At most four lanes run at once.

| Lane | Scope | Owns guarantees |
|---|---|---|
| **A. Spec and proof** | `contracts/`, acceptance tests, the certification lab (recorder, replayer, mock providers), the Compose dev stack, CI including image builds, scanning, SBOMs, and signing; later the security test infrastructure | H1, H2 (test side), C1–C4 test harnesses, D1, D2 |
| **B. Money** | Ledger SQL, attempt state machine, price-book schema and Python oracle, accounting-adapter fixtures; after the bake-off, the pricing and adapter code in the data plane | B1–B6, P1, M1–M5 |
| **C. Data plane** | The bake-off; then relay (federated credentials, destination policy, dispatch, settlement, public listener) and sidecar (enrollment, `connect` and `run`, loopback endpoint, local models, budget MCP); the sidecar's Compose file and Helm chart | C1–C3, A2, A3, A4 (implementation side), A5 (relay side), H1 per row, D3 |
| **D. Control plane and console** | Auth-service integration (Keycloak per ADR 0003: MFA, SSO, SCIM), the install bootstrap command (spec 9.1), orgs and RBAC with row-level security (spec 4), devices and the CA, virtual keys, model catalog, budgets, usage views with evidence tiers and coverage, audit; from Phase 2, the us-east-1 deployment (RDS, ECS or EKS, KMS, Secrets Manager) | A1, A4 (API side), A5, D4 |
| **E. Coverage** (from Phase 4) | Trace readers, keyed-hash dedup, evidence tiers, reconciliation job, Token Meter oracle | C4, M3, M4 |
| **F. Routing lab** (from Phase 4) | Shadow router, decision records, replay, drift alarms, episodes and feedback API, harness-hook recommendations | R1, R2 |

Lanes E and F start only when A and D have capacity to spare.

## 6. Phases and gates

| Phase | Target | Lanes running | Exit gate |
|---|---|---|---|
| **0. Foundations** | 1 week | A, D | **G0:** repo, CI, `CLAUDE.md`, hooks, and subagents in place; the Compose dev stack (Postgres at first) starts in CI, in a cloud session, and on Andrew's Mac; contracts v0 merged; recordings for Claude Code and Codex covering streaming, tool calls, an interrupted stream, and compaction; auth service chosen (ADR) |
| **1. Parallel foundations** | 2 weeks | A, B, C (bake-off), D | **G1:** foundation chosen (ADR, by spec 10.2 criteria, container footprint included); ledger property tests pass (B1, B2, B5 against SQL in the stack's Postgres container, with crashes injected); price fixtures complete for Anthropic and OpenAI (M2, M5 against the oracle); control-plane skeleton with OpenAPI and generated client |
| **2. First vertical slice** | 3 weeks | A, B, C, D | **G2:** Claude Code → sidecar → relay → Anthropic through workload identity federation, under a strict budget, metered, visible in the console, with protocol-correct denials. The relay, API, and console run as containers in us-east-1 against Amazon RDS. The us-east-1 install is created by the bootstrap command. Passing: H1 (Claude Code row), C1–C3, B1–B5, P1, M1, M2, A1, A2, A5, D1, D2, D4. Dogfooding starts. |
| **3. Breadth** | 3 weeks | A, B, C, D | **G3:** Codex, OpenCode, Hermes, OpenClaw, and WorkAgent rows; OpenAI and OpenRouter adapters and federation; budget MCP; the sidecar as a container through Compose and the Helm chart (tested on kind). Passing: H1 (all those rows), M5, B6, A4, W1, D3. Ready for a design-partner pilot. |
| **4. Coverage and routing lab** | 3 weeks | A or E, C, D, F | **G4 (release candidate):** Cursor row through the public listener; trace readers; shadow routing; reconciliation; signed installers and images. Passing: H2, A3, C4, M3, M4, R1, R2. |
| **5. Pilot and experiment** | Ongoing | As needed | First month's reconciliation; the spec 8.4 routing experiment; decision on enforce-mode routing |

Sum of targets through G4: 12 weeks. The v1 schedule doesn't change with spec draft 0.6: the bootstrap command and row-level security are Lane D work inside Phases 1 and 2. That is re-checked at G1, with the D4 and A5 tests counted in the measured throughput.

## 7. First tasks per lane (Phases 0 and 1)

**Lane A**
1. Monorepo skeleton, CI, root and component `CLAUDE.md` files, hooks, the three subagents, CODEOWNERS, and the `spec-change` label check. Also the first Compose dev stack (Postgres only, pinned by digest), a CI job that starts it and waits for health, the `SessionStart` hook that starts it in cloud sessions, and a proposed setup script for the cloud environment that the session has verified works. The session also checks how containers in a cloud session reach the network (proxy settings and CA bundle) and records the answer in `deploy/compose/README.md`, because the recorder will need it for OpenAI and OpenRouter recordings.
2. `contracts/` v0: usage event, attempt, error envelopes (Anthropic and OpenAI shapes), price book, decision record, trace event. JSON Schemas plus examples.
3. Recorder: a recording proxy, run in the Compose stack, that captures full request and response streams from a real harness session, with the scenario catalog from H1. The harness runs natively and sends its own key; the recorder strips credential headers before writing (2.7).
4. Replayer and mock providers, each as a container in the stack. The mock logs every attempt it receives (needed by M1 and B2) and can bill random usage within a request's bounds (needed by B1).

**Lane B**
1. Ledger schema and SQL functions: reserve (read the org's budget policy and deny with its own error code if there is none; lock windows in ascending ID order; check; increment; insert attempt), settle (idempotent on attempt ID), release, mark-unknown. Every function takes the org as an explicit argument from the authenticated identity; the ledger tables have no row-level security in v1 (spec 4, 7.6).
2. B1, B2, B5 (including an org with no budget policy) property tests in Python against the stack's Postgres container and the mock provider, with concurrency, crashes (container kills), and failover (Toxiproxy) injected.
3. Price-book schema covering every rule type in spec 6.3; Python reference implementation; golden fixtures, including the Opus 5.5 fast plus US-only example that must equal $0.209.
4. Anthropic and OpenAI adapter fixtures from Lane A's recordings.

**Lane C**
1. Bake-off harness: the same H1 subset and B2 attempt-coverage check run against candidates A (Rust with Switchyard's crates), B (Go with Bifrost's core), and C (an existing gateway with a pre-dispatch hook), each built as an image and run in the Compose stack.
2. Footprint and latency measurement scripts: natively on the two reference laptops, and as containers for the container-footprint row (spec 10.2).
3. ADR with the measured results and the choice.

**Lane D**
1. Auth-service evaluation against requirements: TOTP and passkey MFA, OIDC and SAML SSO, SCIM. ADR.
2. FastAPI skeleton: orgs, workspaces, users, roles; OpenAPI export; generated TypeScript client; drift check (A1); its Dockerfile, added to the Compose stack. Tokens are validated against the configured OIDC issuer and audience, and every org-scoped table outside the ledger has a forced row-level security policy from the first migration (spec 4, A5). Only platform operators create orgs and assign an org's first owner; each operator call requires an MFA-level `acr` and writes an audit row; creating an org writes its budget policy row (spec 7.1).
3. Install bootstrap command (spec 9.1, D4): one idempotent control-plane task that generates the realm, admin-client secrets, signing keys, the device CA, HMAC keys, and the first admin, who is also the first platform operator, with MFA required. No default credentials outside the dev profile.
4. Device enrollment through an OAuth device-authorization flow; control-plane CA issuing mTLS client certificates.
5. Console shell on the generated client.

## 8. Definition of done for a pull request

- The acceptance tests for every guarantee the PR claims pass, and no existing guarantee test regresses.
- No protected path changed without the `spec-change` label.
- `guarantee-reviewer` has run, and its findings are resolved or answered.
- Code Review has run.
- STATUS.md is updated.
- No prompt or response content appears in logs, metrics labels, or fixtures (C2 check).
- Every image the PR touches builds for `linux/amd64` and `linux/arm64` and passes the D2 checks.

## 9. Risks

| Risk | Mitigation |
|---|---|
| Harness releases change behavior (Claude Code ships often; Cursor's integration is unofficial) | Nightly headless certification replay against the latest harness versions; a failing row is demoted automatically |
| The bake-off is inconclusive | The spec's default rule decides: 100% enforcement coverage, then compatibility, then footprint |
| Federation setup is hard for early customers | Encrypted-key fallback behind a flag from Phase 2 |
| A wrong price leaks budget | `pricing-auditor` subagent, dated sources on every entry, monthly reconciliation (M3) |
| Review bandwidth (one approver, four sessions) | Small PRs, independent reviewer subagents, no more than four concurrent lanes |
| Dogfooding blocks the build during an outage | One-command fallback profile straight to the provider |
| Parallel sessions multiply token spend | Meter the build through the router from G2 and give it its own budget |
| Image pulls are slow or rate-limited in cloud sessions and CI | Base images from public ECR and gcr.io, pinned by digest; pre-pulled by the cloud environment's setup script so the environment cache holds them |
| Container-only testing hides laptop problems | H1 also runs against the native binary on macOS and Windows runners (D1); the bake-off measures native footprint on real laptops |

## 10. Decisions

**Made (2026-10-07):**

| Decision | Outcome |
|---|---|
| Product name | Purser (trademark and domain checks pending) |
| Repository | https://github.com/alindebergASL/MRouter, public for now |
| Cloud and region | AWS us-east-1 |
| Claude plan | Claude Code Max (cloud sessions and environment API credentials available) |
| Build spend on providers | $20 to start, raised deliberately when needed |
| Packaging | Containers first: every service is an image and the build runs on one Compose stack. Native only for laptops, the `purser` CLI, and hosts without a container runtime; managed Postgres and secrets (spec 9) |

**Made (2026-10-09):**

| Decision | Outcome |
|---|---|
| Auth service | Keycloak, self-hosted on ECS Fargate against its own RDS instance; one realm per install with Keycloak Organizations as customer orgs ([ADR 0003](adr/0003-auth-service.md)) |
| Deployment model | v1 is our hosted multi-tenant service, and that is the revenue model. Orgs are isolated by org ID in every query, by row-level security, and by Keycloak Organizations in one realm (A5). Deferred, each with an entry criterion in spec section 12: a customer-hosted relay with our hosted control plane (the first customer who requires content to stay in their network), the self-hosted org install (the first design partner or paying customer who requires it), a dedicated per-org instance as a premium tier (the first customer who will pay for it), and personal mode (after the release candidate, possibly as a free tier). Applying now: every install generates its own secrets through one bootstrap command, with no default credentials outside dev (D4); the data model stays multi-org; the control plane validates tokens against a configured OIDC issuer. The v1 schedule doesn't change (6). |

**Still open:**

| Decision | Needed by |
|---|---|
| The two reference laptops for the bake-off | Start of Phase 1 |
| Hosted orchestration: ECS on Fargate (proposed; no control-plane fee) or EKS ($0.10 per cluster-hour on standard support, $73.00 for a 730-hour month) | Start of Phase 2 |
| Public registry for customers to pull the sidecar image | G3 |
| Design-partner org besides WorkAgent | G3 |

## 11. Build secrets and spend

Provider keys never go in chat, in the repository, or in a cloud environment's plain environment variables.

**Provider-side caps, totaling $20:**

| Provider | Scope | Cap | What happens at the cap |
|---|---|---|---|
| Anthropic | A dedicated `purser-build` workspace in the Claude Console | $8 monthly workspace spend limit | Requests are refused (API control for this is in early access; set it in the Console) |
| OpenAI | A dedicated `purser-build` project | $8 monthly project hard limit | 429 `project_spend_limit_exceeded`; enforcement can lag slightly, so spend can exceed the cap by a small amount |
| OpenRouter | A dedicated key | $4 key credit limit | 402 with `limit_source` `openrouter_key_limit` |

The recorder also keeps its own running total, priced by the Python oracle, and refuses new requests once the build's cap would be exceeded. That is the router's reservation rule applied to its own build.

**Where each key lives:**

- **Cloud sessions:** OpenAI and OpenRouter keys are stored as API credentials on the claude.ai/code environment, with allowed hosts `api.openai.com` and `openrouter.ai`. The agent proxy attaches them after requests leave the VM, so the key never reaches Claude, its commands, or environment variables.
- **The Anthropic key can't use that mechanism.** The agent proxy never attaches a credential to `api.anthropic.com`. Work needing it, including recording Claude Code sessions, runs locally.
- **Local:** keys live in the macOS Keychain or a password manager and are loaded at run time. For Claude Code recordings, an `apiKeyHelper` script reads the key. Never write keys to a file in the repository, not even a gitignored one.
- **CI (from Phase 2):** a GitHub environment named `live-providers` holding the keys, with Andrew as required approver. Live-provider jobs run only on `main` or manual dispatch, never on pull requests, because the repository is public.
- **Production:** workload identity federation per the architecture doc (3.2), not stored keys.

**Expected Phase 0 spend.** One recorded Claude Code scenario on Claude Sonnet 5.5 ($2/M input, $10/M output, $2.50/M 5-minute cache write, $0.20/M cache read): 8 requests, a 40,000-token prefix written to cache on the first request, 3,000 new tokens and 1,500 output tokens per request, with the cached prefix growing by 3,000 tokens per turn.

- First request: $0.115.
- Requests 2–8: $0.0305 to $0.0341 each.
- Total per scenario: $0.3411.
- Six scenarios: about $2.05.

This is about a quarter of the Anthropic cap. Record on Sonnet: the protocol shape, not model quality, is what's being captured.

## Appendix A. Draft root `CLAUDE.md`

```markdown
# Purser

A model router that sits between AI coding agents and model providers, enforcing
budgets and metering every request. Spec: docs/architecture.md. Guarantee IDs
(C1, B1, H1, ...) refer to it.

## Invariants (never break these)
- No provider credential ever reaches a sidecar or an end-user device.
- No prompt or response content is persisted, logged, or put in metrics labels.
- Every upstream attempt, including retries, has a committed reservation first.
- Money is exact decimal, rounded once per attempt to integer nano-dollars;
  ceilings round up.
- Unpriced means unbounded. Unavailable is not zero.
- Pass-through traffic is forwarded byte for byte.
- Images carry no credentials, run as non-root on a read-only root filesystem,
  and contain the same binary as the native release.
- Every install generates its own secrets and keys; no default credentials
  outside dev.
- Every request-path query on org data filters by org ID. Row-level security backs
  it up on control-plane tables; the ledger takes the org explicitly and fails closed.

## Workflow
1. Plan mode first; reference the guarantee IDs the change affects.
2. Acceptance tests live in tests/acceptance/ and are named test_<ID>_*.
   They are protected: never edit them or contracts/ in a normal session.
3. Implement until the tests pass; run the guarantee-reviewer subagent.
4. Update handoffs/<lane>/STATUS.md and open a small pull request.

## Commands
(Each component's CLAUDE.md lists its build, test, and lint commands.)
```

## Appendix B. Protected-path hook

`.claude/hooks/protect-spec.sh`:

```bash
#!/usr/bin/env bash
path=$(jq -r '.tool_input.file_path // empty')
case "$path" in
  */contracts/*|*/tests/acceptance/*)
    if [ "${ROUTER_SPEC_CHANGE:-0}" != "1" ]; then
      echo "Protected path: $path. Contracts and acceptance tests change only in a spec-change session." >&2
      exit 2
    fi
    ;;
esac
exit 0
```

`.claude/settings.json`:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Edit|Write",
        "hooks": [
          { "type": "command", "command": "\"${CLAUDE_PROJECT_DIR}\"/.claude/hooks/protect-spec.sh" }
        ]
      }
    ]
  }
}
```

Exit code 2 from a `PreToolUse` hook blocks the tool call, and its stderr is shown to Claude.

## Appendix C. Draft `guarantee-reviewer` subagent

`.claude/agents/guarantee-reviewer.md`:

```markdown
---
name: guarantee-reviewer
description: Reviews a diff against the spec guarantees it claims to satisfy. Use before opening any pull request.
tools: Read, Grep, Glob, Bash
---

You review changes to Purser. You did not write them.

1. Read the diff and the guarantee IDs it claims (in the PR description or STATUS.md).
2. For each ID, read its definition and acceptance test in docs/architecture.md and
   tests/acceptance/. Confirm the test actually exercises the guarantee, not a
   weaker property.
3. Check the invariants in CLAUDE.md against the diff, especially logging of content,
   credential handling, and any upstream call without a reservation.
4. Run the acceptance tests for those IDs.
5. Report: each ID with pass, fail, or not demonstrated, and every invariant
   concern with file and line. Do not fix anything.
```

## Sources

- Claude Code, run agents in parallel: https://code.claude.com/docs/en/agents
- Claude Code, worktrees: https://code.claude.com/docs/en/worktrees
- Claude Code, hooks reference: https://code.claude.com/docs/en/hooks
- Claude Code, subagents: https://code.claude.com/docs/en/sub-agents
- Claude Code, cloud environments (installed tools including Docker, setup scripts, environment cache, allowed domains): https://code.claude.com/docs/en/cloud-environments
- Amazon EKS pricing: https://aws.amazon.com/eks/pricing/
- Toxiproxy: https://github.com/Shopify/toxiproxy
- WorkAgent repository (handoff and contract patterns): https://github.com/alindebergASL/Workagent
