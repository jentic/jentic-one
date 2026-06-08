import type {
	AgentUsageSummary,
	ApiUsageSummary,
	ExecutionLogEntry,
	ExecutionStatus,
	JobLogEntry,
	MonitorStats,
	TimelinePoint,
	ToolkitUsageSummary,
	TopApiUsage,
} from '@/components/monitor/types';
import type { JobOut, TraceOut, UsageResponse } from '@/api/types';

const TERMINAL_OK = new Set(['success', 'completed', 'complete', 'ok', 'done']);
const TERMINAL_FAIL = new Set(['failed', 'error', 'errored']);
const ACTIVE = new Set(['running', 'in_progress', 'pending', 'queued']);

/**
 * Map a backend status string ("success" / "failed" / etc.) onto the
 * webapp's `ExecutionStatus` enum used by the UI components.
 */
export function mapExecutionStatus(status: string | null | undefined): ExecutionStatus {
	if (!status) return 'QUEUED';
	const s = status.toLowerCase();
	if (TERMINAL_OK.has(s)) return 'COMPLETED';
	if (TERMINAL_FAIL.has(s)) return 'FAILED';
	if (s === 'running' || s === 'in_progress') return 'RUNNING';
	if (s === 'queued' || s === 'pending') return 'QUEUED';
	return 'QUEUED';
}

/** Heuristic: extract a vendor slug from an `operation_id` like "slack.chat.post" */
/**
 * Pull the upstream API host out of an `operation_id`.
 *
 * The mini backend writes `executions.operation_id` in two formats:
 * - Production (broker calls): `METHOD/host/path`, e.g.
 *   `GET/api.stripe.com/v1/payment_intents`. We return `api.stripe.com`.
 * - Legacy/dotted (seed data and older traces): `vendor.operation`, e.g.
 *   `slack.chat.postMessage`. We fall back to splitting on the first `.`.
 *
 * The result is lowercased and stripped of anything that isn't safe for use as
 * a vendor key (icons, palette indexes). Empty input → `'unknown'`.
 *
 * NOTE: Prefer `trace.api_id` from the backend whenever it's available — that's
 * the FK-shaped catalog id (`stripe.com`, not `api.stripe.com`) and joins
 * cleanly to `apis.name`. This helper exists for two cases that don't have
 * an `api_id` on the wire: jobs (whose `capability` is the operation_id with
 * no resolved api), and timeline points whose vendor slug is used purely for
 * palette/icon resolution.
 */
export function vendorFromOperation(operationId: string | null | undefined): string {
	if (!operationId) return 'unknown';
	const trimmed = operationId.trim();
	if (!trimmed) return 'unknown';

	// Production format: METHOD/host/path. Take the second slash-segment.
	if (trimmed.includes('/')) {
		const firstSlash = trimmed.indexOf('/');
		const rest = trimmed.slice(firstSlash + 1);
		const nextSlash = rest.indexOf('/');
		const host = nextSlash > 0 ? rest.slice(0, nextSlash) : rest;
		if (host) return host.toLowerCase().replace(/[^a-z0-9.\-_]/g, '') || 'unknown';
	}

	// Legacy dotted format: vendor.operation.
	const dot = trimmed.indexOf('.');
	const vendor = dot > 0 ? trimmed.slice(0, dot) : trimmed;
	return vendor.toLowerCase().replace(/[^a-z0-9_-]/g, '') || 'unknown';
}

/**
 * Resolve the human-readable API label for a trace.
 *
 * Prefers the backend-joined `api_name` (catalog `apis.name`, e.g. "Stripe API"),
 * falls back to `api_id` (catalog form, e.g. `stripe.com`) when the catalog
 * doesn't have a name, and finally derives a vendor slug from `operation_id`
 * for legacy rows that pre-date the `api_id` column. Returns `'unknown'` only
 * when there is genuinely nothing to display.
 */
export function apiDisplayFromTrace(trace: TraceOut): string {
	if (trace.api_name) return trace.api_name;
	if (trace.api_id) return trace.api_id;
	return vendorFromOperation(trace.operation_id ?? trace.workflow_id ?? null);
}

/**
 * Resolve the *vendor slug* for a trace — the stable key used by `VendorIcon`
 * for palette and icon resolution. Prefers `api_id` (catalog form, stable
 * across renames) over the host segment in `operation_id` (e.g.
 * `api.stripe.com` vs the catalog's `stripe.com` — same vendor, different
 * palette slot if we used the host). Falls back to operation-id parsing
 * for legacy rows.
 */
