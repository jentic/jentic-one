-- Perf-stack Postgres bootstrap: the same schemas + per-plane roles as
-- docker/local-setup/init-schemas.sql, plus pg_stat_statements for the
-- per-call DB-cost measurement. Each plane connects as its own role, so
-- statements are attributed per plane by joining pg_stat_statements.userid to
-- pg_roles. Runs once, on first volume init (`make perf-down` wipes it).

CREATE EXTENSION IF NOT EXISTS pg_stat_statements;

CREATE SCHEMA IF NOT EXISTS registry;
CREATE SCHEMA IF NOT EXISTS control;
CREATE SCHEMA IF NOT EXISTS admin;

CREATE ROLE registry_user LOGIN PASSWORD 'registry_pass';
CREATE ROLE control_user LOGIN PASSWORD 'control_pass';
CREATE ROLE admin_user LOGIN PASSWORD 'admin_pass';

GRANT USAGE, CREATE ON SCHEMA registry TO registry_user;
GRANT USAGE, CREATE ON SCHEMA control TO control_user;
GRANT USAGE, CREATE ON SCHEMA admin TO admin_user;

ALTER DEFAULT PRIVILEGES IN SCHEMA registry GRANT ALL ON TABLES TO registry_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA registry GRANT ALL ON SEQUENCES TO registry_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA registry GRANT ALL ON TYPES TO registry_user;

ALTER DEFAULT PRIVILEGES IN SCHEMA control GRANT ALL ON TABLES TO control_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA control GRANT ALL ON SEQUENCES TO control_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA control GRANT ALL ON TYPES TO control_user;

ALTER DEFAULT PRIVILEGES IN SCHEMA admin GRANT ALL ON TABLES TO admin_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA admin GRANT ALL ON SEQUENCES TO admin_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA admin GRANT ALL ON TYPES TO admin_user;
