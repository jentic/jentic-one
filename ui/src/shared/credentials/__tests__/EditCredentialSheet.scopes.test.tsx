/**
 * EditCredentialSheet — an OAuth2 edit sends `scopes` only when the user
 * changed them: an untouched field must not write `[]` over a credential
 * stored without scopes, while a deliberate "remove all" still sends `[]`.
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { http, HttpResponse } from 'msw';
import { renderWithProviders, screen, userEvent, waitFor } from '@/__tests__/test-utils';
import { worker } from '@/mocks/browser';
import { setToken } from '@/shared/api';
import { CredentialType } from '@/shared/credentials/api';
import { makeMockCredential, resetCredentialsStore } from '@/shared/credentials/mocks/handlers';
import { EditCredentialSheet } from '@/shared/credentials/components/EditCredentialSheet';

function oauthCredential(scopes?: string[]) {
	return makeMockCredential({
		name: 'Slack OAuth',
		type: CredentialType.OAUTH2,
		provider: 'direct_oauth2',
		api: { vendor: 'slack.com', name: 'web', version: '' },
		details: {
			client_id: 'cid',
			token_url: 'https://slack.com/api/oauth.v2.access',
			...(scopes ? { scopes } : {}),
		},
	});
}

/** Capture every PATCH body the sheet sends. */
function capturePatches(): Record<string, unknown>[] {
	const bodies: Record<string, unknown>[] = [];
	worker.use(
		http.patch('/credentials/:id', async ({ request }) => {
			const body = (await request.json()) as Record<string, unknown>;
			bodies.push(body);
			return HttpResponse.json({});
		}),
	);
	return bodies;
}

describe('EditCredentialSheet OAuth2 scopes', () => {
	beforeEach(() => setToken('test-token'));
	afterEach(() => resetCredentialsStore());

	it('omits scopes when only the name changed on a credential stored without scopes', async () => {
		const cred = oauthCredential();
		resetCredentialsStore([cred]);
		const bodies = capturePatches();
		const user = userEvent.setup();
		renderWithProviders(
			<EditCredentialSheet credentialId={cred.credential_id} open onClose={(): void => {}} />,
		);

		const name = await screen.findByLabelText(/^Name/);
		await user.clear(name);
		await user.type(name, 'Slack OAuth renamed');
		await user.click(screen.getByRole('button', { name: /^Save/ }));

		await waitFor(() => expect(bodies).toHaveLength(1));
		expect(bodies[0]).toMatchObject({ name: 'Slack OAuth renamed' });
		expect(bodies[0]).not.toHaveProperty('scopes');
	});

	it('sends [] when the user clears the stored scopes', async () => {
		const cred = oauthCredential(['chat:write', 'channels:read']);
		resetCredentialsStore([cred]);
		const bodies = capturePatches();
		const user = userEvent.setup();
		renderWithProviders(
			<EditCredentialSheet credentialId={cred.credential_id} open onClose={(): void => {}} />,
		);

		const scopes = await screen.findByDisplayValue('chat:write channels:read');
		await user.clear(scopes);
		await user.click(screen.getByRole('button', { name: /^Save/ }));

		await waitFor(() => expect(bodies).toHaveLength(1));
		expect(bodies[0]).toMatchObject({ scopes: [] });
	});
});
