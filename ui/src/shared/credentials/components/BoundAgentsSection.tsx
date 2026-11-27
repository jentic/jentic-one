/**
 * BoundAgentsSection — read-mostly "which agents can use this credential?"
 * list inside the edit-credential sheet (theme 5 phase 5a). The credential
 * module has no detail page, and the sheet is already the credential's only
 * "everything about this credential" surface, so the section lives here
 * rather than as a new page or a per-row table expandable.
 *
 * Read-mostly by design: binding/rule management belongs to each agent's API
 * sidebar on the Agents surface (ApiAccessSidebar), so each row just shows
 * the agent name, suspended state (and why, when its API was deleted), when
 * its access ends (or ended), when it last used the credential, bound-at time
 * and links to that agent AS SELECTED on the flat surface — the tab whose
 * tiles hold the bindings this list is talking about, not a separate per-agent
 * page.
 *
 * The one write here is renewing a binding's end date: Renew opens the shared
 * renew control under its row. It takes the same checks as resuming a binding:
 * `agents:write`, the credential's owner or an `org:admin`, AND the agent's
 * owner or an `org:admin`. A credential's owner sees other users' agents here
 * too, so Renew is offered row by row, only on the viewer's own agents (every
 * row, for an admin).
 *
 * Every host renders this inside an overlay, and on the Agents surface that
 * overlay covers the destination. So a plain in-tab click asks the host to
 * dismiss itself (`onNavigateAway`); a modified click opens a second tab and
 * leaves this one exactly as it was.
 *
 * The list holds only the agents the viewer may see: every bound agent for the
 * credential's owner or an `org:admin`, but only their own agents for anyone
 * else it is shared with. For those viewers the copy says "your agents", so it
 * never claims the credential is unused when other users' agents use it.
 */
import { isoDurationSeconds } from '@/shared/lib';
import { useState, type MouseEvent } from 'react';
import { Bot, Hourglass, PauseCircle, RefreshCw } from 'lucide-react';
import { AppLink, Button, ErrorAlert, LoadingState, StatusChip } from '@/shared/ui';
import { ROUTE_PATHS } from '@/shared/app/routes';
import { cn, formatTimestamp, isolateText, timeAgo } from '@/shared/lib/utils';
import { AGENTS_WRITE, useCanAccess, useOptionalCurrentUser } from '@/shared/auth';
import { useCredentialAgents, type CredentialAgentResponse } from '@/shared/credentials/api';
import { credentialEditableBy } from '@/shared/credentials/lib/credentialAuthority';
import { agentBindingChangeableBy } from '@/shared/credentials/lib/bindAuthority';
import {
	bindingExpiresSoon,
	bindingExpiryLabel,
	bindingIsExpired,
	type BindingExpiryFields,
} from '@/shared/credentials/lib/bindingExpiry';
import { RenewBindingControl } from '@/shared/credentials/components/RenewBindingControl';

