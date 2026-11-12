import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { http, HttpResponse } from 'msw';
import { worker } from '@/mocks/browser';
import { renderWithProviders, screen, userEvent, waitFor } from '@/__tests__/test-utils';
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

	it('gates dismissal, inline Cancel, and duplicate starts while the edit session is starting', async () => {
		seedBinding({ scope_catalog: CATALOG });
		const onBack = vi.fn();
		const dismissHandlers: Array<(() => void) | null> = [];
		let release!: () => void;
		const pending = new Promise<void>((resolve) => {
			release = resolve;
		});
		let starts = 0;
		worker.use(
			http.post('/integrations:edit-connection', async () => {
				starts += 1;
				await pending;
				const session = seedMockAgentConnectSession({
					agent_id: 'agnt_1',
					credential_id: 'cred_gmail',
					vendor_key: 'gmail',
					resolved_flow: 'edit',
					edit: getMockConnectSessions()[0]?.edit,
				});
				return HttpResponse.json(
					{
						session_id: session.session_id,
						poll_token: session.poll_token,
						status: 'consenting',
						challenge: {
							kind: 'authorization_code',
							authorize_url: 'https://vendor.example.test/oauth/authorize',
						},
					},
					{ status: 201 },
				);
			}),
		);
		renderWithProviders(
			<VendorConnectFlow
				mode="edit-scopes"
				agentId="agnt_1"
				credentialId="cred_gmail"
				credentialName="Work Gmail"
				vendorKey="gmail"
				onBack={onBack}
				onDone={vi.fn()}
				onRegisterDismiss={(handler): void => {
					dismissHandlers.push(handler);
				}}
			/>,
		);
		const user = userEvent.setup();
		await user.click(await screen.findByText('gmail.send', { selector: 'code' }));
		await user.click(screen.getByRole('button', { name: 'Sign in and save' }));
		await waitFor(() => expect(starts).toBe(1));
		expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled();
		expect(screen.getByRole('button', { name: 'Sign in and save' })).toBeDisabled();
		const dismiss = dismissHandlers[dismissHandlers.length - 1];
		expect(dismiss).toEqual(expect.any(Function));
		dismiss?.();
		expect(onBack).not.toHaveBeenCalled();
		release();
		await waitFor(() => expect(window.open).toHaveBeenCalledTimes(1));
		expect(starts).toBe(1);
	});

	it('cancels a session whose start response arrives after direct unmount', async () => {
		seedBinding({ scope_catalog: CATALOG });
		let release!: () => void;
		const pending = new Promise<void>((resolve) => {
			release = resolve;
		});
		const session = seedMockAgentConnectSession({
			agent_id: 'agnt_1',
			credential_id: 'cred_gmail',
			vendor_key: 'gmail',
			resolved_flow: 'edit',
		});
		worker.use(
			http.post('/integrations:edit-connection', async () => {
				await pending;
				return HttpResponse.json(
					{
						session_id: session.session_id,
						poll_token: session.poll_token,
						status: 'consenting',
						challenge: {
							kind: 'authorization_code',
							authorize_url: 'https://vendor.example.test/oauth/authorize',
						},
					},
					{ status: 201 },
				);
			}),
		);
		const view = renderWithProviders(
			<VendorConnectFlow
				mode="edit-scopes"
				agentId="agnt_1"
				credentialId="cred_gmail"
				credentialName="Work Gmail"
				vendorKey="gmail"
				onBack={vi.fn()}
				onDone={vi.fn()}
			/>,
		);
		const user = userEvent.setup();
		await user.click(await screen.findByText('gmail.send', { selector: 'code' }));
		await user.click(screen.getByRole('button', { name: 'Sign in and save' }));
		view.unmount();
		release();
		await waitFor(() =>
			expect(getEndedMockConnectSessions().map((s) => s.session_id)).toContain(
				session.session_id,
			),
		);
		expect(window.open).not.toHaveBeenCalled();
	});

	it('renders an applied edit after cancellation loses the finalise race', async () => {
		seedBinding({ scope_catalog: CATALOG });
		let cancelRequested = false;
		worker.use(
			http.post('/connect-sessions/:sessionId\\:cancel', () => {
				cancelRequested = true;
				return new HttpResponse(null, { status: 204 });
			}),
			http.get('/connect-sessions/:sessionId/status', () =>
				HttpResponse.json(
					cancelRequested
						? { status: 'applied', error_code: null }
						: { status: 'polling', error_code: null },
				),
			),
		);
		const { onBack } = renderEdit();
		const user = userEvent.setup();
		await user.click(await screen.findByText('gmail.send', { selector: 'code' }));
		await user.click(screen.getByRole('button', { name: 'Sign in and save' }));
		await screen.findByText('Waiting for you to approve at Work Gmail…');
		await user.click(screen.getByRole('button', { name: 'Cancel' }));
		expect(
			await screen.findByText('Access to Work Gmail updated', undefined, { timeout: 10_000 }),
		).toBeInTheDocument();
		expect(onBack).not.toHaveBeenCalled();
	});

	it('keeps the session open for a retry when cancel cannot reread its outcome', async () => {
		seedBinding({ scope_catalog: CATALOG });
		worker.use(
			http.get('/connect-sessions/:sessionId/status', () =>
				HttpResponse.json({ detail: 'Status temporarily unavailable' }, { status: 500 }),
			),
		);
		const { onBack } = renderEdit();
		const user = userEvent.setup();
		await user.click(await screen.findByText('gmail.send', { selector: 'code' }));
		await user.click(screen.getByRole('button', { name: 'Sign in and save' }));
		await screen.findByText('Waiting for you to approve at Work Gmail…');
		await user.click(screen.getByRole('button', { name: 'Cancel' }));
		expect(
			await screen.findByText('Status temporarily unavailable', undefined, {
				timeout: 10_000,
			}),
		).toBeInTheDocument();
		expect(onBack).not.toHaveBeenCalled();
		expect(screen.getByRole('button', { name: 'Cancel' })).toBeEnabled();
	});

	it('closes after an authoritative cancelled outcome', async () => {
		seedBinding({ scope_catalog: CATALOG });
		const { onBack } = renderEdit();
		const user = userEvent.setup();
		await user.click(await screen.findByText('gmail.send', { selector: 'code' }));
		await user.click(screen.getByRole('button', { name: 'Sign in and save' }));
		await screen.findByText('Waiting for you to approve at Work Gmail…');
		await user.click(screen.getByRole('button', { name: 'Cancel' }));
		await waitFor(() => expect(onBack).toHaveBeenCalled());
		const ended = getEndedMockConnectSessions();
		expect(ended[ended.length - 1]?.outcome).toBe('cancelled');
	});

	it('says when the credential’s scopes can’t change', async () => {
		seedBinding({ granted_scopes: null });
		renderEdit();
		expect(
			await screen.findByText('This credential’s scopes can’t be changed here.'),
		).toBeInTheDocument();
	});
});
