import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, checkA11y, renderWithProviders, screen } from '@/__tests__/test-utils';
import { OAuthPopupReturn } from '@/shared/auth/OAuthPopupReturn';
import { OAUTH_CONNECT_MESSAGE_TYPE } from '@/modules/credentials/api';

/**
 * The OAuth connect popup is redirected here by the backend callback with a
 * coarse `?status=ok|error`. This page (1) posts an advisory, origin-restricted
 * message to its opener so the parent SPA can stop polling promptly (#598),
 * (2) tries to self-close, and (3) reveals a manual close/return affordance if
 * the close was blocked or there's no opener to close (#601). It never reads
 * anything privileged and the message it posts carries no secrets — the parent
 * SPA still learns the real outcome by polling the credential.
 */
describe('OAuthPopupReturn', () => {
	const originalOpener = window.opener;

	beforeEach(() => {
		// Default: a script-opened popup with a same-origin opener.
		Object.defineProperty(window, 'opener', {
			configurable: true,
			writable: true,
			value: { postMessage: vi.fn() },
		});
	});

	afterEach(() => {
		vi.restoreAllMocks();
		vi.useRealTimers();
		Object.defineProperty(window, 'opener', {
			configurable: true,
			writable: true,
			value: originalOpener,
		});
	});

	it('shows the success copy and closes the window immediately on status=ok', () => {
		vi.useFakeTimers();
		const close = vi.spyOn(window, 'close').mockImplementation(() => {});

		renderWithProviders(<OAuthPopupReturn />, { route: '/oauth/connected?status=ok' });

		expect(screen.getByText('Sign-in complete')).toBeInTheDocument();
		expect(screen.getByText('You can close this window.')).toBeInTheDocument();
		expect(screen.queryByText(/something went wrong/i)).not.toBeInTheDocument();

		// Success closes immediately (0ms timer).
		vi.advanceTimersByTime(0);
		expect(close).toHaveBeenCalledTimes(1);
	});

	it('shows the generic error copy and delays the close on status=error', () => {
		vi.useFakeTimers();
		const close = vi.spyOn(window, 'close').mockImplementation(() => {});

		renderWithProviders(<OAuthPopupReturn />, { route: '/oauth/connected?status=error' });

		expect(screen.getByText('Sign-in failed')).toBeInTheDocument();
		expect(
			screen.getByText('Something went wrong. You can close this window and try again.'),
		).toBeInTheDocument();

		// Does not close immediately — the user gets a moment to read.
		vi.advanceTimersByTime(0);
		expect(close).not.toHaveBeenCalled();

		vi.advanceTimersByTime(5000);
		expect(close).toHaveBeenCalledTimes(1);
	});

	it('treats a missing status as success (no error leak in the happy path)', () => {
		vi.useFakeTimers();
		vi.spyOn(window, 'close').mockImplementation(() => {});

		renderWithProviders(<OAuthPopupReturn />, { route: '/oauth/connected' });

		expect(screen.getByText('Sign-in complete')).toBeInTheDocument();
		expect(screen.queryByText(/something went wrong/i)).not.toBeInTheDocument();
	});

	it('posts an advisory, origin-restricted message to the opener (#598)', () => {
		vi.spyOn(window, 'close').mockImplementation(() => {});
		const post = vi.fn();
		Object.defineProperty(window, 'opener', {
			configurable: true,
			writable: true,
			value: { postMessage: post },
		});

		renderWithProviders(<OAuthPopupReturn />, { route: '/oauth/connected?status=ok' });

		expect(post).toHaveBeenCalledTimes(1);
		// The page posts the SAME literal the credentials module owns — importing
		// the canonical constant here doubles as the cross-file sync pin: if the
		// page's private copy drifts, this assertion fails.
		expect(post).toHaveBeenCalledWith(
			{ type: OAUTH_CONNECT_MESSAGE_TYPE, status: 'ok' },
			// Strict target origin — never '*'.
			window.location.origin,
		);
	});

	it('forwards the error status in the advisory message', () => {
		vi.spyOn(window, 'close').mockImplementation(() => {});
		const post = vi.fn();
		Object.defineProperty(window, 'opener', {
			configurable: true,
			writable: true,
			value: { postMessage: post },
		});

		renderWithProviders(<OAuthPopupReturn />, { route: '/oauth/connected?status=error' });

		expect(post).toHaveBeenCalledWith(
			{ type: OAUTH_CONNECT_MESSAGE_TYPE, status: 'error' },
			window.location.origin,
		);
	});

	it('reveals a manual "Close window" button when the auto-close is blocked (#601)', () => {
		vi.useFakeTimers();
		// close() is a no-op (browser refused) — the window lingers.
		vi.spyOn(window, 'close').mockImplementation(() => {});

		renderWithProviders(<OAuthPopupReturn />, { route: '/oauth/connected?status=ok' });

		// No affordance until the close attempt has been made + the follow-up tick.
		expect(screen.queryByRole('button', { name: /close window/i })).not.toBeInTheDocument();

		act(() => {
			vi.advanceTimersByTime(0); // close() fires
			vi.advanceTimersByTime(150); // lingering-detector tick → setCloseBlocked(true)
		});

		expect(screen.getByRole('button', { name: /close window/i })).toBeInTheDocument();
	});

	it('shows the affordance immediately and a return link when there is no opener (same-tab)', () => {
		vi.spyOn(window, 'close').mockImplementation(() => {});
		Object.defineProperty(window, 'opener', {
			configurable: true,
			writable: true,
			value: null,
		});

		renderWithProviders(<OAuthPopupReturn />, { route: '/oauth/connected?status=ok' });

		// Same-tab: no opener to close us, so the affordance is shown up front.
		expect(screen.getByRole('button', { name: /close window/i })).toBeInTheDocument();
		expect(screen.getByRole('link', { name: /return to credentials/i })).toBeInTheDocument();
	});

	it('does not throw when the opener is gone (best-effort notify)', () => {
		vi.spyOn(window, 'close').mockImplementation(() => {});
		Object.defineProperty(window, 'opener', {
			configurable: true,
			writable: true,
			value: null,
		});

		expect(() =>
			renderWithProviders(<OAuthPopupReturn />, { route: '/oauth/connected?status=ok' }),
		).not.toThrow();
	});

	it('has no critical a11y violations', async () => {
		vi.spyOn(window, 'close').mockImplementation(() => {});
		const { container } = renderWithProviders(<OAuthPopupReturn />, {
			route: '/oauth/connected?status=error',
		});
		await checkA11y(container);
	});
});
