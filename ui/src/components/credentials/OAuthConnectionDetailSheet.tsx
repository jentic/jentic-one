import { useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ArrowUpRight, Check, Loader2, RotateCcw, Trash2, X } from 'lucide-react';
import { StatusDot, type CredentialStatus } from './StatusDot';
import { deriveCredentialStatus } from './credentialStatus';
import { api, oauthBrokers } from '@/api/client';
import type { CredentialOut } from '@/api/types';
import { AppLink } from '@/components/ui/AppLink';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Label } from '@/components/ui/Label';
import { LoadingState } from '@/components/ui/LoadingState';
import { Textarea } from '@/components/ui/Textarea';
import { VendorIcon } from '@/components/discovery/VendorIcon';
import { SheetPrimitive } from '@/components/ui/SheetPrimitive';
import { toast } from '@/components/ui/toastStore';
import { emitCredentialImported } from '@/lib/events/credentialImported';
import { timeAgo } from '@/lib/time';

/** Broker that backs the OAuth-connection credentials surfaced here. */
const BROKER_ID = 'pipedream';

/**
 * Read-mostly detail sheet for a Pipedream-managed OAuth connection.
 *
 * Why this exists: "OAuth connections" are conceptually different from
 * manual credentials — the secret (the OAuth grant) lives upstream in
 * Pipedream, so there is nothing to paste or rotate locally. Routing
 * their card click into `<CredentialEditSheet>` (the manual-secret form)
 * made no sense, so previously the card was inert — it *looked* clickable
 * but did nothing. This sheet gives the click a real destination: the
 * connection's facts, the two pieces of metadata the user actually owns,
 * and the lifecycle actions (reconnect / delete).
 *
 * What's editable, and why it's safe:
 *
 *  - **Label** → written through the broker (`renameAccount`), NOT a local
 *    `PATCH /credentials`. The Pipedream sync treats `oauth_broker_accounts`
 *    as the single source of truth for the label and overwrites the local
 *    `credentials.label` on every run — so a local rename would silently
 *    revert. The broker endpoint updates both rows, so the rename survives
 *    the next sync.
 *  - **Description** → local `PATCH /credentials`. The sync's credential
 *    UPDATE never touches `description`, so it's genuinely user-owned and
 *    safe to edit in place.
 *
 * Everything else (API host, account id, environment, status, timestamps)
 * is read-only — it reflects upstream state we don't author.
 *
 * Lifecycle mirrors `<CredentialEditSheet>`: the host owns a sticky id via
 * `useOAuthConnectionSheet` so the body survives the close animation.
 */
export interface OAuthConnectionDetailSheetProps {
	/** Credential id of the OAuth connection. `null` renders nothing. */
	credentialId: string | null;
	open: boolean;
	onClose: () => void;
	onAfterClose?: () => void;
	/**
	 * Open the host's delete-confirmation flow for this connection. The
	 * host owns the `ConfirmDeleteDialog` (it needs the toolkit-binding
	 * cascade preview), so we just signal intent and let it take over.
	 */
	onDelete: (cred: CredentialOut) => void;
}

