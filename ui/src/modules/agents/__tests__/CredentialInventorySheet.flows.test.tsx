/**
 * CredentialInventorySheet — the credential lifecycle end to end, now that the
 * inventory is the only credentials surface (the standalone Credentials page was
 * retired). Create (manual, guided, catalog import), OAuth connect and
 * auto-connect, edit, delete, and an agent's approval link all run through the
 * sheet on the Agents page.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useLocation } from 'react-router';
import { page, userEvent as browserUser } from 'vitest/browser';
import { worker } from '@/mocks/browser';
import {
	createErrorHandler,
	renderWithProviders,
	screen,
	userEvent,
	waitFor,
	within,
} from '@/__tests__/test-utils';
import { setToken } from '@/shared/api';
import { clearAllToasts, Toaster } from '@/shared/ui';
import { resetAgentsStore } from '@/modules/agents/mocks/handlers';
import { CredentialType } from '@/shared/credentials/api';
import {
	makeMockApi,
	makeMockCatalogEntry,
	makeMockCredential,
	resetApisStore,
	resetCredentialsStore,
} from '@/shared/credentials/mocks/handlers';
import AgentsPage from '@/modules/agents/pages/AgentsPage';

function LocationProbe() {
	const location = useLocation();
	return <div data-testid="location-search">{location.search}</div>;
}

/** The Agents page with the inventory opened through its deep link. */
function renderInventory(route = '/?credentials=1') {
	return renderWithProviders(
		<>
			<AgentsPage />
			<LocationProbe />
			<Toaster />
		</>,
		{ route },
	);
}

function inventory() {
	return within(screen.getAllByTestId('sheet-primitive')[0]);
}

/**
 * Assert that a "Credential created" success toast eventually appears. The toast
 * store is global, so older toasts may still be on screen — scan them all.
 */
async function expectCredentialCreatedToast(): Promise<void> {
	await waitFor(() => {
		const toasts = screen.queryAllByTestId('toast');
		expect(toasts.some((t) => t.textContent?.includes('Credential created'))).toBe(true);
	});
}

/** Stub the popup: a handle that never closes on its own, so the connect poll
 * observes the mock connect result first. */
function stubPopup() {
	const fakePopup = { closed: false, close: () => {} } as unknown as Window;
	return vi.spyOn(window, 'open').mockReturnValue(fakePopup);
}

async function openCreateWizard(user: ReturnType<typeof userEvent.setup>): Promise<void> {
	await inventory().findByText('No credentials stored');
	await user.click(inventory().getByRole('button', { name: /add your first credential/i }));
}

