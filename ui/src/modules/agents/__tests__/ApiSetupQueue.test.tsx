/**
 * ApiSetupQueue — one pane per API (① Credential, ② Access, footer "Add <API>").
 * Pins what needs the bind endpoint and the inline form: nothing binds until the
 * footer adds, one failed POST doesn't take the others, a dismissal hands the rest back.
 */
import { useState } from 'react';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { http, HttpResponse } from 'msw';
import { page } from 'vitest/browser';
import {
	renderWithProviders,
	screen,
	waitFor,
	within,
	userEvent,
	checkA11y,
} from '@/__tests__/test-utils';
import { worker } from '@/mocks/browser';
import { setToken } from '@/shared/api';
import {
	makeMockApi,
	makeMockCredential,
	resetApisStore,
	resetCredentialsStore,
} from '@/shared/credentials/mocks/handlers';
import { resetAgentsStore } from '@/modules/agents/mocks/handlers';
import { CredentialType, type Credential, type SelectedApi } from '@/shared/credentials/api';
import {
	ApiSetupQueue,
	type ApiSetupQueueProps,
} from '@/modules/agents/components/flat/ApiSetupQueue';
import type { PreflightItem, PreflightOutcome } from '@/modules/agents/lib/apiPreflight';

/** An agent with no seeded bindings, so every bind in these specs is the first. */
const AGENT_ID = 'agnt_disabled_1';

function makeCredential(over: Partial<Credential> = {}): Credential {
	return makeMockCredential({
		credential_id: 'cred_stripe',
		name: 'Stripe key',
		type: CredentialType.API_KEY,
		api: { vendor: 'stripe.com', name: 'main', version: '1.0.0' },
		...over,
	});
}

function makeItem(
	vendor: string,
	outcome: PreflightOutcome,
	over: Partial<PreflightItem> = {},
): PreflightItem {
	const api: SelectedApi = {
		source: 'local',
		vendor,
		name: 'main',
		version: '1.0.0',
		label: vendor.replace(/\..*$/, ''),
	};
	return {
		key: `${vendor}/main`,
		api,
		outcome,
		covering: [],
		existing: [],
		importsApi: false,
		...over,
	};
}

/** A `choose` item that exactly one existing credential covers. */
function coveredItem(vendor: string, credentialId: string): PreflightItem {
	return makeItem(vendor, 'choose', {
		covering: [
			makeCredential({
				credential_id: credentialId,
				name: `${vendor} key`,
				api: { vendor, name: 'main', version: '1.0.0' },
			}),
		],
	});
}

/** Hosts the queue the way the agents surface does: the batch and the open flag
 * live outside it, so a spec can close mid-way and reopen on the remainder. */
function QueueHarness({
	items,
	onClosed,
	onBack,
}: {
	items: PreflightItem[];
	onClosed?: (remaining: PreflightItem[]) => void;
	onBack?: ApiSetupQueueProps['onBack'];
}) {
	const [batch, setBatch] = useState(items);
	const [open, setOpen] = useState(true);
	return (
		<>
			<button type="button" onClick={(): void => setOpen(true)}>
				Reopen
			</button>
			<ApiSetupQueue
				open={open}
				agentId={AGENT_ID}
				agentName="Support bot"
				items={batch}
				onClose={(remaining): void => {
					setBatch(remaining);
					setOpen(false);
					onClosed?.(remaining);
				}}
				onBack={onBack}
			/>
		</>
	);
}

function progressRows(): HTMLElement[] {
	return screen.queryAllByTestId('queue-progress-row');
}

function rowFor(label: string): HTMLElement {
	const row = progressRows().find((r) => r.textContent?.startsWith(label));
	if (!row) throw new Error(`No progress row for ${label}. Rows: ${progressRows().length}`);
	return row;
}

function credentialSection(): HTMLElement {
	return screen.getByTestId('queue-credential-section');
}

function accessSection(): HTMLElement {
	return screen.getByTestId('queue-access-step');
}

function addButton(): HTMLElement {
	return screen.getByTestId('queue-add');
}

/** A blocked "Add" stays focusable: `aria-disabled` plus a described reason. */
function expectAddBlocked(reason?: RegExp): void {
	expect(addButton()).toHaveAttribute('aria-disabled', 'true');
	if (reason) expect(addButton()).toHaveAccessibleDescription(reason);
}

function expectAddReady(): void {
	expect(addButton()).not.toHaveAttribute('aria-disabled');
}

/** Warning ochre tints the glyph only: ochre text on an ochre fill fails axe contrast. */
function expectCautionTone(row: HTMLElement): void {
	expect(row.querySelector('.text-warning')).not.toBeNull();
	expect(row.className).not.toContain('text-warning');
}

async function accessReady(): Promise<HTMLElement> {
	await waitFor(() => expect(accessSection()).toHaveAttribute('data-ready', 'true'));
	return accessSection();
}

async function addWithAccess(
	user: ReturnType<typeof userEvent.setup>,
	preset: RegExp = /^Read only$/,
): Promise<void> {
	const step = await accessReady();
	await user.click(within(step).getByRole('radio', { name: preset }));
	await waitFor(() => expectAddReady());
	await user.click(addButton());
}

async function writeRule(
	user: ReturnType<typeof userEvent.setup>,
	step: HTMLElement,
	method: string,
): Promise<void> {
	const editor = within(step).getByTestId('queue-access-custom-editor');
	// With no rules yet, the editor opens straight onto the new-rule form.
	await waitFor(() => expect(within(editor).getByRole('button', { name: method })).toBeVisible());
	await user.click(within(editor).getByRole('button', { name: method }));
	await user.click(within(editor).getByRole('button', { name: 'Add' }));
}

/** Records every rules PUT while still letting the store handler answer. */
function watchRules(): { calls: { agentId: string; credentialId: string; body: unknown }[] } {
	const calls: { agentId: string; credentialId: string; body: unknown }[] = [];
	worker.use(
		http.put('/credentials/:cid/agents/:aid/permissions', async ({ params, request }) => {
			calls.push({
				agentId: params.aid as string,
				credentialId: params.cid as string,
				body: await request.clone().json(),
			});
			return undefined;
		}),
	);
	return { calls };
}

/** Records every bind POST while still letting the store handler answer. */
function watchBinds(): { calls: { agentId: string; body: unknown }[] } {
	const calls: { agentId: string; body: unknown }[] = [];
	worker.use(
		http.post('/agents/:id/credentials', async ({ params, request }) => {
			calls.push({
				agentId: params.id as string,
				body: await request.clone().json(),
			});
			// Fall through to the store handler, which creates the binding.
			return undefined;
		}),
	);
	return { calls };
}

/** An API whose spec declares one bearer scheme, so the inline form seeds it. */
const BEARER_API = makeMockApi({
	vendor: 'acme.com',
	name: 'main',
	displayName: 'acme',
	securitySchemes: ['bearer'],
	spec: {
		openapi: '3.0.0',
		info: { title: 'Acme', version: '1.0.0' },
		components: { securitySchemes: { bearerAuth: { type: 'http', scheme: 'bearer' } } },
	},
});

async function tokenField(): Promise<HTMLInputElement> {
	const form = await screen.findByTestId('queue-credential-form');
	return (await within(form).findByLabelText(/^Token/)) as HTMLInputElement;
}

