/**
 * useUsageOverview — everything Monitor's Overview draws, from
 * `GET /monitoring/usage` (jentic-one-internal#561), org:admin.
 *
 * One query per grouping dimension (APIs / Agents) so every
 * lens toggle on the charts and the breakdown is instant; the buckets and the
 * overall stats are identical across groupings, so they're read off the
 * API-grouped response.
 *
 * The window and actor come from the page's filter bar (`?days`, `?actor_id`)
 * so the Overview agrees with the activity log beside it. The actor maps onto
 * the endpoint's `agent_id` (the backend filters it against `actor_id`).
 * "All" is a list-only window — the aggregate needs a bounded range — so a
 * carried-over `days=all` reads as 30d here.
 *
 * The aggregate re-polls every {@link AUTO_REFRESH_MS} while the tab is
 * visible; `refresh` forces one now and `updatedAt` says how fresh it is.
 */
import { useEffect, useMemo, useState } from 'react';
import { GroupBy, useUsageStats, type UsageResponse } from '@/modules/monitor/api';
import {
	usageToEntityRows,
	usageToOverview,
	type EntityUsageRow,
	type UsageOverview,
} from '@/modules/monitor/lib/usage';
import { useMonitorFilters } from '@/modules/monitor/lib/useMonitorFilters';

const TOP_LIMIT = 12;

/**
 * Auto-refresh cadence. Matches the backend's usage-cache TTL (30s): polling
 * faster would only re-read the cached aggregate.
 */
export const AUTO_REFRESH_MS = 30_000;

// Window-edge resolution. 5 minutes balances freshness against cache churn:
// each roll forward is a new query key, while the backend's own usage cache
// TTL (30s) absorbs repeats within a step.
const WINDOW_STEP_MS = 300_000;

/**
 * Current unix-second time floored to `stepMs`. Ticks forward so a long-lived
 * Overview keeps sliding: with a mount-time constant, staleTime/refocus
 * refetches would keep re-fetching the same frozen window forever and
 * executions newer than mount would never appear.
 */
function useCoarseNowSec(stepMs: number): number {
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

export interface UsageOverviewState {
	/** The window in days the aggregate covers (1 | 7 | 30). */
	days: number;
	usage: UsageResponse | undefined;
	overview: UsageOverview | null;
	apis: EntityUsageRow[];
	agents: EntityUsageRow[];
	isLoading: boolean;
	isFetching: boolean;
	error: unknown;
	retry: () => void;
	/** Refetch every grouping now; resolves once all have settled. */
	refresh: () => Promise<void>;
	/** When the data on screen was fetched (epoch ms), 0 before the first. */
	updatedAt: number;
}

export function useUsageOverview({ enabled }: { enabled: boolean }): UsageOverviewState {
	const filters = useMonitorFilters();
	const days = filters.days ?? 30;

	// Unix-second window bounds. The 24h window rolls with "now" resolved to
	// 5-minute steps (stable query key across re-renders, still slides forward
	// on long-lived tabs) — with `until` at the NEXT step boundary: the
	// backend's aggregate uses a strict `started_at < until`, so an
	// already-elapsed bound would hide the current partial step's executions
	// from the charts while the log lists them (#913). The multi-day windows
	// are aligned to local calendar days — midnight (days-1) days ago through
	// the end of today — to suit the day-bucketed volume chart: a rolling
	// now-7d bound straddles 8 calendar dates. `until` is sent explicitly: the
	// backend picks `bucket_seconds` from the window width, and letting the
	// server default `until` to *its* now would shift the window
	// nondeterministically.
	const nowSec = useCoarseNowSec(WINDOW_STEP_MS);
	const { since, until } = useMemo(() => {
		if (days === 1) {
			const edge = nowSec + WINDOW_STEP_MS / 1000;
			return { since: edge - 86_400, until: edge };
		}
		const startOfToday = new Date(nowSec * 1000);
		startOfToday.setHours(0, 0, 0, 0);
		const sinceDate = new Date(startOfToday);
		sinceDate.setDate(sinceDate.getDate() - (days - 1));
		const untilDate = new Date(startOfToday);
		untilDate.setDate(untilDate.getDate() + 1);
		return { since: sinceDate.getTime() / 1000, until: untilDate.getTime() / 1000 };
	}, [nowSec, days]);

	const base = { since, until, topLimit: TOP_LIMIT, agentId: filters.actorId };
	const poll = { enabled, refetchInterval: enabled ? AUTO_REFRESH_MS : false } as const;
	const apiUsage = useUsageStats({ ...base, groupBy: GroupBy.API }, poll);
	const agentUsage = useUsageStats({ ...base, groupBy: GroupBy.AGENT }, poll);
	const queries = [apiUsage, agentUsage];

	const usage = apiUsage.data;
	const overview = useMemo(() => (usage ? usageToOverview(usage) : null), [usage]);
	const apis = useMemo(() => usageToEntityRows(apiUsage.data), [apiUsage.data]);
	const agents = useMemo(() => usageToEntityRows(agentUsage.data), [agentUsage.data]);

	return {
		days,
		usage,
		overview,
		apis,
		agents,
		isLoading: queries.some((q) => q.isLoading),
		isFetching: queries.some((q) => q.isFetching),
		error: queries.find((q) => q.isError && !q.data)?.error ?? null,
		retry: () => {
			for (const q of queries) void q.refetch();
		},
		refresh: async () => {
			await Promise.all(queries.map((q) => q.refetch()));
		},
		updatedAt: Math.min(...queries.map((q) => q.dataUpdatedAt)),
	};
}
