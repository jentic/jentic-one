/**
 * AddApisTray — the multi-select Add-APIs step and its preflight tally. The
 * classification rules have their own unit specs (`apiPreflight.test.ts`); these
 * pin what needs a rendered picker: rows that toggle instead of committing, a
 * tally that stays honest while the credential list drains, rows that only
 * describe the next step (no credential is chosen here), and an already-reached
 * API that cannot be queued twice.
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
	makeMockCatalogEntry,
	makeMockCredential,
	resetApisStore,
	resetCredentialsStore,
} from '@/shared/credentials/mocks/handlers';
import { CredentialType } from '@/shared/credentials/api';
import { AuthProvider } from '@/shared/auth';
import { AddApisTray } from '@/modules/agents/components/flat/AddApisTray';
import type { PreflightItem } from '@/modules/agents/lib/apiPreflight';
import type { CredentialBindingEntity } from '@/modules/agents/api/types';
import type { QueueBackSeed } from '@/modules/agents/lib/setupQueue';
import type { SelectedApi } from '@/shared/credentials/api';

/** Three workspace APIs, one per preflight class the tray has to distinguish. */
const WORKSPACE_APIS = [
	makeMockApi({ vendor: 'stripe.com', name: 'main', displayName: 'Stripe' }),
	makeMockApi({ vendor: 'slack.com', name: 'main', displayName: 'Slack' }),
	makeMockApi({
		vendor: 'notion.so',
		name: 'main',
		displayName: 'Notion',
		securitySchemes: ['oauth2'],
	}),
];

/** Covers `stripe.com/main`, so picking Stripe asks for a choice in the queue. */
const STRIPE_CREDENTIAL = makeMockCredential({
	credential_id: 'cred_stripe',
	name: 'Stripe key',
	type: CredentialType.API_KEY,
	api: { vendor: 'stripe.com', name: 'main', version: '1.0.0' },
});

function makeBinding(over: Partial<CredentialBindingEntity> = {}): CredentialBindingEntity {
	return {
		id: 'acb_1',
		credentialId: 'cred_slack',
		name: 'Slack bot token',
		suspended: false,
		ruleSetId: null,
		boundAt: '2026-01-02T00:00:00Z',
		serves: [{ vendor: 'slack.com', name: 'main', version: null }],
		...over,
	};
}

/** Hosts the tray the way the agents surface does: the open flag and the
 * selected agent live outside it, so a spec can dismiss, reopen and switch. */
function TrayHarness({
	bindings = [],
	onContinue = (): void => {},
	seed = null,
}: {
	bindings?: CredentialBindingEntity[];
	onContinue?: (items: PreflightItem[]) => void;
	seed?: QueueBackSeed | null;
}) {
	const [open, setOpen] = useState(true);
	const [agentId, setAgentId] = useState('agnt_1');
	return (
		<>
			<button type="button" onClick={(): void => setOpen(true)}>
				Open tray
			</button>
			<button type="button" onClick={(): void => setAgentId('agnt_2')}>
				Switch agent
			</button>
			<AddApisTray
				open={open}
				onClose={(): void => setOpen(false)}
				agentId={agentId}
				agentName="Support bot"
				bindings={bindings}
				onContinue={onContinue}
				seed={seed}
			/>
		</>
	);
}

/** The row for one API — in multi-select mode every row is a checkbox. */
function row(name: RegExp) {
	return screen.findByRole('checkbox', { name });
}

function selectionRows(): HTMLElement[] {
	return screen.queryAllByTestId('tray-selection');
}

function tallyLines(): string[] {
	return screen.queryAllByTestId('tray-tally-line').map((el) => el.textContent ?? '');
}

/** An import that completes on submit and registers `api` — no job polling. */
function stubCompletedImport({ row }: ReturnType<typeof makeMockApi>): void {
	const { vendor, name, version } = row.api;
	worker.use(
		http.post('/apis', () =>
			HttpResponse.json(
				{ job_id: 'job_upload', status: 'completed', _links: { self: '/jobs/job_upload' } },
				{ status: 202 },
			),
		),
		http.get('/jobs/job_upload/result', () =>
			HttpResponse.json({ revisions: [{ api: { vendor, name, version } }] }),
		),
		http.get(`/apis/${vendor}/${name}/${version}`, () => HttpResponse.json(row)),
	);
}

