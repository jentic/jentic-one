/**
 * FlatAgentsSection — the flat Agents surface that replaces the roster table.
 *
 * One screen: the pending-approval banner (plan §4.10 — the page's most
 * urgent job keeps top billing, naming the longest-waiting agent with a live
 * clock), the agent pill strip that selects an agent in place (pending agents
 * grouped at its head, D15), the selected agent's APIs band (its one quiet
 * meta line of access stats merged with the 7-day vitals), and its API tile
 * grid. Selection persists in `?agent=<id>` so a selected agent is
 * linkable; the per-agent console at `/agents/:id` stays alive behind this
 * surface for deep links, but every fact it holds is reachable from the dock.
 *
 * Data is client-side composition — agent → bindings → credential → the APIs
 * it serves — over reads the app already has (`GET /agents/{id}/credentials`,
 * `GET /credentials`, `GET /apis`). No new endpoints.
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router';
import { motion, useReducedMotion } from 'framer-motion';
import { Archive, Bot, Clock, Plus, XCircle } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { Button, Card, EmptyState, ErrorAlert, Skeleton } from '@/shared/ui';
import { cn } from '@/shared/lib/utils';
import { useEagerCursorDrain, useHotkey } from '@/shared/hooks';
import {
	useAllApis,
	useAllCredentials,
	type ApiResponse,
	type Credential,
	type DrainedList,
} from '@/shared/credentials/api';
import {
	useAgents,
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
	useResumeAgentCredentialBinding,
	type ActorStatus,
	type AgentEntity,
} from '@/modules/agents/api';
import {
	agentApiCount,
	agentSetupGapCount,
	composeApiTiles,
	tileStats,
} from '@/modules/agents/lib/apiTiles';
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
import { DcrQuickstart } from '@/modules/agents/components/DcrQuickstart';
import { AddApisTray } from '@/modules/agents/components/flat/AddApisTray';
import { ApiSetupQueue } from '@/modules/agents/components/flat/ApiSetupQueue';
import type { PreflightItem } from '@/modules/agents/lib/apiPreflight';
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
}

export function FlatAgentsSection({ createOpen, setCreateOpen, filter }: FlatAgentsSectionProps) {
	const query = useAgents({ status: 'all' });

	// The strip is the fleet — it has no "Load more" affordance, so drain the
	// cursor pages eagerly until the roster is complete. The shared hook stops
	// the drain while the query is errored (a failed page would otherwise
	// re-fire forever); the inline notice below offers the retry.
	const { fetchNextPage, hasNextPage, isFetchingNextPage, isError } = query;
	useEagerCursorDrain({ hasNextPage, isFetchingNextPage, isError, fetchNextPage });

	const agents = useMemo(() => {
		const entities = query.data?.pages.flatMap((p) => p.entities) ?? [];
		return [...entities].sort(
			(a, b) =>
				STATUS_ORDER[a.status] - STATUS_ORDER[b.status] ||
				b.createdAt.localeCompare(a.createdAt),
		);
	}, [query.data]);

	// D10: the selected agent lives in the URL — `?agent=<id>` — so a selection
	// survives refresh and is shareable. An unknown/absent id falls back to the
	// first agent without rewriting the URL.
	const [searchParams, setSearchParams] = useSearchParams();
	const agentParam = searchParams.get('agent');
	const selected = agents.find((a) => a.id === agentParam) ?? agents[0] ?? null;

	function selectAgent(id: string) {
		setSearchParams(
			(prev) => {
				const next = new URLSearchParams(prev);
				next.set('agent', id);
				return next;
			},
			{ replace: false },
		);
	}

	// Requirement 4: creating an agent flows straight into the Add-APIs step.
	// The signal is the agent's ID rather than a boolean, because the new agent
	// only becomes selectable once the invalidated roster read lands — until
	// then `selected` is still whatever was on screen, and a boolean would open
	// the tray over the wrong agent.
	const [addApisFor, setAddApisFor] = useState<string | null>(null);
	const clearAddApisFor = useCallback(() => setAddApisFor(null), []);

	function handleAgentCreated(agent: AgentEntity, opts: { addApis: boolean }) {
		// Selected either way: the operator just named this agent, so the strip
		// owes them the screen for it.
		selectAgent(agent.id);
		setAddApisFor(opts.addApis ? agent.id : null);
	}

	// Approval keeps its prominence above the strip: the banner names the
	// longest-waiting pending agent off the SAME cache slice the nav badge
	// polls (plan §4.10), so removing the roster's band demotes nothing.
	// `atLeast` rides along so the banner hedges its count while the pending
	// drain is incomplete — same honesty contract as the badge's "N+".
	const { agents: pendingAgents, atLeast: pendingAtLeast } = usePendingAgents();
	const approve = useApproveAgent();
	const deny = useDenyAgent();
	const disable = useDisableAgent();
	const archive = useArchiveAgent();
	const [confirm, setConfirm] = useState<PendingConfirm>(null);
	// The dock's sheet surfaces (API key / activity / permissions / MCP /
	// settings — all agent-scoped, D20; the org-wide credential inventory
	// mounts from the page header instead). One slot: opening a surface
	// replaces the previous one, so two sheets can never stack from the dock
	// itself.
	const [dockSurface, setDockSurface] = useState<AgentDockSurface | null>(null);

	// D10: the API access sidebar's state is EPHEMERAL — no URL param — but
	// held HERE as one piece of state (the clicked tile's key) so a future
	// URL contract (risk O3, e.g. `?api=<key>`) is one wiring change, not a
	// refactor. Selecting a different agent closes it (the effect below
	// covers strip clicks, banner reviews, and history navigation alike).
	const [openTileKey, setOpenTileKey] = useState<string | null>(null);
	const selectedId = selected?.id ?? null;
	useEffect(() => {
		setOpenTileKey(null);
	}, [selectedId]);

	// Composition sources shared by the strip (gap hints) and the tile grid —
	// drained to EVERY page, because the join is over the whole workspace: a
	// first-page-only list silently mis-renders any credential/API past page 1.
	const credentialsSource = useAllCredentials();
	const apisSource = useAllApis();
	const credentials = credentialsSource.items;

	const agentIds = useMemo(
		() => agents.filter((a) => a.status !== 'archived').map((a) => a.id),
		[agents],
	);
	const bindingsByAgent = useAgentsCredentialBindings(agentIds);
	// Honesty gating (see SelectedAgentPanel for the grid's version): the
	// "N to set up" pill hint is a claim about the WHOLE credential list — a
	// partial (still-draining or failed) list can only miss awaiting-consent
	// credentials, so until the drain completes the strip shows no hints at
	// all rather than a wrong count.
	const setupGaps = useMemo(() => {
		const map = new Map<string, number>();
		if (!credentialsSource.complete) return map;
		for (const id of agentIds) {
			map.set(id, agentSetupGapCount(bindingsByAgent.get(id), credentials));
		}
		return map;
	}, [agentIds, bindingsByAgent, credentials, credentialsSource.complete]);

	// The count on each tab: how many APIs that agent reaches. Same honesty
	// gate as the grid it summarises — the join needs the WHOLE API registry
	// (a partial one resolves fewer wildcards, so the figure could only be
	// low), and an agent whose bindings haven't landed gets no entry at all,
	// which the strip renders as no count rather than a zero.
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

	// A failed FIRST page is a dead surface; a failed LATER page must keep the
	// loaded fleet rendered (the drain has stopped — the inline notice below
	// offers the retry that resumes it).
	if (query.error && !query.data) return <ErrorAlert message={query.error as Error} />;

	if (query.isPending) {
		return (
			<div role="status" aria-live="polite" aria-busy="true" className="space-y-6">
				<span className="sr-only">Loading agents…</span>
				{/* Shaped like the tab rail it becomes, so the first paint
				    doesn't reflow the page under the operator. */}
				<Skeleton className="h-11 w-full max-w-md rounded-lg" />
				<div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4">
					{[0, 1, 2].map((i) => (
						<Skeleton key={i} className="h-44 rounded-xl" />
					))}
				</div>
			</div>
		);
	}

	if (agents.length === 0) {
		return (
			<>
				<EmptyState
					icon={<Bot className="h-6 w-6" />}
					title="No agents yet"
					description="An agent either registers itself — landing here as pending, waiting for your approval — or you create one now and give it the APIs it needs in the same flow."
					action={
						<Button size="sm" onClick={() => setCreateOpen(true)}>
							<Plus className="h-4 w-4" />
							Create an agent
						</Button>
					}
				/>
				<DcrQuickstart />
				<AgentCreateSheet
					open={createOpen}
					onClose={() => setCreateOpen(false)}
					onCreated={handleAgentCreated}
				/>
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
					autoOpenAddApis={addApisFor === selected.id}
					onAutoOpenAddApisConsumed={clearAddApisFor}
				/>
			)}

			{selected && (
				<>
					{/* Plan §4.2: the selected agent's verbs live in the fixed
					    bottom dock. Approve shares the panel banner's mutation,
					    so both affordances go in-flight together (per-id). */}
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
						archivePending={archive.isPending}
					/>
				</>
			)}

			<LifecycleDialogs
				confirm={confirm}
				onClose={() => setConfirm(null)}
				entityType="agent"
				disableBody="Disabling immediately revokes this agent's ability to authenticate. You can re-enable it later."
				mutations={{ deny, disable, archive }}
			/>
			<AgentCreateSheet
				open={createOpen}
				onClose={() => setCreateOpen(false)}
				onCreated={handleAgentCreated}
			/>
		</>
	);
}

