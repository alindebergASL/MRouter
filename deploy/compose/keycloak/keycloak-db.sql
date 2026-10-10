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

-- Each database admits only its own roles: Keycloak's role can't connect to
-- purser_dev, and other roles can't connect to keycloak. Roles that need
-- purser_dev get CONNECT explicitly.
REVOKE ALL ON DATABASE keycloak FROM PUBLIC;
REVOKE CONNECT, TEMPORARY ON DATABASE purser_dev FROM PUBLIC;
