/**
 * AddApisTray — the multi-select Add-APIs step and its preflight tally.
 *
 * The classification rules have their own unit specs (`apiPreflight.test.ts`);
 * these pin the behaviours that only exist once the rules meet a rendered
 * picker: rows that toggle instead of committing, a tally that stays honest
 * while the credential list is still draining, picks that survive a dismissal
 * because there is no `Skip for now` to fall back on (D13), and an
 * already-reached API that cannot be queued for a duplicate bind.
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
import { AddApisTray } from '@/modules/agents/components/flat/AddApisTray';
import type { PreflightItem } from '@/modules/agents/lib/apiPreflight';
import type { CredentialBindingEntity } from '@/modules/agents/api/types';

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

/** Covers `stripe.com/main`, so picking Stripe is a reuse. */
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

/**
 * Hosts the tray the way the agents surface does: the open/close flag and the
 * selected agent live outside it, so a spec can dismiss, reopen, and switch
 * agents and watch what the draft does.
 */
function TrayHarness({
	bindings = [],
	onContinue = (): void => {},
}: {
	bindings?: CredentialBindingEntity[];
	onContinue?: (items: PreflightItem[]) => void;
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
		// pick is what keeps it honest (D13).
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
		expect(within(selectionRows()[0]).getByText('Reuses a credential you have')).toBeVisible();
		expect(within(selectionRows()[1]).getByText('Needs a new credential')).toBeVisible();

		await waitFor(() =>
			expect(tallyLines()).toEqual([
				'1 API reuses a credential you already have',
				'1 API needs a new credential',
			]),
		);

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

	it('when nothing needs a queue stop, the button says it will finish the job', async () => {
		const user = userEvent.setup();
		renderWithProviders(<TrayHarness />);

		const commit = screen.getByRole('button', { name: /Continue|Add \d+ API/ });
		expect(commit).toBeDisabled();

		// A lone reuse binds straight through — no setup queue to promise.
		await user.click(await row(/Stripe/));
		await waitFor(() =>
			expect(screen.getByRole('button', { name: 'Add 1 API' })).toBeEnabled(),
		);

		// Adding a pick that needs a credential brings the queue back.
		await user.click(await row(/Slack/));
		await waitFor(() => expect(screen.getByRole('button', { name: 'Continue' })).toBeEnabled());
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
		// "needs a new credential" and turn a free reuse into a form.
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
		await waitFor(() =>
			expect(screen.getByRole('button', { name: 'Add 1 API' })).toBeEnabled(),
		);
		expect(screen.getByText('2 selected, 1 already added')).toBeInTheDocument();

		await user.click(screen.getByRole('button', { name: 'Add 1 API' }));
		expect(onContinue).toHaveBeenCalledTimes(1);
		expect(onContinue.mock.calls[0][0]).toEqual([
			expect.objectContaining({ key: 'stripe.com/main', outcome: 'reuse' }),
		]);
		// Committing hands the picks to the queue, so the draft is spent.
		await waitFor(() => expect(selectionRows()).toHaveLength(0));
	});

	it('hands the batch on in pick order and keeps the credential candidates', async () => {
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
			['slack.com/main', 'form'],
			['stripe.com/main', 'reuse'],
		]);
		// The queue binds the reuse without re-reading the credential list.
		expect(items[1].candidates.map((c) => c.credential_id)).toEqual(['cred_stripe']);
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

	it('flags a catalog pick as an import into the workspace (D5)', async () => {
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
		// The one moment the operator has PROVED the API is not catalogued (D6).
		expect(screen.getByText(/or add it from its OpenAPI spec/)).toBeInTheDocument();
		expect(screen.getAllByRole('button', { name: 'Upload an API' }).length).toBeGreaterThan(0);
	});

	it('uploading a spec from the footer leaves the picks alone', async () => {
		const user = userEvent.setup();
		renderWithProviders(<TrayHarness />);

		await user.click(await row(/Stripe/));
		await waitFor(() => expect(selectionRows()).toHaveLength(1));

		// Without this, "the API I need isn't listed" means abandoning the batch,
		// importing on the Workspace page, and starting the flow over (D6).
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

	it('passes an accessibility audit with picks and a tally on screen', async () => {
		const user = userEvent.setup();
		renderWithProviders(<TrayHarness />);

		await user.click(await row(/Stripe/));
		await user.click(await row(/Slack/));
		await waitFor(() => expect(tallyLines()).toHaveLength(2));

		await checkA11y(document.body);
	});

	it('390px: rows and the commit button stay reachable', async () => {
		await page.viewport(390, 844);
		const user = userEvent.setup();
		renderWithProviders(<TrayHarness />);

		await user.click(await row(/Stripe/));
		await waitFor(() =>
			expect(screen.getByRole('button', { name: 'Add 1 API' })).toBeEnabled(),
		);
		expect(screen.getByRole('button', { name: 'Add 1 API' })).toBeVisible();
	});
});
