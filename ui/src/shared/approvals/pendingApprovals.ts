/**
 * Pending execution approvals, collapsed per agent — the one grouping the
 * attention inbox and the Agents page "Waiting for you" section share, so the
 * two never disagree on what an agent is waiting for.
 */
import type { ExecutionApprovalResponse } from '@/shared/api';
import { JOBS_WRITE, useCanAccess } from '@/shared/auth/useCanAccess';

export interface AgentPendingApprovals {
	agentId: string;
	/** Oldest first; never empty. */
	approvals: ExecutionApprovalResponse[];
	/** When the agent's oldest held call was made. */
	since: string;
}

/**
 * Group pending approvals by agent, each agent's oldest first, the agents
 * ordered by their oldest held call (the one waiting longest leads).
 */
export function groupApprovalsByAgent(
	approvals: readonly ExecutionApprovalResponse[],
): AgentPendingApprovals[] {
	const byAgent = new Map<string, ExecutionApprovalResponse[]>();
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

/** "POST /v1/charges on api.stripe.com/payments" — what one held call does. */
export function describeHeldCall(approval: ExecutionApprovalResponse): string {
	return `${approval.method} ${approval.path} on ${approval.api_vendor}/${approval.api_name}`;
}

/** "a call to api.stripe.com/payments", "3 calls" — a group's headline. */
export function summariseHeldCalls(approvals: readonly ExecutionApprovalResponse[]): string {
	if (approvals.length === 1) {
		return `a call to ${approvals[0].api_vendor}/${approvals[0].api_name}`;
	}
	return `${approvals.length} calls`;
}

/**
 * Whether the viewer may decide held calls: deciding takes `jobs:write` (or
 * `org:admin`), and the list the signals read is already scoped server-side to
 * the approvals the viewer reviews (the agents they own, or every agent for an
 * org admin). The waiting signals show only to deciders: a held call the
 * viewer can see but cannot decide is not waiting for them. A UI gate; the
 * server enforces.
 */
export function useCanDecideApprovals(): boolean {
	return useCanAccess(JOBS_WRITE);
}
