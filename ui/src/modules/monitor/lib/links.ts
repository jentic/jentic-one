/**
 * Deep-link helpers for the Monitor module.
 *
 * Monitor is a single page (`ROUTES.monitor`, rendered at `/app/monitor`) whose
 * entire view state — active tab, open detail sheet, and per-tab filters — lives
 * in the URL search params so every lens is shareable, bookmarkable, and
 * back-button friendly. These helpers are the one place that knows the param
 * vocabulary:
 *
 *   tab        overview | executions | jobs | events | audit
 *   trace_id   Executions: open the trace detail sheet for this trace
 *   execution_id Executions: open the detail sheet for a single execution
 *              (fallback when the row has no usable trace)
 *   job_id     Jobs: open the job detail sheet for this job
 *   status     Executions/Jobs: active status filter (the UI filter value)
 *   live       Events: "1" when the live SSE stream is on
 *   target_id  Audit: filter by target (carried from a detail sheet's "View in audit")
 *   target_type Audit: the target's type, required alongside target_id
 *   days       Global: trailing time-window selection (1 | 7 | 30)
 *   actor_id   Global: filter by actor (set by the global filter bar)
 *   actor_type Global: the selected actor's type (carried alongside actor_id)
 *
 * Building links: prefer `monitorHref(...)` so callers don't hand-assemble
 * query strings (and so cross-references always carry their id).
 */
import type { MonitorTab } from '@/modules/monitor/api';
import { ROUTES } from '@/shared/app';

/**
 * The backend stores `trace_id="unknown"` for executions/jobs that ran without
 * a `traceparent`/`x-request-id` header (see the broker's executor). Such a
 * value can't open a trace sheet or filter the audit log, so we treat it — and
 * empty/nullish ids — as "no usable trace" everywhere a cross-link is offered.
 */
export function hasTrace(traceId: string | null | undefined): traceId is string {
	return traceId != null && traceId !== '' && traceId !== 'unknown';
}

export interface MonitorLinkParams {
	tab?: MonitorTab;
	traceId?: string;
	executionId?: string;
	jobId?: string;
	status?: string;
	live?: boolean;
	targetType?: string;
	targetId?: string;
	actorId?: string;
	actorType?: string;
	days?: number;
}

/** Build a Monitor href with the given lens + deep-link params. */
export function monitorHref(params: MonitorLinkParams): string {
	const q = new URLSearchParams();
	if (params.tab) q.set('tab', params.tab);
	// Never emit a placeholder trace id — it would deep-link to nothing.
	if (hasTrace(params.traceId)) q.set('trace_id', params.traceId);
	if (params.executionId) q.set('execution_id', params.executionId);
	if (params.jobId) q.set('job_id', params.jobId);
	if (params.status && params.status !== 'all') q.set('status', params.status);
	if (params.live) q.set('live', '1');
	if (params.targetType) q.set('target_type', params.targetType);
	if (params.targetId) q.set('target_id', params.targetId);
	if (params.actorId) q.set('actor_id', params.actorId);
	if (params.actorType) q.set('actor_type', params.actorType);
	if (params.days) q.set('days', String(params.days));
	const qs = q.toString();
	return `${ROUTES.monitor}${qs ? `?${qs}` : ''}`;
}
