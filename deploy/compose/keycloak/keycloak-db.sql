-- Keycloak's own role and database in the dev Postgres (ADR 0003).
-- Idempotent: run by the keycloak-db-init service on every `make dev-up`, so it
-- also works on a pgdata volume created before Keycloak joined the stack.
-- Development-only credentials. Not secrets; never reuse them anywhere else.

DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'keycloak_dev') THEN
    CREATE ROLE keycloak_dev LOGIN PASSWORD 'keycloak-db-dev-only-not-a-secret';
  END IF;
END
$$;

SELECT 'CREATE DATABASE keycloak OWNER keycloak_dev'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'keycloak')
\gexec

REVOKE ALL ON DATABASE keycloak FROM PUBLIC;
