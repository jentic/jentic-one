/**
 * FlatAgentsSection — the flat Agents surface: pending banner, agent strip, and
 * the selected agent's workspace (`SelectedAgentPanel`). Selection lives in
 * `?agent=<id>` so it is linkable.
 *
 * With no fleet in the org it shows `FirstAgentLanding`, where the first
 * self-registered agent is approved and given its first API before the fleet
 * view takes over. The landing's state (resume on load, the roster poll, the
 * exits) is `useFirstAgentLanding`; its rules are `lib/firstRun.ts`.
 *
 * A caller known to lack `agents:read`, or a refused roster read (403), sees
 * "No access to agents". A `?agent=` the whole roster does not hold shows
 * "Agent not found" rather than another agent, or the first-agent landing when
 * the roster is empty.
 */
import {
	useCallback,
	useEffect,
	useLayoutEffect,
	useMemo,
	useRef,
	useState,
	type CSSProperties,
} from 'react';
import { useSearchParams } from 'react-router';
import { useReducedMotionConfig } from 'framer-motion';
import { AgentInitialsProvider, ErrorAlert, Skeleton } from '@/shared/ui';
import { shellScroller, shellScrollTop } from '@/shared/lib/shellScroll';
import {
	readRecentIds,
	useEagerCursorDrain,
	useFleetInitials,
	useIsRenderedPathCurrent,
} from '@/shared/hooks';
import { useAllApis, useAllCredentials, type SelectedApi } from '@/shared/credentials/api';
import {
	useAgentsCredentialBindings,
	usePendingAgents,
	useApproveAgent,
	useDenyAgent,
	useDisableAgent,
	useArchiveAgent,
	isAgentsAccessDenied,
	isAgentsSessionEnded,
	type ActorStatus,
	type AgentEntity,
} from '@/modules/agents/api';
import {
	agentApiCount,
	agentSetupGapCount,
	partitionBindings,
} from '@/modules/agents/lib/apiTiles';
import { viewerIsOrgAdmin } from '@/shared/credentials/lib/bindAuthority';
import {
	AGENTS_READ,
	useCanAccess,
	useOptionalCurrentUser,
	usePermissionsKnown,
} from '@/shared/auth';
import { AgentStrip, CARD_HANDOFF_MS } from '@/modules/agents/components/flat/AgentStrip';
import { SelectedAgentPanel } from '@/modules/agents/components/flat/SelectedAgentPanel';
import { STRIP_RECENT_STORAGE_KEY } from '@/modules/agents/lib/stripOrder';
import { PendingApprovalBanner } from '@/modules/agents/components/flat/PendingApprovalBanner';
import {
	LifecycleDialogs,
	type PendingConfirm,
} from '@/modules/agents/components/LifecycleDialogs';
import { NewAgentPanel } from '@/modules/agents/components/flat/NewAgentPanel';
import { FirstAgentLanding } from '@/modules/agents/components/flat/FirstAgentLanding';
import {
	AgentNotFound,
	AgentsNoAccess,
	AgentsSessionEnded,
} from '@/modules/agents/components/flat/AgentAccessStates';
import type { PreflightItem } from '@/modules/agents/lib/apiPreflight';
import { useFirstAgentLanding } from '@/modules/agents/lib/useFirstAgentLanding';
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

