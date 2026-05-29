# Jentic Mini UI — Comprehensive Build Report

## 🎯 Completed Tasks

### 1. **New Pages Built** ✅

#### DiscoverPage (`/catalog`) — unified Discover surface

Replaces the former `SearchPage` and `CatalogPage` with a single search-first page. `/search` redirects to `/catalog` preserving `?q=`.

**Two modes in one page:**

- **Browse mode** (`?q=` empty) — grid of APIs (default) or Workflows depending on the Type segment. APIs come from `GET /apis?source=…` which already merges + dedupes workspace and directory rows on the server; we no longer call `GET /catalog` separately (it returned a sparser shape that caused the "directory isn't showing" bug).
- **Search mode** (`?q=<query>`) — BM25 results from `GET /search` rendered in up-to-three labelled sections (Endpoints, Workflows, APIs from the directory) so the entity-type distinction is unmissable.

**Entity types** (single source of truth in `DiscoveryCard.tsx`) — three only:

| Type | Browse | Search | Notes |
|---|:---:|:---:|---|
| `api` | ✅ default | ✅ (via server blend) | A whole HTTP API provider. `catalog_api`/`catalog_workflow_source` search hits collapse into this type with `source: directory` |
| `workflow` | ✅ opt-in | ✅ | Arazzo multi-step recipe |
| `endpoint` | ❌ hidden | ✅ | A single HTTP call. Renamed from "operation" because the intent of search queries like "send an email" matches endpoints, not vendors — and "endpoint" is friendlier than the Arazzo "operation" term |

