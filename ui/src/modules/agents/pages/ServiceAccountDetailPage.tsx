/**
 * Service-account detail page — the identity console for a single service
 * account at `/agents/service-accounts/:serviceAccountId` (router `basename`
 * adds `/app`).
 *
 * Adopts the SAME console shell as the
 * agent detail page (PageHeader-as-identity + KPI strip + `?tab=` panels) so
 * the two actor kinds read identically, minus what jentic-one doesn't serve
 * for SAs:
 *   - Overview  → attribution meta (GET /service-accounts/{id}) + audit
 *                 slice; no toolkit bindings exist for SAs, so no
 *                 Bound-toolkits card
 *   - Activity  → execution volume + recent executions (same per-actor
 *                 monitoring reads; SA ids are actor ids)
 *   - Access    → platform scopes (#615) + filed access requests (#619)
 *   - Keys      → generate only; SA responses expose no key metadata/history
 *                 (backend gap, documented inline)
 *   - Settings  → the copyable account id + danger zone; there is no PATCH
 *                 /service-accounts (backend gap, documented inline)
 *
 * The PageHeader carries the kill switch for the reversible active/disabled
 * flip (same control as the toolkit console) plus the constructive Approve /
 * Deny actions; the terminal Archive lives in Settings' danger zone.
 */
import { useState } from 'react';
import { useParams, useSearchParams } from 'react-router';
import {
	Activity as ActivityIcon,
	Fingerprint,
	Key,
	KeyRound,
	LayoutDashboard,
	Settings,
	ShieldCheck,
	ShieldX,
} from 'lucide-react';
import {
	ActorLabel,
	AgentBadge,
	AppLink,
	BackButton,
	Button,
	DangerZone,
	DetailSection,
	IdentitySettingsCard,
	KillSwitch,
	LoadingState,
	PageHeader,
	PageShell,
	TabNav,
	type DangerZoneAction,
	type TabNavOption,
} from '@/shared/ui';
import { cn, formatTimestamp } from '@/shared/lib/utils';
import {
	useServiceAccount,
	useActorUsageDetail,
	useActorExecutions,
	useApproveServiceAccount,
	useDenyServiceAccount,
	useDisableServiceAccount,
	useEnableServiceAccount,
	useArchiveServiceAccount,
	useGenerateServiceAccountApiKey,
	AgentsApiError,
	STATUS_DOT,
	ACTIONS_FOR_STATUS,
	ACTION_LABEL,
	ACTION_VARIANT,
	type AgentAction,
	type ServiceAccountEntity,
} from '@/modules/agents/api';
import { ActorStatusBadge } from '@/modules/agents/components/ActorStatusBadge';
import { ApiKeyDialog } from '@/modules/agents/components/ApiKeyDialog';
import { ConfirmDialog } from '@/modules/agents/components/confirm/ConfirmDialog';
import { ScopesCard } from '@/modules/agents/components/ScopesCard';
import { ActorAccessRequestsCard } from '@/modules/agents/components/ActorAccessRequestsCard';
import {
	LifecycleDialogs,
	type PendingConfirm,
} from '@/modules/agents/components/LifecycleDialogs';
import { KpiStrip } from '@/modules/agents/components/detail/KpiStrip';
import { ActivityPanel } from '@/modules/agents/components/detail/ActivityPanel';
import { ActorAuditPanel } from '@/modules/agents/components/detail/ActorAuditPanel';
import { MetaItem } from '@/modules/agents/components/detail/shared';
import { ROUTES, ROUTE_PATHS } from '@/shared/app/routes';

const DETAIL_TABS = ['overview', 'activity', 'access', 'keys', 'settings'] as const;
type DetailTab = (typeof DETAIL_TABS)[number];

/** Tab options for the console shell — same icon grammar as the toolkit console. */
const TAB_OPTIONS: TabNavOption<DetailTab>[] = [
	{ value: 'overview', label: 'Overview', icon: <LayoutDashboard className="h-4 w-4" /> },
	{ value: 'activity', label: 'Activity', icon: <ActivityIcon className="h-4 w-4" /> },
	{ value: 'access', label: 'Access', icon: <ShieldCheck className="h-4 w-4" /> },
	{ value: 'keys', label: 'Keys', icon: <Key className="h-4 w-4" /> },
	{ value: 'settings', label: 'Settings', icon: <Settings className="h-4 w-4" /> },
];

