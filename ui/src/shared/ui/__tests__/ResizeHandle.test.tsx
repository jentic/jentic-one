import { useRef } from 'react';
import { describe, expect, it, beforeEach } from 'vitest';
import {
	renderWithProviders,
	screen,
	fireEvent,
	waitFor,
	userEvent,
	checkA11y,
} from '@/__tests__/test-utils';
import { ResizeHandle } from '@/shared/ui/ResizeHandle';
import { useResizableWidth } from '@/shared/hooks/useResizableWidth';

const KEY = 'test.resizeWidth';
const GRID = 1200;

/** Main column · 24px handle · panel, like the Library's grid. */
function Split({ enabled = true }: { enabled?: boolean }) {
	const gridRef = useRef<HTMLDivElement>(null);
	const panelRef = useRef<HTMLDivElement>(null);
	const w = useResizableWidth({
		storageKey: KEY,
		containerRef: gridRef,
		panelRef,
		cssVar: '--panel-w',
		min: 320,
		maxFor: (width) => Math.min(width * 0.5, width - 24 - 480),
		enabled,
	});
	return (
		<div
			ref={gridRef}
			data-testid="grid"
			style={{
				width: GRID,
				height: 300,
				display: 'grid',
				gridTemplateColumns: 'minmax(0,1fr) 24px var(--panel-w, 400px)',
			}}
		>
			<div />
			<ResizeHandle
				label="Resize workspace panel"
				value={w.width}
				min={w.min}
				max={w.max}
				onPreview={w.preview}
				onCommit={w.commit}
				onReset={w.reset}
			/>
			<div ref={panelRef} data-testid="panel" />
		</div>
	);
}

const panelWidth = () => Math.round(screen.getByTestId('panel').getBoundingClientRect().width);
const handle = () => screen.getByRole('separator', { name: 'Resize workspace panel' });
const frame = () => new Promise((r) => requestAnimationFrame(() => r(null)));

async function drag(dx: number) {
	const h = handle();
	const box = h.getBoundingClientRect();
	const x = box.left + box.width / 2;
	const y = box.top + 20;
	fireEvent.pointerDown(h, { button: 0, pointerId: 1, clientX: x, clientY: y });
	fireEvent.pointerMove(h, { pointerId: 1, clientX: x + dx / 2, clientY: y });
	await frame();
	fireEvent.pointerMove(h, { pointerId: 1, clientX: x + dx, clientY: y });
	await frame();
	fireEvent.pointerUp(h, { pointerId: 1, clientX: x + dx, clientY: y });
}

