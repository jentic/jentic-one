/**
 * Events tab — platform events with a live stream.
 *
 * Combines the paginated `GET /events` history with an optional live SSE feed
 * (`GET /events/stream`, fetch-stream so the Bearer header can be sent). When
 * Live is on, streamed events are merged on top of the fetched page (deduped by
 * `event_id`, newest first) and a status pill reflects the connection. Events
 * that `requires_action` and aren't acknowledged get an Acknowledge button.
 */
import { useMemo } from 'react';
import { useSearchParams } from 'react-router';
import { Bell, Radio } from 'lucide-react';
import {
	Badge,
	Button,
	EmptyState,
	ErrorAlert,
	RefreshButton,
	SegmentedToggle,
	ActorLabel,
} from '@/shared/ui';
import { cn } from '@/shared/lib/utils';
import { eventSeverityIcon, idFromLink } from '@/shared/lib';
import {
	useEvents,
	useEventStream,
	useAcknowledgeEvent,
	EventSeverity,
	type EventResponse,
} from '@/modules/monitor/api';
import { CursorPager } from '@/modules/monitor/components/CursorPager';
import { TraceDetailSheet } from '@/modules/monitor/components/TraceDetailSheet';
import {
	MonitorList,
	MonitorRow,
	type MonitorAccent,
} from '@/modules/monitor/components/MonitorList';
import { useMonitorFilters } from '@/modules/monitor/lib/useMonitorFilters';
import { useCursorStack } from '@/modules/monitor/lib/useCursorStack';
import { formatRelative } from '@/modules/monitor/lib/format';
import { hasTrace } from '@/modules/monitor/lib/links';

type EventFilter = 'all' | 'action' | 'unacknowledged';

const EVENT_FILTERS: { value: EventFilter; label: string }[] = [
	{ value: 'all', label: 'All' },
	{ value: 'action', label: 'Needs action' },
	{ value: 'unacknowledged', label: 'Unacknowledged' },
];

function isEventFilter(value: string | null): value is EventFilter {
	return value === 'all' || value === 'action' || value === 'unacknowledged';
}

// Severity filter chips. This is a distinct axis from the status toggle above:
// status is "does it need a human?", severity is "how bad is it?". The backend
// honours `GET /events?severity=` (repeatable), so this is a pure query-param
// wiring — no client-side post-filter (issue #617: the Events tab had no way to
// filter by severity at all).
const SEVERITY_ORDER: EventSeverity[] = [
	EventSeverity.CRITICAL,
	EventSeverity.ERROR,
	EventSeverity.WARNING,
	EventSeverity.INFO,
];

const SEVERITY_CHIP_LABEL: Record<EventSeverity, string> = {
	[EventSeverity.CRITICAL]: 'Critical',
	[EventSeverity.ERROR]: 'Error',
	[EventSeverity.WARNING]: 'Warning',
	[EventSeverity.INFO]: 'Info',
};

// Selected-chip tint per severity. Critical + error intentionally share the
// danger tint (they're both failures), matching how the rail renders them — but
// each remains an independent filter value so selecting one never silently
// hides the other (the confusion behind #617's "critical shows nothing").
const SEVERITY_CHIP_ACTIVE: Record<EventSeverity, string> = {
	[EventSeverity.CRITICAL]: 'border-danger/50 bg-danger/15 text-danger',
	[EventSeverity.ERROR]: 'border-danger/50 bg-danger/15 text-danger',
	[EventSeverity.WARNING]: 'border-warning/50 bg-warning/15 text-warning',
	[EventSeverity.INFO]: 'border-primary/50 bg-primary/15 text-primary',
};

function parseSeverities(raw: string | null): Set<EventSeverity> {
	if (!raw) return new Set();
	const valid = new Set(SEVERITY_ORDER as string[]);
	return new Set(raw.split(',').filter((s) => valid.has(s)) as EventSeverity[]);
}

