/**
 * Dashboard MSW handlers + fixtures.
 *
 * Dashboard has no endpoint of its own — it composes four EXISTING list
 * endpoints owned by other domains:
 *   GET /agents?status=pending      — agents awaiting approval
 *   GET /events?requires_action=true — actionable alerts
 *   GET /executions                 — recent activity / success rate
 *   GET /apis                       — registered API count
 *
 * These are NOT gap-filling mocks (the endpoints are real — verified against
 * the running backend); they exist so Dashboard's overview renders in Mode A
 * and in component tests. At full integration the real backend serves these
 * paths. In Mode A, several of these paths (notably `/agents`) are ALSO
 * registered by sibling modules in the root table, and MSW v2 resolves
 * FIRST-MATCH-WINS — and `...dashboardHandlers` is spread AFTER the siblings,
 * so a sibling can win `/agents` here. Tests therefore drive specific states
 * with `worker.use(...)` (which PREPENDS, so it always wins). See the
 * `seedDashboard()` helper in DashboardPage.test.tsx.
 *
 * Registered additively in src/mocks/handlers.ts. Shapes mirror the generated
 * response models so the typed client deserializes them unchanged.
 */
import { http, HttpResponse } from 'msw';

const now = Date.now();
const minutesAgo = (m: number) => new Date(now - m * 60_000).toISOString();

export const dashboardPendingAgents = [
	{
		id: 'agent_1',
		name: 'invoice-bot',
		description: 'Reconciles invoices nightly',
		status: 'pending',
		registered_by: 'user_1',
		created_at: minutesAgo(12),
	},
	{
		id: 'agent_2',
		name: 'support-triage',
		description: null,
		status: 'pending',
		registered_by: 'user_1',
		created_at: minutesAgo(90),
	},
];

export const dashboardActionableEvents = [
	{
		_links: { self: '/events/evt_1' },
		acknowledged: false,
		created_at: minutesAgo(5),
		detail: 'Stripe credential rejected 3 calls',
		event_id: 'evt_1',
		requires_action: true,
		severity: 'error',
		summary: 'Credential failing',
		type: 'credential.error',
	},
	{
		_links: { self: '/events/evt_2' },
		acknowledged: false,
		created_at: minutesAgo(40),
		detail: null,
		event_id: 'evt_2',
		requires_action: true,
		severity: 'warning',
		summary: 'Rate limit approaching',
		type: 'execution.rate_limit',
	},
];

export const dashboardExecutions = [
	{
		_links: { self: '/executions/exec_1' },
		created_at: minutesAgo(2),
		duration_ms: 142,
		execution_id: 'exec_1',
		http_status: 200,
		operation_id: 'charges/create',
		started_at: minutesAgo(2),
		status: 'completed',
		credential_id: 'cred_payments',
		credential_name: 'Stripe key',
		trace_id: 'trace_1',
	},
	{
		_links: { self: '/executions/exec_2' },
		created_at: minutesAgo(8),
		duration_ms: 87,
		execution_id: 'exec_2',
		http_status: 500,
		operation_id: 'repos/get',
		started_at: minutesAgo(8),
		status: 'failed',
		credential_id: 'cred_dev',
		credential_name: 'GitHub PAT',
		trace_id: 'trace_2',
	},
	{
		_links: { self: '/executions/exec_3' },
		created_at: minutesAgo(15),
		duration_ms: 203,
		execution_id: 'exec_3',
		http_status: 200,
		operation_id: 'messages/send',
		started_at: minutesAgo(15),
		status: 'completed',
		credential_id: 'cred_comms',
		credential_name: 'Slack bot token',
		trace_id: 'trace_3',
	},
];

export const dashboardApis = [
	{
		api: { vendor: 'stripe', name: 'stripe-api', version: '2024-01-01', host: 'stripe.com' },
		display_name: 'Stripe',
		description: 'Payments APIs.',
		icon_url: null,
		current_revision_id: 'rev_1',
		revision_count: 1,
		operation_count: 412,
		security_schemes: ['bearer'],
		created_at: minutesAgo(5000),
		updated_at: minutesAgo(100),
		_links: { self: '/apis/stripe/stripe-api/2024-01-01' },
	},
	{
		api: { vendor: 'github', name: 'github-api', version: '1.1.4', host: 'github.com' },
		display_name: 'GitHub',
		description: 'GitHub REST API.',
		icon_url: null,
		current_revision_id: 'rev_2',
		revision_count: 1,
		operation_count: 900,
		security_schemes: ['bearer'],
		created_at: minutesAgo(5000),
		updated_at: minutesAgo(200),
		_links: { self: '/apis/github/github-api/1.1.4' },
	},
];

export const dashboardHandlers = [
	http.get('/agents', ({ request }) => {
		const status = new URL(request.url).searchParams.get('status');
		// Two dashboard queries hit this path: the approval queue
		// (`?status=pending`) and the first-run probe (`?limit=1`, NO status —
		// "does this workspace have any agents at all?"). Both must see the
		// fixture agents: a pending agent IS an agent, so the probe returning
		// empty would wrongly flip the page to the setup checklist.
		const data = status === null || status === 'pending' ? dashboardPendingAgents : [];
		return HttpResponse.json({ data, has_more: false, next_cursor: null });
	}),

	http.get('/events', ({ request }) => {
		const requiresAction = new URL(request.url).searchParams.get('requires_action');
		// The Dashboard action inbox only consumes the actionable slice. The rail's
		// full backlog query (no `requires_action`) is not ours — fall through so
		// the shared rail handler serves it instead of returning an empty page.
		if (requiresAction !== 'true') return undefined;
		return HttpResponse.json({
			data: dashboardActionableEvents,
			has_more: false,
			next_cursor: null,
		});
	}),

	http.get('/executions', () =>
		HttpResponse.json({ data: dashboardExecutions, has_more: false, next_cursor: null }),
	),

	http.get('/apis', () =>
		HttpResponse.json({ data: dashboardApis, has_more: false, next_cursor: null }),
	),
];

