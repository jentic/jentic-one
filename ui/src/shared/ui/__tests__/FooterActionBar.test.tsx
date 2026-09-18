/**
 * FooterActionBar layout pins — the fixed-bottom primitive must coexist with
 * the mobile `BottomNavbar` (`fixed bottom-0`, `z-50`, `h-16` + safe-area):
 *
 *  - below `md` it sits ABOVE the nav's height, never under it;
 *  - at `md`+ (nav hidden) it owns the true bottom of the viewport;
 *  - its z-index stays below the nav's 50 so nav overlays win the stack.
 *
 * Chromium reports `env(safe-area-inset-bottom)` as 0, so the computed
 * offsets below are the no-notch values of the `calc()` expressions.
 */
import { describe, it, expect } from 'vitest';
import { page } from 'vitest/browser';
import { renderWithProviders, screen, waitFor, checkA11y } from '@/__tests__/test-utils';
import { Button } from '@/shared/ui/Button';
import { FooterActionBar, FOOTER_ACTION_BAR_PAGE_PADDING } from '@/shared/ui/FooterActionBar';

/** The bar element itself (the component's outermost div). */
function bar(container: HTMLElement): HTMLElement {
	return container.firstElementChild as HTMLElement;
}

describe('FooterActionBar', () => {
	it('renders its children', () => {
		renderWithProviders(
			<FooterActionBar>
				<Button>Save</Button>
			</FooterActionBar>,
		);
		expect(screen.getByRole('button', { name: 'Save' })).toBeInTheDocument();
	});

	it('below md: fixed above the bottom nav height, z-index below the nav', async () => {
		await page.viewport(375, 812);
		const { container } = renderWithProviders(
			<FooterActionBar>
				<Button>Save</Button>
			</FooterActionBar>,
		);
		const style = getComputedStyle(bar(container));
		expect(style.position).toBe('fixed');
		// calc(4rem + env(safe-area-inset-bottom)) → 64px with a 0 inset:
		// clears the nav's h-16 row so the two bars stack, never overlap.
		expect(style.bottom).toBe('64px');
		// Strictly below the nav's z-50, so the nav's "More" sheet wins.
		expect(Number(style.zIndex)).toBeLessThan(50);
		expect(Number(style.zIndex)).toBeGreaterThan(0);
	});

	it('at md and up: sits at the true bottom of the viewport, full width', async () => {
		await page.viewport(1280, 900);
		const { container } = renderWithProviders(
			<FooterActionBar>
				<Button>Save</Button>
			</FooterActionBar>,
		);
		const el = bar(container);
		const style = getComputedStyle(el);
		expect(style.position).toBe('fixed');
		expect(style.bottom).toBe('0px');
		// The default variant is edge-to-edge (inset-x-0).
		const rect = el.getBoundingClientRect();
		expect(rect.left).toBe(0);
		expect(rect.right).toBe(1280);
	});

	it('floating variant: a centred pill hovering above the bottom edge', async () => {
		await page.viewport(1280, 900);
		const { container } = renderWithProviders(
			<FooterActionBar floating>
				<Button>Save</Button>
			</FooterActionBar>,
		);
		const el = bar(container);
		const style = getComputedStyle(el);
		expect(style.position).toBe('fixed');
		// calc(env(safe-area-inset-bottom) + 0.75rem) → 12px with a 0 inset.
		expect(style.bottom).toBe('12px');
		// Pill shape, not an edge-to-edge bar.
		expect(style.borderRadius).not.toBe('0px');
		const rect = el.getBoundingClientRect();
		expect(rect.width).toBeLessThan(1280);
		// Centred: symmetric gutters either side (rounding tolerance).
		expect(Math.abs(rect.left - (1280 - rect.right))).toBeLessThanOrEqual(1);
	});

	it('floating variant below md: still clears the bottom nav', async () => {
		await page.viewport(375, 812);
		const { container } = renderWithProviders(
			<FooterActionBar floating>
				<Button>Save</Button>
			</FooterActionBar>,
		);
		// calc(4rem + env(safe-area-inset-bottom) + 0.75rem) → 76px: the nav's
		// height plus the hover gap.
		expect(getComputedStyle(bar(container)).bottom).toBe('76px');
	});

	it('anchorToContainer: centres the floating pill on its DOM parent, not the viewport', async () => {
		await page.viewport(1280, 900);
		// Mimic the app shell at xl+: a content column with a fixed-width rail
		// beside it — viewport-centring would sit the pill 150px off the
		// column's own centre.
		renderWithProviders(
			<div style={{ display: 'flex', width: 1280 }}>
				<div data-testid="content-column" style={{ width: 980 }}>
					<FooterActionBar floating anchorToContainer>
						<Button>Save</Button>
					</FooterActionBar>
				</div>
				<div style={{ width: 300 }} />
			</div>,
		);
		const el = screen.getByRole('button', { name: 'Save' }).closest('div')!;
		const column = screen.getByTestId('content-column');
		await waitFor(() => {
			const rect = el.getBoundingClientRect();
			const columnRect = column.getBoundingClientRect();
			const barCenter = rect.left + rect.width / 2;
			const columnCenter = columnRect.left + columnRect.width / 2;
			// Centred on the column (rounding tolerance)…
			expect(Math.abs(barCenter - columnCenter)).toBeLessThanOrEqual(1);
			// …which is measurably NOT the viewport centre.
			expect(Math.abs(barCenter - 640)).toBeGreaterThan(100);
		});
		// Vertical behaviour is unchanged: still fixed above the bottom edge.
		const style = getComputedStyle(el);
		expect(style.position).toBe('fixed');
		expect(style.bottom).toBe('12px');
	});

	it('anchorToContainer: tracks the parent when its width changes at runtime', async () => {
		await page.viewport(1280, 900);
		renderWithProviders(
			<div data-testid="content-column" style={{ width: 900 }}>
				<FooterActionBar floating anchorToContainer>
					<Button>Save</Button>
				</FooterActionBar>
			</div>,
		);
		const el = screen.getByRole('button', { name: 'Save' }).closest('div')!;
		const column = screen.getByTestId('content-column');
		await waitFor(() => {
			const rect = el.getBoundingClientRect();
			expect(Math.abs(rect.left + rect.width / 2 - 450)).toBeLessThanOrEqual(1);
		});

		// The app shell's rail collapses/expands at runtime — the pill must
		// follow the column's new centre (ResizeObserver, not a one-shot).
		column.style.width = '1200px';
		await waitFor(() => {
			const rect = el.getBoundingClientRect();
			expect(Math.abs(rect.left + rect.width / 2 - 600)).toBeLessThanOrEqual(1);
		});
	});

	it('exposes the page bottom-padding contract', () => {
		// Pages that mount the bar append this to their container so the last
		// row of content scrolls clear of it. It must pad harder below md,
		// where the bar stacks on top of the bottom nav.
		expect(FOOTER_ACTION_BAR_PAGE_PADDING).toMatch(/\bpb-\d+\b/);
		expect(FOOTER_ACTION_BAR_PAGE_PADDING).toMatch(/\bmd:pb-\d+\b/);
	});

	it('has no critical a11y violations', async () => {
		const { container } = renderWithProviders(
			<FooterActionBar>
				<Button>Save</Button>
				<Button variant="secondary">Cancel</Button>
			</FooterActionBar>,
		);
		await checkA11y(container);
	});
});
