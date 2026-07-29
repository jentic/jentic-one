/**
 * Service-account detail page — the identity console for a single service
 * account at `/agents/service-accounts/:serviceAccountId` (router `basename`
 * adds `/app`).
 *
 * Phase 5 of the agents-rebuild plan: adopts the SAME console shell as the
 * agent detail page (identity header + KPI strip + `?tab=` panels) so the two
 * actor kinds read identically, minus what jentic-one doesn't serve for SAs:
 *   - Overview  → attribution meta (GET /service-accounts/{id}); no toolkit
 *                 bindings exist for SAs, so no Bound-toolkits card
 *   - Activity  → execution volume + recent executions (same per-actor
 *                 monitoring reads; SA ids are actor ids)
 *   - Access    → platform scopes (#615) + filed access requests (#619)
 *   - Keys      → generate only; SA responses expose no key metadata/history
 *                 (backend gap, documented inline)
 *   - Settings  → danger zone only; there is no PATCH /service-accounts
 *                 (backend gap, documented inline)
 *
 * The identity header only offers constructive actions (Approve / Deny /
 * Enable); destructive ones (Disable / Archive) live in Settings' danger zone.
 */
import { useState } from 'react';
import { useParams, useSearchParams } from 'react-router';
import { Activity, KeyRound, TriangleAlert } from 'lucide-react';
import {
	ActorLabel,
	AgentBadge,
	AppLink,
	BackButton,
	Button,
	Card,
	CardBody,
	CardHeader,
	CardTitle,
	CopyButton,
	LoadingState,
	PageHeader,
	PageShell,
	SegmentedToggle,
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
	STATUS_DOT,
	ACTIONS_FOR_STATUS,
	ACTION_LABEL,
	ACTION_VARIANT,
	type AgentAction,
	type ServiceAccountEntity,
} from '@/modules/agents/api';
import { ActorStatusBadge } from '@/modules/agents/components/ActorStatusBadge';
import { ApiKeyDialog } from '@/modules/agents/components/ApiKeyDialog';
import { ScopesCard } from '@/modules/agents/components/ScopesCard';
import { ActorAccessRequestsCard } from '@/modules/agents/components/ActorAccessRequestsCard';
import {
	LifecycleDialogs,
	type PendingConfirm,
} from '@/modules/agents/components/LifecycleDialogs';
import { KpiStrip } from '@/modules/agents/components/detail/KpiStrip';
import { ActivityPanel } from '@/modules/agents/components/detail/ActivityPanel';
import { ROUTES } from '@/shared/app/routes';

const DETAIL_TABS = ['overview', 'activity', 'access', 'keys', 'settings'] as const;
type DetailTab = (typeof DETAIL_TABS)[number];

const TAB_LABELS: Record<DetailTab, string> = {
	overview: 'Overview',
	activity: 'Activity',
	access: 'Access',
	keys: 'Keys',
	settings: 'Settings',
};

function isDetailTab(value: string | null): value is DetailTab {
	return DETAIL_TABS.includes(value as DetailTab);
}

const tabId = (tab: string) => `sa-tab-${tab}`;
const panelId = (tab: string) => `sa-panel-${tab}`;

/** A compact label/value pair used in the attribution meta grid. */
function MetaItem({ label, value }: { label: string; value: React.ReactNode }) {
	return (
		<div className="min-w-0">
			<dt className="text-muted-foreground/70 text-[10px] tracking-wider uppercase">
				{label}
			</dt>
			<dd className="text-foreground/90 mt-0.5 truncate text-xs">{value}</dd>
		</div>
	);
}

/**
 * The Keys tab body. Service-account responses expose no key metadata or
 * rotation history (unlike agents — a documented backend gap), so this is a
 * single generate action that surfaces the plaintext once via ApiKeyDialog.
 */
