/**
 * Monitor MSW handlers + fixtures.
 *
 * Mocks the observability surface the Monitor module consumes:
 *   GET   /executions                 — execution trace log
 *   GET   /executions/{execution_id}  — single execution
 *   GET   /jobs                       — async job queue
 *   GET   /jobs/{job_id}             — single job
 *   POST  /jobs/{job_id}:cancel       — cancel a job
 *   GET   /events                     — platform events
 *   PATCH /events/{event_id}          — acknowledge an event
 *   GET   /events/stream              — live event SSE (text/event-stream)
 *   GET   /audit                      — audit log (actor lens)
 *
 * Registered additively in src/mocks/handlers.ts (the sanctioned shared→module
 * bridge). Shapes mirror the generated response models so the typed client
 * deserializes them unchanged.
 */
import { http, HttpResponse } from 'msw';

// ── Rolling fixture clock ────────────────────────────────────────────────────
// Fixtures were authored against a fixed 2026-06-19 anchor. The Monitor filters
// derive a `from`/`since` lower bound from the time-window selector (e.g.
// days=30 → now-30d), so absolute fixture dates silently fall out of every
// window as real time passes — turning these mocks into a time-bomb that breaks
// the suite ~30 days after authoring. Rebase every fixture timestamp at module
// load so the dataset always sits just before "now", preserving the original
// relative ordering and gaps. Any ISO-8601 date/datetime string (or bare
// YYYY-MM-DD) is shifted by the same delta.
const FIXTURE_ANCHOR = Date.parse('2026-06-19T10:07:00Z'); // newest authored instant
const REBASE_DELTA_MS = Date.now() - 24 * 60 * 60 * 1000 - FIXTURE_ANCHOR;
const ISO_RE = /^\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?)?$/;

function shiftIso(value: string): string {
	const ms = Date.parse(value);
	if (Number.isNaN(ms)) return value;
	const shifted = new Date(ms + REBASE_DELTA_MS);
	// Preserve the original granularity: bare date vs full datetime.
	return value.length === 10 ? shifted.toISOString().slice(0, 10) : shifted.toISOString();
}

function rebaseFixture<T>(node: T): T {
	if (typeof node === 'string') {
		return (ISO_RE.test(node) ? shiftIso(node) : node) as unknown as T;
	}
	if (Array.isArray(node)) {
		return node.map((item) => rebaseFixture(item)) as unknown as T;
	}
	if (node && typeof node === 'object') {
		const out: Record<string, unknown> = {};
		for (const [k, v] of Object.entries(node)) out[k] = rebaseFixture(v);
		return out as T;
	}
	return node;
}

const TRACE_A = 'trace_aaaaaaaa';
const TRACE_B = 'trace_bbbbbbbb';

const EXECUTIONS = rebaseFixture([
	{
		_links: { self: '/executions/exec_1' },
		actor_id: 'agent_billing',
		actor_type: 'agent',
		api: { vendor: 'stripe', name: 'stripe-api', version: '2024-01-01', host: 'stripe.com' },
		created_at: '2026-06-19T10:00:00Z',
		duration_ms: 842,
		error: null,
		execution_id: 'exec_1',
		http_status: 200,
		operation_id: 'POST /v1/charges',
		pinned_revisions: null,
		started_at: '2026-06-19T10:00:00Z',
		status: 'completed',
		toolkit_id: 'tk_payments',
		trace_id: TRACE_A,
	},
	{
		_links: { self: '/executions/exec_2' },
		actor_id: 'user_admin',
		actor_type: 'user',
		api: { vendor: 'github', name: 'github-api', version: 'v3', host: 'github.com' },
		created_at: '2026-06-19T10:05:00Z',
		duration_ms: 0,
		error: 'Upstream 503 from github.com',
		execution_id: 'exec_2',
		http_status: 503,
		operation_id: 'GET /repos/{owner}/{repo}',
		pinned_revisions: null,
		started_at: '2026-06-19T10:05:00Z',
		status: 'failed',
		toolkit_id: 'tk_dev',
		trace_id: TRACE_B,
	},
	{
		_links: { self: '/executions/exec_3' },
		actor_id: 'agent_billing',
		actor_type: 'agent',
		api: null,
		created_at: '2026-06-19T10:06:00Z',
		duration_ms: 1240,
		error: null,
		execution_id: 'exec_3',
		http_status: 200,
		operation_id: 'POST /v1/refunds',
		pinned_revisions: null,
		started_at: '2026-06-19T10:06:00Z',
		status: 'completed',
		toolkit_id: 'tk_payments',
		trace_id: TRACE_A,
	},
	{
		// Header-less run: the backend stores `trace_id="unknown"`, so this row
		// must open by execution_id (no trace deep-link / no audit lens).
		_links: { self: '/executions/exec_4' },
		actor_id: 'agent_billing',
		actor_type: 'agent',
		api: { vendor: 'slack', name: 'slack-api', version: 'v2', host: 'slack.com' },
		created_at: '2026-06-19T10:07:00Z',
		duration_ms: 318,
		error: null,
		execution_id: 'exec_4',
		http_status: 200,
		operation_id: 'POST /chat.postMessage',
		pinned_revisions: null,
		started_at: '2026-06-19T10:07:00Z',
		status: 'completed',
		toolkit_id: 'tk_comms',
		trace_id: 'unknown',
	},
]);

