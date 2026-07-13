/**
 * Monitor repository tier.
 *
 * The ONLY place in the Monitor module that talks to `@/shared/api` (the HTTP
 * facade). Views and hooks never import the facade directly — ESLint enforces
 * this (see ui/eslint.config.js "Layering"). Mirrors the backend's Repository
 * layer: thin wrappers that turn typed service calls into UI-shaped data and
 * normalize errors into a single sentinel type the service tier can branch on.
 *
 * The codegen retag (squash #464) split the former coarse `AdminService` into
 * per-tag services; Monitor's four tabs map onto:
 *   ExecutionsService  GET  /executions, GET /executions/{execution_id}
 *   JobsService        GET  /jobs, GET /jobs/{job_id}, POST /jobs/{job_id}:cancel
 *   EventsService      GET  /events, PATCH /events/{event_id}, GET /events/stream
 *   AuditService       GET  /audit (actor lens, org:admin)
 * (The live SSE stream is hand-rolled over fetch — see streamEvents below.)
 */
import {
	ExecutionsService,
	JobsService,
	EventsService,
	AuditService,
	MonitoringService,
	ActorsService,
	ApiError,
	getToken,
	type ActorListResponse,
	type AuditListResponse,
	type AuditResponse,
	type AuditTargetType,
	type EventAcknowledgeRequest,
	type EventListResponse,
	type EventResponse,
	type EventSeverity,
	type ExecutionListResponse,
	type ExecutionResponse,
	type ExecutionStatsResponse,
	type JobListResponse,
	type JobResponse,
} from '@/shared/api';

/**
 * Sentinel error for Monitor repository calls. Hooks/components branch on
 * `error instanceof MonitorApiError` without importing the generated `ApiError`
 * (which lives behind the facade). `status` is null for network/parse failures
 * that never reached the server.
 */
export class MonitorApiError extends Error {
	readonly status: number | null;
	readonly cause?: unknown;

	constructor(message: string, status: number | null, cause?: unknown) {
		super(message);
		this.name = 'MonitorApiError';
		this.status = status;
		this.cause = cause;
	}
}

function toMonitorError(error: unknown, fallback: string): MonitorApiError {
	if (error instanceof ApiError) {
		const detail = (error.body as { detail?: string } | undefined)?.detail ?? error.message;
		return new MonitorApiError(detail || fallback, error.status, error);
	}
	if (error instanceof Error) {
		return new MonitorApiError(error.message || fallback, null, error);
	}
	return new MonitorApiError(fallback, null, error);
}

/* ------------------------------------------------------------------ */
/* Executions                                                          */
/* ------------------------------------------------------------------ */

export interface ListExecutionsParams {
	traceId?: string | null;
	toolkitId?: string | null;
	actorId?: string | null;
	status?: string[] | null;
	from?: string | null;
	to?: string | null;
	cursor?: string | null;
	limit?: number;
}

export async function listExecutions(
	params: ListExecutionsParams = {},
): Promise<ExecutionListResponse> {
	try {
		return await ExecutionsService.listExecutions({
			traceId: params.traceId ?? null,
			toolkitId: params.toolkitId ?? null,
			actorId: params.actorId ?? null,
			status: params.status ?? null,
			from: params.from ?? null,
			to: params.to ?? null,
			cursor: params.cursor ?? null,
			limit: params.limit ?? 25,
		});
	} catch (error) {
		throw toMonitorError(error, 'Failed to load executions.');
	}
}

export async function getExecution(executionId: string): Promise<ExecutionResponse> {
	try {
		return await ExecutionsService.getExecution({ executionId });
	} catch (error) {
		throw toMonitorError(error, 'Failed to load the execution.');
	}
}

/* ------------------------------------------------------------------ */
/* Overview stats (aggregation endpoint, jentic-one#386)              */
/* ------------------------------------------------------------------ */

export interface ExecutionStatsParams {
	/** Trailing window in days (1–30); the endpoint defaults to 7. */
	days?: number;
}

export async function getExecutionStats(
	params: ExecutionStatsParams = {},
): Promise<ExecutionStatsResponse> {
	try {
		return await MonitoringService.getExecutionStats({ days: params.days ?? 7 });
	} catch (error) {
		throw toMonitorError(error, 'Failed to load usage statistics.');
	}
}

/* ------------------------------------------------------------------ */
/* Jobs                                                                */
/* ------------------------------------------------------------------ */

export interface ListJobsParams {
	kind?: string | null;
	status?: string[] | null;
	from?: string | null;
	to?: string | null;
	cursor?: string | null;
	limit?: number;
}

