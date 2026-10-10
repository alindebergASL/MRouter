# Lane C: Data plane — STATUS

Updated: 2026-10-09 by the spec draft 0.6 session (spec-change)
Scope: The bake-off; then relay and sidecar (build plan §5).

## Done
- Nothing yet.

## Next
1. Bake-off harness for candidates A, B, and C (build plan §7, Lane C task 1).
2. When the relay is built: its side of A5 (spec draft 0.6, §4). Relay endpoints refuse org A's
   credentials (device token, mTLS certificate, virtual key) used with org B's identifiers, and
   reject relay tokens and device certificates from another install. Credential lookups go
   through the control plane's `SECURITY DEFINER` functions before an org is set.

## Blocked
- Nothing.

## Guarantee tests passing

| ID | Test | Status |
|---|---|---|
| — | No guarantee tests yet. | — |
