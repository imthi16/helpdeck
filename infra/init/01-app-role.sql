-- Dev-only: provision the login role the API connects as (helpdeck_app).
--
-- The RLS migration (apps/api/alembic/.../row_level_security.py) deliberately
-- never handles credentials, so it cannot leak a password into migration SQL.
-- Instead the login/password is provisioned out of band: this script for local
-- docker-compose, deployment secret management in prod, a conftest fixture in
-- tests. The migration then only normalizes privilege flags and grants CRUD on
-- the tenant tables.
--
-- NOTE: docker-entrypoint-initdb.d scripts run ONLY when the data volume is
-- first initialized. On an existing volume, run this by hand or recreate the
-- volume (docker compose down -v).
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'helpdeck_app') THEN
    CREATE ROLE helpdeck_app LOGIN PASSWORD 'helpdeck_app'
      NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
  ELSE
    ALTER ROLE helpdeck_app LOGIN PASSWORD 'helpdeck_app'
      NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
  END IF;
END
$$;
