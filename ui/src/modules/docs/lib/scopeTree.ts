/**
 * Scope-tree transforms — the data behind the conceptual "Scopes" view.
 *
 * Joins the conceptual scope catalogue (meaning + implication graph, from the
 * reference payload's `scopes` section) with the endpoint list, so each scope
 * can show both *what it means* and *how many endpoints it gates*. Pure
 * functions over the payload; no React, no fetching — easy to unit-test.
 */
import type {
	ReferenceEndpoint,
	ReferencePayload,
	ScopeEntry,
	ScopeFamily,
} from '@/modules/docs/api/types';

/** Visual tier of a scope, derived from its action — drives colour + ordering. */
export type ScopeTier = 'admin' | 'write' | 'execute' | 'read';

export function tierOf(scope: ScopeEntry): ScopeTier {
	if (scope.is_superuser || scope.action === 'admin') return 'admin';
	if (scope.action === 'write') return 'write';
	if (scope.action === 'execute') return 'execute';
	return 'read';
}

/** A scope augmented with how many endpoints in this instance require it. */
export interface ScopeNode extends ScopeEntry {
	tier: ScopeTier;
	/** Endpoints that list this scope in `required_scopes`. */
	endpointCount: number;
}

/** A family with its scopes augmented for display. */
export interface ScopeFamilyView {
	name: string;
	label: string;
	blurb: string;
	scopes: ScopeNode[];
	/** Total endpoints gated by any scope in the family (deduplicated). */
	endpointCount: number;
}

/** Count endpoints that require each scope (a scope may gate several). */
export function endpointCountByScope(endpoints: ReferenceEndpoint[]): Map<string, number> {
	const counts = new Map<string, number>();
	for (const endpoint of endpoints) {
		for (const scope of endpoint.required_scopes ?? []) {
			counts.set(scope, (counts.get(scope) ?? 0) + 1);
		}
	}
	return counts;
}

/**
 * Build the family → scope view for the conceptual tree. Families keep the
 * backend order (admin first, owner-reads last); within a family scopes keep
 * the backend order too (admin/superuser first). Returns `null` when the
 * payload predates the scope catalogue (older server) so the UI can degrade.
 */
export function buildScopeFamilies(payload: ReferencePayload): ScopeFamilyView[] | null {
	if (!payload.scopes) return null;
	const counts = endpointCountByScope(payload.endpoints);

	return payload.scopes.families.map((family: ScopeFamily) => {
		const scopes: ScopeNode[] = family.scopes.map((scope) => ({
			...scope,
			tier: tierOf(scope),
			endpointCount: counts.get(scope.name) ?? 0,
		}));
		const familyEndpoints = new Set<string>();
		for (const endpoint of payload.endpoints) {
			if ((endpoint.required_scopes ?? []).some((s) => s.split(':', 1)[0] === family.name)) {
				familyEndpoints.add(`${endpoint.method} ${endpoint.path}`);
			}
		}
		return {
			name: family.name,
			label: family.label,
			blurb: family.blurb,
			scopes,
			endpointCount: familyEndpoints.size,
		};
	});
}

/** Index the catalogue by scope name (for "implies" lookups / descriptions). */
export function indexScopes(payload: ReferencePayload): Map<string, ScopeEntry> {
	const map = new Map<string, ScopeEntry>();
	for (const scope of payload.scopes?.scopes ?? []) {
		map.set(scope.name, scope);
	}
	return map;
}

/** Endpoints requiring a given scope, sorted by (path, method). */
export function endpointsForScope(payload: ReferencePayload, scope: string): ReferenceEndpoint[] {
	return payload.endpoints
		.filter((e) => (e.required_scopes ?? []).includes(scope))
		.sort((a, b) =>
			a.path === b.path ? a.method.localeCompare(b.method) : a.path.localeCompare(b.path),
		);
}

/** Coarse split of how each endpoint is authorized, for the "two models" view. */
export interface AuthModelCounts {
	/** Authenticated AND gated by at least one platform scope. */
	scopeGated: number;
	/** Authenticated but no scope — authorized by ownership / binding checks. */
	ownershipGated: number;
	/** Unauthenticated (health, login, registration, …). */
	public: number;
	total: number;
}

export function authModelCounts(endpoints: ReferenceEndpoint[]): AuthModelCounts {
	let scopeGated = 0;
	let ownershipGated = 0;
	let pub = 0;
	for (const e of endpoints) {
		if (!e.authenticated) pub += 1;
		else if ((e.required_scopes ?? []).length > 0) scopeGated += 1;
		else ownershipGated += 1;
	}
	return { scopeGated, ownershipGated, public: pub, total: endpoints.length };
}

/**
 * Authenticated endpoints with NO required scope — authorized by ownership /
 * binding checks (e.g. `created_by == me`) rather than a platform scope. Sorted
 * by (path, method). These are *not* a gap: "no scope" is the correct answer.
 */
export function ownershipEndpoints(payload: ReferencePayload): ReferenceEndpoint[] {
	return payload.endpoints
		.filter((e) => e.authenticated && (e.required_scopes ?? []).length === 0)
		.sort((a, b) =>
			a.path === b.path ? a.method.localeCompare(b.method) : a.path.localeCompare(b.path),
		);
}
