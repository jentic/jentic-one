/**
 * ActivityFeed — the "Everything" source of the Activity view: every platform
 * event, newest first, always live.
 *
 * Two feeds merged into one list (deduped by `event_id`):
 *
 *   • history — `GET /events`, paged backwards ("Load older" appends a page)
 *   • live    — `GET /events/stream` (SSE), subscribed from the moment the
 *               filters were set, so it only carries what is genuinely new
 *
 * The list never shifts under the reader: while paused, or scrolled away from
 * the top, new events collect behind a sticky "N new events" pill instead of
 * being inserted. Clicking it reveals them and scrolls back up.
 *
 * Rows use the log's shared layout (see LogList) under sticky day headers.
 * Runs of three or more consecutive successful calls fold into one
 * "N successful calls" row so the interesting events (failures, approvals,
 * expiring credentials) aren't buried. Execution and job rows open their
 * record in the detail pane; everything else navigates to what it's about.
 *
 * Viewing a fixed range (brushed on the timeline) turns the live stream off —
 * nothing new can land inside a window that has already closed.
 */
import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { useNavigate } from 'react-router';
import {
	ArrowUp,
	BellRing,
	CheckCircle2,
	ChevronDown,
	ChevronRight,
	Pause,
	Play,
} from 'lucide-react';
import { ActorLabel, Button, EmptyState, ErrorAlert, SkeletonRows } from '@/shared/ui';
import {
	adaptEvent,
	formatStreamDayLabel,
	formatStreamTime,
	isFailureSeverity,
	isRetiredEventType,
	primaryDestinationFor,
	shellScroller,
	shellScrollTop,
	STREAM_KIND_LABEL,
	streamDayKey,
	type StreamEvent,
} from '@/shared/lib';
import { cn } from '@/shared/lib/utils';
import { StreamEventIcon } from '@/shared/app/rail/StreamEventIcon';
import {
	EventSeverity,
	useAcknowledgeEvent,
	useEventFeed,
	useEventStream,
	type EventResponse,
	type LiveStreamStatus,
} from '@/modules/monitor/api';
import { LogDay, LogList, LogRow, type LogTone } from '@/modules/monitor/components/LogList';
import { LogLayout } from '@/modules/monitor/components/LogDetailPane';
import { RecordDetail } from '@/modules/monitor/components/RecordDetail';
import { useMonitorFilters } from '@/modules/monitor/lib/useMonitorFilters';
import { detailKey, useLogDetail, type LogDetail } from '@/modules/monitor/lib/useLogDetail';
import { useStatusFilter, type FeedStatus } from '@/modules/monitor/lib/statusFilters';
import { hasTrace } from '@/modules/monitor/lib/links';

/** Scrolled further than this from the top, new events wait behind the pill. */
const TOP_THRESHOLD_PX = 160;
/** A run of this many consecutive successful calls folds into one row. */
const MIN_RUN = 3;
/** Reconnect backoff after a dropped stream: 2s, 4s, 8s … capped at 30s. */
const RECONNECT_BASE_MS = 2_000;
const RECONNECT_MAX_MS = 30_000;
/** Clock-skew allowance when discarding replayed backlog from the stream. */
const STREAM_SKEW_MS = 60_000;

type FeedItem =
	{ kind: 'event'; ev: StreamEvent } | { kind: 'run'; id: string; events: StreamEvent[] };

type DayGroup = { key: string; label: string; items: FeedItem[] };

function isSuccessfulCall(ev: StreamEvent): boolean {
	return ev.type === 'execution.completed' && !ev.requiresAction;
}

/** Fold runs of successful calls, then bucket by local day (input is newest-first). */
function buildDays(events: StreamEvent[], now: number): DayGroup[] {
	const days: DayGroup[] = [];
	let run: StreamEvent[] = [];
	let day: DayGroup | null = null;

	const flushRun = () => {
		if (!day) return;
		if (run.length >= MIN_RUN) day.items.push({ kind: 'run', id: run[0].id, events: run });
		else for (const ev of run) day.items.push({ kind: 'event', ev });
		run = [];
	};

	for (const ev of events) {
		const key = streamDayKey(ev.tsMs);
		if (!day || day.key !== key) {
			flushRun();
			day = { key, label: formatStreamDayLabel(ev.tsMs, now), items: [] };
			days.push(day);
		}
		if (isSuccessfulCall(ev)) {
			run.push(ev);
			continue;
		}
		flushRun();
		day.items.push({ kind: 'event', ev });
	}
	flushRun();
	return days;
}

