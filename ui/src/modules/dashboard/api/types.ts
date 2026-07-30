/**
 * Dashboard module — UI-facing types + pure derivations.
 *
 * The Dashboard has NO backend of its own: it composes an overview client-side
 * from four existing list endpoints (agents, events, executions, apis). This
 * file holds the small UI envelopes each composed slice resolves to, plus the
 * pure functions that derive the headline numbers (counts, success rate) from
 * the raw list responses. Keeping the math here (not in components or hooks)
 * makes it unit-testable in isolation and keeps the layers thin.
 */
import type { AgentResponse, EventResponse, ExecutionResponse, UsageResponse } from '@/shared/api';
import type { AccessRequest } from '@/shared/lib';

/**
 * A count that may be a floor rather than an exact total. The list endpoints
 * we compose are cursor-paginated and carry no aggregate `total`, so a single
 * cheap page gives us either the exact count (when the page wasn't full /
 * `has_more` is false) or a lower bound (`atLeast`) we render as "N+".
 */
export interface ApproxCount {
	value: number;
	/** True when more rows exist beyond the page we counted (render "N+"). */
	atLeast: boolean;
}

/** Pending-agents overview slice. */
export interface PendingAgentsOverview {
	count: ApproxCount;
	/** A few representative agents to preview in the card. */
	agents: AgentResponse[];
}

/** Actionable-events overview slice. */
export interface AlertsOverview {
	count: ApproxCount;
	events: EventResponse[];
}

/** Pending access-requests overview slice (the durable approval queue). */
export interface PendingAccessRequestsOverview {
	count: ApproxCount;
	/** A few representative requests to preview in the card. */
	requests: AccessRequest[];
}

/** Recent-executions overview slice, with a derived success rate. */
export interface RecentExecutionsOverview {
	executions: ExecutionResponse[];
	/** Successes / total over the sampled page; null when nothing sampled. */
	successRate: number | null;
	/** Count of executions sampled (the page size we actually saw). */
	sampled: number;
}

/** Catalog-size overview slice (workspace-registered APIs). */
export interface CatalogOverview {
	apiCount: ApproxCount;
}

/**
 * Whether an execution counts as "successful" for the success-rate metric.
 *
 * The execution `status` is `status: string` on the wire contract (the typed
 * `ExecutionStatus` enum is server-side only). We treat an HTTP 2xx, or a
 * status of `completed`/`succeeded`/`success`, as a success, and anything else
 * (failed/error/running/…) as not. This is intentionally lenient so a contract
 * tweak doesn't silently zero the rate.
 */
export function isSuccessfulExecution(execution: ExecutionResponse): boolean {
	const http = execution.http_status;
	if (typeof http === 'number') return http >= 200 && http < 300;
	const status = (execution.status ?? '').toLowerCase();
	return status === 'completed' || status === 'succeeded' || status === 'success';
}

/** Success rate over a page of executions, or null when the page is empty. */
export function deriveSuccessRate(executions: ExecutionResponse[]): number | null {
	if (executions.length === 0) return null;
	const ok = executions.filter(isSuccessfulExecution).length;
	return ok / executions.length;
}

/**
 * Turn a `{ data, has_more }` page into an `ApproxCount`. When `has_more` is
 * true we only know a lower bound, so the UI renders "N+".
 */
export function approxCountFromPage<T>(page: { data: T[]; has_more: boolean }): ApproxCount {
	return { value: page.data.length, atLeast: page.has_more };
}

/** Format an `ApproxCount` for display, e.g. `0`, `3`, or `50+`. */
export function formatApproxCount(count: ApproxCount): string {
	return count.atLeast ? `${count.value}+` : `${count.value}`;
}

/* ------------------------------------------------------------------ */
/* Gateway health — real aggregates from GET /monitoring/usage          */
/* ------------------------------------------------------------------ */

/**
 * The time windows the health/usage layers offer. Values map to `since`
 * lower bounds for `GET /monitoring/usage` (the endpoint defaults `until`
 * to "now floored to the minute").
 */
export type DashboardRange = '24h' | '7d' | '30d';

