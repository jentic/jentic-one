/**
 * ApiSummary — the top of the catalog preview sheet's body: the API's
 * `info.description`, then how a caller authenticates (`ApiAuthRow`).
 *
 * The description is Markdown (sanitized) truncated to
 * a word boundary at ~280 chars with a "Show more / Show less" toggle. When the
 * spec has no description there's nothing to show.
 *
 * The auth row reads the spec's `securitySchemes` and says, in plain words,
 * what kind of credential the API wants and where it goes on the request.
 * Nothing renders when the spec declares no schemes (or they haven't loaded).
 */
import { useState } from 'react';
import { KeyRound } from 'lucide-react';
import { Button, Markdown, type SecuritySchemeMap } from '@/shared/ui';

const SUMMARY_TRUNCATE = 280;

interface ApiSummaryProps {
	description?: string | null;
}

export function ApiSummary({ description }: ApiSummaryProps) {
	const [expanded, setExpanded] = useState(false);
	const desc = (description ?? '').trim();
	if (!desc) return null;

	const truncated = desc.length > SUMMARY_TRUNCATE;
	const raw = desc.slice(0, SUMMARY_TRUNCATE);
	const visible =
		!truncated || expanded
			? desc
			: (raw.includes(' ') ? raw.slice(0, raw.lastIndexOf(' ')) : raw).trimEnd();

	return (
		<div data-testid="api-summary" className="mb-[22px]">
			<Markdown
				source={visible + (truncated && !expanded ? '…' : '')}
				className="text-foreground-lighter text-sm leading-[1.6] opacity-85"
			/>
			{truncated && (
				<Button
					variant="ghost"
					onClick={() => setExpanded((v) => !v)}
					className="text-accent-teal mt-1 h-auto p-0 text-xs font-medium hover:bg-transparent hover:underline active:scale-100"
					data-testid="api-summary-toggle"
				>
					{expanded ? 'Show less' : 'Show more'}
				</Button>
			)}
		</div>
	);
}

/** One plain-language line about a security scheme. */
export interface AuthSummary {
	/** What kind of credential: "API key", "Bearer token", "OAuth 2.0"… */
	label: string;
	/** Where it travels on the request, e.g. "Header · X-API-Key". */
	detail?: string;
}

const LOCATION_LABEL: Record<string, string> = {
	header: 'Header',
	query: 'Query',
	cookie: 'Cookie',
};

const AUTH_HEADER = 'Header · Authorization';

function str(v: unknown): string | undefined {
	return typeof v === 'string' && v.trim() ? v.trim() : undefined;
}

function capitalize(s: string): string {
	return s.charAt(0).toUpperCase() + s.slice(1).toLowerCase();
}

/** Describe one OpenAPI (or Swagger 2) security scheme object. */
export function describeSecurityScheme(
	name: string,
	scheme: Record<string, unknown> | undefined,
): AuthSummary {
	const type = str(scheme?.type)?.toLowerCase();
	const httpScheme = str(scheme?.scheme)?.toLowerCase();

	if (type === 'apikey') {
		const where = str(scheme?.in)?.toLowerCase();
		const param = str(scheme?.name);
		const location = where ? (LOCATION_LABEL[where] ?? capitalize(where)) : undefined;
		const detail = [location, param].filter(Boolean).join(' · ');
		return { label: 'API key', detail: detail || undefined };
	}
	// Swagger 2 spells HTTP basic as its own type.
	if (type === 'basic' || (type === 'http' && httpScheme === 'basic')) {
		return { label: 'Basic auth', detail: `${AUTH_HEADER}: Basic` };
	}
	if (type === 'http') {
		if (!httpScheme || httpScheme === 'bearer') {
			return { label: 'Bearer token', detail: `${AUTH_HEADER}: Bearer` };
		}
		return {
			label: `HTTP ${capitalize(httpScheme)}`,
			detail: `${AUTH_HEADER}: ${capitalize(httpScheme)}`,
		};
	}
	if (type === 'oauth2') {
		return { label: 'OAuth 2.0', detail: `${AUTH_HEADER}: Bearer` };
	}
	if (type === 'openidconnect') {
		return { label: 'OpenID Connect', detail: `${AUTH_HEADER}: Bearer` };
	}
	if (type === 'mutualtls') {
		return { label: 'Mutual TLS' };
	}
	return { label: name };
}

/**
 * Every scheme the spec declares, described and de-duplicated (two API-key
 * schemes on the same header read as one line).
 */
export function summarizeAuth(schemes: SecuritySchemeMap | null | undefined): AuthSummary[] {
	const seen = new Set<string>();
	const out: AuthSummary[] = [];
	for (const [name, scheme] of Object.entries(schemes ?? {})) {
		const entry = describeSecurityScheme(name, scheme);
		const key = `${entry.label}|${entry.detail ?? ''}`;
		if (seen.has(key)) continue;
		seen.add(key);
		out.push(entry);
	}
	return out;
}

/** The inset "how do I authenticate" line(s) under the description. */
export function ApiAuthRow({ schemes }: { schemes: SecuritySchemeMap | null | undefined }) {
	const entries = summarizeAuth(schemes);
	if (entries.length === 0) return null;
	return (
		<ul className="mb-[22px] space-y-1.5" aria-label="Authentication" data-testid="api-auth">
			{entries.map((e) => (
				<li
					key={`${e.label}|${e.detail ?? ''}`}
					className="bg-surface-inset text-foreground-lighter flex items-center gap-2.5 rounded-[10px] px-3 py-2.5 text-[13px]"
				>
					<KeyRound
						size={14}
						className="text-muted-foreground shrink-0"
						aria-hidden="true"
					/>
					<span className="shrink-0">{e.label}</span>
					{e.detail && (
						<span className="text-muted-foreground ml-auto min-w-0 truncate font-mono text-[11.5px]">
							{e.detail}
						</span>
					)}
				</li>
			))}
		</ul>
	);
}
