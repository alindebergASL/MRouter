-- Control-plane roles and schema in the dev Postgres (row-level security, A5).
-- Idempotent: run by the controlplane-db-init service on every `make dev-up`,
-- and by the control plane's tests against each throwaway test database.
-- Production creates the same roles through infrastructure code, with
-- passwords from AWS Secrets Manager.
-- Development-only credentials. Not secrets; never reuse them anywhere else.
--
--   purser_cp_owner    owns the schema and its tables; runs migrations and the
--                      bootstrap CLI. Not used by the API.
--   purser_cp_app      the API's login role: not an owner, NOBYPASSRLS, so every
--                      org-scoped query passes the row-level security policies.
--   purser_cp_sweeper  the pending-row sweeper's login role: NOBYPASSRLS; its
--                      policies see pending rows only.

DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'purser_cp_owner') THEN
    CREATE ROLE purser_cp_owner LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS
      PASSWORD 'cp-owner-dev-only-not-a-secret';
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'purser_cp_app') THEN
    CREATE ROLE purser_cp_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS
      PASSWORD 'cp-app-dev-only-not-a-secret';
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'purser_cp_sweeper') THEN
    CREATE ROLE purser_cp_sweeper LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS
      PASSWORD 'cp-sweeper-dev-only-not-a-secret';
  END IF;
END
$$;

-- keycloak-db-init revokes PUBLIC's CONNECT on purser_dev; only these roles get it.
GRANT CONNECT ON DATABASE purser_dev TO purser_cp_owner, purser_cp_app, purser_cp_sweeper;

CREATE SCHEMA IF NOT EXISTS controlplane AUTHORIZATION purser_cp_owner;
REVOKE ALL ON SCHEMA controlplane FROM PUBLIC;
GRANT USAGE ON SCHEMA controlplane TO purser_cp_app, purser_cp_sweeper;
