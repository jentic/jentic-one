/**
 * ActorAuditPanel — the "Recent changes" card on the agent / service-account
 * detail Overview tab: a thin, actor-scoped wrapper over the shared
 * {@link AuditTrailCard} (the same card the toolkit console renders, so
 * "Recent changes" reads identically across consoles). Surfaces the lifecycle
 * trail recorded against this actor as the TARGET (register, approve/deny,
 * disable/enable, key rotation, toolkit grant/revoke).
 *
 * Requires `org:admin` — the repository maps 401/403 to an empty list, so
 * non-admins see the graceful "no entries" state rather than an error.
 */
import { AuditTrailCard } from '@/shared/ui';
import { useActorAudit } from '@/modules/agents/api';

export interface ActorAuditPanelProps {
	actorKind: 'agent' | 'service-account';
	actorId: string;
}

export function ActorAuditPanel({ actorKind, actorId }: ActorAuditPanelProps) {
	const { data: entries = [], isLoading, isError } = useActorAudit(actorKind, actorId);

	return (
		<AuditTrailCard
			entries={entries.map((entry) => ({
				id: entry.id,
				action: entry.action,
				actorId: entry.actor_id,
				actorType: entry.actor_type,
				reason: entry.reason,
				occurredAt: entry.occurred_at,
			}))}
			isLoading={isLoading}
			isError={isError}
			caption="Lifecycle events · admin only"
			errorMessage="Failed to load the audit log."
			emptyMessage={`No recorded changes for this ${
				actorKind === 'agent' ? 'agent' : 'service account'
			} yet. The full audit trail lives in Monitor → Audit.`}
		/>
	);
}
