/**
 * Monitor service tier — TanStack Query hooks.
 *
 * The ONLY backend access path for Monitor views: components/pages call these
 * hooks, which call the repository (`./client`), which calls `@/shared/api`.
 * Views must never reach past this layer (ESLint-enforced). Mirrors the
 * backend's Service layer.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import {
	keepPreviousData,
	useInfiniteQuery,
	useMutation,
	useQuery,
	useQueryClient,
} from '@tanstack/react-query';
import { toast } from '@/shared/ui';
import {
	acknowledgeEvent,
	cancelJob,
	getExecution,
	getJob,
	getUsageStats,
	listActors,
	listAudit,
	listEvents,
	listExecutions,
	listJobs,
	resolveActor,
	streamEvents,
	type ListActorsParams,
	type ListAuditParams,
	type ListEventsParams,
	type ListExecutionsParams,
	type ListJobsParams,
	type UsageStatsParams,
} from '@/modules/monitor/api/client';
import { AuditTargetType, sharedQueryKeys } from '@/shared/api';
import { useAgentStreamOptional } from '@/shared/lib';
import { toJobStatus } from '@/modules/monitor/api/types';
import type {
	ActorListResponse,
	AuditListResponse,
	EventResponse,
	ExecutionListResponse,
	ExecutionResponse,
	JobListResponse,
	JobResponse,
	UsageResponse,
} from '@/shared/api';

/** Stable query-key roots so callers/tests can target invalidation precisely. */
export const monitorKeys = {
	all: ['monitor'] as const,
	executions: (params: ListExecutionsParams) =>
		[...monitorKeys.all, 'executions', params] as const,
	execution: (id: string) => [...monitorKeys.all, 'execution', id] as const,
	jobs: (params: ListJobsParams) => [...monitorKeys.all, 'jobs', params] as const,
	job: (id: string) => [...monitorKeys.all, 'job', id] as const,
	// Derives from the shared cross-module root: the agent-stream provider's
	// `acknowledge` (rail/toast) invalidates that root, so the two prefixes
	// must be the same list or they'd silently drift apart.
	events: (params: ListEventsParams) => [...sharedQueryKeys.monitorEventsRoot, params] as const,
	// Same root, so an acknowledge anywhere also refreshes the Activity feed.
	eventFeed: (params: Omit<ListEventsParams, 'cursor'>) =>
		[...sharedQueryKeys.monitorEventsRoot, 'feed', params] as const,
	audit: (params: ListAuditParams) => [...monitorKeys.all, 'audit', params] as const,
	usage: (params: UsageStatsParams) => [...monitorKeys.all, 'usage', params] as const,
	actors: () => [...monitorKeys.all, 'actors'] as const,
};

/* ------------------------------------------------------------------ */
/* Executions                                                          */
/* ------------------------------------------------------------------ */

/** `enabled: false` keeps a consumer that has nothing to ask for (e.g. a
 * sheet with no trace yet) from firing an unfiltered list request. */
export function useExecutions(
	params: ListExecutionsParams = {},
	{
		enabled = true,
		refetchInterval = false,
	}: { enabled?: boolean; refetchInterval?: number | false } = {},
) {
	return useQuery<ExecutionListResponse>({
		queryKey: monitorKeys.executions(params),
		queryFn: () => listExecutions(params),
		placeholderData: keepPreviousData,
		enabled,
		refetchInterval,
	});
}

export function useExecution(executionId: string | null) {
	return useQuery<ExecutionResponse>({
		queryKey: monitorKeys.execution(executionId ?? ''),
		queryFn: () => getExecution(executionId as string),
		enabled: executionId != null,
	});
}

/**
 * Enriched usage aggregation for the Usage tab (`GET /monitoring/usage`,
 * jentic-one-internal#561), org:admin. Usage asks for the ACTIVE lens
 * only; `keepPreviousData` holds the last lens on screen while a new one loads.
 * The caller gates `enabled` on org:admin so non-admins never fire a doomed
 * request (the gate lives in the view, not here).
 */
