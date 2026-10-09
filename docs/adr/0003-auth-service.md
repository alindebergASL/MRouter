# 0003. Auth service for people identity

- Status: Proposed
- Date: 2026-10-09
- Deciders: Andrew Lindeberg
- Guarantees affected: none changed. Referenced: G5, A1 (every console action available by
  API), A2 ("a revoked device makes no successful upstream attempt more than 15 minutes after
  revocation"; §4 extends revocation to users, so the control plane must learn of a user
  disable promptly), §4 "Devices" (device-authorization enrollment), C1 (the auth service is
  outside the provider-credential path).

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
- **Lifecycle.** When a user is disabled, deleted, or deprovisioned by SCIM, the control plane
  must learn of it promptly so that the user's devices are revoked and A2's 15-minute bound
  holds for the user as well as the device. Because A1 routes every admin operation through
  our API, and the SCIM endpoint is ours (below), the control plane is the source of those
  changes in the normal case. An event or webhook from the auth service (R14) covers changes
  made behind our back, and relay-token issuance re-checks the user's state in the auth service
  so that even a change made there directly takes effect within one 15-minute token lifetime.
- **C1.** The auth service never holds provider credentials, federation tokens, or data keys.
  It authenticates people and nothing else.

### Requirements

Keys R1 to R14 are used in every table below. All verdicts are yes / partial / no, where "yes"
means the vendor's own documentation says the feature exists today, "partial" means it exists
with a material limitation (paid tier, preview, add-on, or must be built on top in a clean way),
and "no" means absent or roadmap. Every cell carries a footnote with the source and the date it
was checked. Everything was checked on 2026-10-09.

| Key | Requirement | Weight |
|---|---|---|
| R1 | Built-in sign-in with TOTP MFA | 2 |
| R2 | Passkeys (WebAuthn) for end users, as MFA or passwordless | 2 |
| R3 | Per-customer-org OIDC SSO connections | 2 |
| R4 | Per-customer-org SAML SSO connections | 2 |
| R5 | Inbound SCIM provisioning per customer org | 2 |
| R6 | Orgs first-class; our RBAC stays in our database | 3 |
| R7 | OAuth device-authorization grant (RFC 8628), or a clean way to run it on top | 2 |
| R8 | OIDC discovery and JWKS so FastAPI validates tokens with a stock library | 1 |
| R9 | Next.js integration (official SDK, or plain OIDC with a generic library) | 1 |
| R10 | Admin API covers every console operation (A1), plus self-service SSO/SCIM setup for customer admins | 3 |
| R11 | Runs in AWS us-east-1 | 1 |
| R12 | Self-hostable container (§9; customer-hosted deployments later) | 2 |
| R13 | Machine-to-machine access (client credentials) for customers automating the admin API | 1 |
| R14 | User lifecycle events reach the control plane fast enough for A2 | 2 |
| Ops | Operational burden for one person | 3 |
| Cost | Monthly cost at 1,000 MAU with 20 customer SSO connections | 2 |
| Lock | Lock-in and migration path | 2 |
| Sec | Security track record, last 24 months | 2 |
| Info | SOC 2 Type II or ISO 27001 for the hosted offering | unscored |

Cells score yes = 2, partial = 1, no = 0, multiplied by the weight. Maximum:
R1–R5 5 × 2 × 2 = 20; R6 3 × 2 = 6; R7 2 × 2 = 4; R8, R9, R11 3 × 1 × 2 = 6; R10 3 × 2 = 6;
R12 2 × 2 = 4; R13 1 × 2 = 2; R14 2 × 2 = 4; Ops 3 × 2 = 6; Cost 2 × 2 = 4; Lock 2 × 2 = 4;
Sec 2 × 2 = 4. Total 20 + 6 + 4 + 6 + 6 + 4 + 2 + 4 + 6 + 4 + 4 + 4 = **70**.

Rubrics for the four qualitative rows:

- **Ops.** 2: hosted, nothing to run. 1: one or two stateless containers plus Postgres, with
  frequent releases and no long-term-support line; or hosted but we must build a substantial
  piece ourselves (more than a device-flow server, which the control plane can run for any
  candidate; Cognito's SCIM server and per-customer IdP provisioning count, Stytch's device
  flow alone does not). 0: three or more services to run, or two products to wire together.
- **Cost.** 2: published, and at most $500 per month at the reference configuration (1,000 MAU,
  20 customer SSO connections). 1: published and above $500, or not published. 0: a feature we
  require exists only in a tier whose price is not published. Self-hosted options are priced on
  the AWS footprint stated under "Pricing".
- **Lock.** 2: open source, the data lives in our own database, tokens are standard OIDC.
  1: standard OIDC but export of hashes or MFA secrets is restricted, undocumented, or the server
  is closed source. 0: password hashes and MFA secrets cannot leave.
- **Sec.** 2: no advisory of CVSS 7.5 or higher against the server or hosted service in the
  window. 1: high-severity advisories, or critical ones only in client SDKs. 0: one or more
  critical (CVSS 9 or higher) authentication-bypass advisories against the server itself.

### Candidates

Named in the session brief: Keycloak, ZITADEL, Ory (Kratos and Hydra self-hosted, and Ory Network),
authentik, WorkOS, Auth0 (Okta Customer Identity Cloud), Amazon Cognito, FusionAuth. Added
because they fit the shape of the problem: Logto, SuperTokens, Stytch (B2B), Clerk.

Considered but not scored, one line each (sources in the appendix):

- **Descope**: covers every requirement except R12, including RFC 8628, but is hosted only with
  no self-hostable container; Pro "starts at $249/mo billed annually".
- **Frontegg**: hosted only; passkeys and device flow not documented on any fetched page.
- **Hanko**: AGPL-3.0 backend with passkeys and TOTP, but no SCIM and organizations are still
  marked in progress.
- **Microsoft Entra External ID**: Microsoft-cloud only; external-tenant MFA lists email OTP, SMS
  and passkeys but no TOTP.
- **PropelAuth**: the complete product is hosted only; its self-hostable sidecar lacks the org
  model and MFA.
- **SSOReady** (MIT) and **BoxyHQ Jackson, now Ory Polis** (Apache-2.0): SAML and SCIM add-ons with
  no user store, MFA, orgs or device flow. Either could sit beside a self-hosted server if the
  chosen server lacked SSO; the recommendation below does not need them.

### Requirement matrix

Hosted products are scored on their hosted offering; open-source products on self-hosting. Where a
vendor has both and they differ, the cell says which. Footnote numbers refer to the Sources list.