export async function listJobs(params: ListJobsParams = {}): Promise<JobListResponse> {
	try {
		return await JobsService.listJobs({
			kind: params.kind ?? null,
			status: params.status ?? null,
			from: params.from ?? null,
			to: params.to ?? null,
			cursor: params.cursor ?? null,
			limit: params.limit ?? 25,
		});
	} catch (error) {
		throw toMonitorError(error, 'Failed to load jobs.');
	}
}

export async function getJob(jobId: string): Promise<JobResponse> {
	try {
		return await JobsService.getJob({ jobId });
	} catch (error) {
		throw toMonitorError(error, 'Failed to load the job.');
	}
}

export async function cancelJob(jobId: string): Promise<JobResponse> {
	try {
		return await JobsService.cancelJob({ jobId });
	} catch (error) {
		throw toMonitorError(error, 'Failed to cancel the job.');
	}
}

/* ------------------------------------------------------------------ */
/* Events                                                              */
/* ------------------------------------------------------------------ */

export interface ListEventsParams {
	eventType?: string[] | null;
	severity?: EventSeverity[] | null;
	requiresAction?: boolean | null;
	acknowledged?: boolean | null;
	actorId?: string | null;
	actorType?: string | null;
	from?: string | null;
	to?: string | null;
	traceId?: string | null;
	cursor?: string | null;
	limit?: number;
}

export async function listEvents(params: ListEventsParams = {}): Promise<EventListResponse> {
	try {
		return await EventsService.listEvents({
			eventType: params.eventType ?? null,
			severity: params.severity ?? null,
			requiresAction: params.requiresAction ?? null,
			acknowledged: params.acknowledged ?? null,
			actorId: params.actorId ?? null,
			actorType: params.actorType ?? null,
			from: params.from ?? null,
			to: params.to ?? null,
			traceId: params.traceId ?? null,
			cursor: params.cursor ?? null,
			limit: params.limit ?? 25,
		});
	} catch (error) {
		throw toMonitorError(error, 'Failed to load events.');
	}
}

export async function acknowledgeEvent(
	eventId: string,
	requestBody: EventAcknowledgeRequest = { acknowledged: true },
): Promise<EventResponse> {
	try {
		return await EventsService.acknowledgeEvent({ eventId, requestBody });
	} catch (error) {
		throw toMonitorError(error, 'Failed to acknowledge the event.');
	}
}

/* ------------------------------------------------------------------ */
/* Events — live SSE stream                                            */
/* ------------------------------------------------------------------ */

export interface StreamEventsParams {
	since?: string | null;
	eventType?: string[] | null;
	severity?: EventSeverity[] | null;
	requiresAction?: boolean | null;
	actorId?: string | null;
	actorType?: string | null;
	traceId?: string | null;
}

export interface StreamEventsHandlers {
	onEvent: (event: EventResponse) => void;
	onError?: (error: MonitorApiError) => void;
	onOpen?: () => void;
}

/**
 * Parse one SSE frame and forward it as an `EventResponse` IFF it's a real
 * event frame.
 *
 * The backend's `/events/stream` interleaves two frame kinds (see the events
 * router):
 *   - `event: heartbeat\ndata: {"type":"heartbeat","sent_at":…}` — keep-alive,
 *     NOT an event (no `severity`/`event_id`).
 *   - `event: <event_type>\nid: <id>\ndata: <EventResponse JSON>` — a real event.
 *
 * So we read the `event:` field and drop heartbeats, and we defensively require
 * the parsed payload to actually look like an `EventResponse` (`event_id` +
 * `severity`) before forwarding — otherwise a stray frame would crash the
 * severity pill downstream.
 */
function handleFrame(frame: string, onEvent: (event: EventResponse) => void): void {
	let eventName: string | null = null;
	const dataLines: string[] = [];
	for (const line of frame.split('\n')) {
		if (line.startsWith(':')) continue; // SSE comment / keep-alive
		if (line.startsWith('event:')) {
			eventName = line.slice('event:'.length).trim();
		} else if (line.startsWith('data:')) {
			dataLines.push(line.slice('data:'.length).trimStart());
		}
	}
	if (eventName === 'heartbeat') return;
	if (dataLines.length === 0) return;
	const payload = dataLines.join('\n');
	if (payload === '' || payload === '[DONE]') return;

	let parsed: unknown;
	try {
		parsed = JSON.parse(payload);
	} catch {
		return; // non-JSON keep-alive / partial frame
	}
	// Only forward frames that are actually shaped like an event. This guards
	// against heartbeats (no event:-line) and any future frame kind leaking a
	// payload without a severity into the UI.
	if (
		typeof parsed === 'object' &&
		parsed !== null &&
		'event_id' in parsed &&
		'severity' in parsed
	) {
		onEvent(parsed as EventResponse);
	}
}

