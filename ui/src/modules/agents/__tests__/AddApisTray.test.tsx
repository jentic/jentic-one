/**
 * AddApisTray — the multi-select Add-APIs step: rows toggle, Continue waits for an
 * honest preflight, and an already-reached API stays pickable for another account.
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
	settleAnimations,
	sheetSettled,
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
import { liveGithubCatalog } from '@/modules/agents/mocks/githubCatalog';

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
		suspendedReason: null,
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
	bindingsError = null,
	onRetryBindings,
	onContinue = (): void => {},
	seed = null,
}: {
	bindings?: CredentialBindingEntity[];
	bindingsError?: Error | null;
	onRetryBindings?: () => void;
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
				bindingsError={bindingsError}
				onRetryBindings={onRetryBindings}
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

function tickedRows(): HTMLElement[] {
	return screen
		.queryAllByRole('checkbox')
		.filter((el) => el.getAttribute('aria-checked') === 'true');
}

function footerCount(): string {
	return screen.getByTestId('sheet-primitive').querySelector('footer p')?.textContent ?? '';
}

function continueButton(): HTMLElement {
	return screen.getByRole('button', { name: 'Continue' });
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

describe('AddApisTray — multi-select picks, handed to the queue', () => {
	beforeEach(async () => {
		await page.viewport(1280, 900);
		setToken('test-token');
		resetCredentialsStore([STRIPE_CREDENTIAL]);
		resetApisStore(WORKSPACE_APIS);
	});

	it('asks one short question, with no selection panel — the list is the selection', async () => {
		renderWithProviders(<TrayHarness />);
		await row(/Stripe/);
		expect(screen.getByText(/Pick what/)).toHaveTextContent(
			'Pick what Support bot should be able to call.',
		);
		expect(screen.queryByRole('region', { name: 'Selected APIs' })).not.toBeInTheDocument();
		expect(screen.queryByTestId('tray-selection')).not.toBeInTheDocument();
		expect(screen.queryByTestId('tray-tally-line')).not.toBeInTheDocument();
		expect(footerCount()).toBe('Nothing selected yet.');
	});

	it('rows toggle instead of committing; the footer counts the picks', async () => {
		const user = userEvent.setup();
		renderWithProviders(<TrayHarness />);

		const stripe = await row(/Stripe/);
		const slack = await row(/Slack/);
		expect(stripe).toHaveAttribute('aria-checked', 'false');

		await user.click(stripe);
		await user.click(slack);
		expect(tickedRows()).toEqual([stripe, slack]);
		expect(footerCount()).toBe('2 selected');
		// Picking binds nothing and promises no cost here — the queue asks.
		expect(screen.queryByText(/reuses? a credential/)).not.toBeInTheDocument();
		expect(screen.queryByText('Needs a new credential')).not.toBeInTheDocument();

		// Clicking a picked row takes it back out.
		await user.click(stripe);
		expect(stripe).toHaveAttribute('aria-checked', 'false');
		expect(tickedRows()).toEqual([slack]);
		expect(footerCount()).toBe('1 selected');
	});

	it('Continue leads on with a trailing arrow, and never binds — even a lone covered pick', async () => {
		const user = userEvent.setup();
		renderWithProviders(<TrayHarness />);

		expect(continueButton()).toBeDisabled();
		expect(continueButton().lastElementChild).toHaveClass('lucide-arrow-right');
		expect(continueButton().querySelector('.lucide-plus')).toBeNull();

		// One matching credential is not a decision made for the operator: the
		// commit still leads to the queue, where it is confirmed or replaced.
		await user.click(await row(/Stripe/));
		await waitFor(() => expect(continueButton()).toBeEnabled());
		expect(screen.queryByRole('button', { name: /^Add \d+ API/ })).not.toBeInTheDocument();
	});

	it('preflights an oauth2-only API as one sign-in click for the queue', async () => {
		const onContinue = vi.fn();
		const user = userEvent.setup();
		renderWithProviders(<TrayHarness onContinue={onContinue} />);

		await user.click(await row(/Notion/));
		await waitFor(() => expect(continueButton()).toBeEnabled());
		await user.click(continueButton());
		// The mock server has a managed OAuth provider configured, so a new
		// oauth2 credential costs a consent round-trip and nothing typed.
		expect(onContinue.mock.calls[0][0]).toEqual([
			expect.objectContaining({ key: 'notion-so/main', outcome: 'oauth' }),
		]);
	});

	it('holds on a failed bindings read: the error and its retry show, Continue stays off', async () => {
		const onRetryBindings = vi.fn();
		const user = userEvent.setup();
		renderWithProviders(
			<TrayHarness bindingsError={new Error('boom')} onRetryBindings={onRetryBindings} />,
		);

		expect(
			await screen.findByText(/Could not read which APIs Support bot already has/),
		).toBeInTheDocument();
		await user.click(await row(/Stripe/));
		expect(footerCount()).toBe('1 selected');
		// Nothing goes on with a guessed binding set: every bound credential would look new.
		expect(continueButton()).toBeDisabled();

		await user.click(screen.getByRole('button', { name: 'Try again' }));
		expect(onRetryBindings).toHaveBeenCalledTimes(1);
	});

	it('holds Continue until the whole credential list is read', async () => {
		// A first-page-only list would classify an existing credential as
		// "needs a new credential" and hide it from the queue's choice.
		worker.use(http.get('/credentials', () => HttpResponse.error()));
		const user = userEvent.setup();
		renderWithProviders(<TrayHarness />);

		await user.click(await row(/Stripe/));
		expect(
			await screen.findByText(
				"Could not read your credentials, so these picks can't continue yet.",
			),
		).toBeInTheDocument();
		expect(screen.getByRole('button', { name: 'Try again' })).toBeEnabled();
		// Nothing may be committed on a guess.
		expect(continueButton()).toBeDisabled();
	});

	it('an API the agent already reaches stays pickable and names its account', async () => {
		const onContinue = vi.fn();
		const user = userEvent.setup();
		renderWithProviders(<TrayHarness bindings={[makeBinding()]} onContinue={onContinue} />);

		const slack = await row(/Slack/);
		expect(slack).toBeEnabled();
		// Presence, not visibility: the result rows stagger in from opacity 0.
		expect(
			within(slack).getByText('Added via Slack bot token · add another credential'),
		).toBeInTheDocument();

		await user.click(slack);
		await waitFor(() => expect(continueButton()).toBeEnabled());
		await user.click(continueButton());
		expect(onContinue.mock.calls[0][0]).toEqual([
			expect.objectContaining({
				key: 'slack-com/main',
				outcome: 'form',
				existing: [expect.objectContaining({ credentialId: 'cred_slack' })],
			}),
		]);
	});

	it('a pick made while the credential read is in flight says so, rather than a silent disable', async () => {
		const user = userEvent.setup();
		// Continue waits on the whole credential list, so hold that read open.
		let release = (): void => {};
		const held = new Promise<void>((resolve) => {
			release = resolve;
		});
		worker.use(
			http.get('*/credentials', async () => {
				await held;
				return HttpResponse.json({ data: [], _links: {} });
			}),
		);
		renderWithProviders(<TrayHarness />);

		const stripe = await row(/Stripe/);
		expect(footerCount()).toBe('Nothing selected yet.');

		await user.click(stripe);
		await waitFor(() =>
			expect(footerCount()).toBe('Checking which credentials you already have…'),
		);
		expect(continueButton()).toBeDisabled();
		expect(continueButton()).toHaveAttribute('aria-busy', 'true');

		release();
		await waitFor(() => expect(footerCount()).toBe('1 selected'), { timeout: 3000 });
		await waitFor(() => expect(continueButton()).toBeEnabled());
		expect(continueButton()).not.toHaveAttribute('aria-busy', 'true');
	});

	it('a picked API the agent already reaches says the pick adds another credential', async () => {
		const user = userEvent.setup();
		renderWithProviders(<TrayHarness bindings={[makeBinding()]} />);

		const slack = await row(/Slack/);
		expect(screen.queryByTestId('tray-record')).toBeNull();

		await user.click(slack);
		// The record flags a SECOND credential for an API the agent already calls — the checkbox can't.
		const record = await screen.findByTestId('tray-record');
		await waitFor(() => expect(record).toHaveTextContent('1 pick adds another credential'));
		await user.click(within(record).getByRole('button', { name: /Details/ }));
		const note = await screen.findByTestId('tray-added-via');
		await waitFor(() => expect(note).toBeVisible());
		expect(note).toHaveTextContent('Added via Slack bot token — this adds another credential');

		await user.click(slack);
		await waitFor(() => expect(screen.queryByTestId('tray-record')).toBeNull());
	});

	it('a covered API the agent already reaches offers only the credentials it lacks', async () => {
		const onContinue = vi.fn();
		const user = userEvent.setup();
		const sandbox = makeMockCredential({
			credential_id: 'cred_stripe_sandbox',
			name: 'Stripe sandbox',
			type: CredentialType.API_KEY,
			api: { vendor: 'stripe.com', name: 'main', version: '1.0.0' },
		});
		resetCredentialsStore([STRIPE_CREDENTIAL, sandbox]);
		const bound = makeBinding({
			credentialId: 'cred_stripe',
			name: 'Stripe key',
			serves: [{ vendor: 'stripe.com', name: 'main', version: null }],
		});
		renderWithProviders(<TrayHarness bindings={[bound]} onContinue={onContinue} />);

		await user.click(await row(/Stripe/));
		await waitFor(() => expect(continueButton()).toBeEnabled());
		await user.click(continueButton());
		const [item] = onContinue.mock.calls[0][0] as PreflightItem[];
		expect(item.outcome).toBe('choose');
		expect(item.covering.map((c) => c.credential_id)).toEqual(['cred_stripe_sandbox']);
	});

	it('a vendor-wildcard binding is named by the preflight once picked', async () => {
		const onContinue = vi.fn();
		const user = userEvent.setup();
		// A wildcard binding cannot be enumerated as a row key, so the row carries
		// no hint — the preflight is what names it.
		const wildcard = makeBinding({
			serves: [{ vendor: 'slack.com', name: null, version: null }],
		});
		renderWithProviders(<TrayHarness bindings={[wildcard]} onContinue={onContinue} />);

		const slack = await row(/Slack/);
		expect(within(slack).queryByText(/Added via/)).not.toBeInTheDocument();
		await user.click(slack);
		await user.click(await row(/Stripe/));
		await waitFor(() => expect(continueButton()).toBeEnabled());
		expect(footerCount()).toBe('2 selected');

		await user.click(continueButton());
		expect(onContinue).toHaveBeenCalledTimes(1);
		expect(onContinue.mock.calls[0][0]).toEqual([
			expect.objectContaining({
				key: 'slack-com/main',
				existing: [expect.objectContaining({ credentialId: 'cred_slack' })],
			}),
			expect.objectContaining({ key: 'stripe-com/main', outcome: 'choose' }),
		]);
		// Committing hands the picks to the queue, so the draft is spent.
		await waitFor(() => expect(tickedRows()).toHaveLength(0));
	});

	it('hands the batch on in pick order and keeps the covering credentials', async () => {
		const onContinue = vi.fn();
		const user = userEvent.setup();
		renderWithProviders(<TrayHarness onContinue={onContinue} />);

		await user.click(await row(/Slack/));
		await user.click(await row(/Stripe/));
		await waitFor(() => expect(continueButton()).toBeEnabled());

		await user.click(continueButton());
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
		expect(footerCount()).toBe('1 selected');

		// The queue is the only way an API arrives, so a mid-flow dismissal must
		// not silently discard the work.
		await closeTray(user);
		await user.click(screen.getByRole('button', { name: 'Open tray' }));
		expect(await row(/Stripe/)).toHaveAttribute('aria-checked', 'true');
		expect(footerCount()).toBe('1 selected');

		// The draft belongs to one agent, though.
		await closeTray(user);
		await user.click(screen.getByRole('button', { name: 'Switch agent' }));
		await user.click(screen.getByRole('button', { name: 'Open tray' }));
		expect(await row(/Stripe/)).toHaveAttribute('aria-checked', 'false');
		expect(footerCount()).toBe('Nothing selected yet.');
	});

	it('hands a catalog pick on as an import into the workspace', async () => {
		resetApisStore([WORKSPACE_APIS[0]], [makeMockCatalogEntry({ apiId: 'notion.so' })]);
		const onContinue = vi.fn();
		const user = userEvent.setup();
		renderWithProviders(<TrayHarness onContinue={onContinue} />);

		await user.type(screen.getByRole('textbox', { name: 'Search APIs' }), 'notion');
		await user.click(await row(/notion\.so/));
		await waitFor(() => expect(continueButton()).toBeEnabled());
		await user.click(continueButton());
		// No spec read yet, so the cost is a form, not a guessed sign-in; the queue imports it.
		expect(onContinue.mock.calls[0][0]).toEqual([
			expect.objectContaining({
				outcome: 'form',
				api: expect.objectContaining({ source: 'catalog' }),
			}),
		]);
	});

	it('an empty search lists only workspace APIs; "github" finds GitHub in the live catalog', async () => {
		worker.use(liveGithubCatalog);
		const user = userEvent.setup();
		renderWithProviders(<TrayHarness />);

		// The picker never browses the catalog: blank search = workspace only.
		expect(await row(/Stripe/)).toBeInTheDocument();
		expect(screen.queryByRole('checkbox', { name: /github/i })).toBeNull();

		await user.type(screen.getByRole('textbox', { name: 'Search APIs' }), 'github');
		expect(await row(/github\.com\/api\.github\.com/)).toBeInTheDocument();
		expect(await row(/github\.com\/ghec/)).toBeInTheDocument();
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

		const stripe = await row(/Stripe/);
		await user.click(stripe);

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
		expect(stripe).toHaveAttribute('aria-checked', 'true');
		expect(footerCount()).toBe('1 selected');
	});

	it('selects an uploaded API alongside the existing picks', async () => {
		stubCompletedImport(makeMockApi({ vendor: 'acme.io', name: 'main', displayName: 'Acme' }));
		const onContinue = vi.fn();
		const user = userEvent.setup();
		renderWithProviders(<TrayHarness onContinue={onContinue} />);

		await user.click(await row(/Stripe/));

		await user.click(screen.getByRole('button', { name: 'Upload an API' }));
		await waitFor(() => expect(screen.getByTestId('import-spec-dialog')).toBeVisible());
		await user.type(screen.getByTestId('import-spec-url'), 'https://acme.io/openapi.json');
		await user.click(screen.getByTestId('import-spec-submit'));

		// The operator never has to find what they just uploaded: it is picked.
		await waitFor(() => expect(footerCount()).toBe('2 selected'));
		await waitFor(() => expect(screen.getByTestId('import-spec-dialog')).not.toBeVisible());
		await waitFor(() => expect(continueButton()).toBeEnabled());
		await user.click(continueButton());
		expect((onContinue.mock.calls[0][0] as PreflightItem[]).map((i) => i.api.label)).toEqual([
			'Stripe',
			'Acme',
		]);
	});

	it('a covered pick is never chosen in the tray — no radios, no "Uses …" promise', async () => {
		const user = userEvent.setup();
		renderWithProviders(<TrayHarness />);

		await user.click(await row(/Stripe/));
		const sheet = screen.getByTestId('sheet-primitive');
		expect(within(sheet).queryByRole('radio')).not.toBeInTheDocument();
		expect(within(sheet).queryByRole('button', { name: /credential for/ })).toBeNull();
		expect(sheet).not.toHaveTextContent(/Uses Stripe key/);
	});

	it('several covering credentials go on together, and the choice waits for the queue', async () => {
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
		await waitFor(() => expect(continueButton()).toBeEnabled());
		await user.click(continueButton());
		const [item] = onContinue.mock.calls[0][0] as PreflightItem[];
		expect(item.outcome).toBe('choose');
		expect(item.covering.map((c) => c.credential_id)).toEqual([
			'cred_stripe',
			'cred_stripe_sandbox',
		]);
	});

	it('passes an accessibility audit with picks on screen', async () => {
		const user = userEvent.setup();
		renderWithProviders(<TrayHarness />);

		await user.click(await row(/Stripe/));
		await user.click(await row(/Slack/));
		await waitFor(() => expect(continueButton()).toBeEnabled());

		// The backdrop fades in once the sheet opens and the picker rows stagger in
		// from opacity 0; audit the settled frame, not a half-faded row.
		await sheetSettled(() =>
			Array.from(
				screen
					.getByTestId('sheet-primitive')
					.querySelectorAll('section[aria-labelledby^="picker-"] > ul > li'),
			),
		);
		await settleAnimations(document.body);
		await checkA11y(document.body, { modal: true });
	});

	it('390px: rows and the commit button stay reachable', async () => {
		await page.viewport(390, 844);
		const user = userEvent.setup();
		renderWithProviders(<TrayHarness />);

		await user.click(await row(/Stripe/));
		await waitFor(() => expect(continueButton()).toBeEnabled());
		expect(continueButton()).toBeVisible();
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
			expect(footerCount()).toBe('1 selected, 1 added so far');
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
			await waitFor(() => expect(continueButton()).toBeEnabled());
			await user.click(continueButton());
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

			const slack = await row(/Slack/);
			await waitFor(() => expect(slack).toHaveAttribute('aria-checked', 'true'));
			await user.click(slack);
			const done = await screen.findByRole('button', { name: 'Done' });
			await waitFor(() => expect(done).toBeEnabled());
			await user.click(done);
			expect(onContinue).toHaveBeenCalledWith([]);
		});

		it('records what the batch already added, behind one folded line', async () => {
			const user = userEvent.setup();
			const seed: QueueBackSeed = {
				picks: [pick('slack.com', 'Slack')],
				added: [pick('stripe.com', 'Stripe')],
			};
			renderWithProviders(<TrayHarness seed={seed} />);
			await row(/Slack/);

			// A checkbox can't say what an earlier pass committed, so a muted line does.
			const record = await screen.findByTestId('tray-record');
			expect(record).toHaveTextContent('1 already added');
			expect(screen.queryByTestId('tray-selection-added')).toBeNull();

			await user.click(within(record).getByRole('button', { name: /Details/ }));
			const added = await screen.findByTestId('tray-selection-added');
			await waitFor(() => expect(added).toBeVisible());
			expect(added).toHaveTextContent('Stripe');
			expect(added).toHaveTextContent('Added');
			// No remove: a saved binding is not undone by going back.
			expect(within(record).queryByRole('button', { name: /^Remove/ })).toBeNull();
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
	async function coveringIdsOnContinue(): Promise<string[]> {
		const onContinue = vi.fn();
		const user = userEvent.setup();
		renderWithProviders(
			<AuthProvider>
				<TrayHarness onContinue={onContinue} />
			</AuthProvider>,
		);
		await user.click(await row(/Stripe/));
		await waitFor(() => expect(continueButton()).toBeEnabled());
		await user.click(continueButton());
		const [item] = onContinue.mock.calls[0][0] as PreflightItem[];
		return item.covering.map((c) => c.credential_id);
	}

	it('offers a non-admin only the credentials they own', async () => {
		seedMe(['agents:read', 'agents:write', 'credentials:read']);
		expect(await coveringIdsOnContinue()).toEqual(['cred_mine']);
	});

	it('offers an org:admin every credential', async () => {
		seedMe(['org:admin']);
		expect(await coveringIdsOnContinue()).toEqual(['cred_mine', 'cred_theirs']);
	});
});

