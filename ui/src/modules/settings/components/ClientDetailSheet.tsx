/**
 * ClientDetailSheet — the per-client console, in the platform's detail
 * grammar (DetailSection cards): full metadata (consent model, auth method,
 * provenance, redirect URIs, scope restriction), the consent→agent GRANTS
 * held against this client (mirroring the agent console's
 * ConnectedClientsCard, via the admin cross-view `GET
 * /admin/oauth-grants?client_id=…`), the client-scoped audit trail ("Recent
 * changes" — where deny reasons finally surface), and a danger zone
 * (deactivate / rotate secret, confirmed at the section level).
 *
 * A detail view, not a form — no drafts to preserve — but the revoke confirm
 * is a stateless Dialog (conditional mount is fine per the dialog-state
 * rule). The sheet re-reads the client row while open (`useOAuthClient`) so
 * decisions taken elsewhere don't leave a stale snapshot.
 */
import { useState } from 'react';
import { Info, Plug2, ShieldOff, X } from 'lucide-react';
import {
	ActorLabel,
	AuditTrailCard,
	Badge,
	Button,
	CopyButton,
	DangerZone,
	DetailSection,
	Dialog,
	EmptyRow,
	ErrorAlert,
	LoadingState,
	SegmentedToggle,
	SheetPrimitive,
	Tooltip,
	toast,
	type AuditTrailEntry,
	type SegmentedToggleOption,
} from '@/shared/ui';
import { formatTimestamp, timeAgo } from '@/shared/lib/utils';
import {
	useOAuthClient,
	useOAuthClientAudit,
	useOAuthClientGrants,
	useRevokeOAuthClientGrant,
	type OAuthClient,
	type OAuthClientGrant,
} from '@/modules/settings/api/hooks';
import { canRotateSecret, clientOrigins } from '@/modules/settings/components/clientStatus';
import { ClientStatusBadges, ClientTypeChips } from '@/modules/settings/components/clientBadges';

type GrantStatusFilter = 'active' | 'revoked' | 'all';

const GRANT_STATUS_OPTIONS: SegmentedToggleOption<GrantStatusFilter>[] = [
	{ value: 'active', label: 'Active' },
	{ value: 'revoked', label: 'Revoked' },
	{ value: 'all', label: 'All' },
];

/** Why the Revoke button is disabled — the G10 list/revoke divergence, in words. */
const CANNOT_REVOKE_REASON =
	'Only the user who consented to this grant, or an admin with the OAuth-clients write permission, can revoke it.';

/** One dt/dd row in the metadata grid. */
function MetaRow({ label, children }: { label: string; children: React.ReactNode }) {
	return (
		<div className="flex flex-col gap-0.5 sm:flex-row sm:gap-3">
			<dt className="text-muted-foreground w-36 shrink-0 text-xs tracking-wider uppercase">
				{label}
			</dt>
			<dd className="text-foreground min-w-0 flex-1 text-sm">{children}</dd>
		</div>
	);
}

