import { Plus, Key, KeyRound } from 'lucide-react';
import { AppLink } from '@/components/ui/AppLink';
import { Skeleton } from '@/components/ui/Skeleton';
import { SectionTitle } from '@/components/discovery/SectionTitle';
import { timeAgo } from '@/lib/time';

interface CredentialsSectionProps {
	credentials: any[];
	isLoading: boolean;
	apiId: string;
}

/**
 * Renders the "Credentials" block of the API detail surface — empty
 * state with an inline CTA, or a list of credentials each linking to
 * its edit page. Purely presentational; the parent owns the data.
 */
export function CredentialsSection({ credentials, isLoading, apiId }: CredentialsSectionProps) {
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
						<AppLink
							href={`/credentials/new?api_id=${encodeURIComponent(apiId)}`}
							className="text-primary hover:text-primary/80 mt-2 inline-flex items-center gap-1 text-sm font-medium"
						>
							<Plus className="h-3.5 w-3.5" /> Add credential
						</AppLink>
					</div>
				) : (
					<ul className="space-y-2">
						{credentials.map((cred: any) => (
							<li key={cred.id}>
								<AppLink
									href={`/credentials/${encodeURIComponent(cred.id)}/edit`}
									className="border-border/50 hover:border-primary/40 hover:bg-muted/50 flex items-center gap-3 rounded-lg border p-3 transition-colors"
								>
									<KeyRound className="text-muted-foreground h-4 w-4 shrink-0" />
									<div className="min-w-0 flex-1">
										<div className="flex items-baseline gap-2">
											<span className="text-foreground text-sm font-medium">
												{cred.label || 'Unnamed'}
											</span>
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
										{cred.created_at ? timeAgo(cred.created_at) : ''}
									</span>
								</AppLink>
							</li>
						))}
						<li>
							<AppLink
								href={`/credentials/new?api_id=${encodeURIComponent(apiId)}`}
								className="text-primary hover:text-primary/80 mt-1 inline-flex items-center gap-1 text-sm font-medium"
							>
								<Plus className="h-3.5 w-3.5" /> Add credential
							</AppLink>
						</li>
					</ul>
				)}
			</div>
		</section>
	);
}
