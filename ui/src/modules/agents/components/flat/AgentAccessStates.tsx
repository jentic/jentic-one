/**
 * The Agents surface's "nothing to show you" states:
 *
 * - {@link AgentsNoAccess}: the roster read was refused (403), so the caller
 *   lacks `agents:read`. A plain state, never the server's raw reason.
 * - {@link AgentsSessionEnded}: the roster read answered 401 — the session
 *   ended, which says nothing about the caller's permissions.
 * - {@link AgentNotFound}: `?agent=<id>` names an agent the caller's roster
 *   does not hold. Shown in place of the selected agent instead of quietly
 *   selecting a different one.
 */
import { Bot, LogIn, ShieldX } from 'lucide-react';
import { Button, EmptyState } from '@/shared/ui';

export function AgentsNoAccess() {
	return (
		<EmptyState
			icon={<ShieldX className="h-8 w-8" />}
			title="No access to agents"
			description="Your account doesn't have permission to view agents. An organisation admin can grant agents:read."
		/>
	);
}

export function AgentsSessionEnded() {
	return (
		<EmptyState
			icon={<LogIn className="h-8 w-8" />}
			title="Couldn't load agents"
			description="Your session may have ended. Sign in again to see your agents."
		/>
	);
}

export function AgentNotFound({ onShowAgents }: { onShowAgents?: () => void }) {
	return (
		<div data-testid="agent-not-found">
			<EmptyState
				icon={<Bot className="h-8 w-8" />}
				title="Agent not found"
				description="This agent doesn't exist or isn't visible to your account. Another user's agent, or one that is not yet claimed, is visible only to its owner or an organisation admin."
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
