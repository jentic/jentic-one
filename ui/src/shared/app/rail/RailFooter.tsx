/**
 * RailFooter — the way out to the full, filterable activity log in Monitor.
 * Opens Monitor's full log (`view=activity`; admins otherwise land on the
 * Overview) and carries the rail's actor lens along (Monitor's `actor_id` /
 * `actor_type` URL vocabulary) so the log opens on the same agent.
 */
import { ROUTES } from '@/shared/app/routes';
import { AppLink } from '@/shared/ui/AppLink';
import type { ActivityScope } from '@/shared/lib/agentStream';

export function monitorEventsHref(scope: ActivityScope): string {
	const params = new URLSearchParams({ view: 'activity' });
	if (scope) {
		params.set('actor_id', scope.actorId);
		params.set('actor_type', scope.actorType);
	}
	return `${ROUTES.monitor}?${params.toString()}`;
}

export type RailFooterProps = {
	scope: ActivityScope;
	/** Fired after the link is followed (the drawer closes itself). */
	onNavigate?: () => void;
};

export function RailFooter({ scope, onNavigate }: RailFooterProps) {
	return (
		<div className="border-border border-t px-3 py-2">
			<AppLink
				href={monitorEventsHref(scope)}
				onClick={onNavigate}
				className="text-muted-foreground hover:text-foreground text-xs font-medium"
			>
				Open in Monitor →
			</AppLink>
		</div>
	);
}