/** True while the page is scrolled near the top (where inserts are safe). */
function useNearTop(): boolean {
	const [nearTop, setNearTop] = useState(() => shellScrollTop() < TOP_THRESHOLD_PX);
	useEffect(() => {
		const scroller = shellScroller();
		const onScroll = () => setNearTop(shellScrollTop() < TOP_THRESHOLD_PX);
		scroller.addEventListener('scroll', onScroll, { passive: true });
		return () => scroller.removeEventListener('scroll', onScroll);
	}, []);
	return nearTop;
}

export function ActivityFeed() {
	const navigate = useNavigate();
	const { detail, open, close, docked } = useLogDetail('all');
	const openKey = detailKey(detail);
	const filters = useMonitorFilters();
	const { status: statusFilter, setStatus } = useStatusFilter<FeedStatus>('all');

	const params = useMemo(
		() => ({
			from: filters.from,
			to: filters.to,
			actorId: filters.actorId,
			actorType: filters.actorType,
			severity:
				statusFilter === 'failed' ? [EventSeverity.ERROR, EventSeverity.CRITICAL] : null,
			requiresAction: statusFilter === 'action' ? true : null,
			acknowledged: statusFilter === 'action' ? false : null,
		}),
		[filters.from, filters.to, filters.actorId, filters.actorType, statusFilter],
	);
	const paramsKey = JSON.stringify(params);

	// The stream starts at "now" for the current filters — never the window's
	// lower bound, which would replay days of backlog the history already holds.
	// eslint-disable-next-line react-hooks/exhaustive-deps -- re-anchor on a real filter change only
	const streamSince = useMemo(() => new Date().toISOString(), [paramsKey]);
	const streamFloor = Date.parse(streamSince) - STREAM_SKEW_MS;
	const history = useEventFeed(params);
	const fixedRange = filters.to != null;
	const stream = useEventStream({ ...params, from: streamSince, to: null }, !fixedRange, 200, {
		toastOnError: false,
	});

	// A dropped stream retries on its own with backoff; the header says so.
	const [attempt, setAttempt] = useState(0);
	const { status: streamStatus, reconnect } = stream;
	useEffect(() => {
		if (streamStatus === 'live') setAttempt(0);
		if (streamStatus !== 'error') return;
		const delay = Math.min(RECONNECT_BASE_MS * 2 ** attempt, RECONNECT_MAX_MS);
		const t = setTimeout(() => {
			setAttempt((a) => a + 1);
			reconnect();
		}, delay);
		return () => clearTimeout(t);
	}, [streamStatus, attempt, reconnect]);

	const [paused, setPaused] = useState(false);
	const nearTop = useNearTop();
	const [revealed, setRevealed] = useState<ReadonlySet<string>>(() => new Set());
	const autoReveal = !paused && nearTop;

	const historyEvents = useMemo(
		() => history.data?.pages.flatMap((p) => p.data) ?? [],
		[history.data],
	);
	const historyIds = useMemo(
		() => new Set(historyEvents.map((e) => e.event_id)),
		[historyEvents],
	);
	const liveEvents = useMemo(
		() =>
			stream.events.filter(
				(e) =>
					!isRetiredEventType(e.type) &&
					// A backend (or mock) that replays backlog on connect must not
					// resurface old rows as "new".
					Date.parse(e.created_at) >= streamFloor &&
					// The stream can't filter on acknowledged; Needs action can.
					!(params.acknowledged === false && e.acknowledged),
			),
		[stream.events, params.acknowledged, streamFloor],
	);

	// Reveal new arrivals immediately while it's safe to insert.
	useEffect(() => {
		if (!autoReveal) return;
		setRevealed((prev) => {
			const missing = liveEvents.filter((e) => !prev.has(e.event_id));
			if (missing.length === 0) return prev;
			const next = new Set(prev);
			for (const e of missing) next.add(e.event_id);
			return next;
		});
	}, [autoReveal, liveEvents]);

	const pending = liveEvents.filter(
		(e) => !revealed.has(e.event_id) && !historyIds.has(e.event_id),
	);

	const revealAll = () => {
		setRevealed((prev) => {
			const next = new Set(prev);
			for (const e of liveEvents) next.add(e.event_id);
			return next;
		});
		shellScroller().scrollTo({ top: 0, behavior: 'smooth' });
	};

	// Acks flip locally at once; the refetched history confirms them.
	const acknowledge = useAcknowledgeEvent();
	const [ackedIds, setAckedIds] = useState<ReadonlySet<string>>(() => new Set());
	const pendingAckId = acknowledge.isPending ? acknowledge.variables : null;
	const onAcknowledge = (eventId: string) =>
		acknowledge.mutate(eventId, {
			onSuccess: () => setAckedIds((prev) => new Set(prev).add(eventId)),
		});

	const events = useMemo(() => {
		// History wins a dedupe: after an acknowledge it's the fresher copy.
		const byId = new Map<string, EventResponse>();
		for (const e of liveEvents) if (revealed.has(e.event_id)) byId.set(e.event_id, e);
		for (const e of historyEvents) byId.set(e.event_id, e);
		return [...byId.values()]
			.filter((e) => !isRetiredEventType(e.type))
			.map((e) => {
				const ev = adaptEvent(e);
				return ackedIds.has(ev.id) ? { ...ev, acknowledged: true } : ev;
			})
			.filter((ev) => !(params.acknowledged === false && ev.acknowledged))
			.sort((a, b) => b.tsMs - a.tsMs);
	}, [liveEvents, historyEvents, revealed, ackedIds, params.acknowledged]);

	const days = useMemo(() => buildDays(events, Date.now()), [events]);
	const [expandedRuns, setExpandedRuns] = useState<ReadonlySet<string>>(() => new Set());
	const toggleRun = (id: string) =>
		setExpandedRuns((prev) => {
			const next = new Set(prev);
			if (next.has(id)) next.delete(id);
			else next.add(id);
			return next;
		});

	const openEvent = (ev: StreamEvent) => {
		const target = recordFor(ev);
		if (target) {
			open(target);
			return;
		}
		const href = primaryDestinationFor(ev);
		if (href) navigate(href);
	};

	const filtered = statusFilter !== 'all' || filters.actorId != null || filters.range != null;
	const initialLoading = history.isLoading && events.length === 0;
	const showEmpty = !initialLoading && !history.isError && events.length === 0;

	const liveBar = (
		<header className="border-border/60 flex min-h-11 items-center justify-between gap-3 border-b px-3 py-2 sm:px-4">
			{fixedRange ? (
				<span className="text-muted-foreground text-xs font-medium">
					Fixed range — live updates off
				</span>
			) : (
				<LiveIndicator status={stream.status} paused={paused} />
			)}
			{!fixedRange && (
				<Button
					variant="ghost"
					size="sm"
					onClick={() => {
						if (paused) revealAll();
						setPaused((p) => !p);
					}}
					aria-pressed={paused}
				>
					{paused ? (
						<Play className="h-3.5 w-3.5" aria-hidden="true" />
					) : (
						<Pause className="h-3.5 w-3.5" aria-hidden="true" />
					)}
					{paused ? 'Resume' : 'Pause'}
				</Button>
			)}
		</header>
	);

	const loadOlder =
		history.hasNextPage && !showEmpty ? (
			<div className="border-border/60 flex justify-center border-t px-4 py-3">
				<Button
					variant="secondary"
					size="sm"
					onClick={() => void history.fetchNextPage()}
					loading={history.isFetchingNextPage}
				>
					Load older
				</Button>
			</div>
		) : undefined;

	const eventRow = (ev: StreamEvent, nested?: boolean) => (
		<FeedRow
			key={ev.id}
			ev={ev}
			nested={nested}
			active={openKey != null && openKey === detailKey(recordFor(ev))}
			onOpen={() => openEvent(ev)}
			onAcknowledge={() => onAcknowledge(ev.id)}
			acknowledging={pendingAckId === ev.id}
		/>
	);

	return (
		<LogLayout
			detail={detail}
			docked={docked}
			onClose={close}
			renderDetail={(d, frame) => <RecordDetail detail={d} frame={frame} />}
		>
			<div className="space-y-3">
				<p className="sr-only" role="status" aria-live="polite">
					{fixedRange
						? 'Viewing a fixed range; live updates off.'
						: paused
							? 'Live updates paused.'
							: `Live updates ${STATUS_TEXT[stream.status]}.`}
				</p>

				{pending.length > 0 && (
					<div className="pointer-events-none sticky top-[calc(var(--log-top,0px)+1rem)] z-20 flex justify-center">
						<button
							type="button"
							onClick={revealAll}
							className="bg-primary text-primary-foreground pointer-events-auto inline-flex items-center gap-1.5 rounded-full px-3.5 py-1.5 text-xs font-semibold shadow-lg ring-1 ring-black/5 transition-transform hover:scale-[1.03]"
						>
							<ArrowUp className="h-3.5 w-3.5" aria-hidden="true" />
							{pending.length === 1 ? '1 new event' : `${pending.length} new events`}
						</button>
					</div>
				)}

				<LogList
					ariaLabel="Activity feed"
					columns={{ actor: 'Who', subject: 'Area', detail: '' }}
					actionWidth="7.5rem"
					header={liveBar}
					footer={loadOlder}
				>
					{history.isError && events.length === 0 ? (
						<div className="p-4">
							<ErrorAlert
								message={
									history.error instanceof Error
										? history.error
										: 'Failed to load activity.'
								}
								onRetry={() => history.refetch()}
								retrying={history.isFetching}
							/>
						</div>
					) : initialLoading ? (
						<SkeletonRows rows={8} className="px-4" />
					) : showEmpty ? (
						<EmptyState
							icon={<BellRing className="h-8 w-8" />}
							title={filtered ? 'Nothing matches' : 'No activity yet'}
							description={
								statusFilter === 'action'
									? 'Nothing is waiting on you. Failures and approvals that need a decision show up here.'
									: filtered
										? 'No events match the current filters in this window.'
										: 'Calls, jobs, approvals and alerts will stream in here as they happen.'
							}
							action={
								statusFilter !== 'all' ? (
									<Button
										variant="ghost"
										size="sm"
										onClick={() => setStatus('all')}
									>
										Show everything
									</Button>
								) : undefined
							}
						/>
					) : (
						<div role="log" aria-label="Events" aria-relevant="additions">
							{days.map((day) => (
								<LogDay key={day.key} label={day.label}>
									{day.items.map((item) =>
										item.kind === 'event' ? (
											eventRow(item.ev)
										) : (
											<RunRow
												key={item.id}
												events={item.events}
												expanded={expandedRuns.has(item.id)}
												onToggle={() => toggleRun(item.id)}
												renderEvent={(ev) => eventRow(ev, true)}
											/>
										),
									)}
								</LogDay>
							))}
						</div>
					)}
				</LogList>
			</div>
		</LogLayout>
	);
}

