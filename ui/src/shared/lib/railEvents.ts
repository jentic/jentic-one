/**
 * Agent Rail — repository tier for the REAL platform event feed.
 *
 * The rail is no longer a mock: it consumes the same `/events` contract the
 * Monitor module consumes (STATUS.md [ui-agent-rail 2026-06-21] "make it real").
 * This module is the ONLY place the rail talks to `@/shared/api`; the provider
 * in `agentStream.tsx` and the rail components go through here.
 *
 *   GET   /events           — backlog (filter + cursor)            → listEvents
 *   PATCH /events/{id}       — acknowledge an event                 → acknowledgeEvent
 *   GET   /events/stream     — live SSE (Bearer header, fetch-stream)→ streamEvents
 *
 * The SSE call is hand-rolled over `fetch` + `ReadableStream` because the
 * backend requires `Authorization: Bearer <jwt>` and native `EventSource`
 * cannot set headers — this is a verbatim port of Monitor's proven client
 * (jentic-one-ui-monitor `modules/monitor/api/client.ts`), kept local to the
 * rail so the rail has no cross-module dependency.
 */
import {
	EventsService,
	ApiError,
	getToken,
	type EventAcknowledgeRequest,
	type EventListResponse,
	type EventResponse,
	type EventSeverity,
} from '@/shared/api';

/**
 * Sentinel error for rail repository calls. Callers branch on
 * `error instanceof RailApiError` without importing the generated `ApiError`.
 * `status` is null for network/parse failures that never reached the server.
 */
export class RailApiError extends Error {
	readonly status: number | null;
	readonly cause?: unknown;

	constructor(message: string, status: number | null, cause?: unknown) {
		super(message);
		this.name = 'RailApiError';
		this.status = status;
		this.cause = cause;
	}
}

export function toRailError(error: unknown, fallback: string): RailApiError {
	if (error instanceof ApiError) {
		const detail = (error.body as { detail?: string } | undefined)?.detail ?? error.message;
		return new RailApiError(detail || fallback, error.status, error);
	}
	if (error instanceof Error) {
		return new RailApiError(error.message || fallback, null, error);
	}
	return new RailApiError(fallback, null, error);
}

export interface ListEventsParams {
	eventType?: string[] | null;
	severity?: EventSeverity[] | null;
	requiresAction?: boolean | null;
	acknowledged?: boolean | null;
	from?: string | null;
	to?: string | null;
	traceId?: string | null;
	cursor?: string | null;
	limit?: number;
}

/** Backlog fetch — the rail seeds from this before the live stream takes over. */
export async function listEvents(params: ListEventsParams = {}): Promise<EventListResponse> {
	try {
		return await EventsService.listEvents({
			eventType: params.eventType ?? null,
			severity: params.severity ?? null,
			requiresAction: params.requiresAction ?? null,
			acknowledged: params.acknowledged ?? null,
			from: params.from ?? null,
			to: params.to ?? null,
			traceId: params.traceId ?? null,
			cursor: params.cursor ?? null,
			limit: params.limit ?? 25,
		});
	} catch (error) {
		throw toRailError(error, 'Failed to load events.');
	}
}

/** Acknowledge an event (`PATCH /events/{id}`) — the rail's one real action. */
export async function acknowledgeEvent(
	eventId: string,
	requestBody: EventAcknowledgeRequest = { acknowledged: true },
): Promise<EventResponse> {
	try {
		return await EventsService.acknowledgeEvent({ eventId, requestBody });
	} catch (error) {
		throw toRailError(error, 'Failed to acknowledge the event.');
	}
}

export interface StreamEventsParams {
	since?: string | null;
	eventType?: string[] | null;
	severity?: EventSeverity[] | null;
	requiresAction?: boolean | null;
	traceId?: string | null;
}

