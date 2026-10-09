# contracts/ (protected)

The spec in machine-readable form. Every other component builds against what is here.

Planned for v0 (Lane A, build plan §7 task 2):

- OpenAPI 3.1 description of the admin API (A1).
- JSON Schemas: usage event, attempt, decision record, price book, trace event, error envelopes
  (Anthropic and OpenAI shapes).
- Golden fixtures and examples for each schema.

**Protected path.** Change it only in a spec-change session (`ROUTER_SPEC_CHANGE=1`) through a pull request labeled
`spec-change`. See `CLAUDE.md` and build plan §2.2.
