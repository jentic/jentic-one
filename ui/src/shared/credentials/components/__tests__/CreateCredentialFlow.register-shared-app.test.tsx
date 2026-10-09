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
 * ``registerSharedApp`` flips the create flow from the personal-credential
 * path onto the shared OAuth app registration path. These tests pin (a) the
 * default: without the prop the flow is the plain credential create, with no
 * sharing affordance, whoever is signed in; (b) the register submit path maps
 * the form onto the registration API for both sign-in flows; (c) register mode
 * never quietly creates a personal credential — an API that can't be shared
 * says why and blocks submit, and the picker drops the one-click (personal)
 * sign-in tiles.
 *
 * The register path pins a catalog API with no ``specUrl``, so no spec loads
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

function renderRegisterFlow(onClose = vi.fn()): void {
	renderWithProviders(
		<CreateCredentialFlow
			open={true}
			onClose={onClose}
			onCreated={vi.fn()}
			pinnedApi={PINNED_API}
			initialType={CredentialType.OAUTH2}
			registerSharedApp
		/>,
	);
}

describe('CreateCredentialFlow — register shared app mode', () => {
	beforeEach(() => {
		resetCredentialsStore();
		resetOAuthAppRegistrationsStore();
	});

	afterEach(() => {
		vi.restoreAllMocks();
	});

	it('offers no sharing without the prop, even on a shareable OAuth 2.0 API', async () => {
		renderWithProviders(
			<CreateCredentialFlow
				open={true}
				onClose={vi.fn()}
				onCreated={vi.fn()}
				pinnedApi={PINNED_API}
				initialType={CredentialType.OAUTH2}
			/>,
		);

		expect(await screen.findByLabelText(/client id/i)).toBeInTheDocument();
		expect(screen.queryByText(/shared oauth app/i)).not.toBeInTheDocument();
		expect(screen.queryByRole('checkbox')).toBeNull();
		expect(screen.getByLabelText(/^name/i)).toBeInTheDocument();
		expect(screen.getByRole('button', { name: /create credential/i })).toBeInTheDocument();
		expect(screen.queryByRole('button', { name: /register shared app/i })).toBeNull();
	});

	it('registers an authorization-code shared app from the admin form', async () => {
		const capture = captureRegistrationCreate();
		const onClose = vi.fn();
		renderRegisterFlow(onClose);

		expect(await screen.findByLabelText(/registration name/i)).toHaveValue('GitHub REST');
		expect(screen.queryByRole('checkbox')).toBeNull();

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
		const capture = captureRegistrationCreate();
		renderRegisterFlow();

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
		// No ``<domain>/<api>`` catalog id — the server would reject the registration.
		renderWithProviders(
			<CreateCredentialFlow
				open={true}
				onClose={vi.fn()}
				onCreated={vi.fn()}
				pinnedApi={{ ...PINNED_API, apiId: 'github' }}
				initialType={CredentialType.OAUTH2}
				registerSharedApp
			/>,
		);

		const note = await screen.findByRole('note');
		expect(note).toHaveTextContent(/catalog api id of the form <domain>\/<api>/i);
		// Still register mode: no personal-credential fallback.
		const submit = screen.getByRole('button', { name: /register shared app/i });
		expect(submit).toBeDisabled();
		expect(screen.queryByRole('button', { name: /create credential/i })).toBeNull();
		// The reason is not pinned to the Name field.
		expect(screen.getByLabelText(/^name/i)).not.toHaveAttribute('aria-invalid');
	});

	it('register mode on manual entry explains why and blocks submit', async () => {
		renderWithProviders(
			<CreateCredentialFlow
				open={true}
				onClose={vi.fn()}
				onCreated={vi.fn()}
				initialType={CredentialType.OAUTH2}
				registerSharedApp
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
				registerSharedApp
			/>,
		);
		await screen.findByRole('button', { name: /enter manually/i });
		expect(screen.queryByText(/one-click sign-in/i)).not.toBeInTheDocument();
	});
});
