import { KeyRound, Layers } from 'lucide-react';
import { AppLink } from '@/components/ui/AppLink';
import { SectionTitle } from '@/components/discovery/SectionTitle';

export interface BoundToolkit {
	id: string;
	name: string;
	apiCredentials: Array<{ id: string; label: string }>;
}

interface ToolkitsSectionProps {
	toolkits: BoundToolkit[];
}

/**
 * Renders the "Toolkits" block on the API detail surface. Each row
 * links to the toolkit detail page and reveals the credentials of
 * that toolkit which point at this specific API, so the user can see
 * exactly which keys would break if the API were removed.
 */
export function ToolkitsSection({ toolkits }: ToolkitsSectionProps) {
	return (
		<section>
			<SectionTitle count={toolkits.length}>Toolkits</SectionTitle>
			{toolkits.length > 0 ? (
				<ul className="mt-3 space-y-3">
					{toolkits.map((tk) => (
						<li key={tk.id}>
							<AppLink
								href={`/toolkits/${tk.id}`}
								className="border-border/50 hover:border-primary/40 hover:bg-muted/50 block rounded-lg border p-3 transition-colors"
							>
								<div className="flex items-center gap-2">
									<Layers className="text-muted-foreground h-4 w-4 shrink-0" />
									<span className="text-foreground text-sm font-medium">
										{tk.name}
									</span>
									<span className="text-muted-foreground ml-auto text-xs">
										{tk.apiCredentials.length} credential
										{tk.apiCredentials.length !== 1 ? 's' : ''}
									</span>
								</div>
								{tk.apiCredentials.length > 0 && (
									<div className="mt-2 flex flex-wrap gap-1.5 pl-6">
										{tk.apiCredentials.map((cred) => (
											<span
												key={cred.id}
												className="bg-muted text-muted-foreground inline-flex items-center gap-1 rounded px-2 py-0.5 text-xs"
											>
												<KeyRound className="h-3 w-3" />
												{cred.label}
											</span>
										))}
									</div>
								)}
							</AppLink>
						</li>
					))}
				</ul>
			) : (
				<p className="text-muted-foreground mt-2 text-sm">
					No toolkits bound to this API yet.
				</p>
			)}
		</section>
	);
}
