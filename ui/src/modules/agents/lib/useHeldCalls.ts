/**
 * The held calls the Agents page shows, per agent. One read of the shared
 * pending cache (the inbox, the bell and the nav badge read the same slice), so
 * the strip's chips, the card's status and the waiting card always agree with
 * each other and with them.
 */
import { useMemo } from 'react';
import {
	groupApprovalsByAgent,
	useAgentPendingApprovals,
	useCanDecideApprovals,
	usePendingApprovals,
	type HeldCall,
} from '@/shared/approvals';

const NONE: ReadonlyMap<string, readonly HeldCall[]> = new Map();

/**
 * Each agent's pending held calls, oldest first; an agent with none is absent.
 * Empty, with no request made, until the viewer is known (see
 * `useCanDecideApprovals`). The shared list reads a bounded number of pages;
 * past them an agent's calls can be missing from it, so while it is cut short
 * the agent on screen (`focusAgentId`) is read on its own.
 */
export function useHeldCallsByAgent(
	focusAgentId?: string | null,
): ReadonlyMap<string, readonly HeldCall[]> {
	const canDecide = useCanDecideApprovals();
	const { data } = usePendingApprovals({ enabled: canDecide });
	const truncated = data?.truncated ?? false;
	const own = useAgentPendingApprovals(focusAgentId, canDecide && truncated);
	return useMemo(() => {
		if (!canDecide || !data) return NONE;
		const byAgent = new Map(
			groupApprovalsByAgent(data.approvals).map((g) => [g.agentId, g.approvals]),
		);
		if (focusAgentId && truncated && own.data) {
			if (own.data.length > 0) byAgent.set(focusAgentId, own.data);
			else byAgent.delete(focusAgentId);
		}
		return byAgent;
	}, [canDecide, data, focusAgentId, truncated, own.data]);
}
