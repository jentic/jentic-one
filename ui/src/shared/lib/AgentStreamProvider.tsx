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
import { sharedQueryKeys } from '@/shared/api/queryKeys';
import { useCanReadEvents } from '@/shared/auth/useCanReadEvents';
import {
	adaptEvent,
	freezeFeed,
	isApprovalEvent,
	isRetiredEventType,
} from '@/shared/lib/agentStreamModel';
import type {
	ActivityCategory,
	ActivityScope,
	FeedFreeze,
	StreamEvent,
	StreamStatus,
} from '@/shared/lib/agentStreamModel';
import { isEventAccessDenied, listEvents, streamEvents } from '@/shared/lib/railEvents';

/*
  SSE → QUERY-CACHE BRIDGE.

  The rail's event feed is local React state — it never touches the TanStack
  Query cache the Notifications bell (and nav badge) read, so surfaces that count or
  list the same records would go stale until their staleTime lapsed. These
  ROOT keys are invalidated whenever a matching event lands on the stream.

  We invalidate by the shared ROOT keys (not by importing a module's key
  factory) on purpose: this is shared-layer code and must not reach into a
  feature module. The roots are a stable contract owned by the shared
  query-key registry — the bell keys off `attentionRoot` (`['attention', …]`)
  — so a prefix invalidation here refreshes every matching slice without
  crossing a module boundary.
*/
const ATTENTION_ROOT_KEY = sharedQueryKeys.attentionRoot;
const AGENTS_ROOT_KEY = sharedQueryKeys.agentsRoot;

/*
  ACTIVITY RAIL STREAM PROVIDER — backed by the REAL platform event feed.

  The rail consumes jentic-one's `/events` contract: a backlog fetch
  (`GET /events`) seeds the feed, then a live SSE subscription
  (`GET /events/stream`, Bearer-over-fetch) streams new events and adapts wire
  `EventResponse` payloads via `agentStreamModel`.

  The SSE subscription is unfiltered by actor (toasts and the query-cache
  bridge below must see every event the caller can see; the server limits it
  to the caller's own events and their agents', or the whole org for
  `org:admin`). The Activity rail can narrow to one actor: `setScope`
  fetches that actor's backlog via `GET /events?actor_id=&actor_type=` and the
  rail filters the shared list client-side with `matchesActivityScope` — so one
  connection serves every consumer.
*/