export interface StreamEventsHandlers {
	onEvent: (event: EventResponse) => void;
	onError?: (error: RailApiError) => void;
	onOpen?: () => void;
	/**
	 * Fired when the stream drops and a reconnect is scheduled, with the delay
	 * (ms) before the next attempt. Lets the UI show an honest "reconnecting…"
	 * state (vs. a merely quiet but healthy feed).
	 */
	onReconnecting?: (delayMs: number) => void;
}

/** Reconnect backoff schedule (ms): quick first retry, then ramp, then cap. */
const RECONNECT_BACKOFF_MS = [1_000, 2_000, 5_000, 10_000, 15_000];

/**
 * Minimum time (ms) a connection must stay open before we treat it as "healthy"
 * and reset the backoff. A connection that opens then ends almost immediately
 * (proxy 200-then-drop, worker recycle storm) must NOT reset the schedule, or
 * the loop hot-reconnects once a second forever. See `reconnect` below.
 */
const HEALTHY_CONNECTION_MS = 30_000;

/**
 * Subtract 1ms from an ISO timestamp. The backend's `since` filter is EXCLUSIVE
 * (`created_at > since`) at microsecond precision, so resuming from the exact
 * `created_at` of the last event we saw would skip any sibling event sharing
 * that instant if the stream dropped mid-batch. We rewind the resume point by a
 * millisecond so the boundary is re-requested; the provider dedups by id, so the
 * already-seen event is harmlessly dropped and only genuinely-missed siblings
 * come through. Returns the input unchanged if it can't be parsed.
 */
function rewindIso(iso: string): string {
	const ms = Date.parse(iso);
	if (Number.isNaN(ms)) return iso;
	return new Date(ms - 1).toISOString();
}

/**
 * Subscribe to the live event stream at `GET /events/stream`, with automatic
 * reconnection.
 *
 * Hand-rolled SSE over `fetch` + `ReadableStream` (native `EventSource` cannot
 * set the required `Authorization` header). Parses each SSE frame, honouring the
 * `event:` field: `heartbeat` keep-alive frames (which the backend sends with a
 * JSON `data:` body of `{type:"heartbeat",sent_at}` — see the events router) are
 * dropped, as are payloads without an `event_id`. Every remaining frame is
 * forwarded to `onEvent` as a real `EventResponse`.
 *
 * The backend stream is a 5s poll loop that yields a heartbeat when idle; if the
 * connection drops (proxy/idle timeout, worker recycle, network blip) the reader
 * simply ends or throws. Without reconnection the feed would silently die and
 * any newly-filed events would never arrive — so this client RECONNECTS with
 * backoff. On reconnect it resumes from just before the newest event it has seen
 * (`rewindIso`, to beat the backend's exclusive `created_at > since` filter so a
 * mid-batch drop doesn't skip same-instant siblings); the provider dedups by id.
 * The backoff only resets after a connection stays healthy for a while
 * (`HEALTHY_CONNECTION_MS`), so an immediately-dropping stream keeps escalating
 * instead of hot-looping. Returns an unsubscribe fn that aborts the in-flight
 * request and cancels any pending retry.
 */
