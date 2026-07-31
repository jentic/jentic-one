import { useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import {
	AlertTriangle,
	Bot,
	CalendarClock,
	KeyRound,
	Link as LinkIcon,
	Unlink,
} from 'lucide-react';
import {
	ActorLabel,
	ActorStatusBadge,
	AppLink,
	Button,
	DetailSection,
	Dialog,
	EmptyRow,
	ErrorAlert,
} from '@/shared/ui';
import { ruleSummary } from '@/shared/lib';
import {
	useLinkAgentToToolkit,
	useToolkit,
	useToolkitAgents,
	useToolkitBindings,
	useUnlinkAgentFromToolkit,
} from '@/modules/toolkits/api';
import { AgentPicker } from '@/modules/toolkits/components/AgentPicker';
import { BindCredentialDialog } from '@/modules/toolkits/components/BindCredentialDialog';
import { InlineConfirm } from '@/modules/toolkits/components/InlineConfirm';
import { ToolkitAuditPanel } from '@/modules/toolkits/components/ToolkitAuditPanel';
import { rowMotion, toDisplayRules } from '@/modules/toolkits/components/detail/shared';
import { timeAgo } from '@/modules/toolkits/lib/time';
import { ROUTE_PATHS, ROUTES } from '@/shared/app/routes';

/**
 * Overview tab — the safety-critical relationships an operator acts on first.
 *
 * Bound Agents leads (issue #636 rationale preserved from the pre-tab layout):
 * "which agent can use this toolkit" sits closest to the kill switch. Bound
 * credentials sits beside it ("what can this toolkit call") with the SAME
 * primary affordance shape — Link agent / Bind credential — so both halves of the
 * wiring are one click from the landing tab. Rule *editing* stays on Access
 * (the Manage jump); the audit slice follows, so the tab answers "who can call
 * this", "what can it reach", and "what changed recently" without a click.
 */

export interface OverviewTabProps {
	toolkitId: string;
	/** Jump to the Access tab (the credentials summary's Manage action). */
	onManageAccess: () => void;
}

export function OverviewTab({ toolkitId, onManageAccess }: OverviewTabProps) {
	const { data: toolkit } = useToolkit(toolkitId);
	const { data: agents = [], isError: agentsError } = useToolkitAgents(toolkitId);
	const { data: bindings = [], isError: bindingsError } = useToolkitBindings(toolkitId);
	const linkAgent = useLinkAgentToToolkit(toolkitId);
	const unlinkAgent = useUnlinkAgentFromToolkit(toolkitId);
	const [linkAgentOpen, setLinkAgentOpen] = useState(false);
	const [bindOpen, setBindOpen] = useState(false);

	const linkedAgentIds = new Set(agents.map((a) => a.agent_id));
	const boundIds = new Set(bindings.map((b) => b.credential_id));

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
								{/* Resolve the raw actor id to a name, like every other
								    attribution field on the platform. */}
								<ActorLabel actorId={toolkit.created_by} />
							</span>
						</>
					) : null}
					{toolkit.updated_at
						? ` · updated ${timeAgo(Date.parse(toolkit.updated_at))}`
						: ''}
				</p>
			)}

			<div className="grid grid-cols-1 items-stretch gap-6 xl:grid-cols-2">
				<DetailSection
					title={`Bound agents (${agents.length})`}
					icon={<Bot className="h-4 w-4" />}
					className="h-full"
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
											message="Unlink this agent?"
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

				{/* Same rank as agents: credentials bind right here (the wizard decides
				    the grant inline); rule editing stays on Access, one click away. */}
				<DetailSection
					title={`Bound credentials (${bindings.length})`}
					icon={<KeyRound className="h-4 w-4" />}
					className="h-full"
					trailing={
						<div className="flex items-center gap-1.5">
							<Button variant="ghost" size="sm" onClick={onManageAccess}>
								Manage
							</Button>
							<Button variant="secondary" size="sm" onClick={() => setBindOpen(true)}>
								<LinkIcon className="h-4 w-4" /> Bind credential
							</Button>
						</div>
					}
				>
					{bindingsError && <ErrorAlert message="Failed to load bound credentials." />}
					{!bindingsError && bindings.length === 0 ? (
						<EmptyRow icon={<KeyRound />}>
							No credentials bound — this toolkit can't reach any API yet. Bind one to
							grant access.
						</EmptyRow>
					) : (
						bindings.map((cred) => {
							const displayRules = toDisplayRules(cred.permissions);
							return (
								<div
									key={cred.credential_id}
									data-testid="overview-credential-row"
									className="bg-muted/30 border-border/60 flex flex-wrap items-center gap-3 rounded-lg border p-3"
								>
									<div className="bg-accent-blue/10 text-accent-blue flex h-8 w-8 shrink-0 items-center justify-center rounded-lg">
										<KeyRound className="h-4 w-4" />
									</div>
									<div className="min-w-0 flex-1 basis-40">
										<span className="text-foreground block truncate text-sm font-medium">
											{cred.label ?? cred.credential_id}
										</span>
										<p className="text-muted-foreground truncate font-mono text-xs">
											{cred.api_name ?? cred.api_vendor ?? cred.credential_id}
										</p>
										{/* The grant's gist in the platform's rule voice —
										    "Blocks DELETE; Allows GET on 3 operations". */}
										{displayRules.length > 0 && (
											<p
												className="text-muted-foreground mt-0.5 truncate text-xs"
												title={ruleSummary(displayRules)}
											>
												{ruleSummary(displayRules)}
											</p>
										)}
									</div>
									{displayRules.length === 0 && (
										<span className="text-warning ml-auto inline-flex shrink-0 items-center gap-1 text-xs">
											<AlertTriangle className="h-3 w-3" aria-hidden="true" />
											All operations blocked
										</span>
									)}
								</div>
							);
						})
					)}
				</DetailSection>
			</div>

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

			{/* Two-step bind wizard — the same component the Access tab opens, so
			    binding reads identically wherever it starts. The post-bind
			    "link an agent?" prompt opens the link dialog hosted right here. */}
			<BindCredentialDialog
				toolkitId={toolkitId}
				open={bindOpen}
				onClose={() => setBindOpen(false)}
				boundIds={boundIds}
				agentless={!agentsError && agents.length === 0 && (toolkit?.key_count ?? 0) === 0}
				onLinkAgent={() => setLinkAgentOpen(true)}
			/>
		</div>
	);
}
