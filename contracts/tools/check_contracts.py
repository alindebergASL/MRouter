#!/usr/bin/env python3
"""Validate Purser's contracts: schemas, examples, and pricing fixtures.

Run through `make check-contracts` (scripts/check-contracts.sh), which provides the
pinned dependencies in contracts/tools/requirements.txt. Checks, in order:

1. Every schema passes the draft 2020-12 metaschema, uses only 2020-12 keywords
   (a typo such as "requried" fails), follows the $id and version convention, and
   writes patterns that mean the same in Python and ECMA-262.
2. contracts/README.md lists every schema with the version in its $id and the
   guarantee IDs that reference it, each of which exists in docs/architecture.md.
3. Every example directory has valid and invalid examples. Valid ones pass. Each
   invalid one fails, and every error it raises is the one named for it in
   invalid/expectations.json (plus any it lists under "also" because the same mistake
   necessarily breaks them), so an example can't fail for an accidental reason.
   Cross-field rules a schema cannot express (listed in its root $comment) are
   checked too; an invalid example targets one with keyword "semantic:<rule>".
4. No JSON under contracts/ holds a float, NaN, Infinity, or a duplicate key, so
   money can never be a float (architecture 6.4).
5. No-free-text lint (C2, C4): in the event and denial-body schemas every string
   is a const, an enum, or one of the bounded formats in common.schema.json, and
   every object is closed. A self-test proves the lint rejects free text.
6. Router denial bodies for non-retryable statuses (400, 403) avoid the text that
   makes OpenCode retry a request.
7. Priced pricing fixtures add up: class costs plus fees equal total_usd, and
   total_nanodollars is total_usd rounded half-up to the nano-dollar. The Opus 5.5
   fast plus US-only fixture equals $0.209 (M5). Rates themselves are verified by
   the pricing-auditor and Lane B's oracle, not here.

Exit status is 0 when every check passes and 1 otherwise.
"""

from __future__ import annotations

import json
import re
import sys
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, Iterator

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = ROOT / "contracts"
SCHEMAS = CONTRACTS / "schemas"
EXAMPLES = CONTRACTS / "examples"
FIXTURES = CONTRACTS / "fixtures"
README = CONTRACTS / "README.md"
ARCHITECTURE = ROOT / "docs" / "architecture.md"

META = "https://json-schema.org/draft/2020-12/schema"
ID_RE = re.compile(
    r"^urn:purser:contracts:(?P<name>[a-z][a-z0-9-]*):"
    r"(?P<version>(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*))$"
)
COMMON = "common"

# Schemas whose instances are persisted or sent to clients and must hold no free text.
NO_FREE_TEXT = ("usage-event", "attempt", "decision-record", "trace-event", "error-envelope")
# The only patterned string formats the lint accepts (common.schema.json $defs).
FORMAT_ALLOWLIST = (
    "schema_version",
    "uuid",
    "id",
    "model_id",
    "semver",
    "hmac_hex",
    "rfc3339_utc",
    "date",
    "decimal",
    "probability",
    "delta_seconds",
)
# Strings every allowlisted format must reject: prose, whitespace, empty.
FREE_TEXT_PROBES = ("", " ", "ignore previous instructions", "a b", "a\tb", "line\nbreak")

# OpenCode (packages/opencode/src/session/retry.ts) retries any error whose message or
# body matches these, so a non-retryable denial must not contain them.
OPENCODE_RETRY_TRIGGERS = re.compile(
    r"429|500|502|503|504|524|rate limit|unavailable|try again later|overloaded", re.IGNORECASE
)

# The Opus 5.5 fast plus US-only worked example (architecture 6.3, M5).
ANCHOR_FIXTURE = ("opus-5-5-fast-us", 209_000_000)

