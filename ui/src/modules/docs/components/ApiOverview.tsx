import type { ParsedSpec } from '@/modules/docs/lib/apiSpec';
import { Disclosure, SectionLabel } from '@/modules/docs/components/apiReferenceParts';
import { Markdown } from '@/shared/ui';

/** Human one-liner for a security scheme object. */
function describeScheme(s: Record<string, unknown>): string {
	const type = typeof s.type === 'string' ? s.type : 'unknown';
	if (type === 'http') {
		const scheme = typeof s.scheme === 'string' ? s.scheme : '';
		const fmt = typeof s.bearerFormat === 'string' ? ` (${s.bearerFormat})` : '';
		return `HTTP ${scheme}${fmt}`.trim();
	}
	if (type === 'apiKey') {
		const where = typeof s.in === 'string' ? s.in : '';
		const name = typeof s.name === 'string' ? `: ${s.name}` : '';
		return `API key in ${where}${name}`;
	}
	if (type === 'oauth2') return 'OAuth 2.0';
	if (type === 'openIdConnect') return 'OpenID Connect';
	return type;
}

/** Pull the OAuth flows from a scheme into a flat, render-ready list. */
function oauthFlows(
	s: Record<string, unknown>,
): { name: string; tokenUrl?: string; authUrl?: string; scopes: [string, string][] }[] {
	const flows = s.flows;
	if (typeof flows !== 'object' || flows === null) return [];
	return Object.entries(flows as Record<string, unknown>).map(([name, f]) => {
		const flow = (typeof f === 'object' && f !== null ? f : {}) as Record<string, unknown>;
		const scopesObj =
			typeof flow.scopes === 'object' && flow.scopes !== null
				? (flow.scopes as Record<string, unknown>)
				: {};
		return {
			name,
			tokenUrl: typeof flow.tokenUrl === 'string' ? flow.tokenUrl : undefined,
			authUrl: typeof flow.authorizationUrl === 'string' ? flow.authorizationUrl : undefined,
			scopes: Object.entries(scopesObj).map(
				([k, v]) => [k, typeof v === 'string' ? v : ''] as [string, string],
			),
		};
	});
}

/**
 * One security scheme as an expandable card. Collapsed it states the type in a
 * line; opened it shows the description (how to send the token), the OAuth
 * flow's token URL, and the scope catalogue the scheme documents — so a reader
 * can see "how it looks" and how to use it without leaving the page.
 */
function SchemeCard({ name, scheme }: { name: string; scheme: Record<string, unknown> }) {
	const desc = typeof scheme.description === 'string' ? scheme.description : undefined;
	const flows = oauthFlows(scheme);
	const hasDetail = !!desc || flows.length > 0;

	const summary = (
		<span className="flex flex-wrap items-baseline gap-2">
			<code className="text-primary font-mono text-[13px]">{name}</code>
			<span className="text-foreground-sub text-xs">{describeScheme(scheme)}</span>
		</span>
	);

	if (!hasDetail) {
		return (
			<div className="border-border/40 bg-card/30 rounded-lg border px-3 py-2">{summary}</div>
		);
	}

	return (
		<Disclosure summary={summary}>
			{desc && <Markdown source={desc} className="text-foreground/65 text-[13px]" />}
			{flows.map((flow) => (
				<div key={flow.name} className="mt-3 first:mt-2">
					<p className="text-foreground-faint text-[11px] font-semibold tracking-wider uppercase">
						{flow.name} flow
					</p>
					{flow.tokenUrl && (
						<p className="text-foreground-sub mt-1 text-[12px]">
							Token URL:{' '}
							<code className="bg-muted/40 text-foreground rounded px-1 py-0.5 font-mono text-[11px]">
								{flow.tokenUrl}
							</code>
						</p>
					)}
					{flow.scopes.length > 0 && (
						<ul className="border-border/40 mt-2 space-y-1 border-l pl-3">
							{flow.scopes.map(([scope, scopeDesc]) => (
								<li key={scope} className="flex flex-wrap items-baseline gap-2">
									<code className="bg-primary/15 text-primary rounded px-1.5 py-0.5 font-mono text-[11px]">
										{scope}
									</code>
									{scopeDesc && (
										<span className="text-foreground-sub text-[12px]">
											{scopeDesc}
										</span>
									)}
								</li>
							))}
						</ul>
					)}
				</div>
			))}
		</Disclosure>
	);
}