| Key | Keycloak | ZITADEL | Ory | authentik | WorkOS | Auth0 | Cognito | FusionAuth | Logto | SuperTokens | Stytch | Clerk |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| R1 TOTP | yes [1] | yes [11] | yes [21] | yes [31] | yes [41] | yes [51] | yes [61] | yes [71] | yes [81] | partial: MFA is a paid add-on [91] | yes [101] | partial: Pro plan or above [111] |
| R2 passkeys | yes, "supported" since 26.4 [2] | yes [12] | yes [21] | yes [32] | yes, hosted UI only [42] | yes [52] | partial: Essentials plan, USER_AUTH flow only [62] | partial: free "Licensed Community" key; cross-platform authenticators Enterprise [72] | yes [82] | yes [92] | no: B2B MFA lists SMS and TOTP only [101] | partial: Pro plan or above [112] |
| R3 OIDC SSO per org | yes [3] | yes [13] | partial: Ory Network Growth (max 3 orgs) or Enterprise; not in open source [22] | partial: sources are global, no org boundary [33] | yes [43] | yes [53] | partial: IdP objects per pool, no org [63] | yes, per tenant [73] | yes [83] | partial: per-tenant OIDC, paid multi-tenancy [93] | yes [102] | partial: Pro plan plus $100/mo B2B add-on to link to an org [113] |
| R4 SAML SSO per org | yes [3] | yes [13] | partial: Enterprise only [22] | partial [33] | yes [43] | yes [53] | partial [64] | yes [73] | yes [83] | partial: paid, Core 12+, Node/Python SDKs [94] | yes [102] | partial [113] |
| R5 SCIM per org | partial: built-in SCIM is realm-scoped (experimental in April 2026, preview in 26.7, "promoted from preview to supported" in 26.8 [2]); per-org only via Phase Two extension, experimental, Elastic License 2 [4] | partial: Preview, per-org URL, Users only, no Groups [14] | partial: Enterprise, beta [23] | partial: per-source endpoint, tenant-wide matching [34] | yes [44] | yes, included on all plans [54] | no [65] | partial: Enterprise plan only [74] | no, open request [84] | no [95] | yes [103] | partial: no API to enable it; $75/connection from 2027 [114] |
| R6 orgs; our RBAC | yes / yes [3] | yes / yes [15] | yes / yes on Network; no orgs in open source [22] | no: no org object; tenancy is alpha, schema-per-tenant [35] | yes / partial: every membership carries a WorkOS role [45] | yes / yes [55] | partial: patterns, no object / yes [66] | partial: tenants, not orgs / yes [75] | yes / yes [85] | partial: tenants are separate user pools / yes [96] | yes / partial: Stytch RBAC is canonical for SCIM group mapping and portal [104] | yes / partial: every member carries a Clerk org role [115] |
| R7 device flow | yes, native [5] | yes, native [16] | yes, native in Hydra 25.4+ [24] | partial: native endpoints, but "authentik does not include a default flow for this use case" [36] | yes, native ("CLI Auth") [46] | partial: native, but "does not natively support the Organizations feature" [56] | partial: not native; AWS documents a build-on-top pattern [67] | yes, native [76] | yes, native since 1.38.0 [86] | partial: build on top [97] | partial: build on top [105] | partial: native, beta, per-app enablement [116] |
| R8 discovery + JWKS | yes [5] | yes [17] | partial: yes for Hydra; Kratos sessions need the session-to-JWT tokenizer [25] | yes [37] | yes [47] | yes [57] | yes [68] | yes [77] | yes [87] | partial: session JWTs are not OIDC tokens; OIDC needs paid Unified Login [98] | partial: JWKS endpoint needs project Basic auth [106] | yes [117] |
| R9 Next.js | partial: generic OIDC (Auth.js provider) [6] | partial: generic (next-auth example) [18] | yes: @ory/nextjs [26] | partial: generic (Auth.js provider) [38] | yes: @workos-inc/authkit-nextjs [48] | yes: @auth0/nextjs-auth0 [58] | partial: Amplify adapter, server-side "experimental" [69] | partial: generic OIDC [78] | yes: @logto/next [88] | yes [99] | yes: @stytch/nextjs [107] | yes: @clerk/nextjs [118] |
| R10 admin API + self-service | partial: all operations by API (verified in the OpenAPI document); no customer-facing SSO/SCIM portal; per-org SCIM gap [7] | partial: all by API, v1 management API deprecated in favour of v2; customers can use ZITADEL's console per org, not embeddable [19] | partial: Console API plus hosted onboarding portal links; SAML and SCIM Enterprise only [27] | partial: full OpenAPI; no orgs; no customer portal [39] | partial: all by API except creating a SCIM directory (portal or dashboard only); no user-level deactivate; hosted Admin Portal [49] | yes: all by API; Self-Service SSO with SCIM token generation included on Free, Essentials, Professional [59] | partial: all by API; no SCIM, no portal, no org object [70] | partial: all by API; no customer portal; SCIM Enterprise [79] | partial: all by API; no SCIM; no portal [89] | partial: no SCIM, no invite, no deactivate (delete only), no portal [100] | yes: full API plus AdminPortalSSO and AdminPortalSCIM components [108] | partial: no API to enable Directory Sync; self-serve SSO inside OrganizationProfile [119] |
| R11 us-east-1 | yes: self-host on ECS or EKS, Aurora PostgreSQL tested [8] | yes self-host; Cloud is GCP us-central1 [20] | partial: region named only by example; provider not named [28] | yes: official ECS CloudFormation and Helm [40] | partial: AWS subprocessor; region not published [50] | partial: US region on AWS; exact region not published [60] | yes: cognito-idp.us-east-1 [68] | yes self-host; Cloud regions page not found [80] | yes self-host; Cloud is Azure (West US Arizona) [90] | yes: self-host; managed "US East (N. Virginia)" [99] | partial: "exclusively out of U.S. servers", no region selection [109] | no: Google Cloud, "does not offer regional data residency or region selection" [120] |
| R12 container | yes: quay.io/keycloak/keycloak, Apache-2.0 [9] | yes: ghcr.io/zitadel/zitadel, AGPL-3.0 [20] | partial: docker.io/oryd/kratos and oryd/hydra, Apache-2.0, but orgs, SAML, SCIM are Network or Enterprise only [29] | yes: ghcr.io/goauthentik/server, MIT [40] | no [50] | no [60] | no [68] | yes: docker.io/fusionauth/fusionauth-app, proprietary [80] | yes: ghcr.io/logto-io/logto, MPL-2.0 [90] | partial: image is Apache-2.0 but MFA, multi-tenancy, SAML need a license key; M2M managed-only [99] | no [109] | no [120] |
| R13 M2M | yes [1] | yes [14] | yes [30] | yes [40] | yes, org-scoped [49] | partial: client credentials on all plans; org-scoped tokens Professional and above [60] | yes [68] | partial: Starter plan and above [80] | yes [88] | partial: managed service only [99] | yes [108] | partial: "does not yet support" client credentials; proprietary M2M tokens [120] |
| R14 lifecycle events | partial: no built-in webhook; Shared Signals Framework transmitter (experimental) emits account-disabled; Event Listener SPI; Phase Two webhooks [10] | partial: Actions v2 HTTP targets on events; retry and latency not documented [19] | partial: live event streams Enterprise only, "less than 5 seconds" typical [30] | partial: model_deleted/updated to webhook transport; no retry doc [40] | partial: webhooks retried "up to 6 times, with exponential backoff over 3 days"; no latency figure [49] | partial: Log Streams only, Essentials plan and above, at least once, no latency figure [60] | partial: no trigger on disable; CloudTrail to EventBridge [70] | yes: transactional webhooks for user.deactivate and user.delete, three retries [80] | partial: webhooks for User.Deleted and suspension; no retry doc [89] | partial: no webhooks; our backend is the only caller [100] | partial: Svix webhooks, Svix retry schedule, no latency figure [109] | partial: Svix webhooks, "not guaranteed to be delivered immediately or at all" [120] |
| Info compliance | Phase Two hosting: SOC 2 Type II and ISO 27001 [9] | SOC 2 Type II, ISO/IEC 27001:2022 [20] | SOC 2 Type 2, ISO 27001:2022 [30] | no hosted product | SOC 2 Type 2; ISO 27001 not claimed [50] | SOC 2 Type 2, ISO 27001/27017/27018 [60] | AWS SOC 1/2/3, ISO 27001 [68] | SOC 2 Type 2, ISO 27001 [80] | SOC 2 Type II; ISO not mentioned [90] | SOC 2, type not stated [99] | SOC 2, ISO 27001 via Twilio trust center [109] | SOC 2 Type 2; "not ISO 27001 certified" [120] |

### Pricing

Hosted prices are copied from each vendor's pricing page on 2026-10-09. "Not published" means the
page gives no figure. Self-hosted options are priced on one stated AWS us-east-1 footprint so the
row compares like with like.

**Self-hosted footprint F.** One Fargate task, Linux/ARM, 2 vCPU and 4 GB (Keycloak's documented
minimum for small production is 2 GB), plus one Amazon RDS for PostgreSQL db.t4g.medium Multi-AZ
with 20 GB gp3, plus one Application Load Balancer at 1 LCU, all on-demand for 730 hours. AWS
prices were taken from the AWS Price List API files for us-east-1 (ECS file dated 2026-09-11, RDS
2026-10-06, ELB 2026-09-11) because the aws.amazon.com rate tables render client-side; the figures
that do appear as text on those pages agree [130]:

| Item | Rate | Monthly |
|---|---|---|
| Fargate ARM vCPU | $0.03238 per vCPU-hour × 2 × 730 | $47.27 |
| Fargate ARM memory | $0.00356 per GB-hour × 4 × 730 | $10.40 |
| RDS db.t4g.medium Multi-AZ | $0.129 per hour × 730 | $94.17 |
| RDS gp3 Multi-AZ | $0.23 per GB-month × 20 | $4.60 |
| ALB | $0.0225 per hour × 730 + $0.008 per LCU-hour × 730 | $22.27 |
| **F total** | | **$178.71** |