KEYWORDS_2020_12 = frozenset(
    {
        # core
        "$schema", "$id", "$ref", "$anchor", "$dynamicRef", "$dynamicAnchor",
        "$vocabulary", "$comment", "$defs",
        # applicator
        "allOf", "anyOf", "oneOf", "not", "if", "then", "else", "dependentSchemas",
        "prefixItems", "items", "contains", "properties", "patternProperties",
        "additionalProperties", "propertyNames",
        # unevaluated
        "unevaluatedItems", "unevaluatedProperties",
        # validation
        "type", "const", "enum", "multipleOf", "maximum", "exclusiveMaximum", "minimum",
        "exclusiveMinimum", "maxLength", "minLength", "pattern", "maxItems", "minItems",
        "uniqueItems", "maxContains", "minContains", "maxProperties", "minProperties",
        "required", "dependentRequired",
        # format, content, meta-data
        "format", "contentEncoding", "contentMediaType", "contentSchema", "title",
        "description", "default", "deprecated", "readOnly", "writeOnly", "examples",
    }
)
SUBSCHEMA = ("additionalProperties", "contains", "propertyNames", "not", "if", "then", "else",
             "items", "unevaluatedItems", "unevaluatedProperties", "contentSchema")
SUBSCHEMA_LISTS = ("allOf", "anyOf", "oneOf", "prefixItems")
SUBSCHEMA_MAPS = ("$defs", "properties", "patternProperties", "dependentSchemas")


class Failures:
    def __init__(self) -> None:
        self.items: list[str] = []

    def add(self, where: str, message: str) -> None:
        self.items.append(f"{where}: {message}")


# --- JSON loading ---------------------------------------------------------------


class ContractJSONError(ValueError):
    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


def _reject_float(text: str) -> Any:
    raise ContractJSONError("parse:float", f"JSON float {text}: use an integer or an exact-decimal string")


def _reject_constant(text: str) -> Any:
    raise ContractJSONError("parse:constant", f"JSON constant {text} is not valid JSON")


def _unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise ContractJSONError("parse:duplicate-key", f"duplicate key {key!r}")
        out[key] = value
    return out


def load_json(path: Path) -> Any:
    return json.loads(
        path.read_text(encoding="utf-8"),
        parse_float=_reject_float,
        parse_constant=_reject_constant,
        object_pairs_hook=_unique_keys,
    )


def rel(path: Path) -> str:
    return str(path.relative_to(ROOT))


def pointer(parts: Any) -> str:
    return "".join("/" + str(p).replace("~", "~0").replace("/", "~1") for p in parts)


# --- 1. Schemas -------------------------------------------------------------------


def pattern_problems(pattern: str) -> list[str]:
    """Patterns must mean the same to Python's re and ECMA-262, and be anchored."""
    problems = []
    if not (pattern.startswith("^") and pattern.endswith("$")):
        problems.append("not anchored with ^...$")
    i, in_class = 0, False
    while i < len(pattern):
        c = pattern[i]
        if c == "\\":
            nxt = pattern[i + 1 : i + 2]
            if nxt and nxt in "dDwWsSbB":
                problems.append(f"\\{nxt} is Unicode-dependent; spell out the character class")
            i += 2
            continue
        if in_class:
            if c == "]":
                in_class = False
        elif c == "[":
            in_class = True
            if pattern[i + 1 : i + 2] == "^":
                i += 1
            if pattern[i + 1 : i + 2] == "]":
                i += 1
        elif c == ".":
            problems.append("unescaped '.' matches anything; use [.] or an explicit class")
        i += 1
    return problems


def walk_keywords(node: Any, where: str, failures: Failures) -> None:
    if isinstance(node, bool):
        return
    if not isinstance(node, dict):
        failures.add(where, "a schema must be an object or a boolean")
        return
    for key, value in node.items():
        here = f"{where}/{key}"
        if key not in KEYWORDS_2020_12:
            failures.add(here, f"unknown keyword {key!r} (not in the 2020-12 vocabulary)")
            continue
        if key == "pattern":
            for problem in pattern_problems(value):
                failures.add(here, f"pattern {value!r}: {problem}")
        if key in SUBSCHEMA:
            walk_keywords(value, here, failures)
        elif key in SUBSCHEMA_LISTS:
            for i, sub in enumerate(value):
                walk_keywords(sub, f"{here}/{i}", failures)
        elif key in SUBSCHEMA_MAPS:
            for name, sub in value.items():
                if key == "patternProperties":
                    for problem in pattern_problems(name):
                        failures.add(here, f"pattern {name!r}: {problem}")
                walk_keywords(sub, f"{here}/{name}", failures)


