-- Bootstrap schemas and per-surface roles for the isolated upgrade-test
-- PostgreSQL instance. Executed once when the compose volume is initialized.

CREATE SCHEMA IF NOT EXISTS registry;
CREATE SCHEMA IF NOT EXISTS control;
CREATE SCHEMA IF NOT EXISTS admin;

CREATE ROLE registry_user LOGIN PASSWORD 'registry_pass';  -- pragma: allowlist secret
CREATE ROLE control_user LOGIN PASSWORD 'control_pass';    -- pragma: allowlist secret
CREATE ROLE admin_user LOGIN PASSWORD 'admin_pass';        -- pragma: allowlist secret

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