async function closeTray(user: ReturnType<typeof userEvent.setup>): Promise<void> {
	await user.click(screen.getByRole('button', { name: 'Cancel' }));
	await waitFor(() => expect(screen.queryByTestId('sheet-primitive')).not.toBeInTheDocument(), {
		timeout: 2000,
	});
}

describe('AddApisTray — multi-select picks and the preflight tally', () => {
	beforeEach(async () => {
		await page.viewport(1280, 900);
		setToken('test-token');
		resetCredentialsStore([STRIPE_CREDENTIAL]);
		resetApisStore(WORKSPACE_APIS);
	});

	it('states the no-skip contract up front', async () => {
		renderWithProviders(<TrayHarness />);
		await row(/Stripe/);
		// The flow is longer than a skip-based one; saying so before the first
		// pick is what keeps it honest.
		expect(screen.getByText(/nothing is set up later/)).toBeInTheDocument();
	});

	it('rows toggle instead of committing, and each pick shows what it costs', async () => {
		const user = userEvent.setup();
		renderWithProviders(<TrayHarness />);

		const stripe = await row(/Stripe/);
		const slack = await row(/Slack/);
		expect(stripe).toHaveAttribute('aria-checked', 'false');

		await user.click(stripe);
		await user.click(slack);

		await waitFor(() => expect(selectionRows()).toHaveLength(2));
		expect(stripe).toHaveAttribute('aria-checked', 'true');
		expect(
			within(selectionRows()[0]).getByText('Choose a credential in the next step'),
		).toBeVisible();
		expect(within(selectionRows()[1]).getByText('Needs a new credential')).toBeVisible();

		await waitFor(() =>
			expect(tallyLines()).toEqual([
				'1 API: choose from your existing credentials in the next step',
				'1 API needs a new credential',
			]),
		);
		expect(screen.queryByText(/reuses? a credential/)).not.toBeInTheDocument();

		// Clicking a picked row takes it back out.
		await user.click(stripe);
		await waitFor(() => expect(selectionRows()).toHaveLength(1));
		expect(stripe).toHaveAttribute('aria-checked', 'false');
	});

	it('removing a pick from the selection list drops it', async () => {
		const user = userEvent.setup();
		renderWithProviders(<TrayHarness />);

		await user.click(await row(/Slack/));
		await waitFor(() => expect(selectionRows()).toHaveLength(1));

		await user.click(screen.getByRole('button', { name: 'Remove Slack' }));
		await waitFor(() => expect(selectionRows()).toHaveLength(0));
		expect(screen.getByText('Nothing selected yet.')).toBeInTheDocument();
	});

	it('a lone covered pick still goes to the next step — the button never binds', async () => {
		const user = userEvent.setup();
		renderWithProviders(<TrayHarness />);

		const commit = screen.getByRole('button', { name: 'Continue' });
		expect(commit).toBeDisabled();

		// One matching credential is not a decision made for the operator: the
		// commit still leads to the queue, where it is confirmed or replaced.
		await user.click(await row(/Stripe/));
		await waitFor(() => expect(screen.getByRole('button', { name: 'Continue' })).toBeEnabled());
		expect(screen.queryByRole('button', { name: /^Add \d+ API/ })).not.toBeInTheDocument();
	});

	it('claims one sign-in click only for an oauth2-only API', async () => {
		const user = userEvent.setup();
		renderWithProviders(<TrayHarness />);

		await user.click(await row(/Notion/));
		await waitFor(() => expect(selectionRows()).toHaveLength(1));
		// The mock server has a managed OAuth provider configured, so a new
		// oauth2 credential costs a consent round-trip and nothing typed.
		expect(within(selectionRows()[0]).getByText('One sign-in click')).toBeVisible();
		await waitFor(() => expect(tallyLines()).toEqual(['1 API needs one sign-in click']));
	});

	it('withholds the tally until the whole credential list is read', async () => {
		// A first-page-only list would classify an existing credential as
		// "needs a new credential" and hide it from the queue's choice.
		worker.use(http.get('/credentials', () => HttpResponse.error()));
		const user = userEvent.setup();
		renderWithProviders(<TrayHarness />);

		await user.click(await row(/Stripe/));
		await waitFor(() => expect(selectionRows()).toHaveLength(1));

		expect(await screen.findByText(/the cost of these picks is unknown/)).toBeInTheDocument();
		expect(tallyLines()).toEqual([]);
		expect(screen.getByRole('button', { name: 'Try again' })).toBeEnabled();
		// Nothing may be committed on a guess.
		expect(screen.getByRole('button', { name: 'Continue' })).toBeDisabled();
	});

	it('an API the agent already reaches cannot be picked again', async () => {
		renderWithProviders(<TrayHarness bindings={[makeBinding()]} />);

		const slack = await row(/Slack/);
		expect(slack).toBeDisabled();
		// Presence, not visibility: the result rows stagger in from opacity 0.
		expect(within(slack).getByText('Already added')).toBeInTheDocument();
		expect(selectionRows()).toHaveLength(0);
	});

	it('a vendor-wildcard binding is caught by the preflight and excluded from the commit', async () => {
		const onContinue = vi.fn();
		const user = userEvent.setup();
		// A wildcard binding cannot be enumerated as a row key, so the row stays
		// pickable — the preflight is what has to catch it.
		const wildcard = makeBinding({
			serves: [{ vendor: 'slack.com', name: null, version: null }],
		});
		renderWithProviders(<TrayHarness bindings={[wildcard]} onContinue={onContinue} />);

		await user.click(await row(/Slack/));
		await waitFor(() => expect(selectionRows()).toHaveLength(1));
		expect(
			within(selectionRows()[0]).getByText('Already added via Slack bot token'),
		).toBeVisible();
		// Nothing actionable, so there is nothing to hand on.
		expect(screen.getByRole('button', { name: 'Continue' })).toBeDisabled();

		await user.click(await row(/Stripe/));
		await waitFor(() => expect(screen.getByRole('button', { name: 'Continue' })).toBeEnabled());
		expect(screen.getByText('2 selected, 1 already added')).toBeInTheDocument();

		await user.click(screen.getByRole('button', { name: 'Continue' }));
		expect(onContinue).toHaveBeenCalledTimes(1);
		expect(onContinue.mock.calls[0][0]).toEqual([
			expect.objectContaining({ key: 'stripe-com/main', outcome: 'choose' }),
		]);
		// Committing hands the picks to the queue, so the draft is spent.
		await waitFor(() => expect(selectionRows()).toHaveLength(0));
	});

	it('hands the batch on in pick order and keeps the covering credentials', async () => {
		const onContinue = vi.fn();
		const user = userEvent.setup();
		renderWithProviders(<TrayHarness onContinue={onContinue} />);

		await user.click(await row(/Slack/));
		await user.click(await row(/Stripe/));
		await waitFor(() => expect(selectionRows()).toHaveLength(2));
		await waitFor(() => expect(screen.getByRole('button', { name: 'Continue' })).toBeEnabled());

		await user.click(screen.getByRole('button', { name: 'Continue' }));
		const items = onContinue.mock.calls[0][0] as PreflightItem[];
		expect(items.map((i) => [i.key, i.outcome])).toEqual([
			['slack-com/main', 'form'],
			['stripe-com/main', 'choose'],
		]);
		// The queue offers them without re-reading the credential list.
		expect(items[1].covering.map((c) => c.credential_id)).toEqual(['cred_stripe']);
	});

	it('picks survive a dismissal, and reset when the agent changes', async () => {
		const user = userEvent.setup();
		renderWithProviders(<TrayHarness />);

		await user.click(await row(/Stripe/));
		await waitFor(() => expect(selectionRows()).toHaveLength(1));

		// The queue is the only way an API arrives, so a mid-flow dismissal must
		// not silently discard the work.
		await closeTray(user);
		await user.click(screen.getByRole('button', { name: 'Open tray' }));
		await waitFor(() => expect(selectionRows()).toHaveLength(1));

		// The draft belongs to one agent, though.
		await closeTray(user);
		await user.click(screen.getByRole('button', { name: 'Switch agent' }));
		await user.click(screen.getByRole('button', { name: 'Open tray' }));
		await row(/Stripe/);
		expect(selectionRows()).toHaveLength(0);
	});

	it('flags a catalog pick as an import into the workspace', async () => {
		resetApisStore([WORKSPACE_APIS[0]], [makeMockCatalogEntry({ apiId: 'notion.so' })]);
		const user = userEvent.setup();
		renderWithProviders(<TrayHarness />);

		await user.type(screen.getByRole('textbox', { name: 'Search APIs' }), 'notion');
		await user.click(await row(/notion\.so/));

		await waitFor(() => expect(selectionRows()).toHaveLength(1));
		// No spec has been read yet, so the cost is stated as a form rather than
		// guessed as a sign-in click.
		expect(within(selectionRows()[0]).getByText('Needs a new credential')).toBeVisible();
		expect(
			await screen.findByText('1 API will be imported into your Workspace.'),
		).toBeInTheDocument();
	});

	it('a search that finds nothing offers the spec upload instead of a dead end', async () => {
		const user = userEvent.setup();
		renderWithProviders(<TrayHarness />);

		await user.type(
			screen.getByRole('textbox', { name: 'Search APIs' }),
			'nothing-matches-this',
		);
		expect(await screen.findByText('No APIs found')).toBeInTheDocument();
		// The one moment the operator has PROVED the API is not catalogued.
		expect(screen.getByText(/or add it from its OpenAPI spec/)).toBeInTheDocument();
		expect(screen.getAllByRole('button', { name: 'Upload an API' }).length).toBeGreaterThan(0);
	});

	it('uploading a spec from the footer leaves the picks alone', async () => {
		const user = userEvent.setup();
		renderWithProviders(<TrayHarness />);

		await user.click(await row(/Stripe/));
		await waitFor(() => expect(selectionRows()).toHaveLength(1));

		// Without this, "the API I need isn't listed" means abandoning the batch,
		// importing on the Workspace page, and starting the flow over.
		await user.click(screen.getByRole('button', { name: 'Upload an API' }));
		// Visibility, not presence: a native `<dialog>` is always mounted and
		// `showModal()` is what reveals it.
		await waitFor(() => expect(screen.getByTestId('import-spec-dialog')).toBeVisible());

		// By name — the tray sheet is a dialog too, and it stays open behind this one.
		const dialog = screen.getByRole('dialog', { name: 'Import an API' });
		await user.click(within(dialog).getByRole('button', { name: 'Close' }));
		await waitFor(() => expect(screen.getByTestId('import-spec-dialog')).not.toBeVisible());
		expect(selectionRows()).toHaveLength(1);
	});

	it('selects an uploaded API alongside the existing picks', async () => {
		stubCompletedImport(makeMockApi({ vendor: 'acme.io', name: 'main', displayName: 'Acme' }));
		const user = userEvent.setup();
		renderWithProviders(<TrayHarness />);

		await user.click(await row(/Stripe/));
		await waitFor(() => expect(selectionRows()).toHaveLength(1));

		await user.click(screen.getByRole('button', { name: 'Upload an API' }));
		await waitFor(() => expect(screen.getByTestId('import-spec-dialog')).toBeVisible());
		await user.type(screen.getByTestId('import-spec-url'), 'https://acme.io/openapi.json');
		await user.click(screen.getByTestId('import-spec-submit'));

		// The operator never has to find what they just uploaded: it is picked.
		await waitFor(() => expect(selectionRows()).toHaveLength(2));
		expect(selectionRows()[1]).toHaveTextContent('Acme');
		expect(selectionRows()[0]).toHaveTextContent('Stripe');
		await waitFor(() => expect(screen.getByTestId('import-spec-dialog')).not.toBeVisible());
	});

	it('a covered pick is labelled, never chosen in the tray', async () => {
		const user = userEvent.setup();
		renderWithProviders(<TrayHarness />);

		await user.click(await row(/Stripe/));
		await waitFor(() => expect(selectionRows()).toHaveLength(1));
		const selection = selectionRows()[0];
		expect(within(selection).getByText('Choose a credential in the next step')).toBeVisible();
		expect(within(selection).getByTestId('tray-covering-count')).toHaveTextContent(
			'1 of your credentials covers this API — use it or add a new one',
		);

		// No inline selector: no Choose/Change toggle, no radio cards, and no
		// "Uses <credential>" line promising a binding nobody confirmed.
		expect(within(selection).queryByRole('button', { name: /credential for/ })).toBeNull();
		expect(within(selection).queryByRole('radio')).not.toBeInTheDocument();
		expect(within(selection).queryByRole('group')).not.toBeInTheDocument();
		expect(selection).not.toHaveTextContent(/Uses Stripe key/);
		// The only button on the row removes the pick.
		expect(within(selection).getAllByRole('button')).toHaveLength(1);
		expect(within(selection).getByRole('button', { name: 'Remove Stripe' })).toBeVisible();
	});

	it('several covering credentials are counted, and the choice waits for the queue', async () => {
		resetCredentialsStore([
			STRIPE_CREDENTIAL,
			makeMockCredential({
				credential_id: 'cred_stripe_sandbox',
				name: 'Stripe sandbox',
				type: CredentialType.BEARER_TOKEN,
				api: { vendor: 'stripe.com', name: 'main', version: '1.0.0' },
			}),
		]);
		const onContinue = vi.fn();
		const user = userEvent.setup();
		renderWithProviders(<TrayHarness onContinue={onContinue} />);

		await user.click(await row(/Stripe/));
		await waitFor(() => expect(selectionRows()).toHaveLength(1));
		const selection = selectionRows()[0];
		expect(
			await within(selection).findByText(
				'2 of your credentials cover this API — use one or add a new one',
			),
		).toBeVisible();
		expect(within(selection).queryByRole('radio')).not.toBeInTheDocument();
		await waitFor(() =>
			expect(tallyLines()).toEqual([
				'1 API: choose from your existing credentials in the next step',
			]),
		);

		await user.click(screen.getByRole('button', { name: 'Continue' }));
		const [item] = onContinue.mock.calls[0][0] as PreflightItem[];
		expect(item.outcome).toBe('choose');
		expect(item.covering.map((c) => c.credential_id)).toEqual([
			'cred_stripe',
			'cred_stripe_sandbox',
		]);
	});

	it('passes an accessibility audit with picks and a tally on screen', async () => {
		const user = userEvent.setup();
		renderWithProviders(<TrayHarness />);

		await user.click(await row(/Stripe/));
		await user.click(await row(/Slack/));
		await waitFor(() => expect(tallyLines()).toHaveLength(2));

		await checkA11y(document.body, { modal: true });
	});

	it('390px: rows and the commit button stay reachable', async () => {
		await page.viewport(390, 844);
		const user = userEvent.setup();
		renderWithProviders(<TrayHarness />);

		await user.click(await row(/Stripe/));
		await waitFor(() => expect(screen.getByRole('button', { name: 'Continue' })).toBeEnabled());
		expect(screen.getByRole('button', { name: 'Continue' })).toBeVisible();
	});

	describe('editing a batch after Back', () => {
		const pick = (vendor: string, label: string): SelectedApi => ({
			source: 'local',
			vendor,
			name: 'main',
			version: '1.0.0',
			label,
		});

		it('comes back with the owed APIs ticked and the added ones locked', async () => {
			const seed: QueueBackSeed = {
				picks: [pick('slack.com', 'Slack')],
				added: [pick('stripe.com', 'Stripe')],
			};
			renderWithProviders(<TrayHarness seed={seed} />);

			const slack = await row(/Slack/);
			await waitFor(() => expect(slack).toHaveAttribute('aria-checked', 'true'));
			expect(slack).toBeEnabled();

			// Going back never undoes a saved binding: the row reads ticked but cannot
			// be unticked.
			const stripe = await row(/Stripe/);
			expect(stripe).toHaveAttribute('aria-checked', 'true');
			expect(stripe).toBeDisabled();
			expect(within(stripe).getByText('Added')).toBeInTheDocument();

			const added = screen.getByTestId('tray-selection-added');
			expect(added).toHaveTextContent('Stripe');
			expect(within(added).queryByRole('button')).not.toBeInTheDocument();
			expect(screen.getByText('1 selected, 1 added so far')).toBeInTheDocument();
		});

		it('hands on only what is still owed — the locked rows are not re-sent', async () => {
			const onContinue = vi.fn();
			const user = userEvent.setup();
			const seed: QueueBackSeed = {
				picks: [pick('slack.com', 'Slack')],
				added: [pick('stripe.com', 'Stripe')],
			};
			renderWithProviders(<TrayHarness seed={seed} onContinue={onContinue} />);

			await user.click(await row(/Notion/));
			await waitFor(() =>
				expect(screen.getByRole('button', { name: 'Continue' })).toBeEnabled(),
			);
			await user.click(screen.getByRole('button', { name: 'Continue' }));
			const items = onContinue.mock.calls[0][0] as PreflightItem[];
			expect(items.map((i) => i.api.vendor)).toEqual(['slack.com', 'notion.so']);
		});

		it('unticking everything still owed finishes rather than stranding the batch', async () => {
			const onContinue = vi.fn();
			const user = userEvent.setup();
			const seed: QueueBackSeed = {
				picks: [pick('slack.com', 'Slack')],
				added: [pick('stripe.com', 'Stripe')],
			};
			renderWithProviders(<TrayHarness seed={seed} onContinue={onContinue} />);

			await user.click(screen.getByRole('button', { name: 'Remove Slack' }));
			const done = await screen.findByRole('button', { name: 'Done' });
			await waitFor(() => expect(done).toBeEnabled());
			await user.click(done);
			expect(onContinue).toHaveBeenCalledWith([]);
		});
	});
});