/** The record an event opens in the pane — or null when it navigates instead. */
function recordFor(ev: StreamEvent): LogDetail | null {
	const { trace_id: traceId, execution_id: executionId, job_id: jobId } = ev.tokens;
	if (ev.kind === 'execution' && hasTrace(traceId)) return { kind: 'trace', id: traceId };
	if (ev.kind === 'execution' && executionId) return { kind: 'execution', id: executionId };
	if (jobId) return { kind: 'job', id: jobId };
	return null;
}

function eventTone(ev: StreamEvent): LogTone {
	if (isFailureSeverity(ev.severity)) return 'fail';
	if (ev.severity === 'warning') return 'warn';
	if (/\.(completed|approved)$/.test(ev.type)) return 'ok';
	return 'neutral';
}

const TONE_LABEL: Record<LogTone, string> = {
	ok: 'Succeeded',
	fail: 'Failed',
	warn: 'Warning',
	running: 'In progress',
	neutral: 'Info',
};

const STATUS_TEXT: Record<LiveStreamStatus, string> = {
	idle: 'off',
	connecting: 'connecting',
	live: 'live',
	error: 'reconnecting',
};

function LiveIndicator({ status, paused }: { status: LiveStreamStatus; paused: boolean }) {
	const label = paused
		? 'Paused'
		: status === 'live'
			? 'Live'
			: status === 'error'
				? 'Reconnecting…'
				: 'Connecting…';
	const tone = paused ? 'bg-muted-foreground' : status === 'live' ? 'bg-success' : 'bg-warning';
	return (
		<span className="text-muted-foreground inline-flex items-center gap-2 text-xs font-medium">
			<span className="relative flex h-2 w-2" aria-hidden="true">
				{status === 'live' && !paused && (
					<span className="bg-success absolute inline-flex h-full w-full animate-ping rounded-full opacity-60" />
				)}
				<span className={cn('relative inline-flex h-2 w-2 rounded-full', tone)} />
			</span>
			{label}
		</span>
	);
}