**Why no `importable` type and no explicit-import button?** An earlier iteration carved out `catalog_api` search hits as a fourth "Importable" type with its own filter segment, plus a `CatalogPanel` component with an "Import this API" button accessible by expanding the card. Both were removed because **adding a credential silently imports** (`POST /credentials` → `ensure_catalog_api_imported`). The workspace-vs-directory distinction is already carried by the Source axis, so a separate Type was duplicating it; and the explicit "Import this API" button duplicated the credential path with no value-add (you'd still need credentials to actually use the API). Directory APIs now render as regular `ApiCard`s with inline `+ Add credential` and a small `View on GitHub` external link (from `_links.github`).

**API cards open a slide-out detail sheet; everything else expands inline.** Both workspace and directory API cards are clickable surfaces — click opens `ApiDetailSheet` (driven by `?inspect=<api_id>`). Workflows and endpoints still expand inline (they'll move into the sheet in Phase 2 once the pattern proves out for APIs). Inline action buttons on directory cards (`+ Add credential`, `View on GitHub`) call `e.stopPropagation()` so clicking them doesn't ALSO open the sheet.

Each `DiscoveryCard` variant has visually distinct chrome (VendorIcon for APIs, teal-tinted Workflow icon, colored HTTP method badge for endpoints) plus an explicit leading type pill, so the three kinds can be told apart at a glance. Directory APIs get a **synthetic description** in `apiToEntity()` so their cards match the height of workspace cards in the grid (the catalog manifest doesn't carry a description field); the sheet's directory body prefers the live `info.description` from the parsed spec once the preview query resolves.

**API Detail Sheet** (`components/discovery/ApiDetailSheet.tsx`) is the canonical "tell me about this API" surface — right-side slide-out built on the ported `SheetPrimitive`. URL-driven via `?inspect=<api_id>`; share-friendly (deep links open the sheet on page load); two-level navigation via `?op=<capability_id>` for the operation detail drill-down (workspace only). Workspace body queries `/apis/{id}/operations`; directory body queries `/catalog/{id}/operations` (a server-side spec preview that fetches + parses from GitHub, see `src/routers/catalog.py:preview_catalog_operations`). Op rows are clickable buttons for workspace (drill into `InspectPanel`), read-only for directory (until the user adds a credential and the API is imported).

**Layout** — search input + Source/Type segments share a single sticky toolbar (`sticky top-12 z-20`) that bleeds to the viewport edge via `-mx-page-gutter`. Filters stay reachable while scrolling; on mobile they wrap below the search.

**UI vocabulary** — diverges from the server contract on purpose:

| UI label | Server `source` | Meaning |
|---|---|---|
| **My workspace** | `'local'` | Registered locally — ready to call |
| **Public directory** | `'catalog'` | Available in the upstream catalog — adding a credential silently imports it |
| **Endpoint** | search `operation` | A single HTTP call inside an API |
| (no UI label) | search `catalog_api` / `catalog_workflow_source` | Folded into regular `ApiCard` rendering with `source: directory` — see "Why no `importable` type" above |

Translation lives in adapters inside `DiscoverPage.tsx`. Pages that hit the server directly (`CredentialFormPage`, `DashboardPage`, etc.) continue to use the wire format.

**URL contract** (single-select segments + sheet state):

| Param | Browse values | Search values | Default |
|-------|--------------|--------------|---------|
| `?source` | `workspace` \| `directory` | `workspace` \| `directory` | absent → All |
| `?type` | `api` (default, omittable) \| `workflow` | `endpoint` \| `workflow` | absent → All in search; `api` in browse |
| `?inspect` | any api_id | any api_id | absent → sheet closed |
| `?op` | any capability_id | any capability_id | absent → sheet shows API summary, not op detail |

`?inspect` is orthogonal to search mode — `clearSearch` does NOT touch it. Backward-compat parser normalises any legacy URL to the new vocab: `?source=local` → `workspace`, `?source=catalog` → `directory`, comma-lists → `all`; `?type=operation` → `endpoint`, `?type=importable` → `all` (importable used to be a top-level type), comma-lists → `all`. On clearing the search box, search-only type values are dropped so the browse segment isn't visibly out of sync.

**Shared discovery components** (`components/discovery/`):

- `DiscoveryCard` — polymorphic row that dispatches to `ApiCard` / `WorkflowCard` / `EndpointCard` by `entity.type`. Endpoint cards include a parent-API breadcrumb so the "this is one call inside API X" relationship is explicit. API cards open the detail sheet; workflow + endpoint cards expand inline (Phase 2 will migrate them too)
- `VendorIcon` — initials-in-a-square vendor icon (deterministic hashed colour)
- `InspectPanel` — full parameter + auth detail for an operation; used both for inline `EndpointCard` expansion AND as the second-level view inside `ApiDetailSheet`
- `OperationsPanel` — legacy inline ops list; no longer rendered by `DiscoveryCard` (the API sheet inlines its own row renderer to support row-level click drill-down). Kept as a standalone reusable component
- `ApiDetailSheet` — right-side slide-out with workspace + directory bodies, operations list, and `?op=` drill-down. Built on `SheetPrimitive` (also ported from `@jentic/frontend-ui`)

`CatalogPanel.tsx` and `hooks/useImportCatalogApi.ts` were **deleted** when the explicit-import UI was removed. The backend `POST /import` endpoint still exists; it's invoked transparently by `POST /credentials` via `ensure_catalog_api_imported`. The new `GET /catalog/{api_id}/operations` preview endpoint serves the read-only spec view inside the detail sheet without committing to a full import.

#### WorkflowDetailPage (`/workflows/:slug`)

- Workflow name, description, slug
- Badge for step count
- Involved APIs as badges
- Inputs section: name, type, required, description
- Steps section: ordered list with:
    - Step number badge
    - Step ID, description
    - Operation ID or nested workflow ID
    - Parameters display (first 5)
    - Arrow between steps
- Fallback to raw JSON if structure missing

---

### 2. **Major Page Fixes** ✅

#### ToolkitDetailPage — Comprehensive Rebuild

**Fixed:**

- **Keys query bug** — `toolkit.keys` doesn't come from `GET /toolkits/{id}`; now fetches separately from `GET /toolkits/{id}/keys`
- **Keys count** — now uses actual keys array length from separate query (was always 0)
- **Credential count** — already correct (credentials DO come from detail endpoint)

**Added:**

- **Permission management per credential**:
    - Expandable editor for each bound credential
    - Uses `PermissionRuleEditor` component
    - Loads agent rules (filters out system safety rules for display)
    - Save button → `setPermissions` API call
    - Rule count display on each credential card
- **Unbind credential button**:
    - `ConfirmInline` wrapper for safety
    - Calls `api.unbindCredential(toolkitId, credentialId)`
- **Request Access dialog**:
    - Button at top: "Request Access"
    - Modal with:
        - Request type selector (grant | modify_permissions)
        - Credential dropdown (from `/credentials`)
        - Permission rule editor
        - Reason textarea
        - Submit → creates access request via `api.createAccessRequest`
        - Alert with `approve_url` on success
- **Fixed pending requests display** — now shows type badge (grant vs modify)

#### ToolkitsPage

**Fixed:**

- **Pending count** — now uses `usePendingRequests()` hook, groups by `toolkit_id`
- **Credential count** — improved fallback logic (`credential_count` || `credentials.length` || '—')
- **Key count** — shows `key_count` or '—' (list endpoint doesn't return this)

---

### 3. **Routes Added** ✅

Added to `App.tsx`:

```
/credentials/new         → CredentialFormPage
/credentials/:id/edit    → CredentialFormPage
/workflows/:slug         → WorkflowDetailPage
/traces/:id              → TraceDetailPage (already existed)
/jobs/:id                → JobDetailPage (already existed)
```

All imports added correctly.

---

### 4. **API Client Methods Added** ✅

Added to `api/client.ts`:

```
createAccessRequest(toolkitId, body)      // POST /toolkits/{id}/access-requests
patchKey(toolkitId, keyId, body)          // PATCH /toolkits/{id}/keys/{key_id}
inspectCapability(capabilityId, toolkitId?) // GET /inspect/{capability_id}
```

Import for `InspectService` added.

---

### 5. **Permission Request Flow** ✅

**Three entry points:**

1. **From toolkit detail page** (`/toolkits/:id`):
    - "Request Access" button at top-right
    - Opens modal dialog
    - Submit → creates request → shows alert with approval URL

2. **Pending requests banner** (DashboardPage + ToolkitDetailPage):
    - Shows pending count with warning styling
    - "Review" button → navigates to `/approve/:toolkit_id/:req_id`

3. **Direct approval URL** (`/approve/:toolkit_id/:req_id`):
    - Standalone page (outside main Layout chrome)
    - Shows request details: type, reason, rules
    - Approve/Deny buttons
    - Success → redirects to `/toolkits` after 2.5s

**URL pattern:** `/approve/:toolkit_id/:req_id`

- Clean, shareable
- Backend generates as full URL in `approve_url` field
- Easy to copy/paste for human approval

---

## 🔍 Code Audit Results

### Static vs Dynamic Text — All Fixed ✅

- **DashboardPage**: All counts dynamic (`total`, `length`, etc.)
- **CredentialsPage**: All dynamic (count, dates, labels)
- **WorkflowsPage**: All dynamic (step count, involved APIs)
- **TracesPage**: All dynamic (timeAgo helper, status colors)
- **JobsPage**: All dynamic (status filter, counts)
- **ToolkitsPage**: Pending count fixed ✅, credential count improved ✅

### Missing Functionality — All Added ✅

- ✅ Search results → inspect panel
- ✅ Catalog → import flow
- ✅ Workflows → detail page
- ✅ Toolkits → permission management
- ✅ Toolkits → unbind credentials
- ✅ Toolkits → request access UI
- ✅ Credentials → add/edit routes
- ✅ Keys → separate query (bug fixed)

---

## 🧪 Build Status

```bash
✓ TypeScript compilation passed (tsc --noEmit, zero errors)
✓ Vite build succeeded
✓ TailwindCSS 4 via @tailwindcss/vite plugin (no PostCSS)
✓ Zero hardcoded colors, zero emoji icons
✓ Prettier: all files formatted (tabs, single quotes, Tailwind class sorting)
✓ ESLint 9: 0 errors (143 warnings — no-explicit-any, non-blocking)
✓ Husky + lint-staged: pre-commit hook auto-formats and lints staged files
✓ 143 unit + integration tests passing (Vitest browser mode, 19 test files)
✓ 35 Playwright mocked E2E specs
✓ 3 Docker E2E specs (true end-to-end against real backend)
✓ Automated a11y checks via axe-core on all pages
✓ CI: ci-ui.yml (format + lint + tsc + tests) + ci-docker.yml (Docker E2E)
```

**Fixed issues:**

- React Query v5 `onSuccess` → `useEffect` pattern
- Credentials query `queryFn` call signature
- TailwindCSS 3 → 4 migration: `outline-none` → `outline-hidden` (10 files)
- Removed `postcss.config.js` and `tailwind.config.js` (replaced by `@theme inline` in CSS)

---

## 📋 UI Coverage vs API

### Fully Covered ✅

- Discover surface (`/catalog`) — browse + BM25 search (replaces former `/search` and `/catalog`)
- Workflows list + detail (`/workflows`, `/workflows/:slug`)
- Toolkits CRUD + keys + credentials + permissions + access requests
- Credentials CRUD + vault management
- Traces + trace detail
- Jobs + job detail
- User setup + login
- Access request approval flow

### Gaps (if any)

- **Overlays** (`/apis/{id}/overlays`) — no UI page yet (low priority, admin feature)
- **Notes** (`/notes`) — no UI page yet (low priority, internal metadata)
- **OAuth brokers** — no UI (intentional, handled server-side)

Both gaps are expected — overlays and notes are advanced admin features, not core user flows.

---

## 🎨 UI/UX Highlights

1. **Design token system** (TailwindCSS 4) — aligned with `@jentic/frontend-theme`:
    - Single-file theme architecture (`src/index.css`) using shadcn/TW4-native pattern
    - Color tokens use **HSL triplets** in `:root` (e.g. `--primary: 183 29% 72%`) — no `hsl()` wrapper — matching `@jentic/frontend-theme` convention
    - `@theme inline` wraps each token with `hsl()` so Tailwind utilities emit valid values and opacity modifiers work (`bg-primary/50`)
    - Extended token families: `btn-primary-*`, `btn-secondary-*`, `table-header-bg`, `table-body-bg`, `card-border`, `card-border-hover`, `dropdown-*` (7 tokens), `nav-text`, `nav-hover-bg`
    - Semantic token names throughout: `bg-primary`, `text-foreground`, `border-border`, etc.
    - Zero hardcoded Tailwind default colors (no `red-500`, `gray-300`, etc.)
    - No separate `tailwind.config.js` or `styles.css` — everything in `index.css`

2. **Lucide React icons**:
    - All icons are SVG components from `lucide-react`
    - Zero emoji characters used as icons anywhere in the codebase

3. **Consistent design language**:
    - Badge variants for status (success/warning/danger)
    - Method badges (GET/POST/etc.) with color coding
    - Source badges (local/catalog) with icons
    - ConfirmInline for destructive actions

4. **UI Component Library** (shadcn-style owned components):
    - `cn()` utility for class merging (clsx + tailwind-merge)
    - Form primitives: `Button`, `Input`, `Label`, `Textarea`, `Select` — all with `forwardRef`, error states, accessibility
    - Layout: `Dialog` (native `<dialog>`), `EmptyState`, `PageHeader`, `ErrorAlert`, `LoadingState`, `BackButton`
    - Data: `DataTable` (generic typed), `Pagination`, `CopyButton`
    - Shared hooks: `useCopyToClipboard`
    - Shared utilities: `timeAgo`, `formatTimestamp`, `statusVariant`, `statusColor`
    - Barrel export at `src/components/ui/index.ts`
    - ESLint guardrails: `no-restricted-syntax` errors prevent raw `<button>`, `<input>`, `<select>`, `<textarea>` in `src/pages/`

5. **Smart loading states**:
    - Skeleton text ("Loading...")
    - Empty states with helpful CTAs
    - Inline spinners for mutations

6. **Search & filter**:
    - Debounced search inputs
    - Filter chips with clear buttons
    - Pagination controls

7. **Keyboard-friendly**:
    - Autofocus on search inputs
    - Enter to submit forms

8. **Navigation chrome** — aligned with `jentic-webapp` top/bottom pattern:
    - **`TopNavbar`** (`components/layout/TopNavbar.tsx`): fixed `h-12` bar; left = logo + vertical divider + `NavTabs`; right = pending-requests pill + `UserMenu`
    - **`NavTabs`** (`components/layout/NavTabs.tsx`): horizontal desktop tabs with `ResizeObserver`-driven overflow into "More ▾" dropdown; active state = `bg-muted` underlay that morphs between tabs via `framer-motion` `layoutId="activeNavTab"` (spring: stiffness 500, damping 35) — matches `jentic-webapp`'s nav animation
    - **`BottomNavbar`** (`components/layout/BottomNavbar.tsx`): `md:hidden` fixed bottom bar; icon + 10px label tiles; active tile uses the same `framer-motion` `layoutId="activeBottomNavTab"` spring; overflow items open a bottom sheet (Escape + backdrop tap both dismiss)
    - **`UserMenu`** (`components/layout/UserMenu.tsx`): avatar button (initial), dropdown with username, API docs, version, Log out
    - **`navbar.constants.ts`**: single `NAV_ITEMS` array — data-driven, ordered to match previous sidebar
    - Sidebar and mobile drawer **fully removed** from `Layout.tsx`; padding adjusted (`pt-12 pb-20 md:pb-12`)

9. **Page container — `PageShell`** (`components/layout/PageShell.tsx`):
    - Single shared wrapper for every route mounted under `Layout`; owns content max-width and vertical rhythm
    - Three width presets: `wide` (`max-w-screen-2xl`, default — dashboards, lists, tables), `reading` (`max-w-4xl` — detail pages), `form` (`max-w-2xl` — single-column forms)
    - Replaces the previous mess of one-off `<div className="max-w-4xl|5xl|6xl space-y-5|6">` wrappers — every in-Layout page now goes through `PageShell`
    - Auth-only screens (Login, Setup, Approval) keep their own centred card and intentionally bypass `PageShell`

10. **Mobile-responsive**:
    - Grid layouts adapt (1/2/4 columns)
    - Overflow-x-auto on tables

---

## 🚀 Ready for Review

All requested features complete:

- ✅ Unified DiscoverPage replacing SearchPage and CatalogPage
- ✅ All static → dynamic text issues fixed
- ✅ Permission request dialogs working with easy URLs
- ✅ API coverage gaps reviewed (none critical)
- ✅ Build passing with zero errors
- ✅ Workspace page with full CRUD lifecycle, cascade delete with impact info, keyboard shortcuts, mobile responsive cards, expandable descriptions, stats strip, and credential re-link on re-import
- ✅ Operations filtering works across full dataset (batch-fetched) when filter/tag is active
- ✅ Post-import/delete state sync across pages (Discover → Workspace, cross-tab)
- ✅ Navbar active-tab animation fixed for scroll scenarios

The UI is now feature-complete for all core user journeys. Permission management, credential binding, search, catalog import, workspace management, and approval flows all work end-to-end.
