/**
 * Agent detail page — the identity console for a single agent at
 * `/agents/:agentId` (router `basename` adds the `/app` prefix).
 *
 * Layout mirrors the toolkit console exactly: a shared `PageHeader` band (the
 * agent's badge as icon, its name as title, its own description as subtitle,
 * with the kill switch for the reversible active/disabled flip plus the
 * constructive Approve / Deny actions in the header action slot), a back row,
 * a denial banner when rejected, the KPI strip, then six tab panels:
 *   - Overview  → attribution meta + bound toolkits + audit slice
 *   - Activity  → this agent's execution volume + recent executions
 *                 (GET /monitoring/usage?agent_id=…, GET /executions?actor_id=…)
 *                 with a pre-filtered "Open Monitor" deep-link
 *   - Access    → platform scopes (#615) + filed access requests (#619)
 *   - Keys      → API-key metadata, generate/regenerate/revoke, rotation history
 *   - MCP       → per-agent MCP config card + session history (local-MCP 2-E2:
 *                 MCP is a transport of this agent, so the surface lives here)
 *   - Settings  → the copyable agent id + editable metadata (PATCH
 *                 /agents/{id}) + danger zone hosting the terminal Archive
 *
 * The active tab lives in `?tab=` (like Monitor's lenses) so every view is
 * shareable and back-button friendly. Activity/KPI sources are admin-gated:
 * 403s degrade to em-dashes / a quiet note, never an error surface.
 */
import { useState } from 'react';
import { useParams, useSearchParams } from 'react-router';
import {
	Activity as ActivityIcon,
	Fingerprint,
	Key,
	LayoutDashboard,
	Plug,
	Settings,
	ShieldCheck,
	ShieldX,
} from 'lucide-react';
import {
	AgentBadge,
	ActorLabel,
	AppLink,
	BackButton,
	Button,
	DetailSection,
	KillSwitch,
	LoadingState,
	PageHeader,
	PageShell,
	TabNav,
	type TabNavOption,
} from '@/shared/ui';
import { cn, formatTimestamp } from '@/shared/lib/utils';
import {
	useAgent,
	useAgentToolkits,
	useActorUsageDetail,
	useActorExecutions,
	useApproveAgent,
	useDenyAgent,
	useDisableAgent,
	useEnableAgent,
	useArchiveAgent,
	AgentsApiError,
	STATUS_DOT,
	ACTIONS_FOR_STATUS,
	ACTION_LABEL,
	ACTION_VARIANT,
	type AgentAction,
} from '@/modules/agents/api';
import { ActorStatusBadge } from '@/modules/agents/components/ActorStatusBadge';
import { ScopesCard } from '@/modules/agents/components/ScopesCard';
import { ActorAccessRequestsCard } from '@/modules/agents/components/ActorAccessRequestsCard';
import { ConnectedClientsCard } from '@/modules/agents/components/detail/ConnectedClientsCard';
import {
	LifecycleDialogs,
	type PendingConfirm,
} from '@/modules/agents/components/LifecycleDialogs';
import { KpiStrip } from '@/modules/agents/components/detail/KpiStrip';
import { MetaItem } from '@/modules/agents/components/detail/shared';
import { ActivityPanel } from '@/modules/agents/components/detail/ActivityPanel';
import { ActorAuditPanel } from '@/modules/agents/components/detail/ActorAuditPanel';
import { AgentKeysPanel } from '@/modules/agents/components/detail/AgentKeysPanel';
import { AgentSettingsPanel } from '@/modules/agents/components/detail/AgentSettingsPanel';
import { BoundToolkitsCard } from '@/modules/agents/components/detail/BoundToolkitsCard';
import { McpPanel } from '@/modules/agents/components/detail/McpPanel';
import { ROUTES, ROUTE_PATHS } from '@/shared/app/routes';

const DETAIL_TABS = ['overview', 'activity', 'access', 'keys', 'mcp', 'settings'] as const;
type DetailTab = (typeof DETAIL_TABS)[number];