Excluded: SES at "$0.10 / 1,000 emails", backups, data transfer, NAT, and our engineering time.
The same footprint serves 10, 100, and 1,000 MAU. Single-AZ db.t4g.small drops F to $105.60; two
tasks and db.m7g.large Multi-AZ raise it to $388.22 [130].

| Vendor | Hosted plan used | 10 MAU | 100 MAU | 1,000 MAU | Per SSO connection | 1,000 MAU + 20 SSO connections |
|---|---|---|---|---|---|---|
| Keycloak (self-host) | none; Apache-2.0 | F $178.71 | $178.71 | $178.71 | $0 | **$178.71** |
| Keycloak via Phase Two hosting [9] | Starter "$149/month", "sized for 5K active users", 95% uptime target, no SLA | $149 | $149 | $149 | "Unlimited SSO connections" | $149 |
| Keycloak via Red Hat build | "not available for purchase as a separate and distinct product" [9] | not published | not published | not published | not published | not published |
| ZITADEL (self-host) | none; AGPL-3.0 | $178.71 | $178.71 | $178.71 | $0 | **$178.71** |
| ZITADEL Cloud [20] | Free "US$0/Month", "100 Daily Active Users", "3 identity providers"; Pro "US$100/Month", "25'000 Daily Active Users per month included", "3 identity providers included" | $0 | $0 | $100 (billing is summed daily active users, not MAU; 1,000 MAU is $100 only if the month's DAU sum stays under 25,000) | Pro calculator: "External IDP Connections" $100 above 3 included; unit not stated | not computable: unit for the 17 extra connections not published |
| Ory Network [30] | Production "$770 / year" with "$21.00" monthly credit, "$0.14 / aDAU / month"; Growth "$9,350 / year", OIDC SSO only, max 3 orgs; SAML and SCIM Enterprise | $64.17 | $64.17 | $64.17 to $183.17 depending on daily activity | not priced separately | not published (SAML and SCIM are Enterprise) |
| authentik (self-host) [40] | Open Source "Free"; Enterprise "$0.02 / external user / month" billed annually | F plus one more task for the worker: $236.38 | $236.38 | $236.38 (+$20 Enterprise if wanted) | $0 | **$236.38** |
| WorkOS [50] | AuthKit "First 1M MAUs" free, then "$2,500/mo" per additional 1M; SSO per connection 1–15 "$125/ea", 16–30 "$100/ea", 31–50 "$80/ea", 51–100 "$65/ea"; Directory Sync same tiers; "Only production environments are billed" | $0 | $0 | $0 | $125 (SSO), $125 (SCIM) | **$2,375** for SSO; another $2,375 if the same 20 orgs use SCIM |
| Auth0 [60] | Free "$0", "Up to 25,000 monthly active users", 1 enterprise connection, 5 organizations; B2B Essentials "$150/month" at 500 MAU, "$300/month" at 1,000 MAU, 3 connections included, "$100/month ($1,100/year) per additional connection (max 30 total)"; B2B Professional "$800/month" at 500 and 1,000 MAU, 5 included | $150 (Essentials) | $150 | $300 | $100 | **$2,000** (Essentials: $300 + 17 × $100); cap of 30 connections on self-serve plans |
| Amazon Cognito [68] | Essentials: "10,000 MAUs per month" free, then $0.015 per MAU; SAML/OIDC federated users 50 free then $0.015 per MAU; Plus $0.020 per MAU, no free tier; M2M "$0.00225 per token request (US East, N. Virginia)" | $0 | $0 (or $0.75 if all federated) | $0 (or $14.25 if all federated) | $0 | **$0 to $14.25**, plus our own SCIM and device-flow services |
| FusionAuth (self-host) [80] | Community "Free"; Starter "Starting at: $162 /mo"; Essentials and Enterprise "Starting at: $2,970 /mo", "Billed annually"; SCIM is Enterprise only | $178.71 | $178.71 | $178.71 | $0 ("Identity providers: unlimited") | **$178.71** without SCIM; with SCIM, Enterprise "starting at $2,970" plus F |
| Logto (self-host) [90] | none; MPL-2.0. Logto Cloud: Free "Up to 50,000 MAU"; Pro "From $24/mo" plus add-ons: Organizations $48, MFA $48, Enterprise SSO "$48 per connector"; billing by tokens issued | $178.71 | $178.71 | $178.71 | $0 self-host; $48 Cloud | **$178.71** self-host, no SCIM; Cloud $24 + $48 + $48 + 20 × $48 = $1,080 plus token overage |
| SuperTokens (self-host or managed) [99] | Managed "$0.02 per MAU", "Free under 5K monthly active users"; MFA "$0.01 / MAU" with "Minimum Billing of $100 / month"; multi-tenancy "See pricing", SAML listed without a price | $100 (MFA minimum) | $100 | $100 | not published | not published (multi-tenancy and SAML prices absent) |
| Stytch B2B [109] | "Starting at: $0 /Month", "10,000 monthly active users and AI agents", "Unlimited Organizations", "5 SSO or SCIM Connections", then "$125/connection"; additional MAU "Volume discounts" | $0 | $0 | $0 | $125 (SSO and SCIM share the five included) | **$1,875** (15 × $125); $4,375 if the same 20 orgs also use SCIM |
| Clerk [120] | Hobby "Free", no MFA, passkeys, SSO; Pro "$25/mo ($20/mo billed annually)", "1 SSO connection included (Additional $75/mo each)", "1 Directory Sync connection included (Additional $75/mo each from January 1, 2027)"; B2B "Enhanced add-on — $100/mo" needed to link connections to organizations | $125 | $125 | $125 | $75 | **$1,550** ($25 + $100 + 19 × $75); SCIM adds 19 × $75 from 2027 |

### License, self-hosting, and operational burden for one person

| Vendor | License | Image | What one person runs | Release cadence |
|---|---|---|---|---|
| Keycloak | Apache-2.0; no change in three years. Phase Two's org and SCIM extensions moved from AGPL-3.0 to Elastic License 2 and are not used in the recommendation [9] | quay.io/keycloak/keycloak | One JVM container (embedded Infinispan, sessions in the database, cluster discovery by jdbc-ping) plus PostgreSQL 14–18 or Aurora PostgreSQL 15–17; SMTP for invitations. "2 GB for smaller production deployments" [8] | "4 minor releases every year, and a major release every 2-3 years"; no LTS upstream [8] |
| ZITADEL | AGPL-3.0 for the server since v3, previously Apache-2.0; APIs, SDKs, Helm stay Apache-2.0 [20] | ghcr.io/zitadel/zitadel | One stateless Go binary plus PostgreSQL; an init job once and a setup job per upgrade; projections replay after upgrades [20] | No LTS; "stable" tag retired, pin versions; 3.x reached end of life within months [20] |
| Ory | Apache-2.0 (Kratos, Hydra); Ory Enterprise License for orgs and multi-tenancy self-hosted [29] | docker.io/oryd/kratos, oryd/hydra | Kratos, Hydra, a login/consent UI you write, courier, PostgreSQL: four or more containers; orgs, SAML and SCIM only on Network or OEL [29] | Roughly one to two minors a year on CalVer [29] |
| authentik | MIT core; proprietary enterprise directory [40] | ghcr.io/goauthentik/server | Server, worker, PostgreSQL 16; 2 CPU and 2 GB host; outposts version-locked [40] | Three feature releases a year, cannot skip versions, no downgrade [40] |
| WorkOS | Proprietary SaaS | none | Webhook receiver, events reconciliation job, custom domain for passkeys [49] | n/a |
| Auth0 | Proprietary SaaS | none | Log Stream receiver, Actions code, self-service profile issuance [60] | n/a |
| Cognito | Proprietary AWS service | none | Our own SCIM server, our own device-flow service, per-customer IdP and app-client provisioning, pre-token Lambda, CloudTrail to EventBridge [70] | n/a |
| FusionAuth | Proprietary closed source; client libraries Apache-2.0 [80] | docker.io/fusionauth/fusionauth-app | One Java container, PostgreSQL 14+, optional Elasticsearch/OpenSearch; 512 MB to 1 GB per node; Silent Mode locks the database during upgrade [80] | 17 releases in 12 months; no LTS; stay within the last three minors [80] |
| Logto | MPL-2.0 [90] | ghcr.io/logto-io/logto | One Node container plus PostgreSQL; database alteration run from a single instance [90] | Monthly minors; only 1.x supported [90] |
| SuperTokens | Apache-2.0 core; proprietary `ee/` [99] | registry.supertokens.io/supertokens/supertokens-postgresql | One Java container plus PostgreSQL; your backend SDK sends email [99] | Frequent; parallel patch lines [99] |
| Stytch | Proprietary SaaS | none | Webhook receiver, our own device-flow service, mapping Stytch RBAC [109] | n/a |
| Clerk | Proprietary SaaS | none | Webhook receiver, SDK upgrades (several SDK CVEs), middleware [120] | n/a |

### Lock-in and migration

| Vendor | Password hashes out | TOTP secrets out | Passkeys out | Tokens | What breaks on leaving |
|---|---|---|---|---|---|
| Keycloak | Our database; `kc.sh export` writes realm and user JSON, credential contents not stated on the import/export page [8] | our database | our database (public keys; RP-ID bound) | standard OIDC | realm-specific claims, org claim mapper |
| ZITADEL | Export API `with_passwords: true` returns hashed passwords; `with_otp: true` returns OTP secrets [20] | yes | "not possible to migrate passkeys" [20] | standard OIDC | ZITADEL role claims, Actions targets, SCIM URLs |
| Ory | Import formats documented; `include_credential` on list identities, whether it returns hashes unverified [30] | unverified | importable | Hydra standard; Kratos sessions via tokenizer | flow-based UI, identity schema, Network org config |
| authentik | No export documented; self-hosted database access [40] | same | same | standard OIDC | flows, stages, property mappings |
| WorkOS | No documented export of hashes, TOTP or passkeys [49] | no | no | standard OIDC | every customer SSO and SCIM connection, Admin Portal links, passkeys |
| Auth0 | Support ticket, PGP-encrypted, "not available for our Free subscription tier", "Not all requests qualify" [60] | same ticket | not mentioned | standard OIDC | connections, self-service profiles, Actions, passkeys |
| Cognito | Cannot be exported (no API; AWS re:Post answers, no vendor statement found) [70] | no | no | standard OIDC | all passwords, TOTP, passkeys, IdP objects |
| FusionAuth | In our database; not returned by the API; importable with scheme and salt [80] | in our database | public keys only | standard OIDC | tenant and application config, SCIM entities |
| Logto | In our database (Argon2); no export API [90] | in our database | in our database | OIDC when a resource is requested, otherwise opaque | org model, org tokens, webhooks |
| SuperTokens | In our database; bulk import API [99] | in our database | in our database | session JWTs; OIDC only with paid Unified Login | SDK middleware in every backend |
| Stytch | "contact us at support@stytch.com" for hashed password export [109] | not covered | on request | session and M2M JWTs | Admin Portal components, Stytch RBAC, SCIM endpoints |
| Clerk | Dashboard CSV "includes their hashed passwords" [120] | not documented | not documented | Clerk session JWTs | OrganizationProfile UI, org roles, SCIM endpoints, middleware |

### Security track record, 2024-10-09 to 2026-10-09

| Vendor | Advisories | Worst | Bounty | Notes |
|---|---|---|---|---|
| Keycloak | 42 published GitHub advisories, about 20 High, no Critical | CVE-2026-11800, 8.1, "Authentication bypass via JWT algorithm confusion" | "There is currently no active bug bounty." | Batch of seven on 2026-08-06 including SAML broker signature validation [10] |
| ZITADEL | 57, including 7 Critical | GHSA-g8gj-gq47-xgf4, 9.8, unauthenticated account takeover through external IdP linking (2026-09-04); GHSA-45f2-5q3r-xgg6, unauthenticated passkey enrollment takeover (2026-07-29) | vulnerability portal; bounty unverified | Many advisories carry no CVE [20] |
| Ory | 2 (Kratos CVE-2026-33503, Hydra CVE-2026-33504) | 7.2, SQL injection via the default pagination secret | not found | Set `secrets.pagination` [30] |
| authentik | 31, including 3 Critical | CVE-2026-49448, 9.8, "SourceStage bypass via empty POST" | "we do not currently offer monetary bounties" | Recurring SAML-source assertion-validation issues in Feb, May, Jul, Sep 2026 [40] |
| WorkOS | 7 CVEs, six in SDKs | CVE-2025-64762, 8.0, authkit-nextjs session cookies cacheable by CDNs; CVE-2025-23017, 6.0, hosted AuthKit MFA bypass by enrolling a new factor | pays for high and critical findings; no public platform | [50] |
| Auth0 | about 25 Auth0-owned advisories, none against the hosted service | laravel-auth0 deserialization, 9.3 | Bugcrowd, "$100 – $50,000 per vulnerability" | Okta support-system breach was October 2023, outside the window [60] |
| Cognito | 0 | n/a | AWS VDP on HackerOne | [70] |
| FusionAuth | 0 in window | n/a | Bugcrowd-hosted form, pays bounties | Vendor may fix without filing CVEs, unverified [80] |
| Logto | 9 CVEs, all 2026, four Critical | CVE-2026-15611, 9.1, SSO email account-linking takeover; CVE-2026-15616, MFA not enforced during SSO | none | [90] |
| SuperTokens | 1 | CVE-2026-37171, 5.9, tenant separation | monetary reward "may" be offered | [99] |
| Stytch | 0 found | n/a | HackerOne via Twilio | Availability incidents only [109] |
| Clerk | 4 SDK CVEs | CVE-2026-41248, 9.1, `createRouteMatcher` middleware bypass in @clerk/nextjs | "does not run a paid bug-bounty program" | Six availability incidents in 2025–2026, one multi-day [120] |

### Scored table

Raw cells: 2 = yes, 1 = partial, 0 = no. Weighted total out of 70.

| Candidate | R1 | R2 | R3 | R4 | R5 | R6 | R7 | R8 | R9 | R10 | R11 | R12 | R13 | R14 | Ops | Cost | Lock | Sec | **Total** |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Keycloak | 2 | 2 | 2 | 2 | 1 | 2 | 2 | 2 | 1 | 1 | 2 | 2 | 2 | 1 | 1 | 2 | 2 | 1 | **57** |
| ZITADEL | 2 | 2 | 2 | 2 | 1 | 2 | 2 | 2 | 1 | 1 | 2 | 2 | 2 | 1 | 1 | 2 | 2 | 0 | **55** |
| Auth0 | 2 | 2 | 2 | 2 | 2 | 2 | 1 | 2 | 2 | 2 | 1 | 0 | 1 | 1 | 2 | 1 | 1 | 1 | **54** |
| Logto | 2 | 2 | 2 | 2 | 0 | 2 | 2 | 2 | 2 | 1 | 2 | 2 | 2 | 1 | 1 | 2 | 2 | 0 | **54** |
| WorkOS | 2 | 2 | 2 | 2 | 2 | 1 | 2 | 2 | 2 | 1 | 1 | 0 | 2 | 1 | 2 | 1 | 1 | 1 | **51** |
| FusionAuth | 2 | 1 | 2 | 2 | 1 | 1 | 2 | 2 | 1 | 1 | 2 | 2 | 1 | 2 | 1 | 1 | 1 | 2 | **51** |
| Ory Network | 2 | 2 | 1 | 1 | 1 | 2 | 2 | 1 | 2 | 1 | 1 | 1 | 2 | 1 | 2 | 0 | 1 | 2 | **49** |
| Stytch B2B | 2 | 0 | 2 | 2 | 2 | 1 | 1 | 1 | 2 | 2 | 1 | 0 | 2 | 1 | 2 | 1 | 1 | 2 | **49** |
| authentik | 2 | 2 | 1 | 1 | 1 | 0 | 1 | 2 | 1 | 1 | 2 | 2 | 2 | 1 | 1 | 2 | 2 | 0 | **43** |
| SuperTokens | 1 | 2 | 1 | 1 | 0 | 1 | 1 | 1 | 2 | 1 | 2 | 1 | 1 | 1 | 1 | 0 | 2 | 2 | **39** |
| Amazon Cognito | 2 | 1 | 1 | 1 | 0 | 1 | 1 | 2 | 1 | 1 | 2 | 0 | 2 | 1 | 1 | 2 | 0 | 2 | **38** |
| Clerk | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 2 | 2 | 1 | 0 | 0 | 1 | 1 | 2 | 1 | 1 | 1 | **37** |

Worked example, Keycloak: R1–R5 (2 + 2 + 2 + 2 + 1) × 2 = 18; R6 2 × 3 = 6; R7 2 × 2 = 4;
R8 2 + R9 1 + R11 2 = 5; R10 1 × 3 = 3; R12 2 × 2 = 4; R13 2; R14 1 × 2 = 2; Ops 1 × 3 = 3;
Cost 2 × 2 = 4; Lock 2 × 2 = 4; Sec 1 × 2 = 2. Total 57.

Two observations the numbers hide. First, no candidate gives us per-organization SCIM that we
would be comfortable handing to a customer's IdP *and* keeps our RBAC in our database without a
mapping layer; since org → workspace → team → user lives in our Postgres regardless, the natural
home for the SCIM endpoint is our control plane, which calls the auth service to create, disable,
and delete the identity. That makes R5 less decisive than its weight suggests for every candidate.
Second, because A1 forces every admin operation through our API, our API is the source of truth
for user disable and delete; the auth service's webhook (R14) is a safety net for changes made
behind our back, not the primary path.

## Decision

**Recommendation: Keycloak, self-hosted**, as a container on ECS Fargate in us-east-1 against its
own Amazon RDS for PostgreSQL instance, one realm per deployment with Keycloak Organizations as
the customer-org primitive.

Why Keycloak over the field:

- Three self-hostable candidates are "yes" on every hard requirement we cannot build around
  (TOTP, passkeys, per-org OIDC and SAML, first-class orgs that leave RBAC to us, native RFC
  8628, standard discovery and JWKS, client credentials) and ship as a container: Keycloak,
  ZITADEL, and Logto [1][2][3][5][9][11][16][20][81][86][90]. Keycloak separates from the other
  two on license (Apache-2.0 against AGPL-3.0 and MPL-2.0, which matters for the customer-hosted
  case) and on security record (next bullet). Every hosted leader fails R12, and §9 makes
  containers the default.
- Its two gaps are ones we were going to build anyway. The realm-scoped SCIM API cannot be given
  to N customers, so the control plane serves SCIM per org and drives Keycloak's admin API, whose
  organization, invitation, user, and credential endpoints we verified in the published OpenAPI
  document [7]. The missing customer-facing SSO portal is a page in our console over the same
  admin API, which A1 requires of us in any case.
- Its security record is the best among the self-hostable, feature-complete options: no critical
  advisory in the window, against ZITADEL's seven, Logto's four, and authentik's three [10][20][40][90].
- Cost is the footprint, with no per-connection fee. At 20 customer SSO connections the hosted
  leaders cost $1,550 to $2,375 a month before SCIM; Keycloak's footprint is $178.71 [50][60][109][120][130].
- Lock-in is lowest: the data is in our database, tokens are standard OIDC, and the license allows
  anything.

What we accept by choosing it: a JVM service with four minor releases a year and no upstream LTS,
which is real work for one person (Ops scored 1, not 2); no webhook out of the box, mitigated by
A1 as above and by the experimental Shared Signals Framework transmitter if we want a safety net;
and that the console's sign-in will be plain OIDC through Auth.js rather than a vendor SDK.

**Runner-up: Auth0 (Okta Customer Identity Cloud), B2B Essentials.** It is the most complete
hosted offering: SCIM and Self-Service SSO on every plan including Free, first-class
organizations, passkeys, a full management API, and the strongest disclosure program [52][54][59].
We would choose it if we decided not to operate an auth service at all. Its costs are the device
flow's lack of organization support (we would run enrollment through our own device-flow server,
which is clean), no self-hosting, hash export only by support ticket, and $100 per connection with
a cap of 30 on self-serve plans [56][60].