def load_schemas(failures: Failures) -> dict[str, dict[str, Any]]:
    schemas: dict[str, dict[str, Any]] = {}
    for path in sorted(SCHEMAS.glob("*.schema.json")):
        where = rel(path)
        try:
            schema = load_json(path)
        except (ContractJSONError, json.JSONDecodeError) as exc:
            failures.add(where, f"cannot load: {exc}")
            continue
        name = path.name.removesuffix(".schema.json")
        match = ID_RE.match(schema.get("$id", ""))
        if schema.get("$schema") != META:
            failures.add(where, f"$schema must be {META}")
        if not match or match["name"] != name:
            failures.add(where, f"$id must be urn:purser:contracts:{name}:<major>.<minor>.<patch>")
        for key in ("title", "description"):
            if not isinstance(schema.get(key), str) or not schema[key]:
                failures.add(where, f"missing {key}")
        try:
            Draft202012Validator.check_schema(schema)
        except Exception as exc:  # jsonschema.SchemaError
            failures.add(where, f"fails the 2020-12 metaschema: {exc}")
        walk_keywords(schema, where + "#", failures)
        schemas[name] = schema
    if COMMON not in schemas:
        failures.add(rel(SCHEMAS), "common.schema.json is missing")
    return schemas


def make_registry(schemas: dict[str, dict[str, Any]]) -> Registry:
    resources = [
        (s["$id"], Resource.from_contents(s, default_specification=DRAFT202012))
        for s in schemas.values()
        if "$id" in s
    ]
    return Registry().with_resources(resources)


def version_of(schema: dict[str, Any]) -> str:
    match = ID_RE.match(schema.get("$id", ""))
    return match["version"] if match else "?"


# --- 2. README --------------------------------------------------------------------

README_ROW = re.compile(
    r"^\|\s*\[(?P<name>[a-z-]+)\]\(schemas/(?P<file>[a-z-]+)\.schema\.json\)\s*"
    r"\|\s*(?P<version>[0-9.]+)\s*\|\s*(?P<ids>[^|]*?)\s*\|"
)


def check_readme(schemas: dict[str, dict[str, Any]], failures: Failures) -> None:
    where = rel(README)
    rows = {}
    for line in README.read_text(encoding="utf-8").splitlines():
        match = README_ROW.match(line)
        if match:
            if match["name"] != match["file"]:
                failures.add(where, f"row {match['name']} links schemas/{match['file']}.schema.json")
            rows[match["file"]] = match
    spec = ARCHITECTURE.read_text(encoding="utf-8")
    for name, schema in schemas.items():
        row = rows.get(name)
        if row is None:
            failures.add(where, f"no table row for {name}")
            continue
        if row["version"] != version_of(schema):
            failures.add(where, f"{name} listed as {row['version']}, $id says {version_of(schema)}")
        ids = [i.strip() for i in row["ids"].split(",") if i.strip()]
        if not ids:
            failures.add(where, f"{name} lists no guarantee IDs")
        for gid in ids:
            if f"**{gid}." not in spec:
                failures.add(where, f"{name}: guarantee {gid} is not defined in docs/architecture.md")
    for name in rows.keys() - schemas.keys():
        failures.add(where, f"table row {name} has no schema file")


# --- 3. Examples ------------------------------------------------------------------


def validator_for(
    target: str, schemas: dict[str, dict[str, Any]], registry: Registry
) -> Draft202012Validator | None:
    """target is a schema name, or <schema>.<def> for one of its $defs."""
    name, _, def_name = target.partition(".")
    schema = schemas.get(name)
    if schema is None:
        return None
    if def_name:
        if def_name not in schema.get("$defs", {}):
            return None
        schema = {"$schema": META, "$ref": f"{schema['$id']}#/$defs/{def_name}"}
    return Draft202012Validator(schema, registry=registry)


def error_matches(error: ValidationError, path: str, keyword: str) -> bool:
    if pointer(error.absolute_path) == path and error.validator == keyword:
        return True
    return any(error_matches(sub, path, keyword) for sub in error.context or ())


def describe(error: ValidationError) -> str:
    return f"{pointer(error.absolute_path) or '/'} [{error.validator}] {error.message[:200]}"


