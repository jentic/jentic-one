/**
 * Dashboard view helpers — small, pure formatting utilities used by the
 * overview components. Relative-time formatting lives in the shared
 * `@/shared/lib/utils` `timeAgo` (promoted by ui-agents under COLLABORATION §4);
 * this file keeps only the dashboard-specific bits.
 */

/** Render a 0..1 ratio as a whole-percent string, or "—" when null. */
export function formatPercent(ratio: number | null): string {
	if (ratio == null) return '—';
	return `${Math.round(ratio * 100)}%`;
}

/** Render a millisecond latency compactly ("412ms", "1.2s"), or "—" when null. */
export function formatLatency(ms: number | null): string {
	if (ms == null) return '—';
	if (ms >= 1000) return `${(ms / 1000).toFixed(1)}s`;
	return `${Math.round(ms)}ms`;
}

/** Render a count compactly ("980", "12.4k"). */
export function formatCount(value: number): string {
	if (value >= 10_000) return `${(value / 1000).toFixed(1).replace(/\.0$/, '')}k`;
	return value.toLocaleString();
}

/**
 * Label a usage bucket timestamp (unix seconds) for chart axes: clock time
 * for sub-day buckets ("14:00"), month + day otherwise ("Jun 13"). Bucket
 * width comes from the response's `bucket_seconds`, so the same chart adapts
 * as the range toggle moves between hourly and daily bucketing.
 */
export function formatBucketLabel(ts: number, bucketSeconds: number): string {
	const date = new Date(ts * 1000);
	if (bucketSeconds < 86_400) {
		return date.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
	}
	return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}
