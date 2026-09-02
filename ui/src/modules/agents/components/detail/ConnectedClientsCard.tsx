/**
 * ConnectedClientsCard — the OAuth clients holding a live consent→agent grant
 * on THIS agent (phase-3a §4.8): the loud-attachment counter made visible
 * per-agent, in the GitHub/Google "authorized apps" grammar.
 *
 * Each row shows the client (name + redirect-URI origin), the granted scopes,
 * WHO consented (`userId` via `ActorLabel` — deliberately surfaced: after an
 * agent ownership transfer the grant stays with the original consenter, gap
 * G10, so admins can spot stranded grants), created / last-used timestamps,
 * and the per-grant Revoke kill switch (§4.6). A status filter mirrors the
 * access-requests card so revoked history is reachable on demand.
 *
 * Listing is owner-or-admin on the backend; a 403 renders as an honest quiet
 * note (a non-owner operator can see the agent but not its grants), never a
 * hard error surface.
 */
import { useState } from 'react';
import { Plug2, ShieldOff } from 'lucide-react';
import {
	ActorLabel,
	Badge,
	Button,
	DetailSection,
	Dialog,
	EmptyState,
	ErrorAlert,
	LoadingState,
	SegmentedToggle,
	type SegmentedToggleOption,
} from '@/shared/ui';
import { timeAgo } from '@/shared/lib/utils';
import {
	useAgentOauthGrants,
	useRevokeOauthGrant,
	AgentsApiError,
	type OAuthGrantEntity,
} from '@/modules/agents/api';

type StatusFilter = 'active' | 'revoked' | 'all';

const STATUS_OPTIONS: SegmentedToggleOption<StatusFilter>[] = [
	{ value: 'active', label: 'Active' },
	{ value: 'revoked', label: 'Revoked' },
	{ value: 'all', label: 'All' },
];

/** "clientName (origin)" with honest fallbacks for a since-deleted client row. */
function clientLabel(grant: OAuthGrantEntity): string {
	return grant.clientName ?? grant.oauthClientId;
}

export function ConnectedClientsCard({
	agentId,
	agentName,
}: {
	agentId: string;
	agentName: string;
}) {
	const [status, setStatus] = useState<StatusFilter>('active');
	const queryStatus = status === 'all' ? null : status;
	const { data, isPending, isError, error } = useAgentOauthGrants(agentId, queryStatus);
	const revoke = useRevokeOauthGrant(agentId);
	const [revokeTarget, setRevokeTarget] = useState<OAuthGrantEntity | null>(null);

	const grants = data?.entities;
	const forbidden = error instanceof AgentsApiError && error.status === 403;

	return (
		<>
			<DetailSection
				title="Connected clients"
				icon={<Plug2 className="h-4 w-4" />}
				titleExtra={
					grants && grants.length > 0 ? (
						<Badge variant="default">{grants.length}</Badge>
					) : null
				}
				bodyClassName="space-y-3"
			>
				<SegmentedToggle options={STATUS_OPTIONS} value={status} onChange={setStatus} />

				{isPending ? (
					<LoadingState size="sm" />
				) : forbidden ? (
					<p className="text-muted-foreground text-sm">
						Only the agent's owner or an admin can view its connected clients.
					</p>
				) : isError ? (
					<ErrorAlert
						message={
							error instanceof Error
								? error.message
								: "Failed to load this agent's connected clients."
						}
					/>
				) : !grants || grants.length === 0 ? (
					<EmptyState
						icon={<Plug2 className="h-8 w-8" aria-hidden="true" />}
						title={status === 'active' ? 'No connected clients' : 'No matching grants'}
						description={
							status === 'active'
								? `No OAuth client currently holds a grant on ${agentName}. Clients appear here after a user consents to connect one.`
								: `${agentName} has no grants matching this filter.`
						}
					/>
				) : (
					<ul className="divide-border divide-y">
						{grants.map((grant) => (
							<li
								key={grant.id}
								className="flex flex-wrap items-start justify-between gap-3 py-3"
							>
								<div className="min-w-0 flex-1">
									<p className="text-foreground flex flex-wrap items-center gap-2 font-medium">
										<span className="truncate">{clientLabel(grant)}</span>
										{grant.status === 'revoked' && (
											<Badge variant="danger">Revoked</Badge>
										)}
									</p>
									{grant.clientOrigin && (
										<p className="text-muted-foreground truncate font-mono text-xs">
											{grant.clientOrigin}
										</p>
									)}
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
										Consented by <ActorLabel actorId={grant.userId} /> · granted{' '}
										{timeAgo(grant.createdAt)} · last used{' '}
										{grant.lastUsedAt ? timeAgo(grant.lastUsedAt) : 'never'}
									</p>
								</div>
								{grant.status === 'active' && (
									<Button
										variant="outline"
										size="sm"
										onClick={(): void => setRevokeTarget(grant)}
										disabled={revoke.isPending}
										aria-label={`Revoke grant for ${clientLabel(grant)}`}
									>
										<ShieldOff className="h-4 w-4" />
										Revoke
									</Button>
								)}
							</li>
						))}
					</ul>
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
								onClick={(): void => {
									revoke.mutate(revokeTarget.id, {
										onSuccess: () => setRevokeTarget(null),
									});
								}}
							>
								{revoke.isPending ? 'Revoking...' : 'Revoke'}
							</Button>
						</>
					}
				>
					<p className="text-muted-foreground">
						<strong>{clientLabel(revokeTarget)}</strong> will immediately lose access to{' '}
						<strong>{agentName}</strong>: every token issued under this grant is
						revoked. The client must go through consent again to reconnect.
					</p>
				</Dialog>
			)}
		</>
	);
}
