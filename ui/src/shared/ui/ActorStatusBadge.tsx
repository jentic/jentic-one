/**
 * Actor (agent / service account) lifecycle status — the SINGLE source of truth
 * for the status vocabulary and its visual mapping, shared across every module
 * that renders an actor's status (agents roster/detail, the service-account detail
 * "Bound Agents" card, the link-agent picker, …).
 *
 * Lives in `shared/` so sibling modules can render an actor status identically
 * without importing each other (module-boundary rule). Never re-derive
 * status→label or status→variant locally — render through `ActorStatusBadge`
 * (or read these maps) so the language can't drift between pages.
 *
 * The values mirror the backend's `ActorStatus` enum (`shared/models/actors.py`).
 */
import type { HTMLAttributes } from 'react';
import { Archive, CircleCheck, Clock, PowerOff, XCircle } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
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
 * Glyph per status, for the places where a coloured dot is too little to tell
 * the states apart — a tab in a rail, a notice's icon chip. Colour alone fails
 * anyone who can't see it AND anyone who hasn't learned our palette, so the
 * shape carries the meaning: a decision waiting, a refusal, a switch turned
 * off, a retirement.
 *
 * `active` has one too, but it is the quiet default almost everywhere and most
 * callers only reach in here for the non-active states.
 */
export const STATUS_ICON: Record<ActorStatus, LucideIcon> = {
	pending: Clock,
	active: CircleCheck,
	rejected: XCircle,
	disabled: PowerOff,
	archived: Archive,
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
	...props
}: {
	status: ActorStatus | string;
	className?: string;
} & HTMLAttributes<HTMLSpanElement>) {
	const s = toActorStatus(status);
	return (
		<Badge variant={STATUS_BADGE_VARIANT[s]} className={className} {...props}>
			{STATUS_LABELS[s]}
		</Badge>
	);
}