// Aggregation fixture for GET /monitoring/executions (jentic-one#386). Built so
// totals are internally consistent (daily success+failed sums match totals).
const EXECUTION_STATS = rebaseFixture({
	total_executions: 184,
	success_rate_percent: 91.3,
	daily_buckets: [
		{ date: '2026-06-13', total: 22, success: 20, failed: 2 },
		{ date: '2026-06-14', total: 18, success: 17, failed: 1 },
		{ date: '2026-06-15', total: 31, success: 28, failed: 3 },
		{ date: '2026-06-16', total: 27, success: 25, failed: 2 },
		{ date: '2026-06-17', total: 24, success: 22, failed: 2 },
		{ date: '2026-06-18', total: 33, success: 31, failed: 2 },
		{ date: '2026-06-19', total: 29, success: 25, failed: 4 },
	],
	top_operations: [
		{
			api_vendor: 'stripe',
			api_name: 'stripe-api',
			operation_id: 'POST /v1/charges',
			total: 72,
			failed: 3,
		},
		{
			api_vendor: 'github',
			api_name: 'github-api',
			operation_id: 'GET /repos/{owner}/{repo}',
			total: 41,
			failed: 9,
		},
		{
			api_vendor: 'stripe',
			api_name: 'stripe-api',
			operation_id: 'POST /v1/refunds',
			total: 28,
			failed: 0,
		},
	],
});

const JOBS = rebaseFixture([
	{
		_links: { self: '/jobs/job_import_1', result: null, execution: null },
		created_at: '2026-06-19T09:50:00Z',
		error: null,
		execution_id: null,
		job_id: 'job_import_1',
		kind: 'import',
		status: 'running',
		updated_at: '2026-06-19T09:51:00Z',
	},
	{
		_links: { self: '/jobs/job_import_2', result: null, execution: '/executions/exec_1' },
		created_at: '2026-06-19T09:40:00Z',
		error: null,
		execution_id: 'exec_1',
		job_id: 'job_import_2',
		kind: 'import',
		status: 'completed',
		updated_at: '2026-06-19T09:45:00Z',
	},
	{
		_links: { self: '/jobs/job_exec_3', result: null, execution: null },
		created_at: '2026-06-19T09:30:00Z',
		error: 'Worker timed out',
		execution_id: null,
		job_id: 'job_exec_3',
		kind: 'execution',
		status: 'failed',
		updated_at: '2026-06-19T09:35:00Z',
	},
]);

const EVENTS = rebaseFixture([
	{
		_links: { self: '/events/evt_1', execution: '/executions/exec_2', job: null, action: null },
		acknowledged: false,
		acknowledged_at: null,
		acknowledged_by: null,
		created_at: '2026-06-19T10:05:01Z',
		data: { http_status: 503 },
		detail: 'GitHub returned 503 during execution exec_2.',
		event_id: 'evt_1',
		requires_action: true,
		severity: 'error',
		summary: 'Execution failed: github-api',
		trace_id: TRACE_B,
		type: 'execution.failed',
	},
	{
		_links: { self: '/events/evt_2', execution: null, job: '/jobs/job_import_2', action: null },
		acknowledged: true,
		acknowledged_at: '2026-06-19T09:46:00Z',
		acknowledged_by: 'admin@local',
		created_at: '2026-06-19T09:45:00Z',
		data: {},
		detail: 'Catalog import completed for stripe-api.',
		event_id: 'evt_2',
		requires_action: false,
		severity: 'info',
		summary: 'Import completed',
		trace_id: TRACE_A,
		type: 'import.completed',
	},
]);

