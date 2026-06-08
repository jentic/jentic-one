/**
 * Shared numeric formatters for the monitor page.
 *
 * Two helpers:
 *   - formatDuration: human-readable duration for a single execution
 *     (e.g. row durations, detail sheet). Uses ms or s with a sensible
 *     unit crossover.
 *   - formatLatency: compact average-latency display for stat tiles
 *     and cards, always in ms with no decimals.
 *
 * Both round aggressively because sub-millisecond precision is noise
 * in these contexts — backend aggregates can return values like
 * 347.8523 and rendering that fractional tail is distracting.
 */

export function formatDuration(ms: number | null | undefined): string {
	if (ms == null || !Number.isFinite(ms)) return '—';
	if (ms < 1000) return `${Math.round(ms)}ms`;
	if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
	const totalSeconds = Math.round(ms / 1000);
	const minutes = Math.floor(totalSeconds / 60);
	const seconds = totalSeconds % 60;
	return `${minutes}m ${seconds}s`;
}

export function formatLatency(ms: number | null | undefined): string {
	if (ms == null || !Number.isFinite(ms)) return '—';
	return `${Math.round(ms)}ms`;
}

export function formatPercent(value: number | null | undefined): string {
	if (value == null || !Number.isFinite(value)) return '—';
	if (Number.isInteger(value)) return `${value}%`;
	const rounded = value.toFixed(1);
	return `${rounded.endsWith('.0') ? rounded.slice(0, -2) : rounded}%`;
}

/**
 * Compact relative-time string ("42s ago", "3m ago", "2h ago", "5d ago").
 *
 * `dateString` may be either an ISO date string or a unix epoch (seconds or
 * milliseconds) — the mini backend mostly returns numeric `created_at` while
 * the webapp returns ISO strings, so we accept either. Clock skew between the
 * device and the backend can make `createdAt` land in the future; we floor at
 * 0 so we never render "-3s ago".
 */
export function formatRelativeTime(input: string | number | null | undefined): string {
	if (input == null) return '—';
	let t: number;
	if (typeof input === 'number') {
		t = input < 1e12 ? input * 1000 : input;
	} else {
		const parsed = Number(input);
		if (Number.isFinite(parsed) && /^\d+(\.\d+)?$/.test(input.trim())) {
			t = parsed < 1e12 ? parsed * 1000 : parsed;
		} else {
			t = new Date(input).getTime();
		}
	}
	if (!Number.isFinite(t)) return '—';
	const diffMs = Date.now() - t;
	const diffSecs = Math.max(0, Math.floor(diffMs / 1000));
	const diffMins = Math.floor(diffSecs / 60);
	const diffHours = Math.floor(diffMins / 60);
	const diffDays = Math.floor(diffHours / 24);

	if (diffSecs < 60) return `${diffSecs}s ago`;
	if (diffMins < 60) return `${diffMins}m ago`;
	if (diffHours < 24) return `${diffHours}h ago`;
	return `${diffDays}d ago`;
}