ZITADEL scores second but is not the runner-up: it would be the lighter self-hosted choice (single
Go binary, SCIM preview per org, native device flow) if its advisory record were not 57 published
advisories in two years including unauthenticated account takeover through IdP linking and through
passkey enrollment [20]. It stays on the watch list below.

## Consequences

For Lane D's next tasks:

- **Task 2 (FastAPI skeleton).** Validate Keycloak access tokens with the realm's discovery
  document and JWKS using a stock Python JWT library; map `sub` to our user row and the Keycloak
  organization to our org row; store nothing from the token except identifiers. The control plane
  owns the SCIM 2.0 endpoint per org (per-org bearer tokens, stored hashed) and translates to
  Keycloak admin-API calls plus our own team and role tables.
- **Task 3 (device enrollment).** The sidecar runs Keycloak's device-authorization grant against a
  public client configured to issue no refresh or offline tokens; the control plane accepts the
  resulting access token once, binds device to user, issues the mTLS certificate from our CA,
  and discards the token. Revocation stays ours: disabling a user through our API disables the
  Keycloak user and revokes the device, which is what A2 measures. Each 15-minute relay-token
  issuance also checks that the user is still enabled in Keycloak, so a disable made directly in
  Keycloak's admin console (break-glass only; day-to-day administration goes through our API)
  takes effect within one token lifetime.