export function useUsageStats(
	params: UsageStatsParams = {},
	{
		enabled = true,
		refetchInterval = false,
	}: { enabled?: boolean; refetchInterval?: number | false } = {},
) {
	return useQuery<UsageResponse>({
		queryKey: monitorKeys.usage(params),
		queryFn: () => getUsageStats(params),
		placeholderData: keepPreviousData,
		enabled,
		// Polling pauses while the tab is hidden (TanStack's default).
		refetchInterval,
	});
}

/* ------------------------------------------------------------------ */
/* Jobs                                                                */
/* ------------------------------------------------------------------ */

/**
 * `pollWhileActive` (ms) re-polls only while the loaded page still holds a
 * queued/running job, so a settled queue stops hitting the backend.
 */
export function useJobs(
	params: ListJobsParams = {},
	{ pollWhileActive = false }: { pollWhileActive?: number | false } = {},
) {
	return useQuery<JobListResponse>({
		queryKey: monitorKeys.jobs(params),
		queryFn: () => listJobs(params),
		placeholderData: keepPreviousData,
		refetchInterval: (query) =>
			pollWhileActive !== false &&
			(query.state.data?.data ?? []).some((job) =>
				['queued', 'running'].includes(toJobStatus(job.status)),
			)
				? pollWhileActive
				: false,
	});
}

export function useJob(jobId: string | null) {
	return useQuery<JobResponse>({
		queryKey: monitorKeys.job(jobId ?? ''),
		queryFn: () => getJob(jobId as string),
		enabled: jobId != null,
	});
}

/**
 * Cancel an async job (`POST /jobs/{id}:cancel`, org:admin). On success we toast
 * and invalidate just the jobs feeds + this single-job query so the row/detail
 * flips to its new terminal status on refetch — without nuking unrelated
 * executions/events/audit caches under the `monitor` root.
 */
export function useCancelJob() {
	const queryClient = useQueryClient();
	return useMutation({
		mutationFn: (jobId: string) => cancelJob(jobId),
		onSuccess: (job) => {
			toast({
				title: 'Job cancelled',
				description: `Job ${job.job_id} is now ${job.status}.`,
				variant: 'success',
			});
			queryClient.invalidateQueries({ queryKey: [...monitorKeys.all, 'jobs'] });
			queryClient.invalidateQueries({ queryKey: monitorKeys.job(job.job_id) });
		},
		onError: (error: unknown) => {
			toast({
				title: 'Cancel failed',
				description: error instanceof Error ? error.message : 'Could not cancel the job.',
				variant: 'error',
			});
		},
	});
}

/* ------------------------------------------------------------------ */
/* Events                                                              */
/* ------------------------------------------------------------------ */

/** Page size for the Activity feed — a screenful of rows per "Load older". */
const EVENT_FEED_PAGE = 50;

/**
 * The Activity feed's history: `GET /events` newest-first, paged backwards by
 * cursor ("Load older" appends a page). Live events arrive separately through
 * {@link useEventStream} and are merged on top by the view.
 */
export function useEventFeed(params: Omit<ListEventsParams, 'cursor' | 'limit'>) {
	return useInfiniteQuery({
		queryKey: monitorKeys.eventFeed(params),
		queryFn: ({ pageParam }) =>
			listEvents({ ...params, cursor: pageParam, limit: EVENT_FEED_PAGE }),
		initialPageParam: null as string | null,
		getNextPageParam: (last) => (last.has_more ? (last.next_cursor ?? null) : null),
		placeholderData: keepPreviousData,
	});
}

