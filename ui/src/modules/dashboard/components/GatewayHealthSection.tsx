/**
 * GatewayHealthSection — layers 2 + 3 of the rebuilt dashboard: real KPIs,
 * trend charts, and the top-usage context table, all fed by ONE
 * `GET /monitoring/usage` query per (range, lens) pair.
 *
 * This replaces the old approximate overview tiles ("success rate of the
 * last 25", "50+ APIs") with the backend's real aggregates. The section is
 * org:admin-gated exactly like the endpoint — for non-admins it renders
 * nothing (the action queues and recent activity above/below still work),
 * matching how the backend would 403 the call anyway.
 *
 * State model: `range` (24h/7d/30d) re-scopes EVERYTHING in the section;
 * `lens` (APIs/Toolkits/Agents) re-scopes only the top-rows table but rides
 * the same query — the stats/buckets it returns are lens-independent, so the
 * KPIs and charts stay stable when the lens flips.
 */
import { useId, useState } from 'react';
import { Activity, CheckCircle2, Gauge, HeartPulse, Loader2 } from 'lucide-react';
import {
	Card,
	CardHeader,
	CardBody,
	CardTitle,
	DataTable,
	ErrorAlert,
	SegmentedToggle,
	Skeleton,
	SparklineChart,
	StackedBarChart,
	TrendLineChart,
	type Column,
	type StackedBarDatum,
} from '@/shared/ui';
import {
	useUsageOverview,
	usageToKpis,
	usageToSuccessRateSeries,
	usageToTopRows,
	GroupBy,
	type DashboardRange,
	type TopUsageRow,
	type UsageResponse,
} from '@/modules/dashboard/api';
import { StatCard } from '@/shared/ui';
import { SectionHeading } from '@/modules/dashboard/components/CardRow';
import { usePermission, ORG_ADMIN } from '@/shared/auth';
import {
	formatBucketLabel,
	formatCount,
	formatLatency,
	formatPercent,
} from '@/modules/dashboard/components/format';

const RANGE_OPTIONS: { value: DashboardRange; label: string }[] = [
	{ value: '24h', label: '24h' },
	{ value: '7d', label: '7d' },
	{ value: '30d', label: '30d' },
];

const LENS_OPTIONS: { value: GroupBy; label: string }[] = [
	{ value: GroupBy.API, label: 'APIs' },
	{ value: GroupBy.TOOLKIT, label: 'Toolkits' },
	{ value: GroupBy.AGENT, label: 'Agents' },
];

const RANGE_CAPTION: Record<DashboardRange, string> = {
	'24h': 'last 24 hours',
	'7d': 'last 7 days',
	'30d': 'last 30 days',
};

const LENS_CAPTION: Record<GroupBy, string> = {
	[GroupBy.API]: 'Busiest APIs',
	[GroupBy.TOOLKIT]: 'Busiest toolkits',
	[GroupBy.AGENT]: 'Busiest agents',
};

function usageToVolumeBars(usage: UsageResponse): StackedBarDatum[] {
	return usage.buckets.map((bucket) => ({
		key: String(bucket.ts),
		label: formatBucketLabel(bucket.ts, usage.bucket_seconds),
		segments: [
			{
				key: 'success',
				label: 'succeeded',
				value: bucket.success,
				colorClassName: 'bg-accent-green/80',
			},
			{
				key: 'failed',
				label: 'failed',
				value: bucket.failed,
				colorClassName: 'bg-danger/80',
			},
		],
	}));
}

/** A top-usage row with its 1-based position in the ranking. */
type RankedUsageRow = TopUsageRow & { rank: number };

/**
 * Columns for the top-usage ranking. Built per render because the Calls cell
 * draws a share bar scaled against the busiest row (`maxTotal`) — turning what
 * used to be dead space between sparse numbers into a readable "who dominates
 * the traffic" comparison. All numeric columns are right-aligned so the digits
 * line up into scannable rails.
 */
