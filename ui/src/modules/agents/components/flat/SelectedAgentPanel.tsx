/**
 * SelectedAgentPanel — the selected agent's card, stat strip, non-active state
 * banner, "Can call" list/card workspace, Add-APIs tray/queue, and API access
 * sidebar. Tiles are composed client-side from reads the app already makes — no
 * new endpoints.
 */
import { useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { motion, useReducedMotionConfig } from 'framer-motion';
import { Plus } from 'lucide-react';
import { ActorLabel, Button, ErrorAlert, ExpandableText, Skeleton, Tooltip } from '@/shared/ui';
import { cn } from '@/shared/lib/utils';
import { useHotkey, usePersistedChoice, usePinStack } from '@/shared/hooks';
import type { ApiResponse, Credential, DrainedList, SelectedApi } from '@/shared/credentials/api';
import {
	useAgentCredentialBindings,
	useAgentBindingRuleSummaries,
	useRetryBindingRules,
	useActorUsageDetail,
	useActorExecutions,
	useActorApiUsage,
	useActorRecentCalls,
	useUnbindAgentCredential,
	usePurgeOrphanBindings,
	useResumeAgentCredentialBinding,
	ACTION_LABEL,
	ACTION_VARIANT,
	type ActorStatus,
	type AgentEntity,
} from '@/modules/agents/api';
import {
	accountLabels,
	composeApiTiles,
	distinctApiCount,
	multiAccountApis,
	partitionBindings,
	tileApiKey,
	tileStats,
	type ApiTileModel,
} from '@/modules/agents/lib/apiTiles';
import { viewerIsOrgAdmin } from '@/shared/credentials/lib/bindAuthority';
import {
	AGENTS_WRITE,
	CREDENTIALS_WRITE,
	useCanAccess,
	useOptionalCurrentUser,
} from '@/shared/auth';
import { AGENT_PANEL_ID, stripTabId } from '@/modules/agents/components/flat/AgentStrip';
import { AgentStatStrip } from '@/modules/agents/components/flat/AgentStatStrip';
import { AgentCard, type AgentCardPinning } from '@/modules/agents/components/flat/AgentCard';
import {
	StateBannerFrame,
	type BanneredStatus,
} from '@/modules/agents/components/flat/agentCardParts';
import { ApiRow } from '@/modules/agents/components/flat/ApiRow';
import { ExpandAllToggle } from '@/modules/agents/components/flat/ExpandAllToggle';
import { ApiCard } from '@/modules/agents/components/flat/ApiCard';
import { ApiViewToggle } from '@/modules/agents/components/flat/ApiViewToggle';
import { ApiViewSwitch } from '@/modules/agents/components/flat/ApiViewSwitch';
import { API_CARD_GRID, API_CARD_HEIGHT } from '@/modules/agents/components/flat/apiCardGrid';
import { LENS_CARD_VARIANTS, type LensItem } from '@/modules/agents/lib/apiViewMotion';
import { TreeBranch } from '@/modules/agents/components/flat/TreeBranch';
import { API_VIEWS, API_VIEW_STORAGE_KEY, DEFAULT_API_VIEW } from '@/modules/agents/lib/apiView';
import {
	rowActivity,
	tileUsageApiId,
	type ApiRowActivity,
} from '@/modules/agents/lib/apiRowActivity';
import { isBlockedStatus, deriveTileStatus } from '@/modules/agents/lib/tileStatus';
import { ApiAccessSidebar } from '@/modules/agents/components/flat/ApiAccessSidebar';
import { AgentNameText } from '@/modules/agents/components/AgentNameText';
import { ApprovalGrantNote } from '@/modules/agents/components/ApprovalGrantNote';
import { AddApisTray } from '@/modules/agents/components/flat/AddApisTray';
import { ApiSetupQueue } from '@/modules/agents/components/flat/ApiSetupQueue';
import {
	preflightApis,
	stillOwedItems,
	type PreflightItem,
} from '@/modules/agents/lib/apiPreflight';
import type { QueueBackSeed } from '@/modules/agents/lib/setupQueue';
import { usePreflightInputs } from '@/modules/agents/lib/usePreflightInputs';

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

/** The notice above the grid — and, for pending, the decision itself. */
function StateBanner({
	agentId,
	status,
	denialReason,
	deniedBy,
	onApprove,
	approvePending,
	onDeny,
}: {
	agentId: string;
	status: BanneredStatus;
	denialReason: string | null;
	deniedBy: string | null;
	onApprove: () => void;
	approvePending: boolean;
	onDeny: () => void;
}) {
	const grantId = useId();
	// Approving or denying needs `agents:write` (or `org:admin`); anyone else
	// reads the state without the verbs.
	const canDecide = useCanAccess(AGENTS_WRITE);
	const detail =
		status === 'pending' && !canDecide
			? 'Not serving traffic. Someone who can manage agents needs to approve it.'
			: NON_ACTIVE_COPY[status].detail;
	return (
		<StateBannerFrame
			role="status"
			data-testid={`agent-state-banner-${status}`}
			status={status}
			title={NON_ACTIVE_COPY[status].title}
			detail={
				<>
					{detail}
					{status === 'rejected' && denialReason && <> Reason: {denialReason}</>}
					{status === 'rejected' && deniedBy && (
						<>
							{' '}
							Denied by <ActorLabel actorId={deniedBy} />.
						</>
					)}
					{status === 'pending' && (
						<>
							{' '}
							<ApprovalGrantNote agentId={agentId} id={grantId} />
						</>
					)}
				</>
			}
			actions={
				// The banner pins the longest-waiting agent only; any OTHER pending
				// agent is decided here, so both verbs sit on its own panel — in the
				// order and weights every approval surface uses: Approve, then Deny.
				status === 'pending' && canDecide ? (
					<>
						<Button
							size="sm"
							variant={ACTION_VARIANT.approve}
							loading={approvePending}
							onClick={onApprove}
							aria-describedby={grantId}
							data-testid="state-banner-approve"
						>
							{ACTION_LABEL.approve}
						</Button>
						<Button
							size="sm"
							variant={ACTION_VARIANT.deny}
							disabled={approvePending}
							onClick={onDeny}
							data-testid="state-banner-deny"
						>
							{ACTION_LABEL.deny}
						</Button>
					</>
				) : undefined
			}
		/>
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

export interface SelectedAgentPanelProps {
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
	/** Open the page's reason-required deny dialog for this (pending) agent. */
	onDeny: () => void;
	/** This agent was just created and its APIs are the next step. */
	autoOpenAddApis: boolean;
	/** Spend the signal, so re-selecting this agent later does not reopen the tray. */
	onAutoOpenAddApisConsumed: () => void;
	/** APIs already chosen: the auto-open skips the tray and queues these. */
	autoQueueApis: SelectedApi[];
	/** Owned by the parent, because this panel remounts on every agent switch. */
	queueBatch: PreflightItem[];
	onQueueBatchChange: (agentId: string, items: PreflightItem[]) => void;
	/** Report this agent's Blocked credential count, for its tab badge's tooltip. */
	/** The agent's Blocked-credential figure, or `null` once it can't be claimed
	 * (the join is loading or failed, or the panel has gone). */
	onBlockedCount: (agentId: string, blocked: number | null) => void;
	/** Sticky offsets and pin-state callbacks under the measured agent strip. */
	pinning: AgentCardPinning;
	/** Opens the agent picker (the card's name is its button). */
	onSwitchAgent: () => void;
}

/** One row's verbs, made once per row and kept while its credential holds. */
interface RowCallbacks {
	credentialId: string;
	onRetryRules: () => void;
	onOpen: () => void;
	onOpenRules: () => void;
	onManage: () => void;
	onTogglePin: () => void;
	onSuspend: () => void;
	onResume: () => void;
}

/** DOM id of the API access sidebar panel (the tiles' aria-controls target). */
const API_ACCESS_SIDEBAR_ID = 'api-access-sidebar';

export function SelectedAgentPanel({
	agent,
	credentialsSource,
	apisSource,
	openTileKey,
	onOpenTile,
	onCloseTile,
	onApprove,
	approvePending,
	onDeny,
	autoOpenAddApis,
	onAutoOpenAddApisConsumed,
	autoQueueApis,
	queueBatch,
	onQueueBatchChange,
	onBlockedCount,
	pinning,
	onSwitchAgent,
}: SelectedAgentPanelProps) {
	const reducedMotion = useReducedMotionConfig();
	// The list⇄cards lens for this agent's "Can call" area, persisted so a
	// reload keeps it. Workspace-wide, not per agent.
	const [apiView, setApiView] = usePersistedChoice(
		API_VIEW_STORAGE_KEY,
		API_VIEWS,
		DEFAULT_API_VIEW,
	);
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
	const retryRules = useRetryBindingRules(agent.id);
	// A Blocked status opens the sheet ON its rules editor: the tile key whose
	// next open should land on "Add rule" (spent by the sheet once focused).
	const [rulesFocusKey, setRulesFocusKey] = useState<string | null>(null);
	/** The list rows held open on purpose — by click, tap, chevron or keyboard
	 * — any number at once, so two credentials can be compared side by side.
	 * Per agent (the panel remounts for another one) and per lens (switching
	 * to cards and back starts clean). */
	const pins = usePinStack();
	const rowsListId = useId();
	/** Open a row's sheet; that row goes back to rest behind it (its pin goes
	 * — it would otherwise be a stale pin under the sheet). Other pins stay:
	 * the comparison is still there when the sheet closes. */
	const openSheetFor = (key: string) => {
		pins.unpin(key);
		onOpenTile(key);
	};
	const purgeableOrphanIds = useMemo(
		() => orphanBindings.map((b) => b.credentialId),
		[orphanBindings],
	);
	usePurgeOrphanBindings(agent.id, purgeableOrphanIds);

	const tiles = useMemo(
		() => composeApiTiles(liveBindings, credentialsSource.items, apisSource.items),
		[liveBindings, credentialsSource.items, apisSource.items],
	);
	const stats = useMemo(
		() => tileStats(tiles, (tile) => ruleSummaries.get(tile.credentialId)),
		[tiles, ruleSummaries],
	);
	// APIs reached through several credentials: each such tile names its credential
	// and carries a chip saying how a call picks between them.
	const multiAccount = useMemo(() => multiAccountApis(tiles), [tiles]);
	const tileAccountLabels = useMemo(() => accountLabels(tiles), [tiles]);

	// The per-actor 7-day usage rollup; `null` on 403.
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
	// The API registry is ENRICHMENT, not the grid's subject: a binding's own
	// `serves` tuple draws a tile without it (untitled, no icon, no operation
	// count). So a failed — or forbidden, for a viewer without `apis:read` —
	// `GET /apis` degrades the tiles rather than replacing the whole grid with an
	// error card whose Try again can never succeed (#1543). The credential list
	// still gates the grid: it decides ownership, orphans and sign-in state, and
	// tiles that quietly assert those unread is the defect above it.
	const apisDegraded = Boolean(apisSource.error);
	const sourcesError = credentialsSource.error;
	const sourcesDraining =
		!sourcesError && (!credentialsSource.complete || (!apisDegraded && !apisSource.complete));

	// `undefined` = still loading → skeleton; `null` = failed → em-dash. A
	// bindings-less agent skips the drain gate and renders honest zeros.
	const bindingsFailed = Boolean(bindingsQuery.error);
	const joinFailed = bindingsFailed || (hasBindings && Boolean(sourcesError));
	const joinLoading =
		!joinFailed && (bindingsQuery.isPending || (hasBindings && sourcesDraining));
	const stripAccess = joinLoading ? undefined : joinFailed ? null : stats;
	// The grid's own APIs, so the number beside "APIs" is what is on screen —
	// one per API, however many credentials draw it.
	const apiCount = joinLoading || joinFailed ? null : distinctApiCount(tiles);
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
	// Only pending (cannot authenticate yet), rejected and archived block binding,
	// and binding needs `agents:write` (or `org:admin`).
	const canManage = useCanAccess(AGENTS_WRITE);
	// Finishing a sign-in is a credential write, as in the access sidebar.
	const canWriteCredentials = useCanAccess(CREDENTIALS_WRITE);
	const statusAllowsBind = agent.status === 'active' || agent.status === 'disabled';
	const canBind = statusAllowsBind && canManage;
	const bindBlockedReason =
		statusAllowsBind && !canManage
			? 'Adding APIs needs permission to manage agents.'
			: agent.status === 'pending'
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

	// The rows' traffic: each API's 7-day rollup per credential, and the agent's
	// newest calls — both read once for the whole list.
	const usageApiIds = useMemo(
		() =>
			[...new Set(tiles.map(tileUsageApiId).filter((id): id is string => id != null))].sort(),
		[tiles],
	);
	const apiUsage = useActorApiUsage(agent.id, usageApiIds);
	const recentCallsQuery = useActorRecentCalls(agent.id);
	const recentCalls = recentCallsQuery.isError ? null : recentCallsQuery.data;

	useEffect(() => {
		onBlockedCount(agent.id, joinLoading || joinFailed ? null : stats.blockedBindings);
	}, [agent.id, stats.blockedBindings, joinLoading, joinFailed, onBlockedCount]);
	// Deselected (or remounted): the strip stops claiming a figure no read backs.
	useEffect(() => () => onBlockedCount(agent.id, null), [agent.id, onBlockedCount]);

	const openRulesFor = (key: string) => {
		setRulesFocusKey(key);
		openSheetFor(key);
	};
	// The tree's "Add APIs", also on the card where it is easy to find: the
	// same flow, tonal (the page keeps no solid primary of its own).
	const addApiButton = (
		<Button
			variant="tonal"
			className="w-full"
			disabled={!canBind}
			onClick={openAddApis}
			data-testid="card-add-api"
		>
			<Plus className="h-4 w-4" />
			Add API
		</Button>
	);
	// A disabled button can't take focus, so the reason rides on the focusable
	// wrapper (the Tooltip's non-interactive mode), as elsewhere in the app.
	const addApi =
		!isArchived &&
		(bindBlockedReason ? (
			<Tooltip content={bindBlockedReason} placement="bottom">
				{addApiButton}
			</Tooltip>
		) : (
			<span className="inline-flex">{addApiButton}</span>
		));
	const cardAction = addApi || null;

	// The "Can call" tree's branches (`TreeBranch`) draw the Library ledger's
	// trunk + elbows as real elements, so the list entrance can grow them.
	// The last child is "+ Add APIs": its elbow lands on the 32px button's
	// centre, which lines up with the rows' avatars.
	const showAddApis = !isArchived;
	const renderAddApisItem = (item: LensItem) => (
		<TreeBranch key="add-apis" lands="button" item={item}>
			<span className="flex flex-wrap items-center gap-2">
				<Button variant="tonal" size="sm" disabled={!canBind} onClick={openAddApis}>
					<Plus className="h-4 w-4" />
					{owedBatch.length > 0
						? `Finish adding ${owedBatch.length} ${owedBatch.length === 1 ? 'API' : 'APIs'}`
						: 'Add APIs'}
				</Button>
				{bindBlockedReason && (
					<span className="text-muted-foreground text-xs">{bindBlockedReason}</span>
				)}
			</span>
		</TreeBranch>
	);

	// The list⇄cards lens rides in the agent card's header, after its action —
	// never on a row of its own between the card and the tree, so the trunk
	// still starts 3px under the card. It appears only once there is something
	// to switch (`hasBindings` and a composed tile): the skeleton, error,
	// archived-empty and reaches-nothing branches stay list-style. It carries
	// ONLY the toggle: the stat strip already counts the APIs and credentials.
	const showViewToggle = hasBindings && !sourcesError && !sourcesDraining && tiles.length > 0;
	// Pins of rows no longer drawn don't count.
	const anyPinned = pins.keys.some((key) => tiles.some((t) => t.key === key));
	// One Expand all ⇄ Collapse all in list view, between the card's action
	// and the lens toggle: Collapse all whenever any row is open.
	const viewToggle = showViewToggle ? (
		<div className="flex items-center gap-2">
			{apiView === 'list' && (
				<ExpandAllToggle
					anyOpen={anyPinned}
					onExpandAll={() => pins.pinAll(tiles.map((t) => t.key))}
					onCollapseAll={pins.clear}
					controls={rowsListId}
				/>
			)}
			<ApiViewToggle
				value={apiView}
				onChange={(view) => {
					// Another lens, or back: no row is still pinned.
					pins.clear();
					setApiView(view);
				}}
				ariaLabel={`Layout for the APIs ${agent.name} can call`}
			/>
		</div>
	) : null;

	// A Blocked card can route its open through the rules editor like the row's
	// `onOpenRules`; a plain open is fine for the rest.
	const openCardFor = (tile: ApiTileModel) => {
		const tileStatus = deriveTileStatus({
			suspended: tile.suspended,
			agentServing: serving,
			awaitingConsent: tile.awaitingConsent,
			rules: ruleSummaries.get(tile.credentialId),
		});
		if (isBlockedStatus(tileStatus) && canManage) openRulesFor(tile.key);
		else {
			setRulesFocusKey(null);
			openSheetFor(tile.key);
		}
	};

	// The rows are memoised, and the panel re-renders on every pin, peek and
	// sticky-height change: each row's props hold still unless its own inputs
	// move. Its traffic is derived once per change of the reads behind it, and
	// its verbs are per-key callbacks that read the latest handlers when fired.
	const activityByKey = useMemo(
		() => new Map(tiles.map((t) => [t.key, rowActivity(t, apiUsage, recentCalls)])),
		[tiles, apiUsage, recentCalls],
	);
	const rowVerbs = {
		tiles,
		openSheetFor,
		openRulesFor,
		openCardFor,
		toggle: pins.toggle,
		retryRules,
		suspend: suspendBinding.mutate,
		resume: resumeBinding.mutate,
	};
	const rowVerbsRef = useRef(rowVerbs);
	useLayoutEffect(() => {
		rowVerbsRef.current = rowVerbs;
	});
	const rowCallbacks = useRef(new Map<string, RowCallbacks>());
	const callbacksFor = useCallback((tile: ApiTileModel): RowCallbacks => {
		const cached = rowCallbacks.current.get(tile.key);
		if (cached && cached.credentialId === tile.credentialId) return cached;
		const { key, credentialId } = tile;
		const latestTile = () => rowVerbsRef.current.tiles.find((t) => t.key === key) ?? tile;
		const made: RowCallbacks = {
			credentialId,
			onRetryRules: () => rowVerbsRef.current.retryRules(credentialId),
			onOpen: () => {
				// A plain open never inherits a rules focus that didn't land.
				setRulesFocusKey(null);
				rowVerbsRef.current.openSheetFor(key);
			},
			onOpenRules: () => rowVerbsRef.current.openRulesFor(key),
			onManage: () => rowVerbsRef.current.openCardFor(latestTile()),
			onTogglePin: () => rowVerbsRef.current.toggle(key),
			onSuspend: () => rowVerbsRef.current.suspend({ credentialId }),
			onResume: () => rowVerbsRef.current.resume(credentialId),
		};
		rowCallbacks.current.set(key, made);
		return made;
	}, []);

	// The grid's dashed "+ Add API" affordance — the SAME flow as the tree's
	// Add APIs, with the Tooltip-on-a-focusable-wrapper reason pattern the card
	// button already uses when binding is blocked.
	const addApiTileButton = (
		<Button
			variant="ghost"
			size="sm"
			data-testid="card-add-api-tile"
			disabled={!canBind}
			onClick={openAddApis}
			className={cn(
				API_CARD_HEIGHT,
				'border-hairline-field text-foreground-sub flex w-full gap-2 rounded-[14px] border border-dashed px-0 py-0 text-[12.5px] font-bold active:scale-100',
				'transition-[color,background-color,border-color] duration-150 motion-reduce:transition-none',
				'focus-visible:shadow-[0_0_0_1.5px_hsl(var(--primary)/0.65)] focus-visible:ring-0 focus-visible:ring-offset-0',
				canBind
					? 'hover:bg-surface-1/50 hover:text-foreground hover:border-control-edge/40'
					: 'hover:text-foreground-sub hover:bg-transparent disabled:opacity-60',
			)}
		>
			<Plus className="h-4 w-4" />
			Add API
		</Button>
	);
	const addApiTile =
		!isArchived &&
		(bindBlockedReason ? (
			<Tooltip content={bindBlockedReason} placement="bottom" className="w-full">
				{addApiTileButton}
			</Tooltip>
		) : (
			addApiTileButton
		));

	// Items in either lens: one per tile, plus the trailing Add API(s) item.
	const lensItems = tiles.length + (showAddApis ? 1 : 0);

	const cardsGrid = (
		<ul
			data-testid="can-call-cards"
			className={cn(
				// Responsive dense grid (`API_CARD_GRID`): 2-up on phones, 3
				// between, ≥4 cols at ≥1100px, 5 at ≥1680, 12px apart — every cell
				// one fixed height. No tree trunk/elbows in this lens.
				// Flush with the agent card's edges (no tree in this lens), one
				// grid gap under it.
				API_CARD_GRID,
				'pt-3',
			)}
		>
			{tiles.map((tile, i) => (
				<motion.li
					key={tile.key}
					custom={{ i, n: lensItems }}
					variants={LENS_CARD_VARIANTS}
					className="flex min-w-0"
				>
					<ApiCard
						tile={tile}
						rules={ruleSummaries.get(tile.credentialId)}
						agentServing={serving}
						accountLabel={tileAccountLabels.get(tile.key)}
						accountCount={multiAccount.get(tileApiKey(tile))?.count ?? 1}
						expanded={openTileKey === tile.key}
						sidebarId={API_ACCESS_SIDEBAR_ID}
						onOpen={() => openCardFor(tile)}
					/>
				</motion.li>
			))}
			{addApiTile && (
				<motion.li
					custom={{ i: tiles.length, n: lensItems }}
					variants={LENS_CARD_VARIANTS}
					className="flex min-w-0"
				>
					{addApiTile}
				</motion.li>
			)}
		</ul>
	);

	const rowsTree = (
		<ul
			id={rowsListId}
			className={cn(
				// The 3px gap under the parent the ledger leaves before its trunk.
				'pt-[3px] pl-[31px]',
			)}
		>
			{tiles.map((tile, i) => {
				const verbs = callbacksFor(tile);
				return (
					<TreeBranch key={tile.key} lands="row" item={{ i, n: lensItems }}>
						<ApiRow
							agentId={agent.id}
							tile={tile}
							rules={ruleSummaries.get(tile.credentialId)}
							onRetryRules={verbs.onRetryRules}
							activity={activityByKey.get(tile.key) as ApiRowActivity}
							onOpen={verbs.onOpen}
							onOpenRules={verbs.onOpenRules}
							onManage={verbs.onManage}
							pinned={pins.isPinned(tile.key)}
							onTogglePin={verbs.onTogglePin}
							// Pause and resume are binding writes (`agents:write`).
							onSuspend={canManage ? verbs.onSuspend : undefined}
							onResume={canManage ? verbs.onResume : undefined}
							bindingPending={pendingBindingCredentialId === tile.credentialId}
							agentServing={serving}
							expanded={openTileKey === tile.key}
							sidebarId={API_ACCESS_SIDEBAR_ID}
							accountLabel={tileAccountLabels.get(tile.key)}
							accountCount={multiAccount.get(tileApiKey(tile))?.count ?? 1}
							canConnect={canWriteCredentials}
						/>
					</TreeBranch>
				);
			})}
			{showAddApis && renderAddApisItem({ i: tiles.length, n: lensItems })}
		</ul>
	);

	return (
		// No entrance on the panel itself: the card's frame holds still under the
		// strip's notch through a switch (its content fades in on its own), and
		// only the list below rises in.
		<section
			id={AGENT_PANEL_ID}
			role="tabpanel"
			aria-labelledby={stripTabId(agent.id)}
			className="space-y-3"
		>
			<AgentCard
				agent={agent}
				lastActivity={stripLastActivity}
				action={cardAction}
				pinning={pinning}
				onSwitchAgent={onSwitchAgent}
				fadeIn
				viewToggle={viewToggle}
				description={
					agent.description ? (
						<ExpandableText lines={1} className="text-muted-foreground text-sm">
							{agent.description}
						</ExpandableText>
					) : null
				}
				banner={
					bannerStatus ? (
						<StateBanner
							agentId={agent.id}
							status={bannerStatus}
							denialReason={agent.denialReason}
							deniedBy={agent.attribution.deniedBy}
							onApprove={onApprove}
							approvePending={approvePending}
							onDeny={onDeny}
						/>
					) : null
				}
				kpis={
					agent.status === 'pending' || isArchived ? null : (
						<AgentStatStrip
							agentName={agent.name}
							apiCount={joinLoading ? undefined : apiCount}
							access={stripAccess}
							credentialCount={stripCredentialCount}
							usage={stripUsage}
							lastActivity={stripLastActivity}
						/>
					)
				}
			/>

			<motion.section
				aria-label={`APIs ${agent.name} can call`}
				data-testid="can-call"
				initial={reducedMotion ? false : { opacity: 0, y: 8 }}
				animate={{ opacity: 1, y: 0 }}
				transition={{ duration: 0.18, ease: 'easeOut' }}
				className="-mt-3"
			>
				{bindingsQuery.isPending || (hasBindings && sourcesDraining) ? (
					<div
						role="status"
						aria-live="polite"
						aria-busy="true"
						className="space-y-2 pt-[13px] pl-[65px]"
					>
						<span className="sr-only">Loading APIs…</span>
						{[0, 1, 2].map((i) => (
							<Skeleton key={i} className="bg-surface-1 h-16 rounded-[14px]" />
						))}
					</div>
				) : bindingsQuery.error ? (
					<>
						<div className="pt-[13px] pl-[65px]">
							<ErrorAlert message={bindingsQuery.error as Error} />
						</div>
						{/* Still reachable: the tray holds on the failed read and retries it. */}
						{showAddApis && (
							<ul className="pt-[3px] pl-[31px]">
								{renderAddApisItem({ i: 0, n: 1 })}
							</ul>
						)}
					</>
				) : hasBindings && sourcesError ? (
					<div className="pt-[13px] pl-[65px]">
						<ErrorAlert
							message="Couldn't load the credential details behind these rows."
							onRetry={() => credentialsSource.retry()}
						/>
					</div>
				) : tiles.length === 0 && isArchived ? (
					<div className="pt-[13px] pl-[65px]">
						<p
							data-testid="can-call-empty"
							className="bg-surface-1/55 text-foreground-sub rounded-[14px] px-4 py-3.5 text-[12.5px]"
						>
							{NO_APIS_COPY[agent.status]}
						</p>
					</div>
				) : tiles.length === 0 ? (
					<ul className={cn('pt-[3px] pl-[31px]', !serving && 'saturate-[.35]')}>
						<TreeBranch lands="row" item={{ i: 0, n: 2 }}>
							<div
								data-testid="can-call-empty"
								className="bg-surface-1/55 max-w-prose rounded-[14px] px-4 py-3.5 text-[12.5px]"
							>
								{/* The card above names the agent; here it appears once, cut
								    to its budget, so a long name can't swallow the line. */}
								<h3 className="font-heading text-foreground-name text-sm font-semibold">
									<AgentNameText name={agent.name} /> can reach nothing yet
								</h3>
								<p className="text-foreground-sub mt-1">
									{NO_APIS_COPY[agent.status]}
								</p>
							</div>
						</TreeBranch>
						{showAddApis && renderAddApisItem({ i: 1, n: 2 })}
					</ul>
				) : (
					<>
						{/* Non-blocking: the rows below are drawn from the bindings
						    themselves, just without the registry's titles and icons. */}
						{apisDegraded && (
							<div className="pt-[13px] pl-[65px]">
								<div
									role="status"
									data-testid="agent-apis-degraded"
									className="bg-surface-inset flex flex-wrap items-center gap-3 rounded-lg px-4 py-2.5"
								>
									<p className="text-muted-foreground min-w-0 flex-1 text-sm">
										These APIs are showing without their details — the API list
										couldn&rsquo;t be read. Access is unaffected.
									</p>
									<Button
										variant="tonal"
										size="xs"
										onClick={() => apisSource.retry()}
									>
										Try again
									</Button>
								</div>
							</div>
						)}
						{/* A non-active agent's ROWS/CARDS are what read inactive — the
						    card, its verb and the dock stay full strength and clickable.
						    Switching the lens is a staggered fade/lift with the height
						    tweened between the layouts (see `ApiViewSwitch`); reduced
						    motion swaps instantly. */}
						<ApiViewSwitch view={apiView} className={cn(!serving && 'saturate-[.35]')}>
							{apiView === 'cards' ? cardsGrid : rowsTree}
						</ApiViewSwitch>
					</>
				)}
			</motion.section>

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
				onClose={() => {
					setRulesFocusKey(null);
					onCloseTile();
				}}
				sidebarId={API_ACCESS_SIDEBAR_ID}
				agentServing={serving}
				focusRules={openTileKey != null && rulesFocusKey === openTileKey}
				onRulesFocused={() => setRulesFocusKey(null)}
			/>
		</section>
	);
}
