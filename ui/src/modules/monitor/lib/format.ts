/**
 * Small presentational formatters shared across Monitor tabs.
 */

/** Absolute timestamp → compact local datetime, e.g. "Jun 19, 10:05:00". */
export function formatTimestamp(iso: string | null | undefined): string {
	if (!iso) return '—';
	const date = new Date(iso);
	if (Number.isNaN(date.getTime())) return iso;
	return date.toLocaleString(undefined, {
		month: 'short',
		day: 'numeric',
		hour: '2-digit',
		minute: '2-digit',
		second: '2-digit',
	});
}

/** Relative "time ago" for live/recent rows, e.g. "12s ago", "3m ago". */
export function formatRelative(iso: string | null | undefined): string {
	if (!iso) return '—';
	const then = new Date(iso).getTime();
	if (Number.isNaN(then)) return iso;
	const seconds = Math.max(0, Math.round((Date.now() - then) / 1000));
	if (seconds < 60) return `${seconds}s ago`;
	const minutes = Math.round(seconds / 60);
	if (minutes < 60) return `${minutes}m ago`;
	const hours = Math.round(minutes / 60);
	if (hours < 24) return `${hours}h ago`;
	return `${Math.round(hours / 24)}d ago`;
}

/** Duration in ms → human string, e.g. "842ms", "1.2s". */
export function formatDuration(ms: number | null | undefined): string {
	if (ms == null) return '—';
	if (ms < 1000) return `${ms}ms`;
	return `${(ms / 1000).toFixed(1)}s`;
}

/** Compact average-latency display for stat tiles, e.g. "412ms". */
export function formatLatency(ms: number | null | undefined): string {
	if (ms == null || !Number.isFinite(ms)) return '—';
	return `${Math.round(ms)}ms`;
}

/** Percentage with at most one decimal, trimming ".0", e.g. "91.3%", "100%". */
export function formatPercent(value: number | null | undefined): string {
	if (value == null || !Number.isFinite(value)) return '—';
	if (Number.isInteger(value)) return `${value}%`;
	const rounded = value.toFixed(1);
	return `${rounded.endsWith('.0') ? rounded.slice(0, -2) : rounded}%`;
}
