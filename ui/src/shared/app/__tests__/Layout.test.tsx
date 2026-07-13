import { describe, expect, it } from 'vitest';
import { renderWithProviders, screen, userEvent } from '@/__tests__/test-utils';
import { Layout } from '@/shared/app/Layout';
import { AuthProvider } from '@/shared/auth/AuthContext';
import { setToken } from '@/shared/api';
import { isNavItemActive, navItems } from '@/shared/app/nav';

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