- **Console.** Plain OIDC through Auth.js; self-service SSO setup is a console page calling our API,
  which calls Keycloak's organization identity-provider endpoints.
- **Dev stack.** Add Keycloak to `deploy/compose/dev.yaml` from `quay.io/keycloak/keycloak`, pinned
  by digest and pre-pulled by `scripts/cloud-setup.sh` so `make check-pins` passes; a second
  database in the dev Postgres. Never `start-dev` outside development.
- **Production.** A separate RDS instance from the ledger (the ledger's transaction profile must
  not share a database with a session store), Multi-AZ, with Keycloak on its own hostname behind
  its own load balancer (the one priced in footprint F). Its bootstrap admin credential, database
  password, and the control plane's admin-API client secret live in AWS Secrets Manager and are
  read at run time through the task's IAM role, never baked into an image (§9, D2). D2's image
  checks are written for images we publish; the Keycloak image is third-party, pinned by digest,
  and run as a non-root user with a read-only root filesystem where its documentation allows.
  Adding the auth service to §9's component table is a spec change for a later PR. Pin the
  Keycloak version and upgrade on a monthly cadence with the release notes read first.
- **Do not** adopt Phase Two's extensions: their Elastic License 2 would have to be reviewed for
  the customer-hosted case, and the SCIM they add is experimental; our control plane covers it.

What would change the decision:

1. A critical (CVSS 9 or higher) authentication-bypass advisory against the Keycloak server, or
   two breaking upgrades in a year that cost more than a day each: move to Auth0.
2. Keycloak upstream ships per-organization SCIM or a supported webhook/event sink: the control
   plane's SCIM layer thins, the decision stands.
3. The first customer-hosted deployment (§12) arrives before gate G3: the decision is reinforced;
   evaluate Phase Two's hosted Keycloak ($149 per month, unlimited SSO connections, SOC 2 Type II
   and ISO 27001) for our own cloud so one person operates nothing [9].
4. Operating Keycloak measurably exceeds four hours a month over a quarter: Auth0, with the device
   flow run by the control plane.
5. ZITADEL goes twelve months without a critical advisory and its SCIM leaves preview with Groups:
   reconsider it as the lighter self-hosted option.
6. WorkOS or Auth0 publish a us-east-1 residency commitment and the customer-hosted requirement is
   dropped from the roadmap: re-score with R12 at weight 0, which would put Auth0 first.

## Sources

Checked 2026-10-09 unless noted.