/** Acknowledge an event (`PATCH /events/{id}`); invalidates the events feeds. */
export function useAcknowledgeEvent() {
	const queryClient = useQueryClient();
	// Provider-optional: when the app shell's stream is mounted, flip its
	// in-memory copy too — the SSE watermark poll never re-delivers an old
	// event on an ack flip, so without this the rail's failure pill keeps
	// counting an event the operator just acknowledged from the Events tab.
	const stream = useAgentStreamOptional();
	return useMutation({
		mutationFn: (eventId: string) => acknowledgeEvent(eventId),
		onSuccess: (event) => {
			toast({
				title: 'Event acknowledged',
				description: event.summary,
				variant: 'success',
			});
			queryClient.invalidateQueries({ queryKey: [...monitorKeys.all, 'events'] });
			stream?.resolveEvent(event.event_id);
		},
		onError: (error: unknown) => {
			toast({
				title: 'Acknowledge failed',
				description:
					error instanceof Error ? error.message : 'Could not acknowledge the event.',
				variant: 'error',
			});
		},
	});
}

export type LiveStreamStatus = 'idle' | 'connecting' | 'live' | 'error';

/**
 * Subscribe to the live event SSE while `enabled`. Newest-first buffer, capped
 * so a long-lived tab doesn't grow without bound. Re-subscribes when the filter
 * params change; cleans up (aborts the fetch-stream) on unmount/disable.
 *
 * Exposes `reconnect()` to force a re-subscribe after a stream error (the EU
 * surfaces this as a "Reconnect" affordance), and `clear()` to empty the
 * buffer. Toasts once when the stream errors so the failure isn't silent.
 */
export function useEventStream(
	params: ListEventsParams,
	enabled: boolean,
	cap = 100,
	// Surfaces that render the connection state inline (the Activity feed)
	// opt out of the interruption toast so a reconnect loop can't spam it.
	{ toastOnError = true }: { toastOnError?: boolean } = {},
) {
	const [events, setEvents] = useState<EventResponse[]>([]);
	const [status, setStatus] = useState<LiveStreamStatus>('idle');
	const [nonce, setNonce] = useState(0);
	const paramsRef = useRef(params);
	paramsRef.current = params;
	const toastOnErrorRef = useRef(toastOnError);
	toastOnErrorRef.current = toastOnError;

	// Serialize the filter so the effect re-subscribes only on a real change
	// (object identity would re-fire every render). `from` is the time-window
	// lower bound — it must be part of the key so narrowing/widening the window
	// re-subscribes the stream (otherwise the live feed keeps the old window).
	const filterKey = JSON.stringify({
		eventType: params.eventType ?? null,
		severity: params.severity ?? null,
		requiresAction: params.requiresAction ?? null,
		actorId: params.actorId ?? null,
		actorType: params.actorType ?? null,
		traceId: params.traceId ?? null,
		from: params.from ?? null,
	});

	useEffect(() => {
		if (!enabled) {
			setStatus('idle');
			// Dropping out of live mode discards the streamed buffer so stale
			// events don't linger merged into the historical page.
			setEvents([]);
			return;
		}
		// A new subscription (toggled on, filter changed, or reconnect) starts
		// from an empty buffer — the previous filter's events no longer match.
		setEvents([]);
		setStatus('connecting');
		const unsubscribe = streamEvents(
			{
				// The historical query's `from` window lower-bound maps to the
				// stream's `since` so the live feed honours the same time window.
				since: paramsRef.current.from ?? null,
				eventType: paramsRef.current.eventType ?? null,
				severity: paramsRef.current.severity ?? null,
				requiresAction: paramsRef.current.requiresAction ?? null,
				actorId: paramsRef.current.actorId ?? null,
				actorType: paramsRef.current.actorType ?? null,
				traceId: paramsRef.current.traceId ?? null,
			},
			{
				onOpen: () => setStatus('live'),
				onEvent: (event) => setEvents((prev) => [event, ...prev].slice(0, cap)),
				onError: (error) => {
					setStatus('error');
					if (!toastOnErrorRef.current) return;
					toast({
						title: 'Live stream interrupted',
						description: error.message || 'The event stream disconnected.',
						variant: 'error',
					});
				},
			},
		);
		return unsubscribe;
		// Re-subscribe when the serialized filter changes or reconnect is requested.
	}, [enabled, cap, filterKey, nonce]);

	const clear = useCallback(() => setEvents([]), []);
	const reconnect = useCallback(() => setNonce((n) => n + 1), []);
	return { events, status, clear, reconnect };
}