export function OAuthConnectionDetailSheet({
	credentialId,
	open,
	onClose,
	onAfterClose,
	onDelete,
}: OAuthConnectionDetailSheetProps) {
	const headingId = 'oauth-connection-sheet-title';
	const closeButtonRef = useRef<HTMLButtonElement | null>(null);
	const queryClient = useQueryClient();

	const { data: cred, isLoading } = useQuery<CredentialOut>({
		queryKey: ['credential', credentialId],
		queryFn: () => api.getCredential(credentialId!),
		enabled: !!credentialId,
	});

	const { data: bindings = [] } = useQuery({
		queryKey: ['credential-bindings', credentialId],
		queryFn: () => api.credentialBindings(credentialId!),
		enabled: !!credentialId,
		staleTime: 30_000,
	});

	// Local draft state for the two editable fields. Seeded from the
	// credential whenever a *different* connection loads (keyed on id, not
	// `open`, per the dialog-state-lifecycle rule — re-opening the same
	// connection keeps any in-progress edit).
	const [label, setLabel] = useState('');
	const [description, setDescription] = useState('');
	useEffect(() => {
		if (cred) {
			setLabel(cred.label ?? '');
			setDescription(cred.description ?? '');
		}
	}, [cred?.id]); // eslint-disable-line react-hooks/exhaustive-deps -- seed per-connection, not per-render

	useEffect(() => {
		if (open) closeButtonRef.current?.focus();
	}, [open, credentialId]);

	const labelMutation = useMutation({
		mutationFn: (nextLabel: string) => {
			if (!cred?.account_id) throw new Error('This connection has no account id to rename.');
			return oauthBrokers.renameAccount(BROKER_ID, cred.account_id, nextLabel.trim());
		},
		onSuccess: () => {
			queryClient.invalidateQueries({ queryKey: ['credentials'] });
			queryClient.invalidateQueries({ queryKey: ['credential', credentialId] });
			queryClient.invalidateQueries({ queryKey: ['oauth-broker-accounts'] });
			toast({ title: 'Connection renamed', variant: 'success' });
		},
		onError: (e: Error) =>
			toast({ title: 'Rename failed', description: e.message, variant: 'error' }),
	});

	const descriptionMutation = useMutation({
		mutationFn: (nextDescription: string) =>
			api.updateCredential(credentialId!, { description: nextDescription.trim() || null }),
		onSuccess: () => {
			queryClient.invalidateQueries({ queryKey: ['credentials'] });
			queryClient.invalidateQueries({ queryKey: ['credential', credentialId] });
			toast({ title: 'Notes saved', variant: 'success' });
		},
		onError: (e: Error) =>
			toast({ title: 'Save failed', description: e.message, variant: 'error' }),
	});

	const reconnectMutation = useMutation({
		mutationFn: () => {
			if (!cred?.account_id)
				throw new Error('This connection has no account id to reconnect.');
			return oauthBrokers.reconnectLink(BROKER_ID, cred.account_id);
		},
		onSuccess: (res) => {
			window.open(res.connect_link_url, '_blank', 'noopener,noreferrer');
			// The grant lands asynchronously in the other tab; nudge the
			// caches so the status dot refreshes when the user returns.
			queryClient.invalidateQueries({ queryKey: ['credentials'] });
			queryClient.invalidateQueries({ queryKey: ['credential', credentialId] });
			queryClient.invalidateQueries({ queryKey: ['oauth-broker-accounts'] });
			if (cred?.api_id) emitCredentialImported({ api_id: cred.api_id });
			toast({
				title: 'Reconnect started',
				description: 'Finish authorising in the new tab, then return here.',
				variant: 'success',
			});
		},
		onError: (e: Error) =>
			toast({ title: 'Reconnect failed', description: e.message, variant: 'error' }),
	});

	const status = cred ? deriveStatus(cred) : null;
	const labelDirty = !!cred && label.trim() !== (cred.label ?? '').trim() && label.trim() !== '';
	const descriptionDirty = !!cred && description.trim() !== (cred.description ?? '').trim();

	return (
		<SheetPrimitive
			open={open}
			onClose={onClose}
			onAfterClose={onAfterClose}
			side="right"
			ariaLabelledBy={headingId}
			initialFocus={closeButtonRef}
		>
			<div className="flex h-full flex-col">
				<header className="border-border flex items-center justify-between gap-2 border-b px-5 py-3">
					<div className="min-w-0">
						<h2 id={headingId} className="text-foreground text-base font-semibold">
							OAuth connection
						</h2>
						<p className="text-muted-foreground truncate text-xs">
							Managed by Pipedream
						</p>
					</div>
					<Button
						ref={closeButtonRef}
						variant="ghost"
						size="sm"
						aria-label="Close"
						onClick={onClose}
						className="text-muted-foreground hover:text-foreground"
					>
						<X className="h-4 w-4" />
					</Button>
				</header>

				<div className="flex-1 overflow-y-auto px-5 py-4">
					{credentialId && isLoading && (
						<LoadingState
							message="Loading connection…"
							icon={<Loader2 className="h-5 w-5 animate-spin" />}
						/>
					)}

					{cred && status && (
						<div className="space-y-6">
							{/* Identity header — vendor icon + name + status dot. */}
							<div className="flex items-center gap-3">
								<VendorIcon
									name={cred.label || cred.id}
									vendor={cred.api_id ?? undefined}
									size="lg"
								/>
								<div className="min-w-0 flex-1">
									<div className="flex items-center gap-2">
										<h3 className="text-foreground min-w-0 flex-1 truncate text-sm font-semibold">
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

							{/* Broken-grant callout — the single most actionable state. */}
							{status.tone === 'broken' && (
								<div className="border-danger/30 bg-danger/10 text-danger flex items-start gap-2 rounded-lg border px-3 py-2.5 text-xs">
									<RotateCcw className="mt-0.5 h-4 w-4 shrink-0" />
									<span>
										Pipedream rejected this grant. Reconnect to restore access —
										your toolkits keep their binding.
									</span>
								</div>
							)}

							{/* Editable: label (broker-backed, sync-safe). */}
							<div>
								<Label
									htmlFor="oauth-label"
									className="text-muted-foreground mb-1 block text-xs"
								>
									Label
								</Label>
								<div className="flex items-center gap-2">
									<Input
										id="oauth-label"
										value={label}
										onChange={(e) => setLabel(e.target.value)}
										className="bg-background"
									/>
									<Button
										variant="secondary"
										size="sm"
										disabled={!labelDirty || labelMutation.isPending}
										loading={labelMutation.isPending}
										onClick={() => labelMutation.mutate(label)}
										aria-label="Save label"
									>
										{!labelMutation.isPending && <Check className="h-4 w-4" />}
										Save
									</Button>
								</div>
								<p className="text-muted-foreground/70 mt-1 text-[11px]">
									The display name for this connection. Synced to Pipedream so it
									survives the next account sync.
								</p>
							</div>

							{/* Editable: description (local-only, sync-safe). */}
							<div>
								<Label
									htmlFor="oauth-description"
									className="text-muted-foreground mb-1 block text-xs"
								>
									Notes{' '}
									<span className="text-muted-foreground/60">(optional)</span>
								</Label>
								<Textarea
									id="oauth-description"
									value={description}
									onChange={(e) => setDescription(e.target.value)}
									placeholder="What is this connection for? Who owns it?"
									rows={2}
									className="bg-background"
								/>
								<div className="mt-1.5 flex items-center justify-between gap-2">
									<p className="text-muted-foreground/70 text-[11px]">
										Stored locally — never sent to Pipedream.
									</p>
									<Button
										variant="secondary"
										size="sm"
										disabled={
											!descriptionDirty || descriptionMutation.isPending
										}
										loading={descriptionMutation.isPending}
										onClick={() => descriptionMutation.mutate(description)}
									>
										{!descriptionMutation.isPending && (
											<Check className="h-4 w-4" />
										)}
										Save notes
									</Button>
								</div>
							</div>

							{/* Read-only facts. */}
							<dl className="border-border/60 divide-border/60 divide-y rounded-lg border text-xs">
								<DetailRow label="Status" value={status.summary} />
								<DetailRow
									label="API host"
									value={
										cred.api_id ? (
											<span className="font-mono">{cred.api_id}</span>
										) : (
											'—'
										)
									}
								/>
								<DetailRow
									label="Account ID"
									value={
										cred.account_id ? (
											<span className="font-mono break-all">
												{cred.account_id}
											</span>
										) : (
											'—'
										)
									}
								/>
								<DetailRow
									label="Last used"
									value={cred.last_used_at ? timeAgo(cred.last_used_at) : 'Never'}
								/>
								<DetailRow
									label="Synced"
									value={cred.synced_at ? timeAgo(cred.synced_at) : '—'}
								/>
								<DetailRow
									label="Added"
									value={cred.created_at ? timeAgo(cred.created_at) : '—'}
								/>
							</dl>

							{/* Toolkit bindings — same "used by" language as the row. */}
							<div>
								<p className="text-muted-foreground/80 mb-1.5 text-[10px] tracking-wider uppercase">
									Used by
								</p>
								{bindings.length > 0 ? (
									<div className="flex flex-wrap gap-1.5">
										{bindings.map((b) => (
											<AppLink
												key={b.toolkit_id}
												href={`/toolkits/${encodeURIComponent(b.toolkit_id)}`}
												className="border-border/60 bg-background hover:border-primary/40 hover:text-primary text-foreground/80 rounded-full border px-2 py-0.5 text-[11px] transition-colors"
											>
												{b.toolkit_name || b.toolkit_id}
											</AppLink>
										))}
									</div>
								) : (
									<p className="text-muted-foreground/70 text-[11px]">
										Not used by any toolkit
									</p>
								)}
							</div>
						</div>
					)}
				</div>

				{/* Lifecycle footer — reconnect (left) + delete (right). Flush
				    card footer matching the sheet surface, mirroring
				    CredentialFormFields' sheet layout. */}
				{cred && (
					<div className="border-border bg-card flex shrink-0 items-center gap-2 border-t px-5 py-4">
						<Button
							variant="secondary"
							className="flex-1"
							loading={reconnectMutation.isPending}
							disabled={!cred.account_id || reconnectMutation.isPending}
							onClick={() => reconnectMutation.mutate()}
						>
							{!reconnectMutation.isPending && <ArrowUpRight className="h-4 w-4" />}
							Reconnect
						</Button>
						<Button
							variant="danger"
							onClick={() => onDelete(cred)}
							aria-label={`Delete connection ${cred.label}`}
						>
							<Trash2 className="h-4 w-4" />
							Delete
						</Button>
					</div>
				)}
			</div>
		</SheetPrimitive>
	);
}

function DetailRow({ label, value }: { label: string; value: React.ReactNode }) {
	return (
		<div className="flex items-start justify-between gap-3 px-3 py-2">
			<dt className="text-muted-foreground shrink-0">{label}</dt>
			<dd className="text-foreground min-w-0 text-right">{value}</dd>
		</div>
	);
}

/**
 * Tone + human-readable summary for the connection's status. Delegates to the
 * shared `deriveCredentialStatus` so the dot + tooltip read identically to the
 * /credentials list, and adds a terse `summary` string for the read-only facts
 * table on this sheet.
 */
function deriveStatus(cred: CredentialOut): {
	tone: CredentialStatus;
	label: string;
	detail?: string;
	summary: string;
} {
	const base = deriveCredentialStatus(cred);
	const summary =
		base.tone === 'broken'
			? 'Grant rejected'
			: base.tone === 'ok'
				? 'Healthy'
				: base.tone === 'unknown'
					? 'Unverified'
					: 'Not yet used';
	return { ...base, summary };
}
