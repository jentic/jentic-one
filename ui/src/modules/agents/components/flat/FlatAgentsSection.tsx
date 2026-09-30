/**
 * FlatAgentsSection — the flat Agents surface: pending banner, agent strip, the
 * selected agent's APIs band and its tile grid. Selection lives in `?agent=<id>`
 * so it is linkable. Tiles are composed client-side from reads the app already
 * makes — no new endpoints.
 *
 * With no fleet in the org it shows `FirstAgentLanding`, where the first
 * self-registered agent is approved and given its first API before the fleet
 * view takes over. The landing's state (resume on load, the roster poll, the
 * exits) is `useFirstAgentLanding`; its rules are `lib/firstRun.ts`.
 */
import { useCallback, useEffect, useLayoutEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router';
import { motion, useReducedMotionConfig } from 'framer-motion';
import { Plus } from 'lucide-react';
import { Button, Card, ErrorAlert, ExpandableText, Skeleton, STATUS_ICON } from '@/shared/ui';
import { cn } from '@/shared/lib/utils';
import { useEagerCursorDrain, useHotkey } from '@/shared/hooks';
import {
	useAllApis,
	useAllCredentials,
	type ApiResponse,
	type Credential,
	type DrainedList,
	type SelectedApi,
} from '@/shared/credentials/api';
import {
	useAgentCredentialBindings,
	useAgentsCredentialBindings,
	useAgentBindingRuleSummaries,
	useActorUsageDetail,
	useActorExecutions,
	usePendingAgents,
	useApproveAgent,
	useDenyAgent,
	useDisableAgent,
	useArchiveAgent,
	useUnbindAgentCredential,
	usePurgeOrphanBindings,
	useResumeAgentCredentialBinding,
	type ActorStatus,
	type AgentEntity,
} from '@/modules/agents/api';
import {
	accountLabels,
	agentApiCount,
	agentSetupGapCount,
	composeApiTiles,
	multiAccountApis,
	partitionBindings,
	tileApiKey,
	tileStats,
} from '@/modules/agents/lib/apiTiles';
import { viewerIsOrgAdmin } from '@/modules/agents/lib/bindAuthority';
import { useOptionalCurrentUser } from '@/shared/auth';
import { AgentStrip } from '@/modules/agents/components/flat/AgentStrip';
import { AgentStatStrip } from '@/modules/agents/components/flat/AgentStatStrip';
import { ApiTile } from '@/modules/agents/components/flat/ApiTile';
import { ApiAccessSidebar } from '@/modules/agents/components/flat/ApiAccessSidebar';
import { PendingApprovalBanner } from '@/modules/agents/components/flat/PendingApprovalBanner';
import {
	LifecycleDialogs,
	type PendingConfirm,
} from '@/modules/agents/components/LifecycleDialogs';
import { AgentCreateSheet } from '@/modules/agents/components/AgentCreateSheet';
import { FirstAgentLanding } from '@/modules/agents/components/flat/FirstAgentLanding';
import { AddApisTray } from '@/modules/agents/components/flat/AddApisTray';
import { ApiSetupQueue } from '@/modules/agents/components/flat/ApiSetupQueue';
import {
	preflightApis,
	stillOwedItems,
	type PreflightItem,
} from '@/modules/agents/lib/apiPreflight';
import type { QueueBackSeed } from '@/modules/agents/lib/setupQueue';
import { isHistory } from '@/modules/agents/lib/firstRun';
import { useFirstAgentLanding } from '@/modules/agents/lib/useFirstAgentLanding';
import { usePreflightInputs } from '@/modules/agents/lib/usePreflightInputs';
import { AgentDock, type AgentDockSurface } from '@/modules/agents/components/flat/AgentDock';
import {
	AgentActivitySheet,
	AgentKeysSheet,
	AgentMcpSheet,
	AgentPermissionsSheet,
	AgentSettingsSheet,
} from '@/modules/agents/components/flat/AgentDockPanels';

/** Strip scan order: decisions first, then the working fleet. */
const STATUS_ORDER: Record<ActorStatus, number> = {
	pending: 0,
	active: 1,
	disabled: 2,
	rejected: 3,
	archived: 4,
};

interface FlatAgentsSectionProps {
	createOpen: boolean;
	setCreateOpen: (open: boolean) => void;
	/** The page header's fleet filter — applied by the strip. */
	filter: string;
	/** Whether the zero-agents landing is on screen — the header labels itself by it. */
	onLandingChange: (showing: boolean) => void;
}

export function FlatAgentsSection({
	createOpen,
	setCreateOpen,
	filter,
	onLandingChange,
}: FlatAgentsSectionProps) {
	// An ABSENT `?agent=` is written back; an UNKNOWN one is left alone — just
	// after a create it names an agent the roster hasn't refetched yet.
	const [searchParams, setSearchParams] = useSearchParams();
	const selectAgent = useCallback(
		(id: string, { replace = false }: { replace?: boolean } = {}) =>
			setSearchParams(
				(prev) => {
					const next = new URLSearchParams(prev);
					next.set('agent', id);
					return next;
				},
				{ replace },
			),
		[setSearchParams],
	);

	// The signal names the agent, not a boolean: a boolean would open the tray
	// over whichever agent was on screen before. `queue` holds APIs the operator
	// already chose (the landing's GitHub), which skip the tray for the queue.
	const [addApisFor, setAddApisFor] = useState<{
		agentId: string;
		queue: SelectedApi[];
	} | null>(null);
	const clearAddApisFor = useCallback(() => setAddApisFor(null), []);

	const agentParam = searchParams.get('agent');
	const approve = useApproveAgent();
	const deny = useDenyAgent();
	const landing = useFirstAgentLanding({
		approve,
		deny,
		selectedId: agentParam,
		selectAgent,
		openAddApis: setAddApisFor,
	});
	const { query } = landing;

	// The strip is the fleet, with no "Load more", so drain the cursor eagerly.
	const { fetchNextPage, hasNextPage, isFetchingNextPage, isError } = query;
	useEagerCursorDrain({ hasNextPage, isFetchingNextPage, isError, fetchNextPage });

	const agents = useMemo(
		() =>
			[...landing.agents].sort(
				(a, b) =>
					STATUS_ORDER[a.status] - STATUS_ORDER[b.status] ||
					b.createdAt.localeCompare(a.createdAt),
			),
		[landing.agents],
	);

	const selected = agents.find((a) => a.id === agentParam) ?? agents[0] ?? null;
	// Written back only once the fleet view is decided and on screen: the resume
	// reads `?agent=`, and a history agent written there would resume the fleet.
	const fleetShown = landing.ready && !landing.visible;
	const fallbackId = agentParam == null && fleetShown ? (selected?.id ?? null) : null;
	useEffect(() => {
		if (fallbackId != null) selectAgent(fallbackId, { replace: true });
	}, [fallbackId, selectAgent]);

	/** The unfinished Add-APIs batch per agent. Held here, not in
	 *  `SelectedAgentPanel`, which unmounts on a tab switch. */
	const [queueBatches, setQueueBatches] = useState<Record<string, PreflightItem[]>>({});
	const setQueueBatchFor = useCallback((agentId: string, items: PreflightItem[]) => {
		setQueueBatches((prev) => {
			if (items.length === 0) {
				if (prev[agentId] == null) return prev;
				const { [agentId]: _dropped, ...rest } = prev;
				return rest;
			}
			return { ...prev, [agentId]: items };
		});
	}, []);

	function handleAgentCreated(agent: AgentEntity, opts: { addApis: boolean }) {
		// Selected either way: the operator just named this agent. The fleet view
		// shows once the roster has it.
		selectAgent(agent.id);
		setAddApisFor(opts.addApis ? { agentId: agent.id, queue: [] } : null);
		landing.createdManually(agent.id);
	}

	// Same cache slice the nav badge polls; `atLeast` hedges an incomplete drain.
	const { agents: pendingAgents, atLeast: pendingAtLeast } = usePendingAgents();

	const disable = useDisableAgent();
	const archive = useArchiveAgent();
	const [confirm, setConfirm] = useState<PendingConfirm>(null);
	// The dock's agent-scoped sheets. One slot, so two can never stack.
	const [dockSurface, setDockSurface] = useState<AgentDockSurface | null>(null);

	// The open tile's key. Selecting another agent closes the sidebar.
	const [openTileKey, setOpenTileKey] = useState<string | null>(null);
	const selectedId = selected?.id ?? null;
	useEffect(() => {
		setOpenTileKey(null);
		// Each sheet is handed `agent={selected}`, so one left open across a tab
		// switch would re-point at the new agent.
		setDockSurface(null);
	}, [selectedId]);

	// Drained to EVERY page: the join is over the whole workspace.
	const credentialsSource = useAllCredentials();
	const apisSource = useAllApis();
	const credentials = credentialsSource.items;

	const agentIds = useMemo(
		() => agents.filter((a) => a.status !== 'archived').map((a) => a.id),
		[agents],
	);
	const bindingsByAgent = useAgentsCredentialBindings(agentIds);
	// The "N to set up" hint is a claim about the WHOLE credential list, so no
	// hints until the drain completes.
	const setupGaps = useMemo(() => {
		const map = new Map<string, number>();
		if (!credentialsSource.complete) return map;
		for (const id of agentIds) {
			map.set(id, agentSetupGapCount(bindingsByAgent.get(id), credentials));
		}
		return map;
	}, [agentIds, bindingsByAgent, credentials, credentialsSource.complete]);

	// Each tab's count needs the whole API registry (a partial one resolves fewer
	// wildcards); an agent with no entry renders no count.
	const apiCounts = useMemo(() => {
		const map = new Map<string, number>();
		if (!apisSource.complete) return map;
		for (const id of agentIds) {
			const bindings = bindingsByAgent.get(id);
			if (!bindings) continue;
			map.set(id, agentApiCount(bindings, apisSource.items));
		}
		return map;
	}, [agentIds, bindingsByAgent, apisSource.complete, apisSource.items]);

	const firstPageFailed = Boolean(query.error && !query.data);
	const loading = query.isPending || !landing.ready;
	const landingShown = !firstPageFailed && !loading && landing.visible;
	// Before paint, so the header's label never disagrees with the body.
	useLayoutEffect(() => onLandingChange(landingShown), [landingShown, onLandingChange]);

	// Rendered by every branch below: the header's "New agent" flips `createOpen`
	// from outside, and a loading roster would otherwise swallow the click. The
	// lifecycle confirms serve the landing and the fleet.
	const overlays = (
		<>
			<AgentCreateSheet
				open={createOpen}
				onClose={() => setCreateOpen(false)}
				onCreated={handleAgentCreated}
				initialName={landingShown ? landing.commandName : undefined}
			/>
			<LifecycleDialogs
				confirm={confirm}
				onClose={() => setConfirm(null)}
				disableBody="Disabling immediately revokes this agent's ability to authenticate. You can re-enable it later."
				mutations={{ deny, disable, archive }}
			/>
		</>
	);

	// A failed FIRST page is a dead surface; a failed LATER page keeps the loaded
	// fleet on screen, with the inline notice below offering the retry.
	if (firstPageFailed) {
		return (
			<>
				<ErrorAlert message={query.error as Error} />
				{overlays}
			</>
		);
	}

	// Until the resume decision is known, neither the fleet nor the landing.
	if (loading) {
		return (
			<>
				<div role="status" aria-live="polite" aria-busy="true" className="space-y-6">
					<span className="sr-only">Loading agents…</span>
					{/* Shaped like the tab rail, so the first paint doesn't reflow. */}
					<Skeleton className="h-11 w-full max-w-md rounded-lg" />
					<div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4">
						{[0, 1, 2].map((i) => (
							<Skeleton key={i} className="h-44 rounded-xl" />
						))}
					</div>
				</div>
				{overlays}
			</>
		);
	}

	if (landingShown) {
		const landingAgent = landing.agent;
		return (
			<>
				{/* Agents is the app's home, so an empty fleet is a fresh workspace. */}
				<FirstAgentLanding
					onCreateAgent={() => setCreateOpen(true)}
					agent={landingAgent}
					onApprove={() => {
						if (landingAgent) approve.mutate(landingAgent.id);
					}}
					approvePending={landing.approving}
					onDeny={() => {
						if (landingAgent)
							setConfirm({
								kind: 'deny',
								id: landingAgent.id,
								name: landingAgent.name,
							});
					}}
					onExit={landing.exit}
					registerName={landing.registerName}
					onRegisterNameChange={landing.setRegisterName}
					expectedName={landing.expectedName}
					morePending={landing.morePending}
					onShowFleet={landing.showFleet}
					history={landing.history}
					onShowHistory={() => {
						// The strip's own order, so it opens on the tab it lists first.
						const first = agents.find(isHistory);
						if (first) landing.showHistory(first.id);
					}}
					slotRef={landing.slotRef}
				/>
				{overlays}
			</>
		);
	}

	return (
		<>
			<PendingApprovalBanner
				pending={pendingAgents}
				atLeast={pendingAtLeast}
				onReview={selectAgent}
				onApprove={(id) => approve.mutate(id)}
				onDeny={({ id, name }) => setConfirm({ kind: 'deny', id, name })}
				approvePendingId={
					approve.isPending && typeof approve.variables === 'string'
						? approve.variables
						: null
				}
			/>

			<AgentStrip
				agents={agents}
				selectedId={selected?.id ?? null}
				onSelect={selectAgent}
				setupGaps={setupGaps}
				apiCounts={apiCounts}
				filter={filter}
			/>

			{isError && (
				<ErrorAlert
					message="Couldn't load the rest of the fleet — some agents may be missing."
					onRetry={() => void fetchNextPage()}
					retrying={isFetchingNextPage}
				/>
			)}

			{selected && (
				<SelectedAgentPanel
					key={selected.id}
					agent={selected}
					credentialsSource={credentialsSource}
					apisSource={apisSource}
					openTileKey={openTileKey}
					onOpenTile={setOpenTileKey}
					onCloseTile={() => setOpenTileKey(null)}
					onApprove={() => approve.mutate(selected.id)}
					approvePending={approve.isPending && approve.variables === selected.id}
					autoOpenAddApis={addApisFor?.agentId === selected.id}
					autoQueueApis={
						addApisFor?.agentId === selected.id ? addApisFor.queue : EMPTY_PICKS
					}
					onAutoOpenAddApisConsumed={clearAddApisFor}
					queueBatch={queueBatches[selected.id] ?? EMPTY_BATCH}
					onQueueBatchChange={setQueueBatchFor}
				/>
			)}

			{selected && (
				<>
					{/* Approve shares the panel banner's mutation, so both go in-flight together. */}
					<AgentDock
						agent={selected}
						onOpenSurface={setDockSurface}
						onApprove={() => approve.mutate(selected.id)}
						approvePending={approve.isPending && approve.variables === selected.id}
						onArchive={() =>
							setConfirm({ kind: 'archive', id: selected.id, name: selected.name })
						}
					/>
					<AgentKeysSheet
						agent={selected}
						open={dockSurface === 'api-key'}
						onClose={() => setDockSurface(null)}
					/>
					<AgentActivitySheet
						agent={selected}
						open={dockSurface === 'activity'}
						onClose={() => setDockSurface(null)}
					/>
					<AgentPermissionsSheet
						agent={selected}
						open={dockSurface === 'permissions'}
						onClose={() => setDockSurface(null)}
					/>
					<AgentMcpSheet
						agent={selected}
						open={dockSurface === 'mcp'}
						onClose={() => setDockSurface(null)}
					/>
					<AgentSettingsSheet
						agent={selected}
						open={dockSurface === 'settings'}
						onClose={() => setDockSurface(null)}
						onArchive={() =>
							setConfirm({ kind: 'archive', id: selected.id, name: selected.name })
						}
						archivePending={archive.isPending && archive.variables === selected.id}
					/>
				</>
			)}

			{overlays}
		</>
	);
}

// ---------------------------------------------------------------------------
// Selected-agent panel — header, stat strip, non-active banner, tile grid
// ---------------------------------------------------------------------------

/**
 * Copy for the non-active banner. Suspension stops traffic, not editing.
 * `disabled` has no banner — the tile family and the dock's red toggle say it.
 */
const NON_ACTIVE_COPY: Record<BanneredStatus, { title: string; detail: string }> = {
	pending: {
		title: 'Waiting for approval',
		detail: 'Not serving traffic. Approve it to let it authenticate.',
	},
	rejected: {
		title: 'Rejected',
		detail: 'Not serving traffic.',
	},
	archived: {
		title: 'Archived',
		detail: 'This agent is retired. Its bindings, grants and consents were swept.',
	},
};

/** The non-active states that still warrant a banner (see `NON_ACTIVE_COPY`). */
type BanneredStatus = Exclude<ActorStatus, 'active' | 'disabled'>;

/** Per-state banner tint — about attention, not editability. */
const NON_ACTIVE_BANNER: Record<BanneredStatus, { shell: string; chip: string }> = {
	pending: {
		shell: 'border-warning/40 bg-warning/[0.04]',
		chip: 'bg-warning/15 text-warning',
	},
	rejected: {
		shell: 'border-danger/40 bg-danger/[0.04]',
		chip: 'bg-danger/15 text-danger',
	},
	archived: {
		shell: 'border-border/70 bg-muted/20',
		chip: 'bg-muted-foreground/10 text-muted-foreground/70',
	},
};

/** The notice above the grid — and, for pending, the decision itself. */
function StateBanner({
	status,
	denialReason,
	onApprove,
	approvePending,
}: {
	status: BanneredStatus;
	denialReason: string | null;
	onApprove: () => void;
	approvePending: boolean;
}) {
	const { shell, chip } = NON_ACTIVE_BANNER[status];
	const Icon = STATUS_ICON[status];
	return (
		<div
			role="status"
			data-testid={`agent-state-banner-${status}`}
			className={cn(
				'flex flex-wrap items-center gap-x-3 gap-y-3 rounded-xl border p-3 sm:flex-nowrap',
				shell,
			)}
		>
			<span
				aria-hidden="true"
				className={cn('grid h-8 w-8 shrink-0 place-items-center rounded-lg', chip)}
			>
				<Icon className="h-4 w-4" />
			</span>
			<div className="min-w-0 flex-1 space-y-0.5">
				<p className="text-foreground text-sm leading-tight font-medium">
					{NON_ACTIVE_COPY[status].title}
				</p>
				<p className="text-muted-foreground text-xs leading-snug">
					{NON_ACTIVE_COPY[status].detail}
					{status === 'rejected' && denialReason && <> Reason: {denialReason}</>}
				</p>
			</div>
			{status === 'pending' && (
				<Button size="sm" loading={approvePending} onClick={onApprove} className="shrink-0">
					Approve
				</Button>
			)}
		</div>
	);
}

/**
 * Copy for an agent that reaches no API yet, per state. The generic line is only
 * true of an agent that is actually serving.
 */
const NO_APIS_COPY: Record<ActorStatus, string> = {
	active: 'This agent has an identity and can authenticate, but no credential is bound — so every call it makes will fail. Add the APIs it needs.',
	disabled:
		'Nothing is bound yet, and while disabled this agent is not serving traffic. Its APIs and credentials stay editable, so you can set them up now.',
	pending:
		'Nothing is bound yet, and a pending agent cannot authenticate either. Approve it first, then add the APIs it needs.',
	rejected: 'A rejected agent holds no credentials and cannot authenticate.',
	archived: 'Archiving swept its credential bindings; an archived agent keeps no access.',
};

interface SelectedAgentPanelProps {
	agent: AgentEntity;
	/** Drained org credential list (join source for the tiles). */
	credentialsSource: DrainedList<Credential>;
	/** Drained workspace API registry (join source for the tiles). */
	apisSource: DrainedList<ApiResponse>;
	/** The open tile's key. */
	openTileKey: string | null;
	onOpenTile: (key: string) => void;
	onCloseTile: () => void;
	onApprove: () => void;
	approvePending: boolean;
	/** This agent was just created and its APIs are the next step. */
	autoOpenAddApis: boolean;
	/** Spend the signal, so re-selecting this agent later does not reopen the tray. */
	onAutoOpenAddApisConsumed: () => void;
	/** APIs already chosen: the auto-open skips the tray and queues these. */
	autoQueueApis: SelectedApi[];
	/** Owned by the parent, because this panel remounts on every agent switch. */
	queueBatch: PreflightItem[];
	onQueueBatchChange: (agentId: string, items: PreflightItem[]) => void;
}

/** Stable empty batch, so an agent with nothing pending doesn't re-render. */
const EMPTY_BATCH: PreflightItem[] = [];
const EMPTY_PICKS: SelectedApi[] = [];

/** DOM id of the API access sidebar panel (the tiles' aria-controls target). */
const API_ACCESS_SIDEBAR_ID = 'api-access-sidebar';

function SelectedAgentPanel({
	agent,
	credentialsSource,
	apisSource,
	openTileKey,
	onOpenTile,
	onCloseTile,
	onApprove,
	approvePending,
	autoOpenAddApis,
	onAutoOpenAddApisConsumed,
	autoQueueApis,
	queueBatch,
	onQueueBatchChange,
}: SelectedAgentPanelProps) {
	const reducedMotion = useReducedMotionConfig();
	/** Which step of the Add-APIs flow is on screen. */
	const [addStep, setAddStep] = useState<'closed' | 'tray' | 'queue'>('closed');
	/** Set while the tray is editing the queue's batch (the queue's Back). The queue
	 * stays mounted meanwhile so its progress survives; `remaining` is what the
	 * queue would have handed back, kept for a tray that is closed, not continued. */
	const [batchEdit, setBatchEdit] = useState<{
		seed: QueueBackSeed;
		remaining: PreflightItem[];
	} | null>(null);

	const bindingsQuery = useAgentCredentialBindings(agent.id);
	const bindings = bindingsQuery.data;

	// One mutation for the whole grid, keyed back to a tile by the credential the
	// in-flight call names. Suspend is `unbind` WITHOUT `purge`, so it reverses.
	const suspendBinding = useUnbindAgentCredential(agent.id);
	const resumeBinding = useResumeAgentCredentialBinding(agent.id);
	const pendingBindingCredentialId =
		suspendBinding.isPending && suspendBinding.variables?.purge !== true
			? suspendBinding.variables?.credentialId
			: resumeBinding.isPending
				? resumeBinding.variables
				: null;
	// Bindings whose credential was deleted are hidden (no tile, no count) and
	// purged quietly — but only once proven: an `org:admin` viewer, a complete
	// credentials list, and the credential missing from it. For anyone else every
	// binding stays live (see `isOrphanBinding` for why the weaker signals fail).
	const viewerIsAdmin = viewerIsOrgAdmin(useOptionalCurrentUser());
	const credentialsProven = credentialsSource.complete && !credentialsSource.error;
	const { live: liveBindings, orphans: orphanBindings } = useMemo(
		() =>
			partitionBindings(bindings ?? [], credentialsSource.items, {
				viewerIsAdmin,
				credentialsComplete: credentialsProven,
			}),
		[bindings, credentialsSource.items, viewerIsAdmin, credentialsProven],
	);
	// Rules are only read for bindings that draw a tile (the one place they show),
	// so a binding serving nothing — possibly a deleted credential a non-admin
	// can't prove gone — never fires a read that 404s.
	const credentialIds = useMemo(
		() => liveBindings.filter((b) => b.serves.length > 0).map((b) => b.credentialId),
		[liveBindings],
	);
	const ruleSummaries = useAgentBindingRuleSummaries(agent.id, credentialIds);
	const purgeableOrphanIds = useMemo(
		() => orphanBindings.map((b) => b.credentialId),
		[orphanBindings],
	);
	usePurgeOrphanBindings(agent.id, purgeableOrphanIds);

	const tiles = useMemo(
		() => composeApiTiles(liveBindings, credentialsSource.items, apisSource.items),
		[liveBindings, credentialsSource.items, apisSource.items],
	);
	const stats = useMemo(() => tileStats(tiles), [tiles]);
	// APIs reached through several credentials: each such tile names its credential
	// and carries a chip saying how a call picks between them.
	const multiAccount = useMemo(() => multiAccountApis(tiles), [tiles]);
	const tileAccountLabels = useMemo(() => accountLabels(tiles), [tiles]);

	// The same per-actor read the console's KPI strip makes; `null` on 403.
	const usageQuery = useActorUsageDetail(agent.id);
	const executionsQuery = useActorExecutions(agent.id);

	// Resolved live from the current composition, so suspend/resume shows at once.
	// A key with no tile closes the sidebar, but only once bindings have loaded.
	const openTile =
		openTileKey != null ? (tiles.find((t) => t.key === openTileKey) ?? null) : null;
	useEffect(() => {
		if (openTileKey != null && bindings && !tiles.some((t) => t.key === openTileKey)) {
			onCloseTile();
		}
	}, [openTileKey, bindings, tiles, onCloseTile]);
	// Blast radius: the OTHER tiles this credential's binding fans out to.
	const siblingApiTitles = useMemo(
		() =>
			openTile
				? tiles
						.filter((t) => t.bindingId === openTile.bindingId && t.key !== openTile.key)
						.map((t) => t.title)
				: [],
		[openTile, tiles],
	);

	// A grid joined against a PARTIAL list asserts states it can't prove: hold the
	// skeleton while either source drains. No live bindings, no gate — a hidden
	// orphan alone must not hold the skeleton.
	const hasBindings = liveBindings.length > 0;
	const sourcesError = credentialsSource.error ?? apisSource.error;
	const sourcesDraining = !sourcesError && (!credentialsSource.complete || !apisSource.complete);

	// `undefined` = still loading → skeleton; `null` = failed → em-dash. A
	// bindings-less agent skips the drain gate and renders honest zeros.
	const bindingsFailed = Boolean(bindingsQuery.error);
	const joinFailed = bindingsFailed || (hasBindings && Boolean(sourcesError));
	const joinLoading =
		!joinFailed && (bindingsQuery.isPending || (hasBindings && sourcesDraining));
	const stripAccess = joinLoading ? undefined : joinFailed ? null : stats;
	// The grid's own length, so the number beside "APIs" is what is on screen.
	const apiCount = joinLoading || joinFailed ? null : tiles.length;
	const stripCredentialCount = bindingsQuery.isPending
		? undefined
		: bindingsFailed
			? null
			: liveBindings.length;
	// Monitor figures: `null` (403 or failure) → the strip omits them.
	const stripUsage = usageQuery.isError ? null : usageQuery.data;
	const stripLastActivity = executionsQuery.isError
		? null
		: executionsQuery.data === undefined
			? undefined
			: executionsQuery.data === null
				? null
				: { at: executionsQuery.data.items[0]?.startedAt ?? null };

	// `disabled` says it in the tile family rather than a banner.
	const serving = agent.status === 'active';
	const bannerStatus: BanneredStatus | null =
		agent.status === 'active' || agent.status === 'disabled' ? null : agent.status;

	const isArchived = agent.status === 'archived';
	// Only pending (cannot authenticate yet), rejected and archived block binding.
	const canBind = agent.status === 'active' || agent.status === 'disabled';
	const bindBlockedReason =
		agent.status === 'pending'
			? 'Approve this agent before giving it APIs.'
			: agent.status === 'rejected'
				? 'A rejected agent cannot be given APIs.'
				: isArchived
					? 'An archived agent cannot be given APIs.'
					: null;

	// Re-entry lands on the queue while a batch is owed — those picks are decided.
	// "Owed" is judged against the live bindings whenever the queue is shut: an item
	// a new binding now serves (bound by the queue, or elsewhere meanwhile) is done,
	// so it leaves the batch rather than holding "Finish adding N" or reopening. A
	// second-account item is not settled by the account the agent already had.
	// Never pruned while the queue is open — it tracks its own progress.
	const queueShut = addStep === 'closed';
	const owedBatch = useMemo(
		() =>
			queueShut && bindings !== undefined
				? stillOwedItems(queueBatch, liveBindings)
				: queueBatch,
		[queueShut, bindings, queueBatch, liveBindings],
	);
	useEffect(() => {
		if (owedBatch !== queueBatch) onQueueBatchChange(agent.id, owedBatch);
	}, [owedBatch, queueBatch, onQueueBatchChange, agent.id]);
	const openAddApis = (): void => setAddStep(owedBatch.length > 0 ? 'queue' : 'tray');

	// Bound only while the verb is available, so it never fires a no-op.
	useHotkey('a', openAddApis, canBind && addStep === 'closed');

	// Hold the signal until the agent can actually bind, then spend it opening the
	// tray. Consuming before the `canBind` gate would drop a live intent for a
	// not-yet-approved agent; once it's approvable the same signal still fires.
	// APIs already chosen skip the tray: they are preflighted exactly as the
	// tray's Continue would, then handed to the queue — a pick the agent already
	// reaches included, which adds another credential. A preflight that can't run
	// (a failed read) falls back to the tray.
	const preflight = usePreflightInputs(bindings);
	const bindingsFailedToLoad = bindingsQuery.isError;
	useEffect(() => {
		if (!autoOpenAddApis || !canBind) return;
		if (
			autoQueueApis.length === 0 ||
			preflight.credentialsSource.error ||
			bindingsFailedToLoad
		) {
			onAutoOpenAddApisConsumed();
			setAddStep('tray');
			return;
		}
		if (!preflight.ready) return;
		onAutoOpenAddApisConsumed();
		onQueueBatchChange(agent.id, preflightApis(autoQueueApis, preflight.inputs));
		setAddStep('queue');
	}, [
		autoOpenAddApis,
		canBind,
		onAutoOpenAddApisConsumed,
		autoQueueApis,
		bindingsFailedToLoad,
		preflight.credentialsSource.error,
		preflight.ready,
		preflight.inputs,
		onQueueBatchChange,
		agent.id,
	]);

	const addApisButton = !isArchived && (
		<span className="flex items-center gap-2">
			<Button size="sm" disabled={!canBind} onClick={openAddApis}>
				<Plus className="h-4 w-4" />
				{owedBatch.length > 0
					? `Finish adding ${owedBatch.length} ${owedBatch.length === 1 ? 'API' : 'APIs'}`
					: 'Add APIs'}
			</Button>
			{bindBlockedReason && (
				<span className="text-muted-foreground text-xs">{bindBlockedReason}</span>
			)}
		</span>
	);

	return (
		<motion.section
			aria-label={`APIs for ${agent.name}`}
			initial={reducedMotion ? false : { opacity: 0, y: 8 }}
			animate={{ opacity: 1, y: 0 }}
			transition={{ duration: 0.18, ease: 'easeOut' }}
			className="space-y-4"
		>
			{/* Identity lives in the selected tab and the APIs band, so the header carries
			    only the description — one line until asked. */}
			{agent.description && (
				<ExpandableText lines={1} className="text-muted-foreground text-sm">
					{agent.description}
				</ExpandableText>
			)}

			{bannerStatus && (
				<StateBanner
					status={bannerStatus}
					denialReason={agent.denialReason}
					onApprove={onApprove}
					approvePending={approvePending}
				/>
			)}

			<div className="space-y-2">
				<div className="flex flex-wrap items-center justify-between gap-2">
					<h2 className="flex items-baseline gap-1.5 text-sm font-medium">
						APIs
						{apiCount != null && (
							<span className="text-muted-foreground text-xs tabular-nums">
								{apiCount}
							</span>
						)}
					</h2>
					{addApisButton}
				</div>
				<AgentStatStrip
					agentName={agent.name}
					access={stripAccess}
					credentialCount={stripCredentialCount}
					usage={stripUsage}
					lastActivity={stripLastActivity}
				/>
			</div>

			{bindingsQuery.isPending || (hasBindings && sourcesDraining) ? (
				<div
					role="status"
					aria-live="polite"
					aria-busy="true"
					className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4"
				>
					<span className="sr-only">Loading APIs…</span>
					{[0, 1, 2].map((i) => (
						<Skeleton key={i} className="h-44 rounded-xl" />
					))}
				</div>
			) : bindingsQuery.error ? (
				<ErrorAlert message={bindingsQuery.error as Error} />
			) : hasBindings && sourcesError ? (
				<ErrorAlert
					message="Couldn't load the credential and API details behind these tiles."
					onRetry={() => {
						if (credentialsSource.error) credentialsSource.retry();
						if (apisSource.error) apisSource.retry();
					}}
				/>
			) : tiles.length === 0 ? (
				<Card className="border-dashed p-6">
					<h3 className="text-sm font-semibold">{agent.name} can reach nothing yet</h3>
					<p className="text-muted-foreground mt-2 max-w-prose text-sm">
						{NO_APIS_COPY[agent.status]}
					</p>
				</Card>
			) : (
				<div
					className={cn(
						// A non-active agent's TILE FAMILY is what reads inactive. The band,
						// its verb and the dock stay full strength and clickable.
						!serving && 'saturate-[.35]',
					)}
				>
					<div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4">
						{tiles.map((tile) => (
							<ApiTile
								key={tile.key}
								tile={tile}
								rules={ruleSummaries.get(tile.credentialId)}
								onOpen={() => onOpenTile(tile.key)}
								onSuspend={() =>
									suspendBinding.mutate({ credentialId: tile.credentialId })
								}
								onResume={() => resumeBinding.mutate(tile.credentialId)}
								bindingPending={pendingBindingCredentialId === tile.credentialId}
								agentServing={serving}
								expanded={openTileKey === tile.key}
								sidebarId={API_ACCESS_SIDEBAR_ID}
								accountLabel={tileAccountLabels.get(tile.key)}
								accountCount={multiAccount.get(tileApiKey(tile))?.count ?? 1}
							/>
						))}
					</div>
				</div>
			)}

			{/* The tray keeps its draft across a dismissal so it stays mounted; the queue
			    mounts only while it owns a batch. The tray opens once the bindings read
			    settles: while it is pending every bound API would look new. A failed read
			    opens it on the error with a retry, and nothing continues until the read
			    succeeds — an unknown binding set would offer bound credentials again. */}
			{canBind && (
				<>
					<AddApisTray
						open={
							addStep === 'tray' && (bindings !== undefined || bindingsFailedToLoad)
						}
						onClose={() => {
							// Closing mid-edit is closing the flow: the batch waits, unedited.
							if (batchEdit) onQueueBatchChange(agent.id, batchEdit.remaining);
							setBatchEdit(null);
							setAddStep('closed');
						}}
						agentId={agent.id}
						agentName={agent.name}
						bindings={bindings ?? []}
						bindingsError={bindingsFailedToLoad ? (bindingsQuery.error as Error) : null}
						onRetryBindings={() => void bindingsQuery.refetch()}
						bindingsRetrying={bindingsQuery.isFetching}
						seed={batchEdit?.seed ?? null}
						onContinue={(items) => {
							onQueueBatchChange(agent.id, items);
							setBatchEdit(null);
							// An edit that unticked everything still owed leaves nothing to set up.
							setAddStep(items.length > 0 ? 'queue' : 'closed');
						}}
					/>
					{queueBatch.length > 0 &&
						(addStep === 'queue' || (addStep === 'tray' && batchEdit != null)) && (
							<ApiSetupQueue
								open={addStep === 'queue'}
								agentId={agent.id}
								agentName={agent.name}
								items={queueBatch}
								onClose={(remaining) => {
									onQueueBatchChange(agent.id, remaining);
									setAddStep('closed');
								}}
								onBack={(seed, remaining) => {
									setBatchEdit({ seed, remaining });
									setAddStep('tray');
								}}
							/>
						)}
				</>
			)}

			<ApiAccessSidebar
				agent={agent}
				tile={openTile}
				siblingApiTitles={siblingApiTitles}
				accountCount={openTile ? (multiAccount.get(tileApiKey(openTile))?.count ?? 1) : 1}
				open={openTileKey != null}
				onClose={onCloseTile}
				sidebarId={API_ACCESS_SIDEBAR_ID}
			/>
		</motion.section>
	);
}
