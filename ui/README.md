# Jentic One UI

In-repo frontend for Jentic One. Built with React 18 + Vite + TypeScript +
Tailwind v4, served same-origin as static files by the **admin** surface.

This is the **foundation** slice: the themed app shell (`/app`) behind a
Bearer-JWT login + AuthGuard, the design-system primitives under `shared/ui/`,
the codegen'd typed API client under `shared/api/generated/`, and the additive
route/nav/MSW registries the feature PRs plug into. Real product screens land in
follow-up PRs.

The health endpoint path differs by deploy mode (`/health` standalone vs
`/admin/health` combined), so the backend exposes the right path at
`/app-config.json`; the SPA fetches it once on boot and reads it via
`shared/config` — see `src/jentic_one/shared/web/static.py`.

## Develop

```bash
npm ci
npm run dev          # Vite dev server; proxies admin API paths to localhost:8000
```

Override the API target when the backend is elsewhere:

```bash
VITE_API_HOST=http://localhost:8000 npm run dev
```

## API client (codegen)

Typed services/models under `src/shared/api/generated/` are generated from the
backend's OpenAPI schema and committed verbatim (do **not** hand-edit — the files
carry a "do not edit" header).

The OpenAPI schema is itself **generated from the FastAPI app** — both the
canonical control-plane spec (`openapi/control/control.openapi.yaml`) and this
package's `ui/openapi.json` come from `tools/openapi_export`. Regenerate both
after backend endpoint/model changes with one command (DB-free, no running
server needed):

```bash
cd .. && make openapi   # writes openapi/control/control.openapi.yaml + ui/openapi.json
cd ui && npm run codegen # regenerate the typed client from ui/openapi.json
```

A CI drift test (`tests/arch/test_openapi_conformance.py`) fails if the
checked-in spec is out of date, so always run `make openapi` after touching
routers or response/request models.

Import generated code via the `@/shared/api` facade (never `./generated`
directly) so the Bearer-JWT config (`shared/api/client.ts`) is applied first.
Auth: `POST /auth/login` → token stored in `shared/api/token-store` →
`Authorization: Bearer` on every request; 401/403 are non-retryable and evict the
token. All authenticated routes live under `/app/*`.

## Build / test / lint

```bash
npm run build        # tsc && vite build → dist/
npm run test:run     # vitest in browser mode (real Chromium via Playwright)
npm run e2e          # mocked Playwright e2e (boots dev server with MSW)
npm run lint         # eslint (incl. module-boundary rules)
```

### Testing harness

Component tests run in **Vitest browser mode** (real Chromium) with **MSW**
mocking the backend and **axe-core** for a11y. Use `renderWithProviders` and
`checkA11y` from `@/__tests__/test-utils`; add backend mocks via MSW handlers
(`src/mocks/handlers.ts` + per-module `mocks/handlers.ts`). Browser-mode ESM
forbids `vi.spyOn` on module exports — drive behaviour through MSW instead.

Run the SPA backendless against mocks (no backend on :8000):

```bash
VITE_ENABLE_MSW=1 npm run dev
```

First time (or after a Chromium bump): `npx playwright install chromium`.

## Structure

The layout mirrors the backend's module separation:

```
src/
  shared/      # design system (ui/), api client + codegen (api/), auth (auth/),
               # app shell + registries (app/), hooks/ — reusable, no module imports
    ui/        # 29 design-system primitives, each with a vitest browser-mode suite
    api/       # Bearer-JWT client, token-store, generated/ (codegen, committed)
    auth/      # AuthProvider/useAuth, AuthGuard, Login + ChangePassword pages
    app/       # Layout, QueryClient, routes.ts + nav.ts (additive registries)
  modules/     # feature modules; each is self-contained and reached via its index.ts
               # (added by the 8 follow-up PRs; mount under /app/<domain>)
  mocks/       # root MSW handlers (health + auth); modules append their own
```

### Adding a feature module (additive registries)

A feature PR plugs in by touching **one line per registry** (no merge conflicts):

- `shared/app/routes.ts` — one import + one `...xRoutes,` spread (paths are `/app`-relative).
- `shared/app/nav.ts` — replace your placeholder slot's one line (nav `to` is absolute `/app/<domain>`).
- `src/mocks/handlers.ts` — one import + one `...xHandlers,` spread (Mode A / Option B).

See `../../jentic-one-ui-migration/COLLABORATION.md` §3.

### Boundary rules (enforced by ESLint)

- `shared/` must **not** import from `modules/`.
- A module must **not** import from a sibling module — share via `@/shared`.
- Use `@/` absolute imports, never relative `../` parent traversal.

These echo the backend's `tests/arch/test_module_boundaries.py`. A violating
import is a lint **error**, so the boundary is checked in CI.

## Serving

`npm run build` emits `dist/`, which is packaged into the Python wheel
(`pyproject.toml` `force-include`) at `jentic_one/static/` and served by the
admin surface at `/`. Same-origin → no CORS. When no build is present the admin
surface runs API-only, unchanged.

> Supersedes the local-only `.scratch/dev-ui` scratch UI.

## Favicons, web-app manifest & social metadata

The tab icon, PWA install icons and Open Graph card all derive from a **single
source of truth**: the Jentic glyph paths (`LOGO_ICON_PATHS`) in
`src/shared/ui/Logo.tsx` — the same mark rendered in the app shell — so the tab
icon is pixel-identical to the in-app logo (issue #614).

`favicon.svg` is authored by hand (`prefers-color-scheme`-aware: mint on dark
chrome, brand-dark on light). The raster set is **generated deterministically**:

```bash
npm run gen:favicons   # writes the icon set + og-image into public/
```

This regenerates `favicon.ico` (16/32/48), `favicon-96x96.png`,
`apple-touch-icon.png` (opaque `#0E1A1D` plate — iOS masks transparency),
`web-app-manifest-{192,512}.png`, `icon-512-maskable.png` (glyph in the Android
adaptive-icon safe zone) and `og-image.png` (1200×630). Re-run it whenever the
brand mark or palette changes — the glyph paths and colours in
`scripts/gen-favicons.mjs` must stay in lockstep with `Logo.tsx` / `index.css`.

All assets live in `public/` so Vite copies them verbatim into `dist/` →
`jentic_one/static/` in the wheel, served under the `/app/` base. The `<head>`
links use root-absolute `/app/...` paths. Root probes browsers/iOS hardcode to
the site root (`/favicon.ico`, `/apple-touch-icon*.png`) are 307-redirected into
`/app` by the backend (`src/jentic_one/shared/web/static.py`).
