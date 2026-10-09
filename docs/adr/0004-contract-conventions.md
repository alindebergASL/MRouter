# 0004. Contract conventions: money, evidence, no free text, versioning

- Status: Proposed
- Date: 2026-10-09
- Deciders: Andrew Lindeberg (on merge)
- Guarantees affected: M1, M2, M4, M5, P1, B1–B6, C2, C3, C4, R1, R2 (their acceptance tests build on
  `contracts/`)

## Context

`contracts/` v0 (build plan §7, Lane A task 2) is the first code every lane builds against. Lane B's
ledger and pricing oracle, Lane C's relay, and Lane D's console must agree on how money, missing
values, and identifiers are written. A choice made separately in each lane would let a float, a
silent zero, or a prompt excerpt slip into a usage record. Each of those breaks a guarantee: §6.4
requires exact money, §6.5 says unavailable is not zero, and C2 and C4 forbid persisted content. C4's
acceptance test also requires that "CI fails if any field in the event schema can carry free text",
so that property has to be checkable by a machine.

The options considered were documentation-only conventions or conventions enforced by the validator.
Prior art pointed the same way: Token Meter's `EvidenceValue` (value or null, with a basis) and its
identifier allowlist for telemetry. Switchyard's routing log, by contrast, records unknown token
counts as 0.

## Decision

`contracts/README.md` is the canonical statement of the conventions. In short:

- **Money.** Amounts are integer nano-dollars in the signed 64-bit range. Rates, multipliers, fees,
  USD amounts, and probabilities are exact-decimal strings, and rates are in USD per million tokens.
  JSON floats are banned from every file under `contracts/`, and the validator rejects them when it
  parses.
- **Evidence.** Every metered value is `{value, evidence, unavailable_reason}`. The value is null
  exactly when the evidence is `unavailable`, and a reason is required then. Each producer is limited
  to the tiers §6.5 lets it emit.
- **No free text.** In event and denial schemas, every string is a const, an enum, or one of a short
  allowlist of bounded identifier formats in `common.schema.json`, none of which admits whitespace.
  Every object is closed. Price-affecting request values are enumerated, never stored raw. Denial
  messages are fixed templates.
- **Identity and versioning.** `$id` is `urn:purser:contracts:<name>:<semver>`, resolved locally.
  Instances carry `schema_version`, and any `0.1.x` instance is valid under the 0.1.0 schemas.
- **Enforcement.** `contracts/tools/check_contracts.py`, pinned by hash in
  `contracts/tools/requirements.txt`, enforces all of this in `make check-contracts` and CI. It lives in
  the protected `contracts/` tree because it defines what "valid" means and is C4's CI lint.

## Consequences

- A float, a silent zero, or a free-text field in a contract fails CI rather than review.
- An identifier format can still carry a short token such as `ignore_previous_instructions` or base64.
  The lint stops prose and whitespace, not encoding. It also means the allowlisted formats in
  `common.schema.json` deserve review whenever one changes.
- JSON consumers must parse nano-dollars as 64-bit integers. In JavaScript that means `BigInt` or a
  JSON parser that keeps large integers, because doubles lose precision above 2^53 (about $9.0 million).
- Upgrading `jsonschema` is a protected change, made in a spec-change session.
- The pieces around the validator are not protected: `scripts/check-contracts.sh`, the Makefile target,
  and the CI step. A normal session could stop CI from running the check, but not change what it
  checks. CODEOWNERS review of `.github/` and the Makefile is the backstop.
- Revisit when a lane needs a free-text field (it would need its own guarantee argument), or at 1.0.0,
  when the versioning rule tightens to strict semver.