export function BoundAgentsSection({
	credentialId,
	createdBy,
	open,
	onNavigateAway,
}: {
	credentialId: string;
	/** The credential's creator; with it, a viewer who is neither the creator nor
	 * an admin reads "your agents" copy. Omitted, the copy is unscoped. */
	createdBy?: string | null;
	/** Host sheet visibility — gates the fetch so closed sheets don't poll. */
	open: boolean;
	/**
	 * An agent link was followed IN THIS TAB. Hosts that sit over the Agents
	 * surface use it to close themselves, so the agent that was just asked for
	 * isn't left underneath the overlay that asked for it.
	 */
	onNavigateAway?: () => void;
}) {
	const agents = useCredentialAgents(credentialId, { enabled: open });
	const allRows = agents.data?.data ?? [];
	// Finding unused access: the longest-unused (never used first) on top.
	const [unusedFirst, setUnusedFirst] = useState(false);
	const rows = unusedFirst ? [...allRows].sort(byLongestUnused) : allRows;
	const viewer = useOptionalCurrentUser();
	const yoursOnly =
		createdBy !== undefined && !credentialEditableBy({ created_by: createdBy }, viewer);
	// Renewing re-grants the credential: its owner or an admin, with
	// agents:write. An owner that can't be read is not the viewer — fail closed.
	// Each row then also needs the viewer to own its agent (or be an admin).
	const mayWriteAgents = useCanAccess(AGENTS_WRITE);
	const mayRenewCredential =
		mayWriteAgents &&
		createdBy !== undefined &&
		credentialEditableBy({ created_by: createdBy }, viewer);
	// The one row whose renew control is open.
	const [renewing, setRenewing] = useState<string | null>(null);

	// Only a plain left click navigates this tab; a modified click is the
	// browser's own "open elsewhere" and must leave this view untouched.
	const handleAgentClick = (event: MouseEvent<HTMLAnchorElement>): void => {
		if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
		if (event.button !== 0) return;
		onNavigateAway?.();
	};

	return (
		<div className="bg-surface-inset space-y-2 rounded-lg p-3">
			<div className="flex items-center justify-between gap-2">
				<p className="text-foreground-name text-sm font-semibold">
					{yoursOnly ? 'Your bound agents' : 'Bound agents'}
					{agents.isSuccess ? ` (${rows.length})` : ''}
				</p>
				{rows.length > 1 && (
					<Button
						size="xs"
						variant="ghost"
						aria-pressed={unusedFirst}
						onClick={(): void => setUnusedFirst((on) => !on)}
						data-testid="bound-agents-sort-unused"
					>
						Longest unused first
					</Button>
				)}
			</div>
			<p className="text-foreground-sub text-xs">
				{yoursOnly
					? "Your agents allowed to call APIs with this credential. Other users' agents aren't listed."
					: 'Agents allowed to call APIs with this credential.'}{' '}
				Manage bindings from each agent&apos;s API tiles on the Agents page.
			</p>

			{agents.isPending ? (
				<LoadingState size="sm" />
			) : agents.isError ? (
				<ErrorAlert message="Failed to load bound agents." />
			) : rows.length === 0 ? (
				<p
					className="text-muted-foreground text-xs italic"
					data-testid="bound-agents-empty"
				>
					{yoursOnly
						? 'None of your agents are bound to this credential.'
						: 'No agents are bound to this credential.'}
				</p>
			) : (
				<ul className="space-y-1">
					{rows.map((row) => (
						<BoundAgentRow
							key={row.agent_id}
							row={row}
							credentialId={credentialId}
							canRenew={
								mayRenewCredential && agentBindingChangeableBy(row.owner_id, viewer)
							}
							renewOpen={renewing === row.agent_id}
							onToggleRenew={(): void =>
								setRenewing((current) =>
									current === row.agent_id ? null : row.agent_id,
								)
							}
							onAgentClick={handleAgentClick}
						/>
					))}
				</ul>
			)}
		</div>
	);
}

