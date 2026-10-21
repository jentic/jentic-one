/**
 * Manage access → Credential: the credential behind this API, as a read view —
 * its name, auth type, when it was bound and updated and who uses it, with
 * Edit credential (or "Shared with you"); the full id, copyable; how a call
 * picks it among several; and which of the agent's other API rows share its
 * rules. Editing stacks the shared edit sheet (secrets stay
 * write-only there).
 */
import { Info, Pencil } from 'lucide-react';
import { Button, CodeSnippet, CopyButton, Tooltip, UserText } from '@/shared/ui';
import { formatTimestamp, timeAgo } from '@/shared/lib/utils';
import { SharedWithYouBadge } from '@/shared/credentials/components';
import { credentialTypeIcon } from '@/shared/credentials/lib/authTypeIcons';
import { AgentNameText } from '@/modules/agents/components/AgentNameText';
import {
	MultiCredentialNote,
	credentialIdHeader,
} from '@/modules/agents/components/flat/MultiCredentialNote';
import type { ApiTileModel } from '@/modules/agents/lib/apiTiles';

export interface CredentialSectionProps {
	tile: ApiTileModel;
	agentName: string;
	/** "this agent only" / "3 agents" / "1 of your agents"; null until read. */
	usedBy: string | null;
	/** The viewer may see but not change the credential (someone else's). */
	sharedWithViewer: boolean;
	/** The viewer may edit it (owner or admin, with `credentials:write`). */
	canEdit: boolean;
	onEdit: () => void;
	/** How many of the agent's bindings serve this API. */
	accountCount: number;
	/** The other APIs these rules govern, by title. */
	siblingApiTitles: string[];
}

export function CredentialSection({
	tile,
	agentName,
	usedBy,
	sharedWithViewer,
	canEdit,
	onEdit,
	accountCount,
	siblingApiTitles,
}: CredentialSectionProps) {
	const AuthIcon = tile.authType ? credentialTypeIcon(tile.authType) : null;
	// The credential's last update when one happened, else its creation.
	// Omitted when the org row is unreachable.
	const age = tile.credentialUpdatedAt
		? `updated ${timeAgo(tile.credentialUpdatedAt)}`
		: tile.credentialCreatedAt
			? `added ${timeAgo(tile.credentialCreatedAt)}`
			: null;
	return (
		<>
			<section aria-label="Credential" className="space-y-3">
				<h3 className="text-foreground text-sm font-semibold">Credential</h3>
				<div className="bg-surface-inset flex flex-wrap items-center gap-3 rounded-lg px-4 py-3">
					<div className="min-w-0 flex-1">
						<p className="text-foreground-name truncate text-sm font-semibold">
							<UserText>{tile.credentialName}</UserText>
						</p>
						<p className="text-muted-foreground text-xs">
							{AuthIcon && (
								<AuthIcon
									aria-hidden="true"
									className="mr-1 inline-block h-3 w-3 align-[-2px]"
								/>
							)}
							{tile.authLabel ?? 'Credential'} · bound{' '}
							<Tooltip content={formatTimestamp(tile.boundAt)}>
								<span>{timeAgo(tile.boundAt)}</span>
							</Tooltip>
							{age && <> · {age}</>}
							{/* The blast radius of editing or revoking this secret. */}
							{usedBy && <> · used by {usedBy}</>}
						</p>
					</div>
					{sharedWithViewer ? (
						<SharedWithYouBadge />
					) : (
						canEdit && (
							<Button size="xs" variant="tonal" onClick={onEdit}>
								<Pencil className="h-4 w-4" /> Edit credential
							</Button>
						)
					)}
				</div>
				{/* The full id, copyable: what a call names in Jentic-Credential-Id
				    when the agent holds several. */}
				<div
					data-testid="credential-id-row"
					className="flex min-w-0 items-center gap-2 text-xs"
				>
					<span className="text-muted-foreground shrink-0">ID</span>
					<code className="text-foreground/90 min-w-0 flex-1 font-mono [overflow-wrap:anywhere]">
						{tile.credentialId}
					</code>
					<CopyButton
						value={tile.credentialId}
						size="icon"
						variant="ghost"
						ariaLabel="Copy the credential ID"
						toastMessage="Credential ID copied"
					/>
				</div>
				{accountCount > 1 && (
					<>
						<MultiCredentialNote apiTitle={tile.title} count={accountCount} />
						<CodeSnippet
							label="Header that picks this credential"
							code={credentialIdHeader(tile.credentialId)}
							copyAriaLabel="Copy the Jentic-Credential-Id header"
						/>
					</>
				)}
			</section>

			{/* Blast radius: rules are keyed by (credential, agent) — one
			    credential can serve several APIs. */}
			{siblingApiTitles.length > 0 && (
				<p
					data-testid="blast-radius-note"
					className="bg-surface-inset text-foreground-lighter flex items-start gap-2 rounded-lg px-3.5 py-2.5 text-xs leading-relaxed"
				>
					<Info className="text-caution mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
					<span>
						This credential also serves <strong>{siblingApiTitles.join(', ')}</strong>{' '}
						for <AgentNameText name={agentName} />. The rules below are shared —
						changing them changes access to{' '}
						{siblingApiTitles.length === 1 ? 'that API' : 'those APIs'} too.
					</span>
				</p>
			)}
		</>
	);
}