/* ------------------------------------------------------------------ */
/* Audit (actor lens)                                                  */
/* ------------------------------------------------------------------ */

/** `/audit` is org:admin — callers pass `enabled: isAdmin` (and false when
 * they have nothing to look up) so non-admins never fire a 403. */
export function useAudit(
	params: ListAuditParams = {},
	{ enabled = true }: { enabled?: boolean } = {},
) {
	return useQuery<AuditListResponse>({
		queryKey: monitorKeys.audit(params),
		queryFn: () => listAudit(params),
		placeholderData: keepPreviousData,
		enabled,
	});
}

/**
 * Resolve "who did it" for a trace or job.
 *
 * Executions now carry `actor_id`/`actor_type` directly (jentic-one#375), so the
 * trace actor is read straight off the execution record — accurate and available
 * to non-admins. The audit log is kept only as a fallback for older traces whose
 * execution records predate actor attribution.
 *
 * Jobs still have no actor on the wire payload, so they resolve via the audit
 * log filtered server-side by `target_id` (the job id).
 */
export function useActorForTrace(
	traceId: string | null,
	{ canReadAudit = false }: { canReadAudit?: boolean } = {},
) {
	// Primary source: the execution record's own actor fields (#375). Nothing
	// to look up without a trace — don't fire an unfiltered list.
	const execQuery = useExecutions(traceId ? { traceId } : {}, { enabled: traceId != null });
	const exec = traceId
		? (execQuery.data?.data ?? []).find((e) => e.trace_id === traceId)
		: undefined;
	const execActor =
		exec && (exec.actor_id || exec.actor_type)
			? { actorId: exec.actor_id || null, actorType: exec.actor_type }
			: null;

	// Fallback for traces whose execution record predates actor attribution.
	// Audit is org:admin, so only admins can take this path.
	const needsAudit = traceId != null && execQuery.isSuccess && !execActor && canReadAudit;
	const auditQuery = useAudit({ limit: 50 }, { enabled: needsAudit });
	const entries = needsAudit ? (auditQuery.data?.data ?? []) : [];
	const matched = traceId ? entries.filter((e) => e.trace_id === traceId) : [];

	return { ...execQuery, actor: execActor ?? resolveActor(matched) };
}

export function useActorForJob(
	jobId: string | null,
	{ canReadAudit = false }: { canReadAudit?: boolean } = {},
) {
	// Filter server-side by target_type+target_id. The backend rejects a
	// target_id without its matching target_type (400 invalid_input), so both
	// must be sent together. Audit is org:admin — non-admins skip the lookup.
	const enabled = jobId != null && canReadAudit;
	const query = useAudit(jobId ? { targetType: AuditTargetType.JOB, targetId: jobId } : {}, {
		enabled,
	});
	const entries = enabled ? (query.data?.data ?? []) : [];
	const matched = jobId ? entries.filter((e) => e.job_id === jobId || e.target_id === jobId) : [];
	return { ...query, actor: resolveActor(matched), canReadAudit };
}

/* ------------------------------------------------------------------ */
/* Actor directory (global filter picker)                              */
/* ------------------------------------------------------------------ */

/**
 * Hydrate the actor directory for the global filter bar's actor picker.
 * Directory data is small and slow-changing, so we cache it aggressively and
 * pull a large page in one shot.
 */
export function useActors(params: ListActorsParams = {}) {
	return useQuery<ActorListResponse>({
		queryKey: monitorKeys.actors(),
		queryFn: () => listActors(params),
		staleTime: 5 * 60 * 1000,
	});
}
