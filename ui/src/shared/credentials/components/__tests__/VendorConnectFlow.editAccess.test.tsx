import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { http, HttpResponse } from 'msw';
import { worker } from '@/mocks/browser';
import { renderWithProviders, screen, userEvent, waitFor, within } from '@/__tests__/test-utils';
import { VendorConnectFlow } from '@/shared/credentials/components/VendorConnectFlow';
import {
	getEndedMockConnectSessions,
	getMockConnectSessions,
	resetConnectSessionsStore,
	resetCredentialsStore,
	seedMockAgentConnectSession,
} from '@/shared/credentials/mocks/handlers';
import { resetAgentsStore, seedCredentialBindings } from '@/modules/agents/mocks/handlers';

/**
 * Edit mode: a person composes a change to a binding with the connect flow's
 * rules step, then reviews it in the same edit review an agent's request gets.
 */

const READ = {
	effect: 'allow' as const,
	methods: ['GET'],
	path: '/v1/',
	match_mode: 'prefix' as const,
};

function seedBinding(over: Partial<Parameters<typeof seedCredentialBindings>[0][number]> = {}) {
	seedCredentialBindings([
		{
			agent_id: 'agnt_1',
			credential_id: 'cred_gmail',
			name: 'Work Gmail',
			serves: [{ api_vendor: 'gmail' }],
			permissions: [READ],
			granted_scopes: ['gmail.readonly'],
			...over,
		},
	]);
}

function renderEdit() {
	const onBack = vi.fn();
	const onDone = vi.fn();
	renderWithProviders(
		<VendorConnectFlow
			mode="edit"
			agentId="agnt_1"
			credentialId="cred_gmail"
			credentialName="Work Gmail"
			vendorKey="gmail"
			apiReference={null}
			onBack={onBack}
			onDone={onDone}
		/>,
	);
	return { onBack, onDone };
}

/** Through the rules step (today's rules, unchanged) to the details step. */
async function toDetails(user: ReturnType<typeof userEvent.setup>) {
	await screen.findByText('Change what this agent can do with Work Gmail.');
	await user.click(screen.getByRole('button', { name: 'Continue' }));
	await screen.findByRole('button', { name: 'Review change' });
}

beforeEach(() => {
	resetAgentsStore();
	resetConnectSessionsStore();
	resetCredentialsStore();
	vi.spyOn(window, 'open').mockReturnValue(null);
});

afterEach(() => {
	vi.restoreAllMocks();
});

