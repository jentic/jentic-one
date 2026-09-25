/**
 * EditCredentialSheet — a name a sibling credential for the same API holds is
 * warned about with a free suggestion, never blocked, and the credential being
 * edited never clashes with its own name.
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { renderWithProviders, screen, userEvent } from '@/__tests__/test-utils';
import { setToken } from '@/shared/api';
import { makeMockCredential, resetCredentialsStore } from '@/shared/credentials/mocks/handlers';
import { EditCredentialSheet } from '@/shared/credentials/components/EditCredentialSheet';

const ACME_API = { vendor: 'acme.io', name: 'main', version: '' };

function renderSheet(credentialId: string) {
	return renderWithProviders(
		<EditCredentialSheet credentialId={credentialId} open onClose={(): void => {}} />,
	);
}

describe('EditCredentialSheet name clash', () => {
	beforeEach(() => setToken('test-token'));
	afterEach(() => resetCredentialsStore());

	it('does not warn about the credential’s own name', async () => {
		const own = makeMockCredential({ name: 'Acme', api: ACME_API });
		resetCredentialsStore([own]);
		renderSheet(own.credential_id);

		const name = await screen.findByLabelText(/^Name/);
		expect(name).toHaveValue('Acme');
		expect(screen.queryByTestId('credential-name-clash')).not.toBeInTheDocument();
	});

	it('warns when the name repeats a sibling’s, keeping the typed name', async () => {
		const sibling = makeMockCredential({ name: 'Acme', api: ACME_API });
		const own = makeMockCredential({ name: 'Staging', api: ACME_API });
		resetCredentialsStore([sibling, own]);
		const user = userEvent.setup();
		renderSheet(own.credential_id);

		const name = await screen.findByLabelText(/^Name/);
		await user.clear(name);
		await user.type(name, 'Acme');

		const clash = await screen.findByTestId('credential-name-clash');
		expect(name).toHaveValue('Acme');
		expect(clash).toHaveTextContent('Suggested: Acme 2');
		expect(name).toHaveAttribute('aria-describedby', clash.id);
		expect(screen.getByRole('button', { name: /^Save/ })).toBeEnabled();

		await user.click(screen.getByRole('button', { name: 'Use this name' }));
		expect(name).toHaveValue('Acme 2');
		expect(screen.queryByTestId('credential-name-clash')).not.toBeInTheDocument();
	});
});
