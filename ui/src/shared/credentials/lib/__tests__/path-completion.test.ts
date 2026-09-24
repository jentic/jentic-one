import { describe, expect, it } from 'vitest';
import { nextPathCompletion } from '@/shared/credentials/lib/path-completion';

/**
 * The Tab-to-next-segment shortcut on the rule-authoring path input
 * relies on ``nextPathCompletion`` for its extension logic. These
 * cases pin the shape of that extension so we don't accidentally regress
 * to "extend all the way to the top match" (which would defeat the
 * subsection-stepping UX).
 */
describe('nextPathCompletion', () => {
	const paths = [
		'/repos',
		'/repos/{owner}/{repo}/pulls',
		'/repos/{owner}/{repo}/issues',
		'/rest/actions',
		'/user',
	];

	it('extends to the next path segment when there is a unique next boundary', () => {
		// After ``/repos``, all matches share the ``/{owner}/`` segment —
		// so a Tab should extend to ``/repos/{owner}/`` and stop there
		// (not jump all the way to the specific op path).
		expect(nextPathCompletion('/repos', paths)).toBe('/repos/{owner}/');
	});

	it('extends to the LCP when it does not include another slash boundary', () => {
		// ``/re`` matches both ``/repos*`` and ``/rest/*``; the LCP is
		// ``/re`` itself, no extension possible.
		expect(nextPathCompletion('/re', paths)).toBe(null);
	});

	it('extends from an empty input to the deepest shared prefix', () => {
		// With no input, the LCP across all paths is ``/`` — after the
		// leading slash the paths diverge, so we should extend to ``/``.
		// Historical rule: we search past the leading ``/`` so an input
		// of ``""`` still steps forward.
		expect(nextPathCompletion('', paths)).toBe('/');
	});

	it('returns null when no path starts with the input', () => {
		expect(nextPathCompletion('/nope', paths)).toBe(null);
	});

	it('returns null when the LCP is already the input', () => {
		// ``/user`` matches only itself — the LCP equals the input.
		expect(nextPathCompletion('/user', paths)).toBe(null);
	});
});
