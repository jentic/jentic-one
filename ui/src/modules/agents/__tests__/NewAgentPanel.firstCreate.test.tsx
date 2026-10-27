/**
 * Where "Create" lands: the first agent's first-API step in the same mounted panel
 * (across the landing → fleet switch), or over a fleet, the Add-APIs tray.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { MotionConfig } from 'framer-motion';
import { useLocation } from 'react-router';
import { page } from 'vitest/browser';
import { delay, http } from 'msw';
import { worker } from '@/mocks/browser';
import { renderWithProviders, screen, waitFor, within, userEvent } from '@/__tests__/test-utils';
import { setToken } from '@/shared/api';
import { Toaster } from '@/shared/ui';
import { clearAgentsStore, resetAgentsStore } from '@/modules/agents/mocks/handlers';
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
const selectedId = () =>
	/agent=([^&]+)/.exec(screen.getByTestId('location-search').textContent ?? '')?.[1] ?? null;

async function openCreate(user: User, byHotkey = false) {
	if (byHotkey) await user.keyboard('n');
	else await user.click(await screen.findByRole('button', { name: 'New agent' }));
	const sheet = await screen.findByRole('dialog', { name: 'New agent' });
	// Wait for the opening to settle (its register tab, focused), then switch.
	await waitFor(() => expect(within(sheet).getByLabelText('Agent name')).toBeVisible());
	await user.click(within(sheet).getByRole('tab', { name: 'Create it here' }));
	await waitFor(() =>
		expect(within(sheet).getByLabelText('Name', { exact: true })).toHaveFocus(),
	);
	return sheet;
}

async function create(user: User, sheet: HTMLElement, name: string) {
	await user.type(within(sheet).getByLabelText('Name', { exact: true }), name);
	await user.click(within(sheet).getByRole('button', { name: 'Create' }));
}

async function stepFor(sheet: HTMLElement, name: string, timeout = 1500) {
	const firstApi = await within(sheet).findByTestId('first-api-panel', {}, { timeout });
	expect(
		await within(firstApi).findByRole('heading', {
			name: new RegExp(`^(Add GitHub to|Give) ${name}( its first API)?$`),
		}),
	).toBeInTheDocument();
	return firstApi;
}

async function trayFor(name: string, timeout = 3000) {
	const tray = await screen.findByRole('dialog', { name: 'Add APIs' }, { timeout });
	await waitFor(
		() =>
			expect(
				within(tray).getByText(
					(_, el) =>
						el?.tagName === 'P' &&
						new RegExp(`^Pick what ${name} should be able to call`).test(
							el.textContent ?? '',
						),
				),
			).toBeVisible(),
		{ timeout },
	);
	await waitFor(() => expect(queryPanel()).toBeNull());
	expect(screen.queryByTestId('first-api-panel')).toBeNull();
	return tray;
}

const trayFooter = (tray: HTMLElement) => tray.querySelector('footer') as HTMLElement;

async function skipTray(user: User, tray: HTMLElement) {
	const footer = within(trayFooter(tray));
	expect(footer.queryByRole('button', { name: 'Cancel' })).toBeNull();
	expect(within(tray).getAllByRole('button', { name: 'Skip for now' })).toHaveLength(1);
	await user.click(footer.getByRole('button', { name: 'Skip for now' }));
	await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Add APIs' })).toBeNull());
}

describe('New agent panel — where "Create" goes, from the very first one', () => {
	beforeEach(async () => {
		await page.viewport(1440, 900);
		setToken('test-token');
		window.localStorage.clear();
		resetAgentsStore();
	});

	it('from an empty fleet, the first create shows the step while the landing becomes the fleet', async () => {
		clearAgentsStore();
		const user = userEvent.setup();
		renderPage();
		await screen.findByTestId('agents-empty-landing');
		const sheet = await openCreate(user);
		await create(user, sheet, 'first-agent');

		await stepFor(sheet, 'first-agent');
		await waitFor(() => expect(screen.queryByTestId('agents-empty-landing')).toBeNull());
		// The panel stays mounted across the switch, or it forgets the agent and shows the form.
		expect(panel()).toBe(sheet);
		expect(within(sheet).getByTestId('first-api-panel')).toBeInTheDocument();
		expect(within(sheet).queryByLabelText('Name', { exact: true })).toBeNull();
	});

	it('from an empty fleet with a slow roster, the step shows at once from the create itself', async () => {
		clearAgentsStore();
		const user = userEvent.setup();
		renderPage();
		await screen.findByTestId('agents-empty-landing');
		const sheet = await openCreate(user);
		// Every roster read is slow from here: the create's own answer is all the panel has.
		worker.use(
			http.get('/agents', async () => {
				await delay(3000);
			}),
		);
		await create(user, sheet, 'slow-roster');
		await stepFor(sheet, 'slow-roster', 1000);
		expect(within(sheet).getByTestId('arrival-status')).toHaveAttribute(
			'data-status',
			'active',
		);
	});

	it('over a fleet with a slow roster, the create still lands on the tray for the new agent', async () => {
		const user = userEvent.setup();
		renderPage();
		await screen.findByTestId('agent-dock');
		const sheet = await openCreate(user);
		worker.use(
			http.get('/agents', async () => {
				await delay(1200);
			}),
		);
		await create(user, sheet, 'slow-fleet');
		const tray = await trayFor('slow-fleet', 8000);
		expect(
			within(trayFooter(tray)).getByRole('button', { name: 'Skip for now' }),
		).toBeVisible();
	});

	it('over a fleet, from the strip and by `n`: each create goes to the tray, and its Skip lands on the agent', async () => {
		const user = userEvent.setup();
		renderPage();
		await screen.findByTestId('agent-dock');
		for (const [i, name] of ['fleet-a', 'fleet-b', 'fleet-c'].entries()) {
			const sheet = await openCreate(user, i % 2 === 1);
			await create(user, sheet, name);
			const tray = await trayFor(name);
			await skipTray(user, tray);
			await waitFor(() =>
				expect(screen.getByRole('tab', { name: new RegExp(name) })).toHaveAttribute(
					'aria-selected',
					'true',
				),
			);
			const id = selectedId();
			expect(id).toMatch(/^agnt_/);
			expect(isFirstRunDismissed(id!)).toBe(true);
		}
	});

	it('the tray opened from the agent card keeps its Cancel, with no Skip', async () => {
		const user = userEvent.setup();
		renderPage();
		await screen.findByTestId('agent-dock');
		await user.click(screen.getByRole('tab', { name: /support-agent/ }));
		// The card swaps to it a beat after its tab lands; re-read until it has.
		await waitFor(() => expect(screen.getByRole('button', { name: 'Add APIs' })).toBeEnabled());
		await user.click(screen.getByRole('button', { name: 'Add APIs' }));
		const tray = await screen.findByRole('dialog', { name: 'Add APIs' });
		expect(within(trayFooter(tray)).getByRole('button', { name: 'Cancel' })).toBeVisible();
		expect(within(tray).queryByRole('button', { name: 'Skip for now' })).toBeNull();
	});

	it('an empty fleet, then several creates in a row: the first gets the step, the rest the tray', async () => {
		clearAgentsStore();
		const user = userEvent.setup();
		renderPage();
		await screen.findByTestId('agents-empty-landing');

		const first = await openCreate(user);
		await create(user, first, 'agent-a');
		await stepFor(first, 'agent-a');
		await user.click(within(first).getByRole('button', { name: 'Skip for now' }));
		await waitFor(() => expect(queryPanel()).toBeNull());
		await screen.findByTestId('agent-dock');

		for (const name of ['agent-b', 'agent-c']) {
			const sheet = await openCreate(user, true);
			await create(user, sheet, name);
			await skipTray(user, await trayFor(name));
			await waitFor(() =>
				expect(screen.getByRole('tab', { name: new RegExp(name) })).toHaveAttribute(
					'aria-selected',
					'true',
				),
			);
		}
	});
});
