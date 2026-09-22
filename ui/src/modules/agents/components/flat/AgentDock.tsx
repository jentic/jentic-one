/**
 * AgentDock — the fixed-bottom action dock for the selected agent, built on the
 * shared `FooterActionBar` pill. Its verbs are agent-scoped; the org credential
 * inventory opens from the page header instead.
 *
 * `pending` renders Approve in the toggle position, `rejected` has no serving
 * verb, and `archived` keeps only the read affordances.
 */
import { useState } from 'react';
import { useReducedMotion, motion } from 'framer-motion';
import {
	Activity as ActivityIcon,
	Archive,
	Ban,
	KeyRound,
	Power,
	Settings,
	ShieldCheck,
} from 'lucide-react';
import { Button, FooterActionBar, McpIcon, Tooltip, toast } from '@/shared/ui';
import { cn } from '@/shared/lib/utils';
import { ServingRefreshError, useSetAgentServing, type AgentEntity } from '@/modules/agents/api';

/** The dock surfaces a verb can open (hosted by the flat surface's sheets).
 * All agent-scoped — the org-wide inventory is a page-level surface. */
export type AgentDockSurface = 'api-key' | 'permissions' | 'activity' | 'mcp' | 'settings';

export interface AgentDockProps {
	agent: AgentEntity;
	/** Open one of the dock's sheet surfaces (keys / permissions / activity / mcp / settings). */
	onOpenSurface: (surface: AgentDockSurface) => void;
	/** Route a pending agent's Approve through the shared mutation (banner-consistent). */
	onApprove: () => void;
	/** True while THIS agent's approve is in flight (per-id scoped by the caller). */
	approvePending: boolean;
	/** Stage the archive confirm (routes through the existing LifecycleDialogs). */
	onArchive: () => void;
}

/** Divider between the dock's verb groups. */
function DockDivider() {
	return <span aria-hidden="true" className="bg-border mx-0.5 h-5 w-px shrink-0" />;
}

export function AgentDock({
	agent,
	onOpenSurface,
	onApprove,
	approvePending,
	onArchive,
}: AgentDockProps) {
	const reducedMotion = useReducedMotion();
	const isArchived = agent.status === 'archived';

	return (
		// Anchored to the page content column, not the viewport: the shell's collapsible
		// rail at `xl+` would sit a viewport-centred pill off-centre.
		<FooterActionBar floating anchorToContainer className="gap-2 px-4 py-2">
			<motion.div
				role="group"
				aria-label={`Actions for ${agent.name}`}
				initial={reducedMotion ? false : { opacity: 0, y: 8 }}
				animate={{ opacity: 1, y: 0 }}
				transition={{ duration: 0.18, ease: 'easeOut' }}
				className="flex min-w-0 items-center gap-2"
				data-testid="agent-dock"
			>
				<ServingVerb agent={agent} onApprove={onApprove} approvePending={approvePending} />

				<DockDivider />

				<DockIconButton
					label="API key"
					icon={<KeyRound className="h-5 w-5" />}
					onClick={() => onOpenSurface('api-key')}
				/>
				{/* A checked shield: this sheet is about permissions GRANTED. */}
				<DockIconButton
					label="Permissions"
					icon={<ShieldCheck className="h-5 w-5" />}
					onClick={() => onOpenSurface('permissions')}
				/>
				<DockIconButton
					label="Activity"
					icon={<ActivityIcon className="h-5 w-5" />}
					onClick={() => onOpenSurface('activity')}
				/>
				{/* MCP before Settings, mirroring the console's tab order, with the
				    protocol's own mark rather than a generic integration glyph. */}
				<DockIconButton
					label="MCP"
					icon={<McpIcon className="h-5 w-5" />}
					onClick={() => onOpenSurface('mcp')}
				/>
				<DockIconButton
					label="Settings"
					icon={<Settings className="h-5 w-5" />}
					onClick={() => onOpenSurface('settings')}
				/>

				{!isArchived && (
					<>
						<DockDivider />
						<Tooltip content="Archive this agent (irreversible)" interactiveChild>
							<Button
								variant="ghost"
								size="sm"
								onClick={onArchive}
								aria-label={`Archive ${agent.name}`}
								className="text-danger hover:bg-danger/10 hover:text-danger shrink-0 px-2 py-1.5"
							>
								<Archive className="h-5 w-5" aria-hidden="true" />
								<span className="sr-only">Archive</span>
							</Button>
						</Tooltip>
					</>
				)}
			</motion.div>
		</FooterActionBar>
	);
}

/** One icon verb: icon-only at every breakpoint — the tooltip and aria-label
 * carry the name. */
function DockIconButton({
	label,
	icon,
	onClick,
}: {
	label: string;
	icon: React.ReactNode;
	onClick: () => void;
}) {
	return (
		<Tooltip content={label} interactiveChild>
			<Button
				variant="ghost"
				size="sm"
				onClick={onClick}
				aria-label={label}
				className="shrink-0 px-2 py-1.5"
			>
				<span aria-hidden="true" className="flex items-center">
					{icon}
				</span>
				<span className="sr-only">{label}</span>
			</Button>
		</Tooltip>
	);
}

