// Pure helpers for working with OpenAPI `securitySchemes` blobs.
//
// Framework-free (no React, no React Query) so unit tests can exercise the
// parsing rules as plain functions. Shared by the API picker (for at-a-glance
// auth-type badges) and the credential create sheet (for auto-shaping the form
// from a picked API's spec).
//
// Conventions (kept stable with jentic-mini):
//   - `bearer` / `basic` come from `type: 'http'` with the matching `scheme`.
//   - `apiKey` is OpenAPI 3 `type: 'apiKey'`.
//   - `oauth2` is `type: 'oauth2'`.
//   - Anything else — including malformed entries — collapses into `unknown`,
//     which signals the UI to fall back to the manual type toggle.
import { CredentialType } from '@/modules/credentials/api';

export type SchemeType = 'bearer' | 'basic' | 'apiKey' | 'oauth2' | 'unknown';

/** Raw `components.securitySchemes` shape, as it lives in an OpenAPI doc. */
export type RawSchemes =
	| Record<
			string,
			{
				type?: string;
				scheme?: string;
				in?: 'header' | 'query' | 'cookie' | string;
				name?: string;
			}
	  >
	| null
	| undefined;

export interface SchemeOption {
	/** The raw key from the spec's `securitySchemes` map (e.g. `bearerAuth`). */
	name: string;
	type: SchemeType;
	/** Human label rendered on the auth-method pill. */
	label: string;
}

const SCHEME_TYPE_PRIORITY: SchemeType[] = ['bearer', 'apiKey', 'basic', 'oauth2', 'unknown'];

const SCHEME_TYPE_LABELS: Record<SchemeType, string> = {
	bearer: 'Bearer Token',
	apiKey: 'API Key',
	basic: 'Basic Auth',
	oauth2: 'OAuth 2.0',
	unknown: 'Credential',
};

export function schemeTypeFromRaw(s: { type?: string; scheme?: string }): SchemeType {
	if (s.type === 'oauth2') return 'oauth2';
	if (s.type === 'http' && s.scheme?.toLowerCase() === 'bearer') return 'bearer';
	if (s.type === 'http' && s.scheme?.toLowerCase() === 'basic') return 'basic';
	if (s.type === 'apiKey') return 'apiKey';
	return 'unknown';
}

/**
 * Returns one option per distinct scheme TYPE (deduped), ordered by canonical
 * priority. We dedupe by type — not by name — because a spec that defines
 * `bearerAuth` AND `JWTAuth` both as bearer schemes shouldn't present the user
 * with two visually identical pills. The first match per type is kept and its
 * raw name is exposed so the caller can read its `in`/`name` fields.
 */
export function parseSchemeOptions(schemes: RawSchemes): SchemeOption[] {
	if (!schemes || Object.keys(schemes).length === 0) return [];
	const options: SchemeOption[] = Object.entries(schemes).map(([name, s]) => {
		const type = schemeTypeFromRaw(s);
		return { name, type, label: SCHEME_TYPE_LABELS[type] };
	});
	const seen = new Set<SchemeType>();
	return options
		.sort((a, b) => SCHEME_TYPE_PRIORITY.indexOf(a.type) - SCHEME_TYPE_PRIORITY.indexOf(b.type))
		.filter((o) => {
			if (seen.has(o.type)) return false;
			seen.add(o.type);
			return true;
		});
}

/**
 * Adapter: scheme type to jentic-one's strict `CredentialType` discriminator.
 * `unknown` maps to `null` — callers should fall back to the manual type
 * toggle when the spec doesn't expose a recognisable scheme.
 */
export function schemeTypeToCredentialType(type: SchemeType): CredentialType | null {
	switch (type) {
		case 'bearer':
			return CredentialType.BEARER_TOKEN;
		case 'apiKey':
			return CredentialType.API_KEY;
		case 'basic':
			return CredentialType.BASIC;
		case 'oauth2':
			return CredentialType.OAUTH2;
		case 'unknown':
		default:
			return null;
	}
}

/**
 * Pull the apiKey-specific projection (`field_name`, `location`) off the raw
 * scheme map by raw scheme name. Both fields are best-effort: if the spec
 * doesn't declare them we return empty/'header' defaults, and the caller can
 * still let the user override.
 */
export function apiKeyFieldsFromScheme(
	schemes: RawSchemes,
	schemeName: string | null | undefined,
): { fieldName: string; location: 'header' | 'query' } {
	const raw = schemeName && schemes ? schemes[schemeName] : undefined;
	const location: 'header' | 'query' = raw?.in === 'query' ? 'query' : 'header';
	return { fieldName: raw?.name ?? '', location };
}

/** OAuth2 flow URLs extracted from a spec's `securitySchemes.*.flows.*`. */
export interface OAuth2FlowDef {
	/**
	 * Stable, unique identifier for this `(scheme, flow)` pair. Use this as the
	 * `<Select>` value when offering the user a choice — `grantType` alone is
	 * not unique when multiple schemes declare the same flow type.
	 */
	id: string;
	/** Raw scheme name this flow was declared in (e.g. `oauth2Primary`). */
	schemeName: string;
	/** OpenAPI flow key (e.g. `authorizationCode`, `clientCredentials`). */
	flowType: string;
	/** Human label for the grant type selector. */
	label: string;
	/** The `grant_type` value to send on the wire. */
	grantType: string;
	tokenUrl?: string;
	authorizationUrl?: string;
}

