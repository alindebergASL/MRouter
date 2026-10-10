# console

Owner: Lane D. Guarantees: A1.
For now, only the TypeScript client generated from the admin API's OpenAPI document; the
Next.js console (task 4) is built on it. Read the root `CLAUDE.md` first.

## Commands
Run from the repository root; Node 22 runs in the pinned `console-tools` container.

- Build: `make console-client` (regenerate `src/client` from `controlplane/openapi/admin-api.json`).
- Test: `make console-client-check` (fails if the committed client differs from a fresh
  generation; then type-checks it). `make cp-openapi-check` does the same for the document.
- Lint: the generated client is not hand-edited or linted; `tsc --noEmit` runs in the check.
- Format: none yet (arrives with the console UI).

## Rules
- Never edit `src/client/` by hand: change the API, run `make cp-openapi` and
  `make console-client`, and commit all three together.
- The generator (`@hey-api/openapi-ts`) and TypeScript are pinned exactly in `package.json`, and
  `package-lock.json` carries integrity hashes. TypeScript stays on the 6.x line: the generator
  uses the JavaScript compiler API, which TypeScript 7 doesn't ship.
