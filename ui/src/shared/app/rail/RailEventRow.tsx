/**
 * RailEventRow — one platform event in the Activity feed, in plain language:
 * "<who> · <what happened>" plus the time.
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
import { useState } from 'react';
import { ChevronDown, ChevronUp } from 'lucide-react';
import { Button } from '@/shared/ui/Button';
import { Tooltip } from '@/shared/ui';
import { StreamEventIcon } from '@/shared/app/rail/StreamEventIcon';
import {
	formatStreamTime,
	formatStreamDateTimeParts,
	conflictHint,
	inlineActionsFor,
	isFailureSeverity,
	primaryDestinationFor,
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
	return (
		<Tooltip content={<TimeTooltipContent tsMs={tsMs} />} className={cn('shrink-0', className)}>
			<span className="text-muted-foreground font-mono text-[10px] tabular-nums">
				{formatStreamTime(tsMs)}
			</span>
		</Tooltip>
	);
}

export function RailEventRow({
	ev,
	actorName,
	groupCount = 1,
	expanded = false,
	onToggleExpand,
	onAction,
	onNavigate,
}: RailEventRowProps) {
	// A row for an event that happened moments ago is a live arrival: it
	// slides in with a brief glow (`animate-arrive`). Decided once, at mount,
	// so history loads and re-renders stay still.
	const [arrived] = useState(() => Date.now() - ev.tsMs < ARRIVAL_WINDOW_MS);
	const compact = isCompact(ev);
	const failing = isFailureSeverity(ev.severity) && !ev.acknowledged;
	const actions = inlineActionsFor(ev);
	const who = actorName;
	const sentence = (
		<>
			{who && (
				<>
					<span className="text-foreground font-medium">{who}</span>
					<span aria-hidden="true"> · </span>
				</>
			)}
			{ev.title}
		</>
	);
	// The conflict "why" hint (if any) rides in the detail line alongside the
	// event's own meta, so a `catalog.update_conflicts_overlay` row explains the
	// digest drift without a new layout element.
	const detail = [ev.meta, conflictHint(ev)].filter(Boolean).join(' · ');
	const dest = onNavigate ? primaryDestinationFor(ev) : null;
	// The whole row opens the detail, but its verbs (group toggle, inline
	// actions) must not nest inside that control. A stretched overlay carries
	// the navigation; the verbs sit above it (`relative z-10`) as siblings.
	const overlay = dest ? (
		<button
			type="button"
			role="link"
			onClick={() => onNavigate?.(dest)}
			title={`Open ${dest}`}
			aria-label={`${who ? `${who}: ` : ''}${ev.title}. Open detail.`}
			className="focus-visible:ring-ring absolute inset-0 rounded-r focus-visible:ring-2 focus-visible:outline-none"
		/>
	) : null;

	const groupToggle =
		groupCount > 1 ? (
			<button
				type="button"
				onClick={onToggleExpand}
				className="text-muted-foreground hover:text-foreground relative z-10 shrink-0 rounded-full px-1 font-mono text-[10px] font-semibold tabular-nums"
				aria-label={expanded ? 'Collapse group' : `Expand group of ${groupCount}`}
			>
				×{groupCount}
				{expanded ? (
					<ChevronUp className="ml-0.5 inline h-2.5 w-2.5" />
				) : (
					<ChevronDown className="ml-0.5 inline h-2.5 w-2.5" />
				)}
			</button>
		) : null;

	if (compact) {
		return (
			<div
				className={cn(
					'relative flex items-center gap-2 rounded-r border-l-2 px-2 py-1',
					stripeClass(ev),
					arrived && 'animate-arrive',
					ev.acknowledged && 'opacity-55',
					dest && 'hover:bg-background/50 cursor-pointer',
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
			className={cn(
				'relative flex gap-2 rounded-r border-l-2 px-2 py-1.5',
				stripeClass(ev),
				arrived && 'animate-arrive',
				failing && 'bg-danger/5',
				dest && 'hover:bg-background/50 cursor-pointer',
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
