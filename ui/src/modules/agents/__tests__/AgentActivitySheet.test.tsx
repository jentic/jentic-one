/**
 * AgentActivitySheet — the dock's Activity surface: `ActivityPanel` plus the
 * audit slice folded in as a section (changes are activity). Pins the
 * composition, the agent scoping of both sections, the Monitor deep link, the
 * permission gate, and the actor-directory resolution in the audit rows.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { page } from 'vitest/browser';
import {
	renderWithProviders,
	screen,
	within,
	userEvent,
	createErrorHandler,
} from '@/__tests__/test-utils';
import { worker } from '@/mocks/browser';
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

		// The ActivityPanel: the recent-executions feed with its
		// Monitor deep link (Monitor owns the full history).
		expect(await sheet.findByText('Recent executions')).toBeInTheDocument();
		expect(sheet.getByRole('link', { name: /Monitor/ })).toBeInTheDocument();

		// The audit slice, as a SECTION of this sheet — not another surface.
		// Lifecycle events recorded against this agent as the target, newest first.
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

	it("lists the agent's executions with a Monitor deep link carrying the actor filter", async () => {
		const user = userEvent.setup();
		renderPage('/?agent=agnt_active_1');
		const sheet = await openSheet(user);

		// The feed lists this agent's executions only (agents-module fixture) —
		// with the human-readable operation (method + path template) when the
		// record carries one; the opaque operation_id never renders (legacy
		// rows show just the credential attribution).
		expect(
			await sheet.findByText('github · POST /repos/{owner}/{repo}/issues'),
		).toBeInTheDocument();
		expect(sheet.queryByText(/search_issues/)).not.toBeInTheDocument();
		expect(sheet.getByText(/pbac_denied/)).toBeInTheDocument();
		expect(sheet.getByText('Execution volume · 7d')).toBeInTheDocument();

		const link = sheet.getByRole('link', { name: /Open Monitor/ });
		const href = new URL(link.getAttribute('href')!, window.location.origin);
		expect(href.searchParams.get('show')).toBe('calls');
		expect(href.searchParams.get('actor_id')).toBe('agnt_active_1');
		expect(href.searchParams.get('actor_type')).toBe('agent');
	});

	it('shows a quiet permission note for non-admins (403), not an error', async () => {
		worker.use(
			createErrorHandler('get', '/monitoring/usage', { status: 403 }),
			createErrorHandler('get', '/executions', { status: 403 }),
		);
		const user = userEvent.setup();
		renderPage('/?agent=agnt_active_1');
		const sheet = await openSheet(user);

		expect(await sheet.findByText('Activity requires elevated access')).toBeInTheDocument();
		expect(sheet.queryByRole('alert')).not.toBeInTheDocument();
	});
});