export function streamEvents(
	params: StreamEventsParams,
	handlers: StreamEventsHandlers,
): () => void {
	const controller = new AbortController();
	let retryTimer: ReturnType<typeof setTimeout> | null = null;
	let stopped = false;
	// Newest `created_at` we've forwarded. Reconnects resume just before this
	// (see `rewindIso`) so a mid-batch drop doesn't skip same-instant siblings.
	let lastSeen: string | null = params.since ?? null;
	// True once we've opened at least once, so we know a later connect is a
	// RECONNECT (rewind `since`) vs. the initial connect (use the caller's value).
	let hasConnected = false;

	function buildUrl(): string {
		const query = new URLSearchParams();
		// First connect honours the caller's `since` verbatim; reconnects rewind
		// 1ms past the last-seen event to defeat the exclusive `>` boundary filter.
		const since = hasConnected && lastSeen ? rewindIso(lastSeen) : lastSeen;
		if (since) query.set('since', since);
		if (params.requiresAction != null)
			query.set('requires_action', String(params.requiresAction));
		if (params.traceId) query.set('trace_id', params.traceId);
		for (const t of params.eventType ?? []) query.append('event_type', t);
		for (const s of params.severity ?? []) query.append('severity', s);
		const qs = query.toString();
		return `/events/stream${qs ? `?${qs}` : ''}`;
	}

	/**
	 * One connection attempt. Resolves with the time (ms) the connection stayed
	 * open once the stream ends, so the caller can decide whether it was healthy
	 * enough to reset the backoff.
	 */
	async function connectOnce(): Promise<number> {
		const token = getToken();
		const res = await fetch(buildUrl(), {
			method: 'GET',
			headers: {
				Accept: 'text/event-stream',
				...(token ? { Authorization: `Bearer ${token}` } : {}),
			},
			signal: controller.signal,
		});
		if (!res.ok || !res.body) {
			throw new RailApiError(`Event stream failed (${res.status}).`, res.status);
		}
		hasConnected = true;
		const openedAt = Date.now();
		handlers.onOpen?.();

		const reader = res.body.getReader();
		const decoder = new TextDecoder();
		let buffer = '';

		for (;;) {
			const { value, done } = await reader.read();
			if (done) break;
			buffer += decoder.decode(value, { stream: true });

			let sep: number;
			while ((sep = buffer.indexOf('\n\n')) !== -1) {
				const frame = buffer.slice(0, sep);
				buffer = buffer.slice(sep + 2);
				const lines = frame.split('\n');
				// SSE `event:` name — the backend tags keep-alives as `heartbeat`.
				const eventName = lines
					.find((line) => line.startsWith('event:'))
					?.slice('event:'.length)
					.trim();
				if (eventName === 'heartbeat') continue;
				const dataLines = lines
					.filter((line) => line.startsWith('data:'))
					.map((line) => line.slice('data:'.length).trimStart());
				if (dataLines.length === 0) continue;
				const payload = dataLines.join('\n');
				if (payload === '' || payload === '[DONE]') continue;
				try {
					const parsed = JSON.parse(payload) as Partial<EventResponse> & {
						type?: string;
					};
					// Defence in depth: drop heartbeats / non-event payloads even if
					// they arrive without the `event:` tag.
					if (parsed.type === 'heartbeat' || !parsed.event_id) continue;
					// Advance the resume point so a reconnect doesn't replay/miss.
					if (parsed.created_at) lastSeen = parsed.created_at;
					handlers.onEvent(parsed as EventResponse);
				} catch {
					// Ignore keep-alive comments / non-JSON frames.
				}
			}
		}
		return Date.now() - openedAt;
	}

	void (async () => {
		let attempt = 0;
		for (;;) {
			if (stopped || controller.signal.aborted) return;
			try {
				const openMs = await connectOnce();
				// Only reset the backoff if the connection was genuinely healthy.
				// A connect that opens then ends almost immediately (proxy 200-drop,
				// worker recycle) must keep escalating, or we'd hot-loop every 1s.
				if (openMs >= HEALTHY_CONNECTION_MS) attempt = 0;
			} catch (error) {
				if (stopped || controller.signal.aborted) return; // intentional unsubscribe
				handlers.onError?.(toRailError(error, 'Event stream error.'));
			}
			if (stopped || controller.signal.aborted) return;
			// Schedule a reconnect with backoff (capped).
			const delay = RECONNECT_BACKOFF_MS[Math.min(attempt, RECONNECT_BACKOFF_MS.length - 1)];
			attempt += 1;
			handlers.onReconnecting?.(delay);
			await new Promise<void>((resolve) => {
				retryTimer = setTimeout(resolve, delay);
			});
		}
	})();

	return () => {
		stopped = true;
		if (retryTimer) clearTimeout(retryTimer);
		controller.abort();
	};
}
