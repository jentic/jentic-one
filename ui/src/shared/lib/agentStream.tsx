import {
	createContext,
	useCallback,
	useContext,
	useEffect,
	useMemo,
	useRef,
	useState,
} from 'react';
import type { ReactNode } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { EventSeverity, type EventResponse } from '@/shared/api';
import { sharedQueryKeys } from '@/shared/api/queryKeys';
import { acknowledgeEvent, listEvents, streamEvents } from '@/shared/lib/railEvents';
import { decideAllPending } from '@/shared/lib/accessRequests';

/*
  SSE → QUERY-CACHE BRIDGE.

  The rail's event feed is local React state — it never touched the TanStack
  Query cache the dashboard (and nav badge) read, so a freshly-filed or decided
  access request left those surfaces stale until their staleTime lapsed. These
  ROOT keys are invalidated whenever an `access_request.*` event lands on the
  stream, or when the rail's Deny fast-path decides a request.

  We invalidate by the shared ROOT keys (not by importing `dashboardKeys` /
  `pendingAccessRequestCountKey`) on purpose: this is shared-layer code and must
  not reach into a feature module. The roots are a stable contract owned by the
  shared query-key registry — the dashboard hooks key off `dashboardRoot`
  (`['dashboard', …]`), the durable queue + nav badge off `accessRequestsRoot`
  (`['access-requests', …]`) — so a prefix invalidation here refreshes every
  matching slice without crossing a module boundary.
*/
const DASHBOARD_ROOT_KEY = sharedQueryKeys.dashboardRoot;
const ACCESS_REQUESTS_ROOT_KEY = sharedQueryKeys.accessRequestsRoot;

/*
  AGENT RAIL STREAM — backed by the REAL platform event feed.

  The rail consumes jentic-one's `/events` contract: a backlog fetch
  (`GET /events`) seeds the feed, then a live SSE subscription
  (`GET /events/stream`, Bearer-over-fetch) streams new events. This module
  adapts the wire `EventResponse` into the rail's UI-shaped `StreamEvent` and
  exposes the same provider/hook surface the rail components already consume.

  This is an ORG-WIDE platform feed (import/execution/access-request events) —
  there is no per-agent lens because `/events` carries no actor filter
  (tracked: jentic/jentic-one#387). Event types the backend can emit today:
  `import.*`, `execution.*`, `access_request.*`, `credential.*`.
*/

/**
 * Severity, mirroring the real `EventSeverity` enum (info|warning|error|
 * critical). These are the only levels the backend emits.
 */
export type StreamSeverity = 'critical' | 'error' | 'warning' | 'info';

/**
 * Kind — derived from the event-type namespace (the substring before the first
 * dot of `EventResponse.type`). `other` is the safe bucket for any namespace
 * the backend adds later so the rail never crashes on an unknown type.
 */
export type StreamKind = 'import' | 'execution' | 'access_request' | 'credential' | 'other';

/** Tokens lifted from `EventResponse` (`trace_id` + the free-form `data` map). */
export type StreamTokens = {
	trace_id?: string;
	toolkit_id?: string;
	operation_id?: string;
	credential_id?: string;
	job_id?: string;
	execution_id?: string;
	access_request_id?: string;
	agent_id?: string;
};

/** Deep-links carried by `EventResponse._links` (HAL-style). */
export type StreamLinks = {
	self?: string;
	action?: string | null;
	execution?: string | null;
	job?: string | null;
};

/**
 * UI-shaped view of a single platform event. A faithful adaptation of
 * `EventResponse` — `id`/`tsMs`/`title` map to `event_id`/`created_at`/
 * `summary`; `acknowledged` + `requiresAction` drive the inline action slot.
 */
export type StreamEvent = {
	id: string;
	tsMs: number;
	type: string; // raw wire type, e.g. "execution.failed"
	kind: StreamKind;
	severity: StreamSeverity;
	title: string;
	meta?: string;
	tokens: StreamTokens;
	links: StreamLinks;
	requiresAction: boolean;
	acknowledged: boolean;
	acknowledgedAt?: number;
	// Stable key for grouping. Format: "<kind>:<type>:<trace|''>".
	groupKey: string;
};