describe('ApiSetupQueue — one pane per API: ① Credential, then ② Access', () => {
	beforeEach(async () => {
		await page.viewport(1280, 900);
		setToken('test-token');
		resetAgentsStore();
		resetCredentialsStore([makeCredential()]);
		resetApisStore([
			makeMockApi({ vendor: 'stripe.com', name: 'main', displayName: 'stripe' }),
			BEARER_API,
		]);
	});

	it('never binds a lone matching credential on its own — it is preselected, and the footer adds', async () => {
		const { calls } = watchBinds();
		const rules = watchRules();
		const user = userEvent.setup();
		renderWithProviders(<QueueHarness items={[coveredItem('stripe.com', 'cred_stripe')]} />);

		// The pane stops on it: a match is API identity, not account, so the
		// operator confirms it (or picks a new one) before anything binds.
		const options = await within(
			await screen.findByTestId('queue-credential-section'),
		).findByRole('group', { name: 'Use your credential for stripe, or add a new one' });
		expect(within(options).getAllByRole('radio')).toHaveLength(2);
		expect(within(options).getByRole('radio', { name: /stripe\.com key/ })).toBeChecked();
		expect(
			within(options).getByRole('radio', { name: /Add a new credential/ }),
		).not.toBeChecked();
		expect(rowFor('stripe')).toHaveAttribute('data-selected', 'true');
		expect(rowFor('stripe')).toHaveTextContent('In progress');

		const step = await accessReady();
		const group = within(step).getByRole('radiogroup', { name: 'What can Support bot call?' });
		expect(within(group).getByRole('radio', { name: /^Read only$/ })).toBeChecked();
		expect(addButton()).toHaveTextContent('Add stripe');
		expect(calls).toHaveLength(0);

		await user.click(addButton());
		await waitFor(() => expect(calls).toHaveLength(1));
		expect(calls[0].agentId).toBe(AGENT_ID);
		expect(calls[0].body).toEqual({ credential_id: 'cred_stripe' });
		await waitFor(() => expect(rules.calls).toHaveLength(1));
		expect(rules.calls[0]).toMatchObject({
			agentId: AGENT_ID,
			credentialId: 'cred_stripe',
			body: [{ effect: 'allow', methods: ['GET'] }],
		});
		expect(await screen.findByTestId('queue-done-pane')).toHaveTextContent('1 API added');
		expect(screen.getByText('Each API was added with its access rules.')).toBeInTheDocument();
		expect(rowFor('stripe')).toHaveAttribute('data-status', 'added');
		expect(screen.getByText('1 of 1 added')).toBeInTheDocument();
	});

	it('several covering credentials: nothing is guessed, ② waits, and the pick is what binds', async () => {
		const { calls } = watchBinds();
		const user = userEvent.setup();
		const production = makeCredential({
			credential_id: 'cred_prod',
			name: 'Stripe — Production',
		});
		const sandbox = makeCredential({ credential_id: 'cred_sandbox', name: 'Stripe — Sandbox' });
		renderWithProviders(
			<QueueHarness
				items={[makeItem('stripe.com', 'choose', { covering: [production, sandbox] })]}
			/>,
		);

		const options = await screen.findByRole('group', {
			name: 'Pick one of your 2 credentials for stripe',
		});
		// "None of these" is answerable without dropping the API.
		expect(within(options).getAllByRole('radio')).toHaveLength(3);
		for (const radio of within(options).getAllByRole('radio')) expect(radio).not.toBeChecked();
		// Two accounts of one vendor are not interchangeable: ② and the footer wait.
		expect(accessSection()).not.toHaveAttribute('data-ready');
		expect(
			within(accessSection()).getByText('Available once a credential is chosen.'),
		).toBeInTheDocument();
		expectAddBlocked(/Choose a credential first/);

		await user.click(within(options).getByRole('radio', { name: /Stripe — Sandbox/ }));
		expect(
			await within(credentialSection()).findByTestId('queue-chosen-credential'),
		).toHaveTextContent('Stripe — Sandbox');
		await addWithAccess(user);
		await waitFor(() => expect(calls).toHaveLength(1));
		expect(calls[0].body).toEqual({ credential_id: 'cred_sandbox' });
		await waitFor(() => expect(rowFor('stripe')).toHaveAttribute('data-status', 'added'));
	});

	it('"Add a new credential" folds the existing list to one line, the form right under it', async () => {
		const user = userEvent.setup();
		const production = makeCredential({
			credential_id: 'cred_prod',
			name: 'Stripe — Production',
		});
		const sandbox = makeCredential({ credential_id: 'cred_sandbox', name: 'Stripe — Sandbox' });
		renderWithProviders(
			<QueueHarness
				items={[makeItem('stripe.com', 'choose', { covering: [production, sandbox] })]}
			/>,
		);
		const options = await screen.findByRole('group', {
			name: /Pick one of your 2 credentials/,
		});
		await user.click(within(options).getByRole('radio', { name: /Add a new credential/ }));

		const folded = await within(options).findByTestId('queue-existing-collapsed');
		expect(folded).toHaveTextContent('2 existing credentials·Show');
		await waitFor(() =>
			expect(within(options).queryByRole('radio', { name: /Stripe — Sandbox/ })).toBeNull(),
		);
		expect(await screen.findByTestId('queue-credential-form')).toBeVisible();
		expect(accessSection()).not.toHaveAttribute('data-ready');
		expectAddBlocked(/Save the credential first/);

		await user.click(folded);
		expect(
			await within(options).findByRole('radio', { name: /Stripe — Sandbox/ }),
		).toBeInTheDocument();
		await waitFor(() => expect(screen.getByTestId('queue-credential-form')).not.toBeVisible());
		expect(within(options).queryByTestId('queue-existing-collapsed')).toBeNull();
	});

	it('a lone credential folds too, and switching back and forth keeps what was typed', async () => {
		resetCredentialsStore([
			makeCredential({ api: { vendor: 'acme.com', name: 'main', version: '1.0.0' } }),
		]);
		const { calls } = watchBinds();
		const user = userEvent.setup();
		renderWithProviders(
			<QueueHarness
				items={[
					makeItem('acme.com', 'choose', {
						covering: [
							makeCredential({
								credential_id: 'cred_acme',
								name: 'Acme key',
								api: { vendor: 'acme.com', name: 'main', version: '1.0.0' },
							}),
						],
					}),
				]}
			/>,
		);
		const options = await screen.findByRole('group', { name: /Use your credential for acme/ });
		await user.click(within(options).getByRole('radio', { name: /Add a new credential/ }));
		expect(await within(options).findByTestId('queue-existing-collapsed')).toHaveTextContent(
			'1 existing credential·Show',
		);
		await user.type(await tokenField(), 'tok_draft');

		await user.click(within(options).getByTestId('queue-existing-collapsed'));
		await user.click(await within(options).findByRole('radio', { name: /Acme key/ }));
		expect(
			await within(credentialSection()).findByTestId('queue-chosen-credential'),
		).toHaveTextContent('Acme key');
		await accessReady();

		await user.click(within(credentialSection()).getByRole('button', { name: 'Change' }));
		await user.click(
			await within(credentialSection()).findByRole('radio', { name: /Add a new credential/ }),
		);
		expect(await tokenField()).toHaveValue('tok_draft');
		expect(calls).toHaveLength(0);
	});

	it('with no credential of yours there is nothing to fold: the form is open from the start', async () => {
		renderWithProviders(<QueueHarness items={[makeItem('acme.com', 'form')]} />);

		const form = await screen.findByTestId('queue-credential-form');
		expect(form).toBeVisible();
		expect(
			within(credentialSection()).queryByRole('radio', { name: /Add a new credential/ }),
		).toBeNull();
		expect(screen.queryByTestId('queue-existing-collapsed')).toBeNull();
		// The real create form, inline: the API is the queue's premise, not its question.
		expect(within(form).getByTestId('credential-inline-form')).toBeInTheDocument();
		expect(within(form).queryByRole('button', { name: 'Back' })).toBeNull();
		expect(within(form).getByRole('button', { name: /Save and continue/ })).toBeVisible();
	});

	it('creates a credential inline: Save folds ① to its line and opens ②, then the footer adds', async () => {
		const creates: unknown[] = [];
		worker.use(
			http.post('/credentials', async ({ request }) => {
				creates.push(await request.clone().json());
				return undefined;
			}),
		);
		const { calls } = watchBinds();
		const rules = watchRules();
		const user = userEvent.setup();
		renderWithProviders(<QueueHarness items={[makeItem('acme.com', 'form')]} />);

		const form = await screen.findByTestId('queue-credential-form');
		// Seeded from the spec: the name and the one auth method it declares.
		const name = (await within(form).findByLabelText(/^Name/)) as HTMLInputElement;
		await waitFor(() => expect(name.value).toBe('acme'));
		await user.type(await tokenField(), 'tok_live_123');
		expectAddBlocked(/Save the credential first/);
		await user.click(within(form).getByRole('button', { name: /Save and continue/ }));

		await waitFor(() => expect(creates).toHaveLength(1));
		const chosen = await within(credentialSection()).findByTestId('queue-chosen-credential');
		expect(chosen).toHaveTextContent('acme');
		expect(chosen).toHaveTextContent('created just now');
		await accessReady();
		// Saved is not bound: the bind waits for its access.
		expect(calls).toHaveLength(0);

		await addWithAccess(user, /Full access/);
		await waitFor(() => expect(rowFor('acme')).toHaveAttribute('data-status', 'added'));
		expect(calls).toHaveLength(1);
		expect(rules.calls.map((c) => c.body)).toEqual([[{ effect: 'allow', path: '.*' }]]);
	});

	it('Change reopens the chooser with the new credential in it; Keep folds back, access kept', async () => {
		const { calls } = watchBinds();
		const user = userEvent.setup();
		renderWithProviders(
			<QueueHarness
				items={[
					makeItem('acme.com', 'choose', {
						covering: [
							makeCredential({
								credential_id: 'cred_acme_old',
								name: 'Acme old key',
								api: { vendor: 'acme.com', name: 'main', version: '1.0.0' },
							}),
						],
					}),
				]}
			/>,
		);
		const options = await screen.findByRole('group', { name: /Use your credential for acme/ });
		await user.click(within(options).getByRole('radio', { name: /Add a new credential/ }));
		await user.clear(
			await within(await screen.findByTestId('queue-credential-form')).findByLabelText(
				/^Name/,
			),
		);
		await user.type(
			within(screen.getByTestId('queue-credential-form')).getByLabelText(/^Name/),
			'Acme new',
		);
		await user.type(await tokenField(), 'tok_new');
		await user.click(screen.getByRole('button', { name: /Save and continue/ }));

		const step = await accessReady();
		await user.click(within(step).getByRole('radio', { name: /Full access/ }));
		await user.click(
			within(
				await within(credentialSection()).findByTestId('queue-chosen-credential'),
			).getByRole('button', { name: 'Change' }),
		);

		const reopened = await within(credentialSection()).findByRole('group');
		await waitFor(() =>
			expect(within(reopened).getByRole('radio', { name: /Acme new/ })).toBeChecked(),
		);
		expect(within(reopened).getByRole('radio', { name: /Acme old key/ })).toBeInTheDocument();
		expect(
			within(reopened).getByRole('radio', { name: /Add a new credential/ }),
		).toBeInTheDocument();
		expectAddReady();

		await user.click(within(credentialSection()).getByTestId('queue-keep-credential'));
		expect(
			await within(credentialSection()).findByTestId('queue-chosen-credential'),
		).toHaveTextContent('Acme new');
		expect(within(accessSection()).getByRole('radio', { name: /Full access/ })).toBeChecked();

		await user.click(within(credentialSection()).getByRole('button', { name: 'Change' }));
		await user.click(
			await within(credentialSection()).findByRole('radio', { name: /Acme old key/ }),
		);
		expect(
			await within(credentialSection()).findByTestId('queue-chosen-credential'),
		).toHaveTextContent('Acme old key');
		expect(within(accessSection()).getByRole('radio', { name: /Full access/ })).toBeChecked();
		await user.click(addButton());
		await waitFor(() => expect(calls).toHaveLength(1));
		expect(calls[0].body).toEqual({ credential_id: 'cred_acme_old' });
	});

	it('a credential awaiting sign-in says so, and the footer adds and signs in', async () => {
		const unconnected = makeCredential({
			credential_id: 'cred_oauth',
			name: 'Stripe OAuth',
			type: CredentialType.OAUTH2,
			details: { grant_type: 'authorization_code', connected: false },
		});
		renderWithProviders(
			<QueueHarness
				items={[makeItem('stripe.com', 'choose', { covering: [unconnected] })]}
			/>,
		);

		const options = await screen.findByRole('group', {
			name: /Use your credential for stripe/,
		});
		expect(within(options).getByRole('radio', { name: /Stripe OAuth/ })).toBeChecked();
		expect(within(options).getByText('Sign-in needed')).toBeVisible();
		expect(
			screen.getByText(/just needs you to finish signing in, once its access is set/),
		).toBeVisible();
		// The sign-in runs once the binding exists — after its access is set.
		await accessReady();
		expect(addButton()).toHaveTextContent('Add stripe and sign in');
	});

	it('names a workspace pick by vendor and version', async () => {
		renderWithProviders(
			<QueueHarness
				items={[makeItem('stripe.com', 'choose', { covering: [makeCredential()] })]}
			/>,
		);
		expect(await screen.findByTestId('queue-active-identity')).toHaveTextContent(
			/^stripe\.com · v1\.0\.0$/,
		);
	});

	it('names a catalog pick by its vendor, no version', async () => {
		renderWithProviders(
			<QueueHarness
				items={[
					makeItem('github.com', 'form', {
						key: 'github.com/api.github.com',
						api: {
							source: 'catalog',
							vendor: 'github.com',
							name: 'github.com/api.github.com',
							version: '1.0.0',
							apiId: 'github.com/api.github.com',
							label: 'GitHub',
						},
						importsApi: true,
					}),
				]}
			/>,
		);
		expect(await screen.findByTestId('queue-active-identity')).toHaveTextContent(
			/^github\.com$/,
		);
	});

	it('warns, a Details away, before a second credential is added to an API the agent reaches', async () => {
		const user = userEvent.setup();
		renderWithProviders(
			<QueueHarness
				items={[
					makeItem('acme.com', 'form', {
						existing: [
							{ bindingId: 'bind_1', credentialId: 'cred_slack', name: 'Acme bot' },
						],
					}),
				]}
			/>,
		);

		const accounts = await screen.findByTestId('queue-existing-accounts');
		expect(accounts).toHaveTextContent('Needs a new key · Added via Acme bot already');
		await user.click(within(accounts).getByRole('button', { name: /Details/ }));
		await waitFor(() =>
			expect(screen.getByTestId('queue-ambiguity-warning')).toHaveTextContent(
				'This adds a second credential; Support bot keeps its current one. Once added, calls to acme must name a credential with the Jentic-Credential-Id header, unless one is scoped more narrowly. Nothing is stored until you save it.',
			),
		);
	});
	it('the list rail: "N of M added", and any API opens in any order', async () => {
		const { calls } = watchBinds();
		const user = userEvent.setup();
		renderWithProviders(
			<QueueHarness
				items={[makeItem('acme.com', 'form'), coveredItem('stripe.com', 'cred_stripe')]}
			/>,
		);

		// No API is banked ahead of the rest: the first pick is on screen.
		expect(await screen.findByTestId('queue-active-identity')).toHaveTextContent(/^acme\.com/);
		expect(screen.getByText('0 of 2 added')).toBeInTheDocument();
		expect(rowFor('acme')).toHaveTextContent('In progress');
		expect(rowFor('stripe')).toHaveTextContent('To do');

		await user.click(within(rowFor('stripe')).getByRole('button'));
		await waitFor(() =>
			expect(screen.getByTestId('queue-active-identity')).toHaveTextContent(/^stripe\.com/),
		);
		expect(rowFor('stripe')).toHaveAttribute('data-selected', 'true');
		expect(rowFor('stripe')).toHaveTextContent('In progress');
		expect(rowFor('acme')).toHaveTextContent('To do');
		await addWithAccess(user);
		await waitFor(() => expect(rowFor('stripe')).toHaveAttribute('data-status', 'added'));
		expect(screen.getByText('1 of 2 added')).toBeInTheDocument();
		expect(calls).toHaveLength(1);
		await waitFor(() =>
			expect(screen.getByTestId('queue-active-identity')).toHaveTextContent(/^acme\.com/),
		);
	});

	it('Skip for now leaves an API out, and picking it again brings it back', async () => {
		const { calls } = watchBinds();
		const user = userEvent.setup();
		renderWithProviders(<QueueHarness items={[coveredItem('stripe.com', 'cred_stripe')]} />);

		await user.click(await screen.findByTestId('queue-skip'));
		await waitFor(() => expect(rowFor('stripe')).toHaveAttribute('data-status', 'dropped'));
		expect(rowFor('stripe')).toHaveTextContent('Skipped');
		expect(screen.getByTestId('queue-done-pane')).toHaveTextContent('Nothing was added.');
		expect(calls).toHaveLength(0);

		await user.click(within(rowFor('stripe')).getByRole('button'));
		await waitFor(() => expect(rowFor('stripe')).toHaveAttribute('data-status', 'waiting'));
		expect(await accessReady()).toBeInTheDocument();
		expectAddReady();
	});

	it('a failed bind is terminal for that item only, and retryable in place', async () => {
		let failing = true;
		worker.use(
			http.post('/agents/:id/credentials', async ({ request }) => {
				const body = (await request.clone().json()) as { credential_id: string };
				if (failing && body.credential_id === 'cred_stripe') {
					return HttpResponse.json(
						{ detail: 'Upstream is unavailable.' },
						{ status: 503 },
					);
				}
				return undefined;
			}),
		);
		const user = userEvent.setup();
		renderWithProviders(
			<QueueHarness
				items={[
					coveredItem('stripe.com', 'cred_stripe'),
					coveredItem('slack.com', 'cred_slack'),
				]}
			/>,
		);

		await addWithAccess(user);
		await waitFor(() => expect(rowFor('stripe')).toHaveAttribute('data-status', 'failed'));
		expect(rowFor('stripe')).toHaveTextContent('Failed');
		await waitFor(() =>
			expect(screen.getByTestId('queue-active-identity')).toHaveTextContent(/^slack\.com/),
		);
		await addWithAccess(user);
		await waitFor(() => expect(rowFor('slack')).toHaveAttribute('data-status', 'added'));

		failing = false;
		await user.click(within(rowFor('stripe')).getByRole('button'));
		await waitFor(() => expect(rowFor('stripe')).toHaveTextContent('In progress'));
		expect(await screen.findByRole('radio', { name: /stripe\.com key/ })).toBeChecked();
		await addWithAccess(user);
		await waitFor(() => expect(rowFor('stripe')).toHaveAttribute('data-status', 'added'));
		expect(await screen.findByTestId('queue-done-pane')).toHaveTextContent('2 APIs added');
	});

	it('closing mid-way keeps what landed and hands the rest back for re-entry', async () => {
		const onClosed = vi.fn();
		const user = userEvent.setup();
		renderWithProviders(
			<QueueHarness
				items={[
					coveredItem('stripe.com', 'cred_stripe'),
					makeItem('acme.com', 'form'),
					makeItem('notion.so', 'form'),
				]}
				onClosed={onClosed}
			/>,
		);

		await addWithAccess(user);
		await waitFor(() => expect(rowFor('stripe')).toHaveAttribute('data-status', 'added'));
		await user.click(screen.getByRole('button', { name: 'Close' }));
		expect(onClosed).toHaveBeenCalledTimes(1);
		expect(onClosed.mock.calls[0][0].map((i: PreflightItem) => i.key)).toEqual([
			'acme.com/main',
			'notion.so/main',
		]);

		// Re-entry resumes the remainder rather than replaying the whole batch.
		await user.click(screen.getByRole('button', { name: 'Reopen' }));
		await waitFor(() => expect(progressRows()).toHaveLength(2));
		expect(
			screen.getByRole('heading', { name: /Set up 2 APIs for Support bot/ }),
		).toBeInTheDocument();
	});
	it('saves a credential for an umbrella catalog pick while its import is still running', async () => {
		// The live GitHub pick: name is the whole `api_id`, which the backend
		// rejects in `api.name`. The import answers 202 and lands later, so the
		// credential must not depend on it.
		const SPEC_URL =
			'https://raw.githubusercontent.com/jentic/jentic-public-apis/refs/heads/main/apis/openapi/github.com/api.github.com/1.1.4/openapi.json';
		const imports: string[] = [];
		const creates: { api?: Record<string, unknown> }[] = [];
		worker.use(
			http.get(SPEC_URL, () =>
				HttpResponse.json({
					openapi: '3.0.0',
					info: { title: 'GitHub v3 REST API', version: '1.1.4' },
					components: {
						securitySchemes: { bearerAuth: { type: 'http', scheme: 'bearer' } },
					},
				}),
			),
			http.post('/catalog/*', ({ request }) => {
				imports.push(new URL(request.url).pathname);
				return HttpResponse.json(
					{ job_id: 'job_gh', status: 'queued', _links: { self: '/jobs/job_gh' } },
					{ status: 202 },
				);
			}),
			http.post('/credentials', async ({ request }) => {
				creates.push((await request.clone().json()) as { api?: Record<string, unknown> });
				return undefined;
			}),
		);
		const api: SelectedApi = {
			source: 'catalog',
			vendor: 'github.com',
			name: 'github.com/api.github.com',
			version: '1.0.0',
			apiId: 'github.com/api.github.com',
			specUrl: SPEC_URL,
			registered: false,
			label: 'GitHub',
		};
		const { calls } = watchBinds();
		const user = userEvent.setup();
		renderWithProviders(
			<QueueHarness
				items={[
					{
						key: 'github-com/github-com-api-github-com',
						api,
						outcome: 'form',
						covering: [],
						existing: [],
						importsApi: true,
					},
				]}
			/>,
		);

		const form = await screen.findByTestId('queue-credential-form');
		await user.click(within(form).getAllByRole('button', { name: /Details/ })[0]);
		expect(
			await within(form).findByText('The API is imported into your workspace on save.'),
		).toBeInTheDocument();
		await user.type(await tokenField(), 'ghp_live_token');
		await user.click(screen.getByRole('button', { name: /Save and continue/ }));
		await waitFor(() => expect(creates).toHaveLength(1));
		await addWithAccess(user);
		expect(imports).toEqual(['/catalog/github.com/api.github.com:import']);
		expect(creates[0]!.api).toMatchObject({
			vendor: 'github.com',
			name: 'api-github-com',
			catalog_api_id: 'github.com/api.github.com',
		});
		await waitFor(() => expect(calls).toHaveLength(1));
		await waitFor(() => expect(rowFor('GitHub')).toHaveAttribute('data-status', 'added'));
		expect(screen.queryByRole('alert')).toBeNull();
	});

	it('passes an accessibility audit with the list, the chooser folded and the form open', async () => {
		const user = userEvent.setup();
		renderWithProviders(
			<QueueHarness
				items={[coveredItem('acme.com', 'cred_acme'), makeItem('stripe.com', 'form')]}
			/>,
		);
		await user.click(await screen.findByRole('radio', { name: /Add a new credential/ }));
		await screen.findByTestId('queue-existing-collapsed');
		await waitFor(() => checkA11y(document.body, { modal: true }), { timeout: 3000 });
	});

	it('passes an accessibility audit with credential options in the pane', async () => {
		const production = makeCredential({
			credential_id: 'cred_prod',
			name: 'Stripe — Production',
		});
		const sandbox = makeCredential({ credential_id: 'cred_sandbox', name: 'Stripe — Sandbox' });
		renderWithProviders(
			<QueueHarness
				items={[makeItem('stripe.com', 'choose', { covering: [production, sandbox] })]}
			/>,
		);
		await screen.findByRole('group', { name: /Pick one of your 2 credentials/ });
		await waitFor(() => checkA11y(document.body, { modal: true }), { timeout: 3000 });
	});

	it('390px: the footer action and the list stay reachable', async () => {
		await page.viewport(390, 844);
		renderWithProviders(<QueueHarness items={[coveredItem('stripe.com', 'cred_stripe')]} />);
		await accessReady();
		expect(addButton()).toBeVisible();
		expect(addButton()).toHaveTextContent('Add');
		expect(rowFor('stripe')).toBeVisible();
	});
	describe('② Access', () => {
		async function toAccess(): Promise<HTMLElement> {
			return accessReady();
		}

		/** Ask the inline tester about one request; returns its verdict line. */
		async function tryRequest(
			user: ReturnType<typeof userEvent.setup>,
			step: HTMLElement,
			method: string,
			path: string,
		): Promise<HTMLElement> {
			const toggle = within(step).getByRole('button', { name: /Try a request/ });
			if (toggle.getAttribute('aria-expanded') !== 'true') await user.click(toggle);
			const tester = within(step).getByTestId('queue-access-tester');
			await waitFor(() =>
				expect(within(tester).getByLabelText('Request path')).toBeVisible(),
			);
			await user.selectOptions(within(tester).getByLabelText('HTTP method'), method);
			const input = within(tester).getByLabelText('Request path');
			await user.clear(input);
			await user.type(input, path);
			await user.click(within(tester).getByRole('button', { name: 'Test' }));
			return within(tester).findByTestId('rule-verdict');
		}

		it('four presets, Read only preselected; an empty Custom list holds the footer', async () => {
			const { calls } = watchBinds();
			const user = userEvent.setup();
			renderWithProviders(
				<QueueHarness items={[coveredItem('stripe.com', 'cred_stripe')]} />,
			);
			const step = await toAccess();
			const group = within(step).getByRole('radiogroup', {
				name: 'What can Support bot call?',
			});
			expect(
				within(group)
					.getAllByRole('radio')
					.map((r) => r.textContent),
			).toEqual([
				expect.stringContaining('Read only'),
				expect.stringContaining('Read only, ask before anything else'),
				expect.stringContaining('Full access'),
				expect.stringContaining('Custom rules'),
			]);
			// Read only is preselected: the least access that still lets it work.
			expect(within(group).getByRole('radio', { name: /^Read only$/ })).toBeChecked();

			await user.click(within(group).getByRole('radio', { name: /Custom rules/ }));
			const editor = within(step).getByTestId('queue-access-custom-editor');
			await waitFor(() =>
				expect(within(editor).getByLabelText('Path pattern')).toBeVisible(),
			);
			expect(within(step).getByTestId('queue-access-hint')).toHaveTextContent(
				'Add at least one rule, or pick a preset.',
			);
			expectAddBlocked(/Add at least one rule/);
			expect(calls).toHaveLength(0);
		});

		it.each([
			[/Full access/, [{ effect: 'allow', path: '.*' }], 'Full access'],
			[/^Read only$/, [{ effect: 'allow', methods: ['GET'] }], 'Read only'],
			[
				/ask before anything else/,
				[
					{ effect: 'allow', methods: ['GET'] },
					// `.*` with no mode: the backend's default `regex`, a catch-all.
					{ effect: 'require-approval', path: '.*' },
				],
				'Read only, ask before anything else',
			],
		])('a preset writes its rules (%s)', async (preset, body, label) => {
			const rules = watchRules();
			const user = userEvent.setup();
			renderWithProviders(
				<QueueHarness items={[coveredItem('stripe.com', 'cred_stripe')]} />,
			);
			await addWithAccess(user, preset);
			await waitFor(() => expect(rowFor('stripe')).toHaveAttribute('data-status', 'added'));
			expect(rules.calls.map((c) => c.body)).toEqual([body]);
			await user.click(within(rowFor('stripe')).getByRole('button'));
			expect(await screen.findByTestId('queue-added-summary')).toHaveTextContent(label);
		});

		it('custom rules written By path are saved as written', async () => {
			const rules = watchRules();
			const user = userEvent.setup();
			renderWithProviders(
				<QueueHarness items={[coveredItem('stripe.com', 'cred_stripe')]} />,
			);
			const step = await toAccess();
			await user.click(within(step).getByRole('radio', { name: /Custom rules/ }));
			await writeRule(user, step, 'DELETE');
			await waitFor(() => expectAddReady());
			await user.click(addButton());
			await waitFor(() => expect(rowFor('stripe')).toHaveAttribute('data-status', 'added'));
			expect(rules.calls.map((c) => c.body)).toEqual([
				[{ effect: 'allow', methods: ['DELETE'] }],
			]);
			await user.click(within(rowFor('stripe')).getByRole('button'));
			expect(await screen.findByTestId('queue-added-summary')).toHaveTextContent(
				'1 custom rule',
			);
		});

		it('custom rules that are exactly a preset are recorded as that preset', async () => {
			const rules = watchRules();
			const user = userEvent.setup();
			renderWithProviders(
				<QueueHarness items={[coveredItem('stripe.com', 'cred_stripe')]} />,
			);
			const step = await toAccess();
			await user.click(within(step).getByRole('radio', { name: /Custom rules/ }));
			await writeRule(user, step, 'GET');
			await waitFor(() => expectAddReady());
			await user.click(addButton());
			await waitFor(() => expect(rowFor('stripe')).toHaveAttribute('data-status', 'added'));
			expect(rules.calls.map((c) => c.body)).toEqual([
				[{ effect: 'allow', methods: ['GET'] }],
			]);
			await user.click(within(rowFor('stripe')).getByRole('button'));
			const summary = await screen.findByTestId('queue-added-summary');
			expect(summary).toHaveTextContent('Read only');
			expect(summary).not.toHaveTextContent('custom rule');
		});

		it('"Try a request" answers from the rules being edited, unsaved, and follows each edit', async () => {
			const tests: unknown[] = [];
			worker.use(
				http.post('/credentials/:cid/agents/:aid/permissions\\:test', () => {
					tests.push(1);
					return undefined;
				}),
			);
			const user = userEvent.setup();
			renderWithProviders(
				<QueueHarness items={[coveredItem('stripe.com', 'cred_stripe')]} />,
			);
			const step = await toAccess();

			expect(await tryRequest(user, step, 'GET', '/v1/charges')).toHaveTextContent(
				'Allowed GET /v1/charges — allowed by “Read only”',
			);
			expect(await tryRequest(user, step, 'POST', '/v1/charges')).toHaveTextContent(
				'Denied POST /v1/charges — no rule matched (default deny)',
			);

			// Custom: a rule written here answers before it is saved, by its path-rule number.
			await user.click(within(step).getByRole('radio', { name: /Custom rules/ }));
			await writeRule(user, step, 'POST');
			await waitFor(() =>
				expect(within(step).getByTestId('rule-verdict')).toHaveTextContent(
					/^Allowed POST \/v1\/charges — matched path rule #1/,
				),
			);
			// Nothing was bound, and the broker's dry run was never asked.
			expect(tests).toHaveLength(0);
			expect(rowFor('stripe')).not.toHaveAttribute('data-status', 'added');
			expect(rowFor('stripe')).toHaveTextContent('In progress');
		});

		/** Scoped by name: the drawer is a `dialog` too. */
		function laterConfirm(): HTMLElement {
			return screen.getByRole('dialog', { name: /without rules\?$/ });
		}

		it('the rows under the presets read Effective access, Try a request, Add without rules', async () => {
			renderWithProviders(
				<QueueHarness items={[coveredItem('stripe.com', 'cred_stripe')]} />,
			);
			const step = await toAccess();
			const rows = within(within(step).getByTestId('queue-access-tools')).getAllByRole(
				'listitem',
			);
			expect(rows).toHaveLength(3);
			expect(
				rows.map((r) => within(r).getByRole('button').textContent?.replace(/\s+/g, ' ')),
			).toEqual([
				expect.stringContaining('Effective access'),
				expect.stringContaining('Try a request'),
				expect.stringContaining('Add without rules…'),
			]);
			expect(within(rows[0]).getByRole('button')).toHaveAttribute('aria-expanded');
			expect(within(rows[1]).getByRole('button')).toHaveAttribute('aria-expanded');
			expect(within(rows[2]).getByTestId('queue-add-without-rules')).not.toHaveAttribute(
				'aria-expanded',
			);
		});

		it('"Add without rules…" is the caution row: confirmed, it adds the API blocked, with no rules', async () => {
			const { calls } = watchBinds();
			const rules = watchRules();
			const user = userEvent.setup();
			renderWithProviders(
				<QueueHarness items={[coveredItem('stripe.com', 'cred_stripe')]} />,
			);
			const step = await toAccess();

			const rows = within(within(step).getByTestId('queue-access-tools')).getAllByRole(
				'listitem',
			);
			const later = within(rows[rows.length - 1]).getByTestId('queue-add-without-rules');
			expect(later).toHaveTextContent('Add without rules…');
			expectCautionTone(later);

			expect(addButton()).toHaveTextContent('Add stripe');
			expect(addButton()).not.toHaveAttribute('data-identity');

			await user.click(later);
			const confirm = await waitFor(laterConfirm);
			expect(confirm).toHaveTextContent(
				"Support bot won't be able to call stripe until you add rules — every call is denied, and it shows as Blocked until then.",
			);
			await user.click(within(confirm).getByRole('button', { name: 'Keep setting up' }));
			await waitFor(() =>
				expect(within(step).getByTestId('queue-add-without-rules')).toHaveFocus(),
			);
			expect(calls).toHaveLength(0);

			await user.click(within(step).getByTestId('queue-add-without-rules'));
			await user.click(
				within(await waitFor(laterConfirm)).getByRole('button', {
					name: 'Add without rules',
				}),
			);
			await waitFor(() => expect(rowFor('stripe')).toHaveAttribute('data-status', 'added'));
			expect(calls).toHaveLength(1);
			expect(rules.calls).toHaveLength(0);
			expect(rowFor('stripe')).toHaveTextContent('Added · no rules');
			expect(screen.getByTestId('queue-done-pane')).toHaveTextContent(
				'1 API was set up later — every call to it is denied until you add rules on the API.',
			);
		});

		it('a blocked primary commits nothing, and the escape row is inert with it', async () => {
			const { calls } = watchBinds();
			const user = userEvent.setup();
			// `reachUnknown` feeds this same `blocked` gate the commit is checked against.
			renderWithProviders(<QueueHarness items={[makeItem('acme.com', 'form')]} />);
			await screen.findByTestId('queue-credential-form');

			expectAddBlocked(/Save the credential first/);
			await user.click(addButton());
			expect(screen.queryByRole('dialog', { name: /without rules\?$/ })).toBeNull();
			expect(calls).toHaveLength(0);

			await user.type(await tokenField(), 'tok_live_123');
			await user.click(screen.getByRole('button', { name: /Save and continue/ }));
			const step = await accessReady();
			await waitFor(() => expectAddReady());
			expect(within(step).getByTestId('queue-add-without-rules')).toBeEnabled();
			expect(calls).toHaveLength(0);
		});

		it('advances only once the bind AND its rules have landed', async () => {
			const order: string[] = [];
			let releaseRules: () => void = () => {};
			const rulesGate = new Promise<void>((resolve) => {
				releaseRules = resolve;
			});
			worker.use(
				http.post('/agents/:id/credentials', () => {
					order.push('bind');
					return undefined;
				}),
				http.put('/credentials/:cid/agents/:aid/permissions', async () => {
					order.push('rules');
					await rulesGate;
					return undefined;
				}),
			);
			const user = userEvent.setup();
			renderWithProviders(
				<QueueHarness items={[coveredItem('stripe.com', 'cred_stripe')]} />,
			);
			await addWithAccess(user);

			await waitFor(() => expect(order).toEqual(['bind', 'rules']));
			// Bound, rules in flight: not added, and nothing can close over it.
			expect(rowFor('stripe')).toHaveAttribute('data-status', 'working');
			expect(screen.getByRole('button', { name: 'Close' })).toBeDisabled();

			releaseRules();
			await waitFor(() => expect(rowFor('stripe')).toHaveAttribute('data-status', 'added'));
		});

		it('a rules save that fails keeps the binding and the rules, and Save rules retries only them', async () => {
			const { calls } = watchBinds();
			let failRules = true;
			const puts: unknown[] = [];
			worker.use(
				http.put('/credentials/:cid/agents/:aid/permissions', async ({ request }) => {
					puts.push(await request.clone().json());
					if (failRules) return HttpResponse.json({ detail: 'nope' }, { status: 500 });
					return undefined;
				}),
			);
			const user = userEvent.setup();
			renderWithProviders(
				<QueueHarness items={[coveredItem('stripe.com', 'cred_stripe')]} />,
			);
			const step = await toAccess();
			await user.click(within(step).getByRole('radio', { name: /Custom rules/ }));
			await writeRule(user, step, 'GET');
			await waitFor(() => expectAddReady());
			await user.click(addButton());

			const failed = await within(step).findByTestId('queue-rules-failed');
			expect(failed).toHaveTextContent("The access rules weren't saved.");
			expect(failed).toHaveTextContent(
				'stripe is added but blocked — every call is denied until its rules are saved. Your rules are kept; Save rules retries.',
			);
			expect(rowFor('stripe')).toHaveAttribute('data-status', 'active');
			expect(
				within(step).getByRole('list', { name: 'Rules, in evaluation order' }),
			).toHaveTextContent('GET');
			expect(
				within(credentialSection()).getByTestId('queue-chosen-credential'),
			).toHaveTextContent('bound · rules not saved yet');
			expect(
				within(credentialSection()).queryByRole('button', { name: 'Change' }),
			).toBeNull();

			failRules = false;
			expect(addButton()).toHaveTextContent('Save rules');
			await user.click(addButton());
			await waitFor(() => expect(rowFor('stripe')).toHaveAttribute('data-status', 'added'));
			// One bind — the retry re-sent only the rules.
			expect(calls).toHaveLength(1);
			expect(puts).toEqual([
				[{ effect: 'allow', methods: ['GET'] }],
				[{ effect: 'allow', methods: ['GET'] }],
			]);
		});

		it('a second credential for an API the agent reaches gets its own rules', async () => {
			const rules = watchRules();
			const user = userEvent.setup();
			renderWithProviders(
				<QueueHarness
					items={[
						makeItem('stripe.com', 'choose', {
							covering: [
								makeCredential({ credential_id: 'cred_sandbox', name: 'Sandbox' }),
							],
							existing: [
								{
									bindingId: 'bind_1',
									credentialId: 'cred_stripe',
									name: 'Stripe key',
								},
							],
						}),
					]}
				/>,
			);
			await addWithAccess(user);
			await waitFor(() => expect(rules.calls).toHaveLength(1));
			// Rules are per binding: the new credential's, not the existing one's.
			expect(rules.calls[0]).toMatchObject({
				agentId: AGENT_ID,
				credentialId: 'cred_sandbox',
			});
		});

		it('a just-created credential labels Full access with the scope it was saved with', async () => {
			// "Any version" reach must show on first paint: `revealAccess` focuses these radios ~260ms in.
			const rules = watchRules();
			const reads: string[] = [];
			worker.use(
				http.get('/credentials/:id', ({ params }) => {
					reads.push(params.id as string);
					return undefined;
				}),
			);
			const user = userEvent.setup();
			renderWithProviders(<QueueHarness items={[makeItem('acme.com', 'form')]} />);
			await screen.findByTestId('queue-credential-form');
			await user.type(await tokenField(), 'tok_live_123');
			await user.click(screen.getByRole('button', { name: /Save and continue/ }));

			const step = await accessReady();
			expect(within(step).queryByTestId('queue-access-reach-pending')).toBeNull();
			expect(
				within(step).getByRole('radio', { name: /Full access/ }),
			).toHaveAccessibleDescription('Any method, every version — incl. later ones');
			expect(within(step).getByTestId('queue-access-coverage-note')).toHaveTextContent(
				'Covers every version of this API',
			);
			expectAddReady();

			await user.click(within(step).getByRole('radio', { name: /Full access/ }));
			await user.click(addButton());
			await waitFor(() => expect(rowFor('acme')).toHaveAttribute('data-status', 'added'));
			expect(rules.calls.map((c) => c.body)).toEqual([[{ effect: 'allow', path: '.*' }]]);
		});

		it('a covering credential labels Full access from the scope it already carries', async () => {
			// The preflight hands over the stored scope, so ② never re-reads it.
			const reads: string[] = [];
			worker.use(
				http.get('/credentials/:id', ({ params }) => {
					reads.push(params.id as string);
					return undefined;
				}),
			);
			renderWithProviders(
				<QueueHarness
					items={[
						makeItem('stripe.com', 'choose', {
							covering: [
								makeCredential({
									credential_id: 'cred_wide',
									name: 'Vendor-wide key',
									// No API name: the scope covers every API of the vendor.
									api: { vendor: 'stripe.com', name: '', version: '' },
								}),
							],
						}),
					]}
				/>,
			);
			const step = await accessReady();
			expect(within(step).queryByTestId('queue-access-reach-pending')).toBeNull();
			expect(
				within(step).getByRole('radio', { name: /Full access/ }),
			).toHaveAccessibleDescription('Every API this credential covers, incl. later ones');
			expect(within(step).getByTestId('queue-access-coverage-note')).toHaveTextContent(
				'Covers every API of this vendor',
			);
			expectAddReady();
			expect(reads).not.toContain('cred_wide');
		});

		it('a double click on Add starts one commit, not two', async () => {
			const { calls } = watchBinds();
			const user = userEvent.setup();
			renderWithProviders(
				<QueueHarness items={[coveredItem('stripe.com', 'cred_stripe')]} />,
			);
			const step = await accessReady();
			await user.click(within(step).getByRole('radio', { name: /^Read only$/ }));
			// Both clicks land before the first bind resolves; `inFlight` drops the second (no 409).
			const add = addButton();
			await user.dblClick(add);
			await waitFor(() => expect(rowFor('stripe')).toHaveAttribute('data-status', 'added'));
			expect(calls).toHaveLength(1);
		});

		it('a rules retry that throws stays bound-and-blocked, never "Try again"', async () => {
			// The binding exists either way, so the item must not fall to `failed` and re-POST it (409).
			const { calls } = watchBinds();
			let put = 0;
			worker.use(
				http.put('/credentials/:cid/agents/:aid/permissions', () => {
					put += 1;
					if (put === 1) return HttpResponse.json({ detail: 'nope' }, { status: 500 });
					return HttpResponse.error();
				}),
			);
			const user = userEvent.setup();
			renderWithProviders(
				<QueueHarness items={[coveredItem('stripe.com', 'cred_stripe')]} />,
			);
			const step = await accessReady();
			await user.click(within(step).getByRole('radio', { name: /^Read only$/ }));
			await user.click(addButton());
			await within(step).findByTestId('queue-rules-failed');
			expect(addButton()).toHaveTextContent('Save rules');

			await user.click(addButton());
			await waitFor(() => expect(put).toBe(2));
			await waitFor(() => expect(rowFor('stripe')).toHaveAttribute('data-status', 'active'));
			expect(await within(step).findByTestId('queue-rules-failed')).toBeVisible();
			expect(addButton()).toHaveTextContent('Save rules');
			expect(screen.queryByRole('button', { name: 'Try again' })).toBeNull();
			expect(calls).toHaveLength(1);
			expect(
				within(credentialSection()).getByTestId('queue-chosen-credential'),
			).toHaveTextContent('bound · rules not saved yet');
		});

		it('a bound item is finished by saving its rules, never by binding again', async () => {
			// Only the rules are owed: the retry re-sends just them, never a second (409) bind.
			const { calls } = watchBinds();
			let failRules = true;
			const puts: unknown[] = [];
			worker.use(
				http.put('/credentials/:cid/agents/:aid/permissions', async ({ request }) => {
					puts.push(await request.clone().json());
					if (failRules) return HttpResponse.json({ detail: 'nope' }, { status: 500 });
					return undefined;
				}),
			);
			const user = userEvent.setup();
			renderWithProviders(
				<QueueHarness items={[coveredItem('stripe.com', 'cred_stripe')]} />,
			);
			const step = await accessReady();
			await user.click(within(step).getByRole('radio', { name: /^Read only$/ }));
			await user.click(addButton());
			await within(step).findByTestId('queue-rules-failed');
			expect(calls).toHaveLength(1);
			expect(within(step).queryByTestId('queue-add-without-rules')).toBeNull();
			expect(screen.queryByTestId('queue-skip')).toBeNull();

			failRules = false;
			await user.click(addButton());
			await waitFor(() => expect(rowFor('stripe')).toHaveAttribute('data-status', 'added'));
			expect(calls).toHaveLength(1);
			expect(puts).toHaveLength(2);
		});

		it('passes an accessibility audit on ②, the tester and the later-confirm open', async () => {
			const user = userEvent.setup();
			renderWithProviders(
				<QueueHarness items={[coveredItem('stripe.com', 'cred_stripe')]} />,
			);
			const step = await toAccess();
			await tryRequest(user, step, 'GET', '/v1/charges');
			await waitFor(() => checkA11y(document.body, { modal: true }), { timeout: 3000 });
			// The escape row's confirm is a modal over a modal: audit it too.
			await user.click(within(step).getByTestId('queue-add-without-rules'));
			await screen.findByRole('dialog', { name: /without rules\?$/ });
			await waitFor(() => checkA11y(document.body, { modal: true }), { timeout: 3000 });
		});
	});

	describe('Back to APIs', () => {
		it('hands the batch back as a tray seed: owed APIs ticked, added ones locked', async () => {
			const onBack = vi.fn();
			const user = userEvent.setup();
			renderWithProviders(
				<QueueHarness
					items={[coveredItem('stripe.com', 'cred_stripe'), makeItem('acme.com', 'form')]}
					onBack={onBack}
				/>,
			);
			await addWithAccess(user);
			await waitFor(() => expect(rowFor('stripe')).toHaveAttribute('data-status', 'added'));

			await user.click(screen.getByRole('button', { name: 'Back to APIs' }));
			expect(onBack).toHaveBeenCalledTimes(1);
			const [seed, remaining] = onBack.mock.calls[0] as [
				{ picks: SelectedApi[]; added: SelectedApi[] },
				PreflightItem[],
			];
			expect(seed.picks.map((a) => a.vendor)).toEqual(['acme.com']);
			expect(seed.added.map((a) => a.vendor)).toEqual(['stripe.com']);
			expect(remaining.map((i) => i.key)).toEqual(['acme.com/main']);
		});

		it('no Back without a host that can take the operator there', async () => {
			renderWithProviders(<QueueHarness items={[makeItem('acme.com', 'form')]} />);
			await screen.findByTestId('queue-credential-form');
			expect(screen.queryByRole('button', { name: 'Back to APIs' })).not.toBeInTheDocument();
		});

		it('an untouched credential form goes back without asking', async () => {
			const onBack = vi.fn();
			const user = userEvent.setup();
			renderWithProviders(
				<QueueHarness items={[makeItem('acme.com', 'form')]} onBack={onBack} />,
			);
			await screen.findByTestId('queue-credential-form');
			await user.click(screen.getByRole('button', { name: 'Back to APIs' }));
			expect(onBack).toHaveBeenCalledTimes(1);
			expect(
				screen.queryByRole('dialog', { name: 'Discard this credential?' }),
			).not.toBeInTheDocument();
		});

		it('a typed-in credential asks before it is discarded — even folded away', async () => {
			const onBack = vi.fn();
			const user = userEvent.setup();
			renderWithProviders(
				<QueueHarness items={[coveredItem('acme.com', 'cred_acme')]} onBack={onBack} />,
			);
			await user.click(await screen.findByRole('radio', { name: /Add a new credential/ }));
			await user.type(await tokenField(), 'tok_draft');
			// Folding the form away keeps the draft — and keeps guarding it.
			await user.click(screen.getByTestId('queue-existing-collapsed'));
			await user.click(screen.getByRole('button', { name: 'Back to APIs' }));

			const confirm = await screen.findByRole('dialog', { name: 'Discard this credential?' });
			expect(onBack).not.toHaveBeenCalled();
			await user.click(within(confirm).getByRole('button', { name: 'Cancel' }));
			await waitFor(() =>
				expect(
					screen.queryByRole('dialog', { name: 'Discard this credential?' }),
				).not.toBeInTheDocument(),
			);
			await user.click(await screen.findByRole('radio', { name: /Add a new credential/ }));
			expect(await tokenField()).toHaveValue('tok_draft');

			await user.click(screen.getByRole('button', { name: 'Back to APIs' }));
			await user.click(
				within(
					await screen.findByRole('dialog', { name: 'Discard this credential?' }),
				).getByRole('button', { name: 'Discard' }),
			);
			expect(onBack).toHaveBeenCalledTimes(1);
		});
	});
});

