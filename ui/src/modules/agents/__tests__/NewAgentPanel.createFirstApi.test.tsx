/**
 * "Create it here" for an org's first agent: Create keeps the panel open on the
 * same first-API step the register routes end on (choices in the body, Close in the footer).
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { MotionConfig } from 'framer-motion';
import { useLocation } from 'react-router';
import { page } from 'vitest/browser';
import { http, HttpResponse } from 'msw';
import { worker } from '@/mocks/browser';
import {
	renderWithProviders,
	screen,
	waitFor,
	within,
	userEvent,
	checkA11y,
} from '@/__tests__/test-utils';
import { setToken } from '@/shared/api';
import { Toaster } from '@/shared/ui';
import {
	clearAgentsStore,
	resetAgentsStore,
	selfRegisterAgent,
} from '@/modules/agents/mocks/handlers';
import { catalogPage } from '@/modules/agents/mocks/githubCatalog';
import { isFirstRunDismissed } from '@/modules/agents/lib/firstRun';
import AgentsPage from '@/modules/agents/pages/AgentsPage';

function LocationProbe() {
	const location = useLocation();
	return <div data-testid="location-search">{location.search}</div>;
}

function renderPage() {
	return renderWithProviders(
		<MotionConfig reducedMotion="never">
			<AgentsPage />
			<LocationProbe />
			<Toaster />
		</MotionConfig>,
	);
}

type User = ReturnType<typeof userEvent.setup>;

const panel = () => screen.getByRole('dialog', { name: 'New agent' });
const queryPanel = () => screen.queryByRole('dialog', { name: 'New agent' });

let createdId: string | null = null;

async function createHere(user: User, name: string, button: string) {
	await screen.findByTestId('agents-empty-landing');
	await user.click(screen.getByRole('button', { name: 'New agent' }));
	const sheet = await screen.findByRole('dialog', { name: 'New agent' });
	await waitFor(() => expect(within(sheet).getByLabelText('Agent name')).toHaveFocus());
	await user.click(within(sheet).getByRole('tab', { name: 'Create it here' }));
	await waitFor(() =>
		expect(within(sheet).getByLabelText('Name', { exact: true })).toHaveFocus(),
	);
	await user.type(within(sheet).getByLabelText('Name', { exact: true }), name);
	await user.click(within(sheet).getByRole('button', { name: button }));
	expect(await screen.findByText('Agent created')).toBeInTheDocument();
	return sheet;
}

const footer = (sheet: HTMLElement) =>
	within(sheet).getByTestId('new-agent-panel-create').querySelector('footer') as HTMLElement;

async function createEmpty(user: User, name = 'hand-made') {
	const sheet = await createHere(user, name, 'Create');
	const firstApi = await within(sheet).findByTestId('first-api-panel');
	createdId =
		within(sheet).getByTestId('agent-id-fact').querySelector('code')?.textContent ?? null;
	return { sheet, firstApi };
}

/** Waits for the GitHub offer to settle (the workspace and catalog answered). */
async function githubButton(firstApi: HTMLElement) {
	const button = await within(firstApi).findByRole('button', { name: 'Continue with GitHub' });
	await waitFor(() => expect(button).toBeEnabled());
	return button;
}

