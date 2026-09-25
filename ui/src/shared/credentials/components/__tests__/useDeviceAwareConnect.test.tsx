import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { http, HttpResponse } from 'msw';
import { worker } from '@/mocks/browser';
import { renderWithProviders, screen, userEvent, waitFor } from '@/__tests__/test-utils';
import { CredentialType, type ConnectOutcome } from '@/shared/credentials/api';
import { makeMockCredential, resetCredentialsStore } from '@/shared/credentials/mocks/handlers';
import { useDeviceAwareConnect } from '@/shared/credentials/components/useDeviceAwareConnect';

/**
 * Every Agents-surface connect (inventory, access sidebar, setup queue) runs
 * through this hook, so a device-code vendor must get its human step there too —
 * not only on the standalone credentials page. Without it the flow resolves
 * `unsupported_challenge` and the operator is never shown the code.
 */
function Harness({ onOutcome }: { onOutcome: (o: ConnectOutcome) => void }) {
	const { connect, deviceDialog } = useDeviceAwareConnect();
	return (
		<>
			<button
				type="button"
				onClick={() => void connect('cred_device_1', 'GitHub device').then(onOutcome)}
			>
				Connect
			</button>
			{deviceDialog}
		</>
	);
}

describe('useDeviceAwareConnect', () => {
	beforeEach(() => {
		resetCredentialsStore();
		makeMockCredential({
			credential_id: 'cred_device_1',
			name: 'GitHub device',
			type: CredentialType.OAUTH2,
			provider: 'direct_oauth2',
		});
		worker.use(
			http.post('/credentials/:id/connect', () =>
				HttpResponse.json({
					kind: 'device_authorization',
					user_code: 'WXYZ-9876',
					verification_uri: 'https://github.com/login/device',
					verification_uri_complete: null,
					poll_interval_seconds: 1,
				}),
			),
		);
	});

	afterEach(() => {
		resetCredentialsStore();
		vi.restoreAllMocks();
	});

	it('shows the device code, and Cancel stops the wait as a cancelled outcome', async () => {
		const user = userEvent.setup();
		const onOutcome = vi.fn();
		renderWithProviders(<Harness onOutcome={onOutcome} />);

		await user.click(screen.getByRole('button', { name: 'Connect' }));

		// The human step is on screen, named for the credential being approved.
		expect(await screen.findByText('WXYZ-9876')).toBeInTheDocument();
		expect(screen.getByText('Approve GitHub device')).toBeInTheDocument();

		// The dialog is a native modal, which makes the page behind it inert — so
		// its Cancel is the one button still in the accessibility tree.
		await user.click(await screen.findByRole('button', { name: /cancel/i, hidden: true }));

		await waitFor(() => expect(onOutcome).toHaveBeenCalledWith({ status: 'cancelled' }));
		expect(screen.queryByText('WXYZ-9876')).not.toBeInTheDocument();
	});
});