describe('AddApisTray — one-click sign-in (shared OAuth apps)', () => {
	beforeEach(async () => {
		await page.viewport(1280, 900);
		setToken('test-token');
		resetCredentialsStore([STRIPE_CREDENTIAL]);
		resetApisStore(WORKSPACE_APIS);
		worker.use(
			http.get('/vendors', () =>
				HttpResponse.json({
					data: [
						{
							entry_id: 'oar_gmail',
							registration_id: 'oar_gmail',
							key: 'googleapis.com',
							vendor: 'googleapis.com',
							display_name: 'Gmail',
							name: 'Acme Gmail',
							source: 'db',
							flow_kinds: ['authorization_code'],
							catalog_api_id: 'googleapis.com/gmail',
						},
					],
				}),
			),
		);
	});

	it('leads with the shared apps and opens a sign-in for this agent', async () => {
		const user = userEvent.setup();
		renderWithProviders(<TrayHarness />);

		const section = await screen.findByRole('region', { name: /one-click sign-in/i });
		const tile = within(section).getByRole('button', { name: /Acme Gmail/ });
		expect(within(tile).getByText('Shared app')).toBeInTheDocument();

		await user.click(tile);
		expect(await screen.findByText('via Acme Gmail')).toBeInTheDocument();
		// The connect is pinned to the tray's agent, so the picker is locked.
		const picker = await screen.findByRole('radiogroup', { name: /which agent uses this/i });
		expect(within(picker).getByRole('radio', { checked: true })).toBeDisabled();
	});

	it('Escape dismisses the stacked sign-in and leaves the tray open', async () => {
		const onClose = vi.fn();
		const user = userEvent.setup();
		renderWithProviders(
			<AddApisTray
				open
				onClose={onClose}
				agentId="agnt_1"
				agentName="Support bot"
				bindings={[]}
				bindingsError={null}
				onContinue={(): void => {}}
				seed={null}
			/>,
		);

		const section = await screen.findByRole('region', { name: /one-click sign-in/i });
		await user.click(within(section).getByRole('button', { name: /Acme Gmail/ }));
		expect(await screen.findByText('via Acme Gmail')).toBeInTheDocument();

		// The real (CDP-driven) keyboard: both sheets see the same Escape.
		await browserUser.keyboard('{Escape}');
		await waitFor(() => expect(screen.queryByText('via Acme Gmail')).not.toBeInTheDocument());
		expect(onClose).not.toHaveBeenCalled();
		expect(screen.getByRole('heading', { name: 'Add APIs' })).toBeInTheDocument();
	});
});