function topRowColumns(maxTotal: number): Column<RankedUsageRow>[] {
	return [
		{
			key: 'rank',
			header: '#',
			className: 'w-10 pr-0 text-right',
			render: (row) => (
				<span className="text-muted-foreground font-mono text-xs tabular-nums">
					{row.rank}
				</span>
			),
		},
		{
			key: 'label',
			header: 'Name',
			className: 'max-w-[220px] truncate font-medium',
			render: (row) => row.label,
		},
		{
			key: 'trend',
			header: 'Trend',
			className: 'w-28',
			render: (row) => <SparklineChart data={row.trend} className="text-primary" />,
		},
		{
			key: 'total',
			header: 'Calls',
			className: 'w-[32%] min-w-44',
			render: (row) => (
				<span className="flex items-center gap-3">
					<span
						className="bg-muted h-1.5 min-w-10 flex-1 overflow-hidden rounded-full"
						aria-hidden="true"
					>
						<span
							className="bg-primary/70 block h-full rounded-full"
							style={{ width: `${Math.max(4, (row.total / maxTotal) * 100)}%` }}
						/>
					</span>
					<span className="w-12 shrink-0 text-right font-mono text-xs tabular-nums">
						{formatCount(row.total)}
					</span>
				</span>
			),
		},
		{
			key: 'successRate',
			header: 'Success',
			className: 'w-28 text-right tabular-nums',
			render: (row) => (
				<span className="inline-flex items-center gap-1.5">
					<span
						aria-hidden="true"
						className={
							row.successRate == null
								? 'bg-muted-foreground/40 h-1.5 w-1.5 rounded-full'
								: row.successRate >= 0.99
									? 'bg-accent-green h-1.5 w-1.5 rounded-full'
									: row.successRate >= 0.9
										? 'bg-accent-orange h-1.5 w-1.5 rounded-full'
										: 'bg-danger h-1.5 w-1.5 rounded-full'
						}
					/>
					{formatPercent(row.successRate)}
				</span>
			),
		},
		{
			key: 'avgMs',
			header: 'Avg speed',
			className: 'text-muted-foreground w-28 text-right tabular-nums',
			render: (row) => formatLatency(row.avgMs),
		},
	];
}