const FLOW_TYPE_LABELS: Record<string, string> = {
	authorizationCode: 'Authorization Code',
	clientCredentials: 'Client Credentials',
	implicit: 'Implicit',
	password: 'Resource Owner Password',
};

const FLOW_TYPE_TO_GRANT_TYPE: Record<string, string> = {
	authorizationCode: 'authorization_code',
	clientCredentials: 'client_credentials',
	implicit: 'implicit',
	password: 'password',
};

// Canonical ordering for the grant-type picker. Without this, flows surface in
// raw spec order (whatever `Object.entries` yields), which varies per OpenAPI
// doc and is non-deterministic. We rank by how commonly the grant is the right
// default for a server-side OAuth2 client: authorization_code first (the
// browser-redirect flow the connect UI is built around), then the
// non-redirect grants. Unknown flow types sort last, alphabetically.
const FLOW_TYPE_ORDER: string[] = [
	'authorizationCode',
	'clientCredentials',
	'password',
	'implicit',
];

function flowTypeRank(flowType: string): number {
	const idx = FLOW_TYPE_ORDER.indexOf(flowType);
	return idx === -1 ? FLOW_TYPE_ORDER.length : idx;
}

/**
 * Extract available OAuth2 flows from the spec's security schemes. Returns one
 * entry per distinct `(scheme, flow)` pair — flows are NOT deduped across
 * schemes, because a spec that declares e.g. both `oauth2Primary` and
 * `oauth2Service` with their own `authorizationCode` flows must let the user
 * pick which one to target (their token/authorize URLs differ).
 *
 * Pass `schemeName` to restrict extraction to a single scheme. When omitted,
 * every flow across every scheme is returned. Each flow's `id` is
 * `${schemeName}.${flowType}` and is the stable handle the UI should use as
 * the `<Select>` value. The `label` is disambiguated with the scheme name only
 * when the same flow type appears under more than one scheme.
 *
 * Flows are returned in a deterministic, canonical order (see
 * {@link FLOW_TYPE_ORDER}) — by grant-type rank, then scheme name, then flow
 * type — so the grant-type picker is stable across specs and `flows[0]` is the
 * sensible default (authorization_code when present).
 */
export function oauth2FlowsFromSchemes(
	schemes: RawSchemes,
	schemeName?: string | null,
): OAuth2FlowDef[] {
	if (!schemes) return [];
	const entries: Array<[string, unknown]> =
		schemeName && schemes[schemeName]
			? [[schemeName, schemes[schemeName]]]
			: Object.entries(schemes);
	const collected: OAuth2FlowDef[] = [];
	for (const [name, scheme] of entries) {
		const flows = (
			scheme as {
				type?: string;
				flows?: Record<string, { tokenUrl?: string; authorizationUrl?: string }>;
			}
		).flows;
		if (!flows) continue;
		for (const [flowType, flow] of Object.entries(flows)) {
			const baseLabel = FLOW_TYPE_LABELS[flowType] ?? flowType;
			collected.push({
				id: `${name}.${flowType}`,
				schemeName: name,
				flowType,
				label: baseLabel,
				grantType: FLOW_TYPE_TO_GRANT_TYPE[flowType] ?? flowType,
				tokenUrl: flow?.tokenUrl,
				authorizationUrl: flow?.authorizationUrl,
			});
		}
	}
	// Canonical, deterministic order: grant-type rank, then scheme name, then
	// flow type. Sort before label disambiguation (the counts below are
	// order-independent, so this is safe and keeps the dropdown stable).
	collected.sort((a, b) => {
		const byRank = flowTypeRank(a.flowType) - flowTypeRank(b.flowType);
		if (byRank !== 0) return byRank;
		const byScheme = a.schemeName.localeCompare(b.schemeName);
		if (byScheme !== 0) return byScheme;
		return a.flowType.localeCompare(b.flowType);
	});
	// Disambiguate labels only where needed — if the same flow type appears in
	// more than one scheme, suffix the scheme name so the dropdown is readable.
	const flowTypeCounts = collected.reduce<Record<string, number>>((acc, f) => {
		acc[f.flowType] = (acc[f.flowType] ?? 0) + 1;
		return acc;
	}, {});
	return collected.map((f) =>
		flowTypeCounts[f.flowType] > 1 ? { ...f, label: `${f.label} — ${f.schemeName}` } : f,
	);
}

/** A single OAuth2 scope as declared by a spec's `flows.*.scopes` map. */
export interface ScopeDef {
	name: string;
	description?: string;
}

/**
 * Collect the distinct OAuth2 scopes declared across every flow of every
 * oauth2 scheme in the spec. OpenAPI nests scopes under
 * `securitySchemes.<x>.flows.<flowType>.scopes` as a `{ scope: description }`
 * map; we flatten and dedupe by scope name (first description wins).
 *
 * Returns `[]` for specs with no oauth2 scopes — callers fall back to the
 * free-text scope input.
 */
export function oauth2ScopesFromSchemes(schemes: RawSchemes): ScopeDef[] {
	if (!schemes) return [];
	const seen = new Map<string, string | undefined>();
	for (const scheme of Object.values(schemes)) {
		const flows = (scheme as { flows?: Record<string, { scopes?: Record<string, string> }> })
			.flows;
		if (!flows) continue;
		for (const flow of Object.values(flows)) {
			const scopes = flow?.scopes;
			if (!scopes) continue;
			for (const [name, description] of Object.entries(scopes)) {
				if (!seen.has(name)) seen.set(name, description || undefined);
			}
		}
	}
	return Array.from(seen, ([name, description]) => ({ name, description }));
}
