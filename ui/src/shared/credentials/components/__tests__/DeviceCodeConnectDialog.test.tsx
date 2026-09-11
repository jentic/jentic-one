import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { renderWithProviders, screen, userEvent } from '@/__tests__/test-utils';
import { DeviceCodeConnectDialog } from '@/shared/credentials/components/DeviceCodeConnectDialog';
import type { DeviceCodeChallengeResponse } from '@/shared/credentials/api/types';

/**
 * The device-code dialog is display-only: the ConnectPollScanner drives
 * completion server-side. What matters here is that (1) the user_code is
 * visible + copyable, (2) the verification URI opens in a *new tab* not
 * this window (opening in-place would kill the polling SPA), and (3) the
 * Cancel affordance actually notifies the caller. Losing any of these
 * strands the human without a way to complete or abandon the flow.
 */
describe('DeviceCodeConnectDialog', () => {
	const challenge: DeviceCodeChallengeResponse = {
		kind: 'device_code',
		user_code: 'ABCD-1234',
		verification_uri: 'https://idp.example.com/device',
		verification_uri_complete: 'https://idp.example.com/device?user_code=ABCD-1234',
		poll_interval_seconds: 5,
	};

	beforeEach(() => {
		// window.open is spied per-test so we can assert the opener
		// contract (new tab with noopener,noreferrer) without pop-ups
		// actually being spawned during the test run.
		vi.spyOn(window, 'open').mockReturnValue(null);
	});

	afterEach(() => {
		vi.restoreAllMocks();
	});

	it('renders the user_code prominently when open', () => {
		renderWithProviders(
			<DeviceCodeConnectDialog
				open
				challenge={challenge}
				credentialName="GitHub"
				onCancel={vi.fn()}
			/>,
		);
		// Wrapped in <code> for the visual treatment; assert it's on the
		// page as text since a user needs to read it and type it into the
		// vendor page.
		expect(screen.getByText('ABCD-1234')).toBeInTheDocument();
		// The dialog title carries the credential name so a user with
		// multiple in-flight connects can tell which one they're
		// approving.
		expect(screen.getByText('Approve GitHub')).toBeInTheDocument();
	});

	it('renders nothing when challenge is null', () => {
		// The dialog is unmounted, not just hidden, when there's nothing
		// to render — a shell showing "Approve " with no code would be
		// worse UX than no dialog at all.
		const { container } = renderWithProviders(
			<DeviceCodeConnectDialog
				open
				challenge={null}
				credentialName="GitHub"
				onCancel={vi.fn()}
			/>,
		);
		expect(container.textContent).toBe('');
	});

	it('opens the verification URI in a new tab (noopener,noreferrer)', async () => {
		// Opening in the current tab would navigate the polling SPA away
		// from the dialog — we'd lose the whole reason we mounted a
		// browser-driven poller. noopener/noreferrer are the standard
		// hardening flags to prevent the new page from reaching back into
		// window.opener.
		renderWithProviders(
			<DeviceCodeConnectDialog
				open
				challenge={challenge}
				credentialName="GitHub"
				onCancel={vi.fn()}
			/>,
		);
		const user = userEvent.setup();
		await user.click(screen.getByRole('button', { name: /open vendor sign-in/i }));
		expect(window.open).toHaveBeenCalledWith(
			'https://idp.example.com/device?user_code=ABCD-1234',
			'_blank',
			'noopener,noreferrer',
		);
	});

	it('falls back to verification_uri when verification_uri_complete is absent', async () => {
		// Some vendors don't emit verification_uri_complete (the pre-filled
		// URL); the plain URI is the RFC 8628-required fallback. Regressing
		// this makes those vendors' flows look broken to the user.
		renderWithProviders(
			<DeviceCodeConnectDialog
				open
				challenge={{ ...challenge, verification_uri_complete: null }}
				credentialName="GitHub"
				onCancel={vi.fn()}
			/>,
		);
		const user = userEvent.setup();
		await user.click(screen.getByRole('button', { name: /open vendor sign-in/i }));
		expect(window.open).toHaveBeenCalledWith(
			'https://idp.example.com/device',
			'_blank',
			'noopener,noreferrer',
		);
	});

	it('invokes onCancel when the Cancel button is clicked', async () => {
		const onCancel = vi.fn();
		renderWithProviders(
			<DeviceCodeConnectDialog
				open
				challenge={challenge}
				credentialName="GitHub"
				onCancel={onCancel}
			/>,
		);
		const user = userEvent.setup();
		await user.click(screen.getByRole('button', { name: /cancel/i }));
		expect(onCancel).toHaveBeenCalledTimes(1);
	});
});