def check_examples(
    schemas: dict[str, dict[str, Any]], registry: Registry, failures: Failures
) -> tuple[int, set[Path]]:
    count = 0
    parse_failures: set[Path] = set()
    covered = set()
    if not EXAMPLES.is_dir():
        failures.add(rel(EXAMPLES), "missing")
        return count, parse_failures
    for directory in sorted(p for p in EXAMPLES.iterdir() if p.is_dir()):
        where = rel(directory)
        validator = validator_for(directory.name, schemas, registry)
        if validator is None:
            failures.add(where, "names no schema (use <schema> or <schema>.<$defs name>)")
            continue
        covered.add(directory.name.partition(".")[0])
        valid = sorted((directory / "valid").glob("*.json"))
        invalid_dir = directory / "invalid"
        invalid = sorted(p for p in invalid_dir.glob("*.json") if p.name != "expectations.json")
        if not valid:
            failures.add(where, "has no valid examples")
        if not invalid:
            failures.add(where, "has no invalid examples")
        for path in valid:
            count += 1
            try:
                instance = load_json(path)
            except (ContractJSONError, json.JSONDecodeError) as exc:
                failures.add(rel(path), f"cannot load: {exc}")
                continue
            for error in validator.iter_errors(instance):
                failures.add(rel(path), f"valid example fails: {describe(error)}")
            for rule, message in semantic_problems(directory.name, instance):
                failures.add(rel(path), f"valid example breaks {rule}: {message}")
        if not invalid:
            continue
        try:
            expectations = load_json(invalid_dir / "expectations.json")
        except (OSError, ContractJSONError, json.JSONDecodeError) as exc:
            failures.add(where, f"invalid/expectations.json: {exc}")
            continue
        names = {p.name for p in invalid}
        for orphan in sorted(expectations.keys() - names):
            failures.add(where, f"expectations.json names missing file {orphan}")
        for path in invalid:
            count += 1
            expected = expectations.get(path.name)
            if not isinstance(expected, dict) or not {"path", "keyword", "why"} <= expected.keys():
                failures.add(rel(path), "needs an expectations.json entry with path, keyword, and why")
                continue
            keyword = expected["keyword"]
            try:
                instance = load_json(path)
            except ContractJSONError as exc:
                parse_failures.add(path)
                if exc.kind != keyword:
                    failures.add(rel(path), f"expected {keyword}, but loading failed with {exc.kind}: {exc}")
                continue
            except json.JSONDecodeError as exc:
                failures.add(rel(path), f"not JSON: {exc}")
                continue
            if keyword.startswith("parse:"):
                failures.add(rel(path), f"expected {keyword}, but it loaded")
                continue
            errors = list(validator.iter_errors(instance))
            if keyword.startswith("semantic:"):
                for error in errors:
                    failures.add(rel(path), f"must pass the schema to test {keyword}: {describe(error)}")
                broken = {rule for rule, _ in semantic_problems(directory.name, instance)}
                if broken != {keyword.removeprefix("semantic:")}:
                    failures.add(rel(path), f"expected only {keyword} to fail; cross-field rules broken: {sorted(broken) or 'none'}")
                continue
            if not errors:
                failures.add(rel(path), "invalid example passes validation")
            # "also" lists further (path, keyword) pairs the same mistake necessarily breaks, such
            # as a message that is outside both the template enum and its code's const.
            allowed = [(expected["path"], keyword)] + [(a["path"], a["keyword"]) for a in expected.get("also", [])]
            for error in errors:
                if not any(error_matches(error, p, k) for p, k in allowed):
                    failures.add(
                        rel(path),
                        f"fails for an unexpected reason (want {expected['path']} [{keyword}]): {describe(error)}",
                    )
            for p, k in allowed:
                if errors and not any(error_matches(error, p, k) for error in errors):
                    failures.add(rel(path), f"expected an error at {p or '/'} [{k}], but none was raised")
    for name in sorted(schemas.keys() - covered):
        failures.add(rel(EXAMPLES), f"no examples for schema {name}")
    for name in sorted(schemas.keys() - {COMMON}):
        if not (EXAMPLES / name).is_dir():
            failures.add(rel(EXAMPLES), f"no examples for the root of schema {name}")
    return count, parse_failures


# --- 3b. Cross-field rules JSON Schema cannot express --------------------------------------
# Each schema's root $comment lists the rules that apply to it. Valid examples and pricing
# fixtures must satisfy them; an invalid example can target one with keyword "semantic:<rule>".