export function vendorSlugFromTrace(trace: TraceOut): string {
	if (trace.api_id) return trace.api_id.toLowerCase();
	return vendorFromOperation(trace.operation_id ?? trace.workflow_id ?? null);
}

/**
 * Convert a unix timestamp (seconds OR milliseconds) to an ISO string.
 * The mini backend returns numeric seconds; UI formatters expect ISO strings
 * downstream of `formatRelativeTime` callers, so we normalise once here.
 */
function toIso(ts: number | null | undefined): string {
	if (ts == null) return new Date(0).toISOString();
	const ms = ts < 1e12 ? ts * 1000 : ts;
	return new Date(ms).toISOString();
}

function toMs(ts: number | null | undefined): number {
	if (ts == null) return 0;
	return ts < 1e12 ? ts * 1000 : ts;
}

/**
 * Collapse a `TraceOut` into the `ExecutionLogEntry` shape used by the
 * monitor table, sidebar, and detail sheet. Falls back gracefully when the
 * mini backend omits joined fields (tenant_name, vendor, etc.).
 */
export function traceToLogEntry(
	trace: TraceOut,
	toolkitNameById: Map<string, string> = new Map(),
	agentNameById: Map<string, string> = new Map(),
): ExecutionLogEntry {
	const status = mapExecutionStatus(trace.status);
	const isWorkflow = !!trace.workflow_id;
	const vendor = vendorSlugFromTrace(trace);
	const apiName = apiDisplayFromTrace(trace);
	const toolkitId = trace.toolkit_id ?? null;
	const agentId = trace.agent_id ?? null;

	return {
		executionId: trace.id,
		executionLogId: trace.id,
		executionType: isWorkflow ? 'workflow' : 'operation',
		status,
		workflowId: trace.workflow_id ?? null,
		workflowName: trace.workflow_id ?? null,
		operationName: trace.operation_id ?? null,
		toolkitId,
		toolkitName: toolkitId ? (toolkitNameById.get(toolkitId) ?? toolkitId) : null,
		agentId,
		agentName: agentId ? (agentNameById.get(agentId) ?? agentId) : null,
		apiVendor: vendor,
		apiName,
		createdAt: toIso(trace.created_at),
		startedAt: trace.created_at != null ? toIso(trace.created_at) : undefined,
		completedAt: trace.completed_at != null ? toIso(trace.completed_at) : undefined,
		durationMs: trace.duration_ms ?? undefined,
		errorMessage: trace.error ?? undefined,
		jobId: trace.job_id ?? null,
		parentTraceId: trace.parent_trace_id ?? null,
	};
}

/**
 * Convert a job that has not yet materialised a trace into a "job-only"
 * row. Once the job's `trace_id` is set the corresponding trace row supersedes
 * it (deduplication happens in `mergeInFlightWithHistory`).
 */
export function jobToLogEntry(
	job: JobOut,
	toolkitNameById: Map<string, string> = new Map(),
	agentNameById: Map<string, string> = new Map(),
): ExecutionLogEntry {
	const status = mapExecutionStatus(job.status);
	const operationId = (job.capability as string | undefined) ?? null;
	const vendor = vendorFromOperation(operationId);
	// Jobs don't carry api_id on the wire (the join lives on executions, and
	// jobs only have a capability id). Use the vendor slug as the visible
	// label — same heuristic the timeline used to use for everything.
	const apiName = vendor;
	const toolkitId = (job.toolkit_id as string | null | undefined) ?? null;
	const agentId = (job.agent_id as string | null | undefined) ?? null;
	const traceId = (job.trace_id as string | null | undefined) ?? null;
	const id = traceId ?? job.job_id;

	return {
		executionId: id,
		executionLogId: id,
		executionType: 'job',
		status,
		workflowId: null,
		workflowName: null,
		operationName: operationId,
		toolkitId,
		toolkitName: toolkitId ? (toolkitNameById.get(toolkitId) ?? toolkitId) : null,
		agentId,
		agentName: agentId ? (agentNameById.get(agentId) ?? agentId) : null,
		apiVendor: vendor,
		apiName,
		createdAt: toIso(job.created_at),
		completedAt: job.completed_at != null ? toIso(job.completed_at) : undefined,
		isJobOnly: true,
		jobId: job.job_id,
		parentTraceId: (job.parent_trace_id as string | null | undefined) ?? null,
	};
}

/**
 * Merge in-flight (job) rows with the historical trace rows for the
 * execution log table. Webapp keys on `executionId`; here we use the same
 * key — `traceToLogEntry` and `jobToLogEntry` both stamp it with the
 * canonical id (trace.id, falling back to job.id when no trace_id yet).
 */
