/**
 * RailFeed — rendering layer for the live feed.
 *
 * Responsibilities:
 *   1. Apply the view filters: "Failures only" and the "What" categories.
 *   2. Fold a run of consecutive routine events — same type, same actor, same
 *      day — into ONE row ("Support Triage · 6 calls succeeded") that expands.
 *   3. Keep anything that went wrong or needs a human individual: failures,
 *      actionable and acknowledged events never fold.
 *   4. Defer to RailEventRow for actual rendering, so density is decided per-row.
 */
import { useMemo, useRef, useState } from 'react';
import { RailEventRow } from '@/shared/app/rail/RailEventRow';
import {
	categoryForKind,
	formatStreamDayLabel,
	isFailureSeverity,
	railGroupTitle,
	streamDayKey,
} from '@/shared/lib/agentStream';
import type { ActivityCategory, InlineActionSpec, StreamEvent } from '@/shared/lib/agentStream';

export type RailFeedFilters = {
	/** Only error/critical events (acknowledged ones included, dimmed). */
	failuresOnly: boolean;
	/** Only these kinds of activity; empty or absent = every kind. */
	categories?: ReadonlySet<ActivityCategory>;
};

export type RailFeedProps = {
	events: StreamEvent[];
	filters: RailFeedFilters;
	/** Friendly name for the actor behind an event, when resolvable. */
	resolveActor?: (ev: StreamEvent) => string | undefined;
	onAction?: (eventId: string, action: InlineActionSpec) => void;
	onNavigate?: (href: string) => void;
	/**
	 * Set when the feed is narrowed to one actor: the empty state names them
	 * and offers a way back to everything, instead of a misleading "All quiet".
	 */
	scopedTo?: { label: string; onClear: () => void };
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

/** Does `ev` survive the view filters? Shared with the rail's "N new" count. */
export function passesFeedFilters(ev: StreamEvent, f: RailFeedFilters): boolean {
	if (f.failuresOnly && !isFailureSeverity(ev.severity)) return false;
	if (f.categories && f.categories.size > 0) {
		const category = categoryForKind(ev.kind);
		if (!category || !f.categories.has(category)) return false;
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

/** Routine events fold; anything that went wrong or wants a human stays its own row. */
function isFoldable(ev: StreamEvent): boolean {
	return !isFailureSeverity(ev.severity) && !ev.requiresAction && !ev.acknowledged;
}

function foldsWith(a: StreamEvent, b: StreamEvent): boolean {
	return (
		a.type === b.type &&
		a.actorId === b.actorId &&
		a.actorType === b.actorType &&
		streamDayKey(a.tsMs) === streamDayKey(b.tsMs)
	);
}

function buildRows(events: StreamEvent[]): FeedRow[] {
	const out: FeedRow[] = [];
	for (const ev of events) {
		const last = out[out.length - 1];
		if (isFoldable(ev) && last && last.kind !== 'day') {
			const head = last.kind === 'group' ? last.head : last.ev;
			if (isFoldable(head) && foldsWith(head, ev)) {
				if (last.kind === 'group') last.members.push(ev);
				else out[out.length - 1] = { kind: 'group', head, members: [head, ev] };
				continue;
			}
		}
		out.push({ kind: 'single', ev });
	}
	return out;
}

type GroupRow = Extract<FeedRow, { kind: 'group' }>;

/**
 * A group keeps ONE identity while it grows at either end — live arrivals join
 * the newest end, Load older the oldest, and the feed's cap trims it — so the
 * row never remounts (collapsing it and replaying its arrival). The key is
 * the first member it was ever seen with; `seen` remembers member → key
 * across renders.
 */
function groupIdFor(row: GroupRow, seen: Map<string, string>): string {
	const known = row.members.find((m) => seen.has(m.id));
	const id = known ? seen.get(known.id)! : row.members[row.members.length - 1].id;
	for (const m of row.members) seen.set(m.id, id);
	return id;
}

export function RailFeed({
	events,
	filters,
	resolveActor,
	onAction,
	onNavigate,
	scopedTo,
}: RailFeedProps) {
	const filtered = useMemo(
		() => events.filter((ev) => passesFeedFilters(ev, filters)),
		[events, filters],
	);
	const groupKeys = useRef(new Map<string, string>());
	const rows = useMemo(() => {
		const built = withDaySeparators(buildRows(filtered));
		// Rebuilt per render set, so ids that left the feed don't pile up.
		const seen = new Map<string, string>();
		const prev = groupKeys.current;
		const keyed = built.map((row) => {
			if (row.kind !== 'group') return { row, id: null };
			const carried = row.members.find((m) => prev.has(m.id));
			if (carried) seen.set(carried.id, prev.get(carried.id)!);
			return { row, id: groupIdFor(row, seen) };
		});
		groupKeys.current = seen;
		return keyed;
	}, [filtered]);
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
				{filters.failuresOnly && events.length > 0 ? (
					<>No failures in what's loaded.</>
				) : filters.categories && filters.categories.size > 0 && events.length > 0 ? (
					<>Nothing of this kind in what's loaded.</>
				) : scopedTo ? (
					<>
						No recent activity from{' '}
						<span className="text-foreground font-medium">{scopedTo.label}</span>.
						<button
							type="button"
							onClick={scopedTo.onClear}
							className="text-primary mt-1.5 block w-full font-medium hover:underline"
						>
							Show everyone
						</button>
					</>
				) : ago ? (
					<>All quiet. Last event was {ago}.</>
				) : (
					<>All quiet. Waiting for the next event…</>
				)}
			</div>
		);
	}

	return (
		<div className="space-y-1">
			{rows.map(({ row, id: groupKey }) => {
				if (row.kind === 'day') {
					return (
						<div
							key={`day-${row.dayKey}`}
							className="text-muted-foreground flex items-center gap-2 pt-1.5 pb-0.5 text-[10px] font-semibold tracking-wider uppercase"
							// Presentational: keeps the separator out of the role="log"
							// announcement stream. The visible text is still readable in
							// context; an aria-label here would be prohibited ARIA
							// (axe: aria-prohibited-attr) and ignored anyway.
							role="presentation"
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
							actorName={resolveActor?.(row.ev)}
							onAction={onAction}
							onNavigate={onNavigate}
						/>
					);
				}
				const id = groupKey!;
				const isOpen = expanded.has(id);
				return (
					<div key={id}>
						<RailEventRow
							ev={row.head}
							actorName={resolveActor?.(row.head)}
							groupCount={row.members.length}
							groupTitle={
								railGroupTitle(row.head.type, row.members.length) ?? undefined
							}
							expanded={isOpen}
							onToggleExpand={() => toggle(id)}
							onAction={onAction}
							onNavigate={onNavigate}
						/>
						{isOpen && (
							<div className="border-border ml-3.5 space-y-0.5 border-l pl-1.5">
								{row.members.map((member) => (
									<RailEventRow
										key={member.id}
										ev={member}
										actorName={resolveActor?.(member)}
										hideActor
										onAction={onAction}
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
