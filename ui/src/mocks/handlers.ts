import { http, HttpResponse } from 'msw';
import { toolkitsHandlers } from '@/modules/toolkits/mocks/handlers';
import { agentsHandlers } from '@/modules/agents/mocks/handlers';
import { discoverHandlers } from '@/modules/discover/mocks/handlers';
import { dashboardHandlers } from '@/modules/dashboard/mocks/handlers';
import { workspaceHandlers } from '@/modules/workspace/mocks/handlers';
import { credentialsHandlers, credentialsE2eHooks } from '@/modules/credentials/mocks/handlers';
import { railEventsHandlers } from '@/shared/app/rail/mocks/handlers';
import { monitorHandlers } from '@/modules/monitor/mocks/handlers';

/**
 * Root MSW handler table.
 *
 * Feature modules own their endpoints: each adds
 * `modules/<domain>/mocks/handlers.ts` and registers it here with a single
 * additive spread line (see jentic-one-ui-migration/COLLABORATION.md). Keep this
 * file's own handlers limited to cross-cutting endpoints (health/auth) that the
 * shell needs before any feature loads.
 *
 * The health endpoint is mocked at BOTH paths the backend can serve it from
 * (`/admin/health` combined, `/health` standalone) so tests pass regardless of
 * which `window.__APP_CONFIG__.healthPath` is active. See shared/config.ts.
 */

const healthOk = () =>
	HttpResponse.json({
		status: 'ok',
		surface: 'admin',
		setup_required: false,
		next_step: null,
	});

const MOCK_TOKEN = 'mock-access-token';

const mockUser = {
	id: '00000000-0000-0000-0000-000000000001',
	email: 'admin@local',
	first_name: 'Admin',
	last_name: 'User',
	active: true,
	permissions: ['org:admin'],
	must_change_password: false,
	created_at: '2026-01-01T00:00:00Z',
	updated_at: null,
};

/**
 * Seed for the actor directory (`GET /actors`). Ids mirror the actor_id values
 * other module fixtures emit (the dashboard access-request fixtures + the
 * agents store) so `<ActorLabel>` resolves to a name on those surfaces in
 * mocked dev/e2e. Covers all three actor types.
 */
const actorDirectorySeed = [
	{
		id: 'invoice-bot',
		actor_type: 'agent',
		name: 'Invoice Bot',
		active: true,
		created_at: '2026-01-01T00:00:00Z',
	},
	{
		id: 'support-triage',
		actor_type: 'agent',
		name: 'Support Triage',
		active: true,
		created_at: '2026-01-01T00:00:00Z',
	},
	{
		id: 'agnt_active_1',
		actor_type: 'agent',
		name: 'support-agent',
		active: true,
		created_at: '2026-01-01T00:00:00Z',
	},
	{
		id: 'usr_admin_1',
		actor_type: 'user',
		name: 'Admin User',
		active: true,
		created_at: '2026-01-01T00:00:00Z',
	},
	{
		id: 'sva_active_1',
		actor_type: 'service_account',
		name: 'metrics-exporter',
		active: true,
		created_at: '2026-01-01T00:00:00Z',
	},
];

export const handlers = [
	http.get('/admin/health', healthOk),
	http.get('/health', healthOk),
	// Runtime config the SPA fetches on boot (see shared/config.ts). Combined
	// mode is the dev default, so report its health path.
	http.get('/app-config.json', () => HttpResponse.json({ healthPath: '/admin/health' })),
	// Cross-cutting auth so the shell can reach an authenticated state in mocked
	// (Mode A) dev and e2e. Any email/password is accepted in mocks.
	http.post('/auth/login', () =>
		HttpResponse.json({
			access_token: MOCK_TOKEN,
			token_type: 'bearer',
			expires_in: 3600,
			must_change_password: false,
		}),
	),
	http.get('/users/me', ({ request }) => {
		const auth = request.headers.get('Authorization');
		if (auth !== `Bearer ${MOCK_TOKEN}`) {
			return new HttpResponse(null, { status: 401 });
		}
		return HttpResponse.json(mockUser);
	}),
	// Actor directory (GET /actors) — cross-cutting reference data the UI hydrates
	// once to resolve raw `actor_id` values into names. Seeded to match the ids
	// other module stores emit (agents store + the access-request fixtures) so
	// names resolve across the dashboard, access-request, and agent surfaces.
	http.get('/actors', () =>
		HttpResponse.json({
			data: actorDirectorySeed,
			has_more: false,
			next_cursor: null,
		}),
	),
	// Feature modules append their handlers here, e.g.:
	//   import { discoverHandlers } from '@/modules/discover/mocks/handlers';
	//   ...discoverHandlers,
	...toolkitsHandlers,
	...agentsHandlers,
	// Credentials registers before Discover so its guided-picker `/catalog`
	// handler (which falls through when its store is empty) gets a chance to
	// respond before Discover's static `/catalog` fixtures. Only `/catalog`
	// ordering is load-bearing — Discover defines no `/apis` handler, so the
	// `/apis` fallback comes from `dashboardHandlers` further down regardless.
	...credentialsHandlers,
	...discoverHandlers,
	...dashboardHandlers,
	...workspaceHandlers,
	...railEventsHandlers,
	// Monitor owns the full observability surface (/executions, /jobs, /events
	// + SSE, /audit). Several of these paths are ALSO mocked by the dashboard
	// and the ambient Agent Rail for their own shell widgets; those modules
	// register earlier, so in the running app their lighter fixtures answer
	// first. Monitor's own tests can't rely on global ordering, so they install
	// `monitorHandlers` at runtime via `worker.use(...)` (which takes
	// precedence and resets per-test) — see MonitorPage.test.tsx. Registering
	// here keeps the Monitor page working in mocked dev when no other module's
	// handler claimed the path.
	...monitorHandlers,
	// Extensibility seam: `handlers` is exported (not module-private) so a
	// consumer can compose `[...handlers, ...extraHandlers]`. MSW is
	// FIRST-MATCH-WINS, so a consumer must append at a DELIBERATE position —
	// appending after this array lets these fixtures answer first (safest
	// default); to override a path here, a consumer must register its handler
	// BEFORE this one in the composed array.
];

/**
 * Install DEV+MSW-only e2e test hooks on a global target (normally `window`).
 *
 * Mirrors the additive-registry shape of `handlers` above: each module that
 * needs deterministic e2e seeding exports its own `*E2eHooks` bundle, and this
 * root aggregates them under a single `__mswTestHooks` namespace. The app root
 * (`main.tsx`) calls this once and stays module-agnostic — it must never import
 * a module's `mocks/handlers` directly (COLLABORATION.md §1: shared/ never
 * imports modules/). New modules add one spread line here, exactly like a
 * handler.
 *
 * Caller is responsible for the DEV + VITE_ENABLE_MSW gate; this function does
 * not run in production builds because nothing references it there (tree-shaken).
 */
export function installE2eTestHooks(target: Record<string, unknown>): void {
	target.__mswTestHooks = {
		...credentialsE2eHooks,
	};
}
