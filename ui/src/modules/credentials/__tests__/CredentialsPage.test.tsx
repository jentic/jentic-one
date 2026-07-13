import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { worker } from '@/mocks/browser';
import {
	checkA11y,
	createErrorHandler,
	renderWithProviders,
	screen,
	userEvent,
	waitFor,
} from '@/__tests__/test-utils';
import { clearToken, setToken } from '@/shared/api';
import { AuthProvider } from '@/shared/auth/AuthContext';
import { App } from '@/App';
import { clearAllToasts, Toaster } from '@/shared/ui';
import { CredentialType } from '@/modules/credentials/api';
import { CredentialsPage } from '@/modules/credentials/pages/CredentialsPage';
import {
	makeMockApi,
	makeMockCatalogEntry,
	makeMockCredential,
	resetApisStore,
	resetCredentialsStore,
} from '@/modules/credentials/mocks/handlers';

/**
 * The success toast that previously lived as a one-time-secret dialog is now
 * mounted at the app shell. Page-level tests need an explicit `<Toaster />`
 * sibling to observe it.
 */
function renderPage() {
	return renderWithProviders(
		<>
			<CredentialsPage />
			<Toaster />
		</>,
	);
}

/**
 * Assert that a "Credential created" success toast eventually appears.
 * The toast store is global so older toasts may still be on-screen — we
 * scan all toasts rather than relying on a single match.
 */
async function expectCredentialCreatedToast(): Promise<void> {
	await waitFor(() => {
		const toasts = screen.queryAllByTestId('toast');
		expect(toasts.some((t) => t.textContent?.includes('Credential created'))).toBe(true);
	});
}

