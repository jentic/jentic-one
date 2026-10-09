import type { RouteObject } from 'react-router';

/**
 * Canonical client route paths, ALL root-relative to the router `basename`
 * (`/app`, set in `main.tsx` from Vite's `base`). A path like `/agents`
 * here resolves to `/app/agents` in the browser; the basename is the
 * single source of the `/app` prefix, so it appears in exactly one place
 * (vite.config.ts `base`) and never in route literals.
 *
 * The SPA is served under `/app` same-origin behind the admin API (see
 * src/jentic_one/shared/web/static.py), so the admin API's top-level prefixes
 * (/users, /auth, /jobs, /events, /audit, /executions, /permissions, /agents,
 * /credentials, …) live in a different namespace and can
 * never shadow a UI route on hard refresh.
 *
 * `login` / `changePassword` live outside the authenticated Layout — they must
 * be reachable before a session exists — but still under the `/app` basename
 * (→ `/app/login`, `/app/change-password`).
 */
export const ROUTES = {
	root: '/',
	login: '/login',
	// SSO authorization-code callback landing (outside the Layout/AuthGuard):
	// the browser returns here from the platform authorize flow with a one-time
	// code, which the page exchanges for a session (→ /app/auth/callback).
	authCallback: '/auth/callback',
	// First-run, no-credential setup. Lives at the root (outside /app and the
	// authenticated Layout): reachable before any account exists, and self-closes
	// once the first admin is created (see SetupPage / setup_required health gate).
	setup: '/setup',
	changePassword: '/change-password',
	// Authenticated app shell home — the basename index (`/app`), which
	// redirects to Agents (see App.tsx).
	app: '/',

	// ── Feature pages ────────────────────────────────────────────────────
	// Root-relative client paths for the primary feature surfaces, so call-sites
	// (nav, first-run checklist, cross-module links, back buttons) link by
	// a single shared constant instead of a scattered literal. These MUST stay
	// in lockstep with `nav.ts` and each module's `routes.tsx`. New surfaces
	// append here.
	// The Library is ONE page: the public Catalog with your workspace docked
	// beside it (`/library`, owned by the discover module), plus each workspace
	// API's hub under `/library/workspace/…` (owned by the workspace module —
	// build those with `ROUTE_PATHS.workspaceApiHub`). There is no workspace
	// list page: `/library/workspace`, and the retired `/discover` and
	// `/workspace` URLs, redirect to `/library` (see each module's routes.tsx).
	library: '/library',
	agents: '/agents',
	monitor: '/monitor',
	docs: '/docs',
} as const;

/**
 * Path prefix of every workspace API hub (`/library/workspace/:v/:n/:ver`).
 * Not a page of its own (it redirects to the Library) — never link to it bare.
 */
const WORKSPACE_API_HUB_BASE = '/library/workspace';

/**
 * Detail-route path builders for surfaces addressed by an id/sub-path. Kept as
 * functions (not literals) so callers can't forget to encode a segment.
 */
export const ROUTE_PATHS = {
	/**
	 * A workspace API's hub from its raw identity triple (each segment
	 * percent-encoded here), optionally opened on a tab. `tab` is the hub's URL
	 * vocabulary (`?tab=`).
	 */
	workspaceApiHub: (
		ref: { vendor: string; name: string; version: string },
		tab?: 'overview' | 'operations' | 'versions' | 'spec',
		opts?: { addCredential?: boolean },
	) => {
		const path = [ref.vendor, ref.name, ref.version].map(encodeURIComponent).join('/');
		const q = new URLSearchParams();
		if (tab && tab !== 'overview') q.set('tab', tab);
		// `credential=new` opens the hub's Add credential flow on this API's
		// form (read by the workspace module's `ApiHubOverview`).
		if (opts?.addCredential === true) q.set('credential', 'new');
		const qs = q.toString();
		return `${WORKSPACE_API_HUB_BASE}/${path}${qs ? `?${qs}` : ''}`;
	},
	/**
	 * The org-wide credential inventory, which lives in a sheet on the Agents
	 * page rather than at a route of its own. `credentials` is the Agents
	 * page's URL vocabulary (read by `modules/agents/pages/AgentsPage`);
	 * `credentials=new` also opens the create wizard, so a call-site whose
	 * label promises a new credential still lands on the form. The builder
	 * lives here because cross-module links (OAuth popup return, the shell)
	 * need it and modules can't import each other.
	 */
	credentialInventory: (opts?: { create?: boolean }) =>
		`${ROUTES.agents}?credentials=${opts?.create === true ? 'new' : '1'}`,
	/**
	 * One agent AS SELECTED on the Agents page — the one address of an agent for
	 * any caller that wants to show one. `/agents/:agentId` redirects here.
	 */
	agentTab: (agentId: string) => `${ROUTES.agents}?agent=${encodeURIComponent(agentId)}`,
	/**
	 * An agent's connect request opened for approval — the address the backend
	 * mints as the session's `approval_url` (`?approve=<sid>`, no poll token:
	 * the agent's owner or an org admin acts without it). With `agentId`, the
	 * requesting agent is also selected behind the dialog. Read by
	 * `modules/agents/pages/AgentsPage`.
	 */
	connectApproval: (sessionId: string, agentId?: string) => {
		const q = new URLSearchParams();
		if (agentId) q.set('agent', agentId);
		q.set('approve', sessionId);
		return `${ROUTES.agents}?${q}`;
	},
	/**
	 * Monitor's Activity view on API calls, optionally pre-filtered. The `show` /
	 * `actor_id` / `actor_type` names are Monitor's URL vocabulary (read by
	 * `modules/monitor/lib/useMonitorFilters`); the builder lives here because
	 * cross-module deep-links (Agents → Monitor) must agree on it, and modules
	 * can't import from each other. Monitor's own richer builder is
	 * `modules/monitor/lib/links`.
	 */
	monitorExecutions: (filter?: { actorId?: string; actorType?: 'agent' | 'user' }) => {
		const q = new URLSearchParams({ show: 'calls' });
		if (filter?.actorId) q.set('actor_id', filter.actorId);
		if (filter?.actorType) q.set('actor_type', filter.actorType);
		return `${ROUTES.monitor}?${q.toString()}`;
	},
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
 * `{ path: 'library', element: <LibraryPage/> }` mounts at `/app/library`.
 * The matching nav entry in `nav.ts` uses the absolute `/app/library`.
 */
// <-- feature route imports go here (one import line per module) -->
import { agentsRoutes } from '@/modules/agents/routes';
import { discoverRoutes } from '@/modules/discover/routes';
import { workspaceRoutes } from '@/modules/workspace/routes';
import { monitorRoutes } from '@/modules/monitor/routes';
import { settingsRoutes } from '@/modules/settings/routes';

export const moduleRoutes: RouteObject[] = [
	// <-- feature route spreads go here (one `...xRoutes,` line per module) -->
	...agentsRoutes,
	...discoverRoutes,
	...workspaceRoutes,
	...monitorRoutes,
	...settingsRoutes,
];