Keycloak
1. Server administration guide, features list and OTP policy: https://www.keycloak.org/docs/latest/server_admin/index.html
2. Release notes 26.4.0 "Passkeys integration (supported)", 26.0.0 "Organizations supported", 26.7.0 and 26.8.0 SCIM and SSF: https://www.keycloak.org/docs/latest/release_notes/index.html
3. Organizations: identity providers linked per organization, domain routing, managed and unmanaged members, invitations (same guide as [1])
4. SCIM as experimental feature (realm scope, `/realms/{realm}/scim/v2`, service-account auth): https://www.keycloak.org/2026/04/scim-as-experimental-feature ; Phase Two per-org SCIM, experimental: https://phasetwo.io/docs/organizations/scim/ ; Phase Two license change to Elastic License v2: https://github.com/p2-inc/keycloak-orgs
5. OIDC layers: device endpoint `/realms/{realm-name}/protocol/openid-connect/auth/device`, discovery, certs: https://www.keycloak.org/securing-apps/oidc-layers
6. Securing apps overview (use ecosystem libraries): https://www.keycloak.org/securing-apps/overview ; Auth.js Keycloak provider: https://next-auth.js.org/providers/keycloak
7. Admin REST API OpenAPI document (organizations, identity-providers, invite-user, users, credentials, execute-actions-email): https://www.keycloak.org/docs-api/latest/rest-api/openapi.json
8. Database support: https://www.keycloak.org/server/db ; caching and jdbc-ping: https://www.keycloak.org/server/caching ; containers and memory: https://www.keycloak.org/server/containers ; release cadence: https://www.keycloak.org/2024/10/release-updates ; import/export: https://www.keycloak.org/server/importExport
9. License: https://github.com/keycloak/keycloak/blob/main/LICENSE.txt ; Red Hat build availability: https://access.redhat.com/articles/7044244 ; Phase Two hosting pricing, SOC 2 Type II and ISO 27001: https://phasetwo.io/pricing/hosting/
10. Security advisories: https://github.com/keycloak/keycloak/security/advisories?state=published ; CVE-2026-11800: https://github.com/keycloak/keycloak/security/advisories/GHSA-j97h-3f8r-mrjr ; security page, no bug bounty: https://www.keycloak.org/security ; Event Listener SPI: https://www.keycloak.org/docs/latest/server_development/index.html ; Phase Two keycloak-events: https://github.com/p2-inc/keycloak-events

ZITADEL
11. Login policy second factors: https://zitadel.com/docs/guides/manage/console/default-settings
12. Passkeys: https://zitadel.com/docs/concepts/features/passkeys
13. Identity providers per organization: https://zitadel.com/docs/guides/integrate/identity-providers/introduction
14. SCIM v2.0 (Preview), per-org URL, Users only: https://zitadel.com/docs/apis/scim2
15. Organizations and projects: https://zitadel.com/docs/guides/manage/console/organizations ; https://zitadel.com/docs/concepts/structure/projects
16. Device authorization "as per RFC 8628": https://zitadel.com/docs/guides/integrate/login/oidc/device-authorization
17. Endpoints: https://zitadel.com/docs/apis/openidoauth/endpoints
18. Next.js example: https://zitadel.com/docs/sdk-examples/nextjs
19. Management API (add org, deactivate user, remove TOTP), administrators, Actions v2: https://zitadel.com/docs/apis/resources/mgmt/management-service-add-org ; https://zitadel.com/docs/apis/resources/user_service_v2/user-service-remove-totp ; https://zitadel.com/docs/guides/manage/console/administrators ; https://zitadel.com/docs/guides/integrate/actions/usage
20. Cloud regions: https://help.zitadel.com/where-is-zitadel-cloud-data-stored ; deployment and scaling: https://zitadel.com/docs/self-hosting/deploy/overview ; https://zitadel.com/docs/self-hosting/manage/updating_scaling ; license: https://github.com/zitadel/zitadel/blob/main/LICENSE ; https://zitadel.com/blog/apache-to-agpl ; pricing: https://zitadel.com/pricing ; https://zitadel.com/pricing/detail ; billing definitions: https://help.zitadel.com/how-is-daily-active-users-calculated ; export with passwords and OTP: https://zitadel.com/docs/guides/migrate/sources/zitadel ; passkeys not migratable: https://zitadel.com/docs/guides/migrate/users ; advisories: https://github.com/zitadel/zitadel/security/advisories?state=published ; GHSA-g8gj-gq47-xgf4; GHSA-45f2-5q3r-xgg6; certifications: https://help.zitadel.com/security-compliance/certifications-and-compliance ; stable tag retired: https://zitadel.com/docs/support/advisory/a10013

Ory
21. Login methods and MFA: https://www.ory.com/docs/kratos/self-service/flows/user-login ; https://www.ory.com/docs/kratos/mfa/overview
22. Organizations, SSO, SAML "exclusively on select Enterprise plans": https://www.ory.com/docs/kratos/organizations ; OEL: https://www.ory.com/docs/self-hosted/oel
23. SCIM, Enterprise, beta: https://www.ory.com/docs/kratos/manage-identities/scim ; https://changelog.ory.com/announcements/ann_b9p9Vz1axojWt
24. Device authorization grant: https://www.ory.com/docs/oauth2-oidc/device-authorization
25. JWKS and session-to-JWT: https://www.ory.com/docs/hydra/jwks ; https://www.ory.com/docs/identities/session-to-jwt-cors
26. Next.js quickstart: https://www.ory.com/docs/getting-started/integrate-auth/nextjs-app-router-quickstart
27. Console API and onboarding portal links (same as [22] and [23])
28. Personal data location: https://www.ory.com/docs/security-compliance/personal-data-location
29. Images and install: https://www.ory.com/docs/kratos/guides/docker ; https://hub.docker.com/r/oryd/hydra ; https://www.ory.com/docs/self-hosted/deployment ; licenses: https://github.com/ory/kratos/blob/master/LICENSE ; https://github.com/ory/hydra/blob/master/LICENSE ; releases: https://github.com/ory/kratos/releases
30. Client credentials: https://www.ory.com/docs/oauth2-oidc/client-credentials ; live events: https://www.ory.com/docs/actions/live-events ; pricing: https://www.ory.com/pricing ; import: https://www.ory.com/docs/kratos/manage-identities/import-user-accounts-identities ; advisories GHSA-hgx2-28f8-6g2r (Kratos), GHSA-r9w3-57w2-gch2 (Hydra); compliance: https://www.ory.com/docs/security-compliance/compliance-and-certifications

authentik
31. TOTP stage: https://docs.goauthentik.io/docs/add-secure-apps/flows-stages/stages/authenticator_totp/
32. WebAuthn stage: https://docs.goauthentik.io/docs/add-secure-apps/flows-stages/stages/authenticator_webauthn/
33. OAuth and SAML sources: https://docs.goauthentik.io/docs/users-sources/sources/protocols/oauth/ ; https://docs.goauthentik.io/docs/users-sources/sources/protocols/saml/
34. SCIM source: https://docs.goauthentik.io/docs/users-sources/sources/protocols/scim/
35. Brands and tenancy (alpha): https://docs.goauthentik.io/docs/customize/brands ; https://docs.goauthentik.io/sys-mgmt/tenancy/
36. Device code flow: https://docs.goauthentik.io/docs/add-secure-apps/providers/oauth2/device_code
37. OAuth2 provider endpoints: https://docs.goauthentik.io/docs/add-secure-apps/providers/oauth2/
38. Auth.js authentik provider: https://next-auth.js.org/providers/authentik
39. API: https://api.goauthentik.io/
40. AWS install: https://docs.goauthentik.io/docs/install-config/install/aws ; upgrade: https://docs.goauthentik.io/docs/install-config/upgrade ; compose: https://goauthentik.io/docker-compose.yml ; license: https://github.com/goauthentik/authentik/blob/main/LICENSE ; pricing: https://goauthentik.io/pricing ; client credentials: https://docs.goauthentik.io/docs/add-secure-apps/providers/oauth2/client_credentials ; events and transports: https://docs.goauthentik.io/sys-mgmt/events/event-actions/ ; https://docs.goauthentik.io/docs/sys-mgmt/events/transports ; security policy: https://docs.goauthentik.io/docs/security/policy ; advisories: https://github.com/goauthentik/authentik/security/advisories?state=published ; GHSA-xp7f-xjjx-gwm8

WorkOS
41. MFA: https://workos.com/docs/authkit/mfa
42. Passkeys: https://workos.com/docs/authkit/passkeys
43. Create connection: https://workos.com/docs/reference/sso/connection/create
44. Directory Sync and events: https://workos.com/docs/directory-sync ; https://workos.com/docs/events
45. Organizations, memberships, RBAC: https://workos.com/docs/reference/organization/create ; https://workos.com/docs/reference/authkit/organization-membership ; https://workos.com/docs/rbac
46. CLI Auth, "Based on the OAuth 2.0 Device Authorization Flow": https://workos.com/docs/authkit/cli-auth
47. OpenID configuration and JWKS: https://workos.com/docs/reference/workos-connect/metadata/openid-configuration ; https://workos.com/docs/reference/authkit/session-tokens/jwks
48. Next.js: https://workos.com/docs/authkit/nextjs
49. Directory API (no create): https://workos.com/docs/reference/directory-sync/directory ; invitations, MFA, Admin Portal: https://workos.com/docs/reference/authkit/invitation/send ; https://workos.com/docs/reference/authkit/mfa ; https://workos.com/docs/reference/admin-portal/portal-link/generate ; https://workos.com/docs/admin-portal ; M2M: https://workos.com/docs/authkit/connect/m2m ; webhooks retry: https://workos.com/docs/events/data-syncing/webhooks ; migration (import only): https://workos.com/docs/migrate/other-services
50. Subprocessors and security: https://workos.com/legal/subprocessors ; https://workos.com/security ; pricing: https://workos.com/pricing ; advisories GHSA-p8pf-44ff-93gf, GHSA-3x3j-pmx9-j3r7; disclosure: https://workos.com/security/responsible-disclosure