export type ToastScope = 'all' | 'warning' | 'critical' | 'off';

export const TOAST_SCOPE_STORAGE_KEY = 'j1.toasts.scope';
export const RAIL_COLLAPSED_STORAGE_KEY = 'j1.agentRail.collapsed';
export const RAIL_AUDIO_STORAGE_KEY = 'j1.rail.audioOnCritical';
export const TOAST_SCOPE_CHANGE_EVENT = 'j1:toast-scope-change';
export const RAIL_COLLAPSE_CHANGE_EVENT = 'j1:rail-collapse-change';

/* ------------------------------------------------------------------ */
/* Wire → UI adaptation                                                */
/* ------------------------------------------------------------------ */

const KNOWN_KINDS = new Set<StreamKind>(['import', 'execution', 'access_request', 'credential']);

/** Namespace before the first dot → `StreamKind` (`other` for anything else). */
export function kindForType(type: string): StreamKind {
	const ns = type.split('.', 1)[0] as StreamKind;
	return KNOWN_KINDS.has(ns) ? ns : 'other';
}

/**
 * Human Title-case label for an event kind, shown as the headline in rail rows
 * and toasts. Shared so the row and the toast can't drift apart. (The header's
 * filter chips use their own short plural labels — a different surface.)
 */
export const STREAM_KIND_LABEL: Record<StreamKind, string> = {
	import: 'Import',
	execution: 'Execution',
	credential: 'Credential',
	access_request: 'Access request',
	other: 'Platform',
};

/**
 * Map the real `EventSeverity` to the rail's `StreamSeverity`. They share the
 * same four members; this normalises the wire enum (which may arrive as the
 * enum value or a bare string) and falls back to `info`.
 */
export function severityForWire(severity: EventSeverity | string): StreamSeverity {
	switch (severity) {
		case EventSeverity.CRITICAL:
		case 'critical':
			return 'critical';
		case EventSeverity.ERROR:
		case 'error':
			return 'error';
		case EventSeverity.WARNING:
		case 'warning':
			return 'warning';
		default:
			return 'info';
	}
}

function stringField(data: Record<string, unknown> | undefined, key: string): string | undefined {
	const v = data?.[key];
	return typeof v === 'string' && v.length > 0 ? v : undefined;
}

/** Build the grouping key. Consecutive same-key events collapse in the feed. */
function buildGroupKey(t: Pick<StreamEvent, 'kind' | 'type' | 'tokens'>): string {
	const token =
		t.tokens.operation_id ??
		t.tokens.toolkit_id ??
		t.tokens.credential_id ??
		t.tokens.trace_id ??
		'';
	return `${t.kind}:${t.type}:${token}`;
}

/** Test-only re-export of the internal group-key builder. */
export const buildGroupKeyForTest = buildGroupKey;

/** Adapt a wire `EventResponse` into the rail's UI `StreamEvent`. */
export function adaptEvent(e: EventResponse): StreamEvent {
	const data = (e.data ?? {}) as Record<string, unknown>;
	const tokens: StreamTokens = {
		trace_id: e.trace_id ?? stringField(data, 'trace_id'),
		toolkit_id: stringField(data, 'toolkit_id'),
		operation_id: stringField(data, 'operation_id'),
		credential_id: stringField(data, 'credential_id'),
		job_id: stringField(data, 'job_id'),
		execution_id: stringField(data, 'execution_id'),
		access_request_id:
			stringField(data, 'access_request_id') ?? stringField(data, 'request_id'),
		agent_id: stringField(data, 'agent_id') ?? stringField(data, 'actor_id'),
	};
	const kind = kindForType(e.type);
	const parsedTs = e.created_at ? Date.parse(e.created_at) : NaN;
	const ev: StreamEvent = {
		id: e.event_id,
		// Fall back to "now" only when the wire timestamp is missing/unparseable,
		// so a malformed event still sorts sanely instead of jumping to 1970.
		tsMs: Number.isNaN(parsedTs) ? Date.now() : parsedTs,
		type: e.type,
		kind,
		severity: severityForWire(e.severity),
		title: e.summary,
		meta: e.detail ?? undefined,
		tokens,
		links: {
			self: e._links?.self,
			action: e._links?.action ?? null,
			execution: e._links?.execution ?? null,
			job: e._links?.job ?? null,
		},
		requiresAction: e.requires_action,
		acknowledged: e.acknowledged,
		acknowledgedAt: e.acknowledged_at ? Date.parse(e.acknowledged_at) || undefined : undefined,
		groupKey: '',
	};
	ev.groupKey = buildGroupKey(ev);
	return ev;
}

