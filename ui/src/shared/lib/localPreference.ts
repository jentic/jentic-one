/**
 * Local UI preferences — one small, failure-tolerant localStorage read/write
 * pair, shared by every persisted layout choice (the Library workspace
 * resizer's width, the Agents "Can call" list ⇄ cards lens, …).
 *
 * Keys follow one convention: `<module>.<camelCaseName>` in the plain
 * localStorage namespace — e.g. `library.workspaceWidth`, `agents.apiView`.
 * Storage that is missing or throws (SSR, private mode, quota) reads as "no
 * stored value" and writes are dropped: the preference simply doesn't persist.
 */

/** The stored string for `key`, or `null` (nothing stored / storage unavailable). */
export function readLocalPreference(key: string): string | null {
	if (typeof window === 'undefined') return null;
	try {
		return window.localStorage.getItem(key);
	} catch {
		return null;
	}
}

/** Store `value` under `key`; `null` forgets it. Never throws. */
export function writeLocalPreference(key: string, value: string | null): void {
	if (typeof window === 'undefined') return;
	try {
		if (value == null) window.localStorage.removeItem(key);
		else window.localStorage.setItem(key, value);
	} catch {
		/* storage unavailable (private mode / quota) — the preference just won't persist */
	}
}