function SaKeysPanel({
	account,
	onKey,
}: {
	account: ServiceAccountEntity;
	onKey: (key: string) => void;
}) {
	const generateApiKey = useGenerateServiceAccountApiKey();
	return (
		<Card>
			<CardHeader>
				<CardTitle className="flex items-center gap-2">
					<KeyRound className="text-muted-foreground h-4 w-4" aria-hidden />
					API key
				</CardTitle>
			</CardHeader>
			<CardBody className="space-y-3">
				<p className="text-muted-foreground text-sm">
					Generate a bearer key this service account authenticates with. The plaintext is
					shown once — store it securely. jentic-one doesn’t expose key metadata or
					rotation history for service accounts yet.
				</p>
				<Button
					size="sm"
					variant="outline"
					disabled={account.status !== 'active' || generateApiKey.isPending}
					loading={generateApiKey.isPending}
					onClick={async () => {
						const result = await generateApiKey.mutateAsync(account.id);
						onKey(result.key);
					}}
					aria-label={`Generate API key for ${account.name}`}
				>
					<KeyRound className="h-3.5 w-3.5" />
					Generate API Key
				</Button>
				{account.status !== 'active' && (
					<p className="text-muted-foreground text-xs">
						Keys can only be generated for active service accounts.
					</p>
				)}
			</CardBody>
		</Card>
	);
}

/**
 * The Settings tab body. There is no PATCH /service-accounts in jentic-one
 * (backend gap), so — unlike the agent page — there's no metadata form here;
 * only the danger zone hosting the destructive lifecycle actions.
 */