/* ------------------------------------------------------------------ */
/* Provider                                                            */
/* ------------------------------------------------------------------ */

export type StreamStatus = 'idle' | 'connecting' | 'live' | 'error';

type AgentStreamValue = {
	events: StreamEvent[];
	latest: StreamEvent | null;
	status: StreamStatus;
	/** Acknowledge an event against the real backend (`PATCH /events/{id}`). */
	acknowledge: (eventId: string) => Promise<void>;
	/**
	 * Decide an `access_request.filed` event: approve/deny the request's pending
	 * items (`POST /access-requests/{id}:decide`). `reason` is the human's note
	 * fed back to the agent (required by the UI for denials). Resolves the filed
	 * event locally on success; the authoritative approved/denied event arrives
	 * over the live stream.
	 *
	 * This is the row's FAST PATH (deny-all / approve-all with one verdict). For
	 * per-item control the row opens the request-detail dialog, which decides
	 * items individually and then calls `resolveEvent` to settle the row.
	 */
	decide: (eventId: string, decision: 'approved' | 'denied', reason?: string) => Promise<void>;
	/**
	 * Mark a filed event's action slot as handled locally, without issuing a
	 * decision RPC. Used by the request-detail dialog after it has decided the
	 * request's items itself (`POST /access-requests/{id}:decide`); the
	 * authoritative `access_request.approved/denied` event arrives over the
	 * stream and supersedes this optimistic flip.
	 */
	resolveEvent: (eventId: string) => void;
	/** Fetch one older page from `GET /events?cursor=…` and append it. */
	loadOlderEvents: () => Promise<void>;
	canLoadOlder: boolean;
	loadingOlder: boolean;
};

const AgentStreamContext = createContext<AgentStreamValue | null>(null);

const BACKLOG_LIMIT = 30;
const MAX_EVENTS = 300;

/**
 * Provider for the rail's real event feed.
 *
 *   1. On mount, fetch a backlog page (`GET /events`) newest-first.
 *   2. Subscribe to the live SSE (`GET /events/stream`); each new event is
 *      prepended (deduped by id) and exposed as `latest` so the ToastHost +
 *      audio cue can react.
 *   3. `acknowledge` PATCHes the event and optimistically flips its local flag.
 *   4. `loadOlderEvents` pages backwards via the list cursor.
 *
 * `live` defaults to `true`; tests pass `live={false}` to skip the SSE
 * subscription and drive a deterministic, backlog-only feed.
 */
