# Context & Configuration

## Overview

The `jentic_one.shared` package provides the foundational configuration and context system. All modules (broker, control) consume these shared components.

## Configuration

Configuration is loaded via `load_config()` which merges two sources in priority order:

1. **YAML file** — resolved as: explicit `path` argument > `JENTIC_CONFIG_FILE` env var > `./jentic-one.yaml`
2. **Environment variables** — convention: `JENTIC__SECTION__KEY=value` (double-underscore separated, uppercased)

Environment variables override file values. Types are coerced automatically by pydantic (booleans, ints, floats).

### Minimal config file

```yaml
databases:
  registry:
    name: registry_db
  admin:
    name: admin_db
  control:
    name: control_db
```

All other fields have sensible defaults (localhost:5432, pool sizes, etc.).

### Secret handling

Database passwords use `pydantic.SecretStr` — they are automatically redacted in logs, repr, and serialization. Access the raw value only via `.get_secret_value()`.

## Context

`Context` is the central object that holds the resolved config and manages database engines/sessions.

```python
from sqlalchemy import text

from jentic_one.shared import Context, load_config

config = load_config(Path("jentic-one.yaml"))

async with Context(config) as ctx:
    async with ctx.registry_db.session() as session:
        result = await session.execute(text("SELECT 1"))
```

### Database properties

- `ctx.registry_db` — SQLAlchemy session manager for the registry schema
- `ctx.admin_db` — SQLAlchemy session manager for the admin schema
- `ctx.control_db` — SQLAlchemy session manager for the control schema

Each property returns a `DatabaseSession` instance with:
- `.engine` — the underlying `AsyncEngine`
- `.session_factory` — the `async_sessionmaker` bound to the engine
- `.session()` — async context manager yielding an `AsyncSession`

### Lifecycle

- `await ctx.startup()` — creates engines and session factories
- `await ctx.shutdown()` — disposes all engines gracefully
- Or use `async with Context(config) as ctx:` which handles both

## Database Layer

The database layer uses **SQLAlchemy async** (with `asyncpg` as the underlying driver), following the same pattern as `jentic/core`:

- `Base` — declarative base class for all ORM models (import from `jentic_one.shared.db`)
- `DatabaseSession` — manages an async engine and session factory per database
- `get_database_url(config)` — builds a `sqlalchemy.engine.URL` for `postgresql+asyncpg` from a `DatabaseConfig`

### ORM Models

Define models by subclassing the base for the target database (`RegistryBase`, `ControlBase`, or `AdminBase`):

```python
from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from jentic_one.shared.db.base import RegistryBase  # or ControlBase, AdminBase


class MyModel(RegistryBase):
    __tablename__ = "my_table"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
```

See the ORM model definitions under `src/jentic_one/*/repos/` for full conventions and the per-database entity breakdown.

## Migrations (Alembic)

Alembic is configured for async multi-database migrations. Each database has its own named section.

### Running migrations

```bash
uv run alembic -n registry upgrade head
uv run alembic -n control upgrade head
uv run alembic -n admin upgrade head
```

### Creating a new migration

```bash
uv run alembic -n <db_name> revision --autogenerate -m "description of change"
```

Autogenerate compares the target database's base metadata (e.g. `RegistryBase.metadata`) against the live schema. All ORM models must be imported before Alembic runs — place models in packages imported by the migration env.

### Configuration

- `alembic.ini` — multi-database config with `[registry]`, `[control]`, `[admin]` sections
- `src/jentic_one/migrations/env.py` — shared async env that resolves the active section to the correct database URL and metadata
- `src/jentic_one/migrations/{registry,control,admin}/versions/` — per-database migration scripts

## Runtime configuration

`AppConfig.runtime` holds hot-reloadable flags (debug, log_level, maintenance_mode). Use `config.runtime.reload(overrides)` to produce an updated `RuntimeConfig` from a dict of new values.

## Cloud / local coexistence: which backend am I talking to?

Jentic can run as the hosted **cloud** platform (`app.jentic.com`) or as a
**local** self-hosted install. A client — the `jentic` CLI, an agent, or an MCP
server — is pointed at *one* backend via its own configuration. When both are
live on the same machine it is easy for two clients to disagree: e.g. an MCP
server still bound to the cloud while the CLI talks to a fresh local install.
The two backends have independent registries and credentials, so a tool call
answered by the *other* backend looks like data loss ("APIs disappeared",
"credentials vanished", ID-format mismatches) when the systems are simply
different.

### The `GET /instance` identity probe

Every `jentic-one` install exposes an unauthenticated backend-identity endpoint
so any client can confirm which backend it reached before diagnosing missing
data:

```bash
curl -s http://127.0.0.1:8000/instance
```

```json
{
  "backend": "local",
  "canonical_base_url": "http://127.0.0.1:8000",
  "host": "127.0.0.1:8000",
  "instance_id": "…",
  "version": "…"
}
```

- `backend` is a coarse label derived from the canonical host: `cloud` for a
  `jentic.com` host, `local` for a loopback/private host, else `self-hosted`.
- `canonical_base_url` / `host` come from `auth.canonical_base_url` (set in
  `config/local.yaml` to `http://127.0.0.1:8000` for local runs; the cloud is
  `https://app.jentic.com`). This is the instance describing *itself*, so it is
  the value to trust over any client-side assumption.
- `instance_id` is the opaque telemetry instance id when telemetry has resolved
  one (else `null`); it disambiguates two installs that share a host.

To check which backend a given base URL is bound to, hit `/instance` on that
URL. If the `backend`/`host` is not the one you expected, the client is pointed
at the wrong backend.

### Repointing an MCP server (or any client) at a local install

The per-response backend field is added by the external Jentic MCP server; the
`jentic-one` side of the contract is the `/instance` endpoint above. To move an
MCP server (or the CLI) from cloud to a local install, update *that client's*
backend base URL to your local `canonical_base_url` (e.g.
`http://127.0.0.1:8000`) and re-check with `GET /instance`. `jentic-one` never
silently resolves to cloud on its own — a client only reaches cloud because it
is configured to.