function GrantsSection({ client }: { client: OAuthClient }) {
	const [status, setStatus] = useState<GrantStatusFilter>('active');
	const query = useOAuthClientGrants(client.client_id, status === 'all' ? null : status);
	const revoke = useRevokeOAuthClientGrant();
	const [revokeTarget, setRevokeTarget] = useState<OAuthClientGrant | null>(null);

	const grants = query.data?.pages.flatMap((p) => p.data);

	const handleRevoke = (): void => {
		if (!revokeTarget) return;
		revoke.mutate(revokeTarget.id, {
			onSuccess: () => {
				toast({ title: 'Grant revoked', variant: 'success' });
				setRevokeTarget(null);
			},
			onError: (err) => {
				toast({
					title: 'Failed to revoke the grant',
					description: err instanceof Error ? err.message : undefined,
					variant: 'error',
				});
			},
		});
	};

	return (
		<>
			<DetailSection
				title="Grants"
				icon={<Plug2 className="h-4 w-4" />}
				titleExtra={
					grants && grants.length > 0 ? (
						<Badge variant="default">
							{grants.length}
							{query.hasNextPage ? '+' : ''}
						</Badge>
					) : null
				}
				bodyClassName="space-y-3"
			>
				<SegmentedToggle
					options={GRANT_STATUS_OPTIONS}
					value={status}
					onChange={setStatus}
					ariaLabel="Filter grants by status"
				/>

				{query.isPending ? (
					<LoadingState size="sm" />
				) : query.isError ? (
					<ErrorAlert
						message={
							query.error instanceof Error
								? query.error
								: "Failed to load this client's grants."
						}
					/>
				) : !grants || grants.length === 0 ? (
					<EmptyRow icon={<Plug2 />}>
						{status === 'active'
							? `No user has consented to connect ${client.name} to an agent yet.`
							: `${client.name} has no grants matching this filter.`}
					</EmptyRow>
				) : (
					<>
						<ul className="divide-border divide-y">
							{grants.map((grant) => (
								<li
									key={grant.id}
									className="flex flex-wrap items-start justify-between gap-3 py-3"
								>
									<div className="min-w-0 flex-1">
										<p className="text-foreground flex flex-wrap items-center gap-2 text-sm font-medium">
											<ActorLabel
												actorId={grant.agent_id}
												actorType="agent"
											/>
											{grant.status === 'revoked' && (
												<Badge variant="danger">Revoked</Badge>
											)}
										</p>
										<div className="mt-1.5 flex flex-wrap gap-1">
											{grant.scopes.length > 0 ? (
												grant.scopes.map((scope) => (
													<Badge key={scope} variant="default">
														{scope}
													</Badge>
												))
											) : (
												<span className="text-muted-foreground text-xs">
													No scopes granted
												</span>
											)}
										</div>
										<p className="text-muted-foreground mt-1.5 text-xs">
											Consented by <ActorLabel actorId={grant.user_id} /> ·
											granted {timeAgo(grant.created_at)} · last used{' '}
											{grant.last_used_at
												? timeAgo(grant.last_used_at)
												: 'never'}
										</p>
									</div>
									{grant.status === 'active' &&
										(grant.can_revoke ? (
											<Button
												variant="outline"
												size="sm"
												onClick={(): void => setRevokeTarget(grant)}
												disabled={revoke.isPending}
												aria-label={`Revoke grant ${grant.id}`}
											>
												<ShieldOff className="h-4 w-4" />
												Revoke
											</Button>
										) : (
											// The server says the CALLER can't revoke this
											// grant (G10: not the consenter, not a write-set
											// admin) — disable rather than offer a 403.
											<Tooltip content={CANNOT_REVOKE_REASON}>
												<Button
													variant="outline"
													size="sm"
													disabled
													aria-label={`Revoke grant ${grant.id} (not permitted)`}
												>
													<ShieldOff className="h-4 w-4" />
													Revoke
												</Button>
											</Tooltip>
										))}
								</li>
							))}
						</ul>
						{query.hasNextPage && (
							<div className="flex justify-center">
								<Button
									variant="outline"
									size="sm"
									onClick={(): void => void query.fetchNextPage()}
									disabled={query.isFetchingNextPage}
								>
									{query.isFetchingNextPage ? 'Loading…' : 'Load more'}
								</Button>
							</div>
						)}
					</>
				)}
			</DetailSection>

			{revokeTarget != null && (
				<Dialog
					open
					onClose={(): void => setRevokeTarget(null)}
					title="Revoke this grant?"
					footer={
						<>
							<Button variant="outline" onClick={(): void => setRevokeTarget(null)}>
								Cancel
							</Button>
							<Button
								variant="danger"
								disabled={revoke.isPending}
								onClick={handleRevoke}
							>
								{revoke.isPending ? 'Revoking...' : 'Revoke'}
							</Button>
						</>
					}
				>
					<p className="text-muted-foreground">
						<strong>{client.name}</strong> will immediately lose access to{' '}
						<strong>
							<ActorLabel actorId={revokeTarget.agent_id} />
						</strong>
						: every token issued under this grant is revoked. The client must go through
						consent again to reconnect.
					</p>
				</Dialog>
			)}
		</>
	);
}

function AuditSection({ client }: { client: OAuthClient }) {
	const audit = useOAuthClientAudit(client.id);
	const entries: AuditTrailEntry[] = (audit.data ?? []).map((row) => ({
		id: row.id,
		action: row.action,
		actorId: row.actor_id,
		actorType: row.actor_type,
		reason: row.reason,
		occurredAt: row.occurred_at,
	}));
	return (
		<AuditTrailCard
			entries={entries}
			isLoading={audit.isPending}
			isError={audit.isError}
			caption="Client-level events · admin only"
			emptyMessage="No decisions recorded for this client yet."
		/>
	);
}

export interface ClientDetailSheetProps {
	/** The roster row the sheet was opened from (seed; re-read while open). */
	client: OAuthClient | null;
	open: boolean;
	onClose: () => void;
	onEdit: (client: OAuthClient) => void;
	/** Opens the section-level rotate confirm (then the one-time secret dialog). */
	onRotate: (client: OAuthClient) => void;
	/** Opens the section-level deactivate confirm (explains the DCR re-queue). */
	onDeactivate: (client: OAuthClient) => void;
}

