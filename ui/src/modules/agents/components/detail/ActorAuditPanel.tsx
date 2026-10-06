/**
 * ActorAuditPanel — the "Recent changes" section of the dock's Activity
 * sheet: a thin, actor-scoped wrapper over the shared {@link AuditTrailCard},
 * so "Recent changes" reads identically on every surface. Surfaces the lifecycle
 * trail recorded against this actor as the TARGET (register, approve/deny,
 * disable/enable, key rotation, binding grant/revoke).
 *
 * Requires `audit:read` (or `org:admin`): anyone else gets no request and a
 * quiet note that the history is not visible to them, never an error.
 */
import { AuditTrailCard } from '@/shared/ui';
import { AUDIT_READ, useCanAccess } from '@/shared/auth';
import { useActorAudit } from '@/modules/agents/api';

export interface ActorAuditPanelProps {
	actorId: string;
}

export function ActorAuditPanel({ actorId }: ActorAuditPanelProps) {
	const canReadAudit = useCanAccess(AUDIT_READ);
	const { data: entries = [], isLoading, isError } = useActorAudit(actorId);

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
			emptyMessage={
				canReadAudit
					? 'No recorded changes for this agent yet. The full audit trail lives in Monitor → Audit.'
					: "This agent's change history needs audit access."
			}
		/>
	);
}
