/**
 * Monitor page types — ported from the webapp's monitor types, with names
 * kept stable so component code lifts 1:1. The mini backend speaks
 * `TraceOut`/`JobOut` shapes; transformers in `lib/monitor-transformers.ts`
 * map those into the UI-facing entries below.
 */

export type TimeRange = '1h' | '24h' | '7d' | '30d' | 'all';

export type ExecutionStatus = 'QUEUED' | 'PRE_CHECK' | 'RUNNING' | 'COMPLETED' | 'FAILED';

export type ExecutionStatusFilter = ExecutionStatus | 'ALL';

export type ExecutionType = 'operation' | 'workflow' | 'job';

export type MonitorTab = 'overview' | 'log' | 'jobs';

/**
 * Job-kind filter used by the Jobs tab. Maps directly onto the backend
 * `kind` query parameter on `GET /jobs`. "all" is sentinel for "no filter".
 */
export type JobKindFilter = 'all' | 'workflow' | 'broker';

/**
 * Job-status filter used by the Jobs tab. The backend stores cancelled jobs
 * as `status='failed'` with `error='Cancelled by client'` — UI surfaces a
 * "Cancelled" pill by deriving it client-side from those two fields, so the
 * filter set excludes a discrete cancelled value.
 */
export type JobStatusFilter =
	| 'all'
	| 'inflight'
	| 'pending'
	| 'running'
	| 'complete'
	| 'failed'
	| 'upstream_async';

export interface MonitorStats {
	totalExecutions: number;
	successRate: number;
	avgLatencyMs: number;
	activeNow: number;
	failureCount: number;
}

export interface TopApiUsage {
	vendor: string;
	apiName: string;
	count: number;
}

export interface ToolkitUsageSummary {
	toolkitId: string;
	toolkitName: string;
	toolkitMode: 'live' | 'sandbox' | null;
	totalExecutions: number;
	successRate: number;
	avgLatencyMs: number;
	topApis: TopApiUsage[];
	recentTrend: number[];
}

export interface ApiUsageSummary {
	vendor: string;
	apiName: string;
	apiVersion?: string;
	iconUrl?: string;
	totalExecutions: number;
	successRate: number;
	avgLatencyMs: number;
	recentTrend: number[];
}

export interface AgentUsageSummary {
	agentId: string;
	agentName: string;
	totalExecutions: number;
	successRate: number;
	avgLatencyMs: number;
	recentTrend: number[];
}

/** A single execution plotted on the daily bar chart. */
export interface TimelinePoint {
	executionId: string;
	timestamp: number;
	vendor: string;
	apiName: string;
	status: ExecutionStatus;
	durationMs?: number;
	workflowName: string;
	toolkitName: string;
	agentId: string;
	agentName: string;
}

export interface ExecutionLogEntry {
	executionId: string;
	executionLogId: string;
	executionType: ExecutionType;
	status: ExecutionStatus;
	workflowId: string | null;
	workflowName: string | null;
	operationName: string | null;
	toolkitId: string | null;
	toolkitName: string | null;
	agentId: string | null;
	agentName: string | null;
	apiVendor: string | null;
	apiName: string | null;
	createdAt: string;
	startedAt?: string;
	completedAt?: string;
	durationMs?: number;
	errorMessage?: string;
	/** True for rows that originated from a job that hasn't materialised a trace yet. */
	isJobOnly?: boolean;
	/** Async job that owns this trace (Prefer: wait=0, upstream-202, async workflow). */
	jobId?: string | null;
	/** Parent workflow trace id when this trace is a child broker step inside a workflow. */
	parentTraceId?: string | null;
}

/**
 * Row shape for the Jobs tab table. Maps from `JobOut` (1 job → 1 entry).
 * "Cancelled" is derived client-side from (status='failed' && error matches
 * the cancellation marker) so it can render as a distinct pill without the
 * backend exposing a separate status value.
 */
export interface JobLogEntry {
	jobId: string;
	kind: 'workflow' | 'broker' | 'unknown';
	capability: string | null;
	status: 'pending' | 'running' | 'complete' | 'failed' | 'upstream_async' | 'cancelled';
	toolkitId: string | null;
	toolkitName: string | null;
	agentId: string | null;
	agentName: string | null;
	traceId: string | null;
	parentTraceId: string | null;
	upstreamJobUrl: string | null;
	httpStatus: number | null;
	errorMessage: string | null;
	createdAt: string;
	completedAt?: string;
	durationMs?: number;
}

export interface ExecutionStepResult {
	id: string;
	stepId: string;
	stepIndex: number;
	apiVendor: string;
	apiName: string;
	success: boolean;
	durationMs: number | null;
	createdAt: string;
}

/**
 * Per-step backend record for the workflow steps panel in the drawer.
 * Mirrors the `TraceStepOut` shape returned by `GET /traces/{id}.steps`,
 * trimmed to the fields the drawer renders. Optional everywhere because
 * the writer populates them lazily — old traces predating M5 will have
 * `operation` and `status` as `null`.
 */
export interface ExecutionStepRow {
	stepId: string;
	stepIndex: number;
	operation: string | null;
	status: string | null;
	httpStatus: number | null;
	error: string | null;
}

/**
 * Compact shape rendered in the "Child broker calls" panel of a workflow's
 * Execution drawer. Mirrors `TraceChildOut` from the backend, trimmed to
 * the fields the panel paints. The drawer treats this list as read-only:
 * clicking a row deep-links to `?id=<childId>` and the parent drawer is
 * replaced via the same URL-driven flow as any other row click.
 */
export interface ExecutionChildTrace {
	id: string;
	operationId: string | null;
	status: string | null;
	httpStatus: number | null;
	durationMs: number | null;
	createdAt: string | null;
	apiId: string | null;
	apiName: string | null;
}

export interface ExecutionDetail extends ExecutionLogEntry {
	inputs: Record<string, unknown>;
	outputs?: Record<string, unknown>;
	stepResults?: ExecutionStepResult[];
	/**
	 * Per-step trace rows surfaced from `GET /traces/{id}`. Replaces the
	 * webapp-flavoured `stepResults` for the mini Monitor drawer, which
	 * has access to the raw backend shape.
	 */
	stepRows?: ExecutionStepRow[];
	/** Child broker traces when this row is a workflow execution. */
	childTraces?: ExecutionChildTrace[];
	/** True when the trace endpoint can only return seed/limited fields (no request/response bodies). */
	isSeedOnlyRow?: boolean;
}

export interface MonitorFilters {
	statusFilter: ExecutionStatusFilter;
	toolkitFilter: string | null;
	apiFilter: string | null;
	agentFilter: string | null;
	searchQuery: string;
	range: TimeRange;
}
