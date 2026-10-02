import { describe, it, expect, beforeEach, vi } from 'vitest';
import { http, HttpResponse } from 'msw';
import { worker } from '@/mocks/browser';
import { renderWithProviders, screen, within, userEvent, checkA11y } from '@/__tests__/test-utils';
import { setToken } from '@/shared/api';
import { resetCredentialsStore, makeMockCredential } from '@/shared/credentials/mocks/handlers';
import { EditCredentialSheet } from '@/shared/credentials/components/EditCredentialSheet';

/**
 * The read-mostly "Bound agents" section inside the edit-credential sheet
 * (theme 5 phase 5a): which agents may use this credential, with suspended
 * state and a link to each agent's tab on the flat Agents surface. Read-only by
 * design — binding management lives on each agent's API sidebar there.
 *
 * `GET /credentials/{id}/agents` is stubbed per-test here (the shared tier
 * must not reach into a feature module's mock store), mirroring the generated
 * `CredentialAgentListResponse` shape.
 */
function seedBoundAgents(
	credentialId: string,
	rows: Array<{ agent_id: string; agent_name: string; suspended?: boolean }>,
) {
	worker.use(
		http.get('/credentials/:cid/agents', ({ params }) => {
			if (params.cid !== credentialId) {
				return HttpResponse.json({ data: [], has_more: false, next_cursor: null });
			}
			return HttpResponse.json({
				data: rows.map((r) => ({
					agent_id: r.agent_id,
					agent_name: r.agent_name,
					bound_at: '2026-05-02T09:00:00Z',
					rule_set_id: null,
					status: 'active',
					suspended: r.suspended ?? false,
				})),
				has_more: false,
				next_cursor: null,
			});
		}),
	);
}

function renderSheet(credentialId: string, onNavigateAway?: () => void) {
	return renderWithProviders(
		<EditCredentialSheet
			credentialId={credentialId}
			open
			onClose={() => {}}
			onNavigateAway={onNavigateAway}
		/>,
	);
}

describe('BoundAgentsSection (edit-credential sheet)', () => {
	beforeEach(() => {
		setToken('test-token');
	});

	it('lists the agents bound to the credential, each linking to its tab on the Agents surface', async () => {
		resetCredentialsStore([
			makeMockCredential({ credential_id: 'cred_slack_1', name: 'Slack bot token' }),
		]);
		seedBoundAgents('cred_slack_1', [
			{ agent_id: 'agnt_active_1', agent_name: 'support-agent' },
		]);
		const { container } = renderSheet('cred_slack_1');

		expect(await screen.findByText('Bound agents (1)')).toBeInTheDocument();
		const row = await screen.findByTestId('bound-agent-row');
		// The agent resolves to its human name and links to itself AS SELECTED on
		// the flat surface, where its API tiles — the bindings this list is about
		// — are managed.
		const link = within(row).getByRole('link', { name: 'support-agent' });
		expect(link).toHaveAttribute('href', '/agents?agent=agnt_active_1');
		// A healthy binding carries no suspended chip.
		expect(within(row).queryByTestId('bound-agent-suspended')).not.toBeInTheDocument();
		expect(within(row).getByText(/^bound /)).toBeInTheDocument();
		// Read-only: no bind/unbind affordances in this phase.
		expect(
			screen.queryByRole('button', { name: /unbind|bind credential/i }),
		).not.toBeInTheDocument();

		await checkA11y(container);
	});

	it('asks the host to stand aside when a link is followed in this tab', async () => {
		// The hosts on the Agents surface cover the very tab the link selects, so
		// an in-tab click has to dismiss them. A modified click opens a second tab
		// and must leave this one as it was.
		const onNavigateAway = vi.fn();
		resetCredentialsStore([
			makeMockCredential({ credential_id: 'cred_slack_1', name: 'Slack bot token' }),
		]);
		seedBoundAgents('cred_slack_1', [
			{ agent_id: 'agnt_active_1', agent_name: 'support-agent' },
		]);
		const user = userEvent.setup();
		renderSheet('cred_slack_1', onNavigateAway);

		const link = within(await screen.findByTestId('bound-agent-row')).getByRole('link', {
			name: 'support-agent',
		});

		// A modified click is the one case the router leaves to the browser, which
		// in this runner means navigating the test frame itself. The default is
		// swallowed for that click only — what is under test is the handler.
		const swallow = (event: Event): void => event.preventDefault();
		document.addEventListener('click', swallow, true);
		try {
			await user.keyboard('{Meta>}');
			await user.click(link);
			await user.keyboard('{/Meta}');
		} finally {
			document.removeEventListener('click', swallow, true);
		}
		expect(onNavigateAway).not.toHaveBeenCalled();

		await user.click(link);
		expect(onNavigateAway).toHaveBeenCalledTimes(1);
	});

	it('marks suspended bindings distinctly', async () => {
		resetCredentialsStore([
			makeMockCredential({ credential_id: 'cred_github_1', name: 'GitHub PAT' }),
		]);
		seedBoundAgents('cred_github_1', [
			{ agent_id: 'agnt_active_1', agent_name: 'support-agent', suspended: true },
		]);
		renderSheet('cred_github_1');

		const row = await screen.findByTestId('bound-agent-row');
		expect(within(row).getByTestId('bound-agent-suspended')).toHaveTextContent('Suspended');
	});

	it('shows an honest empty state when no agent is bound', async () => {
		resetCredentialsStore([
			makeMockCredential({ credential_id: 'cred_unbound_1', name: 'Unused key' }),
		]);
		seedBoundAgents('cred_unbound_1', []);
		renderSheet('cred_unbound_1');

		expect(await screen.findByTestId('bound-agents-empty')).toHaveTextContent(
			/no agents are bound to this credential/i,
		);
	});
});
