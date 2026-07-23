import type { RouteObject } from 'react-router-dom';

/**
 * Canonical client route paths, ALL root-relative to the router `basename`
 * (`/app`, set in `main.tsx` from Vite's `base`). A path like `/credentials`
 * here resolves to `/app/credentials` in the browser; the basename is the
 * single source of the `/app` prefix, so it appears in exactly one place
 * (vite.config.ts `base`) and never in route literals.
 *
 * The SPA is served under `/app` same-origin behind the admin API (see
 * src/jentic_one/shared/web/static.py), so the admin API's top-level prefixes
 * (/users, /auth, /jobs, /events, /audit, /executions, /permissions, /agents,
 * /service-accounts, /credentials, …) live in a different namespace and can
 * never shadow a UI route on hard refresh.
 *
 * `login` / `changePassword` live outside the authenticated Layout — they must
 * be reachable before a session exists — but still under the `/app` basename
 * (→ `/app/login`, `/app/change-password`).
 */
export const ROUTES = {
	root: '/',
	login: '/login',
	// First-run, no-credential setup. Lives at the root (outside /app and the
	// authenticated Layout): reachable before any account exists, and self-closes
	// once the first admin is created (see SetupPage / setup_required health gate).
	setup: '/setup',
	changePassword: '/change-password',
	// Authenticated app shell home (Dashboard) — the basename index (`/app`).
	app: '/',

	// ── Feature pages ────────────────────────────────────────────────────
	// Root-relative client paths for the primary feature surfaces, so call-sites
	// (nav, dashboard quick-actions, cross-module links, back buttons) link by
	// a single shared constant instead of a scattered literal. These MUST stay
	// in lockstep with `nav.ts` and each module's `routes.tsx`. New surfaces
	// append here.
	discover: '/discover',
	workspace: '/workspace',
	toolkits: '/toolkits',
	credentials: '/credentials',
	agents: '/agents',
	monitor: '/monitor',
	accessRequests: '/access-requests',
	docs: '/docs',
} as const;

/**
 * Detail-route path builders for surfaces addressed by an id/sub-path. Kept as
 * functions (not literals) so callers can't forget to encode a segment; mirrors
 * each module's own encoder (e.g. workspace's `encodeApiId`). The `apiPath`
 * arg is already the encoded `:vendor/:name/:version` triple.
 */
export const ROUTE_PATHS = {
	workspaceApi: (apiPath: string) => `${ROUTES.workspace}/${apiPath}`,
	toolkit: (toolkitId: string) => `${ROUTES.toolkits}/${encodeURIComponent(toolkitId)}`,
	agent: (agentId: string) => `${ROUTES.agents}/${encodeURIComponent(agentId)}`,
	serviceAccount: (serviceAccountId: string) =>
		`${ROUTES.agents}/service-accounts/${encodeURIComponent(serviceAccountId)}`,
} as const;

/**
 * Module route registry — APPEND-ONLY.
 *
 * Each feature PR adds exactly TWO lines:
 *   1. an import of its `routes` array at the top of this file, and
 *   2. a `...featureRoutes` spread inside `moduleRoutes` below.
 * Nothing else in this file should change, so parallel PRs never collide here.
 *
 * Route `path`s here are RELATIVE to the `/app` shell (no leading slash), e.g.
 * `{ path: 'discover', element: <DiscoverPage/> }` mounts at `/app/discover`.
 * The matching nav entry in `nav.ts` uses the absolute `/app/discover`.
 */
// <-- feature route imports go here (one import line per module) -->
import { dashboardRoutes } from '@/modules/dashboard/routes';
import { toolkitRoutes } from '@/modules/toolkits/routes';
import { agentsRoutes } from '@/modules/agents/routes';
import { discoverRoutes } from '@/modules/discover/routes';
import { workspaceRoutes } from '@/modules/workspace/routes';
import { credentialsRoutes } from '@/modules/credentials/routes';
import { monitorRoutes } from '@/modules/monitor/routes';

export const moduleRoutes: RouteObject[] = [
	// <-- feature route spreads go here (one `...xRoutes,` line per module) -->
	...dashboardRoutes,
	...toolkitRoutes,
	...agentsRoutes,
	...discoverRoutes,
	...workspaceRoutes,
	...credentialsRoutes,
	...monitorRoutes,
];
