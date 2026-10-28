/**
 * Pending held executions, collapsed per agent — the one grouping the bell,
 * the rail's Inbox and the waiting card on an agent share, so they never
 * disagree on what an agent is waiting for.
 */
import type { HeldExecutionResponse } from '@/shared/api';
import { usePermissionsKnown } from '@/shared/auth/useCanAccess';

export interface AgentPendingApprovals {
	agentId: string;
	/** Oldest first; never empty. */
	approvals: HeldExecutionResponse[];
	/** When the agent's oldest held call was made. */
	since: string;
}

/**
 * Group pending approvals by agent, each agent's oldest first, the agents
 * ordered by their oldest held call (the one waiting longest leads).
 */
export function groupApprovalsByAgent(
	approvals: readonly HeldExecutionResponse[],
): AgentPendingApprovals[] {
	const byAgent = new Map<string, HeldExecutionResponse[]>();
	for (const approval of approvals) {
		const list = byAgent.get(approval.agent_id) ?? [];
		list.push(approval);
		byAgent.set(approval.agent_id, list);
	}
	return [...byAgent.entries()]
		.map(([agentId, list]) => {
			const sorted = [...list].sort(
				(a, b) => Date.parse(a.created_at) - Date.parse(b.created_at),
			);
			return { agentId, approvals: sorted, since: sorted[0].created_at };
		})
		.sort((a, b) => Date.parse(a.since) - Date.parse(b.since));
}

/**
 * Whether the waiting signals read held calls for the viewer: once the viewer
 * is known, whatever their permissions. The server scopes the list to the held
 * executions the viewer may see (the agents they own, or every agent for an
 * org admin), and those are exactly the ones they may decide, so no permission
 * gates the read or the verbs. The server still enforces each decision.
 */
export function useCanDecideApprovals(): boolean {
	return usePermissionsKnown();
}