export function AgentStreamProvider({
	children,
	live = true,
}: {
	children: ReactNode;
	live?: boolean;
}) {
	const [events, setEvents] = useState<StreamEvent[]>([]);
	const [latest, setLatest] = useState<StreamEvent | null>(null);
	const [status, setStatus] = useState<StreamStatus>('idle');
	const [cursor, setCursor] = useState<string | null>(null);
	const [hasMore, setHasMore] = useState(false);
	const [loadingOlder, setLoadingOlder] = useState(false);
	const queryClient = useQueryClient();

	/**
	 * Refresh the dashboard + durable access-request surfaces (cards, tiles,
	 * queue page, nav badge). Centralised here so EVERY decision path — a live
	 * `access_request.*` event, the Deny fast-path — converges on the same
	 * invalidation, and so the dashboard updates app-wide rather than only while
	 * it happens to be mounted.
	 */
	const invalidateApprovalSurfaces = useCallback(() => {
		void queryClient.invalidateQueries({ queryKey: DASHBOARD_ROOT_KEY });
		void queryClient.invalidateQueries({ queryKey: ACCESS_REQUESTS_ROOT_KEY });
	}, [queryClient]);

	// Fresh mirror of `events` for callbacks that need the current list WITHOUT
	// re-subscribing (e.g. `decide` reads a row's request id). Reading this ref
	// avoids stale closures and avoids side-effecting inside a `setState` updater.
	const eventsRef = useRef<StreamEvent[]>(events);
	eventsRef.current = events;
	// Events with a decision RPC in flight, so a double-click can't fire twice.
	const inFlightRef = useRef<Set<string>>(new Set());

	/**
	 * Flip a single event by id with `fn`, leaving the rest untouched. Centralises
	 * the optimistic-update / rollback pattern used by acknowledge + decide so the
	 * map-by-id boilerplate isn't repeated (and can't drift between flip and undo).
	 */
	const patchEvent = useCallback((eventId: string, fn: (ev: StreamEvent) => StreamEvent) => {
		setEvents((prev) => prev.map((ev) => (ev.id === eventId ? fn(ev) : ev)));
	}, []);

	const markResolved = useCallback(
		(ev: StreamEvent): StreamEvent => ({
			...ev,
			acknowledged: true,
			acknowledgedAt: Date.now(),
		}),
		[],
	);
	const markUnresolved = useCallback(
		(ev: StreamEvent): StreamEvent => ({
			...ev,
			acknowledged: false,
			acknowledgedAt: undefined,
		}),
		[],
	);

	const upsert = useCallback((incoming: StreamEvent[], front: boolean) => {
		setEvents((prev) => {
			const byId = new Map(prev.map((e) => [e.id, e] as const));
			let changed = false;
			const appended: StreamEvent[] = [];
			for (const ev of incoming) {
				const existing = byId.get(ev.id);
				if (!existing) {
					byId.set(ev.id, ev);
					appended.push(ev);
					changed = true;
					continue;
				}
				// Same id already present. Live upserts (`front`) are authoritative
				// — a re-delivered event carries the server's current truth (e.g. an
				// acknowledged flag set elsewhere), so reconcile in place. Backlog
				// pages (`!front`) are historical and must NOT clobber a local
				// optimistic flip, so they're ignored on collision.
				if (front && existing !== ev) {
					byId.set(ev.id, ev);
					changed = true;
				}
			}
			if (!changed) return prev;
			// Rebuild preserving order: fresh-front events lead, then the (possibly
			// reconciled) previous list, then fresh-back events — then sort by time.
			const base = prev.map((e) => byId.get(e.id) ?? e);
			const merged = front ? [...appended, ...base] : [...base, ...appended];
			merged.sort((a, b) => b.tsMs - a.tsMs);
			return merged.slice(0, MAX_EVENTS);
		});
	}, []);

	// 1. Backlog seed.
	useEffect(() => {
		let cancelled = false;
		void (async () => {
			try {
				const page = await listEvents({ limit: BACKLOG_LIMIT });
				if (cancelled) return;
				upsert(page.data.map(adaptEvent), false);
				setCursor(page.next_cursor ?? null);
				setHasMore(page.has_more);
			} catch {
				// A failed backlog is non-fatal — the live stream may still connect.
			}
		})();
		return () => {
			cancelled = true;
		};
	}, [upsert]);

	// 2. Live SSE subscription (with auto-reconnect inside `streamEvents`).
	useEffect(() => {
		if (!live) {
			setStatus('idle');
			return undefined;
		}
		setStatus('connecting');
		const unsubscribe = streamEvents(
			{},
			{
				onOpen: () => setStatus('live'),
				onEvent: (wire) => {
					const ev = adaptEvent(wire);
					upsert([ev], true);
					setLatest(ev);
					// Bridge: a filed/decided access request changes the durable
					// queue + dashboard counts. Refresh those surfaces so they
					// stop going stale (the rail used to be the ONLY thing that
					// reacted to these events).
					if (ev.kind === 'access_request') {
						invalidateApprovalSurfaces();
					}
				},
				onError: () => setStatus('error'),
				onReconnecting: () => setStatus('connecting'),
			},
		);
		return unsubscribe;
	}, [live, upsert, invalidateApprovalSurfaces]);

	const acknowledge = useCallback(
		async (eventId: string) => {
			// Optimistic flip so the row resolves immediately; reconcile on response.
			patchEvent(eventId, markResolved);
			try {
				const updated = await acknowledgeEvent(eventId);
				patchEvent(eventId, () => adaptEvent(updated));
			} catch {
				patchEvent(eventId, markUnresolved);
			}
		},
		[patchEvent, markResolved, markUnresolved],
	);

	const decide = useCallback(
		async (eventId: string, decision: 'approved' | 'denied', reason?: string) => {
			// Guard against a double-click firing two decision RPCs for one row.
			if (inFlightRef.current.has(eventId)) return;
			// Read the row's request id from the fresh mirror — no side effects in
			// a setState updater, no stale closure.
			const target = eventsRef.current.find((ev) => ev.id === eventId);
			const requestId = target?.tokens.access_request_id;
			inFlightRef.current.add(eventId);
			// Optimistically resolve the filed event's action slot.
			patchEvent(eventId, markResolved);
			try {
				if (!requestId) {
					// No request id on the event — fall back to acknowledging it so the
					// row doesn't get stuck asking for a decision it can't route.
					await acknowledgeEvent(eventId);
					return;
				}
				await decideAllPending(requestId, decision, reason);
				// The fast path bypasses React Query entirely, so without this the
				// dashboard tiles/cards, the durable queue, and the nav badge would
				// stay stale until their staleTime lapsed (the bug). The
				// authoritative approved/denied event also arrives over the stream
				// and re-invalidates, but we refresh eagerly here so the surfaces
				// update the instant the Deny resolves.
				invalidateApprovalSurfaces();
			} catch {
				// Roll back the optimistic resolve; the row re-offers the decision.
				patchEvent(eventId, markUnresolved);
			} finally {
				inFlightRef.current.delete(eventId);
			}
		},
		[patchEvent, markResolved, markUnresolved, invalidateApprovalSurfaces],
	);

	const resolveEvent = useCallback(
		(eventId: string) => {
			patchEvent(eventId, markResolved);
		},
		[patchEvent, markResolved],
	);

	const loadOlderEvents = useCallback(async () => {
		if (!cursor) return;
		setLoadingOlder(true);
		try {
			const page = await listEvents({ cursor, limit: BACKLOG_LIMIT });
			upsert(page.data.map(adaptEvent), false);
			setCursor(page.next_cursor ?? null);
			setHasMore(page.has_more);
		} catch {
			/* leave the cursor in place so the user can retry */
		} finally {
			setLoadingOlder(false);
		}
	}, [cursor, upsert]);

	const value = useMemo<AgentStreamValue>(
		() => ({
			events,
			latest,
			status,
			acknowledge,
			decide,
			resolveEvent,
			loadOlderEvents,
			canLoadOlder: hasMore && cursor != null,
			loadingOlder,
		}),
		[
			events,
			latest,
			status,
			acknowledge,
			decide,
			resolveEvent,
			loadOlderEvents,
			hasMore,
			cursor,
			loadingOlder,
		],
	);

	return <AgentStreamContext.Provider value={value}>{children}</AgentStreamContext.Provider>;
}

