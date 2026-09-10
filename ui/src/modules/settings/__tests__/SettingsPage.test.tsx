/**
 * SettingsPage layout pins — the FLAT platform grammar (per review):
 *
 *  - no left sidebar (it was the SPA's only one, with a single destination)
 *    and no "Developer Settings" register — the page is PageShell +
 *    PageHeader + ONE page-level TabNav, the AgentsPage shape;
 *  - the header actions carry "Add client" + the page help;
 *  - exactly one tab bar (Clients / Approval queue) — no nested tab bars,
 *    no double headers;
 *  - no horizontal overflow at phone width.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { page } from 'vitest/browser';
import { renderWithProviders, screen, waitFor, checkA11y } from '@/__tests__/test-utils';
import { setToken } from '@/shared/api';
import { resetSettingsStore } from '@/modules/settings/mocks/handlers';
import { SettingsPage } from '@/modules/settings/pages/SettingsPage';

/** Wait out PageHeader's fade-in so axe doesn't sample blended colours. */
async function settleHeader(): Promise<void> {
	const h1 = await screen.findByRole('heading', { level: 1, name: 'Settings' });
	const motionEl = h1.closest('div[style]');
	if (!motionEl) return;
	await waitFor(() => expect(getComputedStyle(motionEl).opacity).toBe('1'));
}

describe('SettingsPage', () => {
	beforeEach(() => {
		setToken('test-token');
		resetSettingsStore();
	});

	it('renders the flat page grammar: header actions + a single page-level tab bar', async () => {
		await page.viewport(1280, 900);
		renderWithProviders(<SettingsPage />, { route: '/settings' });
		await screen.findByText('Internal Dashboard');

		// PageHeader is the page title; the old section heading register and
		// the sidebar's "Developer Settings" destination are gone.
		expect(screen.getByRole('heading', { level: 1, name: 'Settings' })).toBeInTheDocument();
		expect(screen.queryByText('Developer Settings')).not.toBeInTheDocument();
		expect(screen.queryByText('OAuth Clients')).not.toBeInTheDocument();

		// ONE tab bar, owned by the page: Clients / Approval queue.
		expect(screen.getAllByRole('tablist')).toHaveLength(1);
		expect(screen.getByRole('tab', { name: /Clients/ })).toBeInTheDocument();
		expect(screen.getByRole('tab', { name: /Approval queue/ })).toBeInTheDocument();

		// The section's actions folded into the header's actions slot.
		expect(screen.getByRole('button', { name: /Add client/ })).toBeInTheDocument();
		expect(
			screen.getByRole('button', { name: 'Help for About OAuth Clients' }),
		).toBeInTheDocument();
	});

	it('fits phone width without a sidebar or horizontal overflow', async () => {
		await page.viewport(375, 812);
		const { container } = renderWithProviders(<SettingsPage />, { route: '/settings' });
		await screen.findByText('Internal Dashboard');

		// Same flat structure at 375px — tabs, not a w-56 column.
		expect(screen.getByRole('tab', { name: /Clients/ })).toBeInTheDocument();
		expect(screen.queryByRole('navigation')).not.toBeInTheDocument();

		// The whole surface (header, tabs, roster cards) fits 375px — the
		// document must not scroll sideways.
		expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(window.innerWidth);

		await settleHeader();
		await checkA11y(container);
	});
});
