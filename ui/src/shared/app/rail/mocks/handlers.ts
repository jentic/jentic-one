/**
 * Agent Rail MSW handlers + in-memory event store.
 *
 * Backs the rail's real `/events` contract in mocked (Mode A) dev + tests:
 *   GET   /events                        → backlog page (cursor-paginated)
 *   PATCH /events/{id}                    → acknowledge, returns the updated row
 *   GET   /events/stream                  → SSE; emits the seeded backlog then idles
 *   GET   /access-requests/{id}           → the request behind a filed event
 *   POST  /access-requests/{id}:decide    → approve/deny items (records the call)
 *
 * Shapes match the generated `EventResponse` model. Registered additively in
 * src/mocks/handlers.ts (see jentic-one-ui-migration/COLLABORATION.md).
 */
import { http, HttpResponse } from 'msw';

interface EventRow {
	event_id: string;
	type: string;
	severity: 'info' | 'warning' | 'error' | 'critical';
	summary: string;
	detail: string | null;
	created_at: string;
	requires_action: boolean;
	acknowledged: boolean;
	acknowledged_at: string | null;
	acknowledged_by: string | null;
	trace_id: string | null;
	data: Record<string, unknown>;
	_links: {
		self: string;
		action?: string | null;
		execution?: string | null;
		job?: string | null;
	};
}

const ago = (sec: number) => new Date(Date.now() - sec * 1000).toISOString();

function seed(
	over: Partial<EventRow> & Pick<EventRow, 'event_id' | 'type' | 'severity' | 'summary'>,
): EventRow {
	return {
		detail: null,
		created_at: ago(10),
		requires_action: false,
		acknowledged: false,
		acknowledged_at: null,
		acknowledged_by: null,
		trace_id: null,
		data: {},
		_links: { self: `/events/${over.event_id}` },
		...over,
	};
}

let events: EventRow[] = [];

interface AccessRequestItemRow {
	id: string;
	resource_type: string;
	action: string;
	resource_id: string | null;
	status: string;
	decided_by: string | null;
	decided_at: string | null;
	decision_reason: string | null;
	rules?: Record<string, unknown>[] | null;
	to_type?: string | null;
	to_id?: string | null;
}
interface AccessRequestRow {
	id: string;
	actor_id: string;
	status: string;
	reason: string | null;
	requested_by: string;
	approve_url: string;
	filed_at: string;
	expires_at: string;
	created_by: string;
	filer_owner_id: string | null;
	items: AccessRequestItemRow[];
}

const inHours = (h: number) => new Date(Date.now() + h * 3_600_000).toISOString();

function arItem(
	over: Partial<AccessRequestItemRow> &
		Pick<AccessRequestItemRow, 'id' | 'resource_type' | 'action' | 'status'>,
): AccessRequestItemRow {
	return {
		resource_id: null,
		decided_by: null,
		decided_at: null,
		decision_reason: null,
		...over,
	};
}

let accessRequests: AccessRequestRow[] = [];

/** Records of the decisions the mock received, for test assertions. */
export const decideCalls: {
	request_id: string;
	items: { item_id: string; decision: string; decision_reason: string | null }[];
}[] = [];