describe('ApiSetupQueue — ② reads the API’s actions for a catalog pick too', () => {
	const OPS = {
		data: [
			{ operation_id: 'listShifts', method: 'GET', path: '/v2/shifts', name: 'List shifts' },
			{
				operation_id: 'createShift',
				method: 'POST',
				path: '/v2/shifts',
				name: 'Create shift',
			},
		],
		has_more: false,
		next_cursor: null,
	};
	const SPEC_URL = 'https://specs.test/7shifts.com/openapi.json';
	const CATALOG_PICK: SelectedApi = {
		source: 'catalog',
		vendor: '7shifts.com',
		name: '7shifts.com',
		version: '1.0.0',
		apiId: '7shifts.com',
		specUrl: SPEC_URL,
		label: '7shifts',
	};

	function serveOperations(answer: (path: string, n: number) => Response | undefined): string[] {
		const reads: string[] = [];
		worker.use(
			http.get('/apis/:vendor/:name/:version/operations', ({ request }) => {
				const path = new URL(request.url).pathname;
				reads.push(path);
				return answer(path, reads.length);
			}),
		);
		return reads;
	}

	async function openCustomRules(user: ReturnType<typeof userEvent.setup>): Promise<HTMLElement> {
		const step = await accessReady();
		await user.click(within(step).getByRole('radio', { name: /Custom rules/ }));
		return step;
	}

	beforeEach(async () => {
		await page.viewport(1280, 900);
		setToken('test-token');
		resetAgentsStore();
		resetCredentialsStore([]);
		worker.use(
			http.get(SPEC_URL, () =>
				HttpResponse.json({
					openapi: '3.0.0',
					info: { title: '7shifts', version: '2.1.0' },
					components: {
						securitySchemes: { bearerAuth: { type: 'http', scheme: 'bearer' } },
					},
				}),
			),
		);
	});

	it('a catalog pick already in the workspace reads its workspace API’s actions at once', async () => {
		// Two imported versions: the live one with the highest version is read.
		resetApisStore([
			makeMockApi({
				vendor: '7shifts-com',
				name: '7shifts-com',
				version: '1.9.0',
				catalogApiId: '7shifts.com',
			}),
			makeMockApi({
				vendor: '7shifts-com',
				name: '7shifts-com',
				version: '2.1.0',
				catalogApiId: '7shifts.com',
			}),
		]);
		const reads = serveOperations(() => HttpResponse.json(OPS));
		const covering = [
			makeCredential({
				credential_id: 'cred_7s',
				name: '7shifts key',
				catalog_api_id: '7shifts.com',
				api: { vendor: '7shifts-com', name: '7shifts-com', version: '' },
			}),
		];
		const user = userEvent.setup();
		renderWithProviders(
			<QueueHarness
				items={[makeItem('7shifts.com', 'choose', { api: CATALOG_PICK, covering })]}
			/>,
		);

		const step = await openCustomRules(user);
		const byAction = await within(step).findByRole('button', { name: 'By action' });
		await waitFor(() => expect(byAction).toBeVisible());
		expect(reads[0]).toBe('/apis/7shifts-com/7shifts-com/2.1.0/operations');
		expect(within(step).queryByTestId('queue-access-actions-pending')).toBeNull();
	});

	it('By action: plain English on every row (generated when the spec has none), and Add settles staged choices', async () => {
		resetApisStore([
			makeMockApi({
				vendor: '7shifts-com',
				name: '7shifts-com',
				version: '2.1.0',
				catalogApiId: '7shifts.com',
			}),
		]);
		// One operation the spec names, one it doesn't (adhook.io's common case).
		serveOperations(() =>
			HttpResponse.json({
				data: [
					OPS.data[0],
					{
						operation_id: 'newColumn',
						method: 'POST',
						path: '/v1/boardColumns',
						name: null,
					},
				],
				has_more: false,
				next_cursor: null,
			}),
		);
		const ruleCalls: unknown[] = [];
		worker.use(
			http.put('/credentials/:cid/agents/:aid/permissions', async ({ request }) => {
				ruleCalls.push(await request.json());
				return HttpResponse.json([]);
			}),
		);
		const covering = [
			makeCredential({
				credential_id: 'cred_7s',
				name: '7shifts key',
				catalog_api_id: '7shifts.com',
				api: { vendor: '7shifts-com', name: '7shifts-com', version: '' },
			}),
		];
		const user = userEvent.setup();
		renderWithProviders(
			<QueueHarness
				items={[makeItem('7shifts.com', 'choose', { api: CATALOG_PICK, covering })]}
			/>,
		);
		const step = await openCustomRules(user);
		await user.click(await within(step).findByRole('button', { name: 'By action' }));
		const list = await within(step).findByRole('list', { name: 'Operations' });
		expect(list).toHaveTextContent('List shifts');
		const generated = within(list).getByText('Create a board column');
		expect(generated).toHaveAttribute('data-generated', 'true');
		expect(generated).toHaveClass('italic');
		// The filter searches the plain-English text too, generated included.
		await user.type(within(step).getByLabelText('Filter operations'), 'board column');
		expect(within(list).getAllByRole('group', { name: /^Access for/ })).toHaveLength(1);
		await user.clear(within(step).getByLabelText('Filter operations'));

		// Stage one choice, then Add: asked to apply it first, never dropped.
		await user.click(
			within(
				within(list).getByRole('group', { name: 'Access for POST /v1/boardColumns' }),
			).getByRole('button', { name: 'Allow' }),
		);
		await user.click(addButton());
		const guard = await screen.findByRole('dialog', { name: 'Apply 1 change by action?' });
		expect(guard).toHaveTextContent('apply them before adding 7shifts');
		await checkA11y(document.body, { modal: true });
		expect(ruleCalls).toHaveLength(0);
		await user.click(within(guard).getByRole('button', { name: 'Apply' }));
		await waitFor(() => expect(ruleCalls).toHaveLength(1));
		expect(ruleCalls[0]).toEqual([{ effect: 'allow', operations: ['newColumn'] }]);
	});

	it('a catalog pick imported on save reads the actions of the API its import registered', async () => {
		resetApisStore([]);
		let jobPolls = 0;
		worker.use(
			http.post('/catalog/*', () =>
				HttpResponse.json(
					{ job_id: 'job_7s', status: 'queued', _links: { self: '/jobs/job_7s' } },
					{ status: 202 },
				),
			),
			http.get('/jobs/job_7s', () => {
				jobPolls += 1;
				return HttpResponse.json({
					job_id: 'job_7s',
					kind: 'api_import',
					status: jobPolls > 1 ? 'completed' : 'running',
					error: null,
					created_at: '2026-01-01T00:00:00Z',
					updated_at: '2026-01-01T00:00:01Z',
					_links: { self: '/jobs/job_7s' },
				});
			}),
			http.get('/jobs/job_7s/result', () =>
				HttpResponse.json({
					revisions: [
						{ api: { vendor: '7shifts-com', name: '7shifts-com', version: '2.1.0' } },
					],
				}),
			),
		);
		// The first read lands before the revision is served: "not imported yet".
		const reads = serveOperations((_path, n) =>
			n === 1 ? new HttpResponse(null, { status: 404 }) : HttpResponse.json(OPS),
		);
		const user = userEvent.setup();
		renderWithProviders(
			<QueueHarness
				items={[makeItem('7shifts.com', 'form', { api: CATALOG_PICK, importsApi: true })]}
			/>,
		);
		await user.type(await tokenField(), 'tok_7s');
		await user.click(screen.getByRole('button', { name: /Save and continue/ }));

		// Until the list arrives its row is held at height, so the toggle lands in place.
		const step = await openCustomRules(user);
		const editor = within(step).getByTestId('queue-access-custom-editor');
		const held = await within(editor).findByTestId('queue-access-actions-pending');
		const get = await within(editor).findByRole('button', { name: 'GET' });
		// Relative to the editor: the pane itself may scroll meanwhile.
		const offset = (el: HTMLElement): number =>
			el.getBoundingClientRect().top - editor.getBoundingClientRect().top;
		const editorTopWhileHeld = offset(get);
		const heldHeight = held.getBoundingClientRect().height;

		expect(
			await within(step).findByRole('button', { name: 'By action' }, { timeout: 10_000 }),
		).toBeVisible();
		expect(
			within(editor).getByRole('group', { name: 'Build rules' }).getBoundingClientRect()
				.height,
		).toBeCloseTo(heldHeight, 0);
		expect(offset(within(editor).getByRole('button', { name: 'GET' }))).toBeCloseTo(
			editorTopWhileHeld,
			0,
		);
		expect(new Set(reads)).toEqual(new Set(['/apis/7shifts-com/7shifts-com/2.1.0/operations']));
		expect(reads.length).toBeGreaterThanOrEqual(2);
		expect(within(step).queryByTestId('queue-access-actions-pending')).toBeNull();
	}, 20_000);

	it('a workspace pick reads its own API, as before', async () => {
		resetApisStore([
			makeMockApi({ vendor: 'stripe.com', name: 'main', displayName: 'stripe' }),
		]);
		const reads = serveOperations(() => HttpResponse.json(OPS));
		const user = userEvent.setup();
		renderWithProviders(<QueueHarness items={[coveredItem('stripe.com', 'cred_stripe')]} />);

		const step = await openCustomRules(user);
		const byAction = await within(step).findByRole('button', { name: 'By action' });
		await waitFor(() => expect(byAction).toBeVisible());
		expect(reads[0]).toBe('/apis/stripe.com/main/1.0.0/operations');
	});
});

