/**
 * Service-account detail page — the deep view for a single service account at
 * `/agents/service-accounts/:serviceAccountId` (router `basename` adds `/app`).
 *
 * Built to give service accounts a home for the Scopes card (#615); previously
 * the roster had no SA detail page, so SA rows were non-navigable. Mirrors the
 * agent detail page's identity + lifecycle layout, trimmed to what jentic-one
 * serves for a service account:
 *   - identity + attribution + lifecycle  → GET /service-accounts/{id}
 *   - lifecycle actions                    → the same mutation hooks the list uses
 *   - API key generation                   → POST .../api-key (plaintext shown once)
 *   - scopes                               → <ScopesCard actorKind="service-account">
 *
 * SA responses expose no key metadata/history (unlike agents), so there's no
 * API-key info/history card here — just a generate action that surfaces the
 * plaintext via {@link ApiKeyDialog}.
 */
import { useState, type ReactNode } from 'react';
import { useParams } from 'react-router-dom';
import { KeyRound } from 'lucide-react';
import {
	AgentBadge,
	BackButton,
	Button,
	Card,
	CardBody,
	CascadeDeleteDialog,
	CopyButton,
	LoadingState,
	PageHeader,
	PageShell,
	ActorLabel,
} from '@/shared/ui';
import { cn, formatTimestamp } from '@/shared/lib/utils';
import {
	useServiceAccount,
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
} from '@/modules/agents/api';
import { ActorStatusBadge } from '@/modules/agents/components/ActorStatusBadge';
import { ApiKeyDialog } from '@/modules/agents/components/ApiKeyDialog';
import { ScopesCard } from '@/modules/agents/components/ScopesCard';
import { ActorAccessRequestsCard } from '@/modules/agents/components/ActorAccessRequestsCard';
import { DenyDialog } from '@/modules/agents/components/confirm/DenyDialog';
import { ConfirmDialog } from '@/modules/agents/components/confirm/ConfirmDialog';
import { ROUTES } from '@/shared/app/routes';

/** A pending lifecycle action awaiting confirmation in a dialog. */
type PendingConfirm = { kind: 'deny' } | { kind: 'disable' } | { kind: 'archive' } | null;

/** A compact label/value pair used in the identity meta grid. */
function MetaItem({ label, value }: { label: string; value: ReactNode }) {
	return (
		<div className="min-w-0">
			<dt className="text-muted-foreground/70 text-[10px] tracking-wider uppercase">
				{label}
			</dt>
			<dd className="text-foreground/90 mt-0.5 truncate text-xs">{value}</dd>
		</div>
	);
}

export default function ServiceAccountDetailPage() {
	const { serviceAccountId } = useParams<{ serviceAccountId: string }>();
	const id = serviceAccountId ?? null;

	const accountQuery = useServiceAccount(id);

	const approve = useApproveServiceAccount();
	const deny = useDenyServiceAccount();
	const disable = useDisableServiceAccount();
	const enable = useEnableServiceAccount();
	const archive = useArchiveServiceAccount();
	const generateApiKey = useGenerateServiceAccountApiKey();

	const [confirm, setConfirm] = useState<PendingConfirm>(null);
	const [apiKey, setApiKey] = useState<string | null>(null);

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
	const actions = ACTIONS_FOR_STATUS[account.status];
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
			: disable.isPending
				? 'disable'
				: enable.isPending
					? 'enable'
					: archive.isPending
						? 'archive'
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
				setConfirm({ kind: 'deny' });
				break;
			case 'disable':
				setConfirm({ kind: 'disable' });
				break;
			case 'archive':
				setConfirm({ kind: 'archive' });
				break;
		}
	}

	return (
		<PageShell>
			<PageHeader
				title={account.name}
				subtitle="Identity, attribution, scopes, and lifecycle for this service account."
			/>

			<div className="-mt-2">
				<BackButton to={ROUTES.agents} label="All agents" />
			</div>

			{/* Identity + lifecycle actions */}
			<Card>
				<CardBody className="p-5">
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

						{/* Lifecycle actions — gated by status, mirrors the list page. */}
						{(actions.length > 0 || account.status === 'active') && (
							<div className="flex shrink-0 flex-wrap gap-2">
								{account.status === 'active' && (
									<Button
										size="sm"
										variant="outline"
										disabled={actionPending || generateApiKey.isPending}
										loading={generateApiKey.isPending}
										onClick={async () => {
											const result = await generateApiKey.mutateAsync(
												account.id,
											);
											setApiKey(result.key);
										}}
										aria-label={`Generate API key for ${account.name}`}
									>
										<KeyRound className="h-3.5 w-3.5" />
										Generate API Key
									</Button>
								)}
								{actions.map((action) => (
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

					{/* Timestamps / attribution — a quiet bordered grid below identity. */}
					<dl className="border-border/60 mt-4 grid grid-cols-2 gap-x-4 gap-y-3 border-t pt-4 sm:grid-cols-3 lg:grid-cols-4">
						<MetaItem label="Created" value={formatTimestamp(account.createdAt)} />
						{account.attribution.registeredBy ? (
							<MetaItem
								label="Created by"
								value={<ActorLabel actorId={account.attribution.registeredBy} />}
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
								value={<ActorLabel actorId={account.attribution.approvedBy} />}
							/>
						) : null}
						{account.ownerId ? (
							<MetaItem
								label="Owner"
								value={<ActorLabel actorId={account.ownerId} />}
							/>
						) : null}
					</dl>

					{account.status === 'rejected' && (
						<div className="border-danger/30 bg-danger/5 mt-4 rounded-lg border p-3">
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
				</CardBody>
			</Card>

			{/* Scopes — platform permissions granted to this service account (#615). */}
			<ScopesCard actorKind="service-account" actorId={account.id} actorName={account.name} />

			{/* Pending access requests this service account has filed (#619). */}
			<ActorAccessRequestsCard actorId={account.id} actorName={account.name} />

			<DenyDialog
				open={confirm?.kind === 'deny'}
				subjectName={account.name}
				pending={deny.isPending}
				onConfirm={async (reason) => {
					try {
						await deny.mutateAsync({ id: account.id, reason });
						setConfirm(null);
					} catch {
						// onError toasts; keep the dialog open so the user can retry.
					}
				}}
				onClose={() => setConfirm(null)}
			/>

			<ConfirmDialog
				open={confirm?.kind === 'disable'}
				title={`Disable ${account.name}`}
				body="Disabling immediately revokes this service account's access. You can re-enable it later."
				confirmLabel="Disable"
				pending={disable.isPending}
				onConfirm={async () => {
					try {
						await disable.mutateAsync(account.id);
						setConfirm(null);
					} catch {
						// onError toasts; keep the dialog open so the user can retry.
					}
				}}
				onClose={() => setConfirm(null)}
			/>

			<CascadeDeleteDialog
				open={confirm?.kind === 'archive'}
				entityType="service-account"
				entityName={account.name}
				loading={archive.isPending}
				error={archive.error}
				onConfirm={async () => {
					try {
						await archive.mutateAsync(account.id);
						setConfirm(null);
					} catch {
						// onError toasts; keep the dialog open so the user can retry.
					}
				}}
				onClose={() => setConfirm(null)}
			/>

			<ApiKeyDialog open={apiKey != null} apiKey={apiKey} onClose={() => setApiKey(null)} />
		</PageShell>
	);
}