/**
 * Drill-in ids for an event, so a "critical/failed" event is clickable through
 * to its execution trace (issue #617: the flagged event was a dead end).
 *
 * The backend surfaces the linked execution as a HAL link — `_links.execution`
 * = `/executions/{id}` — NOT as a top-level field or a `data.execution_id`
 * entry, so that link is the primary source; `data` is only a fallback for
 * events that stamp the id there. `trace_id` comes from the top-level field
 * (again falling back to `data`). Returns nulls when the event references no
 * execution — the row is then rendered non-clickable.
 */
function drillInFor(row: EventResponse): { traceId: string | null; executionId: string | null } {
	const data = (row.data ?? {}) as Record<string, unknown>;
	const dataStr = (key: string): string | null => {
		const v = data[key];
		return typeof v === 'string' && v.length > 0 ? v : null;
	};
	// `_links.execution` is `/executions/{execution_id}` — parsed by the shared
	// HAL-link helper so the rail and this tab can't disagree on the rules.
	const executionIdFromLink = idFromLink(row._links?.execution) ?? null;
	return {
		traceId: row.trace_id ?? dataStr('trace_id'),
		executionId: executionIdFromLink ?? dataStr('execution_id'),
	};
}

const LIVE_STATUS_LABEL: Record<string, string> = {
	idle: 'Live off',
	connecting: 'Connecting…',
	live: 'Live',
	error: 'Stream error',
};

const LIVE_STATUS_VARIANT: Record<
	string,
	'default' | 'success' | 'warning' | 'danger' | 'pending'
> = {
	idle: 'default',
	connecting: 'pending',
	live: 'success',
	error: 'danger',
};

const SEVERITY_ACCENT: Record<EventSeverity, MonitorAccent> = {
	[EventSeverity.INFO]: 'blue',
	[EventSeverity.WARNING]: 'orange',
	[EventSeverity.ERROR]: 'pink',
	[EventSeverity.CRITICAL]: 'pink',
};