function isDetailTab(value: string | null): value is DetailTab {
	return DETAIL_TABS.includes(value as DetailTab);
}

const tabId = (tab: string) => `sa-tab-${tab}`;
const panelId = (tab: string) => `sa-panel-${tab}`;

/**
 * The Keys tab body. Service-account responses expose no key metadata or
 * rotation history (unlike agents — a documented backend gap), so this is a
 * single generate action that surfaces the plaintext once via ApiKeyDialog.
 * Because we can't know whether a key already exists, generating ALWAYS
 * confirms first — a regenerate silently invalidates the previous key.
 */
function SaKeysPanel({
	account,
	onKey,
}: {
	account: ServiceAccountEntity;
	onKey: (key: string) => void;
}) {
	const generateApiKey = useGenerateServiceAccountApiKey();
	const [confirmGenerate, setConfirmGenerate] = useState(false);
	return (
		<DetailSection
			title="API key"
			icon={<KeyRound className="h-4 w-4" />}
			bodyClassName="space-y-3"
		>
			<p className="text-muted-foreground text-sm">
				Generate a bearer key this service account authenticates with. The plaintext is
				shown once — store it securely. jentic-one doesn’t expose key metadata or rotation
				history for service accounts yet.
			</p>
			<div className="flex justify-end">
				<Button
					size="sm"
					variant="outline"
					disabled={account.status !== 'active' || generateApiKey.isPending}
					loading={generateApiKey.isPending}
					onClick={() => setConfirmGenerate(true)}
					aria-label={`Generate API key for ${account.name}`}
				>
					<KeyRound className="h-3.5 w-3.5" />
					Generate API Key
				</Button>
			</div>
			{account.status !== 'active' && (
				<p className="text-muted-foreground text-xs">
					Keys can only be generated for active service accounts.
				</p>
			)}
			<ConfirmDialog
				open={confirmGenerate}
				title={`Generate API key for ${account.name}`}
				body="If this service account already has an API key, it stops working the moment the new one is issued — anything still using it will fail to authenticate until updated."
				confirmLabel="Generate"
				pending={generateApiKey.isPending}
				onConfirm={async () => {
					try {
						const result = await generateApiKey.mutateAsync(account.id);
						setConfirmGenerate(false);
						onKey(result.key);
					} catch {
						// The hook toasts the failure; the dialog stays open to retry.
					}
				}}
				onClose={() => setConfirmGenerate(false)}
			/>
		</DetailSection>
	);
}

/**
 * The Settings tab body — the shared console cards in read-only mode: the
 * immutable, copyable account id (same {@link IdentitySettingsCard} the agent
 * and toolkit consoles render — there is no PATCH /service-accounts in
 * jentic-one, a backend gap, so no metadata form) plus the shared danger zone.
 */
function SaSettingsPanel({
	account,
	onLifecycle,
	lifecyclePending,
}: {
	account: ServiceAccountEntity;
	onLifecycle: (action: 'archive') => void;
	lifecyclePending: boolean;
}) {
	const actions = ACTIONS_FOR_STATUS[account.status];
	// Terminal Archive only — the reversible Disable/Enable flip lives in the
	// page header's kill switch, exactly like the toolkit console.
	const dangerActions: DangerZoneAction[] = actions.includes('archive')
		? [
				{
					key: 'archive',
					title: 'Archive service account',
					description:
						'Removes this account from the fleet and cascades to its grants. This cannot be undone.',
					buttonLabel: 'Archive',
					ariaLabel: `Archive ${account.name}`,
				},
			]
		: [];

	return (
		<div className="space-y-4">
			<IdentitySettingsCard
				idLabel="Account ID"
				idValue={account.id}
				name={account.name}
				description={account.description ?? null}
				readOnlyNote="Renaming or re-describing a service account isn’t supported yet — jentic-one has no PATCH /service-accounts endpoint. Create a replacement account if the metadata must change."
			/>

			<DangerZone
				actions={dangerActions}
				pending={lifecyclePending}
				onAction={() => onLifecycle('archive')}
			/>
		</div>
	);
}

