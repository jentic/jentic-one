import { describe, it, expect, vi } from 'vitest';
import { renderWithProviders, screen } from '@/__tests__/test-utils';
import { CredentialCard } from '@/shared/credentials/components/CredentialCard';
import { CredentialType, type Credential } from '@/shared/credentials/api';

/**
 * The card grew a "Source: <registration name>" badge when the credential
 * was minted through an admin-registered OAuth app. "Source" (not
 * "Shared") — the credential itself is per-user; only the underlying
 * OAuth-app registration is org-shared. These two cases pin the render
 * logic — no badge for legacy embedded creds; badge with the registration
 * name when the server hands one back.
 */

function makeCred(overrides: Partial<Credential> = {}): Credential {
	return {
		credential_id: 'cred_test',
		type: CredentialType.OAUTH2,
		name: 'Gmail',
		api: { vendor: 'googleapis-com', name: 'gmail', version: 'v1' },
		catalog_api_id: 'googleapis-com/gmail',
		provider: 'direct_oauth2',
		provider_account_ref: null,
		active: true,
		created_by: 'usr_test',
		created_at: '2026-01-15T09:30:00Z',
		updated_at: null,
		details: {
			client_id: 'iv1.test',
			token_url: 'https://oauth2.googleapis.com/token',
			grant_type: 'authorization_code',
			scopes: null,
			connected: true,
		},
		server_variables: null,
		oauth_app_registration_id: null,
		oauth_app_registration_name: null,
		...overrides,
	} as Credential;
}

describe('CredentialCard — shared-registration badge', () => {
	it('shows "Source: <name>" when the credential is registration-backed', () => {
		renderWithProviders(
			<CredentialCard
				cred={makeCred({
					oauth_app_registration_id: 'oar_prod_gmail',
					oauth_app_registration_name: 'MyOrg Prod Gmail',
				})}
				onEdit={vi.fn()}
				onDelete={vi.fn()}
				onConnect={vi.fn()}
			/>,
		);
		expect(screen.getByText(/Source:\s*MyOrg Prod Gmail/i)).toBeInTheDocument();
	});

	it('omits the badge for a legacy embedded credential', () => {
		renderWithProviders(
			<CredentialCard
				cred={makeCred()}
				onEdit={vi.fn()}
				onDelete={vi.fn()}
				onConnect={vi.fn()}
			/>,
		);
		expect(screen.queryByText(/Source:/i)).not.toBeInTheDocument();
	});
});
