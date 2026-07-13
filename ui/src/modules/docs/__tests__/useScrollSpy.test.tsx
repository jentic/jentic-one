/**
 * useScrollSpy — behaviour tests in a real browser (vitest browser mode).
 *
 * Covers the regressions the position-scan rewrite fixes:
 *  - the active id tracks scrolling DOWN through sections;
 *  - it tracks scrolling back UP (bottom→top) without losing the highlight —
 *    the previous IntersectionObserver-band approach dropped to null/stale here;
 *  - sub-anchor mode (defaultFirst=false) reports null above the first anchor
 *    and resolves an id once scrolled into range.
 */
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { render, renderHook, cleanup, act, waitFor } from '@testing-library/react';
import { useScrollSpy } from '@/modules/docs/lib/useScrollSpy';

const IDS = ['s1', 's2', 's3'];

/** A scrollable document of three viewport-tall sections, below a tall spacer
 *  so the anchors start well below the active line (mirrors real usage where
 *  CLI command anchors sit far down the page). */
function Sections() {
	return (
		<div>
			<div style={{ height: '150vh' }} data-testid="spacer" />
			{IDS.map((id) => (
				<section key={id} id={id} style={{ height: '150vh' }}>
					{id}
				</section>
			))}
		</div>
	);
}

function scrollToSection(id: string) {
	document.getElementById(id)!.scrollIntoView();
}

describe('useScrollSpy', () => {
	beforeEach(() => {
		window.scrollTo(0, 0);
	});
	afterEach(() => {
		cleanup();
		window.scrollTo(0, 0);
	});

	it('defaults to the first id before any scrolling', () => {
		render(<Sections />);
		const { result } = renderHook(() => useScrollSpy(IDS));
		expect(result.current).toBe('s1');
	});

	it('tracks the active section scrolling down then back up', async () => {
		render(<Sections />);
		const { result } = renderHook(() => useScrollSpy(IDS));

		await act(async () => {
			scrollToSection('s3');
		});
		await waitFor(() => expect(result.current).toBe('s3'));

		// Bottom → top: this is the path that used to lose the highlight.
		await act(async () => {
			scrollToSection('s2');
		});
		await waitFor(() => expect(result.current).toBe('s2'));

		await act(async () => {
			window.scrollTo(0, 0);
		});
		await waitFor(() => expect(result.current).toBe('s1'));
	});

	it('sub-anchor mode reports null until an anchor is reached', async () => {
		render(<Sections />);
		const { result } = renderHook(() => useScrollSpy(IDS, '-120px 0px -60% 0px', false));
		expect(result.current).toBeNull();

		await act(async () => {
			scrollToSection('s2');
		});
		await waitFor(() => expect(result.current).toBe('s2'));

		// Scrolling back above every anchor returns to null.
		await act(async () => {
			window.scrollTo(0, 0);
		});
		await waitFor(() => expect(result.current).toBeNull());
	});

	it('returns null for an empty id list', () => {
		const { result } = renderHook(() => useScrollSpy([]));
		expect(result.current).toBeNull();
	});
});