export function resetRailEventsStore(): void {
	decideCalls.length = 0;
	accessRequests = [
		{
			id: 'ar_1',
			actor_id: 'agnt_active_1',
			status: 'pending',
			reason: 'agent requested repo:read + secret:read',
			requested_by: 'usr_admin_1',
			approve_url: 'https://app.example.test/access-requests/ar_1',
			filed_at: ago(18),
			expires_at: inHours(24),
			created_by: 'usr_admin_1',
			filer_owner_id: null,
			items: [
				arItem({
					id: 'ari_1',
					resource_type: 'toolkit',
					action: 'use',
					status: 'pending',
				}),
				arItem({
					id: 'ari_2',
					resource_type: 'credential',
					action: 'bind',
					to_type: 'toolkit',
					to_id: 'tk_github',
					status: 'pending',
					// A deliberately LARGE allow/block grant (~100 operations) so the
					// OperationsDialog + its search/scroll are exercised in dev. The
					// first allow op stays `repos/get` so existing assertions hold.
					rules: bigGitHubRules(),
				}),
				arItem({
					id: 'ari_scope',
					resource_type: 'scope',
					action: 'grant',
					resource_id: 'capabilities:execute',
					status: 'pending',
				}),
			],
		},
		{
			id: 'ar_2',
			actor_id: 'agnt_active_2',
			status: 'pending',
			reason: 'agent needs to call the payments API',
			requested_by: 'usr_admin_1',
			approve_url: 'https://app.example.test/access-requests/ar_2',
			filed_at: ago(120),
			expires_at: inHours(24),
			created_by: 'usr_admin_1',
			filer_owner_id: null,
			items: [
				arItem({
					id: 'ari_3',
					resource_type: 'toolkit',
					action: 'use',
					status: 'pending',
				}),
			],
		},
		// Decided history for agnt_active_1 — backs the card's Approved / Denied /
		// All filters (#619). The detail card defaults to pending, so these only
		// surface when an operator pulls up the actor's history.
		{
			id: 'ar_3',
			actor_id: 'agnt_active_1',
			status: 'approved',
			reason: 'agent needed read access to the analytics toolkit',
			requested_by: 'usr_admin_1',
			approve_url: 'https://app.example.test/access-requests/ar_3',
			filed_at: ago(86_400),
			expires_at: inHours(24),
			created_by: 'usr_admin_1',
			filer_owner_id: null,
			items: [
				arItem({
					id: 'ari_4',
					resource_type: 'toolkit',
					action: 'use',
					status: 'approved',
					decided_by: 'usr_admin_1',
					decided_at: ago(82_800),
				}),
			],
		},
		{
			id: 'ar_4',
			actor_id: 'agnt_active_1',
			status: 'denied',
			reason: 'agent requested org:admin — out of policy',
			requested_by: 'usr_admin_1',
			approve_url: 'https://app.example.test/access-requests/ar_4',
			filed_at: ago(172_800),
			expires_at: inHours(24),
			created_by: 'usr_admin_1',
			filer_owner_id: null,
			items: [
				arItem({
					id: 'ari_5',
					resource_type: 'org',
					action: 'admin',
					status: 'denied',
					decided_by: 'usr_admin_1',
					decided_at: ago(169_200),
					decision_reason: 'org:admin is never granted to agents',
				}),
			],
		},
	];
	events = [
		seed({
			event_id: 'evt_exec_failed_1',
			type: 'execution.failed',
			severity: 'critical',
			summary: 'Execution failed: slack.postMessage',
			detail: 'scope violation: chat:write',
			requires_action: true,
			created_at: ago(8),
			trace_id: 'tr_1',
			data: { trace_id: 'tr_1', execution_id: 'exec_1', toolkit_id: 'slack' },
			_links: { self: '/events/evt_exec_failed_1', execution: '/executions/exec_1' },
		}),
		seed({
			event_id: 'evt_access_filed_1',
			type: 'access_request.filed',
			// Mirror the backend: filed events are emitted at INFO severity
			// (service.py). The View/Deny actions must still render because the
			// event requires a decision — see issue #652.
			severity: 'info',
			summary: 'Access request filed: github read',
			detail: 'agent requested repo:read',
			requires_action: true,
			created_at: ago(18),
			data: { request_id: 'ar_1', agent_id: 'agnt_active_1' },
		}),
		seed({
			event_id: 'evt_import_done_1',
			type: 'import.completed',
			severity: 'info',
			summary: 'Import completed: petstore',
			created_at: ago(27),
			data: { job_id: 'job_1' },
			_links: { self: '/events/evt_import_done_1', job: '/jobs/job_1' },
		}),
		seed({
			event_id: 'evt_exec_done_1',
			type: 'execution.completed',
			severity: 'info',
			summary: 'Execution completed: github.repos.list',
			created_at: ago(36),
			trace_id: 'tr_2',
			data: { trace_id: 'tr_2', execution_id: 'exec_2' },
			_links: { self: '/events/evt_exec_done_1', execution: '/executions/exec_2' },
		}),
	];
}

resetRailEventsStore();

/**
 * A realistic large allow/block grant — the case the OperationsDialog exists to
 * handle. ~100 operationIds across a couple of REST resources so search ("repos",
 * "issues", "DELETE") and the scroll region get a genuine workout in dev.
 */
function bigGitHubRules(): Record<string, unknown>[] {
	const resources = ['repos', 'issues', 'pulls', 'actions', 'teams', 'orgs'];
	const verbs = ['get', 'list', 'create', 'update', 'listForOrg', 'listForUser'];
	const allowOps: string[] = [];
	for (const r of resources) {
		for (const v of verbs) allowOps.push(`${r}/${v}`);
	}
	// Pad out to ~100 so the scroll + filter are visibly exercised.
	for (let i = allowOps.length; i < 100; i++) allowOps.push(`misc/op_${i}`);
	return [
		{ effect: 'allow', methods: ['GET', 'POST'], path: '/repos/*', operations: allowOps },
		{
			effect: 'deny',
			methods: ['DELETE'],
			operations: ['repos/delete', 'orgs/delete', 'teams/delete'],
		},
		{ effect: 'require-approval', methods: ['POST'], operations: ['repos/transfer'] },
	];
}

