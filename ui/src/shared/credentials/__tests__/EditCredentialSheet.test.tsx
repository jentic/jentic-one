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

		await user.type(await screen.findByLabelText(/^Name/), ' Draft');
		await user.click(screen.getByRole('button', { name: 'Cancel' }));

		// Reopen the sheet for the same credential.
		await user.click(screen.getByRole('button', { name: 'Reopen sheet' }));

		expect(await screen.findByLabelText(/^Name/)).toHaveValue('Stripe Key Draft');
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

		// A successful save establishes a clean baseline even if the query has not
		// returned a new credential object by the time the sheet is reopened.
		await user.click(screen.getByRole('button', { name: 'Reopen sheet' }));
		expect(await screen.findByLabelText(/^Name/)).toHaveValue('Stripe Key Renamed');
		expect(await screen.findByLabelText(/^Token/)).toHaveValue('');
		expect(screen.getByRole('button', { name: /^Save/ })).toBeDisabled();
		expect(bodies).toHaveLength(1);
	});

	it('rebases a delayed refetch and sends the second rename against the committed name', async () => {
		const cred = makeMockCredential({
			name: 'Original',
			type: CredentialType.BEARER_TOKEN,
			provider: 'static',
		});
		resetCredentialsStore([cred]);
		const bodies: Record<string, unknown>[] = [];
		let detailReads = 0;
		let release!: () => void;
		const delayed = new Promise<void>((resolve) => {
			release = resolve;
		});
		worker.use(
			http.get('/credentials/:id', async () => {
				detailReads += 1;
				if (detailReads > 1) await delayed;
				return HttpResponse.json({ ...cred, name: 'Original', updated_at: null });
			}),
			http.patch('/credentials/:id', async ({ request }) => {
				const body = (await request.json()) as Record<string, unknown>;
				bodies.push(body);
				return HttpResponse.json({ ...cred, name: body.name });
			}),
		);
		const user = userEvent.setup();
		renderWithProviders(<Host credentialId={cred.credential_id} />);
		const name = await screen.findByLabelText(/^Name/);
		await user.clear(name);
		await user.type(name, 'First save');
		await user.click(screen.getByRole('button', { name: /^Save/ }));
		await waitFor(() => expect(bodies).toHaveLength(1));
		await user.click(screen.getByRole('button', { name: 'Reopen sheet' }));
		expect(await screen.findByLabelText(/^Name/)).toHaveValue('First save');
		await waitFor(() => expect(detailReads).toBeGreaterThanOrEqual(2));
		release();
		const reopened = await screen.findByLabelText(/^Name/);
		await user.clear(reopened);
		await user.type(reopened, 'Second save');
		await user.click(screen.getByRole('button', { name: /^Save/ }));
		await waitFor(() => expect(bodies).toHaveLength(2));
		expect(bodies[1]).toEqual({ type: CredentialType.BEARER_TOKEN, name: 'Second save' });
	});

	it('preserves ordinary dirty drafts and blank secrets across an unrelated refetch', async () => {
		const cred = makeMockCredential({
			name: 'Server name',
			type: CredentialType.BEARER_TOKEN,
			provider: 'static',
			updated_at: '2026-01-01T00:00:00Z',
		});
		resetCredentialsStore([cred]);
		let reads = 0;
		worker.use(
			http.get('/credentials/:id', () => {
				reads += 1;
				return HttpResponse.json({
					...cred,
					updated_at: reads > 1 ? '2026-01-01T00:02:00Z' : cred.updated_at,
				});
			}),
		);
		const user = userEvent.setup();
		renderWithProviders(<Host credentialId={cred.credential_id} />);
		const name = await screen.findByLabelText(/^Name/);
		await user.clear(name);
		await user.type(name, 'Unsaved ordinary draft');
		await user.type(await screen.findByLabelText(/^Token/), 'transient-secret');
		await user.click(screen.getByRole('button', { name: 'Cancel' }));
		await user.click(screen.getByRole('button', { name: 'Reopen sheet' }));
		await waitFor(() => expect(reads).toBeGreaterThan(1));
		expect(await screen.findByLabelText(/^Name/)).toHaveValue('Unsaved ordinary draft');
		expect(await screen.findByLabelText(/^Token/)).toHaveValue('');
	});
});
