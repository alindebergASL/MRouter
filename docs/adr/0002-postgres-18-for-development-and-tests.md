# 0002. Postgres 18 for development and tests

- Status: Accepted
- Date: 2026-10-09
- Deciders: Andrew Lindeberg
- Guarantees affected: B1, B2, B5, P1, M1 (their tests run against this database)

## Context

Production Postgres is Amazon RDS for PostgreSQL (architecture §9), which has supported
PostgreSQL 18 since November 2025. The ledger's transaction rules (§7.6: row locks in
ascending ID order, idempotent settlement, fail-closed behavior in B5) are exercised by
property tests that must behave as they will in production, so development, tests, and CI
need the same major version, decided in one place (build plan §2.8).

Claude Code cloud VMs ship PostgreSQL 16, and laptops may have any version installed. Docker
Hub rate-limits anonymous pulls, which would make CI and cloud sessions flaky.

## Decision

- Development, tests, and CI use Postgres **18** in a container from the Compose dev stack
  (`deploy/compose/dev.yaml`), never a host-installed Postgres.
- The image is `public.ecr.aws/docker/library/postgres:18.6-trixie`, pinned by the digest of
  its multi-architecture index (`linux/amd64` and `linux/arm64`, so Andrew's Mac and CI run the
  same image), from Amazon's public ECR mirror of the official image.
- It listens on `127.0.0.1:55432` only, with development-only credentials that are not
  secrets. Its data volume is mounted at `/var/lib/postgresql`, the layout the Postgres 18
  images use.
- `scripts/cloud-setup.sh` pre-pulls the same reference, and CI's `make check-pins` keeps the
  two in step.

## Consequences

- One version across laptops, cloud sessions, CI, and production.
- Minor-version and security updates are deliberate: a pull request that bumps the tag and
  digest in `dev.yaml` and `cloud-setup.sh` together. Bump to match the RDS minor version in use
  once production exists.
- Moving to Postgres 19 is a new ADR that supersedes this one, made when RDS supports it and
  the ledger tests pass on it.
- The VM's built-in PostgreSQL 16 is never used; nothing should connect to port 5432.