export default function ServiceAccountDetailPage() {
	const { serviceAccountId } = useParams<{ serviceAccountId: string }>();
	const id = serviceAccountId ?? null;
	const [searchParams, setSearchParams] = useSearchParams();
	const tabParam = searchParams.get('tab');
	const activeTab: DetailTab = isDetailTab(tabParam) ? tabParam : 'overview';

	const accountQuery = useServiceAccount(id);
	// KPI-strip enrichment (admin-gated; resolves null on 403 — see hooks).
	const usage = useActorUsageDetail(id);
	const executions = useActorExecutions(id);

	const approve = useApproveServiceAccount();
	const deny = useDenyServiceAccount();
	const disable = useDisableServiceAccount();
	const enable = useEnableServiceAccount();
	const archive = useArchiveServiceAccount();

	const [confirm, setConfirm] = useState<PendingConfirm>(null);
	const [apiKey, setApiKey] = useState<string | null>(null);

	function setTab(tab: string) {
		// Pushed (not replaced) so the browser back button walks tabs — same
		// contract as the agent and toolkit consoles; the back link below is
		// static for exactly that reason.
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

	if (accountQuery.isPending) {
		return (
			<PageShell>
				<LoadingState message="Loading service account…" />
			</PageShell>
		);
	}

	// Only a 404 (or a missing route param) means "no such account" — anything
	// else (403, 500, network) is a load failure and must not masquerade as
	// not-found.
	const errorStatus =
		accountQuery.error instanceof AgentsApiError ? accountQuery.error.status : null;
	if (accountQuery.error && errorStatus !== 404) {
		return (
			<PageShell>
				<PageHeader
					title="Couldn't load service account"
					subtitle={
						errorStatus === 403
							? 'You do not have permission to view this service account.'
							: 'Something went wrong while loading this service account. Try again.'
					}
				/>
				<BackButton to={ROUTES.agents} label="All agents" useHistory={false} />
			</PageShell>
		);
	}
	if (accountQuery.error || !accountQuery.data) {
		return (
			<PageShell>
				<PageHeader
					title="Service account not found"
					subtitle={
						id ? `No service account with id ${id}.` : 'Missing service account id.'
					}
				/>
				<BackButton to={ROUTES.agents} label="All agents" useHistory={false} />
			</PageShell>
		);
	}

	const account = accountQuery.data;
	// The header offers the kill switch for the reversible active/disabled
	// flip (same control as the toolkit console) plus constructive actions
	// (Approve / Deny); Archive lives in Settings. `enable`/`disable` never
	// render as header buttons — the kill switch owns that verb pair.
	const killSwitchStatus = account.status === 'active' || account.status === 'disabled';
	const headerActions = ACTIONS_FOR_STATUS[account.status].filter(
		(a) => a !== 'disable' && a !== 'archive' && a !== 'enable',
	);
	const actionPending =
		approve.isPending ||
		deny.isPending ||
		disable.isPending ||
		enable.isPending ||
		archive.isPending;

	/** Which specific action is in flight (drives the per-button spinner). */
	const pendingAction: AgentAction | null = approve.isPending
		? 'approve'
		: deny.isPending
			? 'deny'
			: null;

	function handleAction(action: AgentAction) {
		switch (action) {
			case 'approve':
				approve.mutate(account.id);
				break;
			case 'deny':
				setConfirm({ kind: 'deny', id: account.id, name: account.name });
				break;
		}
	}

	const lastActivityAt = executions.data?.items[0]?.startedAt ?? null;

	return (
		<PageShell>
			{/* Same header grammar as the agent + toolkit consoles: badge as the
			    icon, description as subtitle, status beside the constructive
			    lifecycle actions. No second identity card. */}
			<PageHeader
				title={account.name}
				subtitle={account.description ?? undefined}
				icon={
					<div className="relative">
						<AgentBadge
							id={account.id}
							name={account.name}
							kind="Service account"
							size="lg"
						/>
						<span
							className={cn(
								'border-background absolute -right-0.5 -bottom-0.5 h-3 w-3 rounded-full border-2',
								STATUS_DOT[account.status],
							)}
							aria-hidden
						/>
					</div>
				}
				actions={
					<>
						{killSwitchStatus ? (
							// Same reversible suspend/restore control as the toolkit
							// header — disable/enable is the account's kill switch.
							<KillSwitch
								active={account.status === 'active'}
								pending={disable.isPending || enable.isPending}
								onToggle={(next) =>
									next ? enable.mutate(account.id) : disable.mutate(account.id)
								}
								inactiveLabel="Disabled"
								suspendAriaLabel={`Disable ${account.name} (kill switch)`}
								restoreAriaLabel={`Enable ${account.name}`}
								suspendPrompt="Block this service account?"
								restorePrompt="Restore access?"
								suspendConfirmLabel="Disable"
								data-testid="detail-status-badge"
							/>
						) : (
							<ActorStatusBadge
								status={account.status}
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
								aria-label={`${ACTION_LABEL[action]} ${account.name}`}
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
					href={ROUTE_PATHS.monitorExecutions({
						actorId: account.id,
						actorType: 'service_account',
					})}
					className="text-muted-foreground hover:text-foreground inline-flex items-center gap-1 text-xs font-medium transition-colors"
				>
					<ActivityIcon className="h-3.5 w-3.5" /> Open Monitor
				</AppLink>
			</div>

			{/* Denial banner — full-width alert grammar shared with the agent
			    console's denial banner and the toolkit suspension banner. */}
			{account.status === 'rejected' && (
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
							{account.denialReason ?? 'No reason recorded.'}
							{account.attribution.deniedBy && (
								<span className="text-muted-foreground block text-xs">
									by <ActorLabel actorId={account.attribution.deniedBy} />
								</span>
							)}
						</p>
					</div>
				</div>
			)}

			{/* 7-day vitals — StatCard grid like the toolkit console (hidden on 403). */}
			<KpiStrip usage={usage.data} lastActivityAt={lastActivityAt} />

			<TabNav<DetailTab>
				options={TAB_OPTIONS}
				value={activeTab}
				onChange={setTab}
				ariaLabel="Service account detail sections"
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
									label="Created"
									value={formatTimestamp(account.createdAt)}
								/>
								{account.attribution.registeredBy ? (
									<MetaItem
										label="Created by"
										value={
											<ActorLabel
												actorId={account.attribution.registeredBy}
											/>
										}
									/>
								) : null}
								{account.approvedAt ? (
									<MetaItem
										label="Approved"
										value={formatTimestamp(account.approvedAt)}
									/>
								) : null}
								{account.attribution.approvedBy ? (
									<MetaItem
										label="Approved by"
										value={
											<ActorLabel actorId={account.attribution.approvedBy} />
										}
									/>
								) : null}
								{account.ownerId ? (
									<MetaItem
										label="Owner"
										value={<ActorLabel actorId={account.ownerId} />}
									/>
								) : null}
							</dl>
						</DetailSection>
						{/* Actor-scoped audit slice — same "Recent changes" grammar as
						    the agent + toolkit consoles (admin only). */}
						<ActorAuditPanel actorKind="service-account" actorId={account.id} />
					</>
				)}

				{activeTab === 'activity' && (
					<ActivityPanel actorId={account.id} actorType="service_account" />
				)}

				{activeTab === 'access' && (
					<>
						{/* Scopes — platform permissions granted to this SA (#615). */}
						<ScopesCard
							actorKind="service-account"
							actorId={account.id}
							actorName={account.name}
						/>
						{/* Pending access requests this SA has filed (#619). */}
						<ActorAccessRequestsCard actorId={account.id} actorName={account.name} />
					</>
				)}

				{activeTab === 'keys' && <SaKeysPanel account={account} onKey={setApiKey} />}

				{activeTab === 'settings' && (
					<SaSettingsPanel
						account={account}
						lifecyclePending={actionPending}
						onLifecycle={(action) =>
							setConfirm({ kind: action, id: account.id, name: account.name })
						}
					/>
				)}
			</div>

			<LifecycleDialogs
				confirm={confirm}
				onClose={() => setConfirm(null)}
				entityType="service-account"
				disableBody="Disabling immediately revokes this service account's access. You can re-enable it later."
				mutations={{ deny, disable, archive }}
			/>

			<ApiKeyDialog open={apiKey != null} apiKey={apiKey} onClose={() => setApiKey(null)} />
		</PageShell>
	);
}
