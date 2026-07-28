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
	/**
	 * Executions with status COMPLETED. Not derivable as `total - failures`:
	 * backend `total` is count(*), so in-flight (pending/running) executions
	 * are in `total` but neither success nor failed.
	 */
	successCount: number;
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
		successCount: success,
		successRate: total > 0 ? (success / total) * 100 : 100,
		failureCount: failed,
		avgLatencyMs: Math.round(usage.stats.avg_ms ?? 0),
		p50Ms: usage.stats.p50_ms != null ? Math.round(usage.stats.p50_ms) : null,
		p95Ms: usage.stats.p95_ms != null ? Math.round(usage.stats.p95_ms) : null,
	};
}

/**
 * Format a top-row key into a display label, per grouping dimension. The
 * backend composes keys/labels mechanically (see monitoring_repo.grouped_top):
 *   api     → "vendor/name" with NULL columns coalesced to "unknown"
 *   toolkit → the raw toolkit_id (NOT NULL column)
 *   agent   → "actor_type/actor_id" (both NOT NULL columns)
 * Keys are therefore never NULL on the wire today; the null branches below
 * are defensive display fallbacks (surfaced as "Unattributed", matching
 * jentic-mini) rather than a backend contract.
 */
function formatEntityLabel(groupBy: string, key: string | null | undefined): string {
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

/**
 * Map the response's `top` rows into entity rows, sorted busiest-first.
 * The attribution columns are NOT NULL so keys are always present today;
 * empty/missing keys are still mapped to an explicit "Unattributed" bucket
 * as a display fallback rather than silently dropped, matching jentic-mini.
 */
export function usageToEntityRows(usage: UsageResponse | undefined): EntityUsageRow[] {
	if (!usage) return [];
	return usage.top
		.map((row) => {
			const total = row.total ?? 0;
			const success = row.success ?? 0;
			const key = (row.key ?? null) as string | null;
			return {
				id: key || '__unattributed__',
				label: formatEntityLabel(usage.group_by, key),
				totalExecutions: total,
				successRate: total > 0 ? (success / total) * 100 : 100,
				avgLatencyMs: Math.round(row.avg_ms ?? 0),
				trend: row.trend ?? [],
			};
		})
		.sort((a, b) => b.totalExecutions - a.totalExecutions);
}
