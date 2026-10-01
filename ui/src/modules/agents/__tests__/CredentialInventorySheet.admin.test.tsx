/**
 * The shared-OAuth-apps section is admin-only: the inventory sheet renders it
 * (above the credential filters, which don't apply to it) only when the viewer
 * holds ``org:admin``.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { renderWithProviders, screen } from '@/__tests__/test-utils';
import { CredentialInventorySheet } from '@/modules/agents/components/flat/CredentialInventorySheet';
import { resetCredentialsStore } from '@/shared/credentials/mocks/handlers';
import { resetOAuthAppRegistrationsStore } from '@/shared/credentials/oauth-app-registrations/mocks/handlers';

const isAdminMock = vi.fn<() => boolean>(() => false);
vi.mock('@/shared/auth/usePermission', async () => {
	const actual = await vi.importActual<typeof import('@/shared/auth/usePermission')>(
		'@/shared/auth/usePermission',
	);
	return { ...actual, useOptionalPermission: () => isAdminMock() };
});

function renderSheet(): void {
	renderWithProviders(<CredentialInventorySheet open={true} onClose={vi.fn()} />);
}

describe('CredentialInventorySheet — shared OAuth apps gate', () => {
	beforeEach(() => {
		resetCredentialsStore();
		resetOAuthAppRegistrationsStore();
	});

	it('hides the section from non-admins', async () => {
		isAdminMock.mockReturnValue(false);
		renderSheet();
		expect(await screen.findByRole('searchbox', { name: /filter credentials/i })).toBeVisible();
		expect(screen.queryByRole('heading', { name: /shared oauth apps/i })).toBeNull();
		expect(screen.queryByRole('button', { name: /register shared app/i })).toBeNull();
	});

	it('shows the section to admins, ahead of the credential filters', async () => {
		isAdminMock.mockReturnValue(true);
		renderSheet();
		const section = await screen.findByRole('heading', { name: /shared oauth apps/i });
		const filter = screen.getByRole('searchbox', { name: /filter credentials/i });
		expect(
			section.compareDocumentPosition(filter) & Node.DOCUMENT_POSITION_FOLLOWING,
		).toBeTruthy();
		expect(screen.getByRole('heading', { name: /^all credentials$/i })).toBeVisible();
	});
});
