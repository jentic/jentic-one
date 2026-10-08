/**
 * The inventory sheet is the credential list only: shared OAuth app
 * management (``SharedOAuthAppsSection``) is not mounted here, whoever is
 * signed in — a host's own admin surface mounts it.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { renderWithProviders, screen } from '@/__tests__/test-utils';
import { CredentialInventorySheet } from '@/modules/agents/components/flat/CredentialInventorySheet';
import { resetCredentialsStore } from '@/shared/credentials/mocks/handlers';

describe('CredentialInventorySheet — no shared OAuth apps section', () => {
	beforeEach(() => {
		resetCredentialsStore();
	});

	it('renders the credential list without the shared-apps section', async () => {
		renderWithProviders(<CredentialInventorySheet open={true} onClose={vi.fn()} />);
		expect(await screen.findByRole('searchbox', { name: /filter credentials/i })).toBeVisible();
		expect(screen.queryByRole('heading', { name: /shared oauth apps/i })).toBeNull();
		expect(screen.queryByRole('button', { name: /register shared app/i })).toBeNull();
		expect(screen.queryByRole('heading', { name: /^all credentials$/i })).toBeNull();
	});
});
