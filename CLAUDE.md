# CLAUDE.md

## Quick Start

```bash
make install   # Full dev setup: sync deps + UI deps + install lefthook hooks
make check     # Run lint + score + secrets audit + arch tests
```

## Rules & Guides

Architectural conventions and patterns are enforced by the architecture tests
in [`tests/arch/`](tests/arch/) and the scoped rules under
[`.cursor/rules/`](.cursor/rules/). Consult those before making changes to:

- ORM models, migrations, or repositories
- Service-layer code
- Web/HTTP handlers
- Architecture tests

## Commands

| Target              | Description                                       |
| ------------------- | ------------------------------------------------- |
| `help`              | Show available targets                            |
| `install`           | Full dev setup: sync deps + UI deps + install lefthook hooks|
| `sync`              | Install/sync project + dev dependencies           |
| `lock`              | Refresh the lockfile                              |
| `upgrade`           | Upgrade locked dependencies                       |
| `fmt`               | Format code with ruff                             |
| `fix`               | Auto-fix lint issues and reformat code            |
| `lint`              | Lint (ruff check + format check + mypy)           |
| `typecheck`         | Run mypy                                          |
| `test`              | Run unit tests                                    |
| `test-unit`         | Run unit tests                                    |
| `test-fast`         | Run unit + arch tests (no external services)      |
| `test-integration`  | Run integration tests (requires running fixtures) |
| `test-arch`         | Run architecture enforcement tests                |
| `test-smoke`        | Run smoke tests (requires running services)       |
| `cov`               | Run tests with coverage report                    |
| `score`             | Validate OpenAPI specs with scorecard (80+ req)   |
| `check`             | Run lint, score, secrets audit, and arch tests    |
| `hooks`             | Install lefthook git hooks (pre-commit + commit-msg) |
| `clean`             | Remove caches and build artifacts                 |
| `start-fixtures`    | Start Docker database fixtures                    |
| `stop-fixtures`     | Stop Docker database fixtures                     |
| `destroy-fixtures`  | Remove Docker database fixtures and volumes       |
| `start-app`         | Start combined app (all surfaces)                 |
| `start-registry`    | Start registry surface standalone                 |
| `start-admin`       | Start admin surface standalone                    |
| `start-control`     | Start control surface standalone                  |
| `build-all`         | Build every service Docker image (see deploy/README.md) |
| `save-all`          | Write image tarballs to `build/jentic-<svc>-<ver>.tar` |
| `images`            | List locally-built `jentic-one/*` images          |

The local k8s workflow (cluster, deploy, obs, smoke) is managed by the deploy CLI:

```
uv run python -m tools.deploy --help
```

## Project Layout

