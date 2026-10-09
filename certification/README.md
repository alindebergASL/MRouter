# certification/

The certification lab: recorder (a recording proxy that strips credential headers before writing),
replayer, mock providers, and the H1 scenario catalog. Each runs as a container in the Compose
stack.

Recordings contain real prompts and code. They live in a private bucket and are **never committed**;
only manifests (scenario, harness version, hashes) go in git (build plan §2.7).

Owner: Lane A. Guarantees: H1, H2 (test side), M1 and B2 (mock attempt log), B1 (random billing).
Nothing here yet.
