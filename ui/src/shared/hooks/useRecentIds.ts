/**
 * useRecentIds — the ids most recently made current, newest first, persisted
 * with the local-preference storage (`<module>.<camelCaseName>` keys).
 *
 * The current id leads the list in the very render it becomes current, so a
 * surface ordered by it never paints the old order first. The list is written
 * back after each change and capped, so it never grows without bound; ids that
 * no longer exist are the caller's to skip. Storage that is missing, throws or
 * holds something unreadable reads as an empty history. A different
 * `storageKey` reads that key's own history — one key's list is never written
 * under another.
 */
import { useEffect, useState } from 'react';
import { readLocalPreference, writeLocalPreference } from '@/shared/lib/localPreference';

/** How many ids the history keeps. */
const DEFAULT_CAP = 50;

function readIds(key: string): string[] {
	const raw = readLocalPreference(key);
	if (!raw) return [];
	try {
		const parsed: unknown = JSON.parse(raw);
		return Array.isArray(parsed)
			? parsed.filter((id): id is string => typeof id === 'string' && id !== '')
			: [];
	} catch {
		return [];
	}
}

function lead(list: readonly string[], current: string | null, cap: number): string[] {
	if (current == null || list[0] === current) return list.slice(0, cap);
	return [current, ...list.filter((id) => id !== current)].slice(0, cap);
}

interface RecentState {
	storageKey: string;
	current: string | null;
	cap: number;
	ids: string[];
}

export function useRecentIds(
	storageKey: string,
	current: string | null,
	cap: number = DEFAULT_CAP,
): readonly string[] {
	const [state, setState] = useState<RecentState>(() => ({
		storageKey,
		current,
		cap,
		ids: lead(readIds(storageKey), current, cap),
	}));
	let { ids } = state;
	// A new key, current id or cap is applied now, in this render — not an
	// effect later, which would paint one frame of the stale list.
	if (state.storageKey !== storageKey || state.current !== current || state.cap !== cap) {
		const base = state.storageKey === storageKey ? state.ids : readIds(storageKey);
		ids = lead(base, current, cap);
		setState({ storageKey, current, cap, ids });
	}
	useEffect(() => {
		writeLocalPreference(storageKey, JSON.stringify(ids));
	}, [storageKey, ids]);
	return ids;
}
