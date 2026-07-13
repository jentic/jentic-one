/**
 * Actor / endpoint transforms — the data behind the "actor explorer".
 *
 * This is the browser-side mirror of the `jentic endpoints` CLI command
 * (cli/internal/cmd/endpoints.go): it reads the same `/reference/endpoints.json`
 * payload and applies the *same* two operations:
 *
 *   1. filter by actor type — keep endpoints whose `actor_types` include the
 *      selected actor (`user` | `agent` | `service_account` | `toolkit`);
 *   2. group by typical caller — the advisory bucket the CLI prints
 *      (Agent-facing / Operator-facing / Any authenticated / Public).
 *
 * Keeping the rules identical means the docs page and the CLI can never tell a
 * different story about who can call what. Pure functions over the payload.
 */
import type { ReferenceEndpoint, ReferencePayload } from '@/modules/docs/api/types';

/** The four actor identities the platform issues tokens for. */
export const ACTOR_TYPES = ['user', 'agent', 'service_account', 'toolkit'] as const;
export type ActorType = (typeof ACTOR_TYPES)[number];

/** Typical-caller buckets, mirroring `endpoints.go`'s group constants + order. */
export type CallerGroup = 'agent' | 'operator' | 'any' | 'public';

export const CALLER_GROUP_ORDER: CallerGroup[] = ['agent', 'operator', 'any', 'public'];

export const CALLER_GROUP_LABEL: Record<CallerGroup, string> = {
	agent: 'Agent-facing',
	operator: 'Operator-facing',
	any: 'Any authenticated actor',
	public: 'Public (unauthenticated)',
};

export const CALLER_GROUP_BLURB: Record<CallerGroup, string> = {
	agent: 'Typically called by an agent, service account, or toolkit.',
	operator: 'Typically called by a human operator or admin.',
	any: 'Any authenticated actor may call these.',
	public: 'No authentication required.',
};

/** Classify one endpoint into its caller bucket — mirrors `endpoint.group()`. */
export function callerGroupOf(e: ReferenceEndpoint): CallerGroup {
	if (e.public) return 'public';
	switch (e.typical_caller) {
		case 'agent':
			return 'agent';
		case 'operator':
			return 'operator';
		default:
			return 'any';
	}
}

/** Endpoints an actor type can be the caller of — mirrors `--actor` filter. */
export function endpointsForActor(
	payload: ReferencePayload,
	actor: ActorType,
): ReferenceEndpoint[] {
	return payload.endpoints
		.filter((e) => (e.actor_types ?? []).includes(actor))
		.sort((a, b) =>
			a.path === b.path ? a.method.localeCompare(b.method) : a.path.localeCompare(b.path),
		);
}

export interface CallerBucket {
	group: CallerGroup;
	endpoints: ReferenceEndpoint[];
}

/** Group an endpoint list into ordered caller buckets (drops empty buckets). */
export function groupByCaller(endpoints: ReferenceEndpoint[]): CallerBucket[] {
	const byGroup = new Map<CallerGroup, ReferenceEndpoint[]>();
	for (const e of endpoints) {
		const g = callerGroupOf(e);
		const list = byGroup.get(g);
		if (list) list.push(e);
		else byGroup.set(g, [e]);
	}
	return CALLER_GROUP_ORDER.filter((g) => byGroup.has(g)).map((g) => ({
		group: g,
		endpoints: byGroup.get(g)!,
	}));
}

/** Per-actor endpoint count, for the actor tabs. */
export function actorCounts(payload: ReferencePayload): Record<ActorType, number> {
	const counts = { user: 0, agent: 0, service_account: 0, toolkit: 0 } as Record<
		ActorType,
		number
	>;
	for (const e of payload.endpoints) {
		for (const a of e.actor_types ?? []) {
			if (a in counts) counts[a as ActorType] += 1;
		}
	}
	return counts;
}