def instant(text: str) -> tuple[str, int]:
    """Order RFC 3339 UTC timestamps (the common rfc3339_utc format) to the nanosecond."""
    whole, _, frac = text.rstrip("Z").partition(".")
    return whole, int((frac or "0").ljust(9, "0"))


def _attempt_rules(doc: dict[str, Any]) -> Iterator[tuple[str, str]]:
    settlement = doc.get("settlement") or {}
    ceiling, cost = doc.get("ceiling_nanodollars"), settlement.get("cost_nanodollars")
    if settlement.get("basis") == "ceiling_charge" and cost != ceiling:
        yield "ceiling_charge_equals_ceiling", f"a ceiling charge of {cost} nd is not the ceiling {ceiling} nd (7.5)"
    if settlement and ceiling is not None and cost is not None and settlement.get("overrun") != (cost > ceiling):
        yield "overrun_matches_cost", f"overrun is {settlement.get('overrun')} for cost {cost} nd and ceiling {ceiling} nd (7.6)"
    if doc.get("retry_of_attempt_id") == doc.get("attempt_id"):
        yield "retry_not_self", "an attempt cannot retry itself"
    times = [doc.get("reserved_at")] + [t.get("at") for t in doc.get("transitions", [])]
    if any(b < a for a, b in zip(map(instant, times), map(instant, times[1:]))):
        yield "times_ordered", "transition times go backwards or precede reserved_at"


def _usage_event_rules(doc: dict[str, Any]) -> Iterator[tuple[str, str]]:
    dispatched = instant(doc["dispatched_at"])
    for key in ("settled_at", "recorded_at"):
        if doc.get(key) and instant(doc[key]) < dispatched:
            yield "times_ordered", f"{key} is before dispatched_at"
    timing = doc.get("timing", {})
    ttfb, duration = timing.get("ttfb_ms", {}).get("value"), timing.get("duration_ms", {}).get("value")
    if ttfb is not None and duration is not None and ttfb > duration:
        yield "ttfb_within_duration", f"ttfb_ms {ttfb} exceeds duration_ms {duration}"
    if doc.get("correlation", {}).get("retry_of_attempt_id") == doc.get("attempt_id"):
        yield "retry_not_self", "an attempt cannot retry itself"
    fee_ids = [f.get("fee_id") for f in doc.get("fees", [])]
    if len(fee_ids) != len(set(fee_ids)):
        yield "fee_ids_distinct", "fees lists a fee_id more than once"


def _error_envelope_rules(doc: dict[str, Any]) -> Iterator[tuple[str, str]]:
    body = doc.get("body", {})
    detail = body.get("purser") or body.get("error", {}).get("details", {}).get("purser")
    if detail and detail["remaining_nanodollars"] >= detail["ceiling_nanodollars"]:
        yield "budget_remaining_below_ceiling", "a budget denial needs remaining below the ceiling (7.6)"


def _window_ok(start: str | None, end: str | None) -> bool:
    return start is None or end is None or instant(start) < instant(end)


def _price_book_rules(doc: dict[str, Any]) -> Iterator[tuple[str, str]]:
    spans: dict[tuple[str, str], list[tuple[str, str | None]]] = {}
    for entry in doc.get("entries", []):
        name = entry.get("entry_id")
        if not _window_ok(entry.get("effective_from"), entry.get("effective_until")):
            yield "windows_ordered", f"{name}: effective_until is not after effective_from"
        spans.setdefault((entry.get("provider"), entry.get("model")), []).append(
            (entry.get("effective_from"), entry.get("effective_until"))
        )
        fee_ids, promotions = [], []
        for rule in entry.get("rules", []):
            kind = rule.get("type")
            if kind in ("speed_or_service_tier", "geography"):
                values = [v.get("value") for v in rule.get("values", [])]
                if len(values) != len(set(values)):
                    yield "dimension_values_distinct", f"{name}: {rule.get('dimension')} lists a value twice"
                if rule.get("default_value") is not None and rule["default_value"] not in values:
                    yield "default_in_values", f"{name}: {rule.get('dimension')} default {rule['default_value']} is not priced"
            elif kind == "per_use_fee":
                fee_ids.append(rule.get("fee_id"))
            elif kind == "promotion":
                if not _window_ok(rule.get("starts_at"), rule.get("ends_at")):
                    yield "windows_ordered", f"{name}: a promotion ends before it starts"
                promotions.append((rule.get("starts_at"), rule.get("ends_at")))
            elif kind == "negotiated" and not _window_ok(rule.get("effective_from"), rule.get("effective_until")):
                yield "windows_ordered", f"{name}: a negotiated rate ends before it starts"
        if len(fee_ids) != len(set(fee_ids)):
            yield "fee_ids_distinct", f"{name}: a fee_id appears twice"
        if _overlapping(promotions):
            yield "promotions_no_overlap", f"{name}: promotion windows overlap"
    for (provider, model), windows in spans.items():
        if _overlapping(windows):
            yield "entries_no_overlap", f"entries for {provider} {model} overlap in time"


