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

### PageHeader — always the first child of PageShell

`components/ui/PageHeader.tsx` mirrors `jentic-webapp`'s `<PageHeader>`: a full-bleed gradient band (`border-b border-border/50 bg-gradient-to-b from-card to-background`) that escapes the Layout padding via negative margins, with an optional framer-motion spring entrance.

**API:**

```tsx
<PageHeader
  title="My Page"                // required — rendered as <h1>
  subtitle="Short sentence."     // optional — replaces old `description`/`category`
  actions={<Button>Export</Button>}  // optional right-aligned slot
  animated={false}               // pass false in tests to skip framer-motion wrapper
/>
```

Do not use `category` (removed) or `description` (removed). Always use `subtitle` for the supporting sentence beneath the title.

### Page container — always use `PageShell`
`components/layout/PageShell.tsx` is the canonical wrapper for every route mounted under `Layout`. It owns the horizontal gutter, content max-width, and vertical rhythm so the whole app feels consistent. Three width variants:
- `wide` (default, uncapped) — dashboards, lists, tables, anything that should fill the screen. Pairs with the full-bleed `<PageHeader>` so body content edge-aligns with the header band on any monitor.
- `reading` (`max-w-4xl`) — detail pages with prose / sequential sections
- `form` (`max-w-2xl`) — single-column forms

**Horizontal padding is one rule across the whole app: the `--spacing-page-gutter` theme token.** It's defined in `src/index.css` (`@theme inline { --spacing-page-gutter: 1rem; }`) which makes Tailwind 4 auto-generate every utility we need: `px-page-gutter`, `py-page-gutter`, `mx-page-gutter`, `-mx-page-gutter`, etc. `Layout` adds NO horizontal padding; every page owns its own gutter via `PageShell` (body) or `PageHeader` (full-bleed band), both pinned to the token. Sticky toolbars that need to bleed to the viewport edge use `-mx-page-gutter px-page-gutter` to escape PageShell and then re-inset content. **NEVER use a literal `px-4` / `px-6` on a page-level surface** — change `--spacing-page-gutter` in `index.css` and the whole app updates in lock-step (matches `jentic-webapp`).

```tsx
import { PageShell } from '@/components/layout/PageShell';
import { PageHeader } from '@/components/ui/PageHeader';

export default function MyPage() {
  return (
    <PageShell>
      <PageHeader title="…" subtitle="…" />
      …
    </PageShell>
  );
}
```

Auth-only routes (`LoginPage`, `SetupPage`, `ApprovalPage`) are mounted outside `Layout` and render their own centred card — they do **not** use `PageShell`. Never reach for a one-off `<div className="max-w-Nxl space-y-N">` on a new page; pick a `PageShell` variant instead.

### Discover surface (`/catalog`)

`/catalog` is the unified API and workflow discovery surface (formerly split across `/catalog` and `/search`). Any link that previously pointed to `/search` now 404s — use `/catalog` instead. `/search` redirects automatically with query string preserved.

**Two modes, one page (`pages/DiscoverPage.tsx`):**

| Mode | Triggered by | Data sources | Top-level types shown |
|------|-------------|--------------|----------------------|
| Browse | empty `?q=` | `GET /apis?source=…` (default), `GET /workflows` | APIs (default), Workflows |
| Search | non-empty `?q=` | `GET /search?q=` (BM25, blended) | Endpoints, Workflows, APIs (sectioned) |

**Why no separate `/catalog` query in browse mode** — `GET /apis` already does the server-side merging and deduplication of workspace + directory APIs and returns a single consistent `ApiOut` shape. Calling `/catalog` directly returned a different, sparser shape (`{api_id}` only — no `name`/`description`) and caused the "public catalog isn't showing" bug. Anything you want about a directory API in browse mode is reachable via `/apis?source=catalog`.

**Entity types** (`DiscoveryEntityType` in `DiscoveryCard.tsx`) — there are only three:

| Type | Where it shows up | Why it exists |
|---|---|---|
| `api` | Browse (default) + Search blender (`catalog_api`/`catalog_workflow_source` rows collapse to this with `source: directory`) | A whole HTTP API provider |
| `workflow` | Browse (opt-in) + Search | An Arazzo multi-step recipe |
| `endpoint` | **Search only** | A single HTTP operation. Searched separately from its parent API because intent queries ("send an email") match operations, not vendors. Renamed from "operation" in the UI because endpoints/HTTP-call language is more familiar than the Arazzo "operation" term |

**No `importable` type, no explicit import flow in the UI.** Earlier iterations exposed `catalog_api` search hits as a separate `Importable` card type and had a `CatalogPanel` component with an "Import this API" button. Both were removed because **adding a credential silently imports** (`credentials.py:165` calls `ensure_catalog_api_imported`), making explicit-import a duplicative path. Directory APIs now appear as regular `ApiCard`s with `source: directory` and their inline action is just **Add credential** (which triggers the credential form and silently imports server-side on submit).

