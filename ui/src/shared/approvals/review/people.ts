/**
 * The people a held call involves, named for a person: "You" for the
 * signed-in viewer, the directory's name otherwise, never a raw id. And who
 * can decide the call — the agent's owner and every org admin — counted when
 * the viewer may read the org's admins (an org admin may), else said in words.
 */
import { useOptionalCurrentUser } from '@/shared/auth/AuthContext';
import { ORG_ADMIN } from '@/shared/auth/usePermission';
import { useActorDirectory } from '@/shared/hooks/useActorDirectory';
import { useOrgAdmins } from '@/shared/approvals/hooks';
import type { HeldCallDetail } from '@/shared/approvals/heldCall';

/** Resolves actor ids to names: "You" for the viewer, the directory's name, else `fallback`. */
export function usePeopleNames(ids: readonly (string | null | undefined)[]) {
	const viewer = useOptionalCurrentUser()?.id;
	const { resolve } = useActorDirectory(ids);
	return (id: string | null | undefined, fallback: string): string => {
		if (!id) return fallback;
		if (id === viewer) return 'You';
		return resolve(id) ?? fallback;
	};
}

export interface DecidersLine {
	/** "You and 2 others can decide this". */
	text: string;
	/** The others by name, when they could be counted. */
	others: string[] | null;
}

const OWNER_FALLBACK = 'The agent’s owner';

/**
 * Who can decide `call`. An org admin viewer reads the org's admins and gets
 * a count ("You and 2 others can decide this"); anyone else can't list them, so
 * the line names the owner and says org admins can decide too. Null while the
 * admins are still loading, so the line doesn't change under the reader.
 */
export function useDecidersLine(
	call: Pick<HeldCallDetail, 'agent_owner_id'> | undefined,
): DecidersLine | null {
	const viewer = useOptionalCurrentUser();
	const viewerId = viewer?.id ?? null;
	const isAdmin = viewer?.permissions?.includes(ORG_ADMIN) ?? false;
	const ownerId = call?.agent_owner_id ?? null;
	const admins = useOrgAdmins(isAdmin && !!call);
	const { resolve } = useActorDirectory([ownerId]);

	if (!call) return null;
	if (isAdmin && admins.isLoading) return null;

	if (isAdmin && admins.data?.complete) {
		const people = new Map<string, string>(admins.data.admins.map((a) => [a.id, a.name]));
		if (ownerId && !people.has(ownerId))
			people.set(ownerId, resolve(ownerId) ?? OWNER_FALLBACK);
		const others = [...people].filter(([id]) => id !== viewerId).map(([, name]) => name);
		if (viewerId && people.has(viewerId)) {
			return {
				text:
					others.length === 0
						? 'Only you can decide this'
						: `You and ${others.length} ${others.length === 1 ? 'other' : 'others'} can decide this`,
				others,
			};
		}
		return {
			text: `${people.size} ${people.size === 1 ? 'person' : 'people'} can decide this`,
			others,
		};
	}

	if (!ownerId) return { text: 'Org admins can decide this', others: null };
	if (ownerId === viewerId) {
		return { text: 'You (the agent’s owner) and org admins can decide this', others: null };
	}
	const owner = resolve(ownerId);
	return {
		text: owner
			? `${owner} (the agent’s owner) and org admins can decide this`
			: `${OWNER_FALLBACK} and org admins can decide this`,
		others: null,
	};
}
