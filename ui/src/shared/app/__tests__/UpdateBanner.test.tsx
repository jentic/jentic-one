import { describe, it, expect, beforeEach } from 'vitest';
import { http, HttpResponse } from 'msw';
import { worker } from '@/mocks/browser';
import { renderWithProviders, screen, waitFor, userEvent } from '@/__tests__/test-utils';
import { UpdateBanner } from '@/shared/app/UpdateBanner';

const updateAvailable = (latest = '0.27.0') =>
	http.get('/system/version', () =>
		HttpResponse.json({ current: '0.26.0', latest, update_available: true }),
	);

describe('UpdateBanner', () => {
	beforeEach(() => {
		window.localStorage.clear();
	});

	it('shows when a newer release is available', async () => {
		worker.use(updateAvailable('0.27.0'));
		renderWithProviders(<UpdateBanner />);
		const banner = await screen.findByRole('status');
		expect(banner).toHaveTextContent('jentic-one v0.27.0 is available');
		expect(screen.getByText('jenticctl update')).toBeVisible();
	});

	it('stays hidden when no update is available', async () => {
		// Default root handler reports no update.
		const { container } = renderWithProviders(<UpdateBanner />);
		// Give the query a tick; the banner must never appear.
		await waitFor(() => expect(container).toBeTruthy());
		expect(screen.queryByRole('status')).toBeNull();
	});

	it('dismisses and persists the dismissed version', async () => {
		worker.use(updateAvailable('0.27.0'));
		const user = userEvent.setup();
		renderWithProviders(<UpdateBanner />);
		await screen.findByRole('status');
		await user.click(screen.getByRole('button', { name: /dismiss/i }));
		await waitFor(() => expect(screen.queryByRole('status')).toBeNull());
		expect(window.localStorage.getItem('j1.updateBanner.dismissedVersion')).toBe('0.27.0');
	});

	it('re-shows when a newer release supersedes the dismissed one', async () => {
		window.localStorage.setItem('j1.updateBanner.dismissedVersion', '0.27.0');
		worker.use(updateAvailable('0.28.0'));
		renderWithProviders(<UpdateBanner />);
		// 0.28.0 !== dismissed 0.27.0, so the banner returns.
		const banner = await screen.findByRole('status');
		expect(banner).toHaveTextContent('jentic-one v0.28.0 is available');
	});

	it('stays hidden for an already-dismissed version', async () => {
		window.localStorage.setItem('j1.updateBanner.dismissedVersion', '0.27.0');
		worker.use(updateAvailable('0.27.0'));
		const { container } = renderWithProviders(<UpdateBanner />);
		await waitFor(() => expect(container).toBeTruthy());
		expect(screen.queryByRole('status')).toBeNull();
	});
});
