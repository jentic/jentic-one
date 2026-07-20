/**
 * Presentational formatters for the LLM Proxy · Sessions surface.
 * (Module-local by convention — there is no shared date/duration formatter.)
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

/** Relative "time ago", e.g. "12s ago", "3m ago". */
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

/** USD amount → "$0.28" / "$1.2k" style, always prefixed "est." by callers. */
export function formatCost(usd: number | null | undefined): string {
	if (usd == null) return '—';
	if (usd < 0.01) return `$${usd.toFixed(4)}`;
	if (usd < 1000) return `$${usd.toFixed(2)}`;
	return `$${(usd / 1000).toFixed(1)}k`;
}

/** Token count → "1,290" / "52.3k" / "1.2M". */
export function formatTokens(n: number | null | undefined): string {
	if (n == null) return '—';
	if (n < 1000) return `${n}`;
	if (n < 1_000_000) return `${(n / 1000).toFixed(1)}k`;
	return `${(n / 1_000_000).toFixed(1)}M`;
}

/** Compact number with thousands separators. */
export function formatCount(n: number | null | undefined): string {
	if (n == null) return '—';
	return n.toLocaleString();
}
