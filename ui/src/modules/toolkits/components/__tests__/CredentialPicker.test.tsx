import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { renderWithProviders, screen } from '@/__tests__/test-utils';
import { CredentialType } from '@/shared/credentials/api';
import { makeMockCredential, resetCredentialsStore } from '@/shared/credentials/mocks/handlers';
import { CredentialPicker } from '@/modules/toolkits/components/CredentialPicker';

describe('CredentialPicker vendor-wide badge (#744)', () => {
	beforeEach(() => resetCredentialsStore([]));
	afterEach(() => resetCredentialsStore([]));

	it('flags a vendor-wide credential (empty api name) with a badge', async () => {
		resetCredentialsStore([
			makeMockCredential({
				credential_id: 'cw',
				name: 'SendGrid vendor key',
				type: CredentialType.API_KEY,
				api: { vendor: 'sendgrid.com', name: '', version: '' },
			}),
			makeMockCredential({
				credential_id: 'cp',
				name: 'Pinned key',
				type: CredentialType.API_KEY,
				api: { vendor: 'stripe.com', name: 'payments', version: '1' },
			}),
		]);

		renderWithProviders(<CredentialPicker boundIds={new Set()} onSelect={vi.fn()} />);

		await screen.findByText('SendGrid vendor key');
		// The row animates in from opacity 0 (framer), so assert presence rather
		// than strict visibility.
		expect(screen.getByText('vendor-wide')).toBeInTheDocument();
		// Only the vendor-wide row carries the badge (one instance).
		expect(screen.getAllByText('vendor-wide')).toHaveLength(1);
	});
});
