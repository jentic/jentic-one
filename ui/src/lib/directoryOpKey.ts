/**
 * Synthetic key for directory operations.
 *
 * Requirements:
 *   1. Is unique within an API's operation list (the inspect view matches
 *      back to the cached preview row by `method + path`).
 *   2. Survives URL round-tripping (we encode/decode through
 *      `?op=<value>`).
 *   3. Is distinguishable from a workspace capability id at decode time
 *      so the sheet picks the right inspect panel. We use ` ` (space) as
 *      separator — capability ids use `/`, so the two formats can't
 *      collide.
 */
export function directoryOpKey(method: string, path: string): string {
	return `${method.toUpperCase()} ${path}`;
}

export function parseDirectoryOpKey(key: string): { method: string; path: string } | null {
	const idx = key.indexOf(' ');
	if (idx <= 0) return null;
	return { method: key.slice(0, idx), path: key.slice(idx + 1) };
}

/** True when the `?op=` value targets a directory operation (space separator)
 *  rather than a workspace capability id (slash separator). */
export function isDirectoryOpKey(key: string): boolean {
	return parseDirectoryOpKey(key) !== null;
}