```
cli/                  # Go CLI module (two binaries: jenticctl + jentic) — see cli/README.md
├── cmd/              #   Binary entry points (jenticctl, jentic, clidocs)
└── internal/         #   Shared Go packages (install wizard, catalog, profiles)
tools/
├── deploy/           # Deploy CLI (kind, Helm, obs lifecycle)
│   ├── cli.py        #   Click commands
│   ├── config.py     #   Constants and path helpers
│   └── runner.py     #   Subprocess wrapper + preflight checks
├── openapi_export.py # Regenerate control-plane OpenAPI spec from code (make openapi)
├── endpoint_tree.py  # Regenerate endpoint + scope reference (make endpoints)
└── broker_reference.py # Regenerate Broker OpenAPI artifact (make broker-reference)
src/jentic_one/
├── __init__.py       # Package version
├── __main__.py       # CLI entry point (python -m jentic_one)
├── py.typed          # PEP 561 marker
├── wiring.py         # Top-level composition root for cross-surface wiring
├── registry/         # Registry surface (API spec catalogue + ingest)
├── control/          # Control surface (credential storage + access requests)
├── admin/            # Admin surface (users, jobs, audit, executions)
├── broker/           # Broker module (credential-injecting data-plane proxy)
├── auth/             # Auth surface (identity, tokens, IdP)
│   # each surface above follows: core/ services/ repos/ web/ (+ scoping/)
├── migrations/       # Alembic migration scripts
│   ├── env.py        # Async migration environment (multi-DB aware)
│   ├── targets.py    # Per-database metadata mapping
│   ├── run.py        # Programmatic migration runner
│   ├── registry/     # Registry DB migrations
│   ├── control/      # Control DB migrations
│   └── admin/        # Admin DB migrations
└── shared/           # Shared utilities (imported by every module)
    ├── __init__.py   # Re-exports Context, AppConfig, DatabaseSession, etc.
    ├── config.py     # Configuration schema (pydantic) and YAML+env loader
    ├── context.py    # Central Context object (holds config + DB sessions)
    ├── logging.py    # Structured logging facade (no stdlib logging elsewhere)
    ├── metrics.py    # Metrics facade (only sanctioned place to add instruments)
    ├── tracing.py    # Tracing facade
    ├── redaction.py  # Secret/PII redaction helpers
    ├── pagination.py # Shared pagination primitives
    ├── scopes.py     # Scope/permission constants
    ├── url.py        # URL utilities (server-variable substitution)
    ├── url_validation.py # Upstream URL validation (SSRF guard)
    ├── lookups.py    # Shared cross-surface lookup helpers
    ├── provider_config_store.py # Dynamic OAuth2 provider configuration store
    ├── audit/        # Audit-log helpers
    ├── auth/         # Shared auth primitives (JWKS, tokens)
    ├── broker/       # Shared broker protocols
    ├── crypto/       # Encryption facade
    ├── events/       # Event/SSE plumbing
    ├── executions/   # Execution tracking
    ├── jobs/         # Background job primitives
    ├── models/       # Shared ORM/pydantic models
    ├── resilience/   # Rate limiting, retries
    ├── schemas/      # Shared API schemas
    ├── state/        # State backend (in-memory / redis)
    ├── telemetry/    # Anonymous product-telemetry client (opt-in, off by default)
    ├── web/          # Shared web layer (app factory, deps, errors, OpenAPI meta)
    └── db/           # Database package (SQLAlchemy async)
        ├── __init__.py   # Re-exports DatabaseSession, get_database_url
        ├── base.py       # Declarative bases (RegistryBase, ControlBase, AdminBase)
        ├── session.py    # Async engine + session factory management
        ├── backends/     # Per-backend URL/engine config (postgres, sqlite)
        ├── errors.py     # DatabaseIntegrityError, DatabaseUnavailableError
        └── utils.py      # DB helpers (utcnow, etc.)
tests/
├── unit/                 # Unit tests (no external services required)
├── integration/          # Integration tests (require running fixtures)
├── arch/                 # Architecture enforcement tests
└── smoke/                # Smoke/liveness tests (require running services)
docker/
└── local-setup/          # Local development Docker infrastructure
    ├── docker-compose.yaml       # Single PostgreSQL instance (3 schemas)
    ├── docker-compose.test.yaml  # Test overlay (tmpfs, no persistent volumes)
    └── init-schemas.sql          # Creates registry/control/admin schemas on first boot
alembic.ini           # Multi-database Alembic configuration
scripts/
├── setup.sh          # Bootstrap local dev environment
├── migrate.sh        # Customer-facing migration CLI
└── version.sh        # Reads version from pyproject.toml (used by Make/Docker/Helm)
deploy/
├── docker/           # Per-service Dockerfiles (multi-stage, uv-based builds)
├── helm/
│   ├── jentic-one/   # Umbrella Helm chart with service subcharts
│   ├── observability/# Standalone LGTM chart (Grafana/Loki/Tempo/Prom + OTel collector)
│   └── values/       # Per-mode + per-overlay value files
└── terraform/        # Terraform modules and environment compositions
```

## Frontend (`ui/`)

The single-page app lives in [`ui/`](ui/) (React + Vite + Tailwind, TanStack Query,
MSW for mocks, Vitest browser-mode + Playwright for tests). It is served
same-origin behind the admin API; all authenticated routes are namespaced under
`/app/*`.

```
ui/src/
├── shared/           # The app spine — owned collectively, imported by every module
│   ├── ui/           #   Design-system primitives (Button, Card, Dialog, …) + index.ts BARREL
│   ├── app/          #   Layout, nav, route + query-client wiring; routes.ts is an APPEND-ONLY registry
│   ├── api/           #   Generated client + the @/shared/api facade (the only place that talks HTTP)
│   ├── auth/         #   Auth context, guard, token store
│   ├── hooks/  lib/  #   Cross-cutting hooks and utilities (barrelled via index.ts)
│   └── config.ts     #   Runtime config
├── modules/<domain>/ # One folder per feature — MIRRORS a backend module/surface
│   ├── components/   #   View tier (router/view) — UI only
│   ├── pages/        #   Route entry points (PageShell + PageHeader + PageHelp)
│   ├── api/hooks.ts  #   Service tier — TanStack Query hooks (the ONLY backend access for views)
│   ├── api/client.ts #   Repository tier — wraps @/shared/api; owns sentinel error types
│   ├── routes.tsx    #   Module routes (relative to /app) — registered additively
│   └── nav.ts        #   Module nav entry (absolute /app/… path) — registered additively
└── mocks/handlers.ts # APPEND-ONLY MSW registry (the other sanctioned shared→modules bridge)
```

