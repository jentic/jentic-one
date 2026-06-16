import { Plus, Key, KeyRound } from 'lucide-react';
import { AppLink } from '@/components/ui/AppLink';
import { Button } from '@/components/ui/Button';
import { Skeleton } from '@/components/ui/Skeleton';
import { SectionTitle } from '@/components/discovery/SectionTitle';
import { StatusDot, deriveCredentialStatus } from '@/components/credentials';
import { timeAgo } from '@/lib/time';

interface CredentialsSectionProps {
	credentials: any[];
	isLoading: boolean;
	apiId: string;
	/**
	 * When provided, clicking a credential row fires this instead of
	 * navigating to `/credentials/:id/edit`. Used by hosts (today:
	 * `ApiDetailPage`) that mount a `CredentialEditSheet` and prefer
	 * an inline edit experience to leaving the page. The fallback
	 * `<AppLink>` path is preserved for hosts that haven't migrated.
	 */
	onEditCredential?: (cred: any) => void;
	/**
	 * When provided, the inline "Add credential" affordances (empty
	 * state CTA + footer link) call this instead of routing to
	 * `/credentials/new?api_id=…`. Hosts that mount the v3 add
	 * dialog wire this to `dialog.openForApi(apiData)`.
	 */
	onAddCredential?: () => void;
}

/**
 * Renders the "Credentials" block of the API detail surface — empty
 * state with an inline CTA, or a list of credentials each linking to
 * its edit page. Purely presentational; the parent owns the data.
 *
 * Each row carries a `StatusDot` derived from the credential's
 * `last_used_at` / `healthy` / `health_checked_at` via the shared
 * `deriveCredentialStatus` helper — the same derivation the main
 * /credentials list uses, so the two surfaces never drift.
 */
export function CredentialsSection({
	credentials,
	isLoading,
	apiId,
	onEditCredential,
	onAddCredential,
}: CredentialsSectionProps) {
	// Shared row rendering — used by both the link path (default)
	// and the button path (when `onEditCredential` is provided).
	const renderRowBody = (cred: any) => {
		const status = deriveCredentialStatus(cred);
		return (
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
						<p className="text-muted-foreground mt-0.5 truncate text-xs">
							{cred.identity}
						</p>
					)}
				</div>
				<span className="text-muted-foreground/60 text-xs whitespace-nowrap">
					{cred.last_used_at
						? `used ${timeAgo(cred.last_used_at)}`
						: cred.created_at
							? timeAgo(cred.created_at)
							: ''}
				</span>
			</>
		);
	};
	return (
		<section>
			<SectionTitle count={credentials.length}>Credentials</SectionTitle>
			<div className="mt-3">
				{isLoading ? (
					<div className="space-y-2">
						<Skeleton className="h-12 w-full rounded-lg" />
						<Skeleton className="h-12 w-full rounded-lg" />
					</div>
				) : credentials.length === 0 ? (
					<div className="border-border/50 rounded-lg border border-dashed px-5 py-6 text-center">
						<Key className="text-muted-foreground/50 mx-auto h-6 w-6" />
						<p className="text-muted-foreground mt-2 text-sm">
							No credentials configured yet.
						</p>
						{onAddCredential ? (
							<Button
								variant="ghost"
								size="sm"
								onClick={onAddCredential}
								className="text-primary hover:text-primary/80 mt-2 inline-flex items-center gap-1 text-sm font-medium"
							>
								<Plus className="h-3.5 w-3.5" /> Add credential
							</Button>
						) : (
							<AppLink
								href={`/credentials/new?api_id=${encodeURIComponent(apiId)}`}
								className="text-primary hover:text-primary/80 mt-2 inline-flex items-center gap-1 text-sm font-medium"
							>
								<Plus className="h-3.5 w-3.5" /> Add credential
							</AppLink>
						)}
					</div>
				) : (
					<ul className="space-y-2">
						{credentials.map((cred: any) => (
							<li key={cred.id}>
								{onEditCredential ? (
									<button
										type="button"
										onClick={() => onEditCredential(cred)}
										className="border-border/50 hover:border-primary/40 hover:bg-muted/50 flex w-full items-center gap-3 rounded-lg border p-3 text-left transition-colors"
									>
										{renderRowBody(cred)}
									</button>
								) : (
									<AppLink
										href={`/credentials/${encodeURIComponent(cred.id)}/edit`}
										className="border-border/50 hover:border-primary/40 hover:bg-muted/50 flex items-center gap-3 rounded-lg border p-3 transition-colors"
									>
										{renderRowBody(cred)}
									</AppLink>
								)}
							</li>
						))}
						<li>
							{onAddCredential ? (
								<Button
									variant="ghost"
									size="sm"
									onClick={onAddCredential}
									className="text-primary hover:text-primary/80 mt-1 inline-flex items-center gap-1 text-sm font-medium"
								>
									<Plus className="h-3.5 w-3.5" /> Add credential
								</Button>
							) : (
								<AppLink
									href={`/credentials/new?api_id=${encodeURIComponent(apiId)}`}
									className="text-primary hover:text-primary/80 mt-1 inline-flex items-center gap-1 text-sm font-medium"
								>
									<Plus className="h-3.5 w-3.5" /> Add credential
								</AppLink>
							)}
						</li>
					</ul>
				)}
			</div>
		</section>
	);
}