describe('CredentialInventorySheet — credential flows', () => {
	beforeEach(async () => {
		await page.viewport(1280, 900);
		setToken('test-token');
		resetAgentsStore();
		resetCredentialsStore();
		resetApisStore([]);
		clearAllToasts();
	});
	afterEach(() => {
		resetCredentialsStore();
		resetApisStore();
		clearAllToasts();
	});

	it('renders a connect-flow pending credential distinctly (Pending sign-in badge)', async () => {
		// An agent-driven connect mints its credential row upfront; until the vendor
		// round-trip completes it is a pending shell that must not read as usable.
		resetCredentialsStore([
			makeMockCredential({
				credential_id: 'c_pending',
				name: 'GitHub (connecting…)',
				type: CredentialType.OAUTH2,
				provider: 'direct_oauth2',
				details: { grant_type: 'device_code', connected: false },
			}),
			makeMockCredential({ credential_id: 'c_live', name: 'Live token' }),
		]);
		renderInventory();
		expect(await inventory().findByText('GitHub (connecting…)')).toBeInTheDocument();
		expect(inventory().getAllByText('Pending sign-in')).toHaveLength(1);
	});

	it('surfaces a load error', async () => {
		worker.use(createErrorHandler('get', '/credentials', { status: 500 }));
		renderInventory();
		expect(await inventory().findByRole('alert')).toBeVisible();
	});

	it('explains credentials from its help, and Escape closes only the help', async () => {
		renderInventory();
		const user = userEvent.setup();

		await user.click(await inventory().findByRole('button', { name: 'About credentials' }));
		const help = await screen.findByRole('dialog', { name: 'About Credentials' });
		expect(within(help).getByText('Secrets are write-only')).toBeVisible();

		await browserUser.keyboard('{Escape}');
		await waitFor(() =>
			expect(
				screen.queryByRole('dialog', { name: 'About Credentials' }),
			).not.toBeInTheDocument(),
		);
		expect(screen.getByRole('heading', { name: 'Credentials' })).toBeInTheDocument();
	});

	it('opens its own help from the shortcut while the sheet is open', async () => {
		renderInventory();
		await inventory().findByRole('button', { name: 'About credentials' });

		await browserUser.keyboard('{Control>}/{/Control}');
		expect(await screen.findByRole('dialog', { name: 'About Credentials' })).toBeVisible();
		expect(screen.queryByRole('dialog', { name: 'About Agents' })).not.toBeInTheDocument();
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
		renderInventory();
		const user = userEvent.setup();

		await inventory().findByText('Stripe key');
		expect(inventory().getByText('GitHub token')).toBeInTheDocument();

		// Narrow to API keys → the bearer token drops out.
		await user.click(inventory().getByRole('button', { name: 'API key' }));
		expect(inventory().getByText('Stripe key')).toBeInTheDocument();
		await waitFor(() =>
			expect(inventory().queryByText('GitHub token')).not.toBeInTheDocument(),
		);
	});

	it('filters the list by name as the operator types', async () => {
		resetCredentialsStore([
			makeMockCredential({ credential_id: 'c1', name: 'Stripe key' }),
			makeMockCredential({ credential_id: 'c2', name: 'GitHub token' }),
		]);
		renderInventory();
		const user = userEvent.setup();

		await inventory().findByText('Stripe key');
		await user.type(inventory().getByLabelText('Filter credentials'), 'github');
		await waitFor(() => expect(inventory().queryByText('Stripe key')).not.toBeInTheDocument());
		expect(inventory().getByText('GitHub token')).toBeInTheDocument();
	});

	it('opens the edit sheet when the credential card is clicked', async () => {
		resetCredentialsStore([
			makeMockCredential({ credential_id: 'c1', name: 'Clickable cred' }),
		]);
		renderInventory();
		const user = userEvent.setup();

		await inventory().findByText('Clickable cred');
		// The full-card overlay is the pointer target (aria-hidden; the "Edit
		// credential <name>" button is the accessible control).
		await user.click(inventory().getByTestId('credential-card-overlay'));

		expect(await screen.findByRole('heading', { name: 'Edit credential' })).toBeVisible();
	});

	it('creates a credential via manual entry and surfaces a success toast', async () => {
		renderInventory();
		const user = userEvent.setup();
		await openCreateWizard(user);

		// The guided picker is the first step; drop into manual entry.
		await user.click(await screen.findByRole('button', { name: /Enter manually/i }));
		await user.type(screen.getByPlaceholderText('Production API key'), 'CI token');
		await user.type(screen.getByPlaceholderText('acme'), 'acme');
		await user.type(screen.getByPlaceholderText('sk_live_…'), 'super-secret-value');
		await user.click(screen.getByRole('button', { name: 'Create credential' }));

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
							ApiKeyAuth: { type: 'apiKey', in: 'header', name: 'X-Acme-Key' },
						},
					},
				},
			}),
		]);
		renderInventory();
		const user = userEvent.setup();
		await openCreateWizard(user);

		await user.click(await screen.findByText('Acme'));

		// Name prefilled from the API; the scheme drove the type to API_KEY with the
		// field name pre-filled from the spec.
		const nameInput = (await screen.findByPlaceholderText(
			'Production API key',
		)) as HTMLInputElement;
		expect(nameInput.value).toBe('Acme');
		const fieldNameInput = (await screen.findByPlaceholderText(
			'X-Api-Key',
		)) as HTMLInputElement;
		await waitFor(() => expect(fieldNameInput.value).toBe('X-Acme-Key'));

		const apiKeyField = (screen.getAllByDisplayValue('') as HTMLInputElement[]).find(
			(el) => el.type === 'password',
		);
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
							securitySchemes: { Bearer: { type: 'http', scheme: 'bearer' } },
						},
					},
				},
			],
		);
		renderInventory();
		const user = userEvent.setup();
		await openCreateWizard(user);

		await user.type(screen.getByLabelText('Search APIs'), 'acme');
		// A bare-domain catalog entry reads `acme.com` verbatim (also the mono
		// subtitle, so pick the first match).
		await user.click((await screen.findAllByText('acme.com'))[0]);
		expect(await screen.findByText(/imports on save/i)).toBeInTheDocument();

		const tokenInput = (await screen.findByPlaceholderText('sk_live_…')) as HTMLInputElement;
		await user.type(tokenInput, 'token-from-catalog');
		await user.click(screen.getByRole('button', { name: 'Create credential' }));

		await expectCredentialCreatedToast();
	});

	it('deletes a credential after confirmation', async () => {
		resetCredentialsStore([makeMockCredential({ credential_id: 'c1', name: 'Doomed' })]);
		renderInventory();
		const user = userEvent.setup();

		await inventory().findByText('Doomed');
		await user.click(inventory().getByRole('button', { name: 'Delete credential Doomed' }));
		const confirm = within(await screen.findByRole('dialog', { name: 'Delete credential' }));
		// Type-to-confirm gates the destructive action.
		await user.type(confirm.getByLabelText(/Type delete to confirm/i), 'delete');
		await user.click(confirm.getByRole('button', { name: 'Delete credential' }));

		await waitFor(() => expect(inventory().queryByText('Doomed')).not.toBeInTheDocument());
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
		const openSpy = stubPopup();
		renderInventory();
		const user = userEvent.setup();

		await inventory().findByText('Slack OAuth');
		await user.click(inventory().getByRole('button', { name: 'Connect Slack OAuth' }));

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
		const openSpy = stubPopup();
		renderInventory();
		const user = userEvent.setup();
		await openCreateWizard(user);
		await user.click(await screen.findByText('Slack'));

		// The copyable callback URL renders from the providers discovery endpoint.
		expect(
			await screen.findByDisplayValue(/credentials\/oauth\/callback/i),
		).toBeInTheDocument();
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
		renderInventory();
		const user = userEvent.setup();
		await openCreateWizard(user);
		await user.click(await screen.findByText('Service'));

		await user.type(await screen.findByLabelText(/Client ID/i), 'cid');
		await user.type(await screen.findByLabelText(/Client secret/i), 'csecret');
		await user.click(screen.getByRole('button', { name: 'Create credential' }));

		await expectCredentialCreatedToast();
		// Usable immediately; no browser flow should open.
		expect(openSpy).not.toHaveBeenCalled();
		openSpy.mockRestore();
	});

	it('keeps Save disabled until the edit form is changed', async () => {
		resetCredentialsStore([makeMockCredential({ credential_id: 'c1', name: 'Editable cred' })]);
		renderInventory();
		const user = userEvent.setup();

		await inventory().findByText('Editable cred');
		await user.click(
			inventory().getByRole('button', { name: 'Edit credential Editable cred' }),
		);
		await screen.findByRole('heading', { name: 'Edit credential' });
		const save = screen.getByRole('button', { name: 'Save changes' });
		expect(save).toBeDisabled();

		await user.type(await screen.findByDisplayValue('Editable cred'), ' v2');
		expect(save).toBeEnabled();
	});

	it('locks the api_key field name and location in the edit sheet (#589)', async () => {
		resetCredentialsStore([
			makeMockCredential({
				credential_id: 'ck1',
				name: 'Key cred',
				type: CredentialType.API_KEY,
				details: { field_name: 'appid', location: 'query', hint: '••••' },
			}),
		]);
		renderInventory();
		const user = userEvent.setup();

		await inventory().findByText('Key cred');
		await user.click(inventory().getByRole('button', { name: 'Edit credential Key cred' }));
		await screen.findByRole('heading', { name: 'Edit credential' });

		expect(await screen.findByDisplayValue('appid')).toBeDisabled();
	});

	describe("an agent's approval link", () => {
		it('opens the wizard in approve mode, and closing it drops the link params', async () => {
			renderInventory('/?agent=agnt_active_1&approve=sess_1&poll_token=tok_1');

			expect(
				await screen.findByRole('dialog', { name: /^Approve integration$/ }),
			).toBeVisible();

			await browserUser.keyboard('{Escape}');
			await waitFor(() =>
				expect(
					screen.queryByRole('dialog', { name: /^Approve integration$/ }),
				).not.toBeInTheDocument(),
			);
			// Spent, so a reload doesn't reopen a stale prompt; the selection survives.
			// Wait on the params themselves: `?agent=` is in the URL before the clear too.
			await waitFor(() => {
				const params = new URLSearchParams(
					screen.getByTestId('location-search').textContent ?? '',
				);
				expect(params.has('approve')).toBe(false);
				expect(params.has('poll_token')).toBe(false);
				expect(params.get('agent')).toBe('agnt_active_1');
			});
			// Backing out of the approval leaves the operator in the inventory.
			expect(screen.getByRole('heading', { name: 'Credentials' })).toBeInTheDocument();
		});

		it('opens from the link exactly as the backend mints it (no agent selected)', async () => {
			renderInventory('/?approve=sess_1&poll_token=tok_1');
			expect(
				await screen.findByRole('dialog', { name: /^Approve integration$/ }),
			).toBeVisible();
		});

		it('ignores a link missing its poll token', async () => {
			renderInventory('/?approve=sess_1');
			await screen.findByRole('button', { name: 'Credentials' });
			expect(
				screen.queryByRole('dialog', { name: /^Approve integration$/ }),
			).not.toBeInTheDocument();
		});
	});
});