export function useAgentStream(): AgentStreamValue {
	const ctx = useContext(AgentStreamContext);
	if (!ctx) throw new Error('useAgentStream must be used within AgentStreamProvider');
	return ctx;
}

/* ------------------------------------------------------------------ */
/* Toast scope + display helpers                                       */
/* ------------------------------------------------------------------ */

export function readToastScope(): ToastScope {
	if (typeof window === 'undefined') return 'critical';
	try {
		const v = window.localStorage.getItem(TOAST_SCOPE_STORAGE_KEY);
		if (v === 'all' || v === 'warning' || v === 'critical' || v === 'off') return v;
	} catch {
		/* ignore */
	}
	// Default: only critical/error toasts pop — the rail itself carries the rest.
	return 'critical';
}

export function writeToastScope(scope: ToastScope) {
	if (typeof window === 'undefined') return;
	try {
		window.localStorage.setItem(TOAST_SCOPE_STORAGE_KEY, scope);
		window.dispatchEvent(new CustomEvent(TOAST_SCOPE_CHANGE_EVENT, { detail: scope }));
	} catch {
		/* ignore */
	}
}

export function matchesToastScope(severity: StreamSeverity, scope: ToastScope): boolean {
	if (scope === 'off') return false;
	if (scope === 'all') return true;
	if (scope === 'warning')
		return severity === 'critical' || severity === 'error' || severity === 'warning';
	return severity === 'critical';
}