export function ClientDetailSheet({
	client: seed,
	open,
	onClose,
	onEdit,
	onRotate,
	onDeactivate,
}: ClientDetailSheetProps) {
	// Re-read while open so decisions taken from the queue/roster (approve,
	// rotate, deactivate) refresh the sheet; the seed row renders immediately.
	const detail = useOAuthClient(open && seed ? seed.id : null);
	const client = detail.data ?? seed;

	if (!client) return null;

	const origins = clientOrigins(client);

	const dangerActions = [
		...(client.active
			? [
					{
						key: 'deactivate',
						title: 'Deactivate client',
						description:
							'Blocks new authorization flows. A deactivated client that re-registers via DCR returns to the approval queue.',
						buttonLabel: 'Deactivate',
						ariaLabel: `Deactivate ${client.name}`,
						emphasis: 'outline' as const,
					},
				]
			: []),
		...(canRotateSecret(client)
			? [
					{
						key: 'rotate',
						title: 'Rotate client secret',
						description:
							'Invalidates the current secret immediately. Any application using the old secret loses access.',
						buttonLabel: 'Rotate secret',
						ariaLabel: `Rotate secret for ${client.name}`,
						emphasis: 'outline' as const,
					},
				]
			: []),
	];

	return (
		<SheetPrimitive
			open={open}
			onClose={onClose}
			side="right"
			ariaLabel={`Details for ${client.name}`}
			className="flex flex-col sm:w-[560px]"
		>
			<header className="border-border flex items-start justify-between gap-3 border-b p-5">
				<div className="min-w-0">
					<h2 className="text-foreground flex flex-wrap items-center gap-2 text-lg font-semibold">
						<span className="min-w-0 truncate">{client.name}</span>
						<ClientStatusBadges client={client} showApproved />
						<ClientTypeChips client={client} />
					</h2>
					<p className="mt-1 flex items-center gap-1">
						<code className="text-muted-foreground min-w-0 truncate font-mono text-xs">
							{client.client_id}
						</code>
						<CopyButton
							value={client.client_id}
							variant="ghost"
							size="icon"
							className="h-6 w-6 shrink-0 p-1"
							toastMessage="Client ID copied"
							ariaLabel={`Copy client ID for ${client.name}`}
						/>
					</p>
				</div>
				<div className="flex shrink-0 items-center gap-1">
					<Button variant="outline" size="sm" onClick={(): void => onEdit(client)}>
						Edit
					</Button>
					<Button
						variant="ghost"
						size="icon"
						onClick={onClose}
						aria-label="Close details"
					>
						<X className="h-4 w-4" />
					</Button>
				</div>
			</header>

			<div className="flex-1 space-y-4 overflow-y-auto p-5">
				<DetailSection
					title="Configuration"
					icon={<Info className="h-4 w-4" />}
					bodyClassName="space-y-2.5"
				>
					<dl className="space-y-2.5">
						<MetaRow label="Consent model">
							{client.consent_model === 'agent'
								? 'Agent — consent binds an agent'
								: 'User — consent acts as the user'}
						</MetaRow>
						<MetaRow label="Client type">
							{client.token_endpoint_auth_method === 'none'
								? 'Public (PKCE-only, no secret)'
								: 'Confidential (client secret)'}
						</MetaRow>
						<MetaRow label="Source">
							{client.registration_source === 'dcr'
								? 'Dynamic client registration'
								: 'Admin-registered'}
						</MetaRow>
						{client.software_id && (
							<MetaRow label="Software ID">
								<code className="font-mono text-xs">{client.software_id}</code>
							</MetaRow>
						)}
						{client.description && (
							<MetaRow label="Description">{client.description}</MetaRow>
						)}
						<MetaRow label="Redirect URIs">
							<ul className="space-y-0.5">
								{client.redirect_uris.map((uri) => (
									<li key={uri} className="truncate font-mono text-xs">
										{uri}
									</li>
								))}
							</ul>
							{origins.length > 0 && (
								<p className="text-muted-foreground mt-1 font-mono text-xs">
									Origins: {origins.join(', ')}
								</p>
							)}
						</MetaRow>
						<MetaRow label="Allowed scopes">
							{client.allowed_scopes == null ? (
								'Unrestricted'
							) : client.allowed_scopes.length === 0 ? (
								'OIDC only (openid, email, profile)'
							) : (
								<span className="flex flex-wrap gap-1">
									{client.allowed_scopes.map((scope) => (
										<Badge key={scope} variant="default">
											{scope}
										</Badge>
									))}
								</span>
							)}
						</MetaRow>
						<MetaRow label="Consent screen">
							{client.require_consent ? 'Required' : 'Skipped (trusted client)'}
						</MetaRow>
						<MetaRow label="Registered">
							<span title={formatTimestamp(client.created_at)}>
								{timeAgo(client.created_at)}
							</span>
							{client.created_by && (
								<>
									{' '}
									by <ActorLabel actorId={client.created_by} />
								</>
							)}
						</MetaRow>
						{client.updated_at && (
							<MetaRow label="Updated">
								<span title={formatTimestamp(client.updated_at)}>
									{timeAgo(client.updated_at)}
								</span>
							</MetaRow>
						)}
					</dl>
				</DetailSection>

				<GrantsSection client={client} />

				<AuditSection client={client} />

				{dangerActions.length > 0 && (
					<DangerZone
						actions={dangerActions}
						onAction={(key): void => {
							if (key === 'deactivate') onDeactivate(client);
							if (key === 'rotate') onRotate(client);
						}}
					/>
				)}
			</div>
		</SheetPrimitive>
	);
}