Endpoints are deliberately **hidden from browse mode** — there's no `GET /endpoints` list endpoint, and a flat browse of every operation across every API would be noise.

**Layout** — search input and the two filter segments share a single sticky toolbar (`-mx-page-gutter px-page-gutter sticky top-12 z-20`) that bleeds to the viewport edge and stays visible while scrolling. On mobile the filters wrap to a second row underneath the search.

**UI vocabulary** — the user-facing words diverge from the server contract on purpose; translate at the adapter boundary in `DiscoverPage.tsx`:

| UI term | Server field value | Meaning |
|---|---|---|
| **My workspace** | `'local'` | Registered locally in this jentic-mini instance — ready to call |
| **Public directory** | `'catalog'` | Available in the upstream Jentic catalog — adding a credential silently imports it |
| **Endpoint** | `operation` (search type) | A single HTTP call, child of an API |
| (no UI label) | `catalog_api` / `catalog_workflow_source` (search types) | Folded into the regular API surface with `source: directory` — there's no separate user-facing concept |

Never propagate the server words (`'local'`, `'catalog'`, `'operation'`) into UI copy or into the `DiscoveryEntity` type. The adapters (`serverSourceToUi`, `apiToEntity`, `searchResultToEntity`) own the translation. Pages that hit the server directly (e.g. `CredentialFormPage`, `DashboardPage`) still use `'local'` because that's the wire format — that's expected.

**URL contract** — filter state lives entirely in the URL so links can deep-target a slice:

