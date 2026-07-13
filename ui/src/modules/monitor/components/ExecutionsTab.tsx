/**
 * Executions tab — the execution trace log.
 *
 * Lists `GET /executions` newest-first in a table, filterable by lifecycle
 * status. Status renders off the UI status union (mapped from the bare wire
 * string), never the raw value, so an unknown server status degrades to a
 * neutral pill rather than a broken colour. Clicking a row opens the trace
 * detail sheet.
 */
import { useSearchParams } from 'react-router-dom';
import { Activity, CheckCircle2, XCircle, Loader2, Ban, CircleDot } from 'lucide-react';
import {
	Button,
	EmptyState,
	ErrorAlert,
	RefreshButton,
	SegmentedToggle,
	StatusBadge,
	ActorLabel,
} from '@/shared/ui';
import { useExecutions, toExecutionStatus, type ExecutionStatusUi } from '@/modules/monitor/api';
import { TraceDetailSheet } from '@/modules/monitor/components/TraceDetailSheet';
import { CursorPager } from '@/modules/monitor/components/CursorPager';
import {
	MonitorList,
	MonitorRow,
	type MonitorAccent,
} from '@/modules/monitor/components/MonitorList';
import { useMonitorFilters } from '@/modules/monitor/lib/useMonitorFilters';
import { useCursorStack } from '@/modules/monitor/lib/useCursorStack';
import { hasTrace } from '@/modules/monitor/lib/links';
import { formatDuration, formatRelative } from '@/modules/monitor/lib/format';
import { cn } from '@/shared/lib/utils';

type StatusFilter = 'all' | 'completed' | 'failed';

const STATUS_FILTERS: { value: StatusFilter; label: string }[] = [
	{ value: 'all', label: 'All' },
	{ value: 'completed', label: 'Completed' },
	{ value: 'failed', label: 'Failed' },
];

function isStatusFilter(value: string | null): value is StatusFilter {
	return value === 'all' || value === 'completed' || value === 'failed';
}

// The backend's ExecutionStatus enum is terminal-only — it accepts exactly
// `completed` and `failed`, and 422s on any other value (see the executions
// router's `_TERMINAL_STATUSES` guard). There is no "running" execution to
// filter on, so we send the exact wire value for the chosen terminal status.
const FILTER_WIRE: Record<Exclude<StatusFilter, 'all'>, string[]> = {
	completed: ['completed'],
	failed: ['failed'],
};

// Per-status icon tile + accent, so the row's leading glyph reads the lifecycle
// at a glance (matching the Overview's colour vocabulary).
const STATUS_VISUAL: Record<
	ExecutionStatusUi,
	{ icon: typeof CheckCircle2; accent: MonitorAccent }
> = {
	completed: { icon: CheckCircle2, accent: 'green' },
	failed: { icon: XCircle, accent: 'pink' },
	running: { icon: Loader2, accent: 'blue' },
	cancelled: { icon: Ban, accent: 'orange' },
	unknown: { icon: CircleDot, accent: 'neutral' },
};