export const RANGE_SECONDS: Record<DashboardRange, number> = {
	'24h': 86_400,
	'7d': 604_800,
	'30d': 2_592_000,
};

/** One point of a bucketed trend series (unix-second x, metric y). */
export interface UsageTrendPoint {
	ts: number;
	value: number;
}

/** The four headline gateway KPIs for the selected window. */
export interface UsageKpis {
	total: number;
	/** Successes / total over the window (0..1), null when nothing ran. */
	successRate: number | null;
	/** Aggregate p95 latency — a window-level KPI only (buckets carry avg, not p95). */
	p95Ms: number | null;
	activeNow: number;
}

export function usageToKpis(usage: UsageResponse): UsageKpis {
	const total = usage.stats.total ?? 0;
	return {
		total,
		successRate: total > 0 ? (usage.stats.success ?? 0) / total : null,
		p95Ms: usage.stats.p95_ms != null ? Math.round(usage.stats.p95_ms) : null,
		activeNow: usage.stats.active_now ?? 0,
	};
}

/**
 * Per-bucket success rate (0..100 percent) for the trend line. Empty buckets
 * (total 0) carry no rate and are skipped — the endpoint returns sparse
 * buckets anyway, so gaps are already the norm.
 */
export function usageToSuccessRateSeries(usage: UsageResponse): UsageTrendPoint[] {
	return usage.buckets
		.filter((b) => b.total > 0)
		.map((b) => ({ ts: b.ts, value: (b.success / b.total) * 100 }));
}

/**
 * Per-bucket average latency (ms) for the trend line. Buckets carry only
 * `avg_ms` (p95 exists solely as a window aggregate), so this series is
 * explicitly the AVERAGE — label it as such.
 */
export function usageToLatencySeries(usage: UsageResponse): UsageTrendPoint[] {
	return usage.buckets.filter((b) => b.total > 0).map((b) => ({ ts: b.ts, value: b.avg_ms }));
}

/** One top api/toolkit/agent row for the usage-context table. */
export interface TopUsageRow {
	id: string;
	label: string;
	total: number;
	/** Successes / total for the row (0..1), null when the row is empty. */
	successRate: number | null;
	avgMs: number;
	/** 12-point sparkline series (may be empty). */
	trend: number[];
}

/**
 * Format a top-row key into a display label, per grouping dimension. The
 * backend composes keys mechanically (see monitoring_repo.grouped_top):
 * api → "vendor/name" (NULLs coalesced to "unknown"), toolkit → the raw
 * toolkit_id, agent → "actor_type/actor_id" (NULL propagates to a null key).
 * Strip the mechanical prefixes and surface null/unknown groups as an
 * explicit "Unattributed" bucket. (Monitor derives the same labels in its
 * own lib — modules can't share siblings' code, and the rule is small.)
 */
function formatTopRowLabel(groupBy: string, key: string | null | undefined): string {
	if (!key) return 'Unattributed';
	if (groupBy === 'api') {
		const [vendor, ...rest] = key.split('/');
		const name = rest.join('/');
		if (vendor === 'unknown' && (name === 'unknown' || name === '')) return 'Unattributed';
		return name && name !== 'unknown' ? name : key;
	}
	if (groupBy === 'agent') {
		const slash = key.indexOf('/');
		return slash >= 0 ? key.slice(slash + 1) || 'Unattributed' : key;
	}
	return key;
}

/** Map the response's `top` rows into display rows, sorted busiest-first. */
export function usageToTopRows(usage: UsageResponse): TopUsageRow[] {
	return usage.top
		.map((row) => {
			const total = row.total ?? 0;
			// The generated type says `key: string`, but the SQL key expression
			// is nullable on the wire for toolkit/agent groupings.
			const key = (row.key ?? null) as string | null;
			return {
				id: key ?? '__unattributed__',
				label: formatTopRowLabel(usage.group_by, key),
				total,
				successRate: total > 0 ? (row.success ?? 0) / total : null,
				avgMs: Math.round(row.avg_ms ?? 0),
				trend: row.trend ?? [],
			};
		})
		.sort((a, b) => b.total - a.total);
}
