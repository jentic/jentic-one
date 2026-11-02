/**
 * The agent strip's rail order: recent first. The agents switched between most
 * lately lead the rail, newest first, so the agent just left sits right after
 * the pinned one; every other agent keeps the fleet's own order after them.
 * Waiting agents stay apart, at the front, in the fleet's order: they are a
 * queue of decisions, not a history. Agents whose verdict is in close the
 * rail: a denied or archived agent once opened is not where the work is. The
 * picker keeps the fleet's order.
 */
import type { ActorStatus } from '@/modules/agents/api';

/** Where the strip's history persists across reloads. */
export const STRIP_RECENT_STORAGE_KEY = 'agents.stripRecent';

/**
 * `agents` (already in the fleet's order) with the ids in `recent` moved to
 * the front, in `recent`'s order; the rest keep their places. An id in `recent`
 * that isn't in `agents` is skipped.
 */
export function recentFirst<T extends { id: string }>(
	agents: readonly T[],
	recent: readonly string[],
): T[] {
	const rank = new Map<string, number>();
	recent.forEach((id, i) => {
		if (!rank.has(id)) rank.set(id, i);
	});
	const unranked = recent.length;
	return agents
		.map((agent, i) => ({ agent, i, r: rank.get(agent.id) ?? unranked }))
		.sort((a, b) => a.r - b.r || a.i - b.i)
		.map(({ agent }) => agent);
}

/** Non-active states whose verdict is in: their tabs read struck through.
 * `pending` is still in motion, so crossing it out would state the opposite. */
export const SETTLED_STATUSES: ReadonlySet<ActorStatus> = new Set<ActorStatus>([
	'rejected',
	'disabled',
	'archived',
]);

/**
 * The rail after the pin: `recentFirst` over the live agents, then the settled
 * ones in the fleet's order. A settled agent rides the history only while it
 * is `justLeft` — the agent the last switch came from — so it still lands
 * beside the pin for that one switch; the next switch moves it to the tail.
 */
export function railOrder<T extends { id: string; status: ActorStatus }>(
	agents: readonly T[],
	recent: readonly string[],
	justLeft: string | null | undefined,
): T[] {
	const settled = (a: T) => SETTLED_STATUSES.has(a.status) && a.id !== justLeft;
	return [
		...recentFirst(
			agents.filter((a) => !settled(a)),
			recent,
		),
		...agents.filter(settled),
	];
}
