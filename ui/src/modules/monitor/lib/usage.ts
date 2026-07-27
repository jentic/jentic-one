/**
 * Usage-aggregation transformers — map `GET /monitoring/usage` responses
 * (`UsageResponse`) into the UI-shaped rows the Overview charts render.
 * Mirrors jentic-mini's `lib/monitor-transformers.ts` (usageToMonitorStats /
 * usageToTopRows / usageToAgentRows), collapsed into one entity-row shape
 * since the jentic-one endpoint returns the same `{key,label,total,success,
 * failed,avg_ms,trend}` rows for every grouping dimension.
 */
import type { UsageResponse } from '@/modules/monitor/api';

/** Overall window stats, UI vocabulary (rates in 0–100 percent). */
export interface UsageOverview {
	totalExecutions: number;
	successRate: number;
	failureCount: number;
	avgLatencyMs: number;
	p50Ms: number | null;
	p95Ms: number | null;
}

/** One api / toolkit / agent row for the bubble chart + breakdown table. */
export interface EntityUsageRow {
	id: string;
	label: string;
	totalExecutions: number;
	successRate: number;
	avgLatencyMs: number;
	trend: number[];
}

export function usageToOverview(usage: UsageResponse): UsageOverview {
	const total = usage.stats.total ?? 0;
	const success = usage.stats.success ?? 0;
	const failed = usage.stats.failed ?? 0;
	return {
		totalExecutions: total,
		successRate: total > 0 ? (success / total) * 100 : 100,
		failureCount: failed,
		avgLatencyMs: Math.round(usage.stats.avg_ms ?? 0),
		p50Ms: usage.stats.p50_ms != null ? Math.round(usage.stats.p50_ms) : null,
		p95Ms: usage.stats.p95_ms != null ? Math.round(usage.stats.p95_ms) : null,
	};
}

/**
 * Map the response's `top` rows into entity rows, sorted busiest-first.
 * Rows with an empty `key` are legacy records with no attribution (e.g.
 * executions that predate actor stamping) — surfaced as an explicit
 * "Unattributed" bucket rather than silently dropped, matching jentic-mini.
 */
export function usageToEntityRows(usage: UsageResponse | undefined): EntityUsageRow[] {
	if (!usage) return [];
	return usage.top
		.map((row) => {
			const total = row.total ?? 0;
			const success = row.success ?? 0;
			return {
				id: row.key || '__unattributed__',
				label: row.label || row.key || 'Unattributed',
				totalExecutions: total,
				successRate: total > 0 ? (success / total) * 100 : 100,
				avgLatencyMs: Math.round(row.avg_ms ?? 0),
				trend: row.trend ?? [],
			};
		})
		.sort((a, b) => b.totalExecutions - a.totalExecutions);
}
