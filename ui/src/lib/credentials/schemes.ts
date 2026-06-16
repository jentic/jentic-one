/**
 * Pure helpers for working with OpenAPI `securitySchemes` blobs.
 *
 * Lives outside the form file so the same parsing rules can be shared
 * by the legacy full-page form (`CredentialFormPage`), the upcoming
 * sheet-based edit surface, and the toolkit-anchored add dialog. The
 * file is intentionally framework-free — no React, no React Query —
 * so unit tests can exercise it as plain functions.
 *
 * Conventions (kept stable across UI surfaces):
 *  - `bearer` and `basic` come from `type: 'http'` with the matching
 *    `scheme` field.
 *  - `apiKey` is the OpenAPI 3 `type: 'apiKey'` shape.
 *  - `oauth2` is `type: 'oauth2'`.
 *  - Anything else — including malformed entries — collapses into
 *    `unknown`, which renders as a single freeform "Credential Value"
 *    field.
 *  - The compound apiKey pattern (`Secret` + `Identity`) is a Jentic
 *    overlay convention for APIs that need both an account ID and a
 *    secret. Detected purely by key presence.
 */

export type SchemeType = 'bearer' | 'basic' | 'apiKey' | 'oauth2' | 'unknown';

export type RawSchemes =
	| Record<string, { type?: string; scheme?: string; in?: string; name?: string }>
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

/** True when the scheme map uses the canonical compound pattern (`Secret` + `Identity`). */
export function isCompoundApiKey(schemes: RawSchemes): boolean {
	if (!schemes) return false;
	return 'Secret' in schemes && 'Identity' in schemes;
}

/**
 * Derive human-friendly field labels for the compound pattern from the
 * underlying header names declared in the overlay. Falls back to
 * generic labels when the overlay omits `name`.
 */
export function compoundLabels(schemes: RawSchemes): {
	secretLabel: string;
	identityLabel: string;
} {
	const secret = schemes?.Secret;
	const identity = schemes?.Identity;
	return {
		secretLabel: secret?.name ?? 'API Key',
		identityLabel: identity?.name ?? 'Username',
	};
}

export function schemeTypeFromRaw(s: { type?: string; scheme?: string }): SchemeType {
	if (s.type === 'oauth2') return 'oauth2';
	if (s.type === 'http' && s.scheme?.toLowerCase() === 'bearer') return 'bearer';
	if (s.type === 'http' && s.scheme?.toLowerCase() === 'basic') return 'basic';
	if (s.type === 'apiKey') return 'apiKey';
	return 'unknown';
}

/**
 * Returns one option per distinct scheme TYPE (deduped), ordered by
 * the canonical priority. We dedupe by type — not by name — because a
 * spec that defines `bearerAuth` AND `JWTAuth` both as bearer schemes
 * shouldn't present the user with two visually identical pills. The
 * UI picks the first match per type and uses that scheme's name when
 * the user submits.
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

export function inferSchemeTypeFromSchemes(schemes: RawSchemes): SchemeType {
	const options = parseSchemeOptions(schemes);
	return options[0]?.type ?? 'unknown';
}

export function firstSchemeNameFromSchemes(schemes: RawSchemes): string | null {
	if (!schemes) return null;
	return Object.keys(schemes)[0] ?? null;
}
