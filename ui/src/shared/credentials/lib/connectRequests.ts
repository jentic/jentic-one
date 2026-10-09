/**
 * Open connect requests, collapsed per agent — the one grouping the attention
 * inbox and the Agents page "Waiting for you" section share, so the two never
 * disagree on what an agent is waiting for.
 */
import type { ConnectSessionSummaryResponse } from '@/shared/api';
import {
	AGENTS_WRITE,
	CREDENTIALS_READ,
	CREDENTIALS_WRITE,
	OWNER_CREDENTIALS_READ,
	useCanAccess,
} from '@/shared/auth';

export interface AgentConnectRequests {
	agentId: string;
	/** Oldest first; never empty. */
	sessions: ConnectSessionSummaryResponse[];
	/** When the agent's oldest open request was made. */
	since: string;
}

/**
 * Group open sessions by their target agent, keeping each agent's sessions
 * oldest first and ordering the agents by their oldest request (the one waiting
 * longest leads). Sessions with no agent are skipped: nobody is blocked on them.
 */
export function groupConnectRequestsByAgent(
	sessions: readonly ConnectSessionSummaryResponse[],
): AgentConnectRequests[] {
	const byAgent = new Map<string, ConnectSessionSummaryResponse[]>();
	for (const session of sessions) {
		if (!session.agent_id) continue;
		const list = byAgent.get(session.agent_id) ?? [];
		list.push(session);
		byAgent.set(session.agent_id, list);
	}
	return [...byAgent.entries()]
		.map(([agentId, list]) => {
			const sorted = [...list].sort(
				(a, b) => Date.parse(a.created_at) - Date.parse(b.created_at),
			);
			return { agentId, sessions: sorted, since: sorted[0].created_at };
		})
		.sort((a, b) => Date.parse(a.since) - Date.parse(b.since));
}

/** "GitHub", "GitHub and Slack", "GitHub, Slack and 2 more" — distinct names only. */
export function summariseConnectTargets(
	sessions: readonly ConnectSessionSummaryResponse[],
): string {
	const names = [...new Set(sessions.map((s) => s.vendor_display_name))];
	if (names.length <= 1) return names[0] ?? '';
	if (names.length === 2) return `${names[0]} and ${names[1]}`;
	const rest = names.length - 2;
	return `${names[0]}, ${names[1]} and ${rest} more`;
}

/**
 * Whether the viewer may approve an agent's connect request: `org:admin`, or
 * holding both `credentials:write` and `agents:write` (approving writes the
 * agent's binding), plus a credentials read to list the requests. The waiting
 * signals show only to approvers: a request the viewer may list but cannot
 * open is not waiting for them. A UI gate; the server enforces.
 */
export function useCanApproveConnectRequests(): boolean {
	const canRead = useCanAccess(CREDENTIALS_READ, OWNER_CREDENTIALS_READ);
	const canWriteCredentials = useCanAccess(CREDENTIALS_WRITE);
	const canWriteAgents = useCanAccess(AGENTS_WRITE);
	return canRead && canWriteCredentials && canWriteAgents;
}