describe('CredentialsPage', () => {
	beforeEach(() => {
		resetCredentialsStore();
		resetApisStore();
		clearAllToasts();
	});
	afterEach(() => {
		resetCredentialsStore();
		resetApisStore();
		clearAllToasts();
	});

	it('renders the empty state when there are no credentials', async () => {
		renderWithProviders(<CredentialsPage />);
		expect(await screen.findByText('No credentials stored')).toBeVisible();
	});

	it('lists stored credentials', async () => {
		resetCredentialsStore([
			makeMockCredential({
				credential_id: 'c1',
				name: 'Stripe key',
				type: CredentialType.API_KEY,
			}),
			makeMockCredential({ credential_id: 'c2', name: 'GitHub token' }),
		]);
		renderWithProviders(<CredentialsPage />);
		expect(await screen.findByText('Stripe key')).toBeInTheDocument();
		expect(screen.getByText('GitHub token')).toBeInTheDocument();
	});

	it('surfaces a load error', async () => {
		worker.use(createErrorHandler('get', '/credentials', { status: 500 }));
		renderWithProviders(<CredentialsPage />);
		expect(await screen.findByRole('alert')).toBeVisible();
	});

	it('filters the list by credential type', async () => {
		resetCredentialsStore([
			makeMockCredential({
				credential_id: 'c1',
				name: 'Stripe key',
				type: CredentialType.API_KEY,
			}),
			makeMockCredential({
				credential_id: 'c2',
				name: 'GitHub token',
				type: CredentialType.BEARER_TOKEN,
			}),
		]);
		renderWithProviders(<CredentialsPage />);
		const user = userEvent.setup();

		await screen.findByText('Stripe key');
		expect(screen.getByText('GitHub token')).toBeInTheDocument();

		// Narrow to API keys → the bearer token drops out.
		await user.click(screen.getByRole('button', { name: 'API key' }));
		expect(screen.getByText('Stripe key')).toBeInTheDocument();
		await waitFor(() => expect(screen.queryByText('GitHub token')).not.toBeInTheDocument());
	});

	it('creates a credential via manual entry and surfaces a success toast', async () => {
		renderPage();
		const user = userEvent.setup();

		await screen.findByText('No credentials stored');
		await user.click(screen.getByRole('button', { name: /add your first credential/i }));

		// Guided picker is the first step; drop into manual entry so the
		// legacy free-text fields render.
		await user.click(await screen.findByRole('button', { name: /Enter manually/i }));

		await user.type(screen.getByPlaceholderText('Production API key'), 'CI token');
		await user.type(screen.getByPlaceholderText('acme'), 'acme');
		await user.type(screen.getByPlaceholderText('sk_live_…'), 'super-secret-value');
		await user.click(screen.getByRole('button', { name: 'Create credential' }));

		// Success is signalled by the toast + the new credential appearing in
		// the list. The raw secret is no longer surfaced post-creation —
		// echoing back a value the user just typed adds friction without a
		// security benefit.
		await expectCredentialCreatedToast();
		const toast = screen
			.getAllByTestId('toast')
			.find((t) => t.textContent?.includes('Credential created'))!;
		expect(toast).toHaveTextContent('CI token');
	});

	it('creates a credential via the guided flow: pick local API → auto-shape to API_KEY', async () => {
		resetApisStore([
			makeMockApi({
				vendor: 'acme',
				name: 'main',
				version: '1.0.0',
				displayName: 'Acme',
				securitySchemes: ['apiKey'],
				spec: {
					openapi: '3.0.0',
					info: { title: 'Acme', version: '1.0.0' },
					components: {
						securitySchemes: {
							ApiKeyAuth: {
								type: 'apiKey',
								in: 'header',
								name: 'X-Acme-Key',
							},
						},
					},
				},
			}),
		]);
		renderPage();
		const user = userEvent.setup();

		await screen.findByText('No credentials stored');
		await user.click(screen.getByRole('button', { name: /add your first credential/i }));

		// Pick the workspace API.
		await user.click(await screen.findByText('Acme'));

		// Form auto-seeded: name prefilled to "Acme", scheme drove type to
		// API_KEY (field name + location pre-filled from the spec).
		const nameInput = (await screen.findByPlaceholderText(
			'Production API key',
		)) as HTMLInputElement;
		expect(nameInput.value).toBe('Acme');

		const fieldNameInput = (await screen.findByPlaceholderText(
			'X-Api-Key',
		)) as HTMLInputElement;
		await waitFor(() => expect(fieldNameInput.value).toBe('X-Acme-Key'));

		const passwordInputs = screen.getAllByDisplayValue('') as HTMLInputElement[];
		const apiKeyField = passwordInputs.find((el) => el.type === 'password');
		expect(apiKeyField).toBeTruthy();
		await user.type(apiKeyField!, 'sk_acme_123');
		await user.click(screen.getByRole('button', { name: 'Create credential' }));

		await expectCredentialCreatedToast();
	});

	it('imports an un-registered catalog API before creating the credential', async () => {
		resetApisStore(
			[],
			[
				{
					entry: makeMockCatalogEntry({
						apiId: 'acme.com',
						vendor: 'acme',
						path: 'acme.com/main/1.0.0',
						registered: false,
					}).entry,
					spec: {
						openapi: '3.0.0',
						info: { title: 'acme.com', version: '1.0.0' },
						components: {
							securitySchemes: {
								Bearer: { type: 'http', scheme: 'bearer' },
							},
						},
					},
				},
			],
		);
		renderPage();
		const user = userEvent.setup();

		await screen.findByText('No credentials stored');
		await user.click(screen.getByRole('button', { name: /add your first credential/i }));
		await user.type(screen.getByLabelText('Search APIs'), 'acme');
		await user.click(await screen.findByText('acme.com'));

		// The summary chip now signals the upcoming :import via an inline
		// subtitle (replacing the old standalone badge) — the wording matches
		// the lowercase "imports on save" used in the chip.
		expect(await screen.findByText(/imports on save/i)).toBeInTheDocument();

		// Auto-shape derived BEARER_TOKEN from the spec → fill the token field.
		const tokenInput = (await screen.findByPlaceholderText('sk_live_…')) as HTMLInputElement;
		await user.type(tokenInput, 'token-from-catalog');
		await user.click(screen.getByRole('button', { name: 'Create credential' }));

		await expectCredentialCreatedToast();
	});

	it('deletes a credential after confirmation', async () => {
		resetCredentialsStore([makeMockCredential({ credential_id: 'c1', name: 'Doomed' })]);
		renderWithProviders(<CredentialsPage />);
		const user = userEvent.setup();

		await screen.findByText('Doomed');
		await user.click(screen.getByRole('button', { name: 'Delete credential Doomed' }));

		const dialog = await screen.findByRole('heading', { name: 'Delete credential' });
		expect(dialog).toBeVisible();

		// The cascade-aware dialog gates the destructive action behind a
		// type-to-confirm field, so the confirm button only fires after the
		// credential name is typed back exactly.
		await user.type(screen.getByLabelText(/Type Doomed to confirm/i), 'Doomed');
		await user.click(screen.getByRole('button', { name: 'Delete credential' }));

		await waitFor(() => expect(screen.queryByText('Doomed')).not.toBeInTheDocument());
	});

	it('connects an oauth2 credential via the popup flow and shows Connected', async () => {
		resetCredentialsStore([
			makeMockCredential({
				credential_id: 'oauth1',
				name: 'Slack OAuth',
				type: CredentialType.OAUTH2,
				provider: 'pipedream',
			}),
		]);
		// Stub the popup window: open() returns a fake handle that never closes
		// on its own, so the page's poll observes the mock connect result first.
		const fakePopup = { closed: false, close: () => {} } as unknown as Window;
		const openSpy = vi.spyOn(window, 'open').mockReturnValue(fakePopup);

		renderWithProviders(<CredentialsPage />);
		const user = userEvent.setup();

		await screen.findByText('Slack OAuth');
		await user.click(screen.getByRole('button', { name: 'Connect Slack OAuth' }));

		await waitFor(() => expect(openSpy).toHaveBeenCalled());
		expect(await screen.findByText('Connected', {}, { timeout: 5000 })).toBeInTheDocument();
		openSpy.mockRestore();
	});

	it('auto-connects after creating an authorization_code OAuth2 credential', async () => {
		resetApisStore([
			makeMockApi({
				vendor: 'slack',
				name: 'web',
				version: '1.0.0',
				displayName: 'Slack',
				securitySchemes: ['oauth2'],
				spec: {
					openapi: '3.0.0',
					info: { title: 'Slack', version: '1.0.0' },
					components: {
						securitySchemes: {
							OAuth: {
								type: 'oauth2',
								flows: {
									authorizationCode: {
										tokenUrl: 'https://slack.com/api/oauth.v2.access',
										authorizationUrl: 'https://slack.com/oauth/v2/authorize',
										scopes: {},
									},
								},
							},
						},
					},
				},
			}),
		]);
		// Stub the popup so the page poll observes the mock connect result.
		const fakePopup = { closed: false, close: () => {} } as unknown as Window;
		const openSpy = vi.spyOn(window, 'open').mockReturnValue(fakePopup);

		renderPage();
		const user = userEvent.setup();

		await screen.findByText('No credentials stored');
		await user.click(screen.getByRole('button', { name: /add your first credential/i }));
		await user.click(await screen.findByText('Slack'));

		// The form auto-shapes to OAuth2 and seeds the spec's token/authorize
		// URLs (so those fields are hidden). The copyable callback URL renders
		// from the providers discovery endpoint.
		expect(
			await screen.findByDisplayValue(/credentials\/oauth\/callback/i),
		).toBeInTheDocument();

		// Fill the still-required client credentials, then create.
		await user.type(await screen.findByLabelText(/Client ID/i), 'cid');
		await user.type(await screen.findByLabelText(/Client secret/i), 'csecret');
		await user.click(screen.getByRole('button', { name: 'Create credential' }));

		await expectCredentialCreatedToast();
		// Auto-connect fires because the credential carries an authorize URL.
		await waitFor(() => expect(openSpy).toHaveBeenCalled());
		expect(await screen.findByText('Connected', {}, { timeout: 5000 })).toBeInTheDocument();
		openSpy.mockRestore();
	});

	it('does NOT auto-connect a client_credentials OAuth2 credential (no authorize URL)', async () => {
		resetApisStore([
			makeMockApi({
				vendor: 'svc',
				name: 'api',
				version: '1.0.0',
				displayName: 'Service',
				securitySchemes: ['oauth2'],
				spec: {
					openapi: '3.0.0',
					info: { title: 'Service', version: '1.0.0' },
					components: {
						securitySchemes: {
							OAuth: {
								type: 'oauth2',
								flows: {
									clientCredentials: {
										tokenUrl: 'https://svc.example/oauth/token',
										scopes: {},
									},
								},
							},
						},
					},
				},
			}),
		]);
		const openSpy = vi.spyOn(window, 'open');

		renderPage();
		const user = userEvent.setup();

		await screen.findByText('No credentials stored');
		await user.click(screen.getByRole('button', { name: /add your first credential/i }));
		await user.click(await screen.findByText('Service'));

		await user.type(await screen.findByLabelText(/Client ID/i), 'cid');
		await user.type(await screen.findByLabelText(/Client secret/i), 'csecret');
		await user.click(screen.getByRole('button', { name: 'Create credential' }));

		await expectCredentialCreatedToast();
		// The credential is usable immediately; no browser flow should open.
		expect(openSpy).not.toHaveBeenCalled();
		openSpy.mockRestore();
	});

	it('opens the edit sheet when the credential card is clicked', async () => {
		resetCredentialsStore([
			makeMockCredential({ credential_id: 'c1', name: 'Clickable cred' }),
		]);
		renderWithProviders(<CredentialsPage />);
		const user = userEvent.setup();

		await screen.findByText('Clickable cred');
		// The whole card is a pointer click target that opens the edit sheet
		// (the explicit "Edit credential <name>" button is the a11y-facing
		// control; the full-card overlay is aria-hidden so we target it by id).
		await user.click(screen.getByTestId('credential-card-overlay'));

		expect(await screen.findByRole('heading', { name: 'Edit credential' })).toBeVisible();
	});

	it('keeps Save disabled until the edit form is changed', async () => {
		resetCredentialsStore([makeMockCredential({ credential_id: 'c1', name: 'Editable cred' })]);
		renderWithProviders(<CredentialsPage />);
		const user = userEvent.setup();

		await screen.findByText('Editable cred');
		await user.click(screen.getByRole('button', { name: 'Edit credential Editable cred' }));

		await screen.findByRole('heading', { name: 'Edit credential' });
		const save = screen.getByRole('button', { name: 'Save changes' });
		expect(save).toBeDisabled();

		const nameField = await screen.findByDisplayValue('Editable cred');
		await user.type(nameField, ' v2');
		expect(save).toBeEnabled();
	});

	it('has no critical a11y violations on the populated list', async () => {
		// Render through the real app shell (authenticated) so the page sits in
		// its painted layout context. The grid reveals cards with a framer
		// opacity transition; wait for it to settle (opacity 1) before running
		// axe, which otherwise flags the mid-animation opacity-0 card as a
		// contrast failure.
		setToken('mock-access-token');
		resetCredentialsStore([makeMockCredential({ credential_id: 'c1', name: 'Visible cred' })]);
		const { container } = renderWithProviders(
			<AuthProvider>
				<App />
			</AuthProvider>,
			{ route: '/credentials' },
		);
		const card = (await screen.findByText('Visible cred')).closest(
			'[data-testid="credential-card"]',
		)?.parentElement as HTMLElement;
		await waitFor(() => expect(getComputedStyle(card).opacity).toBe('1'));
		await checkA11y(container);
		clearToken();
	});
});
