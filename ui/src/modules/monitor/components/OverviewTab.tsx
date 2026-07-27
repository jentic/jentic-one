/**
 * Overview tab — the headline usage lens, ported from jentic-mini's Overview
 * (jentic-one-internal#561).
 *
 * Powered by `GET /monitoring/usage` (`useUsageStats`): HealthStrip (health +
 * latency pills, active-APIs cluster), the Execution Volume chart (sub-day
 * buckets on the 24h window), the bubble chart, and the Breakdown table with
 * per-row sparkline trends — each of the latter two with an APIs / Toolkits /
 * Agents grouping toggle. The tab fires one usage query per grouping
 * dimension (same pattern as jentic-mini's MonitorPage) so toggling lenses is
 * instant; buckets/overall stats are read off the API-grouped response since
 * they're identical across groupings.
 *
 * Intentional divergences from jentic-mini:
 * - Window options are 24h/7d/30d only. Mini also offered `1h`/`all`, but the
 *   `?days` URL param is shared with the list tabs' filter bar, which speaks
 *   integer days — sub-day and unbounded windows would fork that contract.
 * - All-zero data swaps the page for an EmptyState with guidance. Mini
 *   rendered a (misleading) 100%-healthy strip over empty charts.
 */
import { useEffect, useMemo, useState } from 'react';
import { motion } from 'framer-motion';
import { BarChart3 } from 'lucide-react';
import { useSearchParams } from 'react-router-dom';
import { EmptyState, ErrorAlert, LoadingState, SegmentedToggle } from '@/shared/ui';
import { GroupBy, useUsageStats } from '@/modules/monitor/api';
import { usageToEntityRows, usageToOverview } from '@/modules/monitor/lib/usage';
import { HealthStrip } from '@/modules/monitor/components/HealthStrip';
import { UsageCharts } from '@/modules/monitor/components/UsageCharts';
import { UsageBubbleChart } from '@/modules/monitor/components/UsageBubbleChart';
import { UsageBreakdown } from '@/modules/monitor/components/UsageBreakdown';

const WINDOW_OPTIONS = [
	{ value: '1', label: '24h' },
	{ value: '7', label: '7d' },
	{ value: '30', label: '30d' },
];

const TOP_LIMIT = 12;

// Window-edge resolution. 5 minutes balances freshness against cache churn:
// each roll forward is a new query key (a full refetch of all three
// groupings), while the backend's own usage cache TTL is 120s anyway.
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

// Mini's staggered chart entrance: children fade/rise in sequence, re-keyed
// on the window so changing ranges replays the entrance.
const staggerContainer = {
	hidden: {},
	show: { transition: { staggerChildren: 0.08 } },
};
const chartVariant = {
	hidden: { opacity: 0, y: 12 },
	show: { opacity: 1, y: 0, transition: { type: 'spring', stiffness: 260, damping: 24 } },
} as const;

function parseDays(value: string | null): number {
	const n = Number(value);
	return n === 1 || n === 7 || n === 30 ? n : 7;
}

export function OverviewTab() {
	// Share the `?days` URL param with the global filter bar so the window stays
	// consistent when switching between Overview and the list tabs.
	const [searchParams, setSearchParams] = useSearchParams();
	const days = parseDays(searchParams.get('days'));
	const setDays = (value: number) =>
		setSearchParams(
			(prev) => {
				const next = new URLSearchParams(prev);
				next.set('days', String(value));
				return next;
			},
			{ replace: true },
		);

	// Unix-second window bounds, floored to 5-minute steps so the query key
	// stays stable across re-renders (no cache-busting every tick) yet still
	// slides forward on long-lived tabs. `until` is sent explicitly: the
	// backend picks `bucket_seconds` from the window width (until - since),
	// and letting the server default `until` to *its* now makes a 7d window
	// nondeterministically overflow 604800s and flip between 6h and daily
	// buckets.
	const nowSec = useCoarseNowSec(WINDOW_STEP_MS);
	const { since, until } = useMemo(
		() => ({ since: nowSec - days * 86_400, until: nowSec }),
		[nowSec, days],
	);

	const apiUsage = useUsageStats({ since, until, groupBy: GroupBy.API, topLimit: TOP_LIMIT });
	const toolkitUsage = useUsageStats({
		since,
		until,
		groupBy: GroupBy.TOOLKIT,
		topLimit: TOP_LIMIT,
	});
	const agentUsage = useUsageStats({
		since,
		until,
		groupBy: GroupBy.AGENT,
		topLimit: TOP_LIMIT,
	});

	const isLoading = apiUsage.isLoading || toolkitUsage.isLoading || agentUsage.isLoading;
	const firstError = [apiUsage, toolkitUsage, agentUsage].find((q) => q.isError);

	const data = apiUsage.data;
	// Memoized on the query data: fresh array identities every render would
	// re-run UsageBubbleChart's O(n·10⁵) circle packer on each isFetching
	// toggle or unrelated parent re-render.
	const overview = useMemo(() => (data ? usageToOverview(data) : null), [data]);
	const apis = useMemo(() => usageToEntityRows(apiUsage.data), [apiUsage.data]);
	const toolkits = useMemo(() => usageToEntityRows(toolkitUsage.data), [toolkitUsage.data]);
	const agents = useMemo(() => usageToEntityRows(agentUsage.data), [agentUsage.data]);
	const isEmpty = !!overview && overview.totalExecutions === 0;

	const retryAll = () => {
		void apiUsage.refetch();
		void toolkitUsage.refetch();
		void agentUsage.refetch();
	};

	return (
		<div className="space-y-4">
			<div className="flex items-center justify-between gap-3">
				<h2 className="text-foreground text-sm font-semibold">Usage</h2>
				<SegmentedToggle
					options={WINDOW_OPTIONS}
					value={String(days)}
					onChange={(v) => setDays(Number(v))}
					ariaLabel="Stats window"
				/>
			</div>

			{isLoading ? (
				<LoadingState />
			) : firstError ? (
				<ErrorAlert
					message={
						firstError.error instanceof Error
							? firstError.error
							: 'Failed to load usage statistics.'
					}
					onRetry={retryAll}
					retrying={
						apiUsage.isFetching || toolkitUsage.isFetching || agentUsage.isFetching
					}
				/>
			) : isEmpty || !data || !overview ? (
				<EmptyState
					icon={<BarChart3 className="h-8 w-8" />}
					title="No executions yet"
					description={`No API calls were recorded in the last ${days === 1 ? '24 hours' : `${days} days`}. Once agents start running operations, usage trends and per-API activity will appear here.`}
				/>
			) : (
				<motion.div
					key={`content-${days}`}
					variants={staggerContainer}
					initial="hidden"
					animate="show"
					className="space-y-4"
				>
					<motion.div variants={chartVariant}>
						<HealthStrip overview={overview} apis={apis} />
					</motion.div>
					<motion.div variants={chartVariant}>
						<UsageCharts usage={data} />
					</motion.div>
					<motion.div variants={chartVariant}>
						<UsageBubbleChart apis={apis} toolkits={toolkits} agents={agents} />
					</motion.div>
					<motion.div variants={chartVariant}>
						<UsageBreakdown apis={apis} toolkits={toolkits} agents={agents} />
					</motion.div>
				</motion.div>
			)}
		</div>
	);
}