def _overlapping(windows: list[tuple[str, str | None]]) -> bool:
    ordered = sorted((instant(s), instant(e) if e else None) for s, e in windows if s)
    return any(end is None or end > nxt for (_, end), (nxt, _) in zip(ordered, ordered[1:]))


SEMANTIC_RULES = {
    "attempt": _attempt_rules,
    "usage-event": _usage_event_rules,
    "error-envelope": _error_envelope_rules,
    "price-book": _price_book_rules,
}


def semantic_problems(target: str, doc: Any) -> list[tuple[str, str]]:
    rules = SEMANTIC_RULES.get(target)
    if rules is None or not isinstance(doc, dict):
        return []
    try:
        return list(rules(doc))
    except (KeyError, TypeError, AttributeError, ValueError) as exc:
        return [("malformed", f"cannot apply cross-field rules: {exc!r}")]


# --- 4. No floats anywhere ----------------------------------------------------------


def check_json_hygiene(skip: set[Path], failures: Failures) -> int:
    count = 0
    for path in sorted(CONTRACTS.rglob("*.json")):
        if path in skip:
            continue
        count += 1
        try:
            load_json(path)
        except (ContractJSONError, json.JSONDecodeError) as exc:
            failures.add(rel(path), str(exc))
    return count


# --- 5. No free text -----------------------------------------------------------------


