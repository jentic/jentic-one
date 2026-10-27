/**
 * CreateCredentialFlow, inline surface — choosing the auth method from what the
 * spec declares (one → fact, several → choice, none → every supported method),
 * and the folded "Authentication · <method> · Change" line.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { http, HttpResponse } from 'msw';
import {
	checkA11y,
	renderWithProviders,
	screen,
	userEvent,
	waitFor,
	within,
} from '@/__tests__/test-utils';
import { worker } from '@/mocks/browser';
import { setToken } from '@/shared/api';
import {
	makeMockApi,
	resetApisStore,
	resetCredentialsStore,
} from '@/shared/credentials/mocks/handlers';
import { CreateCredentialFlow } from '@/shared/credentials/components/CreateCredentialFlow';
import { CREDENTIAL_TYPE_LABELS, CREDENTIAL_TYPE_ORDER } from '@/shared/credentials/api';
import type { SelectedApi } from '@/shared/credentials/api';

function apiWith(vendor: string, securitySchemes: Record<string, unknown>) {
	return makeMockApi({
		vendor,
		name: 'main',
		displayName: vendor,
		spec: {
			openapi: '3.0.0',
			info: { title: vendor, version: '1.0.0' },
			components: { securitySchemes },
		},
	});
}

const SINGLE = apiWith('single.test', {
	ApiKeyAuth: { type: 'apiKey', in: 'header', name: 'X-Api-Key' },
});
const SEVERAL = apiWith('several.test', {
	bearerAuth: { type: 'http', scheme: 'bearer' },
	ApiKeyAuth: { type: 'apiKey', in: 'header', name: 'X-Api-Key' },
});
/** Only a scheme the app can't map (OpenID Connect): nothing is detected. */
const UNKNOWN = apiWith('unknown.test', {
	oidc: { type: 'openIdConnect', openIdConnectUrl: 'https://unknown.test/.well-known' },
});

function pinned(vendor: string): SelectedApi {
	return { source: 'local', vendor, name: 'main', version: '1.0.0', label: vendor };
}

function renderInline(vendor: string, onCreated = vi.fn()) {
	return renderWithProviders(
		<CreateCredentialFlow
			open
			surface="inline"
			pinnedApi={pinned(vendor)}
			onClose={vi.fn()}
			onCreated={onCreated}
		/>,
	);
}

const methods = (): Promise<HTMLElement> =>
	screen.findByRole('radiogroup', { name: 'Authentication method' });
const fieldsBox = (): HTMLElement => screen.getByTestId('credential-method-fields');
const field = (label: RegExp): Promise<HTMLElement> => within(fieldsBox()).findByLabelText(label);
async function shown(el: HTMLElement): Promise<void> {
	await waitFor(() => expect(el).toBeVisible());
}
/** Let the fades finish, so axe measures colours at rest. */
const settle = (): Promise<void> => new Promise((r) => setTimeout(r, 450));
const save = (): HTMLElement => screen.getByRole('button', { name: /Save and continue/ });

function expectSaveBlocked(reason: RegExp): void {
	expect(save()).toHaveAttribute('aria-disabled', 'true');
	expect(save()).toHaveAccessibleDescription(reason);
}

