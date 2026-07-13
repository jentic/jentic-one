/**
 * Jobs tab — the async job queue.
 *
 * Lists `GET /jobs` newest-first, filterable by lifecycle status. Status renders
 * off the UI job-status union. Clicking a row opens the job detail sheet, which
 * carries the Cancel action (org:admin only). Jobs carry no actor on the wire,
 * so detail resolves "who" from the audit log by `job_id`.
 */
import { useSearchParams } from 'react-router-dom';
import { ListChecks, CheckCircle2, XCircle, Loader2, Ban, Clock, Skull } from 'lucide-react';
import { Button, EmptyState, ErrorAlert, RefreshButton, SegmentedToggle } from '@/shared/ui';
import { useJobs, toJobStatus, type JobStatusUi } from '@/modules/monitor/api';
import { JobDetailSheet } from '@/modules/monitor/components/JobDetailSheet';
import { CursorPager } from '@/modules/monitor/components/CursorPager';
import {
	MonitorList,
	MonitorRow,
	type MonitorAccent,
} from '@/modules/monitor/components/MonitorList';
import { useMonitorFilters } from '@/modules/monitor/lib/useMonitorFilters';
import { useCursorStack } from '@/modules/monitor/lib/useCursorStack';
import { formatRelative } from '@/modules/monitor/lib/format';
import { cn } from '@/shared/lib/utils';

type StatusFilter = 'all' | 'active' | 'completed' | 'failed';

const STATUS_FILTERS: { value: StatusFilter; label: string }[] = [
	{ value: 'all', label: 'All' },
	{ value: 'active', label: 'Active' },
	{ value: 'completed', label: 'Completed' },
	{ value: 'failed', label: 'Failed' },
];

function isStatusFilter(value: string | null): value is StatusFilter {
	return value === 'all' || value === 'active' || value === 'completed' || value === 'failed';
}

// The backend's JobStatus StrEnum: queued/running/completed/failed/cancelled/
// dead_letter. "Active" = not yet terminal; "failed" includes the dead-letter
// (exhausted-retries) bucket so a poison job still surfaces under Failed.
const FILTER_WIRE: Record<Exclude<StatusFilter, 'all'>, string[]> = {
	active: ['queued', 'running'],
	completed: ['completed'],
	failed: ['failed', 'dead_letter'],
};

const STATUS_VISUAL: Record<JobStatusUi, { icon: typeof CheckCircle2; accent: MonitorAccent }> = {
	queued: { icon: Clock, accent: 'neutral' },
	running: { icon: Loader2, accent: 'blue' },
	completed: { icon: CheckCircle2, accent: 'green' },
	failed: { icon: XCircle, accent: 'pink' },
	cancelled: { icon: Ban, accent: 'orange' },
	dead_letter: { icon: Skull, accent: 'pink' },
	unknown: { icon: ListChecks, accent: 'neutral' },
};

export function JobsTab() {
	const [searchParams, setSearchParams] = useSearchParams();
	const statusParam = searchParams.get('status');
	const statusFilter: StatusFilter = isStatusFilter(statusParam) ? statusParam : 'all';
	const openJobId = searchParams.get('job_id');

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

	const setOpenJobId = (jobId: string | null) => {
		setSearchParams(
			(prev) => {
				const next = new URLSearchParams(prev);
				if (jobId) next.set('job_id', jobId);
				else next.delete('job_id');
				return next;
			},
			{ replace: false },
		);
	};

	const status = statusFilter === 'all' ? null : FILTER_WIRE[statusFilter];
	// Jobs has no actor parameter on the backend (the global bar disables it);
	// only the time window applies.
	const filters = useMonitorFilters();
	const filterKey = JSON.stringify({ status, from: filters.from });
	const pager = useCursorStack(filterKey);
	const query = useJobs({ status, from: filters.from, cursor: pager.cursor });
	const rows = query.data?.data ?? [];
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
					message={query.error instanceof Error ? query.error : 'Failed to load jobs.'}
					onRetry={() => query.refetch()}
					retrying={query.isFetching}
				/>
			) : showEmpty ? (
				<EmptyState
					icon={<ListChecks className="h-8 w-8" />}
					title={statusFilter === 'all' ? 'No jobs yet' : 'No matching jobs'}
					description={
						statusFilter === 'all'
							? 'Background jobs (imports, async executions) will appear here as they are queued.'
							: 'No background jobs match the current status filter.'
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
				<MonitorList title="Jobs" ariaLabel="Jobs" isLoading={query.isLoading}>
					{rows.map((row) => {
						const status = toJobStatus(row.status);
						const visual = STATUS_VISUAL[status];
						const Icon = visual.icon;
						return (
							<MonitorRow
								key={row.job_id}
								accent={visual.accent}
								icon={
									<Icon
										className={cn(
											'h-4 w-4',
											status === 'running' && 'animate-spin',
										)}
									/>
								}
								title={row.kind}
								subtitle={
									<span className="flex flex-wrap items-center gap-x-1.5">
										<span className="font-mono">{row.job_id}</span>
										{row.execution_id && (
											<>
												<span aria-hidden>·</span>
												<span>trace linked</span>
											</>
										)}
									</span>
								}
								error={
									status === 'failed' || status === 'dead_letter'
										? row.error
										: null
								}
								meta={
									<>
										<span>{formatRelative(row.updated_at)}</span>
										<span className="text-muted-foreground">
											created {formatRelative(row.created_at)}
										</span>
									</>
								}
								onClick={() => setOpenJobId(row.job_id)}
								label={`View ${row.kind} job ${row.job_id}`}
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

			<JobDetailSheet
				jobId={openJobId}
				open={openJobId != null}
				onClose={() => setOpenJobId(null)}
			/>
		</div>
	);
}
