/**
 * Actor (agent / service account) lifecycle status — the SINGLE source of truth
 * for the status vocabulary and its visual mapping, shared across every module
 * that renders an actor's status (agents roster/detail, the toolkit detail
 * "Bound Agents" card, the link-agent picker, …).
 *
 * Lives in `shared/` so sibling modules can render an actor status identically
 * without importing each other (module-boundary rule). Never re-derive
 * status→label or status→variant locally — render through `ActorStatusBadge`
 * (or read these maps) so the language can't drift between pages.
 *
 * The values mirror the backend's `ActorStatus` enum (`shared/models/actors.py`).
 */
import { Badge } from '@/shared/ui/Badge';
import type { Variant as BadgeVariant } from '@/shared/ui/Badge';

/** Mirrors the backend `ActorStatus` (pending|active|rejected|disabled|archived). */
export type ActorStatus = 'pending' | 'active' | 'rejected' | 'disabled' | 'archived';

export const ACTOR_STATUSES: ActorStatus[] = [
	'pending',
	'active',
	'rejected',
	'disabled',
	'archived',
];

/** Human label per status. */
export const STATUS_LABELS: Record<ActorStatus, string> = {
	pending: 'Pending',
	active: 'Active',
	rejected: 'Rejected',
	disabled: 'Disabled',
	archived: 'Archived',
};

/** Badge variant per status (maps onto `shared/ui` Badge variants). */
export const STATUS_BADGE_VARIANT: Record<ActorStatus, BadgeVariant> = {
	pending: 'pending',
	active: 'success',
	rejected: 'danger',
	disabled: 'warning',
	archived: 'default',
};

/** Status indicator dot colour (Tailwind bg-*) per status. */
export const STATUS_DOT: Record<ActorStatus, string> = {
	pending: 'bg-accent-orange',
	active: 'bg-success',
	rejected: 'bg-danger',
	disabled: 'bg-warning',
	archived: 'bg-muted-foreground/40',
};

/**
 * Narrow a backend free-string status into our union. Unknown statuses map to
 * `archived` — a terminal state that exposes no lifecycle actions — so an
 * unrecognized value can never surface approve/deny on something we don't model.
 */
export function toActorStatus(status: string): ActorStatus {
	return (ACTOR_STATUSES as string[]).includes(status) ? (status as ActorStatus) : 'archived';
}

/** Status pill for an actor (agent / service account) using its lifecycle status. */
export function ActorStatusBadge({
	status,
	className,
}: {
	status: ActorStatus | string;
	className?: string;
}) {
	const s = toActorStatus(status);
	return (
		<Badge variant={STATUS_BADGE_VARIANT[s]} className={className}>
			{STATUS_LABELS[s]}
		</Badge>
	);
}