export function severityStripeClass(s: StreamSeverity): string {
	if (s === 'critical') return 'border-l-danger';
	if (s === 'error') return 'border-l-danger';
	if (s === 'warning') return 'border-l-warning';
	return 'border-l-primary';
}

export function formatStreamTime(tsMs: number): string {
	const d = new Date(tsMs);
	const hh = d.getHours().toString().padStart(2, '0');
	const mm = d.getMinutes().toString().padStart(2, '0');
	const ss = d.getSeconds().toString().padStart(2, '0');
	return `${hh}:${mm}:${ss}`;
}

/* ------------------------------------------------------------------ */
/* Inline actions + navigation                                         */
/* ------------------------------------------------------------------ */

/*
  EVENT BEHAVIOUR — derived from the REAL contract.

  Two backend mutations reach the rail:
    • Acknowledge (`PATCH /events/{id}`) — dismisses ANY action-required event.
    • Decide (`POST /access-requests/{id}:decide`) — the real "feed the agent
      back" action for an `access_request.filed` event. The event carries the
      request id in `data.request_id`; Approve/Deny fan a verdict across the
      request's pending items, and Deny carries a reason the agent reads back.

  So the inline-action slot is:
    • View / Deny — for an unacked `access_request.filed` event that carries a
      request id. "View" opens the request-detail dialog (per-item approve/deny);
      "Deny" is the reason-gated fast path that denies the whole request.
    • "Acknowledge" — for any other action-required event not yet acked.
    • "View" links — deep-link into the execution/job/trace the event references.
  Navigation targets are router-relative (basename `/app` is prepended) to match
  jentic-one's route tree.
*/
export type InlineActionKind =
	| 'acknowledge'
	| 'approve'
	| 'deny'
	| 'view_request'
	| 'view_execution'
	| 'view_job'
	| 'view_trace';

export type InlineActionSpec = {
	kind: InlineActionKind;
	label: string;
	/** Real backend mutation (acknowledge). Omit for pure navigation. */
	acknowledges?: boolean;
	/** Access-request decision (approve/deny via `:decide`). */
	decides?: 'approved' | 'denied';
	/**
	 * Opens the access-request detail dialog (per-item approve/deny) rather than
	 * navigating or firing an RPC. The parent supplies the request id from the
	 * event's `access_request_id` token.
	 */
	opensRequest?: boolean;
	/** Navigation target (omit for pure RPC). */
	href?: (ev: StreamEvent) => string | null;
	/** A human reason must be supplied before the action fires (denials). */
	requiresReason?: boolean;
};

/** Resolve a HAL `_links` URL or token into a router-relative monitor route. */
const NAV = {
	trace: (ev: StreamEvent) =>
		ev.tokens.trace_id ? `/monitor?tab=executions&trace=${ev.tokens.trace_id}` : null,
	execution: (ev: StreamEvent) => {
		const id = ev.tokens.execution_id;
		if (id) return `/monitor?tab=executions&execution=${encodeURIComponent(id)}`;
		return ev.tokens.trace_id ? `/monitor?tab=executions&trace=${ev.tokens.trace_id}` : null;
	},
	job: (ev: StreamEvent) =>
		ev.tokens.job_id ? `/monitor?tab=jobs&job=${encodeURIComponent(ev.tokens.job_id)}` : null,
};