export function mergeInFlightWithHistory(
	traces: ExecutionLogEntry[],
	jobs: ExecutionLogEntry[],
): ExecutionLogEntry[] {
	const seen = new Set<string>();
	const merged: ExecutionLogEntry[] = [];
	for (const job of jobs) {
		seen.add(job.executionId);
		merged.push(job);
	}
	for (const trace of traces) {
		if (seen.has(trace.executionId)) continue;
		merged.push(trace);
	}
	merged.sort((a, b) => {
		const ta = new Date(a.createdAt).getTime();
		const tb = new Date(b.createdAt).getTime();
		return tb - ta;
	});
	return merged;
}

/**
 * Convert recent traces into a `TimelinePoint[]` for the daily bar chart.
 * The chart uses `timestamp`, `vendor`, `apiName`, and `toolkitName` only.
 */
export function tracesToTimelinePoints(
	traces: TraceOut[],
	toolkitNameById: Map<string, string> = new Map(),
	agentNameById: Map<string, string> = new Map(),
): TimelinePoint[] {
	return traces.map((t) => {
		const toolkitId = t.toolkit_id ?? '';
		const agentId = t.agent_id ?? '';
		return {
			executionId: t.id,
			timestamp: toMs(t.created_at),
			vendor: vendorSlugFromTrace(t),
			apiName: apiDisplayFromTrace(t),
			status: mapExecutionStatus(t.status),
			durationMs: t.duration_ms ?? undefined,
			workflowName: t.workflow_id ?? '',
			toolkitName: toolkitId ? (toolkitNameById.get(toolkitId) ?? toolkitId) : 'Unknown',
			agentId: agentId || '__unattributed__',
			agentName: agentId ? (agentNameById.get(agentId) ?? agentId) : 'Unattributed',
		};
	});
}

/**
 * Build the `MonitorStats` block from the `/traces/usage` response. The
 * response gives total/success/failed counts, average ms, and active_now.
 */
export function usageToMonitorStats(usage: UsageResponse): MonitorStats {
	const total = usage.stats.total ?? 0;
	const success = usage.stats.success ?? 0;
	const failed = usage.stats.failed ?? 0;
	const successRate = total > 0 ? (success / total) * 100 : 100;
	return {
		totalExecutions: total,
		successRate,
		avgLatencyMs: Math.round(usage.stats.avg_ms ?? 0),
		activeNow: usage.stats.active_now ?? 0,
		failureCount: failed,
	};
}

interface UsageRowsByGroup {
	apis: ApiUsageSummary[];
	toolkits: ToolkitUsageSummary[];
}

/**
 * Convert two "top" responses (group_by=api and group_by=toolkit) into the
 * `ApiUsageSummary[]` / `ToolkitUsageSummary[]` shapes the bubble chart wants.
 *
 * The mini backend's `top` rows return `{key, label, total, success, failed,
 * avg_ms}`. For the API group, `key` is the host (vendor) and `label` is the
 * api name; for toolkit group, `key` is the toolkit_id and `label` its
 * display name.
 */
export function usageToTopRows(
	apiUsage: UsageResponse | null,
	toolkitUsage: UsageResponse | null,
): UsageRowsByGroup {
	const apis: ApiUsageSummary[] = apiUsage
		? apiUsage.top.map((row) => {
				const total = row.total ?? 0;
				const success = row.success ?? 0;
				return {
					vendor: row.key,
					apiName: row.label ?? row.key,
					totalExecutions: total,
					successRate: total > 0 ? (success / total) * 100 : 100,
					avgLatencyMs: Math.round(row.avg_ms ?? 0),
					recentTrend: row.trend ?? [],
				};
			})
		: [];

	const toolkits: ToolkitUsageSummary[] = toolkitUsage
		? toolkitUsage.top.map((row) => {
				const total = row.total ?? 0;
				const success = row.success ?? 0;
				const topApis: TopApiUsage[] = [];
				return {
					toolkitId: row.key,
					toolkitName: row.label ?? row.key,
					toolkitMode: null,
					totalExecutions: total,
					successRate: total > 0 ? (success / total) * 100 : 100,
					avgLatencyMs: Math.round(row.avg_ms ?? 0),
					topApis,
					recentTrend: row.trend ?? [],
				};
			})
		: [];

	return { apis, toolkits };
}

/**
 * Convert a `group_by=agent` usage response into `AgentUsageSummary[]` rows.
 * The mini backend resolves friendly names via the `agents` table, so
 * `row.label` is the human-readable client name when available; rows where the
 * agent is missing (legacy traces with NULL agent_id) come back with an empty
 * `key`, which we surface as the "Unattributed" bucket so operators can see
 * the volume without it silently disappearing.
 */