export const railEventsHandlers = [
	http.get('/events', ({ request }) => {
		const url = new URL(request.url);
		const cursor = url.searchParams.get('cursor');
		const limit = Number(url.searchParams.get('limit') ?? '25');
		const sorted = [...events].sort(
			(a, b) => Date.parse(b.created_at) - Date.parse(a.created_at),
		);
		const start = cursor ? sorted.findIndex((e) => e.event_id === cursor) + 1 : 0;
		const slice = sorted.slice(start, start + limit);
		const nextIdx = start + limit;
		return HttpResponse.json({
			data: slice,
			has_more: nextIdx < sorted.length,
			next_cursor: nextIdx < sorted.length ? slice[slice.length - 1]?.event_id : null,
		});
	}),
	http.patch('/events/:id', async ({ params, request }) => {
		const body = (await request.json().catch(() => ({}))) as {
			acknowledged?: boolean;
			note?: string | null;
		};
		const row = events.find((e) => e.event_id === params.id);
		if (!row) return new HttpResponse(null, { status: 404 });
		row.acknowledged = body.acknowledged ?? true;
		row.acknowledged_at = row.acknowledged ? new Date().toISOString() : null;
		return HttpResponse.json(row);
	}),
	// SSE — emit a heartbeat (which the client must ignore) + the current backlog
	// as `data:` frames, then keep the stream open.
	http.get('/events/stream', () => {
		const heartbeat = `event: heartbeat\ndata: ${JSON.stringify({
			type: 'heartbeat',
			sent_at: new Date().toISOString(),
		})}\n\n`;
		const frames =
			heartbeat +
			events
				.map((e) => `event: ${e.type}\nid: ${e.event_id}\ndata: ${JSON.stringify(e)}\n\n`)
				.join('');
		// Emit the seeded backlog, then HOLD the connection open (like the real
		// backend's 5s poll loop) instead of closing immediately — a closing body
		// would trip the client's reconnect loop into re-fetching the same backlog
		// every second. The stream stays open until the client aborts (unmount).
		const encoder = new TextEncoder();
		const stream = new ReadableStream<Uint8Array>({
			start(controller) {
				controller.enqueue(encoder.encode(frames));
				// Intentionally never close — keep-alive heartbeat keeps it live.
				const id = setInterval(() => {
					try {
						controller.enqueue(
							encoder.encode(
								`event: heartbeat\ndata: ${JSON.stringify({
									type: 'heartbeat',
									sent_at: new Date().toISOString(),
								})}\n\n`,
							),
						);
					} catch {
						clearInterval(id);
					}
				}, 5_000);
			},
		});
		return new HttpResponse(stream, {
			headers: { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' },
		});
	}),
	// Access-request LIST (the durable approval queue). Supports the `status`
	// filter, the per-actor `actor_id` filter (the agent / service-account detail
	// card, #619), and cursor pagination the real endpoint exposes.
	http.get('/access-requests', ({ request }) => {
		const url = new URL(request.url);
		const status = url.searchParams.get('status');
		const actorId = url.searchParams.get('actor_id');
		const limit = Number(url.searchParams.get('limit') ?? '50');
		const filtered = accessRequests.filter(
			(r) => (!status || r.status === status) && (!actorId || r.actor_id === actorId),
		);
		const sorted = [...filtered].sort(
			(a, b) => Date.parse(b.filed_at) - Date.parse(a.filed_at),
		);
		const slice = sorted.slice(0, limit);
		return HttpResponse.json({
			data: slice,
			has_more: sorted.length > limit,
			next_cursor: sorted.length > limit ? slice[slice.length - 1]?.id : null,
		});
	}),
	// Access-request decision flow (the rail's real "feed the agent back" path).
	// The `:decide` verb shares its prefix with the bare GET, so match it first
	// with a regex (MSW would otherwise treat `ar_1:decide` as the `:id` param).
	http.post(/\/access-requests\/([^/]+):decide$/, async ({ request }) => {
		const match = new URL(request.url).pathname.match(/\/access-requests\/([^/]+):decide$/);
		const requestId = match ? decodeURIComponent(match[1]) : '';
		const body = (await request.json().catch(() => ({}))) as {
			items?: { item_id: string; decision: string; decision_reason?: string | null }[];
		};
		const ar = accessRequests.find((r) => r.id === requestId);
		if (!ar) return new HttpResponse(null, { status: 404 });
		decideCalls.push({
			request_id: requestId,
			items: (body.items ?? []).map((i) => ({
				item_id: i.item_id,
				decision: i.decision,
				decision_reason: i.decision_reason ?? null,
			})),
		});
		for (const decision of body.items ?? []) {
			const item = ar.items.find((i) => i.id === decision.item_id);
			if (item) {
				item.status = decision.decision;
				item.decision_reason = decision.decision_reason ?? null;
				item.decided_at = new Date().toISOString();
				item.decided_by = 'usr_admin_1';
			}
		}
		const allDenied = ar.items.every((i) => i.status === 'denied');
		const allApproved = ar.items.every((i) => i.status === 'approved');
		ar.status = allDenied ? 'denied' : allApproved ? 'approved' : 'partially_approved';
		return HttpResponse.json(ar);
	}),
	http.get('/access-requests/:id', ({ params }) => {
		const ar = accessRequests.find((r) => r.id === params.id);
		if (!ar) return new HttpResponse(null, { status: 404 });
		return HttpResponse.json(ar);
	}),
];