class FreeTextLint:
    """Walks every subschema reachable from a root and flags any place free text could go."""

    def __init__(self, documents: dict[str, dict[str, Any]], common_id: str) -> None:
        self.documents = documents  # by $id
        self.allowed = {(common_id, f"/$defs/{name}") for name in FORMAT_ALLOWLIST}
        self.problems: list[str] = []
        self.seen: set[tuple[str, str, str]] = set()

    def resolve(self, ref: str, base: str) -> tuple[str, str, Any]:
        uri, _, fragment = ref.partition("#")
        uri = uri or base
        node: Any = self.documents[uri]
        for part in [p for p in fragment.split("/") if p]:
            node = node[part.replace("~1", "/").replace("~0", "~")]
        return uri, fragment, node

    def visit(self, node: Any, uri: str, ptr: str, value: bool) -> None:
        """value: this subschema alone must describe a value (not merely constrain one)."""
        key = (uri, ptr, "v" if value else "c")
        if key in self.seen:
            return
        self.seen.add(key)
        where = f"{uri}#{ptr}"
        if node is True or node == {}:
            if value:
                self.problems.append(f"{where}: accepts any value")
            return
        if node is False or not isinstance(node, dict):
            return
        if (uri, ptr) in self.allowed:
            return
        ref_allowed = False
        if "$ref" in node:
            target = self.resolve(node["$ref"], uri)
            ref_allowed = (target[0], target[1]) in self.allowed
            self.visit(target[2], target[0], target[1], value)
        types = node.get("type", [])
        types = [types] if isinstance(types, str) else types
        fixed = "const" in node or "enum" in node
        if "string" in types and not fixed and not ref_allowed:
            self.problems.append(f"{where}: string that is not a const, an enum, or an allowlisted format")
        if "array" in types and "items" not in node and "prefixItems" not in node:
            self.problems.append(f"{where}: array without items")
        if "object" in types:
            if node.get("additionalProperties") is not False and node.get("unevaluatedProperties") is not False:
                self.problems.append(f"{where}: object is not closed (additionalProperties or unevaluatedProperties false)")
            if "patternProperties" in node or isinstance(node.get("additionalProperties"), dict):
                self.problems.append(f"{where}: object keys are open (patternProperties or an additionalProperties schema)")
        combinators = [k for k in ("oneOf", "anyOf", "allOf") if k in node]
        if value and not (types or fixed or "$ref" in node or combinators):
            self.problems.append(f"{where}: no type, const, enum, $ref, or combinator")
        own = bool(types or fixed or "$ref" in node)
        # Members of a node that declares an object (or array) must each describe a
        # value. Members of an untyped node only constrain one declared elsewhere,
        # such as an if/then rule; their strings are still checked.
        for name, sub in node.get("properties", {}).items():
            self.visit(sub, uri, f"{ptr}/properties/{name}", "object" in types)
        if "items" in node:
            self.visit(node["items"], uri, f"{ptr}/items", "array" in types)
        for i, sub in enumerate(node.get("prefixItems", [])):
            self.visit(sub, uri, f"{ptr}/prefixItems/{i}", "array" in types)
        if "propertyNames" in node:
            self.visit(node["propertyNames"], uri, f"{ptr}/propertyNames", True)
        if "contains" in node:
            self.visit(node["contains"], uri, f"{ptr}/contains", False)
        for k in ("oneOf", "anyOf"):
            for i, sub in enumerate(node.get(k, [])):
                self.visit(sub, uri, f"{ptr}/{k}/{i}", value and not own)
        for i, sub in enumerate(node.get("allOf", [])):
            self.visit(sub, uri, f"{ptr}/allOf/{i}", value and not own and len(node["allOf"]) == 1)
        for k in ("if", "then", "else"):
            if k in node:
                self.visit(node[k], uri, f"{ptr}/{k}", False)
        for name, sub in node.get("dependentSchemas", {}).items():
            self.visit(sub, uri, f"{ptr}/dependentSchemas/{name}", False)

    def check_formats(self) -> None:
        for uri, ptr in sorted(self.allowed):
            _, _, node = self.resolve(f"{uri}#{ptr}", uri)
            where = f"{uri}#{ptr}"
            if node.get("type") != "string" or "pattern" not in node or "maxLength" not in node:
                self.problems.append(f"{where}: allowlisted format needs type string, pattern, and maxLength")
                continue
            compiled = re.compile(node["pattern"])
            for probe in FREE_TEXT_PROBES:
                if compiled.search(probe) and len(probe) <= node["maxLength"]:
                    self.problems.append(f"{where}: allowlisted format accepts {probe!r}")


def lint_free_text(schemas: dict[str, dict[str, Any]], failures: Failures) -> None:
    documents = {s["$id"]: s for s in schemas.values() if "$id" in s}
    common_id = schemas.get(COMMON, {}).get("$id", "")

    # Self-test: the lint must catch free text, open objects, and untyped values.
    bad_id = "urn:purser:contracts:lint-self-test:0.0.0"
    bad = {
        "$id": bad_id,
        "type": "object",
        "properties": {
            "note": {"type": "string", "maxLength": 200},
            "label": {"type": "string", "pattern": "^[A-Za-z ]{1,64}$"},
            "extra": {"type": "object", "properties": {}},
            "anything": {},
        },
        "additionalProperties": False,
    }
    probe = FreeTextLint({**documents, bad_id: bad}, common_id)
    probe.visit(bad, bad_id, "", True)
    caught = {p.split("#", 1)[1].split(":", 1)[0] for p in probe.problems}
    for want in ("/properties/note", "/properties/label", "/properties/extra", "/properties/anything"):
        if want not in caught:
            failures.add("check_contracts.py", f"no-free-text self-test did not flag {want}")

    lint = FreeTextLint(documents, common_id)
    if common_id:
        lint.check_formats()
    for name in NO_FREE_TEXT:
        schema = schemas.get(name)
        if schema is None:
            failures.add(rel(SCHEMAS), f"{name}.schema.json is missing")
            continue
        lint.visit(schema, schema["$id"], "", True)
    for problem in lint.problems:
        failures.add("no-free-text", problem)


# --- 6. Denials OpenCode won't retry ------------------------------------------------------


