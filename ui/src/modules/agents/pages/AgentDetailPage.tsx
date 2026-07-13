/**
 * Agent detail page — the canonical deep view for a single agent at
 * `/agents/:agentId` (router `basename` adds the `/app` prefix).
 *
 * Real-data only, on jentic-one's contract:
 *   - identity + lifecycle + attribution  → GET /agents/{id} (useAgent)
 *   - bound toolkits                       → GET /agents/{id}/toolkits (useAgentToolkits)
 *   - lifecycle actions                    → the same mutation hooks the list uses
 *
 * Deliberately omitted (no jentic-one backend yet, logged as BACKEND GAPS in the
 * plan): a per-agent live activity feed (`/executions` carries no agent id, so it
 * can't be filtered by agent) and a JWKS card (`AgentResponse` exposes no key
 * metadata). We link out to Monitor for cross-agent execution history instead.
 */
import { useState, type ReactNode } from 'react';
import { useParams } from 'react-router-dom';
import { ArrowRight, KeyRound, Shield, Activity, Ban, History } from 'lucide-react';
import {
	AgentBadge,
	ActorLabel,
	AppLink,
	BackButton,
	Badge,
	Card,
	CardBody,
	CardHeader,
	CardTitle,
	CascadeDeleteDialog,
	CopyButton,
	ErrorAlert,
	LoadingState,
	PageHeader,
	PageShell,
	Button,
} from '@/shared/ui';
import { cn, formatTimestamp, timeAgo } from '@/shared/lib/utils';
import {
	useAgent,
	useAgentToolkits,
	useAgentApiKeyInfo,
	useAgentApiKeyHistory,
	useApproveAgent,
	useDenyAgent,
	useDisableAgent,
	useEnableAgent,
	useArchiveAgent,
	useGenerateAgentApiKey,
	useRevokeAgentApiKey,
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
import { ROUTES, ROUTE_PATHS } from '@/shared/app/routes';

/** A pending lifecycle action awaiting confirmation in a dialog. */
type PendingConfirm =
	| { kind: 'deny' }
	| { kind: 'disable' }
	| { kind: 'archive' }
	| { kind: 'revoke-api-key' }
	| null;

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

export default function AgentDetailPage() {
	const { agentId } = useParams<{ agentId: string }>();
	const id = agentId ?? null;

	const agentQuery = useAgent(id);
	const toolkits = useAgentToolkits(id);
	const apiKeyInfo = useAgentApiKeyInfo(id);
	const apiKeyHistory = useAgentApiKeyHistory(id);

	const approve = useApproveAgent();
	const deny = useDenyAgent();
	const disable = useDisableAgent();
	const enable = useEnableAgent();
	const archive = useArchiveAgent();
	const generateApiKey = useGenerateAgentApiKey();
	const revokeApiKey = useRevokeAgentApiKey();

	const [confirm, setConfirm] = useState<PendingConfirm>(null);
	const [apiKey, setApiKey] = useState<string | null>(null);

	if (agentQuery.isPending) {
		return (
			<PageShell>
				<LoadingState message="Loading agent…" />
			</PageShell>
		);
	}

	// 404 / unknown id → honest not-found, not a fake agent.
	if (agentQuery.error || !agentQuery.data) {
		return (
			<PageShell>
				<PageHeader
					title="Agent not found"
					subtitle={id ? `No agent with id ${id}.` : 'Missing agent id.'}
				/>
				<BackButton to={ROUTES.agents} label="All agents" />
			</PageShell>
		);
	}

	const agent = agentQuery.data;
	const actions = ACTIONS_FOR_STATUS[agent.status];
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
				approve.mutate(agent.id);
				break;
			case 'enable':
				enable.mutate(agent.id);
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
				title={agent.name}
				subtitle="Identity, attribution, bound toolkits, and lifecycle for this agent."
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

			{/* Identity + lifecycle actions */}
			<Card>
				<CardBody className="p-5">
					<div className="flex flex-wrap items-start gap-4">
						<div className="relative shrink-0">
							<AgentBadge id={agent.id} name={agent.name} size="lg" />
							<span
								className={cn(
									'border-background absolute -right-0.5 -bottom-0.5 h-3 w-3 rounded-full border-2',
									STATUS_DOT[agent.status],
								)}
								aria-hidden
							/>
						</div>
						<div className="min-w-0 flex-1">
							<div className="flex flex-wrap items-center gap-2">
								<span className="text-foreground text-lg font-semibold tracking-tight">
									{agent.name}
								</span>
								<ActorStatusBadge status={agent.status} />
							</div>
							<div className="mt-1 flex items-center gap-1.5">
								<code className="text-muted-foreground/80 truncate font-mono text-[11px]">
									{agent.id}
								</code>
								<CopyButton value={agent.id} />
							</div>
							{agent.description && (
								<p className="text-muted-foreground mt-2 text-sm">
									{agent.description}
								</p>
							)}
						</div>

						{/* Lifecycle actions — gated by status, mirrors the list page. */}
						{(actions.length > 0 || agent.status === 'active') && (
							<div className="flex shrink-0 flex-wrap gap-2">
								{agent.status === 'active' && (
									<>
										<Button
											size="sm"
											variant="outline"
											disabled={
												actionPending ||
												generateApiKey.isPending ||
												revokeApiKey.isPending
											}
											loading={generateApiKey.isPending}
											onClick={async () => {
												const result = await generateApiKey.mutateAsync(
													agent.id,
												);
												setApiKey(result.key);
											}}
											aria-label={`${agent.hasApiKey ? 'Regenerate' : 'Generate'} API key for ${agent.name}`}
										>
											<KeyRound className="h-3.5 w-3.5" />
											{agent.hasApiKey
												? 'Regenerate API Key'
												: 'Generate API Key'}
										</Button>
										{agent.hasApiKey && (
											<Button
												size="sm"
												variant="danger"
												disabled={
													actionPending ||
													generateApiKey.isPending ||
													revokeApiKey.isPending
												}
												loading={revokeApiKey.isPending}
												onClick={() =>
													setConfirm({ kind: 'revoke-api-key' })
												}
												aria-label={`Revoke API key for ${agent.name}`}
											>
												<Ban className="h-3.5 w-3.5" />
												Revoke API Key
											</Button>
										)}
									</>
								)}
								{actions.map((action) => (
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
							</div>
						)}
					</div>

					{/* Timestamps / attribution — a quiet bordered grid below identity. */}
					<dl className="border-border/60 mt-4 grid grid-cols-2 gap-x-4 gap-y-3 border-t pt-4 sm:grid-cols-3 lg:grid-cols-4">
						<MetaItem label="Registered" value={formatTimestamp(agent.createdAt)} />
						{agent.attribution.registeredBy ? (
							<MetaItem
								label="Registered by"
								value={<ActorLabel actorId={agent.attribution.registeredBy} />}
							/>
						) : null}
						{agent.approvedAt ? (
							<MetaItem label="Approved" value={formatTimestamp(agent.approvedAt)} />
						) : null}
						{agent.attribution.approvedBy ? (
							<MetaItem
								label="Approved by"
								value={<ActorLabel actorId={agent.attribution.approvedBy} />}
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

					{agent.status === 'rejected' && (
						<div className="border-danger/30 bg-danger/5 mt-4 rounded-lg border p-3">
							<p className="text-danger text-xs font-semibold tracking-wider uppercase">
								Denial reason
							</p>
							<p className="text-foreground/90 mt-1 text-sm">
								{agent.denialReason ?? '—'}
								{agent.attribution.deniedBy && (
									<span className="text-muted-foreground block text-xs">
										by <ActorLabel actorId={agent.attribution.deniedBy} />
									</span>
								)}
							</p>
						</div>
					)}
				</CardBody>
			</Card>

			{/* API Key — shows key metadata even after revocation. */}
			{apiKeyInfo.data && (
				<Card>
					<CardHeader className="flex flex-row items-center justify-between gap-2">
						<div className="flex items-center gap-2">
							<KeyRound className="text-primary h-4 w-4" />
							<CardTitle>API Key</CardTitle>
						</div>
						<Badge variant={apiKeyInfo.data.status === 'active' ? 'success' : 'danger'}>
							{apiKeyInfo.data.status}
						</Badge>
					</CardHeader>
					<CardBody>
						<dl className="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-3">
							<MetaItem label="Key ID" value={apiKeyInfo.data.id} />
							<MetaItem
								label="Created"
								value={formatTimestamp(apiKeyInfo.data.createdAt)}
							/>
							{apiKeyInfo.data.rotatedAt && (
								<MetaItem
									label="Last rotated"
									value={formatTimestamp(apiKeyInfo.data.rotatedAt)}
								/>
							)}
							{apiKeyInfo.data.createdBy && (
								<MetaItem
									label="Created by"
									value={<ActorLabel actorId={apiKeyInfo.data.createdBy} />}
								/>
							)}
						</dl>
					</CardBody>
				</Card>
			)}

			{/* API Key History — audit trail of key operations. */}
			{apiKeyHistory.data && apiKeyHistory.data.length > 0 && (
				<Card>
					<CardHeader className="flex flex-row items-center justify-between gap-2">
						<div className="flex items-center gap-2">
							<History className="text-primary h-4 w-4" />
							<CardTitle>API Key History</CardTitle>
						</div>
					</CardHeader>
					<CardBody className="space-y-2">
						{apiKeyHistory.data.map((entry) => (
							<div
								key={entry.id}
								className="border-border/60 flex items-center justify-between rounded-lg border px-3 py-2"
							>
								<div className="flex items-center gap-2">
									<Badge
										variant={
											entry.reason === 'api_key_revoked'
												? 'danger'
												: 'default'
										}
									>
										{entry.reason === 'api_key_revoked' ? 'Revoked' : 'Rotated'}
									</Badge>
									{entry.actorId && (
										<span className="text-muted-foreground truncate text-xs">
											by <ActorLabel actorId={entry.actorId} />
										</span>
									)}
								</div>
								<span
									className="text-muted-foreground/70 shrink-0 text-[11px]"
									title={formatTimestamp(entry.occurredAt)}
								>
									{timeAgo(entry.occurredAt)}
								</span>
							</div>
						))}
					</CardBody>
				</Card>
			)}

			{/* Bound toolkits — real (GET /agents/{id}/toolkits). */}
			<Card>
				<CardHeader className="flex flex-row items-center justify-between gap-2">
					<div className="flex items-center gap-2">
						<Shield className="text-primary h-4 w-4" />
						<CardTitle>Bound toolkits</CardTitle>
					</div>
				</CardHeader>
				<CardBody className="space-y-2">
					{toolkits.isPending ? (
						<LoadingState size="sm" />
					) : toolkits.error ? (
						<ErrorAlert message={toolkits.error as Error} />
					) : !toolkits.data || toolkits.data.length === 0 ? (
						<div className="text-muted-foreground border-border/60 rounded-lg border border-dashed p-4 text-center text-sm">
							No toolkits bound to this agent.
						</div>
					) : (
						toolkits.data.map((t) => (
							<AppLink
								key={t.id}
								href={ROUTE_PATHS.toolkit(t.toolkitId)}
								className="group hover:border-primary/40 border-border/60 bg-background/40 flex items-center gap-2 rounded-lg border px-3 py-2 transition-colors"
							>
								<KeyRound className="text-muted-foreground h-3.5 w-3.5 shrink-0" />
								<code className="text-foreground flex-1 truncate font-mono text-xs">
									{t.toolkitId}
								</code>
								<span
									className="text-muted-foreground/70 text-[11px]"
									title={formatTimestamp(t.boundAt)}
								>
									{timeAgo(t.boundAt)}
								</span>
								<ArrowRight className="text-muted-foreground/40 group-hover:text-primary h-3.5 w-3.5 shrink-0 transition-colors" />
							</AppLink>
						))
					)}
				</CardBody>
			</Card>

			{/* Scopes — platform permissions granted to this agent (#615). */}
			<ScopesCard actorKind="agent" actorId={agent.id} actorName={agent.name} />

			{/* Pending access requests this agent has filed (#619). */}
			<ActorAccessRequestsCard actorId={agent.id} actorName={agent.name} />

			{/* Activity is cross-agent in jentic-one today (executions carry no agent
			 *  id), so we link to Monitor rather than show a misleading per-agent feed. */}
			<p className="text-muted-foreground flex items-center gap-1.5 text-xs">
				<Activity className="h-3.5 w-3.5" />
				<AppLink href={ROUTES.monitor} className="hover:text-primary">
					Open Monitor
				</AppLink>{' '}
				for execution history across every agent and toolkit.
			</p>

			<DenyDialog
				open={confirm?.kind === 'deny'}
				subjectName={agent.name}
				pending={deny.isPending}
				onConfirm={async (reason) => {
					try {
						await deny.mutateAsync({ id: agent.id, reason });
						setConfirm(null);
					} catch {
						// onError toasts; keep the dialog open so the user can retry.
					}
				}}
				onClose={() => setConfirm(null)}
			/>

			<ConfirmDialog
				open={confirm?.kind === 'disable'}
				title={`Disable ${agent.name}`}
				body="Disabling immediately revokes this agent's ability to authenticate. You can re-enable it later."
				confirmLabel="Disable"
				pending={disable.isPending}
				onConfirm={async () => {
					try {
						await disable.mutateAsync(agent.id);
						setConfirm(null);
					} catch {
						// onError toasts; keep the dialog open so the user can retry.
					}
				}}
				onClose={() => setConfirm(null)}
			/>

			<CascadeDeleteDialog
				open={confirm?.kind === 'archive'}
				entityType="agent"
				entityName={agent.name}
				loading={archive.isPending}
				error={archive.error}
				onConfirm={async () => {
					try {
						await archive.mutateAsync(agent.id);
						setConfirm(null);
					} catch {
						// onError toasts; keep the dialog open so the user can retry.
					}
				}}
				onClose={() => setConfirm(null)}
			/>

			<ConfirmDialog
				open={confirm?.kind === 'revoke-api-key'}
				title={`Revoke API key for ${agent.name}`}
				body="This will immediately invalidate the agent's current API key. The agent will no longer be able to authenticate until a new key is generated."
				confirmLabel="Revoke"
				pending={revokeApiKey.isPending}
				onConfirm={async () => {
					try {
						await revokeApiKey.mutateAsync(agent.id);
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
