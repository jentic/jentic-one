-- Create schemas and per-surface roles for the local PostgreSQL instance.
-- The postgres entrypoint runs it on first boot, `make start-fixtures`
-- (scripts/setup.sh) runs it again in `jentic` on every start, and the test
-- harness runs it in every test template it builds (tests/harness/postgres.py).
-- It must stay idempotent.
--
-- Schemas and grants belong to each database. Roles belong to the server and
-- are shared by every checkout using it: one is created only when missing, so
-- changing a password or attribute here does not reach an existing volume, and
-- the test harness checks the roles still log in. Rerunning the file only adds:
-- a grant removed from it stays in an existing `jentic` until the volume is
-- recreated (`make destroy-fixtures && make start-fixtures`).

CREATE SCHEMA IF NOT EXISTS registry;
CREATE SCHEMA IF NOT EXISTS control;
CREATE SCHEMA IF NOT EXISTS admin;

-- Per-surface roles (passwords are static; local-dev only).
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'registry_user') THEN
        CREATE ROLE registry_user LOGIN PASSWORD 'registry_pass';
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'control_user') THEN
        CREATE ROLE control_user LOGIN PASSWORD 'control_pass';
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'admin_user') THEN
        CREATE ROLE admin_user LOGIN PASSWORD 'admin_pass';
    END IF;
END
$$;

-- Schema-scoped privileges: each role owns its schema's objects.
GRANT USAGE, CREATE ON SCHEMA registry TO registry_user;
GRANT USAGE, CREATE ON SCHEMA control TO control_user;
GRANT USAGE, CREATE ON SCHEMA admin TO admin_user;

-- Default privileges so tables/sequences/types created by each role are
-- fully accessible to that role (needed for Alembic migrations).
ALTER DEFAULT PRIVILEGES IN SCHEMA registry GRANT ALL ON TABLES TO registry_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA registry GRANT ALL ON SEQUENCES TO registry_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA registry GRANT ALL ON TYPES TO registry_user;

ALTER DEFAULT PRIVILEGES IN SCHEMA control GRANT ALL ON TABLES TO control_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA control GRANT ALL ON SEQUENCES TO control_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA control GRANT ALL ON TYPES TO control_user;

ALTER DEFAULT PRIVILEGES IN SCHEMA admin GRANT ALL ON TABLES TO admin_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA admin GRANT ALL ON SEQUENCES TO admin_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA admin GRANT ALL ON TYPES TO admin_user;

-- ── Adding an extra schema that references these tables ─────────────────────
-- A downstream deployment can add its own isolated schema + role alongside the
-- ones above. If that schema's tables declare foreign keys into these tables,
-- the role needs USAGE on the referenced schema and REFERENCES on the target
-- tables — without them the FK creation fails with a permission error. The base
-- local-dev setup does not create any such schema (so the volume never needs
-- recreating for the default setup); wire the grants into that deployment's own
-- init script / DB-setup chart. Example shape:
--
--   CREATE SCHEMA IF NOT EXISTS extra;
--   CREATE ROLE extra_user LOGIN PASSWORD 'extra_pass';
--   GRANT USAGE, CREATE ON SCHEMA extra TO extra_user;
--   -- Grant on each referenced schema the extra tables FK into:
--   GRANT USAGE ON SCHEMA registry TO extra_user;
--   GRANT REFERENCES ON ALL TABLES IN SCHEMA registry TO extra_user;
--   ALTER DEFAULT PRIVILEGES IN SCHEMA registry
--       GRANT REFERENCES ON TABLES TO extra_user;
--   -- (repeat the three grants above for `control` / `admin` as needed)