Auth0
51. MFA factors: https://auth0.com/docs/secure/multi-factor-authentication/multi-factor-authentication-factors
52. Passkeys: https://auth0.com/docs/authenticate/database-connections/passkeys
53. Connections API and enabling per org: https://auth0.com/docs/api/management/v2/connections/post-connections ; https://auth0.com/docs/manage-users/organizations/configure-organizations/enable-connections
54. Inbound SCIM: https://auth0.com/docs/authenticate/protocols/scim/configure-inbound-scim ; https://auth0.com/docs/api/management/v2/connections/post-scim-configuration
55. Organizations: https://auth0.com/docs/manage-users/organizations/organizations-overview ; https://auth0.com/docs/manage-users/organizations/organization-roles
56. Device flow and the Organizations limitation: https://auth0.com/docs/get-started/authentication-and-authorization-flow/device-authorization-flow ; https://support.auth0.com/center/s/article/missing-org-id-in-auth0-device-authentication-flow
57. Discovery and JWKS: https://auth0.com/docs/get-started/applications/configure-applications-with-oidc-discovery ; https://auth0.com/docs/secure/tokens/json-web-tokens/json-web-key-sets
58. Next.js: https://auth0.com/docs/quickstart/webapp/nextjs
59. Self-Service SSO: https://auth0.com/docs/authenticate/enterprise-connections/self-service-SSO ; block users: https://auth0.com/docs/manage-users/user-accounts/block-and-unblock-users
60. Public cloud endpoints: https://auth0.com/docs/troubleshoot/customer-support/operational-policies/public-cloud-service-endpoints ; deployment options: https://auth0.com/docs/deploy-monitor/deployment-options ; M2M for organizations: https://auth0.com/docs/manage-users/organizations/organizations-for-m2m-applications ; log streams: https://auth0.com/docs/customize/log-streams ; pricing: https://auth0.com/pricing ; hash export: https://auth0.com/docs/manage-users/user-migration/export-password-hashes-and-mfa-secrets ; compliance: https://auth0.com/docs/secure/data-privacy-and-compliance ; bounty: https://bugcrowd.com/auth0-okta ; advisories: https://github.com/advisories?query=auth0

Amazon Cognito
61. TOTP MFA: https://docs.aws.amazon.com/cognito/latest/developerguide/user-pool-settings-mfa-totp.html
62. Authentication flow methods and passkeys: https://docs.aws.amazon.com/cognito/latest/developerguide/amazon-cognito-user-pools-authentication-flow-methods.html
63. Identity federation: https://docs.aws.amazon.com/cognito/latest/developerguide/cognito-user-pools-identity-federation.html
64. SAML IdPs: https://docs.aws.amazon.com/cognito/latest/developerguide/cognito-user-pools-saml-idp.html
65. User pools overview (no SCIM) and quotas: https://docs.aws.amazon.com/cognito/latest/developerguide/cognito-user-pools.html ; https://docs.aws.amazon.com/cognito/latest/developerguide/limits.html
66. Multi-tenant patterns: https://docs.aws.amazon.com/cognito/latest/developerguide/multi-tenant-application-best-practices.html
67. Token endpoint grant types: https://docs.aws.amazon.com/cognito/latest/developerguide/token-endpoint.html ; AWS device-grant pattern: https://aws.amazon.com/blogs/security/implement-oauth-2-0-device-grant-flow-by-using-amazon-cognito-and-aws-lambda/
68. Verifying JWTs: https://docs.aws.amazon.com/cognito/latest/developerguide/amazon-cognito-user-pools-using-tokens-verifying-a-jwt.html ; endpoints: https://docs.aws.amazon.com/general/latest/gr/cognito_identity.html ; pricing: https://aws.amazon.com/cognito/pricing/ ; SOC scope: https://aws.amazon.com/compliance/services-in-scope/SOC/
69. Amplify Next.js: https://docs.amplify.aws/nextjs/build-a-backend/server-side-rendering/
70. AdminDisableUser: https://docs.aws.amazon.com/cognito-user-identity-pools/latest/APIReference/API_AdminDisableUser.html ; Lambda triggers: https://docs.aws.amazon.com/cognito/latest/developerguide/cognito-user-identity-pools-working-with-aws-lambda-triggers.html ; CloudTrail: https://docs.aws.amazon.com/cognito/latest/developerguide/logging-using-cloudtrail.html ; import tool: https://docs.aws.amazon.com/cognito/latest/developerguide/cognito-user-pools-using-import-tool.html ; vulnerability reporting: https://aws.amazon.com/security/vulnerability-reporting/

FusionAuth
71. MFA: https://fusionauth.io/docs/lifecycle/authenticate-users/multi-factor-authentication
72. WebAuthn: https://fusionauth.io/docs/apis/webauthn ; plans and features: https://fusionauth.io/docs/get-started/core-concepts/plans-features
73. OIDC and SAML identity providers: https://fusionauth.io/docs/lifecycle/authenticate-users/identity-providers/overview-oidc ; https://fusionauth.io/docs/lifecycle/authenticate-users/identity-providers/overview-samlv2 ; https://fusionauth.io/docs/apis/identity-providers
74. SCIM: https://fusionauth.io/docs/apis/scim
75. Tenants: https://fusionauth.io/docs/get-started/core-concepts/tenants
76. OAuth grants including device: https://fusionauth.io/docs/lifecycle/authenticate-users/oauth
77. Endpoints: https://fusionauth.io/docs/lifecycle/authenticate-users/oauth/endpoints
78. Docs index (no Next.js SDK page): https://fusionauth.io/docs/llms.txt
79. User delete (soft), two-factor, tenants API: https://fusionauth.io/docs/apis/users/delete ; https://fusionauth.io/docs/apis/two-factor ; https://fusionauth.io/docs/apis/tenants
80. Cluster: https://fusionauth.io/docs/operate/deploy/cluster ; Kubernetes: https://fusionauth.io/docs/get-started/download-and-install/kubernetes ; Docker Hub: https://hub.docker.com/r/fusionauth/fusionauth-app ; cloud vs self-hosted: https://fusionauth.io/docs/get-started/run-in-the-cloud/cloud-vs-self-hosted ; entity management: https://fusionauth.io/docs/get-started/core-concepts/entity-management ; events and webhooks: https://fusionauth.io/docs/extend/events-and-webhooks/events ; https://fusionauth.io/docs/extend/events-and-webhooks/writing-a-webhook ; pricing: https://fusionauth.io/pricing ; license FAQ: https://fusionauth.io/license-faq ; system requirements: https://fusionauth.io/docs/get-started/download-and-install/system-requirements ; release notes: https://fusionauth.io/docs/release-notes ; upgrade: https://fusionauth.io/docs/operate/deploy/upgrade ; import: https://fusionauth.io/docs/apis/users/import ; security: https://fusionauth.io/security ; trust: https://trust.fusionauth.io ; advisories: https://github.com/advisories?query=fusionauth

