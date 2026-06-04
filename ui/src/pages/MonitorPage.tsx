import { type JSX, useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { motion } from 'framer-motion';
import { api } from '@/api/client';
import { PageShell } from '@/components/layout/PageShell';
import { PageHeader } from '@/components/ui/PageHeader';
import { SegmentedToggle } from '@/components/ui/SegmentedToggle';
import { HoverTooltip } from '@/components/ui/HoverTooltip';
import { RefreshButton } from '@/components/ui/RefreshButton';
import { Button } from '@/components/ui/Button';
import { OverviewTab } from '@/components/monitor/overview/OverviewTab';
import { ExecutionLogTab } from '@/components/monitor/execution-log/ExecutionLogTab';
import { ExecutionDetailSheet } from '@/components/monitor/execution-log/ExecutionDetailSheet';
import { JobsTab } from '@/components/monitor/jobs/JobsTab';
import type {
	ExecutionLogEntry,
	ExecutionDetail,
	ExecutionStatusFilter,
	JobKindFilter,
	JobStatusFilter,
	MonitorTab,
	TimeRange,
} from '@/components/monitor/types';
import type { JobOut, TraceOut, UsageResponse } from '@/api/types';
import {
	jobToLogEntry,
	mergeInFlightWithHistory,
	statusFilterToBackend,
	traceToLogEntry,
	tracesToTimelinePoints,
	usageToAgentRows,
	usageToMonitorStats,
	usageToTopRows,
} from '@/lib/monitor-transformers';
import { useToolkitOptions, useAgentOptions } from '@/components/monitor/hooks/usePickerOptions';

const PAGE_SIZE = 20;

const TAB_OPTIONS: Array<{ value: MonitorTab; label: string }> = [
	{ value: 'overview', label: 'Overview' },
	{ value: 'log', label: 'Execution Log' },
	{ value: 'jobs', label: 'Jobs' },
];

const TIME_RANGE_OPTIONS: Array<{ value: TimeRange; label: string }> = [
	{ value: '1h', label: '1h' },
	{ value: '24h', label: '24h' },
	{ value: '7d', label: '7d' },
	{ value: '30d', label: '30d' },
];

function timeRangeToSeconds(range: TimeRange): number {
	switch (range) {
		case '1h':
			return 60 * 60;
		case '7d':
			return 7 * 24 * 60 * 60;
		case '30d':
			return 30 * 24 * 60 * 60;
		case '24h':
		default:
			return 24 * 60 * 60;
	}
}

function readSearchParam(params: URLSearchParams, key: string): string | null {
	const v = params.get(key);
	return v ? v : null;
}

function readTab(params: URLSearchParams): MonitorTab {
	const v = params.get('tab');
	if (v === 'log') return 'log';
	if (v === 'jobs') return 'jobs';
	return 'overview';
}

function readJobStatus(params: URLSearchParams): JobStatusFilter {
	const v = params.get('jobStatus');
	if (
		v === 'inflight' ||
		v === 'pending' ||
		v === 'running' ||
		v === 'complete' ||
		v === 'failed' ||
		v === 'upstream_async'
	) {
		return v;
	}
	return 'all';
}

function readJobKind(params: URLSearchParams): JobKindFilter {
	const v = params.get('jobKind');
	if (v === 'workflow' || v === 'broker') return v;
	return 'all';
}

function readStatus(params: URLSearchParams): ExecutionStatusFilter {
	const v = params.get('status');
	if (v === 'RUNNING' || v === 'COMPLETED' || v === 'FAILED') return v;
	return 'ALL';
}

function readRange(params: URLSearchParams): TimeRange {
	const v = params.get('range');
	if (v === '1h' || v === '24h' || v === '7d' || v === '30d') return v;
	return '24h';
}

export default function MonitorPage(): JSX.Element {
	const [searchParams, setSearchParams] = useSearchParams();

	const tab = readTab(searchParams);
	const range = readRange(searchParams);
	const statusFilter = readStatus(searchParams);
	const toolkitFilter = readSearchParam(searchParams, 'toolkit');
	const apiFilter = readSearchParam(searchParams, 'api');
	const agentFilter = readSearchParam(searchParams, 'agent');
	const jobStatusFilter = readJobStatus(searchParams);
	const jobKindFilter = readJobKind(searchParams);
	const page = parseInt(searchParams.get('page') ?? '1', 10) || 1;

	const [selectedExecution, setSelectedExecution] = useState<ExecutionDetail | null>(null);
	const [isSheetOpen, setIsSheetOpen] = useState(false);

	const since = useMemo(() => {
		const now = Math.floor(Date.now() / 1000);
		return now - timeRangeToSeconds(range);
	}, [range]);

	const toolkitOptionsQuery = useToolkitOptions();
	const agentOptionsQuery = useAgentOptions(true);
	const toolkitNameById = useMemo(() => {
		const m = new Map<string, string>();
		for (const opt of toolkitOptionsQuery.options) m.set(opt.value, opt.label);
		return m;
	}, [toolkitOptionsQuery.options]);
	const agentNameById = useMemo(() => {
		const m = new Map<string, string>();
		for (const opt of agentOptionsQuery.options) m.set(opt.value, opt.label);
		return m;
	}, [agentOptionsQuery.options]);

	const usageStatsQuery = useQuery({
		queryKey: ['monitor', 'usage', 'toolkit', since, toolkitFilter, apiFilter, agentFilter],
		queryFn: () =>
			api.getTracesUsage({
				since,
				groupBy: 'toolkit',
				topLimit: 12,
				toolkitId: toolkitFilter,
				apiId: apiFilter,
				agentId: agentFilter,
			}),
		refetchInterval: 30_000,
	});

	const usageApiQuery = useQuery({
		queryKey: ['monitor', 'usage', 'api', since, toolkitFilter, apiFilter, agentFilter],
		queryFn: () =>
			api.getTracesUsage({
				since,
				groupBy: 'api',
				topLimit: 12,
				toolkitId: toolkitFilter,
				apiId: apiFilter,
				agentId: agentFilter,
			}),
		refetchInterval: 30_000,
	});

	const usageAgentQuery = useQuery({
		queryKey: ['monitor', 'usage', 'agent', since, toolkitFilter, apiFilter, agentFilter],
		queryFn: () =>
			api.getTracesUsage({
				since,
				groupBy: 'agent',
				topLimit: 12,
				toolkitId: toolkitFilter,
				apiId: apiFilter,
				agentId: agentFilter,
			}),
		refetchInterval: 30_000,
		enabled: tab === 'overview',
	});

	const recentTracesQuery = useQuery({
		queryKey: ['monitor', 'traces', since, toolkitFilter, apiFilter, agentFilter],
		queryFn: () =>
			api.listTraces({
				limit: 200,
				since,
				toolkitId: toolkitFilter,
				apiId: apiFilter,
				agentId: agentFilter,
			}),
		enabled: tab === 'overview',
		refetchInterval: 30_000,
	});

	const filteredTracesQuery = useQuery({
		queryKey: [
			'monitor',
			'log',
			'traces',
			page,
			statusFilter,
			toolkitFilter,
			apiFilter,
			agentFilter,
			since,
		],
		queryFn: () =>
			api.listTraces({
				page,
				limit: PAGE_SIZE,
				toolkitId: toolkitFilter,
				apiId: apiFilter,
				agentId: agentFilter,
				status: statusFilterToBackend(statusFilter),
				since,
			}),
		enabled: tab === 'log',
		refetchInterval: 15_000,
	});

	const inFlightJobsQuery = useQuery({
		queryKey: ['monitor', 'log', 'jobs', toolkitFilter, agentFilter],
		queryFn: () =>
			api.listJobs({
				status: 'pending,running',
				limit: 50,
				toolkitId: toolkitFilter,
				agentId: agentFilter,
			}),
		enabled: tab === 'log',
		refetchInterval: 5_000,
	});

	const stats = useMemo(
		() =>
			usageStatsQuery.data
				? usageToMonitorStats(usageStatsQuery.data as unknown as UsageResponse)
				: null,
		[usageStatsQuery.data],
	);

	const { apis: apiUsage, toolkits: toolkitUsage } = useMemo(
		() =>
			usageToTopRows(
				(usageApiQuery.data ?? null) as unknown as UsageResponse | null,
				(usageStatsQuery.data ?? null) as unknown as UsageResponse | null,
			),
		[usageApiQuery.data, usageStatsQuery.data],
	);

	const agentUsage = useMemo(
		() => usageToAgentRows((usageAgentQuery.data ?? null) as unknown as UsageResponse | null),
		[usageAgentQuery.data],
	);

	const timelinePoints = useMemo(
		() =>
			tracesToTimelinePoints(
				(recentTracesQuery.data?.traces ?? []) as unknown as TraceOut[],
				toolkitNameById,
				agentNameById,
			),
		[recentTracesQuery.data, toolkitNameById, agentNameById],
	);

	const traceEntries: ExecutionLogEntry[] = useMemo(() => {
		const traces = (filteredTracesQuery.data?.traces ?? []) as unknown as TraceOut[];
		return traces.map((t) => traceToLogEntry(t, toolkitNameById, agentNameById));
	}, [filteredTracesQuery.data, toolkitNameById, agentNameById]);

	const jobEntries: ExecutionLogEntry[] = useMemo(() => {
		const raw =
			(inFlightJobsQuery.data as unknown as { data?: JobOut[] } | undefined)?.data ?? [];
		const arr: JobOut[] = Array.isArray(raw) ? raw : [];
		return arr.map((j) => jobToLogEntry(j, toolkitNameById, agentNameById));
	}, [inFlightJobsQuery.data, toolkitNameById, agentNameById]);

	const mergedExecutions = useMemo(
		() => mergeInFlightWithHistory(traceEntries, jobEntries),
		[traceEntries, jobEntries],
	);

	const totalCount =
		(filteredTracesQuery.data as unknown as { total?: number } | undefined)?.total ??
		mergedExecutions.length;

	const apiOptions = useMemo(
		() =>
			apiUsage.map((api) => ({
				value: api.vendor,
				label: api.apiName,
			})),
		[apiUsage],
	);

	const hasFilters =
		statusFilter !== 'ALL' ||
		toolkitFilter !== null ||
		apiFilter !== null ||
		agentFilter !== null;

	const hasJobsFilters =
		jobStatusFilter !== 'all' ||
		jobKindFilter !== 'all' ||
		toolkitFilter !== null ||
		agentFilter !== null;

	const updateParam = (
		next: Record<string, string | null>,
		opts: { resetPage?: boolean } = {},
	) => {
		const p = new URLSearchParams(searchParams);
		for (const [key, val] of Object.entries(next)) {
			if (val === null || val === '') p.delete(key);
			else p.set(key, val);
		}
		if (opts.resetPage) p.delete('page');
		setSearchParams(p, { replace: true });
	};

	const handleTabChange = (next: MonitorTab) => {
		updateParam({ tab: next === 'overview' ? null : next });
	};
	const handleRangeChange = (next: TimeRange) =>
		updateParam({ range: next === '24h' ? null : next });
	const handleStatusChange = (next: ExecutionStatusFilter) =>
		updateParam({ status: next === 'ALL' ? null : next }, { resetPage: true });
	const handleToolkitChange = (next: string | null) =>
		updateParam({ toolkit: next }, { resetPage: true });
	const handleApiChange = (next: string | null) =>
		updateParam({ api: next }, { resetPage: true });
	const handleAgentChange = (next: string | null) =>
		updateParam({ agent: next }, { resetPage: true });
	const handleClearFilters = () =>
		updateParam({ status: null, toolkit: null, api: null, agent: null }, { resetPage: true });
	const handleJobStatusChange = (next: JobStatusFilter) =>
		updateParam({ jobStatus: next === 'all' ? null : next }, { resetPage: true });
	const handleJobKindChange = (next: JobKindFilter) =>
		updateParam({ jobKind: next === 'all' ? null : next }, { resetPage: true });
	const handleClearJobsFilters = () =>
		updateParam(
			{ jobStatus: null, jobKind: null, toolkit: null, agent: null },
			{ resetPage: true },
		);
	const handlePageChange = (next: number) => {
		updateParam({ page: next === 1 ? null : String(next) });
	};

	const handleOpenJob = (jobId: string) => {
		updateParam({ tab: 'jobs' });
		// The Jobs tab opens its own detail sheet on row click. Direct deep
		// linking by job id is a follow-up — for now the cross-link drops the
		// user on the correct tab and the polling table includes the job.
		void jobId;
	};

	const handleOpenTrace = (traceId: string) => {
		updateParam({ tab: 'log' });
		// Likewise: navigating to /traces/{id} programmatically is a follow-up.
		// The Execution Log shows the most recent traces; the user can find
		// the row from there.
		void traceId;
	};

	const handleRowClick = (entry: ExecutionLogEntry) => {
		const detail: ExecutionDetail = {
			...entry,
			inputs: {},
			outputs: undefined,
			isSeedOnlyRow: entry.isJobOnly === true,
		};
		setSelectedExecution(detail);
		setIsSheetOpen(true);
		if (!entry.isJobOnly) {
			api.getTrace(entry.executionId)
				.then((trace) => {
					const traceObj = trace as Record<string, unknown>;
					const rawSteps = Array.isArray(traceObj.steps)
						? (traceObj.steps as Array<Record<string, unknown>>)
						: [];
					const stepRows = rawSteps.map((s, index) => ({
						stepId: (s.step_id as string | null) ?? String(index + 1),
						stepIndex: index,
						operation: (s.operation as string | null) ?? null,
						status: (s.status as string | null) ?? null,
						httpStatus: (s.http_status as number | null) ?? null,
						error: (s.error as string | null) ?? null,
					}));
					setSelectedExecution((prev) =>
						prev && prev.executionId === entry.executionId
							? {
									...prev,
									inputs:
										((trace as Record<string, unknown>).request as
											| Record<string, unknown>
											| undefined) ?? {},
									outputs: (trace as Record<string, unknown>).response as
										| Record<string, unknown>
										| undefined,
									errorMessage:
										((trace as Record<string, unknown>).error as
											| string
											| undefined) ?? prev.errorMessage,
									stepRows,
									isSeedOnlyRow: false,
								}
							: prev,
					);
				})
				.catch(() => {
					setSelectedExecution((prev) =>
						prev && prev.executionId === entry.executionId
							? { ...prev, isSeedOnlyRow: true }
							: prev,
					);
				});
		}
	};

	const handleSheetClose = () => {
		setIsSheetOpen(false);
	};

	useEffect(() => {
		const data = filteredTracesQuery.data as unknown as { traces?: unknown[] } | undefined;
		if (page > 1 && data && Array.isArray(data.traces) && data.traces.length === 0) {
			updateParam({ page: null });
		}
	}, [page, filteredTracesQuery.data]); // eslint-disable-line react-hooks/exhaustive-deps

	const isOverviewLoading =
		usageStatsQuery.isLoading ||
		usageApiQuery.isLoading ||
		usageAgentQuery.isLoading ||
		recentTracesQuery.isLoading;

	// `isFetching` is true on every refetch (including background polls and
	// manual refreshes); `isLoading` is only true on the very first fetch
	// while there's no cached data. We feed `isFetching` into the refresh
	// button's spinner so a manual click keeps spinning until every query
	// resolves — visual ack that "your refresh is doing work" even though
	// we deliberately keep the previous data on screen instead of yanking
	// it away with a skeleton.
	const isAnyFetching =
		usageStatsQuery.isFetching ||
		usageApiQuery.isFetching ||
		usageAgentQuery.isFetching ||
		recentTracesQuery.isFetching ||
		filteredTracesQuery.isFetching ||
		inFlightJobsQuery.isFetching;

	const handleRefresh = () => {
		usageStatsQuery.refetch();
		usageApiQuery.refetch();
		usageAgentQuery.refetch();
		recentTracesQuery.refetch();
		filteredTracesQuery.refetch();
		inFlightJobsQuery.refetch();
	};

	return (
		<PageShell spacing="space-y-5">
			<PageHeader
				title="Monitor"
				subtitle="Real-time view of every broker call, async job, and workflow execution."
				actions={
					<>
						<SegmentedToggle
							layoutId="monitorRange"
							options={TIME_RANGE_OPTIONS}
							value={range}
							onChange={handleRangeChange}
						/>
						<RefreshButton
							onRefresh={handleRefresh}
							pending={isAnyFetching}
							disabled={isOverviewLoading}
						/>
					</>
				}
			/>

			<motion.div
				className="flex items-center justify-between"
				initial={{ opacity: 0, y: 6 }}
				animate={{ opacity: 1, y: 0 }}
				transition={{ duration: 0.2 }}
			>
				<SegmentedToggle
					layoutId="monitorTab"
					options={TAB_OPTIONS}
					value={tab}
					onChange={handleTabChange}
				/>
				{stats && stats.activeNow > 0 && (
					<HoverTooltip
						side="bottom"
						closeOnTooltipHover
						triggerClassName="inline-flex"
						content={
							<div className="space-y-1">
								<p className="font-medium">
									{stats.activeNow} async job{stats.activeNow === 1 ? '' : 's'}{' '}
									currently in flight
								</p>
								<p className="text-muted-foreground text-[11px]">
									Counts jobs in <code>pending</code> or <code>running</code>{' '}
									state — async broker calls and workflow executions that
									haven&apos;t reached a terminal state yet. Click to open the
									Jobs tab filtered to in-flight.
								</p>
							</div>
						}
					>
						<Button
							type="button"
							variant="ghost"
							onClick={() => {
								const p = new URLSearchParams(searchParams);
								p.set('tab', 'jobs');
								p.set('jobStatus', 'inflight');
								p.delete('page');
								setSearchParams(p, { replace: true });
							}}
							className="border-accent-blue/25 bg-accent-blue/8 hover:bg-accent-blue/15 hover:border-accent-blue/40 focus-visible:ring-accent-blue/50 flex h-auto cursor-pointer items-center gap-1.5 rounded-full border px-3 py-1.5 text-xs"
							aria-label={`${stats.activeNow} active async jobs — open Jobs tab filtered to in-flight`}
						>
							<span className="relative flex h-2 w-2">
								<span className="bg-accent-blue absolute inline-flex h-full w-full animate-ping rounded-full opacity-75" />
								<span className="bg-accent-blue relative inline-flex h-2 w-2 rounded-full" />
							</span>
							<span className="text-accent-blue font-medium">
								{stats.activeNow} active now
							</span>
						</Button>
					</HoverTooltip>
				)}
			</motion.div>

			{tab === 'overview' ? (
				<OverviewTab
					stats={stats}
					rawTimelinePoints={timelinePoints}
					timeRange={range}
					apiUsage={apiUsage}
					toolkitUsage={toolkitUsage}
					agentUsage={agentUsage}
					isLoading={isOverviewLoading}
				/>
			) : tab === 'log' ? (
				<ExecutionLogTab
					executions={mergedExecutions}
					totalCount={totalCount}
					page={page}
					pageSize={PAGE_SIZE}
					isLoading={filteredTracesQuery.isLoading || inFlightJobsQuery.isLoading}
					statusFilter={statusFilter}
					toolkitFilter={toolkitFilter}
					apiFilter={apiFilter}
					agentFilter={agentFilter}
					toolkitOptions={toolkitOptionsQuery.options}
					apiOptions={apiOptions}
					agentOptions={agentOptionsQuery.options}
					showAgentFilter={true}
					hasFilters={hasFilters}
					onStatusChange={handleStatusChange}
					onToolkitChange={handleToolkitChange}
					onApiChange={handleApiChange}
					onAgentChange={handleAgentChange}
					onClearFilters={handleClearFilters}
					onRowClick={handleRowClick}
					onPageChange={handlePageChange}
					onOpenJob={handleOpenJob}
				/>
			) : (
				<JobsTab
					statusFilter={jobStatusFilter}
					kindFilter={jobKindFilter}
					toolkitFilter={toolkitFilter}
					agentFilter={agentFilter}
					page={page}
					pageSize={PAGE_SIZE}
					since={since}
					toolkitOptions={toolkitOptionsQuery.options}
					agentOptions={agentOptionsQuery.options}
					toolkitNameById={toolkitNameById}
					agentNameById={agentNameById}
					showAgentFilter={true}
					hasFilters={hasJobsFilters}
					onStatusChange={handleJobStatusChange}
					onKindChange={handleJobKindChange}
					onToolkitChange={handleToolkitChange}
					onAgentChange={handleAgentChange}
					onClearFilters={handleClearJobsFilters}
					onPageChange={handlePageChange}
					onOpenTrace={handleOpenTrace}
				/>
			)}

			<ExecutionDetailSheet
				execution={selectedExecution}
				isOpen={isSheetOpen}
				isLoading={false}
				onClose={handleSheetClose}
				onOpenJob={handleOpenJob}
				onOpenTrace={handleOpenTrace}
				side="right"
			/>
		</PageShell>
	);
}