/** Stable empty batch, so an agent with nothing pending doesn't re-render. */
const EMPTY_BATCH: PreflightItem[] = [];
const EMPTY_PICKS: SelectedApi[] = [];

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
	const clearAgentParam = useCallback(
		() =>
			setSearchParams(
				(prev) => {
					const next = new URLSearchParams(prev);
					next.delete('agent');
					return next;
				},
				{ replace: true },
			),
		[setSearchParams],
	);
	// Known to lack `agents:read`: the roster is not requested (see
	// `useFirstAgentLanding`), and the page reads as a refused one would.
	const canReadAgents = useCanAccess(AGENTS_READ);
	const permissionsKnown = usePermissionsKnown();
	const rosterWithheld = permissionsKnown && !canReadAgents;

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
		selectAgent,
		selectedAgentId: agentParam,
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
	// One reading of every avatar's letters, shared with every other surface
	// that draws these agents, so the strip, the card and Monitor agree.
	const fleetInitials = useFleetInitials(agents);

	// Until the URL catches up with an exit's hand-off, the handed-off agent is
	// the one on screen.
	const shownId = agentParam ?? landing.handoffAgentId;
	// An id the settled, whole roster does not hold is not this caller's agent
	// (another user's, an unclaimed one only an admin sees, or none). A roster
	// still loading or refetching — just after a create — may not have it yet,
	// and a cached one may predate an agent registered since, so the roster is
	// read once more before the id is judged; once judged, a later background
	// refetch does not flip it back. Until then nothing is selected, rather than
	// another agent in its place.
	const rosterSettled = query.isSuccess && !query.hasNextPage && !query.isFetching && !isError;
	const paramInRoster = agentParam != null && agents.some((a) => a.id === agentParam);
	const paramUnknown =
		agentParam != null && !paramInRoster && agentParam !== landing.handoffAgentId;
	const [recheckedId, setRecheckedId] = useState<string | null>(null);
	const recheckRequested = useRef<string | null>(null);
	const { refetch: refetchRoster } = query;
	useEffect(() => {
		if (!paramUnknown || !rosterSettled || recheckRequested.current === agentParam) return;
		recheckRequested.current = agentParam;
		void refetchRoster().finally(() => setRecheckedId(agentParam));
	}, [paramUnknown, rosterSettled, agentParam, refetchRoster]);
	const [missingId, setMissingId] = useState<string | null>(null);
	const agentNotFound =
		paramUnknown && ((rosterSettled && recheckedId === agentParam) || missingId === agentParam);
	useEffect(() => {
		setMissingId(agentNotFound ? agentParam : null);
	}, [agentNotFound, agentParam]);
	// With nothing named, the agent last shown here (the strip's history, which
	// the selection writes) is shown again, so coming back to Agents resumes
	// rather than re-pinning the first tab — and the history is not rewritten
	// around a fallback. A fleet with no history yet opens on its first tab,
	// the newest agent waiting for a decision.
	const lastShownId = useMemo(() => {
		if (shownId != null) return null;
		const ids = new Set(agents.map((a) => a.id));
		return readRecentIds(STRIP_RECENT_STORAGE_KEY).find((id) => ids.has(id)) ?? null;
	}, [shownId, agents]);
	const selected = paramUnknown
		? null
		: (agents.find((a) => a.id === shownId) ??
			agents.find((a) => a.id === lastShownId) ??
			agents[0] ??
			null);
	// Written back only once the fleet view is decided and on screen, so the
	// landing leaves the URL plain — and not while a hand-off's own selection
	// is still on its way.
	const fleetShown = landing.ready && !landing.visible;
	const fallbackId =
		agentParam == null && fleetShown && landing.handoffAgentId == null
			? (selected?.id ?? null)
			: null;
	// Not once a navigation away is under way: this page stays mounted until the
	// destination renders, and a write-back then would replace that navigation
	// with this page's own URL.
	const isRenderedPathCurrent = useIsRenderedPathCurrent();
	useEffect(() => {
		if (fallbackId != null && isRenderedPathCurrent())
			selectAgent(fallbackId, { replace: true });
	}, [fallbackId, selectAgent, isRenderedPathCurrent]);

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
		landing.finishedElsewhere(agent.id);
	}

	// Same cache slice the nav badge polls; `atLeast` hedges an incomplete drain.
	const { agents: pendingAgents, atLeast: pendingAtLeast } = usePendingAgents();

	const disable = useDisableAgent();
	const archive = useArchiveAgent();
	const [confirm, setConfirm] = useState<PendingConfirm>(null);
	// The dock's agent-scoped sheets. One slot, so two can never stack.
	const [dockSurface, setDockSurface] = useState<AgentDockSurface | null>(null);
	// The strip's height (where the card pins) and the pinned card's (0: it
	// doesn't pin) — together, what a row's reveal scrolls clear of. Both are
	// measured: the strip reports its full height and its condensed one (less
	// the header row, which slides away while the card is stuck).
	const [stripHeight, setStripHeight] = useState(0);
	const [stripCondensedHeight, setStripCondensedHeight] = useState(0);
	const onStripHeight = useCallback((full: number, condensed: number) => {
		setStripHeight(full);
		setStripCondensedHeight(condensed);
	}, []);
	const [pinnedCardHeight, setPinnedCardHeight] = useState(0);
	const [cardStuck, setCardStuck] = useState(false);
	// One picker, opened from the strip (`+N`, the search button, ⌘K) and the
	// card's name alike.
	// The card follows the selection once the strip's tab has landed: the new
	// card is a remount of the whole panel (~50ms on a big fleet), and made
	// mid-flight it stalls the tab where it moves fastest. Held by id, so the
	// shown agent is always the roster's current copy; at once under reduced
	// motion, on a first selection, or when the shown agent is gone.
	const reducedMotionPref = useReducedMotionConfig() ?? false;
	// Every switch counts, so a quick A → B → A still lands on a fresh card.
	const [switchGen, setSwitchGen] = useState(0);
	const [genFor, setGenFor] = useState(selected?.id ?? null);
	if (genFor !== (selected?.id ?? null)) {
		setGenFor(selected?.id ?? null);
		setSwitchGen((g) => g + 1);
	}
	const [held, setHeld] = useState<{ id: string | null; gen: number }>({
		id: selected?.id ?? null,
		gen: 0,
	});
	const heldAgent = held.id == null ? null : (agents.find((a) => a.id === held.id) ?? null);
	const handingOff =
		!reducedMotionPref && selected != null && heldAgent != null && held.gen !== switchGen;
	const panelAgent = handingOff ? heldAgent : selected;
	const panelKey = `${panelAgent?.id}:${handingOff ? held.gen : switchGen}`;
	useEffect(() => {
		const next = { id: selected?.id ?? null, gen: switchGen };
		const settle = () =>
			setHeld((prev) => (prev.id === next.id && prev.gen === next.gen ? prev : next));
		if (!handingOff) return settle();
		const t = window.setTimeout(settle, CARD_HANDOFF_MS);
		return () => window.clearTimeout(t);
	}, [selected?.id, switchGen, handingOff]);
	const [pickerOpen, setPickerOpen] = useState(false);
	const openPicker = useCallback(() => setPickerOpen(true), []);
	// An agent switch starts the new card at rest: back to the top, unpinned
	// and unfolded (the panel remounts per agent, which resets the fold).
	const selectedId = selected?.id ?? null;
	const previousId = useRef(selectedId);

	// The open tile's key. Selecting another agent closes the sidebar.
	const [openTileKey, setOpenTileKey] = useState<string | null>(null);
	useEffect(() => {
		setOpenTileKey(null);
		// Each sheet is handed `agent={selected}`, so one left open across a tab
		// switch would re-point at the new agent.
		setDockSurface(null);
		const had = previousId.current;
		previousId.current = selectedId;
		if (had && selectedId && had !== selectedId && shellScrollTop() > 0) {
			shellScroller().scrollTo({ top: 0 });
		}
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

	// The tab badge's tooltip: credentials per agent off the bindings already read,
	// and Blocked credentials for the agent whose rules were read (the selected one).
	// Counted as the card counts them: live bindings only, a proven orphan (its
	// credential deleted) left out on the same proof (`partitionBindings`).
	const viewerIsAdmin = viewerIsOrgAdmin(useOptionalCurrentUser());
	const credentialsProven = credentialsSource.complete && !credentialsSource.error;
	const credentialCounts = useMemo(() => {
		const proof = { viewerIsAdmin, credentialsComplete: credentialsProven };
		const map = new Map<string, number>();
		for (const [id, bindings] of bindingsByAgent) {
			map.set(id, partitionBindings(bindings, credentials, proof).live.length);
		}
		return map;
	}, [bindingsByAgent, credentials, viewerIsAdmin, credentialsProven]);
	const [blockedCounts, setBlockedCounts] = useState<ReadonlyMap<string, number>>(new Map());
	/** `null` forgets the agent's figure: deselected, or its join not proven. */
	const reportBlocked = useCallback((agentId: string, blocked: number | null) => {
		setBlockedCounts((prev) => {
			if (blocked === null ? !prev.has(agentId) : prev.get(agentId) === blocked) return prev;
			const next = new Map(prev);
			if (blocked === null) next.delete(agentId);
			else next.set(agentId, blocked);
			return next;
		});
	}, []);

	const firstPageFailed = Boolean(query.error && !query.data);
	const loading = query.isPending || !landing.ready;
	const landingWanted = !rosterWithheld && !firstPageFailed && !loading && landing.visible;
	// A `?agent=` the roster does not hold is judged before the landing shows, so an
	// empty roster reads "Agent not found" for it too, not the first-agent landing.
	const landingShown = landingWanted && !paramUnknown;
	// Before paint, so the header's label never disagrees with the body.
	useLayoutEffect(() => onLandingChange(landingShown), [landingShown, onLandingChange]);

	// Rendered by every branch below: the header's "New agent" flips `createOpen`
	// from outside, and a loading roster would otherwise swallow the click. The
	// lifecycle confirms serve the landing, the panel and the fleet.
	const overlays = (
		<>
			<NewAgentPanel
				open={createOpen}
				onClose={() => setCreateOpen(false)}
				onCreated={handleAgentCreated}
				initialName={landingShown ? landing.commandName : undefined}
				approve={approve}
				deny={deny}
				onDeny={({ id, name }) => setConfirm({ kind: 'deny', id, name })}
				onExit={(agent, to) => {
					selectAgent(agent.id);
					setAddApisFor(
						to.kind === 'skip'
							? null
							: { agentId: agent.id, queue: to.kind === 'queue' ? to.apis : [] },
					);
					landing.finishedElsewhere(agent.id);
				}}
				onShowFleet={(agent) => selectAgent(agent.id)}
			/>
			<LifecycleDialogs
				confirm={confirm}
				onClose={() => setConfirm(null)}
				disableBody="Disabling immediately revokes this agent's ability to authenticate. You can re-enable it later."
				mutations={{ deny, disable, archive }}
			/>
		</>
	);

	if (rosterWithheld) {
		return (
			<>
				<AgentsNoAccess />
				{overlays}
			</>
		);
	}

	// A failed FIRST page is a dead surface; a failed LATER page keeps the loaded
	// fleet on screen, with the inline notice below offering the retry.
	if (firstPageFailed) {
		return (
			<>
				{isAgentsAccessDenied(query.error) ? (
					<AgentsNoAccess />
				) : isAgentsSessionEnded(query.error) ? (
					<AgentsSessionEnded />
				) : (
					<ErrorAlert message={query.error as Error} />
				)}
				{overlays}
			</>
		);
	}

	const loadingState = (
		<div role="status" aria-live="polite" aria-busy="true" className="space-y-6">
			<span className="sr-only">Loading agents…</span>
			{/* Shaped like the strip (header row, then the tab rail), so the
			    first paint doesn't reflow. */}
			<div className="space-y-1.5 pt-2">
				<Skeleton className="h-4 w-36 rounded" />
				<Skeleton className="h-11 w-full max-w-md rounded-lg" />
			</div>
			<div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4">
				{[0, 1, 2].map((i) => (
					<Skeleton key={i} className="bg-surface-1 h-[154px] rounded-lg" />
				))}
			</div>
		</div>
	);

	// Until the resume decision is known, neither the fleet nor the landing.
	if (loading) {
		return (
			<>
				{loadingState}
				{overlays}
			</>
		);
	}

	// The landing's roster does not hold the linked agent: "Agent not found" once
	// the roster has been read again, loading until then. "Show my agents" drops
	// the link, which brings the landing back.
	if (landingWanted && paramUnknown) {
		return (
			<>
				{agentNotFound ? <AgentNotFound onShowAgents={clearAgentParam} /> : loadingState}
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
					commandName={landing.commandName}
					registerNameDuplicateOf={landing.registerNameDuplicateOf}
					expectedName={landing.expectedName}
					morePending={landing.morePending}
					onShowFleet={landing.showFleet}
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

			{/* The strip and the selected agent's card sit flush: the strip's notch
			    points into the card's top edge. */}
			<AgentInitialsProvider initials={fleetInitials}>
				<div
					style={
						{
							// A pinned card means a condensed strip over it.
							'--agent-pinned-clearance': `${(pinnedCardHeight > 0 ? stripCondensedHeight : stripHeight) + pinnedCardHeight + 16}px`,
						} as CSSProperties
					}
				>
					<AgentStrip
						onHeight={onStripHeight}
						cardPinned={Boolean(selected) && pinnedCardHeight > 0}
						condensed={Boolean(selected) && cardStuck}
						pickerOpen={pickerOpen}
						onPickerOpenChange={setPickerOpen}
						agents={agents}
						selectedId={selected?.id ?? null}
						onSelect={selectAgent}
						setupGaps={setupGaps}
						apiCounts={apiCounts}
						credentialCounts={credentialCounts}
						blockedCounts={blockedCounts}
						filter={filter}
						incomplete={isError || Boolean(hasNextPage)}
					/>
					{panelAgent && (
						<SelectedAgentPanel
							key={panelKey}
							agent={panelAgent}
							credentialsSource={credentialsSource}
							apisSource={apisSource}
							openTileKey={openTileKey}
							onOpenTile={setOpenTileKey}
							onCloseTile={() => setOpenTileKey(null)}
							onApprove={() => approve.mutate(panelAgent.id)}
							approvePending={
								approve.isPending && approve.variables === panelAgent.id
							}
							onDeny={() =>
								setConfirm({
									kind: 'deny',
									id: panelAgent.id,
									name: panelAgent.name,
								})
							}
							autoOpenAddApis={addApisFor?.agentId === panelAgent.id}
							autoQueueApis={
								addApisFor?.agentId === panelAgent.id
									? addApisFor.queue
									: EMPTY_PICKS
							}
							onAutoOpenAddApisConsumed={clearAddApisFor}
							queueBatch={queueBatches[panelAgent.id] ?? EMPTY_BATCH}
							onQueueBatchChange={setQueueBatchFor}
							onBlockedCount={reportBlocked}
							pinning={{
								stickyTop: stripHeight,
								stuckTop: stripCondensedHeight,
								onPinnedHeight: setPinnedCardHeight,
								onStuckChange: setCardStuck,
							}}
							onSwitchAgent={openPicker}
						/>
					)}
				</div>
			</AgentInitialsProvider>

			{isError && (
				<ErrorAlert
					message="Couldn't load the rest of the fleet — some agents may be missing."
					onRetry={() => void fetchNextPage()}
					retrying={isFetchingNextPage}
				/>
			)}

			{agentNotFound && (
				<AgentNotFound
					onShowAgents={agents.length > 0 ? () => selectAgent(agents[0].id) : undefined}
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
