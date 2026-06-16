import { useQuery } from '@tanstack/react-query';
import { Settings, Trash2, RotateCcw } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { StatusDot } from './StatusDot';
import { deriveCredentialStatus } from './credentialStatus';
import type { CredentialOut } from '@/api/types';
import { api } from '@/api/client';
import { AppLink } from '@/components/ui/AppLink';
import { Button } from '@/components/ui/Button';
import { VendorIcon } from '@/components/discovery/VendorIcon';
import { timeAgo } from '@/lib/time';

interface CredentialRowProps {
	cred: CredentialOut;
	onDelete: () => void;
	/**
	 * Called when the user clicks Edit (or the credential label) on a
	 * non-Pipedream row. When provided, this takes precedence over
	 * navigating to `/credentials/:id/edit` — used by host pages that
	 * mount a `CredentialEditSheet` and want the click to open the
	 * sheet instead of leaving the page.
	 *
	 * Pipedream rows ignore this prop and use `onEditPipedream`
	 * instead, because broker-managed credentials don't go through the
	 * regular form (their value is upstream).
	 */
	onEdit?: (cred: CredentialOut) => void;
	onEditPipedream?: () => void;
	onReconnect?: () => void;
	deleting?: boolean;
}

/**
 * One credential card in the credentials grid.
 *
 * Deliberately mirrors `ToolkitCard`'s anatomy so the Credentials and
 * Toolkits pages read as one family:
 *
 *   [vendor icon] [name + source]
 *   [description]
 *   [used-by chips]
 *   [meta row .......... edit · delete]
 *
 * Behaviour:
 *
 * 1. The whole card is the edit affordance — clicking anywhere that isn't a
 *    nested control (the action buttons, the "used by" links) opens the edit
 *    sheet. The footer buttons stop propagation so they don't double-fire.
 *
 * 2. `<VendorIcon>` is keyed off `api_id`, so each credential carries the
 *    brand logo the user already recognises from Discover and the workspace.
 *
 * 3. A `<StatusDot>` sits next to the name (health from `last_used_at` /
 *    Pipedream `healthy`).
 *
 * NB: an earlier iteration rendered a "Self-hosted API" / "From Discover"
 * source label here, keyed off `apis.list`'s `local` flag. It was removed
 * because the backend doesn't record API provenance — `local` means
 * "registered in this workspace" (true for every credentialed API, since
 * adding a credential auto-imports the catalog spec), so the label could
 * never be correct. Don't reintroduce it without a real origin signal.
 */