describe('New agent panel — "Create it here" and the first API', () => {
	beforeEach(async () => {
		await page.viewport(1440, 900);
		setToken('test-token');
		window.localStorage.clear();
		resetAgentsStore();
		clearAgentsStore();
		createdId = null;
	});

	it('the footer is Cancel and Create; Create stays in the panel, on "Add GitHub to …", Skip in the body', async () => {
		const user = userEvent.setup();
		renderPage();
		const { sheet, firstApi } = await createEmpty(user);

		expect(
			await within(firstApi).findByRole('heading', { name: 'Add GitHub to hand-made' }),
		).toBeInTheDocument();
		expect(firstApi).toHaveTextContent(
			'Let it read issues and repos — you choose exactly what it can do.',
		);
		await githubButton(firstApi);
		expect(within(firstApi).getByRole('button', { name: 'Add another API' })).toBeEnabled();
		expect(within(firstApi).getByRole('button', { name: 'Skip for now' })).toBeEnabled();
		expect(within(sheet).getAllByRole('button', { name: 'Skip for now' })).toHaveLength(1);
		const footerButtons = within(footer(sheet)).getAllByRole('button');
		expect(footerButtons.map((b) => b.textContent?.trim())).toEqual(['Close']);

		expect(panel()).toBe(sheet);
		expect(within(sheet).getByRole('tab', { name: 'Create it here' })).toHaveAttribute(
			'aria-selected',
			'true',
		);
		expect(within(sheet).queryByLabelText('Name', { exact: true })).toBeNull();
		expect(within(sheet).queryByRole('button', { name: 'Create' })).toBeNull();
		const heading = within(sheet).getByRole('heading', { name: 'hand-made', level: 2 });
		await waitFor(() => expect(heading).toHaveFocus());
		expect(within(sheet).queryByTestId('register-progress')).toBeNull();
		expect(within(sheet).getByTestId('arrival-status')).toHaveAttribute(
			'data-status',
			'active',
		);
		expect(within(sheet).getByTestId('new-agent-rail-status-create')).toHaveTextContent(
			'hand-made · created',
		);
		expect(createdId).not.toBeNull();
		expect(isFirstRunDismissed(createdId!)).toBe(false);
		await waitFor(() => checkA11y(sheet), { timeout: 3000 });
	});

	it('"Continue with GitHub" opens its credential step over the fleet, and records the answer', async () => {
		const user = userEvent.setup();
		renderPage();
		const { firstApi } = await createEmpty(user);
		await user.click(await githubButton(firstApi));

		const queue = await screen.findByRole('dialog', { name: 'Set up 1 API for hand-made' });
		expect(within(queue).getByTestId('queue-active-pane')).toHaveAccessibleName(/GitHub/);
		expect(screen.queryByRole('dialog', { name: 'Add APIs' })).toBeNull();
		await waitFor(() => expect(queryPanel()).toBeNull());
		expect(screen.getByTestId('location-search')).toHaveTextContent(`agent=${createdId}`);
		expect(isFirstRunDismissed(createdId!)).toBe(true);
	});

	it('"Add another API" opens the Add-APIs tray with nothing ticked, and records the answer', async () => {
		const user = userEvent.setup();
		renderPage();
		const { firstApi } = await createEmpty(user);
		await githubButton(firstApi);
		await user.click(within(firstApi).getByRole('button', { name: 'Add another API' }));

		const tray = await screen.findByRole('dialog', { name: 'Add APIs' });
		expect(within(tray).getByText('Nothing selected yet.')).toBeInTheDocument();
		await waitFor(() => expect(queryPanel()).toBeNull());
		expect(screen.getByTestId('location-search')).toHaveTextContent(`agent=${createdId}`);
		expect(isFirstRunDismissed(createdId!)).toBe(true);
	});

	it('"Skip for now" closes onto the fleet with the agent selected, and records the answer', async () => {
		const user = userEvent.setup();
		renderPage();
		const { firstApi } = await createEmpty(user);
		await user.click(within(firstApi).getByRole('button', { name: 'Skip for now' }));

		await waitFor(() => expect(queryPanel()).toBeNull());
		expect(screen.getByTestId('location-search')).toHaveTextContent(`agent=${createdId}`);
		await waitFor(() =>
			expect(screen.getByRole('tab', { name: /hand-made/ })).toHaveAttribute(
				'aria-selected',
				'true',
			),
		);
		expect(screen.queryByRole('dialog', { name: 'Add APIs' })).toBeNull();
		expect(isFirstRunDismissed(createdId!)).toBe(true);
	});

	it('Escape on the step leaves it as "Skip for now" does', async () => {
		const user = userEvent.setup();
		renderPage();
		await createEmpty(user);
		await user.keyboard('{Escape}');
		await waitFor(() => expect(queryPanel()).toBeNull());
		expect(screen.getByTestId('location-search')).toHaveTextContent(`agent=${createdId}`);
		expect(screen.queryByRole('dialog', { name: 'Add APIs' })).toBeNull();
		expect(isFirstRunDismissed(createdId!)).toBe(true);
	});

	it('the footer\'s Close on the step leaves it as "Skip for now" does', async () => {
		const user = userEvent.setup();
		renderPage();
		const { sheet } = await createEmpty(user);
		await user.click(within(footer(sheet)).getByRole('button', { name: 'Close' }));

		await waitFor(() => expect(queryPanel()).toBeNull());
		expect(screen.getByTestId('location-search')).toHaveTextContent(`agent=${createdId}`);
		expect(screen.queryByRole('dialog', { name: 'Add APIs' })).toBeNull();
		expect(isFirstRunDismissed(createdId!)).toBe(true);

		await user.click(screen.getByRole('button', { name: 'New agent' }));
		const next = await screen.findByRole('dialog', { name: 'New agent' });
		await user.click(within(next).getByRole('tab', { name: 'Create it here' }));
		expect(await within(next).findByLabelText('Name', { exact: true })).toHaveValue('');
		expect(within(next).queryByTestId('first-api-panel')).toBeNull();
	});

	it('without GitHub to offer, falls back to "Give … its first API" and the tray', async () => {
		worker.use(
			http.get('/apis', () =>
				HttpResponse.json({ data: [], has_more: false, next_cursor: null }),
			),
			http.get('/catalog', () => HttpResponse.json(catalogPage([]))),
		);
		const user = userEvent.setup();
		renderPage();
		const { sheet, firstApi } = await createEmpty(user);

		await waitFor(() =>
			expect(
				within(firstApi).getByRole('heading', { name: 'Give hand-made its first API' }),
			).toBeInTheDocument(),
		);
		expect(firstApi).toHaveTextContent('Pick an API it can call, or upload an OpenAPI spec');
		expect(within(firstApi).queryByRole('button', { name: 'Continue with GitHub' })).toBeNull();
		expect(within(firstApi).queryByRole('button', { name: 'Add another API' })).toBeNull();
		expect(within(firstApi).getByRole('button', { name: 'Skip for now' })).toBeEnabled();
		expect(within(sheet).getAllByRole('button', { name: 'Skip for now' })).toHaveLength(1);
		expect(within(footer(sheet)).getByRole('button', { name: 'Close' })).toBeEnabled();
		await user.click(within(firstApi).getByRole('button', { name: 'Add an API' }));

		expect(await screen.findByRole('dialog', { name: 'Add APIs' })).toBeInTheDocument();
		await waitFor(() => expect(queryPanel()).toBeNull());
		expect(isFirstRunDismissed(createdId!)).toBe(true);
	});

	it("the form's footer is exactly Cancel and Create", async () => {
		const user = userEvent.setup();
		renderPage();
		await screen.findByTestId('agents-empty-landing');
		await user.click(screen.getByRole('button', { name: 'New agent' }));
		const sheet = await screen.findByRole('dialog', { name: 'New agent' });
		await user.click(within(sheet).getByRole('tab', { name: 'Create it here' }));
		await waitFor(() =>
			expect(within(sheet).getByLabelText('Name', { exact: true })).toHaveFocus(),
		);
		const buttons = within(footer(sheet)).getAllByRole('button');
		expect(buttons.map((b) => b.textContent?.trim())).toEqual(['Cancel', 'Create']);
		expect(
			within(sheet).queryByRole('button', { name: /Create and add APIs|Create empty/ }),
		).toBeNull();
	});

	it('an agent created pending is approved in the panel first, as an arrival is', async () => {
		// `POST /agents` creates active; this pins the register routes' rule should it answer pending.
		worker.use(
			http.post('/agents', async ({ request }) => {
				const { name } = (await request.json()) as { name: string };
				createdId = selfRegisterAgent(name);
				return HttpResponse.json(
					{
						id: createdId,
						name,
						status: 'pending',
						created_at: new Date().toISOString(),
					},
					{ status: 201 },
				);
			}),
		);
		const user = userEvent.setup();
		renderPage();
		const sheet = await createHere(user, 'held-back', 'Create');

		const card = await within(sheet).findByTestId('arrival-card');
		expect(within(sheet).getByTestId('new-agent-panel-create')).toContainElement(card);
		expect(within(card).getByTestId('arrival-decision')).toBeInTheDocument();
		expect(within(card).queryByTestId('first-api-panel')).toBeNull();
		expect(within(footer(sheet)).getByRole('button', { name: 'Close' })).toBeEnabled();
		expect(within(sheet).getByTestId('new-agent-rail-status-create')).toHaveTextContent(
			'held-back · pending',
		);
		expect(within(sheet).getAllByTestId('arrival-card')).toHaveLength(1);
		expect(within(sheet).getByTestId('new-agent-rail-status-register')).not.toHaveTextContent(
			'held-back',
		);
		const approveButton = within(card).getByRole('button', { name: 'Approve held-back' });
		await waitFor(() => expect(approveButton).toBeEnabled());
		await user.click(approveButton);

		const firstApi = await within(sheet).findByTestId('first-api-panel');
		expect(
			await within(firstApi).findByRole('heading', { name: 'Add GitHub to held-back' }),
		).toBeInTheDocument();
		expect(isFirstRunDismissed(createdId!)).toBe(false);
	});
});