export function GatewayHealthSection() {
	const [range, setRange] = useState<DashboardRange>('24h');
	const [lens, setLens] = useState<GroupBy>(GroupBy.API);
	// The endpoint 403s for non-admins, so gate the query here in the view
	// (the api tier can't import `@/shared/auth` — see useUsageOverview's doc).
	const isAdmin = usePermission(ORG_ADMIN);
	const { data, isLoading, isError, error, isFetching } = useUsageOverview(range, lens, {
		enabled: isAdmin,
	});
	const topTableId = useId();

	// Endpoint is org:admin-only — hide the whole section rather than showing
	// a permanently degraded card to users who can never load it.
	if (!isAdmin) return null;

	const kpis = data ? usageToKpis(data) : null;
	const caption = RANGE_CAPTION[range];
	const topRows: RankedUsageRow[] = data
		? usageToTopRows(data).map((row, i) => ({ ...row, rank: i + 1 }))
		: [];
	const maxTotal = Math.max(1, ...topRows.map((row) => row.total));

	return (
		<section aria-label="Gateway health" className="flex flex-col gap-4">
			<SectionHeading
				icon={<HeartPulse className="h-4 w-4" aria-hidden="true" />}
				trailing={
					<SegmentedToggle
						options={RANGE_OPTIONS}
						value={range}
						onChange={setRange}
						ariaLabel="Time range"
					/>
				}
			>
				Gateway health
				{isFetching && !isLoading && (
					<Loader2
						className="text-muted-foreground h-3.5 w-3.5 animate-spin"
						aria-hidden="true"
					/>
				)}
			</SectionHeading>

			{isError ? (
				<ErrorAlert message={error?.message ?? 'Failed to load usage statistics.'} />
			) : (
				<>
					<div className="grid grid-cols-2 gap-3 sm:gap-4 lg:grid-cols-4">
						<StatCard
							label="Executions"
							icon={<Activity className="h-4 w-4 shrink-0" aria-hidden="true" />}
							accent="blue"
							isLoading={isLoading}
							value={kpis ? formatCount(kpis.total) : '—'}
							caption={caption}
						/>
						<StatCard
							label="Success rate"
							icon={<CheckCircle2 className="h-4 w-4 shrink-0" aria-hidden="true" />}
							accent="green"
							isLoading={isLoading}
							value={kpis ? formatPercent(kpis.successRate) : '—'}
							caption={
								kpis && kpis.total > 0 ? `of ${formatCount(kpis.total)}` : 'no runs'
							}
						/>
						<StatCard
							label="p95 latency"
							icon={<Gauge className="h-4 w-4 shrink-0" aria-hidden="true" />}
							accent="orange"
							isLoading={isLoading}
							value={kpis ? formatLatency(kpis.p95Ms) : '—'}
							caption={caption}
						/>
						<StatCard
							label="Active now"
							icon={<HeartPulse className="h-4 w-4 shrink-0" aria-hidden="true" />}
							accent="primary"
							isLoading={isLoading}
							value={kpis ? formatCount(kpis.activeNow) : '—'}
							caption="running executions"
						/>
					</div>

					<div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
						<Card className="lg:col-span-2">
							<CardHeader className="flex flex-wrap items-start justify-between gap-3">
								<div>
									<CardTitle as="h3">Execution volume</CardTitle>
									<p className="text-muted-foreground mt-0.5 text-xs">
										Succeeded vs failed per bucket · {caption}
									</p>
								</div>
								<div className="text-muted-foreground flex items-center gap-4 pt-0.5 text-xs">
									<span className="inline-flex items-center gap-1.5">
										<span
											className="bg-accent-green/80 h-2 w-2 rounded-sm"
											aria-hidden="true"
										/>
										Succeeded
									</span>
									<span className="inline-flex items-center gap-1.5">
										<span
											className="bg-danger/80 h-2 w-2 rounded-sm"
											aria-hidden="true"
										/>
										Failed
									</span>
								</div>
							</CardHeader>
							<CardBody>
								{isLoading || !data ? (
									<Skeleton className="h-48 w-full" />
								) : (
									<StackedBarChart
										bars={usageToVolumeBars(data)}
										height={180}
										formatValue={formatCount}
										ariaLabel={`Execution volume over the ${caption}, split into succeeded and failed`}
									/>
								)}
							</CardBody>
						</Card>
						<Card>
							<CardHeader>
								<CardTitle as="h3">Success rate trend</CardTitle>
								<p className="text-muted-foreground mt-0.5 text-xs">
									Share of runs succeeding · {caption}
								</p>
							</CardHeader>
							<CardBody>
								{isLoading || !data ? (
									<Skeleton className="h-48 w-full" />
								) : (
									<TrendLineChart
										data={usageToSuccessRateSeries(data)}
										yDomain={[0, 100]}
										height={180}
										formatValue={(v) => `${Math.round(v)}%`}
										formatTs={(ts) =>
											formatBucketLabel(ts, data.bucket_seconds)
										}
										colorClassName="text-accent-green"
										ariaLabel={`Success rate per bucket over the ${caption}`}
									/>
								)}
							</CardBody>
						</Card>
					</div>

					<Card>
						<CardHeader className="flex flex-wrap items-start justify-between gap-3">
							<div>
								<CardTitle as="h3">Top usage</CardTitle>
								<p className="text-muted-foreground mt-0.5 text-xs">
									{LENS_CAPTION[lens]} by call volume · {caption}
								</p>
							</div>
							<SegmentedToggle
								options={LENS_OPTIONS}
								value={lens}
								onChange={setLens}
								as="tabs"
								ariaLabel="Top usage grouping"
								getControls={() => topTableId}
							/>
						</CardHeader>
						<CardBody className="px-0 py-0">
							<div id={topTableId} role="tabpanel" aria-label="Top usage table">
								{isLoading || !data ? (
									<div className="px-5 py-4">
										<Skeleton className="h-24 w-full" />
									</div>
								) : (
									<DataTable
										columns={topRowColumns(maxTotal)}
										data={topRows}
										getRowKey={(row) => row.id}
										ariaLabel="Top usage"
										emptyMessage="No executions in this window yet."
									/>
								)}
							</div>
						</CardBody>
					</Card>
				</>
			)}
		</section>
	);
}