const AUDIT = rebaseFixture([
	{
		action: 'execution.start',
		actor_id: 'user_admin',
		actor_session_id: 'sess_1',
		actor_type: 'user',
		after: null,
		before: null,
		diff: null,
		id: 'audit_1',
		ip_address: '10.0.0.1',
		job_id: null,
		occurred_at: '2026-06-19T10:00:00Z',
		reason: null,
		request_id: 'req_1',
		target_id: 'exec_1',
		target_parent_id: null,
		target_type: 'execution_record',
		trace_id: TRACE_A,
		user_agent: 'jentic-cli/1.0',
	},
	{
		action: 'job.cancel',
		actor_id: 'user_admin',
		actor_session_id: 'sess_1',
		actor_type: 'user',
		after: { status: 'cancelled' },
		before: { status: 'running' },
		diff: null,
		id: 'audit_2',
		ip_address: '10.0.0.1',
		job_id: 'job_import_1',
		occurred_at: '2026-06-19T09:52:00Z',
		reason: 'Manual cancel from Monitor',
		request_id: 'req_2',
		target_id: 'job_import_1',
		target_parent_id: null,
		target_type: 'job',
		trace_id: null,
		user_agent: 'Mozilla/5.0',
	},
]);

function paginate<T>(rows: T[]) {
	return { data: rows, has_more: false, next_cursor: null };
}

const ACTORS = rebaseFixture([
	{
		active: true,
		actor_type: 'agent',
		created_at: '2026-06-01T00:00:00Z',
		id: 'agent_billing',
		name: 'Billing Agent',
	},
	{
		active: true,
		actor_type: 'user',
		created_at: '2026-06-01T00:00:00Z',
		id: 'user_admin',
		name: 'Admin User',
	},
]);

/**
 * Cursor-aware pagination for the list handlers so the UI pager is exercised in
 * tests. Splits `rows` into pages of `limit`; the cursor is the 1-based index of
 * the first row on the next page.
 */
function paginateCursor<T>(rows: T[], cursor: string | null, limit: number) {
	const start = cursor ? Number(cursor) : 0;
	const page = rows.slice(start, start + limit);
	const nextStart = start + limit;
	const hasMore = nextStart < rows.length;
	return {
		data: page,
		has_more: hasMore,
		next_cursor: hasMore ? String(nextStart) : null,
	};
}

