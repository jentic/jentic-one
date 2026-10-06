/**
 * API calls — the execution trace log, one source of the Activity view.
 *
 * Lists `GET /executions` newest-first in the shared log layout (see LogList):
 * the operation as the sentence, who called it, the API, and duration + HTTP
 * status. The status narrowing lives in the Activity toolbar
 * (`?status=completed|failed`); this body reads it. Status renders off the UI
 * status union (mapped from the bare wire string), never the raw value, so an
 * unknown server status degrades to a neutral glyph. Clicking a row opens its
 * trace in the detail pane. The first page quietly re-polls so new calls
 * appear without a manual refresh — unless a fixed range is being viewed.
 */
import { useMemo } from 'react';
import { useSearchParams } from 'react-router';
import { Activity, ChevronRight, X } from 'lucide-react';
import { ActorLabel, Button, EmptyState, ErrorAlert, SkeletonRows, VendorIcon } from '@/shared/ui';
import { formatOperation } from '@/shared/lib';
import { cn } from '@/shared/lib/utils';
import { toExecutionStatus, useExecutions, type ExecutionResponse } from '@/modules/monitor/api';
import { CursorPager } from '@/modules/monitor/components/CursorPager';
import { groupByDay, LogDay, LogList, LogRow } from '@/modules/monitor/components/LogList';
import { LogLayout } from '@/modules/monitor/components/LogDetailPane';
import { RecordDetail } from '@/modules/monitor/components/RecordDetail';
import { useMonitorFilters } from '@/modules/monitor/lib/useMonitorFilters';
import { useCursorStack } from '@/modules/monitor/lib/useCursorStack';
import { detailKey, useLogDetail, type LogDetail } from '@/modules/monitor/lib/useLogDetail';
import { hasTrace } from '@/modules/monitor/lib/links';
import { formatDuration } from '@/modules/monitor/lib/format';
import { EXECUTION_LABEL, EXECUTION_TONE } from '@/modules/monitor/lib/logVocabulary';
import {
	CALL_STATUS_WIRE,
	useStatusFilter,
	type CallStatus,
} from '@/modules/monitor/lib/statusFilters';

/** How often the newest page of calls re-polls (older pages stay put). */
const CALLS_POLL_MS = 10_000;

/** A usable trace opens the whole trace; a header-less run opens just itself. */
function detailFor(row: ExecutionResponse): LogDetail {
	return hasTrace(row.trace_id)
		? { kind: 'trace', id: row.trace_id }
		: { kind: 'execution', id: row.execution_id };
}

function apiName(row: ExecutionResponse): string {
	return row.api?.name ?? row.api?.host ?? 'Unknown API';
}

function httpTone(status: number): string {
	if (status >= 500) return 'text-danger';
	if (status >= 400) return 'text-warning';
	return 'text-muted-foreground';
}

export function ExecutionsTab() {
	const [searchParams, setSearchParams] = useSearchParams();
	const { status: statusFilter, setStatus: setStatusFilter } =
		useStatusFilter<CallStatus>('calls');
	const { detail, open, close, docked } = useLogDetail('calls');
	const openKey = detailKey(detail);
	// `?api=vendor:name` — set by the Overview breakdown's drill-down.
	const apiFilter = searchParams.get('api') || null;

	const clearApiFilter = () => {
		setSearchParams(
			(prev) => {
				const next = new URLSearchParams(prev);
				next.delete('api');
				return next;
			},
			{ replace: true },
		);
	};

	const status = statusFilter === 'all' ? null : CALL_STATUS_WIRE[statusFilter];
	const filters = useMonitorFilters();
	const filterKey = JSON.stringify({
		status,
		from: filters.from,
		to: filters.to,
		actorId: filters.actorId,
		origin: filters.origin,
		api: apiFilter,
	});
	const pager = useCursorStack(filterKey);
	const query = useExecutions(
		{
			status,
			from: filters.from,
			to: filters.to,
			actorId: filters.actorId,
			origin: filters.origin,
			api: apiFilter,
			cursor: pager.cursor,
		},
		{ refetchInterval: pager.hasPrev || filters.to ? false : CALLS_POLL_MS },
	);
	const rows = useMemo(() => query.data?.data ?? [], [query.data]);
	const days = useMemo(() => groupByDay(rows, (r) => Date.parse(r.started_at)), [rows]);
	// Distinguish a still-loading first paint from a genuinely empty result so we
	// don't flash the empty state while a filter/source switch is in flight.
	const showEmpty = rows.length === 0 && !query.isLoading && !query.isFetching;
	const narrowed = statusFilter !== 'all' || apiFilter != null || filters.range != null;

	return (
		<LogLayout
			detail={detail}
			docked={docked}
			onClose={close}
			renderDetail={(d, frame) => <RecordDetail detail={d} frame={frame} />}
		>
			<div className="space-y-3">
				{apiFilter && (
					<div className="flex items-center gap-2">
						<Button
							variant="outline"
							size="sm"
							onClick={clearApiFilter}
							aria-label={`Clear API filter ${apiFilter.replace(/:/g, '/')}`}
						>
							API: {apiFilter.replace(/:/g, '/')}
							<X className="h-3.5 w-3.5" aria-hidden="true" />
						</Button>
					</div>
				)}

				{query.isError ? (
					<ErrorAlert
						message={
							query.error instanceof Error
								? query.error
								: 'Failed to load executions.'
						}
						onRetry={() => query.refetch()}
						retrying={query.isFetching}
					/>
				) : showEmpty ? (
					<EmptyState
						icon={<Activity className="h-8 w-8" />}
						title={narrowed ? 'No matching calls' : 'No API calls yet'}
						description={
							narrowed
								? 'No calls match the current filters.'
								: 'Calls will appear here once your agents start using APIs through Jentic.'
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
						ariaLabel="Executions"
						columns={{ actor: 'Called by', subject: 'API', detail: 'Took · HTTP' }}
					>
						{query.isLoading ? (
							<SkeletonRows rows={8} className="px-4" />
						) : (
							days.map((day) => (
								<LogDay key={day.key} label={day.label}>
									{day.items.map((row) => {
										const s = toExecutionStatus(row.status);
										const d = detailFor(row);
										const op = formatOperation(row) ?? 'Unnamed operation';
										return (
											<LogRow
												key={row.execution_id}
												tsMs={Date.parse(row.started_at)}
												tone={EXECUTION_TONE[s]}
												statusLabel={EXECUTION_LABEL[s]}
												title={op}
												mono
												error={row.error}
												secondary={
													row.credential_name ??
													row.credential_id ??
													undefined
												}
												actor={
													row.actor_id ? (
														<ActorLabel
															actorId={row.actor_id}
															actorType={row.actor_type}
														/>
													) : (
														row.actor_type
													)
												}
												subject={
													<>
														<VendorIcon
															name={apiName(row)}
															vendor={row.api?.vendor ?? undefined}
															size="sm"
															className="h-4.5 w-4.5 shrink-0 rounded text-[7px]"
														/>
														<span className="truncate">
															{apiName(row)}
														</span>
													</>
												}
												detail={
													<>
														{formatDuration(row.duration_ms)}
														{row.http_status != null && (
															<span
																className={cn(
																	'ml-2',
																	httpTone(row.http_status),
																)}
															>
																{row.http_status}
															</span>
														)}
													</>
												}
												action={
													<ChevronRight
														className="text-muted-foreground/60 h-4 w-4"
														aria-hidden="true"
													/>
												}
												label={`View trace for ${op}`}
												active={openKey === detailKey(d)}
												onOpen={() => open(d)}
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
