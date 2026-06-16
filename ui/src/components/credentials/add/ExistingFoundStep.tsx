import { useQuery } from '@tanstack/react-query';
import { ChevronRight, Plus, KeyRound, Check } from 'lucide-react';
import type { ApiOut, CredentialOut } from '@/api/types';
import { api } from '@/api/client';
import { Button } from '@/components/ui/Button';
import { LoadingState } from '@/components/ui/LoadingState';
import { StatusDot, deriveCredentialStatus } from '@/components/credentials';
import { timeAgo } from '@/lib/time';

/**
 * Step 2 (conditional) of `<AddCredentialDialog>` — surfaces any
 * existing workspace credentials for the selected API so the user
 * doesn't accidentally double-create. Two outcomes:
 *
 *   - **toolkit mode** — picking an existing credential calls
 *     `onUseExisting(cred)`; the dialog binds it to the toolkit.
 *   - **workspace mode** — picking an existing credential is a
 *     no-op; we still show them so the user can opt to "Add another"
 *     when they're sure they want a fresh one.
 *
 * If the API has zero existing credentials, the parent skips this
 * step entirely and routes to Configure. We keep the empty-state
 * branch here too so a race (the list refreshes and goes empty mid-
 * flight) doesn't leave the user staring at a dead screen.
 */
export interface ExistingFoundStepProps {
	selectedApi: ApiOut;
	mode: 'workspace' | 'toolkit';
	/** Credential ids already bound to the target toolkit (toolkit mode only).
	 *  These rows are shown as "Already bound" and can't be selected, so the
	 *  user never triggers a 409 "Credential already in toolkit". */
	boundCredentialIds?: string[];
	/** Called when the user wants to bind / pick one of the existing
	 *  credentials. Workspace mode hides the row buttons (no-op), so
	 *  this is only fired in toolkit mode. */
	onUseExisting: (cred: CredentialOut) => void;
	/** "Add another" / "Configure new" path. */
	onAddAnother: () => void;
	/** "Change API" path — back to the search step. */
	onChangeApi: () => void;
}

export function ExistingFoundStep({
	selectedApi,
	mode,
	boundCredentialIds,
	onUseExisting,
	onAddAnother,
	onChangeApi,
}: ExistingFoundStepProps) {
	const { data, isLoading } = useQuery({
		queryKey: ['credentials', { api_id: selectedApi.id }],
		queryFn: () => api.listCredentials(selectedApi.id),
		select: (raw: any) =>
			(Array.isArray(raw) ? raw : (raw?.items ?? raw?.data ?? [])) as CredentialOut[],
	});

	const items = data ?? [];
	const bound = new Set(boundCredentialIds ?? []);

	if (isLoading) {
		return <LoadingState message="Checking for existing credentials…" />;
	}

	if (items.length === 0) {
		return (
			<div className="space-y-4">
				<SelectedApiSummary api={selectedApi} onChange={onChangeApi} />
				<p className="text-muted-foreground text-sm">
					No credentials configured for this API yet. Click <strong>Continue</strong> to
					configure a new one.
				</p>
				<Button onClick={onAddAnother} fullWidth>
					Continue
				</Button>
			</div>
		);
	}

	return (
		<div className="space-y-4">
			<SelectedApiSummary api={selectedApi} onChange={onChangeApi} />

			<div>
				<p className="text-muted-foreground mb-1.5 px-1 font-mono text-[10px] tracking-widest uppercase">
					Existing credentials
				</p>
				<p className="text-muted-foreground/80 mb-3 text-xs">
					{mode === 'toolkit'
						? 'Bind one of these to the toolkit, or add a new credential. A toolkit can hold several — the broker picks the right one per request by API, service, or explicit alias.'
						: 'A credential for this API already exists. You can still add another if you need a separate identity.'}
				</p>
				<ul className="space-y-2">
					{items.map((cred) => {
						const isBound = bound.has(cred.id);
						return (
							<li key={cred.id}>
								<ExistingRow
									cred={cred}
									selectable={mode === 'toolkit' && !isBound}
									bound={isBound}
									onSelect={() => onUseExisting(cred)}
								/>
							</li>
						);
					})}
				</ul>
			</div>

			<Button variant="secondary" onClick={onAddAnother} fullWidth>
				<Plus className="h-4 w-4" /> Add another credential
			</Button>
		</div>
	);
}

function SelectedApiSummary({ api: a, onChange }: { api: ApiOut; onChange: () => void }) {
	return (
		<div className="bg-muted/50 border-border flex items-center gap-2 rounded-lg border px-3 py-2.5">
			<div className="min-w-0 flex-1">
				<p className="text-foreground text-sm font-medium">{a.name ?? a.id}</p>
				<p className="text-muted-foreground truncate font-mono text-xs">{a.id}</p>
			</div>
			<Button
				variant="ghost"
				size="sm"
				onClick={onChange}
				className="text-muted-foreground hover:text-foreground shrink-0 text-xs transition-colors"
			>
				Change
			</Button>
		</div>
	);
}

function ExistingRow({
	cred,
	selectable,
	bound,
	onSelect,
}: {
	cred: CredentialOut;
	selectable: boolean;
	bound?: boolean;
	onSelect: () => void;
}) {
	const status = deriveCredentialStatus(cred);
	const body = (
		<>
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
				{cred.identity && (
					<p className="text-muted-foreground mt-0.5 truncate text-xs">{cred.identity}</p>
				)}
			</div>
			{bound ? (
				<span
					title="Already bound to this toolkit"
					className="bg-success/10 text-success border-success/30 inline-flex shrink-0 items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-medium whitespace-nowrap"
				>
					<Check className="h-3 w-3" aria-hidden /> Bound
				</span>
			) : (
				<span className="text-muted-foreground/60 text-xs whitespace-nowrap">
					{cred.last_used_at
						? `used ${timeAgo(cred.last_used_at)}`
						: cred.created_at
							? timeAgo(cred.created_at)
							: ''}
				</span>
			)}
			{selectable && (
				<ChevronRight className="text-muted-foreground h-4 w-4 shrink-0" aria-hidden />
			)}
		</>
	);

	if (selectable) {
		return (
			<button
				type="button"
				onClick={onSelect}
				className="border-border/60 hover:border-primary/40 hover:bg-muted/50 flex w-full items-center gap-3 rounded-lg border p-3 text-left transition-colors"
			>
				{body}
			</button>
		);
	}

	return (
		<div className="border-border/60 bg-background/40 flex w-full items-center gap-3 rounded-lg border p-3">
			{body}
		</div>
	);
}