/* ------------------------------------------------------------------ */
/* Gateway health fixtures — GET /monitoring/usage (module tests only) */
/* ------------------------------------------------------------------ */

/**
 * A deterministic `/monitoring/usage` fixture for DASHBOARD MODULE TESTS.
 *
 * Deliberately NOT part of `dashboardHandlers` (and therefore not in the
 * global root table): `monitorHandlers` already serves `/monitoring/usage`
 * with a richer windowing mock for mocked dev, and `dashboardHandlers`
 * registers EARLIER in the root table — including this here would shadow
 * Monitor's fixture first-match-wins and change that page's dev data.
 * Dashboard tests install it per-test via `worker.use(...)` instead.
 *
 * Numbers are internally consistent: buckets sum to the stats block
 * (200 total = 188 success + 10 failed + 2 in-flight), and each group's
 * top rows sum to ≤ the window totals.
 */
export function buildDashboardUsageFixture(overrides?: {
	stats?: Partial<{
		total: number;
		success: number;
		failed: number;
		avg_ms: number;
		p50_ms: number | null;
		p95_ms: number | null;
		active_now: number;
		pending: number;
	}>;
	empty?: boolean;
}) {
	return http.get('/monitoring/usage', ({ request }) => {
		const url = new URL(request.url);
		const nowSec = Math.floor(Date.now() / 60_000) * 60;
		const untilParam = url.searchParams.get('until');
		const until = untilParam != null ? Number(untilParam) : nowSec;
		const sinceParam = url.searchParams.get('since');
		const since = sinceParam != null ? Number(sinceParam) : until - 86_400;
		const groupBy = url.searchParams.get('group_by') ?? 'api';

		if (overrides?.empty) {
			return HttpResponse.json({
				since,
				until,
				bucket_seconds: 3_600,
				group_by: groupBy,
				stats: {
					total: 0,
					success: 0,
					failed: 0,
					avg_ms: 0,
					p50_ms: null,
					p95_ms: null,
					active_now: 0,
					pending: 0,
				},
				buckets: [],
				top: [],
			});
		}

		// Six evenly-spaced buckets across the requested window, so every
		// range (24h/7d/30d) renders a populated volume + trend chart.
		const step = Math.max(60, Math.floor((until - since) / 6));
		const shape = [
			{ total: 30, success: 28, failed: 2, avg_ms: 420 },
			{ total: 42, success: 40, failed: 2, avg_ms: 385 },
			{ total: 28, success: 26, failed: 2, avg_ms: 510 },
			{ total: 36, success: 34, failed: 2, avg_ms: 440 },
			{ total: 40, success: 39, failed: 1, avg_ms: 400 },
			{ total: 24, success: 21, failed: 1, avg_ms: 455 },
		];
		const buckets = shape.map((b, i) => ({ ts: since + i * step, ...b }));

		const TOP: Record<string, Array<Record<string, unknown>>> = {
			api: [
				{
					key: 'stripe/stripe-api',
					label: 'stripe/stripe-api',
					total: 120,
					success: 116,
					failed: 4,
					avg_ms: 412,
					trend: [6, 9, 8, 12, 7, 10, 9, 11, 8, 9, 6, 5],
				},
				{
					key: 'github/github-api',
					label: 'github/github-api',
					total: 80,
					success: 72,
					failed: 6,
					avg_ms: 655,
					trend: [3, 4, 6, 5, 4, 3, 5, 6, 4, 5, 4, 3],
				},
			],
			credential: [
				{
					key: 'cred_payments',
					label: 'cred_payments',
					total: 120,
					success: 116,
					failed: 4,
					avg_ms: 430,
					trend: [6, 9, 8, 12, 7, 10, 9, 11, 8, 9, 6, 5],
				},
				{
					key: 'cred_dev',
					label: 'cred_dev',
					total: 80,
					success: 72,
					failed: 6,
					avg_ms: 655,
					trend: [3, 4, 6, 5, 4, 3, 5, 6, 4, 5, 4, 3],
				},
			],
			agent: [
				{
					key: 'agent/invoice-bot',
					label: 'agent/invoice-bot',
					total: 150,
					success: 142,
					failed: 8,
					avg_ms: 445,
					trend: [8, 11, 10, 14, 9, 12, 11, 13, 10, 11, 8, 7],
				},
				{
					key: null,
					label: null,
					total: 50,
					success: 46,
					failed: 2,
					avg_ms: 380,
					trend: [2, 3, 2, 4, 3, 2, 3, 2, 3, 4, 2, 2],
				},
			],
		};

		return HttpResponse.json({
			since,
			until,
			bucket_seconds: step,
			group_by: groupBy,
			stats: {
				total: 200,
				success: 188,
				failed: 10,
				avg_ms: 433,
				p50_ms: 388,
				p95_ms: 1240,
				active_now: 3,
				pending: 2,
				...overrides?.stats,
			},
			buckets,
			top: TOP[groupBy] ?? [],
		});
	});
}
