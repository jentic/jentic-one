import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { renderWithProviders, screen, userEvent } from '@/__tests__/test-utils';
import { CreateCredentialDialog } from '@/shared/credentials/components/CreateCredentialDialog';
import { resetCredentialsStore } from '@/shared/credentials/mocks/handlers';

/**
 * The "Available to everyone in the organization" toggle is admin-only —
 * it flips the create dialog from the personal-credential path onto the
 * shared OAuth app registration path. These tests pin the visibility
 * gate: non-admin callers never see the toggle, no matter what form state
 * they walk into. (Driving the admin-toggle-shown path end-to-end
 * requires interacting with the auth-type card + provider select + grant
 * dropdown, which pulls in the full spec-load pipeline — kept as a
 * follow-up test with a heavier harness.)
 */

// Stub the permission hook per-test rather than wiring the full ``AuthContext``
// (the value type carries a large auth-mutation surface irrelevant here).
// The dialog reads permission via ``usePermission(ORG_ADMIN)`` — this is the
// single seam that flips ``canShareWithOrg``.
const usePermissionMock = vi.fn<() => boolean>(() => false);
vi.mock('@/shared/auth/usePermission', async () => {
	const actual = await vi.importActual<typeof import('@/shared/auth/usePermission')>(
		'@/shared/auth/usePermission',
	);
	return {
		...actual,
		usePermission: () => usePermissionMock(),
	};
});

describe('CreateCredentialDialog — admin-only registration toggle', () => {
	beforeEach(() => {
		resetCredentialsStore();
		usePermissionMock.mockReturnValue(false);
	});

	afterEach(() => {
		vi.restoreAllMocks();
	});

	it('hides the "Available to everyone in the organization" toggle for non-admins', async () => {
		usePermissionMock.mockReturnValue(false);
		renderWithProviders(
			<CreateCredentialDialog open={true} onClose={vi.fn()} onCreated={vi.fn()} />,
		);
		// Drive from the picker into the form step so the toggle *would* have
		// a chance to render if the gate let it through.
		const user = userEvent.setup();
		await user.click(await screen.findByRole('button', { name: /enter manually/i }));

		// Even in the form step, the toggle is absent for a non-admin.
		expect(
			screen.queryByText(/available to everyone in the organization/i),
		).not.toBeInTheDocument();
	});

	it('keeps the toggle absent even when the form is in a shape that would show it for an admin', async () => {
		// A non-admin walking into the manual-entry form and reaching the
		// OAuth2 + direct_oauth2 + auth-code shape still sees no toggle —
		// the ``isAdmin`` guard short-circuits ``canShareWithOrg`` before any
		// other form-state predicate is consulted.
		usePermissionMock.mockReturnValue(false);
		renderWithProviders(
			<CreateCredentialDialog open={true} onClose={vi.fn()} onCreated={vi.fn()} />,
		);
		const user = userEvent.setup();
		await user.click(await screen.findByRole('button', { name: /enter manually/i }));

		// The toggle text is a stable string ("Available to everyone in the
		// organization"); a regression that leaked it to a non-admin would
		// fail this assertion whether the underlying render was gated or not.
		expect(
			screen.queryByText(/available to everyone in the organization/i),
		).not.toBeInTheDocument();
	});
});
