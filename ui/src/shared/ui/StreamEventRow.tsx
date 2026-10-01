/**
 * StreamEventRow — one live-stream event as a list row: its title and how long
 * ago it happened, linking to the event's primary destination when it has one
 * (`primaryDestinationFor`). Shared by the Library's docked workspace panel
 * ("Recent changes") and the API hub ("Recent activity").
 */
import { AppLink } from '@/shared/ui/AppLink';
import { primaryDestinationFor, type StreamEvent } from '@/shared/lib/agentStream';
import { cn, timeAgo } from '@/shared/lib/utils';

export function StreamEventRow({
	ev,
	size = 'sm',
	className,
}: {
	ev: StreamEvent;
	/** `sm` for a dense side panel, `md` for a card list. */
	size?: 'sm' | 'md';
	className?: string;
}) {
	const href = primaryDestinationFor(ev);
	const row = cn(
		'flex items-center gap-2 px-1.5 py-1',
		size === 'sm' ? 'text-xs' : 'text-sm',
		className,
	);
	const body = (
		<>
			<span className="text-foreground min-w-0 flex-1 truncate">{ev.title}</span>
			<span className="text-muted-foreground shrink-0 text-[11px]">{timeAgo(ev.tsMs)}</span>
		</>
	);
	return (
		<li>
			{href ? (
				<AppLink href={href} className={cn(row, 'hover:bg-muted/60 rounded-md')}>
					{body}
				</AppLink>
			) : (
				<div className={row}>{body}</div>
			)}
		</li>
	);
}
