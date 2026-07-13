import { useNavigate } from 'react-router-dom';
import { Bell, BellOff } from 'lucide-react';
import {
	Card,
	CardHeader,
	CardBody,
	CardTitle,
	Badge,
	EmptyState,
	ErrorAlert,
	SkeletonRows,
	ActorLabel,
} from '@/shared/ui';
import {
	useActionableEvents,
	formatApproxCount,
	EventSeverity,
	type EventResponse,
} from '@/modules/dashboard/api';
import { ROUTES } from '@/shared/app/routes';
import { eventSeverityIcon } from '@/shared/lib';
import { timeAgo, cn } from '@/shared/lib/utils';
import { CardRow, CardHeaderIcon } from '@/modules/dashboard/components/CardRow';

/** The leading medallion tint per severity — the one place colour is allowed to
 * carry meaning in this card (info = muted, warning = amber, error/critical =
 * red). Colour treatment is intentionally per-surface; the ICON comes from the
 * shared `eventSeverityIcon` so it matches Monitor's Events tab exactly. */
const SEVERITY_MEDALLION: Record<EventSeverity, string> = {
	[EventSeverity.INFO]: 'bg-muted text-muted-foreground ring-border',
	[EventSeverity.WARNING]: 'bg-warning/12 text-warning ring-warning/20',
	[EventSeverity.ERROR]: 'bg-danger/12 text-danger ring-danger/20',
	[EventSeverity.CRITICAL]: 'bg-danger/12 text-danger ring-danger/20',
};

function severityMedallion(severity: EventResponse['severity']): string {
	return SEVERITY_MEDALLION[severity] ?? SEVERITY_MEDALLION[EventSeverity.INFO];
}

/**
 * Alerts needing attention. Composed from `GET /events?requires_action=true`.
 * Links into Monitor's Events tab (`?tab=events`) where the full event log +
 * acknowledge flow live — Dashboard just raises the flag.
 */
export function AlertsCard() {
	const { data, isLoading, isError, error } = useActionableEvents();
	const navigate = useNavigate();
	// Built at render time (not module scope) so the `ROUTES` read doesn't hit a
	// temporal dead zone under the `@/shared/app` barrel's import cycle. Both
	// this card's rows AND the "Needs attention" overview tile point here so the
	// dashboard's two paths into events stay consistent.
	const eventsHref = `${ROUTES.monitor}?tab=events`;

	return (
		<Card>
			<CardHeader className="flex items-center justify-between gap-3">
				<CardTitle as="h2" className="flex items-center gap-2.5">
					<CardHeaderIcon>
						<Bell className="h-4 w-4" aria-hidden="true" />
					</CardHeaderIcon>
					Needs attention
				</CardTitle>
				{data && data.count.value > 0 && (
					<Badge variant="danger" dot>
						{formatApproxCount(data.count)}
					</Badge>
				)}
			</CardHeader>
			<CardBody className="px-0 py-2">
				{isLoading ? (
					<div className="px-5 py-2">
						<SkeletonRows rows={3} />
					</div>
				) : isError ? (
					<div className="px-5 py-2">
						<ErrorAlert message={error ?? 'Failed to load alerts.'} />
					</div>
				) : !data || data.events.length === 0 ? (
					<div className="px-5 py-2">
						<EmptyState
							icon={<BellOff className="h-7 w-7" aria-hidden="true" />}
							title="Nothing needs attention"
							description="Events that require a human decision will show up here."
						/>
					</div>
				) : (
					<ul className="divide-border/70 divide-y">
						{data.events.slice(0, 10).map((event) => {
							const SeverityIcon = eventSeverityIcon(event.severity);
							return (
								<li key={event.event_id}>
									<CardRow
										onClick={() => navigate(eventsHref)}
										action="View"
										aria-label={`View ${event.severity} event: ${event.summary}`}
										leading={
											<span
												className={cn(
													'flex h-7 w-7 shrink-0 items-center justify-center rounded-lg ring-1',
													severityMedallion(event.severity),
												)}
												title={event.severity}
											>
												<SeverityIcon
													className="h-3.5 w-3.5"
													aria-hidden="true"
												/>
											</span>
										}
										title={event.summary}
										subtitle={
											event.detail || event.actor_id ? (
												<>
													{event.detail}
													{event.detail && event.actor_id ? ' · ' : ''}
													{event.actor_id && (
														<ActorLabel
															actorId={event.actor_id}
															actorType={event.actor_type}
														/>
													)}
												</>
											) : undefined
										}
										meta={
											<time
												dateTime={event.created_at}
												title={new Date(event.created_at).toLocaleString()}
											>
												{timeAgo(event.created_at)}
											</time>
										}
									/>
								</li>
							);
						})}
					</ul>
				)}
			</CardBody>
		</Card>
	);
}
