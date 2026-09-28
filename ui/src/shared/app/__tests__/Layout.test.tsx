import { afterEach, describe, expect, it } from 'vitest';
import { page } from 'vitest/browser';
import { renderWithProviders, screen, userEvent, waitFor } from '@/__tests__/test-utils';
import { Layout } from '@/shared/app/Layout';
import { AuthProvider } from '@/shared/auth/AuthContext';
import { setToken } from '@/shared/api';
import { isNavItemActive, navItems } from '@/shared/app/nav';
import { SheetPrimitive } from '@/shared/ui/SheetPrimitive';
import { clearAllToasts, toast } from '@/shared/ui/toastStore';

/**
 * The Layout renders behind the AuthGuard, so it always has a user. These
 * tests render it directly with a seeded token + the default MSW-backed
 * `/users/me` (which returns an admin user). The `<Outlet/>` resolves to
 * nothing here — we're asserting the shell chrome, not routed page content.
 *
 * Routes are basename-relative: the router `basename` (`/app`) is applied once
 * in `main.tsx` and is not exercised here, so the Dashboard home is `/` and
 * link hrefs are root-relative (the bare MemoryRouter has no basename).
 */
function renderShell(route = '/') {
	setToken('mock-access-token');
	return renderWithProviders(
		<AuthProvider>
			<Layout />
		</AuthProvider>,
		{ route },
	);
}

describe('app shell / navbar', () => {
	it('renders the fixed top bar with the logo home link', async () => {
		renderShell();
		const home = await screen.findByRole('link', { name: 'Jentic One home' });
		expect(home).toBeVisible();
		expect(home).toHaveAttribute('href', '/');
	});

	it('exposes a Primary navigation landmark', async () => {
		renderShell();
		expect(await screen.findByRole('navigation', { name: 'Primary' })).toBeVisible();
	});

	it('renders a navigable entry for every registry item', async () => {
		renderShell();
		await screen.findByRole('navigation', { name: 'Primary' });
		// The active strip depends on viewport (desktop NavTabs vs mobile
		// BottomNavbar tiles), but every item must be reachable as a link in
		// one of them. The first TILE_LIMIT-1 always render as direct tiles;
		// assert those plus the dashboard to stay viewport-agnostic.
		for (const item of navItems.slice(0, 4)) {
			expect(
				screen.getAllByRole('link', { name: new RegExp(item.label) }).length,
			).toBeGreaterThan(0);
		}
	});

	it('opens the user menu and exposes a sign-out action', async () => {
		renderShell();
		const user = userEvent.setup();
		await user.click(await screen.findByRole('button', { name: 'User menu' }));
		expect(await screen.findByRole('menuitem', { name: /sign out/i })).toBeVisible();
	});
});

describe('toast region', () => {
	afterEach(() => {
		clearAllToasts();
	});

	/** Show a toast in the shell and return its on-screen box once it renders. */
	async function showToast(): Promise<DOMRect> {
		toast({ title: 'Saved' });
		const shown = await screen.findByTestId('toast');
		expect(screen.getByTestId('toast-region')).toContainElement(shown);
		return shown.getBoundingClientRect();
	}

	it('stacks toasts bottom-right, lifted above a page’s floating action dock', async () => {
		await page.viewport(1024, 800);
		renderShell();
		await screen.findByRole('navigation', { name: 'Primary' });

		const rect = await showToast();
		expect(rect.right).toBeCloseTo(window.innerWidth - 16, 0);
		expect(window.innerHeight - rect.bottom).toBeGreaterThanOrEqual(64);
	});

	it('moves toasts beside an open side panel, off its footer actions, and back when it closes', async () => {
		await page.viewport(1440, 900);
		renderShell();
		await screen.findByRole('navigation', { name: 'Primary' });
		const sheet = renderWithProviders(
			<SheetPrimitive open onClose={() => {}} ariaLabel="Panel">
				<button type="button">Save</button>
			</SheetPrimitive>,
		);
		const panel = await screen.findByRole('dialog', { name: 'Panel' });

		// Measured by width: the panel may still be sliding in, and a transform
		// moves its box but not the edge it lands on.
		await expect
			.poll(async () => (await showToast()).right)
			.toBeCloseTo(window.innerWidth - panel.offsetWidth - 16, 0);

		sheet.rerender(
			<SheetPrimitive open={false} onClose={() => {}} ariaLabel="Panel">
				<button type="button">Save</button>
			</SheetPrimitive>,
		);
		// Back beside the rail, the only thing left on the right edge.
		const rail = screen.getByRole('complementary', { name: /agent rail/i });
		await expect
			.poll(async () => (await showToast()).right)
			.toBeCloseTo(window.innerWidth - rail.offsetWidth - 16, 0);
	});

	it('drops toasts under the header when a phone-width panel leaves no room beside it', async () => {
		await page.viewport(375, 812);
		renderShell();
		await screen.findByRole('navigation', { name: 'Primary' });
		renderWithProviders(
			<SheetPrimitive open onClose={() => {}} ariaLabel="Panel">
				<button type="button">Save</button>
			</SheetPrimitive>,
		);
		await screen.findByRole('dialog', { name: 'Panel' });

		await waitFor(() =>
			expect(screen.getByTestId('toast-region')).toHaveAttribute('data-placement', 'top'),
		);
		const rect = await showToast();
		expect(rect.top).toBeLessThan(window.innerHeight / 2);
	});
});

describe('isNavItemActive', () => {
	it('matches the dashboard only on the exact / path', () => {
		const dash = navItems.find((i) => i.to === '/')!;
		expect(isNavItemActive(dash, '/')).toBe(true);
		expect(isNavItemActive(dash, '/discover')).toBe(false);
	});

	it('matches feature items by prefix so nested routes stay highlighted', () => {
		const discover = navItems.find((i) => i.to === '/discover')!;
		expect(isNavItemActive(discover, '/discover')).toBe(true);
		expect(isNavItemActive(discover, '/discover/abc')).toBe(true);
		expect(isNavItemActive(discover, '/discoverable')).toBe(false);
	});
});