**Frontend conventions (enforced by `ui/eslint.config.js` + cursor-rules):**

- **Modules mirror the backend's module/layer shape.** Views (`components/`,
  `pages/`) reach the backend only through their module's `api/hooks` (service),
  which call `api/client` (repository), which calls the `@/shared/api` facade.
  Views must never import `@/shared/api`, generated services, or call
  `fetch`/`axios` directly. (Mirrors backend Router → Service → Repository.)
- **No sibling-module imports.** Everything shared goes through `@/shared`.
- **Barrel discipline in modules.** Import shared surfaces through their barrels
  (`@/shared`, `@/shared/ui`), never deep paths like `@/shared/ui/Button`.
- **Additive registries.** `shared/app/routes.ts` and `mocks/handlers.ts` are
  append-only (one import + one line per module) so parallel PRs never collide.

## Rules index

`alwaysApply` rules are always in context. The rest are scoped by `globs` and
should be read before working in the matching area:

| Rule | When |
| ---- | ---- |
| [`git-conventions`](.cursor/rules/git-conventions.mdc) | _always_ — commit/PR conventions |

## Code Style

- **Formatter**: ruff format — double-quote strings, space indentation, 100-char line length.
- **Linter**: ruff check — pycodestyle, pyflakes, isort, flake8-bugbear, flake8-comprehensions, pyupgrade, pep8-naming, flake8-simplify, ruff-specific rules.
- **Type checker**: mypy strict — all functions require type annotations, no implicit `Any`, no untyped defs.
- **Target Python**: 3.12.

## Testing Conventions

- **No DB mocking**: All database interactions in tests must use real database connections. Never mock `DatabaseSession`, `create_async_engine`, `AsyncSession`, `sqlalchemy`, or `asyncpg`. This is enforced by `tests/arch/test_no_db_mocking.py`.
- **Unit tests** cover logic that doesn't require external services (config parsing, access restriction, etc.).
- **Integration tests** cover database lifecycle (startup, shutdown, connectivity) using Docker fixtures.
- **Coverage threshold**: 70% for unit tests (configured in `pyproject.toml`). DB lifecycle code is covered by integration tests.
- **Test style**: pytest-style — plain functions or classes. No `unittest.TestCase` subclassing.

## Metrics

- `shared/metrics.py` is the **only** sanctioned way to add application metrics. Use `get_meter(name)` to obtain a meter and create instruments.
- No module outside `shared/metrics.py` may import `prometheus_client` or `opentelemetry.exporter.*` directly — enforced by `tests/arch/test_metrics_facade.py`.
- The metrics exporter is configured via `AppConfig.observability.metrics.exporter` (values: `otlp`, `prometheus`, `none`).

## Workflow

- **Branch naming**: `<type>/<short-description>` (e.g. `feat/add-broker-api`, `fix/control-timeout`).
- **Commits**: Conventional Commits are **enforced repo-wide** by a `commit-msg` hook (`uv run cz check`, pure Python — no Node). Every commit message — backend or UI — must be `type(scope): subject` with a **mandatory scope** (e.g. `feat(broker): ...`, `fix(security): ...`, `ci(ci.yml): ...`). Reserve `fix` for real bugs present on `main`; refactoring your own unshipped branch code is `refactor`, not `fix`. Full rules and type reference: [`.cursor/rules/git-conventions.mdc`](.cursor/rules/git-conventions.mdc).
- **PR process**: CI must pass (lint + typecheck + tests). Pre-commit hooks are enforced; PR titles follow the same `type(scope): description` format and become the squash commit on `main`. Always squash + merge.
- **Dependencies**: managed by uv. Use `uv add <pkg>` for runtime deps, `uv add --group dev <pkg>` for dev deps.