function FeedRow({
	ev,
	onOpen,
	onAcknowledge,
	acknowledging,
	active,
	nested,
}: {
	ev: StreamEvent;
	onOpen: () => void;
	onAcknowledge: () => void;
	acknowledging: boolean;
	active?: boolean;
	nested?: boolean;
}) {
	const tone = eventTone(ev);
	const action =
		ev.requiresAction && !ev.acknowledged ? (
			<Button
				variant="outline"
				size="sm"
				onClick={onAcknowledge}
				loading={acknowledging}
				disabled={acknowledging}
			>
				Acknowledge
			</Button>
		) : ev.requiresAction && ev.acknowledged ? (
			<span className="text-muted-foreground inline-flex items-center gap-1 text-xs">
				<CheckCircle2 className="h-3.5 w-3.5" aria-hidden="true" />
				<span className="max-sm:sr-only">Acknowledged</span>
			</span>
		) : recordFor(ev) || primaryDestinationFor(ev) ? (
			<ChevronRight className="text-muted-foreground/60 h-4 w-4" aria-hidden="true" />
		) : null;

	return (
		<LogRow
			tsMs={ev.tsMs}
			tone={tone}
			statusLabel={TONE_LABEL[tone]}
			title={ev.title}
			secondary={ev.meta || undefined}
			actor={
				ev.actorId ? (
					<ActorLabel actorId={ev.actorId} actorType={ev.actorType} />
				) : undefined
			}
			subject={
				<>
					<StreamEventIcon ev={ev} />
					<span className="truncate">{STREAM_KIND_LABEL[ev.kind]}</span>
				</>
			}
			action={action}
			label={ev.title}
			active={active}
			muted={ev.acknowledged}
			nested={nested}
			onOpen={onOpen}
		/>
	);
}

