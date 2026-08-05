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

## Local / remote coexistence: which backend am I talking to?

A `jentic-one` install declares its own locality via `server.backend`: `local`
for a self-hosted install on your own machine/network, or `remote` for a hosted
install run elsewhere (e.g. Jentic Cloud). It defaults to `local`; the hosted
platform sets `remote` in its own config. A client — the `jentic` CLI, an agent,
or an MCP server — is pointed at *one* backend via its own configuration. When a
local install and a remote one are both reachable it is easy for two clients to
disagree: e.g. an MCP server still bound to a remote backend while the CLI talks
to a fresh local install. The two backends have independent registries and
credentials, so a tool call answered by the *other* backend looks like data loss
("APIs disappeared", "credentials vanished", ID-format mismatches) when the
systems are simply different.

### The deployment's public origin (`server.public_base_url`)

Several externally-visible URLs must point at the origin clients actually use
to reach the deployment: the OAuth connect `redirect_uri`, the OIDC issuer and
JWT-Bearer audience, the DCR `registration_client_uri`, access-request approval
links, async-job `_links.self`, and the 424 `provisioning_url`. Set them all at
once with a single knob:

```yaml
server:
  public_base_url: "https://jentic.example.com"   # scheme + host [+ port]
```

Resolution, per URL:

1. its own specific override if set (`auth.canonical_base_url`,
   `control.access_requests.canonical_base_url`, `broker.jobs_api_base_url`,
   `broker.account_linking_base_url`, `credentials.providers.<id>.redirect_uri`),
   then
2. `server.public_base_url`, then
3. for request-scoped URLs (OAuth callback, issuer), the **incoming request's
   origin**.

> **Note — the JWT-Bearer assertion audience is not request-scoped.** It is
> built request-lessly from step 1→2 only. With **both** `auth.canonical_base_url`
> and `server.public_base_url` unset it collapses to `/oauth/token` (while the
> discovery document, being request-scoped, advertises the request origin). So
> "any-port zero config" covers the OAuth connect callback and discovery, **not**
> the agent JWT-Bearer assertion flow — set `public_base_url` for that. (This is
> pre-existing behaviour, unchanged by the public-origin work.)

Because of (3), local development on any port works with **zero** configuration
— the OAuth callback tracks whatever host/port you actually browsed to, fixing
the "connect breaks on any port but 8000" trap. Set `public_base_url` (or an
override) only when the app can't infer its public origin from the request,
i.e. behind a reverse proxy / ingress. At startup, any *explicitly configured*
server-published public URL whose origin disagrees with the serving origin (and
isn't explained by `public_base_url`) logs a `public_url_origin_mismatch`
warning — the server still starts. (Per-provider `redirect_uri` overrides are
excluded from that check: an OAuth callback is reached by the operator's
browser, which behind a gateway legitimately hits a different origin than the
in-cluster `public_base_url` — that is exactly why the override exists.)

> **Behind a TLS-terminating reverse proxy**, set `public_base_url` explicitly.
> The request-origin fallback (3) reconstructs the scheme/host from the incoming
> request, which for a plain-HTTP hop from the proxy yields `http://…` and the
> proxy's internal host unless forwarded headers are trusted — so a derived
> `redirect_uri` / issuer would be wrong. `public_base_url` (or the specific
> override) short-circuits that and is the supported way to pin the public
> `https://` origin.

> **Security — the request-origin fallback trusts the `Host` header.** The app
> runs no `TrustedHostMiddleware`, so when a URL is derived from the request an
> authenticated caller who controls their own `Host` header can make the
> *authorize* URL carry a `redirect_uri` pointing at an arbitrary origin. This
> is low-risk because it is the caller's own connect flow, and the real gate is
> the IdP's **exact-match** on its registered redirect URIs: the callback only
> works if it matches a URL the operator pre-registered with the IdP. That makes
> IdP-side exact-match redirect-URI registration a **load-bearing security
> boundary** of this design — register the exact callback URL shown by
> `GET /credentials/providers` (`callback_url`), and pin `public_base_url` in
> any deployment where you don't want the callback origin to follow the request.

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
  "instance_id": "…"
}
```

- `backend` is the operator-declared locality from `server.backend`: `local` (the
  default) for a self-hosted install on your own machine/network, `remote` for a
  hosted install run elsewhere. It is a hint for humans/agents, not an
  authorization signal.
- `canonical_base_url` / `host` reflect the deployment's public origin —
  `server.public_base_url` (or the advanced per-surface override
  `auth.canonical_base_url` when set). Set `server.public_base_url` in
  `config/local.yaml` to `http://127.0.0.1:8000` for local runs; a hosted
  platform sets its own public URL. This is the instance describing *itself*,
  so it is the value to trust over any client-side assumption. Any userinfo
  embedded in the configured URL is stripped before echoing.
- `instance_id` is an opaque digest *derived from* the telemetry instance id
  (never the id itself). It only disambiguates two installs sharing a host when
  both have telemetry enabled — it is `null` whenever telemetry has not
  resolved an id (e.g. telemetry disabled).

To check which backend a given base URL is bound to, hit `/instance` on that
URL. If the `backend`/`host` is not the one you expected, the client is pointed
at the wrong backend.

### Repointing an MCP server (or any client) at a local install

The per-response backend field is added by the external Jentic MCP server; the
`jentic-one` side of the contract is the `/instance` endpoint above. To move an
MCP server (or the CLI) from a remote backend to a local install, update *that
client's* backend base URL to your local `canonical_base_url` (e.g.
`http://127.0.0.1:8000`) and re-check with `GET /instance`. `jentic-one` never
silently resolves to a remote backend on its own — a client only reaches a
remote backend because it is configured to.

