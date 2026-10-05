/**
 * ActivityStreamPanel — the live activity stream mounted as a card, for the
 * page that hides the docked rail (Monitor's side panel). It IS the rail's feed (`RailFeed`, same rows, grouping and inline
 * verbs), so the stream reads the same wherever it's docked.
 *
 * Reads the shell's one live stream (`useAgentStreamOptional`) unfiltered — the
 * server already limits it to events the caller can see, and the rail's
 * per-agent lens is a rail concern. The built-in All / Failures control
 * is the same shared Failures only choice the rail shows; hosts add their own
 * header actions and footer.
 *
 * The panel carries the shared `activity-stream` view-transition name, so
 * navigating from a rail page to Monitor morphs the rail into the
 * card (and back) instead of cutting.
 */
import { useMemo, type ReactNode } from 'react';
import { useNavigate } from 'react-router';
import { Card, CardBody, CardHeader, CardTitle, SegmentedToggle } from '@/shared/ui';
import { LiveDot, type LiveDotTone } from '@/shared/app/rail/LiveDot';
import { RailFeed, type RailFeedProps } from '@/shared/app/rail/RailFeed';
import { activityStreamVtStyle } from '@/shared/app/viewTransitions';
import { useActorDirectory } from '@/shared/hooks';
import { useAgentStreamOptional } from '@/shared/lib';
import { cn } from '@/shared/lib/utils';

type StreamEvent = RailFeedProps['events'][number];
type InlineAction = Parameters<NonNullable<RailFeedProps['onAction']>>[1];
type Lens = 'all' | 'failures';

const LENS_OPTIONS: { value: Lens; label: string }[] = [
	{ value: 'all', label: 'All' },
	{ value: 'failures', label: 'Failures' },
];

type Status = NonNullable<ReturnType<typeof useAgentStreamOptional>>['status'];

const STATUS_COPY: Record<Status, { label: string; tone: LiveDotTone }> = {
	live: { label: 'Live', tone: 'live' },
	connecting: { label: 'Connecting…', tone: 'connecting' },
	error: { label: 'Reconnecting…', tone: 'warning' },
	// Not "Paused": that word is the rail's pause, a different thing.
	idle: { label: 'Offline', tone: 'idle' },
};

export interface ActivityStreamPanelProps {
	title?: string;
	/** Extra header controls, after the All / Failures toggle. */
	actions?: ReactNode;
	footer?: ReactNode;
	/** Height cap for the scrolling log (a Tailwind class). */
	logClassName?: string;
	className?: string;
}

export function ActivityStreamPanel({
	title = 'Live activity',
	actions,
	footer,
	logClassName = 'max-h-[520px]',
	className,
}: ActivityStreamPanelProps) {
	const stream = useAgentStreamOptional();
	const navigate = useNavigate();
	const directory = useActorDirectory();
	// Failures only is the rail's own toggle (shared via the provider), so the
	// stream keeps the same filter as it morphs between rail and panel.
	const failuresOnly = stream?.failuresOnly ?? false;
	const lens: Lens = failuresOnly ? 'failures' : 'all';
	const filters = useMemo(() => ({ failuresOnly }), [failuresOnly]);

	const status = STATUS_COPY[stream?.status ?? 'idle'];
	const events = stream?.events ?? [];

	function resolveActor(ev: StreamEvent): string | undefined {
		return ev.actorId ? directory.resolve(ev.actorId) : undefined;
	}

	function handleAction(eventId: string, action: InlineAction) {
		// Pure navigation actions: navigate, skip the RPC.
		if (action.href && !action.acknowledges) {
			const ev = events.find((e) => e.id === eventId);
			const target = ev ? action.href(ev) : null;
			if (target) navigate(target);
			return;
		}
		if (action.acknowledges) void stream?.acknowledge(eventId);
	}

	return (
		<section aria-label={title} className={className} style={activityStreamVtStyle}>
			<Card className="flex h-full flex-col">
				<CardHeader className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2 py-3">
					<div className="flex min-w-0 items-center gap-2">
						<CardTitle as="h2" className="text-base">
							{title}
						</CardTitle>
						<span
							className="text-muted-foreground flex items-center gap-1.5 text-xs"
							role="status"
						>
							<LiveDot tone={status.tone} />
							{status.label}
						</span>
					</div>
					<div className="flex items-center gap-1.5">
						<SegmentedToggle
							options={LENS_OPTIONS}
							value={lens}
							onChange={(next) => stream?.setFailuresOnly(next === 'failures')}
							ariaLabel="Filter activity"
						/>
						{actions}
					</div>
				</CardHeader>
				<CardBody className="bg-muted/30 min-h-0 flex-1 px-2 py-2">
					<div
						className={cn('overflow-y-auto pr-0.5', logClassName)}
						role="log"
						aria-live="polite"
						aria-relevant="additions"
						aria-label="Activity feed"
					>
						{stream ? (
							<RailFeed
								events={events}
								filters={filters}
								resolveActor={resolveActor}
								onAction={handleAction}
								onNavigate={(href) => navigate(href)}
							/>
						) : (
							<p className="text-muted-foreground px-3 py-6 text-center text-xs">
								Live activity isn't available here.
							</p>
						)}
					</div>
				</CardBody>
				{footer}
			</Card>
		</section>
	);
}