// ---------------------------------------------------------------------------
// Selected-agent panel — header, stat strip, non-active banner, tile grid
// ---------------------------------------------------------------------------

/**
 * Copy for the non-active banner: the state as a headline, what it means as a
 * quiet second line. Suspension stops traffic, not editing — so the copy says
 * "not serving traffic", never "read-only".
 *
 * `disabled` has NO banner. It is the one non-active state that carries no
 * information a notice could add: the tile family reads not-serving on its own
 * (dashed, `Not serving` chips) and the dock's red toggle is both the statement
 * and the way back. The three states that stay are the ones with something to
 * say — a decision to take, a reason to read, a sweep to explain.
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

/**
 * Per-state banner treatment (D8/D9): the states must never read identically,
 * so each carries its own icon and tint — urgency for the one that wants a
 * decision, danger for the refusal, quiet grey for the one that is simply
 * retired. The copy above still says "not serving traffic", never "read-only":
 * the tint is about attention, not editability.
 *
 * The tint is carried by the icon's own chip rather than washed across the
 * whole row, so the banner reads as a titled notice on the page surface
 * instead of a coloured slab.
 */
const NON_ACTIVE_BANNER: Record<BanneredStatus, { Icon: LucideIcon; shell: string; chip: string }> =
	{
		pending: {
			Icon: Clock,
			shell: 'border-warning/40 bg-warning/[0.04]',
			chip: 'bg-warning/15 text-warning',
		},
		rejected: {
			Icon: XCircle,
			shell: 'border-danger/40 bg-danger/[0.04]',
			chip: 'bg-danger/15 text-danger',
		},
		archived: {
			Icon: Archive,
			shell: 'border-border/70 bg-muted/20',
			chip: 'bg-muted-foreground/10 text-muted-foreground/70',
		},
	};

