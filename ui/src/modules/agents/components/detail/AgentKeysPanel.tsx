/**
 * AgentKeysPanel — the detail page's Keys tab: current API-key metadata,
 * generate/regenerate/revoke actions, and the rotation history. Relocated
 * from the identity header + flat card stack so the credential story lives
 * in one place. Plaintext is still shown exactly once, via ApiKeyDialog.
 *
 * Generation is only offered for `active` agents (the backend rejects keys
 * for other statuses); metadata for an already-issued key stays visible in
 * every status so a disabled agent's key trail remains auditable.
 */
import { useState } from 'react';
import { Ban, History, KeyRound } from 'lucide-react';
import {
	ActorLabel,
	Badge,
	Button,
	Card,
	CardBody,
	CardHeader,
	CardTitle,
	LoadingState,
} from '@/shared/ui';
import { formatTimestamp, timeAgo } from '@/shared/lib/utils';
import {
	useAgentApiKeyInfo,
	useAgentApiKeyHistory,
	useGenerateAgentApiKey,
	useRevokeAgentApiKey,
	type AgentEntity,
} from '@/modules/agents/api';
import { ApiKeyDialog } from '@/modules/agents/components/ApiKeyDialog';
import { ConfirmDialog } from '@/modules/agents/components/confirm/ConfirmDialog';

/** A compact label/value pair used in the key meta grid. */
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

export function AgentKeysPanel({ agent }: { agent: AgentEntity }) {
	const apiKeyInfo = useAgentApiKeyInfo(agent.id);
	const apiKeyHistory = useAgentApiKeyHistory(agent.id);
	const generateApiKey = useGenerateAgentApiKey();
	const revokeApiKey = useRevokeAgentApiKey();

	const [apiKey, setApiKey] = useState<string | null>(null);
	const [confirmRevoke, setConfirmRevoke] = useState(false);

	if (apiKeyInfo.isPending) {
		return <LoadingState size="sm" message="Loading API key…" />;
	}

	const info = apiKeyInfo.data;
	const history = apiKeyHistory.data ?? [];
	const mutating = generateApiKey.isPending || revokeApiKey.isPending;

	return (
		<div className="space-y-4">
			<Card>
				<CardHeader className="flex flex-row items-center justify-between gap-2">
					<div className="flex items-center gap-2">
						<KeyRound className="text-primary h-4 w-4" />
						<CardTitle>API Key</CardTitle>
					</div>
					{info && (
						<Badge variant={info.status === 'active' ? 'success' : 'danger'}>
							{info.status}
						</Badge>
					)}
				</CardHeader>
				<CardBody className="space-y-4">
					{info ? (
						<dl className="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-3">
							<MetaItem label="Key ID" value={info.id} />
							<MetaItem label="Created" value={formatTimestamp(info.createdAt)} />
							{info.rotatedAt && (
								<MetaItem
									label="Last rotated"
									value={formatTimestamp(info.rotatedAt)}
								/>
							)}
							{info.createdBy && (
								<MetaItem
									label="Created by"
									value={<ActorLabel actorId={info.createdBy} />}
								/>
							)}
						</dl>
					) : (
						<p className="text-muted-foreground text-sm">
							No API key has been issued for this agent yet.
						</p>
					)}

					{agent.status === 'active' ? (
						<div className="flex flex-wrap gap-2">
							<Button
								size="sm"
								variant="outline"
								disabled={mutating}
								loading={generateApiKey.isPending}
								onClick={async () => {
									const result = await generateApiKey.mutateAsync(agent.id);
									setApiKey(result.key);
								}}
								aria-label={`${agent.hasApiKey ? 'Regenerate' : 'Generate'} API key for ${agent.name}`}
							>
								<KeyRound className="h-3.5 w-3.5" />
								{agent.hasApiKey ? 'Regenerate API Key' : 'Generate API Key'}
							</Button>
							{agent.hasApiKey && (
								<Button
									size="sm"
									variant="danger"
									disabled={mutating}
									loading={revokeApiKey.isPending}
									onClick={() => setConfirmRevoke(true)}
									aria-label={`Revoke API key for ${agent.name}`}
								>
									<Ban className="h-3.5 w-3.5" />
									Revoke API Key
								</Button>
							)}
						</div>
					) : (
						<p className="text-muted-foreground text-xs">
							Keys can only be issued while the agent is active.
						</p>
					)}
				</CardBody>
			</Card>

			{history.length > 0 && (
				<Card>
					<CardHeader className="flex flex-row items-center gap-2">
						<History className="text-primary h-4 w-4" />
						<CardTitle>API Key History</CardTitle>
					</CardHeader>
					<CardBody className="space-y-2">
						{history.map((entry) => (
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

			<ConfirmDialog
				open={confirmRevoke}
				title={`Revoke API key for ${agent.name}`}
				body="This will immediately invalidate the agent's current API key. The agent will no longer be able to authenticate until a new key is generated."
				confirmLabel="Revoke"
				pending={revokeApiKey.isPending}
				onConfirm={async () => {
					try {
						await revokeApiKey.mutateAsync(agent.id);
						setConfirmRevoke(false);
					} catch {
						// onError toasts; keep the dialog open so the user can retry.
					}
				}}
				onClose={() => setConfirmRevoke(false)}
			/>

			<ApiKeyDialog open={apiKey != null} apiKey={apiKey} onClose={() => setApiKey(null)} />
		</div>
	);
}