function RunRow({
	events,
	expanded,
	onToggle,
	renderEvent,
}: {
	events: StreamEvent[];
	expanded: boolean;
	onToggle: () => void;
	renderEvent: (ev: StreamEvent) => ReactNode;
}) {
	const newest = events[0];
	const oldest = events[events.length - 1];
	const actors = new Set(events.map((e) => e.actorId ?? ''));
	const soleActor = actors.size === 1 ? newest.actorId : undefined;
	const span = `${formatStreamTime(oldest.tsMs).slice(0, 5)}–${formatStreamTime(newest.tsMs).slice(0, 5)}`;

	return (
		<>
			<LogRow
				tsMs={newest.tsMs}
				tone="ok"
				statusLabel="Succeeded"
				title={`${events.length} successful calls`}
				secondary={expanded ? 'Showing each call' : 'Folded to keep the feed readable'}
				actor={
					soleActor ? (
						<ActorLabel actorId={soleActor} actorType={newest.actorType} />
					) : // One unattributed actor: say so rather than "1 actors".
					actors.size === 1 ? (
						'Unattributed'
					) : (
						`${actors.size} actors`
					)
				}
				subject={
					<>
						<StreamEventIcon ev={newest} />
						<span className="truncate">{STREAM_KIND_LABEL[newest.kind]}</span>
					</>
				}
				detail={span}
				action={
					<ChevronDown
						className={cn(
							'text-muted-foreground h-4 w-4 transition-transform',
							expanded && 'rotate-180',
						)}
						aria-hidden="true"
					/>
				}
				label={`${events.length} successful calls — ${expanded ? 'collapse' : 'expand'}`}
				steppable={false}
				onOpen={onToggle}
			/>
			{expanded && events.map((ev) => renderEvent(ev))}
		</>
	);
}
