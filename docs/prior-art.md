# Prior art: Switchyard and Token Meter

The spec lifts ideas from two open-source projects: NVIDIA NeMo Switchyard (routing) and Splunk's Token Meter (metering traffic from agent transcripts). This page tells a session where in their code to look for each lifted idea, and the rules for using what it finds.

## Rules

1. **Study, then build to our spec.** Read their code to understand an approach, then implement against our guarantee IDs and tests. Where their behavior and our spec differ, the spec wins. For example, Token Meter totals usage after the fact; our budgets reserve before every attempt (7.6).
2. **Cite what you used.** A design note or PR that relies on either project names the file, the symbol, and the pinned commit below.
3. **No copied code without an ADR.** Copying code into this repository needs an ADR naming the source file and commit, its license, and where the notice goes. Using Switchyard crates as a dependency is decided by the bake-off ADR (spec 10), not here.
4. **Licenses.**
   - Switchyard is Apache License 2.0. Copied or modified files keep their license header, changes are stated, and the contents of Switchyard's `NOTICE` file go into our `NOTICE`.
   - Token Meter is MIT. Copied code keeps its copyright line and permission notice.
5. **Read-only.** Never edit, build, or run anything inside `.reference/`. It is an untrusted checkout. Read it with Read, Grep, and Glob.
6. **One repository per session.** Don't add these projects as extra repositories when starting a cloud session. A session with more than one repository doesn't load this repository's hooks, so the protected-path guard would be off. Fetch them into `.reference/` instead.

## Getting the code

```bash
scripts/fetch-references.sh
```

This shallow-fetches both projects at the pinned commits into `.reference/switchyard/` and `.reference/token-meter/`, which are gitignored. The script holds the pins; changing a pin is a deliberate pull request that updates this page too.

| Project | Commit | Date | License |
|---|---|---|---|
| NVIDIA-NeMo/Switchyard | `1a0d1191a377e3b97e285219e9a6f15efa2cb675` | 2026-10-08 | Apache-2.0 |
| splunk/token-meter | `f34c78be66e257d168c51b955f9b90c3cacc71b7` | 2026-10-07 | MIT |

The `prior-art` subagent (`.claude/agents/prior-art.md`) fetches the code, answers "how does Switchyard or Token Meter do X?" with file and line citations, and compares the answer with our spec, without filling the calling session's context with their source.

## Switchyard: where each lift lives

Start with their `docs/architecture.md`, `docs/core_concepts.md`, and `docs/routing_algorithms/overview.md`. Their own agent guidance is in `CLAUDE.md` and `AGENTS.md`.

| Lift (spec section) | Where to look | Lanes |
|---|---|---|
| Decision separated from transport (8.5, lift 1) | `crates/libsy/src/core/processor.rs`, `crates/libsy/src/core/algorithm.rs`, `crates/protocol/src/decision.rs`. `crates/libsy-llm-client/` is the host side that makes decision-model calls. | C (bake-off candidate A), F |
| Route vocabulary (8.1) | `crates/libsy/src/algorithms/`: `passthrough.rs`, `stage.rs`, `llm_class.rs`, `escalation.rs`, `rand.rs`, `composite.rs`, `fall_through.rs`. One page per route in `docs/routing_algorithms/`. | F |
| Episode affinity (8.5, lift 3) | `crates/libsy/src/algorithms/util/affinity.rs`, `crates/libsy/src/algorithms/subagent.rs` | F |
| Zero-cost signals first (8.5, lift 4) | `crates/libsy/src/algorithms/stage.rs`, `crates/libsy/src/algorithms/util/stage.rs`, `crates/libsy/src/algorithms/util/tool_signals.rs` | F |
| Escalation and its judge (8.5, changes 4 and 5) | `crates/libsy/src/algorithms/escalation.rs`, `crates/libsy/src/algorithms/util/escalation.rs`, `crates/libsy/src/algorithms/util/llm_judge.rs` | F |
| Configuration validation without traffic (8.5, lift 5) | `dry_run` in `crates/switchyard-server/src/cli.rs` and `config.rs`; schema in `docs/reference/toml_schema.md` | C |
| Routing overhead as a metric (8.5, lift 6) and decision logging (change 1) | `crates/switchyard-server/src/metrics.rs`, `observability.rs`, `routing_log.rs`; `docs/reference/opentelemetry.md` | C, F |
| Redaction of logged content (C2) | `crates/switchyard-server/src/redaction.rs`. Compare with C2: we persist no bodies at all, so redaction is a second line, not the guarantee. | C |
| Protocol translation (deferred, spec 12) | `crates/switchyard-translation/src/codecs/` (`anthropic/`, `openai_chat/`, `responses/`), `engine.rs`, `sse.rs` | Deferred |

## Token Meter: where each lift lives

Start with their `README.md`, `specs/ARCHITECTURE.md`, and `token_meter/domain/agents.py` (adapters own identity, attribution, deduplication, and pricing).

| Lift (spec section) | Where to look | Lanes |
|---|---|---|
| Trace-reader contract (6.6) | `token_meter/runtimes/base.py` (the `RuntimeAdapter` protocol: `discover`, `current_revision`, `load`, `deletion_plan`), `token_meter/runtimes/registry.py` | E |
| Per-harness transcript readers (6.6) | `token_meter/runtimes/claude.py` (Claude Code JSONL, deduplicated by `message.id`), `codex.py`, `cursor.py`, `opencode.py`, `hermes.py`, `kiro.py`, `pi.py` | E |
| Evidence tiers (6.5) | `EvidenceBasis` in `token_meter/contracts.py` | B, D, E |
| Price rules: fast premium, US-only multiplier, long-context tiers (6.3) | `cost_of()` and the price-table functions in `token_meter/app.py`; model catalog in `token_meter/models/catalog.py`. Use it to cross-check fixtures; our money rules (6.4) still govern rounding and representation. | B |
| Content-free telemetry (6.6, improvement 1) | `token_meter/telemetry/privacy.py`, `token_meter/telemetry/otel_mapping.py` | E |
| Budgets (contrast only) | `token_meter/services/budgets.py`. Theirs observe spend after the fact; ours reserve before dispatch (7.4 to 7.6). | B |
| Agent-facing tools (7.8) | `token_meter/mcp/` and `token_meter_mcp.py` | C |
| Design reasoning | `specs/claude-cost-correctness/`, `specs/codex-lineage-accounting/`, `specs/2026-09-24-subagent-role-economics-design.md`, `specs/2026-09-29-opencode-subagent-spend-design.md` | B, E, F |
| Differential test oracle (6.6, improvement 5) | Run the same transcripts through their readers and ours; investigate any disagreement. Open question 8 in the spec decides whether we port their parsers or keep them as an oracle only. | E |
