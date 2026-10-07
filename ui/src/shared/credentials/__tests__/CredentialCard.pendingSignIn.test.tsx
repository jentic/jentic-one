import { describe, expect, it, vi } from 'vitest';
import { renderWithProviders, screen } from '@/__tests__/test-utils';
import { CredentialType } from '@/shared/credentials/api';
import { makeMockCredential } from '@/shared/credentials/mocks/handlers';
import {
	CredentialCard,
	credentialIsConnected,
	credentialIsPendingSignIn,
} from '@/shared/credentials/components/CredentialCard';

/**
 * The connect flow mints an OAuth row before the vendor sign-in finishes. Until
 * it does, the row must read as "Pending sign-in" — never as a live credential —
 * and a direct OAuth row that finished (`details.connected`) reads as Connected
 * even without a managed-provider account ref.
 */
describe('CredentialCard — OAuth sign-in states', () => {
	const noop = vi.fn();

	it('marks an unfinished OAuth sign-in as pending, not connected', () => {
		const cred = makeMockCredential({
			name: 'GitHub (device)',
			type: CredentialType.OAUTH2,
			provider: 'direct_oauth2',
			provider_account_ref: null,
			details: { connected: false },
		});
		expect(credentialIsPendingSignIn(cred)).toBe(true);
		expect(credentialIsConnected(cred)).toBe(false);

		renderWithProviders(
			<CredentialCard cred={cred} onEdit={noop} onDelete={noop} onConnect={noop} />,
		);
		expect(screen.getByText('Pending sign-in')).toBeInTheDocument();
		expect(screen.queryByText('Connected')).not.toBeInTheDocument();
	});

	it('gives a pending OAuth card title its own line, chips on a row beneath', () => {
		const cred = makeMockCredential({
			name: 'slack.com',
			type: CredentialType.OAUTH2,
			provider: 'direct_oauth2',
			provider_account_ref: null,
			details: { connected: false, grant_type: 'authorization_code' },
		});
		renderWithProviders(
			<CredentialCard cred={cred} onEdit={noop} onDelete={noop} onConnect={noop} />,
		);
		const title = screen.getByRole('heading', { name: 'slack.com' });
		const badges = screen.getByTestId('credential-card-badges');
		// The chips must not share the title's flex row — that starved it to one
		// character per line in the narrow inventory grid.
		expect(badges).not.toContainElement(title);
		expect(title.parentElement).toBe(badges.parentElement);
		expect(badges).toHaveTextContent('Pending sign-in');
		expect(badges).toHaveTextContent('OAuth 2.0 · Authorization Code');
	});

	it('reads a finished direct-OAuth sign-in as connected', () => {
		const cred = makeMockCredential({
			type: CredentialType.OAUTH2,
			provider: 'direct_oauth2',
			provider_account_ref: null,
			details: { connected: true },
		});
		expect(credentialIsConnected(cred)).toBe(true);
		expect(credentialIsPendingSignIn(cred)).toBe(false);
	});

	it('never marks a non-OAuth credential either way', () => {
		const cred = makeMockCredential({ type: CredentialType.API_KEY });
		expect(credentialIsConnected(cred)).toBe(false);
		expect(credentialIsPendingSignIn(cred)).toBe(false);
	});
});
