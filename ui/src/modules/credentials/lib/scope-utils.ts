// OAuth2-specific scope helpers for the credential flow.
//
// The source-agnostic grouping/filtering primitives now live in
// `@/shared/lib/scopes` (promoted so the actors surface can reuse the same
// picker — see #615). This module keeps only the OAuth2-flavoured pieces:
//   - `enhancedScopesFromSchemes` — pull `{ name, description }` out of a spec's
//     oauth2 flows and enrich into `EnhancedScope[]`.
//   - the read/write recommendation heuristic used to pre-select safe scopes.
// It re-exports the shared primitives so existing credential call-sites keep
// importing scope utilities from one place.
//
// Framework-free (no React) so the rules are unit-testable as plain functions.
import { oauth2ScopesFromSchemes, type RawSchemes } from '@/modules/credentials/lib/schemes';
import { type EnhancedScope, type ScopeOrigin } from '@/shared/lib';

export {
	type ScopeOrigin,
	type EnhancedScope,
	type ScopeGroup,
	extractResourceFromScope,
	formatResourceName,
	groupScopesByResource,
	scopesInGroup,
	filterScopeGroups,
} from '@/shared/lib';

// =============================================================================
// EXTRACTION
// =============================================================================

/**
 * Turn a spec's oauth2 scopes into `EnhancedScope[]`, deduped by name and
 * sorted alphabetically. Marks each scope's recommended-by-default status.
 */
export function enhancedScopesFromSchemes(schemes: RawSchemes): EnhancedScope[] {
	return oauth2ScopesFromSchemes(schemes)
		.map((s) => ({
			scope: s.name,
			description: s.description ?? '',
			origin: 'schema' as ScopeOrigin,
			isRecommended: isRecommendedScope(s.name),
		}))
		.sort((a, b) => a.scope.localeCompare(b.scope));
}

// =============================================================================
// RECOMMENDED SCOPES
// =============================================================================

// Read-only patterns (safe to recommend). Checked AFTER write patterns so
// "admin:read" is NOT recommended. Word boundaries/delimiters avoid false hits
// like "spreadsheets" matching "read".
const READ_PATTERNS = [
	/\bread\b/i,
	/[.:_-]read$/i,
	/^read[.:_-]/i,
	/\.readonly$/i,
	/\bview\b/i,
	/[.:_-]view$/i,
	/\blist\b/i,
	/[.:_-]list$/i,
	/\bget\b/i,
	/[.:_-]get$/i,
	/:r$/i,
];

// Write/dangerous patterns (never recommend). Take precedence over read.
const WRITE_PATTERNS = [
	/\bwrite\b/i,
	/[.:_-]write$/i,
	/^write[.:_-]/i,
	/\bcreate\b/i,
	/[.:_-]create$/i,
	/^create[.:_-]/i,
	/\bupdate\b/i,
	/[.:_-]update$/i,
	/^update[.:_-]/i,
	/\bdelete\b/i,
	/[.:_-]delete$/i,
	/^delete[.:_-]/i,
	/\bmodify\b/i,
	/[.:_-]modify$/i,
	/\bmanage\b/i,
	/[.:_-]manage$/i,
	/\badmin\b/i,
	/[.:_-]admin$/i,
	/^admin[.:_-]/i,
	/\bmaster\b/i,
	/[.:_-]master$/i,
	/^master[.:_-]/i,
	/:w$/i,
	/\.write$/i,
	/:rw$/i,
	/readwrite/i,
];

const COMMON_SAFE_SCOPES = [
	'openid',
	'profile',
	'email',
	'offline_access',
	'user:email',
	'user:read',
	'public_repo',
];

/**
 * Whether a scope is safe to pre-select by default. Write/admin scopes are
 * never recommended (even if they also match a read pattern); read-only and a
 * handful of common safe scopes are.
 */
export function isRecommendedScope(scope: string): boolean {
	if (WRITE_PATTERNS.some((p) => p.test(scope))) return false;
	if (READ_PATTERNS.some((p) => p.test(scope))) return true;
	return COMMON_SAFE_SCOPES.some((safe) => scope.toLowerCase().includes(safe.toLowerCase()));
}

/** The recommended subset of a scope list. */
export function getRecommendedScopes(scopes: EnhancedScope[]): EnhancedScope[] {
	return scopes.filter((s) => s.isRecommended);
}