export function EventsTab() {
	const [searchParams, setSearchParams] = useSearchParams();
	const filterParam = searchParams.get('status');
	const filter: EventFilter = isEventFilter(filterParam) ? filterParam : 'all';
	const live = searchParams.get('live') === '1';
	const selectedSeverities = parseSeverities(searchParams.get('severity'));

	// Deep-link params for the trace/execution detail sheet — the SAME vocabulary
	// the Executions tab reads, so a rail deep-link or an Events-row click both
	// land on an open sheet (issue #617).
	const openTraceId = searchParams.get('trace_id');
	const openExecutionId = searchParams.get('execution_id');

	const setFilter = (value: EventFilter) => {
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

	const toggleSeverity = (sev: EventSeverity) => {
		setSearchParams(
			(prev) => {
				const next = new URLSearchParams(prev);
				const current = parseSeverities(prev.get('severity'));
				if (current.has(sev)) current.delete(sev);
				else current.add(sev);
				// Preserve the canonical order so the param is stable regardless of
				// click order (stable query key → no needless refetch).
				const ordered = SEVERITY_ORDER.filter((s) => current.has(s));
				if (ordered.length === 0) next.delete('severity');
				else next.set('severity', ordered.join(','));
				return next;
			},
			{ replace: true },
		);
	};

	const clearSeverities = () => {
		setSearchParams(
			(prev) => {
				const next = new URLSearchParams(prev);
				next.delete('severity');
				return next;
			},
			{ replace: true },
		);
	};

	// Open a row's detail sheet. Prefer a usable trace (groups the whole trace);
	// fall back to a bare execution id lifted from the event payload. Events that
	// reference no execution/trace aren't clickable (handled at the row level).
	const openEvent = (traceId: string | null, executionId: string | null) => {
		setSearchParams(
			(prev) => {
				const next = new URLSearchParams(prev);
				next.delete('trace_id');
				next.delete('execution_id');
				if (hasTrace(traceId)) next.set('trace_id', traceId);
				else if (executionId) next.set('execution_id', executionId);
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

	const setLive = (on: boolean) => {
		setSearchParams(
			(prev) => {
				const next = new URLSearchParams(prev);
				if (on) next.set('live', '1');
				else next.delete('live');
				return next;
			},
			{ replace: true },
		);
	};

	const filters = useMonitorFilters();
	const severityParam = searchParams.get('severity');
	// Cursor pagination applies to the historical page only; reset when any
	// filter (status, severity, window, actor) changes.
	const filterKey = JSON.stringify({
		filter,
		severity: severityParam,
		from: filters.from,
		actorId: filters.actorId,
		actorType: filters.actorType,
	});
	const pager = useCursorStack(filterKey);

	const severityList = SEVERITY_ORDER.filter((s) => selectedSeverities.has(s));
	const listParams = {
		requiresAction: filter === 'action' ? true : null,
		acknowledged: filter === 'unacknowledged' ? false : null,
		severity: severityList.length > 0 ? severityList : null,
		actorId: filters.actorId,
		actorType: filters.actorType,
		from: filters.from,
	};
	const query = useEvents({ ...listParams, cursor: pager.cursor });
	// The live stream honours the same actor + time-window filters (its `from`
	// maps to the SSE `since`); the status/ack filters apply to the historical
	// page only, since the stream forwards every new event for the window.
	const stream = useEventStream(listParams, live);
	const acknowledge = useAcknowledgeEvent();
	// Track which event id is mid-acknowledge so only its button shows pending,
	// instead of disabling every row's button during one in-flight mutation.
	const pendingAckId = acknowledge.isPending ? acknowledge.variables : null;

	// Merge live + fetched, newest-first, deduped by event_id.
	const merged = useMemo(() => {
		const seen = new Set<string>();
		const out: EventResponse[] = [];
		for (const e of [...stream.events, ...(query.data?.data ?? [])]) {
			if (seen.has(e.event_id)) continue;
			seen.add(e.event_id);
			out.push(e);
		}
		return out;
	}, [stream.events, query.data]);

	const showEmpty = merged.length === 0 && !query.isLoading && !query.isFetching;

	// Acknowledge control shared by the desktop column and the mobile card. Stops
	// propagation so it never triggers a row click.
	const renderAck = (row: EventResponse) =>
		row.requires_action && !row.acknowledged ? (
			<Button
				variant="outline"
				size="sm"
				onClick={(e) => {
					e.stopPropagation();
					acknowledge.mutate(row.event_id);
				}}
				loading={pendingAckId === row.event_id}
				disabled={pendingAckId === row.event_id}
			>
				Acknowledge
			</Button>
		) : row.acknowledged ? (
			<Badge variant="success">Acknowledged</Badge>
		) : null;

	return (
		<div className="space-y-4">
			<div className="flex flex-wrap items-center justify-between gap-2">
				<SegmentedToggle options={EVENT_FILTERS} value={filter} onChange={setFilter} />
				<div className="flex items-center gap-2">
					<Badge variant={LIVE_STATUS_VARIANT[stream.status]}>
						<Radio className="h-3 w-3" aria-hidden="true" />
						{LIVE_STATUS_LABEL[stream.status]}
					</Badge>
					{live && stream.status === 'error' && (
						<Button variant="outline" size="sm" onClick={() => stream.reconnect()}>
							Reconnect
						</Button>
					)}
					<Button
						variant={live ? 'danger' : 'secondary'}
						size="sm"
						onClick={() => setLive(!live)}
					>
						{live ? 'Stop live' : 'Go live'}
					</Button>
					<RefreshButton onRefresh={() => query.refetch()} pending={query.isFetching} />
				</div>
			</div>

			{/* Severity filter — an axis orthogonal to the status toggle above.
			    Repeatable `severity=` param, honoured server-side (#617). */}
			<div className="flex flex-wrap items-center gap-1.5">
				<span className="text-muted-foreground mr-1 text-[11px] font-medium">Severity</span>
				{SEVERITY_ORDER.map((sev) => {
					const active = selectedSeverities.has(sev);
					return (
						<button
							key={sev}
							type="button"
							aria-pressed={active}
							onClick={() => toggleSeverity(sev)}
							className={cn(
								'rounded-full border px-2.5 py-0.5 text-[11px] font-medium transition-colors',
								active
									? SEVERITY_CHIP_ACTIVE[sev]
									: 'border-border text-muted-foreground hover:bg-muted',
							)}
						>
							{SEVERITY_CHIP_LABEL[sev]}
						</button>
					);
				})}
				{selectedSeverities.size > 0 && (
					<Button
						variant="ghost"
						size="sm"
						onClick={clearSeverities}
						className="text-muted-foreground hover:text-foreground h-6 px-2 text-[11px]"
					>
						Clear
					</Button>
				)}
			</div>

			{/* Announce only the live connection STATUS to assistive tech. The
			    buffered-event count is intentionally excluded: it changes on every
			    streamed event and would flood the live region with re-announcements. */}
			<p className="sr-only" role="status" aria-live="polite">
				{live
					? `Live event stream ${LIVE_STATUS_LABEL[stream.status]}.`
					: 'Live event stream off.'}
			</p>

			{query.isError ? (
				<ErrorAlert
					message={query.error instanceof Error ? query.error : 'Failed to load events.'}
					onRetry={() => query.refetch()}
					retrying={query.isFetching}
				/>
			) : showEmpty ? (
				(() => {
					const anyFilterActive = filter !== 'all' || selectedSeverities.size > 0;
					return (
						<EmptyState
							icon={<Bell className="h-8 w-8" />}
							title={anyFilterActive ? 'No matching events' : 'No events yet'}
							description={
								anyFilterActive
									? 'No platform events match the current filter.'
									: 'Platform events will appear here. Toggle Go live to stream them as they happen.'
							}
							action={
								anyFilterActive ? (
									<Button
										variant="ghost"
										size="sm"
										onClick={() => {
											setFilter('all');
											clearSeverities();
										}}
										className="text-primary hover:text-primary font-medium hover:underline"
									>
										Clear filter
									</Button>
								) : undefined
							}
						/>
					);
				})()
			) : (
				<MonitorList
					title="Events"
					ariaLabel="Events"
					isLoading={query.isLoading && merged.length === 0}
				>
					{merged.map((row) => {
						const severity = row.severity ?? EventSeverity.INFO;
						const accent =
							SEVERITY_ACCENT[severity] ?? SEVERITY_ACCENT[EventSeverity.INFO];
						const Icon = eventSeverityIcon(severity);
						const ack = renderAck(row);
						const { traceId, executionId } = drillInFor(row);
						const clickable = hasTrace(traceId) || executionId != null;
						return (
							<MonitorRow
								key={row.event_id}
								accent={accent}
								icon={<Icon className="h-4 w-4" />}
								onClick={
									clickable ? () => openEvent(traceId, executionId) : undefined
								}
								label={clickable ? `Open execution for ${row.summary}` : undefined}
								title={row.summary}
								subtitle={
									<span className="flex flex-wrap items-center gap-x-1.5">
										<span className="font-mono">{row.type}</span>
										{row.actor_id && (
											<>
												<span aria-hidden>·</span>
												<ActorLabel
													actorId={row.actor_id}
													actorType={row.actor_type}
												/>
											</>
										)}
										{row.detail && (
											<>
												<span aria-hidden>·</span>
												<span className="text-foreground">
													{row.detail}
												</span>
											</>
										)}
									</span>
								}
								badges={ack}
								meta={<span>{formatRelative(row.created_at)}</span>}
							/>
						);
					})}
				</MonitorList>
			)}

			{/* Pager applies to the historical page. While Live is on, streamed
			    events prepend onto the current page, so paging is hidden to avoid
			    a confusing mix of live + historical navigation. */}
			{!query.isError && !showEmpty && !live && (
				<CursorPager
					hasMore={query.data?.has_more ?? false}
					hasPrev={pager.hasPrev}
					onOlder={() => pager.pushNext(query.data?.next_cursor)}
					onNewer={pager.goPrev}
					page={pager.page}
					loading={query.isFetching}
				/>
			)}
			{live && !showEmpty && (
				<p className="text-muted-foreground text-xs">
					Paging is paused while live — stop the stream to page through history.
				</p>
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
