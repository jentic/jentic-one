/**
 * AgentKeysSheet — the dock's API key surface: `AgentKeysPanel` behind the key
 * verb. Pins the key lifecycle as the sheet serves it for the selected agent:
 * first issue, the confirm before a rotation, and the migrated-key warning a
 * service-account successor gets before its unrecoverable key is replaced.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { http, HttpResponse } from 'msw';
import { page } from 'vitest/browser';
import { renderWithProviders, screen, within, userEvent } from '@/__tests__/test-utils';
import { worker } from '@/mocks/browser';
import { setToken } from '@/shared/api';
import { Toaster } from '@/shared/ui';
import { resetAgentsStore, seedServiceAccountSuccessor } from '@/modules/agents/mocks/handlers';
import { resetApisStore, resetCredentialsStore } from '@/shared/credentials/mocks/handlers';
import AgentsPage from '@/modules/agents/pages/AgentsPage';

function renderPage(route: string) {
	return renderWithProviders(
		<>
			<AgentsPage />
			<Toaster />
		</>,
		{ route },
	);
}

/** Open the API key sheet from the dock and return a scoped `within`. */
async function openSheet(user: ReturnType<typeof userEvent.setup>) {
	const dock = within(await screen.findByTestId('agent-dock'));
	await user.click(dock.getByRole('button', { name: 'API key' }));
	return within(await screen.findByTestId('sheet-primitive'));
}

/** Issue a first key and dismiss the one-time reveal, so a key exists. */
async function issueFirstKey(
	user: ReturnType<typeof userEvent.setup>,
	sheet: ReturnType<typeof within>,
	agentName: string,
) {
	await user.click(
		await sheet.findByRole('button', { name: `Generate API key for ${agentName}` }),
	);
	const reveal = await screen.findByRole('dialog', { name: 'API key generated' });
	await user.click(within(reveal).getByRole('button', { name: 'Done' }));
}

describe('AgentKeysSheet — the dock API key surface', () => {
	beforeEach(async () => {
		await page.viewport(1280, 900);
		setToken('test-token');
		resetAgentsStore();
		resetCredentialsStore([]);
		resetApisStore([]);
	});

	it('issues a first key, revealing the plaintext exactly once', async () => {
		const user = userEvent.setup();
		renderPage('/?agent=agnt_active_1');
		const sheet = await openSheet(user);

		// No key issued yet → honest empty copy + the generate action.
		expect(
			await sheet.findByText('No API key has been issued for this agent yet.'),
		).toBeInTheDocument();
		await user.click(sheet.getByRole('button', { name: 'Generate API key for support-agent' }));

		expect(
			await screen.findByRole('dialog', { name: 'API key generated' }),
		).toBeInTheDocument();
	});

	it('confirms before regenerating an existing API key', async () => {
		const user = userEvent.setup();
		renderPage('/?agent=agnt_active_1');
		const sheet = await openSheet(user);

		// First issue destroys nothing → generates directly (no confirm step).
		await issueFirstKey(user, sheet, 'support-agent');

		// Now a key exists → the action reads Regenerate and confirms first,
		// because rotating invalidates the current key immediately.
		await user.click(
			await sheet.findByRole('button', { name: 'Regenerate API key for support-agent' }),
		);
		const confirm = await screen.findByRole('dialog', {
			name: 'Regenerate API key for support-agent',
		});
		expect(
			within(confirm).getByText(/current API key stops working immediately/),
		).toBeInTheDocument();
		// An ordinary agent's key was never migrated, so there is nothing to warn about.
		expect(within(confirm).queryByTestId('migrated-key-warning')).not.toBeInTheDocument();
		await user.click(within(confirm).getByRole('button', { name: 'Regenerate' }));

		expect(
			await screen.findByRole('dialog', { name: 'API key generated' }),
		).toBeInTheDocument();
	});

	it("warns that a service-account successor's migrated key is unrecoverable before rotating it", async () => {
		seedServiceAccountSuccessor();
		const user = userEvent.setup();
		renderPage('/?agent=agnt_successor_1');
		const sheet = await openSheet(user);

		await user.click(
			await sheet.findByRole('button', {
				name: 'Regenerate API key for service-account:sva_active_1',
			}),
		);
		const confirm = await screen.findByRole('dialog', {
			name: 'Regenerate API key for service-account:sva_active_1',
		});
		expect(within(confirm).getByTestId('migrated-key-warning')).toHaveTextContent(
			/replaced a retired service account/,
		);
		await user.click(within(confirm).getByRole('button', { name: 'Regenerate' }));
		const reveal = await screen.findByRole('dialog', { name: 'API key generated' });
		await user.click(within(reveal).getByRole('button', { name: 'Done' }));

		// Once rotated, the key is a fresh one — the warning no longer applies.
		await user.click(
			await sheet.findByRole('button', {
				name: 'Revoke API key for service-account:sva_active_1',
			}),
		);
		const revoke = await screen.findByRole('dialog', { name: /Revoke API key/ });
		expect(within(revoke).queryByTestId('migrated-key-warning')).not.toBeInTheDocument();
	});

	// The signal is the credential row (migration-created, never rotated), not
	// the audit history: that is capped at the latest 50 agent audit rows, so a
	// key rotated long ago can fall out of it.
	it('does not warn once a successor key was rotated, even with an empty key history', async () => {
		seedServiceAccountSuccessor();
		worker.use(
			http.get('/agents/:id/api-key', () =>
				HttpResponse.json({
					id: 'agc_agnt_successor_1',
					status: 'active',
					created_at: '2026-01-01T00:00:00Z',
					rotated_at: '2026-02-01T00:00:00Z',
					created_by: 'system:theme8-sa-migration',
				}),
			),
			http.get('/agents/:id/api-key/history', () => HttpResponse.json({ data: [] })),
		);
		const user = userEvent.setup();
		renderPage('/?agent=agnt_successor_1');
		const sheet = await openSheet(user);

		await user.click(
			await sheet.findByRole('button', {
				name: 'Regenerate API key for service-account:sva_active_1',
			}),
		);
		const confirm = await screen.findByRole('dialog', {
			name: 'Regenerate API key for service-account:sva_active_1',
		});
		expect(within(confirm).queryByTestId('migrated-key-warning')).not.toBeInTheDocument();
	});
});
