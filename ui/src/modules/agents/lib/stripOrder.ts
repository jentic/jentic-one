/**
 * The agent strip's rail order: recent first. The agents switched between most
 * lately lead the rail, newest first, so the agent just left sits right after
 * the pinned one; every other agent keeps the fleet's own order after them.
 * Waiting agents stay apart, at the front, in the fleet's order: they are a
 * queue of decisions, not a history. The picker keeps the fleet's order.
 */

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
