/**
 * Jobs — the async job queue, one source of the Activity view.
 *
 * Lists `GET /jobs` newest-first in the shared log layout (see LogList): the
 * job's kind and state as a sentence ("Import running"), its id, and how long
 * it took. The status narrowing lives in the Activity toolbar
 * (`?status=active|completed|failed`); this body reads it. While any job on
 * the newest page is still queued/running, the list re-polls so it settles
 * without a manual refresh. Clicking a row opens the job in the detail pane,
 * which carries the Cancel action (org:admin only). Jobs carry no actor on
 * the wire, so the record resolves "who" from the audit log by `job_id`.
 */
import { useMemo } from 'react';
import { ChevronRight, ListChecks } from 'lucide-react';
import { Button, EmptyState, ErrorAlert, SkeletonRows } from '@/shared/ui';
import { toJobStatus, useJobs } from '@/modules/monitor/api';
import { CursorPager } from '@/modules/monitor/components/CursorPager';
import { groupByDay, LogDay, LogList, LogRow } from '@/modules/monitor/components/LogList';
import { LogLayout } from '@/modules/monitor/components/LogDetailPane';
import { RecordDetail } from '@/modules/monitor/components/RecordDetail';
import { useMonitorFilters } from '@/modules/monitor/lib/useMonitorFilters';
import { useCursorStack } from '@/modules/monitor/lib/useCursorStack';
import { formatRelative } from '@/modules/monitor/lib/format';
import { detailKey, useLogDetail } from '@/modules/monitor/lib/useLogDetail';
import { formatSpan, JOB_LABEL, JOB_TONE, jobSentence } from '@/modules/monitor/lib/logVocabulary';
import {
	JOB_STATUS_WIRE,
	useStatusFilter,
	type JobStatusFilter,
} from '@/modules/monitor/lib/statusFilters';

/** Re-poll cadence while the newest page still has non-terminal jobs. */
const ACTIVE_POLL_MS = 5_000;

export function JobsTab() {
	const { status: statusFilter, setStatus: setStatusFilter } =
		useStatusFilter<JobStatusFilter>('jobs');
	const { detail, open, close, docked } = useLogDetail('jobs');
	const openKey = detailKey(detail);

	const status = statusFilter === 'all' ? null : JOB_STATUS_WIRE[statusFilter];
	// Jobs has no actor parameter on the backend (the filter bar disables it);
	// only the time window / range applies.
	const filters = useMonitorFilters();
	const filterKey = JSON.stringify({ status, from: filters.from, to: filters.to });
	const pager = useCursorStack(filterKey);
	const query = useJobs(
		{ status, from: filters.from, to: filters.to, cursor: pager.cursor },
		{ pollWhileActive: pager.hasPrev ? false : ACTIVE_POLL_MS },
	);
	const rows = useMemo(() => query.data?.data ?? [], [query.data]);
	const days = useMemo(() => groupByDay(rows, (r) => Date.parse(r.created_at)), [rows]);
	const showEmpty = rows.length === 0 && !query.isLoading && !query.isFetching;

	return (
		<LogLayout
			detail={detail}
			docked={docked}
			onClose={close}
			renderDetail={(d, frame) => <RecordDetail detail={d} frame={frame} />}
		>
			<div className="space-y-3">
				{query.isError ? (
					<ErrorAlert
						message={
							query.error instanceof Error ? query.error : 'Failed to load jobs.'
						}
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
					<LogList
						ariaLabel="Jobs"
						columns={{ actor: 'Last update', subject: 'Job', detail: 'Took' }}
					>
						{query.isLoading ? (
							<SkeletonRows rows={6} className="px-4" />
						) : (
							days.map((day) => (
								<LogDay key={day.key} label={day.label}>
									{day.items.map((row) => {
										const s = toJobStatus(row.status);
										const failed = s === 'failed' || s === 'dead_letter';
										return (
											<LogRow
												key={row.job_id}
												tsMs={Date.parse(row.created_at)}
												tone={JOB_TONE[s]}
												statusLabel={JOB_LABEL[s]}
												title={jobSentence(row.kind, s)}
												error={failed ? row.error : null}
												secondary={
													row.execution_id
														? 'Produced an API call'
														: undefined
												}
												actor={
													<span className="font-normal">
														{formatRelative(
															row.updated_at ?? row.created_at,
														)}
													</span>
												}
												subject={
													<span className="truncate font-mono">
														{row.job_id}
													</span>
												}
												detail={
													formatSpan(row.created_at, row.updated_at) ??
													'—'
												}
												action={
													<ChevronRight
														className="text-muted-foreground/60 h-4 w-4"
														aria-hidden="true"
													/>
												}
												label={`View ${row.kind} job ${row.job_id}`}
												active={openKey === `job:${row.job_id}`}
												onOpen={() => open({ kind: 'job', id: row.job_id })}
											/>
										);
									})}
								</LogDay>
							))
						)}
					</LogList>
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
			</div>
		</LogLayout>
	);
}