type AgentStreamValue = {
	events: StreamEvent[];
	/** The Activity rail's actor lens, shared by the docked rail and the drawer. */
	scope: ActivityScope;
	setScope: (scope: ActivityScope) => void;
	/**
	 * The rest of the Activity view state, owned here with the lens so it's ONE
	 * choice everywhere — the docked rail, the drawer and Monitor's panel, on
	 * every page. `paused` holds back what arrives after the pause (`frozen`);
	 * rows keep reading their live objects, so a resolve still reflects.
	 */
	failuresOnly: boolean;
	setFailuresOnly: (next: boolean) => void;
	/** The rail's "What" filter; empty = every kind. */
	categories: ReadonlySet<ActivityCategory>;
	setCategories: (next: ReadonlySet<ActivityCategory>) => void;
	paused: boolean;
	setPaused: (next: boolean) => void;
	frozen: FeedFreeze | null;
	latest: StreamEvent | null;
	status: StreamStatus;
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
 *   3. `loadOlderEvents` pages backwards via the list cursor.
 *
 * `live` defaults to `true`; tests pass `live={false}` to skip the SSE
 * subscription and drive a deterministic, backlog-only feed.
 *
 * A caller without event access ({@link useCanReadEvents}) gets neither the
 * backlog nor the stream, and `status` reads `forbidden`. A 401/403 from either
 * read lands in the same state, and the stream does not retry it.
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
	const [scope, setScopeState] = useState<ActivityScope>(null);
	// Cursor for the scoped backlog — paging "older" under a lens pages THAT
	// actor's history, not the org's (which could take many pages to reach it).
	const [scopedCursor, setScopedCursor] = useState<string | null>(null);
	const [scopedHasMore, setScopedHasMore] = useState(false);
	const [failuresOnly, setFailuresOnly] = useState(false);
	const [categories, setCategories] = useState<ReadonlySet<ActivityCategory>>(() => new Set());
	const [frozen, setFrozen] = useState<FeedFreeze | null>(null);
	const canReadEvents = useCanReadEvents();
	// The server refused an event read (401/403) — terminal for this session.
	const [accessDenied, setAccessDenied] = useState(false);
	const queryClient = useQueryClient();
	const setScope = useCallback((next: ActivityScope) => {
		setScopeState((prev) =>
			prev?.actorId === next?.actorId && prev?.actorType === next?.actorType ? prev : next,
		);
	}, []);

	/**
	 * Refresh the agent-approval surfaces (pending-agents card, "Awaiting
	 * approval" tile, agents list + nav badge). A CLI self-registration lands as
	 * an `agent.*` SSE event; without this bridge those surfaces sat on their
	 * 45–60s fallback polls and looked broken right after `jentic register`.
	 */
	const invalidateAgentSurfaces = useCallback(() => {
		void queryClient.invalidateQueries({ queryKey: ATTENTION_ROOT_KEY });
		void queryClient.invalidateQueries({ queryKey: AGENTS_ROOT_KEY });
		// A registration changes the actor DIRECTORY too — it's cached
		// aggressively (5-min staleTime) as reference data, and a CLI agent
		// starts emitting events seconds after registering. Without
		// this, every `actor_id` resolution for the new agent (rail rows, the
		// setup wizard's header badge) misses and
		// falls back to the raw `agnt_…` id until the cache expires.
		void queryClient.invalidateQueries({ queryKey: sharedQueryKeys.actorDirectoryRoot });
	}, [queryClient]);

	/**
	 * Refresh the interactive-OAuth surfaces (Settings approval queue + client
	 * rows with their grant counts, per-agent "Connected clients" panels). A
	 * DCR registration, an approval decision, or a consent grant all land as
	 * `oauth_client.*` / `oauth_grant.*` SSE events; without
	 * this bridge those surfaces sat on their staleTime right after the change.
	 */
	const invalidateOAuthSurfaces = useCallback(() => {
		void queryClient.invalidateQueries({ queryKey: sharedQueryKeys.oauthClientsRoot });
		void queryClient.invalidateQueries({ queryKey: sharedQueryKeys.oauthGrantsRoot });
		void queryClient.invalidateQueries({ queryKey: ATTENTION_ROOT_KEY });
	}, [queryClient]);

	/**
	 * Refresh the held-call surfaces (the Approvals list and review page, the
	 * inbox's held-call rows, the pending-count badges, "Waiting for you", and
	 * the held job in Monitor). A hold is filed by an agent and settles
	 * out-of-band — another reviewer decides, the agent withdraws, the expiry
	 * sweep lapses it — so each `execution.approval_*` event refreshes them.
	 */
	const invalidateApprovalSurfaces = useCallback(() => {
		void queryClient.invalidateQueries({ queryKey: sharedQueryKeys.approvalsRoot });
		void queryClient.invalidateQueries({ queryKey: ATTENTION_ROOT_KEY });
		void queryClient.invalidateQueries({ queryKey: sharedQueryKeys.monitorJobsRoot });
		void queryClient.invalidateQueries({ queryKey: sharedQueryKeys.monitorJobRoot });
	}, [queryClient]);

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
				// — a re-delivered event carries the server's current truth, so
				// reconcile in place. Backlog pages (`!front`) are historical and
				// are ignored on collision.
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
			// History the user asked for (Load older, a lens's backlog) raises the
			// cap instead of being sorted past it and dropped on arrival; live
			// events keep the list at whatever size it has reached.
			const cap = Math.max(MAX_EVENTS, front ? prev.length : prev.length + appended.length);
			return merged.slice(0, cap);
		});
	}, []);

	// 1. Backlog seed. Retired-namespace history is tolerated but dropped.
	useEffect(() => {
		if (!canReadEvents) return undefined;
		let cancelled = false;
		void (async () => {
			try {
				const page = await listEvents({ limit: BACKLOG_LIMIT });
				if (cancelled) return;
				upsert(page.data.filter((e) => !isRetiredEventType(e.type)).map(adaptEvent), false);
				setCursor(page.next_cursor ?? null);
				setHasMore(page.has_more);
			} catch (error) {
				// A refusal means no event access. Any other failed backlog is
				// non-fatal — the live stream may still connect.
				if (!cancelled && isEventAccessDenied(error)) setAccessDenied(true);
			}
		})();
		return () => {
			cancelled = true;
		};
	}, [upsert, canReadEvents]);

	// 1b. Scoped backlog. The shared list only holds the newest unfiltered page,
	// which may contain nothing from a quiet agent — seed that actor's history.
	useEffect(() => {
		setScopedCursor(null);
		setScopedHasMore(false);
		if (!scope || !canReadEvents) return undefined;
		let cancelled = false;
		void (async () => {
			try {
				const page = await listEvents({
					actorId: scope.actorId,
					actorType: scope.actorType,
					limit: BACKLOG_LIMIT,
				});
				if (cancelled) return;
				upsert(page.data.filter((e) => !isRetiredEventType(e.type)).map(adaptEvent), false);
				setScopedCursor(page.next_cursor ?? null);
				setScopedHasMore(page.has_more);
			} catch {
				// Non-fatal: the lens still filters whatever the shared feed holds.
			}
		})();
		return () => {
			cancelled = true;
		};
	}, [scope, upsert, canReadEvents]);

	// 2. Live SSE subscription (with auto-reconnect inside `streamEvents`).
	useEffect(() => {
		if (!live || !canReadEvents) {
			setStatus('idle');
			return undefined;
		}
		setStatus('connecting');
		// Ids already delivered on this subscription. Reconnects rewind `since`
		// past the boundary (and the server re-queries an overlap window), so a
		// re-delivered event must RECONCILE list state (upsert is authoritative)
		// but must NOT re-fire the toast/audio cue or the invalidation fan-out.
		// Bounded FIFO: the server prunes its dedup map to the overlap horizon;
		// mirror that discipline so a very long-lived tab can't grow unbounded.
		const deliveredIds = new Set<string>();
		const DELIVERED_IDS_MAX = 5000;
		const unsubscribe = streamEvents(
			{},
			{
				onOpen: () => setStatus('live'),
				onEvent: (wire) => {
					// Tolerate retired-namespace events (historical redeliveries)
					// without rendering, toasting, or invalidating anything.
					if (isRetiredEventType(wire.type)) return;
					const ev = adaptEvent(wire);
					const firstDelivery = !deliveredIds.has(ev.id);
					if (firstDelivery) {
						deliveredIds.add(ev.id);
						if (deliveredIds.size > DELIVERED_IDS_MAX) {
							// Set iterates in insertion order — drop the oldest.
							const oldest = deliveredIds.values().next().value;
							if (oldest !== undefined) deliveredIds.delete(oldest);
						}
					}
					upsert([ev], true);
					if (!firstDelivery) return;
					setLatest(ev);
					// Bridge: agent lifecycle events (CLI self-registration,
					// approval) refresh the agent surfaces the instant they
					// land; OAuth and execution-approval events likewise
					// refresh theirs.
					if (ev.kind === 'agent') {
						invalidateAgentSurfaces();
					} else if (ev.kind === 'oauth') {
						invalidateOAuthSurfaces();
					} else if (isApprovalEvent(ev)) {
						invalidateApprovalSurfaces();
					}
				},
				onError: (error) => {
					// A refusal ends the subscription (the client does not retry it).
					if (isEventAccessDenied(error)) setAccessDenied(true);
					setStatus('error');
				},
				// The client fires onError then onReconnecting back-to-back on
				// every failed attempt; keep 'error' until a connect succeeds
				// (onOpen) so the header doesn't flicker Offline ↔ Connecting.
				onReconnecting: () => setStatus((s) => (s === 'error' ? 'error' : 'connecting')),
			},
		);
		return unsubscribe;
	}, [
		live,
		canReadEvents,
		upsert,
		invalidateAgentSurfaces,
		invalidateOAuthSurfaces,
		invalidateApprovalSurfaces,
	]);

	// The lens at call time vs. now: a Load older still in flight when the lens
	// changes must not write its actor's cursor under the new one.
	const scopeRef = useRef(scope);
	useEffect(() => {
		scopeRef.current = scope;
	}, [scope]);

	const loadOlderEvents = useCallback(async () => {
		const from = scope ? scopedCursor : cursor;
		if (!from || !canReadEvents) return;
		setLoadingOlder(true);
		try {
			const page = await listEvents({
				cursor: from,
				limit: BACKLOG_LIMIT,
				actorId: scope?.actorId,
				actorType: scope?.actorType,
			});
			upsert(page.data.filter((e) => !isRetiredEventType(e.type)).map(adaptEvent), false);
			if (scopeRef.current !== scope) return;
			if (scope) {
				setScopedCursor(page.next_cursor ?? null);
				setScopedHasMore(page.has_more);
			} else {
				setCursor(page.next_cursor ?? null);
				setHasMore(page.has_more);
			}
		} catch {
			/* leave the cursor in place so the user can retry */
		} finally {
			setLoadingOlder(false);
		}
	}, [cursor, scope, scopedCursor, upsert, canReadEvents]);

	const setPaused = useCallback(
		(next: boolean) => setFrozen(next ? freezeFeed(events) : null),
		[events],
	);

	const forbidden = !canReadEvents || accessDenied;
	const value = useMemo<AgentStreamValue>(
		() => ({
			events,
			scope,
			setScope,
			failuresOnly,
			setFailuresOnly,
			categories,
			setCategories,
			paused: frozen !== null,
			setPaused,
			frozen,
			latest,
			status: forbidden ? 'forbidden' : status,
			loadOlderEvents,
			canLoadOlder:
				!forbidden &&
				(scope ? scopedHasMore && scopedCursor != null : hasMore && cursor != null),
			loadingOlder,
		}),
		[
			events,
			scope,
			setScope,
			failuresOnly,
			categories,
			frozen,
			setPaused,
			scopedHasMore,
			scopedCursor,
			latest,
			status,
			forbidden,
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

/**
 * Provider-optional variant for module-side hooks that should SYNC with the
 * stream when it's mounted (the app shell) but must not require it (tests,
 * embedded surfaces).
 */
export function useAgentStreamOptional(): AgentStreamValue | null {
	return useContext(AgentStreamContext);
}
