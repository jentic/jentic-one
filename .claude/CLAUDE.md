# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What is Jentic Mini?

Jentic Mini is the open-source, self-hosted implementation of the Jentic API. It gives AI agents a local execution layer: search APIs via BM25, broker authenticated requests (credential injection without exposing secrets to the agent), enforce access policies, and observe execution traces. Built with FastAPI + SQLite + Fernet encryption.

See @AGENTS.md for the agent-facing runtime guide (search, inspect, execute workflow; endpoint reference; credential-injection contract from the agent's perspective). Keep the overlapping sections in both files (Jentic Mini overview, credential-injection flow, capability ID format, `X-Jentic-API-Key` header) in sync when changing either.

## Development Setup

See @DEVELOPMENT.md for prerequisites, installation, and running the server.

### Key environment variables
- `JENTIC_VAULT_KEY` — Fernet key for credentials vault (auto-generated from `data/vault.key` if unset)
- `JENTIC_PUBLIC_HOSTNAME` — public hostname for self-links and workflow dispatch
- `JENTIC_ROOT_PATH` — path prefix when Mini is mounted under a reverse proxy (e.g. `/jentic`); falls back to the per-request `X-Forwarded-Prefix` header when unset
- `DB_PATH` — SQLite database path (default: `/app/data/jentic-mini.db`)
- `LOG_LEVEL` — `debug | info | warning | error`
- `JENTIC_HOST_PATH` — project root path for Docker mounts (defaults to `.`)

## Architecture

### Request flow
All requests pass through `APIKeyMiddleware` (`src/auth.py`) which validates `X-Jentic-API-Key` and sets `request.state.toolkit_id` / `request.state.is_admin`.

### Broker catch-all pattern (CRITICAL)
The broker router (`src/routers/broker.py`) is a catch-all `/{target:path}` that proxies requests to upstream APIs. It identifies broker routes by checking if the first path segment contains a `.` (e.g., `api.stripe.com`).

**The broker MUST be the last router registered in `src/main.py`.** If registered before other routers, it swallows all internal routes. Symptom: endpoints return broker errors like `No API found for host 'inspect'`.

Registration order in `src/main.py`:
1. All internal routers (capability, workflows, import, catalog, jobs, traces, overlays, apis, search, credentials, toolkits, etc.)
2. Health, root, favicon, login, docs, redoc routes
3. Static file mounts + SPA catch-all routes
4. `broker_router` — **last**

### Core modules (`src/`)
| File | Purpose |
|------|---------|
| `config.py` | Centralised constants: `DB_PATH`, `JENTIC_PUBLIC_HOSTNAME`, `SPECS_DIR`, `WORKFLOWS_DIR`, `DEFAULT_TOOLKIT_ID` |
| `main.py` | FastAPI app, router registration, lifespan, OpenAPI schema customization |
| `auth.py` | API key middleware — validates `X-Jentic-API-Key` header |
| `db.py` | SQLite schema init + migrations (aiosqlite) |
| `bm25.py` | In-memory BM25 search index over operations and workflows |
| `vault.py` | Fernet-encrypted credential storage |
| `oauth_broker.py` | OAuth broker registry for delegated auth flows |
| `models.py` | Pydantic models |
| `validators.py` | Input validation |
| `negotiate.py` | Content negotiation middleware |
| `startup.py` | Self-registration + broker app seeding at startup |
| `utils.py` | Shared utilities (`_abbreviate()`, `build_absolute_url()`, `parse_prefer_wait()`) |

### Routers (`src/routers/`)
| Router | Tag | Key responsibility |
|--------|-----|--------------------|
| `search.py` | search | BM25 full-text search — main agent entrypoint |
| `capability.py` | inspect | `GET /inspect/{id}` — operation/workflow details |
| `broker.py` | execute | Catch-all proxy with credential injection |
| `workflows.py` | execute | Workflow listing + execution via arazzo-runner |
| `toolkits.py` | toolkits | Toolkit CRUD, access keys, policies |
| `credentials.py` | credentials | Credential vault management (admin) |
| `traces.py` | observe | Execution trace retrieval |
| `apis.py` | catalog | API registration, spec management |
| `catalog.py` | catalog | Public catalog browsing (jentic-public-apis) |
| `import_.py` | catalog | Spec/workflow import endpoint |
| `overlays.py` | catalog | Security scheme overlay management |
| `jobs.py` | observe | Async job handles |
| `default_key.py` | — | First-time key generation from trusted subnet |
| `user.py` | user | Human account management + JWT auth |
| `oauth_brokers.py` | credentials | OAuth broker configuration |

### Credential injection flow
Credentials are **never** exposed to agents or passed as env vars. The broker:
1. Identifies upstream host from the URL path
2. Looks up credentials bound to the requesting toolkit
3. Reads the security scheme from the spec + any confirmed overlays
4. Injects the auth header (reads scheme name from overlay, not hardcoded)
5. Forwards to the real upstream
6. Logs a trace

### Workflow execution
Arazzo workflows use `arazzo-runner` (installed from PyPI). The runner patches `servers[0].url` in source specs to route all HTTP calls through the local broker (`http://localhost:8900/{host}`), ensuring every step gets credential injection, tracing, and policy enforcement.

### ID formats
- **Capability ID**: `METHOD/host/path` (e.g., `GET/api.elevenlabs.io/v1/voices`)
- **Workflow ID**: `POST/{JENTIC_PUBLIC_HOSTNAME}/workflows/{slug}`
- The system distinguishes operations from workflows by checking if the host matches `JENTIC_PUBLIC_HOSTNAME`

### Database
SQLite with aiosqlite. Schema defined in `src/db.py` with inline migrations. Key tables: `apis`, `operations`, `credentials`, `toolkits`, `toolkit_keys`, `toolkit_credentials`, `workflows`, `executions`, `execution_steps`, `api_overlays`, `notes`, `permission_requests`.

### OAuth brokers (`src/brokers/`)
Pluggable OAuth broker system. Currently includes `pipedream.py` for Pipedream-based OAuth credential routing.

## UI

The `ui/` directory contains a React 18 + Vite 7 admin frontend.

### Tech stack
- **TailwindCSS 4** via `@tailwindcss/vite` plugin (no PostCSS, no JS config file)
- **Design tokens**: Single-file theme in `ui/src/index.css` using the shadcn/TW4-native pattern:
  - `@theme inline` maps CSS custom properties to Tailwind utility classes (e.g. `--color-primary: var(--primary)` → `bg-primary`, `text-primary`)
  - `:root` defines the full HSL color palette and semantic mappings
  - `@layer base` sets body, heading, and button cursor styles
- **Icons**: Lucide React
- **Fonts**: Nunito Sans (body, `font-sans`), Sora (headings, `font-heading`), Geist Mono (code, `font-mono`) — loaded via Google Fonts in `index.html`

### Navigation chrome
Mini uses a webapp-style top + bottom nav (no sidebar):
- `components/layout/TopNavbar.tsx` — fixed `h-12` top bar; logo + `NavTabs` (desktop) + pending pill + `UserMenu`
- `components/layout/NavTabs.tsx` — horizontal tabs, overflow into "More ▾"; `ResizeObserver`-driven; active tab pill morphs between tabs via `framer-motion` `layoutId="activeNavTab"` (spring: stiffness 500, damping 35)
- `components/layout/BottomNavbar.tsx` — `md:hidden` bottom bar, same spring on `layoutId="activeBottomNavTab"`; overflow opens a bottom sheet (Escape / backdrop dismiss)
- `components/layout/UserMenu.tsx` — avatar + dropdown (username + version → API docs / jentic.com → Log out)
- `components/layout/navbar.constants.ts` — single `NAV_ITEMS` source of truth
- `components/ui/Menu.tsx` — shared `useDismissable` hook + `MenuPanel` / `MenuSeparator` / `menuItemClass` primitives that `NavTabs` and `UserMenu` (and the mobile sheet) consume so every dropdown shares the same outside-click / Escape behaviour and inset-pill item rounding

`Layout.tsx` wraps all pages: `<TopNavbar /> + <main className="pt-12 pb-20 md:pb-12"> + <BottomNavbar />`

### Page container — always use `PageShell`
`components/layout/PageShell.tsx` is the canonical wrapper for every route mounted under `Layout`. It owns the content max-width and vertical rhythm so the whole app feels consistent. Three width variants:
- `wide` (default, `max-w-screen-2xl`) — dashboards, lists, tables, anything that should fill the screen on a modern monitor
- `reading` (`max-w-4xl`) — detail pages with prose / sequential sections
- `form` (`max-w-2xl`) — single-column forms

```tsx
import { PageShell } from '@/components/layout/PageShell';

export default function MyPage() {
  return (
    <PageShell>
      <PageHeader title="…" />
      …
    </PageShell>
  );
}
```

Auth-only routes (`LoginPage`, `SetupPage`, `ApprovalPage`) are mounted outside `Layout` and render their own centred card — they do **not** use `PageShell`. Never reach for a one-off `<div className="max-w-Nxl space-y-N">` on a new page; pick a `PageShell` variant instead.

### UI Component Library
Shadcn-style owned components in `ui/src/components/ui/`.

- **Primitives**: `Button`, `Input`, `Label`, `Textarea`, `Select` — extend native HTML props, support error states and accessibility
- **Layout**: `Dialog` (native `<dialog>`, zero deps), `EmptyState`, `PageHeader`, `ErrorAlert`, `LoadingState`, `BackButton`, `CopyButton`
- **Overlays**: `Menu` — exports `useDismissable` (outside-click + Escape), `MenuPanel`, `MenuSeparator`, `menuItemClass`. Use these for any new dropdown / popover / sheet rather than re-rolling refs and effect listeners.
- **Data**: `DataTable` (generic typed columns), `Pagination`
- **Shared hooks**: `useCopyToClipboard` in `ui/src/hooks/`
- **Shared utilities**: `timeAgo`, `formatTimestamp`, `statusVariant`, `statusColor` in `ui/src/lib/`
- **Barrel export**: `ui/src/components/ui/index.ts`

### Generated API client
- **Source**: `ui/openapi.json` (static copy of `/openapi.json` from the running server)
- **Generated files**: `ui/src/api/generated/` — do NOT edit manually (header says "do not edit")
- **Manual wrapper**: `ui/src/api/client.ts` — thin wrapper around generated services
- **Regenerate command**: `npx openapi-typescript-codegen --input openapi.json --output src/api/generated --client fetch --useOptions` (run from `ui/`)
- **When to regenerate**: after adding/changing/removing backend endpoints, update `ui/openapi.json` first (`curl localhost:8900/openapi.json | python3 -m json.tool > ui/openapi.json`), then run the codegen
- **`--useOptions` is required** — without it, method signatures change from named objects to positional params, breaking `api/client.ts`

### Build
- **Build output**: `static/` at project root (gitignored, generated at build time)
- **Static path resolution**: `src/main.py` resolves `STATIC_DIR` to `<project_root>/static/`. In Docker this is `/app/static/` (outside the `./src:/app/src` bind mount, so dev mounts don't hide built assets).
- **Docker**: Multi-stage build — Node stage compiles UI to `static/`, Python stage runs the server. Final image has no Node/npm.
- **Vite plugin** (`copyApiDocsAssets` in `ui/vite.config.ts`): copies `swagger-ui-dist` and `redoc` assets from `node_modules` into `static/` after each build, so `/docs` and `/redoc` work offline.
- **Favicon**: lives in `ui/public/favicon.png`, Vite copies it to output automatically.

### Adding colors or tokens
All theming is in `ui/src/index.css`. To add a new semantic color:
1. Add the raw HSL **triplet** to `:root` (e.g. `--info: 210 80% 60%` — no `hsl()` wrapper here)
2. Add the semantic mapping to `:root` if needed (e.g. `--info-foreground: 0 0% 100%`)
3. Map it in `@theme inline` with the `hsl()` wrapper (e.g. `--color-info: hsl(var(--info))`)
4. Use it in components as `bg-info`, `text-info`, etc. — opacity modifiers now work: `bg-info/50`

## Testing

- **Backend**: pytest-based, exercises the API at the HTTP boundary. Uses a real temp SQLite DB with Alembic migrations — no mocking. Tests organized by trust boundary (auth, policy, vault, broker, toolkit). CI: `ci-backend.yml` (path-filtered to `src/`, `tests/`, `alembic/`).
- **UI**: Vitest browser mode + MSW (`msw/browser`) + axe-core + Testing Library. See `ui/TESTING.md` for the full contributor guide. CI: `ci-ui.yml` (path-filtered) + `ci-docker.yml` (always runs).

See @DEVELOPMENT.md for commands.

## Formatting & Linting

- **UI**: ESLint 9 (flat config) with Prettier as plugin. Config: `ui/eslint.config.js`, `ui/prettier.config.js`, `ui/.editorconfig`
- **Husky + lint-staged**: pre-commit hook lints staged files automatically
- **commitlint**: commit-msg hook validates Conventional Commits (config: `ui/.commitlintrc.json`). A Claude PreToolUse hook (`.claude/hooks/commitlint-before-commit.py`) runs the same check before `git commit` is fired so failures surface inside the agent loop.

## Data directory (all gitignored)
- `data/jentic-mini.db` — SQLite database
- `data/vault.key` — Fernet encryption key (auto-generated)
- `data/specs/` — Downloaded API specs
- `data/catalog_manifest.json` — Cached public catalog manifest
- `data/workflow_manifest.json` — Cached workflow manifest
- `data/workflows/` — Imported workflow files