describe('ApiSetupQueue — an added catalog pick is not handed back as waiting', () => {
	beforeEach(async () => {
		await page.viewport(1280, 900);
		setToken('test-token');
		resetAgentsStore();
		resetCredentialsStore([]);
		resetApisStore([]);
	});

	it('picks it from the catalog, adds it, closes: nothing is left waiting', async () => {
		const SPEC_URL = 'https://specs.test/airbrake.io/openapi.json';
		worker.use(
			http.get(SPEC_URL, () =>
				HttpResponse.json({
					openapi: '3.0.0',
					info: { title: 'Airbrake API', version: '4.0.0' },
					components: {
						securitySchemes: { bearerAuth: { type: 'http', scheme: 'bearer' } },
					},
				}),
			),
			http.post('/catalog/*', () =>
				HttpResponse.json(
					{ job_id: 'job_ab', status: 'queued', _links: { self: '/jobs/job_ab' } },
					{ status: 202 },
				),
			),
		);
		const onClosed = vi.fn();
		const user = userEvent.setup();
		const api: SelectedApi = {
			source: 'catalog',
			vendor: 'airbrake.io',
			name: 'airbrake.io',
			version: '1.0.0',
			apiId: 'airbrake.io',
			specUrl: SPEC_URL,
			registered: false,
			label: 'airbrake.io',
		};
		renderWithProviders(
			<QueueHarness
				items={[makeItem('airbrake.io', 'form', { api, importsApi: true })]}
				onClosed={onClosed}
			/>,
		);
		await user.type(await tokenField(), 'tok_ab');
		await user.click(screen.getByRole('button', { name: /Save and continue/ }));
		await addWithAccess(user);
		await user.click(await screen.findByTestId('queue-done'));

		await waitFor(() => expect(onClosed).toHaveBeenCalledTimes(1));
		expect(onClosed).toHaveBeenCalledWith([]);
		await user.click(screen.getByRole('button', { name: 'Reopen' }));
		expect(screen.queryByTestId('queue-progress-row')).toBeNull();
	});
});

