import { useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { Bot, CalendarClock, Link as LinkIcon, Unlink } from 'lucide-react';
import { ActorStatusBadge, AppLink, Button, Dialog, ErrorAlert } from '@/shared/ui';
import {
	useLinkAgentToToolkit,
	useToolkit,
	useToolkitAgents,
	useUnlinkAgentFromToolkit,
} from '@/modules/toolkits/api';
import { AgentPicker } from '@/modules/toolkits/components/AgentPicker';
import { InlineConfirm } from '@/modules/toolkits/components/InlineConfirm';
import { ToolkitAuditPanel } from '@/modules/toolkits/components/ToolkitAuditPanel';
import { DetailSection, EmptyRow, rowMotion } from '@/modules/toolkits/components/detail/shared';
import { timeAgo } from '@/modules/toolkits/lib/time';
import { ROUTE_PATHS, ROUTES } from '@/shared/app/routes';

/**
 * Overview tab — the safety-critical relationships an operator acts on first.
 *
 * Bound Agents leads (issue #636 rationale preserved from the pre-tab layout):
 * "which agent can use this toolkit" sits closest to the kill-switch header.
 * The read-only audit slice follows so the landing tab answers both "who can
 * call this" and "what changed recently" without a click.
 */
export function OverviewTab({ toolkitId }: { toolkitId: string }) {
	const { data: toolkit } = useToolkit(toolkitId);
	const { data: agents = [], isError: agentsError } = useToolkitAgents(toolkitId);
	const linkAgent = useLinkAgentToToolkit(toolkitId);
	const unlinkAgent = useUnlinkAgentFromToolkit(toolkitId);
	const [linkAgentOpen, setLinkAgentOpen] = useState(false);

	const linkedAgentIds = new Set(agents.map((a) => a.agent_id));

	const submitLinkAgent = (agentId: string) => {
		if (!agentId) return;
		linkAgent.mutate(agentId, {
			onSuccess: () => {
				setLinkAgentOpen(false);
			},
		});
	};

	return (
		<div className="space-y-6">
			{/* Provenance line — created_by finally rendered (phase-4 gap #10). */}
			{toolkit && (
				<p
					className="text-muted-foreground flex items-center gap-1.5 text-xs"
					data-testid="toolkit-provenance"
				>
					<CalendarClock className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
					Created {timeAgo(Date.parse(toolkit.created_at))}
					{toolkit.created_by ? (
						<>
							{' '}
							by{' '}
							<span className="text-foreground font-medium">
								{toolkit.created_by}
							</span>
						</>
					) : null}
					{toolkit.updated_at
						? ` · updated ${timeAgo(Date.parse(toolkit.updated_at))}`
						: ''}
				</p>
			)}

			<DetailSection
				title={`Bound Agents (${agents.length})`}
				action={{
					label: (
						<>
							<LinkIcon className="h-4 w-4" /> Link agent
						</>
					),
					onClick: () => setLinkAgentOpen(true),
				}}
			>
				{agentsError && <ErrorAlert message="Failed to load bound agents." />}
				{!agentsError && agents.length === 0 ? (
					<EmptyRow icon={<Bot />}>
						No agents linked. Link an agent to let its identity call this toolkit.
					</EmptyRow>
				) : (
					<AnimatePresence initial={false}>
						{agents.map((agent) => (
							<motion.div
								key={agent.agent_id}
								{...rowMotion}
								layout
								data-testid="bound-agent-row"
								className="bg-muted/30 border-border/60 hover:border-border flex flex-wrap items-center gap-3 overflow-hidden rounded-lg border p-3 transition-colors"
							>
								<div className="bg-primary/10 text-primary flex h-8 w-8 shrink-0 items-center justify-center rounded-lg">
									<Bot className="h-4 w-4" />
								</div>
								<div className="min-w-0 flex-1 basis-40">
									<div className="flex items-center gap-2">
										<AppLink
											href={ROUTE_PATHS.agent(agent.agent_id)}
											className="text-foreground hover:text-primary truncate text-sm font-medium transition-colors"
										>
											{agent.agent_name}
										</AppLink>
										<ActorStatusBadge
											status={agent.status}
											className="text-[10px]"
										/>
									</div>
									<p className="text-muted-foreground truncate font-mono text-xs">
										{agent.agent_id}
										{agent.bound_at
											? ` · linked ${timeAgo(Date.parse(agent.bound_at))}`
											: ''}
									</p>
								</div>
								<div className="ml-auto w-full sm:w-auto">
									<InlineConfirm
										onConfirm={() => unlinkAgent.mutate(agent.agent_id)}
										message="Revoke this toolkit for the agent?"
										confirmLabel="Unlink"
									>
										<Button
											variant="danger"
											size="sm"
											className="inline-flex items-center gap-1 px-2 py-1 text-xs"
										>
											<Unlink className="h-3 w-3" /> Unlink
										</Button>
									</InlineConfirm>
								</div>
							</motion.div>
						))}
					</AnimatePresence>
				)}
			</DetailSection>

			{/* Read-only toolkit-scoped slice of the org-wide audit log. */}
			<ToolkitAuditPanel toolkitId={toolkitId} poll />

			{/* Link-agent dialog — a stateless picker (selection is the commit),
			    so conditional-mount-free `open` toggling per the dialog rule. */}
			<Dialog
				open={linkAgentOpen}
				onClose={() => setLinkAgentOpen(false)}
				title="Link agent"
				size="sm"
				footer={
					<Button variant="secondary" onClick={() => setLinkAgentOpen(false)}>
						Cancel
					</Button>
				}
			>
				<div className="space-y-3">
					<p className="text-muted-foreground text-sm">
						Pick an agent to grant this toolkit. The agent's identity will be able to
						call the toolkit's bound APIs. Manage agents on the{' '}
						<AppLink href={ROUTES.agents} className="text-primary font-medium">
							Agents
						</AppLink>{' '}
						page.
					</p>
					<AgentPicker
						linkedIds={linkedAgentIds}
						onSelect={submitLinkAgent}
						pending={linkAgent.isPending}
						enabled={linkAgentOpen}
					/>
				</div>
			</Dialog>
		</div>
	);
}