export function CredentialRow({
	cred,
	onDelete,
	onEdit,
	onEditPipedream,
	onReconnect,
	deleting,
}: CredentialRowProps) {
	const navigate = useNavigate();

	const handleEditClick = () => {
		if (cred.auth_type === 'pipedream_oauth') {
			if (onEditPipedream) onEditPipedream();
			return;
		}
		if (onEdit) {
			onEdit(cred);
			return;
		}
		// Legacy fallback: hosts that haven't adopted the sheet still
		// navigate to the route-based form. Will be removed in Phase 5
		// once every host wires `onEdit`.
		navigate(`/credentials/${encodeURIComponent(cred.id)}/edit`);
	};

	const { data: bindings = [] } = useQuery({
		queryKey: ['credential-bindings', cred.id],
		queryFn: () => api.credentialBindings(cred.id),
		// Bindings are derived data — happy to show them stale on revisits.
		staleTime: 30_000,
	});

	const status = deriveCredentialStatus(cred);
	const isPipedream = cred.auth_type === 'pipedream_oauth';
	return (
		<div
			role="button"
			tabIndex={0}
			onClick={handleEditClick}
			onKeyDown={(e) => {
				if (e.key === 'Enter' || e.key === ' ') {
					e.preventDefault();
					handleEditClick();
				}
			}}
			aria-label={`Edit credential ${cred.label}`}
			className="group border-border/60 bg-card hover:border-border hover:bg-muted/30 focus-visible:ring-primary/40 flex h-full min-w-0 cursor-pointer flex-col gap-3 overflow-hidden rounded-xl border p-4 text-left transition-all hover:-translate-y-0.5 hover:shadow-sm focus-visible:ring-2 focus-visible:outline-none"
		>
			<div className="flex items-center gap-3">
				<VendorIcon
					name={cred.label || cred.id}
					vendor={cred.api_id ?? undefined}
					size="lg"
				/>
				<div className="min-w-0 flex-1">
					<div className="flex items-center gap-2">
						<h3 className="font-heading text-foreground min-w-0 flex-1 truncate text-sm font-semibold">
							{cred.label}
						</h3>
						<StatusDot
							status={status.tone}
							label={status.label}
							detail={status.detail}
						/>
					</div>
					{cred.api_id && (
						<p className="text-muted-foreground mt-0.5 truncate font-mono text-xs">
							{cred.api_id}
						</p>
					)}
				</div>
			</div>

			{/* Description slot — always rendered, reserved at exactly two lines
			    (`min-h-[2lh]` + `line-clamp-2`), so cards in the same grid row
			    keep identical vertical rhythm whether or not a description
			    exists. Falls back to the humanized auth type so the space
			    reads as content, not a hole. */}
			<p className="text-muted-foreground line-clamp-2 min-h-[2lh] text-xs leading-snug break-words">
				{cred.description || authTypeLabel(cred.auth_type)}
			</p>

			{/* Bindings slot — same always-rendered treatment as the description
			    (min-h matches the chip row) so the async bindings query doesn't
			    shift layout when it resolves, and unbound cards stay level with
			    bound neighbours. "Not used" is information, not filler. */}
			<div className="flex min-h-[24px] flex-wrap items-center gap-1.5">
				{bindings.length > 0 ? (
					<>
						<span className="text-muted-foreground/80 text-[10px] tracking-wider uppercase">
							Used by
						</span>
						{bindings.slice(0, 3).map((b) => (
							<AppLink
								key={b.toolkit_id}
								href={`/toolkits/${encodeURIComponent(b.toolkit_id)}`}
								onClick={(e) => e.stopPropagation()}
								className="border-border/60 bg-background hover:border-primary/40 hover:text-primary text-foreground/80 rounded-full border px-2 py-0.5 text-[11px] transition-colors"
							>
								{b.toolkit_name || b.toolkit_id}
							</AppLink>
						))}
						{bindings.length > 3 && (
							<span className="text-muted-foreground text-[11px]">
								+{bindings.length - 3} more
							</span>
						)}
					</>
				) : (
					<span className="text-muted-foreground/70 text-[11px]">
						Not used by any toolkit
					</span>
				)}
			</div>

			<div className="border-border/50 mt-auto flex items-center gap-2 border-t pt-3">
				<div className="text-muted-foreground flex min-w-0 flex-1 flex-col gap-0.5 text-[11px]">
					<span className="truncate">
						{cred.last_used_at != null
							? `last used ${timeAgo(cred.last_used_at)}`
							: cred.created_at != null
								? `added ${timeAgo(cred.created_at)}`
								: isPipedream
									? 'OAuth via Pipedream'
									: (cred.auth_type ?? '')}
					</span>
				</div>

				<div className="flex shrink-0 items-center gap-1">
					{isPipedream && onReconnect && status.tone === 'broken' && (
						<Button
							variant="secondary"
							size="sm"
							onClick={(e) => {
								e.stopPropagation();
								onReconnect();
							}}
							aria-label={`Reconnect ${cred.label}`}
						>
							<RotateCcw className="h-4 w-4" />
						</Button>
					)}
					<Button
						variant="secondary"
						size="sm"
						onClick={(e) => {
							e.stopPropagation();
							handleEditClick();
						}}
						aria-label={`Edit credential ${cred.label}`}
					>
						<Settings className="h-4 w-4" />
					</Button>
					{/* Delete opens a confirmation dialog with the toolkit
					   binding cascade preview (see CredentialsList →
					   ConfirmDeleteDialog) so the user sees which toolkits
					   would lose this credential before committing. */}
					<Button
						variant="danger"
						size="sm"
						onClick={(e) => {
							e.stopPropagation();
							onDelete();
						}}
						disabled={deleting}
						aria-label={`Delete credential ${cred.label}`}
					>
						<Trash2 className="h-4 w-4" />
					</Button>
				</div>
			</div>
		</div>
	);
}