// ---------------------------------------------------------------------------
// Serving verb — the toggle position, per lifecycle state
// ---------------------------------------------------------------------------

function ServingVerb({
	agent,
	onApprove,
	approvePending,
}: {
	agent: AgentEntity;
	onApprove: () => void;
	approvePending: boolean;
}) {
	switch (agent.status) {
		case 'active':
		case 'disabled':
			return <ServingToggle agent={agent} />;
		case 'pending':
			// A pending agent's lifecycle verb IS approval. Shares the mutation with the
			// panel banner, so the two buttons load together.
			return (
				<Button
					size="sm"
					loading={approvePending}
					onClick={onApprove}
					// Named per-agent so it can't collide with the approval
					// band's own Approve buttons in the accessibility tree.
					aria-label={`Approve ${agent.name}`}
					data-testid="dock-approve"
					className="px-3 py-1.5 text-xs"
				>
					Approve
				</Button>
			);
		case 'rejected':
			// No serving verb: a rejected agent can never serve traffic.
			return (
				<span className="text-muted-foreground px-1 text-xs" data-testid="dock-state-note">
					Rejected — not serving traffic
				</span>
			);
		case 'archived':
			// Irreversible — never render a live-looking toggle (risk O5).
			return (
				<span className="text-muted-foreground px-1 text-xs" data-testid="dock-state-note">
					Archived — retired, read-only here
				</span>
			);
	}
}

/** The enable/disable toggle. Copy: a disabled agent is "not serving" — not
 * read-only. */
function ServingToggle({ agent }: { agent: AgentEntity }) {
	const setServing = useSetAgentServing();
	// The dock is mounted once and NOT keyed by agent, so in-flight bookkeeping is
	// scoped by agent id — a bare boolean would load agent B's toggle for A's write.
	const [inFlightIds, setInFlightIds] = useState<ReadonlySet<string>>(() => new Set());
	const togglePending =
		inFlightIds.has(agent.id) ||
		(setServing.isPending && setServing.variables?.id === agent.id);

	const serving = agent.status === 'active';

	async function handleToggle() {
		// Guard against rapid double-clicks that slip through between React's
		// state flush and the mutation's isPending being set.
		if (togglePending) return;
		setInFlightIds((prev) => new Set(prev).add(agent.id));
		const next = !serving;
		try {
			await setServing.mutateAsync({ id: agent.id, serving: next });
			if (next) {
				toast({ title: `${agent.name} is serving traffic again`, variant: 'success' });
			} else {
				toast({
					title: `${agent.name} is no longer serving traffic`,
					description: 'It stays fully editable while disabled.',
					variant: 'success',
					action: {
						label: 'Undo',
						onClick: () => {
							setServing.mutate(
								{ id: agent.id, serving: true },
								{
									onSuccess: () =>
										toast({
											title: `${agent.name} is serving traffic again`,
											variant: 'success',
										}),
									onError: (error) =>
										toast({
											title:
												error instanceof ServingRefreshError
													? error.message
													: `Couldn't re-enable ${agent.name}`,
											description:
												error instanceof ServingRefreshError
													? 'Reload to see its current state.'
													: error.message,
											variant: 'error',
										}),
								},
							);
						},
					},
				});
			}
		} catch (error) {
			// The write landed but the roster refetch failed — the agent IS in its new
			// state; only the grid may be stale.
			if (error instanceof ServingRefreshError) {
				toast({
					title: error.message,
					description: 'Reload to see its current state.',
					variant: 'default',
				});
			} else {
				toast({
					title: `Couldn't update ${agent.name}`,
					description: error instanceof Error ? error.message : undefined,
					variant: 'error',
				});
			}
		} finally {
			setInFlightIds((prev) => {
				const next = new Set(prev);
				next.delete(agent.id);
				return next;
			});
		}
	}

	return (
		<Tooltip
			content={serving ? 'Stop serving traffic (disable)' : 'Start serving traffic (enable)'}
			interactiveChild
		>
			<Button
				variant="ghost"
				size="sm"
				loading={togglePending}
				disabled={togglePending}
				onClick={() => void handleToggle()}
				aria-pressed={serving}
				aria-label={
					serving
						? `Disable ${agent.name} — stop serving traffic`
						: `Enable ${agent.name} — start serving traffic`
				}
				data-testid="dock-serving-toggle"
				className={cn(
					'shrink-0 gap-1.5 rounded-full border px-3 py-1.5 text-xs font-medium',
					serving
						? 'bg-success/10 text-success border-success/30 hover:bg-success/20 hover:text-success'
						: 'bg-danger/10 text-danger border-danger/30 hover:bg-danger/20 hover:text-danger',
				)}
			>
				{!togglePending &&
					(serving ? (
						<Power className="h-4 w-4" aria-hidden="true" />
					) : (
						<Ban className="h-4 w-4" aria-hidden="true" />
					))}
				{serving ? 'Serving' : 'Not serving'}
			</Button>
		</Tooltip>
	);
}
