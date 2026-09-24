/**
 * Compute the next-subsection completion for a path input.
 *
 * Given the user's in-progress path and the set of real operation
 * paths from the vendor's OpenAPI, return the extension that "Tab"
 * should apply: the longest common prefix of the paths that are
 * STRICTLY longer than the current input, trimmed at the next ``/``
 * boundary after the input length so successive Tab presses step
 * through segments (e.g. ``"" → / → /repos/ → /repos/{owner}/`` for a
 * typical GitHub catalog).
 *
 * Returns ``null`` when there's nothing to extend to — no candidate
 * matches, the LCP is already the current input, or the input equals
 * a leaf path with no deeper siblings. Callers use ``null`` as the
 * signal to let the browser's default Tab behaviour (focus movement)
 * happen instead of extending.
 */
export function nextPathCompletion(current: string, paths: readonly string[]): string | null {
	// Only paths STRICTLY longer than the input are extension candidates.
	// If we included exact matches, an input that already equals a real
	// path (e.g. ``/repos`` when both ``/repos`` and ``/repos/{owner}/…``
	// exist) would produce an LCP of ``/repos`` and we'd fail to step
	// into the deeper subtree.
	const candidates = paths.filter((p) => p.length > current.length && p.startsWith(current));
	if (candidates.length === 0) return null;
	let lcp = candidates[0];
	for (let k = 1; k < candidates.length && lcp.length > 0; k++) {
		const p = candidates[k];
		let i = 0;
		while (i < lcp.length && i < p.length && lcp[i] === p[i]) i++;
		lcp = lcp.slice(0, i);
	}
	if (lcp.length <= current.length) return null;
	// Search STRICTLY past the current position for the next ``/``
	// boundary so we step forward one segment at a time even when the
	// user's input already ends on a ``/``.
	const nextSlash = lcp.indexOf('/', current.length + 1);
	return nextSlash > current.length ? lcp.slice(0, nextSlash + 1) : lcp;
}
