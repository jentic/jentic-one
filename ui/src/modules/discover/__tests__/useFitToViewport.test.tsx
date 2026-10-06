import { useRef } from 'react';
import { describe, expect, it } from 'vitest';
import { renderWithProviders, screen, waitFor } from '@/__tests__/test-utils';
import { useFitToViewport } from '@/modules/discover/lib/useFitToViewport';

/**
 * Pins the dock's fit at the end of the page: when the sticky dock is pushed
 * up, a raw (negative) top must not make it taller — that would make the page
 * taller too, scrolling the dock's header away and leaving a void below the list.
 */
function Page({ mainHeight = 1600 }: { mainHeight?: number }) {
	const dock = useRef<HTMLDivElement>(null);
	const main = useRef<HTMLDivElement>(null);
	useFitToViewport(dock, { until: main, min: 120 });
	return (
		<div
			id="app-scroll"
			data-testid="scroller"
			style={{ height: 400, overflowY: 'auto', position: 'relative' }}
		>
			{/* Page padding under the grid (PageShell + <main>), as in the app. */}
			<div style={{ paddingBottom: 72 }}>
				<div
					style={{
						display: 'grid',
						gridTemplateColumns: '1fr 200px',
						alignItems: 'start',
					}}
				>
					<div ref={main} data-testid="main" style={{ height: mainHeight }} />
					<div
						ref={dock}
						data-testid="dock"
						style={{ position: 'sticky', top: 16, height: 300, overflowY: 'auto' }}
					/>
				</div>
			</div>
		</div>
	);
}

const frames = (n = 3) =>
	new Promise<void>((resolve) => {
		const tick = (left: number) =>
			left === 0 ? resolve() : requestAnimationFrame(() => tick(left - 1));
		tick(n);
	});

describe('useFitToViewport', () => {
	it('keeps the dock on screen at the bottom without growing the page', async () => {
		renderWithProviders(<Page />);
		const scroller = screen.getByTestId('scroller');
		const dock = screen.getByTestId('dock');
		await frames();
		const height = scroller.scrollHeight;
		for (let i = 0; i < 4; i++) {
			scroller.scrollTop = scroller.scrollHeight;
			scroller.dispatchEvent(new Event('scroll'));
			await frames();
		}
		// No feedback loop: the page is exactly as tall as the list.
		expect(scroller.scrollHeight).toBe(height);
		const s = scroller.getBoundingClientRect();
		const d = dock.getBoundingClientRect();
		const m = screen.getByTestId('main').getBoundingClientRect();
		// The dock's top stays on its sticky line, its bottom ends with the list.
		await waitFor(() => expect(Math.round(d.top - s.top)).toBeGreaterThanOrEqual(15));
		expect(d.bottom).toBeLessThanOrEqual(Math.ceil(m.bottom));
		expect(d.bottom).toBeLessThanOrEqual(s.bottom);
	});

	it('keeps its viewport height beside a column shorter than the viewport', async () => {
		// No matches / still loading: a ~150px catalog column must not shrink
		// the panel to its height.
		renderWithProviders(<Page mainHeight={150} />);
		const scroller = screen.getByTestId('scroller');
		const dock = screen.getByTestId('dock');
		await frames();
		// Sized to the viewport (400px less its sticky line and gap), not the column.
		const s = scroller.getBoundingClientRect();
		await waitFor(() =>
			expect(Math.round(dock.getBoundingClientRect().height)).toBe(
				Math.round(s.height - 16 - 16),
			),
		);
	});
});