/**
 * Subscribe to the live event stream at `GET /events/stream`.
 *
 * Native `EventSource` can't be used: the backend requires
 * `Authorization: Bearer <jwt>` and `EventSource` cannot set headers (STATUS.md
 * decision). So we hand-roll SSE over `fetch` + `ReadableStream`, parsing the
 * frames ourselves (see `handleFrame`) and forwarding each real `EventResponse`
 * to `onEvent`. Returns an unsubscribe fn that aborts the request.
 *
 * This is the one repository call that doesn't go through the generated service
 * (the codegen models SSE as `CancelablePromise<any>` and would buffer the whole
 * body); it still reuses the facade's `getToken()` so auth stays centralized.
 */
export function streamEvents(
	params: StreamEventsParams,
	handlers: StreamEventsHandlers,
): () => void {
	const controller = new AbortController();
	const query = new URLSearchParams();
	if (params.since) query.set('since', params.since);
	if (params.requiresAction != null) query.set('requires_action', String(params.requiresAction));
	if (params.actorId) query.set('actor_id', params.actorId);
	if (params.actorType) query.set('actor_type', params.actorType);
	if (params.traceId) query.set('trace_id', params.traceId);
	for (const t of params.eventType ?? []) query.append('event_type', t);
	for (const s of params.severity ?? []) query.append('severity', s);
	const qs = query.toString();
	const url = `/events/stream${qs ? `?${qs}` : ''}`;

	void (async () => {
		try {
			const token = getToken();
			const res = await fetch(url, {
				method: 'GET',
				headers: {
					Accept: 'text/event-stream',
					...(token ? { Authorization: `Bearer ${token}` } : {}),
				},
				signal: controller.signal,
			});
			if (!res.ok || !res.body) {
				handlers.onError?.(
					new MonitorApiError(`Event stream failed (${res.status}).`, res.status),
				);
				return;
			}
			handlers.onOpen?.();

			const reader = res.body.getReader();
			const decoder = new TextDecoder();
			let buffer = '';

			for (;;) {
				const { value, done } = await reader.read();
				if (done) break;
				buffer += decoder.decode(value, { stream: true });

				// SSE frames are separated by a blank line. Process complete frames
				// and keep any trailing partial frame in the buffer.
				let sep: number;
				while ((sep = buffer.indexOf('\n\n')) !== -1) {
					const frame = buffer.slice(0, sep);
					buffer = buffer.slice(sep + 2);
					handleFrame(frame, handlers.onEvent);
				}
			}
		} catch (error) {
			if (controller.signal.aborted) return; // intentional unsubscribe
			handlers.onError?.(toMonitorError(error, 'Event stream error.'));
		}
	})();

	return () => controller.abort();
}

/* ------------------------------------------------------------------ */
/* Audit (actor lens)                                                  */
/* ------------------------------------------------------------------ */

export interface ListAuditParams {
	targetType?: AuditTargetType | null;
	targetId?: string | null;
	actorId?: string | null;
	since?: string | null;
	until?: string | null;
	cursor?: string | null;
	limit?: number;
}

export async function listAudit(params: ListAuditParams = {}): Promise<AuditListResponse> {
	try {
		return await AuditService.listAuditEntries({
			targetType: params.targetType ?? null,
			targetId: params.targetId ?? null,
			actorId: params.actorId ?? null,
			since: params.since ?? null,
			until: params.until ?? null,
			cursor: params.cursor ?? null,
			limit: params.limit ?? 50,
		});
	} catch (error) {
		throw toMonitorError(error, 'Failed to load the audit log.');
	}
}

/**
 * Resolve the most relevant actor for a trace/job by scanning its audit
 * entries. Jobs/executions carry no actor on the wire, so detail views call
 * this to surface "who did it" via the audit log.
 */
export function resolveActor(
	entries: AuditResponse[],
): { actorId: string | null; actorType: string } | null {
	const withActor = entries.find((e) => e.actor_id != null || e.actor_type);
	if (!withActor) return null;
	return { actorId: withActor.actor_id ?? null, actorType: withActor.actor_type };
}

/* ------------------------------------------------------------------ */
/* Actor directory (global filter picker)                              */
/* ------------------------------------------------------------------ */

export interface ListActorsParams {
	cursor?: string | null;
	limit?: number;
}

/**
 * Hydrate the actor directory (GET /actors) for the global filter bar's actor
 * picker. Directory data is small and slow-changing, so callers pull a large
 * page (default 1000) and cache it aggressively.
 */
export async function listActors(params: ListActorsParams = {}): Promise<ActorListResponse> {
	try {
		return await ActorsService.listActors({
			cursor: params.cursor ?? null,
			limit: params.limit ?? 1000,
		});
	} catch (error) {
		throw toMonitorError(error, 'Failed to load the actor directory.');
	}
}
