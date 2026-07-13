/**
 * Monitor service tier — TanStack Query hooks.
 *
 * The ONLY backend access path for Monitor views: components/pages call these
 * hooks, which call the repository (`./client`), which calls `@/shared/api`.
 * Views must never reach past this layer (ESLint-enforced). Mirrors the
 * backend's Service layer.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from '@/shared/ui';
import {
	acknowledgeEvent,
	cancelJob,
	getExecution,
	getExecutionStats,
	getJob,
	listActors,
	listAudit,
	listEvents,
	listExecutions,
	listJobs,
	resolveActor,
	streamEvents,
	type ExecutionStatsParams,
	type ListActorsParams,
	type ListAuditParams,
	type ListEventsParams,
	type ListExecutionsParams,
	type ListJobsParams,
} from '@/modules/monitor/api/client';
import { AuditTargetType } from '@/shared/api';
import type {
	ActorListResponse,
	AuditListResponse,
	EventListResponse,
	EventResponse,
	ExecutionListResponse,
	ExecutionResponse,
	ExecutionStatsResponse,
	JobListResponse,
	JobResponse,
} from '@/shared/api';

/** Stable query-key roots so callers/tests can target invalidation precisely. */
export const monitorKeys = {
	all: ['monitor'] as const,
	executions: (params: ListExecutionsParams) =>
		[...monitorKeys.all, 'executions', params] as const,
	execution: (id: string) => [...monitorKeys.all, 'execution', id] as const,
	jobs: (params: ListJobsParams) => [...monitorKeys.all, 'jobs', params] as const,
	job: (id: string) => [...monitorKeys.all, 'job', id] as const,
	events: (params: ListEventsParams) => [...monitorKeys.all, 'events', params] as const,
	audit: (params: ListAuditParams) => [...monitorKeys.all, 'audit', params] as const,
	stats: (params: ExecutionStatsParams) => [...monitorKeys.all, 'stats', params] as const,
	actors: () => [...monitorKeys.all, 'actors'] as const,
};

/* ------------------------------------------------------------------ */
/* Executions                                                          */
/* ------------------------------------------------------------------ */

export function useExecutions(params: ListExecutionsParams = {}) {
	return useQuery<ExecutionListResponse>({
		queryKey: monitorKeys.executions(params),
		queryFn: () => listExecutions(params),
		placeholderData: keepPreviousData,
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
 * Aggregated execution stats for the Overview tab (GET /monitoring/executions,
 * jentic-one#386). `days` is the trailing window (1–30); the endpoint defaults
 * to 7. Powers the usage charts + top-operations panel.
 */
export function useExecutionStats(params: ExecutionStatsParams = {}) {
	return useQuery<ExecutionStatsResponse>({
		queryKey: monitorKeys.stats(params),
		queryFn: () => getExecutionStats(params),
		placeholderData: keepPreviousData,
	});
}

/* ------------------------------------------------------------------ */
/* Jobs                                                                */
/* ------------------------------------------------------------------ */

export function useJobs(params: ListJobsParams = {}) {
	return useQuery<JobListResponse>({
		queryKey: monitorKeys.jobs(params),
		queryFn: () => listJobs(params),
		placeholderData: keepPreviousData,
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

export function useEvents(params: ListEventsParams = {}) {
	return useQuery<EventListResponse>({
		queryKey: monitorKeys.events(params),
		queryFn: () => listEvents(params),
		placeholderData: keepPreviousData,
	});
}

/** Acknowledge an event (`PATCH /events/{id}`); invalidates the events feeds. */
export function useAcknowledgeEvent() {
	const queryClient = useQueryClient();
	return useMutation({
		mutationFn: (eventId: string) => acknowledgeEvent(eventId),
		onSuccess: (event) => {
			toast({
				title: 'Event acknowledged',
				description: event.summary,
				variant: 'success',
			});
			queryClient.invalidateQueries({ queryKey: [...monitorKeys.all, 'events'] });
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
export function useEventStream(params: ListEventsParams, enabled: boolean, cap = 100) {
	const [events, setEvents] = useState<EventResponse[]>([]);
	const [status, setStatus] = useState<LiveStreamStatus>('idle');
	const [nonce, setNonce] = useState(0);
	const paramsRef = useRef(params);
	paramsRef.current = params;

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

export function useAudit(params: ListAuditParams = {}) {
	return useQuery<AuditListResponse>({
		queryKey: monitorKeys.audit(params),
		queryFn: () => listAudit(params),
		placeholderData: keepPreviousData,
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
export function useActorForTrace(traceId: string | null) {
	// Primary source: the execution record's own actor fields (#375).
	const execQuery = useExecutions(traceId ? { traceId } : {});
	const exec = traceId
		? (execQuery.data?.data ?? []).find((e) => e.trace_id === traceId)
		: undefined;
	const execActor =
		exec && (exec.actor_id || exec.actor_type)
			? { actorId: exec.actor_id || null, actorType: exec.actor_type }
			: null;

	// Fallback for traces whose execution record predates actor attribution.
	const auditQuery = useAudit(traceId && !execActor ? { limit: 50 } : {});
	const entries = auditQuery.data?.data ?? [];
	const matched = traceId ? entries.filter((e) => e.trace_id === traceId) : [];

	return { ...execQuery, actor: execActor ?? resolveActor(matched) };
}

export function useActorForJob(jobId: string | null) {
	// Filter server-side by target_type+target_id. The backend rejects a
	// target_id without its matching target_type (400 invalid_input), so both
	// must be sent together.
	const query = useAudit(jobId ? { targetType: AuditTargetType.JOB, targetId: jobId } : {});
	const entries = query.data?.data ?? [];
	const matched = jobId ? entries.filter((e) => e.job_id === jobId || e.target_id === jobId) : [];
	return { ...query, actor: resolveActor(matched) };
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
