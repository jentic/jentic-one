/**
 * EditCredentialSheet — the OAuth Connect CTA follows the vendor-supplied
 * `authorize_url` only when it is https; any other scheme is refused with an
 * error toast and the page stays put.
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { http, HttpResponse } from 'msw';
import { renderWithProviders, screen, userEvent } from '@/__tests__/test-utils';
import { worker } from '@/mocks/browser';
import { setToken } from '@/shared/api';
import { Toaster } from '@/shared/ui';
import { CredentialType } from '@/shared/credentials/api';
import { makeMockCredential, resetCredentialsStore } from '@/shared/credentials/mocks/handlers';
import { EditCredentialSheet } from '@/shared/credentials/components/EditCredentialSheet';

describe('EditCredentialSheet OAuth connect URL guard', () => {
	beforeEach(() => setToken('test-token'));
	afterEach(() => resetCredentialsStore());

	it.each(['javascript:alert(1)', 'http://provider.example.com/authorize'])(
		'refuses to navigate to a non-https authorize_url (%s)',
		async (authorizeUrl) => {
			const cred = makeMockCredential({
				name: 'Slack OAuth',
				type: CredentialType.OAUTH2,
				provider: 'direct_oauth2',
				api: { vendor: 'slack.com', name: 'web', version: '' },
				details: { client_id: 'cid', token_url: 'https://slack.com/api/oauth.v2.access' },
			});
			resetCredentialsStore([cred]);
			worker.use(
				http.post('/credentials/:id/connect', () =>
					HttpResponse.json({
						kind: 'authorization_code',
						authorize_url: authorizeUrl,
						state: 'mock-state',
					}),
				),
			);
			const before = window.location.href;
			const user = userEvent.setup();
			renderWithProviders(
				<>
					<EditCredentialSheet
						credentialId={cred.credential_id}
						open
						onClose={(): void => {}}
					/>
					<Toaster />
				</>,
			);

			await user.click(await screen.findByRole('button', { name: /^Connect/ }));

			expect(
				await screen.findByText('The provider returned an unsafe sign-in URL'),
			).toBeInTheDocument();
			expect(window.location.href).toBe(before);
		},
	);
});
