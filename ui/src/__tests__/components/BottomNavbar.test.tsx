import { screen, renderWithProviders, userEvent } from '../test-utils';
import { BottomNavbar } from '@/components/layout/BottomNavbar';
import { NAV_ITEMS } from '@/components/layout/navbar.constants';

describe('BottomNavbar', () => {
	it('renders 4 primary tiles + a "More" tile', () => {
		renderWithProviders(<BottomNavbar />);

		const nav = document.querySelector('nav');
		expect(nav).toBeInTheDocument();
		// First 4 items show as tiles (TILE_LIMIT - 1 = 4).
		for (const item of NAV_ITEMS.slice(0, 4)) {
			expect(nav!.textContent).toContain(item.label);
		}
		expect(screen.getByRole('button', { name: /more navigation items/i })).toBeInTheDocument();
	});

	it('opens the overflow sheet when the More tile is tapped', async () => {
		const user = userEvent.setup();
		renderWithProviders(<BottomNavbar />);

		await user.click(screen.getByRole('button', { name: /more navigation items/i }));

		// Overflow items appear in the sheet.
		for (const item of NAV_ITEMS.slice(4)) {
			expect(await screen.findByText(item.label)).toBeInTheDocument();
		}
	});

	it('closes the sheet when the close button is tapped', async () => {
		const user = userEvent.setup();
		renderWithProviders(<BottomNavbar />);

		await user.click(screen.getByRole('button', { name: /more navigation items/i }));
		const closeButton = await screen.findByRole('button', { name: /close/i });
		await user.click(closeButton);

		expect(screen.queryByRole('button', { name: /close/i })).not.toBeInTheDocument();
	});

	it('marks the active tile when the route matches', () => {
		renderWithProviders(<BottomNavbar />, { route: '/toolkits' });

		// The label sits inside an inner `<span>{label}</span>`; the styled
		// wrapper (carrying the active vs idle classes) is its parent.
		const tile = screen.getByText('Toolkits');
		const styled = tile.parentElement;
		expect(styled?.className ?? '').toMatch(/text-foreground/);
	});
});
