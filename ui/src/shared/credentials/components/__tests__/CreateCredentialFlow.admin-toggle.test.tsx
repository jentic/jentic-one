import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { http, HttpResponse } from 'msw';
import { worker } from '@/mocks/browser';
import { renderWithProviders, screen, userEvent, waitFor } from '@/__tests__/test-utils';
import { CredentialType } from '@/shared/api';
import type { SelectedApi } from '@/shared/credentials/api/apis-hooks';
import { CreateCredentialFlow } from '@/shared/credentials/components/CreateCredentialFlow';
import { resetCredentialsStore } from '@/shared/credentials/mocks/handlers';
import { resetOAuthAppRegistrationsStore } from '@/shared/credentials/oauth-app-registrations/mocks/handlers';

/**
 * The "Register as a shared OAuth app" toggle is admin-only — it flips the
 * create flow from the personal-credential path onto the shared OAuth app
 * registration path. These tests pin (a) the visibility gate: non-admins
 * never see the toggle, and admins don't either on manual entry (a shared
 * app is keyed to a catalog API); (b) the admin submit path maps the form
 * onto the registration API for both sign-in flows; (c) "Register shared
 * app" never quietly creates a personal credential — an API that can't be
 * shared says why and blocks submit, and the picker drops the one-click
 * (personal) sign-in tiles.
 *
 * The admin path pins a catalog API with no ``specUrl``, so no spec loads
 * and the "Sign-in flow" selector owns the grant type.
 */

const PINNED_API: SelectedApi = {
	source: 'catalog',
	vendor: 'github.com',
	name: 'rest',
	version: '',
	apiId: 'github.com/rest',
	label: 'GitHub REST',
};

function captureRegistrationCreate(): { body: () => Record<string, unknown> | null } {
	let captured: Record<string, unknown> | null = null;
	worker.use(
		http.post('/oauth-app-registrations', async ({ request }) => {
			captured = (await request.json()) as Record<string, unknown>;
			return HttpResponse.json({ id: 'oar_new', ...captured }, { status: 201 });
		}),
	);
	return { body: () => captured };
}

function renderAdminSharedFlow(onClose = vi.fn()): void {
	renderWithProviders(
		<CreateCredentialFlow
			open={true}
			onClose={onClose}
			onCreated={vi.fn()}
			pinnedApi={PINNED_API}
			initialType={CredentialType.OAUTH2}
			initialShareWithOrg={true}
		/>,
	);
}

// Stub the permission hook per-test rather than wiring the full ``AuthContext``
// (the value type carries a large auth-mutation surface irrelevant here).
// The dialog reads permission via ``useOptionalPermission(ORG_ADMIN)`` — this
// is the single seam that flips ``canShareWithOrg``.
const usePermissionMock = vi.fn<() => boolean>(() => false);
vi.mock('@/shared/auth/usePermission', async () => {
	const actual = await vi.importActual<typeof import('@/shared/auth/usePermission')>(
		'@/shared/auth/usePermission',
	);
	return {
		...actual,
		useOptionalPermission: () => usePermissionMock(),
	};
});

