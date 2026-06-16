import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { KeyRound, Filter } from 'lucide-react';
import type { CredentialOut } from '@/api/types';
import { api } from '@/api/client';
import { Dialog } from '@/components/ui/Dialog';
import { Button } from '@/components/ui/Button';
import { SearchInput } from '@/components/ui/SearchInput';
import { ErrorAlert } from '@/components/ui/ErrorAlert';
import { messageFromApiError } from '@/lib/apiError';
import { LoadingState } from '@/components/ui/LoadingState';
import { StatusDot, deriveCredentialStatus } from '@/components/credentials';
import { timeAgo } from '@/lib/time';

/**
 * Standalone dialog for binding an existing workspace credential to
 * a toolkit, without going through the full
 * `<AddCredentialDialog>` flow. The "existing credentials found"
 * branch inside the add flow already handles this for the
 * new-credential path; this one is the entry-point used when the
 * user explicitly wants to attach a pre-existing cred.
 *
 * Backend contract: `POST /toolkits/{id}/credentials` (admin-only).
 * No new endpoint — we deliberately keep symmetry with the auto-bind
 * step in `AddCredentialDialog` so toolkit-credential bindings are
 * the only API surface for this concept.
 *
 * The dialog filters out credentials already bound to the toolkit so
 * the user can't accidentally double-bind. We pass that set in via
 * `excludeCredentialIds` from the host (it already has the bound
 * list rendered above the trigger button, so a second fetch is
 * wasteful).
 */
export interface BindExistingCredentialDialogProps {
	open: boolean;
	toolkitId: string;
	toolkitName?: string | null;
	/** Credentials already bound — hidden from the picker. */
	excludeCredentialIds?: string[];
	onClose: () => void;
	/** Fired after the bind succeeds. The host typically uses this to
	 *  toast and refresh its bound-credentials list. */
	onBound?: (credentialId: string) => void;
}

export function BindExistingCredentialDialog({
	open,
	toolkitId,
	toolkitName,
	excludeCredentialIds = [],
	onClose,
	onBound,
}: BindExistingCredentialDialogProps) {
	const queryClient = useQueryClient();
	const [filter, setFilter] = useState('');
	const [error, setError] = useState<string | null>(null);

	useEffect(() => {
		if (!open) {
			setFilter('');
			setError(null);
		}
	}, [open]);

	const { data, isLoading } = useQuery({
		queryKey: ['credentials'],
		queryFn: () => api.listCredentials(),
		select: (raw: unknown) => {
			if (Array.isArray(raw)) return raw as CredentialOut[];
			const d =
				(raw as { data?: unknown; items?: unknown })?.data ??
				(raw as { items?: unknown })?.items;
			return Array.isArray(d) ? (d as CredentialOut[]) : [];
		},
		enabled: open,
	});

	const bindMutation = useMutation({
		mutationFn: (credentialId: string) => api.bindCredential(toolkitId, credentialId),
		onSuccess: (_data, credentialId) => {
			queryClient.invalidateQueries({ queryKey: ['toolkit', toolkitId] });
			queryClient.invalidateQueries({ queryKey: ['toolkits'] });
			queryClient.invalidateQueries({ queryKey: ['toolkit-api-bindings'] });
			queryClient.invalidateQueries({ queryKey: ['toolkit-card-enrichment'] });
			queryClient.invalidateQueries({ queryKey: ['workspace'] });
			queryClient.invalidateQueries({ queryKey: ['credential-bindings', credentialId] });
			onBound?.(credentialId);
			onClose();
		},
		onError: (e) => setError(messageFromApiError(e)),
	});

	const excluded = useMemo(() => new Set(excludeCredentialIds), [excludeCredentialIds]);
	const filtered = useMemo(() => {
		const all = data ?? [];
		const term = filter.trim().toLowerCase();
		return all
			.filter((c) => !excluded.has(c.id))
			.filter((c) => {
				if (!term) return true;
				const haystack =
					`${c.label ?? ''} ${c.api_id ?? ''} ${c.identity ?? ''}`.toLowerCase();
				return haystack.includes(term);
			});
	}, [data, filter, excluded]);

	return (
		<Dialog
			open={open}
			onClose={onClose}
			title={`Bind credential${toolkitName ? ` to ${toolkitName}` : ''}`}
			size="lg"
		>
			{!open ? null : (
				<div className="space-y-4">
					<p className="text-muted-foreground text-sm">
						Pick an existing workspace credential to bind to this toolkit. The toolkit
						gains access to the credential's API on bind; rotation still happens in the
						credential itself.
					</p>

					<SearchInput
						value={filter}
						onValueChange={setFilter}
						icon={<Filter className="h-3.5 w-3.5" />}
						placeholder="Filter by label, API, or identity"
						aria-label="Filter credentials"
					/>

					{error && <ErrorAlert message={error} />}

					{isLoading ? (
						<LoadingState message="Loading credentials…" />
					) : bindMutation.isPending ? (
						<LoadingState message="Binding credential…" />
					) : filtered.length === 0 ? (
						<div className="border-border/50 rounded-lg border border-dashed px-5 py-8 text-center">
							<KeyRound className="text-muted-foreground/50 mx-auto h-6 w-6" />
							<p className="text-muted-foreground mt-2 text-sm">
								{filter
									? 'No credentials match your filter.'
									: 'No more credentials to bind.'}
							</p>
						</div>
					) : (
						<ul className="max-h-[420px] space-y-2 overflow-y-auto pr-1">
							{filtered.map((cred) => (
								<li key={cred.id}>
									<CredentialRow
										cred={cred}
										onSelect={() => {
											setError(null);
											bindMutation.mutate(cred.id);
										}}
									/>
								</li>
							))}
						</ul>
					)}

					<div className="flex justify-end">
						<Button variant="secondary" onClick={onClose}>
							Cancel
						</Button>
					</div>
				</div>
			)}
		</Dialog>
	);
}

function CredentialRow({ cred, onSelect }: { cred: CredentialOut; onSelect: () => void }) {
	const status = deriveCredentialStatus(cred);
	return (
		<button
			type="button"
			onClick={onSelect}
			className="border-border/60 hover:border-primary/40 hover:bg-muted/50 flex w-full items-center gap-3 rounded-lg border p-3 text-left transition-colors"
		>
			<KeyRound className="text-muted-foreground h-4 w-4 shrink-0" />
			<div className="min-w-0 flex-1">
				<div className="flex items-center gap-2">
					<span className="text-foreground text-sm font-medium">
						{cred.label || 'Unnamed'}
					</span>
					<StatusDot
						status={status.tone}
						label={status.label}
						detail={status.detail}
						size="sm"
					/>
					{cred.auth_type && (
						<span className="bg-muted text-muted-foreground rounded px-1.5 py-0.5 text-[10px] font-medium uppercase">
							{cred.auth_type}
						</span>
					)}
				</div>
				<p className="text-muted-foreground mt-0.5 truncate text-xs">
					{cred.api_id}
					{cred.identity ? ` · ${cred.identity}` : ''}
				</p>
			</div>
			<span className="text-muted-foreground/60 text-xs whitespace-nowrap">
				{cred.last_used_at
					? `used ${timeAgo(cred.last_used_at)}`
					: cred.created_at
						? timeAgo(cred.created_at)
						: ''}
			</span>
		</button>
	);
}
