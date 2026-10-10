# contracts/ (protected)

The spec in machine-readable form. Every other component builds against what is here: the ledger and
pricing oracle (Lane B), the relay and sidecar (Lane C), and the control plane and console (Lane D).
Guarantee IDs refer to `docs/architecture.md`.

**Protected path.** Change it only in a spec-change session (`ROUTER_SPEC_CHANGE=1`) through a pull
request labeled `spec-change`. See `CLAUDE.md` and build plan §2.2.

## Schemas

JSON Schema draft 2020-12. "Referenced by" names the guarantees whose acceptance tests will use the
schema; no guarantee is demonstrated by a schema alone.

| Schema | Version | Referenced by | What it describes |
|---|---|---|---|
| [common](schemas/common.schema.json) | 0.1.0 | M2, C2, C4 | Shared definitions: nano-dollars, exact decimals, identifiers, timestamps, evidence tiers, evidenced values, keyed hashes |
| [usage-event](schemas/usage-event.schema.json) | 0.1.0 | M1, M2, M4, P1, C2 | One record per dispatched upstream attempt (§6.1): tokens by class, cost, evidence |
| [attempt](schemas/attempt.schema.json) | 0.1.0 | B1, B2, P1, M1, H1 | An attempt and its reservation: the §7.5 state machine, transitions, ceiling, settlement |
| [error-envelope](schemas/error-envelope.schema.json) | 0.1.0 | B3, B4, B5, C3 | The router's own denials in Anthropic Messages, OpenAI Chat Completions, and OpenAI Responses shapes |
| [price-book](schemas/price-book.schema.json) | 0.1.0 | M2, M5, B3, B6, P1 | Versioned, effective-dated price book: every §6.3 rule type and the stacking order |
| [price-fixture](schemas/price-fixture.schema.json) | 0.1.0 | M2, M5 | A golden pricing case: usage in, exact nano-dollars out |
| [decision-record](schemas/decision-record.schema.json) | 0.1.0 | R1, R2 | A replayable routing decision (§8.5) |
| [trace-event](schemas/trace-event.schema.json) | 0.1.0 | C4, M4 | A content-free trace-reader event (§6.6) |

Each schema's `$id` is `urn:purser:contracts:<name>:<version>`; cross-file references use that URN and
resolve locally, never over the network. Instances carry `schema_version`, and any `0.1.x` instance is
valid under the 0.1.0 schemas.

## Layout

```
schemas/                     the schemas
examples/<target>/valid/     instances that must validate
examples/<target>/invalid/   instances that must fail, each for the reason in invalid/expectations.json
fixtures/pricing/            the audited price book and golden pricing fixtures
tools/check_contracts.py     the validator; tools/requirements.txt pins its dependencies
```

`<target>` is a schema name, or `<schema>.<definition>` to test one of its `$defs` (for example
`attempt.transition` or `common.decimal`). Examples under `examples/price-book/` and
`examples/price-fixture/` show shapes only: their sources point at `example.com`. Real prices live only
in `fixtures/pricing/`, where every entry and rule has a dated provider source that the
`pricing-auditor` subagent checks.

## Conventions

These hold in every schema. [ADR 0004](../docs/adr/0004-contract-conventions.md) records why.

- **Money is never a float.** Amounts are integer nano-dollars (1 nd = $0.000000001) in the signed
  64-bit range (§6.4). Rates (USD per million tokens), multipliers, fees, USD amounts, and
  probabilities are exact-decimal strings such as `"8.8"`. No JSON file under `contracts/` may contain a
  float, NaN, or Infinity. Consumers must parse nano-dollars as 64-bit integers: above 2^53 (about
  $9.0 million) an IEEE double loses precision.
- **Unavailable is not zero.** Every metered value is `{value, evidence, unavailable_reason}`. Evidence
  is one of the §6.5 tiers. `evidence: "unavailable"` holds exactly when `value` is null, and only then
  is `unavailable_reason` present. A measured zero is `{"value": 0, "evidence": "measured"}`.
- **Evidence matches its producer** (§6.5 Source column). Usage events never carry `estimated`; trace
  events never carry `reconciled`.
