/**
 * How much a requested scope can do, for the moment an operator approves an
 * agent. A self-registering agent names its own scopes, and approval makes them
 * live (bounded only by the approver's own ceiling), so the ones that can
 * change data, run upstream calls or administer the organisation are flagged
 * before the click.
 */
export type ScopeRisk = 'admin' | 'write' | 'execute';

/**
 * Scopes known to act, by name: they change data or run calls without saying
 * `write` (`overlays:confirm` mutates a spec, `capabilities:execute` runs
 * upstream calls, `credentials:connect` stores a credential).
 */
const RISKY_SCOPES: Readonly<Record<string, ScopeRisk>> = {
	'overlays:confirm': 'write',
	'capabilities:execute': 'execute',
	'credentials:connect': 'write',
};

/**
 * `admin` for any scope mentioning `admin` (`org:admin`, `administrators:read`
 * — erring towards a flag), then the explicit {@link RISKY_SCOPES}, then
 * `write` for a `…:write` scope; otherwise `null`.
 */
export function scopeRisk(scope: string): ScopeRisk | null {
	const normalised = scope.trim().toLowerCase();
	if (normalised.includes('admin')) return 'admin';
	const known = Object.prototype.hasOwnProperty.call(RISKY_SCOPES, normalised)
		? RISKY_SCOPES[normalised]
		: undefined;
	if (known) return known;
	const segments = normalised.split(':');
	if (segments[segments.length - 1] === 'write') return 'write';
	return null;
}

/**
 * The scopes approval grants an agent that holds none — a mirror of
 * `DEFAULT_AGENT_SCOPES` in `src/jentic_one/shared/scopes.py`, which is the
 * source of truth, in the same order. No endpoint serves the list, so a unit
 * test pins it and a change there shows up here in review.
 */
export const DEFAULT_AGENT_SCOPES: readonly string[] = [
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

/** What approving an agent grants, given the scopes it holds now. */
export type ApprovalGrant =
	/** It holds none, so approval grants {@link DEFAULT_AGENT_SCOPES}. */
	| { kind: 'defaults'; granted: readonly string[]; unrecognised: [] }
	/** It requested some: approval makes the recognised ones live and adds no
	 * defaults — even when none of them is recognised, so `granted` can be empty. */
	| { kind: 'requested'; granted: string[]; unrecognised: string[] };

/**
 * Splits an agent's requested scopes the way `AgentService.approve` treats
 * them: an empty request gets the default agent scopes; otherwise scopes
 * outside the permission catalogue grant nothing, and the defaults are not
 * added in their place.
 */
export function approvalGrant(
	requested: readonly string[],
	catalogue: Iterable<string>,
): ApprovalGrant {
	if (requested.length === 0)
		return { kind: 'defaults', granted: DEFAULT_AGENT_SCOPES, unrecognised: [] };
	const known = new Set([...catalogue, ...ALWAYS_KNOWN]);
	return {
		kind: 'requested',
		granted: requested.filter((scope) => known.has(scope)),
		unrecognised: requested.filter((scope) => !known.has(scope)),
	};
}

/** The areas a scope list is grouped into for review, in reading order. */
export const SCOPE_AREAS = [
	'Capabilities',
	'APIs & catalog',
	'Executions, jobs & events',
	'Credentials',
	"Its owner's resources",
	'Organisation',
	'Other',
] as const;

export type ScopeArea = (typeof SCOPE_AREAS)[number];

const AREA_BY_RESOURCE: Record<string, ScopeArea> = {
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

/** The area a scope belongs to, by its first segment (`owner:agents:read` → owner). */
export function scopeArea(scope: string): ScopeArea {
	const resource = scope.trim().toLowerCase().split(':')[0] ?? '';
	return AREA_BY_RESOURCE[resource] ?? 'Other';
}

/** `scopes` grouped by area, areas in {@link SCOPE_AREAS} order, scopes in
 * their given order; empty areas are left out. */
export function groupScopesByArea(
	scopes: readonly string[],
): Array<{ area: ScopeArea; scopes: string[] }> {
	return SCOPE_AREAS.map((area) => ({
		area,
		scopes: scopes.filter((scope) => scopeArea(scope) === area),
	})).filter((group) => group.scopes.length > 0);
}