describe('CreateCredentialFlow (inline) — choosing the authentication method', () => {
	beforeEach(() => {
		setToken('test-token');
		resetApisStore([SINGLE, SEVERAL, UNKNOWN]);
		resetCredentialsStore([]);
	});
	afterEach(() => {
		resetApisStore();
		resetCredentialsStore();
	});

	it('one method in the spec: shown as a fact, no choice, its fields straight away', async () => {
		renderInline('single.test');

		const row = await screen.findByTestId('auth-method-row');
		expect(row).toHaveTextContent(/^AuthenticationAPI key· from the API's spec/);
		expect(screen.queryByRole('radiogroup', { name: 'Authentication method' })).toBeNull();
		expect(screen.queryByRole('button', { name: /Change authentication method/ })).toBeNull();
		await shown(await field(/^API key/));
		expect(await field(/^Field name/)).toHaveValue('X-Api-Key');
		await shown(await field(/^Name/));
	});

	it('several in the spec: a choice among those only, nothing picked, nothing else shown', async () => {
		const { container } = renderInline('several.test');

		const group = await methods();
		const radios = within(group).getAllByRole('radio');
		expect(radios).toHaveLength(2);
		expect(within(group).getByRole('radio', { name: /Bearer token/ })).toBeInTheDocument();
		expect(within(group).getByRole('radio', { name: /API key/ })).toBeInTheDocument();
		for (const radio of radios) expect(radio).not.toBeChecked();
		expect(group).toHaveAccessibleDescription(/spec declares 2 methods/);

		expect(fieldsBox()).not.toBeVisible();
		expect(screen.queryAllByRole('textbox')).toHaveLength(0);
		expectSaveBlocked(/Choose an authentication method first/);
		await shown(radios[0]);
		await settle();
		await checkA11y(container);
	});

	it('nothing recognisable in the spec: every supported method, nothing picked', async () => {
		renderInline('unknown.test');

		const group = await methods();
		const radios = within(group).getAllByRole('radio');
		expect(radios).toHaveLength(CREDENTIAL_TYPE_ORDER.length);
		for (const type of CREDENTIAL_TYPE_ORDER) {
			expect(
				within(group).getByRole('radio', {
					name: new RegExp(`^${CREDENTIAL_TYPE_LABELS[type]}`),
				}),
			).not.toBeChecked();
		}
		expect(group).toHaveAccessibleDescription(/Choose how this credential signs in/);
		expect(fieldsBox()).not.toBeVisible();
		expect(screen.queryAllByRole('textbox')).toHaveLength(0);
		expectSaveBlocked(/Choose an authentication method first/);
	});

	it('a pick folds the list to one line with Change, and unfolds that method’s fields', async () => {
		const user = userEvent.setup();
		const { container } = renderInline('several.test');

		await user.click(within(await methods()).getByRole('radio', { name: /API key/ }));

		const chosen = await screen.findByTestId('auth-method-chosen');
		expect(chosen).toHaveTextContent('AuthenticationAPI key');
		const change = within(chosen).getByRole('button', {
			name: 'Change authentication method (API key)',
		});
		// The radios fold away, out of reach: focus lands on Change.
		await waitFor(() =>
			expect(screen.queryByRole('radiogroup', { name: 'Authentication method' })).toBeNull(),
		);
		expect(change).toHaveFocus();
		await shown(await field(/^API key/));
		expect(await field(/^Field name/)).toHaveValue('X-Api-Key');
		expect(await field(/^Name/)).toHaveValue('several.test');

		// The keyboard goes from Change into the fields — no hidden radio in between.
		await user.tab();
		expect(await field(/^Name/)).toHaveFocus();
		await user.tab({ shift: true });
		expect(change).toHaveFocus();
		await shown(await field(/^Name/));
		await settle();
		await checkA11y(container);
	});

	it('Change reopens the list on the current pick; another pick swaps the fields', async () => {
		const user = userEvent.setup();
		renderInline('several.test');
		await user.click(within(await methods()).getByRole('radio', { name: /API key/ }));
		await user.click(
			await screen.findByRole('button', { name: 'Change authentication method (API key)' }),
		);

		const group = await methods();
		const apiKey = within(group).getByRole('radio', { name: /API key/ });
		expect(apiKey).toBeChecked();
		await waitFor(() => expect(apiKey).toHaveFocus());
		expect(screen.queryByTestId('auth-method-chosen')).toBeNull();
		await shown(await field(/^API key/));

		await user.click(within(group).getByRole('radio', { name: /Bearer token/ }));
		expect(await screen.findByTestId('auth-method-chosen')).toHaveTextContent('Bearer token');
		await shown(await field(/^Token/));
		expect(within(fieldsBox()).queryByLabelText(/^API key/)).toBeNull();
	});

	it('picking the current method again, or Escape, folds back without a change', async () => {
		const user = userEvent.setup();
		renderInline('several.test');
		await user.click(within(await methods()).getByRole('radio', { name: /API key/ }));
		const change = (): Promise<HTMLElement> =>
			screen.findByRole('button', { name: 'Change authentication method (API key)' });

		await user.click(await change());
		await user.click(within(await methods()).getByRole('radio', { name: /API key/ }));
		expect(await screen.findByTestId('auth-method-chosen')).toHaveTextContent('API key');

		await user.click(await change());
		// Escape is the list's own key: it only lands once focus is back on the checked row.
		const reopened = within(await methods()).getByRole('radio', { name: /API key/ });
		await waitFor(() => expect(reopened).toHaveFocus());
		await user.keyboard('{Escape}');
		expect(await screen.findByTestId('auth-method-chosen')).toHaveTextContent('API key');
		await waitFor(async () => expect(await change()).toHaveFocus());
	});

	it('arrow keys move the pick without folding; Enter folds on it', async () => {
		const user = userEvent.setup();
		renderInline('unknown.test');
		const group = await methods();
		const bearer = within(group).getByRole('radio', { name: /Bearer token/ });
		bearer.focus();

		await user.keyboard('{ArrowDown}');
		const apiKey = within(group).getByRole('radio', { name: /API key/ });
		expect(apiKey).toBeChecked();
		expect(apiKey).toHaveFocus();
		expect(screen.queryByTestId('auth-method-chosen')).toBeNull();
		await shown(await field(/^API key/));

		await user.keyboard('{Enter}');
		expect(await screen.findByTestId('auth-method-chosen')).toHaveTextContent('API key');
	});

	it('typed values survive a method switch: the name, and each method’s own secret', async () => {
		const user = userEvent.setup();
		renderInline('several.test');
		await user.click(within(await methods()).getByRole('radio', { name: /API key/ }));
		const name = await field(/^Name/);
		await user.clear(name);
		await user.type(name, 'Prod key');
		await user.type(await field(/^API key/), 'sk_live_1');

		await user.click(
			await screen.findByRole('button', { name: 'Change authentication method (API key)' }),
		);
		await user.click(within(await methods()).getByRole('radio', { name: /Bearer token/ }));
		expect(await field(/^Token/)).toHaveValue('');
		expect(await field(/^Name/)).toHaveValue('Prod key');
		await user.click(
			await screen.findByRole('button', {
				name: 'Change authentication method (Bearer token)',
			}),
		);
		await user.click(within(await methods()).getByRole('radio', { name: /API key/ }));

		expect(await field(/^API key/)).toHaveValue('sk_live_1');
		expect(await field(/^Name/)).toHaveValue('Prod key');
	});

	it('Save waits for a method and its required fields, then creates with that method only', async () => {
		const creates: Record<string, unknown>[] = [];
		worker.use(
			http.post('/credentials', async ({ request }) => {
				creates.push((await request.clone().json()) as Record<string, unknown>);
				return undefined;
			}),
		);
		const onCreated = vi.fn();
		const user = userEvent.setup();
		renderInline('several.test', onCreated);

		await methods();
		expectSaveBlocked(/Choose an authentication method first/);
		await user.click(save());
		expect(creates).toHaveLength(0);

		await user.click(within(await methods()).getByRole('radio', { name: /Bearer token/ }));
		await field(/^Token/);
		expectSaveBlocked(/Token is required/);
		await user.click(save());
		expect(creates).toHaveLength(0);

		// A key typed for another method is not what gets saved.
		await user.click(
			screen.getByRole('button', { name: 'Change authentication method (Bearer token)' }),
		);
		await user.click(within(await methods()).getByRole('radio', { name: /API key/ }));
		await user.type(await field(/^API key/), 'sk_ignored');
		await user.click(
			screen.getByRole('button', { name: 'Change authentication method (API key)' }),
		);
		await user.click(within(await methods()).getByRole('radio', { name: /Bearer token/ }));
		await user.type(await field(/^Token/), 'tok_live');

		await waitFor(() => expect(save()).not.toHaveAttribute('aria-disabled'));
		await user.click(save());
		await waitFor(() => expect(onCreated).toHaveBeenCalledTimes(1));
		expect(creates).toHaveLength(1);
		expect(creates[0]).toMatchObject({ type: 'bearer_token', token: 'tok_live' });
		expect(creates[0]).not.toHaveProperty('key');
	});

	it('"no authentication" from the spec can still be overridden — onto the full choice, nothing picked', async () => {
		resetApisStore([apiWith('public.test', {})]);
		const user = userEvent.setup();
		renderInline('public.test');

		await user.click(await screen.findByTestId('credential-no-auth-override'));
		const group = await methods();
		expect(within(group).getAllByRole('radio')).toHaveLength(CREDENTIAL_TYPE_ORDER.length);
		for (const radio of within(group).getAllByRole('radio')) expect(radio).not.toBeChecked();
		expect(fieldsBox()).not.toBeVisible();
		expectSaveBlocked(/Choose an authentication method first/);
	});

	it('the drawer keeps its card picker, preselected as before', async () => {
		renderWithProviders(
			<CreateCredentialFlow
				open
				pinnedApi={pinned('several.test')}
				onClose={vi.fn()}
				onCreated={vi.fn()}
			/>,
		);
		const group = await screen.findByRole('radiogroup', { name: 'Authentication method' });
		expect(within(group).getByRole('radio', { name: /Bearer token/ })).toHaveAttribute(
			'aria-checked',
			'true',
		);
		await shown(screen.getByLabelText(/^Token/));
		expect(screen.queryByTestId('auth-method-row')).toBeNull();
	});
});

describe('CreateCredentialFlow (inline) — a single detected method is final', () => {
	beforeEach(() => {
		setToken('test-token');
		resetApisStore([SINGLE]);
		resetCredentialsStore([]);
	});
	afterEach(() => {
		resetApisStore();
		resetCredentialsStore();
	});

	it('offers no way to change it: no button on its line, no method list anywhere', async () => {
		const { container } = renderInline('single.test');
		const row = await screen.findByTestId('auth-method-row');
		await shown(await field(/^API key/));
		expect(within(row).queryByRole('button')).toBeNull();
		expect(within(row).queryByRole('link')).toBeNull();
		expect(screen.queryByRole('radiogroup', { name: 'Authentication method' })).toBeNull();
		expect(screen.queryAllByRole('radio')).toHaveLength(0);
		await settle();
		await checkA11y(container);
	});
});

describe('CreateCredentialFlow (inline) — the version a fresh catalog pick is saved for', () => {
	const SPEC_URL = 'https://specs.test/airbrake.io/main/4.0.0/openapi.json';
	const AIRBRAKE: SelectedApi = {
		source: 'catalog',
		registered: false,
		vendor: 'airbrake.io',
		name: 'airbrake.io',
		version: '1.0.0',
		label: 'airbrake.io',
		apiId: 'airbrake.io',
		specUrl: SPEC_URL,
	};
	function serveSpec(version: string | undefined): void {
		worker.use(
			http.get(SPEC_URL, () =>
				HttpResponse.json({
					openapi: '3.0.0',
					info: { title: 'Airbrake API', ...(version ? { version } : {}) },
					components: {
						securitySchemes: { apiKey: { type: 'apiKey', in: 'query', name: 'key' } },
					},
				}),
			),
		);
	}
	const toggle = (): Promise<HTMLElement> =>
		screen.findByRole('group', { name: 'Use this credential for' });

	beforeEach(() => {
		setToken('test-token');
		resetApisStore([]);
		resetCredentialsStore([]);
	});

	it('offers the version its import will register, Any version chosen', async () => {
		serveSpec('4.0.0');
		renderWithProviders(
			<CreateCredentialFlow
				open
				surface="inline"
				pinnedApi={AIRBRAKE}
				onClose={vi.fn()}
				onCreated={vi.fn()}
			/>,
		);
		const group = await toggle();
		expect(within(group).getByRole('button', { name: 'Version 4.0.0' })).toHaveAttribute(
			'aria-pressed',
			'false',
		);
		expect(within(group).getByRole('button', { name: 'Any version' })).toHaveAttribute(
			'aria-pressed',
			'true',
		);
	});

	it('a pin chosen before the import exists is saved on the credential', async () => {
		serveSpec('4.0.0');
		const creates: { api?: Record<string, unknown> }[] = [];
		worker.use(
			http.post('/catalog/*', () =>
				HttpResponse.json(
					{ job_id: 'job_ab', status: 'queued', _links: { self: '/jobs/job_ab' } },
					{ status: 202 },
				),
			),
			http.post('/credentials', async ({ request }) => {
				creates.push((await request.clone().json()) as { api?: Record<string, unknown> });
				return undefined;
			}),
		);
		const onCreated = vi.fn();
		const user = userEvent.setup();
		renderWithProviders(
			<CreateCredentialFlow
				open
				surface="inline"
				pinnedApi={AIRBRAKE}
				onClose={vi.fn()}
				onCreated={onCreated}
			/>,
		);
		await user.click(within(await toggle()).getByRole('button', { name: 'Version 4.0.0' }));
		await screen.findByTestId('credential-method-fields');
		await user.type(await field(/^API key/), 'ab_key');
		await user.click(save());
		await waitFor(() => expect(onCreated).toHaveBeenCalledTimes(1));
		expect(creates[0]!.api).toMatchObject({
			vendor: 'airbrake.io',
			name: 'airbrake-io',
			version: '4.0.0',
			catalog_api_id: 'airbrake.io',
		});
	});

	it('a spec that names no version offers no pin', async () => {
		serveSpec(undefined);
		renderWithProviders(
			<CreateCredentialFlow
				open
				surface="inline"
				pinnedApi={AIRBRAKE}
				onClose={vi.fn()}
				onCreated={vi.fn()}
			/>,
		);
		await screen.findByTestId('credential-method-fields');
		await field(/^API key/);
		expect(screen.queryByRole('group', { name: 'Use this credential for' })).toBeNull();
		expect(screen.getByTestId('selected-api-summary')).toHaveTextContent(
			'Every version of this API',
		);
	});
});
