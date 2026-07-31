import { AuditTrailCard } from '@/shared/ui';
import { useToolkitAudit } from '@/modules/toolkits/api';

/**
 * ToolkitAuditPanel — the toolkit console's "Recent changes" card: a thin,
 * toolkit-scoped wrapper over the shared {@link AuditTrailCard}. Surfaces the
 * toolkit-level events (create / update / suspend / restore) tagged
 * `target_type=toolkit`. Key- and binding-level sub-events live in the
 * org-wide Audit lens (Monitor module) — the `/audit` endpoint only filters
 * by a single `target_id`, so we don't duplicate those here. Requires
 * `org:admin`; for non-admins the repository maps 401/403 to an empty list
 * and we render the graceful "no entries" state.
 */
export interface ToolkitAuditPanelProps {
	toolkitId: string;
	poll?: boolean;
}

export function ToolkitAuditPanel({ toolkitId, poll = true }: ToolkitAuditPanelProps) {
	const { data: entries = [], isLoading, isError } = useToolkitAudit(toolkitId, { poll });

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
			caption="Toolkit-level events · admin only"
			errorMessage="Failed to load the activity log."
			emptyMessage="No recorded activity for this toolkit yet. The full audit trail lives in Monitor → Audit."
		/>
	);
}
