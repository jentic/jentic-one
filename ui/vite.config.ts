import { defineConfig, type Plugin, type ViteDevServer } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { rm } from 'node:fs/promises';

const __dirname = dirname(fileURLToPath(import.meta.url));

// The admin surface backend. Override with VITE_API_HOST when the dev server
// must reach the API by container name (e.g. inside docker compose).
const apiHost = process.env.VITE_API_HOST || 'http://localhost:8000';

// Dev-server proxy, INVERTED. The SPA bundle is served under `/app` in every
// deploy mode (see src/jentic_one/shared/web/static.py), so the namespace is
// cleanly split: `/app/*` belongs to the SPA, everything else is a backend
// call. Rather than hand-maintaining an allow-list of backend prefixes (which
// drifted from the routers — a missing prefix silently served index.html),
// proxy EVERYTHING to the backend except the paths the dev server itself owns:
//
//   * `/app` and `/app/*`         — the SPA (served by Vite in dev / the bundle
//                                    in prod), incl. the OAuth popup landing at
//                                    `/app/oauth/connected`.
//   * `/@…`, `/src/…`, `/node_modules/…`, `/.vite/…` — Vite dev internals.
//   * `/@id`, `/@fs`, `/@vite`    — Vite module-resolution endpoints.
//
// Anything else (`/auth`, `/credentials`, `/agents`, `/openapi.json`, the
// Monitor aggregation endpoint `/monitoring/executions` (#386), a brand new
// router added tomorrow, …) is proxied with zero config changes. This is
// drift-proof: adding a backend router needs no edit here.
//
// `/app-config.json` is the one root-level path the SPA fetches that the
// backend owns; it is NOT under `/app`, so the regex below proxies it (which is
// what we want — `npm run dev` against a real backend learns the deploy-mode
// health path; without a backend the SPA falls back to combined-mode defaults,
// see shared/config.ts).
const backendProxy = {
	// Match any path that does NOT start with an SPA- or Vite-owned prefix.
	'^/(?!app(?:/|$)|@|src/|node_modules/|\\.vite/)': {
		target: apiHost,
		changeOrigin: true,
	},
};

// Dev-only parity with the production backend's root handling. The combined app
// (shared/web/static.py) redirects bare `/` → `/app/` and serves the SPA at
// `/app`; Vite's dev server, however, 404s `/` and shows a "did you mean /app/"
// interstitial for the slashless `/app` (a base-URL mismatch). The router's
// basename index resolves to the slashless `/app`, so a hard refresh / reload
// on the dashboard would hit that interstitial. Normalise both to `/app/` so
// dev mirrors prod and deep-link reloads boot the SPA.
function appBaseRedirect(): Plugin {
	return {
		name: 'app-base-redirect',
		configureServer(server: ViteDevServer) {
			server.middlewares.use((req, res, next) => {
				const url = req.url ?? '';
				if (url === '/' || url === '/app') {
					res.writeHead(302, { Location: '/app/' });
					res.end();
					return;
				}
				next();
			});
		},
	};
}

// `mockServiceWorker.js` is the MSW request mock. It lives in `public/` so the
// dev server and the mocked Playwright e2e suite can register it at runtime,
// which means Vite copies it verbatim into the production `dist/`. But the
// shipped bundle is force-included into the Python wheel (see pyproject.toml
// → tool.hatch.build.targets.wheel.force-include), and hatchling's `exclude`
// does NOT apply to force-included paths — so the only place to keep this
// dev/test-only artifact out of the production package is here, at build time.
// Strip it from the build output (production builds only; dev keeps it).
function dropMockServiceWorker(): Plugin {
	return {
		name: 'drop-mock-service-worker',
		apply: 'build',
		async closeBundle() {
			await rm(resolve(__dirname, 'dist/mockServiceWorker.js'), { force: true });
		},
	};
}

export default defineConfig({
	resolve: {
		alias: {
			'@oss-internal': resolve(__dirname, 'src'),
		},
	},
	plugins: [react(), tailwindcss(), appBaseRedirect(), dropMockServiceWorker()],
	// The SPA is served under `/app` same-origin behind the admin API in every
	// deploy mode, so assets resolve from `/app/assets/...` regardless of the
	// current client-side route. This MUST stay in lockstep with the backend
	// mount (`SPA_MOUNT_PATH` in shared/web/static.py) and the React Router
	// `basename` (derived from `import.meta.env.BASE_URL` in src/main.tsx).
	// A relative './' base would break asset URLs on a refreshed deep link like
	// /app/agents/123, resolving them to /app/agents/assets.
	base: '/app/',
	build: { outDir: 'dist', emptyOutDir: true },
	server: {
		host: '0.0.0.0',
		proxy: backendProxy,
		// Extensibility seam: this config intentionally sets NO `server.fs`
		// restriction. The SPA source root (`ui/src`, the `@oss-internal` alias
		// target) can be consumed as a shared module graph by a separate host app
		// (which mounts it from this repo via its own alias plus a matching
		// `server.fs.allow` entry in that app's own vite config, not here).
		// Leaving `server.fs` unrestricted here avoids pre-emptively blocking that
		// cross-package mount; do not hardcode `server.fs` in a way that would.
	},
});
