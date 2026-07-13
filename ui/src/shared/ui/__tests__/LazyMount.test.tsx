/**
 * LazyMount — behaviour tests in a real browser (vitest browser mode).
 *
 * The component keeps the *anchor element* (with its `id`) in the DOM at all
 * times so scroll-spy and hash navigation keep working, but swaps the expensive
 * children for a fixed-height placeholder until an IntersectionObserver says the
 * element is within `rootMargin` of the viewport. These tests cover the three
 * guarantees the API reference relies on:
 *   - the anchor id is present even while the children are collapsed;
 *   - children mount once the element scrolls into the observation band;
 *   - once shown (`once`, the default) they stay mounted on scroll-back.
 */
import { describe, it, expect, afterEach } from 'vitest';
import { render, screen, cleanup, act, waitFor } from '@testing-library/react';
import { LazyMount } from '@/shared/ui/LazyMount';

afterEach(() => {
	cleanup();
	window.scrollTo(0, 0);
});

/** A LazyMount sitting far below the fold, behind a tall spacer, so it starts
 *  outside the (small) observation band and only mounts once scrolled to. */
function Harness({ rootMargin }: { rootMargin?: string }) {
	return (
		<div>
			<div style={{ height: '300vh' }} data-testid="spacer" />
			<LazyMount id="target" rootMargin={rootMargin} minHeight={240}>
				<p data-testid="payload">heavy content</p>
			</LazyMount>
		</div>
	);
}

describe('LazyMount', () => {
	it('keeps the anchor id in the DOM while collapsed', () => {
		// A tight band so the offscreen target stays collapsed initially.
		render(<Harness rootMargin="0px" />);
		// The id must always resolve so hash navigation / scroll-spy can find it…
		expect(document.getElementById('target')).not.toBeNull();
		// …but the expensive children are not mounted yet.
		expect(screen.queryByTestId('payload')).not.toBeInTheDocument();
	});

	it('reserves placeholder height before the children mount', () => {
		render(<Harness rootMargin="0px" />);
		const el = document.getElementById('target')!;
		expect(el.style.minHeight).toBe('240px');
	});

	it('mounts the children once scrolled into the observation band', async () => {
		render(<Harness rootMargin="0px" />);
		expect(screen.queryByTestId('payload')).not.toBeInTheDocument();

		await act(async () => {
			document.getElementById('target')!.scrollIntoView();
		});

		expect(await screen.findByTestId('payload')).toBeInTheDocument();
		// Placeholder height is dropped once real content measures in.
		await waitFor(() => expect(document.getElementById('target')!.style.minHeight).toBe(''));
	});

	it('mounts eagerly when the element is already in the band', async () => {
		// No spacer: the element sits at the top of the document, inside even a
		// tight band, so it mounts on the first observation without scrolling.
		render(
			<LazyMount id="eager" rootMargin="0px" minHeight={240}>
				<p data-testid="payload">heavy content</p>
			</LazyMount>,
		);
		expect(await screen.findByTestId('payload')).toBeInTheDocument();
	});

	it('stays mounted after scrolling back away (once=true default)', async () => {
		render(<Harness rootMargin="0px" />);

		await act(async () => {
			document.getElementById('target')!.scrollIntoView();
		});
		await screen.findByTestId('payload');

		// Scroll well away — `once` means the children must remain mounted so
		// scrolling back never re-pays the render cost or loses element state.
		await act(async () => {
			window.scrollTo(0, 0);
		});
		expect(screen.getByTestId('payload')).toBeInTheDocument();
	});

	it('applies the provided id and className to the anchor element', () => {
		render(
			<LazyMount id="anchored" className="lazy-anchor" rootMargin="0px">
				<span>child</span>
			</LazyMount>,
		);
		const el = document.getElementById('anchored');
		expect(el).not.toBeNull();
		expect(el).toHaveClass('lazy-anchor');
	});
});
