# 0001. Record architecture decisions

- Status: Accepted
- Date: 2026-10-09
- Deciders: Andrew Lindeberg
- Guarantees affected: none

## Context

Purser is built mostly by parallel Claude Code sessions, with one human reviewer. Sessions
start without the history of the decisions behind the code, and the spec
(`docs/architecture.md`) says *what* v1 guarantees but not every *why* behind implementation
choices. Build plan §3 reserves `docs/adr/` for "one decision record per significant choice",
and several open decisions (auth service, bake-off winner, hosted orchestration) are to be
settled by an ADR.

## Decision

Record each significant decision as an Architecture Decision Record in `docs/adr/`, in the
short form of Michael Nygard's template (`template.md`): context, decision, consequences.

- Files are numbered `NNNN-kebab-title.md`, in order, and never renumbered.
- A decision is significant if a later session could reasonably undo it without knowing why
  it was made: technology and version choices, packaging, security boundaries, and anything
  that changes how a guarantee is met.
- An ADR is accepted when Andrew merges it. Changing a decision means a new ADR that
  supersedes the old one; the old one stays, with its status updated.
- An ADR may not change a guarantee. That needs a spec change to `docs/architecture.md`
  (a `spec-change` pull request).

## Consequences

- New sessions can learn why things are as they are by reading `docs/adr/`.
- Small overhead per decision; keep each record to a page.
- The index in `docs/adr/README.md` is updated with each new record.
