/**
 * SettingsPage layout pins:
 *
 *  - the section nav is RESPONSIVE: a persistent side column at `md+`
 *    (Button-based, `border-r` stretching with the flex row), collapsing to
 *    the platform's horizontal `TabNav` grammar on phones — a hard `w-56`
 *    column would eat half of a 375px screen;
 *  - the sidebar row fills the Layout content column (`min-h-[calc(100dvh-…)]`
 *    on the shell) so the border reaches the viewport bottom on short content
 *    while long content still scrolls with the DOCUMENT (no nested scroll
 *    container);
 *  - no horizontal overflow at phone width.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { page } from 'vitest/browser';
import { renderWithProviders, screen, checkA11y } from '@/__tests__/test-utils';
import { setToken } from '@/shared/api';
import { resetSettingsStore } from '@/modules/settings/mocks/handlers';
import { SettingsPage } from '@/modules/settings/pages/SettingsPage';

describe('SettingsPage', () => {
	beforeEach(() => {
		setToken('test-token');
		resetSettingsStore();
	});

	it('renders the section nav as a side column on desktop, not tabs', async () => {
		await page.viewport(1280, 900);
		renderWithProviders(<SettingsPage />, { route: '/settings' });
		await screen.findByText('Internal Dashboard');

		// The md+ grammar: sidebar buttons inside the "Settings sections" nav…
		expect(screen.getByRole('button', { name: 'Developer Settings' })).toBeInTheDocument();
		// …while the mobile TabNav is display:none (role queries skip hidden).
		expect(screen.queryByRole('tab', { name: 'Developer Settings' })).not.toBeInTheDocument();
	});

	it('collapses the section nav to horizontal tabs at phone width, without overflow', async () => {
		await page.viewport(375, 812);
		const { container } = renderWithProviders(<SettingsPage />, { route: '/settings' });
		await screen.findByText('Internal Dashboard');

		// The phone grammar: a TabNav above the content…
		expect(screen.getByRole('tab', { name: 'Developer Settings' })).toBeInTheDocument();
		// …and no side column eating half the screen.
		expect(
			screen.queryByRole('button', { name: 'Developer Settings' }),
		).not.toBeInTheDocument();

		// The whole surface (header, tabs, roster cards) fits 375px — the
		// document must not scroll sideways.
		expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(window.innerWidth);

		await checkA11y(container);
	});
});
