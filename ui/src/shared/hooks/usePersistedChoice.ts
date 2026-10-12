/**
 * usePersistedChoice — one of a fixed set of string options, persisted with the
 * same local-preference storage (and `<module>.<camelCaseName>` key convention)
 * as the workspace resizer's width (`useResizableWidth`).
 *
 * The stored value is read once on mount; anything that isn't one of
 * `choices` (nothing stored, a stale value, storage unavailable) reads as
 * `fallback`. The setter is stable and writes through.
 */
import { useCallback, useState } from 'react';
import { readLocalPreference, writeLocalPreference } from '@/shared/lib/localPreference';

export function usePersistedChoice<T extends string>(
	storageKey: string,
	choices: readonly T[],
	fallback: T,
): [T, (next: T) => void] {
	const [value, setValue] = useState<T>(() => {
		const stored = readLocalPreference(storageKey);
		return (choices as readonly string[]).includes(stored ?? '') ? (stored as T) : fallback;
	});
	const choose = useCallback(
		(next: T) => {
			setValue(next);
			writeLocalPreference(storageKey, next);
		},
		[storageKey],
	);
	return [value, choose];
}
