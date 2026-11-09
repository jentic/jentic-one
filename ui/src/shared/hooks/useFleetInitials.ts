/**
 * useFleetInitials — the avatar letters every surface gives an actor, read
 * from one place so the Agents tab and Monitor's charts agree by construction
 * rather than by each computing them over its own list.
 *
 * Agents come from the actor directory (`GET /actors`, for a caller who may
 * list it) joined with the roster the calling surface holds: a surface that
 * has the fleet needs nothing more, and one that holds only ids (Monitor's
 * usage rollup) still reads the fleet's letters. Agents are served first,
 * then the directory's users, so an agent's letters never depend on a user
 * and a user drawn beside agents still reads apart (`tieredInitials`).
 *
 * The letters are the directory's, not one surface's: an agent the directory
 * lists but a surface does not show still holds its letters, so a caller who
 * may list the directory reads the same letters on every surface. Until the
 * directory has loaded the roster alone is read, so a cold load can settle
 * the letters once; the listing is cached and shared, so that is rare.
 */
import { useMemo } from 'react';
import { useActorDirectory } from '@/shared/hooks/useActorDirectory';
import { tieredInitials, type Named } from '@/shared/lib/smartInitials';

const NO_ROSTER: readonly Named[] = [];

/**
 * @param roster The agents the calling surface already holds (the Agents tab's
 *   fleet). They fill in whatever the directory lacks: everything, for a caller
 *   who may not list it; an agent registered since the listing, for one who may.
 */
export function useFleetInitials(
	roster: readonly Named[] = NO_ROSTER,
): ReadonlyMap<string, string> {
	const { byId } = useActorDirectory();
	return useMemo(() => {
		const agents = new Map<string, Named>();
		const users: Named[] = [];
		for (const actor of byId.values()) {
			const named = { id: actor.id, name: actor.name, createdAt: actor.created_at ?? '' };
			if (actor.actor_type === 'agent') agents.set(actor.id, named);
			else if (actor.actor_type === 'user') users.push(named);
		}
		for (const agent of roster) if (!agents.has(agent.id)) agents.set(agent.id, agent);
		return tieredInitials([[...agents.values()], users]);
	}, [byId, roster]);
}