describe('VendorConnectFlow edit — a person changing access', () => {
	it('opens the change and reviews it like an agent request, without a Reject', async () => {
		seedBinding();
		renderEdit();
		const user = userEvent.setup();
		await toDetails(user);

		expect(screen.getByText(/Granted today: gmail.readonly/)).toBeInTheDocument();
		await user.type(screen.getByLabelText('Scopes to add (optional)'), 'gmail.send');
		await user.type(screen.getByLabelText('Reason (optional)'), 'Send replies');
		await user.click(screen.getByRole('button', { name: 'Review change' }));

		const scopes = await screen.findByRole('region', { name: 'Scope changes' });
		expect(scopes).toHaveTextContent('gmail.send');
		expect(
			screen.getByText('Review your change to what the agent can do with Work Gmail.'),
		).toBeInTheDocument();
		expect(screen.queryByRole('button', { name: 'Reject' })).not.toBeInTheDocument();
		expect(screen.getByRole('button', { name: 'Discard' })).toBeInTheDocument();

		await user.click(screen.getByRole('button', { name: 'Approve and sign in' }));
		await waitFor(() =>
			expect(getMockConnectSessions()[0].confirmBodies[0]).toMatchObject({
				kind: 'edit',
				revision_id: 'csr_1',
				confirmed_scopes: ['gmail.readonly', 'gmail.send'],
			}),
		);
		expect(getMockConnectSessions()[0].reason).toBe('Send replies');
	});

	it('picks scopes to add from the vendor’s catalog, leaving out the granted ones', async () => {
		seedBinding({
			scope_catalog: [
				{ name: 'gmail.readonly', classification: 'read', description: 'Read mail.' },
				{ name: 'gmail.send', classification: 'write', description: 'Send mail.' },
			],
		});
		renderEdit();
		const user = userEvent.setup();
		await toDetails(user);

		expect(screen.queryByLabelText('Scopes to add (optional)')).not.toBeInTheDocument();
		expect(screen.queryByText('gmail.readonly', { selector: 'code' })).not.toBeInTheDocument();
		await user.click(screen.getByText('gmail.send', { selector: 'code' }));
		await user.click(screen.getByRole('button', { name: 'Review change' }));

		const scopes = await screen.findByRole('region', { name: 'Scope changes' });
		expect(scopes).toHaveTextContent('gmail.send');
	});

	it('names the scopes the vendor doesn’t offer', async () => {
		seedBinding();
		worker.use(
			http.post('/integrations:edit-connection', () =>
				HttpResponse.json(
					{
						type: 'connect_session_unknown_scopes',
						status: 400,
						detail: 'Unknown scopes.',
						unknown_scopes: ['gmail.sned'],
					},
					{ status: 400, headers: { 'Content-Type': 'application/problem+json' } },
				),
			),
		);
		renderEdit();
		const user = userEvent.setup();
		await toDetails(user);
		await user.type(screen.getByLabelText('Scopes to add (optional)'), 'gmail.sned');
		await user.click(screen.getByRole('button', { name: 'Review change' }));
		expect(
			await screen.findByText(/doesn’t offer these scopes: gmail.sned/),
		).toBeInTheDocument();
	});

	it('discarding a person’s own change cancels it', async () => {
		seedBinding({ suspended: true });
		const { onBack } = renderEdit();
		const user = userEvent.setup();
		await toDetails(user);

		expect(screen.getByRole('checkbox', { name: /Switch this binding back on/ })).toBeChecked();
		await user.click(screen.getByRole('button', { name: 'Review change' }));
		await user.click(await screen.findByRole('button', { name: 'Discard' }));
		await waitFor(() => expect(getEndedMockConnectSessions()[0]?.outcome).toBe('cancelled'));
		expect(onBack).toHaveBeenCalled();
	});

	it('says when the change leaves the access as it is', async () => {
		seedBinding({ granted_scopes: null });
		renderEdit();
		const user = userEvent.setup();
		await toDetails(user);

		expect(screen.queryByLabelText('Scopes to add (optional)')).not.toBeInTheDocument();
		await user.click(screen.getByRole('button', { name: 'Review change' }));
		expect(await screen.findByText(/That leaves the access as it is/)).toBeInTheDocument();
	});

	it('opens the edit already waiting for the binding instead of a second one', async () => {
		seedBinding({ suspended: true });
		const open = seedMockAgentConnectSession({
			agent_id: 'agnt_1',
			vendor_key: 'gmail',
			resolved_flow: 'edit',
			reason: 'Needs to send replies',
			edit: {
				binding: {
					agent_id: 'agnt_1',
					credential_id: 'cred_gmail',
					credential_name: 'Work Gmail',
				},
				revision: { id: 'csr_9', number: 1 },
				current: {
					rules: [{ id: 'apr_1', ...READ }],
					rule_set: null,
					suspended: true,
					granted_scopes: ['gmail.readonly'],
				},
				result: {
					target: 'binding',
					rules: [{ id: 'apr_1', ...READ }],
					scopes: ['gmail.readonly'],
					suspended: false,
				},
				widens_access: true,
				other_bound_agents: [],
				needs_vendor_consent: false,
			},
		});
		renderEdit();
		const user = userEvent.setup();
		await toDetails(user);
		await user.click(screen.getByRole('button', { name: 'Review change' }));

		expect(await screen.findByText(/already waiting for approval/)).toBeInTheDocument();
		// The agent's request: its own review, Reject included.
		expect(await screen.findByRole('button', { name: 'Reject' })).toBeInTheDocument();
		expect(getMockConnectSessions().map((s) => s.session_id)).toEqual([open.session_id]);
		expect(within(document.body).getByText('Revision 1')).toBeInTheDocument();
	});
});
