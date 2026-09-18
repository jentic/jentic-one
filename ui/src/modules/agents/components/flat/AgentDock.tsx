/**
 * AgentDock — the fixed-bottom action dock for the selected agent (plan §4.2,
 * D3), built on the shared `FooterActionBar` floating-pill primitive. It
 * carries the agent's verbs: the serving toggle (enable/disable — or Approve
 * for a pending agent), API key, Permissions (platform scopes / access
 * requests / connected clients — plan §4.7, D4), Activity, MCP (the console
 * MCP tab's config + session history, rehosted), Settings (the console
 * Settings tab's identity form + danger zone, rehosted, plus the agent's
 * provenance), and Archive.
 * The dock is agent-scoped ONLY (D20): every verb speaks for the selected
 * agent, so the org-wide credential inventory opens from the page-level
 * control on the Agents page header instead — dock = this agent; page
 * level = org-wide.
 *
 * Presentation: a compact pill centred on the page content column (via
 * `FooterActionBar`'s `anchorToContainer` — the shell's collapsible rail makes
 * viewport-centring sit visibly off-centre at `xl+`). The dock renders the
 * webapp ToolkitDock shape exactly: one state toggle, a divider, bare icon
 * verbs — no agent name or status badge (the pill strip and panel header
 * already say who is selected; the group's `aria-label` keeps that identity
 * in the a11y tree). Every verb except the serving toggle is icon-only at
 * every breakpoint; the shared `Tooltip` plus an aria-label carry each verb's
 * name. The toggle keeps its short state label — it is the primary verb and
 * must read as a toggle at a glance.
 *
 * Serving-toggle behaviour is ported from jentic-webapp's ToolkitDock:
 *   - an in-flight double-click guard (a second click while the mutation is
 *     in flight is a no-op);
 *   - an Undo affordance on the deactivate toast (one click re-enables);
 *   - a distinct "the write landed but the grid didn't refresh" branch
 *     (`ServingRefreshError`) that never mis-reports the toggle as failed.
 *
 * State semantics (D7/D8/D9, risk O5):
 *   - `active`/`disabled` render the live toggle — disabled means "not
 *     serving traffic", never "read-only"; every other verb stays available.
 *   - `pending` renders Approve in the toggle position (its lifecycle verb).
 *   - `rejected` has no serving verb at all.
 *   - `archived` is irreversible: no live-looking toggle, no Archive verb —
 *     only the read affordances (API key trail, Permissions history,
 *     Activity, MCP session history, Settings read-only), with copy
 *     naming the state. Permissions stays in the reduced set deliberately:
 *     archive sweeps the scope grants and OAuth consents, but the sheet is
 *     the only surface left that can show the agent's filed access requests
 *     and revoked consents — it renders as history, never as a grant invite
 *     (the sheet gates the scope editor off for archived).
 */
import { useState } from 'react';
import { useReducedMotion, motion } from 'framer-motion';
import {
	Activity as ActivityIcon,
	Archive,
	Ban,
	Blocks,
	KeyRound,
	Power,
	Settings,
	ShieldCheck,
} from 'lucide-react';
import { Button, FooterActionBar, Tooltip, toast } from '@/shared/ui';
import { cn } from '@/shared/lib/utils';
import { ServingRefreshError, useSetAgentServing, type AgentEntity } from '@/modules/agents/api';

/** The dock surfaces a verb can open (hosted by the flat surface's sheets).
 * All agent-scoped (D20) — the org-wide inventory is a page-level surface. */
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
		// Anchored to the page content column (not the viewport): the app shell
		// docks a collapsible rail beside `<main>` at `xl+`, so viewport-centring
		// would sit the pill visually off-centre against the tiles above it.
		// Sizing tracks the webapp ToolkitDock: a comfortably padded pill —
		// ~20px icons with breathing room, not a razor-slim strip.
		// The page also mounts the shortcut bar, which owns the bottom edge from
		// `md` up (`z-30`, full width) — so from there the dock lifts clear of
		// it. tailwind-merge keeps this `md:bottom-*` over the primitive's.
		<FooterActionBar
			floating
			anchorToContainer
			className="gap-2 px-4 py-2 md:bottom-[calc(env(safe-area-inset-bottom)+3rem)]"
		>
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
				{/* A checked shield, not a bare one: this sheet is about permissions
				    GRANTED (scopes, access requests, consents), where a plain shield
				    reads as generic security beside the key next to it. */}
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
				{/* MCP before Settings, mirroring the console's tab order. Blocks
				    is the conventional integration mark — the sheet is how a client
				    plugs into this agent — and it stays legible at 20px, where a
				    plug's prongs blur against the power mark on the toggle. */}
				<DockIconButton
					label="MCP"
					icon={<Blocks className="h-5 w-5" />}
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

/**
 * One icon verb: icon-only at every breakpoint — the tooltip and aria-label
 * carry the name (the serving toggle is the only labelled control).
 */
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
			// A pending agent's lifecycle verb IS approval — that's its toggle
			// position (D7). Shares the mutation with the panel banner, so the
			// two buttons load together.
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

/**
 * The enable/disable toggle, with ToolkitDock's ported interaction grammar
 * (double-click guard, Undo-on-deactivate, refresh-failure branch — see the
 * file docblock). Copy: a disabled agent is "not serving" — not read-only.
 */
function ServingToggle({ agent }: { agent: AgentEntity }) {
	const setServing = useSetAgentServing();
	// The dock is mounted once and NOT keyed by agent, so this component
	// instance survives selection changes. All in-flight bookkeeping is
	// therefore scoped by agent id — a bare boolean would keep rendering a
	// false loading state (and no-op clicks) on agent B's toggle while agent
	// A's slow mutation (it awaits the fleet refetch) is still in flight.
	// The set closes the gap between the click and TanStack's `isPending`
	// flush (the ToolkitDock double-click guard, per-id), and the `variables`
	// check keeps the Undo-toast mutation — which bypasses this set — showing
	// on its own agent's control only. Mutations for DIFFERENT agents are
	// independent and may overlap freely; only the SAME agent's toggle
	// no-ops while its own mutation runs.
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
			// The write landed but the roster refetch failed — the agent IS in
			// its new state; only the grid may be stale. Never claim the whole
			// toggle failed (the ToolkitDock "refresh failed" branch).
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
