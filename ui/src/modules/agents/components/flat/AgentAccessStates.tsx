/**
 * The Agents surface's two "nothing to show you" states:
 *
 * - {@link AgentsNoAccess}: the roster read was refused (401/403), so the
 *   caller lacks `agents:read`. A plain state, never the server's raw reason.
 * - {@link AgentNotFound}: `?agent=<id>` names an agent the caller's roster
 *   does not hold (another user's, or one that does not exist). Shown in place
 *   of the selected agent instead of quietly selecting a different one.
 */
import { Bot, ShieldX } from 'lucide-react';
import { Button, EmptyState } from '@/shared/ui';
import { AgentsApiError } from '@/modules/agents/api';

/** A refused roster read: retrying or re-rendering cannot change the answer. */
export function isAgentsAccessDenied(error: unknown): boolean {
	return error instanceof AgentsApiError && (error.status === 401 || error.status === 403);
}

export function AgentsNoAccess() {
	return (
		<EmptyState
			icon={<ShieldX className="h-8 w-8" />}
			title="No access to agents"
			description="Your account doesn't have permission to view agents. An organisation admin can grant agents:read."
		/>
	);
}

export function AgentNotFound({ onShowAgents }: { onShowAgents?: () => void }) {
	return (
		<div data-testid="agent-not-found">
			<EmptyState
				icon={<Bot className="h-8 w-8" />}
				title="Agent not found"
				description="This agent doesn't exist or isn't visible to you. It may belong to another user."
				action={
					onShowAgents ? (
						<Button variant="outline" size="sm" onClick={onShowAgents}>
							Show my agents
						</Button>
					) : undefined
				}
			/>
		</div>
	);
}
