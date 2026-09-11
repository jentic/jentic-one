/**
 * BoundAgentsSection — read-mostly "which agents can use this credential?"
 * list inside the edit-credential sheet (theme 5 phase 5a). The credential
 * module has no detail page, and the sheet is already the credential's only
 * "everything about this credential" surface, so the section lives here
 * rather than as a new page or a per-row table expandable.
 *
 * Read-only by design: binding/rule management belongs to the agent detail
 * Access tab (BoundCredentialsCard), so each row just shows the agent name,
 * suspended state, bound-at time and links out to the agent's console.
 */
import { Bot, PauseCircle } from 'lucide-react';
import { AppLink, Badge, ErrorAlert, LoadingState } from '@/shared/ui';
import { ROUTE_PATHS } from '@/shared/app/routes';
import { timeAgo } from '@/shared/lib/utils';
import { useCredentialAgents } from '@/shared/credentials/api';

export function BoundAgentsSection({
	credentialId,
	open,
}: {
	credentialId: string;
	/** Host sheet visibility — gates the fetch so closed sheets don't poll. */
	open: boolean;
}) {
	const agents = useCredentialAgents(credentialId, { enabled: open });
	const rows = agents.data?.data ?? [];

	return (
		<div className="border-border space-y-2 rounded-lg border border-dashed p-3">
			<p className="text-foreground text-sm font-medium">
				Bound agents{agents.isSuccess ? ` (${rows.length})` : ''}
			</p>
			<p className="text-muted-foreground text-xs">
				Agents allowed to call APIs with this credential. Manage bindings from each
				agent&apos;s Access tab.
			</p>

			{agents.isPending ? (
				<LoadingState size="sm" />
			) : agents.isError ? (
				<ErrorAlert message="Failed to load bound agents." />
			) : rows.length === 0 ? (
				<p
					className="text-muted-foreground text-xs italic"
					data-testid="bound-agents-empty"
				>
					No agents are bound to this credential.
				</p>
			) : (
				<ul className="space-y-1">
					{rows.map((row) => (
						<li
							key={row.agent_id}
							data-testid="bound-agent-row"
							className="bg-muted/40 flex items-center justify-between gap-2 rounded-md px-2 py-1.5"
						>
							<span className="flex min-w-0 items-center gap-1.5">
								<Bot className="text-muted-foreground h-3.5 w-3.5 shrink-0" />
								<AppLink
									href={ROUTE_PATHS.agent(row.agent_id)}
									className="text-foreground truncate text-xs font-medium hover:underline"
								>
									{row.agent_name}
								</AppLink>
								{row.suspended && (
									<Badge variant="warning" data-testid="bound-agent-suspended">
										<PauseCircle className="h-3 w-3" /> Suspended
									</Badge>
								)}
							</span>
							<span className="text-muted-foreground shrink-0 text-[11px]">
								bound {timeAgo(row.bound_at)}
							</span>
						</li>
					))}
				</ul>
			)}
		</div>
	);
}
