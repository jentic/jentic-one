/**
 * Agent Rail MSW handlers + in-memory event store.
 *
 * Backs the rail's real `/events` contract in mocked (Mode A) dev + tests:
 *   GET   /events                        → backlog page (cursor-paginated)
 *   PATCH /events/{id}                    → acknowledge, returns the updated row
 *   GET   /events/stream                  → SSE; emits the seeded backlog then idles
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

export function resetRailEventsStore(): void {
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

export const railEventsHandlers = [
	http.get('/events', ({ request }) => {
		const url = new URL(request.url);
		const cursor = url.searchParams.get('cursor');
		const limit = Number(url.searchParams.get('limit') ?? '25');
		// Honour the same filters the real backend applies so the Monitor Events
		// tab's severity/status controls visibly narrow the list in mocked (Mode A)
		// dev — not just against a real backend (issue #617). The rail itself never
		// sends these params (it filters client-side), so unfiltered rail behaviour
		// is unchanged.
		const severities = url.searchParams.getAll('severity');
		const eventTypes = url.searchParams.getAll('event_type');
		const requiresAction = url.searchParams.get('requires_action');
		const acknowledged = url.searchParams.get('acknowledged');
		const filtered = events.filter((e) => {
			if (severities.length && !severities.includes(e.severity)) return false;
			if (eventTypes.length && !eventTypes.includes(e.type)) return false;
			if (requiresAction != null && String(e.requires_action) !== requiresAction)
				return false;
			if (acknowledged != null && String(e.acknowledged) !== acknowledged) return false;
			return true;
		});
		const sorted = [...filtered].sort(
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
];
