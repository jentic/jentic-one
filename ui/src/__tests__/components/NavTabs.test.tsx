import { screen, renderWithProviders, userEvent } from '../test-utils';
import { NavTabs } from '@/components/layout/NavTabs';
import { NAV_ITEMS } from '@/components/layout/navbar.constants';

describe('NavTabs', () => {
	it('always renders the first nav item as a primary tab', () => {
		renderWithProviders(<NavTabs />);

		// Width-bound by the parent container (jsdom/CI gives a narrow
		// viewport), so only the FIRST item is guaranteed to render as a
		// primary tab — the rest live inside the closed More dropdown until
		// the user opens it. Asserting only the first label keeps the test
		// robust across CI widths.
		expect(document.body.textContent).toContain(NAV_ITEMS[0].label);
	});

	it('marks the active tab when the route matches', async () => {
		const user = userEvent.setup();
		// Render at a constrained width so Toolkits ends up either as the
		// second visible tab or inside the More dropdown — either way we
		// open the dropdown first so the styled Toolkits row is mounted.
		renderWithProviders(<NavTabs />, { route: '/toolkits' });

		const moreButtons = screen.queryAllByRole('button', { name: /^more$/i });
		if (moreButtons.length > 0) await user.click(moreButtons[0]);

		const toolkits = screen.getAllByText('Toolkits')[0];
		// The active indicator lives somewhere in the element's ancestor
		// chain. For primary NavTab rows the styled wrapper is the parent
		// <span>; for More menu rows it's the <AppLink> (rendered as <a>)
		// which carries the menuItemClass. Walk up to find the ancestor
		// with `text-foreground` (or check the element itself for menu items).
		const styled = toolkits.closest('[class*="text-foreground"]');
		expect(styled).not.toBeNull();
	});

	describe('overflow into "More" dropdown', () => {
		// Render NavTabs inside a tiny container so the measurement loop is
		// forced to push everything except the first item into overflow.
		const renderTiny = (route = '/') =>
			renderWithProviders(
				<div style={{ width: 120 }}>
					<NavTabs />
				</div>,
				{ route },
			);

		it('shows a "More" button when items overflow', async () => {
			renderTiny();
			expect(await screen.findByRole('button', { name: /more/i })).toBeInTheDocument();
		});

		it('opens the overflow menu on click and lists overflow items', async () => {
			const user = userEvent.setup();
			renderTiny();

			const moreButton = await screen.findByRole('button', { name: /more/i });
			await user.click(moreButton);

			const menu = await screen.findByRole('menu');
			expect(menu).toBeInTheDocument();
			// All NAV_ITEMS beyond the first should appear inside the menu.
			for (const item of NAV_ITEMS.slice(1)) {
				expect(menu.textContent).toContain(item.label);
			}
		});

		it('closes the overflow menu when clicking outside', async () => {
			const user = userEvent.setup();
			renderTiny();

			await user.click(await screen.findByRole('button', { name: /more/i }));
			expect(screen.queryByRole('menu')).toBeInTheDocument();

			// Click on a non-nav element.
			await user.click(document.body);

			expect(screen.queryByRole('menu')).not.toBeInTheDocument();
		});

		it('closes the overflow menu when an item is selected', async () => {
			const user = userEvent.setup();
			renderTiny();

			await user.click(await screen.findByRole('button', { name: /more/i }));
			const menu = await screen.findByRole('menu');

			const firstOverflowItem = NAV_ITEMS[1];
			const link = screen.getByRole('menuitem', {
				name: new RegExp(firstOverflowItem.label, 'i'),
			});
			await user.click(link);

			expect(screen.queryByRole('menu')).not.toBeInTheDocument();
			expect(menu).not.toBeInTheDocument();
		});
	});
});
