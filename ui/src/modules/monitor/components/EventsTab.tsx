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
import { useSearchParams } from 'react-router-dom';
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
import { eventSeverityIcon } from '@/shared/lib';
import {
	useEvents,
	useEventStream,
	useAcknowledgeEvent,
	EventSeverity,
	type EventResponse,
} from '@/modules/monitor/api';
import { CursorPager } from '@/modules/monitor/components/CursorPager';
import {
	MonitorList,
	MonitorRow,
	type MonitorAccent,
} from '@/modules/monitor/components/MonitorList';
import { useMonitorFilters } from '@/modules/monitor/lib/useMonitorFilters';
import { useCursorStack } from '@/modules/monitor/lib/useCursorStack';
import { formatRelative } from '@/modules/monitor/lib/format';

type EventFilter = 'all' | 'action' | 'unacknowledged';

const EVENT_FILTERS: { value: EventFilter; label: string }[] = [
	{ value: 'all', label: 'All' },
	{ value: 'action', label: 'Needs action' },
	{ value: 'unacknowledged', label: 'Unacknowledged' },
];

function isEventFilter(value: string | null): value is EventFilter {
	return value === 'all' || value === 'action' || value === 'unacknowledged';
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
	// Cursor pagination applies to the historical page only; reset when any
	// filter (status, window, actor) changes.
	const filterKey = JSON.stringify({
		filter,
		from: filters.from,
		actorId: filters.actorId,
		actorType: filters.actorType,
	});
	const pager = useCursorStack(filterKey);

	const listParams = {
		requiresAction: filter === 'action' ? true : null,
		acknowledged: filter === 'unacknowledged' ? false : null,
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
				<EmptyState
					icon={<Bell className="h-8 w-8" />}
					title={filter === 'all' ? 'No events yet' : 'No matching events'}
					description={
						filter === 'all'
							? 'Platform events will appear here. Toggle Go live to stream them as they happen.'
							: 'No platform events match the current filter.'
					}
					action={
						filter !== 'all' ? (
							<Button
								variant="ghost"
								size="sm"
								onClick={() => setFilter('all')}
								className="text-primary hover:text-primary font-medium hover:underline"
							>
								Clear filter
							</Button>
						) : undefined
					}
				/>
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
						return (
							<MonitorRow
								key={row.event_id}
								accent={accent}
								icon={<Icon className="h-4 w-4" />}
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
		</div>
	);
}
