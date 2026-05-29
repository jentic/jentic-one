import { Shield } from 'lucide-react';
import { SectionTitle } from '@/components/discovery/SectionTitle';

/**
 * Turns an OpenAPI security scheme record into a human-readable
 * label. Handles the common `http`/`apiKey`/`oauth2`/`openIdConnect`
 * shapes and falls back to the raw `type` string otherwise.
 */
export function formatSchemeType(scheme: Record<string, unknown>): string {
	const type = scheme.type as string | undefined;
	if (!type) return 'Unknown';

	switch (type) {
		case 'http': {
			const httpScheme = (scheme.scheme as string | undefined)?.toLowerCase();
			if (httpScheme === 'bearer') {
				const format = scheme.bearerFormat as string | undefined;
				return format ? `Bearer (${format})` : 'Bearer Token';
			}
			if (httpScheme === 'basic') return 'Basic Auth';
			return `HTTP ${httpScheme ?? ''}`.trim();
		}
		case 'apiKey': {
			const loc = scheme.in as string | undefined;
			const name = scheme.name as string | undefined;
			if (loc && name) return `API Key in ${loc} (${name})`;
			if (loc) return `API Key in ${loc}`;
			return 'API Key';
		}
		case 'oauth2':
			return 'OAuth 2.0';
		case 'openIdConnect':
			return 'OpenID Connect';
		default:
			return type;
	}
}

function SecuritySchemeCard({ name, scheme }: { name: string; scheme: Record<string, unknown> }) {
	const label = formatSchemeType(scheme);
	const description = scheme.description as string | undefined;
	const httpScheme = scheme.scheme as string | undefined;
	const bearerFormat = scheme.bearerFormat as string | undefined;
	const keyIn = scheme.in as string | undefined;
	const keyName = scheme.name as string | undefined;
	const openIdUrl = scheme.openIdConnectUrl as string | undefined;

	return (
		<li className="border-border/50 rounded-lg border p-3">
			<div className="flex items-start gap-3">
				<Shield className="text-muted-foreground mt-0.5 h-4 w-4 shrink-0" />
				<div className="min-w-0 flex-1">
					<div className="flex items-baseline gap-2">
						<code className="text-foreground text-sm font-medium">{name}</code>
						<span className="bg-muted text-muted-foreground rounded px-1.5 py-0.5 text-[10px] font-medium">
							{label}
						</span>
					</div>
					{description && (
						<p className="text-muted-foreground mt-1 text-xs leading-relaxed">
							{description}
						</p>
					)}
					<div className="text-muted-foreground/80 mt-1.5 flex flex-wrap gap-x-4 gap-y-1 text-[11px]">
						{httpScheme && httpScheme !== 'bearer' && httpScheme !== 'basic' && (
							<span>
								Scheme: <code className="text-foreground/70">{httpScheme}</code>
							</span>
						)}
						{bearerFormat && (
							<span>
								Format: <code className="text-foreground/70">{bearerFormat}</code>
							</span>
						)}
						{keyIn && (
							<span>
								Location: <code className="text-foreground/70">{keyIn}</code>
							</span>
						)}
						{keyName && (
							<span>
								Parameter: <code className="text-foreground/70">{keyName}</code>
							</span>
						)}
						{openIdUrl && (
							<span>
								Discovery:{' '}
								<a
									href={openIdUrl}
									target="_blank"
									rel="noopener noreferrer"
									className="text-primary hover:underline"
								>
									{openIdUrl}
								</a>
							</span>
						)}
					</div>
				</div>
			</div>
		</li>
	);
}

interface SecuritySchemesSectionProps {
	schemes: Record<string, unknown>;
}

/**
 * Renders the "Security schemes" block on the API detail surface.
 * Returns null when the API exposes no schemes so the section
 * disappears entirely (the spec rarely defines them all).
 */
export function SecuritySchemesSection({ schemes }: SecuritySchemesSectionProps) {
	const entries = Object.entries(schemes);
	if (entries.length === 0) return null;

	return (
		<section>
			<SectionTitle count={entries.length}>Security Schemes</SectionTitle>
			<ul className="mt-3 space-y-2">
				{entries.map(([name, scheme]) => (
					<SecuritySchemeCard
						key={name}
						name={name}
						scheme={scheme as Record<string, unknown>}
					/>
				))}
			</ul>
		</section>
	);
}
