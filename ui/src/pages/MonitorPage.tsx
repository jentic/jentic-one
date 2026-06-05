import { type JSX, useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { motion } from 'framer-motion';
import { api } from '@/api/client';
import { PageShell } from '@/components/layout/PageShell';
import { PageHeader } from '@/components/ui/PageHeader';
import { PageHelp } from '@/components/ui/PageHelp';
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

/**
 * Tiny debounce — used for the search input so the URL `?q=` and the
 * server query don't churn on every keystroke. 200ms is the sweet spot:
 * fast enough that the result feels live, slow enough that typing
 * "stripe" doesn't fire 6 requests + 6 history pushes.
 */
function useDebouncedValue<T>(value: T, ms: number): T {
	const [debounced, setDebounced] = useState(value);
	useEffect(() => {
		const t = setTimeout(() => setDebounced(value), ms);
		return () => clearTimeout(t);
	}, [value, ms]);
	return debounced;
}

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
	// `?q=` is shareable — the URL is the source of truth. We keep a local
	// echo (`searchInput`) so typing feels instant; debouncing pushes the
	// committed value into the URL + queries 200ms after the user stops.
	const qParam = readSearchParam(searchParams, 'q') ?? '';
	const [searchInput, setSearchInput] = useState(qParam);
	const debouncedSearch = useDebouncedValue(searchInput, 200);
	// Deep-link ids. `?id=` opens the Execution Detail drawer for a trace,
	// `?job=` opens the Job Detail drawer in the Jobs tab. They're mutually
	// exclusive: opening one clears the other so a back-and-forth between
	// drawers doesn't accumulate history-stack noise.
	const selectedExecIdParam = readSearchParam(searchParams, 'id');
	const selectedJobIdParam = readSearchParam(searchParams, 'job');

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
			qParam,
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
				// Mirror the JobsTab guard: trim + drop blanks so the
				// no-search query plan is identical to the legacy one.
				q: qParam.trim() ? qParam.trim() : undefined,
			}),
		enabled: tab === 'log',
		refetchInterval: 15_000,
	});

	// In-flight jobs surfaced at the top of the Execution Log so a running
	// async job is visible before its trace finalises. We only fetch these
	// when no status/api filter is active that would logically exclude them:
	// a status filter (e.g. "success") or an API filter contradicts "show me
	// the pending/running jobs", and in-flight jobs carry no api_id to match
	// against anyway. `since` is included so the window matches the trace
	// query and the key churns with it.
	const showInFlightJobs = statusFilter === 'ALL' && apiFilter === null && !qParam.trim();
	const inFlightJobsQuery = useQuery({
		queryKey: ['monitor', 'log', 'jobs', toolkitFilter, agentFilter, since],
		queryFn: () =>
			api.listJobs({
				status: 'pending,running',
				limit: 50,
				toolkitId: toolkitFilter,
				agentId: agentFilter,
				since,
			}),
		enabled: tab === 'log' && showInFlightJobs,
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
		agentFilter !== null ||
		qParam !== '';

	const hasJobsFilters =
		jobStatusFilter !== 'all' ||
		jobKindFilter !== 'all' ||
		toolkitFilter !== null ||
		agentFilter !== null ||
		qParam !== '';

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
	const handleSearchChange = (next: string) => {
		// Update the local echo immediately so typing feels live; the URL
		// + queries pick up `debouncedSearch` via the effect below.
		setSearchInput(next);
	};
	const handleClearFilters = () => {
		setSearchInput('');
		updateParam(
			{ status: null, toolkit: null, api: null, agent: null, q: null },
			{ resetPage: true },
		);
	};
	const handleJobStatusChange = (next: JobStatusFilter) =>
		updateParam({ jobStatus: next === 'all' ? null : next }, { resetPage: true });
	const handleJobKindChange = (next: JobKindFilter) =>
		updateParam({ jobKind: next === 'all' ? null : next }, { resetPage: true });
	const handleClearJobsFilters = () => {
		setSearchInput('');
		updateParam(
			{ jobStatus: null, jobKind: null, toolkit: null, agent: null, q: null },
			{ resetPage: true },
		);
	};
	const handlePageChange = (next: number) => {
		updateParam({ page: next === 1 ? null : String(next) });
	};

	const handleOpenJob = (jobId: string) => {
		// Cross-link from the Execution Log drawer → Jobs tab. We:
		//   1. Switch to the Jobs tab
		//   2. Stamp `?job=` so JobsTab can open its drawer for this id
		//   3. Clear `?id=` and close any open exec drawer so we don't end up
		//      with two drawers stacked
		updateParam({ tab: 'jobs', job: jobId, id: null });
		setIsSheetOpen(false);
		setSelectedExecution(null);
	};

	const handleOpenTrace = (traceId: string) => {
		// Cross-link to the Execution Log. Stamping `?id=` is enough — the
		// effect below picks it up, fetches the trace, and opens the drawer.
		// The Jobs-side drawer is closed by clearing `?job=`.
		updateParam({ tab: 'log', id: traceId, job: null });
	};

	const fetchAndPopulateTraceDetail = (traceId: string) => {
		api.getTrace(traceId)
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
				const rawChildren = Array.isArray(traceObj.children)
					? (traceObj.children as Array<Record<string, unknown>>)
					: [];
				const childTraces = rawChildren.map((c) => {
					const created = c.created_at as number | null | undefined;
					return {
						id: (c.id as string) ?? '',
						operationId: (c.operation_id as string | null | undefined) ?? null,
						status: (c.status as string | null | undefined) ?? null,
						httpStatus: (c.http_status as number | null | undefined) ?? null,
						durationMs: (c.duration_ms as number | null | undefined) ?? null,
						// Normalise to ISO so the panel can use the same date utils as
						// the rest of the drawer; created_at on the wire is a unix
						// second float, missing on rows the writer hasn't stamped yet.
						createdAt:
							typeof created === 'number'
								? new Date(created * 1000).toISOString()
								: null,
						apiId: (c.api_id as string | null | undefined) ?? null,
						apiName: (c.api_name as string | null | undefined) ?? null,
					};
				});
				// Backend returns workflow-only inputs/outputs columns; broker
				// rows arrive null and the panels stay empty (matches webapp).
				const traceInputs =
					(traceObj.inputs as Record<string, unknown> | null | undefined) ?? {};
				const traceOutputs =
					(traceObj.outputs as Record<string, unknown> | null | undefined) ?? undefined;
				setSelectedExecution((prev) =>
					prev && prev.executionId === traceId
						? {
								...prev,
								inputs: traceInputs,
								outputs: traceOutputs,
								errorMessage:
									((trace as Record<string, unknown>).error as
										| string
										| undefined) ?? prev.errorMessage,
								stepRows,
								childTraces,
								isSeedOnlyRow: false,
								// Backend returns these too — keep them on the
								// detail so the "Linked Context" panel renders
								// even when we landed here from a deep link
								// (i.e. without going through handleRowClick
								// which inherits from the table row).
								parentTraceId:
									(traceObj.parent_trace_id as string | null | undefined) ??
									prev.parentTraceId ??
									null,
								jobId:
									(traceObj.job_id as string | null | undefined) ??
									prev.jobId ??
									null,
							}
						: prev,
				);
			})
			.catch(() => {
				setSelectedExecution((prev) =>
					prev && prev.executionId === traceId ? { ...prev, isSeedOnlyRow: true } : prev,
				);
			});
	};

	const handleRowClick = (entry: ExecutionLogEntry) => {
		// User clicked a row in the table. Stamp the URL so the deep-link is
		// shareable and so reopening from the back button works. The effect
		// below sees `?id=…` and triggers the actual open.
		updateParam({ id: entry.executionId, job: null });
	};

	const handleSheetClose = () => {
		setIsSheetOpen(false);
		// Clearing the URL param is what actually drives the close — this
		// setState is just to make the close feel instant before the URL
		// update propagates.
		updateParam({ id: null });
	};

	// Drive the Execution Detail drawer from `?id=`. Single source of truth:
	// row clicks, deep links, browser history, and the cross-link from the
	// Job drawer all funnel through this effect. The effect re-fetches when
	// the id changes and skips fetching for "job-only" rows (jobs with no
	// trace yet — pending workflow runs that haven't produced an executions
	// row yet).
	useEffect(() => {
		if (!selectedExecIdParam) {
			setIsSheetOpen(false);
			setSelectedExecution(null);
			return;
		}
		// Try to find the row in the merged table data first so we have
		// header context (status pill, timestamps, vendor) before the fetch
		// completes. Falls back to a minimal stub if the trace isn't in the
		// current page — the fetch will fill the rest.
		const rowFromTable: ExecutionLogEntry | undefined = mergedExecutions.find(
			(r: ExecutionLogEntry) => r.executionId === selectedExecIdParam,
		);
		const seed: ExecutionDetail = rowFromTable
			? {
					...rowFromTable,
					inputs: {},
					outputs: undefined,
					isSeedOnlyRow: rowFromTable.isJobOnly === true,
				}
			: ({
					executionId: selectedExecIdParam,
					executionLogId: selectedExecIdParam,
					executionType: 'operation',
					status: 'pending',
					workflowId: null,
					workflowName: null,
					operationName: null,
					toolkitId: null,
					toolkitName: null,
					agentId: null,
					agentName: null,
					apiVendor: null,
					apiName: null,
					createdAt: new Date().toISOString(),
					inputs: {},
					outputs: undefined,
					isSeedOnlyRow: true,
					parentTraceId: null,
					jobId: null,
				} as unknown as ExecutionDetail);
		setSelectedExecution(seed);
		setIsSheetOpen(true);
		if (!seed.isSeedOnlyRow || !rowFromTable) {
			fetchAndPopulateTraceDetail(selectedExecIdParam);
		}
		// `mergedExecutions` is intentionally excluded — refetching when
		// the table polls would re-trigger the fetch effect needlessly.
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [selectedExecIdParam]);

	// Sync the debounced search input → URL (?q=). Only fires when the
	// committed value diverges from the URL, so typing 6 chars produces
	// at most 1 history replace, not 6. resetPage so a search doesn't
	// land on page 7 of the previous result set.
	useEffect(() => {
		if (debouncedSearch === qParam) return;
		// Guard against a stale debounced value clobbering a fresh edit: only
		// push once the debounce has actually caught up to the live input.
		// Without this, clearing the field (searchInput='' + q=null) races the
		// lagging debouncedSearch ('stripe'), which would re-push q=stripe and
		// undo the clear — the "clear flap" bug.
		if (debouncedSearch !== searchInput) return;
		updateParam({ q: debouncedSearch || null }, { resetPage: true });
		// updateParam is not memoised; including it would loop. The
		// closure captures the latest searchParams via the outer hook.
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [debouncedSearch, qParam, searchInput]);

	// Sync the URL → input on external changes (back/forward, deep
	// link). The `searchInput !== qParam` guard prevents this from
	// fighting the outgoing sync above.
	useEffect(() => {
		if (qParam !== searchInput) {
			setSearchInput(qParam);
		}
		// We deliberately don't depend on `searchInput`; this effect's
		// job is to react to URL changes, not to local typing.
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [qParam]);

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
						<PageHelp
							title="About Monitor"
							intro={
								<p>
									<strong>Monitor</strong> is the operational lens on every
									capability call your agents make. The page splits into three
									tabs: <strong>Overview</strong> for trends and breakdowns,{' '}
									<strong>Execution Log</strong> for the historical record of
									every call, and <strong>Jobs</strong> for the control plane over
									async work.
								</p>
							}
							sections={[
								{
									heading: 'Execution Log vs Jobs — the short version',
									body: (
										<>
											<p>
												Both tabs show capability calls, but they answer
												different questions and they're backed by different
												tables.
											</p>
											<ul className="mt-2 list-disc space-y-1 pl-5">
												<li>
													<strong>Execution Log</strong> = "what
													happened." One row per <em>trace</em> — written
													after the call completes. Includes every
													synchronous broker call, every workflow run, and
													every async call once it has finished.
												</li>
												<li>
													<strong>Jobs</strong> = "what was asked for,
													including the parts that haven't happened yet."
													One row per <em>job</em> — written when the call
													was submitted. Only async-flavoured calls live
													here: <code>Prefer: wait=0</code>, async
													workflow runs, and broker calls where upstream
													itself returned 202.
												</li>
											</ul>
										</>
									),
								},
								{
									heading: 'When to use which tab',
									body: (
										<ul className="list-disc space-y-1 pl-5">
											<li>
												Debugging or auditing past calls →{' '}
												<strong>Execution Log</strong>.
											</li>
											<li>
												Watching what's running right now or cancelling a
												runaway job → <strong>Jobs</strong>.
											</li>
											<li>
												Looking at high-level health, top APIs, top agents →{' '}
												<strong>Overview</strong>.
											</li>
										</ul>
									),
								},
								{
									heading: 'Why both tabs exist',
									body: (
										<>
											<p>
												The two tabs overlap on async calls that have
												already completed, but each surface has data the
												other can't show:
											</p>
											<ul className="mt-2 list-disc space-y-1 pl-5">
												<li>
													A synchronous broker call (the common case)
													writes only a trace, never a job — so it{' '}
													<strong>only</strong> appears in the Execution
													Log.
												</li>
												<li>
													A job that is still <em>pending</em> or{' '}
													<em>running</em> hasn't produced a trace yet —
													so it <strong>only</strong> appears in Jobs.
												</li>
												<li>
													Jobs hold the agent-supplied <code>inputs</code>
													, the <code>callback_url</code>, the
													upstream-job URL, and the cancel action. Traces
													don't.
												</li>
											</ul>
										</>
									),
								},
								{
									heading: 'How the two tabs are linked',
									body: (
										<ul className="list-disc space-y-1 pl-5">
											<li>
												Execution Log rows that came from a job render a{' '}
												<code>[job ↗]</code> badge that opens the matching
												Jobs row.
											</li>
											<li>
												Jobs that have produced a trace render a{' '}
												<code>[trace ↗]</code> badge that opens the matching
												Execution Log drawer.
											</li>
											<li>
												The Overview's <strong>Active</strong> pill is
												clickable and routes to the Jobs tab filtered to in-
												flight statuses.
											</li>
										</ul>
									),
								},
								{
									heading: 'Inputs and outputs in the drawer',
									body: (
										<p>
											Workflow rows show real <strong>Inputs</strong> and{' '}
											<strong>Outputs</strong> in the detail drawer because
											workflows have schema-shaped, bounded I/O. Broker
											(single API call) rows leave those panels empty on
											purpose — the broker's natural "input/output" is the raw
											HTTP body, which is unbounded and routinely contains
											PII, so we don't persist it. Use the operation, status,
											and timing fields in the drawer header to inspect a
											broker call.
										</p>
									),
								},
							]}
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
					searchQuery={searchInput}
					toolkitOptions={toolkitOptionsQuery.options}
					apiOptions={apiOptions}
					agentOptions={agentOptionsQuery.options}
					showAgentFilter={true}
					hasFilters={hasFilters}
					onStatusChange={handleStatusChange}
					onToolkitChange={handleToolkitChange}
					onApiChange={handleApiChange}
					onAgentChange={handleAgentChange}
					onSearchChange={handleSearchChange}
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
					searchQuery={searchInput}
					searchQueryDebounced={debouncedSearch}
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
					onSearchChange={handleSearchChange}
					onClearFilters={handleClearJobsFilters}
					onPageChange={handlePageChange}
					onOpenTrace={handleOpenTrace}
					selectedJobId={selectedJobIdParam}
					onSelectionChange={(jobId) =>
						updateParam(jobId ? { job: jobId } : { job: null })
					}
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