Logto
81. MFA: https://docs.logto.io/end-user-flows/mfa
82. Passkey sign-in: https://docs.logto.io/end-user-flows/sign-up-and-sign-in/passkey-sign-in ; March 2026 changelog: https://blog.logto.io/changelogs/2026-march
83. Enterprise connectors and SSO: https://docs.logto.io/connectors/enterprise-connectors ; https://docs.logto.io/end-user-flows/enterprise-sso
84. SCIM feature request, open: https://github.com/logto-io/logto/issues/9331
85. Organizations: https://docs.logto.io/organizations
86. Device flow: https://docs.logto.io/quick-starts/device-flow
87. Validate access tokens: https://docs.logto.io/authorization/validate-access-tokens
88. Next.js App Router: https://docs.logto.io/quick-starts/next-app-router ; M2M: https://docs.logto.io/quick-starts/m2m
89. Management API: https://openapi.logto.io/ ; webhooks: https://docs.logto.io/developers/webhooks ; https://docs.logto.io/developers/webhooks/webhooks-events
90. Cloud regions: https://docs.logto.io/logto-cloud/tenant-settings ; private cloud on Azure: https://docs.logto.io/logto-cloud/private-cloud ; deployment: https://docs.logto.io/logto-oss/deployment-and-configuration ; OSS vs Cloud: https://docs.logto.io/logto-oss ; license: https://github.com/logto-io/logto/blob/master/LICENSE ; pricing: https://logto.io/pricing ; billing: https://docs.logto.io/logto-cloud/billing-and-pricing ; migration: https://docs.logto.io/user-management/user-migration ; trust: https://logto.io/trust-and-security ; advisories: https://github.com/advisories?query=logto ; https://github.com/logto-io/logto/security/advisories ; releases: https://github.com/logto-io/logto/releases

SuperTokens
91. MFA introduction (managed deployments): https://supertokens.com/docs/additional-verification/mfa/introduction
92. Passkeys: https://supertokens.com/docs/authentication/passkeys/introduction
93. Manage tenants: https://supertokens.com/docs/authentication/enterprise/manage-tenants
94. SAML: https://supertokens.com/docs/authentication/enterprise/saml
95. "SuperTokens does not provide SCIM out of the box": https://supertokens.com/blog/scim-provisioning-explained
96. Multi-tenancy introduction: https://supertokens.com/docs/authentication/enterprise/introduction
97. Unified Login OAuth2 basics: https://supertokens.com/docs/authentication/unified-login/oauth2-basics
98. Manual JWT verification; well-known configuration: https://supertokens.com/docs/additional-verification/session-verification/protect-api-routes/manual-jwt-verification ; https://supertokens.com/docs/references/fdi/get-well-known-openid-configuration
99. Next.js: https://supertokens.com/docs/quickstart/integrations/nextjs/app-directory/init ; regions: https://supertokens.com/docs/quickstart/next-steps ; Docker: https://supertokens.com/docs/community/database-setup/postgresql ; licenses: https://github.com/supertokens/supertokens-core/blob/master/LICENSE.md ; https://github.com/supertokens/supertokens-core/blob/master/ee/LICENSE.md ; M2M: https://supertokens.com/docs/authentication/m2m/introduction ; pricing: https://supertokens.com/pricing ; self-host: https://supertokens.com/docs/deployment/self-host-supertokens ; migration: https://supertokens.com/docs/migration/account-migration ; advisory GHSA-j7vw-hh5c-2w6x ; SECURITY.md: https://github.com/supertokens/supertokens-core/blob/master/SECURITY.md ; product page SOC 2: https://supertokens.com/product
100. TOTP remove, common actions, hooks: https://supertokens.com/docs/references/cdi/totp-recipe/removetotpdevice ; https://supertokens.com/docs/post-authentication/user-management/common-actions ; https://supertokens.com/docs/authentication/email-password/hooks-and-overrides

Stytch
101. B2B MFA overview (SMS OTP and TOTP only): https://stytch.com/docs/b2b/guides/mfa/overview
102. SSO overview and create connections: https://stytch.com/docs/b2b/guides/sso/overview ; https://stytch.com/docs/api-reference/b2b/api/sso/oidc/create-oidc-connection.md ; https://stytch.com/docs/api-reference/b2b/api/sso/saml/create-saml-connection.md
103. SCIM: https://stytch.com/docs/b2b/guides/scim/overview ; https://stytch.com/docs/api-reference/b2b/api/scim/connection-management/create-scim-connection.md
104. RBAC: https://stytch.com/docs/b2b/guides/rbac/overview
105. Connected Apps grants and CLI guide: https://stytch.com/docs/connected-apps/oauth-learn-more/oauth-basics.md ; https://stytch.com/docs/connected-apps/guides/cli-agents.md
106. JWKS: https://stytch.com/docs/api-reference/b2b/api/sessions/get-jwks.md
107. Next.js SDK: https://stytch.com/docs/api-reference/b2b/frontend-sdks/nextjs/installation
108. Admin Portal components: https://stytch.com/docs/multi-tenant-auth/enterprise-ready/admin-portal.md ; M2M: https://stytch.com/docs/api-reference/b2b/api/m2m/overview.md
109. Data compliance (U.S. servers): https://stytch.com/docs/resources/policies/security-and-trust/compliance/data-compliance.md ; pricing: https://stytch.com/pricing ; webhooks: https://stytch.com/docs/resources/workspace-management/webhooks.md ; Svix retries: https://docs.svix.com/retries ; export: https://stytch.com/docs/b2b/guides/migrations/exporting-from-stytch.md ; compliance overview: https://stytch.com/docs/resources/policies/security-and-trust/compliance/overview.md ; Twilio security: https://www.twilio.com/en-us/legal/security-overview

Clerk
111. Sign-up and sign-in options: https://clerk.com/docs/guides/configure/auth-strategies/sign-up-sign-in-options
112. Passkeys: https://clerk.com/docs/guides/development/custom-flows/authentication/passkeys ; https://clerk.com/docs/guides/secure/mfa-recovery
113. Enterprise connections: https://clerk.com/docs/guides/configure/auth-strategies/enterprise-connections/overview ; https://clerk.com/changelog/2026-03-09-bapi-enterprise-connections ; https://clerk.com/changelog/2026-09-24-multiple-enterprise-connections-per-organization
114. Directory Sync: https://clerk.com/docs/guides/configure/auth-strategies/enterprise-connections/directory-sync
115. Organizations and roles: https://clerk.com/docs/guides/organizations/overview ; https://clerk.com/docs/guides/organizations/control-access/roles-and-permissions
116. Device Authorization Grant (Beta): https://clerk.com/docs/guides/configure/auth-strategies/oauth/device-authorization-grant ; https://clerk.com/changelog/2026-09-08-device-authorization-grant
117. Manual JWT verification: https://clerk.com/docs/guides/sessions/manual-jwt-verification
118. Next.js quickstart: https://clerk.com/docs/nextjs/getting-started/quickstart
119. Self-serve SSO: https://clerk.com/docs/guides/configure/auth-strategies/enterprise-connections/self-serve-sso ; ban user: https://clerk.com/docs/reference/backend/user/ban-user
120. Security (US-hosted, Google Cloud, no region selection, SOC 2, not ISO 27001, no paid bounty): https://clerk.com/security ; https://clerk.com/articles/clerk-security-how-we-protect-your-users ; pricing: https://clerk.com/pricing ; machine auth: https://clerk.com/docs/guides/development/machine-auth/overview ; webhooks: https://clerk.com/docs/guides/development/webhooks/overview ; migration: https://clerk.com/docs/deployments/migrate-overview

Considered, not scored
121. Descope: https://www.descope.com/pricing ; https://docs.descope.com/sso ; https://docs.descope.com/auth-methods/device-auth
122. Frontegg: https://frontegg.com/pricing ; https://frontegg.com/product/sso-scim
123. Hanko: https://github.com/teamhanko/hanko ; https://www.hanko.io/pricing ; https://docs.hanko.io/guides/enterprise-sso/introduction
124. Microsoft Entra External ID: https://learn.microsoft.com/en-us/entra/external-id/customers/concept-multifactor-authentication-customers ; https://www.microsoft.com/en-us/security/pricing/microsoft-entra-external-id/
125. PropelAuth: https://www.propelauth.com/pricing ; https://docs.byo.propelauth.com/
126. SSOReady: https://github.com/ssoready/ssoready (pricing page unreachable from the session; entry price not verified)
127. Ory Polis, formerly BoxyHQ Jackson: https://github.com/boxyhq/jackson ; https://www.ory.com/polis

AWS
130. Fargate pricing: https://aws.amazon.com/fargate/pricing/ ; RDS for PostgreSQL pricing: https://aws.amazon.com/rds/postgresql/pricing/ ; ELB pricing: https://aws.amazon.com/elasticloadbalancing/pricing/ ; SES pricing: https://aws.amazon.com/ses/pricing/ ; AWS Price List API files for us-east-1: https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonECS/current/us-east-1/index.csv ; .../AmazonRDS/current/us-east-1/index.csv ; .../AWSELB/current/us-east-1/index.csv
