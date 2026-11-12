/**
 * EditCredentialSheet — cancelling/closing the sheet and reopening the same
 * credential resets write-only secret fields, password reveal toggles, error
 * state, and dirty baseline so a subsequent rename-only save sends no secret.
 */
import { useState } from 'react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { http, HttpResponse } from 'msw';
import { renderWithProviders, screen, userEvent, waitFor } from '@/__tests__/test-utils';
import { worker } from '@/mocks/browser';
import { setToken } from '@/shared/api';
import { Button } from '@/shared/ui';
import { CredentialType } from '@/shared/credentials/api';
import { makeMockCredential, resetCredentialsStore } from '@/shared/credentials/mocks/handlers';
import { EditCredentialSheet } from '@/shared/credentials/components/EditCredentialSheet';

function Host({ credentialId }: { credentialId: string }) {
	const [open, setOpen] = useState(true);
	return (
		<>
			<Button onClick={(): void => setOpen(true)}>Reopen sheet</Button>
			<EditCredentialSheet
				credentialId={credentialId}
				open={open}
				onClose={(): void => setOpen(false)}
			/>
		</>
	);
}

describe('EditCredentialSheet reset on close/reopen', () => {
	beforeEach(() => setToken('test-token'));
	afterEach(() => resetCredentialsStore());

	it('resets typed secret and reveal state on cancel/reopen so a rename-only save sends no secret field', async () => {
		const cred = makeMockCredential({
			name: 'Stripe Key',
			type: CredentialType.BEARER_TOKEN,
			provider: 'static',
			api: { vendor: 'stripe.com', name: 'default', version: '1.0.0' },
			details: { hint: '••••4242' },
		});
		resetCredentialsStore([cred]);

		const bodies: Record<string, unknown>[] = [];
		worker.use(
			http.patch('/credentials/:id', async ({ request }) => {
				const body = (await request.json()) as Record<string, unknown>;
				bodies.push(body);
				return HttpResponse.json({ ...cred, name: body.name ?? cred.name });
			}),
		);

		const user = userEvent.setup();
		renderWithProviders(<Host credentialId={cred.credential_id} />);

		// Type a secret and reveal it, then cancel/close the sheet.
		const tokenInput = await screen.findByLabelText(/^Token/);
		await user.type(tokenInput, 'sk_live_accidentally_typed');
		await user.click(screen.getByRole('button', { name: 'Show password' }));
		expect(tokenInput).toHaveAttribute('type', 'text');

		await user.click(screen.getByRole('button', { name: 'Cancel' }));

		// Reopen the sheet for the same credential.
		await user.click(screen.getByRole('button', { name: 'Reopen sheet' }));

		const reopenedTokenInput = await screen.findByLabelText(/^Token/);
		expect(reopenedTokenInput).toHaveValue('');
		expect(reopenedTokenInput).toHaveAttribute('type', 'password');

		// Rename only and save — PATCH body must NOT include `token`.
		const nameInput = await screen.findByLabelText(/^Name/);
		await user.clear(nameInput);
		await user.type(nameInput, 'Stripe Key Renamed');
		await user.click(screen.getByRole('button', { name: /^Save/ }));

		await waitFor(() => expect(bodies).toHaveLength(1));
		expect(bodies[0]).toEqual({
			type: CredentialType.BEARER_TOKEN,
			name: 'Stripe Key Renamed',
		});
		expect(bodies[0]).not.toHaveProperty('token');
	});
});
