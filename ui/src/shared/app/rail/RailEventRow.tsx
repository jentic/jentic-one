/**
 * RailEventRow — one platform event in the Activity feed, in plain language:
 * "<who> · <what happened>" plus how long ago. The text is the short
 * `railTitle` (the icon already says what kind of thing it was); the full
 * summary is the row's accessible name and tooltip.
 *
 * A folded run of routine events renders as ONE row — "Support Triage · 6 calls
 * succeeded" — that expands into its members.
 *
 * Colour is reserved for things that went wrong:
 *   • error / critical: red stripe + faint tint, full layout (summary wraps)
 *   • warning:          subtle amber stripe, full layout
 *   • info:             no stripe, one compact line
 *
 * Exception: an unacknowledged event that `requiresAction` always uses the full
 * layout regardless of severity, so its inline action slot is never hidden
 * (see issue #652). Acknowledged events collapse to the compact line and dim —
 * the dimming is the "handled" signal, no extra label.
 *
 * Inline-action slot (actionable rows only):
 *   • "Acknowledge" — real `PATCH /events/{id}` via the parent
 *   • "View …" / "Review" — pure-navigation deep-links into the
 *     execution/job/trace/agent the event references
 */
import { useState, type ReactNode } from 'react';
import { motion, useReducedMotion } from 'framer-motion';
import { ChevronDown } from 'lucide-react';
import { Button } from '@/shared/ui/Button';
import { Tooltip } from '@/shared/ui';
import { StreamEventIcon } from '@/shared/app/rail/StreamEventIcon';
import { useNow } from '@/shared/app/rail/useNow';
import {
	formatStreamAgo,
	formatStreamDateTimeParts,
	conflictHint,
	inlineActionsFor,
	isFailureSeverity,
	primaryDestinationFor,
	railTitle,
} from '@/shared/lib/agentStream';
import type { InlineActionSpec, StreamEvent } from '@/shared/lib/agentStream';
import { cn } from '@/shared/lib/utils';

/** How recent an event must be, when its row mounts, to animate in. */
const ARRIVAL_WINDOW_MS = 10_000;

export type RailEventRowProps = {
	ev: StreamEvent;
	/** Friendly name of whoever caused the event; omitted when unknown. */
	actorName?: string;
	groupCount?: number; // > 1 means this row represents a collapsed group
	/** A group's plural wording ("6 calls succeeded"); replaces the title. */
	groupTitle?: string;
	/** Inside an expanded group, the actor is already named on the group row. */
	hideActor?: boolean;
	expanded?: boolean;
	onToggleExpand?: () => void;
	onAction?: (eventId: string, action: InlineActionSpec) => void;
	onNavigate?: (href: string) => void;
};

/** Two-line tooltip body: date on top, time below (issue #705 polish). */
function TimeTooltipContent({ tsMs }: { tsMs: number }) {
	const { date, time } = formatStreamDateTimeParts(tsMs);
	return (
		<span className="flex flex-col gap-0.5 text-center leading-tight">
			<span className="text-muted-foreground text-[11px] font-medium">{date}</span>
			<span className="text-foreground font-mono text-xs">{time}</span>
		</span>
	);
}

function isCompact(ev: StreamEvent): boolean {
	if (ev.acknowledged) return true;
	// An unacknowledged event that still needs a human decision must keep its
	// full layout so the inline action slot (Review/Acknowledge) renders —
	// actionable events can be emitted at INFO severity. See issue #652.
	if (ev.requiresAction) return false;
	return ev.severity === 'info';
}

/** Left-edge stripe: red for failures, a faint amber for warnings, none otherwise. */
function stripeClass(ev: StreamEvent): string {
	if (ev.acknowledged) return 'border-l-transparent';
	if (ev.severity === 'critical') return 'border-l-4 border-l-danger';
	if (ev.severity === 'error') return 'border-l-danger';
	if (ev.severity === 'warning') return 'border-l-warning/40';
	return 'border-l-transparent';
}

function TimeStamp({ tsMs, className }: { tsMs: number; className?: string }) {
	const now = useNow();
	return (
		// `relative z-10`: above the row's stretched overlay, or the pointer
		// could never reach the tooltip.
		<Tooltip
			content={<TimeTooltipContent tsMs={tsMs} />}
			className={cn('relative z-10 shrink-0', className)}
		>
			<time
				dateTime={Number.isNaN(tsMs) ? undefined : new Date(tsMs).toISOString()}
				className="text-muted-foreground/80 text-[10px] tabular-nums"
			>
				{formatStreamAgo(tsMs, now)}
			</time>
		</Tooltip>
	);
}

/**
 * A live arrival opens its own space: the row grows from zero height, so the
 * feed below slides down to make room instead of jumping, and fades up as it
 * lands. The clip is only on while it grows — a resting row must not clip its
 * focus ring.
 */
function ArrivalReveal({ children }: { children: ReactNode }) {
	const reduce = useReducedMotion();
	const [settled, setSettled] = useState(false);
	if (reduce) return <>{children}</>;
	return (
		<motion.div
			initial={{ height: 0, opacity: 0 }}
			animate={{ height: 'auto', opacity: 1 }}
			transition={{
				height: { duration: 0.36, ease: [0.32, 0.72, 0, 1] },
				opacity: { duration: 0.28, delay: 0.1 },
			}}
			onAnimationComplete={() => setSettled(true)}
			style={{ overflow: settled ? 'visible' : 'hidden' }}
		>
			{children}
		</motion.div>
	);
}

