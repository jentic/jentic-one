/**
 * AgentActivitySheet — the dock's Activity surface: the console `ActivityPanel`
 * plus the audit slice folded in as a section (changes are activity). The panels
 * keep their console coverage; these pin the composition, the agent scoping of
 * both sections, and the actor-directory resolution in the audit rows.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { page } from 'vitest/browser';
import { renderWithProviders, screen, within, userEvent } from '@/__tests__/test-utils';
import { setToken } from '@/shared/api';
import { Toaster } from '@/shared/ui';
import { resetAgentsStore } from '@/modules/agents/mocks/handlers';
import { resetApisStore, resetCredentialsStore } from '@/shared/credentials/mocks/handlers';
import AgentsPage from '@/modules/agents/pages/AgentsPage';

function renderPage(route = '/') {
	return renderWithProviders(
		<>
			<AgentsPage />
			<Toaster />
		</>,
		{ route },
	);
}

/** Open the Activity sheet from the dock and return a scoped `within`. */
async function openSheet(user: ReturnType<typeof userEvent.setup>) {
	const dock = within(await screen.findByTestId('agent-dock'));
	await user.click(dock.getByRole('button', { name: 'Activity' }));
	return within(await screen.findByTestId('sheet-primitive'));
}

describe('AgentActivitySheet — the dock Activity surface', () => {
	beforeEach(async () => {
		await page.viewport(1280, 900);
		setToken('test-token');
		resetAgentsStore();
		resetCredentialsStore([]);
		resetApisStore([]);
	});

	it('hosts the activity panel AND the Recent changes section', async () => {
		const user = userEvent.setup();
		renderPage('/?agent=agnt_active_1');
		const sheet = await openSheet(user);

		expect(sheet.getByRole('heading', { name: 'Activity' })).toBeInTheDocument();

		// The rehosted ActivityPanel: the recent-executions feed with its
		// Monitor deep link (Monitor owns the full history).
		expect(await sheet.findByText('Recent executions')).toBeInTheDocument();
		expect(sheet.getByRole('link', { name: /Monitor/ })).toBeInTheDocument();

		// The console Overview's audit slice, as a SECTION of this sheet
		// — not another surface. Lifecycle events recorded against this agent
		// as the target, newest first.
		expect(await sheet.findByText('Recent changes')).toBeInTheDocument();
		expect(await sheet.findByText('rotate')).toBeInTheDocument();
		expect(sheet.getByText('approve')).toBeInTheDocument();
		expect(sheet.getByText('register')).toBeInTheDocument();
		// The acting user resolves through the actor directory (ActorLabel) —
		// a raw usr_… id in the feed means the directory wiring regressed.
		expect(await sheet.findAllByText(/Admin User/)).not.toHaveLength(0);
		expect(sheet.queryByText('usr_000000000000000000000admin')).not.toBeInTheDocument();
	});

	it('the Recent changes section is agent-scoped: another agent reads its own (empty) trail', async () => {
		const user = userEvent.setup();
		renderPage('/?agent=agnt_disabled_1');
		const sheet = await openSheet(user);

		// Only agnt_active_1 carries seeded audit rows; this agent renders
		// the honest empty state pointing at the full trail in Monitor.
		expect(await sheet.findByText('Recent changes')).toBeInTheDocument();
		expect(
			await sheet.findByText(/No recorded changes for this agent yet/),
		).toBeInTheDocument();
		expect(sheet.queryByText('rotate')).not.toBeInTheDocument();
	});
});