def check_denial_bodies(failures: Failures) -> None:
    for path in sorted((EXAMPLES / "error-envelope" / "valid").glob("*.json")):
        doc = load_json(path)
        if doc.get("status") in (400, 403):
            body = json.dumps(doc.get("body"), separators=(",", ":"))
            hit = OPENCODE_RETRY_TRIGGERS.search(body)
            if hit:
                failures.add(rel(path), f"non-retryable denial body contains {hit.group(0)!r}, which OpenCode retries")


# --- 7. Pricing fixtures --------------------------------------------------------------------


def iter_pricing_fixtures() -> Iterator[Path]:
    yield from sorted((FIXTURES / "pricing").glob("*.json"))


def check_pricing_fixtures(
    schemas: dict[str, dict[str, Any]], registry: Registry, failures: Failures
) -> int:
    count = 0
    books: dict[str, Any] = {}
    fixtures = []
    for path in iter_pricing_fixtures():
        count += 1
        doc = load_json(path)
        kind = "price-book" if path.name.startswith("price-book") else "price-fixture"
        validator = validator_for(kind, schemas, registry)
        if validator is None:
            failures.add(rel(path), f"schema {kind} is missing")
            continue
        for error in validator.iter_errors(doc):
            failures.add(rel(path), f"does not match {kind}: {describe(error)}")
        for rule, message in semantic_problems(kind, doc):
            failures.add(rel(path), f"breaks {rule}: {message}")
        if kind == "price-book":
            books[path.name] = doc
        else:
            fixtures.append((path, doc))
    anchor_seen = False
    for path, doc in fixtures:
        ref = doc.get("price_book", {})
        book = books.get(Path(ref.get("path", "")).name)
        if book is None:
            failures.add(rel(path), f"price book {ref.get('path')} not found in fixtures/pricing/")
        else:
            if book.get("price_book_version") != ref.get("price_book_version"):
                failures.add(rel(path), "price_book_version does not match the referenced book")
            if ref.get("entry_id") not in {e.get("entry_id") for e in book.get("entries", [])}:
                failures.add(rel(path), f"entry {ref.get('entry_id')} not in the referenced book")
        expected = doc.get("expected", {})
        if expected.get("priced") is True:
            costs = [Decimal(c["cost_usd"]) for c in expected.get("classes", {}).values()]
            costs += [Decimal(f["cost_usd"]) for f in expected.get("fees", [])]
            total = Decimal(expected["total_usd"])
            if sum(costs, Decimal(0)) != total:
                failures.add(rel(path), f"class and fee costs sum to {sum(costs, Decimal(0))}, not total_usd {total}")
            nd = (total * 10**9).quantize(Decimal(1), rounding=ROUND_HALF_UP)
            if nd != expected["total_nanodollars"]:
                failures.add(rel(path), f"total_usd {total} rounds to {nd} nd, not {expected['total_nanodollars']}")
        if doc.get("fixture_id") == ANCHOR_FIXTURE[0]:
            anchor_seen = True
            if expected.get("total_nanodollars") != ANCHOR_FIXTURE[1]:
                failures.add(rel(path), f"must total {ANCHOR_FIXTURE[1]} nd ($0.209, architecture 6.3)")
    if not anchor_seen:
        failures.add(rel(FIXTURES / "pricing"), f"fixture {ANCHOR_FIXTURE[0]} ($0.209, M5) is missing")
    return count


# --- main -------------------------------------------------------------------------------


def main() -> int:
    failures = Failures()
    schemas = load_schemas(failures)
    registry = make_registry(schemas)
    print(f"schemas: {len(schemas)} loaded")
    check_readme(schemas, failures)
    examples, parse_failures = check_examples(schemas, registry, failures)
    print(f"examples: {examples} checked")
    hygiene = check_json_hygiene(parse_failures, failures)
    print(f"json hygiene: {hygiene} files")
    lint_free_text(schemas, failures)
    print(f"no-free-text lint: {', '.join(NO_FREE_TEXT)}")
    check_denial_bodies(failures)
    fixtures = check_pricing_fixtures(schemas, registry, failures)
    print(f"pricing fixtures: {fixtures} checked")
    if failures.items:
        print(f"\nFAILED: {len(failures.items)} problem(s)", file=sys.stderr)
        for item in failures.items:
            print(f"  {item}", file=sys.stderr)
        return 1
    print("\ncontracts: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
