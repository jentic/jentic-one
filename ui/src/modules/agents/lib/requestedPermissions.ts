/**
 * How much a requested permission can do, for the moment an operator approves an
 * agent. A self-registering agent names its own permissions, and approval makes
 * them live (bounded only by the approver's own ceiling), so the ones that can
 * change data, run upstream calls or administer the organisation are flagged
 * before the click.
 */
export type PermissionRisk = 'admin' | 'write' | 'execute';

/**
 * Permissions known to act, by name: they change data or run calls without
 * saying `write` (`overlays:confirm` mutates a spec, `capabilities:execute` runs
 * upstream calls, `credentials:connect` stores a credential).
 */
const RISKY_PERMISSIONS: Readonly<Record<string, PermissionRisk>> = {
	'overlays:confirm': 'write',
	'capabilities:execute': 'execute',
	'credentials:connect': 'write',
};

/**
 * `admin` for any permission mentioning `admin` (`org:admin`,
 * `administrators:read` — erring towards a flag), then the explicit
 * {@link RISKY_PERMISSIONS}, then `write` for a `…:write` permission; otherwise
 * `null`.
 */
export function permissionRisk(permission: string): PermissionRisk | null {
	const normalised = permission.trim().toLowerCase();
	if (normalised.includes('admin')) return 'admin';
	const known = Object.prototype.hasOwnProperty.call(RISKY_PERMISSIONS, normalised)
		? RISKY_PERMISSIONS[normalised]
		: undefined;
	if (known) return known;
	const segments = normalised.split(':');
	if (segments[segments.length - 1] === 'write') return 'write';
	return null;
}

/**
 * The permissions approval grants an agent that holds none — a mirror of
 * `DEFAULT_AGENT_PERMISSIONS` in
 * `src/jentic_one/shared/auth/permission_catalog.py`, which is the source of
 * truth, in the same order. No endpoint serves the list, so a unit test pins it
 * and a change there shows up here in review.
 */
export const DEFAULT_AGENT_PERMISSIONS: readonly string[] = [
	'capabilities:execute',
	'capabilities:read',
	'apis:read',
	'catalog:import',
	'executions:read',
	'jobs:read',
	'events:read',
	'owner:resources:read',
	'owner:agents:read',
	'owner:credentials:read',
	'credentials:connect',
];

/**
 * The permission catalogue (`GET /permissions`) leaves `org:admin` out for a
 * caller who doesn't hold it, but it is a real permission all the same.
 */
const ALWAYS_KNOWN = new Set(['org:admin']);

/** What approving an agent grants, given the permissions it holds now. */
export type ApprovalGrant =
	/** It holds none, so approval grants {@link DEFAULT_AGENT_PERMISSIONS}. */
	| { kind: 'defaults'; granted: readonly string[]; unrecognised: [] }
	/** It requested some: approval makes the recognised ones live and adds no
	 * defaults — even when none of them is recognised, so `granted` can be empty. */
	| { kind: 'requested'; granted: string[]; unrecognised: string[] };

/**
 * Splits an agent's requested permissions the way `AgentService.approve` treats
 * them: an empty request gets the default agent permissions; otherwise
 * permissions outside the permission catalogue grant nothing, and the defaults
 * are not added in their place.
 */
export function approvalGrant(
	requested: readonly string[],
	catalogue: Iterable<string>,
): ApprovalGrant {
	if (requested.length === 0)
		return { kind: 'defaults', granted: DEFAULT_AGENT_PERMISSIONS, unrecognised: [] };
	const known = new Set([...catalogue, ...ALWAYS_KNOWN]);
	return {
		kind: 'requested',
		granted: requested.filter((permission) => known.has(permission)),
		unrecognised: requested.filter((permission) => !known.has(permission)),
	};
}

/** The areas a permission list is grouped into for review, in reading order. */
export const PERMISSION_AREAS = [
	'Capabilities',
	'APIs & catalog',
	'Executions, jobs & events',
	'Credentials',
	"Its owner's resources",
	'Organisation',
	'Other',
] as const;

export type PermissionArea = (typeof PERMISSION_AREAS)[number];

const AREA_BY_RESOURCE: Record<string, PermissionArea> = {
	capabilities: 'Capabilities',
	apis: 'APIs & catalog',
	catalog: 'APIs & catalog',
	executions: 'Executions, jobs & events',
	jobs: 'Executions, jobs & events',
	events: 'Executions, jobs & events',
	audit: 'Executions, jobs & events',
	credentials: 'Credentials',
	owner: "Its owner's resources",
	agents: 'Organisation',
	users: 'Organisation',
	org: 'Organisation',
};

/** The area a permission belongs to, by its first segment (`owner:agents:read` → owner). */
export function permissionArea(permission: string): PermissionArea {
	const resource = permission.trim().toLowerCase().split(':')[0] ?? '';
	return AREA_BY_RESOURCE[resource] ?? 'Other';
}

/** `permissions` grouped by area, areas in {@link PERMISSION_AREAS} order,
 * permissions in their given order; empty areas are left out. */
export function groupPermissionsByArea(
	permissions: readonly string[],
): Array<{ area: PermissionArea; permissions: string[] }> {
	return PERMISSION_AREAS.map((area) => ({
		area,
		permissions: permissions.filter((permission) => permissionArea(permission) === area),
	})).filter((group) => group.permissions.length > 0);
}
