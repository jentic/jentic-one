import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { http, HttpResponse } from 'msw';
import { worker } from '@/mocks/browser';
import { renderWithProviders, screen, userEvent, waitFor } from '@/__tests__/test-utils';
import { VendorConnectFlow } from '@/shared/credentials/components/VendorConnectFlow';
import {
	getMockConnectSessions,
	resetConnectSessionsStore,
	resetCredentialsStore,
	seedMockAgentConnectSession,
} from '@/shared/credentials/mocks/handlers';
import { resetAgentsStore, seedCredentialBindings } from '@/modules/agents/mocks/handlers';

/**
 * Edit-scopes mode: a person adds and removes scopes on a binding's
 * credential. It's their own change, applied at once through an edit
 * session, so it goes straight to the vendor sign-in for the new set, the
 * same step an approved agent request runs.
 */

const READ = {
	effect: 'allow' as const,
	methods: ['GET'],
	path: '/v1/',
	match_mode: 'prefix' as const,
};

const CATALOG = [
	{ name: 'gmail.readonly', classification: 'read' as const, description: 'Read mail.' },
	{ name: 'gmail.send', classification: 'write' as const, description: 'Send mail.' },
];

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
			mode="edit-scopes"
			agentId="agnt_1"
			credentialId="cred_gmail"
			credentialName="Work Gmail"
			vendorKey="gmail"
			onBack={onBack}
			onDone={onDone}
		/>,
	);
	return { onBack, onDone };
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

describe('VendorConnectFlow edit-scopes — a person changing scopes', () => {
	it('ticks and unticks from the vendor’s catalog and goes straight to the sign-in', async () => {
		seedBinding({ scope_catalog: CATALOG });
		renderEdit();
		const user = userEvent.setup();

		await screen.findByText('Change the OAuth scopes on Work Gmail.');
		// Nothing to save until the set differs from the grant.
		expect(screen.getByRole('button', { name: 'Sign in and save' })).toBeDisabled();
		// Swap a granted scope for another.
		await user.click(screen.getByText('gmail.readonly', { selector: 'code' }));
		await user.click(screen.getByText('gmail.send', { selector: 'code' }));
		await user.click(screen.getByRole('button', { name: 'Sign in and save' }));

		await waitFor(() => expect(window.open).toHaveBeenCalled());
		const [session] = getMockConnectSessions();
		expect(session.edit?.result.scopes).toEqual(['gmail.send']);
		expect(window.open).toHaveBeenCalledWith(
			`https://vendor.example.test/oauth/authorize?edit=${session.session_id}`,
			'_blank',
			'noopener,noreferrer',
		);
		// No review screen: the grant landing applies it.
		session.status = { ...session.status, status: 'applied' };
		expect(
			await screen.findByText('Access to Work Gmail updated', undefined, { timeout: 10_000 }),
		).toBeInTheDocument();
	}, 20_000);

	it('takes scope names as text without a catalog, and names any the vendor refuses', async () => {
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

		const field = await screen.findByLabelText('Scopes to add');
		// The grant's scopes are listed to untick, even without a catalog.
		expect(screen.getByText('gmail.readonly', { selector: 'code' })).toBeInTheDocument();
		await user.type(field, 'gmail.sned');
		await user.click(screen.getByRole('button', { name: 'Sign in and save' }));
		expect(
			await screen.findByText(/doesn’t offer these scopes: gmail.sned/),
		).toBeInTheDocument();
	});

	it('adds scopes beside an agent’s waiting request', async () => {
		seedBinding({ scope_catalog: CATALOG });
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
					suspended: false,
					granted_scopes: ['gmail.readonly'],
				},
				result: {
					target: 'binding',
					rules: [{ id: 'apr_1', ...READ }],
					scopes: ['gmail.readonly', 'gmail.send'],
					suspended: false,
				},
				widens_access: true,
				other_bound_agents: [],
				needs_vendor_consent: true,
			},
		});
		renderEdit();
		const user = userEvent.setup();
		await user.click(await screen.findByText('gmail.send', { selector: 'code' }));
		await user.click(screen.getByRole('button', { name: 'Sign in and save' }));

		await waitFor(() => expect(window.open).toHaveBeenCalled());
		// Both are open: the agent's request waits on its review.
		expect(getMockConnectSessions().map((s) => s.session_id)).toContain(open.session_id);
		expect(getMockConnectSessions()).toHaveLength(2);
	});

	it('says when the credential’s scopes can’t change', async () => {
		seedBinding({ granted_scopes: null });
		renderEdit();
		expect(
			await screen.findByText('This credential’s scopes can’t be changed here.'),
		).toBeInTheDocument();
	});
});
