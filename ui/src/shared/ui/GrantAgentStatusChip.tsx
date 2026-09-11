/**
 * GrantAgentStatusChip — the dormant-connection marker on a consent→agent
 * grant row (#1345). Disabling an agent keeps its grants `active` (disable is
 * reversible, so the standing consent survives), but no token resolves while
 * the agent is non-active and client-level active-grant counts exclude the
 * row. The API annotates each grant with `agent_status` so listings can tell
 * a working connection from a dormant one; this chip renders that annotation.
 *
 * Lives in `shared/` because two sibling modules render grant rows (the
 * agents module's "Connected clients" card and the settings module's
 * client-detail Grants panel) and modules never import each other. The
 * agent-status language is not re-derived: label text comes from the shared
 * actor-status vocabulary (`STATUS_LABELS` / `toActorStatus` — the
 * status-and-filter rule).
 */
import { STATUS_LABELS, toActorStatus } from '@/shared/ui/ActorStatusBadge';
import { Tooltip } from '@/shared/ui/Tooltip';

export interface GrantAgentStatusChipProps {
	/** The grant's own lifecycle status (`active` | `revoked`). */
	grantStatus: string;
	/** The bound agent's lifecycle status, when the API annotated the row. */
	agentStatus?: string | null;
}

/**
 * Muted chip ("Agent disabled", "Agent archived", …) on an active grant whose
 * agent is non-active, with a tooltip explaining the dormancy. Renders
 * nothing on a working connection, on a revoked row (its own Revoked badge
 * already explains it), or when the API omitted the annotation.
 */
export function GrantAgentStatusChip({ grantStatus, agentStatus }: GrantAgentStatusChipProps) {
	if (grantStatus !== 'active' || agentStatus == null) return null;
	const status = toActorStatus(agentStatus);
	if (status === 'active') return null;
	const label = STATUS_LABELS[status].toLowerCase();
	return (
		<Tooltip
			content={
				`This grant is dormant: no tokens are issued while the agent is ${label}, ` +
				'and active-connection counts exclude it.' +
				// Disable/Enable vocabulary: the reversible arm names its way back.
				(status === 'disabled' ? ' Enable the agent to restore the connection.' : '')
			}
		>
			<span className="bg-muted text-muted-foreground rounded-full px-2 py-0.5 font-mono text-xs">
				Agent {label}
			</span>
		</Tooltip>
	);
}