/**
 * Card-shaped skeleton that mirrors `CredentialRow`'s layout exactly so the
 * grid doesn't shift when real data resolves. Anatomy matches the row:
 *
 *   [vendor icon] [name + api_id]
 *   [description]
 *   [meta row .......... actions]
 *
 * Used by `CredentialsListSkeleton`. Kept alongside `CredentialRow` for the
 * same reason `ToolkitCardSkeleton` lives next to `ToolkitCard` — the two
 * must move together.
 */
export function CredentialCardSkeleton() {
	return (
		<div className="border-border/60 bg-card flex h-full min-w-0 flex-col gap-3 rounded-xl border p-4">
			<div className="flex items-center gap-3">
				<div className="bg-muted h-12 w-12 shrink-0 animate-pulse rounded-xl" />
				<div className="min-w-0 flex-1 space-y-2">
					<div className="bg-muted h-4 w-2/3 animate-pulse rounded" />
					<div className="bg-muted h-3 w-1/2 animate-pulse rounded" />
				</div>
			</div>
			<div className="space-y-1.5">
				<div className="bg-muted h-3 w-full animate-pulse rounded" />
				<div className="bg-muted h-3 w-3/5 animate-pulse rounded" />
			</div>
			<div className="bg-muted h-5 w-32 animate-pulse rounded-full" />
			<div className="border-border/50 mt-auto flex items-center gap-3 border-t pt-3">
				<div className="bg-muted h-3 w-24 animate-pulse rounded" />
				<div className="ml-auto flex gap-1">
					<div className="bg-muted h-7 w-7 animate-pulse rounded-md" />
					<div className="bg-muted h-7 w-7 animate-pulse rounded-md" />
				</div>
			</div>
		</div>
	);
}

/**
 * A section title placeholder plus a grid of card skeletons in the same
 * responsive layout as the populated list, with a small staggered shimmer
 * delay. Drop-in replacement for the generic spinner so the loading frame
 * matches the loaded frame — same as `ToolkitsListSkeleton` does for Toolkits.
 */
export function CredentialsListSkeleton({ count = 6 }: { count?: number }) {
	return (
		<div className="space-y-5" aria-hidden="true" data-testid="credentials-skeleton">
			<div className="bg-muted h-4 w-32 animate-pulse rounded" />
			<div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
				{Array.from({ length: count }).map((_, i) => (
					// eslint-disable-next-line react/no-array-index-key -- static placeholders, never reordered
					<div key={`cred-skeleton-${i}`} style={{ animationDelay: `${i * 60}ms` }}>
						<CredentialCardSkeleton />
					</div>
				))}
			</div>
		</div>
	);
}

/**
 * Humanized auth-type fallback for the description slot. Credentials
 * frequently have no description; showing *what kind* of secret this is
 * keeps the reserved two-line slot informative instead of blank.
 */
function authTypeLabel(authType: string | null | undefined): string {
	switch (authType) {
		case 'bearer':
			return 'Bearer token';
		case 'basic':
			return 'Basic auth credential';
		case 'oauth2':
			return 'OAuth 2.0 credential';
		case 'pipedream_oauth':
			return 'OAuth connection via Pipedream';
		case 'apiKey':
		case 'api_key':
			return 'API key';
		case null:
		case undefined:
		case '':
			return 'Stored credential';
		default:
			// Unknown/custom scheme names (e.g. "JenticApiKey") pass through —
			// the raw scheme is still more useful than a generic placeholder.
			return authType;
	}
}