export const monitorHandlers = [
	http.get('/executions', ({ request }) => {
		const url = new URL(request.url);
		const traceId = url.searchParams.get('trace_id');
		const actorId = url.searchParams.get('actor_id');
		const from = url.searchParams.get('from');
		const statuses = url.searchParams.getAll('status');
		const cursor = url.searchParams.get('cursor');
		// Fixed small page size (independent of the client's limit) so the
		// three-row fixture spans two pages and exercises the cursor pager.
		const limit = 2;
		let rows = EXECUTIONS;
		if (traceId) rows = rows.filter((r) => r.trace_id === traceId);
		if (actorId) rows = rows.filter((r) => r.actor_id === actorId);
		if (from) rows = rows.filter((r) => r.started_at >= from);
		if (statuses.length) rows = rows.filter((r) => statuses.includes(r.status));
		return HttpResponse.json(paginateCursor(rows, cursor, limit));
	}),
	http.get('/executions/:id', ({ params }) => {
		const row = EXECUTIONS.find((r) => r.execution_id === String(params.id));
		return row ? HttpResponse.json(row) : new HttpResponse(null, { status: 404 });
	}),
	http.get('/monitoring/executions', ({ request }) => {
		// Honour the `days` window by slicing the trailing buckets so the chart
		// and totals shift with the selector (mirrors the real endpoint shape).
		const url = new URL(request.url);
		const days = Math.min(30, Math.max(1, Number(url.searchParams.get('days') ?? 7)));
		const buckets = EXECUTION_STATS.daily_buckets.slice(-days);
		const total = buckets.reduce((sum, b) => sum + b.total, 0);
		const success = buckets.reduce((sum, b) => sum + b.success, 0);
		return HttpResponse.json({
			...EXECUTION_STATS,
			daily_buckets: buckets,
			total_executions: total || EXECUTION_STATS.total_executions,
			success_rate_percent: total
				? (success / total) * 100
				: EXECUTION_STATS.success_rate_percent,
		});
	}),

	http.get('/jobs', ({ request }) => {
		const url = new URL(request.url);
		const statuses = url.searchParams.getAll('status');
		const kind = url.searchParams.get('kind');
		let rows = JOBS;
		if (kind) rows = rows.filter((r) => r.kind === kind);
		if (statuses.length) rows = rows.filter((r) => statuses.includes(r.status));
		return HttpResponse.json(paginate(rows));
	}),
	http.get('/jobs/:id', ({ params }) => {
		const row = JOBS.find((r) => r.job_id === String(params.id));
		return row ? HttpResponse.json(row) : new HttpResponse(null, { status: 404 });
	}),
	http.post('/jobs/*', ({ request }) => {
		const url = new URL(request.url);
		const tail = decodeURIComponent(url.pathname.replace(/^\/jobs\//, ''));
		if (!tail.endsWith(':cancel')) return new HttpResponse(null, { status: 404 });
		const jobId = tail.slice(0, -':cancel'.length);
		const row = JOBS.find((r) => r.job_id === jobId);
		if (!row) return new HttpResponse(null, { status: 404 });
		return HttpResponse.json({
			...row,
			status: 'cancelled',
			updated_at: '2026-06-19T10:10:00Z',
		});
	}),

	http.get('/events', ({ request }) => {
		const url = new URL(request.url);
		const acknowledged = url.searchParams.get('acknowledged');
		const requiresAction = url.searchParams.get('requires_action');
		const eventTypes = url.searchParams.getAll('event_type');
		const severities = url.searchParams.getAll('severity');
		const traceId = url.searchParams.get('trace_id');
		const actorId = url.searchParams.get('actor_id');
		const from = url.searchParams.get('from');
		const to = url.searchParams.get('to');
		let rows = EVENTS;
		if (acknowledged != null)
			rows = rows.filter((r) => String(r.acknowledged) === acknowledged);
		if (requiresAction != null)
			rows = rows.filter((r) => String(r.requires_action) === requiresAction);
		if (eventTypes.length) rows = rows.filter((r) => eventTypes.includes(r.type));
		if (severities.length) rows = rows.filter((r) => severities.includes(r.severity));
		if (traceId) rows = rows.filter((r) => r.trace_id === traceId);
		if (actorId) rows = rows.filter((r) => (r as { actor_id?: string }).actor_id === actorId);
		if (from) rows = rows.filter((r) => r.created_at >= from);
		if (to) rows = rows.filter((r) => r.created_at <= to);
		return HttpResponse.json(paginate(rows));
	}),
	http.patch('/events/:id', ({ params }) => {
		const row = EVENTS.find((r) => r.event_id === String(params.id));
		if (!row) return new HttpResponse(null, { status: 404 });
		return HttpResponse.json({
			...row,
			acknowledged: true,
			acknowledged_at: '2026-06-19T10:11:00Z',
			acknowledged_by: 'admin@local',
		});
	}),
	// Live SSE: mirror the backend's `/events/stream` framing — a `heartbeat`
	// frame (no event payload) interleaved with real `event: <type>` frames that
	// carry the EventResponse JSON. This locks in the regression fix: the client
	// must drop the heartbeat (it has no `severity`) and only forward real events.
	http.get('/events/stream', () => {
		const encoder = new TextEncoder();
		const stream = new ReadableStream({
			start(controller) {
				controller.enqueue(
					encoder.encode(
						`event: heartbeat\ndata: ${JSON.stringify({
							type: 'heartbeat',
							sent_at: '2026-06-19T10:12:00Z',
						})}\n\n`,
					),
				);
				for (const event of EVENTS) {
					controller.enqueue(
						encoder.encode(
							`event: ${event.type}\nid: ${event.event_id}\ndata: ${JSON.stringify(event)}\n\n`,
						),
					);
				}
				controller.enqueue(encoder.encode(': keep-alive\n\n'));
				// Leave open; tests/dev abort via the client's AbortController.
			},
		});
		return new HttpResponse(stream, {
			headers: { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' },
		});
	}),

	http.get('/audit', ({ request }) => {
		const url = new URL(request.url);
		const targetType = url.searchParams.get('target_type');
		const targetId = url.searchParams.get('target_id');
		const actorId = url.searchParams.get('actor_id');
		const since = url.searchParams.get('since');
		const until = url.searchParams.get('until');
		let rows = AUDIT;
		// Mirror the backend: target_type and target_id must be supplied together
		// (a lone target id is rejected with 400 invalid_input).
		if ((targetType == null) !== (targetId == null)) {
			return HttpResponse.json(
				{
					type: 'invalid_input',
					detail: 'target_type and target_id must both be provided',
				},
				{ status: 400 },
			);
		}
		if (targetType) rows = rows.filter((r) => r.target_type === targetType);
		if (targetId) rows = rows.filter((r) => r.target_id === targetId);
		if (actorId) rows = rows.filter((r) => r.actor_id === actorId);
		if (since) rows = rows.filter((r) => r.occurred_at >= since);
		if (until) rows = rows.filter((r) => r.occurred_at <= until);
		return HttpResponse.json({ data: rows, has_more: false, next_cursor: null });
	}),

	http.get('/actors', () => {
		return HttpResponse.json({ data: ACTORS, has_more: false, next_cursor: null });
	}),
];
