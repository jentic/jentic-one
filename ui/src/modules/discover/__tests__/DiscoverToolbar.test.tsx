import { describe, it, expect, vi } from 'vitest';
import { renderWithProviders, screen, waitFor } from '@/__tests__/test-utils';
import { DiscoverToolbar } from '@/modules/discover/components/DiscoverToolbar';
import { SHELL_SCROLL_ID } from '@/shared/lib/shellScroll';

/** The shell's scroller, offset below a stand-in for the fixed navbar — so a
 * viewport-rooted observer would get the pin point wrong by that offset. */
function Shell({ children }: { children: React.ReactNode }) {
	return (
		<main
			id={SHELL_SCROLL_ID}
			style={{
				position: 'fixed',
				top: 48,
				left: 0,
				right: 0,
				height: 400,
				overflowY: 'auto',
			}}
		>
			<div style={{ height: 120 }}>Page header</div>
			{children}
			<div style={{ height: 3000 }}>Catalog</div>
		</main>
	);
}

function renderToolbar() {
	renderWithProviders(
		<Shell>
			<DiscoverToolbar
				query=""
				onQueryChange={vi.fn()}
				filter="all"
				onFilterChange={vi.fn()}
				onRefresh={vi.fn()}
			/>
		</Shell>,
	);
	return {
		toolbar: screen.getByTestId('discover-toolbar'),
		scroller: document.getElementById(SHELL_SCROLL_ID)!,
	};
}

describe('DiscoverToolbar', () => {
	it('flags data-scrolled only while the sticky bar is pinned to the scroller top', async () => {
		const { toolbar, scroller } = renderToolbar();
		expect(toolbar).toHaveAttribute('data-scrolled', 'false');

		// Scrolled, but not yet up to the bar: still at rest.
		scroller.scrollTop = 60;
		await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
		expect(toolbar).toHaveAttribute('data-scrolled', 'false');

		// Past the page header: the bar pins and gets its hairline. The observer
		// reports on its own schedule, which a loaded CI runner can stretch past
		// waitFor's 1s default — give it room rather than race it.
		scroller.scrollTop = 600;
		await waitFor(() => expect(toolbar).toHaveAttribute('data-scrolled', 'true'), {
			timeout: 5000,
		});
		expect(Math.round(toolbar.getBoundingClientRect().top)).toBe(48);

		// Back to the top: it lets go.
		scroller.scrollTop = 0;
		await waitFor(() => expect(toolbar).toHaveAttribute('data-scrolled', 'false'), {
			timeout: 5000,
		});
	});

	it('disconnects its observer on unmount', () => {
		const disconnect = vi.spyOn(IntersectionObserver.prototype, 'disconnect');
		renderWithProviders(
			<DiscoverToolbar
				query=""
				onQueryChange={vi.fn()}
				filter="all"
				onFilterChange={vi.fn()}
				onRefresh={vi.fn()}
			/>,
		).unmount();
		expect(disconnect).toHaveBeenCalled();
		disconnect.mockRestore();
	});
});
