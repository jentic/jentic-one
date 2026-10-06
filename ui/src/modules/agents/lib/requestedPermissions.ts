/**
 * How much a requested permission can do, for the moment an operator approves an
 * agent. A self-registering agent names its own permissions, and approval makes
 * them live (bounded only by the approver's own ceiling), so the ones that can
 * change data or administer the organisation are flagged before the click.
 */
export type PermissionRisk = 'admin' | 'write';

/** `admin` for any permission with an `admin` segment (`org:admin`), `write` for
 * a `…:write` permission, otherwise `null`. */
export function permissionRisk(permission: string): PermissionRisk | null {
	const segments = permission.trim().toLowerCase().split(':');
	if (segments.includes('admin')) return 'admin';
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
