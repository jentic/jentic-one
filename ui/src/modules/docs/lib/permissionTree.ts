/**
 * Permission-tree transforms — the data behind the conceptual "Permissions" view.
 *
 * Joins the conceptual permission catalogue (meaning + implication graph, from
 * the reference payload's `permissions` section) with the endpoint list, so each
 * permission can show both *what it means* and *how many endpoints it gates*.
 * Pure functions over the payload; no React, no fetching — easy to unit-test.
 */
import type {
	PermissionEntry,
	PermissionFamily,
	ReferenceEndpoint,
	ReferencePayload,
} from '@/modules/docs/api/types';

/**
 * Visual tier of a permission, derived from its action — drives colour +
 * ordering.
 */
export type PermissionTier = 'admin' | 'write' | 'execute' | 'read';

export function tierOf(permission: PermissionEntry): PermissionTier {
	if (permission.is_superuser || permission.action === 'admin') return 'admin';
	if (permission.action === 'write') return 'write';
	if (permission.action === 'execute') return 'execute';
	return 'read';
}

/** A permission augmented with how many endpoints in this instance require it. */
export interface PermissionNode extends PermissionEntry {
	tier: PermissionTier;
	/** Endpoints that list this permission in `required_permissions`. */
	endpointCount: number;
}

/** A family with its permissions augmented for display. */
export interface PermissionFamilyView {
	name: string;
	label: string;
	blurb: string;
	permissions: PermissionNode[];
	/** Total endpoints gated by any permission in the family (deduplicated). */
	endpointCount: number;
}

/**
 * Count endpoints that require each permission (one may gate several).
 */
export function endpointCountByPermission(endpoints: ReferenceEndpoint[]): Map<string, number> {
	const counts = new Map<string, number>();
	for (const endpoint of endpoints) {
		for (const permission of endpoint.required_permissions ?? []) {
			counts.set(permission, (counts.get(permission) ?? 0) + 1);
		}
	}
	return counts;
}

/**
 * Build the family → permission view for the conceptual tree. Families keep the
 * backend order (admin first, owner-reads last); within a family permissions
 * keep the backend order too (admin/superuser first). Returns `null` when the
 * payload predates the permission catalogue (older server) so the UI can degrade.
 */
export function buildPermissionFamilies(payload: ReferencePayload): PermissionFamilyView[] | null {
	if (!payload.permissions) return null;
	const counts = endpointCountByPermission(payload.endpoints);

	return payload.permissions.families.map((family: PermissionFamily) => {
		const permissions: PermissionNode[] = family.permissions.map((permission) => ({
			...permission,
			tier: tierOf(permission),
			endpointCount: counts.get(permission.name) ?? 0,
		}));
		const familyEndpoints = new Set<string>();
		for (const endpoint of payload.endpoints) {
			if (
				(endpoint.required_permissions ?? []).some(
					(p) => p.split(':', 1)[0] === family.name,
				)
			) {
				familyEndpoints.add(`${endpoint.method} ${endpoint.path}`);
			}
		}
		return {
			name: family.name,
			label: family.label,
			blurb: family.blurb,
			permissions,
			endpointCount: familyEndpoints.size,
		};
	});
}

/**
 * Index the catalogue by permission name (for "implies" lookups / descriptions).
 */
export function indexPermissions(payload: ReferencePayload): Map<string, PermissionEntry> {
	const map = new Map<string, PermissionEntry>();
	for (const permission of payload.permissions?.permissions ?? []) {
		map.set(permission.name, permission);
	}
	return map;
}

/** Endpoints requiring a given permission, sorted by (path, method). */
export function endpointsForPermission(
	payload: ReferencePayload,
	permission: string,
): ReferenceEndpoint[] {
	return payload.endpoints
		.filter((e) => (e.required_permissions ?? []).includes(permission))
		.sort((a, b) =>
			a.path === b.path ? a.method.localeCompare(b.method) : a.path.localeCompare(b.path),
		);
}

/** Coarse split of how each endpoint is authorized, for the "two models" view. */
export interface AuthModelCounts {
	/** Authenticated AND gated by at least one platform permission. */
	permissionGated: number;
	/**
	 * Authenticated but no permission — authorized by ownership / binding checks.
	 */
	ownershipGated: number;
	/** Unauthenticated (health, login, registration, …). */
	public: number;
	total: number;
}

export function authModelCounts(endpoints: ReferenceEndpoint[]): AuthModelCounts {
	let permissionGated = 0;
	let ownershipGated = 0;
	let pub = 0;
	for (const e of endpoints) {
		if (!e.authenticated) pub += 1;
		else if ((e.required_permissions ?? []).length > 0) permissionGated += 1;
		else ownershipGated += 1;
	}
	return { permissionGated, ownershipGated, public: pub, total: endpoints.length };
}

/**
 * Authenticated endpoints with NO required permission — authorized by ownership
 * / binding checks (e.g. `created_by == me`) rather than a platform permission.
 * Sorted by (path, method). These are *not* a gap: "no permission" is the
 * correct answer.
 */
export function ownershipEndpoints(payload: ReferencePayload): ReferenceEndpoint[] {
	return payload.endpoints
		.filter((e) => e.authenticated && (e.required_permissions ?? []).length === 0)
		.sort((a, b) =>
			a.path === b.path ? a.method.localeCompare(b.method) : a.path.localeCompare(b.path),
		);
}