export function usageToAgentRows(usage: UsageResponse | null): AgentUsageSummary[] {
	if (!usage) return [];
	return usage.top.map((row) => {
		const total = row.total ?? 0;
		const success = row.success ?? 0;
		const id = row.key || '__unattributed__';
		const label = row.label ?? (row.key ? row.key : 'Unattributed');
		return {
			agentId: id,
			agentName: label,
			totalExecutions: total,
			successRate: total > 0 ? (success / total) * 100 : 100,
			avgLatencyMs: Math.round(row.avg_ms ?? 0),
			recentTrend: row.trend ?? [],
		};
	});
}

/** Convert a status string back into the values the executions backend speaks. */
export function statusFilterToBackend(filter: string | null): string | null {
	if (!filter || filter === 'ALL') return null;
	switch (filter) {
		case 'COMPLETED':
			return 'success';
		case 'FAILED':
			return 'failed';
		case 'RUNNING':
			return 'running';
		case 'QUEUED':
			return 'pending';
		default:
			return null;
	}
}

/**
 * Backend stamps cancelled jobs as `status='failed'` with this exact error
 * message (see `cancel_job` in `routers/jobs.py`). The Jobs tab surfaces them
 * as a dedicated "Cancelled" pill rather than lumping them with real failures
 * — keeps the operational signal honest. Match is exact, not substring, to
 * avoid mis-labelling user errors that happen to mention "cancel".
 */
const JOB_CANCELLED_MARKER = 'Cancelled by client';

/**
 * Map a backend `JobOut` row onto the Jobs tab's `JobLogEntry` shape.
 *
 * Notable derivations:
 * - `cancelled` status is synthesised from (status='failed' && error matches
 *   the cancellation marker) — see comment on `JOB_CANCELLED_MARKER`.
 * - `durationMs` is computed from created_at + completed_at; the backend
 *   doesn't expose duration on jobs the way it does on executions.
 */
export function jobToJobLogEntry(
	job: JobOut,
	toolkitNameById: Map<string, string> = new Map(),
	agentNameById: Map<string, string> = new Map(),
): JobLogEntry {
	const rawKind = (job.kind as string | null | undefined) ?? null;
	const kind: JobLogEntry['kind'] =
		rawKind === 'workflow' || rawKind === 'broker' ? rawKind : 'unknown';

	const rawStatus = (job.status as string | null | undefined) ?? 'pending';
	const errorMessage = (job.error as string | null | undefined) ?? null;
	let status: JobLogEntry['status'];
	if (rawStatus === 'failed' && errorMessage === JOB_CANCELLED_MARKER) {
		status = 'cancelled';
	} else if (
		rawStatus === 'pending' ||
		rawStatus === 'running' ||
		rawStatus === 'complete' ||
		rawStatus === 'failed' ||
		rawStatus === 'upstream_async'
	) {
		status = rawStatus;
	} else {
		status = 'pending';
	}

	const toolkitId = (job.toolkit_id as string | null | undefined) ?? null;
	const agentId = (job.agent_id as string | null | undefined) ?? null;

	const createdMs = job.created_at != null ? toMs(job.created_at) : null;
	const completedMs = job.completed_at != null ? toMs(job.completed_at) : null;
	const durationMs =
		createdMs != null && completedMs != null && completedMs >= createdMs
			? completedMs - createdMs
			: undefined;

	return {
		jobId: job.job_id,
		kind,
		capability: (job.capability as string | null | undefined) ?? null,
		status,
		toolkitId,
		toolkitName: toolkitId ? (toolkitNameById.get(toolkitId) ?? toolkitId) : null,
		agentId,
		agentName: agentId ? (agentNameById.get(agentId) ?? agentId) : null,
		traceId: (job.trace_id as string | null | undefined) ?? null,
		parentTraceId: (job.parent_trace_id as string | null | undefined) ?? null,
		upstreamJobUrl: (job.upstream_job_url as string | null | undefined) ?? null,
		httpStatus: (job.http_status as number | null | undefined) ?? null,
		errorMessage: status === 'cancelled' ? null : errorMessage,
		createdAt: toIso(job.created_at),
		completedAt: job.completed_at != null ? toIso(job.completed_at) : undefined,
		durationMs,
	};
}

export const STATUS_HELPERS = {
	TERMINAL_OK,
	TERMINAL_FAIL,
	ACTIVE,
};