/**
 * The notice above the grid for a state that has something to say: what the
 * state is, what it means for traffic, and — for pending — the decision itself.
 */
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
	const { Icon, shell, chip } = NON_ACTIVE_BANNER[status];
	return (
		<div
			role="status"
			data-testid={`agent-state-banner-${status}`}
			className={cn(
				'flex flex-wrap items-center gap-x-3 gap-y-3 rounded-xl border p-3 sm:flex-nowrap',
				shell,
			)}
		>
			{/* The tint lives in the chip, which gives the icon a size worth
			    seeing and keeps the row itself close to the page surface. */}
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
 * Copy for an agent that reaches no API yet, per state.
 *
 * "No APIs" means something different in each state, and the generic line —
 * an identity that authenticates and fails every call — is only true of an
 * agent that is actually serving. Saying it of a pending or disabled agent
 * tells the operator the bind is the missing piece when approval or enabling
 * is.
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
	/** The API access sidebar's centralized state (D10) — the open tile's
	 * key, owned by FlatAgentsSection so a URL contract can be added later. */
	openTileKey: string | null;
	onOpenTile: (key: string) => void;
	onCloseTile: () => void;
	onApprove: () => void;
	approvePending: boolean;
	/** This agent was just created and its APIs are the next step (requirement 4). */
	autoOpenAddApis: boolean;
	/** Spend the signal, so re-selecting this agent later does not reopen the tray. */
	onAutoOpenAddApisConsumed: () => void;
}

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
}: SelectedAgentPanelProps) {
	const reducedMotion = useReducedMotion();
	/** Which step of the Add-APIs flow is on screen (plan §4.4). */
	const [addStep, setAddStep] = useState<'closed' | 'tray' | 'queue'>('closed');
	/**
	 * The batch the tray committed, and what the queue has left of it.
	 *
	 * This is the flow's memory: the queue is the only way an API arrives, so an
	 * operator who closes it half-way must not lose the rest of their picks —
	 * re-entering resumes the remainder instead of starting over (D13).
	 */
	const [queueBatch, setQueueBatch] = useState<PreflightItem[]>([]);

	const bindingsQuery = useAgentCredentialBindings(agent.id);
	const bindings = bindingsQuery.data;

	// Pause/resume for the tiles' own controls — instantiated ONCE for the
	// whole grid (a mutation per tile would multiply identical caches) and
	// keyed back to a tile by the credential the in-flight call names. The
	// suspend path is `unbind` WITHOUT `purge`: the binding and its rules
	// survive, which is what makes the verb reversible.
	const suspendBinding = useUnbindAgentCredential(agent.id);
	const resumeBinding = useResumeAgentCredentialBinding(agent.id);
	const pendingBindingCredentialId =
		suspendBinding.isPending && suspendBinding.variables?.purge !== true
			? suspendBinding.variables?.credentialId
			: resumeBinding.isPending
				? resumeBinding.variables
				: null;
	const credentialIds = useMemo(() => (bindings ?? []).map((b) => b.credentialId), [bindings]);
	const ruleSummaries = useAgentBindingRuleSummaries(agent.id, credentialIds);

	const tiles = useMemo(
		() =>
			bindings ? composeApiTiles(bindings, credentialsSource.items, apisSource.items) : [],
		[bindings, credentialsSource.items, apisSource.items],
	);
	const stats = useMemo(() => tileStats(tiles), [tiles]);

	// Monitor enrichment for the stat strip — the SAME per-actor reads the
	// console header's KPI strip makes (7-day usage rollup + the newest
	// execution). Both are keyed per agent id with a 30s staleTime (see the
	// hooks), so flipping between agents replays from cache instead of
	// refetch-storming, and both resolve `null` on 403 — the strip simply
	// omits those figures for non-admins.
	const usageQuery = useActorUsageDetail(agent.id);
	const executionsQuery = useActorExecutions(agent.id);

	// The sidebar's tile, resolved LIVE from the current composition so
	// suspend/resume/rule changes reflect immediately. A stale key (its tile
	// unbound from another surface) closes the sidebar rather than pinning a
	// ghost — only once the bindings have loaded, so a refetch flicker can't
	// slam it shut.
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

	// Honesty gating: a grid joined against a PARTIAL credential/API list
	// asserts states it can't prove — an awaiting-consent credential on a
	// later page renders wrongly solid, an imported API falls into the "not
	// imported" fallback, the "To set up" stat undercounts. Design choice:
	// hold the grid in the existing loading skeleton while either source is
	// still draining (the common one-page workspace completes in the same
	// round-trip as the bindings read, so the fast path stays fast), and when
	// a drain FAILED show the compact inline retry notice instead of
	// silently-wrong tiles. An agent with no bindings skips the gate — there
	// are no tiles to be wrong about.
	const hasBindings = (bindings?.length ?? 0) > 0;
	const sourcesError = credentialsSource.error ?? apisSource.error;
	const sourcesDraining = !sourcesError && (!credentialsSource.complete || !apisSource.complete);

	// Strip figures under the SAME honesty gate as the grid: the tile-derived
	// numbers are only claimed once the drained join can prove them
	// (`undefined` = still loading → skeleton; `null` = failed → em-dash).
	// A bindings-less agent (archived: swept; pending: none yet) skips the
	// drain gate exactly like the grid does and renders honest zeros.
	const bindingsFailed = Boolean(bindingsQuery.error);
	const joinFailed = bindingsFailed || (hasBindings && Boolean(sourcesError));
	const joinLoading =
		!joinFailed && (bindingsQuery.isPending || (hasBindings && sourcesDraining));
	const stripAccess = joinLoading ? undefined : joinFailed ? null : stats;
	// The heading's figure is the grid's own length — so the number beside
	// "APIs" is always exactly what is on screen, and is withheld (not guessed)
	// while the join is loading or has failed.
	const apiCount = joinLoading || joinFailed ? null : tiles.length;
	const stripCredentialCount = bindingsQuery.isPending
		? undefined
		: bindingsFailed
			? null
			: (bindings?.length ?? 0);
	// Monitor figures: `null` (403 or failure) → the strip omits them.
	const stripUsage = usageQuery.isError ? null : usageQuery.data;
	const stripLastActivity = executionsQuery.isError
		? null
		: executionsQuery.data === undefined
			? undefined
			: executionsQuery.data === null
				? null
				: { at: executionsQuery.data.items[0]?.startedAt ?? null };

	// Serving is the tile family's own state, and `disabled` says it there rather
	// than in a banner (see `NON_ACTIVE_COPY`).
	const serving = agent.status === 'active';
	const bannerStatus: BanneredStatus | null =
		agent.status === 'active' || agent.status === 'disabled' ? null : agent.status;

	const isArchived = agent.status === 'archived';
	// D9: a non-active agent stays fully editable — only pending (cannot
	// authenticate yet), rejected and archived block the bind flow.
	const canBind = agent.status === 'active' || agent.status === 'disabled';
	const bindBlockedReason =
		agent.status === 'pending'
			? 'Approve this agent before giving it APIs.'
			: agent.status === 'rejected'
				? 'A rejected agent cannot be given APIs.'
				: isArchived
					? 'An archived agent cannot be given APIs.'
					: null;

	// Re-entry lands back on the queue when a batch is still owed, because those
	// picks and their preflight are already decided — the tray would only ask the
	// operator to choose them a second time.
	const openAddApis = (): void => setAddStep(queueBatch.length > 0 ? 'queue' : 'tray');

	// `a` is the surface's one creative shortcut (the bar at the page foot
	// advertises it). Bound only while the verb is actually available, so it
	// never fires a no-op on an agent that cannot be given APIs, and never
	// stacks a second tray over the one already open.
	useHotkey('a', openAddApis, canBind && addStep === 'closed');

	// Requirement 4: the tray opens by itself on the agent that was just
	// created, so "create" and "give it APIs" read as one flow rather than two
	// screens the operator has to rejoin. The signal is spent on arrival —
	// re-selecting this agent tomorrow must not reopen the tray — and a freshly
	// created agent owes no queue batch, so the tray is always the right step.
	useEffect(() => {
		if (!autoOpenAddApis) return;
		onAutoOpenAddApisConsumed();
		if (canBind) setAddStep('tray');
	}, [autoOpenAddApis, canBind, onAutoOpenAddApisConsumed]);

	const addApisButton = !isArchived && (
		<span className="flex items-center gap-2">
			<Button size="sm" disabled={!canBind} onClick={openAddApis}>
				<Plus className="h-4 w-4" />
				{queueBatch.length > 0
					? `Finish adding ${queueBatch.length} ${queueBatch.length === 1 ? 'API' : 'APIs'}`
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
			{/* Identity lives in the strip's selected tab and the APIs band
			    below — the header repeats neither (the owner's 4×-name
			    critique), so the agent's own description is all it carries.
			    The section's aria-label still names the agent. */}
			{agent.description && (
				<p className="text-muted-foreground text-sm">{agent.description}</p>
			)}

			{bannerStatus && (
				<StateBanner
					status={bannerStatus}
					denialReason={agent.denialReason}
					onApprove={onApprove}
					approvePending={approvePending}
				/>
			)}

			{/* One band for the whole API story: the heading carries the number
			    that matters (how many APIs this agent reaches) and the verb that
			    changes it, with the supporting vitals as one quiet line beneath
			    — instead of a board of six figures that all read `0` on a fresh
			    agent. The agent's name is stated once, by the selected tab. */}
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
					{/* The band above states the surface; this states the state,
					    naming the agent so the sentence stands on its own. The
					    verb lives in the band — it is not repeated here. */}
					<h3 className="text-sm font-semibold">{agent.name} can reach nothing yet</h3>
					<p className="text-muted-foreground mt-2 max-w-prose text-sm">
						{NO_APIS_COPY[agent.status]}
					</p>
				</Card>
			) : (
				<div
					className={cn(
						// D8: a non-active agent's TILE FAMILY is what reads inactive
						// — dashed and recessed per tile (see `ApiTile`), desaturated
						// as a set so the vendor marks grey out with it. The band, its
						// verb and the dock stay full strength, and every control here
						// stays clickable: not serving is not read-only.
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
							/>
						))}
					</div>
				</div>
			)}

			{/* Plan §4.4, two steps: pick the whole set with its cost on screen,
			    then finish each one. The tray keeps its own draft across a
			    dismissal, so it stays mounted; the queue is mounted only while it
			    owns a batch. */}
			{canBind && (
				<>
					<AddApisTray
						open={addStep === 'tray'}
						onClose={() => setAddStep('closed')}
						agentId={agent.id}
						agentName={agent.name}
						bindings={bindings ?? []}
						onContinue={(items) => {
							setQueueBatch(items);
							setAddStep('queue');
						}}
					/>
					{addStep === 'queue' && queueBatch.length > 0 && (
						<ApiSetupQueue
							open
							agentId={agent.id}
							agentName={agent.name}
							items={queueBatch}
							onClose={(remaining) => {
								setQueueBatch(remaining);
								setAddStep('closed');
							}}
						/>
					)}
				</>
			)}

			{/* Plan §4.5: everything about one tile's access, in one panel. */}
			<ApiAccessSidebar
				agent={agent}
				tile={openTile}
				siblingApiTitles={siblingApiTitles}
				open={openTileKey != null}
				onClose={onCloseTile}
				sidebarId={API_ACCESS_SIDEBAR_ID}
			/>
		</motion.section>
	);
}