function SaSettingsPanel({
	account,
	onLifecycle,
	lifecyclePending,
}: {
	account: ServiceAccountEntity;
	onLifecycle: (action: 'disable' | 'archive') => void;
	lifecyclePending: boolean;
}) {
	const actions = ACTIONS_FOR_STATUS[account.status];
	const canDisable = actions.includes('disable');
	const canArchive = actions.includes('archive');

	return (
		<div className="space-y-4">
			<Card>
				<CardHeader>
					<CardTitle>General</CardTitle>
				</CardHeader>
				<CardBody>
					<p className="text-muted-foreground text-sm">
						Renaming or re-describing a service account isn’t supported yet — jentic-one
						has no PATCH /service-accounts endpoint. Create a replacement account if the
						metadata must change.
					</p>
				</CardBody>
			</Card>

			{(canDisable || canArchive) && (
				<Card className="border-danger/30">
					<CardHeader>
						<CardTitle className="text-danger flex items-center gap-2">
							<TriangleAlert className="h-4 w-4" aria-hidden />
							Danger zone
						</CardTitle>
					</CardHeader>
					<CardBody className="divide-border/60 divide-y">
						{canDisable && (
							<div className="flex flex-wrap items-center justify-between gap-3 py-3 first:pt-0 last:pb-0">
								<div className="min-w-0">
									<p className="text-foreground text-sm font-medium">
										Disable service account
									</p>
									<p className="text-muted-foreground text-xs">
										Immediately revokes this account’s access. Reversible — you
										can re-enable it later.
									</p>
								</div>
								<Button
									size="sm"
									variant="outline"
									className="border-danger/40 text-danger hover:bg-danger/10 shrink-0"
									disabled={lifecyclePending}
									onClick={() => onLifecycle('disable')}
									aria-label={`Disable ${account.name}`}
								>
									Disable
								</Button>
							</div>
						)}
						{canArchive && (
							<div className="flex flex-wrap items-center justify-between gap-3 py-3 first:pt-0 last:pb-0">
								<div className="min-w-0">
									<p className="text-foreground text-sm font-medium">
										Archive service account
									</p>
									<p className="text-muted-foreground text-xs">
										Removes this account from the fleet and cascades to its
										grants. This cannot be undone.
									</p>
								</div>
								<Button
									size="sm"
									variant="danger"
									className="shrink-0"
									disabled={lifecyclePending}
									onClick={() => onLifecycle('archive')}
									aria-label={`Archive ${account.name}`}
								>
									Archive
								</Button>
							</div>
						)}
					</CardBody>
				</Card>
			)}
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
		setSearchParams(
			(prev) => {
				const next = new URLSearchParams(prev);
				if (tab === 'overview') next.delete('tab');
				else next.set('tab', tab);
				return next;
			},
			{ replace: true },
		);
	}

	if (accountQuery.isPending) {
		return (
			<PageShell>
				<LoadingState message="Loading service account…" />
			</PageShell>
		);
	}

	// 404 / unknown id → honest not-found, not a fake account.
	if (accountQuery.error || !accountQuery.data) {
		return (
			<PageShell>
				<PageHeader
					title="Service account not found"
					subtitle={
						id ? `No service account with id ${id}.` : 'Missing service account id.'
					}
				/>
				<BackButton to={ROUTES.agents} label="All agents" />
			</PageShell>
		);
	}

	const account = accountQuery.data;
	// Constructive actions only — Disable/Archive live in Settings (phase 4/5).
	const headerActions = ACTIONS_FOR_STATUS[account.status].filter(
		(a) => a !== 'disable' && a !== 'archive',
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
			: enable.isPending
				? 'enable'
				: null;

	function handleAction(action: AgentAction) {
		switch (action) {
			case 'approve':
				approve.mutate(account.id);
				break;
			case 'enable':
				enable.mutate(account.id);
				break;
			case 'deny':
				setConfirm({ kind: 'deny', id: account.id, name: account.name });
				break;
			case 'disable':
				setConfirm({ kind: 'disable', id: account.id, name: account.name });
				break;
			case 'archive':
				setConfirm({ kind: 'archive', id: account.id, name: account.name });
				break;
		}
	}

	const lastActivityAt = executions.data?.items[0]?.startedAt ?? null;

	return (
		<PageShell>
			<PageHeader
				title={account.name}
				subtitle="Identity, activity, access, and credentials for this service account."
			/>

			<div className="-mt-2 flex items-center justify-between">
				<BackButton to={ROUTES.agents} label="All agents" />
				<AppLink
					href={ROUTES.monitor}
					className="text-muted-foreground hover:text-foreground inline-flex items-center gap-1 text-xs font-medium transition-colors"
				>
					<Activity className="h-3.5 w-3.5" /> Open Monitor
				</AppLink>
			</div>

			{/* Identity header: who this is + lifecycle actions + KPI strip. */}
			<Card>
				<CardBody className="space-y-4 p-5">
					<div className="flex flex-wrap items-start gap-4">
						<div className="relative shrink-0">
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
						<div className="min-w-0 flex-1">
							<div className="flex flex-wrap items-center gap-2">
								<span className="text-foreground text-lg font-semibold tracking-tight">
									{account.name}
								</span>
								<ActorStatusBadge status={account.status} />
							</div>
							<div className="mt-1 flex items-center gap-1.5">
								<code className="text-muted-foreground/80 truncate font-mono text-[11px]">
									{account.id}
								</code>
								<CopyButton value={account.id} />
							</div>
							{account.description && (
								<p className="text-muted-foreground mt-2 text-sm">
									{account.description}
								</p>
							)}
						</div>

						{/* Constructive lifecycle actions — destructive ones are in Settings. */}
						{headerActions.length > 0 && (
							<div className="flex shrink-0 flex-wrap gap-2">
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
							</div>
						)}
					</div>

					{account.status === 'rejected' && (
						<div className="border-danger/30 bg-danger/5 rounded-lg border p-3">
							<p className="text-danger text-xs font-semibold tracking-wider uppercase">
								Denial reason
							</p>
							<p className="text-foreground/90 mt-1 text-sm">
								{account.denialReason ?? '—'}
								{account.attribution.deniedBy && (
									<span className="text-muted-foreground block text-xs">
										by <ActorLabel actorId={account.attribution.deniedBy} />
									</span>
								)}
							</p>
						</div>
					)}

					<KpiStrip usage={usage.data} lastActivityAt={lastActivityAt} />
				</CardBody>
			</Card>

			<SegmentedToggle<DetailTab>
				options={DETAIL_TABS.map((tab) => ({ value: tab, label: TAB_LABELS[tab] }))}
				value={activeTab}
				onChange={setTab}
				as="tabs"
				ariaLabel="Service account detail sections"
				getTabId={tabId}
				getControls={panelId}
				className="w-max max-w-full overflow-x-auto"
			/>

			<div
				role="tabpanel"
				id={panelId(activeTab)}
				aria-labelledby={tabId(activeTab)}
				className="space-y-4"
			>
				{activeTab === 'overview' && (
					<Card>
						<CardBody>
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
						</CardBody>
					</Card>
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
