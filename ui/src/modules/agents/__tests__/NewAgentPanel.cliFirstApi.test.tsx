/**
 * Register routes for an org's first agent: an arrival approved in the panel gets
 * its first API (GitHub) in the same mounted panel, as the "Create it here" step does.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { MotionConfig } from 'framer-motion';
import { useLocation } from 'react-router';
import { page } from 'vitest/browser';
import { renderWithProviders, screen, waitFor, within, userEvent } from '@/__tests__/test-utils';
import { setToken } from '@/shared/api';
import { Toaster } from '@/shared/ui';
import {
	clearAgentsStore,
	resetAgentsStore,
	selfRegisterAgent,
} from '@/modules/agents/mocks/handlers';
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
type QueryClientLike = ReturnType<typeof renderPage>['queryClient'];

const queryPanel = () => screen.queryByRole('dialog', { name: 'New agent' });
const footerButtons = (sheet: HTMLElement) =>
	Array.from(
		(sheet.querySelector('[data-state="active"] footer') as HTMLElement).querySelectorAll(
			'button',
		),
	).map((b) => b.textContent?.trim());

async function openRegister(user: User) {
	await screen.findByTestId('agents-empty-landing');
	await user.click(screen.getByRole('button', { name: 'New agent' }));
	const sheet = await screen.findByRole('dialog', { name: 'New agent' });
	await waitFor(() => expect(within(sheet).getByLabelText('Agent name')).toBeVisible());
	return sheet;
}

async function registerAndApprove(
	user: User,
	sheet: HTMLElement,
	queryClient: QueryClientLike,
	name: string,
) {
	const id = selfRegisterAgent(name);
	await queryClient.invalidateQueries();
	await within(sheet).findByTestId('arrival-card', {}, { timeout: 6000 });
	const approve = within(sheet).getByRole('button', { name: `Approve ${name}` });
	await waitFor(() => expect(approve).toBeEnabled());
	await user.click(approve);
	return id;
}

describe('New agent panel — the first agent, registered from the CLI', () => {
	beforeEach(async () => {
		await page.viewport(1440, 900);
		setToken('test-token');
		window.localStorage.clear();
		resetAgentsStore();
		clearAgentsStore();
	});

	it('approving it shows the GitHub step in the same panel: Skip once in the body, Close in the footer', async () => {
		const user = userEvent.setup();
		const { queryClient } = renderPage();
		const sheet = await openRegister(user);
		const id = await registerAndApprove(user, sheet, queryClient, 'my-first-agent');

		const firstApi = await within(sheet).findByTestId('first-api-panel');
		expect(
			await within(firstApi).findByRole('heading', { name: 'Add GitHub to my-first-agent' }),
		).toBeInTheDocument();
		expect(within(firstApi).getByRole('button', { name: 'Skip for now' })).toBeEnabled();
		expect(within(sheet).getAllByRole('button', { name: 'Skip for now' })).toHaveLength(1);
		expect(footerButtons(sheet)).toEqual(['Close']);
		expect(screen.queryByRole('dialog', { name: 'Add APIs' })).toBeNull();

		// The landing behind may hand over to the fleet: the panel must stay mounted on the step.
		await new Promise((r) => setTimeout(r, 500));
		expect(screen.getByRole('dialog', { name: 'New agent' })).toBe(sheet);
		expect(within(sheet).getByTestId('first-api-panel')).toBe(firstApi);
		expect(within(sheet).queryByTestId('register-command')).toBeNull();

		await user.click(within(firstApi).getByRole('button', { name: 'Skip for now' }));
		await waitFor(() => expect(queryPanel()).toBeNull());
		expect(screen.getByTestId('location-search')).toHaveTextContent(`agent=${id}`);
		expect(isFirstRunDismissed(id)).toBe(true);
	});

	it('"Continue with GitHub" opens its credential step', async () => {
		const user = userEvent.setup();
		const { queryClient } = renderPage();
		const sheet = await openRegister(user);
		await registerAndApprove(user, sheet, queryClient, 'my-first-agent');
		const firstApi = await within(sheet).findByTestId('first-api-panel');
		const github = await within(firstApi).findByRole('button', {
			name: 'Continue with GitHub',
		});
		await waitFor(() => expect(github).toBeEnabled());
		await user.click(github);

		const queue = await screen.findByRole('dialog', { name: /^Set up 1 API for / });
		expect(within(queue).getByTestId('queue-active-pane')).toHaveAccessibleName(/GitHub/);
		await waitFor(() => expect(queryPanel()).toBeNull());
	});
});