/**
 * The reference overview: base server URL(s) and the security schemes the spec
 * declares. These are top-level OpenAPI fields (`servers`, `info`,
 * `components.securitySchemes`) that the per-operation view can't show — without
 * them the reader doesn't know where to send requests or how to authenticate.
 */
export function ApiOverview({ parsed }: { parsed: ParsedSpec }) {
	const schemes = Object.entries(parsed.securitySchemes);
	// Only invite the reader to "expand a scheme" when at least one actually has
	// expandable detail (a description or OAuth flows/scopes) — otherwise the
	// hint points at non-interactive cards (e.g. a bare bearer scheme).
	const schemesHaveDetail = schemes.some(
		([, s]) => typeof s.description === 'string' || oauthFlows(s).length > 0,
	);
	const { meta } = parsed;
	const hasMeta = !!(
		meta.contactName ||
		meta.contactEmail ||
		meta.contactUrl ||
		meta.licenseName
	);
	if (parsed.servers.length === 0 && schemes.length === 0 && !hasMeta) return null;

	return (
		<section className="border-border bg-card/40 space-y-4 rounded-lg border p-4">
			<div className="grid gap-4 sm:grid-cols-2">
				{parsed.servers.length > 0 && (
					<div>
						<SectionLabel>Base URL</SectionLabel>
						<ul className="space-y-1">
							{parsed.servers.map((s) => (
								<li key={s.url}>
									<code className="text-foreground bg-muted/40 rounded px-1.5 py-0.5 font-mono text-[13px]">
										{s.url}
									</code>
									{s.description && (
										<span className="text-foreground-sub ml-2 text-xs">
											{s.description}
										</span>
									)}
								</li>
							))}
						</ul>
					</div>
				)}

				{hasMeta && (
					<div>
						<SectionLabel>About</SectionLabel>
						<div className="text-foreground-sub flex flex-wrap gap-x-4 gap-y-1 text-xs">
							{meta.contactName && (
								<span>
									Contact:{' '}
									{meta.contactEmail ? (
										<a
											href={`mailto:${meta.contactEmail}`}
											className="text-primary underline"
										>
											{meta.contactName}
										</a>
									) : (
										meta.contactName
									)}
								</span>
							)}
							{meta.contactUrl && (
								<a
									href={meta.contactUrl}
									target="_blank"
									rel="noreferrer"
									className="text-primary underline"
								>
									Website
								</a>
							)}
							{meta.licenseName && (
								<span>
									License:{' '}
									{meta.licenseUrl ? (
										<a
											href={meta.licenseUrl}
											target="_blank"
											rel="noreferrer"
											className="text-primary underline"
										>
											{meta.licenseName}
										</a>
									) : (
										meta.licenseName
									)}
								</span>
							)}
						</div>
					</div>
				)}
			</div>

			{schemes.length > 0 && (
				<div>
					<SectionLabel>Authentication</SectionLabel>
					<p className="text-foreground-sub mb-2 text-xs">
						Every authenticated request sends{' '}
						<code className="bg-muted/40 text-foreground/70 rounded px-1 py-0.5 font-mono text-[11px]">
							Authorization: Bearer ***
						</code>
						.{schemesHaveDetail ? ' Expand a scheme for details.' : ''}
					</p>
					<div className="space-y-1.5">
						{schemes.map(([name, s]) => (
							<SchemeCard key={name} name={name} scheme={s} />
						))}
					</div>
				</div>
			)}
		</section>
	);
}