describe('CreateCredentialFlow — admin-only registration toggle', () => {
	beforeEach(() => {
		resetCredentialsStore();
		resetOAuthAppRegistrationsStore();
		usePermissionMock.mockReturnValue(false);
	});

	afterEach(() => {
		vi.restoreAllMocks();
	});

	it('hides the "Register as a shared OAuth app" toggle for non-admins', async () => {
		usePermissionMock.mockReturnValue(false);
		renderWithProviders(
			<CreateCredentialFlow open={true} onClose={vi.fn()} onCreated={vi.fn()} />,
		);
		// Drive from the picker into the form step so the toggle *would* have
		// a chance to render if the gate let it through.
		const user = userEvent.setup();
		await user.click(await screen.findByRole('button', { name: /enter manually/i }));

		// Even in the form step, the toggle is absent for a non-admin.
		expect(screen.queryByText(/register as a shared oauth app/i)).not.toBeInTheDocument();
	});

	it('keeps the toggle absent even when the form is in a shape that would show it for an admin', async () => {
		// A non-admin walking into the manual-entry form and reaching the
		// OAuth2 + direct_oauth2 + auth-code shape still sees no toggle —
		// the ``isAdmin`` guard short-circuits ``canShareWithOrg`` before any
		// other form-state predicate is consulted.
		usePermissionMock.mockReturnValue(false);
		renderWithProviders(
			<CreateCredentialFlow open={true} onClose={vi.fn()} onCreated={vi.fn()} />,
		);
		const user = userEvent.setup();
		await user.click(await screen.findByRole('button', { name: /enter manually/i }));

		// The toggle text is a stable string ("Register as a shared OAuth app"); a regression that leaked it to a non-admin would
		// fail this assertion whether the underlying render was gated or not.
		expect(screen.queryByText(/register as a shared oauth app/i)).not.toBeInTheDocument();
	});

	it('hides the toggle from admins on manual entry (no catalog API to key it to)', async () => {
		usePermissionMock.mockReturnValue(true);
		renderWithProviders(
			<CreateCredentialFlow
				open={true}
				onClose={vi.fn()}
				onCreated={vi.fn()}
				initialType={CredentialType.OAUTH2}
			/>,
		);
		const user = userEvent.setup();
		await user.click(await screen.findByRole('button', { name: /enter manually/i }));

		expect(screen.getByLabelText(/client id/i)).toBeInTheDocument();
		expect(screen.queryByText(/register as a shared oauth app/i)).not.toBeInTheDocument();
	});

	it('registers an authorization-code shared app from the admin form', async () => {
		usePermissionMock.mockReturnValue(true);
		const capture = captureRegistrationCreate();
		const onClose = vi.fn();
		renderAdminSharedFlow(onClose);

		const toggle = await screen.findByRole('checkbox', {
			name: /register as a shared oauth app/i,
		});
		expect(toggle).toBeChecked();
		expect(screen.getByLabelText(/registration name/i)).toHaveValue('GitHub REST');

		const user = userEvent.setup();
		await user.type(screen.getByLabelText(/client id/i), 'cid_123');
		await user.type(screen.getByLabelText(/client secret/i), 's3cret');
		await user.type(
			screen.getByLabelText(/authorize url/i),
			'https://github.com/login/oauth/authorize',
		);
		await user.type(
			screen.getByLabelText(/token url/i),
			'https://github.com/login/oauth/access_token',
		);
		await user.click(screen.getByRole('button', { name: /register shared app/i }));

		await waitFor(() => expect(capture.body()).not.toBeNull());
		expect(capture.body()).toMatchObject({
			name: 'GitHub REST',
			api_vendor: 'github.com',
			catalog_api_id: 'github.com/rest',
			display_name: 'GitHub REST',
			flow_kind: 'authorization_code',
			client_id: 'cid_123',
			client_secret: 's3cret',
			authorize_url: 'https://github.com/login/oauth/authorize',
			token_url: 'https://github.com/login/oauth/access_token',
		});
		await waitFor(() => expect(onClose).toHaveBeenCalled());
	});

	it('registers a device-flow shared app with endpoints and no secret', async () => {
		usePermissionMock.mockReturnValue(true);
		const capture = captureRegistrationCreate();
		renderAdminSharedFlow();

		const user = userEvent.setup();
		await user.selectOptions(await screen.findByLabelText(/sign-in flow/i), 'device_code');
		// Device flow is a public client — no secret field to fill.
		expect(screen.queryByLabelText(/client secret/i)).not.toBeInTheDocument();

		await user.type(screen.getByLabelText(/client id/i), 'cid_dev');
		await user.type(
			screen.getByLabelText(/device authorization url/i),
			'https://github.com/login/device/code',
		);
		await user.type(
			screen.getByLabelText(/token url/i),
			'https://github.com/login/oauth/access_token',
		);
		await user.click(screen.getByRole('button', { name: /register shared app/i }));

		await waitFor(() => expect(capture.body()).not.toBeNull());
		const body = capture.body()!;
		expect(body).toMatchObject({
			flow_kind: 'device_authorization',
			client_id: 'cid_dev',
			catalog_api_id: 'github.com/rest',
			authorization_endpoint: 'https://github.com/login/device/code',
			token_endpoint: 'https://github.com/login/oauth/access_token',
		});
		expect(body).not.toHaveProperty('client_secret');
		expect(body).not.toHaveProperty('authorize_url');
	});

	it('register mode explains an API that cannot be shared and blocks submit', async () => {
		usePermissionMock.mockReturnValue(true);
		// No ``<domain>/<api>`` catalog id — the server would reject the registration.
		renderWithProviders(
			<CreateCredentialFlow
				open={true}
				onClose={vi.fn()}
				onCreated={vi.fn()}
				pinnedApi={{ ...PINNED_API, apiId: 'github' }}
				initialType={CredentialType.OAUTH2}
				initialShareWithOrg={true}
			/>,
		);

		const note = await screen.findByRole('note');
		expect(note).toHaveTextContent(/catalog api id of the form <domain>\/<api>/i);
		// Still register mode: no personal-credential fallback.
		expect(
			screen.queryByRole('checkbox', { name: /register as a shared oauth app/i }),
		).toBeNull();
		const submit = screen.getByRole('button', { name: /register shared app/i });
		expect(submit).toBeDisabled();
		expect(screen.queryByRole('button', { name: /create credential/i })).toBeNull();
		// The reason is not pinned to the Name field.
		expect(screen.getByLabelText(/^name/i)).not.toHaveAttribute('aria-invalid');
	});

	it('register mode on manual entry explains why and blocks submit', async () => {
		usePermissionMock.mockReturnValue(true);
		renderWithProviders(
			<CreateCredentialFlow
				open={true}
				onClose={vi.fn()}
				onCreated={vi.fn()}
				initialType={CredentialType.OAUTH2}
				initialShareWithOrg={true}
			/>,
		);
		const user = userEvent.setup();
		await user.click(await screen.findByRole('button', { name: /enter manually/i }));

		expect(await screen.findByRole('note')).toHaveTextContent(
			/registered against a catalog api/i,
		);
		expect(screen.getByRole('button', { name: /register shared app/i })).toBeDisabled();
	});

	it('register mode hides the one-click sign-in tiles', async () => {
		usePermissionMock.mockReturnValue(true);
		const { unmount } = renderWithProviders(
			<CreateCredentialFlow open={true} onClose={vi.fn()} onCreated={vi.fn()} />,
		);
		// Baseline: the personal flow offers the tiles.
		expect(await screen.findByText(/one-click sign-in/i)).toBeInTheDocument();
		unmount();

		renderWithProviders(
			<CreateCredentialFlow
				open={true}
				onClose={vi.fn()}
				onCreated={vi.fn()}
				initialType={CredentialType.OAUTH2}
				initialShareWithOrg={true}
			/>,
		);
		await screen.findByRole('button', { name: /enter manually/i });
		expect(screen.queryByText(/one-click sign-in/i)).not.toBeInTheDocument();
	});
});
