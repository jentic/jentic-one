import { beforeEach, describe, expect, it } from 'vitest';
import { http, HttpResponse } from 'msw';
import { worker } from '@/mocks/browser';
import { renderWithProviders, screen, waitFor } from '@/__tests__/test-utils';
import { AuthProvider } from '@/shared/auth/AuthContext';
import { clearToken } from '@/shared/api';
import { App } from '@/App';

/**
 * SSO (external-IdP) login: the "Continue with Google" entry point and the
 * authorization-code callback that adopts a session. The full OIDC round-trip
 * (redirect to Google, upstream exchange, admission policy) is backend-owned and
 * covered by `tests/web/auth/test_oidc_login_flow.py`; these specs pin the
 * SPA-side behaviour only.
 *
 * The verifier lives in sessionStorage under this key (see auth/pkce.ts); the
 * callback test seeds it directly to simulate a prior `/authorize` redirect.
 */
const PKCE_VERIFIER_KEY = 'jentic-one.pkce_verifier';

function renderApp(route: string) {
	return renderWithProviders(
		<AuthProvider>
			<App />
		</AuthProvider>,
		{ route },
	);
}

function enableGoogleIdp() {
	worker.use(
		http.get('/auth/idp', () => HttpResponse.json({ enabled: true, provider: 'google' })),
	);
}

describe('SSO login', () => {
	beforeEach(() => {
		clearToken();
		sessionStorage.clear();
	});

	it('hides the SSO button when the backend advertises no IdP', async () => {
		// Default /auth/idp handler reports enabled:false.
		renderApp('/login');
		await screen.findByRole('heading', { name: 'Sign in to Jentic One' });
		expect(screen.queryByRole('button', { name: /continue with/i })).not.toBeInTheDocument();
	});

	it('shows a "Continue with Google" button when Google IdP is enabled', async () => {
		enableGoogleIdp();
		renderApp('/login');
		await screen.findByRole('heading', { name: 'Sign in to Jentic One' });
		expect(await screen.findByRole('button', { name: 'Continue with Google' })).toBeVisible();
		// Password login stays available alongside SSO.
		expect(screen.getByRole('button', { name: 'Sign in' })).toBeVisible();
	});

	it('exchanges the callback code for a session and lands on the dashboard', async () => {
		sessionStorage.setItem(PKCE_VERIFIER_KEY, 'test-verifier');
		let exchanged = false;
		worker.use(
			http.post('/oauth/token', async ({ request }) => {
				const body = (await request.json()) as Record<string, unknown>;
				expect(body.grant_type).toBe('authorization_code');
				expect(body.code).toBe('platform-code-xyz');
				expect(body.code_verifier).toBe('test-verifier');
				exchanged = true;
				return HttpResponse.json({
					access_token: 'sso-access-token',
					token_type: 'bearer',
					expires_in: 3600,
				});
			}),
			http.get('/users/me', ({ request }) => {
				if (request.headers.get('authorization') !== 'Bearer sso-access-token') {
					return new HttpResponse(null, { status: 401 });
				}
				return HttpResponse.json({
					id: '1',
					email: 'ada@jentic.com',
					first_name: 'Ada',
					last_name: 'Lovelace',
					active: true,
					permissions: ['org:admin'],
					must_change_password: false,
					created_at: '2026-01-01T00:00:00Z',
					updated_at: null,
				});
			}),
		);

		renderApp('/auth/callback?code=platform-code-xyz');

		expect(await screen.findByRole('heading', { name: 'Dashboard' })).toBeVisible();
		await waitFor(() => expect(exchanged).toBe(true));
		await waitFor(() =>
			expect(localStorage.getItem('jentic-one.access_token')).toBe('sso-access-token'),
		);
		// The single-use verifier is consumed by the callback.
		expect(sessionStorage.getItem(PKCE_VERIFIER_KEY)).toBeNull();
	});

	it('shows an error with a link back to login when the callback has no code', async () => {
		renderApp('/auth/callback');
		expect(await screen.findByRole('alert')).toHaveTextContent(/invalid or has expired/i);
		expect(screen.getByRole('link', { name: /back to sign in/i })).toBeVisible();
	});

	it('shows an error when the code exchange fails', async () => {
		sessionStorage.setItem(PKCE_VERIFIER_KEY, 'test-verifier');
		worker.use(http.post('/oauth/token', () => new HttpResponse(null, { status: 400 })));

		renderApp('/auth/callback?code=bad-code');

		expect(await screen.findByRole('alert')).toHaveTextContent(/sign-in failed/i);
		expect(localStorage.getItem('jentic-one.access_token')).toBeNull();
	});
});
