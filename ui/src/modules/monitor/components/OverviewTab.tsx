/**
 * Overview tab — the headline usage lens, ported from jentic-mini's Overview
 * at full parity (jentic-one-internal#561).
 *
 * Powered by `GET /monitoring/usage` (`useUsageStats`): HealthStrip (health +
 * latency pills, active-APIs cluster), the Execution Volume chart (sub-day
 * buckets on the 24h window), the bubble chart, and the Breakdown table with
 * per-row sparkline trends — each of the latter two with an APIs / Toolkits /
 * Agents grouping toggle. The tab fires one usage query per grouping
 * dimension (same pattern as jentic-mini's MonitorPage) so toggling lenses is
 * instant; buckets/overall stats are read off the API-grouped response since
 * they're identical across groupings.
 */
import { useMemo } from 'react';
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

	// Unix-second window bounds. Rounded to the minute so the query key stays
	// stable across re-renders instead of busting the cache every tick. `until`
	// is sent explicitly: the backend picks `bucket_seconds` from the window
	// width (until - since), and letting the server default `until` to *its*
	// now makes a 7d window nondeterministically overflow 604800s and flip
	// between 6h and daily buckets.
	const { since, until } = useMemo(() => {
		const nowSec = Math.floor(Date.now() / 60_000) * 60;
		return { since: nowSec - days * 86_400, until: nowSec };
	}, [days]);

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
	const overview = data ? usageToOverview(data) : null;
	const apis = usageToEntityRows(apiUsage.data);
	const toolkits = usageToEntityRows(toolkitUsage.data);
	const agents = usageToEntityRows(agentUsage.data);
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
				<>
					<HealthStrip overview={overview} apis={apis} />
					<UsageCharts usage={data} />
					<UsageBubbleChart apis={apis} toolkits={toolkits} agents={agents} />
					<UsageBreakdown apis={apis} toolkits={toolkits} agents={agents} />
				</>
			)}
		</div>
	);
}