function BoundAgentRow({
	row,
	credentialId,
	canRenew,
	renewOpen,
	onToggleRenew,
	onAgentClick,
}: {
	row: CredentialAgentResponse;
	credentialId: string;
	canRenew: boolean;
	renewOpen: boolean;
	onToggleRenew: () => void;
	onAgentClick: (event: MouseEvent<HTMLAnchorElement>) => void;
}) {
	const binding = expiryFields(row);
	const expired = bindingIsExpired(binding);
	const expiry = bindingExpiryLabel(binding);
	return (
		<li
			data-testid="bound-agent-row"
			data-expired={expired || undefined}
			className="bg-surface-tonal space-y-2 rounded-md px-2 py-1.5"
		>
			<div className="flex items-center justify-between gap-2">
				<span className="flex min-w-0 items-center gap-1.5">
					<Bot className="text-muted-foreground h-3.5 w-3.5 shrink-0" />
					<AppLink
						href={ROUTE_PATHS.agentTab(row.agent_id)}
						onClick={onAgentClick}
						className="text-foreground truncate text-xs font-medium hover:underline"
					>
						{row.agent_name}
					</AppLink>
					{row.suspended && (
						<StatusChip
							tone="caution"
							icon={PauseCircle}
							data-testid="bound-agent-suspended"
						>
							{/* Only an API delete records a reason; a manual pause has none. */}
							{row.suspended_reason === 'api_deleted'
								? 'Suspended · API deleted'
								: 'Suspended'}
						</StatusChip>
					)}
					{expired && (
						<StatusChip
							tone="caution"
							icon={Hourglass}
							data-testid="bound-agent-expired"
						>
							Expired
						</StatusChip>
					)}
				</span>
				<span className="text-muted-foreground flex shrink-0 items-center gap-1.5 text-[11px]">
					<span>
						{row.last_used_at !== undefined && (
							<span data-testid="bound-agent-last-used">
								{lastUsedText(row.last_used_at)}
								{' · '}
							</span>
						)}
						bound {timeAgo(row.bound_at)}
					</span>
					{canRenew && (
						<Button
							size="xs"
							variant={expired ? 'tonal' : 'ghost'}
							aria-expanded={renewOpen}
							aria-label={`${expired ? 'Renew' : 'Change the expiry'} for ${isolateText(row.agent_name)}`}
							onClick={onToggleRenew}
							data-testid="bound-agent-renew"
						>
							<RefreshCw className="h-3 w-3" aria-hidden="true" />
							{expired ? 'Renew' : 'Expiry'}
						</Button>
					)}
				</span>
			</div>
			{expiry && (
				<p
					className={cn(
						'flex items-center gap-1 text-[11px]',
						expired || bindingExpiresSoon(binding)
							? 'text-caution'
							: 'text-muted-foreground',
					)}
					title={row.expires_at ? formatTimestamp(row.expires_at) : undefined}
					data-testid="bound-agent-expiry"
				>
					<Hourglass className="h-3 w-3 shrink-0" aria-hidden="true" />
					{expiry}
				</p>
			)}
			{canRenew && renewOpen && (
				<RenewBindingControl
					agentId={row.agent_id}
					agentName={row.agent_name}
					credentialId={credentialId}
					expiresAt={row.expires_at}
					expired={expired}
					maxIdlePeriod={isoDurationSeconds(row.max_idle_period)}
					idleWarning={row.idle_warning ?? false}
					afterExpiry={row.after_expiry ?? null}
					maxQuota={row.max_quota ?? null}
					onRenewed={onToggleRenew}
					testIdPrefix="bound-agent-renew-control"
				/>
			)}
		</li>
	);
}

/** A roster row's end date, in the shape the expiry helpers read. */
function expiryFields(row: CredentialAgentResponse): BindingExpiryFields {
	return {
		expiresAt: row.expires_at ?? null,
		expired: row.expired ?? false,
		expiredReason: row.expired_reason ?? null,
		maxIdlePeriod: isoDurationSeconds(row.max_idle_period),
		idleExpiresAt: row.idle_expires_at ?? null,
		maxQuota: row.max_quota ?? null,
		quotaRemaining: row.quota_remaining ?? null,
	};
}

/** "used 3d ago" / "used just now" / "never used" for a bound agent's last call. */
function lastUsedText(lastUsedAt: string | null): string {
	if (lastUsedAt === null) return 'never used';
	const age = timeAgo(lastUsedAt);
	return age === 'now' ? 'used just now' : `used ${age} ago`;
}

/** Never used first, then the oldest last use first. */
function byLongestUnused(a: CredentialAgentResponse, b: CredentialAgentResponse): number {
	const at = (row: CredentialAgentResponse): number =>
		row.last_used_at ? Date.parse(row.last_used_at) : Number.NEGATIVE_INFINITY;
	return at(a) - at(b);
}