export function ExecutionsTab() {
	const [searchParams, setSearchParams] = useSearchParams();
	const statusParam = searchParams.get('status');
	const statusFilter: StatusFilter = isStatusFilter(statusParam) ? statusParam : 'all';
	const openTraceId = searchParams.get('trace_id');
	const openExecutionId = searchParams.get('execution_id');

	const setStatusFilter = (value: StatusFilter) => {
		setSearchParams(
			(prev) => {
				const next = new URLSearchParams(prev);
				if (value === 'all') next.delete('status');
				else next.set('status', value);
				return next;
			},
			{ replace: true },
		);
	};

	// Open a row's detail sheet. A usable trace deep-links by `trace_id` (so the
	// sheet can group the whole trace); a header-less run ("unknown" trace) falls
	// back to `execution_id` so we still show that one execution.
	const openExecution = (row: { trace_id: string | null; execution_id: string }) => {
		setSearchParams(
			(prev) => {
				const next = new URLSearchParams(prev);
				next.delete('trace_id');
				next.delete('execution_id');
				if (hasTrace(row.trace_id)) next.set('trace_id', row.trace_id);
				else next.set('execution_id', row.execution_id);
				return next;
			},
			{ replace: false },
		);
	};

	const closeSheet = () => {
		setSearchParams(
			(prev) => {
				const next = new URLSearchParams(prev);
				next.delete('trace_id');
				next.delete('execution_id');
				return next;
			},
			{ replace: false },
		);
	};

	const status = statusFilter === 'all' ? null : FILTER_WIRE[statusFilter];
	const filters = useMonitorFilters();
	const filterKey = JSON.stringify({
		status,
		from: filters.from,
		actorId: filters.actorId,
	});
	const pager = useCursorStack(filterKey);
	const query = useExecutions({
		status,
		from: filters.from,
		actorId: filters.actorId,
		cursor: pager.cursor,
	});
	const rows = query.data?.data ?? [];
	// Distinguish a still-loading first paint from a genuinely empty result so we
	// don't flash the empty state while a filter/tab switch is in flight.
	const showEmpty = rows.length === 0 && !query.isLoading && !query.isFetching;

	return (
		<div className="space-y-4">
			<div className="flex items-center justify-between gap-2">
				<SegmentedToggle
					options={STATUS_FILTERS}
					value={statusFilter}
					onChange={setStatusFilter}
				/>
				<RefreshButton onRefresh={() => query.refetch()} pending={query.isFetching} />
			</div>

			{query.isError ? (
				<ErrorAlert
					message={
						query.error instanceof Error ? query.error : 'Failed to load executions.'
					}
					onRetry={() => query.refetch()}
					retrying={query.isFetching}
				/>
			) : showEmpty ? (
				<EmptyState
					icon={<Activity className="h-8 w-8" />}
					title={statusFilter === 'all' ? 'No executions yet' : 'No matching executions'}
					description={
						statusFilter === 'all'
							? 'Execution traces will appear here once your agents start making API calls.'
							: 'No execution traces match the current status filter.'
					}
					action={
						statusFilter !== 'all' ? (
							<Button
								variant="ghost"
								size="sm"
								onClick={() => setStatusFilter('all')}
								className="text-primary hover:text-primary font-medium hover:underline"
							>
								Clear filter
							</Button>
						) : undefined
					}
				/>
			) : (
				<MonitorList title="Executions" ariaLabel="Executions" isLoading={query.isLoading}>
					{rows.map((row) => {
						const status = toExecutionStatus(row.status);
						const visual = STATUS_VISUAL[status];
						const Icon = visual.icon;
						return (
							<MonitorRow
								key={row.execution_id}
								accent={visual.accent}
								icon={
									<Icon
										className={cn(
											'h-4 w-4',
											status === 'running' && 'animate-spin',
										)}
									/>
								}
								title={row.operation_id ?? '—'}
								subtitle={
									<span className="flex flex-wrap items-center gap-x-1.5">
										<span className="text-foreground">
											{row.api?.name ?? row.api?.host ?? row.toolkit_id}
										</span>
										<span aria-hidden>·</span>
										{row.actor_id ? (
											<ActorLabel
												actorId={row.actor_id}
												actorType={row.actor_type}
											/>
										) : (
											<span className="font-mono">{row.actor_type}</span>
										)}
									</span>
								}
								error={status === 'failed' ? row.error : null}
								badges={<StatusBadge status={row.http_status} />}
								meta={
									<>
										<span className="text-foreground font-medium">
											{formatDuration(row.duration_ms)}
										</span>
										<span>{formatRelative(row.started_at)}</span>
									</>
								}
								onClick={() => openExecution(row)}
								label={`View trace for ${row.operation_id ?? row.execution_id}`}
							/>
						);
					})}
				</MonitorList>
			)}

			{!query.isError && !showEmpty && (
				<CursorPager
					hasMore={query.data?.has_more ?? false}
					hasPrev={pager.hasPrev}
					onOlder={() => pager.pushNext(query.data?.next_cursor)}
					onNewer={pager.goPrev}
					page={pager.page}
					loading={query.isFetching}
				/>
			)}

			<TraceDetailSheet
				traceId={openTraceId}
				executionId={openExecutionId}
				open={openTraceId != null || openExecutionId != null}
				onClose={closeSheet}
			/>
		</div>
	);
}
