/**
 * Usage-window plumbing shared by every `UsageCharts` call site that reads
 * `useUsageStats`: the coarse clock, the derived `since`/`until` window, and
 * the chart legend size.
 *
 * Screens that agree on these values share TanStack Query cache entries for
 * the same window; a screen with its own clock step or top-N would silently
 * split the cache. Consume them through `@/modules/monitor/api`.
 */
import { useEffect, useMemo, useState } from 'react';

/** Chart legend size (top-N rows per grouping) for `UsageCharts` queries. */
export const USAGE_TOP_LIMIT = 12;

/**
 * Window-edge resolution. 5 minutes balances freshness against cache churn:
 * each roll forward is a new query key, while the backend's own usage cache
 * TTL (30s) absorbs repeats within a step.
 */
export const USAGE_WINDOW_STEP_MS = 300_000;

/**
 * Current unix-second time floored to `stepMs`. Ticks forward so a long-lived
 * screen keeps sliding: with a mount-time constant, staleTime/refocus
 * refetches would keep re-fetching the same frozen window forever and
 * executions newer than mount would never appear.
 */
export function useCoarseNowSec(stepMs: number): number {
	const [nowMs, setNowMs] = useState(() => Math.floor(Date.now() / stepMs) * stepMs);
	useEffect(() => {
		const id = setInterval(() => {
			const next = Math.floor(Date.now() / stepMs) * stepMs;
			setNowMs((prev) => (prev === next ? prev : next));
		}, 30_000);
		return () => clearInterval(id);
	}, [stepMs]);
	return nowMs / 1000;
}

/**
 * Unix-second window bounds for a `days` window, given "now" already floored
 * to a `stepSec` boundary.
 *
 * The 24h window rolls with "now" — with `until` at the NEXT step boundary:
 * the backend's aggregate uses a strict `started_at < until`, so an
 * already-elapsed bound would hide the current partial step's executions from
 * the charts while the log lists them (#913). The multi-day windows are
 * aligned to local calendar days — midnight (days-1) days ago through the end
 * of today — to suit the day-bucketed volume chart: a rolling now-7d bound
 * straddles 8 calendar dates.
 */
export function usageWindowBounds(
	nowSec: number,
	days: number,
	stepSec: number,
): { since: number; until: number } {
	if (days === 1) {
		const edge = nowSec + stepSec;
		return { since: edge - 86_400, until: edge };
	}
	const startOfToday = new Date(nowSec * 1000);
	startOfToday.setHours(0, 0, 0, 0);
	const sinceDate = new Date(startOfToday);
	sinceDate.setDate(sinceDate.getDate() - (days - 1));
	const untilDate = new Date(startOfToday);
	untilDate.setDate(untilDate.getDate() + 1);
	return { since: sinceDate.getTime() / 1000, until: untilDate.getTime() / 1000 };
}

/**
 * The `since`/`until` window (unix seconds) for a `days` window, on the
 * {@link USAGE_WINDOW_STEP_MS} coarse clock. Stable across re-renders (a
 * cache-friendly query key) and slides forward on long-lived screens. Send
 * `until` explicitly: the backend picks `bucket_seconds` from the window
 * width, and letting the server default `until` to *its* now would shift the
 * window nondeterministically.
 */
export function useUsageWindow(days: number): { since: number; until: number } {
	const nowSec = useCoarseNowSec(USAGE_WINDOW_STEP_MS);
	return useMemo(
		() => usageWindowBounds(nowSec, days, USAGE_WINDOW_STEP_MS / 1000),
		[nowSec, days],
	);
}
