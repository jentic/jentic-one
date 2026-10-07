/**
 * LiveDot — the activity stream's status light, one look wherever the stream
 * docks (rail header, collapsed strip, Monitor's Live activity panel).
 *
 *   live      green, with a slow soft ping — "breathing", not blinking
 *   failure   red; pops in when it first appears, then pings until seen
 *   warning   amber (reconnecting / paused)
 *   connecting  grey, pinging
 *   idle      grey, still
 *
 * Reduced motion keeps the colour and drops the ping (global reset).
 */
import { cn } from '@/shared/lib/utils';

export type LiveDotTone = 'live' | 'failure' | 'warning' | 'connecting' | 'idle';

const TONE: Record<LiveDotTone, { dot: string; ping: boolean }> = {
	live: { dot: 'bg-success', ping: true },
	failure: { dot: 'bg-danger', ping: true },
	warning: { dot: 'bg-warning', ping: false },
	connecting: { dot: 'bg-muted-foreground', ping: true },
	idle: { dot: 'bg-muted-foreground', ping: false },
};

export function LiveDot({
	tone,
	label,
	className,
}: {
	tone: LiveDotTone;
	/** Accessible name; without one the dot is decorative. */
	label?: string;
	className?: string;
}) {
	const t = TONE[tone];
	return (
		<span
			{...(label
				? { role: 'img', 'aria-label': label, title: label }
				: { 'aria-hidden': true })}
			className={cn('relative inline-flex h-2 w-2 shrink-0', className)}
		>
			{t.ping && (
				// Re-keyed per tone so a change of state restarts the ping from its
				// first beat instead of mid-cycle.
				<span
					key={tone}
					className={cn('animate-live-ping absolute inset-0 rounded-full', t.dot)}
				/>
			)}
			<span
				key={`dot-${tone}`}
				className={cn(
					'relative inline-flex h-2 w-2 rounded-full transition-colors duration-300',
					t.dot,
					tone === 'failure' && 'animate-pop',
				)}
			/>
		</span>
	);
}
