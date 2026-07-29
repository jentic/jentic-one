/**
 * RailFeed — rendering layer for the live feed.
 *
 * Responsibilities:
 *   1. Apply filters (search, severity, kind) from the parent header.
 *   2. Group consecutive same-`groupKey` events inside a 10s window into a
 *      single row with a count.
 *   3. Keep critical/error events always individual (they never group).
 *   4. Defer to RailEventRow for actual rendering, so density is decided per-row.
 */
import { useMemo, useState } from 'react';
import { RailEventRow } from '@/shared/app/rail/RailEventRow';
import { formatStreamDayLabel, streamDayKey } from '@/shared/lib/agentStream';
import type { InlineActionSpec, StreamEvent } from '@/shared/lib/agentStream';

const GROUP_WINDOW_MS = 10_000;

export type RailFeedFilters = {
	search: string;
	severities: Set<StreamEvent['severity']>;
	kinds: Set<StreamEvent['kind']>;
};

export type RailFeedProps = {
	events: StreamEvent[];
	filters: RailFeedFilters;
	onAction?: (eventId: string, action: InlineActionSpec, reason?: string) => void;
	onOpenRequest?: (requestId: string, eventId: string) => void;
	onNavigate?: (href: string) => void;
};

type FeedRow =
	| { kind: 'single'; ev: StreamEvent }
	| { kind: 'group'; head: StreamEvent; members: StreamEvent[] }
	| { kind: 'day'; dayKey: string; tsMs: number };

/** The representative event of a content row (used for day-boundary detection). */
function rowHeadEvent(row: FeedRow): StreamEvent | null {
	if (row.kind === 'single') return row.ev;
	if (row.kind === 'group') return row.head;
	return null;
}

/**
 * Insert day-separator rows between content rows whenever the local calendar day
 * changes. Only applied when the feed spans more than one day, so a same-day
 * feed is visually unchanged (issue #705). Rows arrive newest-first, so the
 * first content row's day leads the feed.
 */
function withDaySeparators(rows: FeedRow[]): FeedRow[] {
	const days = new Set<string>();
	for (const row of rows) {
		const head = rowHeadEvent(row);
		if (!head) continue;
		const key = streamDayKey(head.tsMs);
		// A malformed timestamp yields an empty key; treating '' as a real day
		// would inject a blank, label-less separator, so skip it here and below.
		if (key) days.add(key);
	}
	if (days.size < 2) return rows;

	const out: FeedRow[] = [];
	let prevDay: string | null = null;
	for (const row of rows) {
		const head = rowHeadEvent(row);
		if (head) {
			const day = streamDayKey(head.tsMs);
			if (day && day !== prevDay) {
				out.push({ kind: 'day', dayKey: day, tsMs: head.tsMs });
				prevDay = day;
			}
		}
		out.push(row);
	}
	return out;
}

function passesFilters(ev: StreamEvent, f: RailFeedFilters): boolean {
	if (f.severities.size > 0 && !f.severities.has(ev.severity)) return false;
	if (f.kinds.size > 0 && !f.kinds.has(ev.kind)) return false;
	if (f.search.trim()) {
		const q = f.search.trim().toLowerCase();
		const hay = [
			ev.title,
			ev.meta ?? '',
			ev.type,
			ev.tokens.toolkit_id ?? '',
			ev.tokens.operation_id ?? '',
			ev.tokens.trace_id ?? '',
		]
			.join(' ')
			.toLowerCase();
		if (!hay.includes(q)) return false;
	}
	return true;
}

function formatLastEventAgo(tsMs: number): string {
	const diff = Math.max(0, Date.now() - tsMs);
	const sec = Math.floor(diff / 1000);
	if (sec < 60) return `${sec}s ago`;
	const min = Math.floor(sec / 60);
	if (min < 60) return `${min}m ago`;
	const hr = Math.floor(min / 60);
	if (hr < 24) return `${hr}h ago`;
	const day = Math.floor(hr / 24);
	return `${day}d ago`;
}