describe('AddApisTray — offers only credentials the viewer may bind', () => {
	// Binding is ownership-scoped server-side (a non-admin binding a credential
	// they don't own gets a 404), so the tray and queue must not offer those.
	const ME = 'usr_member_1';

	function seedMe(permissions: string[]) {
		worker.use(
			http.get('/users/me', () =>
				HttpResponse.json({
					id: ME,
					email: 'member@local',
					first_name: 'Member',
					last_name: 'User',
					active: true,
					permissions,
					must_change_password: false,
					created_at: '2026-01-01T00:00:00Z',
					updated_at: null,
				}),
			),
		);
	}

	beforeEach(async () => {
		await page.viewport(1280, 900);
		setToken('test-token');
		resetApisStore(WORKSPACE_APIS);
		resetCredentialsStore([
			makeMockCredential({
				credential_id: 'cred_mine',
				name: 'My token',
				api: { vendor: 'stripe.com', name: 'main', version: '1.0.0' },
				created_by: ME,
			}),
			makeMockCredential({
				credential_id: 'cred_theirs',
				name: 'Shared token',
				api: { vendor: 'stripe.com', name: 'main', version: '1.0.0' },
				created_by: 'usr_someone_else',
			}),
		]);
	});

	/** Pick Stripe (both credentials cover it) and return what Continue hands on. */
	async function coveringIdsOnContinue(expectedLabel: string): Promise<string[]> {
		const onContinue = vi.fn();
		const user = userEvent.setup();
		renderWithProviders(
			<AuthProvider>
				<TrayHarness onContinue={onContinue} />
			</AuthProvider>,
		);
		await user.click(await row(/Stripe/));
		await waitFor(() => expect(selectionRows()).toHaveLength(1));
		await waitFor(() =>
			expect(screen.getByTestId('tray-covering-count')).toHaveTextContent(expectedLabel),
		);
		await user.click(screen.getByRole('button', { name: 'Continue' }));
		const [item] = onContinue.mock.calls[0][0] as PreflightItem[];
		return item.covering.map((c) => c.credential_id);
	}

	it('offers a non-admin only the credentials they own', async () => {
		seedMe(['agents:read', 'agents:write', 'credentials:read']);
		expect(
			await coveringIdsOnContinue(
				'1 of your credentials covers this API — use it or add a new one',
			),
		).toEqual(['cred_mine']);
	});

	it('offers an org:admin every credential', async () => {
		seedMe(['org:admin']);
		expect(
			await coveringIdsOnContinue(
				'2 of your credentials cover this API — use one or add a new one',
			),
		).toEqual(['cred_mine', 'cred_theirs']);
	});
});