/** Tab options for the console shell — same icon grammar as the toolkit console. */
const TAB_OPTIONS: TabNavOption<DetailTab>[] = [
	{ value: 'overview', label: 'Overview', icon: <LayoutDashboard className="h-4 w-4" /> },
	{ value: 'activity', label: 'Activity', icon: <ActivityIcon className="h-4 w-4" /> },
	{ value: 'access', label: 'Access', icon: <ShieldCheck className="h-4 w-4" /> },
	{ value: 'keys', label: 'Keys', icon: <Key className="h-4 w-4" /> },
	// MCP is a transport of this agent (local-MCP §3.10), so its config card
	// and session history live here in the console — no top-level nav.
	{ value: 'mcp', label: 'MCP', icon: <Plug className="h-4 w-4" /> },
	{ value: 'settings', label: 'Settings', icon: <Settings className="h-4 w-4" /> },
];

function isDetailTab(value: string | null): value is DetailTab {
	return DETAIL_TABS.includes(value as DetailTab);
}

const tabId = (tab: string) => `agent-tab-${tab}`;
const panelId = (tab: string) => `agent-panel-${tab}`;

export default function AgentDetailPage() {
	const { agentId } = useParams<{ agentId: string }>();
	const id = agentId ?? null;
	const [searchParams, setSearchParams] = useSearchParams();
	const tabParam = searchParams.get('tab');
	const activeTab: DetailTab = isDetailTab(tabParam) ? tabParam : 'overview';

	const agentQuery = useAgent(id);
	const toolkits = useAgentToolkits(id);
	// KPI-strip enrichment (admin-gated; resolves null on 403 — see hooks).
	const usage = useActorUsageDetail(id);
	const executions = useActorExecutions(id);

	const approve = useApproveAgent();
	const deny = useDenyAgent();
	const disable = useDisableAgent();
	const enable = useEnableAgent();
	const archive = useArchiveAgent();

	const [confirm, setConfirm] = useState<PendingConfirm>(null);

	function setTab(tab: string) {
		// Pushed (not replaced) so the browser back button walks tabs — same
		// contract as the toolkit console; the back link below is static for
		// exactly that reason.
		setSearchParams(
			(prev) => {
				const next = new URLSearchParams(prev);
				if (tab === 'overview') next.delete('tab');
				else next.set('tab', tab);
				return next;
			},
			{ replace: false },
		);
	}

	if (agentQuery.isPending) {
		return (
			<PageShell>
				<LoadingState message="Loading agent…" />
			</PageShell>
		);
	}

	// Only a 404 (or a missing route param) means "no such agent" — anything
	// else (403, 500, network) is a load failure and must not masquerade as
	// not-found.
	const errorStatus = agentQuery.error instanceof AgentsApiError ? agentQuery.error.status : null;
	if (agentQuery.error && errorStatus !== 404) {
		return (
			<PageShell>
				<PageHeader
					title="Couldn't load agent"
					subtitle={
						errorStatus === 403
							? 'You do not have permission to view this agent.'
							: 'Something went wrong while loading this agent. Try again.'
					}
				/>
				<BackButton to={ROUTES.agents} label="All agents" useHistory={false} />
			</PageShell>
		);
	}
	if (agentQuery.error || !agentQuery.data) {
		return (
			<PageShell>
				<PageHeader
					title="Agent not found"
					subtitle={id ? `No agent with id ${id}.` : 'Missing agent id.'}
				/>
				<BackButton to={ROUTES.agents} label="All agents" useHistory={false} />
			</PageShell>
		);
	}

	const agent = agentQuery.data;
	// The identity header offers the kill switch for the reversible
	// active/disabled flip (same control as the toolkit console) plus the
	// constructive actions (Approve / Deny); the terminal Archive lives in
	// the Settings tab's danger zone. `enable`/`disable` never render as
	// header buttons — the kill switch owns that verb pair.
	const killSwitchStatus = agent.status === 'active' || agent.status === 'disabled';
	const headerActions = ACTIONS_FOR_STATUS[agent.status].filter(
		(a) => a !== 'disable' && a !== 'archive' && a !== 'enable',
	);
	const actionPending =
		approve.isPending ||
		deny.isPending ||
		disable.isPending ||
		enable.isPending ||
		archive.isPending;

	/**
	 * Which specific header action is in flight (drives the per-button
	 * spinner). Only Approve / Deny render as header buttons — the
	 * enable/disable pair runs through the kill switch (its own spinner) and
	 * Archive behind the Settings danger zone's confirm dialog.
	 */
	const pendingAction: AgentAction | null = approve.isPending
		? 'approve'
		: deny.isPending
			? 'deny'
			: null;

	function handleAction(action: AgentAction) {
		switch (action) {
			case 'approve':
				approve.mutate(agent.id);
				break;
			case 'deny':
				setConfirm({ kind: 'deny', id: agent.id, name: agent.name });
				break;
		}
	}

	const lastActivityAt = executions.data?.items[0]?.startedAt ?? null;

	return (
		<PageShell>
			{/* Same header grammar as the toolkit console: badge as the icon,
			    the agent's own description as subtitle, status pinned beside the
			    constructive lifecycle actions. No second identity card below —
			    the header IS the identity surface. */}
			<PageHeader
				title={agent.name}
				subtitle={agent.description ?? undefined}
				icon={
					<div className="relative">
						<AgentBadge id={agent.id} name={agent.name} size="lg" />
						<span
							className={cn(
								'border-background absolute -right-0.5 -bottom-0.5 h-3 w-3 rounded-full border-2',
								STATUS_DOT[agent.status],
							)}
							aria-hidden
						/>
					</div>
				}
				actions={
					<>
						{killSwitchStatus ? (
							// Same reversible suspend/restore control as the toolkit
							// header — disable/enable is the agent's kill switch.
							<KillSwitch
								active={agent.status === 'active'}
								pending={disable.isPending || enable.isPending}
								onToggle={(next) =>
									next ? enable.mutate(agent.id) : disable.mutate(agent.id)
								}
								inactiveLabel="Disabled"
								suspendAriaLabel={`Disable ${agent.name} (kill switch)`}
								restoreAriaLabel={`Enable ${agent.name}`}
								suspendPrompt="Block this agent?"
								restorePrompt="Restore access?"
								suspendConfirmLabel="Disable"
								data-testid="detail-status-badge"
							/>
						) : (
							<ActorStatusBadge
								status={agent.status}
								data-testid="detail-status-badge"
							/>
						)}
						{headerActions.map((action) => (
							<Button
								key={action}
								size="sm"
								variant={ACTION_VARIANT[action]}
								disabled={actionPending}
								loading={pendingAction === action}
								onClick={() => handleAction(action)}
								aria-label={`${ACTION_LABEL[action]} ${agent.name}`}
							>
								{ACTION_LABEL[action]}
							</Button>
						))}
					</>
				}
			/>

			<div className="-mt-2 flex items-center justify-between">
				<BackButton to={ROUTES.agents} label="All agents" useHistory={false} />
				<AppLink
					href={ROUTE_PATHS.monitorExecutions({ actorId: agent.id, actorType: 'agent' })}
					className="text-muted-foreground hover:text-foreground inline-flex items-center gap-1 text-xs font-medium transition-colors"
				>
					<ActivityIcon className="h-3.5 w-3.5" /> Open Monitor
				</AppLink>
			</div>

			{/* Denial banner — the same full-width alert grammar as the toolkit
			    console's suspended banner. */}
			{agent.status === 'rejected' && (
				<div
					className="border-danger/40 bg-danger/5 flex items-start gap-3 rounded-xl border p-4"
					role="alert"
				>
					<div className="bg-danger/15 text-danger flex h-9 w-9 shrink-0 items-center justify-center rounded-lg">
						<ShieldX className="h-5 w-5" />
					</div>
					<div className="min-w-0 flex-1">
						<p className="text-danger font-heading text-sm font-semibold">
							Registration denied
						</p>
						<p className="text-foreground/90 mt-0.5 text-sm">
							{agent.denialReason ?? 'No reason recorded.'}
							{agent.attribution.deniedBy && (
								<span className="text-muted-foreground block text-xs">
									by <ActorLabel actorId={agent.attribution.deniedBy} />
								</span>
							)}
						</p>
					</div>
				</div>
			)}

			{/* 7-day vitals — StatCard grid like the toolkit console (hidden on 403). */}
			<KpiStrip
				usage={usage.data}
				lastActivityAt={lastActivityAt}
				toolkitCount={toolkits.data?.length}
			/>

			<TabNav<DetailTab>
				options={TAB_OPTIONS}
				value={activeTab}
				onChange={setTab}
				ariaLabel="Agent detail sections"
				getTabId={tabId}
				getControls={panelId}
			/>

			<div
				role="tabpanel"
				id={panelId(activeTab)}
				aria-labelledby={tabId(activeTab)}
				tabIndex={0}
				className="space-y-4 focus-visible:outline-none"
			>
				{activeTab === 'overview' && (
					<>
						<DetailSection
							title="Attribution"
							icon={<Fingerprint className="h-4 w-4" />}
						>
							<dl className="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-3 lg:grid-cols-4">
								<MetaItem
									label="Registered"
									value={formatTimestamp(agent.createdAt)}
								/>
								{agent.attribution.registeredBy ? (
									<MetaItem
										label="Registered by"
										value={
											<ActorLabel actorId={agent.attribution.registeredBy} />
										}
									/>
								) : null}
								{agent.approvedAt ? (
									<MetaItem
										label="Approved"
										value={formatTimestamp(agent.approvedAt)}
									/>
								) : null}
								{agent.attribution.approvedBy ? (
									<MetaItem
										label="Approved by"
										value={
											<ActorLabel actorId={agent.attribution.approvedBy} />
										}
									/>
								) : null}
								{agent.ownerId ? (
									<MetaItem
										label="Owner"
										value={<ActorLabel actorId={agent.ownerId} />}
									/>
								) : null}
								{agent.parentAgentId ? (
									<MetaItem
										label="Parent agent"
										value={<ActorLabel actorId={agent.parentAgentId} />}
									/>
								) : null}
							</dl>
						</DetailSection>
						<BoundToolkitsCard agentId={agent.id} agentStatus={agent.status} />
						{/* Actor-scoped audit slice — same "Recent changes" grammar as
						    the toolkit console (admin only; empty for non-admins). */}
						<ActorAuditPanel actorKind="agent" actorId={agent.id} />
					</>
				)}

				{activeTab === 'activity' && <ActivityPanel actorId={agent.id} actorType="agent" />}

				{activeTab === 'access' && (
					<>
						{/* Scopes — platform permissions granted to this agent (#615). */}
						<ScopesCard actorKind="agent" actorId={agent.id} actorName={agent.name} />
						{/* Pending access requests this agent has filed (#619). */}
						<ActorAccessRequestsCard actorId={agent.id} actorName={agent.name} />
						{/* OAuth clients holding a consent→agent grant. */}
						<ConnectedClientsCard agentId={agent.id} agentName={agent.name} />
					</>
				)}

				{activeTab === 'keys' && <AgentKeysPanel agent={agent} />}

				{activeTab === 'mcp' && <McpPanel agentName={agent.name} agentId={agent.id} />}

				{activeTab === 'settings' && (
					<AgentSettingsPanel
						agent={agent}
						lifecyclePending={actionPending}
						onLifecycle={(action) =>
							setConfirm({ kind: action, id: agent.id, name: agent.name })
						}
					/>
				)}
			</div>

			<LifecycleDialogs
				confirm={confirm}
				onClose={() => setConfirm(null)}
				entityType="agent"
				disableBody="Disabling immediately revokes this agent's ability to authenticate. You can re-enable it later."
				mutations={{ deny, disable, archive }}
			/>
		</PageShell>
	);
}