function buildRows(events: StreamEvent[]): FeedRow[] {
	const out: FeedRow[] = [];
	for (const ev of events) {
		// Critical/error and acknowledged events never group — visibility floor.
		if (ev.severity === 'critical' || ev.severity === 'error' || ev.acknowledged) {
			out.push({ kind: 'single', ev });
			continue;
		}
		const last = out[out.length - 1];
		if (last && last.kind === 'group') {
			const within = last.head.tsMs - ev.tsMs <= GROUP_WINDOW_MS;
			const sameKey = last.head.groupKey === ev.groupKey;
			if (within && sameKey) {
				last.members.push(ev);
				continue;
			}
		} else if (last && last.kind === 'single') {
			const within = last.ev.tsMs - ev.tsMs <= GROUP_WINDOW_MS;
			const sameKey = last.ev.groupKey === ev.groupKey;
			if (within && sameKey) {
				out[out.length - 1] = { kind: 'group', head: last.ev, members: [last.ev, ev] };
				continue;
			}
		}
		out.push({ kind: 'single', ev });
	}
	return out;
}

export function RailFeed({ events, filters, onAction, onOpenRequest, onNavigate }: RailFeedProps) {
	const filtered = useMemo(
		() => events.filter((ev) => passesFilters(ev, filters)),
		[events, filters],
	);
	const rows = useMemo(() => withDaySeparators(buildRows(filtered)), [filtered]);
	const [expanded, setExpanded] = useState<Set<string>>(() => new Set());

	function toggle(id: string) {
		setExpanded((prev) => {
			const next = new Set(prev);
			if (next.has(id)) next.delete(id);
			else next.add(id);
			return next;
		});
	}

	if (rows.length === 0) {
		const lastEvent = events[0];
		const ago = lastEvent ? formatLastEventAgo(lastEvent.tsMs) : null;
		return (
			<div className="text-muted-foreground border-border bg-background/40 rounded border border-dashed px-3 py-6 text-center text-[11px]">
				{ago ? (
					<>All quiet. Last event was {ago}.</>
				) : (
					<>All quiet. Waiting for the next event…</>
				)}
			</div>
		);
	}

	return (
		<div className="space-y-1">
			{rows.map((row) => {
				if (row.kind === 'day') {
					return (
						<div
							key={`day-${row.dayKey}`}
							className="text-muted-foreground flex items-center gap-2 pt-1.5 pb-0.5 text-[10px] font-semibold tracking-wider uppercase"
							role="presentation"
							aria-label={formatStreamDayLabel(row.tsMs)}
						>
							<span className="shrink-0">{formatStreamDayLabel(row.tsMs)}</span>
							<span className="bg-border h-px flex-1" />
						</div>
					);
				}
				if (row.kind === 'single') {
					return (
						<RailEventRow
							key={row.ev.id}
							ev={row.ev}
							onAction={onAction}
							onOpenRequest={onOpenRequest}
							onNavigate={onNavigate}
						/>
					);
				}
				const isOpen = expanded.has(row.head.id);
				return (
					<div key={row.head.id}>
						<RailEventRow
							ev={row.head}
							groupCount={row.members.length}
							expanded={isOpen}
							onToggleExpand={() => toggle(row.head.id)}
							onAction={onAction}
							onOpenRequest={onOpenRequest}
							onNavigate={onNavigate}
						/>
						{isOpen && (
							<div className="border-border ml-3 space-y-0.5 border-l pl-2">
								{row.members.slice(1).map((member) => (
									<RailEventRow
										key={member.id}
										ev={member}
										onAction={onAction}
										onOpenRequest={onOpenRequest}
										onNavigate={onNavigate}
									/>
								))}
							</div>
						)}
					</div>
				);
			})}
		</div>
	);
}
