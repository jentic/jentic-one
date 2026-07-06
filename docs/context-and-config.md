# Context & Configuration

## Overview

The `jentic_one.shared` package provides the foundational configuration and context system. All surfaces (registry, control, admin, auth, broker) consume these shared components.

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

### SQLite backend

Each database can instead use an embedded SQLite file by setting `backend: sqlite`
and a `path` (the Postgres connection fields are then ignored):

```yaml
databases:
  registry:
    backend: sqlite
    path: .data/registry.db
    schema_name: registry
```

See [`config/local-sqlite.yaml`](../config/local-sqlite.yaml) for a full
single-file-per-surface local setup, or run `make start-app-sqlite`.

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

The database layer uses **SQLAlchemy async** and supports two backends selected
per database via `DatabaseConfig.backend`: **PostgreSQL** (default, `postgresql+asyncpg`
driver) and **SQLite** (`sqlite+aiosqlite` driver, a single-file database). It
follows the same pattern as `jentic/core`:

- `RegistryBase` / `ControlBase` / `AdminBase` — per-database declarative bases for ORM models (import from `jentic_one.shared.db`)
- `DatabaseSession` — manages an async engine and session factory per database
- `get_database_url(config)` — builds a `sqlalchemy.engine.URL` for the configured backend from a `DatabaseConfig`

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
