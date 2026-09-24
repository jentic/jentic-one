// Source-agnostic scope primitives, shared across modules.
//
// Two unrelated surfaces need the same "group a flat scope list by resource,
// search/filter it, render it in a tri-state select-all picker" behaviour:
//   - credentials: OAuth2 *provider* scopes pulled from a securityScheme
//     (`read:user`, `https://www.googleapis.com/auth/calendar`, …)
//   - agents: *platform permission* scopes from
//     `GET /permissions` (`org:admin`, `agents:write`, …)
//
// The grouping/filtering rules here are purely string-shaped and carry no
// domain knowledge, so they live in `shared/` and the picker (`shared/ui`)
// consumes them. Each module supplies its own adapter that turns its wire
// payload into `EnhancedScope[]` (the credentials OAuth2 recommend heuristics
// stay in the credentials module; platform scopes have no such heuristic).
//
// Framework-free (no React) so the rules stay unit-testable as plain functions.

/** Where a scope was declared: an API securityScheme, or the platform catalogue. */
export type ScopeOrigin = 'schema' | 'platform';

/** A scope enriched with display metadata + a default-selection hint. */
export interface EnhancedScope {
	scope: string;
	description: string;
	origin: ScopeOrigin;
	isRecommended: boolean;
}

/** A resource-grouped bundle of scopes (e.g. all `read:jira` / `write:jira`). */
export interface ScopeGroup {
	id: string;
	name: string;
	scopes: EnhancedScope[];
	totalCount: number;
}

// =============================================================================
// GROUPING
// =============================================================================

function isUrl(str: string): boolean {
	return str.startsWith('http://') || str.startsWith('https://') || str.includes('://');
}

/**
 * Extract the resource prefix from a scope string, handling URL-style scopes
 * (`https://www.googleapis.com/auth/calendar` → `calendar`) and the common
 * delimiter conventions (`user:read` → `user`, `repo.admin` → `repo`).
 */
export function extractResourceFromScope(scope: string): string {
	if (isUrl(scope)) {
		try {
			const url = new URL(scope);
			const pathParts = url.pathname.split('/').filter(Boolean);
			if (pathParts.length >= 2) return pathParts[pathParts.length - 1].toLowerCase();
			if (pathParts.length === 1) return pathParts[0].toLowerCase();
			return url.hostname.split('.')[0].toLowerCase();
		} catch {
			// fall through to delimiter handling
		}
	}

	const delimiters = [':', '.', '/', '_'];
	for (const delimiter of delimiters) {
		if (scope.includes(delimiter)) {
			return scope.split(delimiter)[0].toLowerCase();
		}
	}
	return scope.toLowerCase();
}

/** `read-only` / `user_email` → `Read Only` / `User Email`. */
export function formatResourceName(resource: string): string {
	if (resource === 'other') return 'Other';
	return resource
		.split(/[-_]/)
		.map((word) => word.charAt(0).toUpperCase() + word.slice(1))
		.join(' ');
}

/**
 * Group scopes by their resource prefix. Groups (and the scopes within them)
 * are alphabetically sorted for a stable order that doesn't reshuffle as the
 * user selects.
 */
export function groupScopesByResource(scopes: EnhancedScope[]): ScopeGroup[] {
	const groups = new Map<string, EnhancedScope[]>();
	for (const scope of scopes) {
		const resource = extractResourceFromScope(scope.scope);
		groups.set(resource, [...(groups.get(resource) ?? []), scope]);
	}
	return Array.from(groups.entries())
		.map(([resource, resourceScopes]) => ({
			id: resource,
			name: formatResourceName(resource),
			scopes: [...resourceScopes].sort((a, b) => a.scope.localeCompare(b.scope)),
			totalCount: resourceScopes.length,
		}))
		.sort((a, b) => a.name.localeCompare(b.name));
}

/** Scope names belonging to a resource group id. */
export function scopesInGroup(scopes: EnhancedScope[], groupId: string): string[] {
	return scopes.filter((s) => extractResourceFromScope(s.scope) === groupId).map((s) => s.scope);
}

// =============================================================================
// FILTERING
// =============================================================================

/** Filter groups (and their scopes) by a free-text query; drops empty groups. */
export function filterScopeGroups(groups: ScopeGroup[], query: string): ScopeGroup[] {
	const q = query.trim().toLowerCase();
	if (!q) return groups;
	return groups
		.map((group) => ({
			...group,
			scopes: group.scopes.filter(
				(s) => s.scope.toLowerCase().includes(q) || s.description.toLowerCase().includes(q),
			),
		}))
		.filter((group) => group.scopes.length > 0);
}
