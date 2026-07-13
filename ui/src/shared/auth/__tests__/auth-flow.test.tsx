import { beforeEach, describe, expect, it } from 'vitest';
import { http, HttpResponse } from 'msw';
import { worker } from '@/mocks/browser';
import { renderWithProviders, screen, userEvent, waitFor, checkA11y } from '@/__tests__/test-utils';
import { AuthProvider } from '@/shared/auth/AuthContext';
import { clearToken, setToken } from '@/shared/api';
import { App } from '@/App';

function renderApp(route: string) {
	return renderWithProviders(
		<AuthProvider>
			<App />
		</AuthProvider>,
		{ route },
	);
}

// Routes are basename-relative (the `/app` basename is applied once in
// `main.tsx`; the bare test MemoryRouter has none). The authenticated shell
// home is `/`, login is `/login`.

describe('auth flow', () => {
	beforeEach(() => {
		clearToken();
	});

	it('redirects an unauthenticated visit to the login screen', async () => {
		renderApp('/');
		expect(await screen.findByRole('heading', { name: 'Sign in to Jentic One' })).toBeVisible();
	});

	it('logs in and lands on the dashboard shell', async () => {
		renderApp('/login');
		const user = userEvent.setup();

		// SetupGate resolves the health probe before revealing the form.
		await user.type(await screen.findByLabelText('Email'), 'admin@local');
		await user.type(screen.getByLabelText('Password'), 'password');
		await user.click(screen.getByRole('button', { name: 'Sign in' }));

		expect(await screen.findByRole('heading', { name: 'Dashboard' })).toBeVisible();
		expect(screen.getByRole('navigation', { name: 'Primary' })).toBeVisible();
	});

	it('shows an error on bad credentials', async () => {
		worker.use(http.post('/auth/login', () => new HttpResponse(null, { status: 401 })));
		renderApp('/login');
		const user = userEvent.setup();

		await user.type(await screen.findByLabelText('Email'), 'admin@local');
		await user.type(screen.getByLabelText('Password'), 'wrong');
		await user.click(screen.getByRole('button', { name: 'Sign in' }));

		expect(await screen.findByRole('alert')).toHaveTextContent(/incorrect/i);
	});

	it('forces the password-change gate when must_change_password is set', async () => {
		setToken('mock-access-token');
		worker.use(
			http.get('/users/me', () =>
				HttpResponse.json({
					id: '1',
					email: 'admin@local',
					first_name: 'Admin',
					last_name: 'User',
					active: true,
					permissions: ['org:admin'],
					must_change_password: true,
					created_at: '2026-01-01T00:00:00Z',
					updated_at: null,
				}),
			),
		);
		renderApp('/');
		expect(await screen.findByRole('heading', { name: 'Set a new password' })).toBeVisible();
	});

	it('rotates the password, adopts the re-minted token, and enters the app', async () => {
		setToken('stale-token-n-true');
		// The held token reports must_change_password: true. Rotation re-mints a
		// fresh token (n=false) and returns it; we adopt it, so the subsequent
		// /users/me must report the gate cleared. Flip it once rotation has run.
		let rotated = false;
		worker.use(
			http.post('/users/me:change-password', () => {
				rotated = true;
				return HttpResponse.json({
					access_token: 'fresh-token-n-false',
					token_type: 'bearer',
					expires_in: 3600,
					must_change_password: false,
				});
			}),
			http.get('/users/me', () =>
				HttpResponse.json({
					id: '1',
					email: 'admin@local',
					first_name: 'Admin',
					last_name: 'User',
					active: true,
					permissions: ['org:admin'],
					must_change_password: !rotated,
					created_at: '2026-01-01T00:00:00Z',
					updated_at: null,
				}),
			),
		);
		renderApp('/');
		const user = userEvent.setup();

		await screen.findByRole('heading', { name: 'Set a new password' });
		await user.type(screen.getByLabelText('Current password'), '1234');
		await user.type(screen.getByLabelText('New password'), 'a-strong-passw0rd');
		await user.type(screen.getByLabelText('Confirm new password'), 'a-strong-passw0rd');
		await user.click(screen.getByRole('button', { name: 'Set password' }));

		expect(await screen.findByRole('heading', { name: 'Dashboard' })).toBeVisible();
		await waitFor(() =>
			expect(localStorage.getItem('jentic-one.access_token')).toBe('fresh-token-n-false'),
		);
	});

	it('keeps the held token and surfaces an error when the current password is wrong', async () => {
		setToken('stale-token-n-true');
		worker.use(
			// 401 invalid_credentials — the current password didn't match. The gate
			// stays up and the existing token is untouched (no half-applied session).
			http.post('/users/me:change-password', () => new HttpResponse(null, { status: 401 })),
			http.get('/users/me', () =>
				HttpResponse.json({
					id: '1',
					email: 'admin@local',
					first_name: 'Admin',
					last_name: 'User',
					active: true,
					permissions: ['org:admin'],
					must_change_password: true,
					created_at: '2026-01-01T00:00:00Z',
					updated_at: null,
				}),
			),
		);
		renderApp('/');
		const user = userEvent.setup();

		await screen.findByRole('heading', { name: 'Set a new password' });
		await user.type(screen.getByLabelText('Current password'), 'wrong');
		await user.type(screen.getByLabelText('New password'), 'a-strong-passw0rd');
		await user.type(screen.getByLabelText('Confirm new password'), 'a-strong-passw0rd');
		await user.click(screen.getByRole('button', { name: 'Set password' }));

		expect(await screen.findByRole('alert')).toHaveTextContent(/incorrect/i);
		expect(localStorage.getItem('jentic-one.access_token')).toBe('stale-token-n-true');
	});

	it('surfaces a policy error (not "incorrect") when the new password is rejected (400)', async () => {
		setToken('stale-token-n-true');
		worker.use(
			// 400 invalid_input — the NEW password failed the server policy. Blaming
			// the current password would be misleading; show a requirements message.
			http.post('/users/me:change-password', () => new HttpResponse(null, { status: 400 })),
			http.get('/users/me', () =>
				HttpResponse.json({
					id: '1',
					email: 'admin@local',
					first_name: 'Admin',
					last_name: 'User',
					active: true,
					permissions: ['org:admin'],
					must_change_password: true,
					created_at: '2026-01-01T00:00:00Z',
					updated_at: null,
				}),
			),
		);
		renderApp('/app');
		const user = userEvent.setup();

		await screen.findByRole('heading', { name: 'Set a new password' });
		await user.type(screen.getByLabelText('Current password'), 'right-password');
		await user.type(screen.getByLabelText('New password'), 'a-strong-passw0rd');
		await user.type(screen.getByLabelText('Confirm new password'), 'a-strong-passw0rd');
		await user.click(screen.getByRole('button', { name: 'Set password' }));

		const alert = await screen.findByRole('alert');
		expect(alert).toHaveTextContent(/requirements/i);
		expect(alert).not.toHaveTextContent(/incorrect/i);
		expect(localStorage.getItem('jentic-one.access_token')).toBe('stale-token-n-true');
	});

	it('login screen has no critical a11y violations', async () => {
		const { container } = renderApp('/login');
		await screen.findByRole('heading', { name: 'Sign in to Jentic One' });
		await checkA11y(container);
	});

	it('setup screen has no critical a11y violations', async () => {
		worker.use(
			http.get('/admin/health', () =>
				HttpResponse.json({
					status: 'ok',
					surface: 'admin',
					setup_required: true,
					next_step: 'create_admin',
				}),
			),
		);
		const { container } = renderApp('/setup');
		await screen.findByRole('heading', { name: 'Welcome to Jentic One' });
		await checkA11y(container);
	});

	it('change-password gate screen has no critical a11y violations', async () => {
		setToken('mock-access-token');
		worker.use(
			http.get('/users/me', () =>
				HttpResponse.json({
					id: '1',
					email: 'admin@local',
					first_name: 'Admin',
					last_name: 'User',
					active: true,
					permissions: ['org:admin'],
					must_change_password: true,
					created_at: '2026-01-01T00:00:00Z',
					updated_at: null,
				}),
			),
		);
		const { container } = renderApp('/app');
		await screen.findByRole('heading', { name: 'Set a new password' });
		await checkA11y(container);
	});

	it('drops a rejected token and falls back to login', async () => {
		setToken('stale-token');
		worker.use(http.get('/users/me', () => new HttpResponse(null, { status: 401 })));
		renderApp('/');
		expect(await screen.findByRole('heading', { name: 'Sign in to Jentic One' })).toBeVisible();
		await waitFor(() => expect(localStorage.getItem('jentic-one.access_token')).toBeNull());
	});

	it('routes a first-run visit (setup_required) to the setup screen', async () => {
		worker.use(
			http.get('/admin/health', () =>
				HttpResponse.json({
					status: 'ok',
					surface: 'admin',
					setup_required: true,
					next_step: 'create_admin',
				}),
			),
		);
		// Even though the visitor asked for /login, SetupGate steers to /setup
		// because no account exists yet.
		renderApp('/login');
		expect(await screen.findByRole('heading', { name: 'Welcome to Jentic One' })).toBeVisible();
	});

	it('creates the first admin and lands authenticated on the dashboard', async () => {
		let created = false;
		worker.use(
			http.get('/admin/health', () =>
				HttpResponse.json({
					status: 'ok',
					surface: 'admin',
					setup_required: !created,
					next_step: created ? null : 'create_admin',
				}),
			),
			http.post('/users:create-admin', () => {
				created = true;
				return HttpResponse.json({
					access_token: 'first-admin-token',
					token_type: 'bearer',
					expires_in: 3600,
					must_change_password: false,
				});
			}),
			http.get('/users/me', () =>
				HttpResponse.json({
					id: '1',
					email: 'founder@local',
					first_name: 'Admin',
					last_name: 'User',
					active: true,
					permissions: ['org:admin'],
					must_change_password: false,
					created_at: '2026-01-01T00:00:00Z',
					updated_at: null,
				}),
			),
		);
		renderApp('/setup');
		const user = userEvent.setup();

		await user.type(await screen.findByLabelText('Email'), 'founder@local');
		await user.type(screen.getByLabelText('Password'), 'a-strong-passw0rd');
		await user.type(screen.getByLabelText('Confirm password'), 'a-strong-passw0rd');
		await user.click(screen.getByRole('button', { name: 'Create admin account' }));

		expect(await screen.findByRole('heading', { name: 'Dashboard' })).toBeVisible();
		await waitFor(() =>
			expect(localStorage.getItem('jentic-one.access_token')).toBe('first-admin-token'),
		);
	});

	it('bounces /setup to /login once setup is already complete', async () => {
		// Default health handler reports setup_required: false.
		renderApp('/setup');
		expect(await screen.findByRole('heading', { name: 'Sign in to Jentic One' })).toBeVisible();
	});

	// The OAuth connect popup is redirected here by the backend callback
	// (`GET /credentials/oauth/callback` → `<SPA_MOUNT_PATH>/oauth/connected`).
	// The two halves of that contract must stay in lockstep: the backend test
	// `test_oauth_callback_router.py` pins the redirect target is derived from
	// `SPA_MOUNT_PATH`; this pins the matching public SPA route exists and renders
	// WITHOUT a session (the popup has no guaranteed token). If a refactor drops
	// or guards this route, the popup lands on a 404/login and the connect flow
	// silently breaks — this test fails first.
	it('serves the public OAuth connect-return route with no session (outside the AuthGuard)', async () => {
		// No token set (beforeEach clears it). A guarded route would bounce to
		// login; the popup-return page must render regardless.
		renderApp('/oauth/connected?status=ok');
		expect(await screen.findByRole('heading', { name: 'Sign-in complete' })).toBeVisible();
		expect(
			screen.queryByRole('heading', { name: 'Sign in to Jentic One' }),
		).not.toBeInTheDocument();
	});

	// #594: a signed-in user (NOT under the forced gate) can voluntarily rotate
	// their password from the user menu. The page must read neutrally (not the
	// forced "you must change" copy) and offer a Cancel that returns to the app
	// rather than bouncing back through the AuthGuard gate.
	it('opens voluntary change-password from the user menu and cancels back to the app', async () => {
		setToken('mock-access-token');
		// Default /users/me handler reports must_change_password: false, so the
		// AuthGuard lets us into the shell and the page renders in voluntary mode.
		renderApp('/');
		const user = userEvent.setup();

		await screen.findByRole('heading', { name: 'Dashboard' });
		await user.click(screen.getByRole('button', { name: 'User menu' }));
		await user.click(screen.getByRole('menuitem', { name: 'Change password' }));

		// Voluntary copy — never the forced-gate wording.
		expect(await screen.findByRole('heading', { name: 'Change your password' })).toBeVisible();
		expect(screen.queryByText(/you must change your password/i)).not.toBeInTheDocument();

		// Cancel returns to the dashboard (no forced-gate bounce).
		await user.click(screen.getByRole('button', { name: 'Cancel' }));
		expect(await screen.findByRole('heading', { name: 'Dashboard' })).toBeVisible();
	});

	it('shows setup already complete and routes to login on a 410 race', async () => {
		// A concurrent operator won the race: the one-time endpoint self-closed,
		// so an admin now exists and health flips to setup_required: false.
		let raceLost = false;
		worker.use(
			http.get('/admin/health', () =>
				HttpResponse.json({
					status: 'ok',
					surface: 'admin',
					setup_required: !raceLost,
					next_step: raceLost ? null : 'create_admin',
				}),
			),
			http.post('/users:create-admin', () => {
				raceLost = true;
				return new HttpResponse(null, { status: 410 });
			}),
		);
		renderApp('/setup');
		const user = userEvent.setup();

		await user.type(await screen.findByLabelText('Email'), 'founder@local');
		await user.type(screen.getByLabelText('Password'), 'a-strong-passw0rd');
		await user.type(screen.getByLabelText('Confirm password'), 'a-strong-passw0rd');
		await user.click(screen.getByRole('button', { name: 'Create admin account' }));

		expect(await screen.findByRole('heading', { name: 'Sign in to Jentic One' })).toBeVisible();
	});
});
