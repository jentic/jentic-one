import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { QueryClientProvider } from '@tanstack/react-query';
import { MotionConfig } from 'framer-motion';
import { App } from '@/App';
import { createQueryClient } from '@/shared/app/query-client';
import { AuthProvider } from '@/shared/auth/AuthContext';
import { loadAppConfig } from '@/shared/config';
import '@/index.css';

// React Router basename, derived from Vite's `base` (`/app/`) so the SPA's URL
// namespace is single-sourced: change `base` in vite.config.ts and the router
// follows. `import.meta.env.BASE_URL` is `/app/`; the router wants no trailing
// slash (`/app`). Kept in lockstep with the backend mount (`SPA_MOUNT_PATH` in
// shared/web/static.py). Inside the app, all route paths and links are
// root-relative (e.g. `/credentials`); the basename prepends `/app`.
const ROUTER_BASENAME = import.meta.env.BASE_URL.replace(/\/$/, '');

async function enableMocking() {
	// Backendless dev (RUNBOOK Mode A): `VITE_ENABLE_MSW=1 npm run dev` starts the
	// MSW worker so the SPA runs against mock data with no backend on :8000.
	// Tree-shaken out of production builds — import.meta.env.DEV is false there.
	if (!import.meta.env.DEV || import.meta.env.VITE_ENABLE_MSW !== '1') return;
	const { worker } = await import('@/mocks/browser');
	// Expose module-contributed e2e test hooks (seed/reset deterministic
	// fixtures) on `window` so mocked e2e specs can drive them via
	// page.evaluate. Aggregated by the shared MSW root so this app root stays
	// module-agnostic — DEV + MSW only, tree-shaken from production builds.
	const { installE2eTestHooks } = await import('@/mocks/handlers');
	installE2eTestHooks(window as unknown as Record<string, unknown>);
	await worker.start({
		onUnhandledRequest: 'bypass',
		// Vite serves static assets (incl. the generated worker script) under
		// `base: '/app/'`, so the worker lives at `<base>mockServiceWorker.js`
		// and must claim that scope. Without this MSW probes the root URL, which
		// 404s under the base, registration fails, and the SPA never mounts.
		serviceWorker: { url: `${import.meta.env.BASE_URL}mockServiceWorker.js` },
	});
}

function mount() {
	const container = document.getElementById('root');
	if (!container) {
		throw new Error('Root container #root not found');
	}

	const queryClient = createQueryClient();

	// DEV + MSW only: expose the query client so mocked e2e specs can clear the
	// cache after re-seeding the mock stores (otherwise a query cached from an
	// earlier navigation masks freshly-seeded fixtures). Tree-shaken from prod.
	// Kept here (not in the MSW hook aggregator) on purpose: the QueryClient is
	// shell-owned app state created in this root, not a module mock — folding it
	// into `installE2eTestHooks` would couple the mocks root to app wiring.
	if (import.meta.env.DEV && import.meta.env.VITE_ENABLE_MSW === '1') {
		(window as unknown as Record<string, unknown>).__queryClient = queryClient;
	}

	createRoot(container).render(
		<StrictMode>
			<QueryClientProvider client={queryClient}>
				<BrowserRouter basename={ROUTER_BASENAME}>
					<AuthProvider>
						{/* `reducedMotion="user"` makes every framer-motion animation honour
						    the OS "reduce motion" setting (JS-driven transforms aren't
						    covered by the CSS reset in index.css). */}
						<MotionConfig reducedMotion="user">
							<App />
						</MotionConfig>
					</AuthProvider>
				</BrowserRouter>
			</QueryClientProvider>
		</StrictMode>,
	);
}

void enableMocking().then(loadAppConfig).then(mount);
