/**
 * ApiSetupQueue — finishing the batch the Add-APIs tray handed over. The state
 * machine has its own unit specs (`setupQueue.test.ts`); these pin what needs the
 * bind endpoint and the credential wizard: nothing binds until the operator
 * confirms (even a lone matching credential), one failed POST not taking the
 * others, and a mid-way dismissal handing the remainder back.
 */
import { useState } from 'react';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { http, HttpResponse } from 'msw';
import { page, userEvent as browserUser } from 'vitest/browser';
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

describe('ApiSetupQueue — finishing a batch one API at a time', () => {
	beforeEach(async () => {
		await page.viewport(1280, 900);
		setToken('test-token');
		resetAgentsStore();
		resetCredentialsStore([makeCredential()]);
		resetApisStore([
			makeMockApi({ vendor: 'stripe.com', name: 'main', displayName: 'stripe' }),
		]);
	});

	it('never binds a lone matching credential on its own — it is preselected, not chosen', async () => {
		const { calls } = watchBinds();
		const user = userEvent.setup();
		renderWithProviders(<QueueHarness items={[coveredItem('stripe.com', 'cred_stripe')]} />);

		// The pane stops on it: a match is API identity, not account, so the
		// operator confirms it (or picks a new one) before anything binds.
		const options = await screen.findByRole('group', {
			name: 'You have 1 credential for stripe. Should Support bot use it, or a new one?',
		});
		expect(within(options).getAllByRole('radio')).toHaveLength(2);
		expect(within(options).getByRole('radio', { name: /stripe\.com key/ })).toBeChecked();
		expect(
			within(options).getByRole('radio', { name: /Add a new credential/ }),
		).not.toBeChecked();
		expect(screen.queryByTestId('queue-done-pane')).not.toBeInTheDocument();
		expect(rowFor('stripe')).toHaveAttribute('data-status', 'active');
		expect(calls).toHaveLength(0);

		await user.click(screen.getByRole('button', { name: 'Use this credential' }));

		await waitFor(() => expect(calls).toHaveLength(1));
		expect(calls[0].agentId).toBe(AGENT_ID);
		// Least privilege: the binding starts with no rules, and the footer
		// says so rather than leaving the operator to assume access.
		expect(calls[0].body).toEqual({ credential_id: 'cred_stripe' });
		expect(await screen.findByTestId('queue-done-pane')).toBeInTheDocument();
		expect(screen.getByText('1 API added')).toBeInTheDocument();
		expect(
			screen.getByText(/Added APIs start with no access rules, so calls are blocked/),
		).toBeInTheDocument();
		// The finished row keeps the record of which credential it went through.
		expect(rowFor('stripe')).toHaveTextContent('via stripe.com key');
	});

	it('offers a way past a matched credential that is the wrong account', async () => {
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

		// A credential matches on API identity, not on account, so "none of these"
		// has to be answerable without dropping the API — there is no third way.
		const options = await screen.findByRole('group', {
			name: 'You have 2 credentials for stripe. Which should Support bot use?',
		});
		expect(within(options).getAllByRole('radio')).toHaveLength(3);
		await user.click(within(options).getByRole('radio', { name: /Add a new credential/ }));
		await user.click(screen.getByRole('button', { name: 'Add credential' }));

		expect(
			await screen.findByText('Fill in the credential details for stripe'),
		).toBeInTheDocument();
	});

	it('a lone matching credential can be swapped for a new one, and back', async () => {
		const { calls } = watchBinds();
		const user = userEvent.setup();
		renderWithProviders(
			<QueueHarness
				items={[makeItem('stripe.com', 'choose', { covering: [makeCredential()] })]}
			/>,
		);

		const options = await screen.findByRole('group', {
			name: 'You have 1 credential for stripe. Should Support bot use it, or a new one?',
		});
		await user.click(within(options).getByRole('radio', { name: /Add a new credential/ }));
		expect(screen.getByRole('button', { name: 'Add credential' })).toBeEnabled();
		expect(
			screen.queryByRole('button', { name: 'Use this credential' }),
		).not.toBeInTheDocument();

		// Changing your mind here must not cost a trip back to the tray.
		await user.click(within(options).getByRole('radio', { name: /Stripe key/ }));
		expect(screen.queryByRole('button', { name: 'Add credential' })).not.toBeInTheDocument();
		await user.click(screen.getByRole('button', { name: 'Use this credential' }));

		await waitFor(() => expect(calls).toHaveLength(1));
		expect(calls[0].body).toEqual({ credential_id: 'cred_stripe' });
		await waitFor(() => expect(rowFor('stripe')).toHaveTextContent('via Stripe key'));
	});

	it('a credential awaiting sign-in says so, and a new one stays one card away', async () => {
		const user = userEvent.setup();
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

		const options = await screen.findByRole('group', { name: /You have 1 credential/ });
		const existing = within(options).getByRole('radio', { name: /Stripe OAuth/ });
		expect(existing).toBeChecked();
		expect(within(options).getByText('Sign-in needed')).toBeVisible();
		expect(
			screen.getByText(/is ready to use — it just needs you to finish signing in/),
		).toBeVisible();
		expect(screen.getByRole('button', { name: 'Sign in to stripe' })).toBeEnabled();

		await user.click(within(options).getByRole('radio', { name: /Add a new credential/ }));
		expect(screen.queryByRole('button', { name: 'Sign in to stripe' })).not.toBeInTheDocument();
		expect(screen.queryByText(/just needs you to finish signing in/)).not.toBeInTheDocument();
		expect(screen.getByRole('button', { name: 'Add credential' })).toBeEnabled();
	});

	it('an API with no credential of yours shows no options, only the form', async () => {
		renderWithProviders(<QueueHarness items={[makeItem('slack.com', 'form')]} />);

		expect(await screen.findByRole('button', { name: 'Add credential' })).toBeEnabled();
		expect(screen.queryByRole('radio')).not.toBeInTheDocument();
		expect(
			screen.getByText(
				'This API needs a new credential. Nothing is stored until you save it.',
			),
		).toBeVisible();
	});

	it('walks the batch in pick order, stopping on every API', async () => {
		const { calls } = watchBinds();
		renderWithProviders(
			<QueueHarness
				items={[makeItem('slack.com', 'form'), coveredItem('stripe.com', 'cred_stripe')]}
			/>,
		);

		// No covered API is banked ahead of the rest: the first pick is on screen
		// and nothing has been bound behind the operator's back.
		expect(
			await within(await screen.findByTestId('queue-active-pane')).findByText('slack'),
		).toBeVisible();
		expect(progressRows()[0].textContent).toMatch(/^slack/);
		expect(rowFor('stripe')).toHaveAttribute('data-status', 'waiting');
		expect(screen.getByText('0 of 2 done')).toBeInTheDocument();
		expect(calls).toHaveLength(0);
	});

	it('dropping an item says the API is not added, with no promise of later', async () => {
		const { calls } = watchBinds();
		const user = userEvent.setup();
		renderWithProviders(<QueueHarness items={[makeItem('slack.com', 'form')]} />);

		await user.click(await screen.findByRole('button', { name: 'Not this one' }));
		const confirm = await screen.findByTestId('queue-drop-confirm');
		// No deferred state exists, so the copy cannot imply one.
		expect(confirm).toHaveTextContent("slack won't be added.");
		expect(confirm.textContent).not.toMatch(/skip|later/i);

		await user.click(screen.getByRole('button', { name: 'Drop slack' }));
		await waitFor(() => expect(rowFor('slack')).toHaveAttribute('data-status', 'dropped'));
		expect(rowFor('slack')).toHaveTextContent('Not added');
		expect(calls).toHaveLength(0);
		expect(screen.getByText('Nothing was added.')).toBeInTheDocument();
	});

	it('keeps the item when the drop is declined', async () => {
		const user = userEvent.setup();
		renderWithProviders(<QueueHarness items={[makeItem('slack.com', 'form')]} />);

		await user.click(await screen.findByRole('button', { name: 'Not this one' }));
		await user.click(screen.getByRole('button', { name: 'Keep it' }));
		await waitFor(() =>
			expect(screen.queryByTestId('queue-drop-confirm')).not.toBeInTheDocument(),
		);
		expect(screen.getByRole('button', { name: 'Add credential' })).toBeEnabled();
	});

	it('a failed bind is terminal for that item only, and retryable in place', async () => {
		let failing = true;
		worker.use(
			http.post('/agents/:id/credentials', async ({ request }) => {
				// Clone: falling through leaves the body for the store handler.
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

		// The failure must not stall the rest of the batch.
		await user.click(await screen.findByRole('button', { name: 'Use this credential' }));
		await waitFor(() => expect(rowFor('stripe')).toHaveAttribute('data-status', 'failed'));
		await waitFor(() => expect(rowFor('slack')).toHaveAttribute('data-status', 'active'));
		await user.click(screen.getByRole('button', { name: 'Use this credential' }));
		await waitFor(() => expect(rowFor('slack')).toHaveAttribute('data-status', 'added'));
		expect(screen.getByText('1 API added · 1 failed')).toBeInTheDocument();

		failing = false;
		await user.click(within(rowFor('stripe')).getByRole('button', { name: 'Try again' }));
		// Back in its pane, still on the credential it was going to use — the
		// bind is only retried once the operator confirms it again.
		await waitFor(() => expect(rowFor('stripe')).toHaveAttribute('data-status', 'active'));
		expect(screen.getByRole('radio', { name: /stripe\.com key/ })).toBeChecked();
		await user.click(screen.getByRole('button', { name: 'Use this credential' }));
		await waitFor(() => expect(rowFor('stripe')).toHaveAttribute('data-status', 'added'));
		expect(screen.getByText('2 APIs added')).toBeInTheDocument();
	});

	it('closing mid-way keeps what landed and hands the rest back for re-entry', async () => {
		const onClosed = vi.fn();
		const user = userEvent.setup();
		renderWithProviders(
			<QueueHarness
				items={[
					coveredItem('stripe.com', 'cred_stripe'),
					makeItem('slack.com', 'form'),
					makeItem('notion.so', 'form'),
				]}
				onClosed={onClosed}
			/>,
		);

		await user.click(await screen.findByRole('button', { name: 'Use this credential' }));
		await waitFor(() => expect(rowFor('stripe')).toHaveAttribute('data-status', 'added'));
		// The cost of closing is stated before it is paid.
		expect(
			screen.getByText(
				/Closing keeps the APIs already added; the remaining 2 wait here for next time\./,
			),
		).toBeInTheDocument();

		await user.click(screen.getByRole('button', { name: 'Close for now' }));
		expect(onClosed).toHaveBeenCalledTimes(1);
		expect(onClosed.mock.calls[0][0].map((i: PreflightItem) => i.key)).toEqual([
			'slack.com/main',
			'notion.so/main',
		]);

		// Re-entry resumes the remainder rather than replaying the whole batch.
		await user.click(screen.getByRole('button', { name: 'Reopen' }));
		await waitFor(() => expect(progressRows()).toHaveLength(2));
		expect(screen.getByText('Set up 2 APIs')).toBeInTheDocument();
	});

	it('asks which credential to use when several cover the API, and binds the choice', async () => {
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

		const use = await screen.findByRole('button', { name: 'Use this credential' });
		// Nothing is guessed for the operator: two accounts of one vendor are not
		// interchangeable, so the commit stays disabled until one is named.
		expect(use).toBeDisabled();

		await user.click(screen.getByRole('radio', { name: /Stripe — Sandbox/ }));
		await waitFor(() => expect(use).toBeEnabled());
		await user.click(use);

		await waitFor(() => expect(calls).toHaveLength(1));
		expect(calls[0].body).toEqual({ credential_id: 'cred_sandbox' });
		await waitFor(() => expect(rowFor('stripe')).toHaveAttribute('data-status', 'added'));
	});

	it('creates a credential for a form item without re-asking which API it is for', async () => {
		resetApisStore([
			makeMockApi({
				vendor: 'acme.com',
				name: 'main',
				displayName: 'acme',
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
		const { calls } = watchBinds();
		const user = userEvent.setup();
		renderWithProviders(<QueueHarness items={[makeItem('acme.com', 'form')]} />);

		await user.click(await screen.findByRole('button', { name: 'Add credential' }));

		// The API is the queue's premise, not the wizard's question — no pick
		// step, and no way back to one.
		expect(
			await screen.findByText('Fill in the credential details for acme'),
		).toBeInTheDocument();
		expect(screen.queryByRole('button', { name: 'Back' })).not.toBeInTheDocument();
		expect(screen.queryByRole('button', { name: 'Change' })).not.toBeInTheDocument();
		const nameInput = (await screen.findByPlaceholderText(
			'Production API key',
		)) as HTMLInputElement;
		await waitFor(() => expect(nameInput.value).toBe('acme'));

		// The spec-seeded header name proves the schemes landed — the credential
		// fields are held behind a skeleton until then.
		const fieldName = (await screen.findByPlaceholderText('X-Api-Key')) as HTMLInputElement;
		await waitFor(() => expect(fieldName.value).toBe('X-Acme-Key'));

		const secret = screen
			.getAllByDisplayValue('')
			.find((el) => (el as HTMLInputElement).type === 'password') as HTMLInputElement;
		await user.type(secret, 'sk_acme_123');
		await user.click(screen.getByRole('button', { name: 'Create credential' }));

		// Created, then bound, without the operator naming the API twice.
		await waitFor(() => expect(calls).toHaveLength(1));
		await waitFor(() => expect(rowFor('acme')).toHaveAttribute('data-status', 'added'));
		expect(screen.getByText('1 API added')).toBeInTheDocument();
	});

	it('dismissing the stacked credential drawer keeps the queue and its batch', async () => {
		const closed = vi.fn();
		const user = userEvent.setup();
		renderWithProviders(
			<QueueHarness items={[makeItem('slack.com', 'form')]} onClosed={closed} />,
		);

		await user.click(await screen.findByRole('button', { name: 'Add credential' }));
		// The wizard is a drawer stacked over the queue, so both sheets see the
		// same Escape.
		await waitFor(() => expect(screen.getAllByTestId('sheet-primitive')).toHaveLength(2));

		await browserUser.keyboard('{Escape}');

		// Only the wizard goes. Closing the queue would hand the batch back to
		// the host and drop the operator out of a flow they are mid-way through.
		await waitFor(() => expect(screen.getAllByTestId('sheet-primitive')).toHaveLength(1));
		expect(closed).not.toHaveBeenCalled();
		expect(screen.getByRole('button', { name: 'Add credential' })).toBeVisible();
	});

	it('passes an accessibility audit with a pane, a progress list and a drop confirm', async () => {
		const user = userEvent.setup();
		renderWithProviders(
			<QueueHarness
				items={[coveredItem('stripe.com', 'cred_stripe'), makeItem('slack.com', 'form')]}
			/>,
		);

		await user.click(await screen.findByRole('button', { name: 'Use this credential' }));
		await waitFor(() => expect(rowFor('stripe')).toHaveAttribute('data-status', 'added'));
		await user.click(screen.getByRole('button', { name: 'Not this one' }));
		await screen.findByTestId('queue-drop-confirm');

		await checkA11y(document.body, { modal: true });
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
		await screen.findByRole('group', { name: /You have 2 credentials/ });

		await checkA11y(document.body, { modal: true });
	});

	it('390px: the pane action and the progress list stay reachable', async () => {
		await page.viewport(390, 844);
		renderWithProviders(<QueueHarness items={[makeItem('slack.com', 'form')]} />);

		expect(await screen.findByRole('button', { name: 'Add credential' })).toBeVisible();
		expect(rowFor('slack')).toBeVisible();
	});

	describe('Back to APIs', () => {
		it('hands the batch back as a tray seed: owed APIs ticked, added ones locked', async () => {
			const onBack = vi.fn();
			const user = userEvent.setup();
			renderWithProviders(
				<QueueHarness
					items={[
						coveredItem('stripe.com', 'cred_stripe'),
						makeItem('slack.com', 'form'),
					]}
					onBack={onBack}
				/>,
			);

			await user.click(await screen.findByRole('button', { name: 'Use this credential' }));
			await waitFor(() => expect(rowFor('stripe')).toHaveAttribute('data-status', 'added'));

			const back = screen.getByRole('button', { name: 'Back to APIs' });
			await user.click(back);
			expect(onBack).toHaveBeenCalledTimes(1);
			const [seed, remaining] = onBack.mock.calls[0] as [
				{ picks: SelectedApi[]; added: SelectedApi[] },
				PreflightItem[],
			];
			expect(seed.picks.map((a) => a.vendor)).toEqual(['slack.com']);
			expect(seed.added.map((a) => a.vendor)).toEqual(['stripe.com']);
			expect(remaining.map((i) => i.key)).toEqual(['slack.com/main']);
		});

		it('no Back without a host that can take the operator there', async () => {
			renderWithProviders(<QueueHarness items={[makeItem('slack.com', 'form')]} />);
			await screen.findByRole('button', { name: 'Add credential' });
			expect(screen.queryByRole('button', { name: 'Back to APIs' })).not.toBeInTheDocument();
		});

		it('an untouched credential form goes back without asking', async () => {
			const onBack = vi.fn();
			const user = userEvent.setup();
			renderWithProviders(
				<QueueHarness items={[makeItem('slack.com', 'form')]} onBack={onBack} />,
			);

			await user.click(await screen.findByRole('button', { name: 'Add credential' }));
			const wizard = await screen.findByRole('dialog', { name: /Add credential/ });
			await user.click(within(wizard).getByRole('button', { name: 'Back to APIs' }));

			expect(onBack).toHaveBeenCalledTimes(1);
			expect(
				screen.queryByRole('dialog', { name: 'Discard this credential?' }),
			).not.toBeInTheDocument();
		});

		it('a typed-in credential asks before it is discarded', async () => {
			const onBack = vi.fn();
			const user = userEvent.setup();
			renderWithProviders(
				<QueueHarness items={[makeItem('slack.com', 'form')]} onBack={onBack} />,
			);

			await user.click(await screen.findByRole('button', { name: 'Add credential' }));
			const wizard = await screen.findByRole('dialog', { name: /Add credential/ });
			await user.type(within(wizard).getByLabelText(/^Name/), ' draft');
			await user.click(within(wizard).getByRole('button', { name: 'Back to APIs' }));

			const confirm = await screen.findByRole('dialog', { name: 'Discard this credential?' });
			expect(onBack).not.toHaveBeenCalled();

			// Keeping the draft leaves the operator in the form, input intact.
			await user.click(within(confirm).getByRole('button', { name: 'Cancel' }));
			await waitFor(() =>
				expect(
					screen.queryByRole('dialog', { name: 'Discard this credential?' }),
				).not.toBeInTheDocument(),
			);
			expect(within(wizard).getByLabelText(/^Name/)).toHaveValue('slack draft');

			await user.click(within(wizard).getByRole('button', { name: 'Back to APIs' }));
			await user.click(
				within(
					await screen.findByRole('dialog', { name: 'Discard this credential?' }),
				).getByRole('button', { name: 'Discard and go back' }),
			);
			expect(onBack).toHaveBeenCalledTimes(1);
		});
	});
});