describe('ResizeHandle + useResizableWidth', () => {
	beforeEach(() => window.localStorage.removeItem(KEY));

	it('is an accessible vertical separator with its bounds', async () => {
		const { container } = renderWithProviders(<Split />);
		const h = handle();
		expect(h).toHaveAttribute('aria-orientation', 'vertical');
		expect(h).toHaveAttribute('tabindex', '0');
		await waitFor(() => expect(h).toHaveAttribute('aria-valuenow', '400'));
		expect(h).toHaveAttribute('aria-valuemin', '320');
		expect(h).toHaveAttribute('aria-valuemax', '600');
		await checkA11y(container);
	});

	it('dragging left widens the panel, and the width persists', async () => {
		renderWithProviders(<Split />);
		await waitFor(() => expect(handle()).toHaveAttribute('aria-valuenow', '400'));
		await drag(-100);
		await waitFor(() => expect(panelWidth()).toBe(500));
		expect(handle()).toHaveAttribute('aria-valuenow', '500');
		expect(window.localStorage.getItem(KEY)).toBe('500');
		// No stray global drag state once released.
		expect(document.documentElement).not.toHaveClass('is-resizing');
	});

	it('clamps to min and max while dragging', async () => {
		renderWithProviders(<Split />);
		await waitFor(() => expect(handle()).toHaveAttribute('aria-valuenow', '400'));
		await drag(-900);
		await waitFor(() => expect(panelWidth()).toBe(600));
		await drag(900);
		await waitFor(() => expect(panelWidth()).toBe(320));
		expect(window.localStorage.getItem(KEY)).toBe('320');
	});

	it('restores a stored width on mount, clamped to what fits now', async () => {
		window.localStorage.setItem(KEY, '450');
		const { unmount } = renderWithProviders(<Split />);
		await waitFor(() => expect(panelWidth()).toBe(450));
		unmount();
		window.localStorage.setItem(KEY, '5000');
		renderWithProviders(<Split />);
		await waitFor(() => expect(panelWidth()).toBe(600));
	});

	it('double-click resets to the default width and clears storage', async () => {
		window.localStorage.setItem(KEY, '520');
		renderWithProviders(<Split />);
		await waitFor(() => expect(panelWidth()).toBe(520));
		fireEvent.doubleClick(handle());
		await waitFor(() => expect(panelWidth()).toBe(400));
		expect(window.localStorage.getItem(KEY)).toBeNull();
	});

	it('keyboard: ←/→ step, Home/End jump to min/max, Enter resets', async () => {
		const user = userEvent.setup();
		renderWithProviders(<Split />);
		await waitFor(() => expect(handle()).toHaveAttribute('aria-valuenow', '400'));
		handle().focus();
		await user.keyboard('{ArrowLeft}');
		await waitFor(() => expect(panelWidth()).toBe(416));
		await user.keyboard('{ArrowRight}{ArrowRight}');
		await waitFor(() => expect(panelWidth()).toBe(384));
		await user.keyboard('{Shift>}{ArrowLeft}{/Shift}');
		await waitFor(() => expect(panelWidth()).toBe(448));
		await user.keyboard('{End}');
		await waitFor(() => expect(panelWidth()).toBe(600));
		await user.keyboard('{Home}');
		await waitFor(() => expect(panelWidth()).toBe(320));
		await user.keyboard('{Enter}');
		await waitFor(() => expect(panelWidth()).toBe(400));
		expect(window.localStorage.getItem(KEY)).toBeNull();
	});

	it('a click without movement does not pin a width', async () => {
		renderWithProviders(<Split />);
		await waitFor(() => expect(handle()).toHaveAttribute('aria-valuenow', '400'));
		await drag(0);
		expect(window.localStorage.getItem(KEY)).toBeNull();
	});

	it('losing pointer capture mid-drag ends the drag (no stuck resize cursor)', async () => {
		renderWithProviders(<Split />);
		await waitFor(() => expect(handle()).toHaveAttribute('aria-valuenow', '400'));
		const h = handle();
		const box = h.getBoundingClientRect();
		fireEvent.pointerDown(h, { button: 0, pointerId: 1, clientX: box.left, clientY: box.top });
		expect(document.documentElement).toHaveClass('is-resizing');
		fireEvent.lostPointerCapture(h, { pointerId: 1 });
		expect(document.documentElement).not.toHaveClass('is-resizing');
		expect(h).not.toHaveAttribute('data-dragging');
	});

	it('draws a crisp 2×2 grip: equal 3px round dots, 3px gaps, whole-pixel and centred', async () => {
		renderWithProviders(<Split />);
		await frame();
		const grip = screen.getByTestId('resize-handle-grip').getBoundingClientRect();
		const h = handle().getBoundingClientRect();
		const dots = screen
			.getAllByTestId('resize-handle-dot')
			.map((d) => d.getBoundingClientRect());
		expect(dots).toHaveLength(4);
		for (const d of dots) {
			expect([d.width, d.height]).toEqual([3, 3]);
			expect(Number.isInteger(d.left) && Number.isInteger(d.top)).toBe(true);
		}
		const xs = [...new Set(dots.map((d) => d.left))].sort((a, b) => a - b);
		const ys = [...new Set(dots.map((d) => d.top))].sort((a, b) => a - b);
		// Two columns / rows, 3px apart (gap) on both axes.
		expect(xs[1] - xs[0]).toBe(6);
		expect(ys[1] - ys[0]).toBe(6);
		// The dot grid is centred in the pill, and the pill on the handle.
		expect(xs[0] - grip.left).toBe(grip.right - (xs[1] + 3));
		expect(ys[0] - grip.top).toBe(grip.bottom - (ys[1] + 3));
		expect(Number.isInteger(grip.left) && Number.isInteger(grip.top)).toBe(true);
		expect(Math.abs(grip.left + grip.width / 2 - (h.left + h.width / 2))).toBeLessThanOrEqual(
			0.5,
		);
		expect(Math.abs(grip.top + grip.height / 2 - (h.top + h.height / 2))).toBeLessThanOrEqual(
			1,
		);
		const radius = getComputedStyle(screen.getAllByTestId('resize-handle-dot')[0]).borderRadius;
		expect(parseFloat(radius)).toBeGreaterThanOrEqual(1.5);
	});
});
