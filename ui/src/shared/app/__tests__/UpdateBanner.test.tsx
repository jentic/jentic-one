import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { http, HttpResponse } from 'msw';
import { worker } from '@/mocks/browser';
import { renderWithProviders, screen, waitFor, userEvent } from '@/__tests__/test-utils';
import { UpdateBanner } from '@/shared/app/UpdateBanner';
import { AuthProvider } from '@/shared/auth/AuthContext';
import { setToken, clearToken } from '@/shared/api';

const updateAvailable = (latest = '0.27.0') =>
	http.get('/system/version', () =>
		HttpResponse.json({ current: '0.26.0', latest, update_available: true }),
	);

// The banner is admin-only, so render it under an authenticated session — the
// default `/users/me` mock returns an org:admin user.
const renderBanner = () =>
	renderWithProviders(
		<AuthProvider>
			<UpdateBanner />
		</AuthProvider>,
	);

describe('UpdateBanner', () => {
	beforeEach(() => {
		window.localStorage.clear();
		setToken('mock-access-token');
	});
	afterEach(() => clearToken());

	it('shows when a newer release is available', async () => {
		worker.use(updateAvailable('0.27.0'));
		renderBanner();
		const banner = await screen.findByRole('status');
		expect(banner).toHaveTextContent('jentic-one v0.27.0 is available');
		expect(screen.getByText('jenticctl update')).toBeVisible();
	});

	it('stays hidden when no update is available', async () => {
		// Default root handler reports no update.
		const { container } = renderBanner();
		// Give the query a tick; the banner must never appear.
		await waitFor(() => expect(container).toBeTruthy());
		expect(screen.queryByRole('status')).toBeNull();
	});

	it('dismisses and persists the dismissed version', async () => {
		worker.use(updateAvailable('0.27.0'));
		const user = userEvent.setup();
		renderBanner();
		await screen.findByRole('status');
		await user.click(screen.getByRole('button', { name: /dismiss/i }));
		await waitFor(() => expect(screen.queryByRole('status')).toBeNull());
		expect(window.localStorage.getItem('j1.updateBanner.dismissedVersion')).toBe('0.27.0');
	});

	it('re-shows when a newer release supersedes the dismissed one', async () => {
		window.localStorage.setItem('j1.updateBanner.dismissedVersion', '0.27.0');
		worker.use(updateAvailable('0.28.0'));
		renderBanner();
		// 0.28.0 !== dismissed 0.27.0, so the banner returns.
		const banner = await screen.findByRole('status');
		expect(banner).toHaveTextContent('jentic-one v0.28.0 is available');
	});

	it('stays hidden for an already-dismissed version', async () => {
		window.localStorage.setItem('j1.updateBanner.dismissedVersion', '0.27.0');
		worker.use(updateAvailable('0.27.0'));
		const { container } = renderBanner();
		await waitFor(() => expect(container).toBeTruthy());
		expect(screen.queryByRole('status')).toBeNull();
	});

	it('stays hidden for a non-admin user even when an update is available', async () => {
		worker.use(
			updateAvailable('0.27.0'),
			http.get('/users/me', () =>
				HttpResponse.json({
					id: 'usr_member',
					email: 'member@test.local',
					first_name: 'Mem',
					last_name: 'Ber',
					permissions: [],
					must_change_password: false,
					created_at: '2026-01-01T00:00:00Z',
					updated_at: null,
				}),
			),
		);
		const { container } = renderBanner();
		await waitFor(() => expect(container).toBeTruthy());
		expect(screen.queryByRole('status')).toBeNull();
	});
});