describe('ApiSetupQueue — the rail scrolls on its own', () => {
	beforeEach(async () => {
		setToken('test-token');
		resetAgentsStore();
		resetCredentialsStore([]);
		resetApisStore([]);
	});

	it('a long batch scrolls inside the rail, the footer staying at the sheet’s foot', async () => {
		await page.viewport(1440, 780);
		const items = Array.from({ length: 18 }, (_, i) => makeItem(`api${i}.test`, 'form'));
		renderWithProviders(<QueueHarness items={items} />);
		const list = await screen.findByTestId('queue-rail-list');
		await waitFor(() => expect(progressRows()).toHaveLength(18));
		expect(list.scrollHeight).toBeGreaterThan(list.clientHeight);
		expect(getComputedStyle(list).overflowY).toBe('auto');

		const footer = addButton().closest('footer') ?? addButton().parentElement!;
		const sheetBottom = window.innerHeight;
		expect(Math.round(footer.getBoundingClientRect().bottom)).toBeLessThanOrEqual(sheetBottom);
		const pane = screen.getByTestId('queue-pane-scroll');
		const paneTop = pane.scrollTop;
		list.scrollTop = list.scrollHeight;
		expect(pane.scrollTop).toBe(paneTop);
		expect(list.getBoundingClientRect().bottom).toBeLessThanOrEqual(
			footer.getBoundingClientRect().top + 1,
		);
	});

	it('brings the API on screen into the rail’s view when it changes', async () => {
		await page.viewport(1440, 780);
		const items = Array.from({ length: 18 }, (_, i) => makeItem(`api${i}.test`, 'form'));
		const user = userEvent.setup();
		renderWithProviders(<QueueHarness items={items} />);
		const list = await screen.findByTestId('queue-rail-list');
		await waitFor(() => expect(progressRows()).toHaveLength(18));
		for (let i = 0; i < 17; i += 1) {
			await user.click(screen.getByRole('button', { name: /Skip for now/ }));
		}
		const last = rowFor('api17');
		await waitFor(() => expect(last).toHaveAttribute('data-selected', 'true'));
		await waitFor(() => {
			const l = list.getBoundingClientRect();
			const r = last.getBoundingClientRect();
			expect(r.bottom).toBeLessThanOrEqual(l.bottom + 1);
			expect(r.top).toBeGreaterThanOrEqual(l.top - 1);
		});
	});
});