export function inlineActionsFor(ev: StreamEvent): InlineActionSpec[] {
	const actions: InlineActionSpec[] = [];
	if (ev.requiresAction && !ev.acknowledged) {
		// An access_request.filed event with a routable request id gets View
		// (opens the per-item decision dialog) + a reason-gated Deny fast path
		// (denies the whole request). Approving without seeing the items is the
		// risky direction, so approve lives inside the dialog. Everything else
		// that needs action just gets Acknowledge.
		if (ev.type === 'access_request.filed' && ev.tokens.access_request_id) {
			actions.push({ kind: 'view_request', label: 'View', opensRequest: true });
			actions.push({
				kind: 'deny',
				label: 'Deny',
				decides: 'denied',
				requiresReason: true,
			});
		} else {
			actions.push({ kind: 'acknowledge', label: 'Acknowledge', acknowledges: true });
		}
	}
	// A deep-link into the underlying record, when the event references one.
	if (ev.tokens.execution_id || (ev.kind === 'execution' && ev.tokens.trace_id)) {
		actions.push({ kind: 'view_execution', label: 'View execution', href: NAV.execution });
	} else if (ev.tokens.job_id || ev.kind === 'import') {
		actions.push({ kind: 'view_job', label: 'View job', href: NAV.job });
	} else if (ev.tokens.trace_id) {
		actions.push({ kind: 'view_trace', label: 'View trace', href: NAV.trace });
	}
	return actions;
}

/**
 * A serialisable trace bundle exported from the rail ("Export … as trace
 * bundle"). `windowMs` is the recency window actually applied: a number when we
 * exported the trailing window, or `null` when that window was empty and we fell
 * back to every loaded event (so the file is never silently empty just because
 * the sparse feed had nothing in the last few minutes).
 */
export type TraceBundle = {
	exportedAt: string;
	windowMs: number | null;
	eventCount: number;
	events: StreamEvent[];
};

/**
 * Build a trace bundle from the loaded events. Pure (no DOM / Blob), so it's
 * unit-testable; the rail wraps it in the download side-effect.
 *
 * Prefers the trailing `windowMs` of events (most recent activity). The feed is
 * sparse, though — a 5-minute window is frequently empty even when the rail has
 * plenty of older backlog — so when nothing falls in the window we export ALL
 * loaded events and record `windowMs: null` to say so. Events are returned
 * newest-first.
 */
export function buildTraceBundle(
	events: StreamEvent[],
	windowMs: number,
	now: number = Date.now(),
): TraceBundle {
	const sorted = [...events].sort((a, b) => b.tsMs - a.tsMs);
	const cutoff = now - windowMs;
	const windowed = sorted.filter((ev) => ev.tsMs >= cutoff);
	const inWindow = windowed.length > 0;
	const slice = inWindow ? windowed : sorted;
	return {
		exportedAt: new Date(now).toISOString(),
		windowMs: inWindow ? windowMs : null,
		eventCount: slice.length,
		events: slice,
	};
}

/**
 * Primary destination when the row body (not an action button) is clicked.
 * Router-relative (basename `/app` is prepended). Returns null when the event
 * references no navigable record.
 */
export function primaryDestinationFor(ev: StreamEvent): string | null {
	switch (ev.kind) {
		case 'execution':
			return NAV.execution(ev);
		case 'import':
			return NAV.job(ev) ?? NAV.trace(ev);
		case 'credential':
			return ev.tokens.credential_id
				? `/credentials/${ev.tokens.credential_id}`
				: NAV.trace(ev);
		case 'access_request':
			return ev.tokens.agent_id ? `/agents/${ev.tokens.agent_id}` : NAV.trace(ev);
		default:
			return NAV.trace(ev);
	}
}