- `?q=<query>` — enters search mode
- `?source=workspace` | `?source=directory` — single-select; omit (or absent) means "all"
- Browse mode `?type=api` (default, omittable) | `?type=workflow` — `endpoint` is not valid in browse and falls back to `api`
- Search mode `?type=endpoint` | `?type=workflow` — omit means "all"; `api` is search-invalid and falls back to all
- `?inspect=<api_id>` — opens the **API Detail Sheet** for that API. Orthogonal to search mode (`clearSearch` does NOT touch it). Shareable: pasting `/catalog?inspect=stripe.com` opens the sheet on load.
- `?inspect=<api_id>&op=<capability_id>` — drills into the operation detail view inside the same sheet (workspace APIs only — directory ops aren't drillable until imported).
- **Backward compat:** the parser accepts legacy values and normalises:
  - `?source=local` → `workspace`, `?source=catalog` → `directory`, `?source=local,catalog` (or any comma list) → `all`
  - `?type=operation` → `endpoint`, `?type=importable` → `all` (importable used to be a top-level type), comma lists → `all`
- When leaving search mode (`clearSearch`), search-only type values are dropped from the URL so the browse type segment isn't visibly out-of-sync with the URL.

**Search results presentation** — search results are grouped into clearly-labelled sections (`Endpoints`, `Workflows`, `APIs from the directory`) with per-section counts. The Type segment narrows to one section; the `All` default shows every section that has hits. The APIs section is populated by the `catalog_api` rows from `/search`'s blender, now rendered as regular `ApiCard`s.

**Card interaction patterns** — API cards open a slide-out sheet; everything else expands inline. The split is `(type)` not `(type, source)` any more:

| Card | On click | Inline actions |
|------|----------|----------------|
| `ApiCard` (workspace) | Opens **API Detail Sheet** (sets `?inspect=<api_id>`) — sheet renders header + operations list (via `GET /apis/{id}/operations`) | `ChevronRight` indicator only |
| `ApiCard` (directory) | Opens **API Detail Sheet** — sheet lazy-fetches the spec preview via `GET /catalog/{id}/operations` (server-side fetch from GitHub) | `+ Add credential` (primary) and `View on GitHub` icon link (when `_links.github` is present). Both `stopPropagation` so they don't ALSO open the sheet |
| `WorkflowCard` | Inline expand → workflow detail blurb (Phase 2 will move this into the sheet too) | chevron only |
| `EndpointCard` | Inline expand → `InspectPanel` (Phase 2 will move this into the sheet too) | chevron, copyable id |

Both workspace and directory API cards are clickable surfaces now (`CardShell` always renders a `<button>` for the header when `onClick` is provided). The `expanded` prop on `ApiCard` is only used to highlight the active border while the sheet is open over it — no inline expansion content is rendered for `type === 'api'`. `DiscoveryCard` short-circuits the inline `ExpandedPanel` for API entities (`showInlineExpansion = expanded && entity.type !== 'api'`).

A synthetic description is set in `apiToEntity()` for directory APIs so card heights match workspace cards in the grid (the catalog manifest doesn't carry a description field). The sheet's directory body prefers the live `info.description` from the parsed spec when available.

**API Detail Sheet (`components/discovery/ApiDetailSheet.tsx`):**

- Driven by the `?inspect=<api_id>` URL param (sheet open state derives from URL). `?op=<capability_id>` adds a second-level drill-down to `InspectPanel` for workspace ops.
- `DiscoverPage` keeps a `stickyInspect` local state that mirrors the URL but lags behind closing — the sheet content stays mounted during the 300ms exit animation so the user doesn't see an empty slide-out mid-close. `onAfterClose` clears `stickyInspect` and the cached `selectedEntity`.
- `initialEntity` is the `DiscoveryEntity` captured at click time. It lets the header render instantly with the right title/source/credentials without waiting on a round trip. Deep-link / refresh paths don't have it — `useResolvedSource` then races `getApi` (workspace) and `getCatalogEntry` (directory) to figure out which body to render.
- Workspace body: queries `/apis/{id}/operations` (DB-backed). Op rows are clickable buttons — clicking pushes `?op=<jentic_id>` and the sheet swaps to the `InspectPanel` view with a "Back to operations" arrow in the header.
- Directory body: queries `/catalog/{id}/operations` (server-side GitHub fetch + parse — see `src/routers/catalog.py:preview_catalog_operations`). Op rows are read-only — drill-down requires the API to be imported first, which means adding a credential. Capped at 200 operations to keep huge specs (Stripe-style) responsive.
- Inline `+ Add credential` and `View on GitHub` actions on directory cards `stopPropagation` so the click reaches the link, not the sheet-opening button underneath.

**Shared primitives in `components/discovery/`:**

| File | Purpose |
|------|---------|
| `DiscoveryCard.tsx` | Polymorphic card; dispatches to `ApiCard`, `WorkflowCard`, `EndpointCard` by `entity.type`. Card chrome is intentionally distinct per type so the three kinds can be told apart at a glance (vendor icon vs teal Workflow icon vs HTTP method badge). The Source axis (workspace pill vs directory pill) carries the "do I need credentials first?" signal on API cards |
| `DiscoveryFilterBar.tsx` | Two `SegmentedToggle`s (Source + Type). Renders different Type segments depending on `browseMode`. Exports `useDiscoveryFilters()`, `matchesSource()`, `browseEffectiveType()`, `searchEffectiveType()` helpers |
| `VendorIcon.tsx` | Cheap initials icon with deterministic hashed colour; use in any list that shows API / vendor names |
| `InspectPanel.tsx` | Operation detail view — used both as inline expansion under `EndpointCard` AND as the second-level view inside `ApiDetailSheet` when `?op=` is set |
| `OperationsPanel.tsx` | Inline operations list (legacy — no longer rendered inside DiscoveryCard, kept for any standalone consumer). The sheet's workspace body inlines the same shape using `MethodBadge` rows so each row can be a clickable drill-down button |
| `ApiDetailSheet.tsx` | Right-side slide-out (built on `SheetPrimitive`) — replaces inline API expansion. Owns its own operations queries; receives `apiId` + cached `initialEntity` from `DiscoverPage` |

`CatalogPanel.tsx` and `hooks/useImportCatalogApi.ts` were **deleted** when the explicit-import UI was removed. The backend `POST /import` endpoint still exists and is invoked transparently by `POST /credentials` via `ensure_catalog_api_imported`.

**Why single-select segments instead of multi-select chips:** Discover's filter axes are mutually-exclusive 95% of the time; the rare "show me 2 of 3 types" case doesn't justify the cost in clutter and ambiguity. Segmented controls read as "one decision", make the active state unambiguous, and remove the "what's the default?" guesswork.

**Card design vocab** — all `DiscoveryCard` variants follow the `jentic-webapp` `ApiCard` pattern: `rounded-xl border border-border/60 bg-card p-5`, hover shadow (`hover:shadow-lg hover:shadow-black/[0.03] dark:hover:shadow-black/20`), gradient hover overlay (`from-primary/[0.02]`). Pills are `rounded-full px-2.5 py-0.5 ring-1` with soft-tint hue backgrounds. Every card carries a leading type pill (`API`/`Workflow`/`Endpoint`/`Available to import`) so type is always legible without reading the icon.

**Import hook (`hooks/useImportCatalogApi.ts`):** Single shared two-step import flow (resolve spec URL via `GET /catalog/:id`, then `POST /import`). Use this hook everywhere an import button appears; do not inline the fetch logic.

### Workspace page (`/workspace`)

The Workspace page shows everything the user has imported into their local jentic-mini instance. It's the "home base" for managing APIs, workflows, credentials, and toolkits.

**Route structure:**
- `/workspace` — main view with API and workflow tiles, stats strip, search/filter
- `/workspace/apis/:apiId` — API detail page (operations, credentials, toolkit bindings, workflows)
- `/workspace/workflows/:slug` — Workflow detail page (steps, input schema, metadata)

**Components (`components/workspace/`):**
| File | Purpose |
|------|---------|
| `WorkspaceView.tsx` | Main view: stats strip + search + API/workflow tile grids |
| `WorkspaceTile.tsx` | Card component for API and workflow items (imported date, counts) |
| `WorkspaceSearch.tsx` | Auto-focused filter input with keyboard shortcut (`/`) |
| `WorkspaceStatsStrip.tsx` | Compact stats bar (APIs, workflows, credentials, last activity) |
| `ApiDetailView.tsx` | Full API detail: operations list, credentials, toolkit bindings |
| `ImportSourceDialog.tsx` | Import dialog supporting URL, file upload, and paste |
| `WorkspaceAddButton.tsx` | "+" button that opens ImportSourceDialog |

**Key behaviors:**
- **Operations filtering**: When a filter/tag is active, automatically fetches all operations (in 200-item batches) so filtering searches the full set, not just loaded pages. When no filter is active, normal paginated "Load more" UX applies.
- **Delete with cascade info**: `ConfirmDeleteDialog` shows affected workflows (will-delete vs will-affect), credentials, and toolkits with their linked credential names. Only shows toolkits that actually bind credentials for the target API (fetches each toolkit's `bound_apis`).
- **Credential preservation**: Non-cascade API deletion keeps `credentials.api_id` intact. When the same API is re-imported (same derived ID), credentials auto-relink without user action. Cascade delete (`?cascade=true`) removes credentials AND their toolkit bindings.
- **Post-import freshness**: `useImportCatalogApi` and `useCredentialImportedSync` both invalidate `['workspace']` and `['workspace-stats']` query keys so workspace data is fresh regardless of which page the import was triggered from.
- **Keyboard shortcuts**: Same pattern as Discover — `/` to focus filter, arrow navigation via `useRovingGridFocus`, `Enter` to open, `Esc` to go back, `Cmd+/` for help.

**Backend endpoints used:**
- `GET /apis?source=local` — list imported APIs
- `GET /apis/{id}/operations?offset=&limit=` — paginated operations
- `GET /workflows?source=local` — list imported workflows
- `GET /credentials?api_id=` — credentials for an API
- `GET /toolkits` + `GET /toolkits/{id}` — toolkit bindings
- `DELETE /apis/{id}` / `DELETE /apis/{id}?cascade=true` — soft/hard delete
- `DELETE /workflows/{slug}` — remove workflow

### UI Component Library
Shadcn-style owned components in `ui/src/components/ui/`.

- **Primitives**: `Button`, `Input`, `Label`, `Textarea`, `Select`, `SegmentedToggle`, `SheetPrimitive` — extend native HTML props, support error states and accessibility. `SegmentedToggle<V>` is a faithful port of `@jentic/frontend-ui`'s component (animated sliding indicator via `framer-motion`'s shared `layoutId`); reach for it before falling back to chips or `<select>` for short, mutually-exclusive option sets. Each instance needs a unique `layoutId` prop — `framer-motion` uses it to scope the shared-element animation. `SheetPrimitive` is also a faithful port of the same library (focus trap, Escape, body scroll-lock via `overscroll-behavior: contain`, portaled to `document.body`) — one local deviation: `onAfterClose` is read through a ref so inline-closure parents don't reset the 300ms exit timer on every render. When either upstream component updates, keep these files 1:1 with the source.
- **Layout**: `Dialog` (native `<dialog>`, zero deps), `EmptyState`, `PageHeader` (expandable subtitle via ResizeObserver), `ErrorAlert`, `LoadingState`, `BackButton`, `CopyButton`, `KeyboardShortcutsBar` (fixed bottom strip showing available shortcuts), `PageHelp` (⌘/ toggle overlay)
- **Forms**: `Checkbox` (polished styled checkbox with label, cursor-pointer, user-select-none)
- **Overlays**: `Menu` — exports `useDismissable` (outside-click + Escape), `MenuPanel`, `MenuSeparator`, `menuItemClass`. `ConfirmDeleteDialog` — reusable deletion confirmation with cascade impact display (affected workflows, credentials, toolkits). Use these for any new dropdown / popover / sheet rather than re-rolling refs and effect listeners.
- **Data**: `DataTable` (generic typed columns), `Pagination`
- **Shared hooks**: `useCopyToClipboard`, `useRovingGridFocus` (arrow-key grid navigation), `useScrollRestore` in `ui/src/hooks/`
- **Shared utilities**: `timeAgo`, `formatTimestamp`, `statusVariant`, `statusColor`, `isTypingTarget` (keyboard guard) in `ui/src/lib/`
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