import { useQuery } from '@tanstack/react-query';
import { Settings, Trash2, RotateCcw } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { StatusDot, type CredentialStatus } from './StatusDot';
import type { CredentialOut } from '@/api/types';
import { api } from '@/api/client';
import { AppLink } from '@/components/ui/AppLink';
import { Badge } from '@/components/ui/Badge';
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
 * One row in the credentials list.
 *
 * Visually a thin card per row (the page's main content), structured the same
 * way every workspace surface lays out its objects:
 *
 *   [vendor icon] [primary identity + secondary metadata]   [actions]
 *
 * Differences from the old inline implementation:
 *
 * 1. Uses `<VendorIcon>` keyed off `api_id` so the same brand logo the user
 *    sees in Discover and the workspace tile shows up here too. We've been
 *    using a generic key icon, which made every credential look identical
 *    — useless for users with 5+ creds across different APIs.
 *
 * 2. Renders a **`StatusDot`** with the live health derived from
 *    `last_used_at` and the new /test endpoint hint. Static auth types
 *    (bearer / apiKey) get an "OK / unknown" reading from `last_used_at`;
 *    Pipedream OAuth picks up its `healthy` bit from the broker rows
 *    (passed through `cred.synced_at` for now — Phase 3 wires the actual
 *    health field once the API exposes it).
 *
 * 3. Shows **toolkit binding chips** sourced from `/credentials/:id/bindings`.
 *    Each chip is a real link to the toolkit detail page. Capped at 3 visible
 *    so a credential bound to many toolkits stays scannable; overflow goes
 *    into a `+N more` chip that's also a link (to the credentials filter).
 *
 * 4. **Local / Catalog** badge surfaced via `cred.api_source` (set by the
 *    parent component since the API row lives in `apis.list` not the cred).
 *
 * The row is intentionally NOT a single big link — the row is multi-action
 * (Edit, Delete, sometimes Reconnect) and a wrapping `<a>` would steal
 * keyboard focus from those buttons.
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

	const status = deriveStatus(cred);
	const isPipedream = cred.auth_type === 'pipedream_oauth';

	return (
		<div className="bg-muted border-border rounded-xl border p-4">
			<div className="flex items-center gap-3">
				<VendorIcon
					name={cred.label || cred.id}
					vendor={cred.api_id ?? undefined}
					size="md"
				/>
				<div className="min-w-0 flex-1">
					<div className="flex flex-wrap items-center gap-2">
						<button
							type="button"
							onClick={handleEditClick}
							className="text-foreground hover:text-primary truncate font-medium focus-visible:underline focus-visible:outline-none"
						>
							{cred.label}
						</button>

						<StatusDot status={status.tone} label={status.label} />

						{cred.api_id && (
							<span className="text-muted-foreground font-mono text-xs">
								{cred.api_id}
							</span>
						)}
						{(cred as CredentialOut & { api_local?: boolean }).api_local !==
							undefined && (
							<Badge
								variant={
									(cred as CredentialOut & { api_local?: boolean }).api_local
										? 'success'
										: 'default'
								}
								className="text-[10px] uppercase"
							>
								{(cred as CredentialOut & { api_local?: boolean }).api_local
									? 'Local'
									: 'Catalog'}
							</Badge>
						)}
						{isPipedream ? (
							<Badge variant="default" className="text-[10px]">
								OAuth via Pipedream
							</Badge>
						) : cred.scheme_name ? (
							<Badge variant="default" className="text-[10px]">
								{cred.scheme_name}
							</Badge>
						) : cred.auth_type ? (
							<Badge variant="default" className="text-[10px]">
								{cred.auth_type}
							</Badge>
						) : null}
					</div>

					<div className="text-muted-foreground mt-0.5 flex flex-wrap items-baseline gap-x-3 gap-y-1 text-xs">
						{cred.description && (
							<span className="text-foreground/70 max-w-md truncate italic">
								{cred.description}
							</span>
						)}
						{cred.last_used_at != null && (
							<span>last used {timeAgo(cred.last_used_at)}</span>
						)}
						{cred.last_used_at == null && cred.created_at != null && (
							<span>added {timeAgo(cred.created_at)}</span>
						)}
						{isPipedream && cred.account_id && (
							<span className="font-mono text-[11px] opacity-70">
								{cred.account_id}
							</span>
						)}
					</div>

					{bindings.length > 0 && (
						<div className="mt-2 flex flex-wrap items-center gap-1.5">
							<span className="text-muted-foreground/80 text-[11px] tracking-wider uppercase">
								Used by
							</span>
							{bindings.slice(0, 3).map((b) => (
								<AppLink
									key={b.toolkit_id}
									href={`/toolkits/${encodeURIComponent(b.toolkit_id)}`}
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
						</div>
					)}
				</div>

				<div className="flex items-center gap-2">
					{isPipedream && onReconnect && status.tone === 'broken' && (
						<Button variant="secondary" size="sm" onClick={onReconnect}>
							<RotateCcw className="h-4 w-4" /> Reconnect
						</Button>
					)}
					<Button variant="secondary" size="sm" onClick={handleEditClick}>
						<Settings className="h-4 w-4" /> Edit
					</Button>
					{/* Delete now opens a real dialog with the toolkit
					   binding cascade preview (see CredentialsList →
					   ConfirmDeleteDialog). The previous ConfirmInline
					   was a single-tap shortcut that gave users no
					   visibility into which toolkits would lose this
					   credential — see Phase 4 of the credentials
					   revamp plan. */}
					<Button
						variant="danger"
						size="sm"
						onClick={onDelete}
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
 * Tone derivation for the row's `StatusDot`.
 *
 * Order matters — Pipedream's `healthy=false` is the most authoritative
 * signal we have (the broker recorded a 401/403). After that we fall back
 * to "fresh use" as a positive signal, then to "never used" (unknown).
 *
 * NB: This is intentionally **not** a network probe — that's the /test
 * button's job. Rendering 50 credential rows must not fan out 50 upstream
 * GETs.
 */
function deriveStatus(cred: CredentialOut): { tone: CredentialStatus; label: string } {
	if (cred.healthy === false) {
		return {
			tone: 'broken',
			label: 'OAuth grant rejected — reconnect to restore.',
		};
	}
	if (cred.last_used_at) {
		return {
			tone: 'ok',
			label: `Last used ${timeAgo(cred.last_used_at)} — broker observed a healthy upstream call.`,
		};
	}
	return {
		tone: 'unknown',
		label: 'Not yet probed. Use Test connection on the edit page to verify.',
	};
}