- **Token classes.** Priced: `uncached_input`, `cache_read`, `cache_write_5m`, `cache_write_1h`,
  `cache_write_unspecified` (writes whose provider reports no TTL, such as OpenAI), `output`.
  Informational and never priced again: `reasoning` (already in output where reported) and
  `cache_write_total` (the provider's own total, kept so an inconsistent split stays visible).
- **No free text** (C2, C4). In usage events, attempts, decision records, trace events, and denial
  responses, every string is a const, an enum, or one of the bounded identifier formats in `common`
  (`id`, `model_id`, `uuid`, `hmac_hex`, `rfc3339_utc`, `date`, `decimal`, `probability`, `semver`,
  `delta_seconds`, `schema_version`), none of which admits whitespace, and every object is closed.
  Request values that change the price (speed, service tier, geography) are enumerated; an
  unrecognized one is stored as null with reason `unrecognized_value`, never as the raw string.
  Denial messages are fixed templates.
- **Unpriced is never zero** (§6.3). A price book prices only what it lists. A missing model, token
  class, dimension value, or fee makes the attempt unpriced, and so does any value for a dimension the
  entry has no rule for. An unpriced request is unbounded under a strict budget (B3). A rule's
  `when_omitted` says how an omitted setting is priced: the provider's fixed default, the highest
  listed multiplier for a ceiling when the provider resolves it from account settings the relay cannot
  see (then a settled cost uses the reported value), or unpriced.
- **Errors are not free by accident.** An upstream error before any output settles at 0 with basis
  `upstream_error`; an error after output began goes `unknown` and settles from partial stream usage
  or by ceiling charge, so billed partial output is never recorded as 0.
- **Patterns mean the same everywhere.** Patterns start with `^`, end with `(?!\n)$` (in Python, `$`
  alone also matches before a trailing newline), and avoid `\d`, `\w`, `\s`, and bare `.`, which
  differ between Python's `re` and ECMA-262.
- **Denials carry the attempt ID natively.** The Anthropic body's `request_id` and the `request-id` or
  `x-request-id` header equal `purser-attempt-id`, the ID `explain_denial` takes (§7.8).

## Checking

```
make check-contracts
```

This builds a virtualenv in `.cache/contracts-venv` from the hash-pinned `tools/requirements.txt`
(Python 3.11 or later; set `PYTHON` to choose the interpreter) and runs `tools/check_contracts.py`.
CI runs it in the `checks` job. It checks:

1. Every schema passes the 2020-12 metaschema, uses only 2020-12 keywords, follows the `$id`
   convention, and writes portable patterns.
2. This README's table lists every schema with the version in its `$id`, and every guarantee ID it
   names exists in `docs/architecture.md`.
3. Every valid example validates. Every invalid example fails, and every error it raises is the one
   its `expectations.json` entry names (plus any it lists under `also`).
4. No JSON here holds a float, NaN, Infinity, or a duplicate key.
5. The no-free-text lint passes, and its self-test proves it rejects free text, open keys, and the
   other shapes that could smuggle it in.
6. Router denials with non-retryable statuses (400, 403) avoid every message that makes OpenCode
   retry or treat the error as a context overflow (its patterns, pinned to a commit).
7. Cross-field rules a schema cannot express hold (each schema's root `$comment` lists them).
8. Pricing fixtures are exact: each class cost is tokens × rate / 10⁶, the priced classes and fees
   are exactly the ones used, the total rounds half-up to nano-dollars, and each fixture matches its
   price-book entry and effective window. `opus-5-5-fast-us` is $0.209 = 209,000,000 nd (§6.3, M5).
   The rates are verified by the `pricing-auditor` and Lane B's oracle, not by this check.

## Changing a contract

1. Start a spec-change session (`ROUTER_SPEC_CHANGE=1`).
2. Change the schema, bump its version in `$id` and in the table above, and add or update examples.
   Bump the patch for documentation and examples, the minor for a compatible addition, and the major
   for a breaking change; before 1.0.0 a minor bump may break.
3. Run `make check-contracts`. For a change to prices or pricing fixtures, run the `pricing-auditor`.
4. Open a pull request labeled `spec-change`.