export function RailEventRow(props: RailEventRowProps) {
	// A row for an event that happened moments ago is a live arrival: it
	// pushes in and glows briefly (`animate-arrive`). Decided once, at mount,
	// so history loads and re-renders stay still.
	const [arrived] = useState(() => Date.now() - props.ev.tsMs < ARRIVAL_WINDOW_MS);
	const row = <RailEventRowContent {...props} arrived={arrived} />;
	return arrived ? <ArrivalReveal>{row}</ArrivalReveal> : row;
}

function RailEventRowContent({
	arrived,
	ev,
	actorName,
	groupCount = 1,
	groupTitle,
	hideActor = false,
	expanded = false,
	onToggleExpand,
	onAction,
	onNavigate,
}: RailEventRowProps & { arrived: boolean }) {
	const compact = isCompact(ev);
	const failing = isFailureSeverity(ev.severity) && !ev.acknowledged;
	const actions = inlineActionsFor(ev);
	const who = hideActor ? undefined : actorName;
	const text = groupTitle ?? railTitle(ev);
	const sentence = (
		<>
			{who && (
				<>
					<span className="text-foreground font-medium">{who}</span>
					<span aria-hidden="true"> · </span>
				</>
			)}
			{text}
		</>
	);
	// The conflict "why" hint (if any) rides in the detail line alongside the
	// event's own meta, so a `catalog.update_conflicts_overlay` row explains the
	// digest drift without a new layout element.
	const detail = [ev.meta, conflictHint(ev)].filter(Boolean).join(' · ');
	const grouped = groupCount > 1;
	const dest = onNavigate && !grouped ? primaryDestinationFor(ev) : null;
	// The whole row is one control — it opens the detail, or, for a folded
	// group, unfolds it — but its verbs (inline actions) must not nest inside
	// that control. A stretched overlay carries the click; the verbs sit above
	// it (`relative z-10`) as siblings.
	const overlayClass =
		'focus-visible:ring-ring absolute inset-0 rounded-r focus-visible:ring-2 focus-visible:outline-none';
	const overlay = grouped ? (
		<button
			type="button"
			onClick={onToggleExpand}
			aria-expanded={expanded}
			aria-label={`${actorName ? `${actorName}: ` : ''}${groupTitle ?? ev.title}. ${
				expanded ? 'Collapse group' : `Expand group of ${groupCount}`
			}.`}
			className={overlayClass}
		/>
	) : dest ? (
		<button
			type="button"
			role="link"
			onClick={() => onNavigate?.(dest)}
			title={ev.title}
			aria-label={`${actorName ? `${actorName}: ` : ''}${ev.title}. Open detail.`}
			className={overlayClass}
		/>
	) : null;

	// Decorative: the overlay is the control. The count is in the group's
	// words when it has them ("6 calls succeeded"), else a ×N chip — re-keyed
	// on the count so a live arrival that joins the group pops it.
	const groupToggle = grouped ? (
		<span
			aria-hidden="true"
			className="text-muted-foreground inline-flex shrink-0 items-center text-[10px] font-semibold tabular-nums"
		>
			{!groupTitle && (
				<span key={groupCount} className="animate-pop inline-block">
					×{groupCount}
				</span>
			)}
			<ChevronDown
				className={cn(
					'h-3 w-3 transition-transform duration-200',
					expanded && 'rotate-180',
				)}
			/>
		</span>
	) : null;

	if (compact) {
		return (
			<div
				data-rail-row
				className={cn(
					'relative flex items-center gap-2 rounded-r border-l-2 px-2 py-1',
					stripeClass(ev),
					arrived && 'animate-arrive',
					ev.acknowledged && 'opacity-55',
					(dest || grouped) && 'hover:bg-background/50 cursor-pointer',
				)}
			>
				{overlay}
				<StreamEventIcon ev={ev} />
				<span className="text-muted-foreground min-w-0 flex-1 truncate text-xs">
					{sentence}
				</span>
				{groupToggle}
				<TimeStamp tsMs={ev.tsMs} />
			</div>
		);
	}

	return (
		<div
			data-rail-row
			className={cn(
				'relative flex gap-2 rounded-r border-l-2 px-2 py-1.5',
				stripeClass(ev),
				arrived && 'animate-arrive',
				failing && 'bg-danger/5',
				(dest || grouped) && 'hover:bg-background/50 cursor-pointer',
			)}
		>
			{overlay}
			<StreamEventIcon ev={ev} className="mt-0.5" />
			<div className="min-w-0 flex-1">
				<div className="flex items-start gap-1.5">
					<p
						className={cn(
							'line-clamp-2 min-w-0 flex-1 text-xs',
							failing ? 'text-foreground' : 'text-muted-foreground',
						)}
					>
						{sentence}
					</p>
					{groupToggle}
					<TimeStamp tsMs={ev.tsMs} className="mt-px" />
				</div>
				{detail && (
					<p className="text-muted-foreground mt-0.5 truncate text-[11px]">{detail}</p>
				)}
				{actions.length > 0 && onAction && (
					<div className="relative z-10 mt-1.5 flex flex-wrap gap-1">
						{actions.map((action) => (
							<Button
								key={action.kind}
								variant={action.kind === 'acknowledge' ? 'primary' : 'ghost'}
								size="sm"
								onClick={() => onAction?.(ev.id, action)}
								className="h-6 px-2 text-[11px]"
							>
								{action.label}
							</Button>
						))}
					</div>
				)}
			</div>
		</div>
	);
}
